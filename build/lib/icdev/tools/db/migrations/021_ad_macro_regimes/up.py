#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 021: Create ad_macro_regimes table.

Append-only macro regime classification store.  Tracks daily assessments
of macro regimes (liquidity_trap, stagflation, goldilocks, reflation) with
evidence and confidence.  Written by detect_liquidity_trap() and similar
detectors in tools/trading/ta/.
"""
from __future__ import annotations

MIGRATION_ID = "021"
MIGRATION_NAME = "ad_macro_regimes"
DESCRIPTION = "Create append-only ad_macro_regimes table for macro regime signals"

_DDL = """
CREATE TABLE IF NOT EXISTS ad_macro_regimes (
    id           TEXT PRIMARY KEY,
    regime_type  TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 0.0,
    created_at   TEXT NOT NULL
);
"""

_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_ad_macro_regimes_type_date "
    "ON ad_macro_regimes (regime_type, created_at)",
)


def up(conn) -> dict:
    actions: list[str] = []

    try:
        conn.execute(_DDL)
        actions.append("created_ad_macro_regimes")
    except Exception as exc:
        actions.append(f"ad_macro_regimes_ddl_skipped: {exc}")

    for stmt in _IDX:
        try:
            conn.execute(stmt)
            actions.append("created_index")
        except Exception as exc:
            actions.append(f"index_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
