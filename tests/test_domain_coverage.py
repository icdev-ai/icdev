# CUI // SP-CTI
"""Unit tests for the domain_coverage table schema.

Verifies INSERT, SELECT, and UPDATE behaviour against an in-memory SQLite DB
so no real database file is touched.  Covers schema constraints (CHECK, NOT
NULL, DEFAULT) as the acceptance criterion requires ≥3 assertions.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── SQLite DDL (mirrors the @sqlite-only block in migration 206) ─────────────

_DDL = """
CREATE TABLE IF NOT EXISTS domain_coverage (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_key       TEXT    NOT NULL,
    domain_name      TEXT    NOT NULL,
    domain_type      TEXT    NOT NULL DEFAULT 'knowledge'
                     CHECK (domain_type IN ('knowledge', 'data', 'system', 'integration', 'canvas')),
    source_canvas    TEXT,
    coverage_score   REAL    NOT NULL DEFAULT 0.0
                     CHECK (coverage_score >= 0.0 AND coverage_score <= 1.0),
    gap_count        INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'orphaned', 'resolved', 'pending')),
    orphan_reason    TEXT
                     CHECK (orphan_reason IS NULL OR orphan_reason IN (
                         'no_canvas', 'no_source', 'schema_mismatch',
                         'stale_data', 'removed_integration'
                     )),
    last_checked_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at      TEXT,
    detail           TEXT    NOT NULL DEFAULT '{}',
    tenant_id        TEXT    NOT NULL DEFAULT 'default',
    classification   TEXT    NOT NULL DEFAULT 'CUI',
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA enforce_checks = ON")
    conn.executescript(_DDL)
    yield conn
    conn.close()


# ── INSERT / SELECT ───────────────────────────────────────────────────────────


class TestInsertAndRetrieve:
    def test_insert_minimal_row_and_retrieve(self, db):
        db.execute(
            "INSERT INTO domain_coverage (domain_key, domain_name) VALUES (?, ?)",
            ("rag", "RAG Knowledge Domain"),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM domain_coverage WHERE domain_key = ?", ("rag",)
        ).fetchone()
        assert row is not None
        assert row["domain_name"] == "RAG Knowledge Domain"
        assert row["domain_type"] == "knowledge"   # DEFAULT
        assert row["status"] == "active"           # DEFAULT
        assert row["coverage_score"] == pytest.approx(0.0)  # DEFAULT

    def test_insert_full_row_and_retrieve_all_fields(self, db):
        db.execute(
            """INSERT INTO domain_coverage
               (domain_key, domain_name, domain_type, source_canvas,
                coverage_score, gap_count, status, orphan_reason,
                detail, tenant_id, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "network",
                "Network Canvas Domain",
                "canvas",
                "network_canvas",
                0.72,
                3,
                "active",
                None,
                '{"note": "partial"}',
                "acme",
                "CUI//SP-CTI",
            ),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM domain_coverage WHERE domain_key = ?", ("network",)
        ).fetchone()
        assert row["domain_type"] == "canvas"
        assert row["source_canvas"] == "network_canvas"
        assert row["coverage_score"] == pytest.approx(0.72)
        assert row["gap_count"] == 3
        assert row["orphan_reason"] is None
        assert row["tenant_id"] == "acme"
        assert row["classification"] == "CUI//SP-CTI"

    def test_insert_orphaned_row_with_reason(self, db):
        db.execute(
            """INSERT INTO domain_coverage
               (domain_key, domain_name, status, orphan_reason)
               VALUES (?, ?, ?, ?)""",
            ("legacy_data", "Legacy Data Domain", "orphaned", "no_canvas"),
        )
        db.commit()
        row = db.execute(
            "SELECT status, orphan_reason FROM domain_coverage WHERE domain_key = ?",
            ("legacy_data",),
        ).fetchone()
        assert row["status"] == "orphaned"
        assert row["orphan_reason"] == "no_canvas"

    def test_multiple_rows_for_same_domain_key(self, db):
        """Append-only intent: multiple scan rows allowed per domain_key."""
        for score in (0.4, 0.6, 0.8):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, coverage_score) VALUES (?, ?, ?)",
                ("iam", "IAM Domain", score),
            )
        db.commit()
        rows = db.execute(
            "SELECT coverage_score FROM domain_coverage WHERE domain_key = ? ORDER BY id",
            ("iam",),
        ).fetchall()
        assert len(rows) == 3
        assert [r["coverage_score"] for r in rows] == pytest.approx([0.4, 0.6, 0.8])


# ── UPDATE ────────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_status_transition_active_to_resolved(self, db):
        db.execute(
            "INSERT INTO domain_coverage (domain_key, domain_name) VALUES (?, ?)",
            ("compliance", "Compliance Domain"),
        )
        db.commit()
        db.execute(
            "UPDATE domain_coverage SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE domain_key = ?",
            ("resolved", "compliance"),
        )
        db.commit()
        row = db.execute(
            "SELECT status, resolved_at FROM domain_coverage WHERE domain_key = ?",
            ("compliance",),
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None

    def test_update_coverage_score(self, db):
        db.execute(
            "INSERT INTO domain_coverage (domain_key, domain_name, coverage_score) VALUES (?, ?, ?)",
            ("supply_chain", "Supply Chain Domain", 0.3),
        )
        db.commit()
        db.execute(
            "UPDATE domain_coverage SET coverage_score = ? WHERE domain_key = ?",
            (0.95, "supply_chain"),
        )
        db.commit()
        row = db.execute(
            "SELECT coverage_score FROM domain_coverage WHERE domain_key = ?",
            ("supply_chain",),
        ).fetchone()
        assert row["coverage_score"] == pytest.approx(0.95)

    def test_update_orphan_reason(self, db):
        db.execute(
            "INSERT INTO domain_coverage (domain_key, domain_name, status) VALUES (?, ?, ?)",
            ("data_lake", "Data Lake Domain", "pending"),
        )
        db.commit()
        db.execute(
            "UPDATE domain_coverage SET status = ?, orphan_reason = ? WHERE domain_key = ?",
            ("orphaned", "stale_data", "data_lake"),
        )
        db.commit()
        row = db.execute(
            "SELECT status, orphan_reason FROM domain_coverage WHERE domain_key = ?",
            ("data_lake",),
        ).fetchone()
        assert row["status"] == "orphaned"
        assert row["orphan_reason"] == "stale_data"


# ── CHECK constraint violations ───────────────────────────────────────────────


class TestCheckConstraints:
    def test_invalid_domain_type_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, domain_type) VALUES (?, ?, ?)",
                ("x", "X", "invalid_type"),
            )

    def test_coverage_score_below_zero_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, coverage_score) VALUES (?, ?, ?)",
                ("x", "X", -0.1),
            )

    def test_coverage_score_above_one_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, coverage_score) VALUES (?, ?, ?)",
                ("x", "X", 1.001),
            )

    def test_invalid_status_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, status) VALUES (?, ?, ?)",
                ("x", "X", "deleted"),
            )

    def test_invalid_orphan_reason_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, orphan_reason) VALUES (?, ?, ?)",
                ("x", "X", "bad_reason"),
            )

    def test_null_domain_key_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name) VALUES (NULL, ?)",
                ("X",),
            )

    def test_null_domain_name_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name) VALUES (?, NULL)",
                ("x",),
            )

    def test_all_valid_orphan_reasons_accepted(self, db):
        valid = ("no_canvas", "no_source", "schema_mismatch", "stale_data", "removed_integration")
        for reason in valid:
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, orphan_reason) VALUES (?, ?, ?)",
                (f"key_{reason}", "D", reason),
            )
        db.commit()
        count = db.execute(
            "SELECT COUNT(*) FROM domain_coverage WHERE orphan_reason IS NOT NULL"
        ).fetchone()[0]
        assert count == len(valid)

    def test_all_valid_statuses_accepted(self, db):
        for status in ("active", "orphaned", "resolved", "pending"):
            db.execute(
                "INSERT INTO domain_coverage (domain_key, domain_name, status) VALUES (?, ?, ?)",
                (f"key_{status}", "D", status),
            )
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM domain_coverage").fetchone()[0]
        assert count == 4
