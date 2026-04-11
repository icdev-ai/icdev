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

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
        row = conn.execute("SELECT COUNT(*) AS cnt FROM kanban_tasks WHERE status = 'in_progress'").fetchone()
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
            row = conn.execute("SELECT status FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
            if row and dict(row)["status"] == "in_progress":
                count += 1
            elif row and dict(row)["status"] in ("backlog", "scheduled", "token_exhausted"):
                # Task exists and may be retried — don't count but don't delete
                pass
            else:
                # Task is done or doesn't exist — truly orphaned
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
    r"reset(?:s)?\s*(?:at|in)?\s*\d+\s*(?:am|pm)",
    r"hit\s*your\s*limit",
    r"you'?ve\s*hit\s*your\s*limit",
]
_TOKEN_RE = re.compile("|".join(TOKEN_EXHAUSTION_PATTERNS), re.IGNORECASE)

# How long to wait before retrying a token-exhausted task (seconds).
# Claude Max resets at the top of each 5-hour window.
TOKEN_RETRY_DELAY_SECONDS = 300  # 5 minutes between checks
TOKEN_MAX_RETRY_COUNT = 60  # Give up after ~5 hours of retries


def _detect_token_exhaustion(exit_code: int, output: str) -> Tuple[bool, Optional[str]]:
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
            tail,
            re.IGNORECASE,
        )
        reset_hint = reset_match.group(1).strip() if reset_match else None
        return True, reset_hint

    # Exit code 1 with very short output is suspicious but not conclusive
    # Exit code 2 is often used for rate limits by some CLI tools
    return False, None


def _parse_resume_at(reset_hint: Optional[str]) -> datetime:
    """Parse a reset hint into an absolute UTC datetime for resume.

    Handles these forms from Claude CLI output:
      - "5 minutes" / "5m" / "5 min"        → now + N minutes
      - "1 hour" / "2h" / "1 hr"            → now + N hours
      - "300 seconds" / "300s"               → now + N seconds
      - "2:00 AM" / "2:00 pm" / "14:00"     → next occurrence of that wall-clock time (local TZ)
      - None / unparseable                   → now + TOKEN_RETRY_DELAY_SECONDS (5 min fallback)
    """
    now = datetime.now(timezone.utc)
    fallback = now + timedelta(seconds=TOKEN_RETRY_DELAY_SECONDS)

    if not reset_hint:
        return fallback

    hint = reset_hint.strip().lower()

    # ── Relative: "N minutes/hours/seconds" ───────────────────────────
    rel = re.match(
        r"(\d+)\s*(?:"
        r"(s(?:ec(?:ond)?s?)?)|"
        r"(m(?:in(?:ute)?s?)?)|"
        r"(h(?:(?:ou)?rs?)?)"
        r")\b",
        hint,
    )
    if rel:
        n = int(rel.group(1))
        if rel.group(2):  # seconds
            return now + timedelta(seconds=max(n, 60))
        if rel.group(3):  # minutes
            return now + timedelta(minutes=max(n, 1))
        if rel.group(4):  # hours
            return now + timedelta(hours=n)

    # ── Absolute: "2:00 AM" / "14:00" ────────────────────────────────
    abs_match = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)?", hint)
    if abs_match:
        hour = int(abs_match.group(1))
        minute = int(abs_match.group(2))
        ampm = abs_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        # Build a local datetime, then convert to UTC
        try:
            local_now = datetime.now().astimezone()
            target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= local_now:
                target += timedelta(days=1)  # Next occurrence
            return target.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            pass

    return fallback


def _save_resume_at(task_id: str, resume_at: datetime):
    """Persist the resume-at timestamp for a token-exhausted task."""
    _ensure_prompt_dir()
    resume_file = PROMPT_DIR / f"{task_id}.resume_at"
    resume_file.write_text(resume_at.isoformat(), encoding="utf-8")


def _load_resume_at(task_id: str) -> Optional[datetime]:
    """Load the persisted resume-at timestamp, or None if missing."""
    resume_file = PROMPT_DIR / f"{task_id}.resume_at"
    if not resume_file.exists():
        return None
    try:
        text = resume_file.read_text(encoding="utf-8").strip()
        if "+" not in text and text.endswith("Z"):
            text = text[:-1] + "+00:00"
        elif "+" not in text and "-" not in text[10:]:
            text += "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OSError):
        return None


def _clear_resume_at(task_id: str):
    """Remove resume-at file on success or retry-give-up."""
    resume_file = PROMPT_DIR / f"{task_id}.resume_at"
    if resume_file.exists():
        resume_file.unlink(missing_ok=True)


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
            capture_output=True,
            text=True,
            timeout=30,
        )
        if worktree_path.exists():
            logger.info("Created worktree for %s at %s", task_id, worktree_path)
            return str(worktree_path)
    except Exception as exc:
        logger.warning("Worktree creation failed for %s: %s", task_id, exc)

    return None  # Fallback — caller uses BASE_DIR


def _merge_worktree_to_main(task_id: str) -> bool:
    """Fast-forward merge the kanban task branch into the parent branch
    before cleanup so dependent tasks see each other's commits.

    Returns True if merge succeeded (or branch had no commits to merge),
    False if merge was non-fast-forward or encountered an error. On
    failure, the branch is PRESERVED (not deleted) so the user can merge
    manually.

    Rationale: prior behavior hard-deleted the branch on cleanup, losing
    all commits. This fix ensures successful task work is preserved on
    main; dependent multi-phase work (e.g. Internal Awareness Engine
    phases 1..6) now builds incrementally instead of each phase starting
    from the same main HEAD.
    """
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"

    # 1) Is there anything to merge?
    try:
        result = _sp.run(
            ["git", "log", "main.." + branch_name, "--oneline"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # Branch doesn't exist or main branch name is different —
            # treat as no-op and let caller delete
            return True
        if not result.stdout.strip():
            return True  # Nothing to merge
    except Exception as exc:
        logger.warning("Pre-merge commit check failed for %s: %s", task_id, exc)
        return False

    # 2) Determine current branch on main worktree so we can restore it
    try:
        cur_branch_proc = _sp.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        cur_branch = (cur_branch_proc.stdout or "main").strip() or "main"
    except Exception:
        cur_branch = "main"

    # 3) Checkout main and fast-forward merge
    try:
        co = _sp.run(
            ["git", "checkout", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if co.returncode != 0:
            logger.warning(
                "Could not checkout main for merge of %s: %s",
                task_id,
                co.stderr[:200],
            )
            return False

        merge = _sp.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if merge.returncode != 0:
            # Non-fast-forward — main moved ahead. Leave branch in place
            # for manual merge, try to restore the original branch on
            # main worktree, and report failure.
            logger.warning(
                "Non-fast-forward merge for %s: %s — branch preserved",
                task_id,
                merge.stderr[:200],
            )
            if cur_branch != "main":
                _sp.run(
                    ["git", "checkout", cur_branch],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            return False

        logger.info(
            "Merged kanban/%s to main (fast-forward, %d commits)",
            task_id,
            len(result.stdout.strip().splitlines()),
        )
        return True
    except Exception as exc:
        logger.warning("Merge to main failed for %s: %s", task_id, exc)
        return False


def _cleanup_worktree(task_id: str):
    """Merge the kanban task branch to main (fast-forward) then remove
    the worktree. If merge fails, the branch is preserved for manual
    review and the worktree is still cleaned up (disk hygiene).
    """
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    worktree_path = WORKTREE_BASE / task_id

    # Attempt merge first — if this fails, the branch stays around so
    # the user can merge manually (their commits are NOT lost).
    merged_ok = _merge_worktree_to_main(task_id)

    try:
        if worktree_path.exists():
            _sp.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
        # Only delete the branch if merge succeeded; otherwise PRESERVE
        # it so the user doesn't lose commits.
        if merged_ok:
            _sp.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.info("Cleaned up worktree + merged branch for %s", task_id)
        else:
            logger.warning(
                "Worktree removed but branch kanban/%s PRESERVED for manual merge",
                task_id,
            )
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
            capture_output=True,
            text=True,
            timeout=10,
        )
        commits = result.stdout.strip()
        if commits:
            logger.info(
                "Worktree branch %s has new commits:\n%s",
                branch_name,
                commits,
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

        # Backlog cooldown: skip tasks updated in the last 10 minutes
        # (prevents rapid-fire retry of recently failed tasks)
        backlog = conn.execute(
            "SELECT * FROM kanban_tasks "
            "WHERE status = 'backlog' "
            "  AND (updated_at IS NULL "
            "       OR updated_at <= datetime('now', '-10 minutes')) "
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
        sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
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

        sse_manager.broadcast(
            {
                "action": "task_updated",
                "task_id": task_id,
                "changes": {"status": new_status},
            },
            "kanban",
        )
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
- **Scheduled:** {task.get("scheduled_at", "now")}

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
        "failed": "failed (returned to backlog with 10-min cooldown)",
        "token_exhausted": "PAUSED — token limit hit, will auto-resume at scheduled time",
        "retry_exhausted": f"GAVE UP — exceeded {TOKEN_MAX_RETRY_COUNT} retries, moved to backlog",
    }
    label = event_labels.get(event, event)
    title = f"Task {event}: {task['title']}"
    body = (
        f"Kanban task '{task['title']}' ({task.get('task_type', 'build')}/{task.get('priority', 'medium')}) is {label}."
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
                    f"notif-kanban-{task['id']}-{event}-{_utcnow_iso()[:19]}",
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
# Task executor — Claude Code CLI when available, LLMRouter otherwise (OPT-31)
# ---------------------------------------------------------------------------
# Lazy resolution: don't pin the path at import time so a Claude install that
# arrives mid-session is picked up automatically. The default home-dir fallback
# is preserved for systems where Claude is installed but not on PATH.
def _resolve_claude_cli() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


def _claude_code_available() -> bool:
    """True if the `claude` CLI is invokable on this host."""
    return _resolve_claude_cli() is not None


# Track running task handles. Claude path stores subprocess.Popen, LLMRouter
# path stores _LLMTaskHandle — both expose .poll() / .kill() / .wait() / .pid /
# .returncode so the rest of the reflex (timeout sweeper, completion checker)
# can treat them uniformly.
_running: Dict[str, Any] = {}


class _LLMTaskHandle:
    """Popen-compatible handle around a threaded LLMRouter.invoke() call.

    Used by LocalPythonTaskExecutor when Claude Code CLI is unavailable. The
    LLM call runs in a daemon thread; .poll() returns None until the thread
    finishes, then the integer return code (0=success, 1=failure). Output is
    written to the same task log file the Claude path uses, so downstream
    verification logic (_verify_task_completed, token-exhaustion detection)
    works unchanged.
    """

    def __init__(self, task_id: str, log_path: Path):
        self.task_id = task_id
        self.log_path = log_path
        self.pid = -1  # synthetic — no OS process
        self.returncode: Optional[int] = None
        self._thread: Optional[Any] = None
        self._killed = False

    def start(self, target, args=()):
        import threading
        self._thread = threading.Thread(
            target=self._wrap, args=(target, args), name=f"kanban-llm-{self.task_id}", daemon=True,
        )
        self._thread.start()

    def _wrap(self, target, args):
        try:
            target(*args)
            self.returncode = 0
        except Exception as exc:
            try:
                with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                    fh.write(f"\n[LLMTaskHandle EXCEPTION] {type(exc).__name__}: {exc}\n")
            except Exception:
                pass
            self.returncode = 1

    def poll(self) -> Optional[int]:
        if self._thread is None:
            return None
        if self._thread.is_alive():
            return None
        return self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        # Daemon threads cannot be force-killed in CPython; mark as killed and
        # let the timeout sweeper move on. The thread will eventually exit.
        self._killed = True
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write("\n[LLMTaskHandle KILLED by timeout sweeper]\n")
        except Exception:
            pass

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        return self.returncode if self.returncode is not None else 0


def _build_instruction(task_id: str, title: str, prompt_text: str, prompt_path: str) -> str:
    """Compose the full instruction text used by both executors."""
    return (
        f"{prompt_text}\n\n"
        f"When complete:\n"
        f"1. Move to done: POST http://localhost:5050/api/kanban/"
        f'tasks/{task_id}/move with {{"status": "done"}}\n'
        f'2. Notify: python -c "from tools.notifications.adapters.'
        f"telegram import send; send('Task Completed', "
        f"'{title} — done', severity='success')\"\n"
        f"3. Delete prompt file: {prompt_path}\n"
    )


def _dispatch_via_claude_cli(task: dict, prompt_path: str, instruction: str,
                             work_dir: str, task_log: Path) -> None:
    """ClaudeCodeTaskExecutor — original behavior, isolated."""
    task_id = task["id"]
    claude_cli = _resolve_claude_cli()
    if not claude_cli:
        print("  Kanban: claude CLI not found — should have routed to LLM executor")
        return
    try:
        log_fh = open(str(task_log), "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            [
                claude_cli,
                "--dangerously-skip-permissions",
                "--max-turns",
                "50",
                "--output-format",
                "text",
                "-p",
                instruction,
            ],
            cwd=work_dir,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        _running[task_id] = proc
        _dispatch_times[task_id] = datetime.now(timezone.utc)
        print(f"  Kanban: dispatched {task_id} to claude CLI (PID {proc.pid})")
    except FileNotFoundError:
        print(f"  Kanban: claude CLI not found at {claude_cli}")
    except Exception as e:
        print(f"  Kanban: claude dispatch error for {task_id}: {e}")


def _dispatch_via_llm_router(task: dict, prompt_path: str, instruction: str,
                             work_dir: str, task_log: Path) -> None:
    """LocalPythonTaskExecutor — air-gap fallback that runs the prompt through
    tools.llm.router.LLMRouter so Bedrock/Ollama/Vertex/etc. can serve tasks
    without Claude Code CLI installed.

    Note: LLM-only execution cannot perform real file mutations the way the
    Claude CLI agent does. The LLM produces a written response (saved to the
    task log) which a human or downstream tool then applies. For tasks that
    require autonomous file editing, install Claude Code CLI or wait for the
    OPT-42 anvil/* CLI wrappers.
    """
    task_id = task["id"]

    def _runner():
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        from tools.airgap import hook_compat

        # OPT-62: cap the mid-run message-injection loop so a stuck queue
        # can't spin forever. Each iteration is one LLMRouter.invoke() call.
        MAX_ITERATIONS = 10

        # Open the log fresh — overwrite any prior partial content
        with open(task_log, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[LLMRouter dispatch — task {task_id}]\n")
            fh.write(f"[work_dir {work_dir}]\n\n")

            router = LLMRouter()
            system_prompt = (
                "You are an autonomous task executor for the ICDEV™ "
                "kanban system. Read the task prompt, plan the work, and "
                "produce a detailed written plan + result. You cannot "
                "directly execute shell commands or edit files in this "
                "execution mode — describe exactly what should be done so "
                "a downstream tool or human can apply it."
            )
            messages: list = [{"role": "user", "content": instruction}]

            for iteration in range(1, MAX_ITERATIONS + 1):
                fh.write(f"\n[iteration {iteration}/{MAX_ITERATIONS}]\n")
                request = LLMRequest(
                    messages=list(messages),
                    system_prompt=system_prompt,
                    max_tokens=8192,
                    temperature=0.3,
                    agent_id="kanban-executor",
                    project_id="dashboard-kanban",
                )
                response = router.invoke("code_generation", request)

                fh.write(response.content or "")
                fh.write(
                    f"\n[LLM metadata] provider={response.provider} "
                    f"model={response.model_id} "
                    f"in_tokens={response.input_tokens} "
                    f"out_tokens={response.output_tokens} "
                    f"duration_ms={response.duration_ms}\n"
                )
                try:
                    fh.flush()
                except Exception:
                    pass

                # OPT-62: drain the user message queue. If nothing was
                # injected mid-run, the task is done — exit the loop.
                try:
                    queued = hook_compat.check_message_queue(task_id)
                except Exception as exc:
                    fh.write(f"[check_message_queue error] {exc}\n")
                    queued = []

                if not queued:
                    break

                fh.write(
                    f"\n[OPT-62] injecting {len(queued)} queued "
                    f"message(s) into iteration {iteration + 1}\n"
                )
                messages.append(
                    {"role": "assistant", "content": response.content or ""}
                )
                for m in queued:
                    sender = m.get("sender", "user") or "user"
                    content_text = m.get("content", "") or ""
                    messages.append({
                        "role": "user",
                        "content": f"[injected by {sender}] {content_text}",
                    })
            else:
                fh.write(
                    f"\n[OPT-62] hit MAX_ITERATIONS={MAX_ITERATIONS} "
                    f"with pending messages — stopping\n"
                )

    handle = _LLMTaskHandle(task_id=task_id, log_path=task_log)
    handle.start(_runner)
    _running[task_id] = handle
    _dispatch_times[task_id] = datetime.now(timezone.utc)
    print(f"  Kanban: dispatched {task_id} via LLMRouter (no Claude CLI)")


def _dispatch_to_claude(task: dict, prompt_path: str):
    """Dispatch a task to the appropriate executor.

    Picks ClaudeCodeTaskExecutor when the `claude` CLI is available, otherwise
    falls back to LocalPythonTaskExecutor (LLMRouter-backed). The function
    name is preserved for backwards compatibility with existing call sites.

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

    instruction = _build_instruction(task_id, title, prompt_text, prompt_path)
    task_log = PROMPT_DIR / f"{task_id}.log"

    if _claude_code_available():
        _dispatch_via_claude_cli(task, prompt_path, instruction, work_dir, task_log)
    else:
        _dispatch_via_llm_router(task, prompt_path, instruction, work_dir, task_log)


# OPT-76 — phantom-completion guard. Regex matches plausible repo-relative
# file paths in agent output. Covers tools/foo.py, args/foo.yaml, tests/
# subdirs, docs/, goals/, and similar. Skips URLs, package names, and
# arbitrary English phrases containing slashes.
_PATH_MENTION_RE = re.compile(
    r"(?:^|[\s`'\"(\[])"
    r"((?:tools|tests|args|goals|docs|scripts|third_party_licenses|helm|k8s)/"
    r"[A-Za-z0-9_\-./]+"
    r"\.(?:py|yaml|yml|json|md|toml|txt|sh|sql|ini|cfg))"
    r"(?=[\s`'\")\],.:;]|$)",
    re.MULTILINE,
)


def _extract_claimed_file_paths(text: str, max_paths: int = 50) -> list[str]:
    """Pull repo-relative file paths out of agent output.

    Returns a deduplicated list of up to max_paths plausible repo paths
    the agent claims to have created/modified. Used by the phantom-
    completion guard — if the agent claims to write to these paths but
    none of them exist on disk, the task is rejected.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _PATH_MENTION_RE.finditer(text):
        path = m.group(1).strip().rstrip(".,;:)")
        # Skip obvious non-files (patterns / globs / "..." truncations)
        if "*" in path or "..." in path or path.endswith("/"):
            continue
        if path not in seen:
            seen[path] = None
        if len(seen) >= max_paths:
            break
    return list(seen.keys())


def _verify_claimed_files_exist(
    paths: list[str], work_dir: Path | str
) -> tuple[int, int, list[str]]:
    """Check how many agent-claimed paths actually exist on disk.

    Returns (existing_count, claimed_count, missing_paths[:5]).
    Paths are resolved relative to work_dir AND BASE_DIR — the agent
    may have run in a worktree OR the main checkout.
    """
    if not paths:
        return 0, 0, []
    work = Path(work_dir) if work_dir else BASE_DIR
    existing = 0
    missing: list[str] = []
    for rel in paths:
        # Try work_dir first (worktree), fall back to BASE_DIR
        candidates = [work / rel, BASE_DIR / rel]
        if any(c.exists() for c in candidates):
            existing += 1
        else:
            missing.append(rel)
    return existing, len(paths), missing[:5]


def _verify_task_completed(task_id, claude_output):
    """Verify that a task actually produced results before marking done.

    Checks (order matters; early returns on failure):
    1. Claude output must be substantial (>200 chars)
    2. No obvious failure indicators in output
    3. Output must contain evidence of actual file changes (keywords)
    4. **Phantom-completion guard (OPT-76)**: if output claims specific
       file paths, at least some must actually exist on disk. A
       hallucinating agent that says "I created tools/X.py" without
       actually creating the file fails here.
    5. Git has new commits on the task's WORKTREE branch OR staged
       changes visible in the work_dir.

    Returns: (verified: bool, reason: str)
    """
    # Check 1: Claude output must be substantial
    if not claude_output or len(claude_output) < 200:
        return False, "Output too short — likely no work done"

    # Check 2: Look for failure indicators (scan first 1000 chars — the
    # agent usually declares failure up front if it's going to)
    fail_markers = [
        "I cannot",
        "I'm unable",
        "I don't have access",
        "Permission denied",
        "No such file",
        "FileNotFoundError",
        "I was unable to",
        "Error:",
        "failed to",
        "ModuleNotFoundError",
        "ImportError",
        "SyntaxError",
        "there is nothing to",
        "no changes",
        "already up to date",
    ]
    output_lower = claude_output.lower()
    for marker in fail_markers:
        if marker.lower() in output_lower[:1000]:
            return False, f"Output contains failure indicator: {marker}"

    # Check 3: Evidence of file changes in output
    file_change_markers = [
        "created",
        "modified",
        "updated",
        "wrote",
        "edited",
        "added",
        "fixed",
        "refactored",
        "generated",
        "tools/",
        "tests/",
        "args/",
        "goals/",
        "docs/",
    ]
    has_file_evidence = any(m in output_lower for m in file_change_markers)
    if not has_file_evidence:
        return False, "No evidence of file changes in output"

    # Check 4 — OPT-76 phantom guard: extract every path the agent
    # claims to have touched and verify at least SOME of them exist.
    work_dir_for_check = _worktrees.get(task_id) or str(BASE_DIR)
    claimed_paths = _extract_claimed_file_paths(claude_output)
    if claimed_paths:
        existing, claimed, missing = _verify_claimed_files_exist(
            claimed_paths, work_dir_for_check
        )
        # If the agent mentioned paths AND none of them exist, it's
        # phantom output. Fail regardless of how much prose it generated.
        if existing == 0:
            missing_preview = ", ".join(missing[:3])
            return False, (
                f"PHANTOM COMPLETION: agent claimed {claimed} file path(s) "
                f"but NONE exist on disk (missing: {missing_preview}). "
                f"Output was likely hallucinated — see agent log."
            )
        # Partial existence is acceptable — the agent may have
        # referenced pre-existing files it read or mentioned neighbors.
        # But log the ratio so the reviewer can spot drift.
        phantom_ratio = (claimed - existing) / claimed
        if phantom_ratio >= 0.8:
            # 80%+ of claimed paths are missing — suspicious but not blocking
            logger.warning(
                "kanban %s: %d/%d claimed paths missing (%s, ...)",
                task_id, claimed - existing, claimed, missing[:3],
            )

    # Check 5: Git commit check on the WORKTREE branch (not main) OR
    # dirty working-directory check for non-worktree runs. Failures in
    # git commands DO NOT fall through to a lenient pass — that was the
    # original phantom path.
    try:
        import subprocess as _sp

        branch_name = f"kanban/{task_id}"
        result = _sp.run(
            ["git", "log", f"HEAD..{branch_name}", "--oneline"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            timeout=10,
        )
        worktree_commits = result.stdout.strip()
        if worktree_commits:
            return True, f"Verified: worktree has commits: {worktree_commits[:100]}"

        # Fallback: commits in the last 30 min mentioning this task id
        result = _sp.run(
            ["git", "log", "--oneline", "--since=30 minutes ago", "--all", "--grep", task_id[:12]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            timeout=10,
        )
        if result.stdout.strip():
            return True, "Verified: found commits referencing task"

        # No commits found — but the agent may have left uncommitted
        # changes in the work dir. Check for a dirty working tree.
        dirty = _sp.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir_for_check,
            timeout=10,
        )
        dirty_lines = [line for line in (dirty.stdout or "").splitlines() if line.strip()]
        if dirty_lines:
            return True, (
                f"Verified: {len(dirty_lines)} uncommitted change(s) in work dir "
                f"(agent didn't commit but produced files)"
            )

        # No commits AND no dirty state AND no strong claimed-file evidence
        return False, (
            "No git commits AND no uncommitted changes — "
            "agent produced no file-level output"
        )
    except Exception as exc:
        # OPT-76: do NOT fall through to a lenient pass. Git failures
        # are the exact path the phantom-completion bug exploited.
        # Require claimed-path verification instead.
        if claimed_paths and existing > 0:
            return True, (
                f"Verified via claimed-path check: {existing}/{claimed} "
                f"agent-referenced file(s) exist on disk (git check failed: {exc})"
            )
        return False, f"Git check failed ({exc}) and no claimed paths verified"


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
                    claude_output = task_log.read_text(encoding="utf-8", errors="replace").strip()
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
            is_exhausted, reset_hint = _detect_token_exhaustion(ret, claude_output)
            if is_exhausted:
                retry_count = _increment_retry_count(task_id)
                if retry_count >= TOKEN_MAX_RETRY_COUNT:
                    # Exceeded max retries — move to backlog, give up
                    _move_task(task_id, "backlog")
                    _clear_retry_count(task_id)
                    _clear_resume_at(task_id)
                    _send_notification(task_dict, event="failed")
                    print(
                        f"  Kanban: {task_id} TOKEN EXHAUSTED — "
                        f"max retries ({TOKEN_MAX_RETRY_COUNT}) reached, "
                        f"returning to backlog"
                    )
                else:
                    # Park in token_exhausted — scheduler will retry at resume_at
                    _move_task(task_id, "token_exhausted")
                    resume_at = _parse_resume_at(reset_hint)
                    _save_resume_at(task_id, resume_at)
                    wait_seconds = max(0, (resume_at - datetime.now(timezone.utc)).total_seconds())
                    wait_minutes = int(wait_seconds / 60) + 1
                    reset_msg = f" (reset hint: {reset_hint})" if reset_hint else ""
                    resume_local = resume_at.astimezone().strftime("%I:%M %p")
                    print(
                        f"  Kanban: {task_id} TOKEN EXHAUSTED"
                        f"{reset_msg} — retry {retry_count}/"
                        f"{TOKEN_MAX_RETRY_COUNT}, will resume at "
                        f"{resume_local} (~{wait_minutes} min)"
                    )
                    # Notify via Telegram
                    _send_notification(task_dict, event="token_exhausted")
                    try:
                        from tools.notifications.adapters.telegram import (
                            send as tg_send,
                        )

                        tg_send(
                            f"Token limit: {task_dict.get('title', task_id)[:50]}",
                            (
                                f"Claude token/rate limit hit on retry "
                                f"{retry_count}/{TOKEN_MAX_RETRY_COUNT}."
                                f"{reset_msg}\n"
                                f"Will auto-resume at {resume_local} "
                                f"(~{wait_minutes} min)."
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
                verified, reason = _verify_task_completed(task_id, claude_output)

                if verified:
                    try:
                        _move_task(task_id, "done")
                    except Exception:
                        pass
                    _clear_retry_count(task_id)
                    _clear_resume_at(task_id)
                else:
                    print(f"  Kanban: {task_id} UNVERIFIED: {reason}")
                    try:
                        _move_task(task_id, "backlog")
                    except Exception:
                        pass

                if verified:
                    _send_notification(task_dict, event="done")
                    print(f"  Kanban: {task_id} VERIFIED done (exit {ret})")
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
                    print(f"  Kanban: {task_id} returned to backlog: {reason}")

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
                _clear_resume_at(task_id)
                print(f"  Kanban: {task_id} completed (exit {ret}, verified={verified})")

                # ── WORKTREE CLEANUP (only on verified done) ─────────
                if verified and task_id in _worktrees:
                    has_commits = _check_worktree_commits(task_id)
                    if has_commits:
                        print(f"  Kanban: worktree kanban/{task_id} has new commits (review before merging)")
                    _cleanup_worktree(task_id)
                    del _worktrees[task_id]
                elif not verified and task_id in _worktrees:
                    # Preserve worktree for debugging/retry
                    print(f"  Kanban: preserving worktree for unverified task {task_id}")
            else:
                # ── NON-ZERO EXIT (not token exhaustion) ──────────────
                error_tail = ""
                try:
                    if claude_output:
                        lines = claude_output.split("\n")
                        error_tail = "\n".join(lines[-5:])
                except Exception:
                    pass
                print(f"  Kanban: {task_id} failed (exit {ret}){': ' + error_tail[:200] if error_tail else ''}")
                # Preserve worktree for debugging/retry — do NOT clean up
                if task_id in _worktrees:
                    print(f"  Kanban: preserving worktree for failed task {task_id}")
                try:
                    _move_task(task_id, "backlog")
                    _send_notification(task_dict, event="failed")
                except Exception:
                    pass
            del _running[task_id]
    return completed


def _check_token_exhausted_tasks() -> list:
    """Return token-exhausted tasks whose resume_at time has passed.

    Each task has a persisted resume_at timestamp (written when parked).
    The scheduler calls this every 60s — tasks are only returned once
    their resume_at is in the past, so there is zero wasted polling.

    Falls back to TOKEN_RETRY_DELAY_SECONDS after updated_at if no
    resume_at file exists (e.g. task was parked before this code shipped).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM kanban_tasks WHERE status = 'token_exhausted' "
            "ORDER BY CASE priority "
            "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 ELSE 3 END, "
            "updated_at ASC"
        ).fetchall()
        if not rows:
            return []

        now = datetime.now(timezone.utc)
        ready = []
        for row in rows:
            task = dict(row)
            task_id = task["id"]

            # 1. Load persisted resume_at (preferred)
            resume_at = _load_resume_at(task_id)

            # 2. Fallback: updated_at + TOKEN_RETRY_DELAY_SECONDS
            if resume_at is None:
                updated_str = task.get("updated_at", "")
                try:
                    if updated_str:
                        updated_str = updated_str.replace("Z", "+00:00")
                        if "+" not in updated_str and "T" in updated_str:
                            updated_str += "+00:00"
                        updated_at = datetime.fromisoformat(updated_str)
                        if updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=timezone.utc)
                    else:
                        updated_at = now - timedelta(seconds=TOKEN_RETRY_DELAY_SECONDS + 1)
                except (ValueError, TypeError):
                    updated_at = now - timedelta(seconds=TOKEN_RETRY_DELAY_SECONDS + 1)
                resume_at = updated_at + timedelta(seconds=TOKEN_RETRY_DELAY_SECONDS)

            # 3. Not yet — log and skip
            if now < resume_at:
                remaining = (resume_at - now).total_seconds()
                resume_local = resume_at.astimezone().strftime("%I:%M %p")
                logger.info(
                    "Task %s waiting until %s (%d min remaining)",
                    task_id,
                    resume_local,
                    int(remaining / 60) + 1,
                )
                continue

            # 4. Check retry count — give up after TOKEN_MAX_RETRY_COUNT
            retry_count = _get_retry_count(task_id)
            if retry_count >= TOKEN_MAX_RETRY_COUNT:
                logger.info(
                    "Task %s exceeded max retries (%d) — moving to backlog",
                    task_id,
                    TOKEN_MAX_RETRY_COUNT,
                )
                _move_task(task_id, "backlog")
                _clear_retry_count(task_id)
                _clear_resume_at(task_id)
                _send_notification(task, event="retry_exhausted")
                continue

            # 5. Ready to resume
            resume_local = resume_at.astimezone().strftime("%I:%M %p")
            logger.info(
                "Task %s resume_at %s reached — ready for retry (#%d)",
                task_id,
                resume_local,
                retry_count + 1,
            )
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
                row = task_conn.execute("SELECT status FROM kanban_tasks WHERE id = ?", (tid,)).fetchone()
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
        print(f"  Kanban: {len(_running)} task(s) executing in claude, waiting...")
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
            prompt_path = PROMPT_DIR / f"{task['id']}.md"
            if not prompt_path.exists():
                prompt_path = _write_prompt_file(task)
            else:
                prompt_path = str(prompt_path)
            _dispatch_to_claude(task, prompt_path)
            if task["id"] in _running:
                _move_task(task["id"], "in_progress")
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
            else:
                print(f"  Kanban: token retry dispatch failed for {task['id']}")
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
        # Write prompt file first (low risk)
        prompt_path = _write_prompt_file(task)

        # Dispatch to claude CLI — only move to in_progress AFTER
        # subprocess is confirmed running, so tasks don't get stuck
        # in in_progress when dispatch fails.
        _dispatch_to_claude(task, prompt_path)

        if task["id"] in _running:
            # Subprocess launched successfully — now move to in_progress
            _move_task(task["id"], "in_progress")
            _send_notification(task)
            processed.append(
                {
                    "id": task["id"],
                    "title": task["title"],
                    "prompt_file": prompt_path,
                }
            )
            print(f"  Kanban: {task['id']} '{task['title']}' -> in_progress -> dispatched")
        else:
            # Dispatch failed — leave task in backlog, clean up prompt
            errors += 1
            prompt_file = Path(prompt_path)
            if prompt_file.exists():
                prompt_file.unlink()
            print(f"  Kanban: {task['id']} dispatch failed — staying in backlog")
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
