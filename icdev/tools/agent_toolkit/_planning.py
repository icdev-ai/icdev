# CUI // SP-CTI
"""Planning primitives for the agent toolkit (OPT-67).

write_todos / update_todo give agents a deterministic way to persist
their plan into the kanban_tasks table. Unlike the TaskCreate/TaskUpdate
tools provided by Claude Code harness, these work from any Python code
path (cron jobs, daemons, headless CI) — LLM-agnostic by construction.

Tasks go into status='suggested' by default, matching the Innovation
Engine → kanban promoter pattern in OPT-60. A human reviewer promotes
them to 'backlog' when they're ready to be picked up.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, List, Optional

VALID_TASK_TYPES = ("build", "run", "fix", "research", "deploy", "test", "chore")
VALID_PRIORITIES = ("low", "medium", "high", "critical")
VALID_STATUSES = (
    "backlog",
    "scheduled",
    "in_progress",
    "done",
    "token_exhausted",
    "suggested",
)


def _gen_id() -> str:
    return "task-" + secrets.token_hex(5)


def _get_conn():
    """Import-guarded DB connection helper.

    Agent toolkit is importable without the dashboard layer; the DB
    dependency is only required when write_todos is actually invoked.
    """
    from tools.db.storage import get_connection
    from tools.dashboard.config import DB_PATH
    return get_connection(db_path=str(DB_PATH))


def write_todos(
    tasks: List[dict],
    default_priority: str = "medium",
    default_task_type: str = "research",
    default_status: str = "suggested",
    source_prediction_id: Optional[str] = None,
    actor: str = "agent_toolkit",
) -> dict:
    """Persist a list of task dicts to kanban_tasks.

    Each task dict may have: title (required), description, priority,
    task_type, status, source_prediction_id. Missing fields use the
    default_* parameters.

    Args:
        tasks: List of task dicts. Must have 'title' at minimum.
        default_priority: 'low'|'medium'|'high'|'critical'.
        default_task_type: 'build'|'run'|'fix'|'research'|'deploy'|'test'|'chore'.
        default_status: Usually 'suggested' so a human can review before
            promoting to 'backlog'.
        source_prediction_id: Optional provenance link (e.g., an
            innovation_signals row id) applied to all tasks if not
            set individually.
        actor: Identity recorded in the audit row.

    Returns:
        Dict with: inserted (int), task_ids (list), summary (str).

    Raises:
        ValueError: If any task is missing 'title' or has invalid
            priority/task_type/status.
    """
    if not tasks:
        return {"inserted": 0, "task_ids": [], "summary": "no tasks provided"}

    # Validate up-front so we don't half-commit
    for i, t in enumerate(tasks):
        if not t.get("title"):
            raise ValueError(f"write_todos: task[{i}] missing 'title'")
        pr = t.get("priority", default_priority)
        if pr not in VALID_PRIORITIES:
            raise ValueError(
                f"write_todos: task[{i}] invalid priority {pr!r}; "
                f"expected one of {VALID_PRIORITIES}"
            )
        tt = t.get("task_type", default_task_type)
        if tt not in VALID_TASK_TYPES:
            raise ValueError(
                f"write_todos: task[{i}] invalid task_type {tt!r}; "
                f"expected one of {VALID_TASK_TYPES}"
            )
        st = t.get("status", default_status)
        if st not in VALID_STATUSES:
            raise ValueError(
                f"write_todos: task[{i}] invalid status {st!r}; "
                f"expected one of {VALID_STATUSES}"
            )

    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    ids: List[str] = []
    try:
        for t in tasks:
            tid = t.get("id") or _gen_id()
            ids.append(tid)
            sid = t.get("source_prediction_id") or source_prediction_id
            conn.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "source_prediction_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    tid,
                    t["title"],
                    t.get("description", ""),
                    t.get("task_type", default_task_type),
                    t.get("priority", default_priority),
                    t.get("status", default_status),
                    sid,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Best-effort audit
    try:
        import json as _json
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="decision_made",
            actor=actor,
            action="agent_toolkit.write_todos",
            details=_json.dumps({
                "count": len(ids),
                "task_ids": ids,
                "default_status": default_status,
                "source_prediction_id": source_prediction_id,
            }),
            project_id="agent_toolkit",
        )
    except Exception:
        pass

    return {
        "inserted": len(ids),
        "task_ids": ids,
        "summary": f"wrote {len(ids)} todo(s) to kanban_tasks (status={default_status})",
    }


def update_todo(
    task_id: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    description: Optional[str] = None,
    actor: str = "agent_toolkit",
) -> dict:
    """Update an existing kanban_tasks row.

    Args:
        task_id: Existing task id.
        status: New status (if provided, must be a VALID_STATUSES value).
        priority: New priority.
        description: Replacement description.
        actor: Identity recorded in the audit row.

    Returns:
        Dict with: task_id, updated_fields, found (bool).

    Raises:
        ValueError: On invalid status/priority.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"update_todo: invalid status {status!r}; expected {VALID_STATUSES}"
        )
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(
            f"update_todo: invalid priority {priority!r}; expected {VALID_PRIORITIES}"
        )
    if status is None and priority is None and description is None:
        return {"task_id": task_id, "updated_fields": [], "found": False}

    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    updated_fields: List[str] = []
    try:
        row = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not row:
            return {"task_id": task_id, "updated_fields": [], "found": False}

        sets: List[str] = []
        params: List[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
            updated_fields.append("status")
            if status == "done":
                sets.append("completed_at = ?")
                params.append(now)
                updated_fields.append("completed_at")
        if priority is not None:
            sets.append("priority = ?")
            params.append(priority)
            updated_fields.append("priority")
        if description is not None:
            sets.append("description = ?")
            params.append(description)
            updated_fields.append("description")
        sets.append("updated_at = ?")
        params.append(now)
        params.append(task_id)

        conn.execute(
            f"UPDATE kanban_tasks SET {', '.join(sets)} WHERE id = ?",  # nosec B608 — sets list built from hardcoded column names
            tuple(params),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        import json as _json
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="decision_made",
            actor=actor,
            action="agent_toolkit.update_todo",
            details=_json.dumps({
                "task_id": task_id,
                "updated_fields": updated_fields,
                "new_status": status,
            }),
            project_id="agent_toolkit",
        )
    except Exception:
        pass

    return {
        "task_id": task_id,
        "updated_fields": updated_fields,
        "found": True,
    }
