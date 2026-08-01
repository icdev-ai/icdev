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
    compartments: frozenset = frozenset()  # LAC_* / COI_* tags


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
        # 3-tuple: (sql, extra_params, n_params_before). The third element was
        # added so callers can splice extra params at the right index when a
        # subquery precedes the outer WHERE; this test still unpacked two.
        sql, params, _n_before = inject_row_predicate(
            "PRAGMA table_info(projects)", "tenant_a")
        assert sql == "PRAGMA table_info(projects)"
        assert params == ()


# ---------------------------------------------------------------------------
# LAC integration — Label-Based Access Control predicate via compartments
# ---------------------------------------------------------------------------

@pytest.fixture()
def lac_db():
    """In-memory DB with a lac_label column on the projects table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            tenant_id TEXT,
            classification TEXT,
            lac_label TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
        [
            ("p1", "Public",      "tenant_a", "CUI",    None),
            ("p2", "DC East",     "tenant_a", "CUI",    "LAC_DC_EAST"),
            ("p3", "NOFORN",      "tenant_a", "CUI",    "LAC_NOFORN"),
            ("p4", "Other tenant","tenant_b", "CUI",    None),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


class TestLAC:
    def test_null_lac_rows_always_visible(self, lac_db):
        """Rows with lac_label=NULL are world-readable within the tenant."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"LAC_DC_EAST"}))
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p1" in ids  # NULL lac_label → visible

    def test_matching_lac_label_visible(self, lac_db):
        """Row with matching lac_label is returned."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"LAC_DC_EAST"}))
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p2" in ids  # LAC_DC_EAST matches caller's compartment

    def test_non_matching_lac_label_filtered(self, lac_db):
        """Row with a label the caller doesn't hold is hidden."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"LAC_DC_EAST"}))
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p3" not in ids  # LAC_NOFORN not in caller's compartments

    def test_multiple_lac_labels_union(self, lac_db):
        """Caller with multiple LAC labels sees all matching rows."""
        ctx = _Ctx(
            tenant_id="tenant_a",
            compartments=frozenset({"LAC_DC_EAST", "LAC_NOFORN"}),
        )
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p2" in ids
        assert "p3" in ids

    def test_no_lac_compartments_no_filtering(self, lac_db):
        """With no LAC_* compartments, no LAC predicate is injected — all rows visible.

        Enforcement of 'no compartments → hide labeled rows' must happen at a higher
        layer (application/ABAC) that can inspect both the table schema and the caller's
        clearance, not at the generic SQL predicate injection layer which has no
        knowledge of per-table column presence.
        """
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset())
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        # All tenant_a rows visible — no LAC predicate injected for empty set
        assert "p1" in ids
        assert "p2" in ids
        assert "p3" in ids

    def test_cross_tenant_still_blocked(self, lac_db):
        """tenant_b rows are never returned to a tenant_a context, even with matching label."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"LAC_DC_EAST"}))
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p4" not in ids  # tenant_b row always excluded

    def test_lac_update_scoped(self, lac_db):
        """Batch UPDATE only touches rows the caller's LAC label permits."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"LAC_DC_EAST"}))
        cur = _cursor_with_ctx(lac_db, ctx)
        cur.execute("UPDATE projects SET name = ?", ("touched",))
        lac_db.commit()

        rows = {r[0]: r[1] for r in lac_db.execute("SELECT id, name FROM projects").fetchall()}
        assert rows["p1"] == "touched"   # NULL lac_label → visible → updated
        assert rows["p2"] == "touched"   # LAC_DC_EAST matches → updated
        assert rows["p3"] != "touched"   # LAC_NOFORN not in caller's set → not updated


# ---------------------------------------------------------------------------
# COI integration — Community of Interest predicate via compartments
# ---------------------------------------------------------------------------

@pytest.fixture()
def coi_db():
    """In-memory DB with a coi_tag column on the projects table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            tenant_id TEXT,
            classification TEXT,
            coi_tag TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
        [
            ("p1", "Open",        "tenant_a", "CUI", None),
            ("p2", "Finance",     "tenant_a", "CUI", "COI_FINANCE"),
            ("p3", "Intel",       "tenant_a", "CUI", "COI_INTEL"),
            ("p4", "Other tenant","tenant_b", "CUI", None),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


class TestCOI:
    def test_null_coi_rows_always_visible(self, coi_db):
        """Rows with coi_tag=NULL are accessible to all within tenant."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"COI_FINANCE"}))
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p1" in ids

    def test_matching_coi_tag_visible(self, coi_db):
        """Row with matching COI tag is returned."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"COI_FINANCE"}))
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p2" in ids

    def test_non_matching_coi_tag_filtered(self, coi_db):
        """Row with a COI tag the caller doesn't hold is hidden."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"COI_FINANCE"}))
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p3" not in ids  # COI_INTEL not in caller's compartments

    def test_multiple_coi_tags_union(self, coi_db):
        """Caller with multiple COI tags sees all matching rows."""
        ctx = _Ctx(
            tenant_id="tenant_a",
            compartments=frozenset({"COI_FINANCE", "COI_INTEL"}),
        )
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p2" in ids
        assert "p3" in ids

    def test_no_coi_compartments_no_filtering(self, coi_db):
        """With no COI_* compartments, no COI predicate is injected — all tenant rows visible."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset())
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p1" in ids
        assert "p2" in ids
        assert "p3" in ids

    def test_coi_cross_tenant_blocked(self, coi_db):
        """COI membership never grants cross-tenant access."""
        ctx = _Ctx(tenant_id="tenant_a", compartments=frozenset({"COI_FINANCE"}))
        cur = _cursor_with_ctx(coi_db, ctx)
        cur.execute("SELECT id FROM projects", ())
        ids = {r[0] for r in cur.fetchall()}
        assert "p4" not in ids

    def test_lac_and_coi_combined(self):
        """Both LAC and COI predicates injected simultaneously."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE docs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                classification TEXT,
                lac_label TEXT,
                coi_tag TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?)",
            [
                ("d1", "t1", "CUI", None,           None),
                ("d2", "t1", "CUI", "LAC_DC_EAST",  None),
                ("d3", "t1", "CUI", None,            "COI_FINANCE"),
                ("d4", "t1", "CUI", "LAC_DC_EAST",  "COI_FINANCE"),
                ("d5", "t1", "CUI", "LAC_NOFORN",   "COI_INTEL"),
            ],
        )
        conn.commit()

        ctx = _Ctx(
            tenant_id="t1",
            compartments=frozenset({"LAC_DC_EAST", "COI_FINANCE"}),
        )
        from tools.db.storage import StorageCursor
        cur = StorageCursor(conn.cursor(), backend="sqlite")
        cur.set_security_context(ctx)
        cur.execute("SELECT id FROM docs", ())
        ids = {r[0] for r in cur.fetchall()}

        assert "d1" in ids   # NULL lac + NULL coi → visible
        assert "d2" in ids   # LAC_DC_EAST matches, NULL coi → visible
        assert "d3" in ids   # NULL lac, COI_FINANCE matches → visible
        assert "d4" in ids   # both match → visible
        assert "d5" not in ids  # LAC_NOFORN and COI_INTEL both absent → hidden
        conn.close()
