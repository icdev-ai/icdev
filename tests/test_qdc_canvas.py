# CUI // SP-CTI
"""QDC (Quality Design Canvas) tests — cnr-qdc-05.

Covers the qdc_engine pure functions, gate_executor availability, the init_db
schema/seeds, and the blueprint routes: auth enforcement (cnr-qdc-01), stored
XSS escaping (cnr-qdc-02), the IQE query route (cnr-qdc-03), graceful
table-absence rendering (cnr-qdc-04), and collab op persistence + replay
(cnr-qdc-05).
"""
import importlib
import json
import sys
import uuid
from pathlib import Path

import pytest
from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_STUB_BASE = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
    "{% block extra_css %}{% endblock %}</head><body>"
    "{% block global_banner %}{% endblock %}{% block content %}{% endblock %}"
    "</body></html>"
)
_TEMPLATES = REPO / "tools" / "dashboard" / "templates"


# ── qdc_engine pure functions ──────────────────────────────────────────────


def test_engine_assess_returns_structure():
    from tools.qdc_canvas.qdc_engine import assess_quality_design

    g = {"nodes": [{"id": "n1", "type": "gate-sast", "label": "SAST"}], "edges": []}
    a = assess_quality_design(g)
    assert {"findings", "score", "summary"} <= set(a.keys())
    assert isinstance(a["findings"], list)


def test_engine_compute_uqs_grades():
    from tools.qdc_canvas.qdc_engine import compute_uqs

    u = compute_uqs([{"gate_id": "sast", "status": "pass", "dimension": "code_quality", "score": 95.0}])
    assert "uqs_score" in u and "grade" in u and "dimensions" in u


def test_engine_map_gate_to_sa11():
    from tools.qdc_canvas.qdc_engine import map_gate_to_sa11

    m = map_gate_to_sa11("sast")
    assert m.get("control", "").startswith("SA-11")


def test_engine_detect_gaps_and_aggregate():
    from tools.qdc_canvas.qdc_engine import aggregate_cross_canvas_quality, detect_quality_gaps

    assert isinstance(detect_quality_gaps({"findings": []}), list)
    agg = aggregate_cross_canvas_quality({"idc": 80.0, "sdc": 60.0})
    assert isinstance(agg, dict)


def test_gate_executor_tool_availability():
    from tools.qdc_canvas.gate_executor import check_tool_availability

    tools = check_tool_availability()
    assert isinstance(tools, dict)


# ── init_db ─────────────────────────────────────────────────────────────────


def test_init_db_creates_schema_and_seeds(tmp_path, monkeypatch):
    initmod = importlib.import_module("tools.qdc_canvas.db.init_db")
    monkeypatch.setattr(initmod, "DB_PATH", tmp_path / "qdc.db")
    initmod.init_db()
    conn = initmod.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM qdc_templates").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM qdc_snippets").fetchone()[0] == 8
        # collab ops table (cnr-qdc-05) must exist
        assert conn.execute("SELECT COUNT(*) FROM qdc_collab_ops").fetchone()[0] == 0
    finally:
        conn.close()


# ── Flask app fixture with isolated DB + stub base template ─────────────────


@pytest.fixture()
def qdc_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)

    initmod = importlib.import_module("tools.qdc_canvas.db.init_db")
    monkeypatch.setattr(initmod, "DB_PATH", tmp_path / "qdc.db")
    initmod.init_db()

    bpmod = importlib.import_module("tools.qdc_canvas.blueprint")

    app = Flask(__name__, static_folder=str(REPO / "tools" / "dashboard" / "static"))
    app.secret_key = "test-secret"
    app.jinja_loader = ChoiceLoader(
        [DictLoader({"base.html": _STUB_BASE}), FileSystemLoader(str(_TEMPLATES))]
    )
    app.register_blueprint(bpmod.qdc_bp)
    return app


def _seed_design(tmp_db_conn_factory, graph_json):
    from tools.qdc_canvas.db.init_db import get_connection

    did = "d-" + uuid.uuid4().hex[:8]
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO qdc_designs (id, name, graph_json, classification, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (did, "Test", graph_json, "CUI", "2026-01-01", "2026-01-01"),
        )
        conn.commit()
    finally:
        conn.close()
    return did


# ── Auth (cnr-qdc-01) ───────────────────────────────────────────────────────


def test_auth_unauth_delete_all_is_401(qdc_app):
    c = qdc_app.test_client()
    r = c.delete("/quality/api/designs")
    assert r.status_code == 401
    assert r.get_json().get("error") == "Authentication required"


def test_auth_unauth_save_is_401(qdc_app):
    c = qdc_app.test_client()
    r = c.put("/quality/api/designs/x", json={"name": "y"})
    assert r.status_code == 401


def test_auth_unauth_page_redirects(qdc_app):
    c = qdc_app.test_client()
    r = c.get("/quality/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_auth_bypass_env(qdc_app, monkeypatch):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    c = qdc_app.test_client()
    r = c.get("/quality/")
    assert r.status_code == 200


# ── Page render + graceful table-absence (cnr-qdc-04) ───────────────────────


def _auth(client):
    with client.session_transaction() as s:
        s["user_id"] = "tester"


def test_index_renders_authed(qdc_app):
    c = qdc_app.test_client()
    _auth(c)
    r = c.get("/quality/")
    assert r.status_code == 200


def test_pages_graceful_when_table_missing(qdc_app):
    # Drop a seeded table; page must still render (no 500) via _safe_rows.
    from tools.qdc_canvas.db.init_db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE qdc_templates")
        conn.commit()
    finally:
        conn.close()
    c = qdc_app.test_client()
    _auth(c)
    assert c.get("/quality/").status_code == 200


# ── Stored XSS escaping (cnr-qdc-02) ────────────────────────────────────────


def test_stored_xss_graph_json_is_escaped(qdc_app):
    payload = json.dumps({"nodes": [], "edges": [], "x": "</script><script>alert(1)</script>"})
    did = _seed_design(None, payload)
    c = qdc_app.test_client()
    _auth(c)
    r = c.get(f"/quality/canvas/{did}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "</script><script>alert(1)</script>" not in body
    # tojson escapes < > as < / > inside the data island
    assert "u003c" in body.lower()


# ── IQE route (cnr-qdc-03) ──────────────────────────────────────────────────


def test_iqe_route_requires_question(qdc_app):
    c = qdc_app.test_client()
    _auth(c)
    r = c.post("/quality/api/iqe-query", json={})
    assert r.status_code == 400


def test_iqe_route_answers_seed_query(qdc_app):
    c = qdc_app.test_client()
    _auth(c)
    r = c.post("/quality/api/iqe-query", json={"question": "list all designs", "execute": True})
    # Either a successful translation/execution or a structured error — never a crash.
    assert r.status_code in (200, 500)
    data = r.get_json()
    assert isinstance(data, dict)
    if r.status_code == 200:
        assert "results" in data and "row_count" in data


# ── Collab op persistence + replay (cnr-qdc-05) ─────────────────────────────


def test_collab_push_persists_and_poll_replays(qdc_app):
    payload = json.dumps({"nodes": [], "edges": []})
    did = _seed_design(None, payload)
    c = qdc_app.test_client()
    _auth(c)

    # Join, push two ops.
    jr = c.post(f"/quality/api/collab/{did}/join", json={"user_id": "u1", "user_name": "One"})
    sid = jr.get_json()["session_id"]
    p1 = c.post(
        f"/quality/api/collab/{did}/push",
        json={"session_id": sid, "user_id": "u1", "operation": {"type": "add", "node": "n1"}},
    )
    assert p1.get_json()["seq"] == 1
    p2 = c.post(
        f"/quality/api/collab/{did}/push",
        json={"session_id": sid, "user_id": "u1", "operation": {"type": "add", "node": "n2"}},
    )
    assert p2.get_json()["seq"] == 2

    # Poll from 0 replays both, cursor advances.
    poll = c.get(f"/quality/api/collab/{did}/poll?since=0").get_json()
    assert len(poll["operations"]) == 2
    assert poll["cursor"] == 2
    assert poll["operations"][0]["operation"]["node"] == "n1"

    # Poll from cursor returns nothing new.
    poll2 = c.get(f"/quality/api/collab/{did}/poll?since=2").get_json()
    assert poll2["operations"] == []
    assert poll2["cursor"] == 2


def test_assess_has_no_dead_chain_mode(qdc_app):
    payload = json.dumps({"nodes": [{"id": "n1", "type": "gate-sast", "label": "SAST"}], "edges": []})
    did = _seed_design(None, payload)
    c = qdc_app.test_client()
    _auth(c)
    r = c.post(f"/quality/api/designs/{did}/assess", json={"use_cod": True})
    assert r.status_code == 200
    # chain_mode was a dead param — it must no longer appear in the response.
    assert "chain_mode" not in r.get_json()
