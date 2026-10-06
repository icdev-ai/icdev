# CUI // SP-CTI
"""Academy request-scoped connection reuse (aca-perf-demo).

Measured 2026-10-05: Academy helpers never close their connections, so on
PostgreSQL storage's pool drains and every ``get_connection()`` became a fresh
~72ms connect -- 45-58 per page, 214 for the coverage API. ``db.get_connection``
now reuses a connection the request already opened once its previous holder has
released it. These tests pin the safety rules of that reuse with fake
connections: no live holder is ever shared, a reused connection is rolled back
first (what closing it would have done), and teardown closes everything.
"""
from __future__ import annotations

import gc

import pytest
from flask import Flask

import apps.forge_academy.db as fadb


class _FakeConn:
    def __init__(self, backend="postgresql"):
        self._backend = backend
        self.rollbacks = 0
        self.commits = 0
        self.closed = False

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True

    def execute(self, sql, params=None):
        return sql


@pytest.fixture
def opened(monkeypatch):
    made: list[_FakeConn] = []

    def _factory(*a, **k):
        conn = _FakeConn()
        made.append(conn)
        return conn

    monkeypatch.setattr(fadb, "_storage_get_connection", _factory)
    return made


@pytest.fixture
def req():
    app = Flask(__name__)
    with app.test_request_context("/academy"):
        yield
        fadb.release_request_connections()


def test_dropped_connection_is_reused_after_rollback(opened, req):
    fadb.get_connection().execute("SELECT 1")  # dropped at once, like the helpers do
    gc.collect()
    conn = fadb.get_connection()
    assert len(opened) == 1, "a released connection must be reused, not reopened"
    assert opened[0].rollbacks == 1, "reuse must first roll back the previous holder's work"
    assert conn.execute("SELECT 2") == "SELECT 2"


def test_live_holder_is_never_shared(opened, req):
    first = fadb.get_connection()
    second = fadb.get_connection()
    assert len(opened) == 2
    assert first._sc is not second._sc


def test_close_releases_and_teardown_closes_all(opened, req):
    a = fadb.get_connection()
    held = fadb.get_connection()
    a.close()
    fadb.get_connection()  # takes a's slot
    assert len(opened) == 2
    assert held is not None
    assert fadb.release_request_connections() == 2
    assert all(c.closed for c in opened)


def test_context_manager_commits_and_releases(opened, req):
    with fadb.get_connection() as conn:
        conn.execute("UPDATE x")
    assert opened[0].commits == 1
    fadb.get_connection()
    assert len(opened) == 1


def test_passthrough_outside_a_request(opened):
    conn = fadb.get_connection()
    assert conn is opened[0], "outside a request the storage connection is returned as-is"


def test_sqlite_is_passthrough(monkeypatch, req):
    lite = _FakeConn(backend="sqlite")
    monkeypatch.setattr(fadb, "_storage_get_connection", lambda *a, **k: lite)
    assert fadb.get_connection() is lite
    assert fadb.release_request_connections() == 0


def test_teardown_hook_is_registered_app_wide():
    from apps.forge_academy import blueprint as fabp

    app = Flask(__name__)
    app.register_blueprint(fabp.bp)
    assert fabp._release_fa_connections in app.teardown_request_funcs.get(None, [])
