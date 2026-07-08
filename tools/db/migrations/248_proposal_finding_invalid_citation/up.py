#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 248: Add 'invalid_citation' to proposal_review_findings.finding_type.

ground-prop-02 adds deterministic citation validation to proposal drafting:
response_drafter.py validates every Section/Factor/Volume/CLIN reference in a
draft against the parsed solicitation (tools/govcon/solicitation_grounding.py)
and run_writeguard_on_drafts.py raises an 'invalid_citation' finding when the
draft cites structure that does not exist in the document.

PG only: SQLite doesn't support ALTER TABLE ... DROP/ADD CONSTRAINT; fresh
SQLite databases get the expanded constraint directly from
tools/db/init_icdev_db.py's CREATE TABLE statement instead.
"""

MIGRATION_ID = "248"
MIGRATION_NAME = "proposal_finding_invalid_citation"
DESCRIPTION = "Extend proposal_review_findings.finding_type CHECK with invalid_citation"

# Keep in sync with tools/db/init_icdev_db.py::proposal_review_findings DDL.
_FINDING_TYPES = (
    "compliance_gap", "content_weakness", "competitive_risk",
    "formatting", "pricing_concern", "technical_error",
    "missing_content", "invalid_citation", "other",
)


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def up(conn) -> dict:
    actions = []

    if not _table_exists(conn, "proposal_review_findings"):
        return {"status": "skipped", "reason": "proposal_review_findings absent"}

    if not _is_pg(conn):
        return {"status": "skipped", "reason": "SQLite gets the expanded constraint from CREATE TABLE directly"}

    try:
        cur = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) as def "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = ? AND c.conname = ?",
            ("proposal_review_findings", "proposal_review_findings_finding_type_check"),
        ).fetchone()
        current = dict(cur).get("def", "") if cur else ""
        if all(f"'{ft}'" in current for ft in _FINDING_TYPES):
            actions.append("already_current")
        else:
            conn.execute(
                "ALTER TABLE proposal_review_findings "
                "DROP CONSTRAINT IF EXISTS proposal_review_findings_finding_type_check"
            )
            type_list = ", ".join(f"'{ft}'::text" for ft in _FINDING_TYPES)
            conn.execute(
                "ALTER TABLE proposal_review_findings "
                "ADD CONSTRAINT proposal_review_findings_finding_type_check "
                f"CHECK (finding_type = ANY (ARRAY[{type_list}]))"
            )
            actions.append("finding_type_check_expanded")
    except Exception as exc:
        actions.append(f"finding_type_check_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
