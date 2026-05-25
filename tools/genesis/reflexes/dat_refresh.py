#!/usr/bin/env python3
# CUI // SP-CTI
"""DAT Refresh Reflex — 6-hour Diplomatic Tension Index recompute.

Fires on Genesis 6-hour cadence. Refreshes DTI snapshots for all registered
theaters plus the global aggregate.

COOLDOWN_HOURS = 6 prevents double-firing within the same cadence window.
"""
IMPLEMENTATION_STATUS = "full"

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

REFLEX_NAME = "dat_refresh"
COOLDOWN_HOURS = 6


def _last_run_hours_ago(conn) -> float:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ran_at FROM genesis_reflex_log "
            "WHERE reflex_name = %s "
            "ORDER BY ran_at DESC LIMIT 1",
            (REFLEX_NAME,),
        )
        row = cur.fetchone()
        if not row:
            return float("inf")
        last = row[0]
        if isinstance(last, str):
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return float("inf")


def _log_run(conn, result: dict) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO genesis_reflex_log "
            "(id, reflex_name, ran_at, result_json) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), REFLEX_NAME,
             datetime.now(timezone.utc).isoformat(),
             json.dumps(result)),
        )
        conn.commit()
    except Exception:
        try:
            cur.execute(
                "INSERT INTO genesis_reflex_log "
                "(id, reflex_name, ran_at, result_json) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), REFLEX_NAME,
                 datetime.now(timezone.utc).isoformat(),
                 json.dumps(result)),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Could not log reflex run: %s", exc)


def _get_theaters(conn) -> list[str]:
    try:
        rows = conn.execute("SELECT theater_id FROM sg_theaters").fetchall()
        ids = [r[0] if not isinstance(r, dict) else r["theater_id"] for r in rows]
        return ids or ["global"]
    except Exception:
        return ["global"]


def run(args: dict, _ctx) -> dict:
    """Entry point called by Genesis reflex runner."""
    conn = get_connection()
    try:
        hours_ago = _last_run_hours_ago(conn)
        if hours_ago < COOLDOWN_HOURS:
            return {
                "skipped": True,
                "reason": f"last run {hours_ago:.1f}h ago (cooldown {COOLDOWN_HOURS}h)",
            }
    finally:
        conn.close()

    from tools.strategos.dat import refresh_dti

    theaters = []
    conn2 = get_connection()
    try:
        theaters = _get_theaters(conn2)
    finally:
        conn2.close()

    # Always include global aggregate
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
            logger.info("DTI refreshed theater=%s score=%.1f", theater_id, snap["dti_score"])
        except Exception as exc:
            results[theater_id] = {"error": str(exc)}
            logger.error("DTI refresh failed theater=%s: %s", theater_id, exc)

    result = {"theaters": results, "count": len(results)}

    conn3 = get_connection()
    try:
        _log_run(conn3, result)
    finally:
        conn3.close()

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run({}, None)
    print(json.dumps(out, indent=2))