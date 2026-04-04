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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

PROMPT_DIR = BASE_DIR / ".tmp" / "kanban"
WORKTREE_BASE = BASE_DIR / ".tmp" / "worktrees"


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
    """Count prompt files for tasks actually in_progress (not orphaned).

    Previously counted all .md files, which blocked queue promotion when
    stale prompt files lingered from crashed/completed tasks.
    """
    if not PROMPT_DIR.exists():
        return 0
    prompt_files = list(PROMPT_DIR.glob("task-*.md"))
    if not prompt_files:
        return 0
    # Only count prompts whose task is still in_progress
    conn = get_connection()
    try:
        count = 0
        for pf in prompt_files:
            task_id = pf.stem  # e.g. "task-abc123"
            row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row and dict(row)["status"] == "in_progress":
                count += 1
            else:
                # Orphaned prompt — clean it up
                try:
                    pf.unlink()
                    logger.info("Cleaned up orphaned prompt: %s", pf.name)
                except OSError:
                    pass
        return count
    finally:
        conn.close()


# Max tasks to auto-promote per cycle (prevents flooding)
MAX_AUTO_PROMOTE = 2
# Max in-progress tasks at any time (prevents pile-up)
MAX_IN_PROGRESS = 3
# Max seconds a Claude CLI subprocess can run before being killed
MAX_EXECUTION_SECONDS = 1800  # 30 minutes

# ── Token exhaustion detection ────────────────────────────────────────────────

# Patterns that indicate Claude CLI hit a token/rate limit (case-insensitive)
TOKEN_EXHAUSTION_PATTERNS = [
    r"rate\s*limit",
    r"token\s*limit",
    r"usage\s*limit",
    r"quota\s*exceeded",
    r"too\s*many\s*requests",
    r"429",
    r"exceeded.*(?:daily|hourly|monthly)\s*(?:limit|quota|cap)",
    r"out\s*of\s*(?:tokens|credits)",
    r"billing.*limit",
    r"capacity.*limit",
    r"max.*turns.*reached",
    r"conversation.*limit",
    r"please\s*try\s*again\s*(?:later|in\s*\d+)",
    r"reset(?:s)?\s*(?:at|in)\s*\d+",
]
_TOKEN_RE = re.compile(
    "|".join(TOKEN_EXHAUSTION_PATTERNS), re.IGNORECASE
)

# How long to wait before retrying a token-exhausted task (seconds).
# Claude Max resets at the top of each 5-hour window.
TOKEN_RETRY_DELAY_SECONDS = 300  # 5 minutes between checks
TOKEN_MAX_RETRY_COUNT = 60       # Give up after ~5 hours of retries


def _detect_token_exhaustion(
    exit_code: int, output: str
) -> Tuple[bool, Optional[str]]:
    """Check if Claude CLI output indicates token/rate-limit exhaustion.

    Returns (is_exhausted, estimated_reset_info).
    """
    if not output:
        return False, None

    # Check the last 2000 chars (error messages usually at end)
    tail = output[-2000:]

    if _TOKEN_RE.search(tail):
        # Try to extract a reset time hint
        reset_match = re.search(
            r"(?:reset|try again|available)\s*(?:at|in)\s*"
            r"(\d[\d:hm \-]+)",
            tail, re.IGNORECASE,
        )
        reset_hint = reset_match.group(1).strip() if reset_match else None
        return True, reset_hint

    # Exit code 1 with very short output is suspicious but not conclusive
    # Exit code 2 is often used for rate limits by some CLI tools
    return False, None


def _get_retry_count(task_id: str) -> int:
    """Get the current token-exhaustion retry count for a task."""
    conn = get_connection()
    try:
        task_log = PROMPT_DIR / f"{task_id}.retries"
        if task_log.exists():
            try:
                return int(task_log.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                return 0
        return 0
    finally:
        conn.close()


def _increment_retry_count(task_id: str) -> int:
    """Increment and return the retry count."""
    _ensure_prompt_dir()
    retry_file = PROMPT_DIR / f"{task_id}.retries"
    count = 0
    if retry_file.exists():
        try:
            count = int(retry_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            count = 0
    count += 1
    retry_file.write_text(str(count), encoding="utf-8")
    return count


def _clear_retry_count(task_id: str):
    """Remove retry counter on success."""
    retry_file = PROMPT_DIR / f"{task_id}.retries"
    if retry_file.exists():
        retry_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Worktree isolation — each task runs in its own git worktree
# ---------------------------------------------------------------------------

# Track worktree paths: {task_id: worktree_path_str}
_worktrees: Dict[str, str] = {}


def _create_worktree(task_id: str) -> Optional[str]:
    """Create an isolated git worktree for a kanban task.

    Returns the worktree path on success, None on failure.
    Falls back to BASE_DIR if git worktree is unavailable.
    """
    import subprocess as _sp

    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
    branch_name = f"kanban/{task_id}"
    worktree_path = WORKTREE_BASE / task_id

    if worktree_path.exists():
        # Already exists from a previous attempt
        return str(worktree_path)

    try:
        # Create a new branch from HEAD for this task
        _sp.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=30,
        )
        if worktree_path.exists():
            logger.info(
                "Created worktree for %s at %s", task_id, worktree_path
            )
            return str(worktree_path)
    except Exception as exc:
        logger.warning("Worktree creation failed for %s: %s", task_id, exc)

    return None  # Fallback — caller uses BASE_DIR


def _cleanup_worktree(task_id: str):
    """Remove the worktree and branch after task completion."""
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    worktree_path = WORKTREE_BASE / task_id

    try:
        if worktree_path.exists():
            _sp.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=30,
            )
        # Delete the branch too
        _sp.run(
            ["git", "branch", "-D", branch_name],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=10,
        )
        logger.info("Cleaned up worktree for %s", task_id)
    except Exception as exc:
        logger.warning("Worktree cleanup failed for %s: %s", task_id, exc)


def _check_worktree_commits(task_id: str) -> bool:
    """Check if the worktree branch has new commits vs the parent branch."""
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    try:
        result = _sp.run(
            ["git", "log", "HEAD.." + branch_name, "--oneline"],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=10,
        )
        commits = result.stdout.strip()
        if commits:
            logger.info(
                "Worktree branch %s has new commits:\n%s",
                branch_name, commits,
            )
            return True
    except Exception as exc:
        logger.warning("Worktree commit check failed for %s: %s", task_id, exc)
    return False


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
            "  AND scheduled_at <= datetime('now') "
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
            "LIMIT ?",
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
            "UPDATE kanban_tasks SET status = ?, "
            "updated_at = ?"
        )
        vals = [new_status, now]
        if new_status == "done":
            sql += ", completed_at = ?"
            vals.append(now)
        sql += " WHERE id = ?"
        vals.append(task_id)
        conn.execute(sql, tuple(vals))
        conn.commit()
    finally:
        conn.close()
    # Broadcast SSE event for real-time dashboard updates
    try:
        from tools.dashboard.sse_manager import sse_manager
        sse_manager.broadcast({
            "action": "task_updated",
            "task_id": task_id,
            "changes": {"status": new_status},
        }, "kanban")
    except Exception:
        pass  # SSE is best-effort


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
        "token_exhausted": "paused — Claude token limit hit, will auto-retry",
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
                "VALUES (?, ?, ?, ?, ?, ?)",
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
    """Launch claude CLI in background to execute the task.

    Creates a git worktree for isolation so parallel tasks don't collide.
    Falls back to BASE_DIR if worktree creation fails.
    """
    task_id = task["id"]
    title = task.get("title", "Untitled")

    prompt_text = Path(prompt_path).read_text(encoding="utf-8")

    # Create isolated worktree for this task
    worktree_path = _create_worktree(task_id)
    work_dir = worktree_path if worktree_path else str(BASE_DIR)
    if worktree_path:
        _worktrees[task_id] = worktree_path
        print(f"  Kanban: using worktree {worktree_path} for {task_id}")
    else:
        print(f"  Kanban: worktree unavailable, using BASE_DIR for {task_id}")

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

    # Output log for this task
    task_log = PROMPT_DIR / f"{task_id}.log"

    try:
        log_fh = open(str(task_log), "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            [
                CLAUDE_CLI,
                "--dangerously-skip-permissions",
                "--max-turns", "50",
                "--output-format", "text",
                "-p", instruction,
            ],
            cwd=work_dir,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        _running[task_id] = proc
        _dispatch_times[task_id] = datetime.now(timezone.utc)
        print(f"  Kanban: dispatched {task_id} to claude (PID {proc.pid})")
    except FileNotFoundError:
        print(f"  Kanban: claude CLI not found at {CLAUDE_CLI}")
    except Exception as e:
        print(f"  Kanban: dispatch error for {task_id}: {e}")


def _verify_task_completed(task_id, claude_output):
    """Verify that a task actually produced results before marking done.

    Checks:
    1. Claude output must be substantial (>200 chars)
    2. No obvious failure indicators in output
    3. Git has new commits on the task's WORKTREE branch (not main)
    4. Output must contain evidence of actual file changes

    Returns: (verified: bool, reason: str)
    """
    # Check 1: Claude output must be substantial
    if not claude_output or len(claude_output) < 200:
        return False, "Output too short — likely no work done"

    # Check 2: Look for failure indicators (scan full output, not just first 500)
    fail_markers = [
        "I cannot", "I'm unable", "I don't have access",
        "Permission denied", "No such file", "FileNotFoundError",
        "I was unable to", "Error:", "failed to",
        "ModuleNotFoundError", "ImportError", "SyntaxError",
        "there is nothing to", "no changes", "already up to date",
    ]
    output_lower = claude_output.lower()
    for marker in fail_markers:
        if marker.lower() in output_lower[:1000]:
            return False, f"Output contains failure indicator: {marker}"

    # Check 3: Evidence of file changes in output
    file_change_markers = [
        "created", "modified", "updated", "wrote", "edited",
        "added", "fixed", "refactored", "generated",
        "tools/", "tests/", "args/", "goals/", "docs/",
    ]
    has_file_evidence = any(m in output_lower for m in file_change_markers)
    if not has_file_evidence:
        return False, "No evidence of file changes in output"

    # Check 4: Git commit check on the WORKTREE branch (not main)
    try:
        import subprocess as _sp
        branch_name = f"kanban/{task_id}"
        # Check if the worktree branch has commits ahead of HEAD
        result = _sp.run(
            ["git", "log", f"HEAD..{branch_name}", "--oneline"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(BASE_DIR), timeout=10,
        )
        worktree_commits = result.stdout.strip()
        if worktree_commits:
            return True, f"Verified: worktree has commits: {worktree_commits[:100]}"

        # Fallback: if no worktree branch (ran in BASE_DIR), check for
        # commits mentioning this task ID or title
        result = _sp.run(
            ["git", "log", "--oneline", "--since=30 minutes ago",
             "--all", "--grep", task_id[:12]],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(BASE_DIR), timeout=10,
        )
        if result.stdout.strip():
            return True, "Verified: found commits referencing task"

        # No task-specific commits found — output looked good but no proof
        return False, "No git commits found on task branch — work may not have been saved"
    except Exception:
        pass  # Git check failed — don't block on this

    # If git check failed but output has strong evidence, cautiously verify
    if len(claude_output) > 500 and has_file_evidence:
        return True, "Verified: strong output evidence (git check unavailable)"

    return False, "Insufficient evidence of task completion"


# Track when each task was dispatched: {task_id: datetime}
_dispatch_times: Dict[str, datetime] = {}


def _check_completed():
    """Check for completed claude subprocesses and clean up.

    Also enforces MAX_EXECUTION_SECONDS timeout — kills hung processes
    and returns them to backlog.
    """
    completed = []

    # ── TIMEOUT CHECK: kill hung processes ─────────────────────────
    now = datetime.now(timezone.utc)
    for task_id, proc in list(_running.items()):
        dispatch_time = _dispatch_times.get(task_id)
        if dispatch_time:
            elapsed = (now - dispatch_time).total_seconds()
            if elapsed > MAX_EXECUTION_SECONDS:
                print(
                    f"  Kanban: {task_id} TIMEOUT after "
                    f"{int(elapsed)}s (max {MAX_EXECUTION_SECONDS}s) "
                    f"— killing process"
                )
                try:
                    proc.kill()
                    proc.wait(timeout=10)
                except Exception:
                    pass
                _move_task(task_id, "backlog")
                # Build task dict for notification
                task_dict = {"id": task_id, "title": task_id}
                try:
                    task_conn = get_connection()
                    row = task_conn.execute(
                        "SELECT title FROM kanban_tasks WHERE id = ?",
                        (task_id,),
                    ).fetchone()
                    task_conn.close()
                    if row:
                        task_dict["title"] = row["title"]
                except Exception:
                    pass
                _send_notification(task_dict, event="failed")
                try:
                    from tools.notifications.adapters.telegram import (
                        send as tg_send,
                    )
                    tg_send(
                        f"TIMEOUT: {task_dict.get('title', task_id)[:60]}",
                        f"Task killed after {int(elapsed)}s — returned to backlog",
                        severity="warning",
                    )
                except Exception:
                    pass
                del _running[task_id]
                _dispatch_times.pop(task_id, None)
                _dispatch_times.pop(task_id, None)
                # Cleanup worktree
                if task_id in _worktrees:
                    _cleanup_worktree(task_id)
                    del _worktrees[task_id]
                completed.append(task_id)
                continue

    for task_id, proc in list(_running.items()):
        ret = proc.poll()
        if ret is not None:
            completed.append(task_id)
            prompt_path = PROMPT_DIR / f"{task_id}.md"
            # Read Claude output log
            claude_output = ""
            task_log = PROMPT_DIR / f"{task_id}.log"
            try:
                if task_log.exists():
                    claude_output = task_log.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
            except Exception:
                pass

            # Build task dict with title from DB or fallback
            task_dict = {"id": task_id, "title": task_id}
            try:
                task_conn = get_connection()
                row = task_conn.execute(
                    "SELECT title, task_type, priority FROM kanban_tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                task_conn.close()
                if row:
                    task_dict = {
                        "id": task_id,
                        "title": row["title"],
                        "task_type": row["task_type"],
                        "priority": row["priority"],
                    }
            except Exception:
                pass

            # ── TOKEN EXHAUSTION CHECK (runs for ANY exit code) ───────
            is_exhausted, reset_hint = _detect_token_exhaustion(
                ret, claude_output
            )
            if is_exhausted:
                retry_count = _increment_retry_count(task_id)
                if retry_count >= TOKEN_MAX_RETRY_COUNT:
                    # Exceeded max retries — move to backlog, give up
                    _move_task(task_id, "backlog")
                    _clear_retry_count(task_id)
                    _send_notification(task_dict, event="failed")
                    print(
                        f"  Kanban: {task_id} TOKEN EXHAUSTED — "
                        f"max retries ({TOKEN_MAX_RETRY_COUNT}) reached, "
                        f"returning to backlog"
                    )
                else:
                    # Park in token_exhausted — scheduler will retry later
                    _move_task(task_id, "token_exhausted")
                    reset_msg = (
                        f" (reset hint: {reset_hint})"
                        if reset_hint else ""
                    )
                    print(
                        f"  Kanban: {task_id} TOKEN EXHAUSTED"
                        f"{reset_msg} — retry {retry_count}/"
                        f"{TOKEN_MAX_RETRY_COUNT}, will auto-restart"
                    )
                    # Notify via Telegram
                    _send_notification(task_dict, event="token_exhausted")
                    try:
                        from tools.notifications.adapters.telegram import (
                            send as tg_send,
                        )
                        eta_minutes = TOKEN_RETRY_DELAY_SECONDS // 60
                        tg_send(
                            f"Token limit: {task_dict.get('title', task_id)[:50]}",
                            (
                                f"Claude token/rate limit hit on retry "
                                f"{retry_count}/{TOKEN_MAX_RETRY_COUNT}."
                                f"{reset_msg}\n"
                                f"Will auto-retry in ~{eta_minutes} min."
                            ),
                            severity="warning",
                        )
                    except Exception:
                        pass
                # Keep prompt file AND worktree for retry — don't delete
                del _running[task_id]
                _dispatch_times.pop(task_id, None)
                completed.append(task_id)
                continue

            # ── NORMAL SUCCESS PATH ───────────────────────────────────
            if ret == 0:
                # VERIFICATION GATE — prevent false positives
                verified, reason = _verify_task_completed(
                    task_id, claude_output
                )

                if verified:
                    try:
                        _move_task(task_id, "done")
                    except Exception:
                        pass
                    _clear_retry_count(task_id)
                else:
                    print(f"  Kanban: {task_id} UNVERIFIED: {reason}")
                    try:
                        _move_task(task_id, "backlog")
                    except Exception:
                        pass

                if verified:
                    _send_notification(task_dict, event="done")
                    print(
                        f"  Kanban: {task_id} VERIFIED done (exit {ret})"
                    )
                else:
                    _send_notification(task_dict, event="failed")
                    try:
                        from tools.notifications.adapters.telegram import (
                            send as tg_send,
                        )
                        tg_send(
                            f"UNVERIFIED: {task_dict.get('title', task_id)[:60]}",
                            f"Task returned to backlog. Reason: {reason}",
                            severity="warning",
                        )
                    except Exception:
                        pass
                    print(
                        f"  Kanban: {task_id} returned to backlog: "
                        f"{reason}"
                    )

                # Send Claude's actual answer back via Telegram
                if claude_output and verified:
                    try:
                        from tools.notifications.adapters.telegram import (
                            send as tg_send,
                        )
                        answer = claude_output[:3800]
                        tg_send(
                            f"Answer: {task_dict.get('title', task_id)[:60]}",
                            answer,
                            severity="info",
                        )
                    except Exception as tg_exc:
                        logger.warning(
                            "Failed to relay Claude output to Telegram: %s",
                            tg_exc,
                        )
                if prompt_path.exists():
                    prompt_path.unlink()
                _clear_retry_count(task_id)
                print(
                    f"  Kanban: {task_id} completed "
                    f"(exit {ret}, verified={verified})"
                )

                # ── WORKTREE CLEANUP (only on verified done) ─────────
                if verified and task_id in _worktrees:
                    has_commits = _check_worktree_commits(task_id)
                    if has_commits:
                        print(
                            f"  Kanban: worktree kanban/{task_id} has "
                            f"new commits (review before merging)"
                        )
                    _cleanup_worktree(task_id)
                    del _worktrees[task_id]
                elif not verified and task_id in _worktrees:
                    # Preserve worktree for debugging/retry
                    print(
                        f"  Kanban: preserving worktree for "
                        f"unverified task {task_id}"
                    )
            else:
                # ── NON-ZERO EXIT (not token exhaustion) ──────────────
                error_tail = ""
                try:
                    if claude_output:
                        lines = claude_output.split("\n")
                        error_tail = "\n".join(lines[-5:])
                except Exception:
                    pass
                print(
                    f"  Kanban: {task_id} failed (exit {ret})"
                    f"{': ' + error_tail[:200] if error_tail else ''}"
                )
                # Preserve worktree for debugging/retry — do NOT clean up
                if task_id in _worktrees:
                    print(
                        f"  Kanban: preserving worktree for "
                        f"failed task {task_id}"
                    )
                try:
                    _move_task(task_id, "backlog")
                    _send_notification(task_dict, event="failed")
                except Exception:
                    pass
            del _running[task_id]
    return completed


def _check_token_exhausted_tasks() -> list:
    """Re-promote token_exhausted tasks whose retry delay has elapsed.

    Checks updated_at timestamp — if TOKEN_RETRY_DELAY_SECONDS have passed
    since the task was parked, move it back to in_progress for re-dispatch.

    Returns list of task dicts ready for retry.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM kanban_tasks WHERE status = 'token_exhausted' "
            "ORDER BY "
            "CASE priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "updated_at ASC"
        ).fetchall()

        ready = []
        now = datetime.now(timezone.utc)
        for row in rows:
            task = dict(row)
            # Parse updated_at to check if delay has elapsed
            updated = task.get("updated_at")
            if updated:
                if isinstance(updated, str):
                    # Handle ISO format strings
                    try:
                        updated = datetime.fromisoformat(
                            updated.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        updated = now  # Can't parse — retry immediately
                elapsed = (now - updated).total_seconds()
                if elapsed < TOKEN_RETRY_DELAY_SECONDS:
                    remaining = TOKEN_RETRY_DELAY_SECONDS - elapsed
                    logger.debug(
                        "Token-exhausted task %s: %ds until retry",
                        task["id"], remaining,
                    )
                    continue
            ready.append(task)
        return ready
    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Kanban Executor Reflex."""
    # 1. Check for completed claude subprocesses
    completed = _check_completed()

    # 2. Poll Telegram for new commands
    tg_results = _poll_telegram()
    if tg_results:
        print(f"  Kanban: {len(tg_results)} Telegram commands")

    # 3. Don't promote new tasks if claude is already running
    #    BUT first clean up stale entries — if the task was marked done/backlog
    #    externally (e.g., Claude CLI self-reported via HTTP POST), the
    #    _running dict may hold an orphaned Popen reference that blocks
    #    all future promotions forever.
    if _running:
        stale_ids = []
        for tid, proc in list(_running.items()):
            try:
                task_conn = get_connection()
                row = task_conn.execute(
                    "SELECT status FROM kanban_tasks WHERE id = ?", (tid,)
                ).fetchone()
                task_conn.close()
                if row and dict(row)["status"] not in ("in_progress", "scheduled"):
                    # Task was completed/moved externally — clean up
                    stale_ids.append(tid)
            except Exception:
                pass
        for tid in stale_ids:
            print(f"  Kanban: cleaning up stale _running entry for {tid}")
            proc = _running.pop(tid, None)
            _dispatch_times.pop(tid, None)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            if tid in _worktrees:
                _cleanup_worktree(tid)
                del _worktrees[tid]

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

    # 3b. Check for token-exhausted tasks ready for retry
    token_retry_tasks = _check_token_exhausted_tasks()
    if token_retry_tasks:
        # Re-dispatch the highest-priority token-exhausted task
        task = token_retry_tasks[0]
        retry_count = _get_retry_count(task["id"])
        print(
            f"  Kanban: retrying token-exhausted task {task['id']} "
            f"'{task['title'][:40]}' (retry {retry_count}/"
            f"{TOKEN_MAX_RETRY_COUNT})"
        )
        try:
            _move_task(task["id"], "in_progress")
            prompt_path = PROMPT_DIR / f"{task['id']}.md"
            if not prompt_path.exists():
                prompt_path = _write_prompt_file(task)
            else:
                prompt_path = str(prompt_path)
            _dispatch_to_claude(task, prompt_path)
            _send_notification(task, event="in_progress")
            return {
                "success": True,
                "metric_value": 1,
                "details": {
                    "status": "token_retry",
                    "task_id": task["id"],
                    "retry_count": retry_count,
                    "completed_this_cycle": completed,
                    "telegram_commands": len(tg_results),
                },
            }
        except Exception as e:
            print(f"  Kanban: token retry error for {task['id']}: {e}")

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
