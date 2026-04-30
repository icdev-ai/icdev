#!/usr/bin/env python3
# CUI // SP-CTI
"""OpenCTI → sg_opencti_indicators + sg_ghost_signals bridge.

CLI:
  --run       Fetch live indicators and upsert to DB.
  --dry-run   Simulate without writing; print JSON summary.
  --json      Machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _indicator_type(pattern: str) -> str:
    if "[ipv4-addr" in pattern:
        return "rf_anomaly"
    if "[domain-name" in pattern:
        return "intelligence_signal"
    if "[file:hashes" in pattern:
        return "intelligence_signal"
    return "intelligence_signal"


def run(dry_run: bool = False) -> dict:
    from tools.strategos.opencti_client import OpenCTIClient
    from tools.db.storage import get_connection

    client = OpenCTIClient()
    if not client.is_configured():
        return {
            "status": "ok",
            "configured": False,
            "would_insert_indicators": 0,
            "would_insert_ghost": 0,
        }

    try:
        indicators = client.list_indicators(limit=client.indicator_limit)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    would_indicators = len(indicators)
    would_ghost = sum(1 for i in indicators if (i.get("confidence") or 0) >= client.min_confidence)

    if dry_run:
        return {
            "status": "ok",
            "configured": True,
            "would_insert_indicators": would_indicators,
            "would_insert_ghost": would_ghost,
        }

    conn = get_connection()
    try:
        now = _now()
        inserted_indicators = 0
        inserted_ghost = 0
        for ind in indicators:
            row_id = str(uuid.uuid4())
            ind_id = ind.get("id", row_id)
            conn.execute(
                """INSERT INTO sg_opencti_indicators
                   (id, opencti_id, name, indicator_type, pattern, confidence,
                    tlp, valid_from, valid_until, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(opencti_id) DO UPDATE SET
                     name=excluded.name,
                     confidence=excluded.confidence,
                     valid_until=excluded.valid_until,
                     updated_at=excluded.updated_at""",
                (
                    row_id,
                    ind_id,
                    ind.get("name", ""),
                    ",".join(ind.get("indicator_types") or []),
                    ind.get("pattern", ""),
                    ind.get("confidence") or 0,
                    "TLP:WHITE",
                    ind.get("valid_from", ""),
                    ind.get("valid_until", ""),
                    "active",
                    now,
                    now,
                ),
            )
            inserted_indicators += 1
            conf = ind.get("confidence") or 0
            if conf >= client.min_confidence:
                sig_type = _indicator_type(ind.get("pattern", ""))
                conn.execute(
                    """INSERT INTO sg_ghost_signals
                       (id, signal_type, mmsi, lat, lon, confidence, source, detected_at)
                       VALUES (?,?,NULL,NULL,NULL,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        sig_type,
                        conf / 100.0,
                        f"opencti:{ind_id[:20]}",
                        now,
                    ),
                )
                inserted_ghost += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "configured": True,
        "inserted_indicators": inserted_indicators,
        "inserted_ghost": inserted_ghost,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenCTI bridge for Strategos SGX")
    parser.add_argument("--run", action="store_true", help="Fetch and persist indicators")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Simulate only")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if not (args.run or args.dry_run):
        parser.print_help()
        sys.exit(0)

    result = run(dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps(result))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
