# CUI // SP-CTI
"""NOVA ECHO — Reflexion Loop Genesis Reflex (weekly cadence).

Runs the batch reflexion pass across all active task_types, generating
improvement artifacts and proposing hardprompt/goal amendments as kanban
suggested cards.

Risk tier: YELLOW (reads traces, writes improvement artifacts + kanban cards).
Schedule: weekly (Sunday 02:00 UTC).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

LOG = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _create_amendment_card(task_type: str, artifact_id: str, score: float) -> None:
    """Propose a skill amendment review card for human-in-the-loop approval."""
    title = f"[reflexion] Review improvement artifact for task_type={task_type!r}"
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM kanban_tasks WHERE title = %s AND status NOT IN ('done','dismissed') LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return

        task_id = f"nova-echo-amend-{uuid.uuid4().hex[:8]}"
        body = (
            f"**ECHO Reflexion — Skill Amendment Proposal**\n\n"
            f"- Task type: `{task_type}`\n"
            f"- Artifact: `{artifact_id}`\n"
            f"- Current success rate: `{score:.1%}`\n\n"
            f"Review the improvement artifact and, if accepted, apply its suggestions "
            f"to the relevant `.agents/skills/icdev-*` skill file or `hardprompts/` template.\n\n"
            f"**To review:**\n"
            f"```sql\n"
            f"SELECT improvement_text FROM agent_improvement_artifacts "
            f"WHERE artifact_id = '{artifact_id}';\n"
            f"```\n\n"
            f"After applying, mark this card done."
        )
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, status, priority, source, created_at, updated_at)
            VALUES (%s, %s, %s, 'suggested', 'medium', 'reflexion_loop_reflex', %s, %s)
            """,
            (task_id, title, body, _utcnow(), _utcnow()),
        )
        conn.commit()
        LOG.info("[reflexion_loop] created amendment card %s", task_id)
    except Exception as exc:
        LOG.warning("[reflexion_loop] failed to create amendment card: %s", exc)


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute the Reflexion Loop Reflex."""
    import os
    from tools.workflow.reflexion_agent import run_batch_reflexion

    dry_run: bool = config.get("dry_run", False)
    colearn_enabled = os.getenv("ICDEV_HARNESS_COLEARN", "").lower() in ("true", "1")

    if not colearn_enabled:
        LOG.info("[reflexion_loop] ICDEV_HARNESS_COLEARN not set; reflex is a no-op")
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"skipped": True, "reason": "co-learning disabled"},
        }

    results = run_batch_reflexion(dry_run=dry_run)
    n_processed = results.get("task_types_processed", 0)
    artifacts_created = 0
    amendment_cards = 0

    for task_type, result in results.get("results", {}).items():
        if result.get("skipped"):
            continue
        if "artifact_id" in result and not dry_run:
            artifacts_created += 1
            # Only propose amendment card if success rate is below 0.8 (room to improve)
            score = result.get("baseline_score", 1.0)
            if score < 0.8:
                _create_amendment_card(task_type, result["artifact_id"], score)
                amendment_cards += 1

    LOG.info(
        "[reflexion_loop] processed %d task_types; %d artifacts; %d amendment cards",
        n_processed, artifacts_created, amendment_cards,
    )

    return {
        "success": True,
        "metric_value": float(artifacts_created),
        "details": {
            "task_types_processed": n_processed,
            "artifacts_created": artifacts_created,
            "amendment_cards": amendment_cards,
            "dry_run": dry_run,
            "colearn_enabled": colearn_enabled,
        },
    }
