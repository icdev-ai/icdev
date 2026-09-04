# CUI // SP-CTI
"""Migration 173: CPMP funding/obligation tracking + base+option period structure.

Adds to cpmp_contracts:
    - obligated_value REAL — formally obligated contract amount
    - period_type TEXT   — current period (base | option_1 … option_5)
    - option_number INTEGER — 0=base, 1-5=option years

Creates cpmp_contract_periods — one row per period (base + each option year).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ALTER_STMTS = [
    "ALTER TABLE cpmp_contracts ADD COLUMN obligated_value REAL DEFAULT 0.0",
    "ALTER TABLE cpmp_contracts ADD COLUMN period_type TEXT DEFAULT 'base'",
    "ALTER TABLE cpmp_contracts ADD COLUMN option_number INTEGER DEFAULT 0",
]

_PERIODS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_contract_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    period_type TEXT NOT NULL DEFAULT 'base',
    option_number INTEGER DEFAULT 0,
    pop_start TEXT,
    pop_end TEXT,
    obligated_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    ceiling_value REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'unexercised',
    exercised_at TEXT,
    exercised_by TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_periods_contract ON cpmp_contract_periods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_periods_status   ON cpmp_contract_periods(contract_id, status)",
]


def upgrade(conn=None):
    """Apply migration 173."""
    from tools.db.storage import get_connection

    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        for stmt in _ALTER_STMTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # column already exists
        conn.execute(_PERIODS_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
        return {"status": "ok", "migration": 173}
    finally:
        if _close:
            conn.close()


def downgrade(conn=None):
    """Drop cpmp_contract_periods (ALTER DROP COLUMN not supported on all SQLite)."""
    from tools.db.storage import get_connection

    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS cpmp_contract_periods")
        conn.commit()
        return {"status": "ok"}
    finally:
        if _close:
            conn.close()



def up(conn=None):
    """Runner entry point.

    The work lives in ``upgrade``; MigrationRunner calls ``up(conn)`` and would
    otherwise find no entry point at all and run NOTHING while recording the
    migration as applied — worse than the bare .py this was promoted from,
    because it would then look done.
    """
    return upgrade(conn)

if __name__ == "__main__":
    result = upgrade()
    print(result)
