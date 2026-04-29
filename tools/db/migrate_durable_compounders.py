# CUI // SP-CTI
"""Migration — Durable Compounders cache table.

Creates ad_durable_compounders_cache for storing scanner results with
fundamentals_hash-based cache invalidation and drift flagging.

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS ad_durable_compounders_cache (
    ticker                  TEXT PRIMARY KEY,
    balance_sheet_score     REAL,
    customer_loyalty_score  REAL,
    cycle_resilience_score  REAL,
    durable_score           REAL,
    tier                    TEXT,
    fundamentals_hash       TEXT,
    computed_at             TEXT,
    drift_flagged           INTEGER DEFAULT 0,
    drift_reason            TEXT,
    as_of_date              TEXT
)
"""


def run() -> dict:
    conn = get_connection()
    try:
        conn.execute(_DDL)
        conn.commit()
        return {"ok": True, "table": "ad_durable_compounders_cache"}
    finally:
        conn.close()


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
