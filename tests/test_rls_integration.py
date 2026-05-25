#!/usr/bin/env python3
# CUI // SP-CTI
"""RLS integration tests — StorageCursor._inject_rls() end-to-end.

Verifies that row-level security predicates are correctly injected and
that SQLite parameter ordering is preserved for SELECT, UPDATE, DELETE.

Run: pytest tests/test_rls_integration.py -v --tb=short
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Minimal SecurityContext stand-in (mirrors tools.security.security_context)
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    tenant_id: Optional[str] = None
    classification: Optional[str] = None  # None = no classification filter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """In-memory SQLite DB with a test table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT,
            tenant_id TEXT,
            classification TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
        [
            ("p1", "Alpha", "active", "tenant_a", "CUI"),
            ("p2", "Beta",  "inactive", "tenant_b", "CUI"),
            ("p3", "Gamma", "active", "tenant_a", "SECRET"),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def _cursor_with_ctx(db_conn, ctx):
    """Return a StorageCursor backed by db_conn with ctx attached."""
    from tools.db.storage import StorageCursor
    cur = StorageCursor(db_conn.cursor(), backend="sqlite")
    cur.set_security_context(ctx)
    return cur


# ---------------------------------------------------------------------------
# SELECT — predicate prepended, params prepended
# ---------------------------------------------------------------------------

class TestRLSSelect:
    def test_tenant_filters_rows(self, db):
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        cur.execute("SELECT id FROM projects", ())
        rows = cur.fetchall()
        ids = {r[0] for r in rows}
        assert ids == {"p1", "p3"}, "tenant_b row must be excluded"

    def test_no_context_returns_all(self, db):
        from tools.db.storage import StorageCursor
        cur = StorageCursor(db.cursor(), backend="sqlite")
        cur.execute("SELECT id FROM projects", ())
        rows = cur.fetchall()
        assert len(rows) == 3

    def test_existing_where_and_tenant(self, db):
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        cur.execute("SELECT id FROM projects WHERE status = ?", ("active",))
        rows = cur.fetchall()
        ids = {r[0] for r in rows}
        # p3 is active + tenant_a (no classification filter) → included
        assert ids == {"p1", "p3"}

    def test_classification_filter(self, db):
        # CUI context: predicate = (classification IS NULL OR '' OR = 'CUI')
        # p3 has classification='SECRET' → excluded; p1 has 'CUI' → included
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification="CUI"))
        cur.execute("SELECT id FROM projects", ())
        rows = cur.fetchall()
        ids = {r[0] for r in rows}
        assert "p3" not in ids  # SECRET blocked by CUI context
        assert "p1" in ids      # CUI passes

    def test_tenant_only_no_classification_filter(self, db):
        # classification=None → no classification predicate, both p1 (CUI) and p3 (SECRET) returned
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        cur.execute("SELECT id FROM projects", ())
        rows = cur.fetchall()
        ids = {r[0] for r in rows}
        assert ids == {"p1", "p3"}, "both tenant_a rows must appear without classification filter"


# ---------------------------------------------------------------------------
# UPDATE — predicate appended, params appended
# ---------------------------------------------------------------------------

class TestRLSUpdate:
    def test_update_scoped_to_tenant(self, db):
        """UPDATE with existing WHERE — predicate appended, only tenant_a rows touched."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        # Set-param: "archived"; WHERE-param: "active"; RLS-param: "tenant_a" (appended)
        cur.execute(
            "UPDATE projects SET status = ? WHERE status = ?",
            ("archived", "active"),
        )
        db.commit()

        check = db.execute("SELECT id, status, tenant_id FROM projects").fetchall()
        by_id = {r[0]: (r[1], r[2]) for r in check}

        # p1: tenant_a + active → should be archived
        assert by_id["p1"][0] == "archived"
        # p3: tenant_a + active (SECRET) → should be archived
        assert by_id["p3"][0] == "archived"
        # p2: tenant_b + inactive → should remain inactive (not touched by tenant_a ctx)
        assert by_id["p2"][0] == "inactive"

    def test_update_without_existing_where(self, db):
        """UPDATE without WHERE — RLS injects WHERE clause."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_b"))
        cur.execute("UPDATE projects SET status = ?", ("flagged",))
        db.commit()

        check = db.execute("SELECT id, status FROM projects ORDER BY id").fetchall()
        by_id = {r[0]: r[1] for r in check}

        assert by_id["p2"] == "flagged"   # tenant_b → updated
        assert by_id["p1"] != "flagged"   # tenant_a → not touched
        assert by_id["p3"] != "flagged"   # tenant_a → not touched

    def test_update_set_params_not_corrupted(self, db):
        """Confirm SET-slot params bind correctly (not shifted by RLS injection)."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a"))
        cur.execute("UPDATE projects SET name = ? WHERE id = ?", ("Renamed", "p1"))
        db.commit()

        row = db.execute("SELECT name FROM projects WHERE id = 'p1'").fetchone()
        assert row[0] == "Renamed"

        # p2 (tenant_b) must be untouched even though id filter was specific
        row2 = db.execute("SELECT name FROM projects WHERE id = 'p2'").fetchone()
        assert row2[0] == "Beta"


# ---------------------------------------------------------------------------
# DELETE — predicate appended, params appended
# ---------------------------------------------------------------------------

class TestRLSDelete:
    def test_delete_scoped_to_tenant(self, db):
        """DELETE with WHERE — only tenant_a rows matching condition deleted."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        cur.execute("DELETE FROM projects WHERE status = ?", ("active",))
        db.commit()

        remaining = {r[0] for r in db.execute("SELECT id FROM projects").fetchall()}
        assert "p2" in remaining   # tenant_b: unaffected
        # p1 and p3 are active + tenant_a → deleted
        assert "p1" not in remaining
        assert "p3" not in remaining

    def test_delete_without_where(self, db):
        """DELETE without WHERE — RLS injects tenant filter."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_b"))
        cur.execute("DELETE FROM projects", ())
        db.commit()

        remaining = {r[0] for r in db.execute("SELECT id FROM projects").fetchall()}
        assert "p1" in remaining   # tenant_a: protected
        assert "p3" in remaining   # tenant_a: protected
        assert "p2" not in remaining  # tenant_b: deleted

    def test_no_cross_tenant_delete(self, db):
        """tenant_b context cannot delete tenant_a rows even with no WHERE."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_b"))
        cur.execute("DELETE FROM projects", ())
        db.commit()

        count_a = db.execute(
            "SELECT COUNT(*) FROM projects WHERE tenant_id = 'tenant_a'"
        ).fetchone()[0]
        assert count_a == 2


# ---------------------------------------------------------------------------
# executemany — RLS predicates must be injected per row
# ---------------------------------------------------------------------------

class TestRLSExecutemany:
    def test_executemany_update_scoped_to_tenant(self, db):
        """Batch UPDATE: RLS params appended; only tenant_a rows updated."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a", classification=None))
        cur.executemany(
            "UPDATE projects SET status = ? WHERE id = ?",
            [("updated", "p1"), ("updated", "p2")],
        )
        db.commit()

        check = {r[0]: r[1] for r in db.execute("SELECT id, status FROM projects").fetchall()}
        assert check["p1"] == "updated"   # tenant_a → updated
        assert check["p2"] == "inactive"  # tenant_b → RLS predicate blocks update

    def test_executemany_insert_unmodified(self, db):
        """INSERT must never receive RLS injection even in executemany."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a"))
        cur.executemany(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [
                ("p4", "Delta", "active", "tenant_b", "CUI"),
                ("p5", "Echo", "inactive", "tenant_c", "SECRET"),
            ],
        )
        db.commit()

        rows = db.execute("SELECT tenant_id FROM projects WHERE id IN ('p4','p5')").fetchall()
        assert rows[0][0] == "tenant_b"
        assert rows[1][0] == "tenant_c"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

class TestRLSNoOp:
    def test_insert_unmodified(self, db):
        """INSERT must never receive RLS injection."""
        cur = _cursor_with_ctx(db, _Ctx(tenant_id="tenant_a"))
        cur.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            ("p4", "Delta", "active", "tenant_b", "CUI"),
        )
        db.commit()
        row = db.execute("SELECT tenant_id FROM projects WHERE id = 'p4'").fetchone()
        # Row inserted as-is — tenant_b despite ctx=tenant_a (INSERT not filtered)
        assert row[0] == "tenant_b"

    def test_pragma_unmodified(self, db):
        """PRAGMA must not receive RLS injection."""
        from tools.security.row_security import inject_row_predicate
        sql, params = inject_row_predicate("PRAGMA table_info(projects)", "tenant_a")
        assert sql == "PRAGMA table_info(projects)"
        assert params == ()
