# CUI // SP-CTI
"""Tests for the Boundary Impact (ATO) panel routes — task bdr-feat-3.

Surfaces the RICOAS 4-tier boundary-impact engine
(tools/requirements/boundary_analyzer.py) in the /boundary UI via three
additive blueprint routes:

    GET  /boundary/api/impact/context       — ATO systems + intake reqs (pickers)
    POST /boundary/api/impact/assess        — 4-tier assessment result
    POST /boundary/api/impact/alternatives  — RED-tier alternative COAs

The engine reads MAIN-DB tables (ato_system_registry, intake_requirements,
boundary_impact_assessments). We point its module-level DB_PATH at a temp
SQLite file seeded through a translating StorageConnection
(tools.db.storage.get_connection), so the PG-native ``%s`` call sites work
against the SQLite init-fallback exactly as they do in production.

Coverage:
  * context with a registered ATO system  -> systems + requirements populated
  * context with no registered system     -> empty-state payload (200, message)
  * assess returns tier + score + controls + remediation
  * assess RED -> alternatives path yields COA list
  * assess GREEN -> alternatives refused (400, not RED)
  * validation + auth (400 on missing params, 401 without session)
"""
from __future__ import annotations

import pytest

from tools.requirements import boundary_analyzer

# ---------------------------------------------------------------------------
# Schema + seed
# ---------------------------------------------------------------------------

_MAIN_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS ato_system_registry (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    system_name TEXT NOT NULL,
    system_acronym TEXT,
    ato_type TEXT,
    ato_date TEXT,
    ato_expiry TEXT,
    authorizing_official TEXT,
    accreditation_boundary TEXT,
    ssp_document_id INTEGER,
    impact_level TEXT,
    data_types TEXT,
    interconnections TEXT,
    baseline_controls TEXT,
    component_inventory TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS intake_requirements (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    project_id TEXT,
    source_turn INTEGER,
    raw_text TEXT NOT NULL,
    refined_text TEXT,
    requirement_type TEXT DEFAULT 'functional',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS boundary_impact_assessments (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    project_id TEXT NOT NULL,
    system_id TEXT NOT NULL,
    requirement_id TEXT,
    safe_item_id TEXT,
    impact_tier TEXT NOT NULL,
    impact_category TEXT NOT NULL,
    impact_description TEXT NOT NULL,
    affected_controls TEXT,
    affected_components TEXT,
    ssp_sections_impacted TEXT,
    remediation_required TEXT,
    alternative_approach TEXT,
    risk_score REAL DEFAULT 0.0,
    assessed_by TEXT DEFAULT 'icdev-requirements-analyst',
    assessed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(requirement_id, system_id)
);
"""

_PROJECT_ID = "proj-bdr-3"
_SYSTEM_ID = "sys-bdr-3"
_REQ_GREEN = "req-green-1"
_REQ_RED = "req-red-1"


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Temp SQLite main-DB seeded with an ATO system + two requirements.

    Points boundary_analyzer.DB_PATH at it (both the engine and the blueprint's
    requirements/alternatives reads resolve the same path).
    """
    from tools.db.storage import get_connection

    db_file = tmp_path / "main.db"
    conn = get_connection(db_path=str(db_file))
    conn.executescript(_MAIN_DDL)
    conn.execute("INSERT INTO projects (id, name) VALUES (%s, %s)", (_PROJECT_ID, "BDR Feat 3"))
    conn.execute(
        "INSERT INTO ato_system_registry "
        "(id, project_id, system_name, ato_type, impact_level, classification, "
        " accreditation_boundary, data_types, interconnections, baseline_controls, component_inventory) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            _SYSTEM_ID, _PROJECT_ID, "Mission System", "ato", "IL5", "CUI",
            "{}", "[]", "[]", '["AC-2", "AU-2"]', "[]",
        ),
    )
    conn.execute(
        "INSERT INTO intake_requirements (id, session_id, project_id, raw_text, refined_text, requirement_type) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (_REQ_GREEN, "sess-1", _PROJECT_ID,
         "Apply a minor configuration change and patch to an existing internal component.",
         "", "functional"),
    )
    conn.execute(
        "INSERT INTO intake_requirements (id, session_id, project_id, raw_text, refined_text, requirement_type) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (_REQ_RED, "sess-1", _PROJECT_ID,
         "Process SECRET classification data and require boundary expansion to a new enclave.",
         "", "security"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(boundary_analyzer, "DB_PATH", db_file)
    return db_file


@pytest.fixture
def bdc_client(monkeypatch):
    monkeypatch.setenv("ICDEV_BOUNDARY_ENABLED", "true")
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    bp = create_boundary_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(bp, url_prefix="/boundary")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    return client


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def test_context_with_registered_system(bdc_client, seeded_db):
    resp = bdc_client.get("/boundary/api/impact/context?project_id=" + _PROJECT_ID)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["empty"] is False
    assert data["message"] == ""
    assert len(data["systems"]) == 1
    assert data["systems"][0]["id"] == _SYSTEM_ID
    assert data["systems"][0]["impact_level"] == "IL5"
    req_ids = {r["id"] for r in data["requirements"]}
    assert {_REQ_GREEN, _REQ_RED} <= req_ids
    # label is derived (non-empty) for the picker
    assert all(r["label"] for r in data["requirements"])


def test_context_empty_state(bdc_client, seeded_db):
    # A project with no registered ATO system -> clean empty-state, not a 500.
    resp = bdc_client.get("/boundary/api/impact/context?project_id=no-such-project")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["empty"] is True
    assert "register via intake" in data["message"].lower()
    assert data["systems"] == []


def test_context_requires_auth(seeded_db):
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    app = Flask(__name__)
    app.secret_key = "s"
    app.register_blueprint(create_boundary_blueprint(), url_prefix="/boundary")
    resp = app.test_client().get("/boundary/api/impact/context?project_id=" + _PROJECT_ID)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Assess
# ---------------------------------------------------------------------------

def test_assess_returns_tier_fields(bdc_client, seeded_db):
    resp = bdc_client.post(
        "/boundary/api/impact/assess",
        json={"system_id": _SYSTEM_ID, "requirement_id": _REQ_GREEN},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["impact_tier"] in ("GREEN", "YELLOW", "ORANGE", "RED")
    assert data["impact_tier"] == "GREEN"
    assert isinstance(data["impact_score"], (int, float))
    assert "affected_controls" in data
    assert data["remediation_steps"]
    assert data["assessment_id"]
    assert data["project_id"] == _PROJECT_ID


def test_assess_red_tier(bdc_client, seeded_db):
    resp = bdc_client.post(
        "/boundary/api/impact/assess",
        json={"system_id": _SYSTEM_ID, "requirement_id": _REQ_RED},
    )
    assert resp.status_code == 200
    assert resp.get_json()["impact_tier"] == "RED"


def test_assess_missing_params(bdc_client, seeded_db):
    resp = bdc_client.post("/boundary/api/impact/assess", json={"system_id": _SYSTEM_ID})
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"].lower()


def test_assess_unknown_system(bdc_client, seeded_db):
    resp = bdc_client.post(
        "/boundary/api/impact/assess",
        json={"system_id": "sys-nope", "requirement_id": _REQ_GREEN},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Alternatives
# ---------------------------------------------------------------------------

def test_alternatives_for_red_assessment(bdc_client, seeded_db):
    assess = bdc_client.post(
        "/boundary/api/impact/assess",
        json={"system_id": _SYSTEM_ID, "requirement_id": _REQ_RED},
    ).get_json()
    assert assess["impact_tier"] == "RED"

    resp = bdc_client.post(
        "/boundary/api/impact/alternatives",
        json={"assessment_id": assess["assessment_id"]},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["original_tier"] == "RED"
    assert len(data["alternatives"]) >= 1
    first = data["alternatives"][0]
    assert "approach_name" in first
    assert "boundary_tier_after" in first
    assert "feasibility_score" in first


def test_alternatives_refused_for_non_red(bdc_client, seeded_db):
    assess = bdc_client.post(
        "/boundary/api/impact/assess",
        json={"system_id": _SYSTEM_ID, "requirement_id": _REQ_GREEN},
    ).get_json()
    assert assess["impact_tier"] == "GREEN"

    resp = bdc_client.post(
        "/boundary/api/impact/alternatives",
        json={"assessment_id": assess["assessment_id"]},
    )
    assert resp.status_code == 400
    assert "red" in resp.get_json()["error"].lower()


def test_alternatives_missing_id(bdc_client, seeded_db):
    resp = bdc_client.post("/boundary/api/impact/alternatives", json={})
    assert resp.status_code == 400
