# CUI // SP-CTI
"""Unit tests for the SDC Attack Path Twin dashboard — dt-sdc-twin-07.

Tests:
  1. sdc_attack_snapshots table is present in security_canvas.db SCHEMA
  2. get_attackpath_summary() returns the expected dict shape
  3. GET /security/attackpath → 200 (page renders)
  4. GET /security/api/attackpath → 200 JSON with 'snapshots' and 'summary' keys
  5. GET /security/api/attackpath with snapshots → summary counts match fixture data
"""
from __future__ import annotations

import json
import sqlite3

import pytest


# ── Schema presence test ─────────────────────────────────────────────────────

def test_sdc_attack_snapshots_in_schema():
    """sdc_attack_snapshots CREATE TABLE statement must exist in init_db.SCHEMA."""
    from tools.security_canvas.db.init_db import SCHEMA

    assert "sdc_attack_snapshots" in SCHEMA, (
        "SCHEMA in tools/security_canvas/db/init_db.py must define sdc_attack_snapshots table"
    )


# ── Data helper tests ─────────────────────────────────────────────────────────

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id           TEXT PRIMARY KEY,
    component_id TEXT NOT NULL,
    nodes_json   TEXT NOT NULL DEFAULT '[]',
    edges_json   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
"""

_NODES = json.dumps([
    {"id": "internet",  "label": "Internet",   "node_type": "external",       "privilege": "none"},
    {"id": "web_app",   "label": "Web App",    "node_type": "service",        "privilege": "user"},
    {"id": "db_server", "label": "DB Server",  "node_type": "asset-database", "privilege": "user"},
])

_EDGES = json.dumps([
    {"source": "internet", "target": "web_app",   "risk_score": 7},
    {"source": "web_app",  "target": "db_server", "risk_score": 9},
])


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_DDL)
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id, nodes_json, edges_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("snap-test-01", "comp-web", _NODES, _EDGES, "2026-04-18T00:00:00"),
    )
    conn.commit()
    return conn


def test_get_attackpath_summary_shape():
    """get_attackpath_summary() returns dict with required keys."""
    from tools.security_canvas.attackpath import get_attackpath_summary

    conn = _make_conn()
    summary = get_attackpath_summary(conn)
    conn.close()

    assert isinstance(summary, dict)
    assert "total_snapshots" in summary
    assert "total_nodes" in summary
    assert "total_edges" in summary
    assert "max_risk_score" in summary
    assert "snapshots" in summary


def test_get_attackpath_summary_counts():
    """Summary counts reflect fixture: 1 snapshot, 3 nodes, 2 edges, max risk 9."""
    from tools.security_canvas.attackpath import get_attackpath_summary

    conn = _make_conn()
    summary = get_attackpath_summary(conn)
    conn.close()

    assert summary["total_snapshots"] == 1
    assert summary["total_nodes"] == 3
    assert summary["total_edges"] == 2
    assert summary["max_risk_score"] == 9


def test_get_attackpath_summary_empty_db():
    """Empty DB returns zeros — no exceptions."""
    from tools.security_canvas.attackpath import get_attackpath_summary

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_DDL)
    summary = get_attackpath_summary(conn)
    conn.close()

    assert summary["total_snapshots"] == 0
    assert summary["total_nodes"] == 0
    assert summary["total_edges"] == 0
    assert summary["max_risk_score"] == 0


# ── Route / blueprint integration tests ───────────────────────────────────────

@pytest.fixture()
def app():
    """Minimal Flask test app with security_canvas blueprint registered."""
    import os
    os.environ.setdefault("ICDEV_SECURITY_ENABLED", "true")

    from flask import Flask
    from tools.security_canvas.blueprint import create_security_blueprint

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        WTF_CSRF_ENABLED=False,
    )

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")

    # Inject an authenticated session so sc_login_required passes
    @flask_app.before_request
    def _inject_session():
        from flask import session as s
        s["user_id"] = "test-user"

    return flask_app


def test_attackpath_page_returns_200(app):
    """GET /security/attackpath renders the attack path page."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "test-user"
        resp = client.get("/security/attackpath")
    assert resp.status_code == 200


def test_attackpath_api_returns_json(app):
    """GET /security/api/attackpath returns JSON with 'snapshots' and 'summary'."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "test-user"
        resp = client.get("/security/api/attackpath")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "snapshots" in data
    assert "summary" in data
