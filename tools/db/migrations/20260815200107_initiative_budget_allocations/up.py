#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 178: Add cpmp_budget_allocations tables for initiative budget allocation.

Adds three tables supporting tier-prioritized budget allocation by initiative:
  - cpmp_budget_allocations  (the per-initiative allocation + obligation rollup)
  - cpmp_budget_obligations  (append-only record of obligations)
  - cpmp_budget_tier_history (audit trail of tier transitions / status changes)

Also registers a docs/feature note linking to the FORGE feature card.
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
    """CREATE TABLE IF NOT EXISTS cpmp_budget_allocations (
        id TEXT PRIMARY KEY,
        initiative_code TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        fiscal_year INTEGER NOT NULL,
        tier TEXT NOT NULL CHECK(tier IN ('tier_1', 'tier_2')),
        allocated_usd REAL NOT NULL DEFAULT 0.0,
        obligated_usd REAL NOT NULL DEFAULT 0.0,
        available_usd REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','depleted','deferred','cancelled')),
        agency TEXT NOT NULL DEFAULT '',
        contract_id TEXT,
        owner TEXT NOT NULL DEFAULT '',
        justification TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        UNIQUE(initiative_code, fiscal_year)
    )""",
    """CREATE TABLE IF NOT EXISTS cpmp_budget_obligations (
        id TEXT PRIMARY KEY,
        allocation_id TEXT NOT NULL,
        amount_usd REAL NOT NULL DEFAULT 0.0,
        description TEXT NOT NULL DEFAULT '',
        reference_id TEXT,
        recorded_by TEXT,
        recorded_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        FOREIGN KEY (allocation_id) REFERENCES cpmp_budget_allocations(id)
    )""",
    """CREATE TABLE IF NOT EXISTS cpmp_budget_tier_history (
        id TEXT PRIMARY KEY,
        allocation_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('allocation_created','tier_transition','status_change','obligation_recorded')),
        from_tier TEXT,
        to_tier TEXT,
        from_status TEXT,
        to_status TEXT,
        amount_usd REAL,
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT,
        created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
        FOREIGN KEY (allocation_id) REFERENCES cpmp_budget_allocations(id)
    )""",
]

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_tier ON cpmp_budget_allocations(tier)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_fy ON cpmp_budget_allocations(fiscal_year)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_status ON cpmp_budget_allocations(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_obligations_alloc ON cpmp_budget_obligations(allocation_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_tier_history_alloc ON cpmp_budget_tier_history(allocation_id)",
]


def up(conn=None):
    """Accept the runner's connection; open one only when called standalone.

    This was `def up()` with no parameters. MigrationRunner calls `mod.up(conn)`,
    so promoting the file to a discoverable migration without this would make it
    run and immediately raise TypeError — a migration that fails on first
    execution is worse than one that never executes, and it was invisible for
    long enough that nobody found out.

    Preferring the passed connection also puts the DDL in the runner's
    transaction and stops this function closing a connection it did not open.
    """
    _own = conn is None
    if _own:
        conn = get_connection(db_path=DB_PATH)
    try:
        for ddl in DDL:
            conn.execute(ddl)
        for idx in INDEX_DDL:
            conn.execute(idx)
        conn.commit()
        print("[migration 178] cpmp_budget_allocations / _obligations / _tier_history created")
    finally:
        if _own:
            conn.close()


def down():
    conn = get_connection(db_path=DB_PATH)
    try:
        for tbl in (
            "cpmp_budget_tier_history",
            "cpmp_budget_obligations",
            "cpmp_budget_allocations",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
        print("[migration 178] cpmp_budget_* tables dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
