#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-inert-01 — the ATO package generator is real, and it is the ONE packager.

Three things this pins, each of which was inert before this card:

  1. ``tools.compliance.ato_packager.generate_package`` EXISTS, so
     ``POST /api/ato-package/generate`` stops answering 501.
  2. It produces an actual ZIP holding actual evidence — a package that
     contains only a cover sheet is not a package.
  3. The AADC accreditation builder and the ATO packager share ONE zip
     primitive. A second packager forking is the defect the card names, so
     the delegation is asserted structurally, not assumed.
"""

import io
import json
import sqlite3
import zipfile

import pytest


# ---------------------------------------------------------------------------
# Fixture: a self-contained project database. Never the ambient icdev.db —
# a bare get_connection() in a test poisons the checkout it runs in.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE ssp_documents (
    id TEXT PRIMARY KEY, project_id TEXT, version TEXT, system_name TEXT,
    system_boundary TEXT, authorization_type TEXT, status TEXT,
    approved_by TEXT, approved_at TEXT, classification TEXT, created_at TEXT
);
CREATE TABLE project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, control_id TEXT,
    implementation_status TEXT, implementation_description TEXT,
    responsible_role TEXT, evidence_path TEXT, last_assessed TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, severity TEXT,
    status TEXT, milestone_date TEXT, weakness TEXT
);
CREATE TABLE stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, severity TEXT,
    status TEXT, finding_id TEXT
);
CREATE TABLE cato_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, status TEXT,
    evidence_type TEXT
);
CREATE TABLE sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, format TEXT,
    created_at TEXT
);
"""


@pytest.fixture()
def project_db(tmp_path):
    """A populated project database at a throwaway path."""
    db = tmp_path / "ato.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO ssp_documents (id, project_id, version, system_name, "
        "system_boundary, authorization_type, status, classification, created_at) "
        "VALUES ('ssp-1','proj-1','1.0','Test System','enclave boundary',"
        "'ATO','approved','CUI','2026-01-01T00:00:00Z')"
    )
    for cid, status in [
        ("AC-1", "implemented"),
        ("AC-2", "implemented"),
        ("AU-1", "implemented"),
        ("CP-1", "implemented"),
        ("IR-1", "planned"),
    ]:
        conn.execute(
            "INSERT INTO project_controls (project_id, control_id, implementation_status) "
            "VALUES ('proj-1', ?, ?)",
            (cid, status),
        )
    conn.execute(
        "INSERT INTO poam_items (project_id, severity, status, milestone_date, weakness) "
        "VALUES ('proj-1','moderate','open','2099-01-01','Sample weakness')"
    )
    conn.execute(
        "INSERT INTO stig_findings (project_id, severity, status, finding_id) "
        "VALUES ('proj-1','CAT2','Open','V-1001')"
    )
    conn.execute(
        "INSERT INTO cato_evidence (project_id, status, evidence_type) "
        "VALUES ('proj-1','current','scan')"
    )
    conn.execute(
        "INSERT INTO sbom_records (project_id, format, created_at) "
        "VALUES ('proj-1','cyclonedx','2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# 1. The module exists and generates a real package
# ---------------------------------------------------------------------------


def test_generate_package_produces_a_readable_zip(project_db, tmp_path):
    from tools.compliance.ato_packager import generate_package

    out = tmp_path / "packages"
    result = generate_package(
        project_id="proj-1",
        package_type="initial",
        output_dir=str(out),
        db_path=str(project_db),
    )

    assert result["project_id"] == "proj-1"
    assert result["package_type"] == "initial"
    zip_path = result["zip_path"]
    assert zipfile.is_zipfile(zip_path), "generate_package did not write a real ZIP"

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert zf.testzip() is None
        assert "README.md" in names
        # A cover sheet alone is not a package.
        json_members = [n for n in names if n.endswith(".json")]
        assert len(json_members) >= 5, f"package holds too little evidence: {names}"
        for member in json_members:
            json.loads(zf.read(member).decode("utf-8"))


def test_generated_package_carries_the_projects_real_evidence(project_db, tmp_path):
    """The artifacts hold the rows in the database, not empty scaffolding."""
    from tools.compliance.ato_packager import generate_package

    result = generate_package(
        project_id="proj-1",
        output_dir=str(tmp_path / "pkg"),
        db_path=str(project_db),
    )
    with zipfile.ZipFile(result["zip_path"]) as zf:
        blob = {n: zf.read(n).decode("utf-8") for n in zf.namelist()}

    ssp = json.loads(next(v for k, v in blob.items() if "ssp" in k))
    assert any(d["system_name"] == "Test System" for d in ssp["ssp_documents"])

    controls = json.loads(next(v for k, v in blob.items() if "control" in k))
    assert controls["totals"]["total"] == 5
    assert controls["totals"]["implemented"] == 4

    poam = json.loads(next(v for k, v in blob.items() if "poam" in k))
    assert poam["total"] == 1

    readiness = json.loads(next(v for k, v in blob.items() if "readiness" in k))
    assert readiness["required_total"] == 7
    assert readiness["readiness_pct"] is not None
    assert 0 < readiness["readiness_pct"] <= 100

    assert "Test System" in blob["README.md"] or "proj-1" in blob["README.md"]
    assert "CUI" in blob["README.md"]


def test_generate_package_rejects_an_unknown_package_type(project_db, tmp_path):
    from tools.compliance.ato_packager import generate_package

    with pytest.raises(ValueError):
        generate_package(
            project_id="proj-1",
            package_type="not-a-type",
            output_dir=str(tmp_path),
            db_path=str(project_db),
        )


def test_generate_package_requires_a_project_id(tmp_path):
    from tools.compliance.ato_packager import generate_package

    with pytest.raises(ValueError):
        generate_package(project_id="", output_dir=str(tmp_path))


def test_readiness_pct_is_none_over_an_empty_denominator():
    """A perfect score over nothing assessed is what args/perfect_score_gate.yaml bans."""
    from tools.compliance.ato_packager import collect_readiness

    class _NoTables:
        def execute(self, *a, **k):  # pragma: no cover - never reached
            raise AssertionError("should not query when there are no steps")

    result = collect_readiness(_NoTables(), "proj-1", steps=[])
    assert result["readiness_pct"] is None
    assert result["required_total"] == 0


# ---------------------------------------------------------------------------
# 2. The ROUTE — the acceptance criterion, exercised through Flask
# ---------------------------------------------------------------------------


@pytest.fixture()
def package_client(project_db, tmp_path, monkeypatch):
    """A Flask test client over the real blueprint, pointed at the fixture DB."""
    from flask import Flask

    import tools.compliance.ato_packager as packager
    from tools.dashboard.api.ato_package import ato_package_api

    monkeypatch.setattr(packager, "DEFAULT_DB_PATH", project_db)
    monkeypatch.setattr(packager, "DEFAULT_OUTPUT_DIR", tmp_path / "out")

    app = Flask(__name__)
    app.register_blueprint(ato_package_api)
    return app.test_client()


def test_post_generate_returns_a_package_not_501(package_client):
    """The acceptance criterion, asserted on the wire."""
    resp = package_client.post(
        "/api/ato-package/generate",
        json={"project_id": "proj-1", "package_type": "initial"},
    )
    assert resp.status_code != 501, "the route still answers not_implemented"
    assert resp.status_code == 200, resp.get_data(as_text=True)

    body = resp.get_json()
    assert body["status"] == "success"
    result = body["result"]
    assert zipfile.is_zipfile(result["zip_path"])
    assert result["size_bytes"] > 0
    assert result["readiness_pct"] is not None
    assert "README.md" in result["artifacts"]


def test_post_generate_rejects_a_bad_package_type_with_400(package_client):
    resp = package_client.post(
        "/api/ato-package/generate",
        json={"project_id": "proj-1", "package_type": "bogus"},
    )
    assert resp.status_code == 400
    assert "bogus" in resp.get_json()["error"]


def test_post_generate_requires_a_project_id(package_client):
    resp = package_client.post("/api/ato-package/generate", json={})
    assert resp.status_code == 400


def test_status_route_and_package_agree_on_readiness(package_client, project_db, tmp_path):
    """One collector, two surfaces — the package can never contradict the API."""
    api = package_client.get("/api/ato-package/status?project_id=proj-1").get_json()
    gen = package_client.post(
        "/api/ato-package/generate", json={"project_id": "proj-1"}
    ).get_json()["result"]

    assert api["readiness_pct"] == gen["readiness_pct"]
    assert api["required_complete"] == gen["required_complete"]

    with zipfile.ZipFile(gen["zip_path"]) as zf:
        packaged = json.loads(
            zf.read(next(n for n in zf.namelist() if "readiness" in n)).decode("utf-8")
        )
    assert packaged["steps"] == api["steps"]


# ---------------------------------------------------------------------------
# 3. ONE packager — the AADC builder delegates to the generalised primitive
# ---------------------------------------------------------------------------


def test_accred_package_delegates_to_the_shared_zip_primitive(monkeypatch):
    """AADC must not carry a second zipfile implementation."""
    import tools.compliance.ato_packager as packager
    from tools.agentic_ai_canvas import accred_package

    calls = []
    real = packager.build_package_zip

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(packager, "build_package_zip", _spy)

    blob = accred_package.build_accred_zip(
        design={"id": "d1", "name": "Design One", "classification": "CUI"},
        assessment={"score": 70},
        risks=[{"id": "r1"}],
        threat_model_data={"threats": []},
        ato_data={"summary": {"ato_ready": True}},
        reg_data={},
        red_team_data={},
        exec_data={"posture_rating": "MODERATE", "combined_score": 71},
        oscal_data={"component": {}},
    )
    assert calls, "build_accred_zip no longer routes through build_package_zip"
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
    assert "README.md" in names
    assert "oscal-component-d1.json" in names
    assert "assessment-d1.json" in names


def test_accred_package_module_owns_no_zipfile_machinery():
    """Structural: the canvas builder may not re-implement the zip writer."""
    import inspect

    from tools.agentic_ai_canvas import accred_package

    src = inspect.getsource(accred_package)
    assert "zipfile.ZipFile" not in src, (
        "accred_package.py still builds its own ZIP — that is the second packager fork"
    )


def test_build_package_zip_is_subject_agnostic():
    """The primitive takes ANY system, not an AADC design."""
    from tools.compliance.ato_packager import PackageArtifact, build_package_zip

    blob = build_package_zip(
        subject={"id": "sys-9", "name": "Any System", "classification": "CUI"},
        artifacts=[PackageArtifact("evidence.json", {"a": 1}, "Some evidence")],
        title="Generic Package",
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert set(zf.namelist()) == {"README.md", "evidence.json"}
        readme = zf.read("README.md").decode("utf-8")
        assert "Any System" in readme
        assert "Some evidence" in readme
        assert json.loads(zf.read("evidence.json")) == {"a": 1}
