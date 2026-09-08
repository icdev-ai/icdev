# CUI // SP-CTI
"""A fixture must GUARANTEE its table's shape, not assume it (dwr-anchor-02).

REPRODUCED, deterministically, with no test-ordering trigger required::

    an earlier module leaves dic_documents WITHOUT `status`
    -> `CREATE TABLE IF NOT EXISTS dic_documents (... status ...)` NO-OPS
    -> `INSERT INTO dic_documents (... status ...)` raises
       sqlite3.OperationalError: table dic_documents has no column named status

That is what failed PR #2167 in CI shard 2 while the same file passed locally,
alone, in its directory, and in file order. The shard partition decides whether
the precondition happens, and crx-test-07 re-bin-packs shards whenever test
files are added — so it comes and goes with unrelated edits.

The first test here is the one that matters: it asserts the RAW pattern still
breaks. If SQLite ever changed so `CREATE TABLE IF NOT EXISTS` reshaped a table,
this whole module would be unnecessary, and it should fail loudly rather than
quietly protecting against nothing.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests._schema_compat import declared_columns, ensure_table, existing_columns

WIDE = """CREATE TABLE IF NOT EXISTS dic_documents (
    doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT,
    status TEXT, origin TEXT, created_at TEXT)"""

NARROW = "CREATE TABLE dic_documents (doc_id TEXT PRIMARY KEY, collection_id TEXT, created_at TEXT)"


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    yield c
    c.close()


# ---------------------------------------------------- the defect, still real
def test_bare_if_not_exists_silently_keeps_the_narrow_shape(conn):
    """The behaviour this module exists because of. Not a hypothetical."""
    conn.execute(NARROW)
    conn.execute(WIDE)  # the bare pattern a fixture would use
    assert "status" not in existing_columns(conn, "dic_documents")
    with pytest.raises(sqlite3.OperationalError, match="no column named status"):
        conn.execute(
            "INSERT INTO dic_documents (doc_id, status) VALUES ('d1','approved')"
        )


# ------------------------------------------------------------------ the fix
def test_ensure_table_adds_the_missing_columns(conn):
    conn.execute(NARROW)
    added = ensure_table(conn, WIDE)
    assert set(added) == {"title", "status", "origin"}
    cols = existing_columns(conn, "dic_documents")
    assert {"status", "origin", "title"} <= cols
    conn.execute("INSERT INTO dic_documents (doc_id, status) VALUES ('d1','approved')")


def test_ensure_table_on_a_fresh_database_adds_nothing(conn):
    """A table created from the declaration is already the declared shape."""
    assert ensure_table(conn, WIDE) == []
    assert "status" in existing_columns(conn, "dic_documents")


def test_ensure_table_is_idempotent(conn):
    conn.execute(NARROW)
    ensure_table(conn, WIDE)
    assert ensure_table(conn, WIDE) == []


def test_existing_rows_survive_the_repair(conn):
    """Additive, never DROP-and-recreate — a dropped table takes other modules' rows."""
    conn.execute(NARROW)
    conn.execute("INSERT INTO dic_documents (doc_id, collection_id) VALUES ('keep','c')")
    conn.commit()
    ensure_table(conn, WIDE)
    rows = conn.execute("SELECT doc_id FROM dic_documents").fetchall()
    assert [r[0] for r in rows] == ["keep"]


# -------------------------------------------------------------- the parsing
def test_a_check_constraint_does_not_invent_columns():
    """Commas inside CHECK(...) are not column separators."""
    table, cols = declared_columns(
        """CREATE TABLE IF NOT EXISTS t (
            id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','shut','held')),
            note TEXT)"""
    )
    assert table == "t"
    assert [c for c, _ in cols] == ["id", "state", "note"]


def test_table_constraints_are_not_read_as_columns():
    _, cols = declared_columns(
        """CREATE TABLE IF NOT EXISTS t (
            a TEXT, b TEXT,
            PRIMARY KEY (a, b),
            UNIQUE (b),
            FOREIGN KEY (a) REFERENCES other(id))"""
    )
    assert [c for c, _ in cols] == ["a", "b"]


def test_a_column_sqlite_refuses_to_add_is_still_added(conn):
    """SQLite cannot ADD a UNIQUE / NOT NULL-without-default column.

    The fixture only needs the column to EXIST. Dropping the modifier is the
    difference between a repaired fixture and one that silently keeps the old
    shape — which is the defect, not a lesser version of it.
    """
    conn.execute("CREATE TABLE t (id TEXT)")
    added = ensure_table(
        conn,
        """CREATE TABLE IF NOT EXISTS t (
            id TEXT, tag TEXT UNIQUE, must TEXT NOT NULL)""",
    )
    assert set(added) == {"tag", "must"}
    assert {"tag", "must"} <= existing_columns(conn, "t")


def test_a_non_create_statement_raises_rather_than_doing_nothing():
    """A helper that no-ops on the wrong input guarantees nothing, silently."""
    with pytest.raises(ValueError):
        declared_columns("SELECT 1")
