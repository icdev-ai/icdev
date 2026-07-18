# CUI // SP-CTI
"""Regression tests for the tools/dashboard/** introspection sweep (pgrt-sweep-03).

Ad-hoc ``sqlite_master`` / ``PRAGMA`` probes in the dashboard were replaced with
the backend-aware ``tools.db.storage`` helpers (``table_exists``, ``list_tables``,
``column_exists``).  These tests pin the observable behaviour of the class-B fixes
that previously broke or silently skipped on PostgreSQL:

* ``nlq_processor.extract_schema`` — enumerated tables via a partially-translatable
  ``sqlite_master ... NOT LIKE`` query that produced invalid SQL on PG; now uses
  ``list_tables``.
* the per-module ``_table_exists`` wrappers now delegate to the shared helper, so
  their PostgreSQL path must build ``information_schema`` SQL (never ``sqlite_master``
  or ``PRAGMA``).

The dashboard Flask app is intentionally NOT imported (it probes local LLM servers
and hangs) — only the touched functions/modules are exercised directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fake PostgreSQL connection (asserts the PG introspection SQL without a server)
# ---------------------------------------------------------------------------


class _FakePgCursor:
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
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


# Backend detection sniffs the module name to decide PostgreSQL.
_FakePgConnection.__module__ = "psycopg2.extensions"


# ---------------------------------------------------------------------------
# nlq_processor.extract_schema — class-B fix (list_tables)
# ---------------------------------------------------------------------------


def test_extract_schema_enumerates_tables_and_columns(tmp_path):
    from tools.dashboard import nlq_processor
    from tools.db.storage import get_connection

    db = tmp_path / "nlq_probe.db"
    conn = get_connection(db_path=str(db))
    conn.execute("CREATE TABLE alpha (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)")
    conn.execute("CREATE TABLE beta (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    schema = nlq_processor.extract_schema(db_path=db)

    assert {"alpha", "beta"} <= set(schema.keys())
    col_names = {c["name"] for c in schema["alpha"]["columns"]}
    assert {"id", "name", "qty"} <= col_names
    assert schema["alpha"]["row_count"] == 0


def test_extract_schema_excludes_sqlite_internal_tables(tmp_path):
    # list_tables filters sqlite_* internal tables; extract_schema must not choke.
    from tools.dashboard import nlq_processor
    from tools.db.storage import get_connection

    db = tmp_path / "nlq_probe2.db"
    conn = get_connection(db_path=str(db))
    # AUTOINCREMENT forces creation of the internal sqlite_sequence table.
    conn.execute("CREATE TABLE gamma (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn.commit()
    conn.close()

    schema = nlq_processor.extract_schema(db_path=db)
    assert "gamma" in schema
    assert not any(t.startswith("sqlite_") for t in schema)


# ---------------------------------------------------------------------------
# Per-module _table_exists wrappers now delegate to the shared helper
# ---------------------------------------------------------------------------


def test_lineage_table_exists_sqlite_roundtrip(tmp_path):
    from tools.dashboard.api import lineage
    from tools.db.storage import get_connection

    db = tmp_path / "lineage_probe.db"
    conn = get_connection(db_path=str(db))
    conn.execute("CREATE TABLE digital_thread_links (id INTEGER PRIMARY KEY)")
    conn.commit()

    assert lineage._table_exists(conn, "digital_thread_links") is True
    assert lineage._table_exists(conn, "no_such_table") is False
    conn.close()


def test_provenance_table_exists_sqlite_roundtrip(tmp_path):
    from tools.dashboard.pages import provenance
    from tools.db.storage import get_connection

    db = tmp_path / "prov_probe.db"
    conn = get_connection(db_path=str(db))
    conn.execute("CREATE TABLE prov_entities (id INTEGER PRIMARY KEY)")
    conn.commit()

    assert provenance._table_exists(conn, "prov_entities") is True
    assert provenance._table_exists(conn, "missing") is False
    conn.close()


def test_api_table_exists_pg_path_uses_information_schema():
    """On PostgreSQL the wrapper must query information_schema, never sqlite_master."""
    from tools.dashboard.api import cato, iac, lineage, orchestration
    from tools.dashboard.pages import provenance

    for mod in (iac, cato, lineage, orchestration, provenance):
        cur = _FakePgCursor(fetchone_result=(1,))
        conn = _FakePgConnection(cur)
        assert mod._table_exists(conn, "widgets") is True, mod.__name__
        assert cur.executed, mod.__name__
        sql, params = cur.executed[0]
        assert "information_schema" in sql, mod.__name__
        assert "sqlite_master" not in sql.lower(), mod.__name__
        assert "pragma" not in sql.lower(), mod.__name__
        assert params == ("widgets",), mod.__name__
