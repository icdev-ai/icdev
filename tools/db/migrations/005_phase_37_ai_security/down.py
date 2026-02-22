#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback migration 005: Phase 37 AI Security tables."""

import sqlite3


def down(conn: sqlite3.Connection):
    """Remove Phase 37 AI Security tables."""
    tables = [
        "prompt_injection_log", "ai_telemetry", "ai_bom",
        "atlas_assessments", "atlas_red_team_results",
        "owasp_llm_assessments", "nist_ai_rmf_assessments",
        "iso42001_assessments",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    print(f"  Migration 005 rolled back: {len(tables)} tables dropped")
