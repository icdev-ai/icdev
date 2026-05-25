#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for GET /data/lineage page route.

4 cases:
  1. Authenticated request returns 200 with HTML graph page
  2. Unauthenticated request redirects to /login
  3. Classification filter renders without error (200)
  4. Empty lineage DB returns 200 with empty-graph message
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_conn(lineage_rows=None, design_rows=None):
    """Return a mock DB connection yielding controlled rows."""
    conn = MagicMock()

    def _fetchall_side_effect(*args, **kwargs):
        return lineage_rows if lineage_rows is not None else []

    def _execute_side_effect(sql, *args, **kwargs):
        result = MagicMock()
        if "data_designs" in sql:
            result.fetchall.return_value = design_rows or []
        else:
            result.fetchall.return_value = lineage_rows or []
        result.fetchone.return_value = None
        return result

    conn.execute.side_effect = _execute_side_effect
    return conn


def _make_app(lineage_rows=None, design_rows=None):
    """Build a minimal Flask app with the DDC blueprint registered."""
    from flask import Flask

    app = Flask(__name__, template_folder=str(_ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    mock_conn = _mock_conn(lineage_rows=lineage_rows, design_rows=design_rows)

    with (
        patch("tools.data_canvas.db.init_db.init_db"),
        patch("tools.data_canvas.db.init_db.get_connection", return_value=mock_conn),
    ):
        from tools.data_canvas.blueprint import create_data_canvas_blueprint

        bp = create_data_canvas_blueprint()
        if bp:
            app.register_blueprint(bp, url_prefix="/data")

    return app, mock_conn


_STUB_HTML = "<html><body>lineage-stub</body></html>"


def _patched_render(template_name, **ctx):
    """Stub render_template — returns a plain HTML string with ctx keys serialised."""
    import json as _json
    return f"{_STUB_HTML}<!-- {_json.dumps({k: str(v)[:80] for k, v in ctx.items()})} -->"


# ── Test 1: Authenticated GET /data/lineage → 200 ────────────────────────────

def test_lineage_authenticated_returns_200():
    """Logged-in user gets a 200 HTML response for GET /data/lineage."""
    app, mock_conn = _make_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"

    with (
        patch("tools.data_canvas.db.init_db.get_connection", return_value=mock_conn),
        patch("tools.data_canvas.blueprint.render_template", side_effect=_patched_render),
    ):
        resp = client.get("/data/lineage")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "lineage" in body.lower()


# ── Test 2: Unauthenticated → redirect to /login ─────────────────────────────

def test_lineage_unauthenticated_redirects():
    """GET /data/lineage without session redirects to /login."""
    app, _ = _make_app()
    client = app.test_client()

    resp = client.get("/data/lineage")
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "login" in location


# ── Test 3: Classification filter passes correct SQL param ────────────────────

def test_lineage_classification_filter_returns_200():
    """GET /data/lineage?classification=CUI returns 200 and queries with the filter."""
    filtered_calls = []

    def _execute_side_effect(sql, *args, **kwargs):
        if "classification=?" in sql:
            filtered_calls.append(args)
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    filter_conn = MagicMock()
    filter_conn.execute.side_effect = _execute_side_effect

    app, _ = _make_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"

    # Patch the name as imported in the blueprint module
    with (
        patch("tools.data_canvas.blueprint.get_connection", return_value=filter_conn),
        patch("tools.data_canvas.blueprint.render_template", side_effect=_patched_render),
    ):
        resp = client.get("/data/lineage?classification=CUI")

    assert resp.status_code == 200
    # Route must have issued a filtered SQL query with "CUI"
    assert any("CUI" in str(call) for call in filtered_calls)


# ── Test 4: Empty lineage DB returns 200 with empty DAG in context ────────────

def test_lineage_empty_db_returns_200():
    """GET /data/lineage with no lineage records still renders 200 with empty DAG."""
    rendered_ctx = {}

    def _capture_render(template_name, **ctx):
        rendered_ctx.update(ctx)
        return _STUB_HTML

    app, mock_conn = _make_app(lineage_rows=[], design_rows=[])
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"

    with (
        patch("tools.data_canvas.db.init_db.get_connection", return_value=mock_conn),
        patch("tools.data_canvas.blueprint.render_template", side_effect=_capture_render),
    ):
        resp = client.get("/data/lineage")

    assert resp.status_code == 200
    import json as _json
    dag = _json.loads(rendered_ctx.get("dag_json", "{}"))
    assert dag.get("node_count", 0) == 0
    assert dag.get("edge_count", 0) == 0
