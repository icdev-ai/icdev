#!/usr/bin/env python3
# CUI // SP-CTI
"""DAT Refresh Reflex — 6-hour Diplomatic Tension Index recompute.

Fires on Genesis 6-hour cadence. Refreshes DTI snapshots for all registered
theaters plus the global aggregate.

COOLDOWN_HOURS_DEFAULT = 6 is the static fallback. When anomaly detection is
enabled and sufficient DTI history exists, the reflex adapts its cooldown:
shorter on detected spikes/drops, longer during stable periods.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import logging
import math
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from datetime import datetime, timezone  # noqa: E402

import yaml  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

REFLEX_NAME = "dat_refresh"
COOLDOWN_HOURS_DEFAULT = 6
# Backward-compat alias — external callers that read COOLDOWN_HOURS still work.
COOLDOWN_HOURS = COOLDOWN_HOURS_DEFAULT

_DAT_CONFIG_PATH = BASE_DIR / "args" / "dat_config.yaml"


# ---------------------------------------------------------------------------
# Anomaly-detection helpers
# ---------------------------------------------------------------------------

def _load_anomaly_config() -> dict:
    """Load the anomaly_detection block from args/dat_config.yaml.

    Returns safe defaults if the block is absent or the file is unreadable.
    """
    defaults = {
        "enabled": True,
        "min_samples": 4,
        "z_score_threshold": 2.0,
        "cooldown_hours_min": 1,
        "cooldown_hours_max": 12,
        "fallback_to_static": True,
    }
    try:
        raw = yaml.safe_load(_DAT_CONFIG_PATH.read_text(encoding="utf-8"))
        block = (raw or {}).get("anomaly_detection", {})
        return {**defaults, **block}
    except Exception as exc:
        logger.debug("Could not read anomaly_detection config: %s", exc)
        return defaults


def _compute_adaptive_cooldown(conn, *, cfg: dict | None = None) -> float:
    """Return the adaptive cooldown in hours based on recent DTI history.

    Algorithm:
    1. Fetch the last ``cfg["min_samples"] + 1`` DTI snapshots (any theater).
    2. If fewer than ``min_samples`` rows exist → return COOLDOWN_HOURS_DEFAULT.
    3. Compute mean and std of all but the latest score (historical baseline).
    4. Compute z-score for the latest score.
    5. |z| >= z_score_threshold → anomaly → return cooldown_hours_min.
    6. Otherwise → return cooldown_hours_max.
    """
    if cfg is None:
        cfg = _load_anomaly_config()

    if not cfg.get("enabled", True):
        return float(COOLDOWN_HOURS_DEFAULT)

    min_samples: int = int(cfg["min_samples"])
    z_thresh: float = float(cfg["z_score_threshold"])
    cool_min: float = float(cfg["cooldown_hours_min"])
    cool_max: float = float(cfg["cooldown_hours_max"])

    try:
        rows = conn.execute(
            "SELECT dti_score, computed_at "
            "FROM sg_dat_dti_snapshots "
            "ORDER BY computed_at DESC "
            "LIMIT %s",
            (min_samples + 1,),
        ).fetchall()
    except Exception:
        try:
            rows = conn.execute(
                "SELECT dti_score, computed_at "
                "FROM sg_dat_dti_snapshots "
                "ORDER BY computed_at DESC "
                "LIMIT %s",
                (min_samples + 1,),
            ).fetchall()
        except Exception as exc:
            logger.debug("Could not query DTI snapshots for anomaly detection: %s", exc)
            return float(COOLDOWN_HOURS_DEFAULT)

    scores = [
        float(r[0] if not isinstance(r, dict) else r["dti_score"])
        for r in rows
    ]

    if len(scores) < min_samples:
        return float(COOLDOWN_HOURS_DEFAULT)

    latest = scores[0]
    historical = scores[1:]

    n = len(historical)
    mean = sum(historical) / n
    variance = sum((x - mean) ** 2 for x in historical) / n
    std = math.sqrt(variance)

    if std == 0.0:
        # Perfect stability — no anomaly possible.
        return cool_max

    z_score = abs(latest - mean) / std
    if z_score >= z_thresh:
        logger.info(
            "DTI anomaly detected: z=%.2f (threshold=%.2f) → cooldown=%sh",
            z_score, z_thresh, cool_min,
        )
        return cool_min

    return cool_max


# ---------------------------------------------------------------------------
# Reflex internals
# ---------------------------------------------------------------------------

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
                "(id, reflex_name, ran_at, result_json) VALUES (%s, %s, %s, %s)",
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: dict, _ctx) -> dict:
    """Entry point called by Genesis reflex runner."""
    conn = get_connection()
    try:
        hours_ago = _last_run_hours_ago(conn)
        effective_cooldown = _compute_adaptive_cooldown(conn)
        if hours_ago < effective_cooldown:
            return {
                "skipped": True,
                "reason": (
                    f"last run {hours_ago:.1f}h ago "
                    f"(adaptive cooldown {effective_cooldown:.1f}h)"
                ),
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
    out = run({}, None)
    print(json.dumps(out, indent=2))
