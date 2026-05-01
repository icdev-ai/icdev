# CUI // SP-CTI
"""Structured feedback capture for HITL approval gates."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_feedback(
    approval_id: str,
    decision: str,
    submitted_by: str,
    *,
    rating: int | None = None,
    comments: str | None = None,
    feedback_types: list | None = None,
    improvement_tags: list | None = None,
    kickback_reason: str | None = None,
    citations: list | None = None,
) -> str:
    """Submit reviewer feedback for an approval gate.

    decision: 'approve' | 'kickback' | 'conditional'
    When decision='kickback', kickback_reason is required (enforced when
    ICDEV_HITL_REQUIRE_FEEDBACK=true in .env).
    """
    import os
    require_feedback = os.getenv("ICDEV_HITL_REQUIRE_FEEDBACK", "true").lower() in ("true", "1")
    if decision == "kickback" and require_feedback and not kickback_reason:
        raise ValueError("kickback_reason is required when ICDEV_HITL_REQUIRE_FEEDBACK=true")

    conn = get_connection()
    try:
        approval = conn.execute("SELECT * FROM wf_approvals WHERE id=?", (approval_id,)).fetchone()
        if not approval:
            raise ValueError(f"Approval {approval_id!r} not found")
        approval = dict(approval)
        instance_id = approval["instance_id"]
        stage = approval["stage"]
        inst = conn.execute("SELECT * FROM wf_instances WHERE id=?", (instance_id,)).fetchone()
        inst = dict(inst) if inst else {}
    finally:
        conn.close()

    feedback_id = f"wff-{uuid.uuid4().hex[:12]}"
    citations_json = json.dumps(citations) if citations else None

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_feedback
               (id, approval_id, instance_id, task_id, canvas_type, template_id,
                stage, decision, feedback_types, rating, comments, improvement_tags,
                kickback_reason, citations_json, submitted_by, submitted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                feedback_id, approval_id, instance_id,
                inst.get("task_id"), inst.get("canvas_type"), inst.get("template_id"),
                stage, decision,
                json.dumps(feedback_types) if feedback_types else None,
                rating, comments,
                json.dumps(improvement_tags) if improvement_tags else None,
                kickback_reason, citations_json, submitted_by, _now(),
            ),
        )
        # Update approval status
        conn.execute(
            "UPDATE wf_approvals SET status=?, updated_at=? WHERE id=?",
            (decision, _now(), approval_id),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    logger.info("Feedback %s submitted for approval %s: decision=%s", feedback_id, approval_id, decision)

    # Trigger next action
    if decision == "approve" or decision == "conditional":
        try:
            from tools.workflow_hitl.engine import WorkflowEngine
            WorkflowEngine().advance_stage(instance_id)
        except Exception as exc:
            logger.warning("advance_stage failed after feedback: %s", exc)
    elif decision == "kickback":
        try:
            from tools.workflow_hitl.engine import WorkflowEngine
            WorkflowEngine().kickback(instance_id, kickback_reason or "", submitted_by)
        except Exception as exc:
            logger.warning("kickback failed after feedback: %s", exc)

    return feedback_id


def get_by_instance(instance_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_feedback WHERE instance_id=? ORDER BY submitted_at",
            (instance_id,),
        ).fetchall()
        results = []
        for r in rows:
            fb = dict(r)
            # Attach citations
            from tools.workflow_hitl import citation_manager
            fb["citations"] = citation_manager.get_by_record("feedback", fb["id"])
            results.append(fb)
        return results
    finally:
        conn.close()


def get_all(
    canvas_type: str | None = None,
    task_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    limit: int = 100,
) -> list:
    conn = get_connection()
    try:
        clauses, params = [], []
        if canvas_type:
            clauses.append("canvas_type=?")
            params.append(canvas_type)
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        if period_start:
            clauses.append("submitted_at>=?")
            params.append(period_start)
        if period_end:
            clauses.append("submitted_at<=?")
            params.append(period_end)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM wf_feedback {where} ORDER BY submitted_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
