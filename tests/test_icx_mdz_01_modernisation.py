# CUI // SP-CTI
"""Modernisation joins the five-rules audit (icx-mdz-01).

Covers, feature by feature:
  * 7R: the assess route runs the assessor for an application; recording the strategy
    ICDEV proposes is a person's declared step (decl-wft-mdz-7r-strategy), its record is
    the decided step, and it is a proposal, never binding;
  * AI-Ify: a promotion decision is the person's step before aiify_hitl_decisions is
    written (fail closed); Send to Kanban still fails closed; scan deletion is a step;
  * Process-Ify: an observed draft's review is a step, and the reviewer is that person;
  * waves and CAM: the page's routes read the waves and the jobs; approving a job's output
    is a step bound to its files, taken once;
  * cloud-native and the exporter: the section and the bundle are read, never written; a
    decisions export is trusted only against the pinned JWKS;
  * the modernisation twin finds what it should, each finding with a thread, and replays;
  * the MCP tools are registered read-only; the pages call routes by endpoint id; the
    committed audit snapshot has all six features meeting all five rules.

Run: pytest tests/test_icx_mdz_01_modernisation.py -v
"""
from __future__ import annotations

import base64
import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

from tests._schema_compat import ensure_table, ensure_tables

ROOT = Path(__file__).resolve().parents[1]
PM = {"id": "u-pm", "role": "pm"}
DEV = {"id": "u-dev", "role": "developer"}
COR = {"id": "u-cor", "role": "cor"}
FEATURES = ("mdz.seven_r", "mdz.aiify", "mdz.processify", "mdz.waves_cam", "mdz.cloud_native",
            "mdz.exporter")

# The tables the routes read, with the schema of record's NOT NULL columns and keys
# (tools/db/schema/pg_consolidated.sql); only the columns these tests use besides.
LEGACY_DDL = (
    "CREATE TABLE IF NOT EXISTS legacy_applications (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, source_path TEXT NOT NULL, primary_language TEXT NOT NULL, language_version TEXT, framework TEXT, analysis_status TEXT DEFAULT 'registered')",
    "CREATE TABLE IF NOT EXISTS legacy_components (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, name TEXT NOT NULL, component_type TEXT NOT NULL, file_path TEXT NOT NULL, qualified_name TEXT)",
    "CREATE TABLE IF NOT EXISTS legacy_dependencies (id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_app_id TEXT NOT NULL, source_component_id TEXT NOT NULL, target_component_id TEXT, dependency_type TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS legacy_apis (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, component_id TEXT, method TEXT, path TEXT NOT NULL, handler_function TEXT)",
    "CREATE TABLE IF NOT EXISTS legacy_db_schemas (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, db_type TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL, data_type TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS migration_assessments (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, component_id TEXT, assessment_scope TEXT DEFAULT 'application', rehost_score REAL DEFAULT 0.0, replatform_score REAL DEFAULT 0.0, refactor_score REAL DEFAULT 0.0, rearchitect_score REAL DEFAULT 0.0, repurchase_score REAL DEFAULT 0.0, retire_score REAL DEFAULT 0.0, retain_score REAL DEFAULT 0.0, recommended_strategy TEXT, cost_estimate_hours REAL, risk_score REAL DEFAULT 0.0, timeline_weeks INTEGER, ato_impact TEXT, tech_debt_reduction REAL DEFAULT 0.0, scoring_weights TEXT, evidence TEXT, assessed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS migration_plans (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, plan_name TEXT NOT NULL, strategy TEXT NOT NULL, migration_approach TEXT, target_architecture TEXT, status TEXT DEFAULT 'draft', created_at TEXT)",
)

STUDIO_WORKFLOWS_DDL = "CREATE TABLE IF NOT EXISTS studio_workflows (workflow_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, template_yaml TEXT NOT NULL, category TEXT, created_by TEXT, created_at TEXT, updated_at TEXT, version INTEGER DEFAULT 1, shared INTEGER DEFAULT 0, classification TEXT NOT NULL DEFAULT 'CUI', tenant_id TEXT)"


def _app(*blueprints, user=None):
    app = Flask(__name__, template_folder=str(ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""),
                              "classification": "CUI"}

    for bp, kw in blueprints:
        app.register_blueprint(bp, **kw)
    return app.test_client()


def _sql(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture()
def legacy_db(wf_db, monkeypatch):
    """The platform DB with the wf_ tables, plus one legacy application of three components."""
    conn = sqlite3.connect(str(wf_db))
    try:
        ensure_tables(conn, LEGACY_DDL)
        conn.execute("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language, "
                     "framework) VALUES ('app-1', 'proj-1', 'Shop', '/src/shop', 'java', 'struts')")
        for cid, name in (("c1", "Cart"), ("c2", "Catalog"), ("c3", "Order")):
            conn.execute("INSERT INTO legacy_components (id, legacy_app_id, name, component_type, file_path, "
                         "qualified_name) VALUES (?, 'app-1', ?, 'module', 'x.java', ?)",
                         (cid, name, f"shop.{name}"))
        conn.execute("INSERT INTO legacy_dependencies (legacy_app_id, source_component_id, target_component_id, "
                     "dependency_type) VALUES ('app-1', 'c3', 'c1', 'import')")
        conn.execute("INSERT INTO migration_assessments (id, legacy_app_id, recommended_strategy, assessed_at) "
                     "VALUES ('ma-1', 'app-1', 'rehost', '2026-11-01T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()
    api = importlib.import_module("tools.dashboard.api.migration")
    monkeypatch.setattr(api, "DB_PATH", Path(wf_db))
    return wf_db


def _migration_client(user):
    from tools.dashboard.api.migration import migration_api

    return _app((migration_api, {"name": "migration_api_legacy"}), user=user)


# ── 7R ───────────────────────────────────────────────────────────────────────


def test_the_assess_route_runs_the_assessor_for_one_application(legacy_db, monkeypatch):
    calls = []
    sra = importlib.import_module("tools.modernization.seven_r_assessor")
    monkeypatch.setattr(sra, "run_seven_r_assessment", lambda p, a: calls.append((p, a)) or {
        "id": "ma-x", "legacy_app_id": a, "project_id": p, "recommended_strategy": "rehost", "ranking": []})
    client = _migration_client(PM)
    assert client.post("/api/migration/assess", json={"project_id": "proj-1"}).status_code == 400
    resp = client.post("/api/migration/assess", json={"project_id": "proj-1", "app_id": "app-1"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["recommended_strategy"] == "rehost" and calls == [("proj-1", "app-1")]


def test_recording_a_7r_strategy_is_a_persons_step_and_only_a_proposal(legacy_db):
    body = {"decision": "record", "strategy": "replatform", "reason": "the platform team runs Tomcat"}
    assert _migration_client(None).post("/api/migration/assessments/ma-1/strategy",
                                        json=body).status_code == 401
    refused = _migration_client(DEV).post("/api/migration/assessments/ma-1/strategy", json=body)
    assert refused.status_code == 403 and refused.get_json()["workflow_refused"] is True
    client = _migration_client(PM)
    assert client.post("/api/migration/assessments/ma-1/strategy",
                       json={**body, "strategy": "lift"}).status_code == 400
    assert client.post("/api/migration/assessments/ma-1/strategy",
                       json={"decision": "decline", "reason": ""}).status_code == 400
    assert client.post("/api/migration/assessments/nope/strategy", json=body).status_code == 404

    resp = client.post("/api/migration/assessments/ma-1/strategy", json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    out = resp.get_json()
    assert out["decision_step"]["template_id"] == "decl-wft-mdz-7r-strategy"
    assert out["strategy_record"]["binding"] is False and "may-plan" in out["strategy_record"]["means"]

    listed = client.get("/api/migration/assessments").get_json()
    rec = listed["assessments"][0]["strategy_record"]
    assert listed["records_error"] is None
    assert rec["strategy"] == "replatform" and rec["recommended"] == "rehost" and rec["by"] == "u-pm"
    rows = _sql(legacy_db, "SELECT event, surface, subject FROM wf_decision_steps "
                           "WHERE template_id = 'decl-wft-mdz-7r-strategy'")
    assert rows == [("decided", "migration_api_legacy.record_strategy", "ma-1")]


# ── AI-Ify ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def aiify(wf_db, tmp_path, monkeypatch):
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))
    import tools.aiify.blueprint as bp
    import tools.aiify.db.init_db as init_db

    monkeypatch.setattr(init_db, "DB_PATH", tmp_path / "aiify_canvas.db")
    monkeypatch.setattr(bp, "_INIT_DONE", False)
    return bp, tmp_path / "aiify_canvas.db"


def _decisions(db):
    return _sql(db, "SELECT source_type, source_id, decision, actor FROM aiify_hitl_decisions")


def test_an_aiify_promotion_is_the_persons_step_before_anything_is_written(aiify, monkeypatch):
    bp, db = aiify
    body = {"source_type": "prd", "source_id": "rm-1:P1", "phase_id": "P1", "decision": "accept"}
    refused = _app((bp.aiify_bp, {}), user=COR).post("/ai-ify/api/hitl-decision", json=body)
    assert refused.status_code == 403 and refused.get_json()["workflow_refused"] is True
    assert _decisions(db) == []
    client = _app((bp.aiify_bp, {}), user=PM)
    assert client.post("/ai-ify/api/hitl-decision", json={**body, "decision": "reject"}).status_code == 400

    resp = client.post("/ai-ify/api/hitl-decision", json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["decision_step"]["template_id"] == "decl-wft-mdz-aiify-promote"
    assert _decisions(db) == [("prd", "rm-1:P1", "accept", "u-pm")]
    # The key Send to Kanban reads: <roadmap_id>:<phase_id>.
    assert bp._load_prd_hitl_decisions("rm-1") == {"P1": "accept"}


def test_an_aiify_promotion_that_cannot_be_recorded_changes_nothing(aiify, monkeypatch):
    bp, db = aiify
    declared = importlib.import_module("tools.workflow_hitl.declared")

    def _down(*a, **k):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(declared, "_record_decision", _down)
    resp = _app((bp.aiify_bp, {}), user=PM).post(
        "/ai-ify/api/hitl-decision",
        json={"source_type": "prd", "source_id": "rm-1:P1", "decision": "accept"})
    assert resp.status_code == 503 and resp.get_json()["code"] == "store_unavailable"
    assert _decisions(db) == []


def test_send_to_kanban_still_fails_closed_when_decisions_cannot_be_read(aiify, monkeypatch):
    bp, _db = aiify
    client = _app((bp.aiify_bp, {}), user=PM)
    client.get("/ai-ify/api/posture-summary")   # runs the blueprint's one-shot init
    conn = bp._conn()
    try:
        conn.execute("INSERT INTO aiify_scans (scan_id, input_type, input_ref, status) VALUES (7, 'local_path', '/x', 'completed')")
        conn.execute("INSERT INTO aiify_roadmaps (roadmap_id, scan_id, title, phases) VALUES ('rm-7', 7, 't', '[]')")
        conn.commit()
    finally:
        conn.close()

    def _broken(_rid):
        raise RuntimeError("decisions unreadable")

    monkeypatch.setattr(bp, "_load_prd_hitl_decisions", _broken)
    resp = client.post("/ai-ify/api/send-to-kanban", json={"roadmap_id": "rm-7", "phase_id": "P1"})
    assert resp.status_code == 503 and resp.get_json()["gate_unavailable"] is True


def test_deleting_aiify_scans_is_a_persons_step(aiify):
    bp, _db = aiify
    resp = _app((bp.aiify_bp, {}), user=PM).delete("/ai-ify/api/scan/all", json={"reason": "fixtures"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    step = resp.get_json()["decision_step"]
    assert step["template_id"] == "decl-wft-mdz-aiify-scan-delete" and step["decided_by"] == "u-pm"


# ── Process-Ify ──────────────────────────────────────────────────────────────


def _observed_capture():
    return {"status": "ok", "kind": "process", "scenario": {"executable": "/opt/app/bin/app"},
            "provenance": {"target_sha256": "ab" * 32, "authorization_basis": "owned"},
            "record": {"evidence_id": "ev-1",
                       "normalized_result": {"steps": [{"label": "open the order"},
                                                       {"label": "price the order"}]}}}


def test_reviewing_an_observed_draft_is_a_persons_step_and_names_them(wf_db, monkeypatch):
    op = importlib.import_module("tools.workflow_canvas.observed_processify")
    bpmod = importlib.import_module("tools.workflow_canvas.blueprint")
    monkeypatch.setattr(op, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(bpmod, "_INIT_DONE", True)
    conn = sqlite3.connect(str(wf_db))
    try:
        ensure_table(conn, STUDIO_WORKFLOWS_DDL)
        conn.commit()
    finally:
        conn.close()
    draft = op.build_draft(_observed_capture())
    assert [s["title"] for s in draft["steps"]] == ["open the order", "price the order"]
    wid = op.save_draft(draft)
    client = _app((bpmod.create_wfc_blueprint(), {}), user=PM)

    listed = client.get("/workflow-canvas/api/workflows/observed-drafts?state=pending_hitl").get_json()
    assert [d["workflow_id"] for d in listed["drafts"]] == [wid]
    assert client.post(f"/workflow-canvas/api/workflows/{wid}/observed-review",
                       json={"decision": "reject", "note": ""}).status_code == 400
    resp = client.post(f"/workflow-canvas/api/workflows/{wid}/observed-review",
                       json={"decision": "approve", "reviewer": "someone else"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    out = resp.get_json()
    assert out["review"]["reviewer"] == "u-pm"
    assert out["decision_step"]["template_id"] == "decl-wft-mdz-processify-observed-approve"
    again = client.post(f"/workflow-canvas/api/workflows/{wid}/observed-review", json={"decision": "approve"})
    assert again.status_code == 409
    assert _sql(wf_db, "SELECT COUNT(*) FROM wf_decision_steps WHERE subject = ?", (wid,)) == [(1,)]


# ── Waves, CAM jobs, cloud-native, the exporter ──────────────────────────────


@pytest.fixture()
def canvas(legacy_db, tmp_path, monkeypatch):
    """The migration canvas DB on SQLite, and a CAM project for app-1 with one completed job."""
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "mc.db"))
    monkeypatch.setenv("CAM_ARTIFACTS_DIR", str(tmp_path / "cam"))
    monkeypatch.setenv("ICDEV_MIGRATION_CANVAS_ENABLED", "true")
    # The canvas's own login wrapper wants a session; the person is _app's g.current_user.
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_ICOS_JWKS", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.java").write_text('class App { String url = System.getenv("DB_URL"); }\n', encoding="utf-8")
    _sql(legacy_db, "UPDATE legacy_applications SET source_path = ? WHERE id = 'app-1'", (str(src),))
    from tools.migration_canvas.blueprint import create_migration_blueprint
    from tools.modernization import cloud_native_jobs

    rep = cloud_native_jobs.run("app-1")
    return create_migration_blueprint(), rep


def _canvas_client(bp, user=PM):
    return _app((bp, {"url_prefix": "/migration-canvas"}), user=user)


def test_the_modernisation_page_reads_apps_waves_and_the_bundle(canvas):
    bp, _rep = canvas
    client = _canvas_client(bp)
    apps = client.get("/migration-canvas/api/modernisation/apps").get_json()["apps"]
    assert [(a["id"], a["components"], a["assessment"]["recommended_strategy"]) for a in apps] == [
        ("app-1", 3, "rehost")]
    waves = client.get("/migration-canvas/api/modernisation/apps/app-1/waves").get_json()
    assert [w["components"] for w in waves["waves"]] == [["shop.Cart", "shop.Catalog"], ["shop.Order"]]
    assert client.get("/migration-canvas/api/modernisation/apps/nope/waves").status_code == 404
    bundle = client.get("/migration-canvas/api/modernisation/apps/app-1/bundle").get_json()
    assert bundle["valid"] is True and bundle["schema"] == "icos-apptwin/v1"
    assert bundle["sections"]["cloud_native"] is True and "seven_r" in bundle["unverified"]
    page = client.get("/migration-canvas/api/modernisation/apps/app-1/refactor-jobs").get_json()
    assert page["project_id"] == "mcp-app-1" and page["jobs"] == []   # only cloud-native jobs ran


def test_cloud_native_reads_the_jobs_and_the_section_without_running_any(canvas, monkeypatch):
    bp, rep = canvas
    cam = importlib.import_module("tools.migration_canvas.cam_refactor_engine")
    monkeypatch.setattr(cam, "run_job", lambda *a, **k: pytest.fail("a read ran a job"))
    out = _canvas_client(bp).get("/migration-canvas/api/modernisation/apps/app-1/cloud-native").get_json()
    assert sorted(j["scaffold"] for j in out["jobs"]) == sorted(j["scaffold"] for j in rep["jobs"])
    assert out["section"] is not None and out["factors"]
    assert {f["status"] for f in out["factors"]} <= {"met", "not_met", "unverified"}
    assert all(j["review"] is None for j in out["jobs"])


def test_approving_a_cam_jobs_output_binds_its_files_and_is_taken_once(canvas, legacy_db):
    bp, rep = canvas
    job = next(j for j in rep["jobs"] if j["status"] == "completed")
    url = f"/migration-canvas/api/modernisation/refactor-jobs/{job['id']}/approve"
    assert _canvas_client(bp).post(url, json={"decision": "approve", "reason": ""}).status_code == 400
    assert _canvas_client(bp, COR).post(url, json={"decision": "approve", "reason": "x"}).status_code == 403
    resp = _canvas_client(bp).post(url, json={"decision": "approve", "reason": "the Containerfile builds"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    review = resp.get_json()["review"]
    assert review["approved"] is True and review["by"] == "u-pm"
    assert review["artefacts"] and all(len(a["sha256"]) == 64 for a in review["artefacts"])
    assert _canvas_client(bp).post(url, json={"decision": "reject", "reason": "x"}).status_code == 409
    assert _canvas_client(bp).post("/migration-canvas/api/modernisation/refactor-jobs/nope/approve",
                                   json={"decision": "approve", "reason": "x"}).status_code == 404
    cn = _canvas_client(bp).get("/migration-canvas/api/modernisation/apps/app-1/cloud-native").get_json()
    assert next(j for j in cn["jobs"] if j["id"] == job["id"])["review"]["decision"] == "approve"


def _sign(doc: dict, key, kid: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from tools.modernization import icos_decisions as icd

    protected = icd._b64u(json.dumps({"alg": icd.ALG, "typ": icd.TYP, "kid": kid}).encode())
    der = key.sign(f"{protected}.{icd._b64u(icd.jcs(doc).encode())}".encode(), ec.ECDSA(hashes.SHA384()))
    r, s = decode_dss_signature(der)
    return {"document": doc, "signature": {"protected": protected,
                                           "signature": icd._b64u(r.to_bytes(48, "big") + s.to_bytes(48, "big"))}}


def _jwks(key, kid):
    nums = key.public_key().public_numbers()
    b = lambda n: base64.urlsafe_b64encode(n.to_bytes(48, "big")).rstrip(b"=").decode()  # noqa: E731
    return {"keys": [{"kty": "EC", "crv": "P-384", "kid": kid, "x": b(nums.x), "y": b(nums.y)}]}


def test_a_decisions_export_is_trusted_only_against_the_pinned_jwks(canvas, tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    bp, _rep = canvas
    url = "/migration-canvas/api/modernisation/icos-decisions/check"
    doc = {"schema": "icos-portfolio-decisions/v1", "issuer": "icos-test", "chain": {"verifies": True},
           "strategies": [{"application": "app-1", "strategy": "replatform", "at": 1, "item": "i-1",
                           "accepted_by": "mo", "decision": "d-1"}]}
    pinned, other = ec.generate_private_key(ec.SECP384R1()), ec.generate_private_key(ec.SECP384R1())
    client = _canvas_client(bp)
    unpinned = client.post(url, json={"app": "app-1", "export": _sign(doc, pinned, "k1")})
    assert unpinned.status_code == 409 and unpinned.get_json()["status"] == "untrusted"

    jwks = tmp_path / "icos_jwks.json"
    jwks.write_text(json.dumps(_jwks(pinned, "k1")), encoding="utf-8")
    monkeypatch.setenv("ICDEV_ICOS_JWKS", str(jwks))
    forged = client.post(url, json={"app": "app-1", "export": _sign(doc, other, "k1")})
    assert forged.status_code == 409 and "does not verify" in forged.get_json()["error"]
    ok = client.post(url, json={"app": "app-1", "strategy": "rehost", "export": _sign(doc, pinned, "k1")})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    out = ok.get_json()
    assert out["may_plan"]["allowed"] is False and "replatform" in out["may_plan"]["reason"]
    assert out["may_retire"]["allowed"] is False


# ── The twin ─────────────────────────────────────────────────────────────────


def _evidence(**over):
    base = {"canvas": "mdc", "target_id": "fleet", "observed_at": "2026-11-15T00:00:00+00:00",
            "policy": {"strategy_record_warn_days": 14, "job_review_warn_days": 7,
                       "draft_review_warn_days": 7},
            "assessments": [{"id": "ma-1", "app": "app-1", "recommended": "rehost",
                             "at": "2026-10-01T00:00:00+00:00"},
                            {"id": "ma-2", "app": "app-2", "recommended": "retain",
                             "at": "2026-10-01T00:00:00+00:00"}],
            "strategy_records": [{"subject": "ma-2", "decision": "record", "affirmative": True,
                                  "strategy": "retire", "step_id": "step-2", "at": "2026-10-02T00:00:00+00:00"}],
            "plans": [{"id": "mp-1", "app": "app-1", "strategy": "rehost", "status": "draft",
                       "at": "2026-10-03T00:00:00+00:00"},
                      {"id": "mp-2", "app": "app-2", "strategy": "refactor", "status": "draft",
                       "at": "2026-10-03T00:00:00+00:00"}],
            "aiify_decisions": [{"id": "5", "subject": "prd:rm-1:P1", "decision": "accept",
                                 "at": "2026-10-04T00:00:00+00:00"}],
            "aiify_steps": [],
            "drafts": [{"id": "wfl-1", "state": "pending_hitl", "at": "2026-10-01T00:00:00+00:00"}],
            "jobs": [{"id": "job-1", "project_id": "mcp-app-1", "type": "code_scaffold",
                      "scaffold": "container_definition", "at": "2026-10-01T00:00:00+00:00", "files": 2,
                      "missing": ["Containerfile"]}],
            "job_reviews": [],
            "schema": {"path": "context/icos/apptwin.schema.json", "sha256": "a" * 64, "pinned": "a" * 64},
            "errors": {}}
    base.update(over)
    return base


def test_the_twin_finds_each_gap_with_its_thread():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    twin = ModernisationGovernanceTwin()
    found = {(f["rule_id"], f["thread_id"]) for f in twin.judge(_evidence())["findings"]}
    assert found == {
        ("mdz.seven_r.unrecorded", "mdz_assessment:ma-1"),
        ("mdz.seven_r.plan_unrecorded", "migration_plan:mp-1"),
        ("mdz.seven_r.plan_diverges", "migration_plan:mp-2"),
        ("mdz.aiify.undeclared_decision", "aiify_decision:5"),
        ("mdz.processify.observed_pending", "studio_workflow:wfl-1"),
        ("mdz.cam.output_missing", "mc_refactor_job:job-1"),
        ("mdz.cam.unreviewed", "mc_refactor_job:job-1"),
    }
    changed = twin.judge(_evidence(schema={"path": "context/icos/apptwin.schema.json", "sha256": "b" * 64,
                                           "pinned": "a" * 64}))
    assert any(f["rule_id"] == "mdz.exporter.contract_changed" and f["thread_id"].startswith("config:")
               for f in changed["findings"])
    # Pure: the same evidence, the same verdict.
    assert twin.judge(_evidence()) == twin.judge(_evidence())


def test_an_unreadable_source_is_unverified_never_a_pass():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    twin = ModernisationGovernanceTwin()
    env = twin.envelope(_evidence(jobs=None, errors={"jobs": "mc_refactor_jobs unreadable"},
                                  assessments=[], plans=[], aiify_decisions=[], drafts=[]))
    assert env["verdict"] != "pass" and env["coverage_score"] < 1.0
    assert all(v.get("thread_id") for v in env["violations"])


def test_the_twin_observes_records_and_replays(legacy_db):
    from tools.twin_core import governance

    adapter = governance.adapter_for("mdc")
    assert set(adapter.observes) == set(FEATURES)
    env = governance.observe(adapter)
    assert env["extra"]["recorded"] is True
    assert any(v["thread_id"] == "mdz_assessment:ma-1" for v in env["violations"])
    replayed = governance.replay(adapter, env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]
    thread = governance.resolve_thread("mdz_assessment:ma-1")
    assert thread["resolved"] is True and thread["record"]["legacy_app_id"] == "app-1"


# ── AI-native, GUI, the audit ────────────────────────────────────────────────


MCP_TOOLS = ("modernisation_seven_r", "aiify_promotions", "processify_observed_drafts",
             "modernisation_waves_jobs", "modernisation_cloud_native", "modernisation_bundle")


def test_the_modernisation_mcp_tools_are_registered_read_only(legacy_db):
    from tools.mcp import gap_handlers
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    registered = set(list_tools())
    for name in MCP_TOOLS:
        assert name in registered and READ_ONLY_DECLARATIONS[name] is True
        assert tool_authorization(name)["tier"] == "read", name
    assert gap_handlers.handle_modernisation_waves_jobs({}) == {"error": "app_id is required"}
    seven_r = gap_handlers.handle_modernisation_seven_r({"app_id": "app-1"})
    assert [a["id"] for a in seven_r["assessments"]] == ["ma-1"]
    assert seven_r["assessments"][0]["strategy_record"] is None and "may-plan" in seven_r["record_means"]


PAGES = {
    "tools/dashboard/templates/migration.html": ("migration_api_legacy.record_strategy",
                                                  "migration_api_legacy.trigger_assessment"),
    "tools/dashboard/templates/aiify/page.html": ("aiify.api_hitl_decision", "aiify.api_delete_scan",
                                                  "aiify.api_delete_all_scans", "aiify.api_send_to_kanban"),
    "tools/dashboard/templates/workflow_canvas/processify.html": ("workflow_canvas.api_observed_review",
                                                                  "workflow_canvas.api_save_processify_workflow"),
    "tools/dashboard/templates/migration_canvas/modernisation.html": (
        "migration_canvas.api_refactor_job_approve", "migration_canvas.api_modernisation_icos_check",
        "migration_canvas.api_modernisation_bundle", "migration_canvas.api_modernisation_cloud_native"),
}


def test_the_pages_call_their_routes_by_endpoint_id():
    from tools.conformance.feature_audit import refs_in

    for rel, endpoints in PAGES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "/api/" not in text, rel
        assert set(endpoints) <= refs_in(text), rel
        mirror = ROOT / "icdev" / rel
        assert mirror.read_text(encoding="utf-8") == text, f"{rel} is not mirrored to icdev/"
    base = (ROOT / "tools/dashboard/templates/base.html").read_text(encoding="utf-8")
    assert 'href="/migration"' in base and 'href="/migration-canvas/modernisation"' in base


def test_the_committed_audit_has_every_modernisation_feature_meeting_all_five_rules():
    from tools.conformance.feature_audit import RULES, load_config, load_snapshot

    cfg = load_config()
    assert cfg["enforcement"] == "fail" and int(cfg["census_max"]) == 0
    report = load_snapshot()
    features = {f["key"]: f for f in report["features"]}
    for key in FEATURES:
        cells = features[key]["cells"]
        assert all(cells[r]["status"] in ("pass", "n/a") for r in RULES), (key, {r: cells[r]["gaps"] for r in RULES})
        assert not any(c.get("reason") for c in cells.values()), key
    for row in (r for k in FEATURES for r in features[k]["cells"]["gui"]["routes"]):
        if row["write"] and row["by_id"]:
            assert row["observed"], f"{row['endpoint']} is credited to a page no e2e run saw send it"
