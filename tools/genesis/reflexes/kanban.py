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
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.strategos import tier_resolver  # noqa: E402

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

    Globs all *.md files (not just task-*.md) because the scheduler may
    write prompt files named by task ID directly (e.g. sg-import-ds-vault.md).
    The stem is always the task ID regardless of any prefix.
    """
    if not PROMPT_DIR.exists():
        return 0
    prompt_files = list(PROMPT_DIR.glob("*.md"))
    if not prompt_files:
        return 0
    # Only count prompts whose task is still in_progress
    conn = get_connection()
    try:
        count = 0
        for pf in prompt_files:
            task_id = pf.stem  # stem IS the task ID (e.g. "sg-import-ds-vault" or "task-abc123")
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


# Max tasks to auto-promote per cycle — matches MAX_IN_PROGRESS so a full
# batch fills all available slots in one cycle rather than two.
MAX_AUTO_PROMOTE = 3
# Max in-progress tasks at any time (prevents pile-up)
MAX_IN_PROGRESS = 3
# Max seconds a Claude CLI subprocess can run before being killed
MAX_EXECUTION_SECONDS = 900           # 15 min — default for normal tasks
MAX_EXECUTION_SECONDS_SCAN = 1200     # 20 min — codelens, coherence, E2E (tool ~10s but Claude overhead + fix cycles ~15 min)
MAX_EXECUTION_SECONDS_PYTEST = 2400   # 40 min — full test suite (7,519 tests @ ~0.3s each + Claude overhead)
# Minimum remaining budget required to start post-process operations (guard-budget)
VERIFICATION_MIN_BUDGET_SECONDS = 30   # verification pipeline can take ~10-25s
REMEDIATION_MIN_BUDGET_SECONDS = 60    # remediation + re-verify can take ~30-50s
SELF_DEBUG_MIN_BUDGET_SECONDS = 15     # self-debug dispatch is lightweight but still costs time
MAX_TIMEOUT_RETRIES = 3               # hard-quarantine a task after this many identical timeouts

# Task ID patterns that get extended timeouts (regex, case-insensitive).
# Order matters: first match wins.
# e2e tasks get PYTEST-level time — a single E2E step still runs the full
# Selenium suite under the hood, so 20 min is too tight.
_EXTENDED_TIMEOUT_PATTERNS = [
    (r"pytest|regression|test-suite|full-test|e2e", MAX_EXECUTION_SECONDS_PYTEST),
    (r"codelens|coherence|companion", MAX_EXECUTION_SECONDS_SCAN),
]


def _get_task_timeout(task_id: str) -> int:
    """Return per-task timeout budget in seconds.

    pytest / E2E suite / regression tasks get MAX_EXECUTION_SECONDS_PYTEST (40 min);
    codelens / coherence / single-E2E tasks get MAX_EXECUTION_SECONDS_SCAN (20 min);
    everything else gets MAX_EXECUTION_SECONDS (15 min).
    Also checks the task description for a TIMEOUT_HINT:NNNs directive.
    """
    task_id_lower = task_id.lower()
    for pattern, timeout in _EXTENDED_TIMEOUT_PATTERNS:
        if re.search(pattern, task_id_lower):
            return timeout

    # Check task description + task_type for TIMEOUT_HINT or heavy-tool heuristics
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT description, task_type FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        conn.close()
        d = dict(row) if row else {}
        desc = (d.get("description") or "").lower()
        task_type = (d.get("task_type") or "").lower()
        # Explicit override always wins
        m = re.search(r"timeout_hint:\s*(\d+)", desc, re.IGNORECASE)
        if m:
            return min(3600, int(m.group(1)))  # cap at 1 hour
        # PYTEST-level (40 min): full test suites, E2E suites, regression runs
        _pytest_kw = ("pytest", "regression", "full test", "test suite",
                      "e2e suite", "e2e test", "test_orchestrator")
        if any(kw in desc for kw in _pytest_kw):
            return MAX_EXECUTION_SECONDS_PYTEST
        if task_type == "test" and any(kw in desc for kw in ("e2e", "playwright", "selenium")):
            return MAX_EXECUTION_SECONDS_PYTEST
        # SCAN-level (20 min): single tool runs, coherence checks, single E2E steps
        _scan_kw = ("codelens", "coherence_checker", "e2e_full", "companion", "e2e")
        if any(kw in desc for kw in _scan_kw):
            return MAX_EXECUTION_SECONDS_SCAN
    except Exception:
        pass

    return MAX_EXECUTION_SECONDS


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


def _get_timeout_count(task_id: str) -> int:
    """Return the number of times this task has been killed for timeout."""
    timeout_file = PROMPT_DIR / f"{task_id}.timeouts"
    if timeout_file.exists():
        try:
            return int(timeout_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return 0
    return 0


def _increment_timeout_count(task_id: str) -> int:
    """Increment and return the per-task timeout counter."""
    _ensure_prompt_dir()
    timeout_file = PROMPT_DIR / f"{task_id}.timeouts"
    count = 0
    if timeout_file.exists():
        try:
            count = int(timeout_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            count = 0
    count += 1
    timeout_file.write_text(str(count), encoding="utf-8")
    return count


def _clear_timeout_count(task_id: str):
    """Remove timeout counter on task success or quarantine."""
    timeout_file = PROMPT_DIR / f"{task_id}.timeouts"
    if timeout_file.exists():
        timeout_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Worktree isolation — each task runs in its own git worktree
# ---------------------------------------------------------------------------

# Track worktree paths: {task_id: worktree_path_str}
_worktrees: Dict[str, str] = {}
# Snapshot of main's HEAD SHA captured at dispatch time for each task.
# Verification uses this as the baseline (not current main) so agent commits
# stay visible even if main advances between dispatch and verification.
_dispatch_main_heads: Dict[str, str] = {}

# Cache the detected default branch so we only shell out once per process.
_default_branch_cache: Optional[str] = None


def _default_branch() -> str:
    """Return the repo's default branch (main / master / trunk / dev).

    Tries in order:
      1. Cached value from a previous call.
      2. ``git symbolic-ref refs/remotes/origin/HEAD`` — reliable when origin exists.
      3. ``git rev-parse --verify main`` / master / trunk / dev — local fallback.
      4. Hard-coded "main" as last resort.
    """
    global _default_branch_cache
    if _default_branch_cache:
        return _default_branch_cache

    import subprocess as _sp

    # Strategy 1: remote HEAD ref (most reliable)
    try:
        r = _sp.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            branch = r.stdout.strip().removeprefix("origin/")
            _default_branch_cache = branch
            return branch
    except Exception:
        pass

    # Strategy 2: probe common names
    for candidate in ("main", "master", "trunk", "dev"):
        try:
            r = _sp.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                _default_branch_cache = candidate
                return candidate
        except Exception:
            pass

    _default_branch_cache = "main"
    return "main"


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
        # Validate it's a real git worktree, not an orphan empty dir left over
        # from a failed `git worktree remove`. Orphans cause Claude to run in
        # an empty cwd and coherence checks to fail (no tools/manifest.md).
        listed = _sp.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
        )
        if str(worktree_path).replace("\\", "/") in listed.stdout.replace("\\", "/"):
            return str(worktree_path)
        logger.warning("Orphan worktree dir at %s — removing and recreating", worktree_path)
        import shutil
        shutil.rmtree(worktree_path, ignore_errors=True)
        _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=10)
        _sp.run(["git", "branch", "-D", branch_name], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=10)
    else:
        # worktree_path doesn't exist but branch may still exist from a prior
        # _reset_broken_worktree whose `git branch -D` failed silently (e.g.
        # Windows file-lock left the ref in a bad state, or another worktree
        # held it). Without this cleanup, `git worktree add -b` fails with
        # "already exists" and _create_worktree returns None, forcing every
        # subsequent dispatch into BASE_DIR — causing the coherence loop.
        _stale = _sp.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
        )
        if _stale.returncode == 0:
            logger.warning(
                "Stale branch %s found without worktree dir — pruning before recreate",
                branch_name,
            )
            _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                    capture_output=True, text=True, timeout=10)
            _sp.run(["git", "branch", "-D", branch_name], cwd=str(BASE_DIR),
                    capture_output=True, text=True, timeout=10)

    try:
        # Create a new branch from HEAD for this task
        result = _sp.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "git worktree add failed for %s (rc=%d): %s",
                task_id, result.returncode, result.stderr.strip(),
            )
            # Directory may have been created as an empty shell — prune it so
            # the next dispatch starts clean rather than hitting the orphan path.
            import shutil as _shutil
            _shutil.rmtree(worktree_path, ignore_errors=True)
            _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                    capture_output=True, text=True, timeout=10)
            return None
        # Verify the worktree was actually registered: git populates a .git
        # *file* (not a directory) in the worktree root on success.
        if not (worktree_path / ".git").exists():
            logger.warning(
                "Worktree dir created for %s but .git file is missing — "
                "treating as failed registration and cleaning up",
                task_id,
            )
            import shutil as _shutil
            _shutil.rmtree(worktree_path, ignore_errors=True)
            _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                    capture_output=True, text=True, timeout=10)
            return None
        # Verify structural completeness: tools/manifest.md must exist in the
        # worktree. A partial Windows checkout (rmtree file-lock failures) can
        # leave an empty dir with only .git; coherence then fails on every
        # dispatch with "no tools/manifest.md", looping until self_debug fires.
        if not (worktree_path / "tools" / "manifest.md").exists():
            logger.warning(
                "Worktree dir created for %s but tools/manifest.md is missing "
                "(partial checkout) — cleaning up so next dispatch rebuilds clean",
                task_id,
            )
            import shutil as _shutil
            _shutil.rmtree(worktree_path, ignore_errors=True)
            _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                    capture_output=True, text=True, timeout=10)
            return None
        logger.info("Created worktree for %s at %s", task_id, worktree_path)
        # Guard: scrub any accidentally-tracked pyc/pycache files from the new
        # worktree's index before the agent runs. These are build artifacts that
        # should never be committed; if they slipped into the index on main,
        # every worktree inherits them and marks them as dirty after any import.
        try:
            tracked_pycs = _sp.run(
                ["git", "ls-files", "*.pyc", "*.pyo", "*.pyd"],
                cwd=str(worktree_path),
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if tracked_pycs:
                pyc_list = tracked_pycs.split("\n")
                _sp.run(
                    ["git", "rm", "--cached", "--force", "--ignore-unmatch"] + pyc_list,
                    cwd=str(worktree_path),
                    capture_output=True, text=True, timeout=15,
                )
                _sp.run(
                    ["git", "commit", "-m",
                     "chore: remove accidentally-tracked pyc files from worktree index"],
                    cwd=str(worktree_path),
                    capture_output=True, text=True, timeout=15,
                )
                logger.info(
                    "Scrubbed %d tracked pyc(s) from worktree index for %s",
                    len(pyc_list), task_id,
                )
        except Exception as _pyc_exc:
            logger.warning("pyc scrub failed for %s: %s", task_id, _pyc_exc)
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
            ["git", "log", _default_branch() + ".." + branch_name, "--oneline"],
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
        cur_branch = (cur_branch_proc.stdout or _default_branch()).strip() or _default_branch()
    except Exception:
        cur_branch = _default_branch()

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
        if cur_branch != _default_branch():
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
                ["git", "push", "origin", _default_branch()],
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

    # 4) Checkout default branch and attempt fast-forward merge
    try:
        co = _sp.run(
            ["git", "checkout", _default_branch()],
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
            "FF merge failed for %s, attempting rebase onto %s", task_id, _default_branch()
        )
        rebase = _sp.run(
            ["git", "rebase", _default_branch(), branch_name],
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
            # Rebase leaves us on the branch — go back to default branch
            _sp.run(
                ["git", "checkout", _default_branch()],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            _restore()
            return False

        # Rebase succeeded — now on the rebased branch, switch to default branch and ff
        _sp.run(
            ["git", "checkout", _default_branch()],
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


# Batch 4 worktree age sweep threshold — worktrees whose owning task is NOT
# currently in_progress and whose marker/dir mtime is older than this are
# force-cleaned. Disk hygiene for the failure-train class (many failed tasks
# leaving 500 MB worktrees each).
_WORKTREE_STALE_AGE_DAYS = 7


def _sweep_old_worktrees(max_age_days: int = _WORKTREE_STALE_AGE_DAYS) -> list[str]:
    """Force-clean worktrees older than max_age_days whose task isn't in_progress.

    Returns a list of task_ids whose worktrees were cleaned. Non-fatal:
    any cleanup error is logged and skipped. Runs opportunistically
    from the scheduler cycle (not a separate thread).
    """
    import subprocess as _sp
    import time as _time

    removed: list[str] = []
    if not WORKTREE_BASE.exists():
        return removed

    now_ts = _time.time()
    threshold_sec = max_age_days * 86400

    try:
        conn = get_connection()
        try:
            in_progress_rows = conn.execute(
                "SELECT id FROM kanban_tasks WHERE status = 'in_progress'"
            ).fetchall()
            in_progress_ids = {dict(r)["id"] for r in in_progress_rows}
        finally:
            conn.close()
    except Exception:
        in_progress_ids = set()

    for sub in sorted(WORKTREE_BASE.iterdir() if WORKTREE_BASE.is_dir() else []):
        if not sub.is_dir():
            continue
        task_id = sub.name
        if task_id in in_progress_ids:
            continue
        try:
            age_sec = now_ts - sub.stat().st_mtime
        except OSError:
            continue
        if age_sec < threshold_sec:
            continue
        try:
            _sp.run(
                ["git", "worktree", "remove", str(sub), "--force"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
            )
            logger.info(
                "Sweep: removed stale worktree %s (age %.1f days, task not in_progress)",
                sub, age_sec / 86400,
            )
            removed.append(task_id)
        except Exception as exc:
            logger.warning("Sweep: could not remove %s: %s", sub, exc)
    return removed


def _capture_diff_stats(task_id: str) -> dict:
    """Return files_changed/lines_added/lines_removed for kanban/{task_id} vs main.

    Parses the last line of `git diff --stat main..kanban/{task_id}` which is:
      "N files changed, M insertions(+), L deletions(-)"
    Returns zeros on any error (non-blocking, best-effort).
    """
    import subprocess as _sp
    import re as _re

    branch = f"kanban/{task_id}"
    default = {"files_changed": 0, "lines_added": 0, "lines_removed": 0}
    try:
        result = _sp.run(
            ["git", "diff", "--stat", f"{_default_branch()}..{branch}"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return default
        summary = result.stdout.strip().splitlines()[-1]
        fc = int((_re.search(r"(\d+) file", summary) or type("", (), {"group": lambda *_: "0"})()).group(1))
        la = int((_re.search(r"(\d+) insertion", summary) or type("", (), {"group": lambda *_: "0"})()).group(1))
        lr = int((_re.search(r"(\d+) deletion", summary) or type("", (), {"group": lambda *_: "0"})()).group(1))
        return {"files_changed": fc, "lines_added": la, "lines_removed": lr}
    except Exception:
        return default


def _cleanup_worktree(task_id: str):
    """Merge the kanban task branch to main (fast-forward) then remove
    the worktree. If merge fails, the branch is preserved for manual
    review and the worktree is still cleaned up (disk hygiene).
    """
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    worktree_path = WORKTREE_BASE / task_id

    # Detach the worktree FIRST so the branch ref isn't held while we
    # rebase. Commits remain safe on refs/heads/kanban/<task_id> after
    # the worktree is gone. Prior ordering ran merge before detach, so
    # rebase consistently failed with "already used by worktree" and
    # every post-dispatch-divergence task got preserved unnecessarily.
    try:
        if worktree_path.exists():
            _sp.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
            )
    except Exception as exc:
        logger.warning("Worktree remove failed for %s: %s", task_id, exc)

    # Capture diff stats + branch name + commit summary before the branch is deleted
    diff_stats = _capture_diff_stats(task_id)

    # Commit summary (one line per commit on the branch)
    _commit_summary = ""
    try:
        import subprocess as _sp2
        _log = _sp2.run(
            ["git", "log", "--oneline", f"{_default_branch()}..kanban/{task_id}"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
        )
        _commit_summary = _log.stdout.strip()[:1000] if _log.returncode == 0 else ""
    except Exception:
        pass

    merged_ok = _merge_worktree_to_main(task_id)

    # Persist change metrics + branch info to kanban_tasks (best-effort)
    try:
        _ds_conn = get_connection()
        _ds_conn.execute(
            "UPDATE kanban_tasks SET "
            "files_changed = ?, lines_added = ?, lines_removed = ?, "
            "branch_name = ?, commit_summary = ? "
            "WHERE id = ?",
            (
                diff_stats["files_changed"], diff_stats["lines_added"], diff_stats["lines_removed"],
                f"kanban/{task_id}", _commit_summary or None,
                task_id,
            ),
        )
        _ds_conn.commit()
        _ds_conn.close()
    except Exception as _ds_exc:
        logger.warning("diff_stats write failed for %s: %s", task_id, _ds_exc)

    try:
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
        logger.warning("Branch cleanup failed for %s: %s", task_id, exc)


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


_PHASE_ID_RE = re.compile(r"^(?P<prefix>[a-z0-9]+)-(?P<phase>[A-Z])(?:[0-9.]+|-gate)")


def _extract_phase(task_id: str) -> tuple[str, str] | None:
    """Return ``(prefix, phase)`` for phased task IDs like ``efa-E3-*``.

    Returns None for non-phased tasks (standalone/followup). The phase
    letter matches ``[A-Z]`` only; digits-only or lower-case prefixes are
    ignored. ``-gate`` suffixes are treated as belonging to the gate's
    phase (``efa-E-gate`` → phase ``E``).
    """
    m = _PHASE_ID_RE.match(task_id)
    if not m:
        return None
    return m.group("prefix"), m.group("phase")


def _phase_complete(prefix: str, phase: str) -> tuple[bool, list[str]]:
    """Return ``(all_done, unfinished_ids)`` for every task matching prefix+phase.

    Used by ``_get_due_tasks`` to gate phase-F dispatches on phase-E being
    fully done — defense in depth against a task whose immediate parent
    is marked done while earlier siblings in the same phase are not
    (the E-gate orphan-done incident class).
    """
    pattern = f"{prefix}-{phase}%"
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, status FROM kanban_tasks "
                "WHERE id LIKE ? AND status != 'done'",
                (pattern,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return True, []  # fail-open on DB error
    unfinished = [dict(r)["id"] for r in rows]
    return len(unfinished) == 0, unfinished


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
        # Native task dependency gating — a task is blocked if ANY of its
        # declared dependencies (scalar AND/OR junction table) are not yet done.
        # A task may have BOTH a scalar depends_on_task_id AND junction rows.
        # Both must be satisfied. 'decomposed' counts as done (parent was split;
        # it will never reach 'done' directly so dependents must not wait for it).
        #
        # Scalar dep check: always applied when depends_on_task_id is set.
        # Junction dep check: when kanban_task_deps rows exist, ALL must be done.
        # A task without either dependency is always eligible.
        _scalar_dep_ok = (
            "(kt.depends_on_task_id IS NULL "
            " OR EXISTS (SELECT 1 FROM kanban_tasks dep "
            "            WHERE dep.id = kt.depends_on_task_id "
            "             AND dep.status IN ('done', 'decomposed')))"
        )
        _junction_dep_ok = (
            "(NOT EXISTS (SELECT 1 FROM kanban_task_deps d WHERE d.task_id = kt.id) "
            " OR NOT EXISTS (SELECT 1 FROM kanban_task_deps d2 "
            "                JOIN kanban_tasks p ON p.id = d2.depends_on_id "
            "                WHERE d2.task_id = kt.id "
            "                 AND p.status NOT IN ('done', 'decomposed')))"
        )
        dep_clause = f"({_scalar_dep_ok} AND {_junction_dep_ok})"

        # Always pick up scheduled-and-due tasks.
        # Chain-priority (2026-04-15): tasks with depends_on_task_id IS NOT NULL
        # are part of an explicit dependency sequence — finish those FIRST,
        # before picking up standalone tasks at the same priority tier. Prevents
        # high-priority one-off followups from leapfrogging a mid-phase chain
        # task and starving the pipeline.
        scheduled = conn.execute(
            "SELECT kt.* FROM kanban_tasks kt "
            "WHERE kt.status = 'scheduled' "
            "  AND kt.scheduled_at IS NOT NULL "
            "  AND kt.scheduled_at <= datetime('now') "
            f"  AND {dep_clause} "  # nosec B608
            "ORDER BY "
            "CASE WHEN kt.depends_on_task_id IS NOT NULL THEN 0 ELSE 1 END, "
            "CASE kt.priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "kt.created_at ASC"
        ).fetchall()
        result = [dict(r) for r in scheduled]

        # Phase-exit validation (2026-04-15 V&V Batch 2): for phased task IDs
        # like ``efa-E3-*``, refuse to dispatch phase N+1 tasks until phase N
        # is 100% done. Defense-in-depth against the orphan-done class where
        # a task's immediate parent is done but earlier same-phase siblings
        # aren't (E-gate incident). Non-phased IDs pass through untouched.
        filtered_result = []
        for t in result:
            phase_info = _extract_phase(t["id"])
            if not phase_info:
                filtered_result.append(t)
                continue
            prefix, phase = phase_info
            if phase == "A":
                filtered_result.append(t)
                continue
            prior_phase = chr(ord(phase) - 1)
            prior_complete, unfinished = _phase_complete(prefix, prior_phase)
            if prior_complete:
                filtered_result.append(t)
            else:
                logger.info(
                    "phase-exit gate: holding %s until phase %s completes "
                    "(%d task(s) still not done: %s)",
                    t["id"], prior_phase, len(unfinished), unfinished[:3],
                )
        result = filtered_result

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

        # Backlog cooldown: skip tasks updated in the last 2 minutes
        # (prevents rapid-fire retry of recently failed tasks; 2 min aligns
        # with the 60s scheduler cycle and keeps parallel slots filling fast).
        # Tasks with fc≥5 are quarantined by the stale-reaper sweep and will
        # not appear here (status='suggested' after the sweep).
        backlog = conn.execute(
            "SELECT kt.* FROM kanban_tasks kt "
            "WHERE kt.status = 'backlog' "
            "  AND (kt.updated_at IS NULL "
            "       OR kt.updated_at <= datetime('now', '-2 minutes')) "
            "  AND (kt.last_failure_reason IS NULL "
            "       OR kt.last_failure_reason NOT LIKE ?) "
            f"  AND {dep_clause} "  # nosec B608
            "ORDER BY "
            "CASE WHEN kt.depends_on_task_id IS NOT NULL THEN 0 ELSE 1 END, "
            "CASE kt.priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "kt.created_at ASC "
            "LIMIT ?",
            ("QUARANTINED by self_debug%", slots),
        ).fetchall()
        result.extend(dict(r) for r in backlog)

        # Decompose batch tasks into individual children before returning
        # (guard-3). Batch cards have 96-100% phantom completion rate, so
        # we never dispatch them directly.
        result = _decompose_batch_tasks(result, conn)

        # Decompose PHASE-EXIT GATE tasks before they hit the 900s timeout.
        # 5-step validation gates (codelens|coherence|e2e|pytest|companion)
        # consistently exceed the dispatch budget; split them into 5
        # sequential sub-tasks each within its own 900s window.
        result = _decompose_phase_exit_gates(result, conn)

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
        # Skip batch meta-subjects — oracle-generated placeholders ("All
        # subjects share the same rule. A single fix may resolve all.")
        # and batch summary lines ("Source prediction IDs: ...") are not
        # concrete work items and have no actionable subject. Promoting
        # them spawned orphan needs_decomposition rows (2026-04-18 audit
        # found 2 such stuck tasks).
        _META_SUBJECT_MARKERS = (
            "all subjects share the same rule",
            "a single fix may resolve all",
            "source prediction ids:",
        )

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
                # Skip meta/placeholder lines — not real subjects
                if any(marker in line.lower() for marker in _META_SUBJECT_MARKERS):
                    continue
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

        # Derive project-scoped child prefix for known oracle gap rules so
        # children appear in Projects in Flight cards instead of being orphaned.
        _CDH_GAP_RULES = {
            "tool_not_in_manifest", "orphan_db_table", "route_no_e2e",
            "missing_test", "missing_e2e", "import_error", "stale_route",
        }
        if rule in _CDH_GAP_RULES:
            _child_prefix = "cdh-gap-"
        else:
            _child_prefix = f"{task['id'][:20]}-"

        created_children: list = []
        for subj in subjects:
            child_id = f"{_child_prefix}{_uuid.uuid4().hex[:8]}"
            child_title = f"{rule} gap: {subj}" if rule else f"Batch child: {subj}"
            child_desc = (
                f"AUTO-DECOMPOSED from batch task {task['id']}\n"
                f"Rule: {rule or 'unknown'}\n"
                f"Subject: {subj}\n"
                f"Parent: {task.get('title', '')}"
            )
            now = _utcnow_iso()
            # Capture active span so each decomposed task carries a trace link
            _decomp_trace_id: str | None = None
            _decomp_span_id: str | None = None
            try:
                from tools.observability import get_tracer as _get_tracer  # noqa: PLC0415
                _sp = _get_tracer().get_active_span()
                if _sp:
                    _decomp_trace_id = getattr(_sp, "trace_id", None)
                    _decomp_span_id = getattr(_sp, "span_id", None)
            except Exception:
                pass
            try:
                conn.execute(
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, "
                    " executor_type, source_prediction_id, created_at, updated_at, "
                    " trace_id, span_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        child_id, child_title, child_desc, parent_type,
                        parent_priority, "backlog", "claude_cli",
                        source_pred, now, now,
                        _decomp_trace_id, _decomp_span_id,
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


# Step labels for phase-exit gate decomposition (matches established F-gate / E-gate sub-task pattern)
_PHASE_GATE_STEPS = [
    ("codelens", "CodeLens scan",
     "Run: python tools/code_intelligence/codelens.py --all --json. Report pass/fail."),
    ("coherence", "Coherence check",
     "Run: python tools/workflow/coherence_checker.py --all --fix --gate. Report pass/fail."),
    ("e2e", "E2E dashboard test",
     "Run: python tools/testing/e2e_full_dashboard.py. Report pass/fail."),
    ("pytest", "Regression pytest",
     "Run: pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium. Report pass/fail."),
    ("companion", "Companion sync",
     "Run: python tools/dx/companion.py --sync --write --json. Report pass/fail."),
]


def _decompose_phase_exit_gates(tasks: list, conn: Any) -> list:
    """Detect and decompose PHASE-EXIT GATE tasks before they hit 900s timeout.

    A phase-exit gate task is identified by:
    - Description starts with "PHASE-EXIT GATE"
    - Contains the standard 5 validation steps (codelens, coherence, e2e, pytest, companion)
    - Task ID matches phase pattern (e.g. efa-F-gate, efa-G-gate)

    These tasks consistently exceed the 900s MAX_EXECUTION_SECONDS budget because
    running all 5 validations in a single dispatch can take 15+ minutes. We split
    them into 5 sequential sub-tasks (each within its own 900s budget) and mark
    the parent as 'done' so downstream phase tasks unblock immediately.

    Mirrors the manual decomposition pattern used for E-gate, F-gate, G-gate, H-gate.
    """
    result: list = []
    for task in tasks:
        description = (task.get("description") or "").strip()
        task_id = task.get("id", "")

        # Must look like a phase-exit gate task
        if "PHASE-EXIT GATE" not in description.upper():
            result.append(task)
            continue
        # Must be a phased gate ID (e.g. efa-F-gate)
        phase_info = _extract_phase(task_id)
        if not phase_info or "-gate" not in task_id:
            result.append(task)
            continue
        # Skip if sub-tasks already exist
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id LIKE ?",
            (f"{task_id}-1-%",),
        ).fetchone()
        if existing:
            result.append(task)
            continue

        # Decompose into 5 sub-tasks
        prev_dep = task.get("depends_on_task_id")
        now = _utcnow_iso()
        # Capture active span for phase-gate sub-tasks
        _gate_trace_id: str | None = None
        _gate_span_id: str | None = None
        try:
            from tools.observability import get_tracer as _gt  # noqa: PLC0415
            _gsp = _gt().get_active_span()
            if _gsp:
                _gate_trace_id = getattr(_gsp, "trace_id", None)
                _gate_span_id = getattr(_gsp, "span_id", None)
        except Exception:
            pass
        created = 0
        for idx, (slug, label, desc) in enumerate(_PHASE_GATE_STEPS, start=1):
            child_id = f"{task_id}-{idx}-{slug}"
            child_title = f"{phase_info[1]}-gate step {idx}: {label}"
            try:
                conn.execute(
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, "
                    " scheduled_at, created_at, updated_at, "
                    " executor_type, depends_on_task_id, dispatch_source, failure_count, "
                    " trace_id, span_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        child_id, child_title, desc,
                        task.get("task_type") or "test",
                        task.get("priority") or "high",
                        "scheduled", now, now, now, "claude_cli",
                        prev_dep, "auto_decomp_phase_gate", 0,
                        _gate_trace_id, _gate_span_id,
                    ),
                )
                created += 1
                prev_dep = child_id
            except Exception as exc:
                print(f"  Kanban: failed to create gate child {child_id}: {exc}")

        if created == 5:
            # Mark parent gate as 'done' (decomposed marker — mirrors F-gate pattern)
            try:
                conn.execute(
                    "UPDATE kanban_tasks SET status = ?, updated_at = ?, "
                    "completed_at = ?, last_failure_reason = ? WHERE id = ?",
                    (
                        "done", now, now,
                        "AUTO-DECOMPOSED into 5 sub-tasks (codelens|coherence|e2e|pytest|companion) "
                        "to prevent 900s dispatch budget timeout",
                        task_id,
                    ),
                )
                conn.commit()
                print(
                    f"  Kanban: auto-decomposed phase-exit gate {task_id} into "
                    f"5 sequential sub-tasks (parent marked done)"
                )
            except Exception as exc:
                print(f"  Kanban: failed to mark gate {task_id} done: {exc}")
        else:
            # Partial failure — leave parent as-is, dispatch as before (best-effort)
            result.append(task)

    return result


MAX_FAILURES_BEFORE_DECOMPOSITION = 1


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

            # Chain-blocker escalation: if any tasks are blocked waiting for
            # this one, escalate priority to critical so failure_triage picks
            # it up first and its dependents can be unblocked sooner.
            blocked_dep_rows = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE depends_on_task_id = ? "
                "  AND status NOT IN ('done','decomposed')",
                (task_id,),
            ).fetchall()
            blocked_dep_ids = [dict(r)["id"] for r in blocked_dep_rows]
            if blocked_dep_ids:
                conn.execute(
                    "UPDATE kanban_tasks SET priority = 'critical', updated_at = ? "
                    "WHERE id = ?",
                    (now, task_id),
                )
                logger.warning(
                    "Task %s failed with %d blocked dependent(s) %s — "
                    "escalated priority to critical",
                    task_id, len(blocked_dep_ids), blocked_dep_ids,
                )
            conn.commit()

            if new_count >= MAX_FAILURES_BEFORE_DECOMPOSITION:
                logger.warning(
                    "Task %s failed %d times — flagging for decomposition",
                    task_id, new_count,
                )
                import os as _os
                _is_test = (
                    task_id.startswith("test-") or
                    _os.environ.get("PYTEST_CURRENT_TEST") or
                    _os.environ.get("ICDEV_SUPPRESS_NOTIFICATIONS") == "1"
                )
                if not _is_test:
                    try:
                        from tools.notifications.adapters.telegram import send as tg_send
                        chain_note = (
                            f"\n\nWARNING: {len(blocked_dep_ids)} downstream task(s) are "
                            f"blocked on this task: {blocked_dep_ids[:5]}"
                            if blocked_dep_ids else ""
                        )
                        tg_send(
                            f"DECOMPOSITION NEEDED: {task_id[:24]}",
                            f"Task failed {new_count} verification attempts. "
                            f"It is likely too big for one Claude CLI session. "
                            f"Please split it into smaller sub-tasks.\n"
                            f"Latest reason: {reason_short[:200]}"
                            f"{chain_note}",
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


def _record_status_transition(
    task_id: str, from_status: str | None, to_status: str,
    actor: str = "scheduler", reason: str | None = None,
) -> None:
    """Append a row to kanban_status_transitions (best-effort).

    Created by migration 025 (2026-04-15) after the E-gate orphan-done
    incident where investigators had no forensic trail for the rogue
    status=done UPDATE. Never raises — audit log failure must not block
    the primary state transition.
    """
    try:
        import secrets as _secrets  # noqa: PLC0415
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO kanban_status_transitions "
                "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "kst-" + _secrets.token_hex(6),
                    task_id, from_status, to_status, actor, reason,
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Audit-log writes are best-effort. If the table is missing
        # (migration 025 not yet run) or the DB is locked, we do NOT
        # block the primary state transition. The alternative \u2014
        # crashing _move_task on an audit write \u2014 would be worse.
        pass


def _parent_is_done(task_id: str) -> tuple[bool, str | None]:
    """Return (True, None) if task has no parent OR parent is done.

    Used by _move_task's done-transition guard. Defense-in-depth against
    manually set status=done bypassing _get_due_tasks' dependency check
    (the E-gate orphan-done incident, 2026-04-15).
    """
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT t.depends_on_task_id, p.status AS parent_status "
            "FROM kanban_tasks t "
            "LEFT JOIN kanban_tasks p ON p.id = t.depends_on_task_id "
            "WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            conn.close()
            return True, None
        row = dict(row)

        # Check scalar dep
        parent_id = row.get("depends_on_task_id")
        if parent_id:
            parent_status = row.get("parent_status")
            if parent_status not in ("done", "decomposed"):
                conn.close()
                return False, f"parent {parent_id} status={parent_status!r}"

        # Check junction deps — any undone junction parent blocks
        unmet = conn.execute(
            "SELECT d.depends_on_id, p.status "
            "FROM kanban_task_deps d "
            "JOIN kanban_tasks p ON p.id = d.depends_on_id "
            "WHERE d.task_id = ? AND p.status NOT IN ('done', 'decomposed')",
            (task_id,),
        ).fetchone()
        conn.close()
        if unmet:
            unmet = dict(unmet)
            return False, (
                f"junction dep {unmet['depends_on_id']!r} status={unmet['status']!r}"
            )
    except Exception:
        return True, None  # fail-open on DB error
    return True, None


def _close_orphaned_rca_children(parent_task_id: str, actor: str = "scheduler") -> None:
    """When a task moves to 'done', cancel any open diag-/RCA children that
    the self_debug reflex created for it. These tasks become moot once the
    parent is resolved and must not linger in 'suggested'/'backlog' forever.

    Matches tasks whose id starts with ``diag-<parent_task_id>`` or whose
    title contains the parent task id and task_type is 'chore'/'research'
    (the two types self_debug uses for RCA cards).
    """
    try:
        conn = get_connection()
        now = _utcnow_iso()
        prefix = f"diag-{parent_task_id}"
        open_statuses = ("suggested", "backlog", "scheduled", "in_progress")
        placeholders = ",".join("?" * len(open_statuses))
        rows = conn.execute(
            f"SELECT id FROM kanban_tasks "  # nosec B608
            f"WHERE (id LIKE ? OR (title LIKE ? AND task_type IN ('chore','research','fix'))) "
            f"  AND status IN ({placeholders})",
            (f"{prefix}%", f"%{parent_task_id}%", *open_statuses),
        ).fetchall()
        orphan_ids = [dict(r)["id"] for r in rows]
        if orphan_ids:
            ph = ",".join("?" * len(orphan_ids))
            conn.execute(
                f"UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=?, "  # nosec B608
                f"last_failure_reason=? WHERE id IN ({ph})",
                (now, now, f"auto-closed: parent {parent_task_id} resolved", *orphan_ids),
            )
            conn.commit()
            logger.info(
                "_close_orphaned_rca_children: closed %d orphan(s) of %s: %s",
                len(orphan_ids), parent_task_id, orphan_ids,
            )
        conn.close()
    except Exception as exc:
        logger.warning("_close_orphaned_rca_children failed for %s: %s", parent_task_id, exc)


def _auto_close_decomposed_parent(child_task_id: str, actor: str = "scheduler") -> Optional[str]:
    """If the just-completed child had a decomposed parent whose remaining
    children are now all done, close the parent as well.

    Tries two linkage mechanisms in order:
    1. ``source_prediction_id`` — batch children inherit this from the parent
       at decomposition time; the parent row shares the same value.
    2. ``depends_on_task_id`` — fallback for tasks created without a prediction
       ID (manual inserts, LLM failures to propagate the field). The child's
       depends_on_task_id IS the parent; siblings are all tasks sharing that
       same parent.

    Returns the closed parent's task_id, or None if nothing was closed.
    Safe to call on any done transition — no-op when the task isn't a
    batch child or the parent still has open siblings.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source_prediction_id, depends_on_task_id FROM kanban_tasks WHERE id = ?",
            (child_task_id,),
        ).fetchone()
        if not row:
            return None
        row_d = dict(row)
        sp = row_d.get("source_prediction_id")

        if sp:
            # --- Path 1: source_prediction_id linkage (batch-decomposed tasks) ---
            parent_row = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE source_prediction_id = ? AND status = 'decomposed' "
                "  AND id <> ? LIMIT 1",
                (sp, child_task_id),
            ).fetchone()
            if not parent_row:
                return None
            parent_id = dict(parent_row)["id"]

            open_count = conn.execute(
                "SELECT COUNT(*) AS n FROM kanban_tasks "
                "WHERE source_prediction_id = ? AND id <> ? "
                "  AND status IN ('backlog', 'scheduled', 'in_progress', "
                "                 'suggested', 'needs_decomposition', 'dispatched')",
                (sp, parent_id),
            ).fetchone()
            if dict(open_count).get("n", 0) > 0:
                return None
        else:
            # --- Path 2: depends_on_task_id linkage (manually created tasks) ---
            parent_id = row_d.get("depends_on_task_id")
            if not parent_id:
                # --- Path 3: ID naming-convention ({parent}-d{N}) ---
                # Delegate to the canonical state_machine implementation which
                # handles this case — covers tasks where subtasks chain to each
                # other but none explicitly declares depends_on_task_id=parent.
                try:
                    from tools.kanban.state_machine import auto_close_by_naming_convention
                    result = auto_close_by_naming_convention(child_task_id, conn, actor=actor)
                    if result and result.applied:
                        return result.task_id
                except Exception as _e:
                    logger.debug("_auto_close_decomposed_parent path-3 failed: %s", _e)
                return None

            parent_status_row = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = ? AND status = 'decomposed'",
                (parent_id,),
            ).fetchone()
            if not parent_status_row:
                return None

            open_siblings = conn.execute(
                "SELECT COUNT(*) AS n FROM kanban_tasks "
                "WHERE depends_on_task_id = ? AND id <> ? "
                "  AND status IN ('backlog', 'scheduled', 'in_progress', "
                "                 'suggested', 'needs_decomposition', 'dispatched')",
                (parent_id, child_task_id),
            ).fetchone()
            if dict(open_siblings).get("n", 0) > 0:
                return None

        now = _utcnow_iso()
        conn.execute(
            "UPDATE kanban_tasks SET status = 'done', completed_at = ?, "
            "updated_at = ? WHERE id = ? AND status = 'decomposed'",
            (now, now, parent_id),
        )
        # Audit-trail bypass row so guard-22 stays consistent: the parent
        # closure is bookkeeping, not fresh work that needs verification.
        linkage = "sp-linkage" if sp else "dep-linkage"
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(task_id, verified_at, result, reason, dispatch_source) "
            "VALUES (?, ?, 'bypassed', ?, ?)",
            (parent_id, now,
             f"Auto-closed by _auto_close_decomposed_parent ({linkage}): "
             f"last child {child_task_id} completed, all siblings done",
             "scheduler"),
        )
        conn.commit()
        logger.info(
            "auto-closed decomposed parent %s (%s) after last child %s completed",
            parent_id, linkage, child_task_id,
        )
        _record_status_transition(
            parent_id, "decomposed", "done", actor=actor,
            reason=f"auto-close ({linkage}): last child {child_task_id} done",
        )
        return parent_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _move_task(task_id: str, new_status: str, actor: str = "scheduler",
               reason: str | None = None, completed_via_bypass: bool = False):
    """Update task status in the database.

    Policy changes (2026-04-15 V&V hardening):
      * Done-transition guard: if a task has a non-null
        ``depends_on_task_id`` and that parent is NOT in ``done`` state,
        REFUSE the transition to ``done``. Logs a warning and is a no-op.
        Defense-in-depth against manually set done bypassing
        ``_get_due_tasks`` (E-gate orphan-done incident).
      * Cascade rollback: if a task transitions FROM ``done`` to anything
        else (remediation, orphan sweep, manual reset), every downstream
        task whose ``depends_on_task_id`` points at it is rolled back to
        ``scheduled`` (only those currently ``in_progress`` or ``backlog``;
        already-done descendants stay done \u2014 that's a separate decision
        the operator can make explicitly).
      * Audit log: every transition (accepted or refused) is appended to
        ``kanban_status_transitions`` via ``_record_status_transition``.
      * completed_via_bypass: when True and new_status == 'done', sets
        the completed_via_bypass flag on the task row so bypass completions
        are queryable without a JOIN to kanban_verifications.
    """
    conn = get_connection()
    try:
        # Look up current status for audit + cascade logic
        row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = ?", (task_id,),
        ).fetchone()
        prior_status = dict(row)["status"] if row else None

        # Done-transition guard
        if new_status == "done":
            ok, guard_reason = _parent_is_done(task_id)
            if not ok:
                logger.warning(
                    "_move_task: REFUSED done transition for %s \u2014 %s",
                    task_id, guard_reason,
                )
                conn.close()
                _record_status_transition(
                    task_id, prior_status, "REFUSED_done",
                    actor=actor,
                    reason=f"guard: {guard_reason}",
                )
                return

        # HITL gate: block in_progress→done when a HITL approval is pending
        if new_status == "done" and __import__("os").getenv("ICDEV_HITL_KANBAN_GATE", "").lower() in ("true", "1"):
            try:
                from tools.workflow_hitl.gate import HITLGate
                pending = HITLGate().get_pending(task_id)
                if pending:
                    logger.info(
                        "_move_task: HITL gate active for %s — not advancing to done (approval: %s)",
                        task_id, pending["id"],
                    )
                    conn.close()
                    _record_status_transition(
                        task_id, prior_status, "HITL_PENDING",
                        actor=actor,
                        reason=f"HITL gate: approval {pending['id']} stage={pending.get('stage')} pending",
                    )
                    return
            except ImportError:
                pass  # HITL module not installed — gate is no-op

        now = _utcnow_iso()
        sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
        vals = [new_status, now]
        if new_status == "done":
            sql += ", completed_at = ?"
            vals.append(now)
            if completed_via_bypass:
                sql += ", completed_via_bypass = 1"
        elif new_status == "in_progress":
            # Clear stale failure reason on re-dispatch so the Autonomous
            # Recovery panel doesn't keep showing this task as broken.
            sql += ", last_failure_reason = NULL"
        elif new_status == "scheduled":
            # Root-cause fix (2026-04-17): the scheduler's due-task query
            # (_get_due_tasks) requires `status='scheduled' AND scheduled_at
            # IS NOT NULL AND scheduled_at <= now()`. Any caller moving a
            # task to 'scheduled' MUST populate scheduled_at or the row
            # becomes invisible to the dispatcher. Multiple silent-failure
            # incidents (cascade rollback, orphan_sweep, manual API moves)
            # all originated from this gap. Setting scheduled_at here makes
            # every move-to-scheduled call site safe by default.
            sql += ", scheduled_at = ?"
            vals.append(now)
        sql += " WHERE id = ?"
        vals.append(task_id)
        conn.execute(sql, tuple(vals))
        conn.commit()

        # Cascade rollback: done \u2192 not-done demotes active descendants.
        #
        # Descendants go back to 'backlog' (not 'scheduled') because:
        #   1. Moving to 'scheduled' without setting scheduled_at produced an
        #      invisible row — the scheduler's due-task query (see
        #      _get_due_tasks) requires scheduled_at IS NOT NULL, so the task
        #      became permanently un-dispatchable. Silent outage on 2026-04-17
        #      where dt-iqe-04 blocked an 82-task chain for ~2 hours.
        #   2. 'backlog' is the natural "waiting to be re-evaluated" state.
        #      The existing dep_clause in _get_due_tasks keeps descendants
        #      blocked until the parent reaches 'done' again, so there's no
        #      risk of premature dispatch.
        #   3. The 10-minute backlog cooldown (kt.updated_at) prevents
        #      rapid-fire retries of cascaded tasks.
        rolled_back: list[str] = []
        if prior_status == "done" and new_status != "done":
            desc_rows = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE depends_on_task_id = ? "
                "  AND status IN ('in_progress', 'backlog', 'scheduled')",
                (task_id,),
            ).fetchall()
            for r in desc_rows:
                rolled_back.append(dict(r)["id"])
            if rolled_back:
                placeholders = ",".join("?" * len(rolled_back))
                conn.execute(
                    "UPDATE kanban_tasks SET status='backlog', "
                    "scheduled_at=NULL, "
                    "updated_at=?, failure_count=0, "
                    "last_failure_reason=?, last_failure_at=NULL "
                    f"WHERE id IN ({placeholders})",  # nosec B608
                    (now, f"cascade: parent {task_id} demoted from done", *rolled_back),
                )
                conn.commit()
                logger.info(
                    "_move_task: cascade rolled back %d descendant(s) of %s to backlog: %s",
                    len(rolled_back), task_id, rolled_back,
                )
    finally:
        conn.close()

    _record_status_transition(task_id, prior_status, new_status, actor=actor, reason=reason)
    if prior_status == "done" and new_status != "done" and rolled_back:
        for dep_id in rolled_back:
            _record_status_transition(
                dep_id, None, "backlog", actor="cascade",
                reason=f"parent {task_id} demoted {prior_status}\u2192{new_status}",
            )

    # Auto-close decomposed parents when their last child completes.
    # Batch-decomposer children inherit source_prediction_id from the parent
    # (see _decompose_batch_tasks). When all siblings are done, the parent
    # has no work left — previously it stuck in 'decomposed' forever (2026-04-18
    # audit found 7 such stuck parents). Guard: only auto-close when the
    # just-moved task is transitioning TO done, not on demotions.
    if new_status == "done" and prior_status != "done":
        try:
            _auto_close_decomposed_parent(task_id, actor=actor)
        except Exception as exc:
            logger.warning("auto-close check failed for %s: %s", task_id, exc)
        # Orphan sweep: close any diag-/RCA tasks created by self_debug for
        # this task that are still open. When a task is done its diagnostics
        # are moot — leaving them in 'suggested' or 'backlog' pollutes the
        # board indefinitely with no actionable work.
        try:
            _close_orphaned_rca_children(task_id, actor=actor)
        except Exception as exc:
            logger.warning("rca-orphan sweep failed for %s: %s", task_id, exc)

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


def _detect_orphan_done_tasks() -> list[dict]:
    """Find done tasks whose parent isn't done and roll them back.

    Runs at the start of each scheduler cycle. Catches the class of bugs
    where a row was SET to done without its prerequisite work completing
    (E-gate incident 2026-04-15: E-gate done while E4/E5/E6 were not).

    Returns a list of ``{id, parent_id, prior_parent_status}`` dicts for
    every row rolled back. The rollback itself goes through ``_move_task``
    so the audit trail captures the orphan-sweep actor.
    """
    try:
        conn = get_connection()
        try:
            # Scalar dep orphans: done task whose depends_on_task_id parent isn't done
            scalar_rows = conn.execute(
                "SELECT t.id AS id, t.depends_on_task_id AS parent_id, "
                "       p.status AS parent_status "
                "FROM kanban_tasks t "
                "JOIN kanban_tasks p ON p.id = t.depends_on_task_id "
                "WHERE t.status = 'done' AND p.status NOT IN ('done', 'decomposed')"
            ).fetchall()
            # Junction dep orphans: done task with at least one unfinished junction parent
            junction_rows = conn.execute(
                "SELECT DISTINCT t.id AS id, d.depends_on_id AS parent_id, "
                "       p.status AS parent_status "
                "FROM kanban_tasks t "
                "JOIN kanban_task_deps d ON d.task_id = t.id "
                "JOIN kanban_tasks p ON p.id = d.depends_on_id "
                "WHERE t.status = 'done' AND p.status NOT IN ('done', 'decomposed')"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("orphan-done sweep: DB error %s", exc)
        return []

    seen: set[str] = set()
    orphans: list[dict] = []
    for r in list(scalar_rows) + list(junction_rows):
        d = dict(r)
        if d["id"] not in seen:
            seen.add(d["id"])
            orphans.append({
                "id": d["id"],
                "parent_id": d["parent_id"],
                "parent_status": d.get("parent_status"),
            })

    for o in orphans:
        logger.warning(
            "ORPHAN-DONE detected: %s was done but parent %s is %r \u2014 rolling back to backlog",
            o["id"], o["parent_id"], o["parent_status"],
        )
        # Roll back to backlog (not scheduled) \u2014 parent is not done so this
        # task must wait for the dependency chain to complete before re-scheduling.
        _move_task(
            o["id"], "backlog",
            actor="orphan_sweep",
            reason=f"parent {o['parent_id']} status={o['parent_status']!r} at sweep",
        )

    return orphans


def _get_resume_context(task_id: str) -> str:
    """Return a '## Resume Context' section if the task branch has commits
    ahead of the default branch, otherwise return an empty string.

    Uses `git log main..kanban/{task_id}` and
    `git diff --name-only main..kanban/{task_id}` to inspect prior work.
    All subprocess failures are swallowed — this must never break prompt
    writing.
    """
    branch = f"kanban/{task_id}"
    cwd = str(BASE_DIR)
    try:
        log_result = subprocess.run(
            ["git", "log", f"main..{branch}", "--oneline"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        commits = log_result.stdout.strip()
        if not commits:
            return ""

        diff_result = subprocess.run(
            ["git", "diff", "--name-only", f"main..{branch}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        changed_files = diff_result.stdout.strip()

        lines = [
            "## Resume Context",
            "",
            "This task was interrupted (power outage or restart). The following "
            "work was already committed and is preserved in your worktree:",
            "",
            "**Commits already on this branch:**",
        ]
        for commit_line in commits.splitlines():
            lines.append(f"- {commit_line}")

        if changed_files:
            lines.append("")
            lines.append("**Files already modified:**")
            for f in changed_files.splitlines():
                lines.append(f"- {f}")

        lines.append("")
        lines.append(
            "Pick up where it left off. Do NOT redo work already committed."
        )
        lines.append("")
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def _write_prompt_file(task: dict):
    """Write a prompt file for Claude Code to pick up."""
    _ensure_prompt_dir()
    task_id = task["id"]
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    task_type = task.get("task_type", "chore")
    priority = task.get("priority", "medium")

    resume_section = _get_resume_context(task_id)

    prompt = f"""{resume_section}# Kanban Task: {title}
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


def _queue_alert_locally(
    task: dict,
    reason: str,
    event: str = "failed",
    severity: str = "warning",
    max_retries: int = 3,
) -> bool:
    """Persist an alert to the local kanban_alert_queue table.

    Retries on DB contention so alerts are never silently discarded.
    Returns True when the row is persisted.
    """
    import time

    task_id = task.get("id", "")
    title = task.get("title", task_id)
    body = f"Task returned to backlog. Reason: {reason}"
    for attempt in range(max_retries):
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO kanban_alert_queue "
                "(task_id, event, severity, title, body, reason, actor, created_at, retry_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    event,
                    severity,
                    title,
                    body,
                    reason,
                    "stale-cleanup",
                    datetime.now(timezone.utc).isoformat(),
                    attempt,
                ),
            )
            conn.commit()
            conn.close()
            logger.info("Queued alert locally for %s (attempt %d)", task_id, attempt)
            return True
        except Exception as exc:
            logger.warning(
                "Local alert queue write attempt %d/%d failed for %s: %s",
                attempt + 1,
                max_retries,
                task_id,
                exc,
            )
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
    return False


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

# Semaphore counter for EXEC_OLLAMA_LOCAL concurrent dispatch limit.
_ollama_running_count: int = 0

# Task IDs dispatched synchronously via Ollama (already completed when added).
# The reflex run() checks this to mark them done immediately rather than
# routing through the in_progress polling loop.
_ollama_completed: set = set()


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
        "Previous run left ruff lint errors that ruff --fix could not auto-resolve. "
        "The SPECIFIC errors are listed in the 'Reason' field above. "
        "For each file:line:col:code listed, open the file and manually fix that issue. "
        "After fixing, verify with: python -m ruff check <modified_files>. "
        "Do NOT just run ruff --fix again — those were already tried and did not help."
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

        # Write instruction to a temp file and pipe via stdin to avoid the
        # Windows 32767-char command-line length limit (WinError 206).
        # Claude auto-detects non-TTY stdout and enters non-interactive mode.
        import tempfile as _tempfile
        _instr_tmp = _tempfile.NamedTemporaryFile(
            mode="w", suffix="_instr.txt", delete=False,
            dir=str(BASE_DIR / ".tmp"),
            encoding="utf-8", errors="replace",
        )
        _instr_tmp.write(instruction)
        _instr_tmp.close()
        _stdin_fh = open(_instr_tmp.name, "r", encoding="utf-8", errors="replace")

        proc = subprocess.Popen(
            [
                claude_cli,
                "--dangerously-skip-permissions",
                "--max-turns",
                "50",
                "--output-format",
                "text",
            ],
            cwd=work_dir,
            stdin=_stdin_fh,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
        )
        _stdin_fh.close()  # subprocess inherits the fd; close our handle
        # Clean up temp instruction file after 5 min (process has read it by then)
        import threading as _threading
        import os as _os2

        def _cleanup_instr(path, delay=300.0):
            import time
            time.sleep(delay)
            try:
                _os2.unlink(path)
            except Exception:
                pass

        _threading.Thread(
            target=_cleanup_instr, args=(_instr_tmp.name,), daemon=True
        ).start()

        _running[task_id] = proc
        _dispatch_times[task_id] = datetime.now(timezone.utc)
        print(f"  Kanban: dispatched {task_id} to claude CLI (PID {proc.pid})")
    except FileNotFoundError as e:
        print(f"  Kanban: claude dispatch error for {task_id}: {e}")
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


def _dispatch_gitlab(task_id: str, task_desc: str, task_type: str) -> bool:
    """Trigger a GitLab CI pipeline for the given task (EXEC_GITLAB tier).

    POSTs to the pipeline trigger endpoint with TASK_ID, TASK_DESCRIPTION,
    TASK_TYPE, and LLM_PROVIDER=ollama. Stores the returned pipeline_id in
    the task row. Returns True on HTTP 201, False on any error.
    """
    import os as _os
    try:
        import requests as _requests
    except ImportError:
        logger.warning("kanban: requests library not available — cannot dispatch via GitLab")
        return False

    gitlab_url = _os.getenv("GITLAB_URL", "").rstrip("/")
    project_id = _os.getenv("GITLAB_PROJECT_ID", "")
    trigger_token = _os.getenv("GITLAB_TRIGGER_TOKEN", "")

    if not gitlab_url or not project_id or not trigger_token:
        logger.warning(
            "kanban: GITLAB_URL / GITLAB_PROJECT_ID / GITLAB_TRIGGER_TOKEN not set"
        )
        return False

    path = f"/api/v4/projects/{project_id}/trigger/pipeline"
    url = urllib.parse.urljoin(gitlab_url + "/", path.lstrip("/"))

    payload = {
        "token": trigger_token,
        "ref": "main",
        "variables[TASK_ID]": task_id,
        "variables[TASK_DESCRIPTION]": task_desc,
        "variables[TASK_TYPE]": task_type,
        "variables[LLM_PROVIDER]": "ollama",
    }

    try:
        resp = _requests.post(url, data=payload, timeout=10)
    except _requests.RequestException as exc:
        logger.warning("kanban: GitLab pipeline trigger failed for %s: %s", task_id, exc)
        return False

    if resp.status_code == 201:
        pipeline_id = str(resp.json().get("id", ""))
        pipeline_web_url = resp.json().get("web_url", "")
        try:
            conn = get_connection()
            conn.execute(
                "UPDATE kanban_tasks SET execution_id = ?, executor_url = ?, updated_at = ? WHERE id = ?",
                (pipeline_id, pipeline_web_url, datetime.now(timezone.utc).isoformat(), task_id),
            )
            conn.commit()
        except Exception as _db_exc:
            logger.warning("kanban: failed to store pipeline_id for %s: %s", task_id, _db_exc)
        logger.info("kanban: GitLab pipeline %s triggered for task %s", pipeline_id, task_id)
        return True

    logger.warning(
        "kanban: GitLab pipeline trigger returned %d for %s", resp.status_code, task_id
    )
    return False


def _dispatch_ollama_local(task_id: str, task_desc: str, task_type: str) -> bool:
    """Run an Ollama-backed anvil script directly for the EXEC_OLLAMA_LOCAL tier.

    Executes tools/anvil/{task_type}.py synchronously with LLM_PROVIDER=ollama.
    A module-level semaphore (_ollama_running_count) limits concurrent Ollama
    dispatches to OLLAMA_MAX_CONCURRENT (default 1) to avoid VRAM contention.
    Returns True on success (returncode==0), False on semaphore limit, timeout,
    or subprocess failure.
    """
    import os as _os

    global _ollama_running_count

    _max_concurrent = int(_os.getenv("OLLAMA_MAX_CONCURRENT", "1"))
    if _ollama_running_count >= _max_concurrent:
        logger.warning(
            "kanban: Ollama concurrency limit %d reached, skipping %s",
            _max_concurrent,
            task_id,
        )
        return False

    anvil_script = f"tools/anvil/{task_type}.py"
    env = {**_os.environ, "LLM_PROVIDER": "ollama"}

    _ollama_running_count += 1
    try:
        result = subprocess.run(
            [sys.executable, anvil_script, "--json", "--", task_desc],
            env=env,
            capture_output=True,
            timeout=600,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            stderr_snippet = result.stderr.decode("utf-8", errors="replace")[:500]
            logger.warning(
                "kanban: Ollama local dispatch failed for %s (exit %d): %s",
                task_id,
                result.returncode,
                stderr_snippet,
            )
            return False
        _dispatch_times[task_id] = datetime.now(timezone.utc)
        logger.info("kanban: dispatched %s via Ollama local anvil (%s)", task_id, anvil_script)
        return True
    except subprocess.TimeoutExpired:
        logger.warning("kanban: Ollama local dispatch timed out for %s", task_id)
        return False
    except Exception as exc:
        logger.warning("kanban: Ollama local dispatch error for %s: %s", task_id, exc)
        return False
    finally:
        _ollama_running_count -= 1


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


def _set_executor_type(task_id: str, executor_type: str) -> None:
    """Stamp executor_type on the task row so the UI badge is accurate."""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE kanban_tasks SET executor_type = ? WHERE id = ?",
            (executor_type, task_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("kanban: failed to set executor_type for %s: %s", task_id, exc)


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
        return  # No notification — false-positive resolves are scheduler noise

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
            ["git", "rev-parse", _default_branch()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=10,
        )
        if head_proc.returncode == 0:
            _dispatch_main_heads[task_id] = head_proc.stdout.strip()
    except Exception as exc:
        logger.debug("kanban: failed to capture main HEAD for %s: %s", task_id, exc)

    instruction = _build_instruction(task_id, title, prompt_text, prompt_path)
    task_log = PROMPT_DIR / f"{task_id}.log"

    try:
        import yaml as _yaml  # noqa: PLC0415
        _cfg_path = BASE_DIR / "args" / "strategos_config.yaml"
        with open(_cfg_path, encoding="utf-8") as _f:
            _sc = _yaml.safe_load(_f) or {}
        _fallback_chain = _sc.get("executor", {}).get(
            "fallback_chain", ["claude_cli", "gitlab", "ollama_local"]
        )
    except Exception:
        _fallback_chain = ["claude_cli", "gitlab", "ollama_local"]

    task_desc = task.get("description", task.get("title", ""))
    task_type = task.get("task_type", "chore")

    dispatched = False
    for tier in _fallback_chain:
        if tier == "claude_cli":
            if _claude_code_available():
                _dispatch_via_claude_cli(task, prompt_path, instruction, work_dir, task_log)
                _set_executor_type(task_id, "claude_cli")
                dispatched = True
                break
        elif tier == "gitlab":
            ok = _dispatch_gitlab(task_id, task_desc, task_type)
            if ok:
                _dispatch_times[task_id] = datetime.now(timezone.utc)
                _set_executor_type(task_id, "gitlab")
                print(f"  Kanban: dispatched {task_id} via GitLab CI pipeline")
                dispatched = True
                break
        elif tier == "ollama_local":
            ok = _dispatch_ollama_local(task_id, task_desc, task_type)
            if ok:
                _set_executor_type(task_id, "ollama_local")
                _ollama_completed.add(task_id)
                print(f"  Kanban: dispatched {task_id} via Ollama local")
                dispatched = True
                break

    if not dispatched:
        if _fallback_chain and _fallback_chain[-1] == "ollama_local":
            _no_exec_reason = (
                "no executor available: internet=False, "
                "gitlab=unreachable, ollama=unreachable"
            )
            try:
                _conn = get_connection()
                _conn.execute(
                    "UPDATE kanban_tasks SET last_failure_reason = ?, "
                    "updated_at = ? WHERE id = ?",
                    (_no_exec_reason, _utcnow_iso(), task_id),
                )
                _conn.commit()
                _conn.close()
            except Exception as _lfr_exc:
                logger.warning(
                    "kanban: failed to set last_failure_reason for %s: %s",
                    task_id, _lfr_exc,
                )
            _move_task(task_id, "suggested", actor="scheduler", reason=_no_exec_reason)
            print(
                f"  Kanban: {task_id} NO EXECUTOR — "
                "moved to suggested (no internet, gitlab, or ollama available)"
            )
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

    # 2. uncommitted changes in the worktree — only valid when the task has
    # an actual registered worktree; checking BASE_DIR's dirty state would
    # produce false positives from unrelated in-progress work.
    if task_id in _worktrees:
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
    # worktree has real file changes or commits, trust that.
    #
    # Policy (2026-04-15 Batch 3): run the git-first check for EVERY task.
    # Dangerous tasks (deploy/delete/destructive) do NOT skip it — they
    # instead require BOTH git-first AND the full downstream chain to
    # pass. This tightens the verifier without creating new false-positive
    # surface for safe tasks.
    _git_ok, _git_reason = _git_worktree_has_real_changes(task_id)
    _is_dangerous = _is_dangerous_task(task_id)
    if _git_ok and not _is_dangerous:
        return True, f"Verified (git-first): {_git_reason}"
    # Dangerous task: git-first is a necessary condition but not sufficient.
    # Fall through to the full check chain; at the end we require the
    # git-first signal to have fired as well.
    if _is_dangerous and not _git_ok:
        return False, (
            "Dangerous task has no git-side evidence of work (no commits, "
            "no uncommitted changes, no recent main advance). Running full "
            "check chain would be pointless — failing fast."
        )

    # Check 0b — SCAN-ONLY EARLY EXIT: tasks like pytest, codelens, coherence
    # are read-only commands that produce no git commits. Claude CLI with
    # --output-format text only writes stdout at exit — when killed by timeout,
    # output is 0 bytes. For scan-only tasks, verify by checking:
    #   1. Process ran for a meaningful duration (not an immediate crash)
    #   2. Exit code was 0 (if available)
    #   3. Task description matches known scan commands
    # This runs BEFORE the output-length check to avoid false rejection.
    _SCAN_ONLY_KEYWORDS = [
        "pytest", "codelens", "coherence_checker", "health_check",
        "e2e_full_dashboard", "companion.py --sync", "companion sync",
        "regression", "report pass/fail",
    ]
    try:
        _c0b = get_connection()
        _r0b = _c0b.execute(
            "SELECT description, task_type FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        _c0b.close()
        _desc0b = ((_r0b["description"] or "").lower() if _r0b else "")
        _type0b = ((_r0b["task_type"] or "").lower() if _r0b else "")
    except Exception:
        _desc0b = ""
        _type0b = ""
    _is_scan_task = _type0b == "test" and any(kw in _desc0b for kw in _SCAN_ONLY_KEYWORDS)
    if _is_scan_task and (not claude_output or len(claude_output) < 200):
        # Scan task with empty/short output — check process exit code
        _proc = _running.get(task_id)
        _exit_ok = (_proc is not None and hasattr(_proc, 'returncode')
                    and _proc.returncode == 0)
        _dispatch_t = _dispatch_times.get(task_id)
        _ran_long = (
            _dispatch_t is not None
            and (datetime.now(timezone.utc) - _dispatch_t).total_seconds() > 60
        )
        if _exit_ok:
            return True, (
                "Verified (scan-only): process exited 0 — "
                "no git commits expected for read-only validation task"
            )
        if _ran_long:
            return True, (
                "Verified (scan-only): process ran >60s without crash — "
                "accepting as successful scan (stdout lost due to kill; "
                "no git commits expected)"
            )
        # If scan task crashed immediately (<60s, non-zero exit), fall through
        # to the normal checks which will reject it properly.

    # Check 0c — BYPASS COMPLETION EARLY EXIT: agent detected pre-existing
    # correct state and completed without any code changes. Two signals are
    # checked (either is sufficient):
    #   1. completed_via_bypass flag on the task row (primary — set by move
    #      API / _move_task when bypass_verification=true is supplied)
    #   2. kanban_verifications row with result='bypassed' (secondary —
    #      belt-and-suspenders for callers that write the verifications row
    #      but do not set the task flag)
    # Tasks completed via bypass produce no git commits by design — skip the
    # no-commits check entirely.
    try:
        _c0c = get_connection()
        # Primary signal: metadata flag on the task row — cheapest check.
        _bypass_meta = _c0c.execute(
            "SELECT completed_via_bypass FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if _bypass_meta and _bypass_meta["completed_via_bypass"]:
            _c0c.close()
            return True, (
                "Verified (bypass): completed_via_bypass flag set on task — "
                "no git commits expected (pre-existing correct state)"
            )
        # Secondary signal: verification row written by the move-to-done API.
        _brow = _c0c.execute(
            "SELECT reason FROM kanban_verifications "
            "WHERE task_id = ? AND result = 'bypassed' "
            "ORDER BY verified_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        _c0c.close()
        if _brow is not None:
            _bypass_reason_txt = (_brow["reason"] or "pre-existing correct state")[:80]
            return True, (
                f"Verified (bypass): task completed via bypass — "
                f"no git commits expected ({_bypass_reason_txt})"
            )
    except Exception:
        pass

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

    # Permission-blocked guard: detect before hard-fail markers so the task
    # is quarantined for human review instead of endlessly retried. The agent
    # cannot self-resolve a permission prompt — retrying is futile.
    _perm_blocked_signals = [
        "approve the write permission",
        "grant write permission",
        "please approve",
        "awaiting permission",
        "permission to write to the file",
        "need permission to write",
        "i need permission to",
        "request permission",
        "requires your permission",
        "waiting for permission",
        "waiting for your approval",
        "once you grant",
    ]
    if any(sig in output_lower for sig in _perm_blocked_signals):
        return False, "PERMISSION_BLOCKED: agent awaiting write approval — route to human review"

    for marker in hard_fail_markers:
        if marker.lower() in output_lower[:1000]:
            return False, f"Output contains failure indicator: {marker}"

    # Track soft "no-change" signal — task-specific verification will decide
    # whether this is a legitimate false-positive resolution or a cop-out.
    has_nochange_signal = any(
        m in output_lower[:2000] for m in soft_nochange_markers
    )

    # Task types that are expected to produce file-level output. Others
    # (research, test, chore) may legitimately produce only pass/fail.
    _FILE_CREATION_TASK_TYPES = {"feature", "fix", "build", "refactor"}

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
        # Only hard-fail for task types that SHOULD produce file changes.
        # research/test/chore tasks may legitimately produce only pass/fail
        # output (e.g. codelens scan, pytest run, dependency check) with
        # no file mutations. Per V&V policy these are fail-open.
        try:
            _c3 = get_connection()
            _r3 = _c3.execute(
                "SELECT task_type FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            _c3.close()
            _tt = (_r3["task_type"] or "").lower() if _r3 else ""
        except Exception:
            _tt = ""
        if _tt in _FILE_CREATION_TASK_TYPES:
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
    # EXCEPTION 2 (self-debug lesson): research/test/chore tasks may
    # legitimately produce zero file output (e.g. "verify dependency
    # available", "run a check"). Per V&V policy these are fail-open.
    # Only apply zero-path guard to task types that imply file creation.
    if not claimed_paths and not has_nochange_signal:
        try:
            _c = get_connection()
            _row = _c.execute(
                "SELECT description, task_type FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            _c.close()
            task_desc = (_row["description"] or "").lower() if _row else ""
            task_type = (_row["task_type"] or "").lower() if _row else ""
        except Exception:
            task_desc = ""
            task_type = ""
        if task_type not in _FILE_CREATION_TASK_TYPES:
            pass  # research/test/chore/etc — skip zero-path guard
        else:
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
        # "no changes needed" AND _verify_task_specific returns AFFIRMATIVE
        # confirmation (not the "not applicable" default), accept as
        # completed. False-positive gaps are legitimate completions.
        #
        # Batch 3 tightening (2026-04-15): the default ``return True,
        # "Task-specific checks passed or not applicable"`` from
        # ``_verify_task_specific`` is NOT sufficient on its own — an
        # unmatched task pattern always returns that, so any no-change
        # claim on an efa-* task would silently pass. Require that the
        # task-specific reason indicate a POSITIVE check ran (keyword
        # "Verified" / "exists" / "present" in reason) — the
        # "not applicable" or "skipping" variants are rejected.
        if has_nochange_signal:
            try:
                specific_ok, specific_reason = _verify_task_specific(task_id)
                specific_lower = (specific_reason or "").lower()
                is_affirmative = specific_ok and not any(
                    marker in specific_lower
                    for marker in (
                        "not applicable", "skipping", "skip ", "no check",
                        "skipped", "failed ("  # e.g. "Manifest read failed (...)"
                    )
                )
                if is_affirmative:
                    return True, (
                        "Verified: agent reported no changes needed AND "
                        f"task-specific state check confirmed ({specific_reason[:80]})"
                    )
                # Soft no-change signal without affirmative specific-check
                # confirmation → reject. Better to fail-closed on ambiguous
                # signals than to accept a phantom completion.
                return False, (
                    "Agent signaled no-change but task-specific check was "
                    f"non-affirmative ({specific_reason[:80]!r}) \u2014 "
                    "unable to confirm claimed state"
                )
            except Exception:
                pass

        # Fallback D0: diag- tasks are auto-created by self_debug reflex to
        # investigate a looping source task. They run tests/checks and write
        # findings to .tmp/kanban/<source>-findings.md (gitignored). They NEVER
        # modify production code, so no git commits are expected. Verify by
        # findings artifact existence or output diagnostic pass signals.
        # Root cause fixed: cdh-fix-04-d1 stuck in loop because verification
        # demanded git commits from a read-only diagnostic investigation task
        # (self_debug card diag-efff350a5b).
        if task_id.startswith("diag-"):
            _kanban_tmp = BASE_DIR / ".tmp" / "kanban"
            _diag_artifacts = list(_kanban_tmp.glob("*-findings.md")) if _kanban_tmp.exists() else []
            if _diag_artifacts:
                return True, (
                    f"Verified (diag): findings artifact exists "
                    f"({_diag_artifacts[0].name}) — no git commits expected "
                    "for self_debug diagnostic investigation task"
                )
            _diag_pass_signals = [
                "findings written to", "task marked done", "diagnosis found no errors",
                "no issues found", "all tests pass", "tests pass cleanly",
                " passed", "0 failed", "no failures", "no errors",
                "pass cleanly", "pass.", "done.",
            ]
            if any(sig in output_lower for sig in _diag_pass_signals):
                return True, (
                    "Verified (diag): output contains diagnostic PASS signal — "
                    "no git commits expected for self_debug investigation task"
                )

        # Fallback D: scan-only tasks (codelens, coherence check, etc.) write
        # results to .tmp/ (gitignored) and are NOT expected to produce git
        # commits — they verify code quality without modifying files. Trust the
        # task if its description names a known scan command AND the output
        # contains a clear PASS signal (or a result artifact exists on disk).
        # Root cause fixed: efa-E-gate-1-codelens stuck in loop because the
        # verification demanded git commits from a scan-only task (self_debug
        # card diag-970d8445f8).
        _SCAN_CMDS = [
            "codelens.py", "coherence_checker.py", "health_check.py",
            "e2e_full_dashboard.py",
            # Read-only validation commands (no git commits expected)
            "pytest", "pytest tests/", "regression pytest",
            "companion.py --sync", "companion sync",
        ]
        _scan_desc = ""
        try:
            _c_sd = get_connection()
            _r_sd = _c_sd.execute(
                "SELECT description FROM kanban_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            _c_sd.close()
            _scan_desc = ((_r_sd["description"] or "").lower() if _r_sd else "")
        except Exception:
            pass
        if any(cmd in _scan_desc for cmd in _SCAN_CMDS):
            # Strongest signal: result artifact file in .tmp/
            _tmp_dir = BASE_DIR / ".tmp"
            _id_prefix = re.sub(r"-(codelens|coherence|e2e|scan)$", "", task_id)
            _artifacts = (
                list(_tmp_dir.glob(f"codelens-{task_id}*.json"))
                + list(_tmp_dir.glob(f"codelens-{_id_prefix}*.json"))
            )
            if _artifacts:
                return True, f"Verified: scan artifact exists ({_artifacts[0].name})"
            # Artifact may be gone (ephemeral) — fall back to output PASS signal.
            # These strings are emitted by codelens.py / coherence_checker.py on
            # success and are specific enough to avoid false positives.
            _pass_signals = [
                '"status": "pass"', "gate: pass", "gate: **pass**",
                "scan complete \u2014 gate: pass", "codelens scan complete",
                "| status | **pass**", "codelens gate: pass",
                "coherence gate: pass", "health check: pass",
                # Brief Markdown forms produced by lightweight gate sub-tasks
                "codelens scan: pass", "**codelens scan: pass**",
                "coherence: pass", "coherence check: pass",
                "companion sync: pass", "pytest: pass",
                "e2e: pass", "regression pytest: pass",
                # pytest stdout patterns (e.g. "7519 passed", "== X passed ==")
                " passed", "tests passed", "passed,", "passed in ",
                "0 failed", "no failures", "all tests pass",
                # companion sync patterns
                "platforms_targeted", "files_written", "sync complete",
            ]
            if any(sig in output_lower for sig in _pass_signals):
                return True, (
                    "Verified: scan task output contains PASS signal "
                    "(no git commits expected for scan-only tasks)"
                )

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

    # diag- tasks are auto-created by self_debug reflex to INVESTIGATE a looping
    # source task. Their descriptions list "Suspect files:" — hypothetical files
    # flagged for review, NOT files the agent creates or modifies. Manifest and
    # route checks are therefore not applicable; accept immediately.
    # Root cause: diag-efff350a5b kept failing because its description mentioned
    # tools/kanban_verify.py as a suspect file, which triggered the manifest check
    # even though the diag task never creates or ships that file.
    if task_id.startswith("diag-"):
        return True, "diag- task: suspect-files list is not subject to manifest/route checks"

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
                if not manifest_text or tool_path not in manifest_text:
                    # Fallback to working-tree shards when the kanban branch
                    # either has no manifest files or is missing the specific
                    # entry (entry may have been committed to main, not the
                    # feature branch, which is the typical case post-merge).
                    parts = []
                    try:
                        parts.append(
                            (BASE_DIR / "tools" / "manifest.md").read_text(
                                encoding="utf-8"
                            )
                        )
                    except Exception:
                        pass
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
        # Skip "IF NOT EXISTS" so "Add CREATE TABLE IF NOT EXISTS foo" extracts "foo".
        table_match = re.search(
            r"(?:create\s+table|add\s+(?:table|DB\s+table|schema))[ \t]+"
            r"(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
            description,
            re.IGNORECASE,
        )
        if table_match:
            table_name = table_match.group(1)
            # Skip tokens that are SQL keywords or common English words that appear
            # near "CREATE TABLE IF NOT EXISTS" in descriptive prose — e.g.,
            # "use the CREATE TABLE IF NOT EXISTS pattern from migration X".
            _NON_TABLE_WORDS = frozenset({
                "if", "pattern", "template", "approach", "style",
                "format", "structure", "syntax", "statement", "clause",
            })
            if table_name.lower() in _NON_TABLE_WORDS:
                table_name = None
            # Blocklist: common English words that appear after "CREATE TABLE IF NOT EXISTS"
            # in prose descriptions (e.g. "use the CREATE TABLE IF NOT EXISTS pattern from
            # migration X") but are never actual table names.
            _PROSE_WORDS = {
                "pattern", "syntax", "template", "like", "similar", "example",
                "approach", "format", "style", "convention", "idiom", "method",
            }
            if table_name and table_name.lower() in _PROSE_WORDS:
                table_name = None
    if table_name:
        # For orphan_db_table fixes, the agent commits a new migration
        # file with CREATE TABLE <name>, but that migration has not been
        # applied to the working-tree DB at validation time. So check for
        # the CREATE TABLE statement in the agent's branch files instead
        # of querying the live DB.
        if "orphan_db_table" in desc_lower:
            import subprocess as _sp
            # Use POSIX ERE-safe syntax: [[:space:]] instead of \s, capturing
            # group (...) instead of non-capturing (?:...), no \b word boundary.
            # Git's -E uses POSIX ERE which does not support \s or (?:...).
            _safe = re.escape(table_name).replace(r"\-", r"[-]")
            pattern = (
                rf"CREATE[[:space:]]+TABLE[[:space:]]+"
                rf"(IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+)?{_safe}"
            )
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
                # Python-based fallback: search worktree directory directly.
                # git grep may fail when the branch is checked out in a
                # worktree (stale process state, PATH issues, POSIX ERE
                # engine differences). Reading files with Python is robust.
                import re as _re
                _py_pat = _re.compile(
                    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table_name)}\b",
                    _re.IGNORECASE,
                )
                for _root in [
                    BASE_DIR / ".tmp" / "worktrees" / task_id,
                    BASE_DIR,
                ]:
                    if found:
                        break
                    for _sub in ("tools/db", "tools"):
                        _d = _root / _sub
                        if not _d.is_dir():
                            continue
                        for _ext in ("*.py", "*.sql"):
                            for _fp in _d.rglob(_ext):
                                try:
                                    if _py_pat.search(
                                        _fp.read_text(encoding="utf-8", errors="replace")
                                    ):
                                        found = True
                                        break
                                except OSError:
                                    continue
                            if found:
                                break
                        if found:
                            break
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
    elif work_dir:
        # Worktree path recorded but directory is gone (deleted by a prior
        # _reset_broken_worktree or Windows rmtree). Falling back to BASE_DIR
        # would validate the wrong directory and could incorrectly mark the
        # task done. Return the worktree-missing signal so auto_remediate
        # can prune git state and let the next dispatch rebuild from HEAD.
        _empty: Dict[str, Any] = {
            "codelens_passed": None, "ruff_issues": 0, "bandit_issues": 0,
            "coherence_passed": None, "e2e_ran": False, "e2e_passed": None,
            "companion_synced": False, "modified_files": 0, "modified_py": 0,
            "budget_sec": 0, "elapsed_sec": 0,
        }
        return False, "worktree missing on disk — rebuild required", _empty
    else:
        cwd = str(BASE_DIR)

    # Identify files changed by the agent (branch vs main)
    branch_name = f"kanban/{task_id}"
    try:
        result = _sp.run(
            ["git", "diff", "--name-only", f"{_default_branch()}...{branch_name}"],
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
    # guard-budget: compute remaining budget once for all post-process gates
    _budget_dispatch_time = _dispatch_times.get(task_id)
    _budget_elapsed = (
        (datetime.now(timezone.utc) - _budget_dispatch_time).total_seconds()
        if _budget_dispatch_time is not None else 0.0
    )
    _budget_remaining = _get_task_timeout(task_id) - _budget_elapsed

    work_dir = _worktrees.get(task_id) or str(BASE_DIR)

    if not verified and not _is_phantom:
        # guard-budget: skip remediation (and re-verification) if total elapsed
        # time is within REMEDIATION_MIN_BUDGET_SECONDS of the hard cap.
        # Remediation + re-verify can add another 30-50s; aborting prevents
        # the task from overshooting MAX_EXECUTION_SECONDS.
        if _budget_remaining < REMEDIATION_MIN_BUDGET_SECONDS:
            logger.warning(
                "guard-budget: %s skipping remediation — only %.0fs remaining "
                "(need %ds); proceeding to self-debug gate",
                task_id, _budget_remaining, REMEDIATION_MIN_BUDGET_SECONDS,
            )
            reason = f"{reason} | BUDGET_SKIP_REMEDIATION ({_budget_remaining:.0f}s remaining)"
        else:
            try:
                from tools.workflow.auto_remediate import attempt_remediation
                # Get the list of files the agent touched (for targeted ruff/manifest)
                import subprocess as _sp
                diff = _sp.run(
                    ["git", "diff", "--name-only", f"{_default_branch()}...kanban/{task_id}"],
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

        # self-debug reflex: if the same failure signature recurs, stop
        # looping, capture state, ask an LLM to diagnose, create an Oracle
        # RCA card, and quarantine this task.
        if not verified:
            # guard-budget: self-debug dispatch is lightweight but still
            # consumes time; skip if we're within SELF_DEBUG_MIN_BUDGET_SECONDS
            # of the hard cap to avoid overshooting MAX_EXECUTION_SECONDS.
            # Re-use _budget_remaining computed above (same task, same clock).
            if _budget_remaining < SELF_DEBUG_MIN_BUDGET_SECONDS:
                logger.warning(
                    "guard-budget: %s skipping self-debug — only %.0fs remaining "
                    "(need %ds)",
                    task_id, _budget_remaining, SELF_DEBUG_MIN_BUDGET_SECONDS,
                )
                reason = f"{reason} | BUDGET_SKIP_SELF_DEBUG ({_budget_remaining:.0f}s remaining)"
            else:
                try:
                    from tools.workflow.self_debug import check_and_diagnose
                    diag = check_and_diagnose(task_id, reason, work_dir)
                    if diag:
                        logger.warning(
                            "self_debug: quarantined %s — %s (card %s)",
                            task_id, diag.get("root_cause", "?"),
                            diag.get("diagnosis_card_id"),
                        )
                        reason = f"{reason} | SELF_DEBUG: {diag.get('root_cause', '?')} (card {diag.get('diagnosis_card_id')})"
                except Exception as exc:
                    logger.warning("self_debug reflex error for %s: %s", task_id, exc)

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
    # Bypass completions made no code changes — post-task validation (coherence/E2E)
    # would run against the unmodified base codebase and any pre-existing issue
    # would incorrectly block the task. Skip it entirely for bypass tasks.
    _is_bypass = "bypass" in reason.lower()
    if verified:
        specific_ok, specific_reason = _verify_task_specific(task_id)
        if not specific_ok:
            verified = False
            reason = f"{reason} | {specific_reason}"
        else:
            reason = f"{reason} | {specific_reason}"
    if verified and not _is_bypass:
        validation_ok, validation_reason, metrics = _run_post_task_validation(task_id)
        if not validation_ok:
            verified = False
            reason = f"{reason} | VALIDATION FAILED: {validation_reason}"
        else:
            reason = f"{reason} | {validation_reason}"
    return verified, reason, metrics


# Track when each task was dispatched: {task_id: datetime}
_dispatch_times: Dict[str, datetime] = {}

# Current executor tier — updated once per scheduler cycle
_current_exec_tier: Optional[str] = None

_SILENT_DISPATCH_THRESHOLD = 5 * 60  # 5 min — no log file content yet = never dispatched
_ABSOLUTE_MAX_IN_PROGRESS_SECONDS = 24 * 60 * 60  # 24 h hard ceiling — force-reap even if in _running


def _task_log_is_empty(tid: str) -> bool:
    """Return True if the task's .tmp/kanban/<id>.log is absent or has no content."""
    log_path = Path(__file__).resolve().parent.parent.parent / ".tmp" / "kanban" / f"{tid}.log"
    try:
        return not log_path.exists() or log_path.stat().st_size == 0
    except Exception:
        return False


_SUGGESTED_DECAY_HOURS = 48  # tasks soft-stuck in 'suggested' are re-queued after this


def _promote_stale_suggested() -> None:
    """Decay sweep: re-queue 'suggested' tasks that have been stuck >48 h
    and are NOT hard-quarantined (failure_count < 5 and last_failure_reason
    does not contain 'hard-quarantine' or 'hitl').

    Prevents tasks from rotting in 'suggested' forever when the underlying
    issue resolves on its own (transient resource exhaustion, flaky E2E,
    resolved dependency). Hard-quarantined tasks (fc >= 5 or explicit
    hard-quarantine reason) still require human review.
    """
    try:
        conn = get_connection()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_SUGGESTED_DECAY_HOURS)
        ).isoformat()
        rows = conn.execute(
            "SELECT id, failure_count, last_failure_reason FROM kanban_tasks "
            "WHERE status = 'suggested' AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        promoted = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in rows:
            d = dict(r)
            fc = d.get("failure_count") or 0
            reason = (d.get("last_failure_reason") or "").lower()
            if fc >= 5 or "hard-quarantine" in reason or "hitl" in reason:
                continue  # genuinely quarantined — leave for human review
            conn.execute(
                "UPDATE kanban_tasks SET status='scheduled', scheduled_at=?, "
                "updated_at=?, failure_count=0, "
                "last_failure_reason='decay-promoted: re-queued after 48 h in suggested' "
                "WHERE id=?",
                (now_iso, now_iso, d["id"]),
            )
            promoted.append(d["id"])
        if promoted:
            conn.commit()
            logger.info("suggested-decay: re-queued %d task(s): %s", len(promoted), promoted)
            for tid in promoted:
                print(f"  Kanban: suggested-decay promoted {tid} -> scheduled")
        conn.close()
    except Exception as exc:
        logger.warning("suggested-decay sweep failed: %s", exc)


def _reap_stale_in_progress() -> None:
    """Periodic reaper: reset in_progress tasks not tracked in _running.

    Catches three failure modes that survive past startup-recovery:
      1. Process died mid-run after dispatch (PID gone, DB still in_progress).
      2. Verification gate left the task in_progress with 'human review needed'
         instead of resetting to backlog.
      3. Silent dispatch failure — task promoted to in_progress but subprocess
         never started (execution_id still NULL, log file empty). Fast-reaped
         after _SILENT_DISPATCH_THRESHOLD (5 min) so the board never shows a
         ghost in_progress for more than one scheduler cycle window.

    Normal threshold: 2× task timeout (30–80 min).
    Silent-dispatch threshold: 5 min (log empty + not in _running).
    Only resets tasks NOT currently in _running to avoid killing live agents.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, failure_count FROM kanban_tasks "
            "WHERE status = 'in_progress'"
        ).fetchall()
        if not rows:
            conn.close()
            return

        now = datetime.now(timezone.utc)
        reaped = []
        for r in rows:
            d = dict(r)
            tid = d["id"]

            # Fetch updated_at separately to get the real timestamp
            ts_row = conn.execute(
                "SELECT updated_at FROM kanban_tasks WHERE id = ?", (tid,)
            ).fetchone()
            if not ts_row:
                continue
            updated_raw = dict(ts_row)["updated_at"]
            if updated_raw is None:
                continue

            # Parse updated_at
            try:
                if hasattr(updated_raw, "tzinfo"):
                    updated_at = updated_raw if updated_raw.tzinfo else updated_raw.replace(tzinfo=timezone.utc)
                else:
                    from dateutil.parser import parse as _dp
                    updated_at = _dp(str(updated_raw))
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            age_seconds = (now - updated_at).total_seconds()

            # Hard ceiling: any task in_progress for >24 h is force-reaped even
            # if it appears to have a live subprocess. A genuine 24 h run does
            # not exist; this catches hung processes whose PID is still in
            # _running but whose work long since stalled (scheduler-crash + restart
            # race, grandchild processes that survived a kill, etc.).
            if age_seconds >= _ABSOLUTE_MAX_IN_PROGRESS_SECONDS:
                threshold = _ABSOLUTE_MAX_IN_PROGRESS_SECONDS
                reap_label = "absolute-max-age (>24 h)"
                if tid in _running:
                    try:
                        _running[tid].kill()
                    except Exception:
                        pass
                    _running.pop(tid, None)
            elif tid in _running:
                continue  # live subprocess within normal budget — skip

            # Fast-reap silent dispatch: task is not in _running AND log file
            # is still empty — subprocess never wrote a single byte, so it
            # never actually started. Use a short 5-min window instead of the
            # normal 2× budget to catch these within the next cycle or two.
            elif _task_log_is_empty(tid) and age_seconds >= _SILENT_DISPATCH_THRESHOLD:
                threshold = _SILENT_DISPATCH_THRESHOLD
                reap_label = "silent-dispatch (no log output)"
            else:
                threshold = _get_task_timeout(tid) * 2  # 2× normal budget = 30–80 min
                reap_label = "no live subprocess"

            if age_seconds < threshold:
                continue  # task is recent enough — let it run

            now_iso = now.isoformat()
            # Check current failure count before incrementing — if this
            # reap would bring fc to ≥5, escalate to 'suggested' for HITL
            # review instead of infinite backlog retry (fc≥5 quarantine).
            fc_row = conn.execute(
                "SELECT COALESCE(failure_count, 0) FROM kanban_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
            new_fc = (fc_row[0] if fc_row else 0) + 1
            next_status = "suggested" if new_fc >= 5 else "backlog"
            reason = (
                f"stale-reaper: task was in_progress for {age_seconds / 60:.0f} min "
                f"with {reap_label} (threshold={threshold / 60:.0f} min). "
                + (
                    f"fc={new_fc}>=5 - escalated to suggested for HITL review."
                    if next_status == "suggested"
                    else "Automatically reset to backlog for re-dispatch."
                )
            )
            conn.execute(
                "UPDATE kanban_tasks SET "
                "  status = ?, "
                "  failure_count = COALESCE(failure_count, 0) + 1, "
                "  last_failure_reason = ?, "
                "  last_failure_at = ?, "
                "  updated_at = ? "
                "WHERE id = ? AND status = 'in_progress'",
                (next_status, reason, now_iso, now_iso, tid),
            )
            reaped.append(tid)
            print(
                f"  Kanban: stale-reaper reset {tid} "
                f"(in_progress {age_seconds / 60:.0f} min, {reap_label}) -> {next_status}"
            )

        if reaped:
            conn.commit()
            logger.info("stale-reaper: reset %d orphaned in_progress task(s): %s", len(reaped), reaped)
        conn.close()
    except Exception as exc:
        logger.warning("stale-reaper sweep error: %s", exc)


# Startup-recovery flag: True after the first cycle's stale-in_progress sweep runs.
_startup_recovery_done: bool = False


def _startup_recover_stale_in_progress() -> None:
    """On first cycle after a scheduler restart, reset any tasks stuck in
    'in_progress' back to 'backlog'.  After a crash, _running is empty but
    the DB still has rows from the previous session — they will never be
    promoted or timed-out without this sweep.
    """
    global _startup_recovery_done
    if _startup_recovery_done:
        return
    _startup_recovery_done = True
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title FROM kanban_tasks WHERE status = 'in_progress'"
        ).fetchall()
        if not rows:
            conn.close()
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        reason = (
            "startup-recovery: task was in_progress when the scheduler "
            "restarted — process died or scheduler crashed mid-run."
        )
        for r in rows:
            tid = dict(r)["id"]
            if tid in _running:
                continue  # live process from this session — skip
            conn.execute(
                "UPDATE kanban_tasks SET status='backlog', "
                "last_failure_reason=?, updated_at=? WHERE id=?",
                (reason, now_iso, tid),
            )
            print(f"  Kanban: startup-recovery reset {tid} in_progress -> backlog")
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("startup-recovery sweep failed: %s", exc)


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
            # Per-task timeout: pytest tasks get 30 min; everything else 15 min.
            task_budget = _get_task_timeout(task_id)
            if elapsed > task_budget:
                print(
                    f"  Kanban: {task_id} TIMEOUT after "
                    f"{int(elapsed)}s (max {task_budget}s) "
                    f"— killing process"
                )
                try:
                    proc.kill()
                    proc.wait(timeout=10)
                except Exception:
                    pass

                # ── SCAN-ONLY TIMEOUT ACCEPTANCE ─────────────────────
                # Scan tasks (pytest, codelens, coherence, companion) are
                # read-only: they produce no git commits and Claude CLI's
                # --output-format text yields empty stdout when killed.
                # If the task ran for >90% of its budget, the underlying
                # command almost certainly completed — Claude was just
                # formatting the response when killed.  Accept as done.
                _SCAN_KW_TIMEOUT = ["pytest", "codelens", "coherence",
                                    "companion", "report pass/fail"]
                try:
                    _stc = get_connection()
                    _str = _stc.execute(
                        "SELECT description, task_type FROM kanban_tasks WHERE id = ?",
                        (task_id,),
                    ).fetchone()
                    _stc.close()
                    _stdesc = ((_str["description"] or "").lower() if _str else "")
                    _sttype = ((_str["task_type"] or "").lower() if _str else "")
                except Exception:
                    _stdesc = ""
                    _sttype = ""
                _is_scan_timeout = (
                    _sttype == "test"
                    and any(kw in _stdesc for kw in _SCAN_KW_TIMEOUT)
                    and elapsed > task_budget * 0.9
                )
                if _is_scan_timeout:
                    _move_task(task_id, "done", actor="scheduler",
                              reason=(f"Verified (scan-only timeout): ran {int(elapsed)}s "
                                      f"(budget {task_budget}s) — read-only validation "
                                      f"task accepted without git commits"))
                    print(
                        f"  Kanban: {task_id} SCAN-ONLY ACCEPTED — "
                        f"ran {int(elapsed)}s of {task_budget}s budget"
                    )
                    del _running[task_id]
                    _dispatch_times.pop(task_id, None)
                    if task_id in _worktrees:
                        _cleanup_worktree(task_id)
                        del _worktrees[task_id]
                    completed.append(task_id)
                    continue

                # guard-timeout-retry: track how many times this exact task has
                # been killed for timeout. After MAX_TIMEOUT_RETRIES identical
                # timeouts, hard-quarantine to 'suggested' so the scheduler
                # stops burning 900s slots on a structurally broken task.
                # This fires BEFORE self_debug's recurrence check (threshold=3)
                # so both mechanisms are belt-and-suspenders.
                _tout_count = _increment_timeout_count(task_id)
                _timeout_reason = (
                    f"TIMEOUT after {int(elapsed)}s "
                    f"(max {task_budget}s) — task exceeded dispatch budget"
                )
                # Increment failure_count so the task health signal is accurate
                try:
                    _fc_conn = get_connection()
                    _fc_now = datetime.now(timezone.utc).isoformat()
                    _fc_conn.execute(
                        "UPDATE kanban_tasks SET "
                        "failure_count = COALESCE(failure_count, 0) + 1, "
                        "last_failure_reason = ?, last_failure_at = ? "
                        "WHERE id = ?",
                        (_timeout_reason, _fc_now, task_id),
                    )
                    _fc_conn.commit()
                    _fc_conn.close()
                except Exception as _fc_exc:
                    logger.warning("failure_count update failed for %s: %s", task_id, _fc_exc)
                if _tout_count >= MAX_TIMEOUT_RETRIES:
                    _move_task(
                        task_id, "suggested",
                        actor="scheduler",
                        reason=(
                            f"hard-quarantine: timed out {_tout_count}× "
                            f"(max {MAX_TIMEOUT_RETRIES}) — "
                            + _timeout_reason
                        ),
                    )
                    _clear_timeout_count(task_id)
                    print(
                        f"  Kanban: {task_id} HARD-QUARANTINE — "
                        f"timed out {_tout_count}× ({MAX_TIMEOUT_RETRIES} max); "
                        f"moved to suggested"
                    )
                else:
                    _move_task(task_id, "backlog")
                    # Backoff delay: 5 min × retry count before next dispatch.
                    # Prevents a structurally-slow task from immediately burning
                    # another 900 s slot on the very next scheduler cycle.
                    _backoff_seconds = _tout_count * 5 * 60
                    _backoff_at = (datetime.now(timezone.utc) + timedelta(seconds=_backoff_seconds)).isoformat()
                    try:
                        _bo_conn = get_connection()
                        _bo_conn.execute(
                            "UPDATE kanban_tasks SET scheduled_at = ? WHERE id = ?",
                            (_backoff_at, task_id),
                        )
                        _bo_conn.commit()
                        _bo_conn.close()
                    except Exception as _bo_exc:
                        logger.warning("backoff scheduled_at update failed for %s: %s", task_id, _bo_exc)
                    print(
                        f"  Kanban: {task_id} timeout {_tout_count}/"
                        f"{MAX_TIMEOUT_RETRIES} — backoff {_backoff_seconds // 60} min"
                    )
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
                    _tg_dest = "quarantined" if _tout_count >= MAX_TIMEOUT_RETRIES else "backlog"
                    tg_send(
                        f"TIMEOUT: {task_dict.get('title', task_id)[:60]}",
                        f"Task killed after {int(elapsed)}s — {_tg_dest} "
                        f"(timeout {_tout_count}/{MAX_TIMEOUT_RETRIES})",
                        severity="warning",
                    )
                except Exception:
                    pass
                # self-debug reflex: timeouts are their own recurrence class
                try:
                    from tools.workflow.self_debug import check_and_diagnose
                    work_dir = _worktrees.get(task_id) or str(BASE_DIR)
                    check_and_diagnose(task_id, _timeout_reason, work_dir)
                except Exception as exc:
                    logger.warning("self_debug reflex error on timeout for %s: %s", task_id, exc)
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
                # guard-budget: pre-flight check before entering verification
                # pipeline. Verification (including potential remediation and
                # self-debug) can take 30-60s. If the total elapsed time since
                # dispatch is already within VERIFICATION_MIN_BUDGET_SECONDS of
                # MAX_EXECUTION_SECONDS we'd overshoot the budget — return to
                # backlog instead so the task gets a fresh 900s slot next time.
                _vb_dispatch_time = _dispatch_times.get(task_id)
                if _vb_dispatch_time is not None:
                    _vb_elapsed = (datetime.now(timezone.utc) - _vb_dispatch_time).total_seconds()
                    _vb_remaining = _get_task_timeout(task_id) - _vb_elapsed
                    if _vb_remaining < VERIFICATION_MIN_BUDGET_SECONDS:
                        print(
                            f"  Kanban: {task_id} BUDGET EXHAUSTED before verification "
                            f"({_vb_remaining:.0f}s remaining, need {VERIFICATION_MIN_BUDGET_SECONDS}s) "
                            f"— returning to backlog"
                        )
                        logger.warning(
                            "guard-budget: %s skipping verification — only %.0fs remaining "
                            "(need %ds); returning to backlog",
                            task_id, _vb_remaining, VERIFICATION_MIN_BUDGET_SECONDS,
                        )
                        _move_task(task_id, "backlog",
                                   actor="scheduler",
                                   reason=f"budget exhausted before verification ({_vb_remaining:.0f}s remaining)")
                        del _running[task_id]
                        _dispatch_times.pop(task_id, None)
                        if task_id in _worktrees:
                            _cleanup_worktree(task_id)
                            del _worktrees[task_id]
                        continue

                # guard-cwd: if the worktree was deleted between dispatch and
                # verification (Windows file-lock cleanup, concurrent sweep,
                # etc.), rebuild it now so validation runs in the right dir.
                # Acceptance criterion: cwd_exists=false + is_worktree_path=true
                # → trigger rebuild immediately before the verification gate.
                _wt_path = _worktrees.get(task_id)
                if _wt_path:
                    _wt = Path(_wt_path)
                    _is_wt = ".tmp" in _wt.parts and "worktrees" in _wt.parts
                    if _is_wt and not _wt.exists():
                        logger.warning(
                            "guard-cwd: worktree missing for %s (%s) "
                            "— rebuilding before verification",
                            task_id, _wt_path,
                        )
                        print(
                            f"  Kanban: {task_id} worktree missing "
                            f"— rebuilding before verification"
                        )
                        _rebuilt = _create_worktree(task_id)
                        if _rebuilt:
                            _worktrees[task_id] = _rebuilt
                            logger.info(
                                "guard-cwd: worktree rebuilt at %s for %s",
                                _rebuilt, task_id,
                            )
                        else:
                            logger.warning(
                                "guard-cwd: worktree rebuild failed for %s "
                                "— verification will use BASE_DIR",
                                task_id,
                            )
                            del _worktrees[task_id]

                # VERIFICATION GATE — prevent false positives.
                # Batch 4 atomic-ish wrap (2026-04-15): verify first, then
                # state-change. If _move_task raises, we DO NOT swallow the
                # exception silently — that was the class of bug where a
                # verified task could stay in_progress after DB glitches.
                verified, reason = _verify_task_completed(task_id, claude_output)

                if verified:
                    try:
                        _move_task(task_id, "done",
                                   actor="scheduler",
                                   reason=f"verified: {reason[:80]}")
                    except Exception as _mt_exc:
                        # Loud fail: task stays in_progress; next cycle's
                        # orphan detection / stale-dispatch sweep will pick
                        # it up. Logging beats silent pass.
                        logger.error(
                            "_move_task(done) failed for %s after verified=True: %s",
                            task_id, _mt_exc,
                        )
                    _clear_retry_count(task_id)
                    _clear_resume_at(task_id)
                    _clear_timeout_count(task_id)
                else:
                    print(f"  Kanban: {task_id} UNVERIFIED: {reason}")
                    # Permission-blocked: agent cannot self-resolve a write
                    # permission prompt. Retrying is futile — quarantine to
                    # 'suggested' for human review instead of burning retries.
                    if reason.startswith("PERMISSION_BLOCKED:"):
                        try:
                            _move_task(
                                task_id, "suggested",
                                actor="scheduler",
                                reason=reason[:200],
                            )
                        except Exception as _mt_exc:
                            logger.error(
                                "_move_task(suggested/perm-blocked) failed for %s: %s",
                                task_id, _mt_exc,
                            )
                        try:
                            from tools.notifications.adapters.telegram import (
                                send as tg_send,
                            )
                            tg_send(
                                f"PERMISSION BLOCKED: {task_dict.get('title', task_id)[:60]}",
                                f"Task quarantined — agent awaiting write approval.\n{reason}",
                                severity="warning",
                            )
                        except Exception:
                            pass
                        print(f"  Kanban: {task_id} PERMISSION-BLOCKED — moved to suggested")
                    else:
                        # guard-18: track failure count; flag for decomposition
                        # after repeated failures (task is probably too big).
                        new_status = _record_failure_and_maybe_flag(task_id, reason)
                        try:
                            _move_task(task_id, new_status,
                                       actor="scheduler",
                                       reason=f"unverified: {reason[:80]}")
                        except Exception as _mt_exc:
                            logger.error(
                                "_move_task(%s) failed for %s: %s",
                                new_status, task_id, _mt_exc,
                            )

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
                _clear_timeout_count(task_id)
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


def _close_orphaned_decomposed() -> None:
    """Step 3d: Auto-close decomposed parents that have no live children.

    When the LLM decomposer sets a parent to 'decomposed' but fails to create
    child tasks, the parent is orphaned — nothing ever closes it and it renders
    as 'Backlog' in the kanban UI (JS buckets unknown statuses there).

    A decomposed task is orphaned when:
      - status = 'decomposed'
      - no children (kanban_tasks WHERE depends_on_task_id = id AND status != 'done')
      - updated_at older than 5 minutes (give the decomposer time to finish)

    Safe to auto-close: if children exist and are all done, the parent should
    already be closed by _auto_close_decomposed_parent — this catches the ones
    that slipped through.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, title FROM kanban_tasks WHERE status = 'decomposed' "
                "AND updated_at < NOW() - INTERVAL '5 minutes'"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("orphaned_decomposed: DB read failed: %s", exc)
        return

    if not rows:
        return

    now = _utcnow_iso()
    for row in rows:
        task = dict(row)
        tid = task["id"]
        try:
            conn2 = get_connection()
            try:
                live_children = conn2.execute(
                    "SELECT COUNT(*) AS cnt FROM kanban_tasks "
                    "WHERE depends_on_task_id = %s AND status != 'done'",
                    (tid,),
                ).fetchone()
                live_count = dict(live_children)["cnt"] if live_children else 0
                if live_count == 0:
                    conn2.execute(
                        "UPDATE kanban_tasks SET status = 'done', completed_at = %s, "
                        "updated_at = %s WHERE id = %s AND status = 'decomposed'",
                        (now, now, tid),
                    )
                    conn2.commit()
                    logger.info(
                        "orphaned_decomposed: auto-closed %s (no live children)", tid
                    )
                    print(f"  Kanban: orphaned-decomposed {tid!r} auto-closed (no live children)")
            finally:
                conn2.close()
        except Exception as exc:
            logger.warning("orphaned_decomposed: failed to close %s: %s", tid, exc)


def _auto_decompose_stalled_tasks() -> list:
    """Step 3c: LLM-powered decomposer for needs_decomposition tasks.

    For each needs_decomposition task:
    1. If other tasks depend on it (chain-blocker), reset it directly to backlog
       with reset_count incremented so the scheduler retries it immediately.
    2. Otherwise, call the LLM to break it into 2-5 subtasks.
    3. If the LLM fails for any reason, fall back to a direct backlog reset so
       the task retries rather than staying stuck forever.

    Returns list of parent IDs processed (reset or decomposed).
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM kanban_tasks WHERE status = 'needs_decomposition' "
                "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, created_at ASC LIMIT 3"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("auto_decompose: DB read failed: %s", exc)
        return []

    if not rows:
        return []

    processed = []
    for row in rows:
        task = dict(row)
        tid = task["id"]
        try:
            # Check if this task is a chain-blocker (other tasks depend on it).
            # If so, bypass LLM decomposition and reset directly to backlog —
            # blocking the chain is worse than a direct retry.
            is_blocker = _is_chain_blocker(tid)
            if is_blocker:
                _reset_to_backlog(tid, reason="chain-blocker reset by auto_decompose")
                print(f"  Kanban: chain-blocker {tid!r} reset to backlog (has dependents)")
                processed.append(tid)
                continue

            _decompose_one_task(task)
            processed.append(tid)
        except Exception as exc:
            logger.warning("auto_decompose: LLM failed for %s: %s — falling back to backlog reset", tid, exc)
            # Fallback: reset to backlog so the scheduler retries directly
            # rather than leaving the task permanently stuck.
            try:
                _reset_to_backlog(tid, reason=f"LLM decompose failed: {exc}")
                processed.append(tid)
            except Exception as reset_exc:
                logger.warning("auto_decompose: backlog reset also failed for %s: %s", tid, reset_exc)

    return processed


def _is_chain_blocker(task_id: str) -> bool:
    """Return True if any other task depends on task_id."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM kanban_tasks WHERE depends_on_task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def _reset_to_backlog(task_id: str, reason: str = "") -> None:
    """Reset a needs_decomposition task to backlog, bypassing the 10-minute cooldown.

    Back-dates updated_at by 11 minutes so _get_due_tasks() picks it up on the
    very next cycle (cooldown window is 10 minutes, checked via updated_at).
    Does NOT increment failure_count — this is a recovery reset, not a new failure.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE kanban_tasks SET status = 'backlog', "
            "last_failure_reason = ?, "
            # Back-date updated_at so the 10-min cooldown passes immediately
            "updated_at = datetime('now', '-11 minutes') "
            "WHERE id = ? AND status = 'needs_decomposition'",
            (reason[:500] if reason else None, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def _complexity_score(task: dict) -> int:
    """Heuristic complexity score — no LLM, no I/O.

    Returns an int 0-10. Score ≥ 7 → decompose upfront before first dispatch.
    Threshold raised from 4 → 7: the old threshold triggered on virtually any
    well-described build task (words>120 +2, build+words>80 +2 = 4 already),
    burning LLM tokens on the decomposer and cascading subtask chains.

    Signals:
      +2  description word count > 200  (very long — multiple distinct tasks)
      +1  description word count > 120
      +2  ≥ 5 bullet/numbered items    (many distinct steps — not just 3)
      +1  ≥ 3 bullet/numbered items
      +1  title contains "and" / "&" / "+" (compound scope)
      +2  ≥ 5 distinct file paths mentioned (*.py / *.html / *.yaml / *.md)
      +1  ≥ 3 distinct file paths mentioned
      +1  title or description contains "redesign", "overhaul", "full migration",
          "rewrite" (truly broad-scope verbs; "implement"/"integrate" removed —
          those are normal single-task verbs)
    Note: build+words no longer stacks — removed to stop over-triggering.
    """
    import re as _re

    title = (task.get("title") or "").lower()
    desc = (task.get("description") or "")
    failure_count = int(task.get("failure_count") or 0)

    # Tasks that already failed once skip pre-dispatch decompose
    # (they go through _record_failure_and_maybe_flag instead).
    if failure_count > 0:
        return 0

    words = len(desc.split())
    score = 0

    if words > 200:
        score += 2
    elif words > 120:
        score += 1

    bullets = len([l for l in desc.splitlines()
                   if _re.match(r"^\s*[-*•]|\s*\d+\.", l)])
    if bullets >= 5:
        score += 2
    elif bullets >= 3:
        score += 1

    if any(tok in title for tok in (" and ", " & ", " + ")):
        score += 1

    file_refs = _re.findall(r"[\w/\-]+\.(?:py|html|yaml|yml|md|ts|js|go)", desc)
    if len(file_refs) >= 5:
        score += 2
    elif len(file_refs) >= 3:
        score += 1

    broad_verbs = ("redesign", "overhaul", "full migration", "rewrite")
    if any(v in title or v in desc.lower() for v in broad_verbs):
        score += 1

    return score


def _decompose_one_task(task: dict) -> None:
    """Call LLM to decompose a single needs_decomposition task into subtasks."""
    from tools.llm.router import LLMRouter, LLMRequest

    tid = task["id"]
    title = task.get("title") or tid
    description = task.get("description") or ""
    priority = task.get("priority") or "medium"
    task_type = task.get("task_type") or "build"
    failure_count = int(task.get("failure_count") or 0)
    failure_reason = task.get("last_failure_reason") or "Task exceeded single-session capacity."

    # Determine valid child task_type (constrained by DB CHECK)
    VALID_TYPES = {"build", "run", "fix", "research", "deploy", "test", "chore"}
    child_type = task_type if task_type in VALID_TYPES else "build"

    is_upfront = failure_count == 0

    system_prompt = (
        "You are an expert software task decomposer. Your job is to break a task into "
        "the smallest possible ATOMIC subtasks — each must do ONE thing, touch at most "
        "2-3 files, and complete in under 10 minutes of agent work.\n\n"
        "RULES (enforce strictly):\n"
        "1. Each subtask has a SINGLE acceptance criterion — one verifiable outcome.\n"
        "2. Each subtask touches at most 2-3 files. If more files are needed, split further.\n"
        "3. Title must be a specific action: 'Add X to Y', 'Fix Z in W', not 'Implement feature'.\n"
        "4. Subtasks are ordered: each builds on the previous (list in execution order).\n"
        "5. No 'scaffolding' or 'setup' tasks that just create empty files — do real work.\n"
        "6. Prefer 3-5 focused subtasks over 2 large ones — smaller is always safer.\n\n"
        "Output ONLY valid JSON — a list of objects with keys: "
        "'title' (str, max 120 chars), 'description' (str, include the specific files to "
        "touch and the exact acceptance criterion), 'task_type' "
        f"(one of: {sorted(VALID_TYPES)}), 'priority' (one of: critical, high, medium, low). "
        "No markdown, no explanation, just the JSON array."
    )

    if is_upfront:
        decompose_instruction = (
            "Decompose this proactively into 3-5 atomic subtasks BEFORE any attempt is made. "
            "The goal is to get each subtask right on the first try with no retries."
        )
    else:
        decompose_instruction = (
            f"This task FAILED with: {failure_reason}\n"
            "Decompose into 3-5 atomic subtasks. Each must be small enough that an agent "
            "cannot possibly miss it. Address the failure reason in your decomposition."
        )

    user_prompt = (
        f"Task ID: {tid}\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Priority: {priority}\n"
        f"Task type: {task_type}\n\n"
        f"{decompose_instruction}"
    )

    try:
        router = LLMRouter()
        req = LLMRequest(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1200,
        )
        raw = router.invoke("kanban_decompose", req)
        if isinstance(raw, str):
            response_text = raw
        elif hasattr(raw, "content"):
            response_text = raw.content
        else:
            response_text = str(raw)
    except Exception as exc:
        raise RuntimeError(f"LLM invoke failed: {exc}") from exc

    # Parse the JSON subtask list
    import re as _re
    # Strip markdown fences if present
    clean = _re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip(), flags=_re.MULTILINE)
    try:
        import json as _json
        subtasks = _json.loads(clean)
        if not isinstance(subtasks, list):
            raise ValueError("Expected JSON array")
    except Exception as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {exc}\nRaw: {response_text[:300]}") from exc

    VALID_PRIORITIES = {"critical", "high", "medium", "low"}
    VALID_TYPES_SET = {"build", "run", "fix", "research", "deploy", "test", "chore"}

    now = _utcnow_iso()
    conn = get_connection()
    try:
        # Mark parent decomposed first
        conn.execute(
            "UPDATE kanban_tasks SET status = 'decomposed', updated_at = ? WHERE id = ?",
            (now, tid),
        )

        inserted = []
        for i, sub in enumerate(subtasks[:5], start=1):
            sub_title = str(sub.get("title") or f"{title} — part {i}")[:120]
            sub_desc = str(sub.get("description") or "")
            sub_type = sub.get("task_type") or child_type
            if sub_type not in VALID_TYPES_SET:
                sub_type = child_type
            sub_pri = sub.get("priority") or priority
            if sub_pri not in VALID_PRIORITIES:
                sub_pri = priority

            # Generate child ID: parent_id + suffix
            child_id = f"{tid}-d{i}"
            # Truncate if too long for any DB column limit
            child_id = child_id[:64]

            # depends_on_task_id: chain children sequentially so they
            # run in order (each child depends on the previous)
            dep = inserted[-1] if inserted else None

            conn.execute(
                "INSERT OR IGNORE INTO kanban_tasks "
                "(id, title, description, priority, task_type, status, "
                " executor_type, depends_on_task_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'backlog', 'claude_cli', ?, ?, ?)",
                (child_id, sub_title, sub_desc, sub_pri, sub_type, dep, now, now),
            )
            inserted.append(child_id)

        conn.commit()
        print(
            f"  Kanban: auto-decomposed {tid!r} into {len(inserted)} subtask(s): "
            + ", ".join(inserted)
        )

        # Telegram notification
        try:
            import os as _os
            if not (_os.environ.get("PYTEST_CURRENT_TEST") or
                    _os.environ.get("ICDEV_SUPPRESS_NOTIFICATIONS") == "1"):
                from tools.notifications.adapters.telegram import send as tg_send
                tg_send(
                    f"AUTO-DECOMPOSED: {title[:50]}",
                    f"Task {tid} was split into {len(inserted)} subtasks: "
                    + ", ".join(inserted),
                    severity="info",
                )
        except Exception:
            pass

    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Kanban Executor Reflex."""
    global _current_exec_tier
    tier = tier_resolver.resolve_tiers().exec_tier
    if tier != _current_exec_tier:
        logger.info("Executor tier changed to %s", tier)
        _current_exec_tier = tier

    # 0a. Orphan-done sweep — roll back any done task whose parent isn't done.
    # Defense-in-depth against manual SQL / spurious automation that marked
    # a row done while its prerequisite work hadn't completed. See memory
    # feedback_kanban_vv_policy.md and the 2026-04-15 E-gate incident.
    try:
        orphans = _detect_orphan_done_tasks()
        if orphans:
            print(
                f"  Kanban: orphan-done sweep rolled back {len(orphans)} "
                f"task(s): {[o['id'] for o in orphans]}"
            )
    except Exception as _osw_exc:
        logger.warning("orphan-done sweep failed: %s", _osw_exc)

    # 0b. Startup-recovery sweep — on the very first cycle after a restart,
    # reset any tasks still in_progress (they were orphaned when the scheduler
    # crashed or the OS killed the Claude CLI process mid-run).  Must run
    # before step 3 (promotion gate) so these tasks can be re-queued.
    try:
        _startup_recover_stale_in_progress()
    except Exception as _sr_exc:
        logger.warning("startup-recovery sweep failed: %s", _sr_exc)

    # 0c. Worktree age sweep — remove worktrees older than the threshold
    # whose owning task is not in_progress. Disk hygiene; prevents long
    # runs from filling disk with failure-train worktrees. Opportunistic
    # (only runs when cycle count % 30 == 0 to avoid spending every 60s
    # walking the tree).
    try:
        import random as _r  # noqa: PLC0415
        if _r.random() < 0.033:  # ~1 in 30 cycles ≈ once per 30 min  # noqa: S311
            swept = _sweep_old_worktrees()
            if swept:
                print(f"  Kanban: worktree age sweep removed {len(swept)} stale worktree(s)")
    except Exception as _ws_exc:
        logger.warning("worktree age sweep failed: %s", _ws_exc)

    # 0d. Periodic stale-in_progress reaper — catches tasks that are in_progress
    # in the DB but absent from _running (process died after dispatch without
    # going through the verification gate, OR verification left them in_progress
    # with "human review needed" instead of resetting to backlog).
    # Runs ~every 30 cycles (≈30 min). Threshold: 2× the per-task timeout.
    try:
        import random as _rr  # noqa: PLC0415
        if _rr.random() < 0.033:  # ~1-in-30 ≈ once per 30 min  # noqa: S311
            _reap_stale_in_progress()
    except Exception as _rip_exc:
        logger.warning("stale-in_progress reaper failed: %s", _rip_exc)
    # Suggested-decay: re-queue soft-stuck tasks once every ~4 h (1/240 cycles).
    try:
        if _rr.random() < 0.004:  # noqa: S311
            _promote_stale_suggested()
    except Exception as _pss_exc:
        logger.warning("suggested-decay sweep failed: %s", _pss_exc)

    # 1. Check for completed claude subprocesses
    completed = _check_completed()

    # 2. Poll Telegram for new commands
    tg_results = _poll_telegram()
    if tg_results:
        print(f"  Kanban: {len(tg_results)} Telegram commands")

    # 3. Don't promote new tasks if claude is already running
    #    BUT first clean up stale entries — if the task was marked done/backlog
    #    externally (e.g., Claude CLI self-reported via HTTP POST, or the
    #    subprocess died before completing), the _running dict may hold an
    #    orphaned Popen reference that blocks all future promotions forever.
    #
    # 2026-04-17 stale-cleanup fix: previously this path cleaned up the
    # in-memory dict + worktree silently — no failure_count bump, no
    # last_failure_reason, no alert queued. dt-iqe-11 became invisible
    # because the agent subprocess died (or moved the task externally) and
    # neither the user nor failure_triage could tell anything had gone
    # wrong. We now treat every stale cleanup as a first-class unverified
    # failure, record it in the DB, and queue an alert locally instead of
    # relying on fragile external notification adapters.
    if _running:
        stale_info: list[tuple[str, str]] = []
        for tid, proc in list(_running.items()):
            try:
                task_conn = get_connection()
                row = task_conn.execute(
                    "SELECT status FROM kanban_tasks WHERE id = ?", (tid,),
                ).fetchone()
                task_conn.close()
                if row and dict(row)["status"] not in ("in_progress", "scheduled"):
                    # Task was completed/moved externally — clean up. The
                    # cause is most often: agent subprocess died before
                    # completion (network/API hiccup, OS kill), or agent
                    # self-reported via the dashboard API. We can't tell
                    # which without the subprocess's own log, so the reason
                    # string names both possibilities.
                    cur_status = dict(row)["status"]
                    stale_info.append((tid, cur_status))
            except Exception:
                pass
        for tid, cur_status in stale_info:
            print(f"  Kanban: cleaning up stale _running entry for {tid} "
                  f"(DB status={cur_status})")
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

            # Record the failure in the DB so it's visible to operators +
            # failure_triage. If the task is already in 'done' we leave it
            # alone (it legitimately completed and self-reported).
            if cur_status != "done":
                reason = (
                    f"stale-cleanup: claude CLI subprocess went stale mid-run "
                    f"(DB moved to {cur_status!r} without going through the "
                    f"verification gate). Agent likely died before completion "
                    f"or self-reported via an external path."
                )
                # Run self_debug FIRST so signature_count is incremented
                # before we decide the target state — quarantine to
                # 'suggested' takes precedence over backlog/needs_decomposition.
                try:
                    from tools.workflow.self_debug import check_and_diagnose as _cad
                    _sd_work_dir = str(BASE_DIR)
                    _cad(tid, reason, _sd_work_dir)
                except Exception as _sd_exc:
                    logger.warning(
                        "stale-cleanup self_debug call failed for %s: %s",
                        tid, _sd_exc,
                    )
                new_target = _record_failure_and_maybe_flag(tid, reason)
                if new_target == "needs_decomposition" and cur_status != "needs_decomposition":
                    _move_task(
                        tid, "needs_decomposition",
                        actor="stale-cleanup", reason=reason,
                    )
                # Bump failure count + persist reason explicitly so
                # failure_triage's recency window picks it up.
                try:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    _fc_conn = get_connection()
                    _fc_conn.execute(
                        "UPDATE kanban_tasks SET "
                        "  last_failure_reason = ?, "
                        "  last_failure_at = ?, "
                        "  updated_at = ? "
                        "WHERE id = ?",
                        (reason, now_iso, now_iso, tid),
                    )
                    _fc_conn.commit()
                    _fc_conn.close()
                except Exception as _fc_exc:
                    logger.warning(
                        "stale-cleanup failure-write failed for %s: %s",
                        tid, _fc_exc,
                    )

                # Build a task dict for the local alert queue.
                task_dict = {"id": tid, "title": tid}
                try:
                    _tc = get_connection()
                    _tr = _tc.execute(
                        "SELECT title, task_type, priority FROM kanban_tasks "
                        "WHERE id = ?", (tid,),
                    ).fetchone()
                    _tc.close()
                    if _tr:
                        _tr_d = dict(_tr)
                        task_dict = {
                            "id": tid,
                            "title": _tr_d.get("title") or tid,
                            "task_type": _tr_d.get("task_type"),
                            "priority": _tr_d.get("priority"),
                        }
                except Exception:
                    pass

                # Queue the alert locally instead of calling fragile external
                # notification adapters. The local queue is drained by the
                # dashboard or a background worker so operators still see it.
                try:
                    _queue_alert_locally(task_dict, reason=reason)
                except Exception as _qa_exc:
                    logger.warning(
                        "stale-cleanup local alert queue failed for %s: %s",
                        tid, _qa_exc,
                    )
                except Exception:
                    pass

        # Stale cleanup happened — defer promotion to the next cycle so the
        # quarantine state written by check_and_diagnose has time to settle
        # before the scheduler considers the task eligible for re-dispatch.
        if stale_info:
            print(f"  Kanban: stale-cleanup finished ({len(stale_info)} task(s)) "
                  f"— deferring promotion to next cycle")
            return {
                "success": True,
                "metric_value": len(completed),
                "details": {
                    "status": "stale_cleanup",
                    "cleaned": [t for t, _ in stale_info],
                    "completed_this_cycle": completed,
                    "telegram_commands": len(tg_results),
                },
            }

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

    # 3c. Auto-decompose tasks stuck at needs_decomposition
    try:
        decomposed = _auto_decompose_stalled_tasks()
        if decomposed:
            print(f"  Kanban: auto-decomposed {len(decomposed)} stalled task(s): {decomposed}")
    except Exception as _ad_exc:
        logger.warning("auto_decompose sweep failed: %s", _ad_exc)

    # 3d. Close orphaned-decomposed tasks — decomposed parents whose LLM
    # decomposition produced no children (LLM failed silently) get stuck
    # forever in 'decomposed' status. Auto-close them as done so they
    # don't block visibility and don't mislead the kanban board.
    try:
        _close_orphaned_decomposed()
    except Exception as _cod_exc:
        logger.warning("orphaned-decomposed sweep failed: %s", _cod_exc)

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
    decomposed_this_cycle = []

    for task in due_tasks:
        # Pre-dispatch complexity gate: score the task before spending any tokens.
        # If it looks too big for a single session (score ≥ 7) decompose it now
        # instead of letting it fail and waste a full 900s agent run.
        _cscore = _complexity_score(task)
        if _cscore >= 7:
            logger.info(
                "pre-dispatch: %s complexity score %d ≥ 7 — decomposing upfront",
                task["id"], _cscore,
            )
            print(
                f"  Kanban: pre-dispatch complexity gate triggered for {task['id']!r} "
                f"(score={_cscore}) — decomposing upfront to avoid wasted token run"
            )
            try:
                _decompose_one_task(task)
            except Exception as _pd_exc:
                logger.warning(
                    "pre-dispatch decompose failed for %s (%s) — dispatching anyway",
                    task["id"], _pd_exc,
                )
            else:
                # Decomposed successfully — skip dispatch for this task;
                # children will be picked up next cycle. Continue to next task.
                decomposed_this_cycle.append(task["id"])
                continue

        try:
            # Write prompt file first (low risk)
            prompt_path = _write_prompt_file(task)

            # Dispatch to claude CLI — only move to in_progress AFTER
            # subprocess is confirmed running, so tasks don't get stuck
            # in in_progress when dispatch fails.
            _dispatch_to_claude(task, prompt_path)

            if task["id"] in _ollama_completed:
                # Synchronous Ollama dispatch — completed immediately, mark done
                _ollama_completed.discard(task["id"])
                _move_task(task["id"], "done")
                _send_notification(task, event="done")
                processed.append(
                    {
                        "id": task["id"],
                        "title": task["title"],
                        "prompt_file": prompt_path,
                    }
                )
                print(f"  Kanban: {task['id']} '{task['title']}' -> done (Ollama sync)")
            elif task["id"] in _running:
                # Async Claude/LLM subprocess launched — move to in_progress
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
            "decomposed_this_cycle": decomposed_this_cycle,
            "errors": errors,
            "tasks": processed,
        },
    }
