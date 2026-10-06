#!/usr/bin/env python3
# CUI // SP-CTI
"""Roll back 20261006080431_ndc_pna_predictive_tables -- EMPTY tables only.

Six of the seven tables are append-only prediction history. A rollback that
dropped rows would destroy evidence, so a table holding any row is left in
place and named in the result instead of being dropped.
"""
from __future__ import annotations

PNA_TABLES = (
    "nc_eol_predictions",
    "nc_bgp_events",
    "nc_bgp_predictions",
    "nc_compliance_drift",
    "nc_capacity_predictions",
    "nc_change_risk",
    "nc_supply_chain_risk",
)


def down(conn) -> dict:
    if getattr(conn, "_backend", "sqlite") != "postgresql":
        return {"dropped": [], "reason": "sqlite_uses_network_canvas_db"}
    dropped, kept = [], []
    for table in PNA_TABLES:
        present = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        if not present:
            continue
        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            kept.append(table)
            continue
        conn.execute(f"DROP TABLE {table}")
        dropped.append(table)
    return {"dropped": dropped, "kept_nonempty": kept}
