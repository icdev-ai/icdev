#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 261: conformance-review + pytest-ran columns on kanban_verifications.

Adds (nullable, additive — backward compatible):
  - review_passed   INTEGER   (Conformance Review gate verdict: 1/0/NULL=not-run)
  - review_findings TEXT      (JSON gap findings from the reviewer)
  - pytest_ran      INTEGER DEFAULT 0

Part of Governed Delivery Pipeline Phase 2 (record-only). The gates that write
these run in record mode by default (KANBAN_PIPELINE_ENFORCE off), so these
columns just surface real results in the dashboard pipeline view. Idempotent.
"""

MIGRATION_ID = "261"
MIGRATION_NAME = "kanban_verifications_conformance"
DESCRIPTION = "Add review_passed/review_findings/pytest_ran to kanban_verifications"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    """kanban_verifications is created at app runtime by tools/kanban/init_db.py,
    so a migrate-only fresh DB may legitimately lack it — skip rather than abort
    (matching migration 020)."""
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _has_column(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name=?",
            (table, column),
        ).fetchone()
        return row is not None
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == column
        for r in rows
    )


_COLUMNS = [
    ("review_passed", "INTEGER"),
    ("review_findings", "TEXT"),
    ("pytest_ran", "INTEGER DEFAULT 0"),
]


def up(conn) -> dict:
    actions = []
    if not _table_exists(conn, "kanban_verifications"):
        return {"status": "skipped", "reason": "kanban_verifications absent (created at runtime)"}
    for col, coltype in _COLUMNS:
        if not _has_column(conn, "kanban_verifications", col):
            conn.execute(
                f"ALTER TABLE kanban_verifications ADD COLUMN {col} {coltype}"
            )
            actions.append(f"added_{col}")
        else:
            actions.append(f"{col}_already_present")
    conn.commit()
    return {"status": "applied", "actions": actions}
