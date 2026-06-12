#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 098 — aisg_roi_events table for AISG ROI time-savings tracking."""
from tools.db.storage import get_connection

ACTION_TYPES = (
    "self_heal",
    "compliance_check",
    "security_scan",
    "test_run",
    "evidence_collect",
    "pattern_deploy",
    "fine_tune_eval",
    "genesis_reflex",
)
_CHECK = "', '".join(ACTION_TYPES)


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS aisg_roi_events (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type          TEXT    NOT NULL
                                     CHECK(action_type IN ('{_CHECK}')),
                time_saved_minutes   NUMERIC NOT NULL,
                description          TEXT,
                triggered_by         TEXT,
                occurred_at          TEXT    NOT NULL
                                     DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aisg_roi_events_action_type "
            "ON aisg_roi_events(action_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aisg_roi_events_occurred_at "
            "ON aisg_roi_events(occurred_at DESC)"
        )
        conn.commit()
        print("Migration 098 up: aisg_roi_events created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
