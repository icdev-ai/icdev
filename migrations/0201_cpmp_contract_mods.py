"""Migration: Add cpmp_contract_mods table for contract modification workflow."""
from tools.db.storage import get_connection
import os

DB_PATH = os.environ.get("ICDEV_DB_PATH", "data/icdev.db")

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

def up():
    conn = get_connection(db_path=DB_PATH)
    try:
        conn.execute(DDL)
        for idx in INDEX_DDL:
            conn.execute(idx)
        conn.commit()
        print("[migration] cpmp_contract_mods table created")
    finally:
        conn.close()

def down():
    conn = get_connection(db_path=DB_PATH)
    try:
        conn.execute("DROP TABLE IF EXISTS cpmp_contract_mods")
        conn.commit()
        print("[migration] cpmp_contract_mods table dropped")
    finally:
        conn.close()

if __name__ == "__main__":
    up()
