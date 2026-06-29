# CUI // SP-CTI
"""NOVA ECHO — Execution Trace Logger.

Records structured execution traces for every kanban task dispatch so the
Reflexion Agent can read them post-completion and generate improvement
artifacts.  Inspired by hexo-ai/sia's per-generation execution tracing.

Schema: agent_execution_traces (append-only, NIST AU compliant).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ensure_tables(conn) -> None:
    """Lazy-init NOVA tables on first use (delegates to canonical init_db)."""
    try:
        from tools.nova.db.init_db import init_nova_tables
        init_nova_tables(conn)
    except Exception as exc:
        logger.debug("[trace] lazy init failed (tables may already exist): %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class TraceEvent:
    """Single recorded event within a task execution."""
    event_type: str           # tool_call | llm_decision | status_change | error | milestone
    payload: dict[str, Any]   # event-specific data
    recorded_at: str = field(default_factory=_utcnow)


@dataclass
class ExecutionTrace:
    """Full trace for one kanban task dispatch."""
    trace_id: str
    task_id: str
    task_type: str
    skill_used: str
    outcome: str              # success | failure | timeout | token_exhaustion | partial
    events: list[TraceEvent] = field(default_factory=list)
    started_at: str = field(default_factory=_utcnow)
    completed_at: str = ""
    lesson_pattern: str = ""  # from LessonPattern taxonomy
    improvement_notes: str = ""


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def start_trace(task_id: str, task_type: str = "", skill_used: str = "") -> str:
    """Open a new execution trace; returns trace_id for subsequent log_event calls."""
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    try:
        conn = _conn()
        _ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO agent_execution_traces
                (trace_id, task_id, task_type, skill_used, outcome, events_json, started_at)
            VALUES (%s, %s, %s, %s, 'in_progress', '[]', %s)
            """,
            (trace_id, task_id, task_type, skill_used, _utcnow()),
        )
        conn.commit()
        logger.debug("[trace] started %s for task %s", trace_id, task_id)
    except Exception as exc:
        logger.warning("[trace] start_trace failed: %s", exc)
    return trace_id


def log_event(trace_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Append a single event to an open trace."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT events_json FROM agent_execution_traces WHERE trace_id = %s",
            (trace_id,),
        ).fetchone()
        if not row:
            return
        events = json.loads(row[0] if isinstance(row, (list, tuple)) else row["events_json"])
        events.append(asdict(TraceEvent(event_type=event_type, payload=payload)))
        conn.execute(
            "UPDATE agent_execution_traces SET events_json = %s WHERE trace_id = %s",
            (json.dumps(events), trace_id),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("[trace] log_event failed: %s", exc)


def close_trace(
    trace_id: str,
    outcome: str,
    lesson_pattern: str = "",
    improvement_notes: str = "",
) -> None:
    """Mark a trace as complete with final outcome."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        conn.execute(
            """
            UPDATE agent_execution_traces
               SET outcome = %s, lesson_pattern = %s, improvement_notes = %s, completed_at = %s
             WHERE trace_id = %s
            """,
            (outcome, lesson_pattern, improvement_notes, _utcnow(), trace_id),
        )
        conn.commit()
        logger.debug("[trace] closed %s outcome=%s", trace_id, outcome)
    except Exception as exc:
        logger.warning("[trace] close_trace failed: %s", exc)


def get_traces_for_task_type(task_type: str, limit: int = 50) -> list[dict]:
    """Fetch recent completed traces for a given task_type (for eval dataset building)."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT trace_id, task_id, task_type, skill_used, outcome,
                   lesson_pattern, improvement_notes, started_at, completed_at
              FROM agent_execution_traces
             WHERE task_type = %s AND outcome != 'in_progress'
             ORDER BY completed_at DESC
             LIMIT %s
            """,
            (task_type, limit),
        ).fetchall()
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(dict(row))
            else:
                cols = ["trace_id", "task_id", "task_type", "skill_used", "outcome",
                        "lesson_pattern", "improvement_notes", "started_at", "completed_at"]
                result.append(dict(zip(cols, row)))
        return result
    except Exception as exc:
        logger.warning("[trace] get_traces_for_task_type failed: %s", exc)
        return []


def get_recent_traces(limit: int = 100, outcome_filter: str | None = None) -> list[dict]:
    """Fetch recent traces, optionally filtered by outcome."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        if outcome_filter:
            rows = conn.execute(
                """
                SELECT trace_id, task_id, task_type, skill_used, outcome,
                       lesson_pattern, improvement_notes, started_at, completed_at
                  FROM agent_execution_traces
                 WHERE outcome = %s
                 ORDER BY completed_at DESC LIMIT %s
                """,
                (outcome_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT trace_id, task_id, task_type, skill_used, outcome,
                       lesson_pattern, improvement_notes, started_at, completed_at
                  FROM agent_execution_traces
                 WHERE outcome != 'in_progress'
                 ORDER BY completed_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        cols = ["trace_id", "task_id", "task_type", "skill_used", "outcome",
                "lesson_pattern", "improvement_notes", "started_at", "completed_at"]
        return [
            dict(zip(cols, row)) if not isinstance(row, dict) else dict(row)
            for row in rows
        ]
    except Exception as exc:
        logger.warning("[trace] get_recent_traces failed: %s", exc)
        return []
