#!/usr/bin/env python3
# CUI // SP-CTI
"""Kanban MCP server — thin wrappers around tools/kanban/ for MCP tool dispatch."""

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def _kanban_cli(*args, **kwargs):
    from tools.kanban.cli import main as kanban_main
    return kanban_main


def handle_kanban_list_tasks(params: dict) -> dict:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        cur = conn.cursor()
        status = params.get("status")
        if status:
            cur.execute("SELECT id, title, status, priority FROM kanban_tasks WHERE status = %s ORDER BY created_at DESC LIMIT %s", (status, params.get("limit", 50)))
        else:
            cur.execute("SELECT id, title, status, priority FROM kanban_tasks ORDER BY created_at DESC LIMIT %s", (params.get("limit", 50),))
        rows = cur.fetchall()
        conn.close()
        return {"tasks": [{"id": r[0], "title": r[1], "status": r[2], "priority": r[3]} for r in rows], "count": len(rows)}
    except Exception as exc:
        return {"error": str(exc), "tasks": [], "count": 0}


def handle_kanban_get_task(params: dict) -> dict:
    try:
        task_id = params.get("task_id") or params.get("id")
        if not task_id:
            return {"error": "task_id is required"}
        from tools.db.storage import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, title, description, status, priority, task_type FROM kanban_tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return {"error": f"Task {task_id} not found"}
        return {"id": row[0], "title": row[1], "description": row[2], "status": row[3], "priority": row[4], "task_type": row[5]}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kanban_create_task(params: dict) -> dict:
    try:
        from tools.kanban.task_factory import create_tasks
        tasks = create_tasks([{
            "title": params.get("title", ""),
            "description": params.get("description", ""),
            "task_type": params.get("task_type", "chore"),
            "priority": params.get("priority", "medium"),
        }])
        return {"created": tasks, "count": len(tasks)}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kanban_update_task(params: dict) -> dict:
    try:
        task_id = params.get("task_id") or params.get("id")
        if not task_id:
            return {"error": "task_id is required"}
        from tools.db.storage import get_connection
        conn = get_connection()
        cur = conn.cursor()
        updates = {k: v for k, v in params.items() if k not in ("task_id", "id")}
        if not updates:
            return {"error": "No fields to update"}
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        cur.execute(f"UPDATE kanban_tasks SET {set_clause} WHERE id = %s", (*updates.values(), task_id))
        conn.commit()
        conn.close()
        return {"updated": task_id, "fields": list(updates.keys())}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kanban_move_task(params: dict) -> dict:
    try:
        task_id = params.get("task_id") or params.get("id")
        status = params.get("status")
        if not task_id or not status:
            return {"error": "task_id and status are required"}
        from tools.db.storage import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE kanban_tasks SET status = %s WHERE id = %s", (status, task_id))
        conn.commit()
        conn.close()
        return {"moved": task_id, "status": status}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kanban_delete_task(params: dict) -> dict:
    try:
        task_id = params.get("task_id") or params.get("id")
        if not task_id:
            return {"error": "task_id is required"}
        from tools.db.storage import get_connection
        from tools.kanban.gates import is_manual_gate
        conn = get_connection()
        cur = conn.cursor()
        # Mirror the dashboard's canonical delete (tools/dashboard/api/kanban.py::
        # delete_task): a real hard DELETE. The previous UPDATE ... SET
        # status = 'archived' wrote a status the kanban_tasks CHECK constraint
        # forbids (valid: backlog, scheduled, in_progress, done, token_exhausted,
        # suggested, decomposed, validating, needs_decomposition, pr_opened,
        # ci_failed, merge_conflict, changes_requested, failed), so on PostgreSQL
        # it raised CheckViolation and no delete ever occurred.
        cur.execute("SELECT id, title FROM kanban_tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return {"error": f"Task {task_id} not found"}
        title = row[1]
        # A manual-mode gate is a sentinel: deleting it would permanently strand
        # its dependents (a task whose parent row is missing never satisfies its
        # dependency and can never be promoted again). Refuse, as the dashboard does.
        if is_manual_gate(task_id, title):
            conn.close()
            return {"error": "Manual-mode gate cannot be deleted from the board"}
        cur.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
        conn.commit()
        conn.close()
        return {"deleted": task_id}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kanban_board_summary(params: dict) -> dict:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM kanban_tasks GROUP BY status")
        rows = cur.fetchall()
        conn.close()
        return {"lanes": {r[0]: r[1] for r in rows}, "total": sum(r[1] for r in rows)}
    except Exception as exc:
        return {"error": str(exc), "lanes": {}, "total": 0}


def handle_kanban_queue_plan(params: dict) -> dict:
    try:
        tasks = params.get("tasks", [])
        if not tasks:
            return {"error": "tasks list is required"}
        from tools.kanban.task_factory import create_tasks
        created = create_tasks(tasks)
        return {"queued": created, "count": len(created)}
    except Exception as exc:
        return {"error": str(exc)}
