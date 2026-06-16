# CUI // SP-CTI
"""NOVA Reflexion Loop — generates improvement traces on ACE instance completion.

Fires:
  - On a weekly schedule (Sunday 02:00 UTC) via the genesis reflex system.
  - On every ACE instance completion via canvas event bus subscription
    (event_type='task.completed', target_canvas='nova').

Call register() once at app startup to wire the event-driven trigger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("icdev.nova.reflexion_loop")

# Topics this reflex listens to on the nova canvas
LISTEN_TOPICS = ("task.completed",)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_for_instance(instance_id: str) -> str:
    """Record an execution trace for a completed ACE instance.

    Returns the trace_id of the newly created row.
    """
    from tools.workflow.trace_logger import start_trace, log_event, close_trace

    trace_id = start_trace(
        task_id=instance_id,
        task_type="ace_instance",
        skill_used="reflexion_loop",
    )
    log_event(trace_id, "milestone", {
        "trigger": "instance_complete",
        "instance_id": instance_id,
    })
    close_trace(
        trace_id,
        outcome="success",
        lesson_pattern="ace_instance_complete",
        improvement_notes=f"Reflexion triggered by ACE instance {instance_id} completion",
    )
    logger.info("[reflexion_loop] trace %s created for instance %s", trace_id, instance_id)
    return trace_id


def _on_task_completed(event_id: str, canvas_id: str, event_type: str, payload: dict) -> None:
    """Canvas event bus handler — called when ACE publishes task.completed."""
    instance_id = payload.get("instance_id", event_id)
    try:
        run_for_instance(instance_id)
    except Exception as exc:
        logger.warning("[reflexion_loop] handler error for instance %s: %s", instance_id, exc)


def register() -> None:
    """Subscribe reflexion_loop to LISTEN_TOPICS events on the 'nova' canvas.

    Safe to call at app startup. In tests, clear event_bus._LISTENERS before
    calling to avoid duplicate registrations.
    """
    from icdev.tools.canvas.event_bus import subscribe

    for topic in LISTEN_TOPICS:
        subscribe("nova", topic, _on_task_completed)
    logger.info("[reflexion_loop] subscribed to %s on nova canvas", LISTEN_TOPICS)


def run_weekly() -> dict:
    """Weekly scheduled sweep — placeholder for future batch reflexion."""
    logger.info("[reflexion_loop] weekly sweep triggered")
    return {"status": "ok", "trigger": "weekly"}
