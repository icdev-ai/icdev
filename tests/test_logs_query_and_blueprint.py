# CUI // SP-CTI
"""Tests for the EQO centralized-logging query surface (eqo-log-04).

Covers, driven through one shared in-memory SQLite ``centralized_logs`` table:

  * ``tools.logging.log_query.query_logs`` — component / level / since / contains
    filters, newest-first ordering, limit clamping, and empty-state on a missing
    table.
  * ``tools.logging.blueprint.create_logs_blueprint`` — feature-flag gating, the
    ``/logs`` page (JSON fallback when the template isn't on the bare test app),
    and ``GET /api/logs`` honoring the same query-param filters.
  * ``tools.iqe.adapters.logs`` — the ``logs.entries`` collection returns rows.

The blueprint + query path reach the DB via ``tools.db.storage.get_connection``;
the suite patches that on the exact module object (resolved via importlib, since
``tools.*`` is a compat shim over ``icdev.tools.*``) to hand back one shared
in-memory connection whose ``close()`` is a no-op.
"""
import sqlite3

import pytest

from tools.logging.blueprint import create_logs_blueprint
from tools.logging.log_query import query_logs


class _PersistentConn(sqlite3.Connection):
    """In-memory connection whose ``close()`` is a no-op so one instance survives
    across query_logs / blueprint calls within a test."""

    def close(self):  # noqa: D401 — intentional no-op; torn down via _hard_close
        pass

    def _hard_close(self):
        super().close()


def _create_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS centralized_logs (
            id              TEXT PRIMARY KEY,
            ts              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            component       TEXT NOT NULL,
            level           TEXT NOT NULL DEFAULT 'INFO',
            message         TEXT NOT NULL DEFAULT '',
            trace_id        TEXT,
            session_id      TEXT,
            classification  TEXT NOT NULL DEFAULT 'CUI',
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            extra_json      TEXT
        )
        """
    )
    conn.commit()


def _seed(conn):
    rows = [
        ("l1", "2026-06-06T08:00:00", "genesis", "INFO", "reflex cycle start"),
        ("l2", "2026-06-06T08:01:00", "genesis", "ERROR", "reflex timeout hit"),
        ("l3", "2026-06-06T08:02:00", "scheduler", "WARNING", "queue backpressure"),
        ("l4", "2026-06-05T23:59:00", "scheduler", "INFO", "older entry before window"),
        ("l5", "2026-06-06T08:03:00", "kanban", "ERROR", "dispatch failed: timeout"),
    ]
    for rid, ts, comp, lvl, msg in rows:
        conn.execute(
            "INSERT INTO centralized_logs (id, ts, component, level, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (rid, ts, comp, lvl, msg),
        )
    conn.commit()


@pytest.fixture
def shared_conn(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    conn = sqlite3.connect(":memory:", factory=_PersistentConn)
    conn.row_factory = sqlite3.Row
    _create_table(conn)
    _seed(conn)

    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    yield conn
    conn._hard_close()


@pytest.fixture
def client(shared_conn, monkeypatch):
    from flask import Flask

    monkeypatch.setenv("ICDEV_LOGS_ENABLED", "true")
    bp = create_logs_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client()


# --------------------------------------------------------------------------- #
# query_logs
# --------------------------------------------------------------------------- #
def test_query_logs_no_filter_newest_first(shared_conn):
    rows = query_logs()
    assert len(rows) == 5
    # Newest ts first (l5 @ 08:03 leads; l4 @ prev-day trails).
    assert rows[0]["id"] == "l5"
    assert rows[-1]["id"] == "l4"


def test_query_logs_component_filter(shared_conn):
    rows = query_logs(component="genesis")
    assert {r["id"] for r in rows} == {"l1", "l2"}


def test_query_logs_level_filter(shared_conn):
    rows = query_logs(level="ERROR")
    assert {r["id"] for r in rows} == {"l2", "l5"}


def test_query_logs_contains_is_case_insensitive(shared_conn):
    rows = query_logs(contains="TIMEOUT")
    assert {r["id"] for r in rows} == {"l2", "l5"}


def test_query_logs_since_lower_bound(shared_conn):
    rows = query_logs(since="2026-06-06T00:00:00")
    # l4 is before the window and must be excluded.
    assert "l4" not in {r["id"] for r in rows}
    assert len(rows) == 4


def test_query_logs_limit_clamped(shared_conn):
    rows = query_logs(limit=2)
    assert len(rows) == 2
    # Huge / bad limits are clamped, not fatal.
    assert len(query_logs(limit=10_000)) == 5
    assert len(query_logs(limit=0)) == 1


def test_query_logs_missing_table_is_empty(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    conn = sqlite3.connect(":memory:", factory=_PersistentConn)
    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    assert query_logs() == []
    conn._hard_close()


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
def test_factory_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("ICDEV_LOGS_ENABLED", "0")
    assert create_logs_blueprint() is None


def test_factory_returns_blueprint_when_enabled(monkeypatch):
    monkeypatch.setenv("ICDEV_LOGS_ENABLED", "true")
    bp = create_logs_blueprint()
    assert bp is not None
    assert bp.name == "logs"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_api_logs_returns_rows(client):
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 5
    assert len(data["rows"]) == 5


def test_api_logs_honors_filters(client):
    resp = client.get("/api/logs?level=ERROR&component=kanban")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["rows"][0]["id"] == "l5"


def test_logs_page_renders_or_falls_back(client):
    resp = client.get("/logs")
    assert resp.status_code == 200
    # Bare test app has no template loader for base.html → JSON fallback path;
    # either way the route is exercisable and returns the rows.
    body = resp.get_data(as_text=True)
    assert "l5" in body or "genesis" in body or resp.is_json


def test_iqe_query_requires_question(client):
    resp = client.post("/logs/api/iqe-query", json={})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# IQE adapter
# --------------------------------------------------------------------------- #
def test_logs_entries_adapter_returns_rows(shared_conn):
    from tools.iqe.adapters.logs import entries_adapter

    rows = entries_adapter(None)
    assert len(rows) == 5
    assert {r["component"] for r in rows} == {"genesis", "scheduler", "kanban"}
