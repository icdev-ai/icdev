#!/usr/bin/env python3
# CUI // SP-CTI
"""Workflow loop chat extension handler (Phase 66, D-WF-1).

Hooks into ``chat_message_after`` to surface workflow loop status and
next-action recommendations in chat. Follows the same pattern as
010_ai_governance_chat.py (D325, D327).

When the user is chatting in a project context and has open workflow
loops, the extension periodically injects advisory messages such as:
  - "Loop 'auth-module' is in APPLYING state — 2/3 tasks done"
  - "Loop needs UNIFY — reconcile planned vs actual before closing"
  - "No open loops — create one to track your next task"

Advisory messages are throttled by a cooldown (default: 8 turns).

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection, table_exists as _table_exists
from pathlib import Path

logger = get_logger("icdev.extensions.workflow_loop_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Cooldown tracking (in-memory, per-context)
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 8
_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Workflow loop status check
# ---------------------------------------------------------------------------


def _check_loop_status(project_id: str) -> dict | None:
    """Check workflow loop status for a project. Returns advisory dict or None."""
    if not DB_PATH.exists():
        return None

    try:
        conn = get_connection()
    except Exception:
        return None

    try:
        if not _table_exists(conn, "workflow_loops"):
            return None

        loops = conn.execute(
            """SELECT * FROM workflow_loops
               WHERE project_id = %s AND status NOT IN ('closed', 'abandoned')
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()

        if not loops:
            return None

        # Pick the most actionable loop
        for loop in loops:
            status = loop["status"]
            phase = loop["phase_name"]
            lid = loop["id"]

            if status == "unifying":
                # Check if reconciliation exists
                has_recon = False
                if _table_exists(conn, "workflow_reconciliations"):
                    r = conn.execute(
                        "SELECT COUNT(*) as cnt FROM workflow_reconciliations WHERE loop_id = %s",
                        (lid,),
                    ).fetchone()
                    has_recon = (r[0] if isinstance(r, (tuple, list)) else r["cnt"]) > 0

                if not has_recon:
                    return {
                        "gap_id": "loop_needs_reconciliation",
                        "severity": "high",
                        "message": (
                            f"Loop '{phase}' is in UNIFY state but has no reconciliation. "
                            "Run the reconciler to compare planned vs actual before closing."
                        ),
                        "action": f"python tools/workflow/reconciler.py --reconcile --loop-id {lid} --json",
                        "loop_id": lid,
                        "loop_status": status,
                    }

            elif status == "applied":
                return {
                    "gap_id": "loop_needs_unify",
                    "severity": "medium",
                    "message": (
                        f"Loop '{phase}' has all tasks completed. Start UNIFY to reconcile planned vs actual work."
                    ),
                    "action": f"python tools/workflow/loop_engine.py --start-unify --loop-id {lid} --json",
                    "loop_id": lid,
                    "loop_status": status,
                }

            elif status == "applying":
                remaining = loop["task_count"] - loop["tasks_completed"]
                if remaining > 0:
                    return {
                        "gap_id": "loop_in_progress",
                        "severity": "low",
                        "message": (
                            f"Loop '{phase}' is in APPLY state — "
                            f"{loop['tasks_completed']}/{loop['task_count']} tasks done, "
                            f"{remaining} remaining."
                        ),
                        "action": f"python tools/workflow/loop_engine.py --complete-task --loop-id {lid} --json",
                        "loop_id": lid,
                        "loop_status": status,
                    }

            elif status == "planned":
                return {
                    "gap_id": "loop_not_started",
                    "severity": "low",
                    "message": (
                        f"Loop '{phase}' is planned but not started. Begin the APPLY phase to start executing tasks."
                    ),
                    "action": f"python tools/workflow/loop_engine.py --start-apply --loop-id {lid} --json",
                    "loop_id": lid,
                    "loop_status": status,
                }

        return None
    except Exception as exc:
        logger.debug("Error checking workflow loops: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject workflow loop advisory.

    Args:
        context: dict with keys context_id, role, content, turn_number,
                 and optionally project_id.

    Returns:
        context dict, possibly with ``workflow_advisory`` key added.
    """
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")

    # Only process assistant responses
    if context.get("role") != "assistant":
        return context

    # Cooldown check
    if not _should_advise(context_id, turn_number):
        return context

    # No project context → skip
    if not project_id:
        return context

    # Check for workflow loop status
    advisory = _check_loop_status(project_id)
    if not advisory:
        return context

    _record_advisory(context_id, turn_number)

    result = dict(context)
    result["workflow_advisory"] = advisory
    return result


# ---------------------------------------------------------------------------
# Extension registration metadata
# ---------------------------------------------------------------------------

NAME = "workflow_loop_chat"
PRIORITY = 30
ALLOW_MODIFICATION = True
DESCRIPTION = "Surface workflow loop status and next-action advisories in chat (Phase 66, D-WF-1)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
