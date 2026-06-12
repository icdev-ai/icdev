#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 006 rollback: Remove Phase 36 evolution engine tables."""


def down(conn):
    """Drop Phase 36 evolution engine tables in reverse dependency order."""
    tables = [
        "propagation_log",
        "staging_environments",
        "capability_evaluations",
        "genome_versions",
        "capability_genome",
        "child_learned_behaviors",
        "child_telemetry",
        "child_capabilities",
        "atlas_assessments",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
