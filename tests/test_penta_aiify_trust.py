# CUI // SP-CTI
"""penta-aiify-04 — TRUST: sanitize PRD HTML + citations/provenance on AI Boost.

Three controls:

  1. The AI-boosted PRD path now requires inline [source: …] citations validated
     (via tools/quality/citation_grounding.py) against the scan evidence, and
     persists a provenance record to aiify_prd_provenance.
  2. send-to-kanban is gated on citation defects for AI-boosted PRDs (mirrors the
     placeholder/citation guard): a boosted-but-uncited phase cannot seed tasks
     without an explicit force override (which is audited).
  3. Generated PRD HTML is sanitized server-side (strict allowlist) so injected
     <script>/handlers render inert, and opportunity text is escaped client-side.
"""
from __future__ import annotations

import json

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. HTML sanitizer (unit)
# ─────────────────────────────────────────────────────────────────────────────

def test_sanitizer_drops_script_and_content():
    from tools.quality.html_sanitizer import sanitize_html
    out = sanitize_html("<p>ok</p><script>alert('x')</script>")
    assert "<script" not in out.lower()
    assert "alert" not in out  # content of <script> discarded, not just the tag
    assert "<p>ok</p>" in out


def test_sanitizer_strips_event_handlers_and_bad_urls():
    from tools.quality.html_sanitizer import sanitize_html
    out = sanitize_html('<a href="javascript:steal()" onclick="x()">hi</a>')
    assert "onclick" not in out.lower()
    assert "javascript:" not in out.lower()
    assert ">hi</a>" in out  # anchor kept, dangerous href/handler removed


def test_sanitizer_keeps_allowlisted_markup_and_escapes_text():
    from tools.quality.html_sanitizer import sanitize_html
    out = sanitize_html("<h2>Title</h2><ul><li>a &amp; b</li></ul><b><i>x</i></b>")
    assert "<h2>Title</h2>" in out
    assert "<li>a &amp; b</li>" in out
    out2 = sanitize_html("<div>1 < 2 and 3 > 2</div>")
    assert "&lt;" in out2 and "&gt;" in out2


# ─────────────────────────────────────────────────────────────────────────────
# Shared Flask/app harness (same seam as penta-aiify-03)
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

    # init_db.DB_PATH is a process-global computed from AIIFY_DB_PATH at *import*
    # time. Because init_db may be imported lazily here (after setenv), that value
    # can already be a prior test's tmp. Point it at this test's tmp for the test,
    # then restore it to the canonical repo default on teardown so a later test
    # that reads module DB_PATH (e.g. the auth suite's delete-all) never observes
    # this test's seeded scans.
    _default_dbpath = init_db._ICDEV_ROOT / "data" / "aiify_canvas.db"
    _orig_init_done = bp._INIT_DONE
    init_db.DB_PATH = tmp_path / "aiify_canvas.db"
    bp._INIT_DONE = False
    # Translating canvas connection so the blueprint's PG-authored %s queries run
    # against the SQLite test DB (production runs these against PG).
    monkeypatch.setattr(bp, "_conn", lambda: get_canvas_connection("AIIFY_DB_PATH"))

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    app.register_blueprint(bp.aiify_bp)
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


def _init_aiify():
    import tools.aiify.db.init_db as init_db
    init_db.init_db()


def _seed(project_summary="A demo project", phases=None, module_path="tools/demo.py"):
    """Seed a scan + opportunity + roadmap. Returns roadmap_id."""
    _init_aiify()
    if phases is None:
        phases = [{
            "phase_id": "P1",
            "label": "P1 — Quick Wins",
            "opportunities": [{
                "opportunity_id": 1,
                "module_path": module_path,
                "function_name": "run",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
            }],
            "total_effort_days": 3,
        }]
    conn = _cx()
    try:
        conn.execute(
            "INSERT INTO aiify_scans (scan_id, input_type, input_ref, project_summary, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (1, "path", "tools/demo", project_summary, "completed"),
        )
        conn.execute(
            "INSERT INTO aiify_opportunities (opportunity_id, scan_id, module_path, function_name, "
            "language, pattern_type, ai_paradigm) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (1, 1, module_path, "run", "python", "hardcoded_threshold", "anomaly_detection"),
        )
        conn.execute(
            "INSERT INTO aiify_roadmaps (scan_id, roadmap_id, title, phases) VALUES (%s, %s, %s, %s)",
            (1, "rm-trust01", "Demo roadmap", json.dumps(phases)),
        )
        conn.commit()
    finally:
        conn.close()
    return "rm-trust01"


def _seed_provenance(roadmap_id, phase_id, *, ai_boosted, citation_valid):
    conn = _cx()
    try:
        conn.execute(
            "INSERT INTO aiify_prd_provenance (roadmap_id, phase_id, ai_boosted, citation_valid) "
            "VALUES (%s, %s, %s, %s)",
            (roadmap_id, phase_id, 1 if ai_boosted else 0, 1 if citation_valid else 0),
        )
        conn.commit()
    finally:
        conn.close()


def _count_tasks(db_path):
    from tools.db.storage import get_connection
    conn = get_connection(db_path=str(db_path))
    try:
        try:
            return [dict(r) for r in conn.execute("SELECT id, status FROM kanban_tasks").fetchall()]
        except Exception:
            return []
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Server-side PRD HTML sanitization (route)
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_prd_sanitizes_injected_script(client, app):
    with app.app_context():
        _seed(project_summary="Bad <script>alert(document.cookie)</script> summary")
    resp = client.post(
        "/ai-ify/api/generate-prd",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_json().get("prd_html", "")
    assert html, "expected rendered prd_html"
    # No live <script> element reaches the DOM — whether markdown escaped it to
    # inert text or the sanitizer stripped it, an executable tag must be absent.
    low = html.lower()
    assert "<script" not in low
    assert "onerror" not in low and "onclick" not in low


# ─────────────────────────────────────────────────────────────────────────────
# 2. send-to-kanban citation gate for AI-boosted PRDs
# ─────────────────────────────────────────────────────────────────────────────

def test_boosted_prd_without_citations_blocked(client, tmp_path, app):
    with app.app_context():
        _seed()
        _seed_provenance("rm-trust01", "P1", ai_boosted=True, citation_valid=False)
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert resp.get_json().get("citation_defect") is True
    assert _count_tasks(tmp_path / "icdev.db") == []


def test_boosted_prd_defect_force_override_promotes(client, tmp_path, app):
    with app.app_context():
        _seed()
        _seed_provenance("rm-trust01", "P1", ai_boosted=True, citation_valid=False)
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1", "force": True},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    tasks = _count_tasks(tmp_path / "icdev.db")
    assert tasks
    assert all(t["status"] == "suggested" for t in tasks)


def test_unboosted_prd_unaffected(client, tmp_path, app):
    """No provenance record == not boosted == gate does not apply."""
    with app.app_context():
        _seed()
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _count_tasks(tmp_path / "icdev.db")


def test_boosted_prd_with_valid_citations_unaffected(client, tmp_path, app):
    with app.app_context():
        _seed()
        _seed_provenance("rm-trust01", "P1", ai_boosted=True, citation_valid=True)
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _count_tasks(tmp_path / "icdev.db")


def test_all_phase_skips_only_defective_phase(client, tmp_path, app):
    phases = [
        {"phase_id": "P1", "label": "P1 — Quick Wins",
         "opportunities": [{"opportunity_id": 1}], "total_effort_days": 2},
        {"phase_id": "P2", "label": "P2 — Strategic",
         "opportunities": [{"opportunity_id": 1}], "total_effort_days": 5},
    ]
    with app.app_context():
        _seed(phases=phases)
        _seed_provenance("rm-trust01", "P1", ai_boosted=True, citation_valid=False)
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "all"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    tasks = _count_tasks(tmp_path / "icdev.db")
    ids = [t["id"] for t in tasks]
    assert ids, "P2 should still have been promoted"
    assert not any("-p1-" in i for i in ids), f"defective P1 must be skipped: {ids}"
    assert any("-p2-" in i for i in ids), f"clean P2 must be promoted: {ids}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. AI Boost writes a provenance record + citation validity
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, text, model="fake-model"):
        self.content = text
        self.model_id = model


def _install_fake_router(monkeypatch, reply_text):
    import importlib
    router_mod = importlib.import_module("tools.llm.router")

    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, fn, req):
            return _FakeResp(reply_text)

    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)


def _latest_provenance(roadmap_id, phase_id):
    conn = _cx()
    try:
        row = conn.execute(
            "SELECT ai_boosted, citation_valid FROM aiify_prd_provenance "
            "WHERE roadmap_id = %s AND phase_id = %s ORDER BY id DESC LIMIT 1",
            (roadmap_id, phase_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_boost_without_citations_marks_invalid_and_persists(client, app, monkeypatch):
    with app.app_context():
        _seed()
    # Long reply (> 0.8 * original PRD length) with NO [source: …] tags.
    _install_fake_router(monkeypatch, "IMPROVED PRD. " + ("detail line. " * 2000))
    resp = client.post(
        "/ai-ify/api/prd-dry-run",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body.get("ai_boosted") is True
    assert body.get("citation_valid") is False
    with app.app_context():
        rec = _latest_provenance("rm-trust01", "P1")
    assert rec is not None
    assert int(rec["ai_boosted"]) == 1
    assert int(rec["citation_valid"]) == 0


def test_boost_with_valid_citation_marks_valid(client, app, monkeypatch):
    with app.app_context():
        _seed()
    reply = "IMPROVED PRD grounded in [source: OPP-1]. " + ("detail line. " * 2000)
    _install_fake_router(monkeypatch, reply)
    resp = client.post(
        "/ai-ify/api/prd-dry-run",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-trust01", "phase_id": "P1"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("citation_valid") is True
    with app.app_context():
        rec = _latest_provenance("rm-trust01", "P1")
    assert rec is not None and int(rec["citation_valid"]) == 1
