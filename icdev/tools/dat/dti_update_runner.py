#!/usr/bin/env python3
# CUI // SP-CTI
"""DTI Update Runner — one-shot ingestion + DTI calculation pipeline.

Called by the Windows Task Scheduler every 6 hours (see icdev_dat_scheduler_task.xml).
Also usable as a CLI smoke-test and for headless invocation.

Usage::

    python tools/dat/dti_update_runner.py --json
    python tools/dat/dti_update_runner.py --bypass-cooldown --json
    python tools/dat/dti_update_runner.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.dat.dti_update_runner")

INTERVAL_HOURS = 6
MAX_RUNTIME_MINUTES = 10


def _run_ingestion(dry_run: bool) -> dict:
    from tools.dat.ingestion_engine import run as ingest_run
    cfg_path = _REPO_ROOT / "args" / "dat_config.yaml"
    return ingest_run(config_path=cfg_path, dry_run=dry_run)


def _run_dti_refresh(bypass_cooldown: bool, dry_run: bool) -> dict:
    if dry_run:
        return {"skipped": True, "reason": "dry-run"}
    if bypass_cooldown:
        from tools.strategos.dat import refresh_dti
        from tools.db.storage import get_connection

        def _get_theaters(conn):
            try:
                rows = conn.execute("SELECT theater_id FROM sg_theaters").fetchall()
                ids = [r[0] if not isinstance(r, dict) else r["theater_id"] for r in rows]
                return ids or ["global"]
            except Exception:
                return ["global"]

        conn = get_connection()
        try:
            theaters = _get_theaters(conn)
        finally:
            conn.close()

        if "global" not in theaters:
            theaters.append("global")

        results = {}
        for theater_id in theaters:
            try:
                snap = refresh_dti(theater_id)
                results[theater_id] = {
                    "dti_score": snap["dti_score"],
                    "snapshot_id": snap.get("snapshot_id"),
                    "computed_at": snap["computed_at"],
                }
            except Exception as exc:
                results[theater_id] = {"error": str(exc)}
        return {"theaters": results, "count": len(results)}
    else:
        from tools.genesis.reflexes.dat_refresh import run as reflex_run
        return reflex_run({}, None)


def run(dry_run: bool = False, bypass_cooldown: bool = False) -> dict:
    """Execute ingestion then DTI refresh; return combined result dict."""
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    ingestion_result = _run_ingestion(dry_run=dry_run)
    ingestion_elapsed = time.monotonic() - t0

    t1 = time.monotonic()
    dti_result = _run_dti_refresh(bypass_cooldown=bypass_cooldown, dry_run=dry_run)
    dti_elapsed = time.monotonic() - t1

    total_elapsed = time.monotonic() - t0
    finished_at = datetime.now(timezone.utc).isoformat()

    result = {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(total_elapsed, 3),
        "within_time_limit": total_elapsed < MAX_RUNTIME_MINUTES * 60,
        "ingestion": {
            "elapsed_seconds": round(ingestion_elapsed, 3),
            "total_records": ingestion_result.get("total_records", 0),
            "status": ingestion_result.get("status", "unknown"),
        },
        "dti_refresh": {
            "elapsed_seconds": round(dti_elapsed, 3),
            **dti_result,
        },
    }

    logger.info(
        "DTI update complete — %.1fs total | ingestion %d records | dti %s",
        total_elapsed,
        ingestion_result.get("total_records", 0),
        "skipped" if dti_result.get("skipped") else f"{dti_result.get('count', 0)} theaters",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="One-shot DTI update: ingestion + DTI calculation."
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Read sources but skip DB writes and DTI refresh")
    p.add_argument("--bypass-cooldown", action="store_true",
                   help="Skip 6-hour cooldown gate (used for testing)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Output result as JSON")
    p.add_argument("--verbose", action="store_true",
                   help="Enable debug logging")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    result = run(dry_run=args.dry_run, bypass_cooldown=args.bypass_cooldown)

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        dti = result["dti_refresh"]
        status = "skipped (cooldown)" if dti.get("skipped") else f"{dti.get('count', 0)} theaters refreshed"
        print(f"DTI Update — {result['finished_at']}")
        print(f"  elapsed   : {result['elapsed_seconds']:.1f}s")
        print(f"  ingestion : {result['ingestion']['total_records']} records ({result['ingestion']['status']})")
        print(f"  dti       : {status}")
        print(f"  time ok   : {'YES' if result['within_time_limit'] else 'NO (exceeded 10 min)'}")

    return 0 if result["within_time_limit"] else 1


if __name__ == "__main__":
    sys.exit(main())
