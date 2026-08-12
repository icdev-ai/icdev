# CUI // SP-CTI
"""Tests for GET /api/ace/<id>/nova-state endpoint.

Covers:
  * 200 response with at least one coworker entry when instance has coworkers.
  * trust_score is present and is a float.
  * fact_count reflects seeded ace_coworker_memory rows.
  * recent_improvements list is present (may be empty).
  * Instance with no coworkers returns empty list (not 500).
"""
from __future__ import annotations

import importlib
import sqlite3
import uuid

import pytest
from flask import Flask

from icdev.tools.ace.db.init_db import SCHEMA as ACE_SCHEMA
from tests._sql_compat import translating


# ---------------------------------------------------------------------------
# NOVA table DDL (mirrored from tools/nova/db/init_db.py)
# ---------------------------------------------------------------------------

_NOVA_SCHEMA = """
CREATE TABLE IF NOT EXISTS ace_trust_ledger (
    id              TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL,
    delta           REAL NOT NULL,
    reason          TEXT NOT NULL,
    new_score       REAL NOT NULL,
    source_task_id  TEXT NOT NULL DEFAULT '',
    recorded_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ace_coworker_memory (
    id               TEXT PRIMARY KEY,
    role_id          TEXT NOT NULL,
    fact_type        TEXT NOT NULL DEFAULT 'observation',
    content          TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.8,
    source_task_id   TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_improvement_artifacts (
    artifact_id      TEXT PRIMARY KEY,
    task_type        TEXT NOT NULL,
    skill_used       TEXT NOT NULL DEFAULT '',
    generation_n     INTEGER NOT NULL DEFAULT 1,
    improvement_text TEXT NOT NULL,
    composite_score  REAL NOT NULL DEFAULT 0.0,
    baseline_score   REAL NOT NULL DEFAULT 0.0,
    evidence_traces  TEXT NOT NULL DEFAULT '[]',
    applied_count    INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at       TEXT
);
"""


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ace_db(tmp_path):
    """Canvas DB with full ACE schema."""
    db_path = tmp_path / "ace_nova_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(ACE_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def nova_db(tmp_path):
    """Main DB with NOVA tables."""
    db_path = tmp_path / "nova_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_NOVA_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Minimal Flask test client — only mounts the ace_api blueprint
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(ace_db, nova_db, monkeypatch):
    """Minimal Flask test client with only the ACE API blueprint mounted."""

    # Patch canvas connection to the temporary ACE DB
    def _fake_canvas_conn(env_var=None):
        import sqlite3 as _sqlite3
        # Subclass allows setting _backend on Python 3.14 C extension type
        class _CanvasConn(_sqlite3.Connection):
            pass
        conn = _sqlite3.connect(str(ace_db), factory=_CanvasConn)
        conn.row_factory = None
        conn._backend = "sqlite"
        # `_backend` still resolves through TranslatingConnection.__getattr__.
        return translating(conn)

    # Patch main DB connection to the temporary NOVA DB.
    # Both fixtures hand back a translating connection so the %s -> ? rewrite the
    # runtime SQL relies on stays in the loop — a bare sqlite3 connection here
    # makes any %s query raise 'near "%": syntax error' into the blueprint's own
    # `except Exception`, which reads as a missing feature, not a broken fixture.
    def _fake_main_conn():
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(nova_db))
        conn.row_factory = None
        return translating(conn)

    _storage = importlib.import_module("icdev.tools.db.storage")
    monkeypatch.setattr(_storage, "get_canvas_connection", _fake_canvas_conn)
    monkeypatch.setattr(_storage, "get_connection", _fake_main_conn)

    # Stub init_nova_tables — tables already created in nova_db fixture
    try:
        _nova_init = importlib.import_module("tools.nova.db.init_db")
        monkeypatch.setattr(_nova_init, "init_nova_tables", lambda conn=None: {"status": "ok"})
    except ImportError:
        pass

    # Reset blueprint db_ready flag so _ensure_db uses the patched canvas conn
    _bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    _bp_mod._state["db_ready"] = False  # type: ignore[attr-defined]

    # Build a minimal Flask app — no dashboard startup, just the two ACE blueprints
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Register the ACE page blueprint (ace_bp), which auto-registers ace_api_bp
    from icdev.tools.ace.blueprint import ace_bp
    app.register_blueprint(ace_bp)
    # ace_api_bp is registered inside ace_bp.record_once(_mount_api);
    # if it already registered itself (module-level singleton), register manually.
    from icdev.tools.ace.blueprint import ace_api_bp
    if "ace_api" not in app.blueprints:
        app.register_blueprint(ace_api_bp)

    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_instance(ace_db_path: str, instance_id: str, role_ids: list) -> None:
    conn = sqlite3.connect(ace_db_path)
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state, trust_tier, created_at, updated_at) "
        "VALUES (?, ?, ?, 'active', 'yellow', ?, ?)",
        (instance_id, "test instance", role_ids[0] if role_ids else "analyst", now, now),
    )
    for role_id in role_ids:
        cw_id = f"cw-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, created_at) "
            "VALUES (?, ?, ?, ?, 'idle', ?)",
            (cw_id, instance_id, role_id, role_id, now),
        )
    conn.commit()
    conn.close()


def _seed_trust(nova_db_path: str, role_id: str, score: float) -> None:
    conn = sqlite3.connect(nova_db_path)
    conn.execute(
        "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
        "VALUES (?, ?, 0.05, 'success', ?)",
        (f"tl-{uuid.uuid4().hex[:8]}", role_id, score),
    )
    conn.commit()
    conn.close()


def _seed_memory_facts(nova_db_path: str, role_id: str, n: int) -> None:
    conn = sqlite3.connect(nova_db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO ace_coworker_memory (id, role_id, content) VALUES (?, ?, ?)",
            (f"fact-{uuid.uuid4().hex[:8]}", role_id, f"learned fact #{i}"),
        )
    conn.commit()
    conn.close()


def _seed_improvement(nova_db_path: str, task_type: str, text: str, score: float = 0.9,
                      evidence: str = "[]") -> None:
    conn = sqlite3.connect(nova_db_path)
    conn.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, improvement_text, composite_score, evidence_traces, status) "
        "VALUES (?, ?, ?, ?, ?, 'applied')",
        (f"art-{uuid.uuid4().hex[:8]}", task_type, text, score, evidence),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_nova_state_returns_200_with_coworker(client, ace_db, nova_db):
    """Seeded instance returns 200 with at least one coworker entry."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "analyst"
    _seed_instance(str(ace_db), iid, [role])
    _seed_trust(str(nova_db), role, 0.72)

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert "coworkers" in data
    assert len(data["coworkers"]) >= 1


def test_nova_state_trust_score_present(client, ace_db, nova_db):
    """trust_score is returned as a float from the ledger."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "planner"
    _seed_instance(str(ace_db), iid, [role])
    _seed_trust(str(nova_db), role, 0.65)

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    cws = resp.get_json()["coworkers"]
    roles_found = {c["role_id"]: c for c in cws}
    assert role in roles_found
    entry = roles_found[role]
    assert isinstance(entry["trust_score"], float)
    assert abs(entry["trust_score"] - 0.65) < 0.01


def test_nova_state_fact_count(client, ace_db, nova_db):
    """fact_count reflects the number of rows in ace_coworker_memory for the role."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "researcher"
    _seed_instance(str(ace_db), iid, [role])
    _seed_trust(str(nova_db), role, 0.5)
    _seed_memory_facts(str(nova_db), role, 4)

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    cws = resp.get_json()["coworkers"]
    entry = next((c for c in cws if c["role_id"] == role), None)
    assert entry is not None
    assert entry["fact_count"] == 4


def test_nova_state_recent_improvements(client, ace_db, nova_db):
    """recent_improvements list is populated from agent_improvement_artifacts."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "engineer"
    _seed_instance(str(ace_db), iid, [role])
    _seed_trust(str(nova_db), role, 0.8)
    _seed_improvement(str(nova_db), "build", "Improved test coverage by 20%")

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    cws = resp.get_json()["coworkers"]
    entry = next((c for c in cws if c["role_id"] == role), None)
    assert entry is not None
    assert isinstance(entry["recent_improvements"], list)
    assert len(entry["recent_improvements"]) >= 1
    imp = entry["recent_improvements"][0]
    assert "improvement_text" in imp
    assert "task_type" in imp


def test_nova_state_improvement_carries_its_lesson_evidence(client, ace_db, nova_db):
    """exa-refine-04 — the coworker card shows WHY a refinement was applied.

    An applied improvement whose `evidence_traces` holds a
    `refinement_evidence/v1` bundle must surface a human-readable
    `evidence_summary` naming the lesson count, dominant pattern and recurrence.
    """
    import json

    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "ai_developer"
    _seed_instance(str(ace_db), iid, [role])
    _seed_improvement(
        str(nova_db), "build", "Add a retry around the flaky migration step.",
        evidence=json.dumps({
            "schema": "refinement_evidence/v1", "lesson_count": 3,
            "recurrence_score": 0.42, "dominant_pattern": "verification_fail",
            "systemic_count": 2, "lessons": [], "patterns": [], "trace_ids": [],
        }),
    )

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    imp = resp.get_json()["coworkers"][0]["recent_improvements"][0]
    assert "3 lesson_learned row(s)" in imp["evidence_summary"]
    assert "verification_fail" in imp["evidence_summary"]
    assert "0.42" in imp["evidence_summary"]


def test_nova_state_legacy_improvement_reports_no_evidence_honestly(client, ace_db, nova_db):
    """A pre-exa-refine-04 artifact must say it has none, not fabricate one."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "ai_developer"
    _seed_instance(str(ace_db), iid, [role])
    _seed_improvement(str(nova_db), "build", "Legacy improvement.",
                      evidence='["trace-a", "trace-b"]')

    resp = client.get(f"/api/ace/{iid}/nova-state")
    imp = resp.get_json()["coworkers"][0]["recent_improvements"][0]
    assert "no lesson evidence" in imp["evidence_summary"].lower()


def test_nova_state_no_coworkers_returns_empty_list(client, ace_db, nova_db):
    """Instance with no coworkers returns empty list, not a 500."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(str(ace_db))
    now = "2026-01-01T00:00:00"
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state, trust_tier, created_at, updated_at) "
        "VALUES (?, 'empty', 'none', 'pending', 'yellow', ?, ?)",
        (iid, now, now),
    )
    conn.commit()
    conn.close()

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["coworkers"] == []


def test_nova_state_default_trust_when_no_ledger(client, ace_db, nova_db):
    """Coworker with no ledger entry gets trust_score=0.5 (INITIAL_TRUST)."""
    iid = f"ace-{uuid.uuid4().hex[:8]}"
    role = "coordinator"
    _seed_instance(str(ace_db), iid, [role])
    # No trust ledger entry seeded

    resp = client.get(f"/api/ace/{iid}/nova-state")
    assert resp.status_code == 200
    cws = resp.get_json()["coworkers"]
    entry = next((c for c in cws if c["role_id"] == role), None)
    assert entry is not None
    assert entry["trust_score"] == 0.5
