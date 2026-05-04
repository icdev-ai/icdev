#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 079 DOWN — drop all wf_ tables (reverse dependency order)."""

from tools.db.storage import get_connection

MIGRATION_ID = "079"
MIGRATION_NAME = "workflow_hitl"

_DROP = [
    "DROP TABLE IF EXISTS wf_citations",
    "DROP TABLE IF EXISTS wf_document_submissions",
    "DROP TABLE IF EXISTS wf_document_templates",
    "DROP TABLE IF EXISTS wf_external_steps",
    "DROP TABLE IF EXISTS wf_feedback_insights",
    "DROP TABLE IF EXISTS wf_feedback",
    "DROP TABLE IF EXISTS wf_approvals",
    "DROP TABLE IF EXISTS wf_instances",
    "DROP TABLE IF EXISTS wf_team_assignments",
    "DROP TABLE IF EXISTS wf_team_members",
    "DROP TABLE IF EXISTS wf_teams",
    "DROP TABLE IF EXISTS wf_templates",
]


def run():
    conn = get_connection()
    try:
        for stmt in _DROP:
            conn.execute(stmt)
        try:
            conn.commit()
        except Exception:
            pass
        print(f"Migration {MIGRATION_ID} ({MIGRATION_NAME}) rolled back.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
