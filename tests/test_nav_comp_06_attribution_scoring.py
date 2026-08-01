# CUI // SP-CTI
"""Regression tests for nav-comp-06 — attribution binding + honest scoring.

From the merged nav-comp-03 audit, four P2 authenticity defects in the
compliance-menu API family:

  1. **Attribution from the request body.** ``stig_manager`` ``assessed_by`` and the
     SIPA (``/integrity``) HITL ``reviewed_by`` were taken verbatim from the POST
     body, letting any caller attribute a compliance decision to someone else.
     Both are now bound to the authenticated user (``g.current_user``); the body
     value is ignored.
  2. **AI transparency score.** ``ai_transparency/stats`` applied a canned 50/100
     ``artifact_score`` floor (an incomplete artifact set still scored 50), so a
     system with no transparency artifacts manufactured posture. The floor is
     gone (proportional presence, absent → 0) and the score is explicitly
     labelled ``method: "heuristic"``.
  3. **cATO health.** ``cato/health`` defaulted ``poam_score`` to 100.0 when POAM
     data was absent, inflating overall health at 25% weight. An unassessed POAM
     is now excluded from the composite and the remaining weights re-normalized;
     the payload marks ``poam: "not_assessed"``.
  4. **Control inheritance.** The hardcoded ``INHERITANCE_MODEL``/``CSP_PROFILES``
     reference split was surfaced as if it were an assessed posture. Responses now
     carry ``data_source: "reference_model"`` and the page badges it.

These tests are backend-agnostic (SQLite forced by conftest).
"""

import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

import tools.dashboard.api.ai_transparency as trans_mod
import tools.dashboard.api.cato as cato_mod
import tools.dashboard.api.control_inheritance as ci_mod
import tools.dashboard.api.stig_manager as stig_mod
import tools.integrity.blueprint as integ_bp
import tools.integrity.engine as integ_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client(blueprint, user=None):
    """Flask test client with only ``blueprint`` registered.

    When ``user`` is given, an app-level before_request sets ``g.current_user``
    to it (runs before view dispatch, so ``@require_role`` sees the authenticated
    user). When ``user`` is None, no credential is set — a role-gated mutation
    aborts 401.
    """
    app = Flask(__name__)
    if user is not None:
        @app.before_request
        def _inject_user():
            g.current_user = user
    app.register_blueprint(blueprint)
    return app.test_client()


def _raw(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Bug 1a — STIG assess attribution is bound to the authenticated user
# ---------------------------------------------------------------------------
def test_stig_assess_ignores_body_assessed_by_records_session_user(icdev_db, monkeypatch):
    """A body-supplied ``assessed_by`` is ignored; the DB records g.current_user."""
    conn = _raw(icdev_db)
    conn.executescript(stig_mod.CREATE_TABLE_SQL)
    conn.execute(
        "INSERT INTO stig_findings (id, project_id, stig_id, finding_id, rule_id, "
        "severity, title, status) VALUES (1,'p1','s1','f1','r1','CAT1','t','Open')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(stig_mod, "DB_PATH", Path(str(icdev_db)))
    user = {"id": "u1", "email": "real.reviewer@icdev.local",
            "display_name": "Real Reviewer", "role": "isso"}
    client = _client(stig_mod.stig_manager_api, user=user)

    resp = client.post(
        "/api/stig-manager/assess",
        json={"finding_id": 1, "status": "NotAFinding",
              "assessed_by": "attacker@evil.example", "comments": "ok"},
    )
    assert resp.status_code == 200, resp.get_json()

    conn = _raw(icdev_db)
    row = conn.execute("SELECT assessed_by, status FROM stig_findings WHERE id = 1").fetchone()
    conn.close()
    assert row["status"] == "NotAFinding"
    assert row["assessed_by"] == "real.reviewer@icdev.local", (
        "attribution must be the authenticated user, never the request body"
    )
    assert row["assessed_by"] != "attacker@evil.example"


def test_stig_assess_still_denies_unauthenticated(icdev_db, monkeypatch):
    """The mutation gate is intact — no credential means 401 (not silent write)."""
    monkeypatch.setattr(stig_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(stig_mod.stig_manager_api).post(
        "/api/stig-manager/assess",
        json={"finding_id": 1, "status": "NotAFinding", "assessed_by": "x"},
    )
    assert resp.status_code == 401


def test_stig_source_binds_actor_not_body():
    src = Path(stig_mod.__file__).read_text(encoding="utf-8")
    assert "assessed_by = _current_actor()" in src
    assert 'assessed_by = data.get("assessed_by"' not in src, (
        "assessed_by must not be read from the request body for the write"
    )


# ---------------------------------------------------------------------------
# Bug 1b — SIPA (/integrity) HITL reviewer is bound to the authenticated user
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("authorized, path_suffix", [(True, "promote"), (False, "reject")])
def test_integrity_hitl_ignores_body_reviewed_by(monkeypatch, authorized, path_suffix):
    """promote/reject record ``g.current_user`` as reviewer, not the body value."""
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")

    captured = {}

    def _fake(assessment_id, reviewed_by, reason):
        captured["assessment_id"] = assessment_id
        captured["reviewed_by"] = reviewed_by
        captured["reason"] = reason
        return {"ok": True, "assessment_id": assessment_id, "status": path_suffix}

    monkeypatch.setattr(integ_engine, "promote", _fake)
    monkeypatch.setattr(integ_engine, "reject", _fake)

    bp = integ_bp.create_integrity_blueprint()
    assert bp is not None, "blueprint must build when the feature flag is on"

    user = {"id": "u9", "email": "hitl.officer@icdev.local", "role": "isso"}
    client = _client(bp, user=user)

    resp = client.post(
        f"/api/integrity/assessment/7/{path_suffix}",
        json={"reviewed_by": "spoofed@evil.example", "reason": "r"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert captured["assessment_id"] == 7
    assert captured["reviewed_by"] == "hitl.officer@icdev.local", (
        "HITL reviewer must be the authenticated user, never the request body"
    )
    assert captured["reviewed_by"] != "spoofed@evil.example"


def test_integrity_source_reviewer_not_body():
    src = Path(integ_bp.__file__).read_text(encoding="utf-8")
    assert "reviewed_by = _reviewer()" in src
    assert 'data.get("reviewed_by")' not in src, (
        "reviewed_by must not be read from the request body"
    )


# ---------------------------------------------------------------------------
# Bug 2 — transparency score is labelled heuristic and has no 50 floor
# ---------------------------------------------------------------------------
def test_transparency_stats_heuristic_label_and_no_50_floor(icdev_db, monkeypatch):
    """One artifact type present of four → proportional 25, not the old 50 floor."""
    conn = _raw(icdev_db)
    conn.execute("CREATE TABLE ai_use_case_inventory (id INTEGER PRIMARY KEY, project_id TEXT, name TEXT)")
    conn.execute("INSERT INTO ai_use_case_inventory (project_id, name) VALUES ('icdev-platform', 'uc1')")
    conn.execute(
        "CREATE TABLE omb_m25_21_assessments (id INTEGER PRIMARY KEY, project_id TEXT, "
        "requirement_id TEXT, status TEXT)"
    )
    # 2 distinct requirements, 1 satisfied -> framework coverage 50%.
    conn.execute("INSERT INTO omb_m25_21_assessments (project_id, requirement_id, status) VALUES ('icdev-platform','r1','satisfied')")
    conn.execute("INSERT INTO omb_m25_21_assessments (project_id, requirement_id, status) VALUES ('icdev-platform','r2','open')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(trans_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(trans_mod.ai_transparency_api).get("/api/ai-transparency/stats")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()

    # Payload labels the score as an explicit heuristic.
    assert data["transparency_score_method"] == "heuristic"
    assert data.get("transparency_score_note"), "a human-readable method note is required"

    # Hand computation:
    #   framework_avg = 50.0 (1 of 2 requirements satisfied)
    #   artifact_score = 25.0 (1 of 4 artifact types present, NO 50 floor)
    #   fairness = 0
    #   score = 0.4*50 + 0.4*25 + 0.2*0 = 30.0
    assert data["transparency_score"] == 30.0
    # The old floored computation (artifact_score=50) would have been 40.0.
    assert data["transparency_score"] != 40.0, "the artificial 50 floor must be gone"


def test_transparency_source_no_50_floor():
    src = Path(trans_mod.__file__).read_text(encoding="utf-8")
    assert "else 50.0" not in src, "the 50/100 artifact_score floor must be removed"
    assert '"transparency_score_method": "heuristic"' in src


# ---------------------------------------------------------------------------
# Bug 3 — cATO health excludes an unassessed POAM and re-normalizes
# ---------------------------------------------------------------------------
def test_cato_health_excludes_absent_poam_renormalized(icdev_db, monkeypatch):
    """No poam_items table -> POAM not_assessed, excluded, weights re-normalized."""
    conn = _raw(icdev_db)
    conn.execute("CREATE TABLE cato_evidence (id INTEGER PRIMARY KEY, project_id TEXT, status TEXT)")
    conn.execute("INSERT INTO cato_evidence (project_id, status) VALUES ('p1','current')")
    conn.execute("INSERT INTO cato_evidence (project_id, status) VALUES ('p1','expired')")
    conn.execute("CREATE TABLE project_controls (id INTEGER PRIMARY KEY, project_id TEXT, control_id TEXT, implementation_status TEXT)")
    conn.execute("INSERT INTO project_controls (project_id, control_id, implementation_status) VALUES ('p1','ac-1','implemented')")
    conn.execute("INSERT INTO project_controls (project_id, control_id, implementation_status) VALUES ('p1','ac-2','implemented')")
    conn.execute("CREATE TABLE cssp_certifications (id INTEGER PRIMARY KEY, project_id TEXT, status TEXT, expiration_date TEXT)")
    conn.execute("INSERT INTO cssp_certifications (project_id, status, expiration_date) VALUES ('p1','certified','2999-01-01')")
    conn.execute("INSERT INTO cssp_certifications (project_id, status, expiration_date) VALUES ('p1','certified','2999-01-01')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(cato_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(cato_mod.cato_api).get("/api/cato/health")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()

    # evidence=50, control=100, cert=100; POAM absent (not assessed).
    # Re-normalized over {0.30, 0.30, 0.15}:
    #   (0.30*50 + 0.30*100 + 0.15*100) / 0.75 = (15 + 30 + 15) / 0.75 = 80.0
    assert data["health_score"] == 80.0
    # The old code scored an absent POAM as 100 at 25% weight:
    #   0.30*50 + 0.30*100 + 0.25*100 + 0.15*100 = 85.0
    assert data["health_score"] != 85.0, "an absent POAM must not inflate health"
    assert data["poam"] == "not_assessed"
    assert data["components"]["poam_resolution"] is None


def test_cato_source_marks_poam_not_assessed():
    src = Path(cato_mod.__file__).read_text(encoding="utf-8")
    assert "poam_score = 100.0" not in src, "the 100.0 perfect-if-absent default must be gone"
    assert '"not_assessed"' in src


# ---------------------------------------------------------------------------
# Bug 4 — control inheritance responses carry the reference-model marker
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/control-inheritance/csps",
    "/api/control-inheritance/model",
])
def test_control_inheritance_responses_marked_reference_model(icdev_db, monkeypatch, path):
    monkeypatch.setattr(ci_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(ci_mod.control_inheritance_api).get(path)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("data_source") == "reference_model"


def test_control_inheritance_summary_marked_reference_model(icdev_db, monkeypatch):
    monkeypatch.setattr(ci_mod, "DB_PATH", Path(str(icdev_db)))
    resp = _client(ci_mod.control_inheritance_api).get("/api/control-inheritance/summary")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("data_source") == "reference_model"


def test_control_inheritance_template_has_reference_badge():
    tpl = (Path(ci_mod.__file__).resolve().parent.parent / "templates" / "control_inheritance.html")
    html = tpl.read_text(encoding="utf-8")
    assert "Typical inheritance (reference)" in html, (
        "the consuming page must badge the reference-model posture"
    )
