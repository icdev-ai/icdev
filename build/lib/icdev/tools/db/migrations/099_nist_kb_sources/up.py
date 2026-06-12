#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 099 — NIST knowledge-base source tables (CSF 2.0, 800-207 ZTA, 800-218 SSDF)."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nist_csf_20_sections (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id    TEXT    NOT NULL UNIQUE,
                title         TEXT    NOT NULL,
                description   TEXT,
                guidance_text TEXT,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nist_csf_20_section_id "
            "ON nist_csf_20_sections(section_id)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS nist_800_207_sections (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id    TEXT    NOT NULL UNIQUE,
                title         TEXT    NOT NULL,
                description   TEXT,
                guidance_text TEXT,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nist_800_207_section_id "
            "ON nist_800_207_sections(section_id)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS nist_800_218_sections (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id    TEXT    NOT NULL UNIQUE,
                title         TEXT    NOT NULL,
                description   TEXT,
                guidance_text TEXT,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nist_800_218_section_id "
            "ON nist_800_218_sections(section_id)"
        )

        conn.commit()
        print("Migration 099 up: nist_csf_20_sections, nist_800_207_sections, nist_800_218_sections created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
