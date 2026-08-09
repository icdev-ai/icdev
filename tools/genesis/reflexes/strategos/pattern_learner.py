# CUI // SP-CTI
"""Pattern Learner Reflex — Reinforcement Learning loop for signal-scoring weights.

Runs every 24h.  Reads recent annotation deltas from sg_pattern_learner_log,
aggregates per-signature weight adjustments, and writes consolidated entries
back to the log so signal_scout can observe the drift on the next cycle.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

_LOOKBACK_HOURS = 24


def fetch_annotations_since(since_ts: str) -> List[Dict[str, Any]]:
    """Return sg_pattern_learner_log rows with ts >= since_ts."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, signature_id, delta, reason, ts "
            "FROM sg_pattern_learner_log "
            "WHERE ts >= %s "
            "ORDER BY ts ASC",
            (since_ts,),
        )
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def adjust_weights(annotations: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate per-signature deltas; return {signature_id: net_delta}."""
    net: Dict[str, float] = {}
    for ann in annotations:
        sig = ann["signature_id"]
        net[sig] = net.get(sig, 0.0) + float(ann["delta"])
    return net


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Orchestrate the RL loop: fetch annotations → adjust weights → log results."""
    since_dt = datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
    since_ts = since_dt.strftime("%Y-%m-%d %H:%M:%S")

    annotations = fetch_annotations_since(since_ts)
    if not annotations:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"annotations_found": 0, "weights_adjusted": 0, "since_ts": since_ts},
        }

    net_deltas = adjust_weights(annotations)

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        for sig_id, net_delta in net_deltas.items():
            conn.execute(
                "INSERT INTO sg_pattern_learner_log (signature_id, delta, reason, ts) "
                "VALUES (%s, %s, %s, %s)",
                (sig_id, net_delta, "pattern_learner:24h-consolidation", now_ts),
            )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "pattern_learner: %d annotations → %d signatures adjusted",
        len(annotations),
        len(net_deltas),
    )
    return {
        "success": True,
        "metric_value": float(len(net_deltas)),
        "details": {
            "annotations_found": len(annotations),
            "weights_adjusted": len(net_deltas),
            "since_ts": since_ts,
            "net_deltas": net_deltas,
        },
    }
