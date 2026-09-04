#!/usr/bin/env python3
# CUI // SP-CTI
"""Revert llm_response_cache to UNLOGGED.

Provided for completeness. Running it re-opens the defect this migration
closes: the savings ledger becomes volatile again and any crash recovery resets
the dashboard's cumulative dollars-saved to $0.0000.
"""
from __future__ import annotations

from .up import _is_postgres


def down(conn) -> dict:
    if not _is_postgres(conn):
        return {"status": "skipped", "reason": "not postgresql"}
    conn.execute("ALTER TABLE llm_response_cache SET UNLOGGED")
    return {"status": "applied", "now": "u"}
