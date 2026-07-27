# CUI // SP-CTI
"""Session persistence for :func:`icdev.tools.llm.agent_loop.run_agent_loop`.

Saves an :class:`AgentLoopResult`'s message history and metadata to the ACE
canvas DB so long-running agent tasks can survive process restarts and budget
hits.  Callers receive a ``session_id`` UUID in the result and can pass
``resume_session_id=<uuid>`` to a subsequent :func:`run_agent_loop` call to
restore the full conversation history.

Table: ``agent_loop_sessions`` (migration 220). Canvas-scoped — uses
:func:`icdev.tools.db.storage.get_canvas_connection`.
"""
from __future__ import annotations

import json
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.agent_loop_session")

_DB_ENV = "ACE_DB_PATH"

# Module-level reference so tests can monkeypatch without fighting lazy imports.
try:
    from icdev.tools.db.storage import get_canvas_connection
except Exception:  # noqa: BLE001 — DB may not be available in test environments
    get_canvas_connection = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_session(
    result: Any,
    *,
    instance_id: str = "",
    coworker_id: str = "",
    llm_function: str = "",
    system_prompt: str = "",
) -> bool:
    """Persist an :class:`AgentLoopResult` to the ``agent_loop_sessions`` table.

    Args:
        result:        The completed (or truncated) :class:`AgentLoopResult`.
        instance_id:   ACE instance ID (optional metadata).
        coworker_id:   ACE co-worker ID (optional metadata).
        llm_function:  Router function key used by the loop.
        system_prompt: System prompt used by the loop (stored for context).

    Returns:
        True on success, False on any DB error (best-effort, never raises).
    """
    session_id = getattr(result, "session_id", "") or ""
    if not session_id:
        logger.warning("agent_loop_session.save: result has no session_id — skipping")
        return False
    try:
        messages_json = json.dumps(
            getattr(result, "messages", []) or [], ensure_ascii=False
        )
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                """
                INSERT INTO agent_loop_sessions
                    (session_id, instance_id, coworker_id, llm_function,
                     result_subtype, turns, total_input_tokens, total_output_tokens,
                     total_cost_usd, messages_json, system_prompt, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT (session_id) DO UPDATE SET
                    result_subtype    = excluded.result_subtype,
                    turns             = excluded.turns,
                    total_input_tokens  = excluded.total_input_tokens,
                    total_output_tokens = excluded.total_output_tokens,
                    total_cost_usd    = excluded.total_cost_usd,
                    messages_json     = excluded.messages_json,
                    system_prompt     = excluded.system_prompt,
                    updated_at        = datetime('now')
                """,
                (
                    session_id,
                    instance_id,
                    coworker_id,
                    llm_function,
                    getattr(result, "result_subtype", "") or "",
                    getattr(result, "turns", 0) or 0,
                    getattr(result, "total_input_tokens", 0) or 0,
                    getattr(result, "total_output_tokens", 0) or 0,
                    getattr(result, "total_cost_usd", 0.0) or 0.0,
                    messages_json,
                    system_prompt,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        logger.debug("agent_loop_session.save: saved session %s (%d msgs)", session_id, len(result.messages))
        return True
    except Exception as exc:
        logger.warning("agent_loop_session.save: failed for session %s: %s", session_id, exc)
        return False


def load_session(session_id: str) -> list[dict[str, Any]]:
    """Load a prior session's message history from the ``agent_loop_sessions`` table.

    Args:
        session_id: UUID returned in a prior :class:`AgentLoopResult`.

    Returns:
        The saved ``messages`` list, or an empty list if the session is not found
        or the DB is unavailable (never raises).
    """
    if not session_id:
        return []
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            cur = conn.execute(
                "SELECT messages_json FROM agent_loop_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            logger.warning("agent_loop_session.load: session %s not found", session_id)
            return []
        messages = json.loads(row[0])
        logger.debug("agent_loop_session.load: loaded session %s (%d msgs)", session_id, len(messages))
        return messages
    except Exception as exc:
        logger.warning("agent_loop_session.load: failed for session %s: %s", session_id, exc)
        return []


def get_session_metadata(session_id: str) -> dict[str, Any] | None:
    """Return stored metadata for a session without loading full message history.

    Returns a dict with ``result_subtype``, ``turns``, ``total_cost_usd``, etc.,
    or ``None`` if the session is not found.
    """
    if not session_id:
        return None
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            cur = conn.execute(
                """
                SELECT session_id, instance_id, coworker_id, llm_function,
                       result_subtype, turns, total_input_tokens, total_output_tokens,
                       total_cost_usd, created_at, updated_at
                FROM agent_loop_sessions WHERE session_id = ?
                """,
                (session_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        keys = [
            "session_id", "instance_id", "coworker_id", "llm_function",
            "result_subtype", "turns", "total_input_tokens", "total_output_tokens",
            "total_cost_usd", "created_at", "updated_at",
        ]
        return dict(zip(keys, row))
    except Exception as exc:
        logger.warning("agent_loop_session.get_metadata: failed for %s: %s", session_id, exc)
        return None


def list_sessions(
    *,
    limit: int = 50,
    offset: int = 0,
    result_subtype: str = "",
    coworker_id: str = "",
    instance_id: str = "",
) -> dict:
    """Return a paginated list of sessions with summary stats.

    Returns a dict with keys: ``sessions`` (list[dict]), ``total`` (int for
    current filter), ``total_all`` (int across all filters), ``success_count``,
    ``error_count``, ``avg_turns`` (float), ``total_cost`` (float).
    Never raises.
    """
    empty: dict = {
        "sessions": [], "total": 0, "total_all": 0,
        "success_count": 0, "error_count": 0, "avg_turns": None, "total_cost": None,
    }
    if get_canvas_connection is None:
        return empty
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            # Build WHERE clause.
            conditions: list[str] = []
            params: list = []
            if result_subtype:
                conditions.append("result_subtype = ?")
                params.append(result_subtype)
            if coworker_id:
                conditions.append("coworker_id = ?")
                params.append(coworker_id)
            if instance_id:
                conditions.append("instance_id = ?")
                params.append(instance_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            cur = conn.execute(
                f"SELECT COUNT(*) FROM agent_loop_sessions {where}", params
            )
            total = (cur.fetchone() or [0])[0]

            cur = conn.execute(
                "SELECT COUNT(*) FROM agent_loop_sessions"
            )
            total_all = (cur.fetchone() or [0])[0]

            cur = conn.execute(
                "SELECT COUNT(*) FROM agent_loop_sessions WHERE result_subtype = 'success'"
            )
            success_count = (cur.fetchone() or [0])[0]

            cur = conn.execute(
                "SELECT COUNT(*) FROM agent_loop_sessions WHERE result_subtype != 'success' AND result_subtype != ''"
            )
            error_count = (cur.fetchone() or [0])[0]

            cur = conn.execute(
                "SELECT AVG(CAST(turns AS REAL)), SUM(total_cost_usd) FROM agent_loop_sessions"
            )
            agg_row = cur.fetchone() or (None, None)
            avg_turns = agg_row[0]
            total_cost = agg_row[1]

            cols = [
                "session_id", "instance_id", "coworker_id", "llm_function",
                "result_subtype", "turns", "total_input_tokens", "total_output_tokens",
                "total_cost_usd", "created_at", "updated_at",
            ]
            cur = conn.execute(
                f"SELECT {', '.join(cols)} FROM agent_loop_sessions {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            sessions = [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
        return {
            "sessions": sessions,
            "total": total,
            "total_all": total_all,
            "success_count": success_count,
            "error_count": error_count,
            "avg_turns": avg_turns,
            "total_cost": total_cost,
        }
    except Exception as exc:
        logger.warning("agent_loop_session.list_sessions: failed: %s", exc)
        return empty
