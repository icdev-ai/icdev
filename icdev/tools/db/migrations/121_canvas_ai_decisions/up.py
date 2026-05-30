#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 121 — canvas_ai_decisions table.

Shared cross-canvas AI decision audit store. Every LLM-backed assessment,
compliance finding, threat analysis, narrative generation, or remediation
recommendation in any of the 10 design canvases writes one row here.

Satisfies:
  NIST AI RMF MEASURE 2.5/2.7/2.8 — observability of AI decisions
  DoD RAI "Traceable" principle
  FedRAMP SI-4, NIST AU-2
  OMB M-25-21 AI use case inventory

Table: canvas_ai_decisions (in icdev.db / main DB, NOT a canvas-specific DB)
Indexes: canvas_type, record_id, trace_id, created_at
"""
from __future__ import annotations

MIGRATION_ID = "121"
MIGRATION_NAME = "canvas_ai_decisions"
DESCRIPTION = (
    "Create canvas_ai_decisions table — shared cross-canvas AI decision audit store "
    "with indexes on canvas_type, record_id, and trace_id."
)


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    c = conn or get_connection()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS canvas_ai_decisions (
                id             TEXT PRIMARY KEY,
                canvas_type    TEXT NOT NULL,
                record_id      TEXT,
                decision_type  TEXT NOT NULL,
                decision       TEXT NOT NULL,
                rationale      TEXT,
                model_used     TEXT,
                confidence     REAL,
                alternatives   TEXT,
                trace_id       TEXT,
                span_id        TEXT,
                actor          TEXT NOT NULL DEFAULT 'icdev-system',
                project_id     TEXT,
                classification TEXT NOT NULL DEFAULT 'CUI',
                created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cad_canvas_type "
            "ON canvas_ai_decisions(canvas_type)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cad_record_id "
            "ON canvas_ai_decisions(record_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cad_trace_id "
            "ON canvas_ai_decisions(trace_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_cad_created_at "
            "ON canvas_ai_decisions(created_at)"
        )
        c.commit()
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        if conn is None:
            c.close()


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
