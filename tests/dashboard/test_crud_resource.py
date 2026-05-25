# CUI // SP-CTI
"""OPT-69: tests for tools/dashboard/crud_resource.py."""
from __future__ import annotations

import pathlib
import sys

import pytest
from flask import Flask


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dashboard.crud_resource import (  # noqa: E402
    ColumnSpec,
    ResourceConfig,
    _build_delete,
    _build_insert,
    _build_list_query,
    _build_update,
    _safe_ident,
    register_resource,
)


# ────────────────────────────────────────────────────────────────────────────
# Identifier safety
# ────────────────────────────────────────────────────────────────────────────


def test_safe_ident_accepts_valid():
    assert _safe_ident("audit_trail") == "audit_trail"
    assert _safe_ident("Col1") == "Col1"
    assert _safe_ident("_hidden") == "_hidden"


def test_safe_ident_rejects_unsafe():
    for bad in ("1col", "col; DROP", "col-name", "", "col name"):
        with pytest.raises(ValueError):
            _safe_ident(bad)


def test_columnspec_validates_name():
    with pytest.raises(ValueError):
        ColumnSpec("col; DROP TABLE audit")


# ────────────────────────────────────────────────────────────────────────────
# Query builders
# ────────────────────────────────────────────────────────────────────────────


def _cfg():
    return ResourceConfig(
        name="audit_trail",
        url_prefix="/api/audit",
        columns=[
            ColumnSpec("id", pk=True),
            ColumnSpec("actor"),
            ColumnSpec("action"),
            ColumnSpec("created_at", writable=False),
        ],
        sortable=["created_at", "actor"],
        filterable=["actor", "action"],
    )


class _Args(dict):
    def items(self):
        return super().items()

    def get(self, key, default=None):
        return super().get(key, default)


def test_list_query_basic():
    plan = _build_list_query(_cfg(), _Args())
    assert "SELECT id, actor, action, created_at FROM audit_trail" in plan["sql"]
    assert "LIMIT" in plan["sql"]
    assert plan["params"] == ()


def test_list_query_applies_filter():
    plan = _build_list_query(_cfg(), _Args(actor="alice"))
    assert "WHERE actor = %s" in plan["sql"]
    assert plan["params"] == ("alice",)


def test_list_query_ignores_unlisted_filter():
    plan = _build_list_query(_cfg(), _Args(password="xxx"))
    assert "WHERE" not in plan["sql"]
    assert plan["params"] == ()


def test_list_query_sort_and_order():
    plan = _build_list_query(
        _cfg(), _Args(_sort="created_at", _order="DESC")
    )
    assert "ORDER BY created_at DESC" in plan["sql"]


def test_list_query_sort_rejected_if_not_sortable():
    plan = _build_list_query(_cfg(), _Args(_sort="notacolumn"))
    assert "ORDER BY" not in plan["sql"]


def test_list_query_pagination():
    plan = _build_list_query(
        _cfg(), _Args(_page="3", _page_size="20")
    )
    assert plan["page"] == 3
    assert plan["page_size"] == 20
    assert "LIMIT 20 OFFSET 40" in plan["sql"]


def test_list_query_clamps_page_size():
    plan = _build_list_query(_cfg(), _Args(_page_size="99999"))
    assert plan["page_size"] <= 500  # default max


def test_insert_filters_writable_columns():
    plan = _build_insert(
        _cfg(), {"actor": "bob", "action": "test", "created_at": "nope"}
    )
    # created_at is writable=False, should be dropped
    assert "actor" in plan["sql"]
    assert "action" in plan["sql"]
    assert "created_at" not in plan["sql"]


def test_insert_rejects_empty_payload():
    with pytest.raises(ValueError):
        _build_insert(_cfg(), {"unknown": "x"})


def test_update_builds_pk_where():
    plan = _build_update(_cfg(), "abc123", {"actor": "new"})
    assert "UPDATE audit_trail SET actor = %s WHERE id = %s" in plan["sql"]
    assert plan["params"] == ("new", "abc123")


def test_update_rejects_empty_assignments():
    with pytest.raises(ValueError):
        _build_update(_cfg(), "x", {"created_at": "no"})


def test_delete_builds_pk_where():
    plan = _build_delete(_cfg(), "row-1")
    assert "DELETE FROM audit_trail WHERE id = %s" in plan["sql"]
    assert plan["params"] == ("row-1",)


# ────────────────────────────────────────────────────────────────────────────
# Blueprint wiring end-to-end with a fake DB
# ────────────────────────────────────────────────────────────────────────────


class _FakeRow(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, store):
        self.store = store
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if sql.upper().startswith("SELECT COUNT"):
            return _FakeCursor([(len(self.store["rows"]),)])
        if sql.upper().startswith("SELECT"):
            return _FakeCursor(self.store["rows"])
        if sql.upper().startswith("INSERT"):
            self.store["rows"].append({"id": "new", **dict(zip(
                ("actor", "action"), params
            ))})
            return _FakeCursor([])
        if sql.upper().startswith("UPDATE"):
            return _FakeCursor([])
        if sql.upper().startswith("DELETE"):
            self.store["rows"] = [
                r for r in self.store["rows"] if r.get("id") != params[0]
            ]
            return _FakeCursor([])
        return _FakeCursor([])

    def commit(self):
        pass

    def close(self):
        pass


def _make_app(store):
    app = Flask(__name__)

    def factory():
        return _FakeConn(store)

    register_resource(
        app,
        name="audit_trail",
        url_prefix="/api/audit",
        columns=[
            ColumnSpec("id", pk=True),
            ColumnSpec("actor"),
            ColumnSpec("action"),
            ColumnSpec("created_at", writable=False),
        ],
        sortable=["created_at"],
        filterable=["actor"],
        get_connection=factory,
    )
    return app


def test_blueprint_list_returns_rows():
    store = {"rows": [
        _FakeRow(id="a1", actor="alice", action="x", created_at="t1"),
        _FakeRow(id="a2", actor="bob", action="y", created_at="t2"),
    ]}
    app = _make_app(store)
    client = app.test_client()
    r = client.get("/api/audit?_page=1&_page_size=10")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["actor"] == "alice"


def test_blueprint_create_appends():
    store = {"rows": []}
    app = _make_app(store)
    client = app.test_client()
    r = client.post(
        "/api/audit",
        json={"actor": "carol", "action": "go"},
    )
    assert r.status_code == 201
    assert len(store["rows"]) == 1


def test_blueprint_delete_removes_row():
    store = {"rows": [
        _FakeRow(id="a1", actor="alice", action="x", created_at="t1"),
    ]}
    app = _make_app(store)
    client = app.test_client()
    r = client.delete("/api/audit/a1")
    assert r.status_code == 200
    assert store["rows"] == []


def test_blueprint_get_one_404_on_missing():
    store = {"rows": []}
    app = _make_app(store)
    client = app.test_client()
    r = client.get("/api/audit/absent")
    assert r.status_code == 404


def test_register_resource_honors_allow_flags(monkeypatch):
    """allow_create=False should leave POST unregistered."""
    store = {"rows": []}
    app = Flask(__name__)
    register_resource(
        app,
        name="audit_trail",
        url_prefix="/api/ro",
        columns=[ColumnSpec("id", pk=True), ColumnSpec("actor")],
        filterable=[],
        allow_create=False,
        allow_edit=False,
        allow_delete=False,
        get_connection=lambda: _FakeConn(store),
    )
    client = app.test_client()
    r = client.post("/api/ro", json={"actor": "x"})
    # Route not registered → 405 Method Not Allowed or 404
    assert r.status_code in (404, 405)
