#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 179: Add kanban_task_revivals tracking table.

Supports bounded auto-revive of failure-quarantined kanban tasks. When the
stale-reaper quarantines a task to 'suggested' after fc>=5, the suggested-decay
sweep may auto-revive it (deps satisfied + cooldown) up to a capped number of
times before holding it for human (HITL) review. This side table holds the
per-task revival counter so the cap survives re-quarantine.

Deliberately a SEPARATE table (not an ALTER on the hot kanban_tasks table) to
avoid the known ALTER-ADD-COLUMN lock storm on kanban_tasks.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))

DDL = [
    """CREATE TABLE IF NOT EXISTS kanban_task_revivals (
        task_id TEXT PRIMARY KEY,
        revive_count INTEGER NOT NULL DEFAULT 0,
        last_revived_at TEXT,
        hitl_alerted INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
    )""",
]


def up():
    conn = get_connection(db_path=DB_PATH)
    try:
        for ddl in DDL:
            conn.execute(ddl)
        conn.commit()
        print("[migration 179] kanban_task_revivals created")
    finally:
        conn.close()


def down():
    conn = get_connection(db_path=DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS kanban_task_revivals")
        conn.commit()
        print("[migration 179] kanban_task_revivals dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
