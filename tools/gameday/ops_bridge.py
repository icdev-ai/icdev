# CUI // SP-CTI
"""AI GameDay League — OHC (Ops Hub) bridge.

Publishes GameDay LLMOps/MLOps/AIOps events to the existing
tools/ops_hub/ pipeline so they appear in the /ops dashboard.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging

log = get_logger(__name__)


def publish_llmops_summary(tournament_id: int) -> dict:
    """Push tournament LLMOps data to the ops hub aggregator."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT model,
                      COUNT(*) AS calls,
                      SUM(prompt_tokens + completion_tokens) AS total_tokens,
                      AVG(latency_ms) AS avg_latency_ms,
                      COUNT(CASE WHEN error IS NOT NULL THEN 1 END) AS errors
               FROM gd_ai_llmops_events
               WHERE tournament_id = ?
               GROUP BY model""",
            (tournament_id,),
        ).fetchall()
        summary = [dict(r) for r in rows]
        log.info("[OpsBridge] LLMOps summary: %d model rows", len(summary))
        return {"status": "ok", "rows": summary}
    except Exception as exc:
        log.warning("[OpsBridge] LLMOps publish failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def publish_mlops_training_event(
    tournament_id: int,
    dataset_id: str,
    pair_count: int,
    model_name: str,
) -> None:
    """Record a LoRA training trigger event in the ops hub."""
    try:
        from tools.ops_hub.mlops_engine import log_training_event
        log_training_event(
            run_name=f"gameday-lora-{tournament_id}",
            dataset_id=dataset_id,
            sample_count=pair_count,
            model_name=model_name,
            source="gameday_league",
        )
    except (ImportError, Exception) as exc:
        log.debug("[OpsBridge] mlops training event skipped: %s", exc)


def publish_aiops_health(tournament_id: int) -> None:
    """Surface a GameDay health check to the AIOps SLO dashboard."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS rounds,
                      SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed
               FROM gd_ai_rounds WHERE tournament_id = ?""",
            (tournament_id,),
        ).fetchone()
        if row:
            health = "healthy" if row["rounds"] == row["completed"] else "degraded"
            log.info("[OpsBridge] AIOps health: %s (%d/%d rounds)", health, row["completed"], row["rounds"])
    except Exception as exc:
        log.debug("[OpsBridge] aiops health skipped: %s", exc)
