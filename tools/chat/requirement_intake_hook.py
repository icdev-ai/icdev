#!/usr/bin/env python3
# CUI // SP-CTI
"""Auto-intake hook: detect requirements in chat messages → decompose → promote to Kanban.

Called from chat_manager.send_message() in a daemon thread after every user message.
Flow:
  1. Fast regex pre-filter — skip casual messages immediately
  2. Get or create an intake session linked to this chat context
  3. process_turn() — extracts requirements from the message (deterministic, no LLM)
  4. decompose_requirements() — SAFe Epic>Feature>Story breakdown
  5. promote() — push new items to kanban_tasks (idempotent)

Programmatic:
    from tools.chat.requirement_intake_hook import process_message_for_intake
    result = process_message_for_intake("ctx-abc123", "The system shall support SSO via SAML 2.0")
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("icdev.chat.intake_hook")

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Requirement signal patterns (no LLM — deterministic pre-filter)
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
    r"\bgiven\b.{0,120}\bwhen\b.{0,120}\bthen\b",
    r"\bcapabilit(?:y|ies)\b",
    r"\bacceptance criteri(?:a|on)\b",
    r"\bthe system\b.{0,60}\b(?:shall|should|must|will|needs?)\b",
    r"\bfeature request\b",
    r"\bfunctional requirement\b",
]

_REQ_RE = re.compile("|".join(_REQ_PATTERNS), re.IGNORECASE | re.DOTALL)


def _is_requirement_bearing(text: str) -> bool:
    return bool(_REQ_RE.search(text))


# ---------------------------------------------------------------------------
# Chat→intake session mapping
# ---------------------------------------------------------------------------

def _ensure_mapping_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_intake_sessions (
            context_id  TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )"""
    )
    conn.commit()


def _get_or_create_session(context_id: str, conn: sqlite3.Connection) -> str:
    """Return the intake session_id linked to this chat context, creating one if needed."""
    _ensure_mapping_table(conn)

    row = conn.execute(
        "SELECT session_id FROM chat_intake_sessions WHERE context_id = ?",
        (context_id,),
    ).fetchone()
    if row:
        return row[0]

    # Create a new intake session
    try:
        import sys
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from tools.requirements.intake_engine import create_session

        result = create_session(
            project_id="chat-auto",
            customer_name="Chat User",
            customer_org="ICDEV™",
            impact_level="IL5",
            created_by=f"chat:{context_id}",
            role="developer",
            goal="build",
        )
        session_id: str = result.get("session_id") or result.get("id") or ""
        if not session_id:
            raise ValueError(f"create_session returned no ID: {result}")
    except Exception as exc:
        logger.warning("Could not create intake session for context %s: %s", context_id, exc)
        raise

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO chat_intake_sessions (context_id, session_id, created_at) VALUES (?,?,?)",
        (context_id, session_id, now),
    )
    conn.commit()
    logger.info("Created intake session %s for chat context %s", session_id, context_id)
    return session_id


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------

def _req_count(session_id: str, conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM intake_requirements WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def process_message_for_intake(
    context_id: str,
    user_message: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Analyze user_message; if requirements found, decompose and promote to Kanban.

    Returns:
        {
          "skipped": True   # message had no requirement signals
        }
        or
        {
          "session_id": str,
          "requirements_found": int,
          "tasks_added": int,
          "error": str   # only on failure
        }
    """
    if not _is_requirement_bearing(user_message):
        return {"skipped": True}

    import sys
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    resolved_db = db_path or DB_PATH

    try:
        conn = sqlite3.connect(str(resolved_db))
        conn.row_factory = sqlite3.Row
        session_id = _get_or_create_session(context_id, conn)
        reqs_before = _req_count(session_id, conn)
        conn.close()
    except Exception as exc:
        logger.error("Intake hook — session setup failed for context %s: %s", context_id, exc)
        return {"error": str(exc), "tasks_added": 0}

    # Process the turn through the intake engine
    try:
        from tools.requirements.intake_engine import process_turn
        process_turn(session_id, user_message, db_path=resolved_db)
    except Exception as exc:
        logger.error("Intake hook — process_turn failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "error": str(exc), "tasks_added": 0}

    # Count newly extracted requirements
    conn = sqlite3.connect(str(resolved_db))
    conn.row_factory = sqlite3.Row
    reqs_after = _req_count(session_id, conn)
    conn.close()

    new_reqs = reqs_after - reqs_before
    if new_reqs <= 0:
        return {"session_id": session_id, "requirements_found": 0, "tasks_added": 0}

    # Decompose new requirements into SAFe hierarchy
    try:
        from tools.requirements.decomposition_engine import decompose_requirements
        decompose_requirements(
            session_id=session_id,
            target_level="story",
            generate_bdd=True,
            estimate=True,
            db_path=resolved_db,
        )
    except Exception as exc:
        logger.error("Intake hook — decomposition failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "requirements_found": new_reqs, "error": str(exc), "tasks_added": 0}

    # Promote to Kanban (idempotent)
    try:
        from tools.requirements.intake_kanban_promoter import promote
        result = promote(session_id=session_id)
        tasks_added = result.get("inserted", 0)
    except Exception as exc:
        logger.error("Intake hook — promote failed for session %s: %s", session_id, exc)
        return {"session_id": session_id, "requirements_found": new_reqs, "error": str(exc), "tasks_added": 0}

    logger.info(
        "Intake hook: context=%s session=%s reqs=%d tasks_added=%d",
        context_id, session_id, new_reqs, tasks_added,
    )
    return {
        "session_id": session_id,
        "requirements_found": new_reqs,
        "tasks_added": tasks_added,
    }
