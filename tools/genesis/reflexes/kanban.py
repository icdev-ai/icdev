# CUI // SP-CTI
"""Kanban Executor Reflex — polls kanban_tasks for due scheduled tasks
and prepares them for execution.

Flow:
1. Query kanban_tasks WHERE status='scheduled' AND scheduled_at <= NOW()
2. Move each due task to 'in_progress'
3. Write a prompt file to .tmp/kanban/ for Claude Code to pick up
4. Send a dashboard notification
5. Return metrics on tasks processed

The prompt files are consumed by Claude Code sessions (via /start
or manual pickup). Each file contains the full task context needed
for autonomous execution.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

PROMPT_DIR = BASE_DIR / ".tmp" / "kanban"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_prompt_dir():
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)


def _get_due_tasks() -> list:
    """Find tasks ready for execution:
    1. Scheduled tasks whose scheduled_at has passed
    2. Backlog tasks (auto-promote to in_progress)

    Priority order: critical > high > medium > low,
    then oldest first.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM kanban_tasks "
            "WHERE (status = 'scheduled' "
            "  AND scheduled_at IS NOT NULL "
            "  AND scheduled_at <= NOW()) "
            "OR status = 'backlog' "
            "ORDER BY "
            "CASE priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _move_task(task_id: str, new_status: str):
    """Update task status in the database."""
    conn = get_connection()
    try:
        now = _utcnow_iso()
        sql = (
            "UPDATE kanban_tasks SET status = %s, "
            "updated_at = %s"
        )
        vals = [new_status, now]
        if new_status == "done":
            sql += ", completed_at = %s"
            vals.append(now)
        sql += " WHERE id = %s"
        vals.append(task_id)
        conn.execute(sql, tuple(vals))
        conn.commit()
    finally:
        conn.close()


def _write_prompt_file(task: dict):
    """Write a prompt file for Claude Code to pick up."""
    _ensure_prompt_dir()
    task_id = task["id"]
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    task_type = task.get("task_type", "chore")
    priority = task.get("priority", "medium")

    prompt = f"""# Kanban Task: {title}
- **ID:** {task_id}
- **Type:** {task_type}
- **Priority:** {priority}
- **Scheduled:** {task.get('scheduled_at', 'now')}

## Description
{desc}

## Instructions
Execute this task as described above. When complete:
1. POST to http://localhost:5050/api/kanban/tasks/{task_id}/move
   with {{"status": "done"}} to mark it complete on the board.
"""

    prompt_path = PROMPT_DIR / f"{task_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    return str(prompt_path)


def _send_notification(task: dict):
    """Send notification via dashboard DB + Telegram."""
    title = f"Task due: {task['title']}"
    body = (
        f"Kanban task '{task['title']}' "
        f"({task['task_type']}/{task['priority']}) "
        f"is now in progress.\n"
        f"Prompt file ready for next Claude session."
    )

    # Dashboard notification
    try:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO notifications "
                "(id, title, message, severity, source, "
                "created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    f"notif-kanban-{task['id']}",
                    title,
                    body,
                    "info",
                    "genesis.kanban",
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    # Telegram notification
    try:
        from tools.notifications.adapters.telegram import send
        send(title, body, severity="info")
    except Exception:
        pass  # Telegram not configured or unavailable


def _poll_telegram():
    """Poll Telegram for incoming task commands."""
    try:
        from tools.notifications.adapters.telegram_listener import (
            poll_updates,
        )
        return poll_updates()
    except Exception:
        return []


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Kanban Executor Reflex."""
    # Poll Telegram for new commands first
    tg_results = _poll_telegram()
    if tg_results:
        print(f"  Kanban: {len(tg_results)} Telegram commands")

    due_tasks = _get_due_tasks()

    if not due_tasks:
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "no_due_tasks"},
        }

    processed = []
    errors = 0

    for task in due_tasks:
        try:
            # Move to in_progress
            _move_task(task["id"], "in_progress")

            # Write prompt file for Claude Code
            prompt_path = _write_prompt_file(task)

            # Send notification
            _send_notification(task)

            processed.append({
                "id": task["id"],
                "title": task["title"],
                "prompt_file": prompt_path,
            })
            print(
                f"  Kanban: {task['id']} "
                f"'{task['title']}' -> in_progress"
            )
        except Exception as e:
            errors += 1
            print(f"  Kanban error: {task['id']}: {e}")

    return {
        "success": errors == 0,
        "metric_value": len(processed),
        "details": {
            "tasks_activated": len(processed),
            "telegram_commands": len(tg_results),
            "errors": errors,
            "tasks": processed,
        },
    }
