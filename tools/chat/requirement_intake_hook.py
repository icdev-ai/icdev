#!/usr/bin/env python3
# CUI // SP-CTI
"""Auto-intake hook: detect requirements in chat messages -> decompose -> HITL review -> Kanban.

Called from chat_manager.send_message() in a daemon thread after every user message.
Flow:
  1. Fast regex pre-filter -- skip casual messages immediately
  2. Get or create an intake session linked to this chat context
  3. process_turn() -- extracts requirements from the message (deterministic)
  4. decompose_requirements() -- SAFe Epic>Feature>Story breakdown
  5. Create a HITL review instance (human must approve before Kanban promotion)
  6. On HITL approval -> intake_promote_handler.maybe_promote() -> kanban_tasks

Programmatic:
    from tools.chat.requirement_intake_hook import process_message_for_intake
    result = process_message_for_intake("ctx-abc123", "The system shall support SSO via SAML 2.0")
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection

logger = get_logger("icdev.chat.intake_hook")

# ---------------------------------------------------------------------------
# Requirement signal patterns -- fast pre-filter, no LLM
# ---------------------------------------------------------------------------

_REQ_PATTERNS = [
    r"\bshall\b",
    r"\bshould\b",
    r"\bmust\b",
    r"\bneeds? to\b",
    r"\bhas to\b",
    r"\brequirement[s]?\b",
    r"\buser stor(?:y|ies)\b",
    r"\bas a\b.{0,60}\bi want\b",
    r"\bi want\b",
    r"\bwe want\b",
    r"\bgiven\b.{0,120}\bwhen\b.{0,120}\bthen\b",
    r"\bcapabilit(?:y|ies)\b",
    r"\bacceptance criteri(?:a|on)\b",
    r"\bthe system\b.{0,60}\b(?:shall|should|must|will|needs?)\b",
    r"\bfeature request\b",
    r"\bfunctional requirement\b",
    # imperative sentences (starts with action verb)
    r"(?:^|\. )(?:create|build|develop|design|implement|deploy|integrate|generate)\b",
    r"(?:^|\. )(?:monitor|capture|track|detect|alert|display|visuali[sz]e|depict)\b",
    r"(?:^|\. )(?:analyze|analyse|process|correlate|aggregate|ingest|expose)\b",
    r"(?:^|\. )(?:ensure|enforce|provide|enable|allow|establish|configure)\b",
    # interest / desire expressions
    r"\bi(?:'?m| am) interested in\b",
    r"\b(?:looking for|looking to|hoping to|plan to|trying to)\b",
    r"\bwould like\b",
    r"\bi(?:'?d| would) like\b",
]

_REQ_RE = re.compile("|".join(_REQ_PATTERNS), re.IGNORECASE | re.DOTALL)


def _is_requirement_bearing(text: str) -> bool:
    return bool(_REQ_RE.search(text))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Mapping tables -- created once per DB via ensure_mapping_tables()
# ---------------------------------------------------------------------------

def ensure_mapping_tables() -> None:
    """Idempotent: create chat_intake_sessions and hitl_intake_pending if absent."""
    conn = get_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_intake_sessions (
                context_id  TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hitl_intake_pending (
                instance_id TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                context_id  TEXT,
                created_at  TEXT NOT NULL
            )"""
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Intake session management
# ---------------------------------------------------------------------------

def _get_or_create_session(context_id: str) -> str:
    """Return the intake session_id linked to this chat context, creating one if needed."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id FROM chat_intake_sessions WHERE context_id = %s",
            (context_id,),
        ).fetchone()
        if row:
            return row[0]
    finally:
        conn.close()

    from tools.requirements.intake_engine import create_session
    result = create_session(
        project_id=None,
        customer_name="Chat User",
        customer_org="ICDEV",
        impact_level="IL5",
        classification="CUI",
        created_by=f"chat:{context_id}",
        role="developer",
        goal="build",
    )
    session_id: str = result.get("session_id") or result.get("id") or ""
    if not session_id:
        raise ValueError(f"create_session returned no ID: {result}")

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO chat_intake_sessions (context_id, session_id, created_at) VALUES (%s, %s, %s)",
            (context_id, session_id, _now()),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    logger.info("Created intake session %s for chat context %s", session_id, context_id)
    return session_id


def _req_count(session_id: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM intake_requirements WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HITL instance creation
# ---------------------------------------------------------------------------

def _create_hitl_review(session_id: str, context_id: str, req_count: int) -> str:
    """Create a gatekeeper kanban task + HITL review instance. Returns instance_id."""
    from tools.workflow_hitl.engine import WorkflowEngine

    task_id = "task-" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:10]
    now = _now()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO kanban_tasks
               (id, title, description, task_type, priority, status,
                dispatch_source, scheduled_at, created_at, updated_at, hitl_stage)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                task_id,
                f"[HITL REVIEW] {req_count} requirement(s) from chat session",
                (
                    f"SAFe decomposition pending human review.\n\n"
                    f"Intake session: {session_id}\nChat context: {context_id}"
                ),
                "chore",
                "high",
                "backlog",
                f"chat:{context_id}",
                now, now, now,
                "review",
            ),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    engine = WorkflowEngine()
    instance_id = engine.create_instance(
        task_id=task_id,
        canvas_type="requirements",
    )

    # Auto-advance past the automated "build" stage -- human starts at "review"
    try:
        engine.advance_stage(instance_id)
    except Exception as exc:
        logger.debug("Auto-advance past build stage: %s", exc)

    # Store instance -> session mapping for post-approve promote
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO hitl_intake_pending
               (instance_id, session_id, context_id, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (instance_id) DO NOTHING""",
            (instance_id, session_id, context_id, now),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    logger.info(
        "HITL review instance %s created for session %s (%d reqs)",
        instance_id, session_id, req_count,
    )
    return instance_id


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def process_message_for_intake(
    context_id: str,
    user_message: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Analyze user_message; if requirements found, decompose and queue for HITL review.

    Returns:
        {"skipped": True}                                       -- no requirement signals
        {"session_id", "requirements_found", "hitl_instance_id", "tasks_staged", "review_url"}
        {"session_id", "requirements_found", "error"}          -- on failure
    """
    if not _is_requirement_bearing(user_message):
        return {"skipped": True}

    ensure_mapping_tables()

    # Get or create intake session
    try:
        session_id = _get_or_create_session(context_id)
        reqs_before = _req_count(session_id)
    except Exception as exc:
        logger.error("Intake hook -- session setup failed for context %s: %s", context_id, exc)
        return {"error": str(exc), "tasks_staged": 0}

    # Process turn through intake engine
    try:
        from tools.requirements.intake_engine import process_turn
        process_turn(session_id, user_message)
    except Exception as exc:
        logger.error("Intake hook -- process_turn failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "error": str(exc), "tasks_staged": 0}

    reqs_after = _req_count(session_id)
    new_reqs = reqs_after - reqs_before
    if new_reqs <= 0:
        return {"session_id": session_id, "requirements_found": 0, "tasks_staged": 0}

    # Decompose new requirements into SAFe hierarchy
    try:
        from tools.requirements.decomposition_engine import decompose_requirements
        decompose_requirements(
            session_id=session_id,
            target_level="story",
            generate_bdd=True,
            estimate=True,
        )
    except Exception as exc:
        logger.error("Intake hook -- decomposition failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "requirements_found": new_reqs, "error": str(exc), "tasks_staged": 0}

    # Route to HITL review -- human must approve before Kanban promotion
    try:
        instance_id = _create_hitl_review(session_id, context_id, new_reqs)
    except Exception as exc:
        logger.error("Intake hook -- HITL instance creation failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "requirements_found": new_reqs, "error": str(exc), "tasks_staged": 0}

    logger.info(
        "Intake hook: context=%s session=%s reqs=%d -> HITL review %s",
        context_id, session_id, new_reqs, instance_id,
    )
    return {
        "session_id": session_id,
        "requirements_found": new_reqs,
        "hitl_instance_id": instance_id,
        "tasks_staged": new_reqs,
        "review_url": f"/workflow/?instance={instance_id}",
    }
