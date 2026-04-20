#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 020: ad_options_coach_events (append-only NIST AU).

Phase 7.6 coach epic — position-coach event log. Every threshold trip
(profit-target, loss, DTE, IV crush) writes a row. Append-only; LLM
recommendation is attached by updating the `recommendation` column on
the SAME row (the append-only rule is about not deleting history, not
about enriching nullable columns).
"""

MIGRATION_ID = "020"
MIGRATION_NAME = "ad_options_coach_events"
DESCRIPTION = "Add ad_options_coach_events append-only table (Phase 7.6 coach)"

# Keep this tuple in sync with tools/trading/options/coach_engine.py.
COACH_EVENT_TYPES = (
    "profit_target",
    "loss_threshold",
    "dte_warning",
    "dte_roll_window",
    "iv_crush",
    "greeks_drift",
    "trap_against_position",
)

COACH_EVENT_SEVERITIES = ("info", "warn", "critical")


def _ddl_sqlite() -> str:
    et = ",".join(f"'{t}'" for t in COACH_EVENT_TYPES)
    sev = ",".join(f"'{s}'" for s in COACH_EVENT_SEVERITIES)
    return f"""
    CREATE TABLE IF NOT EXISTS ad_options_coach_events (
        id                    TEXT PRIMARY KEY,
        position_id           TEXT NOT NULL,
        user_id               TEXT NOT NULL,
        tenant_id             TEXT NOT NULL DEFAULT 'default',
        event_type            TEXT NOT NULL CHECK(event_type IN ({et})),
        severity              TEXT NOT NULL DEFAULT 'info' CHECK(severity IN ({sev})),
        summary               TEXT NOT NULL,
        recommendation        TEXT,
        position_snapshot_json TEXT,
        trap_event_id         TEXT,
        created_at            TEXT NOT NULL
    )
    """


def _ddl_pg() -> str:
    et = ",".join(f"'{t}'" for t in COACH_EVENT_TYPES)
    sev = ",".join(f"'{s}'" for s in COACH_EVENT_SEVERITIES)
    return f"""
    CREATE TABLE IF NOT EXISTS ad_options_coach_events (
        id                    TEXT PRIMARY KEY,
        position_id           TEXT NOT NULL,
        user_id               TEXT NOT NULL,
        tenant_id             TEXT NOT NULL DEFAULT 'default',
        event_type            TEXT NOT NULL,
        severity              TEXT NOT NULL DEFAULT 'info',
        summary               TEXT NOT NULL,
        recommendation        TEXT,
        position_snapshot_json TEXT,
        trap_event_id         TEXT,
        created_at            TEXT NOT NULL,
        CONSTRAINT ad_options_coach_events_event_type_check
            CHECK (event_type IN ({et})),
        CONSTRAINT ad_options_coach_events_severity_check
            CHECK (severity IN ({sev}))
    )
    """


INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ad_coach_user_created "
    "ON ad_options_coach_events(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ad_coach_position_created "
    "ON ad_options_coach_events(position_id, created_at DESC)",
    # Partial unique index: enforce one coach event per (position, trap) pair.
    # WHERE clause excludes non-trap events so NULL trap_event_id rows are ignored.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ad_coach_position_trap_dedup "
    "ON ad_options_coach_events(position_id, trap_event_id) "
    "WHERE trap_event_id IS NOT NULL",
]

# Column additions for tables that already exist (idempotent via try/except).
_ALTER_STMTS = [
    "ALTER TABLE ad_options_coach_events ADD COLUMN trap_event_id TEXT",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def up(conn) -> dict:
    actions = []
    conn.execute(_ddl_pg() if _is_pg(conn) else _ddl_sqlite())
    actions.append("ad_options_coach_events_table")
    for stmt in _ALTER_STMTS:
        try:
            conn.execute(stmt)
            actions.append(f"column_added: {stmt.split('ADD COLUMN')[1].strip()}")
        except Exception:
            # Column already exists — idempotent.
            pass
    for idx in INDEXES:
        conn.execute(idx)
    actions.append("indexes_created")
    conn.commit()
    return {"status": "applied", "actions": actions}
