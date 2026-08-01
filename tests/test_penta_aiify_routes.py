# CUI // SP-CTI
"""penta-aiify-05 — Flask test-client coverage for AI-ify blueprint routes.

Covers every AI-ify route NOT already exercised by the auth / fail-closed /
trust suites, asserting each renders/returns without a 500:

  GET  /ai-ify/                       index page render
  POST /ai-ify/api/scan               scan (run_scan mocked)
  GET  /ai-ify/api/scan/<id>          scan detail (found + 404)
  POST /ai-ify/api/generate-prd       base (non-boosted) PRD
  POST /ai-ify/api/prd-dry-run        boost preview (LLM router mocked)
  GET  /ai-ify/api/intelligence-feed  engine signals (best-effort, empty)
  POST /ai-ify/api/hitl-decision      record + validation
  POST /ai-ify/api/iqe-query          NL->IQE (iqe pipeline mocked) + validation
  POST /ai-ify/api/run-innovation     pipeline thread (target mocked)
  GET  /ai-ify/posture                posture page render
  GET  /ai-ify/api/posture-summary    live posture + trend
  POST /ai-ify/api/posture/snapshot   persist snapshot
  GET  /ai-ify/<subpath>              aiify_compat legacy redirect

The harness mirrors the penta-aiify-04 seam: a minimal Flask app with a fake
auth before_request, the blueprint's one-shot init pointed at a temp canvas DB,
and init_db.DB_PATH restored on teardown so a later test never observes this
test's seeded rows.
"""
from __future__ import annotations

import importlib
import json

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared Flask/app harness (same seam as penta-aiify-03/04)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))

    from flask import Flask, g, request
    import tools.aiify.blueprint as bp
    import tools.aiify.db.init_db as init_db
    from tools.db.storage import get_canvas_connection

    _default_dbpath = init_db._ICDEV_ROOT / "data" / "aiify_canvas.db"
    _orig_init_done = bp._INIT_DONE
    init_db.DB_PATH = tmp_path / "aiify_canvas.db"
    bp._INIT_DONE = False
    monkeypatch.setattr(bp, "_conn", lambda: get_canvas_connection("AIIFY_DB_PATH"))
    # Keep index()/posture pages template-free in the minimal app.
    monkeypatch.setattr(bp, "render_template", lambda *a, **k: "RENDERED")

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    app.register_blueprint(bp.aiify_bp)
    app.register_blueprint(bp.aiify_compat_bp)
    try:
        yield app
    finally:
        init_db.DB_PATH = _default_dbpath
        bp._INIT_DONE = _orig_init_done


@pytest.fixture
def client(app):
    return app.test_client()


def _cx():
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("AIIFY_DB_PATH")


def _seed(phases=None):
    """Seed a completed scan + opportunity + roadmap. Returns roadmap_id."""
    import tools.aiify.db.init_db as init_db
    init_db.init_db()
    if phases is None:
        phases = [{
            "phase_id": "P1",
            "label": "P1 — Quick Wins",
            "opportunities": [{
                "opportunity_id": 1,
                "module_path": "tools/demo.py",
                "function_name": "run",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
            }],
            "total_effort_days": 3,
        }]
    conn = _cx()
    try:
        conn.execute(
            "INSERT INTO aiify_scans (scan_id, input_type, input_ref, project_summary, "
            "total_files, total_loc, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (1, "path", "tools/demo", "A demo project", 3, 120, "completed"),
        )
        conn.execute(
            "INSERT INTO aiify_opportunities (opportunity_id, scan_id, module_path, function_name, "
            "language, pattern_type, ai_paradigm, il_recommended_model) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (1, 1, "tools/demo.py", "run", "python", "hardcoded_threshold",
             "anomaly_detection", "claude-sonnet-4-6"),
        )
        conn.execute(
            "INSERT INTO aiify_scores (opportunity_id, value_score, feasibility_score, "
            "risk_score, composite_score, verdict, ai_readiness, category) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (1, 0.8, 0.7, 0.3, 0.75, "ai_ready", "ai_ready", "enhancement"),
        )
        conn.execute(
            "INSERT INTO aiify_roadmaps (scan_id, roadmap_id, title, phases, total_effort_days) "
            "VALUES (%s, %s, %s, %s, %s)",
            (1, "rm-routes01", "Demo roadmap", json.dumps(phases), 3),
        )
        conn.commit()
    finally:
        conn.close()
    return "rm-routes01"


def _hdr(role="admin"):
    return {"X-Test-Role": role}


# ─────────────────────────────────────────────────────────────────────────────
# Read-only pages
# ─────────────────────────────────────────────────────────────────────────────

def test_index_renders_empty(client):
    resp = client.get("/ai-ify/", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_index_renders_with_data(client, app):
    with app.app_context():
        _seed()
    resp = client.get("/ai-ify/", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_posture_page_renders(client):
    resp = client.get("/ai-ify/posture", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ─────────────────────────────────────────────────────────────────────────────
# Scan get / detail
# ─────────────────────────────────────────────────────────────────────────────

def test_get_scan_detail(client, app):
    with app.app_context():
        _seed()
    resp = client.get("/ai-ify/api/scan/1", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["scan"]["scan_id"] == 1
    assert len(body["opportunities"]) == 1
    assert body["roadmap"]["roadmap_id"] == "rm-routes01"


def test_get_scan_detail_404(client, app):
    with app.app_context():
        _seed()
    resp = client.get("/ai-ify/api/scan/999", headers=_hdr())
    assert resp.status_code == 404


def test_scan_post_mocked(client, monkeypatch):
    """run_scan is imported into the blueprint namespace; patch it there."""
    import tools.aiify.blueprint as bp
    monkeypatch.setattr(
        bp, "run_scan",
        lambda it, ir, ctx: {"scan_id": 42, "opportunities_count": 0, "status": "completed"},
    )
    resp = client.post(
        "/ai-ify/api/scan", headers=_hdr(),
        json={"input_type": "local_path", "input_ref": "tools/demo"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["scan_id"] == 42


# ─────────────────────────────────────────────────────────────────────────────
# PRD generation (base + dry-run boost)
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_prd_base(client, app):
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/generate-prd", headers=_hdr(),
        json={"roadmap_id": "rm-routes01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body.get("prd"), "expected markdown prd"
    assert "prd_html" in body


def test_generate_prd_missing_args_400(client, app):
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/generate-prd", headers=_hdr(),
        json={"roadmap_id": "rm-routes01"},
    )
    assert resp.status_code == 400


def _install_fake_router(monkeypatch, reply_text):
    router_mod = importlib.import_module("tools.llm.router")

    class _FakeResp:
        def __init__(self, text):
            self.content = text
            self.model_id = "fake-model"

    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, fn, req):
            return _FakeResp(reply_text)

    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)


def test_prd_dry_run_boost(client, app, monkeypatch):
    with app.app_context():
        _seed()
    _install_fake_router(monkeypatch, "IMPROVED PRD [source: OPP-1]. " + ("detail. " * 2000))
    resp = client.post(
        "/ai-ify/api/prd-dry-run", headers=_hdr(),
        json={"roadmap_id": "rm-routes01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "ai_boosted" in body


# ─────────────────────────────────────────────────────────────────────────────
# Intelligence feed + HITL decisions
# ─────────────────────────────────────────────────────────────────────────────

def test_intelligence_feed_ok(client, app):
    with app.app_context():
        _seed()
    resp = client.get("/ai-ify/api/intelligence-feed", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body.keys()) == {"innovation", "creative", "research"}


def test_hitl_decision_records(client, app):
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/hitl-decision", headers=_hdr(),
        json={"source_type": "prd", "source_id": "P1", "decision": "accept",
              "roadmap_id": "rm-routes01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["decision"] == "accept"


def test_hitl_decision_invalid_source_type_400(client, app):
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/hitl-decision", headers=_hdr(),
        json={"source_type": "bogus", "source_id": "1", "decision": "accept"},
    )
    assert resp.status_code == 400


def test_hitl_decision_invalid_decision_400(client, app):
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/hitl-decision", headers=_hdr(),
        json={"source_type": "innovation", "source_id": "1", "decision": "maybe"},
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# IQE query
# ─────────────────────────────────────────────────────────────────────────────

def test_iqe_query_missing_question_400(client):
    resp = client.post("/ai-ify/api/iqe-query", headers=_hdr(), json={})
    assert resp.status_code == 400


def test_iqe_query_happy_path_mocked(client, monkeypatch):
    import tools.iqe.nl_to_iqe as nlmod
    import tools.iqe.parser as parsermod
    import tools.iqe.executor as execmod
    monkeypatch.setattr(nlmod, "nl_to_iqe", lambda q, collections=None: "foreach x in aiify.scans")
    monkeypatch.setattr(parsermod, "parse", lambda s: object())
    monkeypatch.setattr(execmod, "execute_query", lambda ast: [{"scan_id": 1}])
    resp = client.post(
        "/ai-ify/api/iqe-query", headers=_hdr(),
        json={"question": "list all completed scans", "execute": True},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["row_count"] == 1
    assert body["iqe"] == "foreach x in aiify.scans"


# ─────────────────────────────────────────────────────────────────────────────
# run-innovation (thread target mocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_innovation_started(client, monkeypatch):
    import tools.innovation.innovation_manager as im
    monkeypatch.setattr(im, "run_full_pipeline", lambda *a, **k: None)
    resp = client.post("/ai-ify/api/run-innovation", headers=_hdr(), json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "started"


# ─────────────────────────────────────────────────────────────────────────────
# Posture summary + snapshot
# ─────────────────────────────────────────────────────────────────────────────

def test_posture_summary(client, app):
    with app.app_context():
        _seed()
    resp = client.get("/ai-ify/api/posture-summary", headers=_hdr())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "overall_score" in body
    assert "grade" in body
    assert "trend" in body


def test_posture_snapshot_persists(client, app):
    with app.app_context():
        _seed()
    resp = client.post("/ai-ify/api/posture/snapshot", headers=_hdr(), json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "ok"
    assert "overall_score" in body["posture"]
    # Snapshot row persisted
    with app.app_context():
        conn = _cx()
        try:
            n = conn.execute("SELECT COUNT(*) FROM aiify_posture_snapshots").fetchone()[0]
        finally:
            conn.close()
    assert n == 1


# ─────────────────────────────────────────────────────────────────────────────
# aiify_compat legacy redirect
# ─────────────────────────────────────────────────────────────────────────────

def test_aiify_compat_redirect(client):
    resp = client.get("/ai-augmentation/some/path", headers=_hdr())
    assert resp.status_code in (301, 302, 308)
    assert "/ai-ify/" in resp.headers.get("Location", "")
