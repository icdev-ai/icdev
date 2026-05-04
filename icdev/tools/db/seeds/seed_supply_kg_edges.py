#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed PRODUCES/DEPENDS_ON_SUPPLY edges in sg_kg_edges from sg_supply_nodes.

Defines logical supply-chain topology:
  - depot → airfield/port (PRODUCES: supplies the downstream node)
  - port → airfield/depot (DEPENDS_ON_SUPPLY: requires port logistics)

Run once; idempotent via ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# (source_id, target_id, relation)
SUPPLY_EDGES = [
    # INDOPACOM logistics chain
    ("depot-sasebo",       "port-yokosuka",   "PRODUCES"),
    ("port-yokosuka",      "depot-camp-zama", "DEPENDS_ON_SUPPLY"),
    ("depot-camp-zama",    "airfield-kadena", "PRODUCES"),
    ("port-okinawa",       "airfield-kadena", "DEPENDS_ON_SUPPLY"),
    ("port-guam",          "airfield-andersen","DEPENDS_ON_SUPPLY"),
    ("depot-red-hill",     "airfield-andersen","PRODUCES"),
    ("port-busan",         "airfield-osan",   "DEPENDS_ON_SUPPLY"),
    ("airfield-misawa",    "port-yokosuka",   "DEPENDS_ON_SUPPLY"),
    ("port-keelung",       "port-kaohsiung",  "DEPENDS_ON_SUPPLY"),
    # Black Sea / Ukraine theater
    ("port-novorossiysk",  "depot-crimea",    "PRODUCES"),
    ("depot-crimea",       "airfield-saki",   "PRODUCES"),
    # South China Sea theater
    ("port-qingdao",       "port-zhoushan",   "PRODUCES"),
    ("port-zhoushan",      "airfield-longhua","DEPENDS_ON_SUPPLY"),
]


def run() -> dict:
    conn = get_connection()
    inserted = 0
    skipped = 0
    try:
        for src, tgt, rel in SUPPLY_EDGES:
            exists = conn.execute(
                "SELECT 1 FROM sg_kg_edges WHERE source_id=%s AND target_id=%s AND relation=%s",
                (src, tgt, rel),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO sg_kg_edges (source_id, target_id, relation, weight) "
                "VALUES (%s, %s, %s, %s)",
                (src, tgt, rel, 1.0),
            )
            inserted += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        return {"status": "error", "error": str(exc)}
    conn.close()
    return {"status": "ok", "inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    import json
    result = run()
    print(json.dumps(result, indent=2))
