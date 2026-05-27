# CUI // SP-CTI
"""Harness Reflex — runs every 6h, checks evaluation gates, promotes degradation cards.

Calls eval_harness.check_gates() and creates kanban tasks for any metrics that
have fallen below threshold. Idempotent: skips if an open degradation card
already exists for the same reflex+metric pair.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _open_degradation_card_exists(reflex: str, metric: str) -> bool:
    """Return True if an unresolved harness degradation card already exists."""
    try:
        conn = _conn()
        row = conn.execute(
            """
            SELECT id FROM kanban_tasks
             WHERE title LIKE ?
               AND status NOT IN ('done', 'dismissed')
             LIMIT 1
            """,
            (f"%[harness] {reflex}.{metric}%",),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _create_degradation_card(alert: dict) -> str | None:
    """Insert a kanban_tasks row for a degradation alert."""
    import uuid
    reflex = alert["reflex"]
    metric = alert["metric"]
    value = alert["value"]
    severity = alert["severity"]
    recommendation = alert["recommendation"]

    task_id = f"harness-{reflex[:6]}-{metric[:8]}-{uuid.uuid4().hex[:6]}"
    title = f"[harness] {reflex}.{metric} degraded ({value:.3f})"
    body = (
        f"**Harness Gate Failure**\n\n"
        f"- Reflex: `{reflex}`\n"
        f"- Metric: `{metric}` = `{value:.4f}`\n"
        f"- Threshold: `{alert['threshold']}`\n"
        f"- Severity: `{severity}`\n\n"
        f"**Recommendation:** {recommendation}"
    )

    try:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, status, priority, source, created_at, updated_at)
            VALUES (?, ?, ?, 'backlog', ?, 'harness_reflex', ?, ?)
            """,
            (
                task_id,
                title,
                body,
                "high" if severity == "high" else "medium",
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
        LOG.info("[harness] Created degradation card: %s", task_id)
        return task_id
    except Exception as exc:
        LOG.warning("[harness] Failed to create degradation card: %s", exc)
        return None


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute the Harness Reflex."""
    from tools.genesis.harness.eval_harness import check_gates, compute_metrics

    dry_run: bool = config.get("dry_run", False)

    # Compute metrics for summary
    metrics_summary = {}
    for reflex in ("oracle_triage", "heal"):
        metrics_summary[reflex] = compute_metrics(reflex, window_days=30)

    # Check gates
    alerts = check_gates()
    new_cards: list[str] = []

    for alert in alerts:
        reflex = alert["reflex"]
        metric = alert["metric"]

        if _open_degradation_card_exists(reflex, metric):
            LOG.debug("[harness] skipping %s.%s — card already open", reflex, metric)
            continue

        if not dry_run:
            card_id = _create_degradation_card(alert)
            if card_id:
                new_cards.append(card_id)
        else:
            LOG.info("[harness] [dry-run] would create card for %s.%s", reflex, metric)
            new_cards.append(f"dry-run:{reflex}.{metric}")

    status = "ok" if not alerts else ("degraded" if new_cards else "cards_exist")

    return {
        "success": True,
        "metric_value": float(len(new_cards)),
        "details": {
            "status": status,
            "alerts_found": len(alerts),
            "new_cards_created": len(new_cards),
            "new_card_ids": new_cards,
            "metrics": metrics_summary,
            "dry_run": dry_run,
        },
    }
