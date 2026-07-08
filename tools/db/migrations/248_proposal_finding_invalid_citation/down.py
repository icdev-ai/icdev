#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 248 rollback."""

MIGRATION_ID = "248"
MIGRATION_NAME = "proposal_finding_invalid_citation"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    if not _is_pg(conn):
        return {"status": "skipped", "reason": "SQLite constraint reverts via CREATE TABLE only"}

    actions = []
    try:
        conn.execute(
            "ALTER TABLE proposal_review_findings "
            "DROP CONSTRAINT IF EXISTS proposal_review_findings_finding_type_check"
        )
        conn.execute(
            "ALTER TABLE proposal_review_findings "
            "ADD CONSTRAINT proposal_review_findings_finding_type_check "
            "CHECK (finding_type = ANY (ARRAY["
            "'compliance_gap'::text, 'content_weakness'::text, 'competitive_risk'::text, "
            "'formatting'::text, 'pricing_concern'::text, 'technical_error'::text, "
            "'missing_content'::text, 'other'::text]))"
        )
        actions.append("finding_type_check_reverted")
    except Exception as exc:
        actions.append(f"finding_type_check_revert_skipped: {exc}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
