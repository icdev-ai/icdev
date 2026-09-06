#!/usr/bin/env python3
"""RLS must not inject a row predicate into a SELECT that names no table.

`GET /api/health` probed the database with::

    get_connection().execute("SELECT 1")

Inside a Flask request the connection carries a security context, so
`StorageCursor._inject_rls` rewrote that to::

    SELECT 1 WHERE (classification IS NULL OR classification = ''
                    OR classification IN (%s))

PostgreSQL raises `UndefinedColumn: column "classification" does not exist` on
a statement with no FROM clause -- there is no relation for the column to
resolve against. `api_health`'s bare `except Exception` swallowed it and
answered `{"db": false, "status": "degraded"}` on EVERY request while the
database served real rows on the same process. A load balancer or uptime check
reading that endpoint sees permanent degradation, so a REAL outage is
indistinguishable from the steady state.

The guard is deliberately narrower than "no FROM at depth 0". It declines only
a SELECT with **no FROM keyword anywhere**, which therefore reads no table row
and has nothing to filter. A scalar subquery like
``SELECT (SELECT c FROM t LIMIT 1)`` does name a table, so it keeps whatever
behaviour it had -- skipping injection there would be a privilege escalation,
and the negative cases below matter more than the positive ones.

Both directions are asserted for the endpoint, because the shipped code passed
any test that only checked the unhealthy case.
"""
from __future__ import annotations

import pytest

from tools.security.row_security import inject_row_predicate

_CLS = {"CUI", "UNCLASSIFIED", "PUBLIC"}


def _inject(sql):
    return inject_row_predicate(
        sql, tenant_id="acme", classifications=_CLS, placeholder="%s")


# --------------------------------------------------------------------------- #
# Table-less SELECTs are left alone
# --------------------------------------------------------------------------- #


def test_the_exact_probe_that_broke_api_health():
    """Regression, verbatim from tools/dashboard/app.py::api_health."""
    out, extra, _ = _inject("SELECT 1")
    assert out == "SELECT 1", "the health probe was rewritten"
    assert extra == ()
    assert "classification" not in out


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select 1",
    "SELECT 1;",
    "SELECT 1 AS ok",
    "SELECT now()",
    "SELECT version()",
    "SELECT current_timestamp",
])
def test_tableless_select_is_not_rewritten(sql):
    out, extra, n = _inject(sql)
    assert out == sql
    assert extra == ()
    assert n == 0


# --------------------------------------------------------------------------- #
# Anything that names a table is STILL filtered -- these matter more
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM kanban_tasks",
    "SELECT * FROM proposals WHERE id = %s",
    "SELECT id FROM dic_documents ORDER BY created_at LIMIT 10",
    "SELECT (SELECT title FROM proposals LIMIT 1)",
    "DELETE FROM kanban_tasks WHERE id = %s",
    "UPDATE kanban_tasks SET status = 'done' WHERE id = %s",
    "UPDATE kanban_tasks SET status = 'done'",
])
def test_a_statement_naming_a_table_is_still_rewritten(sql):
    out, extra, _ = _inject(sql)
    assert out != sql, "row security was skipped for a statement over a table"
    assert "classification" in out
    assert extra, "no predicate params were bound"


def test_a_string_literal_spelling_from_does_not_count_as_a_table():
    """`FROM` inside a quoted literal names no relation."""
    sql = "SELECT 'select 1 from nowhere' AS note"
    out, extra, _ = _inject(sql)
    assert out == sql and extra == ()


# --------------------------------------------------------------------------- #
# The storage layer, where the rewrite actually fired
# --------------------------------------------------------------------------- #


def test_storage_cursor_runs_select_1_under_a_security_context(icdev_db, monkeypatch):
    """A connection carrying a security context must still answer the probe."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection
    from tools.security.security_context import SecurityContext

    conn = get_connection(db_path=str(icdev_db))
    conn.set_security_context(
        SecurityContext(user_id="u", tenant_id="acme", classification="CUI"))
    try:
        assert conn.execute("SELECT 1").fetchone() is not None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# The endpoint, asserted BOTH ways
# --------------------------------------------------------------------------- #


@pytest.fixture
def health_client(icdev_db, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield tc


def test_api_health_reports_db_true_against_a_reachable_database(health_client):
    body = health_client.get("/api/health").get_json()
    assert body["db"] is True, f"healthy database reported down: {body}"
    assert body["status"] == "ok"


def test_api_health_reports_db_false_when_the_database_is_unreachable(
    health_client, monkeypatch
):
    """The other direction: the shipped code passed this one on its own."""
    import tools.db.storage as _storage

    def _boom(*a, **kw):
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(_storage, "get_connection", _boom)
    body = health_client.get("/api/health").get_json()
    assert body["db"] is False
    assert body["status"] == "degraded"
