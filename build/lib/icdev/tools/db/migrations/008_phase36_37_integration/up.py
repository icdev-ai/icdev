#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 008: Phase 36 <-> Phase 37 security integration.

Adds trust_level and injection_scan_result columns to child_learned_behaviors
table to support prompt injection scanning of child-reported behaviors.
"""

MIGRATION_ID = "008"
MIGRATION_NAME = "phase36_37_integration"
DESCRIPTION = "Add trust level and injection scan columns for Phase 37 security integration"


def up(conn):
    """Apply migration -- add security integration columns."""
    cursor = conn.cursor()

    # Add trust_level column to child_learned_behaviors
    try:
        cursor.execute("""
            ALTER TABLE child_learned_behaviors
            ADD COLUMN trust_level TEXT DEFAULT 'child'
            CHECK(trust_level IN ('system', 'user', 'external', 'child'))
        """)
    except Exception:
        pass  # Column may already exist

    # Add injection_scan_result column
    try:
        cursor.execute("""
            ALTER TABLE child_learned_behaviors
            ADD COLUMN injection_scan_result TEXT DEFAULT NULL
        """)
    except Exception:
        pass  # Column may already exist

    conn.commit()
    return True


def down(conn):
    """Rollback migration.

    Note: SQLite does not support DROP COLUMN before 3.35.0.
    For older SQLite, this is a no-op (columns remain but are unused).
    """
    try:
        cursor = conn.cursor()
        # SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN
        cursor.execute("ALTER TABLE child_learned_behaviors DROP COLUMN trust_level")
        cursor.execute("ALTER TABLE child_learned_behaviors DROP COLUMN injection_scan_result")
        conn.commit()
    except Exception:
        pass  # Older SQLite -- columns remain as vestigial
    return True
