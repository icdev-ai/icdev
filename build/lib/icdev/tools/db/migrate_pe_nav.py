# CUI // SP-CTI
"""Migration: Add PE/NAV analysis columns to ad_fundamental_metrics and ad_quality_scores.

New columns on ad_fundamental_metrics:
  nav_per_share   REAL  — explicit NAV/share for REITs, CEFs, BDCs; NULL for others
  sector_tier     INTEGER DEFAULT 2  — 1 (explicit NAV) / 2 (balance sheet) / 3 (intangibles)

New columns on ad_quality_scores:
  pe_nav_score    REAL  — PE/NAV mispricing score (0-100)
  implied_roe     REAL  — implied ROE from P/NAV ÷ PE (P/B ÷ PE identity)
  roe_gap         REAL  — actual ROE − implied ROE (positive = undervalued)
  nav_quadrant    TEXT  — deep_value | efficient_compounder | asset_rich_earnings_poor | fully_priced

CLI:
    python tools/db/migrate_pe_nav.py --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection

_FUNDAMENTAL_COLUMNS = [
    ("nav_per_share", "REAL"),
    ("sector_tier",   "INTEGER DEFAULT 2"),
]

_QUALITY_COLUMNS = [
    ("pe_nav_score", "REAL"),
    ("implied_roe",  "REAL"),
    ("roe_gap",      "REAL"),
    ("nav_quadrant", "TEXT"),
]


def _add_columns(conn, table: str, columns: list[tuple[str, str]]) -> list[str]:
    added = []
    for col_name, col_type in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
            added.append(col_name)
        except Exception:
            pass  # column already exists
    return added


def run_migration() -> dict:
    conn = get_connection()
    try:
        fm_added  = _add_columns(conn, "ad_fundamental_metrics", _FUNDAMENTAL_COLUMNS)
        qs_added  = _add_columns(conn, "ad_quality_scores",      _QUALITY_COLUMNS)
        conn.commit()
        return {
            "ok": True,
            "ad_fundamental_metrics": {"added": fm_added},
            "ad_quality_scores":      {"added": qs_added},
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


if __name__ == "__main__":
    result = run_migration()
    print(json.dumps(result, indent=2))
