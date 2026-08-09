#!/usr/bin/env python3
# CUI // SP-CTI
"""Post-HITL-approval handler: promote SAFe decomposition items to Kanban.

Called by feedback.py when a HITL workflow instance reaches final approval
(all stages complete, WfStatus.APPROVED).

Only acts on instances that were created by the chat intake hook
(i.e., those present in the hitl_intake_pending mapping table).
All other instances are silently skipped.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.workflow_hitl.intake_promote_handler")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def maybe_promote(instance_id: str) -> dict:
    """Promote SAFe items to Kanban if instance_id was created by the intake hook.

    Returns:
        {"skipped": True}                     -- instance not from intake hook
        {"inserted", "skipped", "session_id"} -- promote() result
        {"error": str}                         -- on failure
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id, context_id FROM hitl_intake_pending WHERE instance_id = %s",
            (instance_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning("hitl_intake_pending lookup failed for %s: %s", instance_id, exc)
        return {"error": str(exc)}
    finally:
        conn.close()

    if not row:
        return {"skipped": True}

    session_id = row[0]
    context_id = row[1] or ""

    try:
        from tools.requirements.intake_kanban_promoter import promote
        result = promote(session_id=session_id)
    except Exception as exc:
        logger.error("promote() failed for session %s after HITL approval: %s", session_id, exc)
        return {"error": str(exc), "session_id": session_id}

    inserted = result.get("inserted", 0)
    logger.info(
        "HITL post-approve: %d task(s) promoted to Kanban for session=%s context=%s instance=%s",
        inserted, session_id, context_id, instance_id,
    )

    # Mark gatekeeper kanban task as done
    try:
        conn = get_connection()
        now = _now()
        conn.execute(
            """UPDATE kanban_tasks SET status='done', completed_at=%s, updated_at=%s
               WHERE dispatch_source=%s AND task_type='chore' AND hitl_stage='review'
               AND status IN ('backlog', 'in_progress')""",
            (now, now, f"chat:{context_id}"),
        )
        try:
            conn.commit()
        except Exception:
            pass
        conn.close()
    except Exception as exc:
        logger.debug("Could not mark gatekeeper task done: %s", exc)

    return result
