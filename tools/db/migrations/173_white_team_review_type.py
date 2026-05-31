# CUI // SP-CTI
"""Migration 173: Add 'white_team' to proposal_reviews review_type CHECK constraint.

SQLite does not support ALTER TABLE to modify CHECK constraints. This migration
recreates the proposal_reviews table with 'white_team' added to the allowed
review_type values, preserving all existing rows.

Idempotent: if white_team is already in the constraint (new DB), the migration
detects that and skips the table recreation.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def migrate() -> None:
    """Apply migration 173 against the main ICDEV DB (idempotent)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        # Check if white_team is already allowed by inserting a test row
        # We detect the old constraint by querying sqlite_master for the table DDL.
        ddl_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='proposal_reviews'"
        ).fetchone()
        if ddl_row is None:
            print("[migration 173] proposal_reviews table not found — skipping")
            return

        ddl = ddl_row[0] if isinstance(ddl_row, (tuple, list)) else ddl_row["sql"]
        if "white_team" in ddl:
            print("[migration 173] white_team already present in proposal_reviews — skipping")
            return

        print("[migration 173] Recreating proposal_reviews with white_team review_type...")

        conn.execute("PRAGMA foreign_keys = OFF")

        # Rename old table
        conn.execute("ALTER TABLE proposal_reviews RENAME TO _proposal_reviews_old")

        # Create new table with updated CHECK constraint
        conn.execute("""
CREATE TABLE proposal_reviews (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES proposal_opportunities(id),
    review_type TEXT NOT NULL CHECK(review_type IN (
        'pink_team', 'red_team', 'gold_team', 'white_team', 'white_glove', 'internal')),
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN (
        'scheduled', 'in_progress', 'completed', 'cancelled')),
    scheduled_date TEXT,
    started_at TEXT,
    completed_at TEXT,
    lead_reviewer TEXT,
    participants TEXT,
    summary TEXT,
    overall_rating TEXT CHECK(overall_rating IN (
        'pass', 'pass_with_findings', 'major_rework', 'fail')),
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
)
        """)

        # Copy all existing rows
        conn.execute("""
INSERT INTO proposal_reviews
    (id, opportunity_id, review_type, status, scheduled_date,
     started_at, completed_at, lead_reviewer, participants,
     summary, overall_rating, classification, created_at)
SELECT
    id, opportunity_id, review_type, status, scheduled_date,
    started_at, completed_at, lead_reviewer, participants,
    summary, overall_rating, classification, created_at
FROM _proposal_reviews_old
        """)

        # Recreate indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prop_rev_opp ON proposal_reviews(opportunity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prop_rev_type ON proposal_reviews(review_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prop_rev_status ON proposal_reviews(status)")

        # Drop the old table
        conn.execute("DROP TABLE _proposal_reviews_old")

        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        print("[migration 173] proposal_reviews recreated with white_team support")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"[migration 173] failed: {exc}") from exc
    finally:
        conn.close()


def main() -> int:
    """CLI entry point."""
    print("Migration 173: Add white_team to proposal_reviews review_type constraint")
    migrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
