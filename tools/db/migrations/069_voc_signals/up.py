#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 069: Create voc_documents and voc_job_statements append-only tables.

Rationale (VOC signal capture, 2026-04-28): stores ingested voice-of-customer
documents and extracted job statements with scoring. Both tables are
append-only (no UPDATE/DELETE) per NIST AU requirements.

Guard: skipped if kanban_tasks is missing (pre-kanban environment).
"""

import sqlite3

MIGRATION_ID = "069"
MIGRATION_NAME = "voc_signals"
DESCRIPTION = "Create voc_documents and voc_job_statements append-only tables"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    actions = []

    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks missing"}

    cur = conn.cursor()

    if _table_exists(conn, "voc_documents"):
        actions.append("voc_documents_exists")
    else:
        cur.execute("""
            CREATE TABLE voc_documents (
                id                   TEXT PRIMARY KEY,
                filename             TEXT NOT NULL,
                source_type          TEXT NOT NULL,
                ingested_at          TEXT NOT NULL,
                word_count           INTEGER,
                job_statement_count  INTEGER,
                classification       TEXT DEFAULT 'CUI // SP-CTI'
            )
        """)
        actions.append("voc_documents_created")

    if _table_exists(conn, "voc_job_statements"):
        actions.append("voc_job_statements_exists")
    else:
        cur.execute("""
            CREATE TABLE voc_job_statements (
                id                  TEXT PRIMARY KEY,
                document_id         TEXT NOT NULL,
                raw_text            TEXT NOT NULL,
                job_category        TEXT,
                frequency           INTEGER,
                severity_score      REAL,
                strategic_fit_score REAL,
                composite_score     REAL,
                creative_gap_id     TEXT,
                analyzed_at         TEXT NOT NULL
            )
        """)
        actions.append("voc_job_statements_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_voc_score "
        "ON voc_job_statements(composite_score)"
    )
    actions.append("idx_voc_score_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_voc_document_id "
        "ON voc_job_statements(document_id)"
    )
    actions.append("idx_voc_document_id_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_voc_category "
        "ON voc_job_statements(job_category)"
    )
    actions.append("idx_voc_category_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
