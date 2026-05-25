# CUI // SP-CTI
"""Cyber Feed Refresh Reflex — daily NVD delta + CISA KEV refresh.

Fires on Genesis 24-hour cadence. Runs CISA KEV sync (which also upserts
new CVE rows into sg_cve_feed) and reports row counts back as reflex output.
NVD delta ingest is handled by the same CISA KEV merge path: every KEV entry
that is not yet in sg_cve_feed is inserted as a new row (is_kev=1).

COOLDOWN_HOURS = 24 prevents double-firing within a calendar day.
"""

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

REFLEX_NAME = "cyber_feed_refresh"
COOLDOWN_HOURS = 24


def _last_run_hours_ago(conn) -> float:
    """Return hours since the last run, or inf if never run."""
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
        delta = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return delta
    except Exception:
        return float("inf")


def _log_run(conn, result: dict) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO genesis_reflex_log (id, reflex_name, ran_at, result_json) "
            "VALUES (%s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                REFLEX_NAME,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(result),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("cyber_feed_refresh: could not log run: %s", exc)


def run(context: dict, session) -> dict:  # noqa: ANN001
    """Genesis reflex entry point.

    Returns:
        {ok, nvd_upserted, kev_flagged, skipped_cooldown, ran_at}
    """
    conn = get_connection()
    try:
        hours_ago = _last_run_hours_ago(conn)
        if hours_ago < COOLDOWN_HOURS:
            result = {
                "ok": True,
                "skipped_cooldown": True,
                "hours_since_last": round(hours_ago, 1),
                "nvd_upserted": 0,
                "kev_flagged": 0,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info("cyber_feed_refresh: cooldown active (%.1fh < %dh)", hours_ago, COOLDOWN_HOURS)
            return result

        from tools.strategos.cisa_kev_importer import run as kev_run  # noqa: PLC0415

        kev_result = kev_run(sync=True)

        result = {
            "ok": kev_result.get("ok", False),
            "skipped_cooldown": False,
            "nvd_upserted": kev_result.get("new_inserted", 0),
            "kev_flagged": kev_result.get("kev_flagged", 0),
            "ran_at": datetime.now(timezone.utc).isoformat(),
        }
        if not result["ok"]:
            result["error"] = kev_result.get("error", "unknown")

        _log_run(conn, result)
        return result

    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run({}, None), indent=2))
