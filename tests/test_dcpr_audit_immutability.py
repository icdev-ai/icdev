# CUI // SP-CTI
"""dcpr-db-02 — Data Canvas append-only immutability on PostgreSQL.

Verifies two things:

1. The Data Canvas init bootstrap installs PG (plpgsql) immutability triggers
   for BOTH ``dm_audit`` and ``dd_mapping_transforms`` (mirroring ``dd_audit``),
   so UPDATE/DELETE raise on the primary PostgreSQL backend.
2. The naive ``SCHEMA.split(";")`` was replaced by a BEGIN..END-aware splitter
   that does NOT fragment SQLite ``CREATE TRIGGER ... BEGIN ... END;`` bodies.

If a live PostgreSQL is reachable, the immutability guards are asserted
end-to-end (UPDATE/DELETE must raise). Otherwise the source-level and
splitter unit assertions still fully gate the change.
"""

import inspect
import re

import pytest

from tools.data_canvas.db import init_db as ddc_init


# ---------------------------------------------------------------------------
# 1. Splitter unit tests — the load-bearing fix
# ---------------------------------------------------------------------------

def test_splitter_keeps_sqlite_trigger_body_intact():
    """A CREATE TRIGGER ... BEGIN ... END; body must stay a single statement."""
    sql = (
        "CREATE TABLE t (id TEXT);\n"
        "CREATE TRIGGER t_no_update\n"
        "    BEFORE UPDATE ON t\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'immutable; really');\n"
        "    END;\n"
        "CREATE INDEX idx_t ON t(id);\n"
    )
    stmts = ddc_init._split_sql_statements(sql)
    assert len(stmts) == 3, stmts
    trigger = stmts[1]
    # The whole trigger, including the inner semicolon after RAISE and the
    # semicolon inside the string literal, must be in ONE statement.
    assert trigger.upper().startswith("CREATE TRIGGER")
    assert "BEGIN" in trigger.upper()
    assert trigger.rstrip().upper().endswith("END")
    assert "RAISE(ABORT" in trigger
    # And the trailing real statement survived intact.
    assert stmts[2].upper().startswith("CREATE INDEX")


def test_splitter_naive_split_would_have_fragmented():
    """Guard against regressing to SCHEMA.split(';')."""
    sql = "CREATE TRIGGER x BEFORE UPDATE ON t BEGIN SELECT RAISE(ABORT,'a;b'); END;"
    naive = [s for s in sql.split(";") if s.strip()]
    smart = ddc_init._split_sql_statements(sql)
    assert len(naive) > 1          # naive fragments it
    assert len(smart) == 1         # splitter keeps it whole


def test_splitter_handles_dollar_quoted_body():
    """PG-style $$...$$ bodies (with inner ';') must not be split."""
    sql = (
        "CREATE OR REPLACE FUNCTION f() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    RAISE EXCEPTION 'no; way';\n"
        "    RETURN NULL;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "CREATE TABLE u (id TEXT);\n"
    )
    stmts = ddc_init._split_sql_statements(sql)
    assert len(stmts) == 2, stmts
    assert "$$" in stmts[0]
    assert stmts[1].upper().startswith("CREATE TABLE U")


def test_splitter_strips_line_comments_and_empty():
    sql = "-- a comment\nCREATE TABLE a (id TEXT);\n\n-- trailing\n"
    stmts = ddc_init._split_sql_statements(sql)
    assert any(s.upper().startswith("CREATE TABLE A") for s in stmts)
    # No bare empty / comment-only statements leak through as executable DDL.
    assert all(s.strip() for s in stmts)


def test_full_schema_splits_without_naked_fragments():
    """Every SQLite trigger in the real SCHEMA stays whole after splitting."""
    stmts = ddc_init._split_sql_statements(ddc_init.SCHEMA)
    triggers = [s for s in stmts if s.upper().lstrip().startswith("CREATE TRIGGER")]
    # dd_audit (x2), dm_audit (x2), dd_mapping_transforms (x2) == 6
    assert len(triggers) >= 6, f"expected >=6 whole triggers, got {len(triggers)}"
    for trig in triggers:
        assert "BEGIN" in trig.upper() and "RAISE" in trig.upper(), trig
        # A fragmented body would be a bare "END" or a headless "SELECT RAISE".
        assert trig.rstrip().upper().endswith("END")


# ---------------------------------------------------------------------------
# 2. Source-presence: PG immutability triggers exist for both tables
# ---------------------------------------------------------------------------

_SRC = inspect.getsource(ddc_init)


@pytest.mark.parametrize(
    "table",
    ["dm_audit", "dd_mapping_transforms"],
)
def test_pg_immutability_function_present(table):
    assert re.search(
        rf"CREATE OR REPLACE FUNCTION\s+{table}_immutable\(\)",
        _SRC,
    ), f"missing plpgsql immutability function for {table}"
    assert "RAISE EXCEPTION" in _SRC


@pytest.mark.parametrize(
    "table,op",
    [
        ("dm_audit", "UPDATE"),
        ("dm_audit", "DELETE"),
        ("dd_mapping_transforms", "UPDATE"),
        ("dd_mapping_transforms", "DELETE"),
    ],
)
def test_pg_trigger_present(table, op):
    pat = rf"CREATE TRIGGER\s+{table}_no_{op.lower()}\s+BEFORE {op} ON {table}\s+FOR EACH ROW EXECUTE FUNCTION\s+{table}_immutable\(\)"
    assert re.search(pat, _SRC), f"missing PG {op} trigger for {table}"


def test_no_naive_schema_split_remains():
    assert 'SCHEMA.split(";")' not in _SRC
    assert "_split_sql_statements(SCHEMA)" in _SRC


# ---------------------------------------------------------------------------
# 3. Live PostgreSQL end-to-end (skipped when PG is not reachable)
# ---------------------------------------------------------------------------

def _pg_conn_or_skip():
    if ddc_init._DDC_BACKEND != "postgresql":
        pytest.skip("DDC backend is not postgresql")
    try:
        conn = ddc_init.get_connection()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable: {e}")
    # get_connection() silently falls back to sqlite3 on ImportError.
    if conn.__class__.__module__.startswith("sqlite3"):
        conn.close()
        pytest.skip("Canvas connection fell back to SQLite")
    return conn


@pytest.mark.parametrize("table", ["dm_audit", "dd_mapping_transforms"])
def test_live_pg_update_delete_raises(table):
    conn = _pg_conn_or_skip()
    try:
        ddc_init.init_db()
        for verb in ("UPDATE", "DELETE"):
            if verb == "UPDATE":
                sql = f"UPDATE {table} SET id = id WHERE 1=1"
            else:
                sql = f"DELETE FROM {table} WHERE 1=1"
            with pytest.raises(Exception):  # noqa: PT011
                conn.execute(sql)
                conn.commit()
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
