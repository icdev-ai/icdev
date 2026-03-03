#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 004 rollback: Remove innovation engine tables."""


def down(conn):
    """Drop innovation engine tables in reverse dependency order."""
    tables = [
        "innovation_feedback",
        "innovation_standards_updates",
        "innovation_competitor_scans",
        "innovation_trends",
        "innovation_solutions",
        "innovation_triage_log",
        "innovation_signals",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
