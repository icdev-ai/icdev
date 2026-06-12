#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback migration 028: Drop mitre_coverage table and its indexes."""


def down(conn) -> dict:
    actions = []
    for idx in ["idx_mc_project", "idx_mc_technique", "idx_mc_state", "idx_mc_observed"]:
        try:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
            actions.append(f"dropped_{idx}")
        except Exception:  # nosec B110 — index may not exist; safe to ignore in rollback
            pass
    conn.execute("DROP TABLE IF EXISTS mitre_coverage")
    actions.append("dropped_mitre_coverage")
    conn.commit()
    return {"status": "rolled_back", "actions": actions}
