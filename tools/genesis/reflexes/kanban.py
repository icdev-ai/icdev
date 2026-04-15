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
MAX_EXECUTION_SECONDS = 900  # 15 minutes — guard-6: lowered from 1800

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
# Snapshot of main's HEAD SHA captured at dispatch time for each task.
# Verification uses this as the baseline (not current main) so agent commits
# stay visible even if main advances between dispatch and verification.
_dispatch_main_heads: Dict[str, str] = {}


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
    """Merge the kanban task branch into the parent branch before cleanup
    so dependent tasks see each other's commits.

    Strategy (in order):
      1. Fast-forward merge (``--ff-only``) — cheapest, no merge commit.
      2. If ff fails because main diverged, rebase the branch onto main
         and retry ff.  This handles the common case where the scheduler
         or another session committed to main while the task was running.
      3. If the working tree is dirty (uncommitted edits on main), stash
         before checkout and pop after merge so dirty files never block.

    Returns True if merge succeeded (or branch had no commits to merge),
    False on unrecoverable conflict.  On failure the branch is PRESERVED
    (not deleted) so the user can merge manually.
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

    # 3) Stash dirty working tree if needed
    stashed = False
    try:
        dirty = _sp.run(
            ["git", "status", "--porcelain"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if dirty.stdout.strip():
            stash = _sp.run(
                ["git", "stash", "push", "-m", f"kanban-merge-{task_id}"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if stash.returncode == 0 and "No local changes" not in stash.stdout:
                stashed = True
                logger.info("Stashed dirty working tree for merge of %s", task_id)
    except Exception:
        pass  # Best-effort — proceed anyway

    def _restore():
        """Restore original branch and pop stash."""
        if cur_branch != "main":
            _sp.run(
                ["git", "checkout", cur_branch],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
        if stashed:
            _sp.run(
                ["git", "stash", "pop"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )

    def _push_main():
        """Push merged main to origin. Called only after full validation passed.

        The stop hook no longer pushes kanban branches — this is the ONLY
        point where validated work reaches origin/main.
        """
        try:
            push = _sp.run(
                ["git", "push", "origin", "main"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if push.returncode == 0:
                logger.info("Pushed main to origin after merging %s", task_id)
            else:
                logger.warning(
                    "Push main failed after merging %s: %s",
                    task_id, push.stderr[:200],
                )
        except Exception as exc:
            logger.warning("Push main error for %s: %s", task_id, exc)

    # 4) Checkout main and attempt fast-forward merge
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
            _restore()
            return False

        merge = _sp.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if merge.returncode == 0:
            logger.info(
                "Merged kanban/%s to main (fast-forward, %d commits)",
                task_id,
                len(result.stdout.strip().splitlines()),
            )
            _push_main()
            _restore()
            return True

        # 5) FF failed — try rebase-then-ff
        logger.info(
            "FF merge failed for %s, attempting rebase onto main", task_id
        )
        rebase = _sp.run(
            ["git", "rebase", "main", branch_name],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if rebase.returncode != 0:
            # Rebase conflict — abort and preserve branch
            _sp.run(
                ["git", "rebase", "--abort"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.warning(
                "Rebase conflict for %s: %s — branch preserved",
                task_id,
                rebase.stderr[:200],
            )
            # Rebase leaves us on the branch — go back to main
            _sp.run(
                ["git", "checkout", "main"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            _restore()
            return False

        # Rebase succeeded — now on the rebased branch, switch to main and ff
        _sp.run(
            ["git", "checkout", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        merge2 = _sp.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if merge2.returncode == 0:
            logger.info(
                "Merged kanban/%s to main (rebase + fast-forward)", task_id
            )
            _push_main()
            _restore()
            return True

        logger.warning(
            "Post-rebase FF merge still failed for %s: %s — branch preserved",
            task_id,
            merge2.stderr[:200],
        )
        _restore()
        return False
    except Exception as exc:
        logger.warning("Merge to main failed for %s: %s", task_id, exc)
        _restore()
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
        # Native task dependency gating — a task with a non-NULL
        # depends_on_task_id is invisible to the listener until its
        # dependency has been marked `done`. This replaces the
        # park-in-scheduled workaround previously handled by
        # tools/awareness/promote_next_phase.py. Idempotent and
        # backward compatible: rows with NULL depends_on_task_id behave
        # exactly as before.
        dep_clause = (
            "(kt.depends_on_task_id IS NULL "
            " OR EXISTS (SELECT 1 FROM kanban_tasks dep "
            "            WHERE dep.id = kt.depends_on_task_id "
            "              AND dep.status = 'done'))"
        )

        # Always pick up scheduled-and-due tasks
        scheduled = conn.execute(
            "SELECT kt.* FROM kanban_tasks kt "
            "WHERE kt.status = 'scheduled' "
            "  AND kt.scheduled_at IS NOT NULL "
            "  AND kt.scheduled_at <= datetime('now') "
            f"  AND {dep_clause} "  # nosec B608 -- internal constant
            "ORDER BY "
            "CASE kt.priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "kt.created_at ASC"
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
            "SELECT kt.* FROM kanban_tasks kt "
            "WHERE kt.status = 'backlog' "
            "  AND (kt.updated_at IS NULL "
            "       OR kt.updated_at <= datetime('now', '-10 minutes')) "
            f"  AND {dep_clause} "  # nosec B608 -- internal constant
            "ORDER BY "
            "CASE kt.priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "kt.created_at ASC "
            "LIMIT ?",
            (slots,),
        ).fetchall()
        result.extend(dict(r) for r in backlog)

        # Decompose batch tasks into individual children before returning
        # (guard-3). Batch cards have 96-100% phantom completion rate, so
        # we never dispatch them directly.
        result = _decompose_batch_tasks(result, conn)

        return result
    finally:
        conn.close()


def _decompose_batch_tasks(tasks: list, conn: Any) -> list:
    """Detect and decompose batch tasks into individual sub-tasks.

    A batch task is identified by:
    - Title starting with "[Batch]"
    - Description containing "Subjects:" followed by a list

    For each batch task found:
    1. Parse subjects from the description
    2. Create a new kanban_task for each subject (status=backlog)
    3. Mark the parent batch task as "decomposed" (non-dispatchable)
    4. Remove the batch task from the returned list — dispatch the children instead

    The children inherit priority, task_type, source_prediction_id from the parent.
    Returns a new list with batch parents replaced by their children.
    """
    import uuid as _uuid

    result: list = []
    for task in tasks:
        title = (task.get("title") or "").strip()
        description = (task.get("description") or "").strip()

        # Not a batch — keep as-is
        if not title.startswith("[Batch]"):
            result.append(task)
            continue

        # Parse "Subjects:" block from description
        subjects: list[str] = []
        if "Subjects:" in description:
            after = description.split("Subjects:", 1)[1]
            for line in after.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("-"):
                    line = line[1:].strip()
                if line.startswith("*"):
                    line = line[1:].strip()
                # Stop at next section header
                if line.endswith(":") and not line.startswith("/"):
                    break
                # Only accept identifier-like subjects
                if line and len(line) < 200:
                    subjects.append(line)

        if not subjects:
            # Batch card with no parseable subjects — mark decomposed
            # but don't dispatch (prevents phantom completion)
            try:
                conn.execute(
                    "UPDATE kanban_tasks SET status = ?, updated_at = ? WHERE id = ?",
                    ("decomposed", _utcnow_iso(), task["id"]),
                )
                conn.commit()
                print(
                    f"  Kanban: batch {task['id']} has no parseable subjects — "
                    f"marked 'decomposed' (not dispatching)"
                )
            except Exception:
                pass
            continue

        # Extract gap rule from title if present: "[Batch] rule_name: ..."
        rule = ""
        m = re.search(r"\[Batch\]\s*(\w+):", title)
        if m:
            rule = m.group(1)

        parent_priority = task.get("priority") or "medium"
        parent_type = task.get("task_type") or "chore"
        source_pred = task.get("source_prediction_id")

        created_children: list = []
        for subj in subjects:
            child_id = f"task-{_uuid.uuid4().hex[:10]}"
            child_title = f"{rule} gap: {subj}" if rule else f"Batch child: {subj}"
            child_desc = (
                f"AUTO-DECOMPOSED from batch task {task['id']}\n"
                f"Rule: {rule or 'unknown'}\n"
                f"Subject: {subj}\n"
                f"Parent: {task.get('title', '')}"
            )
            now = _utcnow_iso()
            try:
                conn.execute(
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, "
                    " executor_type, source_prediction_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        child_id, child_title, child_desc, parent_type,
                        parent_priority, "backlog", "claude_cli",
                        source_pred, now, now,
                    ),
                )
                child_task = {
                    **task,
                    "id": child_id,
                    "title": child_title,
                    "description": child_desc,
                    "status": "backlog",
                    "source_prediction_id": source_pred,
                }
                created_children.append(child_task)
            except Exception as exc:
                print(f"  Kanban: failed to create child for '{subj}': {exc}")

        # Mark parent batch as decomposed (never dispatch)
        try:
            conn.execute(
                "UPDATE kanban_tasks SET status = ?, updated_at = ? WHERE id = ?",
                ("decomposed", _utcnow_iso(), task["id"]),
            )
            conn.commit()
            print(
                f"  Kanban: decomposed batch {task['id']} into "
                f"{len(created_children)} children ({rule or 'unknown'} rule)"
            )
        except Exception as exc:
            print(f"  Kanban: failed to mark batch decomposed: {exc}")

        # Add children to the dispatch queue (up to MAX_AUTO_PROMOTE)
        result.extend(created_children[:MAX_AUTO_PROMOTE])

    return result


MAX_FAILURES_BEFORE_DECOMPOSITION = 3


def _record_failure_and_maybe_flag(task_id: str, reason: str) -> str:
    """guard-18: Increment failure_count; flag for decomposition after N fails.

    When a task fails verification, we increment its failure_count. If the
    count reaches MAX_FAILURES_BEFORE_DECOMPOSITION (default 3), the task is
    moved to 'needs_decomposition' status instead of plain 'backlog'. This
    signals that the task is probably too big for a single Claude CLI session
    and should be split into smaller sub-tasks (either by a human reviewer or
    by a future LLM-powered decomposer).

    Returns the new status to move the task to: 'needs_decomposition' or 'backlog'.
    """
    now = _utcnow_iso()
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT failure_count FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            prev = 0
            if row:
                prev_val = dict(row).get("failure_count")
                prev = int(prev_val) if prev_val is not None else 0
            new_count = prev + 1

            reason_short = (reason or "")[:500]
            conn.execute(
                "UPDATE kanban_tasks SET failure_count = ?, "
                "last_failure_reason = ?, last_failure_at = ? WHERE id = ?",
                (new_count, reason_short, now, task_id),
            )
            conn.commit()

            if new_count >= MAX_FAILURES_BEFORE_DECOMPOSITION:
                logger.warning(
                    "Task %s failed %d times — flagging for decomposition",
                    task_id, new_count,
                )
                # Notify via Telegram so someone can decompose it.
                # Skip for test tasks (id starts with 'test-') and when
                # PYTEST_CURRENT_TEST env var is present, to avoid spam.
                import os as _os
                _is_test = (
                    task_id.startswith("test-") or
                    _os.environ.get("PYTEST_CURRENT_TEST") or
                    _os.environ.get("ICDEV_SUPPRESS_NOTIFICATIONS") == "1"
                )
                if not _is_test:
                    try:
                        from tools.notifications.adapters.telegram import send as tg_send
                        tg_send(
                            f"DECOMPOSITION NEEDED: {task_id[:24]}",
                            f"Task failed {new_count} verification attempts. "
                            f"It is likely too big for one Claude CLI session. "
                            f"Please split it into smaller sub-tasks.\n"
                            f"Latest reason: {reason_short[:200]}",
                            severity="warning",
                        )
                    except Exception:
                        pass
                return "needs_decomposition"
            return "backlog"
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("failure-count tracking failed for %s: %s", task_id, exc)
        return "backlog"


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


def _fetch_verification_details(task_id: str) -> Dict[str, Any]:
    """Load the most recent kanban_verifications row for a task.

    Returns dict of validation gate outcomes (codelens/coherence/e2e/companion)
    plus the reason text. Used to enrich Telegram notifications so users can
    see at a glance which gates passed or failed.
    """
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT result, reason, codelens_passed, ruff_issues, "
                "bandit_issues, coherence_passed, e2e_ran, e2e_passed, "
                "companion_synced, claimed_paths, existing_paths, git_commits "
                "FROM kanban_verifications "
                "WHERE task_id = ? ORDER BY verified_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("kanban: fetch verification details skipped: %s", exc)
        return {}


def _format_gate_status(val: Any) -> str:
    """Render a gate metric as a check/cross/dash."""
    if val is None or val == "":
        return "-"
    if val in (1, True, "1", "true", "True"):
        return "[OK]"
    if val in (0, False, "0", "false", "False"):
        return "[FAIL]"
    return str(val)


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

    # guard-19: enrich done/failed notifications with validation gate results
    # so users see at a glance which checks passed or failed.
    if event in ("done", "failed"):
        details = _fetch_verification_details(task.get("id", ""))
        if details:
            codelens = _format_gate_status(details.get("codelens_passed"))
            coherence = _format_gate_status(details.get("coherence_passed"))
            companion = _format_gate_status(details.get("companion_synced"))
            if details.get("e2e_ran"):
                e2e = _format_gate_status(details.get("e2e_passed"))
            else:
                e2e = "skipped"
            ruff = details.get("ruff_issues") or 0
            bandit = details.get("bandit_issues") or 0
            commits = details.get("git_commits") or 0
            result_enum = details.get("result") or "unknown"
            reason_text = (details.get("reason") or "")[:300]

            gate_lines = [
                "",
                "Validation gates:",
                f"  CodeLens:  {codelens}  (ruff={ruff}, bandit={bandit})",
                f"  Coherence: {coherence}",
                f"  E2E:       {e2e}",
                f"  Companion: {companion}",
                f"  Result:    {result_enum}  (commits={commits})",
            ]
            if event == "failed" and reason_text:
                gate_lines.append(f"  Reason:    {reason_text}")
            body = body + "\n" + "\n".join(gate_lines)

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


_FAILURE_COACHING = {
    "no_commits": (
        "Previous run produced NO git commits. The verification requires "
        "committed file changes. Make sure to actually edit files and the "
        "stop hook will commit them. If you believe the task requires no "
        "changes, say so explicitly in your output."
    ),
    "phantom_paths": (
        "Previous run mentioned file paths in output that DID NOT EXIST on "
        "disk. Only reference files you actually created or modified. Do "
        "not hallucinate paths. Use Read to confirm before mentioning a file."
    ),
    "ruff_issues": (
        "Previous run left ruff lint errors (F401 unused imports, etc). "
        "Before finishing, run: python -m ruff check --fix <modified_files> "
        "and remove any remaining unused imports/variables."
    ),
    "bandit_security": (
        "Previous run introduced a medium+ security issue (bandit). Avoid: "
        "eval/exec, subprocess shell=True, unvalidated paths, hardcoded "
        "credentials. If a pattern is safe in context, add a '# nosec B###' "
        "comment with justification."
    ),
    "coherence_broken": (
        "Previous run broke coherence (likely a new tool missing from "
        "tools/manifest.md, or a ruff issue). Before finishing: add any "
        "new tools to tools/manifest.md AND run ruff check --fix on all "
        "modified .py files."
    ),
    "stale_baseline": (
        "Previous run was on a stale branch baseline. Rebase onto latest "
        "main before making changes: git rebase main. Then re-apply your "
        "work on top of current code."
    ),
    "e2e_regression": (
        "Previous run passed code checks but broke an E2E test. Verify UI/API "
        "routes still work after your change. Run tests/e2e_kanban_depends_on.py "
        "locally before finishing."
    ),
}


def _get_retry_coaching(task_id: str) -> str:
    """Build a coaching preamble for the next run based on last failure.

    Reads failure_count + last_failure_reason from kanban_tasks, classifies
    the failure, and returns advice text to prepend to the agent prompt.
    Empty string on first run or if info unavailable.
    """
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT failure_count, last_failure_reason, last_failure_at "
                "FROM kanban_tasks WHERE id = ?", (task_id,),
            ).fetchone()
            if not row:
                return ""
            d = dict(row)
            count = d.get("failure_count") or 0
            reason = (d.get("last_failure_reason") or "").strip()
            if not count or not reason:
                return ""
        finally:
            conn.close()
    except Exception:
        return ""

    try:
        from tools.workflow.auto_remediate import classify_failure
        failure_type = classify_failure(reason)
    except Exception:
        failure_type = "unknown"

    coaching = _FAILURE_COACHING.get(failure_type, "")
    preamble = (
        "IMPORTANT — THIS IS RETRY ATTEMPT #" + str(count + 1) + ".\n"
        "The previous attempt failed verification. Do NOT repeat the same\n"
        "mistake. Here is what went wrong and how to avoid it:\n\n"
        f"  Failure type: {failure_type}\n"
        f"  Reason:       {reason[:300]}\n"
    )
    if coaching:
        preamble += f"\nCoaching: {coaching}\n"
    preamble += (
        "\nAdditional requirements for this retry:\n"
        "  - Actually modify files on disk; don't just describe changes.\n"
        "  - Keep the scope small and focused on the task title.\n"
        "  - If the task seems too large for one session, state so in your\n"
        "    output and make whatever partial progress you can — the\n"
        "    scheduler will flag it for decomposition after 3 failures.\n"
        "\n---\n\n"
    )
    return preamble


def _build_instruction(task_id: str, title: str, prompt_text: str, prompt_path: str) -> str:
    """Compose the full instruction text used by both executors.

    Injects retry coaching if the task has prior failures (guard-22), so the
    agent knows what went wrong last time and how to avoid repeating it.
    """
    coaching = _get_retry_coaching(task_id)
    return (
        f"{coaching}{prompt_text}\n\n"
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
        # guard-23: propagate dispatch_source via env so the stop hook can
        # tag this session's commits as 'genesis_scheduler' rather than
        # 'claude_interactive'. Also tag the kanban task row immediately.
        import os as _os
        env = _os.environ.copy()
        env["ICDEV_DISPATCH_SOURCE"] = "genesis_scheduler"
        env["ICDEV_DISPATCH_TASK_ID"] = task_id

        _tag_task_source(task_id, "genesis_scheduler")

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
            env=env,
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


def _pre_dispatch_check(task: dict) -> Tuple[bool, str]:
    """Check if a gap task is already resolved BEFORE dispatching Claude.

    For common false-positive gap types (tool_not_in_manifest,
    route_not_listed, etc.) we can validate the expected state DIRECTLY
    without running the agent. If the state is already as desired, the
    task is auto-completed — no tokens spent, no worktree created.

    Returns (already_resolved: bool, reason: str). If True, the caller
    should mark the task done and skip dispatch entirely.
    """
    title = (task.get("title") or "")
    description = (task.get("description") or "")

    # tool_not_in_manifest gap: is the tool already in the manifest?
    tool_match = re.search(r"tool_not_in_manifest[^:]*:\s*(tools/[A-Za-z0-9_/\-]+\.py)", title)
    if not tool_match:
        tool_match = re.search(r"(tools/[A-Za-z0-9_/\-]+\.py)", description)
    if tool_match and ("tool_not_in_manifest" in title or "tool_not_in_manifest" in description):
        tool_path = tool_match.group(1)
        try:
            manifest_text = (BASE_DIR / "tools" / "manifest.md").read_text(encoding="utf-8")
            if tool_path in manifest_text:
                return True, (
                    f"Pre-dispatch check: {tool_path} is already in tools/manifest.md "
                    f"(false-positive gap)"
                )
            # Also search shard files under tools/manifest/
            manifest_dir = BASE_DIR / "tools" / "manifest"
            if manifest_dir.is_dir():
                for shard in manifest_dir.glob("*.md"):
                    try:
                        if tool_path in shard.read_text(encoding="utf-8"):
                            return True, (
                                f"Pre-dispatch check: {tool_path} is already in "
                                f"tools/manifest/{shard.name} (false-positive gap)"
                            )
                    except Exception:
                        pass
        except Exception:
            pass

    # route_not_listed gap: is the route already in start.md Pages line?
    route_match = re.search(r"route_not_listed[^:]*:\s*(/[A-Za-z0-9_<>/\-]+)", title)
    if route_match:
        route = route_match.group(1)
        # API routes don't belong in Pages list — treat as resolved
        if route.startswith("/api/"):
            return True, f"Pre-dispatch check: {route} is an API route (N/A for Pages list)"
        try:
            start_md = (BASE_DIR / ".claude" / "commands" / "start.md").read_text(encoding="utf-8")
            if route in start_md:
                return True, (
                    f"Pre-dispatch check: {route} is already in "
                    f".claude/commands/start.md (false-positive gap)"
                )
        except Exception:
            pass

    return False, ""


def _dispatch_to_claude(task: dict, prompt_path: str):
    """Dispatch a task to the appropriate executor.

    Picks ClaudeCodeTaskExecutor when the `claude` CLI is available, otherwise
    falls back to LocalPythonTaskExecutor (LLMRouter-backed). The function
    name is preserved for backwards compatibility with existing call sites.

    Creates a git worktree for isolation so parallel tasks don't collide.
    Falls back to BASE_DIR if worktree creation fails.

    FAST-PATH: _pre_dispatch_check runs first. If the task is a
    false-positive gap (tool already in manifest, route already in start.md,
    etc.), we mark it done immediately and skip Claude entirely — saving
    tokens and preventing false-negative "no commits" rejections.
    """
    task_id = task["id"]
    title = task.get("title", "Untitled")

    # Fast-path: auto-complete false-positive gaps without dispatching.
    already_resolved, resolution_reason = _pre_dispatch_check(task)
    if already_resolved:
        logger.info("kanban: %s auto-resolved pre-dispatch: %s", task_id, resolution_reason)
        _write_verification_log(task_id, True, f"AUTO-RESOLVED (pre-dispatch): {resolution_reason}")
        try:
            _move_task(task_id, "done")
        except Exception:
            pass
        _send_notification({"id": task_id, "title": title,
                            "task_type": task.get("task_type", "chore"),
                            "priority": task.get("priority", "medium")},
                           event="done")
        return

    prompt_text = Path(prompt_path).read_text(encoding="utf-8")

    # Create isolated worktree for this task
    worktree_path = _create_worktree(task_id)
    work_dir = worktree_path if worktree_path else str(BASE_DIR)
    if worktree_path:
        _worktrees[task_id] = worktree_path
        print(f"  Kanban: using worktree {worktree_path} for {task_id}")
    else:
        print(f"  Kanban: worktree unavailable, using BASE_DIR for {task_id}")

    # FIX: Capture main HEAD at dispatch time. Verification uses this as the
    # baseline so that agent commits are visible even if main advances
    # (another task merges, auto-commit runs, etc.). Previously verification
    # used `git log main..kanban/branch` with CURRENT main, which went empty
    # once the agent's work was absorbed into main, causing false "no commits"
    # rejection.
    try:
        import subprocess as _sp
        head_proc = _sp.run(
            ["git", "rev-parse", "main"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=10,
        )
        if head_proc.returncode == 0:
            _dispatch_main_heads[task_id] = head_proc.stdout.strip()
    except Exception as exc:
        logger.debug("kanban: failed to capture main HEAD for %s: %s", task_id, exc)

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


# ---------------------------------------------------------------------------
# Git-first fast-path helpers (memory: feedback_kanban_vv_policy.md)
# ---------------------------------------------------------------------------

# Destructive / externally-visible / shared-infra task_types and description
# keywords that MUST NOT use the git-first shortcut. Dangerous tasks run
# every downstream verifier guard for full audit trail.
_DANGEROUS_TASK_TYPES = frozenset({"deploy", "delete", "destructive"})
_DANGEROUS_DESCRIPTION_KEYWORDS = (
    "drop table", "force-push", "force push", "push --force",
    "rm -rf", "git reset --hard", "public release",
    "marketplace publish", "DELETE FROM audit", "UPDATE audit",
    "k8s deploy", "kubectl apply", "dns change", "cert rotation",
    "iam policy",
)


def _is_dangerous_task(task_id: str) -> bool:
    """Return True if the task is destructive, external-visible, or shared-infra.

    Dangerous tasks skip the git-first shortcut so every downstream guard
    runs. Lookup uses task_type + description keyword scan.
    """
    try:
        _c = get_connection()
        row = _c.execute(
            "SELECT task_type, description FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        _c.close()
    except Exception:
        return False
    if not row:
        return False
    row = dict(row)
    task_type = (row.get("task_type") or "").lower()
    if task_type in _DANGEROUS_TASK_TYPES:
        return True
    desc = (row.get("description") or "").lower()
    return any(kw.lower() in desc for kw in _DANGEROUS_DESCRIPTION_KEYWORDS)


def _git_worktree_has_real_changes(task_id: str) -> Tuple[bool, str]:
    """Fast-path: did the agent actually touch the filesystem / commit work?

    Checks in order (any positive → True):
      1. ``git log <dispatch_baseline>..kanban/<task_id> --name-only`` —
         real commits on the task branch since dispatch, with at least
         one changed file path.
      2. ``git status --porcelain`` in the worktree — staged or unstaged
         file changes.
      3. ``git log <dispatch_baseline>..HEAD --name-only`` on main — the
         scheduler's own auto-merge may have landed the work before the
         verifier ran. If main advanced AND the worktree is clean, the
         agent's commits are there already.

    Returns ``(ok, reason)``. On git failure or no evidence, ``(False, "")``.
    """
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    work_dir = _worktrees.get(task_id) or str(BASE_DIR)
    dispatch_baseline = _dispatch_main_heads.get(task_id, None)

    # 1. branch commits with file changes since dispatch
    if dispatch_baseline:
        try:
            r = _sp.run(
                ["git", "log", f"{dispatch_baseline}..{branch_name}",
                 "--name-only", "--pretty=format:"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=10,
            )
            files = [
                ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()
            ]
            if files:
                preview = ", ".join(sorted(set(files))[:3])
                return True, (
                    f"{len(set(files))} file(s) changed on {branch_name} "
                    f"(e.g. {preview})"
                )
        except Exception:
            pass

    # 2. uncommitted changes in the worktree
    try:
        r = _sp.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=work_dir, timeout=10,
        )
        porcelain = [
            ln for ln in (r.stdout or "").splitlines() if ln.strip()
        ]
        if porcelain:
            return True, f"{len(porcelain)} uncommitted change(s) in worktree"
    except Exception:
        pass

    # 3. main advanced since dispatch AND worktree is clean → likely merged
    if dispatch_baseline:
        try:
            r = _sp.run(
                ["git", "log", f"{dispatch_baseline}..HEAD",
                 "--name-only", "--pretty=format:"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=10,
            )
            main_files = [
                ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()
            ]
            if main_files:
                dirty = _sp.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    cwd=work_dir, timeout=10,
                )
                if not (dirty.stdout or "").strip():
                    preview = ", ".join(sorted(set(main_files))[:3])
                    return True, (
                        f"main advanced {len(set(main_files))} file(s) since dispatch; "
                        f"worktree clean (likely merged) — e.g. {preview}"
                    )
        except Exception:
            pass

    return False, ""


def _run_verify_checks(task_id, claude_output):
    """Inner check implementation — called only by _verify_task_completed.

    Checks (order matters; early returns on success/failure):
    0. **Git-first fast-path** (memory: feedback_kanban_vv_policy.md):
       if the worktree branch has commits or file changes vs dispatch
       baseline, trust the filesystem truth and return verified=True.
       Skipped for dangerous task types (see _is_dangerous_task) so
       destructive ops still go through every downstream guard.
    1. Claude output must be substantial (>200 chars)
    2. No obvious failure indicators in output
    3. Output must contain evidence of actual file changes (keywords)
    4a. **Zero-path guard**: if task description mentions file creation
        but the output claims 0 paths, FAIL.
    4b. **Phantom-completion guard (OPT-76)**: if output claims specific
        file paths, at least some must actually exist on disk, AND no
        more than 50% may be missing (threshold lowered from 80%).
    5. Git has new commits on the task's WORKTREE branch.
       Dirty-working-tree fallback removed — uncommitted changes alone
       are NOT evidence of completion.

    Returns: (verified: bool, reason: str)
    """
    # Check 0 — GIT-FIRST FAST-PATH (memory: feedback_kanban_vv_policy.md).
    # Rationale: filesystem truth beats stdout heuristics. E3 (2026-04-15)
    # shipped migration 023_sharepoint correctly but the text heuristic
    # returned phantom, blocking the whole E-F-G-H-I chain. If the
    # worktree has real file changes or commits, trust that and return
    # verified=True without running Check 4's phantom text heuristic.
    #
    # Dangerous tasks (deploy/delete/destructive ops) bypass this shortcut
    # — they still run every downstream guard so the audit trail is full.
    if not _is_dangerous_task(task_id):
        _git_ok, _git_reason = _git_worktree_has_real_changes(task_id)
        if _git_ok:
            return True, f"Verified (git-first): {_git_reason}"

    # Check 1: Claude output must be substantial
    if not claude_output or len(claude_output) < 200:
        return False, "Output too short — likely no work done"

    # Check 2: Look for failure indicators (scan first 1000 chars — the
    # agent usually declares failure up front if it's going to)
    # HARD fail markers: agent explicitly could not do the work → reject.
    hard_fail_markers = [
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
    ]
    # SOFT markers: agent claims nothing to do. This may be a FALSE POSITIVE
    # gap (e.g., tool already in manifest, route already listed). Don't fail
    # here — let task-specific verification (_verify_task_specific) decide
    # by checking the actual state. If the state is as expected, task is
    # genuinely complete. If not, the no-commits check downstream catches it.
    soft_nochange_markers = [
        "there is nothing to",
        "no changes",
        "already up to date",
        "already in the manifest",
        "already documented",
        "already listed",
        "false positive",
        "already exists",
    ]
    output_lower = claude_output.lower()
    for marker in hard_fail_markers:
        if marker.lower() in output_lower[:1000]:
            return False, f"Output contains failure indicator: {marker}"

    # Track soft "no-change" signal — task-specific verification will decide
    # whether this is a legitimate false-positive resolution or a cop-out.
    has_nochange_signal = any(
        m in output_lower[:2000] for m in soft_nochange_markers
    )

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
    if not has_file_evidence and not has_nochange_signal:
        # Only hard-fail for "no evidence" when the agent ALSO didn't claim
        # a legitimate no-change resolution.
        return False, "No evidence of file changes in output"

    # Check 4 — OPT-76 phantom guard: extract every path the agent
    # claims to have touched and verify at least SOME of them exist.
    work_dir_for_check = _worktrees.get(task_id) or str(BASE_DIR)
    claimed_paths = _extract_claimed_file_paths(claude_output)

    # Check 4a: If the task description mentions file creation but the
    # output claims zero paths, the agent likely hallucinated.
    # EXCEPTION: if the agent signaled a legitimate no-change outcome
    # (false-positive gap), skip this check and defer to task-specific
    # verification downstream which will confirm the expected state.
    if not claimed_paths and not has_nochange_signal:
        try:
            _c = get_connection()
            _row = _c.execute(
                "SELECT description FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            _c.close()
            task_desc = (_row["description"] or "").lower() if _row else ""
        except Exception:
            task_desc = ""
        _creation_kws = ["creat", "generat", "add ", "implement", "write ", "build "]
        if any(kw in task_desc for kw in _creation_kws):
            return False, (
                "Task description indicates file creation but agent claimed "
                "0 file paths in output — likely phantom completion"
            )

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
        # If 50%+ of claimed paths are missing, treat as phantom completion.
        phantom_ratio = (claimed - existing) / claimed
        if phantom_ratio >= 0.5:
            missing_preview = ", ".join(missing[:3])
            return False, (
                f"PHANTOM COMPLETION: {claimed - existing}/{claimed} claimed paths "
                f"missing ({missing_preview}). "
                f"Ratio {phantom_ratio:.0%} >= 50% threshold — failing."
            )

    # Check 5: Git commit check on the WORKTREE branch (not main).
    # Failures in git commands DO NOT fall through to a lenient pass —
    # that was the original phantom path.
    try:
        import subprocess as _sp

        branch_name = f"kanban/{task_id}"

        # FIX: Use the snapshot of main captured at dispatch time as baseline,
        # so agent commits remain visible even if main has advanced (auto-merge
        # of another task, auto-commit, etc.). Falls back to HEAD..branch for
        # tasks dispatched before this snapshot was recorded.
        dispatch_baseline = _dispatch_main_heads.get(task_id, "HEAD")

        result = _sp.run(
            ["git", "log", f"{dispatch_baseline}..{branch_name}", "--oneline"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            timeout=10,
        )
        worktree_commits = result.stdout.strip()
        if worktree_commits:
            return True, f"Verified: branch has commits since dispatch: {worktree_commits[:100]}"

        # Fallback A: maybe work was already merged to main (stop hook +
        # scheduler merge race). Look for commits on ANY branch since dispatch
        # that touch files in our worktree.
        if dispatch_baseline != "HEAD":
            try:
                r2 = _sp.run(
                    ["git", "log", f"{dispatch_baseline}..HEAD", "--oneline"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=str(BASE_DIR), timeout=10,
                )
                main_advanced = r2.stdout.strip()
                if main_advanced:
                    # Check if the worktree has uncommitted changes — if clean
                    # AND main advanced, the agent's work likely merged already.
                    work_dir_for_check = _worktrees.get(task_id) or str(BASE_DIR)
                    dirty = _sp.run(
                        ["git", "status", "--porcelain"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=work_dir_for_check, timeout=10,
                    )
                    if not dirty.stdout.strip():
                        return True, (
                            f"Verified: main advanced {len(main_advanced.splitlines())} "
                            f"commit(s) since dispatch; worktree is clean (work likely merged)"
                        )
            except Exception:
                pass

        # Fallback B: commits in the last 30 min mentioning this task id
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

        # Fallback C: legitimate no-change case. If the agent claimed
        # "no changes needed" AND _verify_task_specific confirms the
        # expected state (e.g., tool IS in manifest for a
        # tool_not_in_manifest task), accept as completed. False-positive
        # gaps are legitimate completions.
        if has_nochange_signal:
            try:
                specific_ok, specific_reason = _verify_task_specific(task_id)
                if specific_ok:
                    return True, (
                        "Verified: agent reported no changes needed AND "
                        f"task-specific state check passed ({specific_reason[:80]})"
                    )
            except Exception:
                pass

        # No commits on the task branch — uncommitted changes are NOT
        # sufficient evidence of completion (dirty-tree fallback removed).
        return False, (
            "No git commits found on task branch — "
            "agent produced no committed file-level output"
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


def _write_verification_log(task_id: str, verified: bool, reason: str) -> None:
    """Persist verification result to .tmp/kanban/{task_id}.verification.json."""
    try:
        import json as _json
        log_dir = BASE_DIR / ".tmp" / "kanban"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{task_id}.verification.json"
        log_path.write_text(
            _json.dumps(
                {
                    "task_id": task_id,
                    "verified": verified,
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(
            "kanban: failed to write verification log for %s: %s", task_id, exc
        )
    # Also write to kanban_verifications table (guard-5) for dashboard visibility
    try:
        import uuid as _uuid
        conn = get_connection()
        verification_id = f"kv-{_uuid.uuid4().hex[:10]}"
        result_enum = "passed" if verified else "failed"
        if "PHANTOM" in (reason or "").upper():
            result_enum = "phantom"

        # guard-23: read dispatch_source from task row (set at dispatch time)
        source_row = conn.execute(
            "SELECT dispatch_source FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        dispatch_source = (
            dict(source_row).get("dispatch_source") if source_row else None
        ) or "genesis_scheduler"  # scheduler-invoked verifications are scheduler by default

        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, reason, dispatch_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                verification_id, task_id,
                datetime.now(timezone.utc).isoformat(),
                result_enum, reason, dispatch_source,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        # Don't fail verification just because audit log is missing
        logger.debug("kanban: kanban_verifications write skipped: %s", exc)


def _tag_task_source(task_id: str, source: str) -> None:
    """Set the dispatch_source on a kanban_tasks row (guard-23)."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE kanban_tasks SET dispatch_source = ? WHERE id = ?",
                (source, task_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("kanban: tag dispatch_source skipped for %s: %s", task_id, exc)


def _verify_task_specific(task_id: str) -> Tuple[bool, str]:
    """Task-type-specific verification based on description keywords.

    Parses the task description and runs targeted checks:
    - "manifest" / "tool_not_in_manifest" → grep tools/manifest.md for the tool path
    - "route" / "page" / "start.md Pages" → grep start.md Pages line
    - "table" / "schema" / "migration" → query DB for table existence
    - "template" / ".html" → check template file exists
    - "[Batch]" title → reject — batch cards must be decomposed first

    Returns (True, reason) if specific checks pass or don't apply.
    Returns (False, reason) if a targeted check fails.
    """
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT title, description FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        conn.close()
        if not row:
            return True, "Task row not found — skipping specific checks"
    except Exception as exc:
        return True, f"DB read failed ({exc}) — skipping specific checks"

    title = (row["title"] or "").strip()
    description = (row["description"] or "").strip()
    desc_lower = description.lower()

    # Batch card — reject if not decomposed (guard-3 provides decomposition)
    if title.startswith("[Batch]"):
        return False, (
            "Batch cards must be decomposed into individual tasks before dispatch "
            "(see guard-3 auto-decompose). Reject to prevent phantom completion."
        )

    # Manifest check: task mentions adding a tool to the manifest
    if "tool_not_in_manifest" in desc_lower or (
        "manifest" in desc_lower and "tools/" in description
    ):
        tool_match = re.search(r"(tools/[A-Za-z0-9_/\-]+\.py)", description)
        if tool_match:
            tool_path = tool_match.group(1)
            try:
                # Read the manifest from the kanban branch (where the agent
                # committed), NOT main — the agent's manifest update hasn't
                # been merged yet at verification time.
                # Manifest is sharded (2026-04-14): root tools/manifest.md is a
                # thin index; tool entries live in tools/manifest/<topic>.md.
                # Search the kanban branch first (agent's commits), fall back to
                # working-tree shards.
                manifest_text = ""
                try:
                    import subprocess as _sp
                    r = _sp.run(
                        ["git", "ls-tree", "-r", "--name-only",
                         f"kanban/{task_id}", "tools/"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        cwd=str(BASE_DIR), timeout=10,
                    )
                    if r.returncode == 0:
                        shard_files = [
                            f for f in r.stdout.splitlines()
                            if f == "tools/manifest.md"
                            or f.startswith("tools/manifest/")
                        ]
                        chunks = []
                        for sf in shard_files:
                            sr = _sp.run(
                                ["git", "show", f"kanban/{task_id}:{sf}"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                cwd=str(BASE_DIR), timeout=10,
                            )
                            if sr.returncode == 0:
                                chunks.append(sr.stdout)
                        manifest_text = "\n".join(chunks)
                except Exception:
                    pass
                if not manifest_text:
                    parts = [
                        (BASE_DIR / "tools" / "manifest.md").read_text(
                            encoding="utf-8"
                        )
                    ]
                    shard_dir = BASE_DIR / "tools" / "manifest"
                    if shard_dir.is_dir():
                        for shard in shard_dir.glob("*.md"):
                            try:
                                parts.append(shard.read_text(encoding="utf-8"))
                            except Exception:
                                continue
                    manifest_text = "\n".join(parts)
                if tool_path not in manifest_text:
                    return False, (
                        f"SPECIFIC CHECK FAILED: task mentions {tool_path} "
                        f"but it is NOT in tools/manifest.md or any "
                        f"tools/manifest/*.md shard"
                    )
                # Positive signal: tool IS in manifest — task state is as expected
                return True, (
                    f"Task-specific check passed: {tool_path} is present in "
                    f"the manifest (root or shard)"
                )
            except Exception as exc:
                return True, f"Manifest read failed ({exc}) — skipping manifest check"

    # Route/Pages check: task about dashboard routes
    if "route_not_listed" in desc_lower or (
        "route" in desc_lower and "start.md" in desc_lower
    ):
        route_match = re.search(r"route_not_listed gap: (/[A-Za-z0-9_<>/\-]+)", title)
        if not route_match:
            route_match = re.search(r"gap: (/[A-Za-z0-9_<>/\-]+)", title)
        if route_match:
            route = route_match.group(1)
            # API routes don't need to be in Pages list
            if not route.startswith("/api/"):
                try:
                    start_md = (BASE_DIR / ".claude" / "commands" / "start.md").read_text(
                        encoding="utf-8"
                    )
                    # Look for the route in the Pages: line
                    pages_match = re.search(r"Pages:\s*(.+?)(?=\n-|\n\n|$)", start_md, re.DOTALL)
                    pages_line = pages_match.group(1) if pages_match else ""
                    if route not in pages_line:
                        return False, (
                            f"SPECIFIC CHECK FAILED: task mentions route {route} "
                            f"but it is NOT in .claude/commands/start.md Pages list"
                        )
                except Exception as exc:
                    return True, f"start.md read failed ({exc}) — skipping route check"

    # Table/schema check: task creates a DB table.
    # Special case: orphan_db_table gap reports describe an *existing*
    # missing CREATE TABLE for a known orphan; the table name lives in the
    # title ("gap: <name>") or "Subject:" / "table:" lines, not after a
    # "create table" verb. Use that, and skip the generic verb-regex which
    # otherwise false-matches the "Evidence:" section header that follows
    # "...without a matching CREATE TABLE\n\nEvidence:".
    table_name = None
    if "orphan_db_table" in desc_lower:
        m = (
            re.search(r"orphan_db_table\s+gap:\s*(\w+)", title, re.IGNORECASE)
            or re.search(r"orphan_db_table\s+on\s+(\w+)", description, re.IGNORECASE)
            or re.search(r"^\s*(?:Subject|table)\s*:\s*(\w+)", description, re.IGNORECASE | re.MULTILINE)
        )
        if m:
            table_name = m.group(1)
        # Skip pg_*, sqlite_*, information_schema.* — system catalogs are
        # never "orphan" tables to create.
        if table_name and re.match(r"^(pg_|sqlite_|information_schema)", table_name, re.IGNORECASE):
            return True, (
                f"Skipped orphan_db_table check: {table_name} is a system catalog"
            )
    else:
        # Anchor to a single line so newlines don't pull in section headers.
        table_match = re.search(
            r"(?:create\s+table|add\s+(?:table|DB\s+table|schema))[ \t]+(\w+)",
            description,
            re.IGNORECASE,
        )
        if table_match:
            table_name = table_match.group(1)
    if table_name:
        # For orphan_db_table fixes, the agent commits a new migration
        # file with CREATE TABLE <name>, but that migration has not been
        # applied to the working-tree DB at validation time. So check for
        # the CREATE TABLE statement in the agent's branch files instead
        # of querying the live DB.
        if "orphan_db_table" in desc_lower:
            import subprocess as _sp
            pattern = rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table_name)}\b"
            found = False
            try:
                r = _sp.run(
                    ["git", "grep", "-l", "-i", "-E", pattern,
                     f"kanban/{task_id}", "--", "tools/db/", "tools/"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    cwd=str(BASE_DIR), timeout=15,
                )
                found = r.returncode == 0 and bool(r.stdout.strip())
            except Exception:
                pass
            if not found:
                # Fall back to working tree (may be main or agent-merged).
                try:
                    r = _sp.run(
                        ["git", "grep", "-l", "-i", "-E", pattern, "--",
                         "tools/db/", "tools/"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        cwd=str(BASE_DIR), timeout=15,
                    )
                    found = r.returncode == 0 and bool(r.stdout.strip())
                except Exception:
                    pass
            if not found:
                return False, (
                    f"SPECIFIC CHECK FAILED: orphan_db_table task for "
                    f"'{table_name}' but no CREATE TABLE statement found "
                    f"in branch kanban/{task_id} or working tree"
                )
            return True, (
                f"Task-specific check passed: CREATE TABLE {table_name} "
                f"found in branch or tree"
            )
        # Non-orphan_db_table path: verify table exists in live DB
        try:
            conn = get_connection()
            _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            if _pg:
                check = conn.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ?",
                    (table_name,),
                ).fetchone()
            else:
                check = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table_name,),
                ).fetchone()
            conn.close()
            if not check:
                return False, (
                    f"SPECIFIC CHECK FAILED: task says create table '{table_name}' "
                    f"but it does NOT exist in the database"
                )
        except Exception as exc:
            return True, f"DB table check failed ({exc}) — skipping"

    # Template check: task creates an HTML template
    template_match = re.search(
        r"(?:create|add)\s+(?:template\s+)?([a-z_]+\.html)", description, re.IGNORECASE
    )
    if template_match:
        template_name = template_match.group(1)
        template_dir = BASE_DIR / "tools" / "dashboard" / "templates"
        matches = list(template_dir.rglob(template_name))
        if not matches:
            return False, (
                f"SPECIFIC CHECK FAILED: task says create template '{template_name}' "
                f"but file does NOT exist under tools/dashboard/templates/"
            )

    return True, "Task-specific checks passed or not applicable"


def _run_post_task_validation(task_id: str) -> Tuple[bool, str, Dict[str, Any]]:
    """guard-7: Run the unified validation suite on the agent's worktree.

    Delegates to tools/workflow/validated_commit.validate_working_tree() so
    kanban and interactive sessions share the exact same pipeline:
      1. CodeLens (py_compile + ruff + bandit)
      2. Coherence (compared to main baseline — pre-existing issues ignored)
      3. E2E (Selenium, if UI files touched AND dashboard running)
      4. Companion sync (best-effort)

    Returns: (passed: bool, reason: str, metrics: dict)
    """
    import subprocess as _sp
    from tools.workflow.validated_commit import validate_working_tree

    # Resolve the task's worktree — validation runs here, not in main.
    work_dir = _worktrees.get(task_id)
    if work_dir and Path(work_dir).exists():
        cwd = str(work_dir)
    else:
        cwd = str(BASE_DIR)

    # Identify files changed by the agent (branch vs main)
    branch_name = f"kanban/{task_id}"
    try:
        result = _sp.run(
            ["git", "diff", "--name-only", f"main...{branch_name}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=15,
        )
        modified = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        modified = []

    return validate_working_tree(
        cwd=cwd,
        modified_files=modified,
        compare_to_main=True,
        run_e2e=True,
        run_companion=True,
    )


def _update_verification_metrics(task_id: str, metrics: Dict[str, Any]) -> None:
    """Update the latest kanban_verifications row for this task with post-task metrics."""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE kanban_verifications SET "
            "codelens_passed = ?, ruff_issues = ?, bandit_issues = ?, "
            "pytest_passed = ?, coherence_passed = ?, "
            "e2e_ran = ?, e2e_passed = ?, companion_synced = ? "
            "WHERE task_id = ? AND id = ("
            "  SELECT id FROM kanban_verifications WHERE task_id = ? "
            "  ORDER BY verified_at DESC LIMIT 1)",
            (
                1 if metrics.get("codelens_passed") else 0 if metrics.get("codelens_passed") is False else None,
                metrics.get("ruff_issues", 0),
                metrics.get("bandit_issues", 0),
                1 if metrics.get("pytest_passed") else 0 if metrics.get("pytest_passed") is False else None,
                1 if metrics.get("coherence_passed") else 0 if metrics.get("coherence_passed") is False else None,
                1 if metrics.get("e2e_ran") else 0,
                1 if metrics.get("e2e_passed") else 0 if metrics.get("e2e_passed") is False else None,
                1 if metrics.get("companion_synced") else 0,
                task_id, task_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("guard-7: metrics update skipped: %s", exc)


def _verify_task_completed(task_id, claude_output):
    """Verify that a task actually produced results before marking done.

    Pipeline:
      1. _run_verify_checks — generic guards (OPT-76 phantom detection, commits)
      2. _verify_task_specific — manifest/route/table/template checks
      3. _run_post_task_validation — CodeLens + Coherence + E2E + companion
      4. (guard-21) On failure: attempt auto-remediation ONCE. If it succeeds,
         re-run the full validation pipeline. If still failing, or the
         failure type is not auto-remediable, return to caller so the task
         goes to backlog.

    Logs every result to kanban_verifications table.

    Returns: (verified: bool, reason: str)
    """
    verified, reason, metrics = _run_full_verification(task_id, claude_output)

    # guard-21: try to auto-remediate common, safe failures before giving up.
    # Only one attempt — if it doesn't work, backlog is the answer.
    # Skip remediation for PHANTOM COMPLETION — hallucinated output has nothing
    # on disk to fix; running git commands would be pointless noise.
    _is_phantom = "PHANTOM COMPLETION" in reason
    if not verified and not _is_phantom:
        try:
            from tools.workflow.auto_remediate import attempt_remediation
            work_dir = _worktrees.get(task_id) or str(BASE_DIR)
            # Get the list of files the agent touched (for targeted ruff/manifest)
            import subprocess as _sp
            diff = _sp.run(
                ["git", "diff", "--name-only", f"main...kanban/{task_id}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=15,
            )
            modified_files = [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]

            remediated, rem_msg, rem_info = attempt_remediation(
                cwd=work_dir, task_id=task_id,
                reason=reason, metrics=metrics,
                modified_files=modified_files,
            )

            if remediated:
                # Re-run full verification after fix was applied
                logger.info(
                    "guard-21: remediation succeeded for %s (%s) — re-verifying",
                    task_id, rem_info.get("failure_type", "?"),
                )
                verified, reason, metrics = _run_full_verification(task_id, claude_output)
                reason = f"AUTO-REMEDIATED ({rem_info.get('failure_type', '?')}): {rem_msg} | {reason}"
            else:
                reason = f"{reason} | REMEDIATION={rem_info.get('failure_type', '?')}: {rem_msg}"
        except Exception as exc:
            logger.warning("guard-21: remediation error for %s: %s", task_id, exc)

    _write_verification_log(task_id, verified, reason)
    if metrics:
        _update_verification_metrics(task_id, metrics)
    return verified, reason


def _run_full_verification(task_id: str, claude_output: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Run all 3 verification layers. Returns (verified, reason, metrics).

    Extracted as its own function so it can be re-invoked after remediation.
    """
    verified, reason = _run_verify_checks(task_id, claude_output)
    metrics: Dict[str, Any] = {}
    if verified:
        specific_ok, specific_reason = _verify_task_specific(task_id)
        if not specific_ok:
            verified = False
            reason = f"{reason} | {specific_reason}"
        else:
            reason = f"{reason} | {specific_reason}"
    if verified:
        validation_ok, validation_reason, metrics = _run_post_task_validation(task_id)
        if not validation_ok:
            verified = False
            reason = f"{reason} | VALIDATION FAILED: {validation_reason}"
        else:
            reason = f"{reason} | {validation_reason}"
    return verified, reason, metrics


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
                    # guard-18: track failure count; flag for decomposition
                    # after repeated failures (task is probably too big).
                    new_status = _record_failure_and_maybe_flag(task_id, reason)
                    try:
                        _move_task(task_id, new_status)
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
