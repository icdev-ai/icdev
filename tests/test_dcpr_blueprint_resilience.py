#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Data Canvas blueprint PostgreSQL resilience (dcpr-db-05 / dcpr-db-06).

Two guarantees added by this hardening pass:

  dcpr-db-05 — missing-table resilience:
    A fresh / unmigrated deploy (a data-canvas table does not exist yet) must
    NOT return an HTML 500. PAGE routes degrade to an empty-state render (200)
    and JSON routes return a clean ``503`` with an ``error`` body. No demo data
    is fabricated.

  dcpr-db-06 — reserved word ``user`` quoted in PG DML:
    Runtime INSERT/SELECT against dm_*/dd_* tables must double-quote the
    reserved column name ``user`` so PostgreSQL parses it as an identifier
    (unquoted ``user`` is a syntax error on INSERT and silently resolves to
    CURRENT_USER on SELECT).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_app():
    """Build a minimal Flask app with the DDC blueprint registered at /data."""
    from flask import Flask

    app = Flask(__name__, template_folder=str(_ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    with patch("tools.data_canvas.db.init_db.init_db"):
        from tools.data_canvas.blueprint import create_data_canvas_blueprint

        bp = create_data_canvas_blueprint()
    assert bp is not None, "DDC blueprint disabled — set ICDEV_DATA_CANVAS_ENABLED"
    app.register_blueprint(bp, url_prefix="/data")
    return app


def _authed_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
    return client


class _MissingTableConn:
    """A fake connection whose every DB call raises a missing-table error.

    ``kind='sqlite'`` mimics ``sqlite3.OperationalError: no such table``.
    ``kind='pg'`` mimics psycopg2 ``UndefinedTable`` — SQLSTATE 42P01 with a
    ``relation ... does not exist`` message.
    """

    def __init__(self, kind: str = "sqlite"):
        self._kind = kind

    def _raise(self):
        if self._kind == "pg":
            exc = Exception('relation "data_designs" does not exist')
            exc.pgcode = "42P01"  # type: ignore[attr-defined]
            raise exc
        raise sqlite3.OperationalError("no such table: data_designs")

    def execute(self, *args, **kwargs):
        self._raise()

    def commit(self):  # pragma: no cover - never reached
        pass

    def close(self):
        pass


# ══════════════════════════════════════════════════════════════════════════
# dcpr-db-05 — missing-table resilience
# ══════════════════════════════════════════════════════════════════════════


def test_is_missing_table_error_detects_both_backends():
    from tools.data_canvas.blueprint import _is_missing_table_error

    assert _is_missing_table_error(sqlite3.OperationalError("no such table: data_designs"))

    pg = Exception('relation "data_designs" does not exist')
    pg.pgcode = "42P01"  # type: ignore[attr-defined]
    assert _is_missing_table_error(pg)

    # A message-only PG error (no pgcode) is still detected.
    assert _is_missing_table_error(Exception('relation "x" does not exist'))

    # An unrelated error is NOT treated as a missing table.
    assert not _is_missing_table_error(ValueError("something else entirely"))


def test_index_page_degrades_to_empty_state_when_table_missing():
    """The index PAGE route must render an empty state (200), never a 500.

    ``render_template`` is mocked because the standalone test app does not
    register the dashboard's ``nav_tree`` context processor that ``base.html``
    needs — so we assert the guard rendered the index template with an EMPTY
    context (no fabricated records) rather than exercising full page HTML.
    """
    app = _make_app()
    client = _authed_client(app)

    captured = {}

    def _fake_render(template, **ctx):
        captured["template"] = template
        captured["ctx"] = ctx
        return "EMPTY-STATE-OK"

    with patch("tools.data_canvas.blueprint.get_connection",
               return_value=_MissingTableConn("sqlite")), \
            patch("tools.data_canvas.blueprint.render_template",
                  side_effect=_fake_render):
        resp = client.get("/data/")

    assert resp.status_code == 200, (
        f"index should degrade to 200 empty state, got {resp.status_code}"
    )
    assert captured.get("template") == "data_canvas/index.html"
    # Empty state — the guard must not fabricate designs/templates/counts.
    assert captured["ctx"].get("designs") == []
    assert captured["ctx"].get("templates") == []
    assert captured["ctx"].get("sop_count") == 0
    assert captured["ctx"].get("approved_sop_count") == 0


def test_json_list_route_returns_503_when_table_missing_sqlite():
    """A JSON list route must return 503 (not 500) when its table is missing."""
    app = _make_app()
    client = _authed_client(app)

    with patch("tools.data_canvas.blueprint.get_connection",
               return_value=_MissingTableConn("sqlite")):
        resp = client.get("/data/api/designs")

    assert resp.status_code == 503, (
        f"JSON list should return 503 on missing table, got {resp.status_code}"
    )
    body = resp.get_json()
    assert body and "error" in body


def test_json_list_route_returns_503_when_table_missing_pg():
    """The PostgreSQL UndefinedTable (SQLSTATE 42P01) path also yields 503."""
    app = _make_app()
    client = _authed_client(app)

    with patch("tools.data_canvas.blueprint.get_connection",
               return_value=_MissingTableConn("pg")):
        resp = client.get("/data/api/designs")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body and "error" in body


def test_templates_json_route_degrades_503():
    app = _make_app()
    client = _authed_client(app)

    with patch("tools.data_canvas.blueprint.get_connection",
               return_value=_MissingTableConn("sqlite")):
        resp = client.get("/data/api/templates")

    assert resp.status_code == 503


def test_missing_table_does_not_fabricate_data():
    """Empty-state / 503 must carry no fabricated design records."""
    app = _make_app()
    client = _authed_client(app)

    with patch("tools.data_canvas.blueprint.get_connection",
               return_value=_MissingTableConn("sqlite")):
        resp = client.get("/data/api/designs")

    # 503 body is an error object, never a populated list of fake designs.
    body = resp.get_json()
    assert isinstance(body, dict)
    assert "error" in body


# ══════════════════════════════════════════════════════════════════════════
# dcpr-db-06 — reserved word ``user`` quoted in PG DML (string-level)
# ══════════════════════════════════════════════════════════════════════════

_BLUEPRINT_SRC = (_ROOT / "tools" / "data_canvas" / "blueprint.py").read_text(encoding="utf-8")
_DATA_MESH_SRC = (_ROOT / "tools" / "data_canvas" / "data_mesh.py").read_text(encoding="utf-8")

# Matches an unquoted ``user`` used as a column inside an INSERT/SELECT column
# list, e.g. ``(id, design_id, user, ...)`` or ``SELECT ..., user, ...``.
_UNQUOTED_USER_DML = re.compile(
    r"(?:INSERT INTO|SELECT)[^\"']*?[,(]\s*user\s*[,)]",
    re.IGNORECASE,
)


def test_no_unquoted_user_column_in_blueprint_dml():
    offenders = [
        line for line in _BLUEPRINT_SRC.splitlines()
        if _UNQUOTED_USER_DML.search(line)
    ]
    assert not offenders, f"unquoted `user` in blueprint DML: {offenders}"


def test_no_unquoted_user_column_in_data_mesh_dml():
    offenders = [
        line for line in _DATA_MESH_SRC.splitlines()
        if _UNQUOTED_USER_DML.search(line)
    ]
    assert not offenders, f"unquoted `user` in data_mesh DML: {offenders}"


def test_dm_audit_insert_quotes_user_both_files():
    """The dm_audit INSERT (the verified dm_* site) must quote ``user``."""
    for src, name in ((_BLUEPRINT_SRC, "blueprint.py"), (_DATA_MESH_SRC, "data_mesh.py")):
        assert 'INSERT INTO dm_audit' in src, f"dm_audit insert missing from {name}"
        for line in src.splitlines():
            if "INSERT INTO dm_audit" in line:
                assert '"user"' in line, f"dm_audit insert not quoted in {name}: {line}"


def test_dd_query_history_dml_quotes_user():
    """All dd_query_history INSERT/SELECT statements quote ``user``."""
    for line in _BLUEPRINT_SRC.splitlines():
        if "dd_query_history" in line and ("INSERT INTO" in line or "SELECT" in line):
            # Line references the table in a DML context; if it names the user
            # column at all it must be quoted (the unquoted regex above already
            # guarantees this, but assert explicitly for the query-history site).
            assert not _UNQUOTED_USER_DML.search(line), f"unquoted user: {line}"
