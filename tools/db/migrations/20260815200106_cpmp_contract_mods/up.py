#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 177: Add cpmp_contract_mods table + status history support.

Adds the contract modification request/approval workflow table and
updates cpmp_status_history entity_type to accept 'contract_mod'.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))

DDL = """
CREATE TABLE IF NOT EXISTS cpmp_contract_mods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    mod_number INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('admin','funding','scope','pop')),
    description TEXT NOT NULL DEFAULT '',
    value_delta REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','in_review','approved','rejected','executed')),
    requested_by TEXT,
    requested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    reviewed_by TEXT,
    reviewed_at TEXT,
    approved_by TEXT,
    approved_at TEXT,
    rejection_reason TEXT,
    effective_date TEXT,
    executed_at TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (contract_id) REFERENCES cpmp_contracts(id)
)
"""

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_contract ON cpmp_contract_mods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_status ON cpmp_contract_mods(status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cpmp_contract_mods_number ON cpmp_contract_mods(contract_id, mod_number)",
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
        conn.execute(DDL)
        for idx in INDEX_DDL:
            conn.execute(idx)
        conn.commit()
        print("[migration 177] cpmp_contract_mods table created")
    finally:
        if _own:
            conn.close()


def down():
    conn = get_connection(db_path=DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS cpmp_contract_mods")
        conn.commit()
        print("[migration 177] cpmp_contract_mods table dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
