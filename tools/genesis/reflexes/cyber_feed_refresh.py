# CUI // SP-CTI
from __future__ import annotations

"""Cyber Feed Refresh Reflex — daily NVD delta + CISA KEV refresh.

Fires on Genesis 24-hour cadence. Runs CISA KEV sync (which also upserts
new CVE rows into sg_cve_feed) and reports row counts back as reflex output.
NVD delta ingest is handled by the same CISA KEV merge path: every KEV entry
that is not yet in sg_cve_feed is inserted as a new row (is_kev=1).

Cooldown is adaptive: anomaly detection on historical run activity (z-score)
dynamically adjusts between MIN_COOLDOWN_HOURS and MAX_COOLDOWN_HOURS.
"""
IMPLEMENTATION_STATUS = "full"
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

# Adaptive cooldown bounds (hours)
DEFAULT_COOLDOWN_HOURS = 24
MIN_COOLDOWN_HOURS = 6   # Used when high-activity anomaly detected
MAX_COOLDOWN_HOURS = 48  # Used when low-activity anomaly detected

# Anomaly detection parameters
ANOMALY_WINDOW = 7       # Historical runs to establish baseline
ANOMALY_Z_THRESHOLD = 2.0  # Z-score magnitude to flag anomaly


def _compute_adaptive_cooldown(conn) -> float:
    """Return adaptive cooldown hours using z-score anomaly detection.

    Inspects the last ANOMALY_WINDOW actual (non-skipped) runs.  If the most
    recent run's total CVE activity (nvd_upserted + kev_flagged) deviates by
    more than ANOMALY_Z_THRESHOLD standard deviations from the historical mean,
    the cooldown is shortened (high activity) or extended (low activity).
    Falls back to DEFAULT_COOLDOWN_HOURS when history is insufficient.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT result_json FROM genesis_reflex_log "
            "WHERE reflex_name = %s AND result_json IS NOT NULL "
            "ORDER BY ran_at DESC LIMIT %s",
            (REFLEX_NAME, ANOMALY_WINDOW),
        )
        rows = cur.fetchall()
    except Exception as exc:
        logger.warning("cyber_feed_refresh: anomaly detection query failed: %s", exc)
        return DEFAULT_COOLDOWN_HOURS

    activities: list[float] = []
    for row in rows:
        try:
            result = json.loads(row[0])
            if result.get("skipped_cooldown"):
                continue
            activities.append(
                float(result.get("nvd_upserted", 0) + result.get("kev_flagged", 0))
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    if len(activities) < 3:
        return DEFAULT_COOLDOWN_HOURS

    mean = sum(activities) / len(activities)
    variance = sum((x - mean) ** 2 for x in activities) / len(activities)
    std = variance ** 0.5

    last_activity = activities[0]
    z_score = (last_activity - mean) / std if std > 0 else 0.0

    if z_score >= ANOMALY_Z_THRESHOLD:
        cooldown = MIN_COOLDOWN_HOURS
        logger.info(
            "cyber_feed_refresh: high-activity anomaly (z=%.2f >= %.1f), "
            "shortening cooldown to %dh",
            z_score, ANOMALY_Z_THRESHOLD, cooldown,
        )
    elif z_score <= -ANOMALY_Z_THRESHOLD:
        cooldown = MAX_COOLDOWN_HOURS
        logger.info(
            "cyber_feed_refresh: low-activity anomaly (z=%.2f <= -%.1f), "
            "extending cooldown to %dh",
            z_score, ANOMALY_Z_THRESHOLD, cooldown,
        )
    else:
        cooldown = DEFAULT_COOLDOWN_HOURS

    return cooldown


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
        adaptive_cooldown = _compute_adaptive_cooldown(conn)
        hours_ago = _last_run_hours_ago(conn)
        if hours_ago < adaptive_cooldown:
            result = {
                "ok": True,
                "skipped_cooldown": True,
                "hours_since_last": round(hours_ago, 1),
                "cooldown_hours": adaptive_cooldown,
                "nvd_upserted": 0,
                "kev_flagged": 0,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                "cyber_feed_refresh: cooldown active (%.1fh < %.1fh adaptive)",
                hours_ago, adaptive_cooldown,
            )
            return result

        from tools.strategos.cisa_kev_importer import run as kev_run  # noqa: PLC0415

        kev_result = kev_run(sync=True)

        result = {
            "ok": kev_result.get("ok", False),
            "skipped_cooldown": False,
            "cooldown_hours": adaptive_cooldown,
            "nvd_upserted": kev_result.get("new_inserted", 0),
            "kev_flagged": kev_result.get("kev_flagged", 0),
            "ran_at": datetime.now(timezone.utc).isoformat(),
        }
        if not result["ok"]:
            result["error"] = kev_result.get("error", "unknown")

        _log_run(conn, result)

        # Notify downstream canvases (e.g. NDC vuln overlays) that new CVE /
        # KEV data landed so they can mark affected overlays stale.
        try:
            total_new = int(result.get("nvd_upserted", 0)) + int(
                result.get("kev_flagged", 0)
            )
            if total_new > 0:
                from tools.canvas.event_bus import publish as _eb_publish

                _eb_publish(
                    "sdc",
                    "sdc.cve.published",
                    {
                        "nvd_upserted": result.get("nvd_upserted", 0),
                        "kev_flagged": result.get("kev_flagged", 0),
                        "cve_ids": [],
                    },
                )
        except Exception as _emit_exc:  # noqa: BLE001 — bus failure must not break the reflex
            logger.debug(
                "cyber_feed_refresh: cve.published emit skipped: %s", _emit_exc
            )

        return result

    finally:
        conn.close()


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run({}, None), indent=2))
