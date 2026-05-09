#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 122 — trace linkage columns.

Adds trace_id and span_id to kanban_tasks and reflex_observations so every
AI-driven task creation and every Genesis reflex execution can be linked back
to an OpenTelemetry span in otel_spans.

Satisfies NIST AU-3 (content of audit records — include enough information to
trace actions to individual users/agents and their operations).

Tables modified (icdev.db):
  kanban_tasks        — ADD COLUMN trace_id TEXT
  kanban_tasks        — ADD COLUMN span_id  TEXT
  reflex_observations — ADD COLUMN trace_id TEXT
  reflex_observations — ADD COLUMN span_id  TEXT

Idempotent: ALTER TABLE IF NOT EXISTS column (SQLite ≥ 3.37).
Falls back silently if column already exists.
"""
from __future__ import annotations

MIGRATION_ID = "122"
MIGRATION_NAME = "trace_linkage"
DESCRIPTION = (
    "Add trace_id + span_id to kanban_tasks and reflex_observations "
    "to link AI-driven decisions back to OTel spans (NIST AU-3)."
)

_ALTERS = [
    ("kanban_tasks",        "trace_id", "TEXT"),
    ("kanban_tasks",        "span_id",  "TEXT"),
    ("reflex_observations", "trace_id", "TEXT"),
    ("reflex_observations", "span_id",  "TEXT"),
]


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    c = conn or get_connection()
    try:
        for table, col, col_type in _ALTERS:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except Exception as exc:
                if "duplicate column" in str(exc).lower():
                    pass  # already exists — idempotent
                else:
                    raise
        c.commit()
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        if conn is None:
            c.close()


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
