# CUI // SP-CTI
"""Migration 213 — usage_events + usage_daily_rollup tables.

usage_events is append-only (NIST AU-9): every billable event is an immutable
audit record. Never UPDATE or DELETE rows.

usage_daily_rollup is mutable: the daily job upserts one row per
(tenant_id, event_type, model, rollup_date) bucket. It is recomputable from
usage_events so it is not append-only.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_EVENT_TYPES = ("llm_token", "api_call", "storage_mb", "canvas_load", "coworker_task")
_CHECK = ", ".join(f"'{t}'" for t in _EVENT_TYPES)

DDL = f"""
CREATE TABLE IF NOT EXISTS usage_events (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    event_type   TEXT NOT NULL CHECK (event_type IN ({_CHECK})),
    quantity     REAL NOT NULL DEFAULT 1.0,
    model        TEXT,
    canvas_key   TEXT,
    metadata     TEXT,
    recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant
    ON usage_events(tenant_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_usage_events_type
    ON usage_events(event_type, recorded_at);

CREATE TABLE IF NOT EXISTS usage_daily_rollup (
    tenant_id      TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    model          TEXT NOT NULL DEFAULT '',
    rollup_date    TEXT NOT NULL,
    total_quantity REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (tenant_id, event_type, model, rollup_date)
);

CREATE INDEX IF NOT EXISTS idx_usage_rollup_tenant
    ON usage_daily_rollup(tenant_id, rollup_date);
"""


def up() -> None:
    with get_connection() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 213 (usage_events + usage_daily_rollup) applied.")
