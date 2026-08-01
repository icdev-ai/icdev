# CUI // SP-CTI
"""Tests for tools/boundary_canvas/oscal_cato_exporter.py — task bdr-feat-1.

The exporter is a THIN delegation over tools/compliance/oscal_generator.py. These
tests exercise:
  * artifact-type alias normalization (ssp/poam/ar/cd + long forms)
  * genuine delegation: each of the 4 artifact types produces a file on disk that
    validate_oscal accepts (schema-valid), driven off a seeded temp DB
  * the unlinked-project error path: a project_id with no `projects` row returns an
    honest {"status": "error", ...} payload (never a phantom artifact)
  * the twin.export_oscal facade delegates and stamps snapshot_id
  * the blueprint route surfaces success (200) and error (400) payloads and
    requires auth (401) — per the tests/test_bdc_cato_readiness.py route pattern.

The generator reads project/control state from the ICDEV main DB; we point it at a
temp SQLite file via db_path. Main-DB reads use PG-native `%s`, which the storage
layer translates for the SQLite fallback, so we seed through a translating
StorageConnection (tools.db.storage.get_connection).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.boundary_canvas import oscal_cato_exporter as ox

# ---------------------------------------------------------------------------
# Seed schema — only the columns the generator actually reads.
# ---------------------------------------------------------------------------

_MAIN_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    impact_level TEXT DEFAULT 'IL5',
    classification TEXT DEFAULT 'CUI',
    type TEXT DEFAULT 'webapp',
    status TEXT DEFAULT 'under-development',
    ato_status TEXT DEFAULT 'none',
    cloud_environment TEXT DEFAULT 'aws-govcloud',
    created_by TEXT DEFAULT 'ICDEV',
    directory_path TEXT DEFAULT '',
    tech_stack_backend TEXT,
    tech_stack_frontend TEXT,
    tech_stack_database TEXT
);
CREATE TABLE IF NOT EXISTS project_controls (
    project_id TEXT,
    control_id TEXT,
    implementation_status TEXT,
    implementation_description TEXT,
    responsible_role TEXT,
    evidence_path TEXT,
    last_assessed TEXT
);
CREATE TABLE IF NOT EXISTS compliance_controls (
    id TEXT PRIMARY KEY,
    family TEXT,
    title TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    weakness_id TEXT,
    weakness_description TEXT,
    severity TEXT,
    source TEXT,
    control_id TEXT,
    status TEXT DEFAULT 'open',
    corrective_action TEXT,
    milestone_date TEXT,
    responsible_party TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS fedramp_assessments (
    project_id TEXT,
    control_id TEXT,
    baseline TEXT,
    status TEXT,
    implementation_status TEXT,
    evidence_description TEXT,
    evidence_path TEXT,
    notes TEXT,
    assessment_date TEXT,
    assessor TEXT
);
CREATE TABLE IF NOT EXISTS cmmc_assessments (
    project_id TEXT,
    practice_id TEXT,
    domain TEXT,
    level INTEGER,
    status TEXT,
    evidence_description TEXT,
    evidence_path TEXT,
    notes TEXT,
    nist_171_id TEXT,
    assessment_date TEXT,
    assessor TEXT
);
CREATE TABLE IF NOT EXISTS stig_findings (
    project_id TEXT,
    stig_id TEXT,
    finding_id TEXT,
    rule_id TEXT,
    severity TEXT,
    title TEXT,
    description TEXT,
    check_content TEXT,
    fix_text TEXT,
    status TEXT,
    comments TEXT,
    target_type TEXT,
    assessed_by TEXT,
    assessed_at TEXT
);
CREATE TABLE IF NOT EXISTS sbom_records (
    project_id TEXT,
    format TEXT,
    version TEXT,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TEXT
);
"""

_PROJECT_ID = "proj-bdr-feat-1"

_ALL_TYPES = ["ssp", "poam", "assessment_results", "component_definition"]


@pytest.fixture
def seeded_db(tmp_path):
    """A temp SQLite main DB seeded with one project + a couple controls / a POA&M.

    Returns the db_path as a Path (the generator's _get_connection needs a Path).
    """
    from tools.db.storage import get_connection

    db_path = tmp_path / "icdev.db"
    conn = get_connection(db_path=str(db_path))
    conn.executescript(_MAIN_DDL)
    conn.execute(
        "INSERT INTO projects (id, name, description, impact_level, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (_PROJECT_ID, "Boundary Twin Test System", "A test ATO boundary.", "IL5", "operational"),
    )
    for cid, status in [("AC-2", "satisfied"), ("AU-6", "not_satisfied")]:
        conn.execute(
            "INSERT INTO project_controls "
            "(project_id, control_id, implementation_status, implementation_description, responsible_role) "
            "VALUES (%s, %s, %s, %s, %s)",
            (_PROJECT_ID, cid, status, f"{cid} implemented via platform baseline.", "ISSO"),
        )
    conn.execute(
        "INSERT INTO poam_items (project_id, weakness_id, weakness_description, severity, control_id, status) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (_PROJECT_ID, "W-001", "Audit review gap", "high", "AU-6", "open"),
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Alias normalization
# ---------------------------------------------------------------------------

def test_normalize_artifact_type_aliases():
    assert ox.normalize_artifact_type("ssp") == "ssp"
    assert ox.normalize_artifact_type("POAM") == "poam"
    assert ox.normalize_artifact_type("ar") == "assessment_results"
    assert ox.normalize_artifact_type("assessment-results") == "assessment_results"
    assert ox.normalize_artifact_type("cd") == "component_definition"
    assert ox.normalize_artifact_type("component_definition") == "component_definition"
    assert ox.normalize_artifact_type("bogus") is None
    assert ox.normalize_artifact_type("") is None


def test_public_delegation_functions_exist():
    for name in (
        "generate_oscal_ssp",
        "generate_oscal_poam",
        "generate_oscal_assessment_results",
        "generate_oscal_component_definition",
        "validate_oscal",
    ):
        assert callable(getattr(ox, name)), f"{name} missing/not callable"


# ---------------------------------------------------------------------------
# Delegation: each artifact type produces a schema-valid file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("artifact_type", _ALL_TYPES)
def test_export_produces_valid_file(seeded_db, tmp_path, artifact_type):
    out_dir = tmp_path / "oscal"
    result = ox.export_oscal_artifact(
        _PROJECT_ID, artifact_type=artifact_type, output_dir=str(out_dir), db_path=seeded_db
    )
    assert result["status"] == "ok", result
    assert result["artifact_type"] == artifact_type
    assert result["path"], "no path returned"
    assert Path(result["path"]).exists(), "artifact file not written"
    assert result["valid"] is True, result.get("errors")
    # validate_oscal (re-run explicitly) also accepts the produced file.
    v = ox.validate_oscal(result["path"], artifact_type)
    assert v["valid"] is True, v["errors"]


@pytest.mark.parametrize("alias,canonical", [("ar", "assessment_results"), ("cd", "component_definition")])
def test_export_accepts_ui_aliases(seeded_db, tmp_path, alias, canonical):
    result = ox.export_oscal_artifact(
        _PROJECT_ID, artifact_type=alias, output_dir=str(tmp_path / "oscal"), db_path=seeded_db
    )
    assert result["status"] == "ok"
    assert result["artifact_type"] == canonical
    assert result["valid"] is True


def test_direct_generate_ssp_delegates(seeded_db, tmp_path):
    # The thin delegation returns the generator's own rich dict (file_path/uuid/...).
    res = ox.generate_oscal_ssp(_PROJECT_ID, output_dir=str(tmp_path / "oscal"), db_path=seeded_db)
    assert Path(res["file_path"]).exists()
    assert res["validation"]["valid"] is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_unlinked_project_returns_error_payload(seeded_db, tmp_path):
    result = ox.export_oscal_artifact(
        "does-not-exist", artifact_type="ssp", output_dir=str(tmp_path / "oscal"), db_path=seeded_db
    )
    assert result["status"] == "error"
    assert result["path"] is None
    assert result["valid"] is False
    assert "not found" in result["error"].lower()


def test_unknown_artifact_type_returns_error(seeded_db, tmp_path):
    result = ox.export_oscal_artifact(
        _PROJECT_ID, artifact_type="totally-bogus", output_dir=str(tmp_path / "oscal"), db_path=seeded_db
    )
    assert result["status"] == "error"
    assert result["path"] is None
    assert "unknown" in result["error"].lower()


# ---------------------------------------------------------------------------
# twin.export_oscal facade delegates + stamps snapshot_id
# ---------------------------------------------------------------------------

def test_twin_export_oscal_delegates(monkeypatch):
    import tools.boundary_canvas.twin as twin

    captured = {}

    def _fake(project_id, artifact_type="ssp", output_dir=None, db_path=None):
        captured["project_id"] = project_id
        captured["artifact_type"] = artifact_type
        return {"status": "ok", "artifact_type": artifact_type, "path": "/x/ssp.json", "valid": True}

    monkeypatch.setattr(
        "tools.boundary_canvas.oscal_cato_exporter.export_oscal_artifact", _fake
    )
    out = twin.export_oscal("d1", "snap-1", "ssp")
    assert captured["project_id"] == "d1"
    assert out["status"] == "ok"
    assert out["path"] == "/x/ssp.json"
    assert out["snapshot_id"] == "snap-1"


# ---------------------------------------------------------------------------
# Route tests (Flask test client) — mirrors test_bdc_cato_readiness.py
# ---------------------------------------------------------------------------

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


def test_route_success_returns_path_and_valid(bdc_client, monkeypatch):
    monkeypatch.setattr(
        "tools.boundary_canvas.twin.export_oscal",
        lambda *a, **k: {"status": "ok", "artifact_type": "ssp",
                         "path": "/data/oscal/d1/ssp.oscal.json", "valid": True, "snapshot_id": None},
    )
    resp = bdc_client.get("/boundary/api/twin/d1/oscal-export?artifact_type=ssp")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["valid"] is True
    assert data["path"].endswith("ssp.oscal.json")


def test_route_unlinked_project_returns_400_error(bdc_client, monkeypatch):
    monkeypatch.setattr(
        "tools.boundary_canvas.twin.export_oscal",
        lambda *a, **k: {"status": "error", "artifact_type": "ssp", "path": None,
                         "valid": False, "error": "Project 'd1' not found in database."},
    )
    resp = bdc_client.get("/boundary/api/twin/d1/oscal-export?artifact_type=ssp")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert data["path"] is None
    assert "not found" in data["error"].lower()


def test_route_requires_auth():
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    app = Flask(__name__)
    app.secret_key = "s"
    app.register_blueprint(create_boundary_blueprint(), url_prefix="/boundary")
    resp = app.test_client().get("/boundary/api/twin/d1/oscal-export")
    assert resp.status_code == 401
