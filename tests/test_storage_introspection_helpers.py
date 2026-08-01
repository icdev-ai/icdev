# CUI // SP-CTI
"""Tests for backend-aware schema introspection helpers (pgrt-sweep-02).

``tools.db.storage.table_exists / list_tables / column_exists`` replace ad-hoc
``sqlite_master`` / ``PRAGMA table_info`` probes that only work on SQLite or
that silently rely on translate_sql's narrow rule-14 rewrite.  These tests
cover:

* real SQLite (raw in-memory connection AND StorageConnection-wrapped)
* the PostgreSQL path via a fake raw psycopg2-style connection asserting the
  exact information_schema SQL + params (no live server needed)
* a live PG server when reachable (skipped gracefully otherwise)
* missing table / column -> False
* invalid identifier -> ValueError
* the pg_portability_linter rule-14 / rule-1 shape escalation
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.db.storage import (  # noqa: E402
    StorageConnection,
    column_exists,
    list_tables,
    table_exists,
)


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _make_sqlite(raw=True):
    """Return an in-memory SQLite connection with a seeded table.

    raw=True -> a bare sqlite3.Connection; raw=False -> StorageConnection-wrapped.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)")
    conn.execute("CREATE TABLE gadgets (id INTEGER PRIMARY KEY)")
    conn.commit()
    if raw:
        return conn
    return StorageConnection(conn, "sqlite")


class _FakePgCursor:
    """Records execute() calls and replays a queued fetchone/fetchall result."""

    def __init__(self, fetchone_result=None, fetchall_result=None):
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result


class _FakePgConnection:
    """Minimal raw psycopg2-style connection: no _backend, module sniffed as PG."""

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


# Make backend detection see this as PostgreSQL via module sniffing.
_FakePgConnection.__module__ = "psycopg2.extensions"


# ---------------------------------------------------------------------------
# SQLite — raw connection
# ---------------------------------------------------------------------------


def test_table_exists_sqlite_raw():
    conn = _make_sqlite(raw=True)
    assert table_exists(conn, "widgets") is True
    assert table_exists(conn, "gadgets") is True
    assert table_exists(conn, "nonexistent") is False


def test_list_tables_sqlite_raw():
    conn = _make_sqlite(raw=True)
    assert list_tables(conn) == ["gadgets", "widgets"]


def test_column_exists_sqlite_raw():
    conn = _make_sqlite(raw=True)
    assert column_exists(conn, "widgets", "name") is True
    assert column_exists(conn, "widgets", "qty") is True
    assert column_exists(conn, "widgets", "missing_col") is False
    # Missing table -> False (no raise)
    assert column_exists(conn, "no_such_table", "name") is False


# ---------------------------------------------------------------------------
# SQLite — StorageConnection-wrapped
# ---------------------------------------------------------------------------


def test_table_exists_sqlite_wrapped():
    conn = _make_sqlite(raw=False)
    assert table_exists(conn, "widgets") is True
    assert table_exists(conn, "nonexistent") is False


def test_list_tables_sqlite_wrapped():
    conn = _make_sqlite(raw=False)
    assert list_tables(conn) == ["gadgets", "widgets"]


def test_column_exists_sqlite_wrapped():
    conn = _make_sqlite(raw=False)
    assert column_exists(conn, "widgets", "name") is True
    assert column_exists(conn, "widgets", "missing_col") is False


# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["bad-name", "a b", "1abc", "", "drop;", 'x"y', "a.b"])
def test_invalid_identifier_raises(bad):
    conn = _make_sqlite(raw=True)
    with pytest.raises(ValueError):
        table_exists(conn, bad)
    with pytest.raises(ValueError):
        column_exists(conn, bad, "name")
    with pytest.raises(ValueError):
        column_exists(conn, "widgets", bad)


# ---------------------------------------------------------------------------
# PostgreSQL path — fake raw psycopg2-style connection (asserts exact SQL/params)
# ---------------------------------------------------------------------------


def test_table_exists_pg_sql_and_params():
    cur = _FakePgCursor(fetchone_result=(1,))
    conn = _FakePgConnection(cur)
    assert table_exists(conn, "widgets") is True
    sql, params = cur.executed[0]
    assert sql == (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s LIMIT 1"
    )
    assert params == ("widgets",)


def test_table_exists_pg_missing_returns_false():
    cur = _FakePgCursor(fetchone_result=None)
    conn = _FakePgConnection(cur)
    assert table_exists(conn, "widgets") is False


def test_list_tables_pg_sql_and_dict_rows():
    # RealDictCursor yields dict rows; helper must tolerate them.
    cur = _FakePgCursor(
        fetchall_result=[{"table_name": "alpha"}, {"table_name": "beta"}]
    )
    conn = _FakePgConnection(cur)
    assert list_tables(conn) == ["alpha", "beta"]
    sql, params = cur.executed[0]
    assert sql == (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    assert params is None


def test_column_exists_pg_sql_and_params():
    cur = _FakePgCursor(fetchone_result=(1,))
    conn = _FakePgConnection(cur)
    assert column_exists(conn, "widgets", "name") is True
    sql, params = cur.executed[0]
    assert sql == (
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "AND column_name = %s LIMIT 1"
    )
    assert params == ("widgets", "name")


def test_column_exists_pg_missing_returns_false():
    cur = _FakePgCursor(fetchone_result=None)
    conn = _FakePgConnection(cur)
    assert column_exists(conn, "widgets", "missing") is False


def test_pg_helpers_never_use_pragma_or_sqlite_master():
    """The PG path must build information_schema SQL — never PRAGMA/sqlite_master."""
    cur = _FakePgCursor(fetchone_result=(1,), fetchall_result=[{"table_name": "x"}])
    conn = _FakePgConnection(cur)
    table_exists(conn, "widgets")
    list_tables(conn)
    column_exists(conn, "widgets", "name")
    for sql, _ in cur.executed:
        assert "information_schema" in sql
        assert "sqlite_master" not in sql.lower()
        assert "pragma" not in sql.lower()


# ---------------------------------------------------------------------------
# Live PostgreSQL — only when reachable
# ---------------------------------------------------------------------------


def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _pg_conn_or_skip():
    try:
        import psycopg2  # noqa: F401
    except Exception:
        pytest.skip("psycopg2 not installed")
    _load_env()
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "127.0.0.1"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            connect_timeout=3,
        )
        return conn
    except Exception as e:  # sandbox blocks localhost / server down
        pytest.skip(f"live PostgreSQL not reachable: {e}")


def test_live_pg_introspection():
    conn = _pg_conn_or_skip()
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TEMP TABLE _pgrt_probe (id INTEGER PRIMARY KEY, label TEXT)"
        )
        conn.commit()
        # TEMP tables live in a pg_temp schema, so information_schema('public')
        # will NOT see them — assert the helper runs and answers consistently
        # against a real catalogue instead.
        tables = list_tables(conn)
        assert isinstance(tables, list)
        # A well-known ICDEV table should generally exist; if the schema is bare
        # (fresh worktree DB) just assert the call succeeded without raising.
        assert table_exists(conn, "definitely_not_a_real_table_xyz") is False
        assert column_exists(conn, "definitely_not_a_real_table_xyz", "id") is False
    finally:
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Linter tightening — rule-14 / rule-1 shape escalation
# ---------------------------------------------------------------------------


def _load_linter():
    import importlib.util

    path = PROJECT_ROOT / "tools" / "lint" / "pg_portability_linter.py"
    spec = importlib.util.spec_from_file_location("_pgp_linter_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan_snippet(linter, tmp_path, src):
    f = tmp_path / "probe_module.py"
    f.write_text(src, encoding="utf-8")
    return linter.scan_file(f)


@pytest.mark.parametrize(
    "src,expected",
    [
        # rule-14-matching sqlite_master shapes stay MEDIUM
        ("cur.execute(\"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?\", (t,))", "medium"),
        ("cur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")", "medium"),
        ("cur.execute(\"SELECT count(*) FROM sqlite_master WHERE type='table'\")", "medium"),
        # bypassing sqlite_master usage is HIGH
        ("cur.execute(\"SELECT sql FROM sqlite_master WHERE name='foo'\")", "high"),
        # rule-1 PRAGMA table_info stays MEDIUM
        ('cur.execute("PRAGMA table_info(users)")', "medium"),
        # config PRAGMA stays MEDIUM (no-op on PG, not introspection)
        ('conn.execute("PRAGMA journal_mode=WAL")', "medium"),
        # other introspection PRAGMA is HIGH
        ('cur.execute("PRAGMA index_list(users)")', "high"),
        ('cur.execute("PRAGMA foreign_key_list(users)")', "high"),
    ],
)
def test_linter_rule14_shape_severity(tmp_path, src, expected):
    linter = _load_linter()
    findings = _scan_snippet(linter, tmp_path, src)
    assert findings, f"expected a finding for: {src}"
    sev = {f["severity"] for f in findings if f["pattern"] in ("sqlite_master", "PRAGMA ")}
    assert expected in sev, f"{src!r} -> {sev}, expected {expected}"
