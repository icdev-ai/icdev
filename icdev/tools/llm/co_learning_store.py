# CUI // SP-CTI
"""
Co-learning persistence layer for the agent loop.

After each session, persist rule-based improvement suggestions keyed by role.
On next session start, read the top suggestions and format them as a system
prompt patch that the coworker thread can prepend.

Table: ace_colearning_suggestions
  id SERIAL PK
  role TEXT NOT NULL
  category TEXT NOT NULL        -- maps to suggest_improvements() "field" key
                                --   e.g. 'system_prompt', 'max_iterations',
                                --        'folder_access', 'reasoning_style'
  suggestion TEXT NOT NULL      -- human-readable improvement text
  session_id TEXT NOT NULL      -- session that triggered this
  severity TEXT NOT NULL        -- 'high' | 'medium' | 'low'
  applied_count INTEGER DEFAULT 0
  dismissed_count INTEGER DEFAULT 0
  created_at TEXT NOT NULL
  last_seen_at TEXT NOT NULL

Usage:
  from icdev.tools.llm.co_learning_store import (
      record_session_suggestions,
      build_system_prompt_patch,
      auto_record_from_loop_result,
  )
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TABLE = "ace_colearning_suggestions"


def _conn():
    from icdev.tools.db.storage import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def record_session_suggestions(
    session_id: str,
    role: str,
    suggestions: list[dict[str, Any]],
) -> int:
    """
    Persist suggestions from a completed session.

    Parameters
    ----------
    session_id : str
        The agent loop session that generated these suggestions.
    role : str
        Coworker role (e.g. 'ai_developer', 'qa_manager').
    suggestions : list[dict]
        Output of suggest_improvements(eval_result). Each dict has keys:
        'suggestion' (str), 'field' (str), 'severity' (str), 'issue' (str).

    Returns
    -------
    int : Number of suggestions persisted.
    """
    if not suggestions:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    try:
        conn = _conn()
        for s in suggestions:
            # suggest_improvements() uses 'field' for category, 'suggestion' for text
            category = s.get("field") or s.get("category", "general")
            text = s.get("suggestion", "")
            severity = s.get("severity", "medium")
            if not text:
                continue
            with conn:
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}
                      (role, category, suggestion, session_id, severity,
                       created_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (role, category, suggestion) DO UPDATE SET
                      last_seen_at = EXCLUDED.last_seen_at,
                      severity     = EXCLUDED.severity
                    """,
                    (role, category, text, session_id, severity, now, now),
                )
            count += 1
        logger.info(
            "co_learning_store: persisted %d suggestions for role=%s session=%s",
            count, role, session_id,
        )
    except Exception:
        logger.warning(
            "co_learning_store: failed to persist suggestions (non-fatal)",
            exc_info=True,
        )
    return count


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def build_system_prompt_patch(
    role: str,
    *,
    max_suggestions: int = 5,
    categories: list[str] | None = None,
) -> str:
    """
    Build a system prompt addendum from stored suggestions for a role.

    Returns an empty string if no suggestions exist.
    Only 'system_prompt' and 'reasoning_style' categories are included by
    default — these are safe to embed in a system prompt. 'max_iterations'
    and 'folder_access' are handled via separate config paths.

    Parameters
    ----------
    role : str
        Coworker role to look up.
    max_suggestions : int
        Maximum number of suggestions to include.
    categories : list[str] | None
        Filter to specific categories. Defaults to
        ['system_prompt', 'reasoning_style'].
    """
    if categories is None:
        categories = ["system_prompt", "reasoning_style"]

    placeholders = ", ".join(["%s"] * len(categories))
    try:
        conn = _conn()
        cur = conn.execute(
            f"""
            SELECT suggestion, category, applied_count
            FROM {_TABLE}
            WHERE role = %s
              AND category IN ({placeholders})
              AND dismissed_count < 3
            ORDER BY applied_count DESC, last_seen_at DESC
            LIMIT %s
            """,
            (role, *categories, max_suggestions),
        )
        rows = cur.fetchall()
        if not rows:
            return ""

        lines = [f"[Co-Learning Note — recurring improvement for {role} role]"]
        for suggestion, _category, _count in rows:
            lines.append(f"- {suggestion}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        logger.debug(
            "co_learning_store: build_system_prompt_patch failed (non-fatal)",
            exc_info=True,
        )
        return ""


# ---------------------------------------------------------------------------
# Feedback signals
# ---------------------------------------------------------------------------

def mark_applied(role: str, category: str) -> None:
    """Increment applied_count for all suggestions of this role+category."""
    try:
        conn = _conn()
        with conn:
            conn.execute(
                f"UPDATE {_TABLE} SET applied_count = applied_count + 1 "
                f"WHERE role = %s AND category = %s",
                (role, category),
            )
    except Exception:
        logger.debug(
            "co_learning_store: mark_applied failed (non-fatal)", exc_info=True
        )


def dismiss_suggestion(role: str, suggestion_text: str) -> None:
    """Increment dismissed_count (hides from patches when dismissed_count >= 3)."""
    try:
        conn = _conn()
        with conn:
            conn.execute(
                f"UPDATE {_TABLE} SET dismissed_count = dismissed_count + 1 "
                f"WHERE role = %s AND suggestion = %s",
                (role, suggestion_text),
            )
    except Exception:
        logger.debug(
            "co_learning_store: dismiss_suggestion failed (non-fatal)", exc_info=True
        )


# ---------------------------------------------------------------------------
# Query path (dashboard / API)
# ---------------------------------------------------------------------------

def get_suggestions_for_role(role: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return stored suggestions for a role (for dashboard/API display)."""
    try:
        conn = _conn()
        cur = conn.execute(
            f"""
            SELECT id, role, category, suggestion, session_id, severity,
                   applied_count, dismissed_count, created_at, last_seen_at
            FROM {_TABLE}
            WHERE role = %s
            ORDER BY last_seen_at DESC
            LIMIT %s
            """,
            (role, limit),
        )
        rows = cur.fetchall()
        keys = [
            "id", "role", "category", "suggestion", "session_id", "severity",
            "applied_count", "dismissed_count", "created_at", "last_seen_at",
        ]
        return [dict(zip(keys, row)) for row in rows]
    except Exception:
        logger.warning(
            "co_learning_store: get_suggestions_for_role failed (non-fatal)",
            exc_info=True,
        )
        return []


# ---------------------------------------------------------------------------
# Convenience: score + suggest + persist in one call
# ---------------------------------------------------------------------------

def auto_record_from_loop_result(
    session_id: str,
    role: str,
    loop_result: Any,
) -> int:
    """
    Run eval scoring + suggestion extraction + persist in one call.

    Designed to be called from CoWorkerThread's on_stop callback once
    coworker_thread.py is wired. Can also be called directly from test harnesses.

    Parameters
    ----------
    session_id : str
    role : str
    loop_result : AgentLoopResult
        The result from run_agent_loop().

    Returns
    -------
    int : Number of suggestions persisted (0 on any error).
    """
    try:
        from icdev.tools.ace.evaluator import score_session, suggest_improvements
        eval_result = score_session(session_id)
        if eval_result is None:
            return 0
        suggestions = suggest_improvements(eval_result)
        return record_session_suggestions(session_id, role, suggestions)
    except Exception:
        logger.warning(
            "co_learning_store: auto_record_from_loop_result failed (non-fatal)",
            exc_info=True,
        )
        return 0
