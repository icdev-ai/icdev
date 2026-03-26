# CUI // SP-CTI
"""Kanban Executor Reflex — polls kanban_tasks for due scheduled tasks,
promotes them, and dispatches to Claude Code CLI for autonomous execution.

Flow:
1. Poll Telegram for incoming commands
2. Query kanban_tasks for due scheduled + backlog tasks (rate-limited)
3. Move each due task to 'in_progress', write prompt file
4. Dispatch prompt file to `claude` CLI as a background subprocess
5. On completion: move to 'done', notify via Telegram, delete prompt file

The claude CLI runs headless with --dangerously-skip-permissions so tasks
execute without human approval. The daemon monitors subprocess completion.
"""

import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

PROMPT_DIR = BASE_DIR / ".tmp" / "kanban"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_prompt_dir():
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)


def _count_in_progress() -> int:
    """Count how many tasks are currently in_progress."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kanban_tasks "
            "WHERE status = 'in_progress'"
        ).fetchone()
        return dict(row).get("cnt", 0)
    finally:
        conn.close()


def _count_pending_prompts() -> int:
    """Count unexecuted prompt files in .tmp/kanban/."""
    if not PROMPT_DIR.exists():
        return 0
    return len(list(PROMPT_DIR.glob("task-*.md")))


# Max tasks to auto-promote per cycle (prevents flooding)
MAX_AUTO_PROMOTE = 2
# Max in-progress tasks at any time (prevents pile-up)
MAX_IN_PROGRESS = 3


def _get_due_tasks() -> list:
    """Find tasks ready for execution:
    1. Scheduled tasks whose scheduled_at has passed (always promoted)
    2. Backlog tasks (auto-promote, rate-limited)

    Rate limiting:
    - Max MAX_AUTO_PROMOTE backlog tasks promoted per cycle
    - Won't promote if MAX_IN_PROGRESS tasks already in_progress
    - Won't promote if there are unexecuted prompt files waiting

    Priority order: critical > high > medium > low,
    then oldest first.
    """
    conn = get_connection()
    try:
        # Always pick up scheduled-and-due tasks
        scheduled = conn.execute(
            "SELECT * FROM kanban_tasks "
            "WHERE status = 'scheduled' "
            "  AND scheduled_at IS NOT NULL "
            "  AND scheduled_at <= NOW() "
            "ORDER BY "
            "CASE priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "created_at ASC"
        ).fetchall()
        result = [dict(r) for r in scheduled]

        # Rate-limit backlog auto-promotion
        current_in_progress = _count_in_progress()
        pending_prompts = _count_pending_prompts()

        if current_in_progress >= MAX_IN_PROGRESS:
            return result  # Too many in-progress, only return scheduled

        if pending_prompts >= MAX_AUTO_PROMOTE:
            return result  # Prompt files waiting, don't add more

        slots = min(
            MAX_AUTO_PROMOTE,
            MAX_IN_PROGRESS - current_in_progress,
        )
        if slots <= 0:
            return result

        backlog = conn.execute(
            "SELECT * FROM kanban_tasks "
            "WHERE status = 'backlog' "
            "ORDER BY "
            "CASE priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "created_at ASC "
            "LIMIT %s",
            (slots,),
        ).fetchall()
        result.extend(dict(r) for r in backlog)

        return result
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


def _send_notification(task: dict, event: str = "in_progress"):
    """Send notification via dashboard DB + Telegram.

    Args:
        task: The kanban task dict.
        event: Status event — 'in_progress', 'done', 'failed'.
    """
    event_labels = {
        "in_progress": "now in progress",
        "done": "completed",
        "failed": "failed (will retry)",
    }
    label = event_labels.get(event, event)
    title = f"Task {event}: {task['title']}"
    body = (
        f"Kanban task '{task['title']}' "
        f"({task.get('task_type', 'build')}/{task.get('priority', 'medium')}) "
        f"is {label}."
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
                    f"notif-kanban-{task['id']}-{event}",
                    title,
                    body,
                    "success" if event == "done" else "info",
                    "genesis.kanban",
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Dashboard notification failed: %s", exc)

    # Telegram notification — load .env for bot token
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    try:
        from tools.notifications.adapters.telegram import send
        severity = "success" if event == "done" else "info"
        result = send(title, body, severity=severity)
        if result.get("status") != "sent":
            logger.warning(
                "Telegram notification failed: %s",
                result.get("message", "unknown"),
            )
    except Exception as exc:
        logger.warning("Telegram notification error: %s", exc)


def _poll_telegram():
    """Poll Telegram for incoming task commands."""
    try:
        from tools.notifications.adapters.telegram_listener import (
            poll_updates,
        )
        return poll_updates()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Claude CLI executor
# ---------------------------------------------------------------------------
CLAUDE_CLI = shutil.which("claude") or str(
    Path.home() / ".local" / "bin" / "claude"
)

# Track running subprocesses: {task_id: subprocess.Popen}
_running: Dict[str, subprocess.Popen] = {}


def _dispatch_to_claude(task: dict, prompt_path: str):
    """Launch claude CLI in background to execute the task."""
    task_id = task["id"]
    title = task.get("title", "Untitled")

    prompt_text = Path(prompt_path).read_text(encoding="utf-8")

    # Build the full instruction for Claude
    instruction = (
        f"{prompt_text}\n\n"
        f"When complete:\n"
        f"1. Move to done: POST http://localhost:5050/api/kanban/"
        f"tasks/{task_id}/move with {{\"status\": \"done\"}}\n"
        f"2. Notify: python -c \"from tools.notifications.adapters."
        f"telegram import send; send('Task Completed', "
        f"'{title} — done', severity='success')\"\n"
        f"3. Delete prompt file: {prompt_path}\n"
    )

    try:
        proc = subprocess.Popen(
            [
                CLAUDE_CLI,
                "--print",
                "--dangerously-skip-permissions",
                "--max-turns", "50",
                "-p", instruction,
            ],
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _running[task_id] = proc
        print(f"  Kanban: dispatched {task_id} to claude (PID {proc.pid})")
    except FileNotFoundError:
        print(f"  Kanban: claude CLI not found at {CLAUDE_CLI}")
    except Exception as e:
        print(f"  Kanban: dispatch error for {task_id}: {e}")


def _check_completed():
    """Check for completed claude subprocesses and clean up."""
    completed = []
    for task_id, proc in list(_running.items()):
        ret = proc.poll()
        if ret is not None:
            completed.append(task_id)
            prompt_path = PROMPT_DIR / f"{task_id}.md"
            if ret == 0:
                # Claude completed successfully — mark done, notify
                try:
                    _move_task(task_id, "done")
                except Exception:
                    pass
                _send_notification(
                    {"id": task_id, "title": task_id}, event="done",
                )
                if prompt_path.exists():
                    prompt_path.unlink()
                print(f"  Kanban: {task_id} completed (exit {ret})")
            else:
                # Claude failed — move task back to backlog for retry
                stderr = proc.stderr.read() if proc.stderr else ""
                print(
                    f"  Kanban: {task_id} failed (exit {ret})"
                    f"{': ' + stderr[:200] if stderr else ''}"
                )
                try:
                    _move_task(task_id, "backlog")
                    _send_notification(
                        {"id": task_id, "title": task_id},
                        event="failed",
                    )
                except Exception:
                    pass
            del _running[task_id]
    return completed


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Kanban Executor Reflex."""
    # 1. Check for completed claude subprocesses
    completed = _check_completed()

    # 2. Poll Telegram for new commands
    tg_results = _poll_telegram()
    if tg_results:
        print(f"  Kanban: {len(tg_results)} Telegram commands")

    # 3. Don't promote new tasks if claude is already running
    if _running:
        print(
            f"  Kanban: {len(_running)} task(s) executing in claude, "
            f"waiting..."
        )
        return {
            "success": True,
            "metric_value": len(completed),
            "details": {
                "status": "executing",
                "running": list(_running.keys()),
                "completed_this_cycle": completed,
                "telegram_commands": len(tg_results),
            },
        }

    # 4. Find due tasks
    due_tasks = _get_due_tasks()

    if not due_tasks:
        return {
            "success": True,
            "metric_value": len(completed),
            "details": {
                "status": "no_due_tasks",
                "completed_this_cycle": completed,
            },
        }

    processed = []
    errors = 0

    # Only dispatch ONE task at a time to claude
    task = due_tasks[0]
    try:
        # Move to in_progress
        _move_task(task["id"], "in_progress")

        # Write prompt file
        prompt_path = _write_prompt_file(task)

        # Send notification
        _send_notification(task)

        # Dispatch to claude CLI
        _dispatch_to_claude(task, prompt_path)

        processed.append({
            "id": task["id"],
            "title": task["title"],
            "prompt_file": prompt_path,
        })
        print(
            f"  Kanban: {task['id']} "
            f"'{task['title']}' -> in_progress -> dispatched"
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
            "completed_this_cycle": completed,
            "errors": errors,
            "tasks": processed,
        },
    }
