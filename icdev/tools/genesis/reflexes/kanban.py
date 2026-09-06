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
IMPLEMENTATION_STATUS = "full"

import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger(__name__)

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.transition_reason import (  # noqa: E402
    resolve_transition_reason as _resolve_transition_reason,
)
from tools.strategos import tier_resolver  # noqa: E402

PROMPT_DIR = BASE_DIR / ".tmp" / "kanban"


def _canonical_repo_root() -> Path:
    """The MAIN worktree's root, even when this module runs inside a linked one.

    BASE_DIR comes from ``__file__``, so a dispatch triggered from inside a
    worktree resolved WORKTREE_BASE to *that worktree* and created the next
    worktree at ``<worktree>/.tmp/worktrees/<id>``. Nested worktrees are how the
    leak compounded — measured 2026-08-02 at 122 registered worktrees including
    paths three levels deep such as
    ``.tmp/worktrees/tsh-e2e-01-d4/.tmp/worktrees/tsh-e2e-01-d4/.tmp/worktrees/tsr-gen-01-d4``.

    ``git rev-parse --git-common-dir`` reports the MAIN repository's .git from
    anywhere in the family, which is exactly the "resolve the repo root from a
    known location, never from cwd or a linked checkout" rule in CLAUDE.md.
    Falls back to BASE_DIR when git is unavailable, preserving today's behavior
    rather than failing dispatch.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(BASE_DIR), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            common = Path(out.stdout.strip())
            if not common.is_absolute():
                common = (BASE_DIR / common).resolve()
            return common.parent
    except Exception as exc:  # noqa: BLE001 - git absent or not a repo
        logger.debug("canonical repo root resolution failed (%s) — using BASE_DIR", exc)
    return BASE_DIR


WORKTREE_BASE = _canonical_repo_root() / ".tmp" / "worktrees"


# ---------------------------------------------------------------------------
# Repo-aware dispatch (ked-core-01)
#
# Every git/gh call below used to run with cwd=BASE_DIR. For an ICDev task that
# is right. For an EXTERNAL-repo task (compass / idea_lab, per
# args/kanban_external_repos.yaml) it is wrong in the most expensive way: the
# done-gate asks ICDEV whether COMPASS's work landed, the answer is always no,
# and the task churns forever. That is why _dispatch_task simply PARKED every
# external task — and why the entire Premium Suite had to be driven by hand.
#
# These three helpers resolve, per task, WHICH repo it builds in. A task that
# matches no prefix — or any failure to resolve at all — returns the ICDev
# default, so an absent/empty registry is a complete no-op and every existing
# task behaves byte-identically.
# ---------------------------------------------------------------------------
def _manual_build() -> bool:
    """True when Manual Build is on: promote and track, but do not dispatch.

    Never raises, and fails to AUTOMATIC on any error. An unreadable flag file must
    not silently stop every build on the board — "nothing happened and nobody noticed"
    is the failure mode worth engineering against here.
    """
    try:
        from tools.kanban.build_mode import is_manual

        return is_manual()
    except Exception as exc:  # noqa: BLE001
        logger.debug("build-mode check failed (defaulting to automatic): %s", exc)
        return False


def _task_repo_target(task_id: str):
    """The RepoTarget for a task, or None if resolution failed (-> ICDev)."""
    try:
        from tools.kanban.repo_registry import resolve_task_repo

        return resolve_task_repo(task_id)
    except Exception as exc:  # noqa: BLE001 — resolution must never break dispatch
        logger.debug("repo resolve failed for %s (defaulting to ICDev): %s", task_id, exc)
        return None


def _task_repo_root(task_id: str) -> Path:
    """The on-disk repo root a task builds in. BASE_DIR (ICDev) unless external."""
    target = _task_repo_target(task_id)
    if target is not None and target.is_external and target.root is not None:
        return Path(target.root)
    return BASE_DIR


def _task_base_branch(task_id: str) -> str:
    """The branch a task builds off, in ITS repo. compass's main is not ICDev's."""
    target = _task_repo_target(task_id)
    if target is not None and target.is_external and target.root is not None:
        return target.base_branch or "main"
    return _default_branch()


def _live_worktree_holding(repo_root, branch_name: str) -> Optional[str]:
    """Path of an EXISTING worktree that has *branch_name* checked out, else None.

    "Live" means the directory is still on disk. That is the whole distinction
    this predicate exists to draw, and it is the one the ``update-ref -d``
    fallback below was missing.

    MEASURED, git 2.55.0.windows.2 (kph-repark-fni-ana-01):

        git branch -D held      rc=1  "cannot delete branch 'held' used by
                                       worktree at '<path>'"          <- the safety
        git update-ref -d ...   rc=0                                  <- bypasses it

    ``update-ref`` is plumbing and does not honour a worktree checkout, so the
    fallback deleted the branch pointer out from under a RUNNING worker, leaving
    its commits reachable from nothing. ``git worktree add`` then failed anyway
    (the registration still names the branch) and recreated the name at the base
    commit -- so the branch appeared intact while pointing somewhere else
    entirely.

    Returning None on any failure is deliberate and matches the caller: if we
    cannot read the worktree list we do NOT claim the branch is free.
    ``_worktree_is_disposable`` refuses on the same principle -- prove it is
    disposable, and refuse when you cannot tell.
    """
    import subprocess as _sp

    try:
        listed = _sp.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
        if listed.returncode != 0:
            return None
    except Exception as exc:  # noqa: BLE001 — never wedge dispatch on a git hiccup
        logger.debug("worktree list failed for %s: %s", repo_root, exc)
        return None

    # Porcelain emits a record per worktree: `worktree <path>` then, when a
    # branch is checked out, `branch refs/heads/<name>`.
    current: Optional[str] = None
    for line in (listed.stdout or "").splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):].strip()
        elif line.startswith("branch ") and current:
            ref = line[len("branch "):].strip()
            if ref == f"refs/heads/{branch_name}":
                try:
                    if Path(current).exists():
                        return current
                except OSError:
                    return current  # unreadable is not provably gone
                return None
    return None


def _worktree_is_disposable(path, listed) -> tuple:
    """May this directory be deleted to make room for a fresh worktree?

    Returns ``(disposable, reason)``. The reason is returned either way so the
    refusal is auditable rather than a silent skip.

    THIS FUNCTION EXISTS BECAUSE THE OLD TEST WAS "git worktree list did not
    mention it". Three sessions lost work in one night to that test
    (2026-08-29, kpr-dup-10). A directory nothing in THIS repo claims is not
    thereby empty, abandoned, or yours -- it is merely unexplained, and the
    correct response to unexplained is to refuse.

    The posture is deliberately asymmetric, and it is the same one
    ``pr_watcher.reclaim_worktree`` takes: a wrongly-kept directory costs one
    parked task that a human unsticks in a minute, while a wrongly-deleted one
    costs work that no ordinary means recovers. So every branch that cannot
    prove disposability returns False, INCLUDING every error path -- an
    exception here must never fall through to "go ahead and delete".
    """
    import os

    try:
        if listed is not None and getattr(listed, "returncode", 1) != 0:
            # The measured inversion: empty stdout contains no path, so without
            # this check EVERY existing directory reads as an orphan -- and it
            # does so exactly when git is already unhealthy.
            return False, "git worktree list failed, so absence from it proves nothing"

        entries = list(os.scandir(path))
        if not entries:
            return True, "empty directory"

        # A `.git` entry means this IS a git worktree, registered against SOME
        # repository -- just not the one asked. That is the external-repo task
        # and the CLI-session cases, both of which are alive.
        if any(e.name == ".git" for e in entries):
            code, out = _quiet_git(["status", "--porcelain"], cwd=str(path))
            if code != 0:
                return False, "a git worktree whose status could not be read"
            if out.strip():
                return False, "a git worktree with uncommitted changes"
            # HEAD, NOT `--branches`. A worktree SHARES its .git with the main
            # checkout, so `--branches` answers "does this REPOSITORY have any
            # unpushed commit on any branch" -- the same repo-wide number from
            # every worktree. Measured 2026-08-30 in a worktree whose branch was
            # merged and pushed:
            #     git log --branches --not --remotes --oneline | wc -l  -> 2142
            #     git log HEAD       --not --remotes --oneline | wc -l  ->    0
            # The over-broad form errs toward REFUSING, so nothing was ever
            # destroyed by it -- but on any active repository at least one branch
            # always has an unpushed commit, which made `disposable` UNREACHABLE
            # and left the cleanup path dead. That re-opens the leak
            # `reclaim_worktree` exists for (122 registered worktrees, recursively
            # nested, 2026-08-02). A guard that can never PASS is the same defect
            # as one that never FIRES, mirrored.
            code, out = _quiet_git(["log", "HEAD", "--not", "--remotes",
                                    "--oneline"], cwd=str(path))
            if code != 0:
                return False, "a git worktree whose unpushed commits could not be counted"
            if out.strip():
                # A REPO WITH NO REMOTES MAKES EVERY COMMIT LOOK UNPUSHED, and
                # refusing on that basis would make this predicate unable to ever
                # say yes there -- the same "guard that can never pass" defect
                # kpr-dup-11 removed. `--not --remotes` excludes nothing when
                # there is nothing to exclude, so ask the question that still has
                # meaning: is HEAD reachable from some OTHER ref in this repo? If
                # it is, the worktree holds nothing unique and losing it loses
                # nothing.
                rcode, remotes = _quiet_git(["remote"], cwd=str(path))
                if rcode == 0 and not remotes.strip():
                    bcode, containing = _quiet_git(
                        ["branch", "--contains", "HEAD"], cwd=str(path))
                    if bcode != 0:
                        return False, "no remote, and reachability could not be read"
                    named = [ln.strip().lstrip("* ").strip()
                             for ln in containing.splitlines() if ln.strip()]
                    named = [b for b in named if b and not b.startswith("(")]
                    if named:
                        return True, (f"no remote configured; HEAD is reachable from "
                                      f"{named[0]}, so nothing here is unique")
                    return False, "no remote, and HEAD is on no branch -- unique work"
                n = len([ln for ln in out.splitlines() if ln.strip()])
                return False, f"a git worktree holding {n} commit(s) that are on no remote"
            return True, "a clean git worktree with nothing unpushed"

        # Content, but no .git. Could be a partial checkout whose .git was
        # already taken by a half-finished delete -- which is precisely the
        # state the old `ignore_errors=True` left behind, and precisely when
        # the commits are least recoverable. Not ours to judge.
        return False, f"{len(entries)} entries but no .git -- possibly a partial delete"
    except Exception as exc:  # noqa: BLE001 -- unreadable is never disposable
        return False, f"could not inspect the directory: {exc}"


def _quiet_git(args, cwd):
    """(returncode, stdout). Never raises; a failure is (1, "")."""
    import subprocess as _sp2

    try:
        proc = _sp2.run(["git", *args], cwd=cwd, capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=30)
        return proc.returncode, (proc.stdout or "")
    except Exception:  # noqa: BLE001
        return 1, ""


def _task_worktree_path(task_id: str) -> Path:
    """Where the task's worktree lives.

    ICDev tasks keep the historical location (.tmp/worktrees/<id>). An EXTERNAL
    task's worktree goes in the system temp dir — never inside either repo. A
    compass worktree nested under ICDev's tree would show up in ICDev's git
    status and in every tree-scoped gate that walks the checkout, which is the
    same confusion of repos this whole change exists to remove.
    """
    target = _task_repo_target(task_id)
    if target is not None and target.is_external and target.root is not None:
        import tempfile

        return Path(tempfile.gettempdir()) / "icdev-kanban" / target.name / task_id
    return WORKTREE_BASE / task_id


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_prompt_dir():
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)


def _count_in_progress() -> int:
    """Count tasks currently in_progress that represent REAL work.

    Manual-mode gates are excluded. A gate is a sentinel, not work: per
    ``tools/kanban/gates.py`` it is held ``in_progress`` FOREVER by design, so
    counting it consumed a dispatch slot that nothing was ever going to release.
    ``_get_due_tasks`` already filters gates out of dispatch via the same
    predicate; counting them here contradicted that and throttled the pipeline
    in proportion to how many cards were gated.

    Concretely, with the default ``MAX_IN_PROGRESS=3`` and two gates held,
    ``available_slots`` was 1 instead of 3, and a single running task drove it to
    0 — where ``_get_due_tasks`` returns ``[]`` and the scheduler logs
    "idle (no due tasks)" while due, dependency-satisfied tasks sit in
    ``scheduled``. Three gates would have stopped dispatch outright.

    Imported locally because ``_is_manual_gate`` is bound further down this
    module; ``tools.kanban.gates`` imports nothing, so this is cheap.
    """
    from tools.kanban.gates import is_manual_gate

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title FROM kanban_tasks WHERE status = 'in_progress'"
        ).fetchall()
        return sum(
            1
            for r in (dict(x) for x in rows)
            if not is_manual_gate(r.get("id"), r.get("title"))
        )
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
            row = conn.execute("SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
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


def _int_env(key: str, default: int) -> int:
    """Load an integer threshold from an env var, falling back to default."""
    import os as _os
    try:
        val = _os.getenv(key)
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def _float_env(key: str, default: float) -> float:
    """Load a float threshold from an env var, falling back to default."""
    import os as _os
    try:
        val = _os.getenv(key)
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


# Max tasks to auto-promote per cycle — matches MAX_IN_PROGRESS so a full
# batch fills all available slots in one cycle rather than two.
# Override via KANBAN_MAX_AUTO_PROMOTE env var.
MAX_AUTO_PROMOTE = _int_env("KANBAN_MAX_AUTO_PROMOTE", 3)
# Max in-progress tasks at any time (prevents pile-up).
# Override via KANBAN_MAX_IN_PROGRESS env var.
MAX_IN_PROGRESS = _int_env("KANBAN_MAX_IN_PROGRESS", 3)
# Bounded auto-revive of failure-quarantined ('suggested', fc>=5/HITL) tasks.
# A quarantined task whose dependency is satisfied and that has been parked
# longer than the cooldown is auto-revived to backlog (failure_count reset),
# up to MAX_AUTO_REVIVE times. After the cap it is held for HITL review with a
# one-time Telegram alert. Prevents tasks (and their dependency chains) from
# rotting in 'suggested' forever after repeated failures.
MAX_AUTO_REVIVE = _int_env("KANBAN_MAX_AUTO_REVIVE", 2)
QUARANTINE_REVIVE_COOLDOWN_MIN = _int_env("KANBAN_REVIVE_COOLDOWN_MIN", 30)
# Max seconds a Claude CLI subprocess can run before being killed.
# Override via KANBAN_MAX_EXECUTION_SECONDS / _SCAN / _PYTEST env vars.
# 900s was too tight by a hair, not by an order of magnitude: recorded failures
# include "TIMEOUT after 902s (max 900s)" and "911s (max 900s)" — tasks killed
# with ~0.2-1.2% of their work left. 1800s gives real headroom without letting a
# wedged task hold one of only MAX_IN_PROGRESS slots for long.
#
# The ladder MUST stay monotonic (default <= scan <= pytest). _EXTENDED_TIMEOUT_
# PATTERNS routes `codelens|coherence|companion` to SCAN and `pytest|e2e|...` to
# PYTEST, so a tier below the default would SHORTEN those tasks' budgets — which
# is exactly what happened when the default was raised on its own.
# Turn ceiling for a dispatched Claude CLI session. This is a HARD cutoff: the
# CLI stops mid-task with "Error: Reached max turns (N)" and whatever the agent
# had not yet committed is discarded, so the whole session is re-dispatched from
# a cold worktree. At the previous hardcoded 50 that was firing on 14 of the 92
# task logs written in the last two days — 15% of dispatches thrown away for
# want of turns, not for want of time (the separate 1800s wall-clock budget
# below is what should be bounding a runaway task).
# Override via KANBAN_MAX_TURNS env var.
MAX_TURNS = _int_env("KANBAN_MAX_TURNS", 200)
MAX_EXECUTION_SECONDS = _int_env("KANBAN_MAX_EXECUTION_SECONDS", 1800)
MAX_EXECUTION_SECONDS_SCAN = _int_env("KANBAN_MAX_EXECUTION_SECONDS_SCAN", 1800)
# Full-suite tasks legitimately run past an hour; they should still set
# max_runtime_seconds explicitly rather than lean on this ceiling.
MAX_EXECUTION_SECONDS_PYTEST = _int_env("KANBAN_MAX_EXECUTION_SECONDS_PYTEST", 3600)
# Minimum remaining budget required to start post-process operations (guard-budget).
# Override via KANBAN_VERIFICATION_MIN_BUDGET_SECONDS / _REMEDIATION / _SELF_DEBUG env vars.
VERIFICATION_MIN_BUDGET_SECONDS = _int_env("KANBAN_VERIFICATION_MIN_BUDGET_SECONDS", 30)
REMEDIATION_MIN_BUDGET_SECONDS = _int_env("KANBAN_REMEDIATION_MIN_BUDGET_SECONDS", 60)
SELF_DEBUG_MIN_BUDGET_SECONDS = _int_env("KANBAN_SELF_DEBUG_MIN_BUDGET_SECONDS", 15)
# Hard-quarantine a task after this many identical timeouts.
# Override via KANBAN_MAX_TIMEOUT_RETRIES env var.
MAX_TIMEOUT_RETRIES = _int_env("KANBAN_MAX_TIMEOUT_RETRIES", 3)
# Failures before a task is flagged for decomposition.
# Override via KANBAN_MAX_FAILURES_BEFORE_DECOMPOSITION env var.
# 3, not 1. At 1 a single failure split a task into 3-5 LLM-generated children,
# which meant the retry-with-coaching path (_get_retry_coaching) could never run
# — the task was decomposed before it was ever retried — and the coaching text
# the agent is shown said "after 3 failures", which was a lie. Most first
# failures on this board were harness artifacts (reaping/timeout), so we were
# splitting healthy tasks in response to noise; measured, decomposition children
# fail ~3x more often than undecomposed tasks.
_MAX_FAILURES_BEFORE_DECOMPOSITION_DEFAULT = _int_env("KANBAN_MAX_FAILURES_BEFORE_DECOMPOSITION", 3)
# Minimum claude output length (chars) to be considered non-trivial.
# Override via KANBAN_MIN_OUTPUT_LENGTH env var.
MIN_OUTPUT_LENGTH = _int_env("KANBAN_MIN_OUTPUT_LENGTH", 200)
# Phantom-completion ratio — fraction of claimed paths that may be missing.
# Override via KANBAN_PHANTOM_RATIO_THRESHOLD env var.
PHANTOM_RATIO_THRESHOLD = _float_env("KANBAN_PHANTOM_RATIO_THRESHOLD", 0.5)
# Scan-only task minimum run duration before accepting as successful.
# Override via KANBAN_SCAN_MIN_RUN_SECONDS env var.
SCAN_MIN_RUN_SECONDS = _int_env("KANBAN_SCAN_MIN_RUN_SECONDS", 60)
# A single task's diff should never approach this. Past it, the base ref is
# almost certainly wrong and the gates degrade to silent passes rather than
# failing — so refuse to run them. Override via KANBAN_MAX_CHANGED_FILES.
_MAX_CHANGED_FILES_FOR_GATES = _int_env("KANBAN_MAX_CHANGED_FILES", 500)

# Task ID patterns that get extended timeouts (regex, case-insensitive).
# Order matters: first match wins.
# e2e tasks get PYTEST-level time — a single E2E step still runs the full
# Selenium suite under the hood, so 20 min is too tight.
_EXTENDED_TIMEOUT_PATTERNS = [
    (r"pytest|regression|test-suite|full-test|e2e", MAX_EXECUTION_SECONDS_PYTEST),
    (r"codelens|coherence|companion", MAX_EXECUTION_SECONDS_SCAN),
]


def _detect_execution_anomalies(task_type: Optional[str] = None, window: int = 200) -> dict:
    """Anomaly detection for kanban execution metrics using IQR outlier analysis.

    Reads recent completed tasks from kanban_tasks and computes adaptive
    threshold recommendations for execution_seconds and failure_count.
    Uses interquartile range (IQR) to identify statistical outliers without
    requiring external ML dependencies.

    Args:
        task_type: Optional filter to restrict analysis to a specific task type.
        window: Number of recent completed tasks to sample (default 200).

    Returns a dict with:
        - exec_seconds_p50: median execution time (seconds)
        - exec_seconds_upper_fence: IQR upper fence — tasks above this are anomalous
        - failure_rate_mean: mean failure_count across completed tasks
        - failure_rate_upper_fence: IQR upper fence for failure counts
        - sample_size: number of tasks analysed
        - recommended_max_execution_seconds: adaptive cap (clamped 300–7200)
        - recommended_max_failures: adaptive decomposition threshold (clamped 1–10)

    Non-fatal: any DB or arithmetic error returns an empty dict so callers can
    fall back to the static configured constants.
    """
    try:
        conn = get_connection()
        try:
            query = (
                "SELECT execution_seconds, failure_count "
                "FROM kanban_tasks "
                "WHERE status IN ('done', 'verified') "
                "  AND execution_seconds IS NOT NULL "
                "  AND execution_seconds > 0 "
            )
            # %s, not ? — this is runtime SQL against PostgreSQL (the primary
            # backend). The bare ? here relied on translate_sql's init-fallback
            # rewrite, which warns and is explicitly not load-bearing.
            params: list = []
            if task_type:
                query += "  AND task_type = %s "
                params.append(task_type)
            query += "ORDER BY updated_at DESC LIMIT %s"
            params.append(window)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    if not rows:
        return {}

    exec_times = sorted(float(dict(r).get("execution_seconds") or 0) for r in rows)
    fail_counts = sorted(float(dict(r).get("failure_count") or 0) for r in rows)
    n = len(exec_times)

    def _iqr_fence(values: list) -> tuple:
        if len(values) < 4:
            return values[-1], values[-1]
        q1 = values[len(values) // 4]
        q3 = values[(3 * len(values)) // 4]
        iqr = q3 - q1
        return q1 - 1.5 * iqr, q3 + 1.5 * iqr

    exec_p50 = exec_times[n // 2]
    _, exec_upper = _iqr_fence(exec_times)
    fail_mean = sum(fail_counts) / n if n else 0.0
    _, fail_upper = _iqr_fence(fail_counts)

    # Clamp to sensible operational ranges.
    adaptive_exec = int(min(7200, max(300, exec_upper)))
    adaptive_fails = int(min(10, max(1, round(fail_upper))))

    return {
        "exec_seconds_p50": exec_p50,
        "exec_seconds_upper_fence": exec_upper,
        "failure_rate_mean": fail_mean,
        "failure_rate_upper_fence": fail_upper,
        "sample_size": n,
        "recommended_max_execution_seconds": adaptive_exec,
        "recommended_max_failures": adaptive_fails,
    }


def _nlp_extract_timeout_hint(desc: str) -> Optional[int]:
    """Use the routed ``timeout_extraction`` LLM to extract a timeout in seconds.

    Augments the structured ``timeout_hint:NNN`` regex so human-written phrases
    like "allow 25 minutes" or "needs about 1 hour" are also understood.

    Returns seconds (clamped 60–3600) or None.  Never raises — any failure
    falls through silently so existing heuristics remain in control.
    """
    if not desc or len(desc) < 20:
        return None
    try:
        import json as _json
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        req = LLMRequest(
            system_prompt=(
                "Extract a timeout duration from the task description. "
                "If the text states a specific time budget (e.g. '25 minutes', "
                "'allow 30 min', 'needs 1 hour', 'takes about 20 minutes'), "
                "return JSON: {\"timeout_seconds\": <integer>}. "
                "If no timeout intent is found, return JSON: {\"timeout_seconds\": null}. "
                "Return ONLY the JSON object, nothing else."
            ),
            messages=[{"role": "user", "content": desc[:500]}],
            max_tokens=32,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("timeout_extraction", req)
        if resp and resp.content:
            raw = resp.content.strip()
            # Strip markdown fences if present
            import re as _re2
            raw = _re2.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re2.MULTILINE).strip()
            data = _json.loads(raw)
            secs = data.get("timeout_seconds")
            if secs is not None:
                return min(3600, max(60, int(secs)))
    except Exception:
        pass
    return None


def _nlp_extract_gap_subject(title: str, description: str, gap_type: str) -> Optional[str]:
    """Use the routed ``gap_subject_extraction`` LLM to extract a gap entity.

    Augments regex patterns in _pre_dispatch_check that require specific
    formatting (e.g. "tool_not_in_manifest: tools/foo.py") so natural language
    variants ("register tools/foo.py in the manifest") are also understood.

    gap_type must be one of: "tool_not_in_manifest", "route_not_listed".
    Returns the extracted entity string or None. Never raises.
    """
    if not title and not description:
        return None

    _PROMPTS = {
        "tool_not_in_manifest": (
            "Extract the Python tool file path (e.g. 'tools/foo/bar.py') that "
            "needs to be registered in the manifest. "
            "Return JSON: {\"subject\": \"<path>\"} or {\"subject\": null} if not found."
        ),
        "route_not_listed": (
            "Extract the URL route path (e.g. '/dashboard/foo') that needs to be "
            "listed in the Pages configuration. "
            "Return JSON: {\"subject\": \"<route>\"} or {\"subject\": null} if not found."
        ),
        "orphan_db_table": (
            "Extract the database table name from a task about an orphaned DB table. "
            "Look for patterns like 'orphan_db_table gap: <name>', 'orphan_db_table on <name>', "
            "'Subject: <name>', or 'table: <name>'. "
            "Return JSON: {\"subject\": \"<table_name>\"} or {\"subject\": null} if not found."
        ),
        "db_table": (
            "Extract the database table name that needs to be created. "
            "Look for phrases like 'CREATE TABLE <name>', 'add table <name>', "
            "'add DB table <name>', or 'add schema <name>'. "
            "Return JSON: {\"subject\": \"<table_name>\"} or {\"subject\": null} if not found."
        ),
    }

    system_prompt = _PROMPTS.get(gap_type)
    if not system_prompt:
        return None

    combined = f"Title: {title}\n\nDescription: {description[:400]}"
    try:
        import json as _json
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import re as _re2

        req = LLMRequest(
            system_prompt=system_prompt + " Return ONLY the JSON object, nothing else.",
            messages=[{"role": "user", "content": combined}],
            max_tokens=48,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("gap_subject_extraction", req)
        if resp and resp.content:
            raw = _re2.sub(
                r"^```(?:json)?\s*|\s*```$", "", resp.content.strip(), flags=_re2.MULTILINE
            ).strip()
            data = _json.loads(raw)
            subject = data.get("subject")
            if subject and isinstance(subject, str):
                return subject.strip()
    except Exception:
        pass
    return None


def _get_task_timeout(task_id: str) -> int:
    """Return per-task timeout budget in seconds.

    Uses adaptive anomaly detection (_detect_execution_anomalies) when sufficient
    historical data exists to compute a data-driven ceiling; falls back to the
    configured static constants when data is sparse or the DB is unreachable.

    Priority order (highest first):
      1. Pattern match on task_id (pytest / scan) — but ceiling is still adaptive
      2. timeout_hint:NNNs directive in description — hard override, no adaptation
      3. NLP-extracted timeout from description — hard override, no adaptation
      4. Description / task_type keyword match — adaptive ceiling
      5. Default — adaptive ceiling or MAX_EXECUTION_SECONDS
    """
    task_id_lower = task_id.lower()
    for pattern, static_timeout in _EXTENDED_TIMEOUT_PATTERNS:
        if re.search(pattern, task_id_lower):
            anomalies = _detect_execution_anomalies(window=100)
            adaptive = anomalies.get("recommended_max_execution_seconds")
            if adaptive and adaptive > static_timeout:
                return adaptive
            return static_timeout

    # Check task description + task_type for TIMEOUT_HINT or heavy-tool heuristics
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT description, task_type, max_runtime_seconds FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
        d = dict(row) if row else {}
        desc = (d.get("description") or "").lower()
        task_type = (d.get("task_type") or "").lower()
        # Per-task explicit cap wins over all heuristics
        max_rt = d.get("max_runtime_seconds")
        if max_rt:
            return int(min(7200, max(60, max_rt)))
        # Explicit structured override always wins — no anomaly adaptation
        m = re.search(r"timeout_hint:\s*(\d+)", desc, re.IGNORECASE)
        if m:
            return min(3600, int(m.group(1)))  # cap at 1 hour
        # NLP fallback: understand natural language timeout hints.
        #
        # This may only RAISE the budget, never lower it. It is an LLM reading
        # free text, and a misread was setting the build's kill timer: a task
        # died with "TIMEOUT after 60s (max 60s)" because the extractor returned
        # the clamp floor. A model's guess about prose must not be able to kill
        # a build faster than the tier default would.
        nlp_secs = _nlp_extract_timeout_hint(desc)
        if nlp_secs is not None and nlp_secs > MAX_EXECUTION_SECONDS:
            return nlp_secs
        # PYTEST-level: try adaptive ceiling first, then fall back to static
        _pytest_kw = ("pytest", "regression", "full test", "test suite",
                      "e2e suite", "e2e test", "test_orchestrator")
        if any(kw in desc for kw in _pytest_kw):
            anomalies = _detect_execution_anomalies(task_type="test", window=100)
            adaptive = anomalies.get("recommended_max_execution_seconds")
            return adaptive if adaptive and adaptive > MAX_EXECUTION_SECONDS_PYTEST else MAX_EXECUTION_SECONDS_PYTEST
        if task_type == "test" and any(kw in desc for kw in ("e2e", "playwright", "selenium")):
            anomalies = _detect_execution_anomalies(task_type="test", window=100)
            adaptive = anomalies.get("recommended_max_execution_seconds")
            return adaptive if adaptive and adaptive > MAX_EXECUTION_SECONDS_PYTEST else MAX_EXECUTION_SECONDS_PYTEST
        # SCAN-level: single tool runs, coherence checks, single E2E steps
        _scan_kw = ("codelens", "coherence_checker", "e2e_full", "companion", "e2e")
        if any(kw in desc for kw in _scan_kw):
            anomalies = _detect_execution_anomalies(window=100)
            adaptive = anomalies.get("recommended_max_execution_seconds")
            return adaptive if adaptive and adaptive > MAX_EXECUTION_SECONDS_SCAN else MAX_EXECUTION_SECONDS_SCAN
    except Exception:
        pass

    # Default: use anomaly-detected ceiling or static fallback
    anomalies = _detect_execution_anomalies(window=100)
    adaptive = anomalies.get("recommended_max_execution_seconds")
    return adaptive if adaptive and adaptive > MAX_EXECUTION_SECONDS else MAX_EXECUTION_SECONDS


# ── Token exhaustion detection ────────────────────────────────────────────────

# Patterns that indicate the worker hit a token/rate/quota/capacity limit or was
# interrupted (case-insensitive). PROVIDER-AGNOSTIC: the dispatch model may be
# Claude (CLI), Kimi/Moonshot (cloud), or Ollama (local), and can SWAP between
# them mid-task when credits are exhausted — so detection must not rely on any
# single provider's phrasing. The authoritative done-gate is the git/origin
# merge-verify check in _move_task, not this text scan; this only decides whether
# an interrupted task parks at token_exhausted (retry) vs follows the fail path.
TOKEN_EXHAUSTION_PATTERNS = [
    # Generic rate/quota/limit (Claude, OpenAI/Kimi, most cloud providers)
    r"rate\s*limit",
    r"rate_limit",
    r"token\s*limit",
    r"usage\s*limit",
    r"quota\s*exceeded",
    r"insufficient_quota",
    r"\bquota\b",
    r"too\s*many\s*requests",
    r"\b429\b",
    r"exceeded.*(?:daily|hourly|monthly)\s*(?:limit|quota|cap)",
    r"out\s*of\s*(?:tokens|credits)",
    r"billing.*limit",
    r"capacity.*limit",
    r"max.*turns.*reached",
    r"conversation.*limit",
    r"please\s*try\s*again\s*(?:later|in\s*\d+)",
    r"reset(?:s)?\s*(?:at|in)?\s*\d{1,2}:\d{2}\s*(?:am|pm)",
    r"hit\s*your\s*limit",
    r"you'?ve\s*hit\s*your\s*limit",
    r"session\s+limit",
    # Context-window exhaustion (any provider)
    r"context\s*(?:length|window)",
    r"maximum\s*context",
    r"context.*exceeded",
    # Ollama / local-model failures (credit-exhaustion fallback to local can fail)
    r"connection\s*refused",
    r"failed\s*to\s*connect\s*to\s*ollama",
    r"model\s*not\s*found",
    r"out\s*of\s*memory",
    r"\boom\b",
    r"cuda.*out\s*of\s*memory",
]
_TOKEN_RE = re.compile("|".join(TOKEN_EXHAUSTION_PATTERNS), re.IGNORECASE)

# How long to wait before retrying a token-exhausted task (seconds).
# Claude Max resets at the top of each 5-hour window.
TOKEN_RETRY_DELAY_SECONDS = 300  # 5 minutes between checks
TOKEN_MAX_RETRY_COUNT = 60  # Give up after ~5 hours of retries


def _detect_token_exhaustion(exit_code: int, output: str) -> Tuple[bool, Optional[str]]:
    """Check if a worker was token/rate/quota-exhausted or interrupted mid-task.

    PROVIDER-AGNOSTIC: the worker may be Claude, Kimi/Moonshot, or Ollama and can
    swap providers mid-task on credit exhaustion, so this must not depend on any
    single provider's phrasing (see TOKEN_EXHAUSTION_PATTERNS). Detection here only
    routes an interrupted task to token_exhausted (park + retry, branch preserved)
    rather than 'done' — the authoritative done-gate is the git/origin merge-verify
    check in _move_task, which is independent of the worker's model entirely.

    Returns (is_exhausted, estimated_reset_info).
    """
    # Signal-kill / abnormal termination is a strong provider-independent
    # "interrupted mid-task" signal (OOM killer = 137, SIGTERM = 143, negative =
    # killed by signal on POSIX). Treat as exhaustion so the task parks and
    # retries with its branch preserved rather than being scored as a clean
    # failure. Plain exit code 1/2 is NOT treated as exhaustion here — that would
    # mislabel genuine task failures; the merge-verify gate already prevents any
    # of those from reaching 'done'.
    if exit_code is not None and (exit_code < 0 or exit_code >= 128):
        return True, None

    if not output:
        return False, None

    # Check the last 2000 chars (error messages usually at end)
    tail = output[-2000:]

    if _TOKEN_RE.search(tail):
        # Try to extract a reset time hint
        reset_match = re.search(
            r"(?:reset|resets|try again|available)\s*(?:at|in)?\s*"
            r"(\d[\d:hmap. \-]+)",
            tail,
            re.IGNORECASE,
        )
        reset_hint = reset_match.group(1).strip() if reset_match else None
        return True, reset_hint

    return False, None


def _nlp_extract_resume_at(reset_hint: str, now: datetime) -> Optional[datetime]:
    """Use the routed ``resume_at_extraction`` LLM to parse a reset hint to UTC.

    Augments the structured regex in _parse_resume_at for natural language
    expressions like "in about twenty minutes", "at noon", "try again tomorrow".

    Returns a UTC datetime or None. Never raises.
    """
    if not reset_hint or len(reset_hint) < 2:
        return None
    try:
        import json as _json
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import re as _re2

        now_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        req = LLMRequest(
            system_prompt=(
                f"Current time is {now_str}. "
                "Parse the rate-limit reset hint and return how many seconds from now "
                "to wait before retrying. "
                "Return JSON: {\"wait_seconds\": <integer>} or "
                "{\"wait_seconds\": null} if the hint is unparseable. "
                "Clamp to [60, 21600]. Return ONLY the JSON object, nothing else."
            ),
            messages=[{"role": "user", "content": reset_hint[:200]}],
            max_tokens=32,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("resume_at_extraction", req)
        if resp and resp.content:
            raw = _re2.sub(
                r"^```(?:json)?\s*|\s*```$", "", resp.content.strip(), flags=_re2.MULTILINE
            ).strip()
            data = _json.loads(raw)
            secs = data.get("wait_seconds")
            if secs is not None:
                return now + timedelta(seconds=min(21600, max(60, int(secs))))
    except Exception:
        pass
    return None


def _parse_resume_at(reset_hint: Optional[str]) -> datetime:
    """Parse a reset hint into an absolute UTC datetime for resume.

    Handles these forms from Claude CLI output:
      - "5 minutes" / "5m" / "5 min"        → now + N minutes
      - "1 hour" / "2h" / "1 hr"            → now + N hours
      - "300 seconds" / "300s"               → now + N seconds
      - "2:00 AM" / "2:00 pm" / "14:00"     → next occurrence of that wall-clock time (local TZ)
      - Natural language (e.g. "twenty minutes", "at noon") → NLP extraction via Haiku
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

    # NLP fallback: understand natural language hints the regex couldn't parse
    nlp_dt = _nlp_extract_resume_at(hint, now)
    if nlp_dt is not None:
        return nlp_dt

    return fallback


def _save_resume_at(task_id: str, resume_at: datetime):
    """Persist the resume-at timestamp for a token-exhausted task."""
    _ensure_prompt_dir()
    resume_file = PROMPT_DIR / f"{task_id}.resume_at"
    resume_file.write_text(resume_at.isoformat(), encoding="utf-8", newline="")


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


# Backoff ladder for a token-exhaustion retry whose DISPATCH failed (as opposed
# to a task that ran and hit the token limit again). Capped so a task that will
# never dispatch cannot hold the single per-cycle retry slot indefinitely.
# Override via KANBAN_TOKEN_RETRY_BACKOFF_MIN / _MAX_MIN env vars.
TOKEN_RETRY_BACKOFF_BASE_MIN = _int_env("KANBAN_TOKEN_RETRY_BACKOFF_MIN", 5)
TOKEN_RETRY_BACKOFF_MAX_MIN = _int_env("KANBAN_TOKEN_RETRY_BACKOFF_MAX_MIN", 60)


def _token_retry_backoff(task_id: str, retry_count: int) -> None:
    """Push a token-exhausted task's resume_at out after a failed dispatch.

    Without this the task keeps its already-elapsed resume_at, so it is handed
    straight back on the next 60s cycle and re-consumes the one token-retry the
    cycle permits — starving every other parked task behind it.
    """
    minutes = min(
        TOKEN_RETRY_BACKOFF_BASE_MIN * max(1, retry_count + 1),
        TOKEN_RETRY_BACKOFF_MAX_MIN,
    )
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    _save_resume_at(task_id, resume_at)
    print(
        f"  Kanban: token retry dispatch failed for {task_id} "
        f"— backing off {minutes} min (resume_at={resume_at.isoformat()})"
    )


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
    retry_file.write_text(str(count), encoding="utf-8", newline="")
    # The two counters are the only places the runner decides an execution gets
    # another go, so they are where agent_execution_retried belongs. Emitting at
    # the re-dispatch instead would conflate a retry with a first attempt, since
    # dispatch cannot see why it was called.
    _audit_agent_execution(
        "agent_execution_retried", task_id, reason="token_exhaustion", attempt=count,
    )
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
    timeout_file.write_text(str(count), encoding="utf-8", newline="")
    _audit_agent_execution(
        "agent_execution_retried", task_id, reason="timeout", attempt=count,
    )
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


def _work_dir_for(task_id: str) -> str:
    """Where this task's work actually is — surviving a scheduler restart.

    `_worktrees` is an in-memory dict, and the scheduler RE-EXECS ITSELF
    routinely: `code_reload.restart_if_code_changed` runs in its poll loop, so
    every merge that touches its import closure replaces the process and empties
    this map while tasks are still in flight.

    The old expression was ``_worktrees.get(task_id) or str(BASE_DIR)``, and the
    fallback is not harmless. `_verify_claimed_files_exist` asks whether the
    files the agent SAID it wrote exist under this directory. An agent works in
    its own worktree on its own branch, so under BASE_DIR none of its new files
    exist, `existing == 0`, and correct work is rejected as a PHANTOM
    COMPLETION. The verdict is confidently wrong and the cause — a restart
    minutes earlier — appears nowhere in it.

    So rebuild from the deterministic path before falling back. The worktree is
    named from the task id, so it can be recovered without any in-memory state;
    BASE_DIR remains only for a task that genuinely has no worktree directory.
    """
    known = _worktrees.get(task_id)
    if known:
        return known
    try:
        path = _task_worktree_path(task_id)
        if path.is_dir():
            # Repopulate: later checks in the same run then agree with this one.
            _worktrees[task_id] = str(path)
            return str(path)
    except Exception:  # noqa: BLE001 — recovery is best-effort, never fatal
        pass
    return str(BASE_DIR)
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


_default_base_ref_cache: Optional[str] = None


def _default_base_ref(repo_root: Optional[Path] = None) -> str:
    """Ref to diff a task branch against — ``origin/<default>``, not the local ref.

    Task branches are cut from ``origin/<default>`` (see _create_worktree), but
    the changed-file set was being computed against the LOCAL branch name. The
    shared checkout's local ``main`` drifts behind origin as other sessions
    merge — measured 86 commits / 244 files behind — so every task's "changed
    files" list picked up hundreds of files it never touched. That inflates the
    diff handed to the conformance reviewer, wastes bandit-delta work, records
    nonsense in files_changed (observed up to 11,118 files for one task), and
    overflows the argv budget in validated_commit._coherence_cmd, which
    silently degrades the fast coherence tier back to the slow full tier.

    Falls back to the bare local name when no remote-tracking ref exists, so a
    detached or origin-less checkout still works.
    """
    global _default_base_ref_cache
    if _default_base_ref_cache:
        return _default_base_ref_cache

    branch = _default_branch()
    candidate = f"origin/{branch}"
    try:
        import subprocess as _sp

        r = _sp.run(
            ["git", "rev-parse", "--verify", "--quiet", candidate],
            capture_output=True, text=True,
            cwd=str(repo_root or BASE_DIR), timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            _default_base_ref_cache = candidate
            return candidate
    except Exception as exc:
        logger.debug("kanban: could not verify %s, using local ref: %s", candidate, exc)
    _default_base_ref_cache = branch
    return branch


def _create_worktree(task_id: str) -> Optional[str]:
    """Create an isolated git worktree for a kanban task.

    Returns the worktree path on success, None on failure.
    Falls back to BASE_DIR if git worktree is unavailable.
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    worktree_path = _task_worktree_path(task_id)
    # Make the PARENT of the resolved path, not WORKTREE_BASE. An external task's
    # worktree lives under the system temp dir, and mkdir'ing ICDev's .tmp/worktrees
    # left that parent missing — so `git worktree add` created the BRANCH and then
    # failed on the directory, and the task was parked as "worktree creation failed".
    # Caught by the first real compass dispatch; the unit tests never made a worktree.
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    if worktree_path.exists():
        # Validate it's a real git worktree, not an orphan empty dir left over
        # from a failed `git worktree remove`. Orphans cause Claude to run in
        # an empty cwd and coherence checks to fail (no tools/manifest.md).
        listed = _sp.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
        )
        if listed.returncode == 0 and str(worktree_path).replace("\\", "/") in (
            listed.stdout or ""
        ).replace("\\", "/"):
            return str(worktree_path)

        # NOT LISTED IS NOT THE SAME AS DISPOSABLE, and treating it as such cost
        # three sessions their work in one night (2026-08-29, kpr-dup-10). The
        # old code went straight from "git worktree list did not name this path"
        # to `shutil.rmtree(..., ignore_errors=True)` over a directory that, in
        # every one of those cases, held a live session's uncommitted edits.
        #
        # TWO WAYS THE OLD TEST SAID "ORPHAN" ABOUT A LIVE CHECKOUT:
        #
        #   * the substring test read `listed.stdout` WITHOUT checking
        #     `returncode`. A git that fails, times out at 10s, or is run
        #     against a repo mid-operation returns empty stdout -- and an empty
        #     haystack contains no path, so EVERY existing directory reads as an
        #     orphan. The failure mode is not "one path misjudged"; it is the
        #     predicate inverting wholesale, precisely when the machine is
        #     already unhealthy.
        #
        #   * `_repo_root` is THIS task's repo. A worktree registered against a
        #     different repository (an external-repo task, or a CLI session's
        #     own checkout that happens to land on the same path) is correctly
        #     absent from this list while being entirely alive.
        #
        # `ignore_errors=True` then made it worse in a way that is easy to miss:
        # a partial delete on Windows takes `.git` and leaves the tree, so the
        # work is not merely deleted, it is deleted AND unrecoverable by
        # ordinary means, because the commits are no longer reachable from any
        # branch.
        #
        # The rule is the one `pr_watcher.reclaim_worktree` already follows:
        # PROVE the directory is disposable, and REFUSE when you cannot tell.
        disposable, why = _worktree_is_disposable(worktree_path, listed)
        if not disposable:
            logger.warning(
                "Refusing to remove %s: %s. Dispatching into it would race a live "
                "session, so this task is left for a human.", worktree_path, why,
            )
            return None
        logger.warning("Orphan worktree dir at %s (%s) — removing and recreating",
                       worktree_path, why)
        import shutil
        shutil.rmtree(worktree_path, ignore_errors=True)
        _sp.run(["git", "worktree", "prune"], cwd=str(_repo_root),
                capture_output=True, text=True, timeout=10)
        _sp.run(["git", "branch", "-D", branch_name], cwd=str(_repo_root),
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
            cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
        )
        if _stale.returncode == 0:
            logger.warning(
                "Stale branch %s found without worktree dir — pruning before recreate",
                branch_name,
            )
            _sp.run(["git", "worktree", "prune"], cwd=str(_repo_root),
                    capture_output=True, text=True, timeout=10)
            _del = _sp.run(["git", "branch", "-D", branch_name], cwd=str(_repo_root),
                    capture_output=True, text=True, timeout=10)
            if _del.returncode != 0:
                # THE BRANCH IS NOT ALWAYS STALE, AND THIS IS WHERE THAT BIT
                # (kph-repark-fni-ana-01). "Stale" here means only "no worktree
                # at the path WE would use" — and for an EXTERNAL task the
                # dispatcher and the worker it launches do not use the same
                # path. The dispatcher looks in
                # `<tmp>/icdev-kanban/<repo>/<task>`; the worker follows
                # CLAUDE.md into `<tmp>/icdev-worktrees/kanban/<task>`. So a
                # LIVE worker worktree reads as a stale leftover on every retry.
                #
                # `git branch -D` refuses that case, correctly and by name.
                # `update-ref -d` is PLUMBING and does not: measured on git
                # 2.55.0.windows.2, `branch -D` returns rc=1 "cannot delete
                # branch 'held' used by worktree at ..." while `update-ref -d`
                # returns rc=0 and deletes the ref anyway. The worker's commits
                # are then reachable from nothing, `git worktree add` still
                # fails (the registration names the branch), and it recreates
                # the NAME at the base commit — so the branch looks intact while
                # pointing somewhere else. On 2026-09-04 that ran against a
                # branch carrying 1,179 lines of unpushed work; it survived only
                # because the worker was still running and re-committed.
                #
                # So the low-level fallback is used ONLY where its comment says
                # it is for: a registration whose directory is GONE and which
                # `git worktree prune` did not clear. A live holder is refused.
                _holder = _live_worktree_holding(_repo_root, branch_name)
                if _holder is not None:
                    logger.warning(
                        "Refusing to delete branch %s: it is checked out in a LIVE "
                        "worktree at %s. `git branch -D` already refused (%s), and "
                        "forcing it through update-ref would orphan that worktree's "
                        "commits. The worktree add below will fail — this is NOT "
                        "transient, and requeuing will not clear it. Land or remove "
                        "that worktree first.",
                        branch_name, _holder,
                        (_del.stderr or "").strip() or f"rc={_del.returncode}",
                    )
                else:
                    # On Windows a branch checked out in a (now-pruned) worktree
                    # may resist `git branch -D`. Nothing live holds it, so the
                    # low-level ref path is safe here — but its RETURN CODE is
                    # checked, because logging "deleted" unconditionally is what
                    # made this condition read as transient to the human who
                    # requeued it at 03:16:51 and watched it repark 11s later.
                    _upd = _sp.run(
                        ["git", "update-ref", "-d", f"refs/heads/{branch_name}"],
                        cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
                    )
                    if _upd.returncode == 0:
                        logger.info(
                            "Stale branch %s deleted via update-ref fallback", branch_name)
                    else:
                        logger.warning(
                            "Stale branch %s could NOT be deleted (branch -D: %s; "
                            "update-ref -d: %s). It is still present, so the worktree "
                            "add below will fail — this is not transient and requeuing "
                            "will not clear it.",
                            branch_name,
                            (_del.stderr or "").strip() or f"rc={_del.returncode}",
                            (_upd.stderr or "").strip() or f"rc={_upd.returncode}",
                        )

    # Determine the best base commit for the new worktree:
    # prefer origin/main so tasks build on the latest pushed state even when
    # the local main branch hasn't been updated (e.g. after a detached-
    # worktree merge).  Falls back to the local default branch.
    # NOTE (done-hardening #5): each task branches off origin/main here — NOT off
    # a sibling task's branch — so branches don't stack and merges can't land out
    # of dependency order. Dependency ordering itself is enforced by the
    # parent-done guard in _move_task, so no extra gate is needed here.
    base_check = _sp.run(
        ["git", "rev-parse", "--verify", f"origin/{_base_branch}"],
        cwd=str(_repo_root), capture_output=True, text=True, timeout=5,
    )
    if base_check.returncode == 0:
        base = f"origin/{_base_branch}"
    else:
        base = _base_branch

    try:
        # Create a new branch from the chosen base for this task
        result = _sp.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base],
            cwd=str(_repo_root),
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
            _sp.run(["git", "worktree", "prune"], cwd=str(_repo_root),
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
            _sp.run(["git", "worktree", "prune"], cwd=str(_repo_root),
                    capture_output=True, text=True, timeout=10)
            return None
        # Verify structural completeness: tools/manifest.md must exist in the
        # worktree. A partial Windows checkout (rmtree file-lock failures) can
        # leave an empty dir with only .git; coherence then fails on every
        # dispatch with "no tools/manifest.md", looping until self_debug fires.
        #
        # ICDEV-ONLY. tools/manifest.md is an ICDev artefact — compass and idea_lab do
        # not have one and never will. Applying it to them tore down a perfectly good
        # worktree and returned None, which the caller reported as "worktree creation
        # failed" and parked the task. That is exactly how the first live compass
        # dispatch failed, and it is the same mistake in miniature as the whole bug this
        # change fixes: judging another repo against ICDev's shape.
        #
        # For an external repo the honest structural check is the one above — git wrote a
        # .git file, so the worktree is registered. We do not know what that repo's tree
        # is supposed to look like, and we must not pretend to.
        _icdev_worktree = _repo_root == BASE_DIR
        if _icdev_worktree and not (worktree_path / "tools" / "manifest.md").exists():
            logger.warning(
                "Worktree dir created for %s but tools/manifest.md is missing "
                "(partial checkout) — cleaning up so next dispatch rebuilds clean",
                task_id,
            )
            import shutil as _shutil
            _shutil.rmtree(worktree_path, ignore_errors=True)
            _sp.run(["git", "worktree", "prune"], cwd=str(_repo_root),
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
    """Merge the kanban task branch into the parent branch using a temporary
    git worktree.  The main repository working tree is NEVER touched — no stash,
    no checkout, no branch switch.  This lets the scheduler dispatch tasks while
    a human is actively editing files in the main repo.

    Strategy (in order):
      1. Create a detached worktree at the current parent-branch commit.
      2. Inside the detached worktree create a temporary branch and attempt a
         fast-forward merge (``--ff-only``).
      3. If ff fails because main diverged, rebase the task branch onto main
         inside the detached worktree and retry ff.
      4. Push from the detached worktree using ``HEAD:{branch}`` so the merge
         reaches origin without needing a local branch named ``main``.
      5. Always clean up the temporary worktree.

    Returns True if merge succeeded (or branch had no commits to merge),
    False on unrecoverable conflict.  On failure the branch is PRESERVED
    (not deleted) so the user can merge manually.
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    default_branch = _base_branch

    # 1) Is there anything to merge?
    try:
        result = _sp.run(
            ["git", "log", f"{default_branch}..{branch_name}", "--oneline"],
            cwd=str(_repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return True  # Nothing to merge
    except Exception as exc:
        logger.warning("Pre-merge commit check failed for %s: %s", task_id, exc)
        return False

    # 2) Resolve the parent-branch commit hash for a detached worktree.
    #    Using a commit hash avoids the "branch already used by worktree" error.
    main_commit_proc = _sp.run(
        ["git", "rev-parse", default_branch],
        cwd=str(_repo_root), capture_output=True, text=True, timeout=5,
    )
    if main_commit_proc.returncode != 0:
        logger.warning("Could not resolve %s for merge of %s", default_branch, task_id)
        return False
    main_commit = main_commit_proc.stdout.strip()

    # 3) Create a detached worktree for the merge
    merge_wt = _task_worktree_path(task_id).parent / f".merge-{task_id}"
    try:
        if merge_wt.exists():
            _sp.run(
                ["git", "worktree", "remove", str(merge_wt), "--force"],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=30,
            )
            _sp.run(
                ["git", "worktree", "prune"],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
            )

        add = _sp.run(
            ["git", "worktree", "add", str(merge_wt), main_commit],
            cwd=str(_repo_root), capture_output=True, text=True, timeout=30,
        )
        if add.returncode != 0:
            logger.warning(
                "Temp merge worktree creation failed for %s: %s",
                task_id, add.stderr[:200],
            )
            return False

        # 4) Create a temporary branch inside the detached worktree so we have
        #    a named branch to push from.
        temp_branch = f"temp-merge-{task_id}"
        co = _sp.run(
            ["git", "checkout", "-b", temp_branch],
            cwd=str(merge_wt), capture_output=True, text=True, timeout=10,
        )
        if co.returncode != 0:
            logger.warning(
                "Checkout temp branch failed for %s: %s", task_id, co.stderr[:200]
            )
            return False

        # 5) Fast-forward merge inside the detached worktree
        merge = _sp.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(merge_wt), capture_output=True, text=True, timeout=30,
        )
        if merge.returncode == 0:
            logger.info(
                "Merged kanban/%s to %s (fast-forward, %d commits)",
                task_id,
                default_branch,
                len(result.stdout.strip().splitlines()),
            )
            # FAIL-CLOSED: only report success if the push actually reached
            # origin. A swallowed push failure here is what let tasks reach
            # 'done' while origin/main never received the commit.
            return _push_main(cwd=str(merge_wt))

        # 6) FF failed — rebase branch onto default_branch inside detached worktree
        logger.info(
            "FF merge failed for %s, attempting rebase onto %s", task_id, default_branch
        )
        rebase = _sp.run(
            ["git", "rebase", default_branch, branch_name],
            cwd=str(merge_wt), capture_output=True, text=True, timeout=60,
        )
        if rebase.returncode != 0:
            _sp.run(
                ["git", "rebase", "--abort"],
                cwd=str(merge_wt), capture_output=True, text=True, timeout=10,
            )
            logger.warning(
                "Rebase conflict for %s: %s — branch preserved",
                task_id,
                rebase.stderr[:200],
            )
            return False

        # Rebase succeeded — re-checkout our temp branch and ff-merge
        _sp.run(
            ["git", "checkout", temp_branch],
            cwd=str(merge_wt), capture_output=True, text=True, timeout=10,
        )
        merge2 = _sp.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(merge_wt), capture_output=True, text=True, timeout=30,
        )
        if merge2.returncode == 0:
            logger.info(
                "Merged kanban/%s to %s (rebase + fast-forward)", task_id, default_branch
            )
            # FAIL-CLOSED: success is contingent on the push reaching origin.
            return _push_main(cwd=str(merge_wt))

        logger.warning(
            "Post-rebase FF merge still failed for %s: %s — branch preserved",
            task_id,
            merge2.stderr[:200],
        )
        return False
    except Exception as exc:
        logger.warning("Merge to main failed for %s: %s", task_id, exc)
        return False
    finally:
        # Always clean up the temporary merge worktree and the temp branch ref
        try:
            if merge_wt.exists():
                _sp.run(
                    ["git", "worktree", "remove", str(merge_wt), "--force"],
                    cwd=str(_repo_root), capture_output=True, text=True, timeout=30,
                )
        except Exception as exc:
            logger.debug("Temp merge worktree cleanup failed for %s: %s", task_id, exc)
        try:
            _sp.run(
                ["git", "branch", "-D", f"temp-merge-{task_id}"],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass


#: Statuses a timeout must NOT demote out of. `done` is obvious; `pr_opened`
#: is the one that was missing — a session whose PR is up has finished the work,
#: and a timeout after that point is a session that overran, not a task that
#: failed. Demoting it to `scheduled` rebuilds output that already exists.
_TIMEOUT_NO_DEMOTE_STATUSES = ("done", "pr_opened", "merged")


def _branch_has_unmerged_commits(task_id: str) -> bool:
    """Return True IFF branch ``kanban/<task_id>`` exists locally AND has commits
    that are not yet on ``origin/<default_branch>``.

    This is the merge-verification primitive behind the done-gate: a task is only
    allowed to reach 'done' when its work has actually landed on origin/main.

    PROVIDER-AGNOSTIC: this checks git/origin state, NOT the worker's self-report.
    The dispatch model may be Claude, Kimi/Moonshot, or Ollama and can swap on
    credit exhaustion mid-task; none of that affects this check, which is why the
    authoritative done-gate lives here and not in the worker's output parsing.

    FAIL-OPEN on infrastructure errors: if the branch does not exist, git is
    unavailable, or the compare errors, return False (do NOT block completion) —
    an unreachable git must never wedge every task's completion. Only a positive
    "branch exists AND has commits not on origin" signal blocks the transition.
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp
    default_branch = _base_branch
    try:
        # Which branches carry this task's work? `kanban/<task_id>` is the
        # convention, but workers routinely add a descriptive suffix
        # (kanban/dwo-mcp-02-d5-audit) or use another prefix
        # (test/dwo-vv-03-d3-trigger-link, fix/dwo-mcp-03-d5-d4-r3). Matching
        # only the exact name made the gate fail open on precisely those
        # branches, which is how work sitting in an open PR reached 'done'.
        candidates = _branches_for_task(task_id, _repo_root)
        if not candidates:
            return False  # nothing to verify (fail-open)
        # Best-effort refresh of the origin ref so the compare is current; the
        # check still works against the stale local origin ref if fetch fails.
        try:
            _sp.run(
                ["git", "fetch", "origin", default_branch, "--quiet"],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass
        for branch_name in candidates:
            if _branch_is_abandoned(branch_name, _repo_root):
                continue
            # `git cherry`, not `git log A..B`: the runner re-lands work under
            # new SHAs constantly (rebases, cherry-picks onto a fresh base), and
            # `git log` counts every one of those as unmerged even though the
            # patch is already on origin. `git cherry` compares by patch-id and
            # prefixes '-' when an equivalent commit is upstream, '+' when it is
            # genuinely absent. Only '+' should block a completion.
            cherry = _sp.run(
                ["git", "cherry", f"origin/{default_branch}", branch_name],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=15,
            )
            if cherry.returncode != 0:
                continue  # this compare errored — fail-open for this ref
            if any(line.startswith("+") for line in cherry.stdout.splitlines()):
                return True  # a branch for this task has work not on origin
        return False
    except Exception as exc:
        logger.warning("_branch_has_unmerged_commits(%s) errored (fail-open): %s", task_id, exc)
        return False


#: branch name -> True when its PR is closed/merged. Populated per process;
#: a branch's PR state does not change often enough to be worth re-asking.
_ABANDONED_BRANCH_CACHE: dict = {}


def timeout_demotion_skip_reason(task_id: str, status: str) -> str:
    """Why a timed-out task must NOT be demoted to ``scheduled``, or "".

    Extracted from the timeout handler so the decision is testable on its own:
    the handler is one branch inside a very long dispatch loop, and a rule that
    can only be exercised by driving the whole loop is a rule nobody checks.

    Returns a human-readable reason (truthy) to skip demotion, or "" to demote
    normally. Fail-OPEN by construction: any error inside the branch probe
    surfaces as "" and the ordinary demotion proceeds, because an unreachable
    git must never wedge the scheduler.
    """
    if status in _TIMEOUT_NO_DEMOTE_STATUSES:
        return f"status is {status!r}"
    try:
        if _branch_has_unmerged_commits(task_id):
            return "branch carries unmerged commits (work exists)"
    except Exception as exc:  # noqa: BLE001 — see the fail-open note above
        logger.debug("timeout branch probe failed for %s: %s", task_id, exc)
    return ""


def _branch_is_abandoned(ref: str, repo_root) -> bool:
    """True when this ref's pull request is already CLOSED or MERGED.

    A superseded branch keeps its commits forever, so the merge gate would go on
    refusing a task whose work actually landed under a *different* branch — the
    re-land pattern. Observed 2026-07-28: every `kanban/dwo-*` branch was
    re-landed, and 34 of them are still pinned by leftover runner worktrees, so
    their refs cannot even be deleted.

    OFFLINE-SAFE and FAIL-CLOSED **for this predicate**: no `gh`, no network, a
    timeout or any error all answer False — "not known to be abandoned" — which
    leaves the ref in the comparison and preserves the existing behaviour. This
    check can only ever *remove* a false refusal, never create a new one.
    """
    if not ref:
        return False
    name = ref.split("origin/", 1)[-1] if ref.startswith("origin/") else ref
    if name in _ABANDONED_BRANCH_CACHE:
        return _ABANDONED_BRANCH_CACHE[name]

    abandoned = False
    import json as _json
    import os as _os
    if _os.environ.get("KANBAN_GATE_SKIP_CLOSED_PRS", "1").strip().lower() not in ("0", "false", "no"):
        import subprocess as _sp
        try:
            out = _sp.run(
                ["gh", "pr", "list", "--head", name, "--state", "all",
                 "--limit", "5", "--json", "state"],
                cwd=str(repo_root), capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                states = {e.get("state") for e in _json.loads(out.stdout)}
                # Abandoned only when EVERY PR for the branch is finished. An
                # open PR means the work is still in flight and must block.
                abandoned = bool(states) and states <= {"CLOSED", "MERGED"}
        except Exception:
            abandoned = False

    _ABANDONED_BRANCH_CACHE[name] = abandoned
    return abandoned


def all_task_refs(repo_root) -> list:
    """Every local + origin branch ref, one ``git for-each-ref``. [] on error.

    Split out so a caller that resolves MANY task ids can pay for the ref listing
    once and hand it to :func:`_branches_for_task`. ``tools/kanban/stranded_audit``
    walks every terminal task on the board (3,169 of them); at one subprocess per
    call that alone exceeded the 300s reflex watchdog.

    Deliberately NOT memoised here. This module is imported by the long-lived
    kanban scheduler, and a cached ref list goes stale the moment a worker pushes
    a new branch — the gate would then find no refs for that task, fail OPEN, and
    let work reach `done` unverified. Freshness is the caller's decision because
    only the caller knows how long its snapshot is allowed to live.
    """
    import subprocess as _sp
    try:
        out = _sp.run(
            ["git", "for-each-ref", "--format=%(refname:short)",
             "refs/heads", "refs/remotes/origin"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return []
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def _branches_for_task(task_id: str, repo_root, refs=None) -> list:
    """Local + remote branch refs whose name contains ``task_id``.

    Ordered so the canonical ``kanban/<task_id>`` is checked first. Matching is
    on a name boundary, so ``dwo-mcp-01`` does not match ``dwo-mcp-01x``.

    A parent id DOES match its decomposed children's refs: ``dwo-mcp-03-d5``
    matches ``kanban/dwo-mcp-03-d5-d1``. That is deliberate — a parent whose
    subtask branch has not merged is not done either — but it means the parent
    is gated on its children's branches as well as its own. If that ever proves
    too strict, tighten the trailing group rather than dropping the boundary.

    FAIL-OPEN: returns [] on any git error.

    ``refs`` optionally supplies the branch listing (see :func:`all_task_refs`)
    so a caller resolving many task ids pays for it once. Omit it and the listing
    is read fresh, which is the only correct default for the dispatch gate.
    """
    import re
    if refs is None:
        refs = all_task_refs(repo_root)
    if not refs:
        return []

    # <task_id> at a name boundary: end of ref, or followed by '-'/'_'/'.'/'/'.
    pat = re.compile(rf"(^|[/_-]){re.escape(task_id)}([/_.-]|$)")
    canonical = f"kanban/{task_id}"
    seen, matches = set(), []
    for ref in refs:
        ref = ref.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        name = ref.split("origin/", 1)[-1] if ref.startswith("origin/") else ref
        if not pat.search(name):
            continue
        if ref in seen:
            continue
        seen.add(ref)
        matches.append(ref)
    matches.sort(key=lambda r: (r not in (canonical, f"origin/{canonical}"), r))
    return matches


#: task_id -> landed-check report, for one scheduler cycle. The check is two
#: subprocess calls (~0.3s) and the same task is asked about twice per dispatch
#: — once by the pre-dispatch gate and once by the prompt writer — so the answer
#: is memoised. Cleared by :func:`clear_landed_cache` at the top of each cycle:
#: a cached "not on main" that outlived the merge it was about is exactly the
#: stale answer this whole module exists to stop reporting.
_LANDED_CACHE: Dict[str, dict] = {}


def clear_landed_cache() -> None:
    """Drop the per-cycle landed-check memo. Called once per scheduler cycle."""
    _LANDED_CACHE.clear()


def _landed_preflight(task_id: str, with_prs: bool = True) -> dict:
    """Is this task id ALREADY on origin/<default>, and who else has a PR open?

    The board tracks task -> PR and nothing checked task -> main, which is how
    ctx-perf-02 (landed as #1641) and ctx-trust-02 (landed as #1638) sat in
    ``pr_opened`` behind #1646 and #1651 — two PRs that could only ever have
    landed as reverts. See :mod:`tools.kanban.landed_check`.

    FAIL-OPEN: any error answers "not checked, not landed", so an unreachable
    git or gh never wedges dispatch.
    """
    if task_id in _LANDED_CACHE:
        return _LANDED_CACHE[task_id]
    try:
        from tools.kanban import landed_check as _lc

        report = _lc.preflight(
            task_id,
            repo_root=_task_repo_root(task_id),
            branch=_task_base_branch(task_id),
            with_prs=with_prs,
        )
    except Exception as exc:  # noqa: BLE001 — advisory check, never load-bearing
        logger.debug("landed preflight failed for %s (fail-open): %s", task_id, exc)
        report = {"task_id": task_id, "checked": False, "landed": False,
                  "referenced": False, "confidence": None, "commits": [],
                  "blocking": False, "prs": None, "reason": str(exc)}
    _LANDED_CACHE[task_id] = report
    return report


def _push_main(cwd: str) -> bool:
    """Push the merged commit to origin/{default_branch}.

    Uses ``HEAD:{branch}`` so the push works from any detached/temp branch
    inside a temporary worktree — the local branch name does not matter.

    The stop hook no longer pushes kanban branches — this is the ONLY
    point where validated work reaches origin/main.

    Returns True iff the push actually reached origin (rc == 0). FAIL-CLOSED:
    a failed or errored push returns False so the caller does NOT treat the
    task as merged. Previously this swallowed push failures (returned None,
    logged a warning), so a task whose push failed was still marked done while
    origin/main never received the commit — the exact done-but-not-on-main bug.
    """
    import subprocess as _sp
    default_branch = _default_branch()
    try:
        push = _sp.run(
            ["git", "push", "origin", f"HEAD:{default_branch}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if push.returncode == 0:
            logger.info("Pushed HEAD:%s to origin after merge", default_branch)
            return True
        logger.warning(
            "Push HEAD:%s to origin failed (rc=%d): %s",
            default_branch,
            push.returncode,
            push.stderr[:200],
        )
        return False
    except Exception as exc:
        logger.warning("Push HEAD:%s to origin error: %s", default_branch, exc)
        return False


# Batch 4 worktree age sweep threshold — worktrees whose owning task is NOT
# currently in_progress and whose marker/dir mtime is older than this are
# force-cleaned. Disk hygiene for the failure-train class (many failed tasks
# leaving 500 MB worktrees each).
# Override via KANBAN_WORKTREE_STALE_AGE_DAYS env var.
_WORKTREE_STALE_AGE_DAYS = _int_env("KANBAN_WORKTREE_STALE_AGE_DAYS", 7)


def _sweep_roots() -> list:
    """Every directory tree a worktree may legitimately live under.

    THE DEFECT THIS EXISTS FOR. `_sweep_old_worktrees` walked exactly one
    directory -- `WORKTREE_BASE`, the repo's own `.tmp/worktrees` -- while
    `tools/git/worktree_paths` had long since moved every actor to
    `%TEMP%/icdev-worktrees/<actor>/...`. The sweep was cleaning the location the
    path policy ABANDONED.

    MEASURED on the live board 2026-08-30, after 292 worktrees had already been
    removed by hand: 39 remained -- 2 under WORKTREE_BASE, 23 under the
    sanctioned root, 14 elsewhere. About 5% coverage, which is why 341
    accumulated while a sweep ran every half hour and found nothing to do.
    """
    roots = []
    if WORKTREE_BASE.is_dir():
        roots.append(WORKTREE_BASE)
    try:
        from tools.git.worktree_paths import worktree_root

        sanctioned = Path(str(worktree_root()))
        if sanctioned.is_dir():
            roots.append(sanctioned)
    except Exception as exc:  # noqa: BLE001 -- a missing resolver must not stop the legacy sweep
        logger.debug("Sweep: sanctioned worktree root unavailable (%s)", exc)
    return roots


def _sweep_candidates(max_depth: int = 4) -> list:
    """Directories that ARE git worktrees, under any sanctioned root.

    The layout under the sanctioned root is NESTED
    (``<root>/<actor>/<session>/<slug>``), not flat like WORKTREE_BASE, so this
    descends rather than listing one level. A directory is a candidate only when
    it carries a `.git` entry -- the actor and session levels are containers and
    must never be handed to `git worktree remove`.
    """
    out: list = []
    seen: set = set()

    def walk(d, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for c in children:
            if not c.is_dir():
                continue
            key = str(c).lower()
            if key in seen:
                continue
            if (c / ".git").exists():
                seen.add(key)
                out.append(c)
                continue          # a worktree is a leaf; never descend into one
            walk(c, depth + 1)

    for root in _sweep_roots():
        walk(root, 1)
    return out


def _worktree_task_id(path):
    """The task a worktree belongs to, from its CHECKED-OUT BRANCH.

    `task_id = sub.name` held only for the flat WORKTREE_BASE layout. Under the
    sanctioned root a directory is named for a slug or a session, so a name-based
    guess would invent task ids that match nothing -- and a task id that matches
    nothing silently defeats the `in_progress` guard, which is the one thing
    standing between this sweep and a live session's worktree. The branch
    (`kanban/<id>`) is what actually ties a worktree to a task; anything else has
    no task, and says so.
    """
    import subprocess as _sp2

    try:
        r = _sp2.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(path),
                     capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    branch = (r.stdout or "").strip()
    if branch.startswith("kanban/"):
        return branch.split("/", 1)[1]

    # THE FLAT LAYOUT'S DIRECTORY NAME IS AUTHORITATIVE, and only there. Under
    # WORKTREE_BASE a worktree is created as `<base>/<task_id>` -- that is the
    # layout's contract, and it holds even for a detached checkout with no
    # branch to read. Falling back to the name ANYWHERE would be the guess this
    # function exists to avoid (under the sanctioned root a directory is named
    # for a slug or a session, and an invented id matches no task, which
    # silently defeats the `in_progress` guard). So the fallback is scoped to
    # the one layout where the name is a fact rather than a guess.
    try:
        if Path(path).resolve().parent == Path(WORKTREE_BASE).resolve():
            return Path(path).name
    except Exception:  # noqa: BLE001
        pass
    return None


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

    for sub in _sweep_candidates():
        task_id = _worktree_task_id(sub)
        if task_id and task_id in in_progress_ids:
            continue
        try:
            age_sec = now_ts - sub.stat().st_mtime
        except OSError:
            continue
        if age_sec < threshold_sec:
            continue

        # AGE IS NOT EVIDENCE OF ABANDONMENT, and this check is what makes the
        # widened scope safe. Before kpr-dup-12 the sweep walked ONE directory
        # holding 2 of the 39 live worktrees, and its only guards were "task not
        # in_progress" and mtime -- then it FORCE-removed. Reaching the other ~25
        # without asking whether they hold work would loose a force-remover on
        # directories it has never touched, which is exactly the harm kpr-dup-10
        # exists to prevent. An old worktree holding unpushed commits is the MOST
        # valuable one to keep: nothing else has that work.
        listed = _sp.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(_canonical_repo_root()), capture_output=True, text=True, timeout=10,
        )
        disposable, why = _worktree_is_disposable(sub, listed)
        if not disposable:
            logger.info("Sweep: keeping %s -- %s", sub, why)
            continue

        try:
            if _remove_worktree(sub):
                logger.info(
                    "Sweep: removed stale worktree %s (age %.1f days, task not "
                    "in_progress, %s)", sub, age_sec / 86400, why,
                )
                # The GUARD above uses the branch-derived task id and skips only
                # on a real match; this returned list is informational, so the
                # directory name is the right fallback and keeps the contract the
                # flat WORKTREE_BASE layout always had.
                removed.append(task_id or sub.name)
        except Exception as exc:
            logger.warning("Sweep: could not remove %s: %s", sub, exc)

    # Drop registry entries whose directory is already gone. Without this, `git worktree
    # list` keeps reporting worktrees that do not exist.
    try:
        _unlock_dead_entries()
        _sp.run(["git", "worktree", "prune"], cwd=str(BASE_DIR),
                capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass

    return removed


def _unlock_dead_entries() -> int:
    """Unlock registry entries whose DIRECTORY NO LONGER EXISTS, so prune can drop them.

    The same bug as _remove_worktree, one layer down: ``git worktree prune`` also refuses
    to touch a LOCKED entry. So an entry that is both locked and whose directory has been
    deleted is unreachable by every cleanup path we have — prune skips it because it is
    locked, and remove never sees it because the sweeper only walks directories that
    exist. It stays in `git worktree list` forever.

    That is how 26 of them were still being reported after a sweep that had genuinely
    removed everything it could reach.

    Unlocking is unambiguously safe here: the working tree is GONE. There is nothing left
    to protect and nothing that can be lost — we are removing a stale pointer to a
    directory that no longer exists.

    Returns the number of dead entries unlocked.
    """
    import subprocess as _sp
    from pathlib import Path as _Path

    listing = _sp.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
    )
    if listing.returncode != 0:
        return 0

    unlocked = 0
    for block in listing.stdout.split("\n\n"):
        if "locked" not in block:
            continue
        line = next((ln for ln in block.splitlines()
                     if ln.startswith("worktree ")), "")
        path = line[len("worktree "):].strip()
        if not path or _Path(path).exists():
            continue  # a live directory keeps its lock — we only touch dead pointers
        result = _sp.run(["git", "worktree", "unlock", path],
                         cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            unlocked += 1

    if unlocked:
        logger.info(
            "Sweep: unlocked %d dead worktree entries (directory gone) so prune can "
            "drop them", unlocked,
        )
    return unlocked


def _remove_worktree(path) -> bool:
    """Actually remove a worktree. Returns True only if it is really gone.

    ## The sweeper was reporting removals it had not performed

    It ran ``git worktree remove <path> --force`` and never looked at the return code.
    ``subprocess.run`` does not raise on a non-zero exit, so the ``except`` clause below
    it never fired — and git REFUSES to remove a locked worktree even with --force:

        fatal: cannot remove a locked working tree;
        use 'remove -f -f' to override or unlock first          (rc=128)

    So every cycle the sweeper logged "Sweep: removed stale worktree ..." and counted it,
    for a worktree that was still there. It had been reporting success for months while
    97 locked worktrees accumulated in .tmp/worktrees, and the log said the cleanup was
    working the whole time.

    A cleanup routine that cannot fail is one that cannot clean up.

    ## Unlocking is safe HERE, and only here

    The caller has already established that this worktree's task is NOT in_progress and
    that the directory has not been touched for _WORKTREE_STALE_AGE_DAYS. A lock on a
    worktree in that state is a leftover from a run that ended days ago, not a live
    agent's claim. We do not unlock anything else.

    ## A directory git has never heard of is still ours to delete

    Reporting the failure fixed the lie but not the leak. ``git worktree remove`` also
    refuses a directory that is not a registered worktree at all:

        fatal: 'C:/AI/ICDev/.tmp/worktrees/foo' is not a working tree   (rc=128)

    That happens whenever the registration is dropped while the directory survives — a
    ``git worktree prune`` after a partial Windows rmtree, a repo re-clone, a crash between
    ``add`` and first write. git will never reclaim those, so returning False left them on
    disk to be re-attempted on the next sweep, forever. 334 of them had accumulated against
    28 live registrations, and 13,095 log lines were this one refusal repeating.

    So: when git says the path is not a working tree, there is no registration to protect
    and nothing for git to do. Delete the directory ourselves. The caller's staleness and
    not-in_progress checks are what make that safe, exactly as they are for the unlock above.
    """
    import shutil as _shutil
    import subprocess as _sp

    # Whether there was anything here to begin with. The orphan branch below
    # reports success when the directory is gone afterwards — which is also true
    # of a path that never existed, so a phantom path counted as a removal and
    # re-inflated the sweep total this function exists to make honest.
    _existed = Path(path).exists()

    result = _sp.run(
        ["git", "worktree", "remove", str(path), "--force"],
        cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        return True

    stderr = (result.stderr or "").lower()

    if "locked" in stderr:
        _sp.run(["git", "worktree", "unlock", str(path)],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
        result = _sp.run(
            ["git", "worktree", "remove", str(path), "--force"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Sweep: unlocked stale worktree %s before removing it", path)
            return True
        stderr = (result.stderr or "").lower()

    # Orphan: on disk but not a registered worktree. git cannot help; we can.
    if "is not a working tree" in stderr:
        if not _existed:
            # Nothing was here. Deleting nothing is not a removal — saying so
            # would put phantom entries back into the sweep count.
            logger.warning(
                "Sweep: %s is neither a worktree nor a directory — nothing to remove", path,
            )
            return False
        # THE SAME DEFECT, SECOND FACE (kpr-dup-10). git's verdict here is
        # stronger than dispatch's was -- it explicitly said "is not a working
        # tree" rather than merely omitting the path from a list -- but it is
        # still a claim about REGISTRATION, not about CONTENT. The state that
        # produces this stderr includes the partial delete that already took
        # `.git` and left the tree, which is exactly when the commits inside are
        # least recoverable. A fixture-based test on the dispatch site would not
        # have caught this one, so it is repaired with the same predicate rather
        # than a second opinion.
        disposable, why = _worktree_is_disposable(Path(path), None)
        if not disposable:
            logger.warning(
                "Sweep: refusing to remove %s: %s. Left for a human.", path, why,
            )
            return False
        _shutil.rmtree(str(path), ignore_errors=True)
        if not Path(path).exists():
            logger.info(
                "Sweep: removed orphan worktree dir %s (not registered with git)", path,
            )
            return True
        logger.warning(
            "Sweep: orphan worktree dir %s survived rmtree (file lock?) — will retry", path,
        )
        return False

    # Say what happened. The previous code's silence here is the whole bug.
    logger.warning(
        "Sweep: git refused to remove %s (rc=%d): %s",
        path, result.returncode, (result.stderr or "").strip()[:200],
    )
    return False


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
            ["git", "diff", "--stat", f"{_default_base_ref()}..{branch}"],
            cwd=str(_task_repo_root(task_id)),
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


def _post_merge_route_smoke(task_id: str, commit_summary: str) -> None:
    """Smoke-test routes affected by this task immediately after merge.

    Creates a Kanban bug task if any route returns a server error, so the
    failure surfaces in the backlog rather than silently breaking the dashboard.
    """
    try:
        from tools.testing.route_smoke import (
            _routes_for_changed_files,
            _server_up,
            run_smoke,
        )
    except ImportError:
        logger.debug("route_smoke not available — skipping post-merge smoke")
        return

    base = "http://localhost:5050"
    if not _server_up(base, timeout=3.0):
        logger.debug("Dashboard not running — post-merge smoke skipped for %s", task_id)
        return

    # Derive affected routes from the commit summary (filenames in short log)
    changed_files: list[str] = []
    for line in commit_summary.splitlines():
        # git log --oneline lines don't contain filenames; use a best-effort
        # parse of task_id path components as the canvas slug
        pass

    # Better: list files changed on the branch via git
    try:
        import subprocess as _sp3
        _flist = _sp3.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=str(_task_repo_root(task_id)),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if _flist.returncode == 0:
            changed_files = [f.strip() for f in _flist.stdout.splitlines() if f.strip()]
    except Exception:
        pass

    routes = _routes_for_changed_files(changed_files) if changed_files else []
    if not routes:
        logger.debug("No affected routes detected for %s — smoke skipped", task_id)
        return

    logger.info(
        "Post-merge smoke: %d routes for task %s (changed files: %s)",
        len(routes), task_id, changed_files[:5],
    )
    passed, results = run_smoke(routes, base=base, timeout=10.0, verbose=False)

    if passed:
        logger.info("Post-merge smoke PASSED for task %s (%d routes)", task_id, len(routes))
        return

    # Smoke failed — log failures and create a bug task
    failures = [r for r in results if not r["ok"]]
    failure_lines = "\n".join(
        f"  - {f['route']}: HTTP {f.get('status')} — {f.get('error')}"
        for f in failures
    )
    logger.warning(
        "Post-merge smoke FAILED for task %s: %d/%d routes broken\n%s",
        task_id, len(failures), len(routes), failure_lines,
    )

    try:
        from tools.logging.build_logger import capture_pytest
        capture_pytest(
            returncode=1,
            stdout=f"Post-merge smoke failed for {task_id}:\n{failure_lines}",
            stderr="",
            duration_s=0,
            passed=len(results) - len(failures),
            failed=len(failures),
            skipped=0,
        )
    except Exception as _log_exc:
        logger.debug("build_logger capture failed: %s", _log_exc)

    # Create a high-priority bug task so the failure enters the remediation queue
    try:
        import json as _json
        import urllib.request as _ureq
        desc = (
            f"## Post-Merge Smoke Failure\n\n"
            f"Task **{task_id}** was merged to main but the following routes are broken:\n\n"
            f"{failure_lines}\n\n"
            f"**Triggered by:** post-merge route smoke gate\n"
            f"**Commit summary:**\n```\n{commit_summary[:500]}\n```\n\n"
            f"**Changed files:** {', '.join(changed_files[:10])}\n\n"
            f"Fix the routes listed above so they return HTTP 200 without server errors."
        )
        payload = _json.dumps({
            "title": f"[SMOKE-FAIL] Post-merge: {len(failures)} route(s) broken — {task_id}",
            "task_type": "bug",
            "priority": "critical",
            "status": "backlog",
            "description": desc,
        }).encode("utf-8")
        req = _ureq.Request(
            f"{base}/api/kanban/tasks",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ureq.urlopen(req, timeout=10) as resp:
            body = _json.loads(resp.read())
            logger.warning(
                "Created smoke-fail bug task %s for post-merge failures of %s",
                body.get("id"), task_id,
            )
    except Exception as exc:
        logger.warning("Failed to create smoke-fail bug task for %s: %s", task_id, exc)


def _ensure_pr_base(pr_ref: str, task_id: str) -> str | None:
    """Verify a PR targets the repo default branch; retarget it if not.

    Incident 2026-07-08: PR #114 (ground-dic-05) was opened with base
    feat/rfi-six-parts instead of main and auto-merged there by
    pr_watcher, stranding the change off-main. ``pr_ref`` may be a PR
    URL, number, or head branch name — the branch form also catches PRs
    the task agent opened itself with an explicit wrong --base.

    Returns the PR URL if one exists for ``pr_ref``, else None.
    """
    import json as _json
    import subprocess as _sp

    default_branch = _default_branch()
    try:
        view = _sp.run(
            ["gh", "pr", "view", pr_ref, "--json", "baseRefName,url"],
            cwd=str(_task_repo_root(task_id)), capture_output=True, text=True, timeout=30,
        )
        if view.returncode != 0:
            logger.warning(
                "PR flow: base check failed for %s (%s): %s",
                task_id, pr_ref, view.stderr.strip(),
            )
            return None
        data = _json.loads(view.stdout or "{}")
        pr_url = (data.get("url") or "").strip() or None
        base = (data.get("baseRefName") or "").strip()
        if not base or base == default_branch:
            return pr_url
        logger.warning(
            "PR flow: PR %s for task %s targets '%s' instead of default "
            "'%s' — retargeting", pr_url or pr_ref, task_id, base, default_branch,
        )
        edit = _sp.run(
            ["gh", "pr", "edit", pr_ref, "--base", default_branch],
            cwd=str(_task_repo_root(task_id)), capture_output=True, text=True, timeout=30,
        )
        if edit.returncode != 0:
            logger.warning(
                "PR flow: retarget to %s failed for %s: %s",
                default_branch, task_id, edit.stderr.strip(),
            )
        return pr_url
    except Exception as exc:
        logger.warning("PR flow: base verification errored for %s: %s", task_id, exc)
        return None


def _open_prs_for_task(task_id: str, repo_root, *, exclude_branch: str = "") -> list:
    """Open PRs whose head branch belongs to ``task_id``.

    Returns ``[{"url", "number", "branch"}, ...]``, newest first.

    A task's work does not always live on ``kanban/<task_id>``: workers
    routinely push a descriptive suffix (``kanban/<id>-land``) or another prefix
    (``fix/<id>-...``), which is why `_branches_for_task` exists. Asking gh only
    about the canonical branch therefore misses the PR a worker already opened,
    and the dispatcher goes on to open a second one for the same task.
    """
    import json as _json
    import subprocess as _sp

    out = []
    for branch in _branches_for_task(task_id, repo_root):
        short = branch.split("origin/", 1)[-1]
        if exclude_branch and short == exclude_branch:
            continue
        try:
            r = _sp.run(
                ["gh", "pr", "list", "--head", short, "--state", "open",
                 "--json", "url,number,headRefName,createdAt"],
                cwd=str(repo_root), capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                continue
            for pr in _json.loads(r.stdout or "[]"):
                if any(p["number"] == pr.get("number") for p in out):
                    continue
                out.append({
                    "url": pr.get("url", ""), "number": pr.get("number"),
                    "branch": pr.get("headRefName", short),
                    "createdAt": pr.get("createdAt", ""),
                })
        except Exception as exc:  # noqa: BLE001 — best-effort discovery
            logger.debug("PR flow: open-PR lookup failed for %s: %s", short, exc)
    return sorted(out, key=lambda p: p.get("createdAt") or "", reverse=True)


def _supersede_stale_prs(task_id: str, keep_url: str, keep_branch: str, repo_root) -> list:
    """Close a task's OTHER open PRs once ``keep_url`` carries the work.

    A retry works on a fresh branch, so without this a task accumulates competing
    PRs — gdx-aud-01 reached three (#1135, #1220, #1221), and pr_linker could only
    guess which one mattered.

    Refuses to close a PR whose branch holds commits the surviving branch does
    not: ``git cherry`` compares by patch-id, so a rebase or cherry-pick of the
    same work does not count as unique. If anything is genuinely only on the old
    branch, the PR is left open and a warning is logged — losing committed work
    to tidy the board would be a far worse bug than a duplicate PR.

    Returns the list of closed PR URLs.
    """
    import subprocess as _sp

    closed = []
    for pr in _open_prs_for_task(task_id, repo_root, exclude_branch=keep_branch):
        if pr["url"] == keep_url:
            continue
        try:
            cherry = _sp.run(
                ["git", "cherry", keep_branch, f"origin/{pr['branch']}"],
                cwd=str(repo_root), capture_output=True, text=True, timeout=30,
            )
            if cherry.returncode != 0:
                logger.warning(
                    "PR flow: cannot compare %s against %s for task %s — leaving "
                    "PR %s open", pr["branch"], keep_branch, task_id, pr["url"],
                )
                continue
            unique = [ln for ln in cherry.stdout.splitlines() if ln.startswith("+")]
            if unique:
                logger.warning(
                    "PR flow: NOT closing %s for task %s — its branch %s has %d "
                    "commit(s) absent from %s",
                    pr["url"], task_id, pr["branch"], len(unique), keep_branch,
                )
                continue
            _sp.run(
                ["gh", "pr", "close", str(pr["number"]), "--comment",
                 f"Superseded by {keep_url} — the same task ({task_id}) was retried "
                 f"on `{keep_branch}`, and every commit on `{pr['branch']}` is "
                 f"already present there (compared by patch-id). Closing so the "
                 f"task has exactly one open PR."],
                cwd=str(repo_root), capture_output=True, text=True, timeout=60,
            )
            closed.append(pr["url"])
            logger.info(
                "PR flow: closed superseded PR %s for task %s (kept %s)",
                pr["url"], task_id, keep_url,
            )
        except Exception as exc:  # noqa: BLE001 — never block the PR flow
            logger.warning("PR flow: supersede failed for %s: %s", pr["url"], exc)
    return closed


#: The ONE `gh pr create` failure that is retried without ``--draft``
#: (kpr-watch-06). Draft PRs are a GitHub plan feature; every other failure —
#: a rejected push, a bad base, an existing PR — must still fail exactly as it
#: did before, so this pattern is deliberately narrow rather than a substring
#: test for "draft".
_DRAFT_UNSUPPORTED_RE = re.compile(
    r"draft pull requests? (are|is) not supported", re.IGNORECASE)


def _pr_opens_as_draft() -> bool:
    """True when the runner opens a kanban PR as a DRAFT (kpr-watch-06).

    Default ON — the fail-safe direction. ``ICDEV_KANBAN_PR_DRAFT=0`` (also
    ``false``/``no``/``off``) restores the pre-inversion behaviour, and is the
    switch to reach for if a deployment's ``pr_watcher`` cannot promote drafts;
    never neutralise the draft by turning ``auto_ready_draft_prs`` off as well.
    """
    import os as _os

    return _os.environ.get("ICDEV_KANBAN_PR_DRAFT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _push_branch_and_open_pr(task_id: str, commit_summary: str) -> str | None:
    """Push the kanban branch to origin and open a GitHub PR.

    Returns the PR URL on success, None on failure.
    The branch is NOT merged here — pr_watcher.py (OPT-70) polls CI and
    auto-merges when green, so the kanban board shows a real PR URL in
    executor_url just like Claude CLI does.
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    default_branch = _base_branch

    # Nothing to push?
    check = _sp.run(
        ["git", "log", f"{default_branch}..{branch_name}", "--oneline"],
        cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0 or not check.stdout.strip():
        logger.info("PR flow: no commits to push for %s — skipping PR", task_id)
        return None

    # Push branch (--force-with-lease is safe: only overwrites if nobody else pushed)
    push = _sp.run(
        ["git", "push", "origin", branch_name, "--force-with-lease"],
        cwd=str(_repo_root), capture_output=True, text=True, timeout=60,
    )
    if push.returncode != 0:
        logger.warning("PR flow: branch push failed for %s: %s", task_id, push.stderr.strip())
        return None

    # Fetch task title for a human-readable PR title
    pr_title = f"kanban: {task_id}"
    try:
        with get_connection() as _tc:
            _row = _tc.execute(
                "SELECT title FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            if _row and _row[0]:
                pr_title = _row[0][:72]
    except Exception:
        pass

    # task -> main before task -> PR (trust-disc-05). #1646 and #1651 were opened
    # against tasks whose work had ALREADY merged under a different PR number, so
    # both re-applied changes already present against files that had moved on —
    # #1651's diff was -38/+26 on rest_v1.py, i.e. it would have DELETED 38 lines
    # main currently has. A revert wearing a feature's clothes, and nothing on the
    # board could see it, because the board only ever asked about the PR.
    _landed_banner = ""
    try:
        from tools.kanban.landed_check import format_warning as _fmt_landed

        _landed = _landed_preflight(task_id)
        _landed_msg = _fmt_landed(_landed)
        if _landed_msg:
            logger.warning("PR flow: landed check fired for %s:\n%s", task_id, _landed_msg)
            _landed_banner = (
                "> [!WARNING]\n> **This task id is already on the default branch.**\n"
                + "\n".join(f"> {ln}" for ln in _landed_msg.splitlines())
                + "\n>\n> Review this diff as a possible REVERT before merging.\n\n"
            )
        if _landed.get("blocking"):
            logger.warning(
                "PR flow: NOT opening a PR for %s — its id is already on %s "
                "(KANBAN_LANDED_CHECK=enforce)", task_id, _landed.get("ref"))
            return None
    except Exception as _lc_exc:  # noqa: BLE001 — never block the PR flow
        logger.debug("PR flow: landed check failed for %s: %s", task_id, _lc_exc)

    pr_body = (
        f"{_landed_banner}"
        f"Autonomous kanban task: **{task_id}**\n\n"
        f"{commit_summary or '_no commit summary_'}\n\n"
        "---\n🤖 Generated by ICDEV Kanban Scheduler\n"
        "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )
    # One task, one PR (kpr-dup-02). A retry runs on a fresh branch, so
    # `gh pr create` happily opens a SECOND PR for a task that already has one
    # — the create below only fails when a PR exists for THIS exact head.
    # gdx-aud-01 reached three open PRs that way. If an open PR already carries
    # this branch's commits, reuse it instead of adding another.
    for _existing in _open_prs_for_task(task_id, _repo_root, exclude_branch=branch_name):
        try:
            _cherry = _sp.run(
                ["git", "cherry", f"origin/{_existing['branch']}", branch_name],
                cwd=str(_repo_root), capture_output=True, text=True, timeout=30,
            )
        except Exception:  # noqa: BLE001
            continue
        if _cherry.returncode != 0:
            continue
        if any(ln.startswith("+") for ln in _cherry.stdout.splitlines()):
            continue  # this branch has work that PR does not — a new PR is right
        logger.info(
            "PR flow: task %s already has open PR %s carrying these commits — "
            "reusing it instead of opening a second", task_id, _existing["url"],
        )
        _ensure_pr_base(_existing["url"], task_id)
        return _existing["url"]

    # OPEN IT AS A DRAFT (kpr-watch-06). The PR used to open READY, so anything
    # wanting to hold one had to convert it back to a draft before the watcher's
    # next 30s poll — an external poller with a time window and a session
    # lifetime, neither of which is a property a safety control may have.
    #
    # Inverted, the hold is a ROW. `pr_watcher.PRWatcher._mark_ready` promotes
    # the draft only once CI is green AND the task is not a manual-gate sentinel
    # AND `tools.kanban.deps.blocking_deps` is empty — the same interlock
    # `promote_backlog_to_scheduled` already reads. So the ABSENCE of a decision
    # now leaves work HELD rather than merged, which is the correct direction
    # for a loop that merges to main unattended.
    #
    # Stand it down with ICDEV_KANBAN_PR_DRAFT=0, which is auditable in a way a
    # shell operator inside a JSON string is not. Do NOT also turn
    # `auto_ready_draft_prs` off: that combination is the one failure mode
    # strictly worse than the old default — every kanban PR stuck in draft with
    # nothing left in the loop able to clear it.
    _create_argv = [
        "gh", "pr", "create",
        "--title", pr_title,
        "--body", pr_body,
        "--head", branch_name,
        "--base", default_branch,
    ]
    if _pr_opens_as_draft():
        _create_argv.append("--draft")
    create = _sp.run(
        _create_argv,
        cwd=str(_repo_root), capture_output=True, text=True, timeout=60,
    )
    if create.returncode != 0 and "--draft" in _create_argv and _DRAFT_UNSUPPORTED_RE.search(
            create.stderr or ""):
        # A FORGE THAT CANNOT DO DRAFTS AT ALL (kpr-watch-06). Drafts are a
        # GitHub plan feature, so an EXTERNAL task targeting a repository
        # without them would otherwise stop opening PRs entirely — a whole class
        # of repos broken by a safety default they cannot express. Retry ready,
        # and say so LOUDLY: the property is unavailable on this forge, which is
        # a different thing from it being switched off, and a reader must be
        # able to tell those apart. Narrow on purpose — only this one error
        # retries, so a rejected push or a bad base still fails as before.
        logger.warning(
            "PR flow: %s does not support draft PRs (%s) — opening %s READY. "
            "The draft hold is UNAVAILABLE on this forge, not disabled; nothing "
            "will hold this PR back except the watcher's own gates.",
            _repo_root, (create.stderr or "").strip()[:200], task_id,
        )
        _create_argv.remove("--draft")
        create = _sp.run(
            _create_argv,
            cwd=str(_repo_root), capture_output=True, text=True, timeout=60,
        )

    if create.returncode != 0:
        logger.warning("PR flow: gh pr create failed for %s: %s", task_id, create.stderr.strip())
        # The task agent may already have opened a PR for this branch
        # (possibly with a wrong --base). The base guard resolves the PR
        # by head branch, retargets it to the default branch if needed,
        # and returns its URL so the watcher tracks the right PR.
        existing_url = _ensure_pr_base(branch_name, task_id)
        if existing_url:
            logger.info("PR flow: reusing existing PR %s for task %s", existing_url, task_id)
        return existing_url

    pr_url = create.stdout.strip()
    _ensure_pr_base(pr_url, task_id)
    logger.info("PR flow: opened PR %s for task %s", pr_url, task_id)
    # A new PR was warranted (this branch had work the others lacked), so the
    # earlier attempts are superseded. Close only the ones whose commits are all
    # present here — anything with unique work stays open and gets a warning.
    _supersede_stale_prs(task_id, pr_url, branch_name, _repo_root)
    return pr_url


def _cleanup_worktree(task_id: str):
    """Merge the kanban task branch to main (fast-forward) then remove
    the worktree. If merge fails, the branch is preserved for manual
    review and the worktree is still cleaned up (disk hygiene).
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp
    import os as _os

    branch_name = f"kanban/{task_id}"
    worktree_path = _task_worktree_path(task_id)

    # Detach the worktree FIRST so the branch ref isn't held while we
    # rebase. Commits remain safe on refs/heads/kanban/<task_id> after
    # the worktree is gone. Prior ordering ran merge before detach, so
    # rebase consistently failed with "already used by worktree" and
    # every post-dispatch-divergence task got preserved unnecessarily.
    try:
        if worktree_path.exists():
            _sp.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=str(_repo_root),
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
            ["git", "log", "--oneline", f"{_base_branch}..kanban/{task_id}"],
            cwd=str(_repo_root), capture_output=True, text=True, timeout=10,
        )
        _commit_summary = _log.stdout.strip()[:1000] if _log.returncode == 0 else ""
    except Exception:
        pass

    # PR flow: push branch + open PR instead of direct-merging to main.
    # pr_watcher.py (OPT-70) polls the PR, monitors CI, and auto-merges when green.
    _pr_flow = _os.environ.get("ICDEV_KANBAN_PR_FLOW", "").lower() in ("1", "true", "yes")
    _pr_url: str | None = None
    if _pr_flow:
        _pr_url = _push_branch_and_open_pr(task_id, _commit_summary)
        merged_ok = _pr_url is not None  # "submitted" means PR is open
    else:
        merged_ok = _merge_worktree_to_main(task_id)

    # Post-merge route smoke — only in direct-merge mode (code not on main yet in PR flow).
    if merged_ok and not _pr_flow:
        _post_merge_route_smoke(task_id, _commit_summary)

    # Persist change metrics + branch info to kanban_tasks (best-effort)
    try:
        with get_connection() as _ds_conn:
            if _pr_flow and _pr_url:
                _ds_conn.execute(
                    "UPDATE kanban_tasks SET "
                    "files_changed = %s, lines_added = %s, lines_removed = %s, "
                    "branch_name = %s, commit_summary = %s, executor_url = %s "
                    "WHERE id = %s",
                    (
                        diff_stats["files_changed"], diff_stats["lines_added"], diff_stats["lines_removed"],
                        f"kanban/{task_id}", _commit_summary or None,
                        _pr_url,
                        task_id,
                    ),
                )
            else:
                _ds_conn.execute(
                    "UPDATE kanban_tasks SET "
                    "files_changed = %s, lines_added = %s, lines_removed = %s, "
                    "branch_name = %s, commit_summary = %s "
                    "WHERE id = %s",
                    (
                        diff_stats["files_changed"], diff_stats["lines_added"], diff_stats["lines_removed"],
                        f"kanban/{task_id}", _commit_summary or None,
                        task_id,
                    ),
                )
    except Exception as _ds_exc:
        logger.warning("diff_stats write failed for %s: %s", task_id, _ds_exc)

    try:
        if _pr_flow and merged_ok:
            # Branch stays on origin — pr_watcher will delete it after auto-merge.
            logger.info(
                "PR flow: branch kanban/%s pushed; PR %s open for task %s",
                task_id, _pr_url, task_id,
            )
        elif merged_ok:
            # Direct-merge: clean up local branch after successful push to main.
            _sp.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(_repo_root),
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


def _pr_flow_enabled() -> bool:
    """True when the runner opens a PR instead of merging straight to main."""
    import os as _os

    return _os.environ.get("ICDEV_KANBAN_PR_FLOW", "").lower() in ("1", "true", "yes")


def _check_worktree_commits(task_id: str) -> bool:
    """Check if the worktree branch has new commits vs the parent branch."""
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    _base_branch = _task_base_branch(task_id)
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    try:
        result = _sp.run(
            ["git", "log", "HEAD.." + branch_name, "--oneline"],
            cwd=str(_repo_root),
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
                "WHERE id LIKE %s AND status != 'done'",
                (pattern,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return True, []  # fail-open on DB error
    unfinished = [dict(r)["id"] for r in rows]
    return len(unfinished) == 0, unfinished


from tools.kanban.gates import is_manual_gate as _is_manual_gate  # noqa: F401
from tools.kanban.fixtures import is_test_fixture as _is_test_fixture  # noqa: F401
from tools.kanban.deps import (  # noqa: E402
    dep_clause_sql as _dep_clause_sql,
    junction_dep_ids as _junction_dep_ids,
    parent_holds_done as _parent_holds_done,
    scalar_is_superseded as _scalar_is_superseded,
)


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
        # Native task dependency gating. The rule is ONE rule and it lives in
        # tools.kanban.deps, rendered here as SQL so the dispatch query can stay
        # set-based (kpr-fix-02). It used to be written inline and ANDed the
        # scalar depends_on_task_id with the junction table, which let SEEDING
        # ORDER — the linear chain a seeder writes as it walks its list —
        # override the real fan-in graph. Four CEF tasks with every genuine
        # prerequisite already done sat in backlog for a day because of it.
        #
        # dep_params carries the manual-gate LIKE patterns as BOUND PARAMETERS.
        # A literal % in the clause would be a psycopg format directive the
        # moment any parameter is passed — including one RLS injects — so it
        # must never be interpolated. Splice dep_params in at the POSITION the
        # clause appears in each statement.
        dep_clause, dep_params = _dep_clause_sql("kt")

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
            "kt.created_at ASC",
            dep_params,
        ).fetchall()
        # `_is_test_fixture` is the SECOND door. Promotion (the first) already
        # stops every case reproduced so far, but a card can reach `scheduled`
        # by a dashboard move or decay-promotion without passing through it,
        # and an E2E fixture dispatched from there costs the same wasted
        # worker session. Same belt-and-braces the manual gate gets.
        result = [
            dict(r) for r in scheduled
            if not _is_manual_gate(dict(r).get("id"), dict(r).get("title"))
            and not _is_test_fixture(dict(r).get("id"), dict(r).get("title"))
        ]

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

        # A `scheduled` row can hold a dispatch_pid whose process is gone: the
        # dispatch died before the row reached in_progress, so startup_recovery
        # — which sweeps WHERE status = 'in_progress' — never looks at it. The
        # row is then neither running nor reclaimable and the slot it would have
        # used is never used. Reclaim per cycle rather than at startup only,
        # because the death happens mid-run. Conservative: a PID whose liveness
        # cannot be determined is left alone (exa-bench-10, 2026-08-12).
        try:
            from tools.kanban.startup_recovery import (
                reclaim_stale_scheduled_dispatches,
            )

            reclaim_stale_scheduled_dispatches()
        except Exception as _rc_exc:  # noqa: BLE001 — never wedge dispatch
            logger.warning("kanban: scheduled-dispatch reclaim skipped: %s", _rc_exc)

        # Rate-limit both scheduled dispatch and backlog auto-promotion.
        # Cap scheduled tasks by available slots so we never exceed MAX_IN_PROGRESS.
        current_in_progress = _count_in_progress()
        pending_prompts = _count_pending_prompts()

        available_slots = MAX_IN_PROGRESS - current_in_progress

        # Flow control (clx-flow-01). MAX_IN_PROGRESS bounds tasks that are
        # EXECUTING; it does not bound finished-but-unreviewed output. A task
        # that moves to pr_opened stops being counted here, frees a slot, and
        # the loop dispatches more — so open PRs can stack without limit,
        # conflicting with each other and deferring the human review that is
        # supposed to be this loop's feedback signal. OFF unless
        # KANBAN_BACKPRESSURE_ENABLED is set: throttling autonomous throughput
        # is an operator's call, and this returns available_slots untouched
        # when disabled.
        try:
            from tools.kanban.backpressure import apply_backpressure

            available_slots = apply_backpressure(available_slots)
        except Exception as _bp_exc:  # noqa: BLE001 — never wedge dispatch
            logger.warning("backpressure check skipped: %s", _bp_exc)

        if available_slots <= 0:
            return []  # At capacity — don't dispatch any scheduled or backlog tasks

        # Drop tasks the dispatcher would refuse anyway (open PR / just
        # completed) BEFORE truncating. Truncating first let un-dispatchable
        # tasks hold slots they could never use and starved everything behind
        # them — see _drop_respawn_guarded.
        result = _drop_respawn_guarded(result)

        # Serialize a card against an in-flight SIBLING that edits the same file
        # (mfx-sib-01). Same reason as the filter above: a held task must yield
        # its selection slot BEFORE the truncation, or it occupies a slot it can
        # never use. See _drop_sibling_overlapped.
        result = _drop_sibling_overlapped(result)

        # Cap scheduled results to available slots (prevents burst on restart)
        result = result[:available_slots]

        if pending_prompts >= MAX_AUTO_PROMOTE:
            return result  # Prompt files waiting, don't add more backlog

        slots = min(
            MAX_AUTO_PROMOTE,
            MAX_IN_PROGRESS - current_in_progress - len(result),
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
            "       OR kt.last_failure_reason NOT LIKE %s) "
            f"  AND {dep_clause} "  # nosec B608
            "ORDER BY "
            "CASE WHEN kt.depends_on_task_id IS NOT NULL THEN 0 ELSE 1 END, "
            "CASE kt.priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "kt.created_at ASC "
            "LIMIT %s",
            ("QUARANTINED by self_debug%", *dep_params, slots),
        ).fetchall()
        result.extend(
            d for d in (dict(r) for r in backlog)
            if not _is_manual_gate(d.get("id"), d.get("title"))
        )

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
                    "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
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
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
                ("decomposed", _utcnow_iso(), task["id"]),
            )
            conn.commit()
            print(
                f"  Kanban: decomposed batch {task['id']} into "
                f"{len(created_children)} children ({rule or 'unknown'} rule)"
            )
            # ── LESSONS LEARNED: auto-decomposed batch ────────────────
            try:
                from tools.workflow.lesson_learned import analyze_task, write_lesson
                lesson = analyze_task(task["id"], outcome="auto_decomposed")
                write_lesson(lesson)
            except Exception:
                pass
        except Exception as exc:
            print(f"  Kanban: failed to mark batch decomposed: {exc}")

        # Add children to the dispatch queue (up to MAX_AUTO_PROMOTE)
        result.extend(created_children[:MAX_AUTO_PROMOTE])

    return result


# Step labels for phase-exit gate decomposition (matches established F-gate / E-gate sub-task pattern)
#
# These five steps are DETERMINISTIC tool invocations, so they are executed by
# _dispatch_via_tool_runner rather than handed to an LLM. Wrapping a 40-second
# subprocess in a 1200-second agent dispatch was pure overhead, and the agent
# had no way to distinguish a failure it caused from one already present on
# main — so a single pre-existing failure made every gate sub-task unwinnable.
# The descriptions are kept human-readable for the board.
_PHASE_GATE_STEPS = [
    ("codelens", "CodeLens scan",
     "Runs CodeLens (py_compile + ruff + bandit, delta vs main) over the phase branch."),
    ("coherence", "Coherence check",
     "Runs the FULL coherence tier, comparing failures per-check-id against the "
     "cached main baseline so pre-existing failures do not block the gate."),
    ("e2e", "E2E dashboard test",
     "Runs the Selenium/Playwright dashboard lifecycle test."),
    ("pytest", "Regression pytest",
     "Runs pytest over the phase branch's changed test files."),
    ("companion", "Companion sync",
     "Runs: python tools/dx/companion.py --sync --write --json (best-effort)."),
]

_GATE_STEP_SLUGS = tuple(slug for slug, _label, _desc in _PHASE_GATE_STEPS)
_GATE_STEP_RE = re.compile(
    r"-\d+-(" + "|".join(_GATE_STEP_SLUGS) + r")$", re.IGNORECASE
)


def _gate_step_slug(task_id: str) -> Optional[str]:
    """Return the gate-step slug for an auto-decomposed phase-gate child task."""
    match = _GATE_STEP_RE.search(task_id or "")
    return match.group(1).lower() if match else None


def _run_gate_step(slug: str, work_dir: str, task_id: str) -> Tuple[bool, str]:
    """Execute one phase-exit gate step natively. Returns (passed, detail).

    Phase gates validate a whole phase rather than one task's diff, so the
    scans run unscoped (full coherence tier, whole-tree CodeLens). Coherence
    still compares per-check-id against the main baseline, which is what makes
    the step winnable when main is already red.
    """
    from tools.workflow.validated_commit import (  # noqa: PLC0415
        _run_codelens, _run_coherence, _run_companion_sync, _run_e2e, _run_pytest,
    )

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{_default_base_ref()}...HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=work_dir, timeout=30,
        )
        changed = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    except Exception:
        changed = []
    modified_py = [
        f for f in changed
        if f.endswith(".py") and (Path(work_dir) / f).exists()
    ]

    if slug == "codelens":
        ok, reason, _m = _run_codelens(work_dir, modified_py, True)
        return ok, reason
    if slug == "coherence":
        ok, reason = _run_coherence(
            work_dir, compare_to_main=True, changed_files=None,
            timeout=MAX_EXECUTION_SECONDS_SCAN, tier="full",
        )
        # None = could not be evaluated. Do NOT block the phase on an
        # unevaluated gate, but say so plainly instead of reporting a pass.
        return ok is not False, reason
    if slug == "pytest":
        passed, failed = _run_pytest(work_dir, changed, float(MAX_EXECUTION_SECONDS_SCAN))
        if passed is None:
            return True, "pytest not run — no changed test files on this branch"
        return passed, ("pytest passed" if passed else f"pytest failed: {', '.join(failed)}")
    if slug == "e2e":
        ok, reason, _m = _run_e2e(work_dir, True, modified_files=changed)
        return ok, reason
    if slug == "companion":
        ok, reason = _run_companion_sync()
        return True, reason  # best-effort: never blocks a phase gate
    return True, f"unknown gate step '{slug}' — skipped"


# ---------------------------------------------------------------------------
# Deterministic tool tasks (the non-phase-gate shape)
# ---------------------------------------------------------------------------
# A board task whose entire job is one documented `python tools/...` scan does
# not need a 900-1800s LLM dispatch wrapped around a ~40s subprocess. Such a
# task opts in with a marker line anywhere in its description:
#
#     TOOL-RUNNER: python tools/testing/health_check.py --json
#
# The marker only SELECTS from the closed set below — it cannot introduce a new
# command, and that distinction is the entire point. Task descriptions are
# written by an LLM, so a prefix-only check ("starts with python tools/") would
# let anything in the tree run unattended, `tools/db/init_icdev_db.py` included.
# Two gates apply, in order:
#
#   1. tools/skills/invoke.py::_is_safe_command — the shared prefix allowlist,
#      reused rather than re-implemented (tools/agent_runtime/cron.py reuses the
#      same one for its script jobs).
#   2. _TOOL_RUNNER_COMMANDS — this module's closed set, compared on the
#      canonical argv tuple so that spacing, `python3`, or a backslash path
#      cannot smuggle an unlisted command past a string compare.
#
# Membership is deliberately limited to read-only scans. A command that fails
# either gate is REFUSED, not run: the task falls through to the normal LLM
# executor chain, which is exactly the pre-existing behaviour for every task.
_TOOL_RUNNER_COMMANDS: frozenset = frozenset({
    ("python", "tools/testing/health_check.py", "--json"),
    ("python", "tools/db/storage.py", "--health", "--json"),
    ("python", "tools/awareness/health_prober.py", "--run-all", "--json"),
    ("python", "tools/awareness/drift_detector.py", "--detect", "--json"),
    ("python", "tools/awareness/gap_detector.py", "--detect", "--json"),
    ("python", "tools/workflow/coherence_checker.py", "--all", "--gate"),
})

_TOOL_RUNNER_MARKER_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?TOOL-RUNNER:[ \t]*`?(?P<cmd>[^`\r\n]+?)`?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _canonical_command(cmd: str) -> Optional[Tuple[str, ...]]:
    """Normalise a command string to an argv tuple for allowlist comparison."""
    import shlex  # noqa: PLC0415

    try:
        parts = shlex.split(cmd.strip(), posix=False)
    except ValueError:
        return None
    if not parts:
        return None
    if parts[0] in ("python3", "py"):
        parts[0] = "python"
    return tuple(p.replace("\\", "/") for p in parts)


def _tool_runner_command(description: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a TOOL-RUNNER marker against the allowlist.

    Returns ``(command, refusal_reason)``:
      ``(None, None)``   — no marker; not a tool-runner task at all.
      ``(None, reason)`` — marked as one, but the command is refused.
      ``(cmd, None)``    — cleared both gates and may run.
    """
    match = _TOOL_RUNNER_MARKER_RE.search(description or "")
    if not match:
        return None, None
    raw = match.group("cmd").strip()

    from tools.skills.invoke import _is_safe_command  # noqa: PLC0415

    # Normalise FIRST so both gates judge one string rather than two spellings
    # of it. This cannot widen anything: gate 2 is exact set membership, and
    # `python3 x` / `tools\x` are the same invocation as `python x` / `tools/x`.
    canonical = _canonical_command(raw)
    if canonical is None:
        return None, f"command could not be parsed: {raw[:200]}"
    normalised = " ".join(canonical)

    if not _is_safe_command(normalised):
        return None, ("prefix not in the shared allowlist "
                      f"(python tools/, python -m tools, python -c): {raw[:200]}")

    if canonical not in _TOOL_RUNNER_COMMANDS:
        return None, f"command not in the tool_runner allowlist: {raw[:200]}"
    return normalised, None


def _run_tool_command(cmd: str, work_dir: str) -> Tuple[bool, str]:
    """Execute one already-allowlisted command via the shared skill invoker."""
    from tools.skills.invoke import run_command  # noqa: PLC0415

    res = run_command(cmd, [], timeout=MAX_EXECUTION_SECONDS_SCAN, cwd=work_dir)
    if res.get("skipped"):
        return False, res.get("reason", "command skipped")
    if res.get("error"):
        return False, f"{cmd} errored: {res['error']}"
    rc = res.get("returncode", 1)
    out = ((res.get("stdout") or "") + (res.get("stderr") or "")).strip()
    return rc == 0, f"$ {cmd}\nexit={rc}\n{out[-3000:]}"


def _dispatch_via_tool_runner(task: dict, work_dir: str, task_log: Path) -> bool:
    """Run a deterministic task in-process. Returns True if handled.

    Two shapes qualify: an auto-decomposed phase-gate child (matched on task id)
    and a task carrying a TOOL-RUNNER marker naming an allowlisted read-only
    scan. Everything else — including a marker naming an unlisted command —
    returns False and takes the normal LLM executor chain.
    """
    task_id = task["id"]
    slug = _gate_step_slug(task_id)

    if slug:
        kind, label = "gate step", slug
    else:
        cmd, refusal = _tool_runner_command(task.get("description") or "")
        if refusal:
            logger.warning("kanban: tool_runner REFUSED %s — %s", task_id, refusal)
            print(f"  Kanban: tool_runner refused {task_id} — {refusal}")
            return False
        if not cmd:
            return False
        kind, label = "tool command", cmd

    started = time.monotonic()
    try:
        ok, detail = (_run_gate_step(slug, work_dir, task_id) if slug
                      else _run_tool_command(cmd, work_dir))
    except Exception as exc:
        ok, detail = False, f"{kind} raised: {exc}"
    elapsed = round(time.monotonic() - started, 1)

    try:
        task_log.write_text(
            f"[tool-runner dispatch — task {task_id}]\n"
            f"[work_dir {work_dir}]\n"
            f"[{kind} {label}] {'PASS' if ok else 'FAIL'} in {elapsed}s\n\n{detail}\n",
            encoding="utf-8", errors="replace", newline="",
        )
    except Exception as exc:
        logger.debug("kanban: tool-runner log write failed for %s: %s", task_id, exc)

    _set_executor_type(task_id, "tool_runner")
    print(f"  Kanban: {kind} {task_id} ({label}) "
          f"{'PASSED' if ok else 'FAILED'} in {elapsed}s via tool_runner")
    if ok:
        _move_task(task_id, "done", actor="tool_runner", reason=detail[:400])
    else:
        _move_task(task_id, "backlog", actor="tool_runner", reason=detail[:400])
    return True


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
            "SELECT id FROM kanban_tasks WHERE id LIKE %s",
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
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    "UPDATE kanban_tasks SET status = %s, updated_at = %s, "
                    "completed_at = %s, last_failure_reason = %s WHERE id = %s",
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
                # ── LESSONS LEARNED: auto-decomposed phase gate ───────────
                try:
                    from tools.workflow.lesson_learned import analyze_task, write_lesson
                    lesson = analyze_task(task_id, outcome="auto_decomposed")
                    write_lesson(lesson)
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
            except Exception as exc:
                print(f"  Kanban: failed to mark gate {task_id} done: {exc}")
        else:
            # Partial failure — leave parent as-is, dispatch as before (best-effort)
            result.append(task)

    return result


MAX_FAILURES_BEFORE_DECOMPOSITION = _MAX_FAILURES_BEFORE_DECOMPOSITION_DEFAULT


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
                "SELECT failure_count FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            prev = 0
            if row:
                prev_val = dict(row).get("failure_count")
                prev = int(prev_val) if prev_val is not None else 0
            new_count = prev + 1

            # Put the FAILURE clause first and keep the full narrative
            # elsewhere. `reason` arrives as a pipe-joined pipeline story built
            # across _run_verify_checks -> _verify_task_specific ->
            # _run_post_task_validation -> auto_remediate, and its first clause
            # is usually whatever the git-first check said. So a task that
            # PASSED the git check and then failed validation was storing a
            # string beginning "Verified (git-first): ..." in
            # last_failure_reason — and at 500 chars the real failure was often
            # truncated off the end entirely. 41% of the rows in that column
            # were success or auto-remediation text, which is why triage (and
            # the Autonomous Recovery panel, and _get_retry_coaching's
            # classify_failure) had nothing to work with.
            failure_clause, narrative = _split_failure_narrative(reason)
            conn.execute(
                "UPDATE kanban_tasks SET failure_count = %s, "
                "last_failure_reason = %s, last_failure_at = %s, "
                "last_run_summary = %s WHERE id = %s",
                (new_count, failure_clause[:500], now, narrative[:2000], task_id),
            )
            conn.commit()

            # Chain-blocker escalation: if any tasks are blocked waiting for
            # this one, escalate priority to critical so failure_triage picks
            # it up first and its dependents can be unblocked sooner.
            blocked_dep_rows = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE depends_on_task_id = %s "
                "  AND status NOT IN ('done','decomposed')",
                (task_id,),
            ).fetchall()
            blocked_dep_ids = [dict(r)["id"] for r in blocked_dep_rows]
            if blocked_dep_ids:
                conn.execute(
                    "UPDATE kanban_tasks SET priority = 'critical', updated_at = %s "
                    "WHERE id = %s",
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
                            f"Latest reason: {failure_clause[:200]}"
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

    ``reason`` stays optional in the signature so no caller has to change, but
    a blank one never reaches the table: it is replaced with a string naming
    the call site that omitted it (see tools/kanban/transition_reason.py).
    #1183 closed the reason-less call sites and blank rows kept arriving
    anyway, because the boundary — here — still accepted them.
    """
    try:
        import secrets as _secrets  # noqa: PLC0415
        # skip_frames=1 hides this function so the synthesized text names
        # _move_task and the code that called it, not this writer.
        reason = _resolve_transition_reason(
            reason, from_status=from_status, to_status=to_status,
            actor=actor, skip_frames=1,
        )
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO kanban_status_transitions "
                "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "kst-" + _secrets.token_hex(6),
                    task_id, from_status, to_status, actor, reason,
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Audit-log writes are best-effort. If the table is missing
        # (migration 025 not yet run) or the DB is locked, we do NOT
        # block the primary state transition. The alternative \u2014
        # crashing _move_task on an audit write \u2014 would be worse.
        logger.warning(
            "_record_status_transition: best-effort INSERT into kanban_status_transitions failed (non-blocking): %s",
            exc,
        )


def _parent_is_done(task_id: str) -> tuple[bool, str | None]:
    """Return (True, None) if every GATING dependency is done/decomposed.

    Used by _move_task's done-transition guard. Defense-in-depth against
    manually set status=done bypassing _get_due_tasks' dependency check
    (the E-gate orphan-done incident, 2026-04-15).

    It asks ``tools.kanban.deps`` — the same question dispatch asked — so a task
    the dispatcher released can actually be completed. Guarding on the raw
    scalar would refuse to mark FINISHED work done because its seeding
    predecessor is still open, which is not the incident this guard exists for
    and is a refusal nothing on the board can clear (kpr-fix-02).
    """
    try:
        with get_connection() as conn:
            return _parent_holds_done(task_id, conn)
    except Exception:
        return True, None  # fail-open on DB error


def _close_orphaned_rca_children(parent_task_id: str, actor: str = "scheduler") -> None:
    """When a task moves to 'done', cancel any open diag-/RCA children that
    the self_debug reflex created for it. These tasks become moot once the
    parent is resolved and must not linger in 'suggested'/'backlog' forever.

    Matches tasks whose id starts with ``diag-<parent_task_id>`` or whose
    title contains the parent task id and task_type is 'chore'/'research'
    (the two types self_debug uses for RCA cards).
    """
    try:
        with get_connection() as conn:
            now = _utcnow_iso()
            prefix = f"diag-{parent_task_id}"
            open_statuses = ("suggested", "backlog", "scheduled", "in_progress")
            placeholders = ",".join(["%s"] * len(open_statuses))
            rows = conn.execute(
                f"SELECT id FROM kanban_tasks "  # nosec B608
                f"WHERE (id LIKE %s OR (title LIKE %s AND task_type IN ('chore','research','fix'))) "
                f"  AND status IN ({placeholders})",
                (f"{prefix}%", f"%{parent_task_id}%", *open_statuses),
            ).fetchall()
            orphan_ids = [dict(r)["id"] for r in rows]
            if orphan_ids:
                ph = ",".join(["%s"] * len(orphan_ids))
                conn.execute(
                    f"UPDATE kanban_tasks SET status='done', completed_at=%s, updated_at=%s, "  # nosec B608
                    f"last_failure_reason=%s WHERE id IN ({ph})",
                    (now, now, f"auto-closed: parent {parent_task_id} resolved", *orphan_ids),
                )
                logger.info(
                    "_close_orphaned_rca_children: closed %d orphan(s) of %s: %s",
                    len(orphan_ids), parent_task_id, orphan_ids,
                )
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
            "SELECT source_prediction_id, depends_on_task_id FROM kanban_tasks WHERE id = %s",
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
                "WHERE source_prediction_id = %s AND status = 'decomposed' "
                "  AND id <> %s LIMIT 1",
                (sp, child_task_id),
            ).fetchone()
            if not parent_row:
                return None
            parent_id = dict(parent_row)["id"]

            open_count = conn.execute(
                "SELECT COUNT(*) AS n FROM kanban_tasks "
                "WHERE source_prediction_id = %s AND id <> %s "
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
                "SELECT id FROM kanban_tasks WHERE id = %s AND status = 'decomposed'",
                (parent_id,),
            ).fetchone()
            if not parent_status_row:
                return None

            open_siblings = conn.execute(
                "SELECT COUNT(*) AS n FROM kanban_tasks "
                "WHERE depends_on_task_id = %s AND id <> %s "
                "  AND status IN ('backlog', 'scheduled', 'in_progress', "
                "                 'suggested', 'needs_decomposition', 'dispatched')",
                (parent_id, child_task_id),
            ).fetchone()
            if dict(open_siblings).get("n", 0) > 0:
                return None

        now = _utcnow_iso()
        conn.execute(
            "UPDATE kanban_tasks SET status = 'done', completed_at = %s, "
            "updated_at = %s WHERE id = %s AND status = 'decomposed'",
            (now, now, parent_id),
        )
        # Audit-trail bypass row so guard-22 stays consistent: the parent
        # closure is bookkeeping, not fresh work that needs verification.
        linkage = "sp-linkage" if sp else "dep-linkage"
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(task_id, verified_at, result, reason, dispatch_source) "
            "VALUES (%s, %s, 'bypassed', %s, %s)",
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
            "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,),
        ).fetchone()
        prior_status = dict(row)["status"] if row else None

        # ── Un-complete guard (kpr-dup-09) ────────────────────────────────
        # EVERY guard below protects the transition TO done. Nothing protected
        # the transition FROM it, so the last writer won — and the last writer
        # is routinely a stale one.
        #
        # MEASURED 2026-08-19. cef-ui-03 was dispatched while `backlog`; its PR
        # merged mid-flight and pr_watcher marked it `done`; the agent then hit
        # the verification budget and the scheduler's outcome handler wrote
        # `backlog` over the completion. Each rollback cascaded a failure_count
        # reset onto cef-ci-01, and the pair flipped done<->backlog 95 times in
        # 5.5 hours. Nothing was broken in either writer: pr_watcher was right
        # that the PR merged, and the scheduler was right that its run ran out
        # of budget. They were answering different questions about the same row.
        #
        # kpr-dup-04 already established the rule — "a background sweep must not
        # un-complete work that is already on main" — and fixed it for exactly
        # one caller, `_detect_orphan_done_tasks`. This moves it to the seam
        # every caller goes through, which is where a rule about a row belongs.
        #
        # CANNOT-ANSWER REFUSES THE ROLLBACK, following kpr-dup-04: "between the
        # two ways of being wrong, rolling back is the one that destroys a
        # record." A demotion that cannot be justified is not performed.
        #
        # `manual` is the escape hatch, and terminal targets are untouched: an
        # operator re-opening a task, or moving done -> cancelled/archived, is a
        # decision this guard has no business overriding.
        if (
            prior_status == "done"
            and new_status != "done"
            and new_status in _UNCOMPLETE_GUARDED_TARGETS
            and actor not in _UNCOMPLETE_EXEMPT_ACTORS
        ):
            verdict, detail = _work_already_landed(task_id)
            if verdict is not False:
                logger.warning(
                    "_move_task: REFUSED un-complete of %s (%s -> %s by %s) — %s",
                    task_id, prior_status, new_status, actor, detail,
                )
                conn.close()
                _record_status_transition(
                    task_id, prior_status, f"REFUSED_uncomplete_{new_status}",
                    actor=actor, reason=f"guard: {detail}"[:400],
                )
                return

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
                from tools.workflow_hitl.gate import HITLGate, HITLGateUnavailable
            except ImportError:
                pass  # HITL module not installed — gate is no-op
            else:
                try:
                    pending = HITLGate().get_pending(task_id)
                except HITLGateUnavailable as exc:
                    # exa-policy-06: the gate could not read approval state. An
                    # undeterminable gate must BLOCK, not wave the task through —
                    # otherwise a DB blip is a free approval.
                    logger.error(
                        "_move_task: HITL gate UNAVAILABLE for %s — refusing done (fail-closed): %s",
                        task_id, exc,
                    )
                    conn.close()
                    _record_status_transition(
                        task_id, prior_status, "REFUSED_done_hitl_unavailable",
                        actor=actor,
                        reason=f"HITL gate unavailable (fail-closed): {exc}",
                    )
                    return
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

        # Merge-verify gate (2026-07-11 done-hardening): a task may only reach
        # 'done' when its work has actually landed on origin/main. If the task's
        # branch still carries commits not on origin, REFUSE done and leave the
        # branch preserved for merge. This is the PRIMARY, provider-independent
        # guarantee against the "board says done but not on main" failure — it
        # checks git/origin, not the worker's self-report, so it holds regardless
        # of whether the dispatch model was Claude/Kimi/Ollama or swapped mid-task
        # on credit exhaustion. Toggle off with KANBAN_REQUIRE_MERGE_FOR_DONE=0.
        if new_status == "done" and __import__("os").getenv(
            "KANBAN_REQUIRE_MERGE_FOR_DONE", "1"
        ).lower() in ("1", "true", "yes"):
            if _branch_has_unmerged_commits(task_id):
                logger.warning(
                    "_move_task: REFUSED done for %s — branch kanban/%s has "
                    "commits not on origin/%s (unmerged)",
                    task_id, task_id, _default_branch(),
                )
                conn.close()
                _record_status_transition(
                    task_id, prior_status, "REFUSED_done_unmerged",
                    actor=actor,
                    reason=f"guard: kanban/{task_id} has commits not on origin/{_default_branch()}",
                )
                # Lessons-Learned (kph): a stranded/unmerged done attempt is a
                # SYSTEMIC pipeline signal — record it so recurrence detection +
                # remediation cards fire (classified as UNMERGED_STRANDED).
                try:
                    from tools.workflow.lesson_learned import analyze_task, write_lesson
                    write_lesson(analyze_task(task_id, outcome="refused_done_unmerged"))
                except Exception as _ll_exc:  # noqa: BLE001
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
                return

        # Delivery-evidence gate (kpr-rvfy-04): the POSITIVE half of the gate
        # above. `_branch_has_unmerged_commits` asks whether this task's branch
        # holds work that has not landed — so a task nothing ever built, whose
        # branch does not exist, satisfies it by having no unmerged work. That is
        # how ftp-prd-11 went from seeded to `done` in six minutes with no PR, no
        # commit and no dispatch. A negative check cannot establish that
        # something happened; this one asks for the positive.
        #
        # Refuses only on a MEASURED absence, and only for an automatic actor.
        # See done_delivery_refusal for the fail-open reasoning and the env
        # toggle.
        if new_status == "done":
            _delivery_reason = done_delivery_refusal(task_id, actor=actor)
            if _delivery_reason:
                logger.warning(
                    "_move_task: REFUSED done for %s (%s) — %s",
                    task_id, actor, _delivery_reason,
                )
                conn.close()
                _record_status_transition(
                    task_id, prior_status, "REFUSED_done_no_delivery_evidence",
                    actor=actor, reason=f"guard: {_delivery_reason}"[:400],
                )
                return

        now = _utcnow_iso()
        sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
        vals = [new_status, now]
        if new_status == "done":
            sql += ", completed_at = ?"
            vals.append(now)
            if completed_via_bypass:
                sql += ", completed_via_bypass = 1"
            # Record observed wall-clock runtime so _detect_execution_anomalies
            # has a sample to work from. Without this the adaptive-timeout
            # ceiling has nothing to read and every task silently falls back to
            # the static constant. Only for tasks this process dispatched —
            # _dispatch_times is in-memory, so a manual/CLI completion or a
            # post-restart completion simply leaves the column NULL, which the
            # anomaly query already filters out.
            _started = _dispatch_times.get(task_id)
            if _started is not None:
                _elapsed = (datetime.now(timezone.utc) - _started).total_seconds()
                if 0 < _elapsed < _ABSOLUTE_MAX_IN_PROGRESS_SECONDS:
                    sql += ", execution_seconds = ?"
                    vals.append(round(_elapsed, 1))
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
                "WHERE depends_on_task_id = %s "
                "  AND status IN ('in_progress', 'backlog', 'scheduled')",
                (task_id,),
            ).fetchall()
            for r in desc_rows:
                rolled_back.append(dict(r)["id"])
            if rolled_back:
                placeholders = ",".join(["%s"] * len(rolled_back))
                conn.execute(
                    "UPDATE kanban_tasks SET status='backlog', "
                    "scheduled_at=NULL, "
                    "updated_at=%s, failure_count=0, "
                    "last_failure_reason=%s, last_failure_at=NULL "
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

    # Release this session's per-task coordination lease once the task leaves the
    # active state (terminal or re-queued). release() is ownership-aware — it only
    # frees a lease THIS session holds, so it never disturbs a lease held by an
    # interactive CLI session working the task out-of-band. Best-effort.
    if new_status in ("done", "failed", "token_exhausted", "backlog", "suggested", "decomposed"):
        try:
            from tools.coordination import leases as _leases
            _leases.release(f"kanban:task:{task_id}")
        except Exception:
            pass

    # Fire webhook subscriptions on terminal transitions
    _SUBSCRIPTION_EVENTS = {"done", "token_exhausted", "decomposed"}
    if new_status in _SUBSCRIPTION_EVENTS:
        try:
            _fire_task_subscriptions(task_id, new_status)
        except Exception as _sub_exc:
            logger.debug("subscription fire failed for %s: %s", task_id, _sub_exc)
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

    # Harness co-learning: record task outcomes so eval_harness can compute
    # precision/recall and fire degradation cards when metrics slip.
    if new_status in ("done", "token_exhausted", "failed") and prior_status not in ("done", "token_exhausted", "failed"):
        try:
            from tools.genesis.harness.eval_harness import record_outcome
            actual = "resolved" if new_status == "done" else "failed"
            record_outcome(task_id, actual)
        except Exception as _ho_exc:
            logger.debug("harness record_outcome skipped for %s: %s", task_id, _ho_exc)


def _orphan_rows() -> list[dict]:
    """Candidate orphans: `done` tasks whose dependency parent is not done.

    Split out of :func:`_detect_orphan_done_tasks` so the landed check that now
    filters them can be tested without a database.
    """
    try:
        conn = get_connection()
        try:
            # Scalar dep orphans: done task whose depends_on_task_id parent isn't done
            scalar_rows = conn.execute(
                "SELECT t.id AS id, t.depends_on_task_id AS parent_id, "
                "       p.status AS parent_status, p.title AS parent_title "
                "FROM kanban_tasks t "
                "JOIN kanban_tasks p ON p.id = t.depends_on_task_id "
                "WHERE t.status = 'done' AND p.status NOT IN ('done', 'decomposed') "
                # A manual gate never completes — that is the whole point of it.
                # Its done dependents are finished work, not orphans.
                "  AND p.id NOT LIKE '%%-gate-00' "
                "  AND COALESCE(p.title, '') NOT LIKE '%%MANUAL-MODE GATE%%'"
            ).fetchall()
            # Junction dep orphans: done task with at least one unfinished junction parent
            junction_rows = conn.execute(
                "SELECT DISTINCT t.id AS id, d.depends_on_id AS parent_id, "
                "       p.status AS parent_status, p.title AS parent_title "
                "FROM kanban_tasks t "
                "JOIN kanban_task_deps d ON d.task_id = t.id "
                "JOIN kanban_tasks p ON p.id = d.depends_on_id "
                "WHERE t.status = 'done' AND p.status NOT IN ('done', 'decomposed') "
                "  AND p.id NOT LIKE '%%-gate-00' "
                "  AND COALESCE(p.title, '') NOT LIKE '%%MANUAL-MODE GATE%%'"
            ).fetchall()
            # A scalar parent the junction graph SUPERSEDED never gated this task,
            # so finishing ahead of it is not evidence the work jumped a
            # prerequisite — it is the parallelism the graph declared. Dropping
            # these matters because this sweep ROLLS A DONE TASK BACK TO BACKLOG,
            # and a rolled-back task is re-dispatched into a PR that can only land
            # as a revert (kpr-fix-02, and the #1651/#1784 class before it).
            scalar_rows = [
                r for r in scalar_rows
                if not _scalar_is_superseded(
                    dict(r).get("parent_id"),
                    _junction_dep_ids(dict(r)["id"], conn),
                    scalar_is_gate=_is_manual_gate(
                        dict(r).get("parent_id"), dict(r).get("parent_title")
                    ),
                )
            ]
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
    return orphans


def _landed_reports(task_ids) -> dict:
    """``{task_id: landed-check report}`` \u2014 one ``git log --grep`` for the batch.

    Same call and same batching as ``pr_watcher._landed_map``, so "is this task
    already on the default branch" has one implementation and the sweep cannot
    drift from the dispatch gate.

    Returns {} on any failure, which the caller reads as "could not verify".
    """
    ids = [str(t) for t in task_ids if str(t or "").strip()]
    if not ids:
        return {}
    try:
        from tools.kanban import landed_check

        return landed_check.check_landed_bulk(ids)
    except Exception as exc:  # noqa: BLE001 \u2014 must never break the scheduler
        logger.warning("orphan-done sweep: landed check failed: %s", exc)
        return {}


#: Statuses that put a task back into the working queue. Moving a DONE task to
#: one of these is an un-completion; moving it to `cancelled`/`archived` is an
#: operator decision and is deliberately not guarded.
_UNCOMPLETE_GUARDED_TARGETS = frozenset({
    "backlog", "scheduled", "in_progress", "token_exhausted",
    "ci_failed", "merge_conflict", "changes_requested", "failed",
})

#: A human moving a task by hand is making a decision, not losing a race.
_UNCOMPLETE_EXEMPT_ACTORS = frozenset({"manual", "cli", "operator"})

#: Actors whose ``done`` this gate does not judge, and there are only two kinds.
#:
#: A ``cli``/``manual``/``operator`` completion is a human DECISION, already
#: carried through the CLI's own merge-verify refusal and its audited
#: ``--force-done`` escape hatch. This gate exists for the AUTOMATIC path, which
#: has no human behind it.
#:
#: ``pre_dispatch_resolver`` is the one automatic path whose completion does not
#: CLAIM a delivery: :func:`_pre_dispatch_check` re-derives that the gap is
#: already resolved and closes the card without dispatching, so it has no branch
#: by construction. It carries its own evidence, and it declares itself rather
#: than completing under ``scheduler``'s name.
#:
#: Do NOT add a fourth entry to quieten a fire. Every other refusal has positive
#: evidence available to it — see :func:`done_delivery_refusal`.
_DELIVERY_EVIDENCE_EXEMPT_ACTORS = frozenset({
    "manual", "cli", "operator", "pre_dispatch_resolver",
})

#: ``1``/``true`` (default) refuse; anything else stands the gate down. Named
#: rather than shell-neutralised, on the same terms as
#: ``KANBAN_REQUIRE_MERGE_FOR_DONE``: an operator standing a control down must
#: leave a record a reader can find.
_DELIVERY_EVIDENCE_ENV = "KANBAN_REQUIRE_DELIVERY_EVIDENCE"


def _has_dispatch_record(task_id: str) -> Optional[bool]:
    """``True`` | ``False`` | ``None`` — did anything ever dispatch this task?

    Two independent witnesses, because they fail separately: a transition INTO
    ``in_progress`` in ``kanban_status_transitions`` (written by ``_move_task``
    itself, so it survives a scheduler restart) and a ``kanban_executions`` row
    (written by ``_open_execution`` on every executor tier). Either is enough.

    ``None`` when the board could not be read. NEVER ``False`` on an error: this
    feeds a refusal, and "the database was briefly unreachable" must not read as
    "nothing ever built this task".
    """
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001 — unreadable board is unmeasurable
        logger.debug("dispatch-record probe: no connection for %s: %s", task_id, exc)
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM kanban_status_transitions "
            "WHERE task_id = %s AND to_status = 'in_progress' LIMIT 1",
            (task_id,),
        ).fetchone()
        if row:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch-record probe: transitions unreadable for %s: %s",
                     task_id, exc)
        conn.close()
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM kanban_executions WHERE task_id = %s LIMIT 1",
            (task_id,),
        ).fetchone()
        return bool(row)
    except Exception as exc:  # noqa: BLE001 — one witness answered, the other
        # did not. That is not proof of absence.
        logger.debug("dispatch-record probe: executions unreadable for %s: %s",
                     task_id, exc)
        return None
    finally:
        conn.close()


def _children_all_done(task_id: str) -> Optional[bool]:
    """``True`` when this task has children and every one of them is ``done``.

    ``None`` when it has no children, or the board could not be read. A parent's
    delivery evidence IS its children's: ``auto_close_parent`` in
    ``tools/kanban/state_machine.py`` completes a gate sentinel that was never
    dispatched and has no branch, and it is right to. 13 of the 32 fires in the
    survey below were exactly that.
    """
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        logger.debug("children probe: no connection for %s: %s", task_id, exc)
        return None
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE depends_on_task_id = %s",
            (task_id,),
        ).fetchone()[0]
        if not total:
            return None
        undone = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks "
            "WHERE depends_on_task_id = %s AND status != 'done'",
            (task_id,),
        ).fetchone()[0]
        return undone == 0
    except Exception as exc:  # noqa: BLE001 — unreadable is not "no children"
        logger.debug("children probe failed for %s: %s", task_id, exc)
        return None
    finally:
        conn.close()


def done_delivery_refusal(task_id: str, actor: str = "scheduler",
                          dispatched: Optional[bool] = None) -> str:
    """Why an AUTOMATIC move to ``done`` must be refused, or ``""`` to allow it.

    THE HOLE (kpr-rvfy-04). The merge-verify gate beside this one asks
    ``_branch_has_unmerged_commits`` — "does this task's branch hold work that
    has NOT landed" — so it is satisfied by unmerged work being ABSENT. A task
    nothing ever built, whose branch does not exist, passes it trivially. A
    NEGATIVE check cannot establish that anything happened. This is the positive
    half: **something must have built this.**

    Extracted as a function rather than written inline for the reason
    :func:`timeout_demotion_skip_reason` states: a rule that can only be
    exercised by driving the whole scheduler loop is a rule nobody checks.

    SURVEYED BEFORE ARMING, and the survey is what shaped it. Population: the
    904 tasks whose latest ``-> done`` transition was written by an actor that
    reaches this function (``scheduler``, ``pr_watcher``, ``tool_runner``,
    ``startup_backfill``). The dashboard is NOT in it — ``tools/dashboard/api/
    kanban.py`` has its own move path and never calls ``_move_task`` — which is
    why the first measurement, taken over every done task, read a meaningless
    17.63%.

    The bare rule "no dispatch record and no branch" fires **29 times, 3.21%**,
    twice the 1.63% this repo already calls refusing routine work. EVERY ONE OF
    THE 29 WAS A LEGITIMATE COMPLETION, in three kinds, so each is answered with
    POSITIVE evidence rather than an exemption list:

      * parent auto-closes ("auto-closed: all N child tasks done", from
        ``state_machine.auto_close_parent``). A gate sentinel is never
        dispatched and has no branch; its delivery evidence is its children's —
        :func:`_children_all_done`, which narrows 12 of the 29.
      * :func:`_pre_dispatch_check` auto-resolving a false-positive gap
        (``tool_not_in_manifest`` on ``cdh-gap-*``). Nothing was built because
        there was nothing to build, and that path now declares itself through
        its own actor instead of wearing ``scheduler``'s.
      * a ``pr_watcher`` completion whose PR merged and whose branch was then
        deleted, so the work is ON MAIN — which :func:`_work_already_landed`
        already knows how to see, and sees through the merge_ref/subject tiers
        only.

    AFTER NARROWING: 17 fires, 1.88%, and **every one of them is dated
    2026-06-14 to 2026-06-30** — the June-era auto-resolve path, which wrote no
    reason and so cannot be told apart in replay from the actor that now labels
    it. Over the population the gate will actually meet, it fires ZERO times:
    0.00% over the last 30 days (410 completions) and 0.00% over the last 60
    (451). That is what supports shipping it armed.

    Re-derive with ``python -m tools.kanban.artifact_evidence --survey``. Do NOT
    respond to a future fire by adding a fourth exempt actor: find the positive
    evidence that completion rests on, or the completion has no evidence.

    FAIL-OPEN, deliberately and at every step. Only a MEASURED absence refuses:
    ``delivery_evidence`` returns ``has_evidence=False`` solely when the dispatch
    record, the branch listing and the commit compare were ALL read and all three
    came back negative. Anything unreadable is ``None`` and allows the
    completion, because an unreachable git or a briefly-down board must never
    wedge every task on the board.
    """
    if (actor or "") in _DELIVERY_EVIDENCE_EXEMPT_ACTORS:
        return ""
    import os as _os
    if _os.getenv(_DELIVERY_EVIDENCE_ENV, "1").strip().lower() not in ("1", "true", "yes"):
        return ""
    try:
        from tools.kanban.artifact_evidence import delivery_evidence
        if dispatched is None:
            dispatched = _has_dispatch_record(task_id)
        evidence = delivery_evidence(
            task_id,
            repo_root=_task_repo_root(task_id),
            base_branch=_task_base_branch(task_id),
            dispatched=dispatched,
        )
    except Exception as exc:  # noqa: BLE001 — an unanswerable check allows
        logger.debug("delivery-evidence gate errored for %s (fail-open): %s",
                     task_id, exc)
        return ""
    if evidence.get("has_evidence") is not False:
        return ""

    # The three narrowings, cheapest first. Each is POSITIVE evidence of a real
    # completion, not a licence granted by name.
    try:
        if _children_all_done(task_id) is True:
            return ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("children narrowing failed for %s (fail-open): %s", task_id, exc)
        return ""
    try:
        # `_work_already_landed` returns True | None | False; only a firm False
        # means the work is genuinely not on the default branch. Reusing it
        # rather than re-deriving keeps ONE statement of the merge_ref/subject
        # tiering — and file content is never in it (landed_check's
        # NON_LANDING_CONFIDENCE), so a forward reference in a comment cannot
        # complete a task through this door either.
        landed, _detail = _work_already_landed(task_id)
        if landed is not False:
            return ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("landed narrowing failed for %s (fail-open): %s", task_id, exc)
        return ""

    return (
        "no delivery evidence: nothing dispatched this task, no branch carries "
        "its id, nothing to merge, no completed children, and its work is not "
        f"on the default branch — {evidence.get('reason') or 'measured absent'}"
    )


def _work_already_landed(task_id: str):
    """``(True|None|False, detail)`` — is this task's work on the default branch?

    ``True``  the id is on the branch (merge_ref or subject evidence).
    ``None``  the check could not answer — treated the SAME as True by the
              un-complete guard, because a demotion that cannot be justified
              destroys a record, which is the worse of the two errors
              (kpr-dup-04's rule, applied at the seam).
    ``False`` the work is genuinely not there; a rollback is legitimate.

    Never raises: an exception is an unanswerable check, not a licence to
    un-complete.
    """
    try:
        report = (_landed_reports([task_id]) or {}).get(task_id) or {}
    except Exception as exc:  # noqa: BLE001 — must never break a status write
        return None, f"landed check raised ({exc}) — refusing to un-complete"
    if not report.get("checked"):
        return None, (
            "landed check could not verify whether the work is on the default "
            f"branch ({report.get('reason') or 'no reason given'}) — refusing "
            "to un-complete"
        )
    if report.get("landed"):
        return True, (
            "its work is already on the default branch "
            f"({report.get('confidence') or 'unknown'} evidence)"
        )
    return False, "work is not on the default branch"


def _detect_orphan_done_tasks() -> list[dict]:
    """Roll back a `done` task whose prerequisite work never happened.

    Runs at the start of each scheduler cycle. Catches the class of bug where a
    row was SET to done without its prerequisite completing (E-gate incident
    2026-04-15: E-gate done while E4/E5/E6 were not).

    IT CANNOT TELL THAT APART FROM WORK THAT GENUINELY LANDED, and until
    kpr-dup-04 it did not try. MEASURED on kanban_status_transitions: 80
    firings, **100% of them done->backlog**, across 61 distinct tasks \u2014 the
    sweep has never reset anything else. Running `check_landed_bulk` over those
    61 says 20 were ALREADY ON MAIN, with `merge_ref` or `subject` evidence, so
    roughly a third of everything it ever did was un-completing merged work.

    That is not a cosmetic error. A rolled-back task is dispatchable again, a
    second session re-implements it, and the PR that opens can only merge as a
    REVERT \u2014 #1651 was -38/+26 on rest_v1.py, and #1784 (2026-08-17) was -10,615
    lines across 73 files, deleting 30 files main currently had.

    So the rollback now asks the oracle this repo already built and already
    consults at seed time and at dispatch time. Landed work is not an orphan:
    the DEPENDENCY GRAPH is what is wrong there, and that gets reported instead.
    The other 41 are still caught \u2014 this narrows the check, it does not disarm
    it.

    When the landed check cannot answer, the task is LEFT ALONE and the failure
    is logged. A sweep that could not verify is not a sweep that found an orphan
    (the same rule `red_first_gate` encodes as exit 2), and between the two ways
    of being wrong, rolling back is the one that destroys a record.

    Returns ``{id, parent_id, parent_status}`` for every row actually rolled
    back. The rollback goes through ``_move_task`` so the audit trail captures
    the orphan-sweep actor.
    """
    candidates = _orphan_rows()
    if not candidates:
        return []

    reports = _landed_reports([o["id"] for o in candidates])

    rolled: list[dict] = []
    for o in candidates:
        report = reports.get(o["id"]) or {}
        if not report.get("checked"):
            logger.warning(
                "ORPHAN-DONE: %s has parent %s in %r, but the landed check "
                "could not verify whether its work is on main \u2014 leaving it "
                "alone. A sweep that could not verify has not found an orphan.",
                o["id"], o["parent_id"], o["parent_status"],
            )
            continue
        if report.get("landed"):
            # The work IS on main. The task is finished; what is wrong is the
            # dependency graph that still calls its parent unfinished.
            logger.warning(
                "ORPHAN-DONE: %s has parent %s in %r, but its work has already "
                "LANDED on the default branch (%s evidence) \u2014 not rolling "
                "back. The dependency declaration is what is stale here, not "
                "the task.",
                o["id"], o["parent_id"], o["parent_status"],
                report.get("confidence") or "unknown",
            )
            continue
        logger.warning(
            "ORPHAN-DONE detected: %s was done but parent %s is %r, and its "
            "work is NOT on the default branch \u2014 rolling back to backlog",
            o["id"], o["parent_id"], o["parent_status"],
        )
        # Roll back to backlog (not scheduled) \u2014 parent is not done so this
        # task must wait for the dependency chain to complete before re-scheduling.
        _move_task(
            o["id"], "backlog",
            actor="orphan_sweep",
            reason=f"parent {o['parent_id']} status={o['parent_status']!r} at sweep",
        )
        rolled.append(o)

    return rolled


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

    # Show the agent the criteria it will be GRADED on. `review_conformance` (via
    # make_pipeline_grader) judges the finished work against acceptance_criteria, but the
    # column never reached the prompt — the agent was marked down against a spec it was
    # never given. (`_check_acceptance_criteria` was the second judge named here until
    # wire-req-01 deleted it: it was never called from anywhere, and it returned True on
    # no-criteria, DB error, judge-unavailable and judge-exception, so wiring it would have
    # added a rung that cannot fail.)
    criteria = (task.get("acceptance_criteria") or "").strip()
    criteria_section = (
        f"\n## Acceptance Criteria\nYou will be graded against these. "
        f"Satisfy every one.\n{criteria}\n"
        if criteria else ""
    )

    # task -> main, not task -> PR (trust-disc-05). The session about to build
    # this is the one that would re-implement already-merged work, so the
    # evidence goes where it will actually be read — at the top of its prompt,
    # above the description that tells it to build.
    landed_section = ""
    try:
        from tools.kanban.landed_check import format_warning as _fmt

        _report = _landed_preflight(task_id)
        _warning = _fmt(_report)
        if _warning:
            landed_section = (
                "\n> [!WARNING]\n> ## Check this before you build\n"
                + "\n".join(f"> {ln}" for ln in _warning.splitlines())
                + "\n>\n> If the work below is already on the default branch, do NOT "
                  "re-apply it — say so and close the task out instead. Re-applying a "
                  "diff whose base has moved on deletes lines main currently has.\n\n"
            )
    except Exception as _lc_exc:  # noqa: BLE001 — a banner must never block dispatch
        logger.debug("landed banner skipped for %s: %s", task_id, _lc_exc)

    prompt = f"""{resume_section}{landed_section}# Kanban Task: {title}
- **ID:** {task_id}
- **Type:** {task_type}
- **Priority:** {priority}
- **Scheduled:** {task.get("scheduled_at", "now")}

## Description
{desc}
{criteria_section}
## Instructions
Execute this task as described above. When complete:
1. POST to http://localhost:5050/api/kanban/tasks/{task_id}/move
   with {{"status": "done"}} to mark it complete on the board.
"""

    prompt_path = PROMPT_DIR / f"{task_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8", newline="")
    return str(prompt_path)


def _run_adversarial_verify(task_id: str, work_dir: str) -> tuple:
    """Adversarial verifier gate — spawn a short Claude CLI review of completed work.

    Only fires when adversarial_enabled=1 on the task (loop_type='non_deterministic').
    Uses a separate Claude CLI session with a review-only prompt and --max-turns 10
    so the verifier cannot make changes, only judge.

    Returns (passed: bool, feedback: str). Fails open on any error so the
    adversarial gate never permanently blocks a task.
    """
    try:
        with get_connection() as _c:
            row = _c.execute(
                "SELECT adversarial_enabled, title, description "
                "FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
        if not row or not dict(row).get("adversarial_enabled"):
            return True, ""
        task_title = dict(row).get("title", task_id)
        task_desc = (dict(row).get("description") or "")[:1200]
    except Exception as exc:
        logger.warning("adversarial_verify: DB read failed for %s: %s", task_id, exc)
        return True, ""

    claude_cli = _resolve_claude_cli()
    if not claude_cli:
        logger.warning("adversarial_verify: claude CLI not found — skipping for %s", task_id)
        return True, ""

    review_prompt = (
        "You are an adversarial code reviewer. "
        "Review the changes made in this worktree for the task below.\n\n"
        f"Task: {task_title}\n\n"
        f"Acceptance criteria:\n{task_desc}\n\n"
        "Run `git diff main...HEAD` to inspect what changed. "
        "Check new/modified files against the acceptance criteria.\n\n"
        "Respond with EXACTLY one of these two formats and nothing else:\n"
        "APPROVED: <one sentence reason>\n"
        "REJECTED: <specific actionable feedback on what is missing or wrong>\n\n"
        "Be strict. Missing tests, unfulfilled criteria, or obvious bugs = REJECTED."
    )

    try:
        # Same adapter as the build path, so the review session inherits the
        # argv, the stdin temp-file and the PATHEXT-aware discovery from the
        # one place that knows how to invoke the CLI. Deliberately NOT tagged
        # with a dispatch_source: a review makes no commits to attribute.
        from tools.agents.adapter_base import AgentSession  # noqa: PLC0415
        from tools.agents.adapters.claude_cli import ADAPTER as _claude_adapter  # noqa: PLC0415

        result = _claude_adapter.invoke(AgentSession(
            task_id=task_id,
            prompt=review_prompt,
            working_dir=work_dir,
            # --max-turns 10 is the review-only budget: enough to read the diff
            # and judge, not enough to rewrite the work it is judging.
            max_turns=10,
            timeout_seconds=180,
            metadata={"temp_dir": str(BASE_DIR / ".tmp")},
        ))

        output = (result.output or "").strip()
        if not output:
            logger.warning("adversarial_verify: empty output for %s — passing", task_id)
            return True, ""

        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.upper().startswith("APPROVED"):
                feedback = line[len("APPROVED"):].lstrip(": ").strip()
                logger.info("adversarial_verify: APPROVED %s — %s", task_id, feedback[:80])
                return True, feedback
            if line.upper().startswith("REJECTED"):
                feedback = line[len("REJECTED"):].lstrip(": ").strip()
                logger.warning(
                    "adversarial_verify: REJECTED %s — %s", task_id, feedback[:200]
                )
                return False, feedback

        logger.warning("adversarial_verify: no verdict found for %s — passing", task_id)
        return True, ""

    except subprocess.TimeoutExpired:
        logger.warning("adversarial_verify: timeout for %s — passing", task_id)
        return True, ""
    except Exception as exc:
        logger.warning("adversarial_verify: error for %s: %s", task_id, exc)
        return True, ""


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
                "WHERE task_id = %s ORDER BY verified_at DESC LIMIT 1",
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
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO kanban_alert_queue "
                    "(task_id, event, severity, title, body, reason, actor, created_at, retry_count) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                "VALUES (%s, %s, %s, %s, %s, %s)",
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


def _poll_all_channels():
    """Poll all configured channels (Telegram + Teams + MatterMost + GitHub + GitLab + Skype)."""
    results = list(_poll_telegram())

    _channel_listeners = [
        ("tools.notifications.adapters.teams_listener", "Teams"),
        ("tools.notifications.adapters.mattermost_listener", "MatterMost"),
        ("tools.notifications.adapters.github_listener", "GitHub"),
        ("tools.notifications.adapters.gitlab_listener", "GitLab"),
        ("tools.notifications.adapters.skype_listener", "Skype"),
    ]
    for module_path, name in _channel_listeners:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            channel_results = mod.poll_updates()
            if channel_results:
                results.extend(channel_results)
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Task executor — Claude Code CLI when available, LLMRouter otherwise (OPT-31)
# ---------------------------------------------------------------------------
# Lazy resolution: don't pin the path at import time so a Claude install that
# arrives mid-session is picked up automatically. The default home-dir fallback
# is preserved for systems where Claude is installed but not on PATH.
def _resolve_claude_cli() -> Optional[str]:
    """Absolute path to the ``claude`` CLI, or None.

    Delegates to ``tools.agents.adapters.claude_cli.resolve_claude_cli`` — the
    resolution rules live beside the shellout that uses them, so there is one
    answer to "where is the CLI" rather than one per call site.

    The rules themselves are load-bearing history: the fallback used to test
    ``~/.local/bin/claude`` with no extension, which never exists on Windows —
    the installed binary is ``claude.EXE``. So on Windows resolution depended
    entirely on ``shutil.which``, i.e. on PATH, and a process started with a
    thinner environment (a dashboard-spawned scheduler, a service) silently
    found nothing. ``_claude_code_available()`` then returned False, the
    executor chain fell through gitlab and ollama, and every task dispatched in
    that window was quarantined to ``suggested`` with "no executor available" —
    25 tasks on 2026-08-01 before it was traced.
    """
    try:
        from tools.agents.adapters.claude_cli import resolve_claude_cli
    except Exception as exc:  # noqa: BLE001 — a broken adapter must be loud
        logger.error(
            "kanban: claude_cli adapter unimportable (%s) — the runner cannot "
            "resolve its executor", exc,
        )
        return None
    return resolve_claude_cli()


def _claude_code_available() -> bool:
    """True if the `claude` CLI is invokable on this host."""
    return _resolve_claude_cli() is not None


# Executor-chain tiers that are served by an AgentAdapter, mapped to the
# adapter name in tools/agents/registry.py. Tiers absent from this map
# (gitlab, github_actions, ollama_local) are dispatched by their own helpers —
# they are CI/queue backends, not agent sessions.
_ADAPTER_TIERS: Dict[str, str] = {
    "claude_cli": "claude_cli",
    "local_agent": "local_agent",
}


def _agent_adapter_override() -> str:
    """The operator's forced adapter, if any. Read fresh — it is a live switch."""
    import os as _os  # noqa: PLC0415

    return _os.environ.get("ICDEV_AGENT_ADAPTER", "").strip()


def _pick_chain_adapter(chain: list, task_type: Optional[str] = None):
    """Resolve the AgentAdapter serving the adapter-backed tiers of ``chain``.

    Selection goes through ``tools.agents.registry.pick_default`` so
    ``ICDEV_AGENT_ADAPTER`` overrides the chain — that env var is the only
    supported way to force the owned executor without editing config.

    The EXECUTOR CHAIN, not ``args/agent_adapters.yaml``'s per-task-type table,
    decides the order: the config handed to ``pick_default`` sets
    ``per_task_type_preference`` empty and derives ``fallback_order`` from the
    chain. That keeps default resolution byte-unchanged — with the CLI present,
    claude_cli is still picked for every task type, including the ``chore``
    tasks the adapter config would otherwise route to ``local_llm_router``.

    Returns None when no adapter is available; the caller then walks on to the
    non-adapter tiers exactly as before.
    """
    names = [_ADAPTER_TIERS[t] for t in chain if t in _ADAPTER_TIERS]
    if not names:
        return None
    try:
        from tools.agents import registry as _agent_registry  # noqa: PLC0415

        return _agent_registry.pick_default(
            task_type,
            config={
                "enabled_adapters": names,
                "per_task_type_preference": {},
                "fallback_order": names,
            },
        )
    except Exception as exc:  # noqa: BLE001 — falling through the chain is the fallback
        if _agent_adapter_override():
            # A typo'd override that silently changed nothing is exactly the
            # "control that looks like it worked" failure this codebase keeps
            # producing — say so at WARNING, not at INFO.
            logger.warning(
                "kanban: ICDEV_AGENT_ADAPTER=%r could not be resolved (%s) — "
                "the executor chain is running WITHOUT your override",
                _agent_adapter_override(), exc,
            )
        else:
            logger.info(
                "kanban: no agent adapter available for tiers %s: %s", names, exc,
            )
        return None


# Track running task handles. Claude path stores subprocess.Popen, LLMRouter
# path stores _LLMTaskHandle — both expose .poll() / .kill() / .wait() / .pid /
# .returncode so the rest of the reflex (timeout sweeper, completion checker)
# can treat them uniformly.
_running: Dict[str, Any] = {}

# Open runtime_invocations handles for dispatched agents, keyed by task id.
# Separate from _running because the invocation outlives its entry there: the
# completion path deletes from _running while still needing to close the row.
_agent_invocations: Dict[str, Any] = {}

# Imported defensively — the reflex must still dispatch on a tree where the
# observability package is unavailable (partial checkout, older wheel).
try:
    from tools.observability.invocation_recorder import SURFACE_AGENT as _SURFACE_AGENT
    from tools.observability.invocation_recorder import (
        close_invocation as _close_agent_invocation,
    )
    from tools.observability.invocation_recorder import (
        open_invocation as _open_agent_invocation,
    )
except Exception:  # noqa: BLE001
    _SURFACE_AGENT = "agent"

    def _open_agent_invocation(*_a, **_kw):  # type: ignore[misc]
        return None

    def _close_agent_invocation(*_a, **_kw):  # type: ignore[misc]
        return None


def _audit_agent_execution(event_type: str, task_id: str, **details) -> None:
    """Write one agent_execution_* row to the audit trail. Never raises.

    VALID_EVENT_TYPES declared four agent_execution_* types and nothing in the
    tree wrote any of them; the single agent_execution_completed row on the
    board was hand-written in June 2026. So the CHECK constraint advertised a
    lifecycle the code could not produce, and querying the schema read as
    coverage.

    They are wired to the same choke points as the runtime_invocations handle
    above, and for the same reason: this subprocess IS the agent execution.
    Attaching them to tools/agent/agent_executor.py::execute_agent would look
    tidier and observe nothing, which is the mistake #1196 made and #1304
    corrected.

    runtime_invocations already records duration and status, so this is not a
    duplicate for its own sake --- the audit trail is the append-only NIST AU
    record with a retention guarantee and a hash chain, and runtime_invocations
    is operational telemetry. An auditor asking "when did agents run and which
    failed" has to be able to answer it from audit_trail alone.

    Dispatch must survive a broken audit path, so every failure here is
    swallowed: an agent that cannot build because the audit table is locked
    would be a far worse outcome than a missing row.
    """
    try:
        from tools.audit.audit_logger import log_event

        log_event(
            event_type=event_type,
            actor="kanban-runner",
            action=f"{event_type} for {task_id}",
            details={"task_id": task_id, **details},
            classification="CUI",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent execution audit failed for %s: %s", task_id, exc)


# Semaphore counter for EXEC_OLLAMA_LOCAL concurrent dispatch limit.
_ollama_running_count: int = 0

# Task IDs dispatched synchronously via Ollama (already completed when added).
# The reflex run() checks this to mark them done immediately rather than
# routing through the in_progress polling loop.
_ollama_completed: set = set()

# Tasks dispatched to GitHub Actions (async, no local Popen). run() checks
# this to move them to in_progress without the _running dict.
_github_actions_dispatched: set = set()

# D-AUTO-DEGRADE: Track executors that have hit rate/token limits.
# When degraded, the scheduler skips them in the fallback chain.
_degraded_executors: set = set()
_degraded_executors_probed_at: Dict[str, datetime] = {}

# kax-exec-03: tiers that have actually dispatched at least once in this
# process. Membership in the configured executor chain says only that an
# operator listed a tier; it is not evidence the tier works on this host. Used
# by _build_effective_executor_chain so that degrading the one tier that has
# ever worked cannot leave a "non-empty" chain of tiers that never have.
# Deliberately in-memory and per-process, matching _degraded_executors: a fresh
# scheduler starts with no evidence and behaves exactly as before.
_tiers_ever_dispatched: set = set()
_DEGRADATION_PROBE_INTERVAL = timedelta(minutes=5)  # Default if no reset hint parsed


def _selected_model() -> Optional[dict]:
    """The model the operator picked for the runner, resolved. None => config routing.

    Never raises: an unreadable override degrades to the default rather than stopping
    every build.
    """
    try:
        from tools.kanban.model_override import spec

        return spec()
    except Exception as exc:  # noqa: BLE001
        logger.debug("model-override check failed (using config routing): %s", exc)
        return None


def _build_effective_executor_chain(original_chain: list) -> list:
    """Return executor chain with degraded executors moved to the end (or removed).

    If all executors are degraded, returns the original chain anyway so the
    scheduler can attempt them as a last resort.

    MODEL OVERRIDE: if the operator selected a model the Claude Code CLI cannot serve
    (Kimi, Ollama, GPT...), claude_cli is REMOVED from the chain. Leaving it in would
    mean the runner keeps building with Claude while the dropdown says otherwise — a
    control that looks like it worked and did nothing, which is worse than no control.
    Selecting a model means building with it.
    """
    model = _selected_model()
    if model and not model.get("cli_capable"):
        dropped = [t for t in original_chain if t == "claude_cli"]
        if dropped:
            logger.info(
                "kanban: model %r (provider %s) cannot be served by the Claude CLI — "
                "dropping claude_cli from the executor chain for this dispatch.",
                model["name"], model.get("provider"),
            )
        original_chain = [t for t in original_chain if t != "claude_cli"]

    degraded = [tier for tier in original_chain if tier in _degraded_executors]
    active = [tier for tier in original_chain if tier not in _degraded_executors]

    # Check if any degraded executor is past its resume time
    now = datetime.now(timezone.utc)
    recovered = []
    for tier in degraded:
        resume_at = _degraded_executors_probed_at.get(tier)
        if resume_at is not None and now >= resume_at:
            recovered.append(tier)
            _degraded_executors.discard(tier)
            _degraded_executors_probed_at.pop(tier, None)
            logger.info("kanban: executor %s recovered (resume_at passed)", tier)

    active.extend(recovered)

    # kax-exec-03: the fallback below is documented as "if all executors are
    # degraded, try them anyway". It never fired, because only claude_cli is
    # ever ADDED to _degraded_executors (the single call site guards on
    # executor_type == "claude_cli"), while gitlab and ollama_local are never
    # marked degraded — nothing probes them, and on a host that has neither they
    # are never successfully used either. So degrading claude_cli always left
    # active = ["gitlab", "ollama_local"], which is non-empty, and the scheduler
    # walked two tiers that cannot work here. Quarantine stopped being a
    # possible outcome and became a guaranteed one.
    #
    # "In the configured chain" is not the same claim as "usable on this host".
    # A tier that has never once dispatched successfully is not evidence of a
    # working executor, so it must not suppress the last-resort fallback.
    if active and any(tier in _tiers_ever_dispatched for tier in active):
        return active
    if active and not _tiers_ever_dispatched:
        # Nothing has dispatched yet this process (fresh scheduler start) — no
        # evidence either way, so behave exactly as before rather than second-
        # guessing a cold cache.
        return active
    if active:
        logger.warning(
            "kanban: effective chain %s contains no tier that has ever "
            "dispatched on this host (degraded=%s) — falling back to the full "
            "chain %s rather than walking known-dead tiers",
            active, sorted(_degraded_executors), original_chain,
        )
    return original_chain


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
                "FROM kanban_tasks WHERE id = %s", (task_id,),
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


def _get_parent_handoff(task_id: str) -> Optional[str]:
    """Return the parent task's last_run_summary and last_run_metadata as a
    formatted context block, or None if no parent or no handoff data exists.
    """
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT kt.depends_on_task_id, "
            "       p.last_run_summary, p.last_run_metadata, p.title "
            "FROM kanban_tasks kt "
            "LEFT JOIN kanban_tasks p ON p.id = kt.depends_on_task_id "
            "WHERE kt.id = %s",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if not d.get("depends_on_task_id"):
            return None
        summary = (d.get("last_run_summary") or "").strip()
        metadata_raw = (d.get("last_run_metadata") or "").strip()
        if not summary and not metadata_raw:
            return None
        lines = [f"## Parent task output: {d.get('title', d['depends_on_task_id'])}"]
        if summary:
            lines.append(f"Summary: {summary}")
        if metadata_raw:
            lines.append(f"Metadata (JSON): {metadata_raw}")
        return "\n".join(lines) + "\n\n"
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def _external_repo_brief(task_id: str) -> str:
    """Tell an external-repo agent which repo it is in and which gates apply.

    Returns "" for an ICDev task, so the ICDev instruction is byte-unchanged.

    This matters because ICDev's gates are the WRONG gates in compass: compass and
    idea_lab have no CI, so there is no green check to wait for, and ICDev's
    coherence checker walks an ICDev-shaped tree that does not exist there. An agent
    that ran them would fail on rules that do not apply to the repo it is standing in.
    """
    target = _task_repo_target(task_id)
    if target is None or not target.is_external or target.root is None:
        return ""

    return (
        f"## You are working in the {target.name.upper()} repository, not ICDev\n\n"
        f"Repo root: `{target.root}`  ·  base branch: `{target.base_branch}`\n"
        f"You are already in an isolated worktree of that repo. Everything you do — "
        f"edits, commits, the branch, the PR — belongs to {target.name}. Do NOT edit, "
        f"commit to, or reason about the ICDev checkout.\n\n"
        f"### The gate here is NOT ICDev's\n\n"
        f"{target.name} has **no CI**, so there is no green check to wait for. The "
        f"accepted gate is a LOCAL full-suite run, compared against a clean baseline so "
        f"you can tell your failures from pre-existing ones:\n\n"
        f"    python -m pytest -q\n"
        f"    python -m ruff check .\n\n"
        f"Do NOT run ICDev's coherence checker, companion sync, or route verifier — they "
        f"walk an ICDev-shaped tree that does not exist here, and failing them tells you "
        f"nothing about this repo.\n\n"
        f"If the full suite has pre-existing failures, say so explicitly and compare "
        f"against `origin/{target.base_branch}` rather than assuming they are yours.\n\n"
        f"### Do NOT mark this task done, and do NOT bypass the verification gate\n\n"
        f"Open a PR against {target.name} and stop there. The scheduler marks the task "
        f"done once the commits are actually on {target.name}'s `origin/"
        f"{target.base_branch}` — that is the only thing that counts as done.\n\n"
        f"`bypass_verification` means 'ICDev's CodeLens/Coherence/E2E suite could not "
        f"run here'. That is TRUE in {target.name} and it is IRRELEVANT: it has never "
        f"meant 'this work does not have to land anywhere'. Marking the task done with "
        f"your work sitting on an unmerged branch is a phantom completion — the board "
        f"goes green and nobody ever looks at the branch again. The API will refuse it.\n\n"
    )


def _build_instruction(task_id: str, title: str, prompt_text: str, prompt_path: str) -> str:
    """Compose the full instruction text used by both executors.

    Injects retry coaching if the task has prior failures (guard-22), and
    parent handoff context when the task has a dependency whose executor
    submitted a structured summary/metadata via POST /api/kanban/tasks/<id>/handoff.
    """
    coaching = _get_retry_coaching(task_id)
    parent_context = _get_parent_handoff(task_id) or ""
    external = _external_repo_brief(task_id)
    return (
        f"{coaching}{parent_context}{external}{prompt_text}\n\n"
        f"When complete:\n"
        f"1. (Optional) Submit handoff: POST http://localhost:5050/api/kanban/"
        f'tasks/{task_id}/handoff with {{"summary": "...", "metadata": {{...}}}}\n'
        f"2. Move to done: POST http://localhost:5050/api/kanban/"
        f'tasks/{task_id}/move with {{"status": "done"}}\n'
        f'3. Notify: python -c "from tools.notifications.adapters.'
        f"telegram import send; send('Task Completed', "
        f"'{title} — done', severity='success')\"\n"
        f"4. Delete prompt file: {prompt_path}\n"
    )


def _agent_session(task: dict, instruction: str, work_dir: str,
                   dispatch_source: str = "genesis_scheduler"):
    """Build the AgentSession an adapter needs to run this task.

    Everything executor-specific about a kanban dispatch is expressed here as
    session metadata: the stop-hook tags, the scratch directory for the stdin
    temp file, and the operator's model override.

    MODEL OVERRIDE: a Claude model selected in the dashboard is handed to the
    adapter, which passes it through as ``--model``. The ``cli_capable`` guard
    stays on this side because a NON-Claude selection must never be handed to
    the Claude CLI — such a selection never reaches here at all, because it
    removes claude_cli from the executor chain
    (``_build_effective_executor_chain``); quietly ignoring the choice would
    make the dropdown a lie.
    """
    from tools.agents.adapter_base import AgentSession  # noqa: PLC0415

    task_id = task["id"]
    metadata: Dict[str, Any] = {
        # guard-23: propagate dispatch_source via env so the stop hook can tag
        # this session's commits as 'genesis_scheduler' rather than
        # 'claude_interactive'.
        "dispatch_source": dispatch_source,
        "temp_dir": str(BASE_DIR / ".tmp"),
        "project_id": str(task.get("project_id") or ""),
        "task_type": str(task.get("task_type") or ""),
    }
    _model = _selected_model()
    if _model and _model.get("cli_capable") and _model.get("model_id"):
        metadata["model_id"] = str(_model["model_id"])
        logger.info("kanban: dispatching %s on model %s (%s)",
                    task_id, _model["name"], _model["model_id"])

    # The SAME budget the reaper uses to kill this task, so a blocking adapter
    # stops itself just before the kill timer rather than being killed from
    # outside with no result. An unreachable board must not stop a dispatch.
    try:
        timeout_seconds = int(_get_task_timeout(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("kanban: task timeout lookup failed for %s: %s", task_id, exc)
        timeout_seconds = int(MAX_EXECUTION_SECONDS)

    return AgentSession(
        task_id=task_id,
        prompt=instruction,
        working_dir=work_dir,
        max_turns=MAX_TURNS,
        timeout_seconds=timeout_seconds,
        metadata=metadata,
    )


def _dispatch_via_claude_cli(task: dict, prompt_path: str, instruction: str,
                             work_dir: str, task_log: Path, adapter=None) -> None:
    """ClaudeCodeTaskExecutor — the bookkeeping around the ONE claude shellout.

    The shellout itself (argv, env tagging, the stdin temp-file that dodges the
    Windows 32767-char command-line limit, the model override) lives in
    ``tools/agents/adapters/claude_cli.py`` so exactly one implementation
    exists. What stays here is what is genuinely kanban's: the task-source tag,
    the ``_running`` handle the poll/timeout loop reaps, the agent-surface
    invocation and the audit row.
    """
    task_id = task["id"]
    if adapter is None:
        adapter = _pick_chain_adapter(["claude_cli"], task.get("task_type"))
    if adapter is None or not _claude_code_available():
        print("  Kanban: claude CLI not found — should have routed to LLM executor")
        return
    try:
        log_fh = open(str(task_log), "w", encoding="utf-8", errors="replace")
        _tag_task_source(task_id, "genesis_scheduler")

        proc = adapter.spawn(
            _agent_session(task, instruction, work_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )

        _running[task_id] = proc

        _record_dispatch_pid(task_id, proc)
        _dispatch_times[task_id] = datetime.now(timezone.utc)
        # THE agent surface. Not agent_executor.execute_agent — this runner
        # never calls it. Instrumenting that function left `agent` with zero
        # rows for its entire existence while the real build path, this
        # subprocess, went unobserved. Opened here and closed in
        # _check_completed when proc.poll() returns, so the recorded duration is
        # the agent's actual wall-clock rather than a scheduler cycle.
        # Supersede any handle this task left open. A task can be re-dispatched
        # without its previous invocation ever being closed, and #1188 was
        # exactly this bug in kanban_executions: the stranded rows sat in
        # 'running' forever and "what is running now" drifted from the truth.
        _close_agent_invocation(
            _agent_invocations.pop(task_id, None), status="superseded",
        )
        _agent_invocations[task_id] = _open_agent_invocation(
            _SURFACE_AGENT, task_id,
            project_id=str(task.get("project_id") or ""),
        )
        _audit_agent_execution(
            "agent_execution_started", task_id,
            pid=proc.pid,
            executor="claude-cli",
            project_id=str(task.get("project_id") or ""),
        )
        print(f"  Kanban: dispatched {task_id} to claude CLI (PID {proc.pid})")
    except FileNotFoundError as e:
        print(f"  Kanban: claude dispatch error for {task_id}: {e}")
    except Exception as e:
        print(f"  Kanban: claude dispatch error for {task_id}: {e}")


def _dispatch_via_agent_adapter(adapter, task: dict, prompt_path: str,
                                instruction: str, work_dir: str,
                                task_log: Path) -> bool:
    """Dispatch through an AgentAdapter. Returns True if a handle is running.

    Two shapes of adapter, one entry point:

    * an adapter that can ``spawn`` (claude_cli) hands back a real process, so
      the runner keeps its own poll/kill/timeout loop and the hardened
      bookkeeping in ``_dispatch_via_claude_cli``;
    * an adapter that only implements the protocol's blocking ``invoke``
      (``local_agent``, ``local_llm_router``) runs on a thread behind
      ``_LLMTaskHandle``, which is Popen-compatible — so everything downstream
      (timeout sweeper, completion checker, verification) is unchanged.
    """
    task_id = task["id"]
    if getattr(adapter, "name", "") == "claude_cli":
        _dispatch_via_claude_cli(task, prompt_path, instruction, work_dir,
                                 task_log, adapter=adapter)
        # Reaching the CLI executor counts as dispatched even when the spawn
        # itself failed — unchanged from before hgx-exec-03. Walking on to
        # gitlab/ollama after a transient claude error would put two competing
        # implementations of the same task in flight.
        return True

    session = _agent_session(task, instruction, work_dir)
    adapter_name = getattr(adapter, "name", "agent-adapter")

    def _runner():
        with open(task_log, "w", encoding="utf-8", newline="",
                  errors="replace") as fh:
            fh.write(f"[{adapter_name} dispatch — task {task_id}]\n")
            fh.write(f"[work_dir {work_dir}]\n\n")
            result = adapter.invoke(session)
            fh.write(result.output or "")
            if result.error:
                fh.write(f"\n[error] {result.error}\n")
            fh.flush()
            if not result.completed:
                # Signal failure (returncode 1) so the task is NOT marked done;
                # the standard verify/remediation/lesson chain still runs.
                raise RuntimeError(
                    f"{adapter_name} did not complete {task_id}"
                    + (f": {result.error}" if result.error else "")
                )

    handle = _LLMTaskHandle(task_id=task_id, log_path=task_log)
    handle.start(_runner)
    _running[task_id] = handle
    _record_dispatch_pid(task_id, handle)
    _dispatch_times[task_id] = datetime.now(timezone.utc)
    _close_agent_invocation(
        _agent_invocations.pop(task_id, None), status="superseded",
    )
    _agent_invocations[task_id] = _open_agent_invocation(
        _SURFACE_AGENT, task_id,
        project_id=str(task.get("project_id") or ""),
    )
    _audit_agent_execution(
        "agent_execution_started", task_id,
        executor=adapter_name,
        project_id=str(task.get("project_id") or ""),
    )
    print(f"  Kanban: dispatched {task_id} via agent adapter {adapter_name}")
    return True


def _rubric_loop_enabled() -> bool:
    """Phase 3b opt-in: build via the rubric-gated agent loop (which can EDIT
    files) instead of the text-only LLMRouter path. Default OFF —
    ``_dispatch_via_claude_cli`` stays primary and the existing air-gap path is
    byte-unchanged unless ``KANBAN_RUBRIC_LOOP`` is truthy."""
    import os
    return os.environ.get("KANBAN_RUBRIC_LOOP", "0").strip().lower() in ("1", "true", "yes", "on")


def _dispatch_via_rubric_loop(task: dict, prompt_path: str, instruction: str,
                              work_dir: str, task_log: Path) -> None:
    """Rubric-gated build loop (Phase 3b) — air-gap executor that actually EDITS
    files. Runs ``run_agent_loop_with_rubric`` with the delivery-pipeline gates
    (``make_pipeline_grader``) as the rubric, so a task builds -> runs the gates
    -> revises in-session until it satisfies the pipeline or hits the
    iteration/budget cap. LLM-agnostic: every model call routes through the
    injected ``LLMRouter`` by function name — no provider is assumed. Opt-in via
    ``KANBAN_RUBRIC_LOOP``; ``_dispatch_via_claude_cli`` remains primary.
    """
    task_id = task["id"]

    def _runner():
        import threading

        # import-as form so a monkeypatched ``tools.llm.*`` module is the same
        # object the loop uses (see _dispatch_via_llm_router for the rationale).
        import tools.llm.router as _llm_router_mod
        # run_agent_loop_with_rubric lives ONLY in the canonical icdev copy —
        # the physical tools/llm/agent_loop.py is a stale shim without it.
        try:
            import icdev.tools.llm.agent_loop as _agent_loop_mod
        except ImportError:
            import tools.llm.agent_loop as _agent_loop_mod
        from tools.genesis.rubric_build_tools import build_worktree_toolset
        from tools.workflow.pipeline_grader import make_pipeline_grader

        def _changed():
            # Git-changed files in the worktree; never let a diff failure crash
            # the grader (make_pipeline_grader accepts a callable).
            try:
                from tools.integrity.pr_gates import _git_changed_files
                return _git_changed_files("origin/main", False, Path(work_dir))
            except Exception:
                return []

        with open(task_log, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[rubric-loop dispatch — task {task_id}]\n")
            fh.write(f"[work_dir {work_dir}]\n\n")

            tools_schema, tool_handlers = build_worktree_toolset(work_dir)
            _task_budget = _get_task_timeout(task_id)
            # Cap one gate sweep at a quarter of the task's dispatch budget.
            # The rubric loop grades up to max_grading_iterations times before
            # post-task validation runs again, so an ungoverned gate could (and
            # did) spend the whole dispatch window judging instead of building.
            _gate_budget = max(60.0, _task_budget * 0.25)
            # Session wall-clock ceiling (ars-wall-01). _get_task_timeout is the
            # SAME number the reaper uses to kill this task — and it already
            # honours kanban_tasks.max_runtime_seconds ahead of every heuristic,
            # so the loop-level and task-level ceilings derive from one source
            # instead of racing. Held slightly under the kill timer so the loop
            # stops itself and returns a real result with
            # truncation_reason="max_wall_clock_seconds"; being killed from
            # outside yields no result and no reason at all.
            _wall_budget = max(60.0, _task_budget * 0.9)
            grader = make_pipeline_grader(
                cwd=work_dir,
                task_id=task_id,
                modified_files=_changed,
                run_e2e=False,
                run_conformance=True,
                compare_to_main=True,
                budget_sec=_gate_budget,
            )
            router = _llm_router_mod.LLMRouter()
            stop_event = threading.Event()
            system_prompt = (
                "You are an autonomous software engineer building ONE kanban task "
                "inside an isolated git worktree. Use write_file / patch_file to "
                "implement the change and read_file / list_files to inspect the "
                "tree, then call done. Your work is graded by the delivery "
                "pipeline (code quality, coherence, conformance to the task's "
                "acceptance criteria, and tests); if it fails you receive specific "
                "feedback and must fix it. Make the smallest correct change that "
                "satisfies the task."
            )

            def _on_grade(round_no, grade):
                fh.write(f"[grade {round_no}] {grade.verdict}: {str(grade.feedback)[:400]}\n")
                try:
                    fh.flush()
                except Exception:
                    pass

            result = _agent_loop_mod.run_agent_loop_with_rubric(
                router,
                grader=grader,
                max_grading_iterations=3,
                on_grade=_on_grade,
                system_prompt=system_prompt,
                user_prompt=instruction,
                tools=tools_schema,
                tool_handlers=tool_handlers,
                llm_function="code_generation",
                max_iterations=12,
                stop_event=stop_event,
                # Budget for the WHOLE rubric run (all rounds + grading).
                max_wall_clock_seconds=_wall_budget,
                # Continuous Harness: key the recorded codegen decision on the
                # kanban task id so record_outcome() (fired on the task's status
                # transition) attaches to a real decision row.
                harness_task_id=task_id,
            )

            ar = result.result
            fh.write(
                f"\n[rubric-loop done] satisfied={result.satisfied} "
                f"grading_attempts={result.grading_attempts} "
                f"loop_done={getattr(ar, 'done', None)} "
                f"cost_usd={getattr(ar, 'total_cost_usd', 0)} "
                f"elapsed_s={getattr(ar, 'elapsed_seconds', 0):.0f}/{_wall_budget:.0f} "
                f"truncation_reason={getattr(ar, 'truncation_reason', '')}\n"
            )
            if not result.satisfied:
                # In-session revision exhausted without passing the gates. Signal
                # failure (returncode 1) so the task is NOT marked done; the
                # standard post-task verify/remediation/lesson chain still runs.
                raise RuntimeError(
                    f"rubric loop did not satisfy the pipeline after "
                    f"{result.grading_attempts} round(s)"
                )

    handle = _LLMTaskHandle(task_id=task_id, log_path=task_log)
    handle.start(_runner)
    _running[task_id] = handle
    _record_dispatch_pid(task_id, handle)
    _dispatch_times[task_id] = datetime.now(timezone.utc)
    print(f"  Kanban: dispatched {task_id} via rubric-gated agent loop (Phase 3b)")


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

    # Phase 3b opt-in: when enabled, build with the rubric-gated agent loop
    # (which can edit files + self-verify against the pipeline) instead of this
    # text-only path. Default OFF keeps this path byte-unchanged.
    if _rubric_loop_enabled():
        return _dispatch_via_rubric_loop(task, prompt_path, instruction, work_dir, task_log)

    def _runner():
        # Use `import … as` form so the attribute-access chain goes through
        # _ToolsRedirect.__getattr__ → icdev.tools.llm.router, the same
        # module object that tests monkeypatch via `import tools.llm.router`.
        # `from tools.llm.router import LLMRouter` hits sys.modules["tools.llm.router"]
        # which is the PHYSICAL tools/llm/router.py — a different object.
        import tools.llm.router as _llm_router_mod
        LLMRouter = _llm_router_mod.LLMRouter
        import tools.llm.provider as _llm_provider_mod
        LLMRequest = _llm_provider_mod.LLMRequest
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

            _dispatch_completed = False
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
                # Model override: the operator picked a model, so build with THAT — not
                # with whatever llm_config routes 'code_generation' to. This is the path
                # a Kimi/Ollama/GPT selection takes (claude_cli having been dropped from
                # the chain), and it is the whole point of the selector: when Claude is
                # exhausted, the runner keeps going on something else.
                _mo = _selected_model()
                if _mo:
                    request.model = _mo["name"]
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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)

                # OPT-62: drain the user message queue. If nothing was
                # injected mid-run, the task is done — exit the loop.
                try:
                    queued = hook_compat.check_message_queue(task_id)
                except Exception as exc:
                    fh.write(f"[check_message_queue error] {exc}\n")
                    queued = []

                if not queued:
                    _dispatch_completed = True
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

            # Continuous Harness feed — the text-only LLMRouter executor does not
            # go through run_agent_loop, so record the codegen decision directly
            # here at dispatch completion. This lets the kanban reflex's later
            # record_outcome() attach an outcome regardless of which executor ran.
            try:
                from tools.genesis.harness.eval_harness import record_decision
                record_decision(
                    task_id=task_id,
                    reflex="codegen",
                    decision="done" if _dispatch_completed else "error_max_turns",
                    confidence=0.6 if _dispatch_completed else 0.3,
                    metadata={
                        "executor": "llm_router",
                        "llm_function": "code_generation",
                        "completed": _dispatch_completed,
                    },
                )
            except Exception as _hd_exc:
                logger.debug("harness record_decision skipped for %s: %s", task_id, _hd_exc)

    handle = _LLMTaskHandle(task_id=task_id, log_path=task_log)
    handle.start(_runner)
    _running[task_id] = handle
    _record_dispatch_pid(task_id, handle)
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
            with get_connection() as conn:
                conn.execute(
                    "UPDATE kanban_tasks SET execution_id = %s, executor_url = %s, updated_at = %s WHERE id = %s",
                    (pipeline_id, pipeline_web_url, datetime.now(timezone.utc).isoformat(), task_id),
                )
        except Exception as _db_exc:
            logger.warning("kanban: failed to store pipeline_id for %s: %s", task_id, _db_exc)
        logger.info("kanban: GitLab pipeline %s triggered for task %s", pipeline_id, task_id)
        return True

    logger.warning(
        "kanban: GitLab pipeline trigger returned %d for %s", resp.status_code, task_id
    )
    return False


def _dispatch_github_actions(task_id: str, task_desc: str, task_type: str) -> bool:
    """Trigger a GitHub Actions workflow for the given task (EXEC_GITHUB_ACTIONS tier).

    POSTs to the GitHub API workflow_dispatch endpoint with task_id, task_desc,
    and task_type as inputs. Stores the returned run_id in the task row.
    Returns True on HTTP 204, False on any error.
    """
    import os as _os
    try:
        import requests as _requests
    except ImportError:
        logger.warning("kanban: requests library not available — cannot dispatch via GitHub Actions")
        return False

    token = _os.getenv("GITHUB_TOKEN", "").strip()
    repo = _os.getenv("GITHUB_RUNNER_REPO", "").strip()
    if not token or not repo:
        logger.warning("kanban: GITHUB_TOKEN / GITHUB_RUNNER_REPO not set")
        return False

    workflow_file = _os.getenv("GITHUB_WORKFLOW_FILE", "icdev-kanban-runner.yml")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    llm_provider = _os.getenv("LLM_PROVIDER", "ollama_cloud").strip() or "ollama_cloud"
    payload = {
        "ref": "main",
        "inputs": {
            "task_id": task_id,
            "task_desc": task_desc,
            "task_type": task_type,
            "llm_provider": llm_provider,
        },
    }

    try:
        resp = _requests.post(url, headers=headers, json=payload, timeout=15)
    except _requests.RequestException as exc:
        logger.warning("kanban: GitHub Actions dispatch failed for %s: %s", task_id, exc)
        return False

    if resp.status_code == 204:
        run_url = f"https://github.com/{repo}/actions"
        run_id = "pending"
        try:
            import time as _time
            _time.sleep(6)
            runs_url = (
                f"https://api.github.com/repos/{repo}/actions/workflows/"
                f"{workflow_file}/runs?per_page=10&event=workflow_dispatch"
            )
            runs_resp = _requests.get(runs_url, headers=headers, timeout=10)
            if runs_resp.status_code == 200:
                for _run in runs_resp.json().get("workflow_runs", []):
                    if task_id in (_run.get("display_title") or _run.get("name") or ""):
                        run_id = str(_run["id"])
                        run_url = _run.get("html_url", run_url)
                        break
        except Exception as _poll_exc:
            logger.warning("kanban: could not capture run_id for %s: %s", task_id, _poll_exc)
        try:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE kanban_tasks SET execution_id = %s, executor_url = %s, updated_at = %s WHERE id = %s",
                    (run_id, run_url, datetime.now(timezone.utc).isoformat(), task_id),
                )
        except Exception as _db_exc:
            logger.warning("kanban: failed to store execution_id for %s: %s", task_id, _db_exc)
        logger.info("kanban: GitHub Actions workflow triggered for task %s (run_id=%s)", task_id, run_id)
        return True

    logger.warning(
        "kanban: GitHub Actions dispatch returned %d for %s: %s",
        resp.status_code, task_id, resp.text[:200],
    )
    return False


_ga_last_polled: dict = {}  # task_id -> datetime of last poll


def _poll_github_actions_completions() -> None:
    """Check GA API for completed runs and move tasks to done/backlog.

    Runs at the start of each scheduler cycle. Rate-limited to one API call
    per task per 60 seconds to stay well within GitHub's 5000-req/hour limit.
    Tasks with execution_id='pending' are matched by task_id in the run name.
    """
    import os as _os
    try:
        import requests as _req
    except ImportError:
        return

    token = _os.getenv("GITHUB_TOKEN", "").strip()
    repo = _os.getenv("GITHUB_RUNNER_REPO", "").strip()
    workflow_file = _os.getenv("GITHUB_WORKFLOW_FILE", "icdev-kanban-runner.yml")
    if not token or not repo:
        return

    _hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        _conn = get_connection()
    except Exception:
        return
    try:
        _rows = _conn.execute(
            "SELECT id, execution_id FROM kanban_tasks "
            "WHERE status='in_progress' AND executor_type='github_actions'"
        ).fetchall()
    except Exception:
        try:
            _conn.close()
        except Exception:
            pass
        return

    now = datetime.now(timezone.utc)
    for _row in _rows:
        task_id = _row["id"]
        run_id = _row["execution_id"] or "pending"

        # Rate-limit: skip if polled within the last 60 seconds
        _last = _ga_last_polled.get(task_id)
        if _last and (now - _last).total_seconds() < 60:
            continue
        _ga_last_polled[task_id] = now

        try:
            if run_id == "pending":
                # Discover run_id by matching task_id in the run display_title
                _search_url = (
                    f"https://api.github.com/repos/{repo}/actions/workflows/"
                    f"{workflow_file}/runs?per_page=20&event=workflow_dispatch"
                )
                _sr = _req.get(_search_url, headers=_hdrs, timeout=10)
                if _sr.status_code == 200:
                    for _run in _sr.json().get("workflow_runs", []):
                        if task_id in (_run.get("display_title") or _run.get("name") or ""):
                            run_id = str(_run["id"])
                            _conn.execute(
                                "UPDATE kanban_tasks SET execution_id=%s, executor_url=%s WHERE id=%s",
                                (run_id, _run.get("html_url", ""), task_id),
                            )
                            _conn.commit()
                            break
                if run_id == "pending":
                    continue  # still not found — check next cycle

            # Check run status
            _run_resp = _req.get(
                f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
                headers=_hdrs, timeout=10,
            )
            if _run_resp.status_code != 200:
                continue
            _data = _run_resp.json()
            if _data.get("status") == "completed":
                _conclusion = _data.get("conclusion", "")
                if _conclusion == "success":
                    logger.info("kanban: GA run %s for %s succeeded → done", run_id, task_id)
                    _move_task(task_id, "done",
                               reason=f"GitHub Actions run {run_id} concluded 'success'")
                else:
                    logger.warning(
                        "kanban: GA run %s for %s conclusion=%s → backlog", run_id, task_id, _conclusion
                    )
                    _move_task(
                        task_id, "backlog", actor="scheduler",
                        reason=f"GitHub Actions run {run_id} concluded "
                               f"'{_conclusion or 'unknown'}'",
                    )
                _ga_last_polled.pop(task_id, None)
        except Exception as _exc:
            logger.warning("kanban: GA poll error for %s: %s", task_id, _exc)

    # Release the connection — the network poll loop above must not hold an
    # open (idle-in-transaction) connection for its whole duration.
    try:
        _conn.close()
    except Exception:
        pass


# Workflows to monitor for CI failures. Kanban runner is intentionally excluded.
_CI_WATCHED_WORKFLOWS: list = ["icdev-ci.yml", "ci_cd_pipeline.yml"]


def _detect_and_queue_ci_failures() -> None:
    """Detect failed CI runs on main and enqueue auto-fix tasks in the backlog.

    Watches _CI_WATCHED_WORKFLOWS for conclusion=failure on the main branch.
    For each unprocessed failure:
      - Fetches failed-step logs from the GitHub API
      - Creates a backlog task (id=ci-fix-{run_id}, type=fix, executor=github_actions)
        so the scheduler dispatches it to the kanban runner for auto-remediation.

    Infinite-loop guards:
      - Deduped by run_id: once a ci-fix-{id} task exists, the run is never requeued.
      - Kanban runner failures are skipped (workflow path contains kanban-runner).
      - Runs triggered by a ci-fix task (display_title contains 'ci-fix-') are skipped.
    """
    import os as _os
    try:
        import requests as _req
    except ImportError:
        return

    token = _os.getenv("GITHUB_TOKEN", "").strip()
    repo = _os.getenv("GITHUB_RUNNER_REPO", "").strip()
    if not token or not repo:
        return

    _hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        _conn = get_connection()
    except Exception:
        return

    now = datetime.now(timezone.utc)

    for wf_file in _CI_WATCHED_WORKFLOWS:
        try:
            runs_resp = _req.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/"
                f"{wf_file}/runs?status=failure&per_page=5&branch=main",
                headers=_hdrs, timeout=10,
            )
            if runs_resp.status_code != 200:
                continue
        except Exception as _re:
            logger.warning("kanban: CI failure poll error for %s: %s", wf_file, _re)
            continue

        for _run in runs_resp.json().get("workflow_runs", []):
            run_id = str(_run["id"])
            run_title = _run.get("display_title") or _run.get("name") or ""
            wf_path = _run.get("path", "")

            # Guard 1: never queue failures from the kanban runner itself
            if "kanban-runner" in wf_path or "kanban_runner" in wf_path:
                continue

            # Guard 2: never queue a failure caused by a ci-fix task (no recursion)
            if "ci-fix-" in run_title:
                continue

            # Guard 3: only act on failures from the last 24 hours
            try:
                from datetime import timedelta as _td
                _run_created = datetime.fromisoformat(
                    (_run.get("created_at") or "").replace("Z", "+00:00")
                )
                if (now - _run_created) > _td(hours=24):
                    continue
            except Exception:
                pass

            task_id = f"ci-fix-{run_id}"

            # Guard 3: dedup — skip if a fix task for this run already exists
            if _conn.execute("SELECT 1 FROM kanban_tasks WHERE id=%s", (task_id,)).fetchone():
                continue

            # Fetch failed job logs for error context
            error_ctx = (
                f"CI workflow: {wf_file}\n"
                f"Run URL: {_run.get('html_url', '')}\n"
                f"Triggered by commit: {run_title}\n\n"
            )
            try:
                _jobs_resp = _req.get(
                    f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
                    headers=_hdrs, timeout=10,
                )
                _jobs = _jobs_resp.json().get("jobs", []) if _jobs_resp.status_code == 200 else []
                _failed_jobs = [j for j in _jobs if j.get("conclusion") == "failure"]

                for _job in _failed_jobs[:3]:
                    _jname = _job.get("name", "unknown job")
                    _fsteps = [s for s in _job.get("steps", []) if s.get("conclusion") == "failure"]
                    _step_names = ", ".join(s.get("name", "") for s in _fsteps[:3])
                    error_ctx += f"=== Job: {_jname} | Failed steps: {_step_names} ===\n"

                    # Fetch the job log (redirects to pre-signed URL)
                    _log_resp = _req.get(
                        f"https://api.github.com/repos/{repo}/actions/jobs/{_job['id']}/logs",
                        headers=_hdrs, timeout=15, allow_redirects=True,
                    )
                    if _log_resp.status_code == 200:
                        # Keep error-bearing lines only (filter runner noise)
                        _error_lines = [
                            ln for ln in _log_resp.text.splitlines()
                            if any(
                                kw in ln
                                for kw in (
                                    "error", "Error", "ERROR", "FAILED", "failed",
                                    "exit code", ">> Issue", "ModuleNotFoundError",
                                    "ImportError", "SyntaxError", "not found",
                                    "cannot import", "Traceback",
                                )
                            )
                        ]
                        error_ctx += "\n".join(_error_lines[:80]) + "\n\n"
            except Exception as _le:
                logger.warning("kanban: log fetch failed for run %s: %s", run_id, _le)
                error_ctx += f"(log fetch failed: {_le})\n"

            # Insert fix task
            _title = f"fix(ci): auto-fix {wf_file} failure — run {run_id}"
            try:
                _conn.execute(
                    "INSERT OR IGNORE INTO kanban_tasks "
                    "(id, title, description, priority, task_type, status, "
                    " executor_type, tags, created_at, updated_at) "
                    "VALUES (%s, %s, %s, 'high', 'fix', 'backlog', 'claude_cli', "
                    "        'ci_autofix', %s, %s)",
                    (_task_id := task_id, _title, error_ctx[:8000], now.isoformat(), now.isoformat()),
                )
                _conn.commit()
                logger.info("kanban: queued CI fix task %s for run %s", task_id, run_id)
            except Exception as _ie:
                logger.warning("kanban: failed to insert ci-fix task %s: %s", task_id, _ie)

    # Release the connection held across the CI-failure network poll loop.
    try:
        _conn.close()
    except Exception:
        pass


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
    if ("tool_not_in_manifest" in title or "tool_not_in_manifest" in description):
        tool_path = (
            tool_match.group(1) if tool_match
            else _nlp_extract_gap_subject(title, description, "tool_not_in_manifest")
        )
        if tool_path:
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
    if not route_match and "route_not_listed" in (title + description).lower():
        nlp_route = _nlp_extract_gap_subject(title, description, "route_not_listed")
        if nlp_route:
            route = nlp_route
        else:
            route = None
    elif route_match:
        route = route_match.group(1)
    else:
        route = None
    if route:
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
    """Stamp executor_type on the task row so the UI badge is accurate.

    Also opens the task's kanban_executions row. This is the one place every
    executor tier passes through exactly once on a successful dispatch, so it is
    where "a dispatch started, at this time, via this executor" is true for all
    of them — rather than four call sites that would drift apart.
    """
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE kanban_tasks SET executor_type = %s WHERE id = %s",
                (executor_type, task_id),
            )
    except Exception as exc:
        logger.debug("kanban: failed to set executor_type for %s: %s", task_id, exc)

    _open_execution(task_id, executor_type)


def _open_execution(task_id: str, executor_type: str) -> Optional[str]:
    """Open a kanban_executions row for this dispatch. Returns its id, or None.

    ``kanban_executions`` has had exactly the columns needed to answer "how long
    does a task actually take" since migration 010, and zero rows for its entire
    existence — nothing ever wrote to it. That is why ``execution_seconds`` is
    populated on 7 of 2,586 tasks and ``_detect_execution_anomalies`` has been
    falling back to the static timeout constants instead of adapting.

    Columns here are the LIVE ones (migration 010 + later additions), not the
    stale set in tools/kanban/init_db.py — an INSERT naming a column that only
    exists in some DDL fails at runtime and gets swallowed by the except below,
    which is precisely how a table ends up with no rows and nobody notices.
    """
    execution_id = f"exec-{task_id}-{uuid.uuid4().hex[:8]}"
    try:
        with get_connection() as conn:
            # Close any row this task left open. A task can be re-dispatched
            # without its previous execution ever being closed — the scheduler
            # restarts, or startup recovery resets it to backlog and it is
            # promoted again — and _close_execution only ever closes the MOST
            # RECENT open row. Without this, every such re-dispatch strands a
            # row in 'running' forever, so "what is running now" drifts further
            # from the truth the longer the board runs. Observed immediately
            # after the first restart that enabled this telemetry: two tasks
            # each had two rows in 'running'.
            conn.execute(
                "UPDATE kanban_executions SET status = %s, completed_at = %s "
                "WHERE task_id = %s AND completed_at IS NULL",
                ("superseded", _utcnow_iso(), task_id),
            )
            conn.execute(
                "INSERT INTO kanban_executions "
                "(id, task_id, executor_type, execution_id, started_at, status) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (execution_id, task_id, executor_type, execution_id,
                 _utcnow_iso(), "running"),
            )
            conn.execute(
                "UPDATE kanban_tasks SET execution_id = %s WHERE id = %s",
                (execution_id, task_id),
            )
        return execution_id
    except Exception as exc:  # noqa: BLE001 — telemetry must never block dispatch
        logger.warning("kanban: could not open execution row for %s: %s", task_id, exc)
        return None


def _close_execution(task_id: str, status: str, exit_code: Optional[int] = None,
                     output_summary: str = "") -> None:
    """Close the task's open execution row and stamp kanban_tasks.execution_seconds.

    Best-effort and idempotent-ish: if no open row exists (scheduler restarted
    mid-task, telemetry insert failed) this is a no-op rather than an error.
    """
    try:
        now = datetime.now(timezone.utc)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, started_at FROM kanban_executions "
                "WHERE task_id = %s AND completed_at IS NULL "
                "ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if not row:
                return
            d = dict(row)
            conn.execute(
                "UPDATE kanban_executions SET completed_at = %s, status = %s, "
                "exit_code = %s, output_summary = %s WHERE id = %s",
                (now.isoformat(), status, exit_code,
                 (output_summary or "")[:2000], d["id"]),
            )

            started = d.get("started_at")
            if started:
                try:
                    text = str(started).replace("Z", "+00:00")
                    start_dt = datetime.fromisoformat(text)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                    seconds = max(0.0, (now - start_dt).total_seconds())
                    conn.execute(
                        "UPDATE kanban_tasks SET execution_seconds = %s WHERE id = %s",
                        (seconds, task_id),
                    )
                except (ValueError, TypeError) as exc:
                    logger.debug("kanban: unparseable started_at for %s: %s", task_id, exc)
    except Exception as exc:  # noqa: BLE001 — telemetry must never block completion
        logger.warning("kanban: could not close execution row for %s: %s", task_id, exc)


def _get_executor_type(task_id: str) -> str | None:
    """Read executor_type from the task row."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT executor_type FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _dispatch_to_claude(task: dict, prompt_path: str):
    """Dispatch a task to the appropriate executor.

    Picks ClaudeCodeTaskExecutor when the `claude` CLI is available, otherwise
    falls back to LocalPythonTaskExecutor (LLMRouter-backed). The function
    name is preserved for backwards compatibility with existing call sites.

    Creates a git worktree for isolation so parallel tasks don't collide.
    A task whose worktree cannot be created is PARKED, never built in the
    shared checkout (autonomy-adm-02).

    FAST-PATH: _pre_dispatch_check runs first. If the task is a
    false-positive gap (tool already in manifest, route already in start.md,
    etc.), we mark it done immediately and skip Claude entirely — saving
    tokens and preventing false-negative "no commits" rejections.
    """
    task_id = task["id"]
    title = task.get("title", "Untitled")

    # ── Manual Build: the board keeps working; the runner does not build ──────
    # This is the single choke point where an executor is spawned — the normal
    # path, the token-exhausted retry, and every recovery re-dispatch all come
    # through here. Guarding it here rather than at each call site is what makes
    # "no automatic build" actually true rather than mostly true.
    #
    # The task is left at `scheduled`, NOT moved to in_progress: callers only
    # advance it when the subprocess is confirmed running (`task_id in _running`),
    # so a no-op dispatch leaves it visible in the Scheduled column, which is
    # exactly where a CLI session should find it and pick it up.
    #
    # Promotion, project cards, and the rest of the cycle are untouched — that is
    # the whole difference between this and Pause Scheduler.
    if _manual_build():
        logger.info(
            "kanban: Manual Build is ON — not dispatching %s (%s). It stays SCHEDULED "
            "for a CLI session to pick up.", task_id, title[:50],
        )
        return

    # ── Admission: is this task already carried by a merged PR? ───────────────
    # Asked ONCE, here, before a token is spent (autonomy-adm-01). #1862
    # duplicated rem-hyg-14 — already merged as #1858 — and would have deleted
    # 5,545 lines across 76 files by re-applying a ten-commit-old tree.
    #
    # DEFAULT `report`: it logs and dispatches anyway. The survey supports
    # arming (2.99% fire rate, 0.35% of dispatches wrongly refused, against the
    # 1.63% this repo calls refusing routine work) but shipping a gate enforcing
    # on day one against a survey written by the same change is the pattern that
    # has bitten this repo twice. `KANBAN_DISPATCH_ADMISSION=enforce` arms it.
    #
    # FAIL-OPEN by construction: an unreachable forge yields `unmeasurable`,
    # which never blocks. A gate that stops dispatch when it cannot see is how a
    # board stops moving at 3am.
    try:
        from tools.kanban.dispatch_admission import assess as _admission_assess

        _admission = _admission_assess(task_id)
        if _admission.verdict == "refuse":
            if _admission.blocks:
                logger.warning(
                    "kanban: admission REFUSED %s — %s. Not dispatching.",
                    task_id, _admission.reason,
                )
                try:
                    _move_task(task_id, "validating", actor="dispatch-admission",
                               reason=f"admission refused: {_admission.reason}")
                except Exception:  # noqa: BLE001
                    logger.exception("kanban: could not park %s after admission refusal",
                                     task_id)
                return
            logger.warning(
                "kanban: admission would REFUSE %s — %s (mode=report, dispatching "
                "anyway)", task_id, _admission.reason,
            )
    except Exception as exc:  # noqa: BLE001 — admission must never wedge dispatch
        logger.debug("kanban: admission check unavailable for %s: %s", task_id, exc)

    # ── Repo-aware dispatch: external-repo tasks ──────────────────────────────
    # An external-repo task (prem-* compass / idea_lab work, per
    # args/kanban_external_repos.yaml) must NEVER be built inside ICDev — its
    # deliverables land in ANOTHER repo, so ICDev's phantom-completion and
    # merge-to-origin/main gates always fail it and it churns.
    #
    # This USED TO park every external task unconditionally, which is why no
    # prem-* task ever auto-dispatched and the whole Premium Suite was driven by
    # hand. Now we park ONLY the ones we cannot build: an external task whose
    # repo root is not configured (root_env unset). Everything else is built
    # IN ITS OWN REPO — worktree, gates, PR and done-gate all pointed there by
    # _task_repo_root / _task_base_branch.
    #
    # ICDev tasks resolve to the default (is_external False) and are byte-
    # unchanged; an absent registry is a total no-op.
    _repo_target = _task_repo_target(task_id)
    if _repo_target is not None and _repo_target.is_external and not _repo_target.dispatchable:
        logger.warning(
            "kanban: %s is an external-repo task (%r) whose root is not configured "
            "(%s unset) — parking. It is NEVER built inside ICDev.",
            task_id, _repo_target.name, f"root_env for {_repo_target.name}",
        )
        try:
            _move_task(
                task_id, "validating", actor="repo-aware-guard",
                reason=(f"external repo {_repo_target.name!r}: repo root not configured; "
                        "parked rather than built inside ICDev"),
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # ── Respawn guard 1: recent success ───────────────────────────────────────
    # Don't re-dispatch a task that completed successfully within the last 30 min.
    # Catches stale DB reads where the executor loops and picks up an already-done task.
    if _had_recent_success(task_id, within_minutes=30):
        logger.info("Respawn guard: %s completed recently — skipping dispatch", task_id)
        return

    # ── Respawn guard 2: open PR exists ──────────────────────────────────────
    # Don't re-dispatch if an open PR for kanban/<task_id> already exists.
    # The executor's merge logic handles the PR — re-dispatch would create
    # a second competing implementation on a new commit.
    if _has_open_pr(task_id):
        logger.info("Respawn guard: open PR found for %s — skipping dispatch", task_id)
        return

    # ── Per-task circuit breaker ──────────────────────────────────────────────
    # Auto-block if failure_count has reached max_retries for this task.
    # Overrides the global decomposition threshold for tasks that explicitly
    # set a different cap.
    _task_max_retries = int(task.get("max_retries") or 5)
    _task_failures = int(task.get("failure_count") or 0)
    if _task_failures >= _task_max_retries:
        logger.warning(
            "Circuit breaker: %s hit max_retries=%d (failure_count=%d) — blocking",
            task_id, _task_max_retries, _task_failures,
        )
        try:
            conn = get_connection()
            conn.execute(
                "UPDATE kanban_tasks SET status = 'token_exhausted', "
                "last_failure_reason = %s, updated_at = %s WHERE id = %s",
                (
                    f"Circuit breaker: failure_count {_task_failures} >= max_retries {_task_max_retries}",
                    _utcnow_iso(),
                    task_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        return

    # Fast-path: auto-complete false-positive gaps without dispatching.
    already_resolved, resolution_reason = _pre_dispatch_check(task)
    if already_resolved:
        logger.info("kanban: %s auto-resolved pre-dispatch: %s", task_id, resolution_reason)
        _write_verification_log(task_id, True, f"AUTO-RESOLVED (pre-dispatch): {resolution_reason}")
        try:
            # Its OWN actor (kpr-rvfy-04). This completion's claim is "there was
            # nothing to build", and `_pre_dispatch_check` proves it by
            # re-deriving the desired state — the tool IS in the manifest, the
            # route IS in the Pages list. That is a different claim from "the
            # work was delivered", and the delivery-evidence gate would
            # otherwise refuse it for lacking a branch it was never going to
            # have. Declaring the path is honest where hiding inside
            # `scheduler` was not: 18 of the 32 fires in that gate's survey were
            # this path, unlabelled.
            _move_task(task_id, "done", actor="pre_dispatch_resolver",
                       reason=f"auto-resolved pre-dispatch: {resolution_reason}")
        except Exception:
            pass
        return  # No notification — false-positive resolves are scheduler noise

    prompt_text = Path(prompt_path).read_text(encoding="utf-8")

    # Create isolated worktree for this task (in ITS repo — see _task_repo_root)
    worktree_path = _create_worktree(task_id)
    _is_external = _repo_target is not None and _repo_target.is_external

    if not worktree_path and _is_external:
        # The BASE_DIR fallback below is the whole hazard this change exists to
        # remove: it would build compass work inside the ICDev checkout. For an
        # external task there is NO fallback — fail the dispatch instead.
        logger.error(
            "kanban: worktree creation failed for external task %s (%r) — NOT falling "
            "back to the ICDev tree. Parking.", task_id, _repo_target.name,
        )
        try:
            _move_task(
                task_id, "validating", actor="repo-aware-guard",
                reason=(f"external repo {_repo_target.name!r}: worktree creation failed; "
                        "refusing to build it inside ICDev"),
            )
        except Exception:  # noqa: BLE001
            pass
        return

    if not worktree_path:
        # NO TASK BUILDS IN THE SHARED CHECKOUT (autonomy-adm-02).
        #
        # This used to read `work_dir = worktree_path if worktree_path else
        # str(BASE_DIR)`, so an INTERNAL task whose worktree could not be created
        # printed one line and dispatched an autonomous worker into BASE_DIR —
        # the working directory concurrent sessions share. The external branch
        # directly above has refused exactly that since it was written; only the
        # internal half was left open, and it fired on 2026-08-20 for rem-hyg-18.
        #
        # The harm is documented one screen up in this very module:
        # `_create_worktree`'s stale-branch cleanup exists because a failure there
        # was "forcing every subsequent dispatch into BASE_DIR — causing the
        # coherence loop". Beyond that, a second session's `git checkout` moves
        # HEAD under the worker, so its commits land on the wrong branch.
        #
        # FAIL-CLOSED: a task that cannot be isolated does not build. Parking is
        # strictly better than the silent downgrade it replaces — the task stays
        # VISIBLE in `validating` instead of quietly corrupting a shared tree.
        # Do NOT "fix" a recurring park by retrying until creation succeeds; that
        # hides the cause. `_create_worktree` already logs why it failed.
        logger.error(
            "kanban: worktree creation failed for %s — NOT falling back to the "
            "shared checkout at %s. Parking.", task_id, BASE_DIR,
        )
        try:
            _move_task(
                task_id, "validating", actor="worktree-isolation-guard",
                reason=("worktree creation failed; refusing to build in the shared "
                        "checkout (see the git worktree add failure logged above)"),
            )
        except Exception:  # noqa: BLE001
            # The park itself failing must not become a reason to dispatch — the
            # safe outcome is still "no worker in the shared tree".
            logger.exception("kanban: could not park %s after worktree failure", task_id)
        return

    work_dir = worktree_path
    _worktrees[task_id] = worktree_path
    print(f"  Kanban: using worktree {worktree_path} for {task_id}")

    # FIX: Capture main HEAD at dispatch time. Verification uses this as the
    # baseline so that agent commits are visible even if main advances
    # (another task merges, auto-commit runs, etc.). Previously verification
    # used `git log main..kanban/branch` with CURRENT main, which went empty
    # once the agent's work was absorbed into main, causing false "no commits"
    # rejection.
    try:
        import subprocess as _sp
        head_proc = _sp.run(
            # The baseline for verification must come from the task's OWN repo:
            # ICDev's main head says nothing about whether compass advanced.
            ["git", "rev-parse", _task_base_branch(task_id)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_task_repo_root(task_id)), timeout=10,
        )
        if head_proc.returncode == 0:
            _dispatch_main_heads[task_id] = head_proc.stdout.strip()
    except Exception as exc:
        logger.debug("kanban: failed to capture main HEAD for %s: %s", task_id, exc)

    instruction = _build_instruction(task_id, title, prompt_text, prompt_path)
    task_log = PROMPT_DIR / f"{task_id}.log"

    try:
        import os as _os_chain  # noqa: PLC0415
        # Env-var override for quick mode switching (e.g. air-gap toggle).
        _env_chain = _os_chain.environ.get("ICDEV_KANBAN_EXECUTOR_CHAIN", "")
        if _env_chain:
            _fallback_chain = [x.strip() for x in _env_chain.split(",") if x.strip()]
        else:
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

    # Deterministic gate sub-tasks (auto-decomposed phase-exit gates) run their
    # tool natively instead of paying for an LLM dispatch around a subprocess.
    try:
        if _dispatch_via_tool_runner(task, work_dir, task_log):
            return
    except Exception as exc:
        logger.warning(
            "kanban: tool_runner dispatch failed for %s, falling back to LLM chain: %s",
            task_id, exc,
        )

    # D-AUTO-DEGRADE: Build effective chain skipping degraded executors.
    # If all executors are degraded, fall back to the full chain anyway.
    effective_chain = _build_effective_executor_chain(_fallback_chain)

    # Adapter-backed tiers (claude_cli, local_agent) resolve ONCE, through
    # tools/agents/registry.pick_default(), so ICDEV_AGENT_ADAPTER is honoured
    # here and not only by whatever else happens to call the registry. Order
    # still comes from the executor chain, so the default is unchanged.
    chain_adapter = _pick_chain_adapter(effective_chain, task_type)
    adapter_forced = bool(_agent_adapter_override())

    dispatched = False
    # kax-exec-01: record why EACH tier was not used. The reason this replaces
    # was a string literal ("internet=False, gitlab=unreachable,
    # ollama=unreachable") that nothing measured, so two unrelated incidents —
    # the 2026-08-01 PATHEXT resolution failure (25 tasks) and the 2026-08-12
    # executor degrade (2 tasks) — produced the identical sentence and each
    # needed its own investigation. A constant cannot discriminate causes.
    tier_outcomes: List[str] = []
    for tier in effective_chain:
        if tier in _ADAPTER_TIERS:
            if chain_adapter is None:
                tier_outcomes.append(f"{tier}=no adapter resolved")
                continue
            # Without a forced override the adapter must be THIS tier's, or the
            # chain order would be silently reshuffled (a later adapter tier
            # jumping ahead of gitlab). With one, the operator's choice runs at
            # the first adapter position in the chain.
            if not adapter_forced and _ADAPTER_TIERS[tier] != chain_adapter.name:
                tier_outcomes.append(
                    f"{tier}=skipped (chain adapter is {chain_adapter.name})"
                )
                continue
            if _dispatch_via_agent_adapter(chain_adapter, task, prompt_path,
                                           instruction, work_dir, task_log):
                _set_executor_type(task_id, chain_adapter.name)
                _tiers_ever_dispatched.add(tier)
                dispatched = True
                break
            tier_outcomes.append(f"{tier}=adapter declined")
        elif tier == "gitlab":
            ok = _dispatch_gitlab(task_id, task_desc, task_type)
            if ok:
                _dispatch_times[task_id] = datetime.now(timezone.utc)
                _set_executor_type(task_id, "gitlab")
                _tiers_ever_dispatched.add(tier)
                print(f"  Kanban: dispatched {task_id} via GitLab CI pipeline")
                dispatched = True
                break
            tier_outcomes.append("gitlab=dispatch returned False")
        elif tier == "github_actions":
            ok = _dispatch_github_actions(task_id, task_desc, task_type)
            if ok:
                _dispatch_times[task_id] = datetime.now(timezone.utc)
                _set_executor_type(task_id, "github_actions")
                # Move to in_progress immediately — GA is async and the
                # _github_actions_dispatched in-memory set is lost on restart.
                _move_task(task_id, "in_progress",
                           reason="dispatched via GitHub Actions (async executor)")
                _github_actions_dispatched.add(task_id)
                _tiers_ever_dispatched.add(tier)
                print(f"  Kanban: dispatched {task_id} via GitHub Actions → in_progress")
                dispatched = True
                break
            tier_outcomes.append("github_actions=dispatch returned False")
        elif tier == "ollama_local":
            ok = _dispatch_ollama_local(task_id, task_desc, task_type)
            if ok:
                _set_executor_type(task_id, "ollama_local")
                _ollama_completed.add(task_id)
                _tiers_ever_dispatched.add(tier)
                print(f"  Kanban: dispatched {task_id} via Ollama local")
                dispatched = True
                break
            tier_outcomes.append("ollama_local=dispatch returned False")
        else:
            tier_outcomes.append(f"{tier}=unknown tier")

    # Tiers dropped from the configured chain before the loop even ran are part
    # of the answer too — "claude_cli is not in effective_chain" is exactly the
    # fact that was invisible on 2026-08-12.
    for tier in _fallback_chain:
        if tier not in effective_chain:
            why = "degraded" if tier in _degraded_executors else "removed from chain"
            tier_outcomes.append(f"{tier}={why}")

    if not dispatched:
        if _fallback_chain and _fallback_chain[-1] == "ollama_local":
            _no_exec_reason = "no executor available: " + ", ".join(
                tier_outcomes or ["effective chain was empty"]
            )
            try:
                with get_connection() as _conn:
                    _conn.execute(
                        "UPDATE kanban_tasks SET last_failure_reason = %s, "
                        "updated_at = %s WHERE id = %s",
                        (_no_exec_reason, _utcnow_iso(), task_id),
                    )
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


def _fetch_origin_main_quiet() -> None:
    """Best-effort single lightweight fetch of origin/main. Never raises.

    Called at most once per _verify_claimed_files_exist invocation, and only
    when at least one path is missing on disk — the hot path (everything found
    locally) never touches the network.
    """
    import subprocess as _sp

    try:
        _sp.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=15,
        )
    except Exception:
        pass


def _path_in_origin_main(rel: str) -> bool:
    """True if *rel* exists in the ``origin/main`` git tree.

    origin/main is the source of truth for merged work. A task whose branch
    merged and whose worktree was removed leaves no on-disk trace when the
    shared checkout is itself stale/behind origin/main — but the file is still
    in the origin/main tree. Windows backslashes are normalised to forward
    slashes for the git pathspec.
    """
    import subprocess as _sp

    pathspec = str(rel).replace("\\", "/").strip()
    if not pathspec:
        return False
    try:
        r = _sp.run(
            ["git", "cat-file", "-e", f"origin/main:{pathspec}"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _verify_claimed_files_exist(
    paths: list[str], work_dir: Path | str
) -> tuple[int, int, list[str]]:
    """Check how many agent-claimed paths actually exist.

    Returns (existing_count, claimed_count, missing_paths[:5]).
    Paths are resolved relative to work_dir AND BASE_DIR — the agent
    may have run in a worktree OR the main checkout. A path not found on
    disk is then checked against the ``origin/main`` git tree: merged work
    whose worktree was removed leaves no on-disk trace when the shared
    checkout is stale/behind origin/main, so origin/main (the source of
    truth for merged work) is treated as existence. A single best-effort
    ``git fetch origin main`` refreshes the ref, run ONLY when something is
    missing on disk so the hot path stays network-free.
    """
    if not paths:
        return 0, 0, []
    work = Path(work_dir) if work_dir else BASE_DIR
    existing = 0
    on_disk_missing: list[str] = []
    for rel in paths:
        # Try work_dir first (worktree), fall back to BASE_DIR
        candidates = [work / rel, BASE_DIR / rel]
        if any(c.exists() for c in candidates):
            existing += 1
        else:
            on_disk_missing.append(rel)

    if not on_disk_missing:
        return existing, len(paths), []

    # Something is missing on disk — refresh origin/main once, then treat
    # presence in the origin/main tree as existence (merged, worktree gone).
    _fetch_origin_main_quiet()
    missing: list[str] = []
    for rel in on_disk_missing:
        if _path_in_origin_main(rel):
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
        with get_connection() as _c:
            row = _c.execute(
                "SELECT task_type, description FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
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


def _scan_result_artifact(task_id: str):
    """The scan task's result file in ``.tmp/``, or None.

    Scan-only tasks (codelens, coherence, health_check) write their report to
    ``.tmp/`` rather than committing anything, so this file is the one durable
    trace that the underlying command actually RAN. It survives the agent being
    killed, which is what makes it usable as evidence at a timeout — unlike
    stdout, which Claude CLI only writes at exit and which is therefore empty
    for exactly the runs that need evidence most.

    Extracted from the inline lookup in ``_run_verify_checks`` Fallback D so the
    timeout path and the verification path agree on what counts as proof;
    the globs and the id-suffix strip are unchanged.
    """
    tmp_dir = BASE_DIR / ".tmp"
    id_prefix = re.sub(r"-(codelens|coherence|e2e|scan)$", "", task_id)
    try:
        artifacts = (
            list(tmp_dir.glob(f"codelens-{task_id}*.json"))
            + list(tmp_dir.glob(f"codelens-{id_prefix}*.json"))
        )
    except OSError:  # unreadable .tmp — absence of proof, not proof
        return None
    return artifacts[0] if artifacts else None


def _dir_owns_its_repo_root(work_dir: str) -> bool:
    """True when ``work_dir`` IS a git repo/worktree root, not a dir inside one.

    git has no "this must be a worktree" assertion. A worktree that is pruned or
    removed mid-run leaves its files behind as an ordinary directory, and because
    the kanban worktree base lives under gitignored ``.tmp/``, git does not error
    there — it walks UP to the parent repo and answers for the SHARED CHECKOUT.
    Any ``git status`` run in such a directory describes BASE_DIR's dirty state
    while looking exactly like the task's own output.

    Comparing ``rev-parse --show-toplevel`` to the directory itself is what tells
    the two apart: a real worktree reports itself, a fallen-through leftover
    reports its parent repo. Resolved on both sides so a symlinked or
    differently-cased temp path does not read as a mismatch.
    """
    import subprocess as _sp

    if not work_dir:
        return False
    try:
        r = _sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=work_dir, timeout=10,
        )
    except Exception:  # noqa: BLE001 — git missing/timeout: cannot prove it, so don't
        return False
    top = (r.stdout or "").strip()
    if r.returncode != 0 or not top:
        return False
    try:
        return Path(top).resolve() == Path(work_dir).resolve()
    except Exception:  # noqa: BLE001 — unresolvable path is not a worktree root
        return False


def _git_worktree_has_real_changes(task_id: str,
                                   committed_only: bool = False) -> Tuple[bool, str]:
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

    ``committed_only`` DROPS arm 2 (kpr-rvfy-04). The three arms answer two
    different questions and the callers want different ones:

      * "did anything happen here?" — all three arms. A dirty worktree is the
        right answer for :func:`timeout_demotion_skip_reason`, which is
        deciding whether demoting a timed-out task would throw work away.
      * "was this task DELIVERED?" — arms 1 and 3 only. An uncommitted change
        is evidence the agent WORKED, and none at all that it delivered: a
        worktree is torn down, and what was in it is then simply gone.

    ``_run_verify_checks``'s own docstring has said "uncommitted changes alone
    are NOT evidence of completion" since the dirty fallback was removed from
    check 5 — and check 0, which runs FIRST, reinstated it. Measured 2026-08-29:
    four of five falsely-completed ``ftp-*`` tasks were verified on this arm,
    among them ``ftp-ezb-06``, marked done on 24 uncommitted changes while its
    worker was still running pytest. That session exited without committing;
    ``kanban/ftp-ezb-06`` carries no commits and ``git fsck`` finds no dangling
    one, so the work is gone. The false ``done`` also removed the pressure that
    would have caught the loss.

    SURVEYED before narrowing, as this repo requires: over the last 498
    scheduler ``verified:`` completions the arms split 55.6% branch commits,
    18.9% main advanced, 22.5% not git-first, and **3.01% this arm alone**. A
    task that now falls through runs the full check chain, and if that fails it
    is RETRIED with its worktree preserved — the direction that keeps the work.

    Returns ``(ok, reason)``. On git failure or no evidence, ``(False, "")``.
    """
    import subprocess as _sp

    branch_name = f"kanban/{task_id}"
    work_dir = _work_dir_for(task_id)
    dispatch_baseline = _dispatch_main_heads.get(task_id, None)
    default_branch_for_task = _task_base_branch(task_id)

    # 1. branch commits with file changes since dispatch
    #
    # `--not origin/<default>` is load-bearing (kpr-rvfy-04). Without it the
    # range is `<main at dispatch>..<branch>`, which counts every commit MAIN
    # itself gained while the task ran — other tasks' merged work — whenever the
    # branch sits at or below the current default branch. Measured 2026-08-29:
    # ftp-prd-07 was marked done on "18 file(s) changed on kanban/ftp-prd-07"
    # while that branch was 0 commits ahead of origin/main and its declared
    # deliverable (icdev_fin/fathomdesk/alert_delivery.py) was absent from the
    # tree. Excluding what is already on the default branch leaves exactly the
    # commits this task contributed; work that has since MERGED stops matching
    # here and is picked up by arm 3 below, which is the case that arm is for.
    if dispatch_baseline:
        try:
            r = _sp.run(
                ["git", "log", f"{dispatch_baseline}..{branch_name}",
                 "--not", f"origin/{default_branch_for_task}",
                 "--name-only", "--pretty=format:"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=str(_task_repo_root(task_id)), timeout=10,
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
    #
    # `task_id in _worktrees` does not establish that on its own. It proves the
    # runner RECORDED a path, not that the path is still its own worktree — and
    # a leftover under gitignored `.tmp/` answers for the SHARED CHECKOUT rather
    # than failing (see _dir_owns_its_repo_root). That is the same false
    # positive the note above intends to prevent, arriving through a door the
    # membership test does not cover.
    #
    # Observed 2026-08-11 (hgx-vv-01): the gate accepted "8 uncommitted
    # change(s) in worktree" where all 8 were BASE_DIR's companion-sync files
    # (.amazonq/mcp.json, .cline/mcp_settings.json, …) — dirty for hours before
    # that task was dispatched and belonging to no task. It was marked done on
    # work that never reached a branch, and its card read 100%.
    #
    # Asserted HERE, at use, rather than at dispatch: removal happens mid-run,
    # so a start-of-task check would have passed and still let this through.
    if (not committed_only) and task_id in _worktrees and _dir_owns_its_repo_root(work_dir):
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
                cwd=str(_task_repo_root(task_id)), timeout=10,
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
       if the task branch has COMMITS the default branch does not, or the
       default branch advanced while the worktree stayed clean, trust the
       filesystem truth and return verified=True. Uncommitted changes do NOT
       satisfy it (kpr-rvfy-04) — they are evidence the agent worked, not that
       it delivered, and this check used to contradict check 5 below.
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
    #
    # `committed_only=True` (kpr-rvfy-04): the fast path may accept only
    # COMMITTED evidence. A dirty worktree says the agent worked, never that it
    # delivered, and a worktree is torn down — see
    # `_git_worktree_has_real_changes` for the four tasks that were completed on
    # it and the work that was lost. The DANGEROUS fail-fast below asks the
    # OTHER question ("is there any sign of activity at all?"), where a dirty
    # worktree is the right signal, so it keeps the broad form.
    _git_ok, _git_reason = _git_worktree_has_real_changes(task_id, committed_only=True)
    _is_dangerous = _is_dangerous_task(task_id)
    if _git_ok and not _is_dangerous:
        return True, f"Verified (git-first): {_git_reason}"
    # Dangerous task: git-first is a necessary condition but not sufficient.
    # Fall through to the full check chain; at the end we require the
    # git-first signal to have fired as well.
    if _is_dangerous and not _git_ok:
        _git_ok, _git_reason = _git_worktree_has_real_changes(task_id)
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
        with get_connection() as _c0b:
            _r0b = _c0b.execute(
                "SELECT description, task_type FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
        _desc0b = ((_r0b["description"] or "").lower() if _r0b else "")
        _type0b = ((_r0b["task_type"] or "").lower() if _r0b else "")
    except Exception:
        _desc0b = ""
        _type0b = ""
    _is_scan_task = _type0b == "test" and any(kw in _desc0b for kw in _SCAN_ONLY_KEYWORDS)
    if _is_scan_task and (not claude_output or len(claude_output) < MIN_OUTPUT_LENGTH):
        # Scan task with empty/short output — check process exit code
        _proc = _running.get(task_id)
        _exit_ok = (_proc is not None and hasattr(_proc, 'returncode')
                    and _proc.returncode == 0)
        _dispatch_t = _dispatch_times.get(task_id)
        _ran_long = (
            _dispatch_t is not None
            and (datetime.now(timezone.utc) - _dispatch_t).total_seconds() > SCAN_MIN_RUN_SECONDS
        )
        if _exit_ok:
            return True, (
                "Verified (scan-only): process exited 0 — "
                "no git commits expected for read-only validation task"
            )
        # Reached only when the process did NOT exit 0 and produced no usable
        # output — i.e. it crashed or was killed. Duration alone used to pass
        # here ("ran >60s without crash"), but a run that is long AND did not
        # exit cleanly is the signature of a kill, not of success; the same
        # reasoning that marked hgx-vv-01 done on a timeout. Require the scan's
        # own result artifact, which the command writes and which outlives the
        # kill. Without it, fall through to the normal verification chain —
        # Fallback D can still accept this task on a PASS signal, so a genuine
        # scan that printed its result is not penalised.
        _scan_artifact = _scan_result_artifact(task_id) if _ran_long else None
        if _scan_artifact is not None:
            return True, (
                f"Verified (scan-only): scan artifact {_scan_artifact.name} on "
                f"disk after a >{SCAN_MIN_RUN_SECONDS}s run (stdout lost due to "
                f"kill; no git commits expected)"
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
        try:
            # Primary signal: metadata flag on the task row — cheapest check.
            _bypass_meta = _c0c.execute(
                "SELECT completed_via_bypass FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
            if _bypass_meta and _bypass_meta["completed_via_bypass"]:
                return True, (
                    "Verified (bypass): completed_via_bypass flag set on task — "
                    "no git commits expected (pre-existing correct state)"
                )
            # Secondary signal: verification row written by the move-to-done API.
            _brow = _c0c.execute(
                "SELECT reason FROM kanban_verifications "
                "WHERE task_id = %s AND result = 'bypassed' "
                "ORDER BY verified_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        finally:
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
    if not claude_output or len(claude_output) < MIN_OUTPUT_LENGTH:
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
            with get_connection() as _c3:
                _r3 = _c3.execute(
                    "SELECT task_type FROM kanban_tasks WHERE id = %s", (task_id,)
                ).fetchone()
            _tt = (_r3["task_type"] or "").lower() if _r3 else ""
        except Exception:
            _tt = ""
        if _tt in _FILE_CREATION_TASK_TYPES:
            return False, "No evidence of file changes in output"

    # Check 4 — OPT-76 phantom guard: extract every path the agent
    # claims to have touched and verify at least SOME of them exist.
    work_dir_for_check = _work_dir_for(task_id)
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
            with get_connection() as _c:
                _row = _c.execute(
                    "SELECT description, task_type FROM kanban_tasks WHERE id = %s", (task_id,)
                ).fetchone()
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
        # If PHANTOM_RATIO_THRESHOLD+ of claimed paths are missing, treat as phantom.
        phantom_ratio = (claimed - existing) / claimed
        if phantom_ratio >= PHANTOM_RATIO_THRESHOLD:
            missing_preview = ", ".join(missing[:3])
            return False, (
                f"PHANTOM COMPLETION: {claimed - existing}/{claimed} claimed paths "
                f"missing ({missing_preview}). "
                f"Ratio {phantom_ratio:.0%} >= {PHANTOM_RATIO_THRESHOLD:.0%} threshold — failing."
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
            cwd=str(_task_repo_root(task_id)),
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
                    cwd=str(_task_repo_root(task_id)), timeout=10,
                )
                main_advanced = r2.stdout.strip()
                if main_advanced:
                    # Check if the worktree has uncommitted changes — if clean
                    # AND main advanced, the agent's work likely merged already.
                    work_dir_for_check = _work_dir_for(task_id)
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
            cwd=str(_task_repo_root(task_id)),
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
            with get_connection() as _c_sd:
                _r_sd = _c_sd.execute(
                    "SELECT description FROM kanban_tasks WHERE id = %s", (task_id,)
                ).fetchone()
            _scan_desc = ((_r_sd["description"] or "").lower() if _r_sd else "")
        except Exception:
            pass
        if any(cmd in _scan_desc for cmd in _SCAN_CMDS):
            # Strongest signal: result artifact file in .tmp/. Shared with the
            # timeout-acceptance path so both agree on what counts as proof.
            _artifact = _scan_result_artifact(task_id)
            if _artifact is not None:
                return True, f"Verified: scan artifact exists ({_artifact.name})"
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
            encoding="utf-8", newline="",
        )
    except Exception as exc:
        logger.warning(
            "kanban: failed to write verification log for %s: %s", task_id, exc
        )
    # Also write to kanban_verifications table (guard-5) for dashboard visibility
    try:
        import uuid as _uuid
        with get_connection() as conn:
            verification_id = f"kv-{_uuid.uuid4().hex[:10]}"
            result_enum = "passed" if verified else "failed"
            if "PHANTOM" in (reason or "").upper():
                result_enum = "phantom"

            # guard-23: read dispatch_source from task row (set at dispatch time)
            source_row = conn.execute(
                "SELECT dispatch_source FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            dispatch_source = (
                dict(source_row).get("dispatch_source") if source_row else None
            ) or "genesis_scheduler"  # scheduler-invoked verifications are scheduler by default

            conn.execute(
                "INSERT INTO kanban_verifications "
                "(id, task_id, verified_at, result, reason, dispatch_source) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    verification_id, task_id,
                    datetime.now(timezone.utc).isoformat(),
                    result_enum, reason, dispatch_source,
                ),
            )
    except Exception as exc:
        # Don't fail verification just because audit log is missing
        logger.debug("kanban: kanban_verifications write skipped: %s", exc)


def _tag_task_source(task_id: str, source: str) -> None:
    """Set the dispatch_source on a kanban_tasks row (guard-23)."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE kanban_tasks SET dispatch_source = %s WHERE id = %s",
                (source, task_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("kanban: tag dispatch_source skipped for %s: %s", task_id, exc)


# 6 platforms that were missing Karpathy principle headings and required explicit sync.
# Source: karpathy_sync coherence check — these files lacked the 5 canonical headings
# (State assumptions / Enumerate interpretations / Prefer simpler /
#  Bound your edit scope / Success criteria) when the check was introduced.
# Used by _verify_task_specific to confirm karpathy_sync tasks actually patched the files.
_KARPATHY_SYNC_MISSING_PLATFORMS: list[tuple[str, str]] = [
    ("cline",    ".clinerules"),
    ("cursor",   ".cursor/rules/icdev.mdc"),
    ("windsurf", ".windsurf/rules/icdev.md"),
    ("copilot",  ".github/copilot-instructions.md"),
    ("amazonq",  ".amazonq/rules/icdev.md"),
    ("junie",    ".junie/guidelines.md"),
]

_KARPATHY_CANONICAL_HEADINGS: list[str] = [
    "State assumptions",
    "Enumerate interpretations",
    "Prefer simpler",
    "Bound your edit scope",
    "Success criteria",
]


def _verify_task_specific(task_id: str) -> Tuple[bool, str]:
    """Task-type-specific verification based on description keywords.

    Parses the task description and runs targeted checks:
    - "manifest" / "tool_not_in_manifest" → grep tools/manifest.md for the tool path
    - "route" / "page" / "start.md Pages" → grep start.md Pages line
    - "table" / "schema" / "migration" → query DB for table existence
    - "template" / ".html" → check template file exists
    - "karpathy_sync" / "karpathy headings" → verify _KARPATHY_SYNC_MISSING_PLATFORMS
    - "[Batch]" title → reject — batch cards must be decomposed first

    Returns (True, reason) if specific checks pass or don't apply.
    Returns (False, reason) if a targeted check fails.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT title, description FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
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
                        cwd=str(_task_repo_root(task_id)), timeout=10,
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
                                cwd=str(_task_repo_root(task_id)), timeout=10,
                            )
                            if sr.returncode == 0:
                                chunks.append(sr.stdout)
                        manifest_text = "\n".join(chunks)
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
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
        else:
            route = _nlp_extract_gap_subject(title, description, "route_not_listed")
        if route:
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
        else:
            table_name = _nlp_extract_gap_subject(title, description, "orphan_db_table")
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
        # Only invoke LLM when the task text contains DB-related keywords;
        # avoids unnecessary LLM calls for completely unrelated tasks.
        _DB_HINTS = {"table", "schema", "migration", "create", "db_table", "init_db"}
        if not table_name and any(h in desc_lower for h in _DB_HINTS):
            table_name = _nlp_extract_gap_subject(title, description, "db_table")
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
                    cwd=str(_task_repo_root(task_id)), timeout=15,
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
                        cwd=str(_task_repo_root(task_id)), timeout=15,
                    )
                    found = r.returncode == 0 and bool(r.stdout.strip())
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
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
            with get_connection() as conn:
                _pg = getattr(conn, "_backend", "sqlite") == "postgresql"
                if _pg:
                    check = conn.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = %s",
                        (table_name,),
                    ).fetchone()
                else:
                    # pg-portability: sqlite-only path — reached only when the
                    # backend is SQLite (PG uses the information_schema branch above).
                    check = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = %s",
                        (table_name,),
                    ).fetchone()
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
    from tools.workflow.validated_commit import validate_working_tree, _pipeline_enforce

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
            ["git", "diff", "--name-only", f"{_default_base_ref()}...{branch_name}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_task_repo_root(task_id)), timeout=15,
        )
        modified = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        modified = []

    # Defence in depth against a diff that is too large to be a real task.
    # An over-long list does not fail loudly — it fails SILENTLY, three ways:
    #   * _run_codelens builds `py_compile <paths>` / `ruff check <paths>` as
    #     literal argv; past Windows' 32767-char command line the call raises,
    #     is caught and only logged, and CodeLens degrades to a PASS.
    #   * _run_pytest selects every changed tests/* path under a <=120s cap, so
    #     it always TimeoutExpires and is recorded as "not run".
    #   * ui_touched matches almost any dashboard template, so E2E runs on every
    #     task and exhausts the verification budget.
    # Three gates quietly answering "fine" is far worse than one loud failure.
    if len(modified) > _MAX_CHANGED_FILES_FOR_GATES:
        logger.error(
            "guard-7: %s has %d changed files vs %s — refusing to run the gates "
            "on a diff this size; the base ref is probably wrong",
            task_id, len(modified), _default_base_ref(),
        )
        _oversize: Dict[str, Any] = {
            "codelens_passed": None, "ruff_issues": 0, "bandit_issues": 0,
            "coherence_passed": None, "e2e_ran": False, "e2e_passed": None,
            "companion_synced": False, "modified_files": len(modified),
            "modified_py": 0, "budget_sec": 0, "elapsed_sec": 0,
        }
        return False, (
            f"changed-file set is implausibly large ({len(modified)} files vs "
            f"{_default_base_ref()}) — gates not run"
        ), _oversize

    passed, reason, metrics = validate_working_tree(
        cwd=cwd,
        modified_files=modified,
        compare_to_main=True,
        run_e2e=True,
        run_companion=True,
    )

    # Conformance Review gate (Governed Delivery Pipeline Phase 2). Needs the
    # task's acceptance_criteria (which validate_working_tree doesn't see), so it
    # runs here. RECORD-ONLY by default — it only blocks completion when
    # KANBAN_PIPELINE_ENFORCE is on. Best-effort: never let it crash the gate.
    try:
        import json as _json
        from tools.testing.conformance_reviewer import review_conformance
        cr = review_conformance(task_id, changed_files=modified)
        metrics["review_passed"] = cr.get("review_passed")
        metrics["review_findings"] = _json.dumps(cr.get("findings") or [])[:4000]
        if passed and cr.get("review_passed") is False and _pipeline_enforce():
            passed = False
            reason = f"conformance review failed: {cr.get('reason', '')}"
    except Exception as _cexc:
        logger.debug("conformance review skipped for %s: %s", task_id, _cexc)

    return passed, reason, metrics


def _update_verification_metrics(task_id: str, metrics: Dict[str, Any]) -> None:
    """Update the latest kanban_verifications row for this task with post-task metrics."""
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE kanban_verifications SET "
                "codelens_passed = %s, ruff_issues = %s, bandit_issues = %s, "
                "pytest_passed = %s, pytest_ran = %s, failed_tests = %s, "
                "coherence_passed = %s, coherence_violations = %s, "
                "e2e_ran = %s, e2e_passed = %s, companion_synced = %s, "
                "review_passed = %s, review_findings = %s "
                "WHERE task_id = %s AND id = ("
                "  SELECT id FROM kanban_verifications WHERE task_id = %s "
                "  ORDER BY verified_at DESC LIMIT 1)",
                (
                    1 if metrics.get("codelens_passed") else 0 if metrics.get("codelens_passed") is False else None,
                    metrics.get("ruff_issues", 0),
                    metrics.get("bandit_issues", 0),
                    1 if metrics.get("pytest_passed") else 0 if metrics.get("pytest_passed") is False else None,
                    1 if metrics.get("pytest_ran") else 0,
                    metrics.get("failed_tests"),
                    1 if metrics.get("coherence_passed") else 0 if metrics.get("coherence_passed") is False else None,
                    metrics.get("coherence_violations"),
                    1 if metrics.get("e2e_ran") else 0,
                    1 if metrics.get("e2e_passed") else 0 if metrics.get("e2e_passed") is False else None,
                    1 if metrics.get("companion_synced") else 0,
                    1 if metrics.get("review_passed") else 0 if metrics.get("review_passed") is False else None,
                    metrics.get("review_findings"),
                    task_id, task_id,
                ),
            )
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

    work_dir = _work_dir_for(task_id)

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
                    ["git", "diff", "--name-only", f"{_default_base_ref()}...kanban/{task_id}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=str(_task_repo_root(task_id)), timeout=15,
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
    # PEV (agx-verify-03): record a three-valued step verdict alongside the
    # boolean done-gate. Opt-in (ICDEV_KANBAN_PEV, default off) and ADDITIVE —
    # it never changes `verified`, so the terminal merge-verify done-gate is
    # unweakened. Wrapped so a trail-write issue can never break the runner.
    try:
        from tools.kanban.pev import record_completion_pev
        record_completion_pev(task_id, verified=verified, reason=reason, metrics=metrics)
    except Exception:  # noqa: BLE001 — PEV is best-effort telemetry
        pass
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

# Override via KANBAN_SILENT_DISPATCH_THRESHOLD_SECONDS / KANBAN_ABSOLUTE_MAX_IN_PROGRESS_SECONDS env vars.
#
# This was 60s and it was the single largest source of task failures: 31 of 182
# recorded failures were "stale-reaper: ... silent-dispatch (no log output)".
# An LLM dispatch routinely produces no stdout for minutes while the model
# thinks, so an empty log after one minute is not evidence of a dead process.
# Liveness is now carried by last_heartbeat_at (refreshed every scheduler cycle
# for every live subprocess, see _refresh_running_heartbeats); this threshold is
# only the fallback for a task that never recorded a heartbeat at all.
_SILENT_DISPATCH_THRESHOLD = _int_env("KANBAN_SILENT_DISPATCH_THRESHOLD_SECONDS", 10 * 60)

def _parse_utc_timestamp(raw) -> Optional[datetime]:
    """A UTC-aware datetime, or None. Thin alias over the shared helper.

    The implementation moved to `tools.common.helpers` (tsg-iso-03) so the
    notification service could stop importing `dateutil` for the same job. The
    name stays here because the reaper's tests pin it, and because a second
    implementation of "read a timestamp" is how the two would drift.
    """
    from tools.common.helpers import parse_utc_timestamp

    return parse_utc_timestamp(raw)


_ABSOLUTE_MAX_IN_PROGRESS_SECONDS = _int_env("KANBAN_ABSOLUTE_MAX_IN_PROGRESS_SECONDS", 24 * 60 * 60)

# Anomaly detection parameters for _detect_execution_anomaly.
# Override via env vars to tune sensitivity without code changes.
_ANOMALY_HISTORY_LIMIT = _int_env("KANBAN_ANOMALY_HISTORY_LIMIT", 50)
_ANOMALY_MIN_SAMPLES = _int_env("KANBAN_ANOMALY_MIN_SAMPLES", 10)
_ANOMALY_MIN_STD_SECONDS = _float_env("KANBAN_ANOMALY_MIN_STD_SECONDS", 1.0)
_ANOMALY_Z_THRESHOLD = _float_env("KANBAN_ANOMALY_Z_THRESHOLD", 2.0)


def _task_log_is_empty(tid: str) -> bool:
    """Return True if the task's .tmp/kanban/<id>.log is absent or has no content."""
    log_path = Path(__file__).resolve().parent.parent.parent / ".tmp" / "kanban" / f"{tid}.log"
    try:
        return not log_path.exists() or log_path.stat().st_size == 0
    except Exception:
        return False


# Clause prefixes that describe SUCCESS, not failure. A pipeline narrative
# frequently leads with one of these; storing it as the failure reason is what
# made 41% of last_failure_reason rows useless for triage.
_SUCCESS_CLAUSE_PREFIXES = (
    "verified (",
    "verified:",
    "auto-remediated",
    "remediation=",
    "passed",
    "all validation gates passed",
    # _verify_task_specific's non-failure default (see its final return).
    # It starts with "Task-specific", not "passed", so it slipped through
    # this filter and was stored as the failure clause — which is how a task
    # with nothing wrong with it reached failure_triage's autofix queue on
    # 2026-08-08 (kax-recover-01). Its FAILURE returns are prefixed
    # "SPECIFIC CHECK FAILED:" and are unaffected.
    "task-specific checks passed",
)


def _split_failure_narrative(reason: Optional[str]) -> Tuple[str, str]:
    """Split a pipeline narrative into (failure_clause, full_narrative).

    The narrative is pipe-joined across the verification stages. Returns the
    first clause that actually describes a failure, plus the untouched whole
    story for last_run_summary. When every clause reads as success the failure
    clause falls back to the whole string prefixed as unclassified — callers
    still record *something*, but it is visibly not a real diagnosis rather
    than silently masquerading as one.
    """
    narrative = (reason or "").strip()
    if not narrative:
        return "", ""
    clauses = [c.strip() for c in narrative.split("|") if c.strip()]
    for clause in clauses:
        low = clause.lower()
        if not any(low.startswith(p) for p in _SUCCESS_CLAUSE_PREFIXES):
            return clause, narrative
    logger.warning(
        "failure recorded with no failure clause — every clause reads as "
        "success: %s", narrative[:200],
    )
    return f"UNCLASSIFIED (no failure clause): {narrative}", narrative


_main_worktree_cache: Optional[Path] = None


def _main_worktree_root() -> Path:
    """The MAIN git worktree, which is the one whose state is shared.

    ``git worktree list --porcelain`` always names the main working tree first,
    regardless of which worktree we are running from. Cached: the answer cannot
    change within a process, and this is called on every reaper sweep.
    """
    global _main_worktree_cache
    if _main_worktree_cache is not None:
        return _main_worktree_cache
    root = BASE_DIR
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    root = Path(line[len("worktree "):].strip())
                    break
    except Exception as exc:
        logger.debug("kanban: main-worktree lookup failed, using BASE_DIR: %s", exc)
    _main_worktree_cache = root
    return root


def _foreign_scheduler_pid() -> int:
    """PID of a LIVE kanban scheduler that is not this process, or 0.

    ``_running`` is a module global, so it is per-process. Any second process
    that calls the kanban reflex's run() — the heartbeat daemon's wakeup, a
    dashboard-triggered reflex, an interactive `--once` — sees an empty
    ``_running`` and concludes that the real scheduler's live tasks are dead.
    Its reaper then resets them to backlog with failure_count++, and the real
    scheduler kills the orphaned subprocess as "stale-cleanup".

    Reuses the lockfile tools/genesis/kanban_scheduler.py already maintains
    rather than introducing a second ownership mechanism.

    The lockfile is resolved from the MAIN worktree, not from BASE_DIR. A
    dashboard started inside a git worktree spawns its own scheduler from that
    worktree, and such a process reading ``BASE_DIR/.tmp/`` would find its own
    private lockfile, see no foreign owner, and dispatch against the shared
    board anyway. Observed 2026-08-01: three worktree-spawned schedulers
    (tsh-e2e-01, aca-trn-04, and a nested aca-trn-03) running alongside the
    real one. They all write the SAME database, so ownership has to be asked
    of the one tree they share.
    """
    import os as _os

    lock_path = _main_worktree_root() / ".tmp" / "kanban_scheduler.pid"
    try:
        owner_pid = int(lock_path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0
    if owner_pid == _os.getpid():
        return 0
    try:
        import psutil as _ps

        if _ps.pid_exists(owner_pid):
            proc = _ps.Process(owner_pid)
            if "kanban_scheduler" in " ".join(proc.cmdline()):
                return owner_pid
    except Exception:
        pass
    return 0


def _refresh_running_heartbeats() -> int:
    """Stamp last_heartbeat_at for every task whose subprocess is still alive.

    Called once per scheduler cycle (≤ MAX_IN_PROGRESS rows, so it is cheap).
    This is what turns last_heartbeat_at into a real liveness signal instead of
    a column only the dashboard ever wrote: the reaper can then distinguish
    "the model is thinking and has not printed anything yet" from "the
    subprocess is gone", which an empty log file cannot do.

    Returns the number of tasks stamped. Never raises.
    """
    alive: List[str] = []
    for tid, proc in list(_running.items()):
        try:
            if proc is not None and proc.poll() is None:
                alive.append(tid)
        except Exception:  # noqa: BLE001 — a handle that can't be polled isn't proof of death
            continue
    if not alive:
        return 0
    try:
        now = _utcnow_iso()
        with get_connection() as conn:
            for tid in alive:
                conn.execute(
                    "UPDATE kanban_tasks SET last_heartbeat_at = %s WHERE id = %s",
                    (now, tid),
                )
    except Exception as exc:
        logger.debug("kanban: heartbeat refresh skipped: %s", exc)
        return 0
    return len(alive)


def _heartbeat_is_stale(tid: str, conn, threshold_seconds: float) -> bool:
    """True when this task has no usable heartbeat newer than *threshold_seconds*.

    A task that never sent a heartbeat is treated as stale — that is the
    pre-heartbeat case the age-based threshold already covers. A task with a
    heartbeat inside the window is alive and must never be reaped.
    """
    age = _heartbeat_age_seconds(tid, conn)
    if age is None:
        return True
    return age >= threshold_seconds


def _heartbeat_age_seconds(tid: str, conn) -> Optional[float]:
    """Seconds since this task's last heartbeat, or None if it never sent one."""
    try:
        row = conn.execute(
            "SELECT last_heartbeat_at FROM kanban_tasks WHERE id = %s", (tid,)
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    raw = dict(row).get("last_heartbeat_at")
    if not raw:
        return None
    try:
        beat = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - beat).total_seconds()
    except Exception:
        return None


def _task_dispatched_by_scheduler(tid: str, conn) -> bool:
    """Return False only if the most recent transition into in_progress for
    *tid* was explicitly recorded with a non-scheduler actor (e.g. 'manual',
    via tools/kanban/cli.py --set-status).

    The silent-dispatch fast-reap (_SILENT_DISPATCH_THRESHOLD, default 1 min)
    exists to catch a scheduler-spawned subprocess that died before writing
    any output — but an externally/manually managed task (an interactive
    session working a task directly, not through the scheduler's subprocess
    dispatch) also has an empty log by construction, since nothing ever
    writes one for it. Without this check the two are indistinguishable and
    genuinely-in-progress manual work gets reaped back to backlog within a
    minute regardless of how long the real work takes (observed 2026-07-08:
    6 tasks worked via isolated worktrees + manual CLI status updates were
    repeatedly bounced backlog<->in_progress by this fast path even though
    the work was already complete and merged).

    Defaults to True (preserve existing aggressive behavior) when no
    transition row exists or the audit table/query fails — this only ever
    *relaxes* the threshold for tasks explicitly marked manual, it never
    tightens it for anything else.
    """
    try:
        row = conn.execute(
            "SELECT actor FROM kanban_status_transitions "
            "WHERE task_id = %s AND to_status = 'in_progress' "
            "ORDER BY recorded_at DESC LIMIT 1",
            (tid,),
        ).fetchone()
        if not row:
            return True
        actor = dict(row).get("actor")
        return actor in (None, "scheduler")
    except Exception:
        return True


def _detect_execution_anomaly(age_seconds: float) -> Tuple[bool, str]:
    """Detect if age_seconds is anomalously long vs historical task durations.

    Queries the last _ANOMALY_HISTORY_LIMIT completed tasks and applies the
    z-score method (mean + _ANOMALY_Z_THRESHOLD × σ). Returns
    (is_anomaly, description). Falls back to (False, "") on insufficient data
    or any error. All parameters are configurable via env vars:
      KANBAN_ANOMALY_HISTORY_LIMIT, KANBAN_ANOMALY_MIN_SAMPLES,
      KANBAN_ANOMALY_MIN_STD_SECONDS, KANBAN_ANOMALY_Z_THRESHOLD.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""
                SELECT (julianday(completed_at) - julianday(created_at)) * 86400.0 AS dur
                FROM kanban_tasks
                WHERE status = 'done'
                  AND completed_at IS NOT NULL
                  AND created_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT {_ANOMALY_HISTORY_LIMIT}
                """
            ).fetchall()
        finally:
            conn.close()

        durations = [dict(r)["dur"] for r in rows if (dict(r).get("dur") or 0) > 0]
        if len(durations) < _ANOMALY_MIN_SAMPLES:
            return False, ""

        mean = sum(durations) / len(durations)
        variance = sum((d - mean) ** 2 for d in durations) / len(durations)
        std = variance ** 0.5
        if std < _ANOMALY_MIN_STD_SECONDS:
            return False, ""

        z = (age_seconds - mean) / std
        if z > _ANOMALY_Z_THRESHOLD:
            return True, (
                f"anomaly_detection: {age_seconds / 60:.0f} min is {z:.1f}σ above "
                f"historical mean {mean / 60:.0f} min (σ={std / 60:.0f} min, n={len(durations)})"
            )
    except Exception:
        pass
    return False, ""


_SUGGESTED_DECAY_HOURS = _int_env("KANBAN_SUGGESTED_DECAY_HOURS", 48)

#: How often the 'suggested' recovery sweep is allowed to run, in seconds.
#:
#: It used to be a COIN FLIP: the call site read ``if _rr.random() < 0.004``,
#: commented "once every ~4 h (1/240 cycles)". A probability is not a schedule —
#: 0.4% per cycle has an expected wait of ~250 cycles and NO UPPER BOUND, so a
#: task could sit far longer than four hours and nothing would be wrong.
#:
#: That mattered because of WHAT the sweep recovers. ``_unblock_dep_chain``
#: revives tasks parked with "no executor available" — a task that was never
#: attempted, carries failure_count = 0, and is parked for an infrastructure
#: reason ``failure_triage`` already classifies as "nothing in the repo is
#: broken". 369 such rows exist on this board. That function's own docstring
#: says the churn "is deliberate — it is cheap (a status write; no branch, no
#: PR, no merge)" and describes reviving "each cycle", so it was written to run
#: often; only the call site disagreed.
#:
#: A SHORTER INTERVAL CANNOT PROMOTE ANYTHING THAT WAS NOT ALREADY ELIGIBLE.
#: Each pass owns its own criteria and they are untouched here: the decay pass
#: still requires ``updated_at`` older than _SUGGESTED_DECAY_HOURS, the
#: quarantine revive still requires its cooldown and per-task cap, and
#: _unblock_dep_chain still only selects blocked or no-executor rows. The only
#: thing that changes is how promptly an ALREADY-eligible task is picked up.
#:
#: 300s matches the "five-minute executor degrade" the unblock docstring is
#: written around, and bounds the churn it warns about: a long outage now
#: revives-and-requarantines ~12 times an hour per task rather than ~250 times.
#: Measured cost of one sweep on this board: 6.5 ms (decay select) + 1.8 ms
#: (unblock select).
_SUGGESTED_SWEEP_INTERVAL_SEC = _int_env("KANBAN_SUGGESTED_SWEEP_INTERVAL_SEC", 300)

#: Monotonic timestamp of the last sweep. Process-local on purpose, matching
#: `_degraded_executors`: a fresh scheduler should sweep promptly rather than
#: inherit a suppression window from the process it replaced.
_last_suggested_sweep_at: float = 0.0


def _suggested_sweep_due(now: Optional[float] = None) -> bool:
    """True when the recovery sweep is due, and records that it ran.

    Deterministic and bounded, which a probability is not. Uses a monotonic
    clock so a system time change cannot postpone recovery indefinitely.
    """
    global _last_suggested_sweep_at
    current = time.monotonic() if now is None else now
    if current - _last_suggested_sweep_at < _SUGGESTED_SWEEP_INTERVAL_SEC:
        return False
    _last_suggested_sweep_at = current
    return True

#: Why a decay promotion happened. Recorded on the ``kanban_status_transitions``
#: row — never on ``last_failure_reason``, which is a *triage input*, not a
#: general-purpose note field (see ``_promote_stale_suggested``).
_DECAY_PROMOTION_REASON = (
    f"suggested-decay: re-queued after {_SUGGESTED_DECAY_HOURS} h in 'suggested' "
    "with no hard-quarantine signal (failure_count preserved)"
)


def _promote_stale_suggested() -> None:
    """Decay sweep: re-queue 'suggested' tasks that have been stuck >48 h
    and are NOT hard-quarantined (failure_count < 5 and last_failure_reason
    does not contain 'hard-quarantine' or 'hitl').

    Prevents tasks from rotting in 'suggested' forever when the underlying
    issue resolves on its own (transient resource exhaustion, flaky E2E,
    resolved dependency). Hard-quarantined tasks (fc >= 5 or explicit
    hard-quarantine reason) still require human review.

    The promotion goes through ``tools/kanban/requeue.py::requeue_task`` rather
    than a local UPDATE, because the local UPDATE had both of the failure modes
    that module exists to prevent (kax-recover-05):

    * It wrote the promotion *rationale* into ``last_failure_reason`` while
      setting ``status='scheduled'`` and a fresh ``updated_at`` — precisely the
      triple ``failure_triage.find_recent_failures`` selects on
      (``last_failure_reason IS NOT NULL AND updated_at > cutoff AND status IN
      (...,'scheduled',...)``). Every decay-promoted task therefore entered the
      autofix queue carrying a "reason" that describes a promotion rather than a
      failure; 114 rows on the live board still carry that string. The rationale
      belongs on the ``kanban_status_transitions`` row, which is an audit
      surface, not on a triage input.
    * It set ``failure_count=0`` while the guard below uses ``fc >= 5`` as the
      hard-quarantine test — so a task that passed through 'suggested' had its
      quarantine budget reset every 48 h and could never reach hard quarantine.
      ``requeue_task`` preserves the count on purpose: it is
      ``recovery_guard.py``'s budget and the task's real history. Preserving it
      cannot trip the dispatcher's circuit breaker here, because that fires at
      ``failure_count >= max_retries`` (default 5) and this pass only promotes
      tasks below 5.
    """
    try:
        from tools.kanban.requeue import requeue_task  # noqa: PLC0415

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_SUGGESTED_DECAY_HOURS)
        ).isoformat()
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, failure_count, last_failure_reason FROM kanban_tasks "
                "WHERE status = 'suggested' AND updated_at < %s",
                (cutoff,),
            ).fetchall()

        candidates = []
        for r in rows:
            d = dict(r)
            fc = d.get("failure_count") or 0
            reason = (d.get("last_failure_reason") or "").lower()
            if fc >= 5 or "hard-quarantine" in reason or "hitl" in reason:
                continue  # genuinely quarantined — leave for human review
            candidates.append(d["id"])

        # requeue_task opens its own connection, so the read above is closed
        # before promoting rather than nesting a second connection inside it.
        promoted = []
        for tid in candidates:
            outcome = requeue_task(
                tid,
                status="scheduled",
                reason=_DECAY_PROMOTION_REASON,
                actor="suggested-decay-sweep",
            )
            if outcome.get("requeued"):
                promoted.append(tid)
            else:
                logger.warning(
                    "suggested-decay: %s not re-queued: %s", tid, outcome.get("error"),
                )
        if promoted:
            logger.info("suggested-decay: re-queued %d task(s): %s", len(promoted), promoted)
            for tid in promoted:
                print(f"  Kanban: suggested-decay promoted {tid} -> scheduled")

        with get_connection() as conn:
            # ── BOUNDED AUTO-REVIVE of failure-quarantined tasks ──────────
            # The decay pass above deliberately skips fc>=5 / HITL-quarantined
            # tasks. Without this pass they (and their dependency chains) rot
            # in 'suggested' forever. Here we revive them to backlog when their
            # dependency is satisfied and they've cooled down — capped at
            # MAX_AUTO_REVIVE per task (tracked in kanban_task_revivals so the
            # cap survives re-quarantine), then held for HITL with one alert.
            _revive_quarantined_suggested(conn)
            # Critical-path unblock: revive low-fc 'suggested' tasks that are
            # directly blocking 'backlog' children (executor-transient failures).
            _unblock_dep_chain(conn)
    except Exception as exc:
        logger.warning("suggested-decay sweep failed: %s", exc)


def _revive_quarantined_suggested(conn: Any) -> None:
    """Auto-revive failure-quarantined 'suggested' tasks, bounded by a cap.

    A task qualifies as failure-quarantined when failure_count >= 5 OR its
    last_failure_reason mentions 'hitl' / 'hard-quarantine' (genuine AI
    prediction cards have fc=0 and no failure reason, so they never match).
    Such a task is revived to 'backlog' (failure_count reset) when:
      - its dependency is satisfied (no dep, or parent done/decomposed), and
      - it has been parked longer than QUARANTINE_REVIVE_COOLDOWN_MIN, and
      - it has been auto-revived fewer than MAX_AUTO_REVIVE times.
    After the cap it stays quarantined and fires a one-time HITL Telegram alert.
    """
    # Self-healing: create the side table if the migration hasn't run yet.
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kanban_task_revivals ("
            "  task_id TEXT PRIMARY KEY,"
            "  revive_count INTEGER NOT NULL DEFAULT 0,"
            "  last_revived_at TEXT,"
            "  hitl_alerted INTEGER NOT NULL DEFAULT 0,"
            "  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP))"
        )
        conn.commit()
    except Exception as exc:
        logger.warning("auto-revive: could not ensure kanban_task_revivals table: %s", exc)
        return

    now = datetime.now(timezone.utc)
    cooldown_cutoff = (now - timedelta(minutes=QUARANTINE_REVIVE_COOLDOWN_MIN)).isoformat()
    now_iso = now.isoformat()

    rows = conn.execute(
        "SELECT id, failure_count, last_failure_reason, depends_on_task_id "
        "FROM kanban_tasks "
        "WHERE status = 'suggested' AND updated_at < %s",
        (cooldown_cutoff,),
    ).fetchall()

    revived: list[str] = []
    revive_reasons: dict[str, str] = {}
    held: list[str] = []
    for r in rows:
        d = dict(r)
        tid = d["id"]
        fc = d.get("failure_count") or 0
        reason = (d.get("last_failure_reason") or "").lower()
        # Only act on failure-quarantined tasks (skip genuine prediction cards).
        is_quarantined = fc >= 5 or "hard-quarantine" in reason or "hitl" in reason
        if not is_quarantined:
            continue

        # Dependency must be satisfied (no dep, or parent done/decomposed).
        dep = d.get("depends_on_task_id")
        if dep:
            prow = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (dep,)
            ).fetchone()
            if not prow or dict(prow).get("status") not in ("done", "decomposed"):
                continue  # still blocked — leave quarantined

        # How many times have we already auto-revived this task?
        rc_row = conn.execute(
            "SELECT revive_count, hitl_alerted FROM kanban_task_revivals WHERE task_id = %s",
            (tid,),
        ).fetchone()
        revive_count = (dict(rc_row).get("revive_count") if rc_row else 0) or 0
        hitl_alerted = (dict(rc_row).get("hitl_alerted") if rc_row else 0) or 0

        if revive_count >= MAX_AUTO_REVIVE:
            # Cap reached — hold for human review, alert once.
            if not hitl_alerted:
                held.append(tid)
                conn.execute(
                    "UPDATE kanban_task_revivals SET hitl_alerted = 1, updated_at = %s "
                    "WHERE task_id = %s",
                    (now_iso, tid),
                )
            continue

        # Revive to backlog with a fresh failure budget.
        #
        # kax-recover-05: the rationale is NOT written to last_failure_reason.
        # That column plus a fresh updated_at plus status='backlog' is exactly
        # what failure_triage.find_recent_failures selects on, so describing a
        # revival there put every revived task straight into the autofix queue
        # carrying a non-failure "reason". It is cleared instead, and the
        # rationale goes on the kanban_status_transitions row below.
        #
        # failure_count IS still reset here, unlike the decay pass above. This
        # pass only acts on fc>=5 tasks, and the dispatcher's circuit breaker
        # blocks at fc >= max_retries (default 5) — preserving the count would
        # make the revival a no-op that re-parks the task immediately. The
        # budget that bounds this path is revive_count in kanban_task_revivals,
        # which survives re-quarantine; the failure count is not it.
        new_rc = revive_count + 1
        revive_reason = (
            f"auto-revive {new_rc}/{MAX_AUTO_REVIVE}: deps satisfied + cooled down, "
            "re-queued to backlog for another attempt."
        )
        conn.execute(
            "UPDATE kanban_tasks SET status = 'backlog', failure_count = 0, "
            "last_failure_reason = NULL, updated_at = %s "
            "WHERE id = %s AND status = 'suggested'",
            (now_iso, tid),
        )
        # Upsert the revival counter (works on both SQLite and PostgreSQL).
        if rc_row:
            conn.execute(
                "UPDATE kanban_task_revivals SET revive_count = %s, last_revived_at = %s, "
                "updated_at = %s WHERE task_id = %s",
                (new_rc, now_iso, now_iso, tid),
            )
        else:
            conn.execute(
                "INSERT INTO kanban_task_revivals "
                "(task_id, revive_count, last_revived_at, hitl_alerted, updated_at) "
                "VALUES (%s, %s, %s, 0, %s)",
                (tid, new_rc, now_iso, now_iso),
            )
        revived.append(tid)
        revive_reasons[tid] = revive_reason

    if revived or held:
        conn.commit()
    for tid in revived:
        # Recorded after the commit so the audit row never describes a revival
        # that did not land. This is where the rationale lives now.
        _record_status_transition(
            tid, "suggested", "backlog",
            actor="auto-revive", reason=revive_reasons[tid],
        )
        print(f"  Kanban: auto-revive quarantined {tid} -> backlog")
    if revived:
        logger.info("auto-revive: re-queued %d quarantined task(s): %s", len(revived), revived)
    if held:
        logger.warning(
            "auto-revive: %d task(s) hit revive cap (%d) — holding for HITL: %s",
            len(held), MAX_AUTO_REVIVE, held,
        )
        import os as _os
        if not (_os.environ.get("PYTEST_CURRENT_TEST")
                or _os.environ.get("ICDEV_SUPPRESS_NOTIFICATIONS") == "1"):
            for tid in held:
                try:
                    from tools.notifications.adapters.telegram import send as tg_send
                    tg_send(
                        f"HITL REVIEW NEEDED: {tid[:32]}",
                        (
                            f"Task '{tid}' has been auto-revived {MAX_AUTO_REVIVE} times and "
                            "still fails. It is now held in 'suggested' for human review — "
                            "the task likely needs to be split, fixed, or closed."
                        ),
                        severity="warning",
                    )
                except Exception:
                    pass


def _unblock_dep_chain(conn: Any) -> None:
    """Revive 'suggested' tasks parked by a transient executor failure.

    When an executor is transiently unavailable (e.g., claude_cli not on PATH
    during a previous scheduler run), tasks are moved to 'suggested' with
    reason "no executor available". The standard _revive_quarantined_suggested
    only targets fc>=5 tasks, leaving these low-fc transient failures stuck.

    TWO criteria, either of which revives (kax-exec-02):

    1. the task is the direct dependency of a task sitting in 'backlog' — a
       child waiting proves the parent is on the critical path;
    2. the task carries a "no executor available" reason at all.

    (2) exists because (1) alone leaves most of the board unprotected: it
    requires BOTH a child AND that child to be in 'backlog' specifically. On
    2026-08-12 that gap stranded two real tasks — exa-policy-08 is a leaf and
    could never match, and exa-live-01's only child was 'scheduled' rather than
    'backlog'. Both had to be revived by hand. Measured on the same board, 14 of
    24 exa-* tasks were leaves, so ~58% of that card could not self-recover from
    a five-minute executor degrade.

    Selection is keyed on the reason string, so rows that legitimately sit in
    'suggested' awaiting human triage — reflex-proposed cards, which carry a
    NULL reason — are never touched.

    Churn note: nothing increments failure_count on this quarantine path, so a
    genuinely long executor outage will revive-and-requarantine each cycle. That
    is deliberate — it is cheap (a status write; no branch, no PR, no merge) and
    strictly better than a task parking silently forever. The condition that
    used to CAUSE the outage is fixed separately at the degrade site.
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Criterion 1: blocking a backlog child. Criterion 2: parked by a
        # transient executor failure, whether or not anything depends on it.
        rows = conn.execute(
            "SELECT DISTINCT p.id, p.failure_count, p.last_failure_reason "
            "FROM kanban_tasks p "
            "JOIN kanban_tasks c ON c.depends_on_task_id = p.id "
            "WHERE p.status = 'suggested' AND c.status = 'backlog' "
            "UNION "
            "SELECT id, failure_count, last_failure_reason "
            "FROM kanban_tasks "
            "WHERE status = 'suggested' "
            "AND last_failure_reason LIKE '%no executor available%'"
        ).fetchall()
        unblocked: list[str] = []
        unblock_reasons: dict[str, str] = {}
        for r in rows:
            d = dict(r)
            tid = d["id"]
            fc = d.get("failure_count") or 0
            reason = (d.get("last_failure_reason") or "").lower()
            # Only auto-revive transient failures (no executor / low fc).
            # Hard-quarantined (fc>=5 without "no executor" reason) or HITL tasks
            # are left for _revive_quarantined_suggested which has the revive cap.
            is_hard_quarantine = fc >= 5 and "no executor" not in reason
            is_hitl = "hitl" in reason or "hard-quarantine" in reason
            if is_hard_quarantine or is_hitl:
                continue
            # kax-recover-05: rationale goes on the transition row, not into
            # last_failure_reason — writing it there made every unblocked task
            # match failure_triage.find_recent_failures with a reason that
            # describes an unblock rather than a failure. The failure_count
            # reset stays: this pass lets fc>=5 "no executor" tasks through, and
            # the dispatcher blocks at fc >= max_retries (default 5), so
            # preserving it would re-park the task the moment it was unblocked.
            unblock_reasons[tid] = (
                f"dep-chain-unblock: child waiting in backlog, revived from suggested (fc was {fc})"
            )
            conn.execute(
                "UPDATE kanban_tasks SET status = 'backlog', failure_count = 0, "
                "last_failure_reason = NULL, updated_at = %s "
                "WHERE id = %s AND status = 'suggested'",
                (now_iso, tid),
            )
            unblocked.append(tid)
        if unblocked:
            conn.commit()
            for tid in unblocked:
                _record_status_transition(
                    tid, "suggested", "backlog",
                    actor="dep-chain-unblock", reason=unblock_reasons[tid],
                )
                print(f"  Kanban: dep-chain-unblock {tid} -> backlog (was blocking child)")
            logger.info(
                "dep-chain-unblock: revived %d critical-path task(s): %s",
                len(unblocked), unblocked,
            )
    except Exception as exc:
        logger.warning("dep-chain-unblock: failed: %s", exc)
def _fire_task_subscriptions(task_id: str, event: str) -> None:
    """Fire webhook notifications for all subscriptions matching this event.

    Non-fatal: any individual webhook failure is logged but does not block
    the caller. Uses a 5-second connect timeout per target.
    """
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, channel, target, events FROM kanban_task_subscriptions "
            "WHERE task_id = %s",
            (task_id,),
        ).fetchall()
    except Exception:
        return
    finally:
        if conn:
            conn.close()

    import urllib.request as _urllib_req
    import json as _json

    for row in rows:
        d = dict(row)
        subscribed_events = [e.strip() for e in (d.get("events") or "").split(",")]
        if event not in subscribed_events:
            continue
        target = d.get("target", "")
        if not target:
            continue
        payload = _json.dumps({
            "task_id": task_id,
            "event": event,
            "subscription_id": d.get("id"),
            "channel": d.get("channel"),
        }).encode("utf-8")
        try:
            req = _urllib_req.Request(
                target,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urllib_req.urlopen(req, timeout=5):
                pass
            logger.info("Subscription fired: %s → %s (%s)", task_id, target, event)
        except Exception as exc:
            logger.warning("Subscription webhook failed: %s → %s: %s", task_id, target, exc)


def _decompose_triage_task(task: dict) -> bool:
    """Decompose a triage task into typed child tasks using LLM.

    Creates 3-7 child tasks in backlog. Moves parent to 'decomposed' on success.
    Returns True on success, False on failure (leaves task in triage for retry).
    """
    task_id = task["id"]
    title = task.get("title", "")
    description = (task.get("description") or "").strip()
    custom_prompt = (task.get("triage_prompt") or "").strip()

    base_context = custom_prompt or (
        f"Title: {title}\nDescription: {description or '(none)'}"
    )

    try:
        import json as _json
        import re as _re2
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        from tools.kanban.task_factory import create_tasks

        prompt = (
            f"Decompose this task into 3-7 concrete, independently-executable subtasks.\n\n"
            f"{base_context}\n\n"
            "Return ONLY a JSON array of subtasks:\n"
            '[{"title": "...", "description": "...", "task_type": "build|fix|test|research|deploy|chore", "priority": "critical|high|medium|low"}, ...]'
        )
        req = LLMRequest(
            system_prompt="You are a software task decomposer. Return valid JSON array only.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("triage_decomposition", req)
        if not resp or not resp.content:
            logger.warning("Triage decompose: LLM returned no content for %s", task_id)
            return False

        raw = _re2.sub(
            r"^```(?:json)?\s*|\s*```$", "", resp.content.strip(), flags=_re2.MULTILINE
        ).strip()
        subtasks = _json.loads(raw)
        if not isinstance(subtasks, list) or not subtasks:
            logger.warning("Triage decompose: empty subtask list for %s", task_id)
            return False

        child_specs = []
        # The parent's own dependency — inherited by the first child so a gated
        # parent cannot be decomposed into an ungated chain.
        parent_dep = task.get("depends_on_task_id")

        prev_child: str | None = None
        for i, sub in enumerate(subtasks[:7]):
            child_id = f"{task_id}-d{i + 1:02d}"
            child_specs.append({
                "id": child_id,
                "title": str(sub.get("title", "Subtask"))[:255],
                "description": str(sub.get("description", "")),
                "task_type": sub.get("task_type", "build"),
                "priority": sub.get("priority", "medium"),
                "status": "backlog",
                # Chain sequentially; the FIRST child inherits the PARENT's dep so
                # decomposing a gated task cannot produce an ungated chain.
                "depends_on_task_id": prev_child or parent_dep,
                "dispatch_source": f"triage:{task_id}",
            })
            prev_child = child_id
        create_tasks(child_specs)
        logger.info("Triage decompose: created %d child tasks for %s", len(child_specs), task_id)

        _move_task(task_id, "decomposed", reason=f"triage: decomposed into {len(child_specs)} subtasks")
        return True
    except Exception as exc:
        logger.warning("Triage decompose failed for %s: %s", task_id, exc)
        return False


def _reclaim_zombie_tasks() -> None:
    """Hermes-style zombie reclaim: demote in_progress tasks whose heartbeat
    has gone silent for longer than KANBAN_ZOMBIE_SILENCE_HOURS (default 2h).

    Only applies to tasks that have sent at least one heartbeat — tasks that
    pre-date heartbeat support have last_heartbeat_at = NULL and are handled
    by the existing stale-in_progress reaper (0d).
    """
    silence_hours = _int_env("KANBAN_ZOMBIE_SILENCE_HOURS", 2)
    conn = None
    try:
        conn = get_connection()
        # The cutoff is computed in Python rather than in SQL: the previous
        # `datetime('now', %s || ' hours')` is SQLite-only and raises
        # UndefinedFunction on PostgreSQL (the primary backend), so this whole
        # sweep silently never ran. Comparing ISO-8601 UTC strings is correct
        # for both backends and needs no dialect branch.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=silence_hours)
        ).isoformat()
        rows = conn.execute(
            "SELECT id, title, failure_count "
            "FROM kanban_tasks "
            "WHERE status = 'in_progress' "
            "  AND last_heartbeat_at IS NOT NULL "
            "  AND last_heartbeat_at < %s",
            (cutoff,),
        ).fetchall()
        if not rows:
            return
        for row in rows:
            d = dict(row)
            task_id = d["id"]
            if task_id in _running:
                continue  # still tracked locally — reaper handles it
            logger.warning(
                "Zombie reclaim: %s went silent >%dh — demoting to token_exhausted",
                task_id, silence_hours,
            )
            try:
                _move_task(task_id, "token_exhausted",
                           reason=(f"zombie reclaim: heartbeat silent >{silence_hours}h "
                                   f"— demoted for retry"))
                conn.execute(
                    "UPDATE kanban_tasks "
                    "SET failure_count = failure_count + 1, "
                    "    last_failure_reason = %s, "
                    "    last_failure_at = %s, "
                    "    updated_at = %s "
                    "WHERE id = %s",
                    (
                        f"Zombie reclaim: no heartbeat for >{silence_hours}h",
                        _utcnow_iso(),
                        _utcnow_iso(),
                        task_id,
                    ),
                )
                conn.commit()
            except Exception as exc:
                logger.warning("Zombie reclaim move failed for %s: %s", task_id, exc)
    except Exception as exc:
        logger.warning("_reclaim_zombie_tasks error: %s", exc)
    finally:
        if conn:
            conn.close()


#: Per-task open-PR query timeout. Was 10s inline; a `gh` call that overruns it
#: now reports PR_UNKNOWN rather than "no PR", so this is a latency knob and no
#: longer decides whether pushed work survives.
_OPEN_PR_QUERY_TIMEOUT_SECONDS = _int_env("KANBAN_OPEN_PR_QUERY_TIMEOUT_SECONDS", 30)

#: The three answers to "is there an open PR for this task?".
#:
#: They exist because two DIFFERENT callers ask that question and need opposite
#: defaults when it cannot be answered. `_open_pr_listing_unavailable` already
#: draws this distinction for the stale reaper, in the same words: "no evidence
#: of a PR" and "could not look for a PR" lead to opposite decisions about a
#: task's fate. This is that seam extended to the PR-flow caller, not a second
#: copy of it.
PR_OPEN = "open"
PR_NONE = "none"
PR_UNKNOWN = "unknown"


def _pr_open_state(task_id: str) -> str:
    """PR_OPEN / PR_NONE / PR_UNKNOWN for ``kanban/<task_id>``.

    PR_NONE means gh answered and there is no open PR. PR_UNKNOWN means we could
    not ask — a timeout, a non-zero exit, missing gh, or output we cannot parse.
    Collapsing those two into False is what made a slow `gh` indistinguishable
    from a PR that never opened.

    An exit code of 0 with empty stdout is UNKNOWN, not NONE: gh printing
    nothing told us nothing, and the previous code read exactly that case as
    "no PR" because it only tested stdout for truthiness.
    """
    # Repo-aware (ked-core-01/03): an EXTERNAL task's git/gh state lives in ITS repo,
    # not ICDev's. Asking ICDev whether compass's work landed always answers 'no'.
    _repo_root = _task_repo_root(task_id)
    branch_name = f"kanban/{task_id}"
    try:
        import json as _json
        import subprocess as _sp

        result = _sp.run(
            ["gh", "pr", "list", "--head", branch_name, "--state", "open", "--json", "number"],
            capture_output=True, text=True,
            timeout=_OPEN_PR_QUERY_TIMEOUT_SECONDS, cwd=str(_repo_root),
        )
    except Exception as exc:  # noqa: BLE001 — every failure is "could not ask"
        logger.debug("open-PR query for %s could not run: %s", task_id, exc)
        return PR_UNKNOWN
    if getattr(result, "returncode", 1) != 0:
        logger.debug(
            "open-PR query for %s exited %s: %s", task_id,
            getattr(result, "returncode", "?"),
            (getattr(result, "stderr", "") or "").strip()[:160],
        )
        return PR_UNKNOWN
    raw = (getattr(result, "stdout", "") or "").strip()
    if not raw:
        return PR_UNKNOWN
    try:
        prs = _json.loads(raw)
    except ValueError:
        logger.debug("open-PR query for %s returned non-JSON", task_id)
        return PR_UNKNOWN
    return PR_OPEN if prs else PR_NONE


def _pr_flow_outcome(state: str) -> Tuple[Optional[str], str]:
    """``(target_status, reason)`` for the post-push PR-flow confirmation.

    A target of None means LEAVE THE TASK WHERE IT IS. That is the whole fix:
    `PR flow: branch pushed but the PR could not be opened` accounted for 66 of
    the 126 backwards transitions on this board — more than orphan_sweep, the
    stale reaper and auto-revive combined — and every one of them threw away a
    branch that had real commits on it, because the boolean could not say "I
    could not tell".

    Leaving it alone is safe in both directions: if a PR really did open,
    `pr_linker` links it on the next poll; if the dispatch genuinely died, the
    stale reaper still reaps it on its own threshold. Rolling back is the only
    option that destroys work.
    """
    if state == PR_OPEN:
        return "pr_opened", "PR opened — awaiting CI + merge"
    if state == PR_NONE:
        return "backlog", "PR flow: branch pushed but the PR could not be opened"
    return None, (
        "PR flow: branch pushed, but whether a PR opened could not be "
        "determined (gh unreachable) — leaving the task as-is rather than "
        "discarding pushed commits; pr_linker or the stale reaper will settle it"
    )


def _has_open_pr(task_id: str) -> bool:
    """Respawn guard: True only when an open PR is CONFIRMED for kanban/<task_id>.

    Deliberately unchanged: an unanswerable query still reads as False here, so
    dispatch proceeds when gh is unavailable (air-gap environments). That is the
    right default for THIS caller — the cost of being wrong is one extra
    dispatch. It is the wrong default for the post-push confirmation, which is
    why that caller now uses `_pr_open_state` instead.
    """
    return _pr_open_state(task_id) == PR_OPEN


_open_pr_branch_cache: Dict[str, Tuple[float, Set[str]]] = {}
#: The SAME listing, keyed head branch -> PR number (mfx-own-01). Filled by the
#: one `gh pr list` call `_open_pr_head_branches` already makes; nothing asks
#: the forge a second time for a number it printed the first time.
_open_pr_numbers_cache: Dict[str, Tuple[float, Dict[str, int]]] = {}
_OPEN_PR_CACHE_TTL_SECONDS = 45.0

#: Monotonic stamp of the last _open_pr_head_branches call that could not
#: reach `gh` for a given repo root. See _open_pr_listing_unavailable.
_open_pr_listing_failed_at: Dict[str, float] = {}


def _open_pr_head_branches(repo_root: str) -> Set[str]:
    """Head branch names of every open PR in *repo_root*, cached per cycle.

    ONE `gh` call for the whole board instead of one per candidate task. The
    per-task _has_open_pr costs a subprocess with a 10s timeout, which is fine
    as a final check before dispatching a single task but far too expensive to
    run across every candidate during selection.

    Returns an empty set on any error, matching _has_open_pr's air-gap
    behaviour: when gh is unavailable we do not filter, and the per-task guard
    at dispatch time remains the backstop.
    """
    cached = _open_pr_branch_cache.get(repo_root)
    if cached and (time.monotonic() - cached[0]) < _OPEN_PR_CACHE_TTL_SECONDS:
        return cached[1]
    branches: Set[str] = set()
    try:
        import json as _json
        import subprocess as _sp

        result = _sp.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "200",
             "--json", "headRefName,number"],
            capture_output=True, text=True, timeout=20, cwd=repo_root,
        )
        if result.returncode != 0:
            # gh missing, unauthenticated, or the repo has no remote. Record it
            # so callers that must distinguish "no open PRs" from "could not
            # ask" can (see _open_pr_listing_unavailable), and do NOT cache the
            # empty set as an answer.
            _open_pr_listing_failed_at[repo_root] = time.monotonic()
            logger.debug(
                "open-PR branch listing failed for %s (gh exit %d)",
                repo_root, result.returncode,
            )
            return set()
        numbers: Dict[str, int] = {}
        if result.stdout.strip():
            for p in _json.loads(result.stdout):
                head = p.get("headRefName")
                if not head:
                    continue
                branches.add(str(head))
                try:
                    numbers[str(head)] = int(p.get("number"))
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        _open_pr_listing_failed_at[repo_root] = time.monotonic()
        logger.debug("open-PR branch listing unavailable for %s: %s", repo_root, exc)
        return set()
    _open_pr_listing_failed_at.pop(repo_root, None)
    stamp = time.monotonic()
    _open_pr_branch_cache[repo_root] = (stamp, branches)
    _open_pr_numbers_cache[repo_root] = (stamp, numbers)
    return branches


def _open_pr_for_branch(repo_root: str, branch: str) -> Optional[int]:
    """The number of the OPEN PR whose head is *branch*, or None (mfx-own-01).

    None covers BOTH "the forge answered and there is no such PR" and "the
    forge could not be asked". That conflation is the right one for every
    caller of this function: each only ever moves a task FORWARD on positive
    evidence that its PR exists, so an unanswerable forge is a no-op rather
    than a wrong move in either direction.

    Reads the per-cycle listing `_open_pr_head_branches` already makes; it
    never runs a second `gh` call of its own.
    """
    _open_pr_head_branches(repo_root)  # refresh the cache if it is stale
    cached = _open_pr_numbers_cache.get(repo_root)
    if not cached:
        return None
    return cached[1].get(branch)


def _hand_parked_task_to_pr_watcher(task_id: str, *, context: str) -> Optional[int]:
    """token_exhausted + an OPEN PR on kanban/<id>  ->  pr_opened  (mfx-own-01).

    MEASURED 2026-09-03: rmf-rfp-01 and rmf-wp-02 parked `token exhaustion:
    parked for retry 2/60` with PRs #2040 / #2042 OPEN and red on CI. Their
    resume_at then slid forward every cycle for SIX HOURS and no worker ever
    started: respawn guard 2 in the dispatcher refuses a task whose
    branch has an open PR (correctly -- it is what stops duplicate PRs), so
    every token-retry dispatch was refused and `_token_retry_backoff` pushed
    resume_at out again; and pr_watcher polls `pr_opened` / `ci_failed` /
    `merge_conflict` / `changes_requested`, never `token_exhausted`. Two
    correct guards composed into a task owned by NEITHER actor. A human fixed
    both CI failures by hand.

    THIS IS A HAND-OFF, NOT A THIRD DISPATCHER. The task moves to `pr_opened`
    with actor=scheduler and a reason naming the PR and the new owner, so the
    watcher's existing resume-on-CI-failure path picks it up on its next poll.
    Nothing here spawns a worker -- dispatching from this state is exactly the
    duplicate PR the respawn guard exists to prevent.

    Returns the PR number when the task was handed off, None otherwise. None is
    also what an unavailable forge returns: the move happens only on POSITIVE
    evidence that the PR exists (`_open_pr_for_branch`), never on its absence.
    Called at park time (`_check_completed`), on every retry evaluation while
    the task is parked (`_check_token_exhausted_tasks`), and the same hand-off
    runs at startup from `tools/kanban/startup_recovery.py`.
    """
    try:
        root = str(_task_repo_root(task_id))
    except Exception:  # noqa: BLE001 — a bad repo target reads as ICDev
        root = str(BASE_DIR)
    number = _open_pr_for_branch(root, f"kanban/{task_id}")
    if number is None:
        return None
    _move_task(
        task_id, "pr_opened", actor="scheduler",
        reason=(f"open PR #{number} found while parked ({context}); "
                f"handed to pr_watcher"),
    )
    # A resume_at for a task the scheduler no longer owns is a lie the next
    # evaluation would read; the retry counter is kept, because it bounds the
    # token budget across the task's WHOLE life and a hand-off is not a reset.
    _clear_resume_at(task_id)
    logger.info(
        "parked hand-off: %s token_exhausted -> pr_opened (open PR #%d, %s)",
        task_id, number, context,
    )
    return number


def _open_pr_listing_unavailable(repo_root: str) -> bool:
    """True when the most recent open-PR listing for *repo_root* could not run.

    _open_pr_head_branches returns an empty set both when a repo genuinely has
    no open PRs and when `gh` is unavailable. That conflation is deliberate and
    correct for the dispatch window — "do not filter" is the safe default there
    — but not for the reaper, where "no evidence of a PR" and "could not look
    for a PR" lead to opposite decisions about a task's fate. This reads the
    failure stamp the listing already records; it never calls `gh` itself.
    """
    failed_at = _open_pr_listing_failed_at.get(repo_root)
    if failed_at is None:
        return False
    return (time.monotonic() - failed_at) < _OPEN_PR_CACHE_TTL_SECONDS


def _tasks_with_recent_success(task_ids: List[str], within_minutes: int = 30) -> Set[str]:
    """Ids from *task_ids* that transitioned to done within the window.

    Batched counterpart to _had_recent_success — one query for the whole
    candidate set. The cutoff is computed in Python because the per-task
    version's `datetime('now', ...)` is SQLite dialect and PostgreSQL is the
    primary backend.
    """
    if not task_ids:
        return set()
    conn = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()
        placeholders = ",".join(["%s"] * len(task_ids))
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT task_id FROM kanban_status_transitions "
            f"WHERE task_id IN ({placeholders}) "  # nosec B608 — placeholders only
            "  AND to_status = 'done' AND recorded_at > %s",
            (*task_ids, cutoff),
        ).fetchall()
        return {dict(r)["task_id"] for r in rows}
    except Exception as exc:
        logger.debug("recent-success batch lookup skipped: %s", exc)
        return set()
    finally:
        if conn:
            conn.close()


#: Holds recorded by the most recent ``_drop_sibling_overlapped`` call, so
#: ``run()`` can report the per-cycle count without re-asking the board.
#:
#: ``None`` means the admission did NOT RUN this cycle — the board was at
#: capacity, or the check is stood down. That is deliberately not the same as an
#: empty list: reporting a cycle that never looked as "0 holds" is the fabricated
#: clean zero this repo refuses everywhere else, and it would also let last
#: cycle's count be read as this cycle's.
_LAST_SIBLING_HOLDS: Optional[List[dict]] = None

#: One wait row per (task, sibling) episode rather than one per cycle. A hold
#: that lasts an afternoon is ONE wait, and writing it every 60s would bury the
#: transition log under a fact that has not changed.
_SIBLING_HOLD_RECORDED: Set[str] = set()


def _serialize_overlapping_siblings_enabled() -> bool:
    """Read ``reflexes.kanban.serialize_overlapping_siblings`` (default ON).

    ``KANBAN_SERIALIZE_SIBLINGS=0`` stands it down without editing config, which
    is the auditable escape hatch CLAUDE.md asks for. Any unreadable config
    leaves the check ON: the surveyed fire rate is 1.06%, so failing open here
    would silently restore the defect the survey measured.
    """
    import os as _os  # noqa: PLC0415 — module-local, matching this file's idiom

    override = _os.environ.get("KANBAN_SERIALIZE_SIBLINGS")
    if override is not None:
        return override.strip().lower() not in {"0", "false", "no", "off"}
    try:
        import yaml as _yaml  # noqa: PLC0415

        with open(BASE_DIR / "args" / "genesis_config.yaml", encoding="utf-8") as fh:
            cfg = _yaml.safe_load(fh) or {}
        kanban_cfg = (cfg.get("reflexes") or {}).get("kanban") or {}
        return bool(kanban_cfg.get("serialize_overlapping_siblings", True))
    except Exception as exc:  # noqa: BLE001 — an unreadable config is not an opt-out
        logger.debug("sibling serialization config unreadable, staying on: %s", exc)
        return True


def _drop_sibling_overlapped(tasks: List[dict]) -> List[dict]:
    """Hold a task whose in-flight SIBLING already owns a file it declares.

    THE DEFECT (mfx-sib-01). Ten ``rmf-ui-*`` cards — "one route per card" — each
    appended to the same lines of the same canvas ``blueprint.py``, the same nav
    dropdown and the same feature doc. Whichever landed first made every open
    sibling CONFLICTING, ``pr_watcher`` classified that ``real`` (git conflicts
    too), its rebase aborted four times per card, five LLM resumes burned, and a
    human unioned the hunks by hand. Ten times, roughly six hours.

    The MERGE door has serialized siblings since ``hold_on_sibling_conflict``.
    DISPATCH did not, so four siblings were built concurrently and three of the
    four were guaranteed to conflict — the expensive place to find out, because
    by then the work is written.

    A HOLD IS A WAIT, NOT A PARK. The task stays ``scheduled``, yields its
    selection slot to something that can actually run (the same reason
    ``_drop_respawn_guarded`` filters before the truncation), and is re-evaluated
    next cycle. When the sibling reaches ``done`` the hold evaporates with no
    further action. Nothing on the task row changes.

    The predicate is ``tools.kanban.sibling_overlap.overlap`` — the same function
    the survey replayed — over
    ``tools.kanban.artifact_evidence.declared_artifacts`` and
    ``pr_watcher._is_additive_path``. No second copy of any of the three.

    SURVEYED BEFORE ARMING over 1,977 recorded dispatches (30 days to
    2026-09-04): 21 holds, 1.06%, below the 1.63% CLAUDE.md calls refusing
    routine work; 18 of the 21 (85.71%) went on to record a real merge conflict;
    it fires on exactly the ten rmf-ui cards. Re-derive with
    ``python -m tools.kanban.sibling_overlap --survey``.

    FAIL-OPEN. Anything unreadable returns the candidate list untouched — a
    missed hold costs one rebase, a wedged scheduler costs the whole queue.
    """
    global _LAST_SIBLING_HOLDS
    if not tasks or not _serialize_overlapping_siblings_enabled():
        return tasks
    _LAST_SIBLING_HOLDS = []
    try:
        from tools.kanban.sibling_overlap import find_holds

        holds = find_holds(tasks)
    except Exception as exc:  # noqa: BLE001 — never wedge dispatch
        logger.warning("sibling-overlap admission skipped: %s", exc)
        _LAST_SIBLING_HOLDS = None  # did not measure — never report that as zero
        return tasks
    if not holds:
        _SIBLING_HOLD_RECORDED.clear()
        return tasks

    _LAST_SIBLING_HOLDS = [h.to_dict() for h in holds.values()]
    print(
        f"  Kanban: holding {len(holds)} task(s) behind an in-flight sibling "
        f"that edits the same file: "
        f"{', '.join(f'{h.task_id}<-{h.sibling_id}' for h in holds.values())}"
    )
    for hold in holds.values():
        logger.info("dispatch window: %s %s — yielding its slot",
                    hold.task_id, hold.reason)
        episode = f"{hold.task_id}->{hold.sibling_id}"
        if episode not in _SIBLING_HOLD_RECORDED:
            _SIBLING_HOLD_RECORDED.add(episode)
            _record_status_transition(
                hold.task_id, "scheduled", "scheduled",
                actor="sibling-serializer", reason=hold.reason,
            )
    # Episodes that cleared this cycle may legitimately be re-recorded later.
    _SIBLING_HOLD_RECORDED.intersection_update(
        {f"{h.task_id}->{h.sibling_id}" for h in holds.values()})
    return [t for t in tasks if t.get("id") not in holds]


def _drop_respawn_guarded(tasks: List[dict]) -> List[dict]:
    """Remove tasks the dispatcher would refuse to dispatch anyway.

    _dispatch_to_claude skips a task that already has an open PR or completed
    in the last 30 minutes. That check used to run only AFTER _get_due_tasks
    had truncated the candidate list to available_slots — so a task that could
    never be dispatched still consumed one of the three slots in the selection
    window, every cycle, forever.

    Observed on this board: the three highest-priority due tasks all had open
    PRs. The scheduler selected exactly those three, skipped all three, and
    dispatched nothing, while thirteen ready tasks behind them were never even
    considered. The board looked idle for hours with every gate green.

    Filtering here, before the cap, means blocked tasks yield their place. The
    per-task guards at dispatch time stay as the backstop for state that
    changes between selection and dispatch.

    A HELD COORDINATION LEASE IS THE THIRD CAUSE, and it starved the board the
    same way (rem-hyg-15). ``_dispatch_to_claude`` takes ``kanban:task:<id>``
    before spending a token and skips the task when it cannot, so a lease-held
    task is exactly as un-dispatchable as one with an open PR — but it was not
    filtered here, so it kept consuming a selection slot. Measured 2026-08-20:
    the three highest-priority due tasks were all lease-held, the scheduler
    selected exactly those three every cycle, dispatched nothing, and reported
    `idle [review_bound]` for over an hour while two ready tasks behind them
    were never considered.

    Worse, all three holders were DEAD — one-shot seeding scripts that exited
    seconds after taking the lease — so the block was permanent rather than
    transient. ``release_stale`` existed and nothing on this path called it.
    """
    if not tasks:
        return tasks

    recent = _tasks_with_recent_success([t.get("id") for t in tasks if t.get("id")])

    # Group by repo root: an external task's PRs live in ITS repo, so one
    # listing per distinct root rather than one for ICDev and wrong answers
    # for everything else.
    pr_branches_by_root: Dict[str, Set[str]] = {}
    kept: List[dict] = []
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue
        if task_id in recent:
            logger.info(
                "dispatch window: %s completed within the last 30 min — "
                "yielding its slot", task_id,
            )
            continue
        try:
            root = str(_task_repo_root(task_id))
        except Exception:
            root = str(BASE_DIR)
        if root not in pr_branches_by_root:
            pr_branches_by_root[root] = _open_pr_head_branches(root)
        if f"kanban/{task_id}" in pr_branches_by_root[root]:
            logger.info(
                "dispatch window: %s already has an open PR — yielding its slot "
                "to a task that can actually run", task_id,
            )
            continue
        if _lease_blocks_dispatch(task_id):
            continue
        kept.append(task)
    return kept


# Lease liveness is answered in ONE place (autonomy-adm-03). The heartbeat probe
# and its window used to live here as a private pair, which left every OTHER
# reader of the same lease (cli --release, the idle advisor, startup recovery)
# asking ``holder_is_alive`` on its own and reading a dead pid as dead work.
# The names are kept as aliases so nothing that imported them breaks, but they
# ARE the shared objects — not copies.
from tools.kanban.lease_liveness import (  # noqa: E402
    HEARTBEAT_LIVE_MINUTES as _HEARTBEAT_LIVE_MINUTES,  # noqa: F401 — alias, see above
    task_is_heartbeating as _task_is_heartbeating,  # noqa: F401 — alias, see above
)


def _lease_blocks_dispatch(task_id: str) -> bool:
    """Is ``kanban:task:<id>`` held by LIVE WORK? Reap it if it is litter.

    Two different answers that used to look identical from the scheduler's seat:

      * held by a live session  -> another worker owns this task. Correct to
        skip, and correct to yield the selection slot rather than burn it.
      * held by a DEAD process  -> nobody owns it. The lease is litter, and
        leaving it means the task is blocked forever, because nothing else on
        this path ever calls ``release_stale``.

    Measured 2026-08-20: three tasks were pinned by leases whose holders were
    one-shot seeding scripts that had exited seconds after claiming. They were
    the three highest-priority due tasks, so they filled the whole selection
    window every cycle and the board sat `idle [review_bound]` for over an hour
    with capacity free and no open PRs anywhere.

    FAIL-SAFE TOWARDS NOT REAPING, ON TWO SIGNALS RATHER THAN ONE — and the
    two-signal verdict is ``tools.kanban.lease_liveness.task_lease_verdict``,
    shared with every other reader of this lease, not a rule of this function.

    ``holder_is_alive`` returns ``None`` when the answer cannot be determined
    (no psutil, an unreadable process table), and that is treated as ALIVE —
    reclaiming on ignorance is how two workers build the same task, which is far
    worse than one cycle's delay. It also guards PID reuse internally.

    A DEAD PID IS NOT ENOUGH ON ITS OWN, and assuming it was is a mistake this
    function made in its first draft. The pid on the lease is the pid of the
    process that TOOK it, which for a dispatch is the scheduler's short-lived
    child — it exits as soon as it has handed the task to a worker, while the
    worker runs on for minutes under a different pid. Verified live: rem-hyg-13
    showed ``holder_is_alive() is False`` while heartbeating four seconds
    earlier. Reaping on the pid alone would have freed a lease guarding work
    that was actively running.

    So the task's own HEARTBEAT is the second signal, and it is the one about
    the work rather than about the bookkeeping. A lease is litter only when the
    holder is gone AND the task is not heartbeating: a still-scheduled task that
    never started (the one-shot seeding script case this was written for) has no
    heartbeat at all, while a live worker refreshes one continuously.
    """
    try:
        from tools.kanban import lease_liveness
    except Exception as exc:  # noqa: BLE001 — never wedge dispatch on an import
        logger.debug("lease check unavailable for %s: %s", task_id, exc)
        return False

    try:
        verdict, reaped = lease_liveness.reap_if_litter(task_id)
        if verdict.state == lease_liveness.STATE_FREE:
            return False
        if verdict.state == lease_liveness.STATE_LITTER:
            logger.info(
                "dispatch window: %s was pinned by a lease whose holder is gone "
                "and the task is not heartbeating — reaped=%s, dispatchable "
                "again", task_id, reaped,
            )
            return not reaped                 # reaped -> dispatchable now
        if verdict.state == lease_liveness.STATE_WORKING:
            logger.info(
                "dispatch window: %s has a dead lease holder but IS heartbeating "
                "— the worker outlived the process that took the lease, so the "
                "lease is kept and the slot yielded", task_id,
            )
            return True
        # live: True, or None ("cannot tell" -> assume alive).
        logger.info(
            "dispatch window: %s is claimed by a live session — yielding its "
            "slot to a task that can actually run", task_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("lease check failed for %s: %s", task_id, exc)
        return False


def _task_executor_url(conn, task_id: str) -> Optional[str]:
    """kanban_tasks.executor_url for *task_id*, or None.

    Read per-task rather than in the reaper's sweep SELECT: this column is one
    of several that arrived by migration, and the reaper is the last line of
    defence against a wedged board — it must not lose its whole sweep to a
    schema variance in one column it only needs for tasks it is about to touch.
    Only reached for a task already past its threshold, so the extra query is
    rare by construction.
    """
    try:
        row = conn.execute(
            "SELECT executor_url FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — absent column is not a reaper failure
        logger.debug("executor_url unavailable for %s: %s", task_id, exc)
        return None
    if not row:
        return None
    value = dict(row).get("executor_url")
    return str(value) if value else None


def _finished_with_open_pr(task_id: str, executor_url: Optional[str]) -> Optional[str]:
    """Evidence that an in_progress task has ALREADY OPENED ITS PR.

    A worker that pushed its branch, opened a PR and then went quiet — no log
    output, no heartbeat — is indistinguishable from a dead subprocess when
    liveness is judged on the heartbeat alone. It is not dead, it is finished,
    and the proof is sitting in the same row the reaper is about to update.

    Observed 2026-08-16: last_heartbeat_at 16:24:36Z, PR #1744 created
    16:27:16Z, stale-reaper fired 16:35:00Z against a row that already carried
    executor_url=https://github.com/icdev-ai/icdev/pull/1744. The task was
    recorded as a failure, its failure_count incremented, and its status set to
    backlog while an open PR existed — and enough of those feed the fc>=5 sweep
    that parks a healthy task in 'suggested'.

    Returns a human-readable evidence string, or None when there is none.

    Evidence order matters:

    1. ``kanban/<task_id>`` among the open-PR head branches. Authoritative, and
       it is the SAME per-cycle cached listing the dispatch window already uses
       (_open_pr_head_branches) — no second `gh` call is made here.
    2. ``executor_url`` naming a pull request, but ONLY when that listing could
       not run at all. The column is never cleared on re-dispatch, so a task
       whose earlier PR merged still carries the URL; treating it as proof of a
       *currently* open PR would park a genuinely dead task in pr_opened. Where
       `gh` is unreachable (air-gapped runners) it is the only record there is.
    """
    try:
        root = str(_task_repo_root(task_id))
    except Exception:  # noqa: BLE001 — fall back to this repo, as elsewhere
        root = str(BASE_DIR)

    if f"kanban/{task_id}" in _open_pr_head_branches(root):
        return f"open PR on branch kanban/{task_id}"

    url = (executor_url or "").strip()
    if url and "/pull/" in url and _open_pr_listing_unavailable(root):
        return f"executor_url records an opened PR ({url}); open-PR listing unavailable"

    return None


def _had_recent_success(task_id: str, within_minutes: int = 30) -> bool:
    """Respawn guard: return True if this task completed successfully very recently.

    Catches auth-failure loops where a task completes but the executor
    immediately re-dispatches it due to a stale DB read.
    """
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM kanban_status_transitions "
            "WHERE task_id = %s AND to_status = 'done' "
            "  AND recorded_at > datetime('now', %s || ' minutes')",
            (task_id, f"-{within_minutes}"),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


def _record_dispatch_pid(task_id: str, handle) -> None:
    """Persist the dispatched pid so a LATER scheduler can still clean it up.

    _running is in-memory: it does not survive a restart, and it never existed
    for a task dispatched by a previous instance. That is exactly when a reap
    orphans a live process tree, so the durable record is the point.
    """
    pid = getattr(handle, "pid", None)
    if not pid:
        return
    try:
        from tools.kanban.dispatch_reaper import record_dispatch
        conn = get_connection()
        try:
            record_dispatch(conn, task_id, pid)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001 — never fail a dispatch over bookkeeping
        logger.debug("could not record dispatch pid for %s: %s", task_id, exc)


def _reconcile_pr_opened() -> int:
    """Move a PRE-PR task that already has an open PR to ``pr_opened`` (rem-hyg-18).

    THE GAP. `pr_opened` had exactly ONE writer in the whole tree — the
    stale-reaper below — and it is gated on ``WHERE status = 'in_progress'``. So
    a task sitting in ``scheduled`` or ``backlog`` with an open PR could never
    reach ``pr_opened`` by any path at all. It is not a rare shape: it is what
    every PR opened outside the runner produces, including every one a human
    raises by hand.

    The symptom is two surfaces with the SAME NAME disagreeing. The Home panel
    "Awaiting Merge" reads the FORGE (`gh pr list`); the Kanban column "Awaiting
    Merge" (kanban.html) reads ``status = 'pr_opened'``. Measured 2026-08-21:
    the panel listed three PRs while the column was EMPTY and the board showed
    those tasks as `scheduled` / `in_progress`.

    DELIBERATELY NOT TOUCHING `in_progress`. That case is already handled, and
    handled better: the stale-reaper moves it once the task stops heartbeating
    ("finished, not stalled"). A task whose worker is alive and pushing commits
    genuinely IS in progress, and moving it early would free a dispatch slot
    while that worker still burns tokens — quietly raising concurrency above
    MAX_IN_PROGRESS, which is the flow-control property clx-flow-01 exists to
    hold. So this closes only the hole nothing else can reach.

    FORWARD ONLY. `changes_requested`, `merge_conflict`, `ci_failed` and `done`
    are all AHEAD of `pr_opened`; rewriting them backwards would erase a review
    outcome. Only the two pre-PR states are eligible.
    """
    #: States that precede a PR. `in_progress` is excluded on purpose — see above.
    eligible = ("scheduled", "backlog")
    moved = 0
    conn = None
    try:
        branches = _open_pr_head_branches(str(BASE_DIR))
        if not branches:
            # Empty means gh was unavailable OR there are genuinely no open PRs.
            # Both are correctly a no-op: this only ever moves a task FORWARD on
            # positive evidence that its PR exists.
            return 0
        conn = get_connection()
        placeholders = ",".join(["%s"] * len(eligible))
        rows = conn.execute(
            f"SELECT id, status FROM kanban_tasks WHERE status IN ({placeholders})",  # nosec B608
            eligible,
        ).fetchall()
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in rows:
            record = dict(row)
            task_id = record.get("id")
            if not task_id or f"kanban/{task_id}" not in branches:
                continue
            # Belt as well as braces. The SELECT already filters on `eligible`,
            # but that guard lives in SQL where no unit test can see it and a
            # later edit could widen it without anything noticing. Re-asserting
            # it here makes "only ever moves FORWARD" a property of the FUNCTION
            # rather than of one query string.
            if record.get("status") not in eligible:
                continue
            conn.execute(
                "UPDATE kanban_tasks SET status = 'pr_opened', updated_at = %s "
                "WHERE id = %s AND status = %s",
                (now_iso, task_id, record.get("status")),
            )
            moved += 1
            logger.info(
                "pr-reconcile: %s was %s with an open PR (kanban/%s) -> pr_opened",
                task_id, record.get("status"), task_id,
            )
        if moved:
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — never wedge the cycle
        logger.warning("pr-reconcile skipped: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return moved


def _reap_stale_in_progress() -> None:
    """Periodic reaper: reset in_progress tasks not tracked in _running.

    Catches three failure modes that survive past startup-recovery:
      1. Process died mid-run after dispatch (PID gone, DB still in_progress).
      2. Verification gate left the task in_progress with 'human review needed'
         instead of resetting to backlog.
      3. Silent dispatch failure — task promoted to in_progress but subprocess
         never started (execution_id still NULL, log file empty). Fast-reaped
         after _SILENT_DISPATCH_THRESHOLD (default 1 min, see env var
         KANBAN_SILENT_DISPATCH_THRESHOLD_SECONDS) so the board never shows a
         ghost in_progress for more than one scheduler cycle window. Skipped
         for tasks whose most recent in_progress transition was recorded with
         a non-scheduler actor (see _task_dispatched_by_scheduler) — those
         fall through to the normal threshold instead, since an empty log is
         expected (not evidence of a dead subprocess) for externally/manually
         managed work.

    A task whose PR is already open is NOT reaped in any of those three modes.
    It has finished, not stalled: it is moved to pr_opened and gains no
    failure_count and no last_failure_reason (see _finished_with_open_pr).

    Normal threshold: 2× task timeout (30–80 min).
    Silent-dispatch threshold: _SILENT_DISPATCH_THRESHOLD (log empty AND no
    fresh heartbeat AND not in _running AND last in_progress transition actor
    is 'scheduler' or unrecorded).
    Only resets tasks NOT currently in _running to avoid killing live agents.

    NO-OP when another live scheduler owns the runner: _running is per-process,
    so a second process would see every one of the owner's live tasks as dead.
    """
    _foreign = _foreign_scheduler_pid()
    if _foreign:
        logger.info(
            "stale-reaper: skipped — scheduler pid=%d owns the runner and this "
            "process cannot see its live subprocesses", _foreign,
        )
        return

    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, failure_count FROM kanban_tasks "
            "WHERE status = 'in_progress'"
        ).fetchall()
        if not rows:
            return

        now = datetime.now(timezone.utc)
        reaped = []
        finished = []
        for r in rows:
            d = dict(r)
            tid = d["id"]

            # Manual-mode gates are held in_progress indefinitely by design —
            # reaping one to backlog gets it re-dispatched, which its whole
            # existence is meant to prevent.
            if _is_manual_gate(tid, d.get("title")):
                continue

            # Fetch updated_at separately to get the real timestamp
            ts_row = conn.execute(
                "SELECT updated_at FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            if not ts_row:
                continue
            updated_raw = dict(ts_row)["updated_at"]
            if updated_raw is None:
                continue

            # Parse updated_at. A stamp this cannot read is ONE task's problem
            # and is said out loud; it used to be silent, and that silence is
            # what let a missing dependency read as "no stale tasks".
            updated_at = _parse_utc_timestamp(updated_raw)
            if updated_at is None:
                logger.warning(
                    "stale-reaper: %s has an unreadable updated_at (%r) — "
                    "skipping this task, not the sweep", tid, updated_raw,
                )
                continue

            age_seconds = (now - updated_at).total_seconds()

            # Manual Build: do not reap a task a HUMAN is building.
            #
            # From the outside, a CLI session two hours into a task is indistinguishable
            # from a scheduler subprocess that died an hour ago: no live PID in _running,
            # no log output. The reaper would reset it to backlog, incrementing
            # failure_count — silently destroying the record of where the build had got
            # to, which is the one thing Manual Build exists to preserve.
            #
            # So while Manual Build is on, a task whose in_progress transition was
            # recorded by a NON-scheduler actor is left alone, for as long as it takes.
            # A genuinely dead scheduler subprocess is still reaped: it was dispatched by
            # the scheduler, so it does not match, and it really is dead.
            if _manual_build() and not _task_dispatched_by_scheduler(tid, conn):
                logger.debug(
                    "stale-reaper: skipping %s — Manual Build is on and this task is "
                    "manually dispatched (age %.0f min)", tid, age_seconds / 60,
                )
                continue

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

            # Fast-reap silent dispatch: the subprocess never actually started.
            #
            # An empty log alone is NOT evidence of that — an LLM dispatch
            # routinely prints nothing for minutes while the model thinks, and
            # reaping on that signal at 60s was the single largest source of
            # task failures on this board. So require a stale heartbeat too:
            # _refresh_running_heartbeats stamps last_heartbeat_at every cycle
            # for every live subprocess, so a fresh beat means the task is
            # working regardless of what it has printed.
            #
            # Skipped for tasks explicitly marked actor='manual' in
            # kanban_status_transitions (tools/kanban/cli.py --set-status) —
            # an externally-managed task also has an empty log by
            # construction, but that's not evidence of a dead subprocess.
            elif (
                _task_log_is_empty(tid)
                and age_seconds >= _SILENT_DISPATCH_THRESHOLD
                and _heartbeat_is_stale(tid, conn, _SILENT_DISPATCH_THRESHOLD)
                and _task_dispatched_by_scheduler(tid, conn)
            ):
                threshold = _SILENT_DISPATCH_THRESHOLD
                reap_label = "silent-dispatch (no log output, no heartbeat)"
            else:
                threshold = _get_task_timeout(tid) * 2  # 2× normal budget = 30–80 min
                reap_label = "no live subprocess"

            if age_seconds < threshold:
                continue  # task is recent enough — let it run

            # FINISHED, not silent. Before recording a failure, ask whether the
            # task already opened its PR. A worker that has done so and gone
            # quiet looks exactly like a dead one to a heartbeat-only liveness
            # test — and this is not a threshold problem, so raising the
            # threshold again is not the fix. Move it to pr_opened, where
            # pr_watcher owns it, and do NOT touch failure_count or
            # last_failure_reason: neither describes what happened.
            #
            # Nothing is killed on this path. The kill below exists to stop a
            # reaped task's orphan from wedging while the scheduler re-dispatches
            # it; pr_opened is not a dispatcher pickup state, so that cycle
            # cannot occur, and a session still posting its own bookkeeping must
            # not be shot for it.
            pr_evidence = _finished_with_open_pr(tid, _task_executor_url(conn, tid))
            if pr_evidence:
                finished.append((tid, pr_evidence, age_seconds))
                continue

            # Kill what we are reaping. Without this the reap only flips a
            # status: the tree keeps running, the scheduler re-dispatches, and
            # the orphan wedges forever holding its worktree and its port. That
            # is how one dead launcher became three reap/re-dispatch cycles on
            # task-e2e-ebf5ab21. Declines unless the pid is provably still the
            # process we dispatched — pids are reused.
            try:
                from tools.kanban.dispatch_reaper import kill_recorded_dispatch
                _kill = kill_recorded_dispatch(conn, tid)
                if _kill.get("killed"):
                    logger.warning(
                        "stale-reaper: killed orphaned process tree for %s", tid)
            except Exception as _exc:  # noqa: BLE001 — cleanup must not block the reap
                logger.debug("stale-reaper: cleanup failed for %s: %s", tid, _exc)

            now_iso = now.isoformat()
            # Check current failure count before incrementing — if this
            # reap would bring fc to ≥5, escalate to 'suggested' for HITL
            # review instead of infinite backlog retry (fc≥5 quarantine).
            fc_row = conn.execute(
                "SELECT COALESCE(failure_count, 0) FROM kanban_tasks WHERE id = %s",
                (tid,),
            ).fetchone()
            new_fc = (fc_row[0] if fc_row else 0) + 1
            next_status = "suggested" if new_fc >= 5 else "backlog"
            _is_anomaly, _anomaly_detail = _detect_execution_anomaly(age_seconds)
            _anomaly_suffix = f" [{_anomaly_detail}]" if _is_anomaly else ""
            reason = (
                f"stale-reaper: task was in_progress for {age_seconds / 60:.0f} min "
                f"with {reap_label} (threshold={threshold / 60:.0f} min){_anomaly_suffix}. "
                + (
                    f"fc={new_fc}>=5 - escalated to suggested for HITL review."
                    if next_status == "suggested"
                    else "Automatically reset to backlog for re-dispatch."
                )
            )
            conn.execute(
                "UPDATE kanban_tasks SET "
                "  status = %s, "
                "  failure_count = COALESCE(failure_count, 0) + 1, "
                "  last_failure_reason = %s, "
                "  last_failure_at = %s, "
                "  updated_at = %s "
                "WHERE id = %s AND status = 'in_progress'",
                (next_status, reason, now_iso, now_iso, tid),
            )
            reaped.append((tid, next_status, reason))
            print(
                f"  Kanban: stale-reaper reset {tid} "
                f"(in_progress {age_seconds / 60:.0f} min, {reap_label}) -> {next_status}"
            )

        # Tasks that had already opened their PR: status only. No
        # failure_count, no last_failure_reason, no last_failure_at — none of
        # the three describes what happened, and the first two are what made
        # the failure history fictional.
        finished_rows = []
        for _tid, _evidence, _age in finished:
            _reason = (
                f"stale-reaper: task was in_progress for {_age / 60:.0f} min with no "
                f"heartbeat, but its PR is already open ({_evidence}) — finished, "
                "not stalled. Moved to pr_opened; no failure recorded."
            )
            conn.execute(
                "UPDATE kanban_tasks SET status = 'pr_opened', updated_at = %s "
                "WHERE id = %s AND status = 'in_progress'",
                (now.isoformat(), _tid),
            )
            finished_rows.append((_tid, _reason))
            print(
                f"  Kanban: stale-reaper found {_tid} already has an open PR "
                f"(in_progress {_age / 60:.0f} min) -> pr_opened (no failure recorded)"
            )

        if reaped or finished_rows:
            conn.commit()
            reaped_ids = [r[0] for r in reaped]
            if reaped_ids:
                logger.info(
                    "stale-reaper: reset %d orphaned in_progress task(s): %s",
                    len(reaped_ids), reaped_ids,
                )
            for _tid, _rsn in finished_rows:
                logger.info("stale-reaper: %s", _rsn)
                _record_status_transition(
                    _tid, "in_progress", "pr_opened",
                    actor="stale-reaper",
                    reason=_rsn,
                )
            # Guard: emit audit-log transitions for every reaped task.
            # The direct SQL UPDATE above bypasses _move_task, so we must
            # call _record_status_transition here to keep the forensic trail
            # intact — especially for 'suggested' escalations which previously
            # had no audit entry (the /quality-scores stale-cleanup incident).
            for _tid, _nst, _rsn in reaped:
                _record_status_transition(
                    _tid, "in_progress", _nst,
                    actor="stale-reaper",
                    reason=_rsn,
                )
    except Exception as exc:
        logger.warning("stale-reaper sweep error: %s", exc)
    finally:
        if conn is not None:
            conn.close()


# Startup-recovery flag: True after the first cycle's stale-in_progress sweep runs.
_startup_recovery_done: bool = False


def _startup_recover_stale_in_progress() -> None:
    """On first cycle after a scheduler restart, reset any tasks stuck in
    'in_progress' back to 'backlog'.  After a crash, _running is empty but
    the DB still has rows from the previous session — they will never be
    promoted or timed-out without this sweep.

    Policy lives in ``tools/kanban/startup_recovery.py``, shared with the
    scheduler entrypoint's own sweep. Both run on a restart (this one on cycle
    1), so a liveness guard in only one of them buys nothing — the other resets
    the live task a minute later. That module also stops writing
    ``last_failure_reason`` for an interruption: it is not a failure, and the
    reason column is what pulls a task into ``failure_triage``'s autofix queue.
    """
    global _startup_recovery_done
    if _startup_recovery_done:
        return

    # Hard guard: this sweep resets EVERY in_progress row that is not provably
    # live. Run from a second process (the heartbeat daemon's wakeup, a dashboard
    # reflex trigger, an interactive --once) it would sweep the owning
    # scheduler's board, and its _running is not visible from here.
    _foreign = _foreign_scheduler_pid()
    if _foreign:
        logger.info(
            "startup-recovery: skipped — scheduler pid=%d owns the runner", _foreign,
        )
        return

    _startup_recovery_done = True
    try:
        from tools.kanban.startup_recovery import recover_interrupted_tasks

        result = recover_interrupted_tasks(
            running_ids=set(_running),
            # Ownership was just settled above via _foreign_scheduler_pid, which
            # resolves the lockfile from the MAIN worktree; re-asking would only
            # re-derive it from a second anchor.
            respect_foreign_owner=False,
            # Bind the sweep to THIS module's connection factory so a caller that
            # redirected get_connection (tests, a scoped operator run) is not
            # silently swept against the ambient database instead.
            conn_factory=get_connection,
        )
        for entry in result["reset"]:
            print(
                f"  Kanban: startup-recovery reset {entry['id']} in_progress -> "
                f"backlog ({entry['provenance']['summary']})"
            )
        for held in result["held"]:
            print(f"  Kanban: startup-recovery HELD {held['id']} — {held['detail']}")
        for handed in (result.get("handed_to_pr_watcher") or {}).get("handed", []):
            print(
                f"  Kanban: startup-recovery handed {handed['id']} token_exhausted "
                f"-> pr_opened (open PR #{handed['pr_number']}; pr_watcher owns it)"
            )
    except Exception as exc:
        logger.warning("startup-recovery sweep failed: %s", exc)


def _check_completed():
    """Check for completed claude subprocesses and clean up.

    Also enforces MAX_EXECUTION_SECONDS timeout — kills hung processes
    and returns them to backlog.
    """
    # Reconcile agent invocations against reality before doing anything else.
    # Eight different paths remove a task from _running (timeout kill, stale
    # cleanup, zombie reclaim, ...), and instrumenting each one would be a
    # standing invitation to miss the ninth. Closing whatever no longer has a
    # live process is one rule that covers all of them, and it runs every cycle.
    for _stale_id in [t for t in _agent_invocations if t not in _running]:
        _close_agent_invocation(
            _agent_invocations.pop(_stale_id, None),
            status="error",
            error_class="abandoned",
            error_message="process left _running without the completion path closing it",
        )

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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)

                # ── SCAN-ONLY TIMEOUT ACCEPTANCE ─────────────────────
                # Scan tasks (pytest, codelens, coherence, companion) are
                # read-only: they produce no git commits and Claude CLI's
                # --output-format text yields empty stdout when killed. So the
                # usual evidence is unavailable for exactly these runs, and
                # without some allowance a scan that really did finish gets
                # retried forever.
                #
                # The old allowance was `elapsed > task_budget * 0.9`, on the
                # reasoning that a task which burned ~all its budget had
                # probably finished the command and died while formatting. That
                # test could never fail: this whole block is reached ONLY from
                # `if elapsed > task_budget`, so `elapsed > task_budget * 0.9`
                # is a tautology. In practice the rule was "any task_type=test
                # whose description mentions pytest/coherence/companion is DONE
                # when it times out" — with no evidence of any kind.
                #
                # It fired on hgx-vv-01 (2026-08-09), HGX's end-to-end
                # verification task: killed at 3641s of a 3600s budget, marked
                # done, zero output, nothing on a branch, and the card read
                # 38/38 on a proof that did not exist.
                #
                # Duration is not evidence — past the budget, a LONGER run means
                # more certainly killed, not more certainly finished. Require
                # the one durable trace a scan leaves: its result artifact in
                # .tmp/, which is written by the command itself and survives the
                # agent being killed. No artifact -> fall through to the
                # timeout-retry/quarantine path below, which is what it is for.
                _SCAN_KW_TIMEOUT = ["pytest", "codelens", "coherence",
                                    "companion", "report pass/fail", "behave"]
                try:
                    with get_connection() as _stc:
                        _str = _stc.execute(
                            "SELECT description, task_type FROM kanban_tasks WHERE id = %s",
                            (task_id,),
                        ).fetchone()
                    _stdesc = ((_str["description"] or "").lower() if _str else "")
                    _sttype = ((_str["task_type"] or "").lower() if _str else "")
                except Exception:
                    _stdesc = ""
                    _sttype = ""
                _scan_artifact = (
                    _scan_result_artifact(task_id)
                    if (_sttype == "test"
                        and any(kw in _stdesc for kw in _SCAN_KW_TIMEOUT))
                    else None
                )
                _is_scan_timeout = _scan_artifact is not None
                if _is_scan_timeout:
                    _move_task(task_id, "done", actor="scheduler",
                              reason=(f"Verified (scan-only timeout): ran {int(elapsed)}s "
                                      f"(budget {task_budget}s), scan artifact "
                                      f"{_scan_artifact.name} on disk — read-only "
                                      f"validation task accepted without git commits"))
                    print(
                        f"  Kanban: {task_id} SCAN-ONLY ACCEPTED — "
                        f"ran {int(elapsed)}s of {task_budget}s budget, "
                        f"artifact {_scan_artifact.name}"
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
                # Skip demotion when the work ALREADY EXISTS. Two ways it can:
                #
                #  1. the task reached a terminal-ish status while this zombie
                #     ran (`done` externally, or `pr_opened` because the session
                #     got its PR up), or
                #  2. the session produced commits on kanban/<task_id> and then
                #     overran the budget before or during the PR wait — the
                #     status may still read `in_progress`, and the branch is the
                #     only evidence left.
                #
                # Demoting either case to `scheduled` re-dispatches a task whose
                # output is already sitting in an open, green PR, and the retry
                # rebuilds it from scratch. Observed 2026-08-15 on
                # trust-struct-03: PR #1679 was open with EVERY check passing
                # (E2E included) while the board had the task back in
                # `scheduled`, counting a failure against it. Nothing reported
                # the contradiction — a board saying "retry this" and a forge
                # saying "this is done" are equally confident and only one is
                # right.
                #
                # The branch check is the same merge-verification primitive the
                # done-gate uses, so dispatch and completion agree on what
                # "there is work here" means, and it is repo-aware and
                # fail-OPEN: an unreachable git returns False and the ordinary
                # demotion proceeds.
                try:
                    with get_connection() as _done_chk:
                        _done_row = _done_chk.execute(
                            "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,),
                        ).fetchone()
                    _cur_status = dict(_done_row)["status"] if _done_row else ""
                    _skip_reason = timeout_demotion_skip_reason(task_id, _cur_status)
                    if _skip_reason:
                        logger.info(
                            "timeout handler: %s not demoted — %s; leaving status %r "
                            "so the open PR is landed rather than rebuilt",
                            task_id, _skip_reason, _cur_status,
                        )
                        del _running[task_id]
                        _dispatch_times.pop(task_id, None)
                        if task_id in _worktrees:
                            _cleanup_worktree(task_id)
                            del _worktrees[task_id]
                        completed.append(task_id)
                        continue
                except Exception as _dc_exc:
                    logger.warning("done-check in timeout handler failed for %s: %s", task_id, _dc_exc)
                # Increment failure_count so the task health signal is accurate
                try:
                    _fc_now = datetime.now(timezone.utc).isoformat()
                    with get_connection() as _fc_conn:
                        _fc_conn.execute(
                            "UPDATE kanban_tasks SET "
                            "failure_count = COALESCE(failure_count, 0) + 1, "
                            "last_failure_reason = %s, last_failure_at = %s "
                            "WHERE id = %s AND status != 'done'",
                            (_timeout_reason, _fc_now, task_id),
                        )
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
                    _move_task(
                        task_id, "backlog", actor="scheduler",
                        reason=f"timeout {_tout_count}/{MAX_TIMEOUT_RETRIES} — "
                               + _timeout_reason,
                    )
                    # Backoff delay: 5 min × retry count before next dispatch.
                    # Prevents a structurally-slow task from immediately burning
                    # another 900 s slot on the very next scheduler cycle.
                    _backoff_seconds = _tout_count * 5 * 60
                    _backoff_at = (datetime.now(timezone.utc) + timedelta(seconds=_backoff_seconds)).isoformat()
                    try:
                        with get_connection() as _bo_conn:
                            _bo_conn.execute(
                                "UPDATE kanban_tasks SET scheduled_at = %s WHERE id = %s",
                                (_backoff_at, task_id),
                            )
                    except Exception as _bo_exc:
                        logger.warning("backoff scheduled_at update failed for %s: %s", task_id, _bo_exc)
                    print(
                        f"  Kanban: {task_id} timeout {_tout_count}/"
                        f"{MAX_TIMEOUT_RETRIES} — backoff {_backoff_seconds // 60} min"
                    )
                # Build task dict for notification
                task_dict = {"id": task_id, "title": task_id}
                try:
                    with get_connection() as task_conn:
                        row = task_conn.execute(
                            "SELECT title FROM kanban_tasks WHERE id = %s",
                            (task_id,),
                        ).fetchone()
                    if row:
                        task_dict["title"] = row["title"]
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
                # self-debug reflex: timeouts are their own recurrence class
                try:
                    from tools.workflow.self_debug import check_and_diagnose
                    work_dir = _work_dir_for(task_id)
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
            # Close the agent invocation opened at dispatch. Done FIRST, before
            # verification/remediation/merge, so duration_ms is the agent's own
            # wall-clock and not the pipeline's — the same reason
            # _close_execution is called early below.
            _close_agent_invocation(
                _agent_invocations.pop(task_id, None),
                status="ok" if ret == 0 else "error",
                error_class=None if ret == 0 else f"exit_{ret}",
            )
            _audit_agent_execution(
                "agent_execution_completed" if ret == 0 else "agent_execution_failed",
                task_id,
                exit_code=ret,
                executor="claude-cli",
            )
            # Continuous Harness feed — the claude-cli executor is the PRIMARY
            # build path but records nothing at dispatch, so its later
            # record_outcome() would attach to no decision row and codegen
            # metrics would only ever see fallback builds. Record the codegen
            # decision here, at the point the subprocess finishes, mirroring the
            # LLMRouter executor's record_decision. Guard on isinstance(Popen)
            # so the _LLMTaskHandle paths (LLMRouter / rubric loop), which
            # already self-record at dispatch, are not double-counted.
            if isinstance(proc, subprocess.Popen):
                try:
                    from tools.genesis.harness.eval_harness import record_decision
                    record_decision(
                        task_id=task_id,
                        reflex="codegen",
                        decision="done" if ret == 0 else "error_nonzero_exit",
                        confidence=0.6 if ret == 0 else 0.3,
                        metadata={
                            "executor": "claude_cli",
                            "returncode": ret,
                            "completed": ret == 0,
                        },
                    )
                except Exception as _hd_exc:
                    logger.warning(
                        "harness record_decision skipped for %s: %s", task_id, _hd_exc
                    )
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

            # Close the execution row here, at the point the subprocess exits —
            # before verification, remediation or merge, which can each take
            # minutes and are not the agent's build time. This is what makes
            # execution_seconds mean "how long the agent ran" and lets
            # _detect_execution_anomalies adapt timeouts instead of falling back
            # to the static constants.
            _close_execution(
                task_id,
                status="completed" if ret == 0 else "failed",
                exit_code=ret,
                output_summary=claude_output[-2000:] if claude_output else "",
            )

            # Build task dict with title from DB or fallback
            task_dict = {"id": task_id, "title": task_id}
            try:
                with get_connection() as task_conn:
                    row = task_conn.execute(
                        "SELECT title, task_type, priority FROM kanban_tasks WHERE id = %s",
                        (task_id,),
                    ).fetchone()
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

                # Decide SIZE here, before deciding when to retry. This is the
                # moment the system learns a task did not fit in a session, and
                # it is the only measurement of task size it ever gets.
                #
                # The give-up branch below is NOT a substitute: TOKEN_MAX_RETRY_COUNT
                # is 60 (~5h of retries), so a task can park 46 separate times --
                # tsr-dash-01-d3 did -- and still be "under budget", never
                # reaching a branch that reconsiders its size. That is how 240
                # re-dispatches of already-too-big tasks accumulated while the
                # LLM decomposer sat idle.
                #
                # Counted over the LIFETIME from kanban_status_transitions, not
                # from retry_count: the give-up branch clears that counter, so a
                # task returning for its second budget starts at zero and every
                # pass looks like a first attempt.
                _lifetime_exh = _lifetime_exhaustion_count(task_id)
                if _lifetime_exh >= EXHAUSTIONS_BEFORE_DECOMPOSITION:
                    logger.warning(
                        "Task %s has exhausted tokens %d times — decomposing "
                        "instead of parking for retry %d/%d",
                        task_id, _lifetime_exh, retry_count, TOKEN_MAX_RETRY_COUNT,
                    )
                    _move_task(
                        task_id, "needs_decomposition", actor="scheduler",
                        reason=(f"token-exhausted {_lifetime_exh}x lifetime "
                                f"(>= {EXHAUSTIONS_BEFORE_DECOMPOSITION}): too large for one "
                                f"session — decompose rather than retry unchanged"),
                    )
                    _clear_retry_count(task_id)
                    _clear_resume_at(task_id)
                    _send_notification(task_dict, event="needs_decomposition")
                    print(f"  Kanban: {task_id} exhausted {_lifetime_exh}x — "
                          f"flagged needs_decomposition")
                elif retry_count >= TOKEN_MAX_RETRY_COUNT:
                    # Exceeded max retries — move to backlog, give up
                    _move_task(
                        task_id, "backlog", actor="scheduler",
                        reason=f"token exhaustion: gave up after {retry_count} "
                               f"retries (max {TOKEN_MAX_RETRY_COUNT})",
                    )
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
                    _move_task(task_id, "token_exhausted",
                               reason=(f"token exhaustion: parked for retry "
                                       f"{retry_count + 1}/{TOKEN_MAX_RETRY_COUNT}"))
                    resume_at = _parse_resume_at(reset_hint)
                    _save_resume_at(task_id, resume_at)
                    # mfx-own-01: the exhaustion is RECORDED above (the lifetime
                    # count reads that transition), and then -- if this branch
                    # already carries an open PR -- the task is handed to
                    # pr_watcher rather than left for a retry the respawn guard
                    # would refuse every cycle. None when there is no such PR
                    # or the forge could not be asked; the park stands then.
                    _handed_pr = _hand_parked_task_to_pr_watcher(
                        task_id, context="at park",
                    )
                    wait_seconds = max(0, (resume_at - datetime.now(timezone.utc)).total_seconds())
                    wait_minutes = int(wait_seconds / 60) + 1
                    reset_msg = f" (reset hint: {reset_hint})" if reset_hint else ""
                    resume_local = resume_at.astimezone().strftime("%I:%M %p")

                    # D-AUTO-DEGRADE: Mark claude_cli as degraded.
                    #
                    # kax-exec-02: ONLY on evidence about the PROVIDER, never on
                    # the bare exit-code path. _detect_token_exhaustion returns
                    # True for any exit_code < 0 or >= 128, which is right for
                    # parking THIS task (an interrupted session should keep its
                    # branch) but says nothing about whether the executor still
                    # works — an operator killing one wedged session, an OOM, and
                    # a real quota all look identical there.
                    #
                    # Degrading is global and lasts 300s, so one such death used
                    # to remove the primary executor for EVERY other task. On
                    # 2026-08-12 a single operator kill quarantined exa-policy-08
                    # and exa-live-01 into `suggested` that way, and neither had a
                    # revive path (see _unblock_dep_chain). A genuine provider
                    # outage still degrades, because _TOKEN_RE matched real
                    # quota/rate text — and if it is genuine the very next
                    # dispatch observes it again anyway, so nothing is lost by
                    # requiring the stronger evidence.
                    executor_type = _get_executor_type(task_id) or "claude_cli"
                    _provider_evidence = bool(
                        claude_output and _TOKEN_RE.search(claude_output[-2000:])
                    )
                    if executor_type == "claude_cli" and _provider_evidence:
                        _degraded_executors.add(executor_type)
                        _degraded_executors_probed_at[executor_type] = resume_at
                        logger.info(
                            "kanban: executor %s degraded for %s (resume_at=%s)",
                            executor_type,
                            task_id,
                            resume_local,
                        )
                    elif executor_type == "claude_cli":
                        logger.info(
                            "kanban: %s parked (abnormal exit, no provider quota "
                            "evidence) — executor NOT degraded", task_id,
                        )

                    if _handed_pr is not None:
                        print(
                            f"  Kanban: {task_id} TOKEN EXHAUSTED{reset_msg} — "
                            f"open PR #{_handed_pr} on its branch, handed to "
                            f"pr_watcher as pr_opened (no scheduler retry)"
                        )
                    else:
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

                        if _handed_pr is not None:
                            _resume_line = (
                                f"Open PR #{_handed_pr} found on its branch — "
                                f"handed to pr_watcher (pr_opened); the "
                                f"scheduler will not retry it."
                            )
                        else:
                            _resume_line = (
                                f"Will auto-resume at {resume_local} "
                                f"(~{wait_minutes} min)."
                            )
                        tg_send(
                            f"Token limit: {task_dict.get('title', task_id)[:50]}",
                            (
                                f"Claude token/rate limit hit on retry "
                                f"{retry_count}/{TOKEN_MAX_RETRY_COUNT}."
                                f"{reset_msg}\n"
                                f"{_resume_line}"
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
                    # ── ADVERSARIAL VERIFY GATE ────────────────────────────
                    # For non_deterministic tasks with adversarial_enabled=1,
                    # spawn a second Claude CLI review session. If the verifier
                    # rejects, return to backlog with feedback injected so the
                    # next dispatch incorporates the verifier's critique.
                    _adv_work_dir = _worktrees.get(task_id, str(BASE_DIR))
                    _adv_passed, _adv_feedback = _run_adversarial_verify(
                        task_id, _adv_work_dir
                    )
                    if not _adv_passed:
                        _adv_reason = (
                            f"adversarial_verify: REJECTED — {_adv_feedback[:300]}"
                        )
                        logger.warning(
                            "adversarial gate blocked done for %s: %s",
                            task_id, _adv_feedback[:100],
                        )
                        print(
                            f"  Kanban: {task_id} ADVERSARIAL REJECTED "
                            f"— returning to backlog"
                        )
                        try:
                            _move_task(
                                task_id, "backlog",
                                actor="adversarial_verifier",
                                reason=_adv_reason,
                            )
                        except Exception as _adv_mt_exc:
                            logger.error(
                                "adversarial _move_task(backlog) failed for %s: %s",
                                task_id, _adv_mt_exc,
                            )
                        del _running[task_id]
                        _dispatch_times.pop(task_id, None)
                        continue

                    # In PR flow the work is NOT done until the PR MERGES. The
                    # PR is opened further down (_cleanup_worktree), so marking
                    # the task done HERE claims completion before the PR even
                    # exists — which is exactly what produced the
                    # REFUSED_done_unmerged transitions, and why the "Awaiting
                    # Merge" column never held a single task. Defer: the cleanup
                    # block below moves it to pr_opened once the PR is real, and
                    # pr_watcher moves it to done when the PR actually merges.
                    #
                    # A verified task with NO commits (a research/answer task)
                    # opens no PR and is genuinely done right now.
                    _will_open_pr = (
                        _pr_flow_enabled()
                        and task_id in _worktrees
                        and _check_worktree_commits(task_id)
                    )
                    if not _will_open_pr:
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
                    # ── LESSONS LEARNED: success ───────────────────────────────
                    try:
                        from tools.workflow.lesson_learned import analyze_task, write_lesson, maybe_create_remediation_card
                        lesson = analyze_task(task_id, outcome="success")
                        write_lesson(lesson)
                        maybe_create_remediation_card(lesson)
                    except Exception:
                        pass
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
                        # ── LESSONS LEARNED: failure ──────────────────────────────
                        try:
                            from tools.workflow.lesson_learned import analyze_task, write_lesson, maybe_create_remediation_card
                            lesson = analyze_task(task_id, outcome="failure")
                            write_lesson(lesson)
                            maybe_create_remediation_card(lesson)
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
                _clear_timeout_count(task_id)
                print(f"  Kanban: {task_id} completed (exit {ret}, verified={verified})")

                # ── WORKTREE CLEANUP (only on verified done) ─────────
                if verified and task_id in _worktrees:
                    has_commits = _check_worktree_commits(task_id)
                    if has_commits:
                        print(f"  Kanban: worktree kanban/{task_id} has new commits (review before merging)")
                    _cleanup_worktree(task_id)
                    del _worktrees[task_id]

                    # _cleanup_worktree pushed the branch and opened the PR.
                    # Reflect that on the board: the task is awaiting merge, not
                    # done. pr_watcher takes it from here.
                    if _pr_flow_enabled() and has_commits:
                        # THREE answers, not two. "gh says there is no PR" and
                        # "gh could not be reached" are different facts and this
                        # branch used to act on them identically — rolling the
                        # task back to backlog either way, with real commits
                        # already pushed. That was 66 of the 126 backwards
                        # transitions on this board (kpr-dup-06).
                        _state = _pr_open_state(task_id)
                        _target, _reason = _pr_flow_outcome(_state)
                        if _target is None:
                            # Leave it exactly where it is. pr_linker links the
                            # PR if one opened; the stale reaper still reaps a
                            # dispatch that genuinely died. Rolling back is the
                            # only option here that destroys work.
                            logger.warning(
                                "PR flow: %s — %s", task_id, _reason)
                        else:
                            _move_task(
                                task_id, _target, actor="scheduler",
                                reason=_reason,
                            )
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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
                print(f"  Kanban: {task_id} failed (exit {ret}){': ' + error_tail[:200] if error_tail else ''}")
                # Preserve worktree for debugging/retry — do NOT clean up
                if task_id in _worktrees:
                    print(f"  Kanban: preserving worktree for failed task {task_id}")
                try:
                    _move_task(
                        task_id, "backlog", actor="scheduler",
                        reason=(f"claude CLI exited {ret}"
                                + (f": {error_tail.strip()[:300]}" if error_tail.strip() else "")),
                    )
                    _send_notification(task_dict, event="failed")
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
            del _running[task_id]
    return completed


#: Lifetime token-exhaustions after which a task is decomposed rather than
#: re-queued unchanged. Override via KANBAN_EXHAUSTIONS_BEFORE_DECOMPOSITION.
#:
#: 2, not 3. One exhaustion can be an unlucky session — a long but tractable
#: task, or a session that spent its budget exploring. The SECOND is a repeat
#: measurement of the same task against the same budget, and by then the board
#: has paid twice for the same unfinished work. Set against the measured
#: distribution: 49 tasks have exhausted at least twice (338 events) and 29 at
#: least three times (298 events), so a threshold of 3 would still have let ~40
#: pointless re-dispatches through on this board alone.
EXHAUSTIONS_BEFORE_DECOMPOSITION = _int_env(
    "KANBAN_EXHAUSTIONS_BEFORE_DECOMPOSITION", 2)


#: How many times a task may be split before a human is asked instead.
#: Override via KANBAN_MAX_DECOMPOSITION_DEPTH.
#:
#: 2 is a real constraint, not a nominal one: depth 3 ALREADY exists on this
#: board (501 tasks at depth 1, 63 at depth 2, 11 at depth 3, e.g.
#: ci-fix-27599865917-d3-d3-d1), reached through the older verification-failure
#: path. Wiring token exhaustion into the decomposer adds a far more frequent
#: trigger -- 402 exhaustion events historically -- so without a cap the trees
#: get deeper and wider on exactly the tasks that fire it most.
#:
#: A task split twice that still does not fit is not converging, and a third
#: split is guessing.
MAX_DECOMPOSITION_DEPTH = _int_env("KANBAN_MAX_DECOMPOSITION_DEPTH", 2)

#: A file this large makes a task context-bound rather than large in scope.
#: Override via KANBAN_LARGE_FILE_LINES.
#:
#: Measured 2026-08-15 across 5,387 files under tools/: only 9 (0.2%) exceed
#: 5,000 lines, so this flags the genuine context hogs and nothing else. The
#: largest is tools/db/schema/pg_consolidated.sql at 63,970 lines.
LARGE_FILE_LINES = _int_env("KANBAN_LARGE_FILE_LINES", 5000)

#: Same vocabulary _complexity_score scans for, plus `sql` — the extension of
#: the single largest file in the repo, and the one that produced the case this
#: guard exists for.
_TASK_FILE_RE = re.compile(r"[\w/\-]+\.(?:py|html|yaml|yml|md|ts|js|go|sql)")


def _decomposition_depth(task_id: str) -> int:
    """How many times this task id has already been split.

    Children are minted as ``f"{parent}-d{i}"`` by _decompose_one_task, so the
    id carries its own lineage and no extra column is needed:
    ``ci-fix-27599865917-d3-d3-d1`` is depth 3.
    """
    try:
        return len(re.findall(r"-d\d+", task_id or ""))
    except Exception:  # noqa: BLE001 — an unparseable id must not block a split
        return 0


def _oversized_files_in(description: str) -> list:
    """Files named by the task that are too large to load into a session.

    Returns ``[(path, line_count), ...]`` for anything over LARGE_FILE_LINES.

    THE CASE THIS EXISTS FOR: trust-anchor-03 is "add audit_chain_genesis to
    pg_consolidated.sql" -- 107 words, _complexity_score 0 -- and it exhausted
    its token budget. pg_consolidated.sql is 63,970 lines. The cost is the
    context the task must LOAD, not the work it must do, so every child of a
    split inherits it identically: decomposing that produces subtasks that each
    open the same file and exhaust the same way, and each of those decomposes
    again. Splitting cannot help, and saying so is more useful than a fork bomb.

    FAIL-OPEN: an unreadable or missing path is skipped, never counted. A broken
    probe must not block a legitimate decomposition.
    """
    out = []
    if not description:
        return out
    seen = set()
    for rel in _TASK_FILE_RE.findall(description):
        rel = rel.strip().lstrip("./")
        if rel in seen:
            continue
        seen.add(rel)
        try:
            path = BASE_DIR / rel
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                n = sum(1 for _ in fh)
            if n >= LARGE_FILE_LINES:
                out.append((rel, n))
        except Exception:  # noqa: BLE001 — probing must never raise
            continue
    return out


def decomposition_refusal_reason(task_id: str, description: str = "") -> str:
    """Why this task must NOT be split, or "" when splitting is fine.

    Two deterministic refusals -- no LLM, no network, decided from the id, the
    description and os.stat:

      * it has already been split MAX_DECOMPOSITION_DEPTH times, or
      * it names a file too large to fit in a session, so every child would
        inherit the same cost.

    Both FAIL OPEN: anything unexpected returns "" (no objection), because a
    guard that errors closed would stop the decomposer working at all.
    """
    try:
        depth = _decomposition_depth(task_id)
        if depth >= MAX_DECOMPOSITION_DEPTH:
            return (f"already decomposed {depth}x (max {MAX_DECOMPOSITION_DEPTH}) "
                    f"— splitting further is guessing, not converging")
        big = _oversized_files_in(description or "")
        if big:
            rel, n = big[0]
            return (f"context-bound: {rel} is {n:,} lines (>= {LARGE_FILE_LINES:,}) "
                    f"— every subtask would load it too; needs a targeted script, "
                    f"not a smaller scope")
    except Exception as exc:  # noqa: BLE001
        logger.debug("decomposition_refusal_reason(%s) failed: %s", task_id, exc)
    return ""


def _lifetime_exhaustion_count(task_id: str) -> int:
    """How many times this task has EVER entered ``token_exhausted``.

    Read from ``kanban_status_transitions`` rather than the per-attempt retry
    counter, which ``_clear_retry_count`` wipes on the give-up path — so the
    counter is structurally incapable of seeing that a task has been round the
    loop before. Deriving from the transition log needs no new column and cannot
    drift from the board's actual history.

    FAIL-OPEN: any error returns 0, which routes the task down the pre-existing
    backlog path. A missing or unreadable transition log must never turn into a
    decomposition nobody asked for.
    """
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM kanban_status_transitions "
                "WHERE task_id = %s AND to_status = 'token_exhausted'",
                (task_id,),
            ).fetchone()
            if not row:
                return 0
            val = dict(row).get("n")
            return int(val) if val is not None else 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — counting must never wedge dispatch
        logger.debug("lifetime exhaustion count failed for %s: %s", task_id, exc)
        return 0


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

            # 0. An OPEN PR on this branch means pr_watcher owns the task, not
            #    the retry loop (mfx-own-01). Asked BEFORE the resume_at wait:
            #    the watcher can act on a red PR now, and a scheduler retry of
            #    this task would only ever be refused by the respawn guard and
            #    backed off again -- the six-hour slide measured 2026-09-03.
            #    Costs nothing extra: the open-PR listing is cached per cycle.
            try:
                if _hand_parked_task_to_pr_watcher(
                    task_id, context="retry evaluation",
                ) is not None:
                    continue
            except Exception as _ho_exc:  # noqa: BLE001 — never wedge the sweep
                logger.debug("parked hand-off check failed for %s: %s", task_id, _ho_exc)

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

            # 4a. Per-task circuit breaker — if failure_count has already hit
            #     max_retries the dispatch function would immediately re-park this
            #     task as token_exhausted (refreshing updated_at), causing an
            #     infinite spin loop.  Catch it here instead and send to
            #     'suggested' for HITL review so the board stays clean.
            _task_max_retries = int(task.get("max_retries") or 5)
            _task_failures = int(task.get("failure_count") or 0)
            if _task_failures >= _task_max_retries:
                logger.warning(
                    "Task %s circuit-broken (fc=%d >= max=%d) — parking in 'suggested' for HITL",
                    task_id, _task_failures, _task_max_retries,
                )
                _move_task(task_id, "suggested",
                           reason=(f"circuit-broken: fc={_task_failures} >= max="
                                   f"{_task_max_retries} — parked for HITL review"))
                _clear_retry_count(task_id)
                _clear_resume_at(task_id)
                _send_notification(task, event="circuit_broken")
                continue

            # 4b. Check token-exhaustion retry count — give up after TOKEN_MAX_RETRY_COUNT
            retry_count = _get_retry_count(task_id)
            if retry_count >= TOKEN_MAX_RETRY_COUNT:
                # Where "give up" sends it decides whether the task ever gets
                # SMALLER. Sending it to `backlog` and clearing the counter
                # restarts the identical cycle -- dispatch, exhaust, retry N
                # times, back to backlog -- with the task unchanged. Measured
                # 2026-08-15: tsr-dash-01-d3 went round it 46 times, aca-trn-01
                # 26, and across the board 29 tasks exhausted 3+ times for 298
                # events. 240 of those dispatches were re-runs of a task already
                # measured too big, and only 5 of the 29 were ever flagged for
                # decomposition -- none by this path.
                #
                # Token exhaustion is the ONLY ground-truth measurement of task
                # size this system produces. The pre-dispatch _complexity_score
                # cannot substitute: it scores description VERBOSITY (words,
                # bullets, file paths), so on the three tasks that exhausted here
                # it returned 2, 1 and 0 against a threshold of 7, while the
                # highest scorer (5) completed successfully. It measures how much
                # the author wrote, not how much work there is.
                #
                # So a repeat offender goes to `needs_decomposition` and gets
                # LLM-split into subtasks instead. The count comes from
                # kanban_status_transitions rather than _get_retry_count because
                # that counter is cleared on this very path and therefore cannot
                # see a lifetime pattern -- which is exactly why 46 identical
                # retries never looked like anything but a first attempt.
                _lifetime = _lifetime_exhaustion_count(task_id)
                if _lifetime >= EXHAUSTIONS_BEFORE_DECOMPOSITION:
                    logger.warning(
                        "Task %s exhausted tokens %d times over its lifetime — "
                        "flagging needs_decomposition instead of re-queuing it unchanged",
                        task_id, _lifetime,
                    )
                    _move_task(
                        task_id, "needs_decomposition", actor="scheduler",
                        reason=(f"token-exhausted {_lifetime}x lifetime "
                                f"(>= {EXHAUSTIONS_BEFORE_DECOMPOSITION}): too large for one "
                                f"session — decompose rather than retry unchanged"),
                    )
                    _clear_retry_count(task_id)
                    _clear_resume_at(task_id)
                    _send_notification(task, event="needs_decomposition")
                    continue

                logger.info(
                    "Task %s exceeded max retries (%d) — moving to backlog",
                    task_id,
                    TOKEN_MAX_RETRY_COUNT,
                )
                _move_task(
                    task_id, "backlog", actor="scheduler",
                    reason=f"token-retry budget exhausted: {retry_count} retries "
                           f"(max {TOKEN_MAX_RETRY_COUNT})",
                )
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

            # Splitting is not always the right answer, and splitting FOREVER
            # never is. Checked after the chain-blocker rule, which keeps its
            # precedence, and before any LLM call so a refusal costs nothing.
            refusal = decomposition_refusal_reason(tid, task.get("description"))
            if refusal:
                logger.warning("auto_decompose: refusing to split %s — %s", tid, refusal)
                _move_task(
                    tid, "suggested", actor="scheduler",
                    reason=f"not decomposed: {refusal} — needs a human decision",
                )
                _send_notification(task, event="decomposition_refused")
                print(f"  Kanban: {tid} NOT split — {refusal}")
                processed.append(tid)
                continue

            _decompose_one_task(task, ai_narrative=True)
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
                "SELECT 1 FROM kanban_tasks WHERE depends_on_task_id = %s LIMIT 1",
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
            "last_failure_reason = %s, "
            # Back-date updated_at so the 10-min cooldown passes immediately
            "updated_at = datetime('now', '-11 minutes') "
            "WHERE id = %s AND status = 'needs_decomposition'",
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


# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5304): optional LLM-synthesized decomposition
# narrative (metadata_extraction → llm_generation).
#
# _decompose_one_task already uses an LLM to decide *how* to split a task.
# This companion helper synthesises a short, grounded narrative that explains
# *what* was decided — useful for Telegram notifications, audit trails, and
# operator dashboards. Any failure degrades silently so the deterministic
# decomposition always completes.
# ---------------------------------------------------------------------------

_DECOMPOSE_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC engineering lead writing a brief decomposition note for "
    "a kanban task. Write a concise narrative (2-4 sentences) that: "
    "(1) states what the original task was and why it needed splitting, "
    "(2) describes the resulting subtasks and their execution order, and "
    "(3) highlights the single most important risk or dependency to watch. "
    "Use only the facts provided — never invent task IDs, titles, or counts. "
    "Output only the narrative prose; no headers, no markdown, no preamble."
)


def _ai_decompose_narrative(task_id: str, facts: dict) -> str | None:
    """Synthesize an optional LLM narrative for a completed task decomposition.

    Args:
        task_id: The parent task ID being decomposed. Used for log context only;
            the model receives it through ``facts``.
        facts: Grounding facts already derived from the deterministic
            decomposition (task title, subtask count, subtask titles, failure
            reason if any). Passed verbatim so the narrative cannot drift from
            the authoritative result.

    Returns:
        A short narrative string, or ``None`` if generation is unavailable or
        fails for any reason. Callers MUST treat ``None`` as "no narrative"
        and proceed with the deterministic decomposition unchanged.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        fact_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(facts.items()))
        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Task decomposition: {task_id}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the decomposition narrative."
                    ),
                }
            ],
            system_prompt=_DECOMPOSE_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.3,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass
    return None


def _decompose_one_task(task: dict, ai_narrative: bool = False) -> dict:
    """Call LLM to decompose a single needs_decomposition task into subtasks.

    Args:
        task: Kanban task row dict containing at minimum ``id``.
        ai_narrative: When ``True``, an optional LLM narrative is synthesized
            from the decomposition facts and returned under the ``narrative``
            key. Defaults to ``False`` for backward compatibility.

    Returns:
        Dict with keys ``subtasks`` (list of inserted child IDs) and
        ``narrative`` (str or None).
    """
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
        import concurrent.futures as _cf
        router = LLMRouter()
        req = LLMRequest(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1200,
        )
        _pool = _cf.ThreadPoolExecutor(max_workers=1)
        _future = _pool.submit(router.invoke, "kanban_decompose", req)
        try:
            raw = _future.result(timeout=45)
        except _cf.TimeoutError:
            # shutdown(wait=False) releases the thread without blocking — the
            # context-manager form calls shutdown(wait=True) which blocks
            # forever if the underlying LLM network call never returns.
            _pool.shutdown(wait=False)
            raise RuntimeError("LLM invoke timed out after 45s") from None
        finally:
            _pool.shutdown(wait=False)
        if isinstance(raw, str):
            response_text = raw
        elif hasattr(raw, "content"):
            response_text = raw.content
        else:
            response_text = str(raw)
    except RuntimeError:
        raise
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
            "UPDATE kanban_tasks SET status = 'decomposed', updated_at = %s WHERE id = %s",
            (now, tid),
        )

        inserted = []
        # The parent's own dependency — inherited by the first child below, so a
        # gated parent cannot be decomposed into an ungated chain.
        parent_dep = task.get("depends_on_task_id")

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

            # depends_on_task_id: chain children sequentially so they run in
            # order (each depends on the previous). The FIRST child inherits the
            # PARENT's dependency — otherwise decomposing a gated task produces a
            # child with no dep at all, and the whole chain walks straight around
            # the gate that was holding it (observed: prem-bid-01 -> -d1 dispatched
            # despite prem-gate-00).
            dep = inserted[-1] if inserted else parent_dep

            conn.execute(
                "INSERT OR IGNORE INTO kanban_tasks "
                "(id, title, description, priority, task_type, status, "
                " executor_type, depends_on_task_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 'backlog', 'claude_cli', %s, %s, %s)",
                (child_id, sub_title, sub_desc, sub_pri, sub_type, dep, now, now),
            )
            inserted.append(child_id)

        conn.commit()
        print(
            f"  Kanban: auto-decomposed {tid!r} into {len(inserted)} subtask(s): "
            + ", ".join(inserted)
        )

        # Optional LLM narrative (aiify-opp-5304: metadata_extraction → llm_generation)
        narrative: str | None = None
        if ai_narrative:
            sub_titles = [
                str(subtasks[i].get("title") or f"{title} — part {i+1}")[:80]
                for i in range(len(subtasks[:5]))
            ]
            narrative = _ai_decompose_narrative(tid, {
                "task_id": tid,
                "task_title": title,
                "subtask_count": len(inserted),
                "subtask_ids": ", ".join(inserted),
                "subtask_titles": "; ".join(sub_titles),
                "failure_reason": failure_reason if not is_upfront else "none (upfront decomposition)",
            })

        # Telegram notification
        try:
            import os as _os
            if not (_os.environ.get("PYTEST_CURRENT_TEST") or
                    _os.environ.get("ICDEV_SUPPRESS_NOTIFICATIONS") == "1"):
                from tools.notifications.adapters.telegram import send as tg_send
                body = (
                    f"Task {tid} was split into {len(inserted)} subtasks: "
                    + ", ".join(inserted)
                )
                if narrative:
                    body = f"{narrative}\n\nSubtasks: " + ", ".join(inserted)
                tg_send(
                    f"AUTO-DECOMPOSED: {title[:50]}",
                    body,
                    severity="info",
                )
        except Exception:
            pass

    finally:
        conn.close()

    return {"subtasks": inserted, "narrative": narrative}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Kanban Executor Reflex."""
    global _current_exec_tier, _LAST_SIBLING_HOLDS
    # Unmeasured until the admission actually runs this cycle (mfx-sib-01). A
    # stale list from the previous cycle read as this one's would be a count
    # nobody took, which is the defect the whole surrounding file guards against.
    _LAST_SIBLING_HOLDS = None
    tier = tier_resolver.resolve_tiers().exec_tier
    if tier != _current_exec_tier:
        logger.info("Executor tier changed to %s", tier)
        _current_exec_tier = tier

    # Fresh landed-check answers each cycle. This scheduler is long-lived and
    # main moves under it constantly; a memo that outlived its cycle would report
    # yesterday's merge state as today's, which is the exact class of staleness
    # the check exists to catch.
    clear_landed_cache()

    # 0. Promote dep-satisfied backlog tasks to SCHEDULED.
    #
    # tools/kanban/promote_backlog_to_scheduled.py existed but NOTHING ever called
    # it, so the "Scheduled" column had never held a task in the board's lifetime —
    # backlog went straight to in_progress and the board could not distinguish
    # "queued, still blocked" from "ready, waiting for a slot".
    #
    # This is a VISIBILITY change, not a dispatch change: _get_due_tasks already
    # picks up backlog AND scheduled under the same dependency clause, so exactly
    # the same tasks run. They are now simply visible as ready first.
    try:
        from tools.kanban.promote_backlog_to_scheduled import promote as _promote

        _promoted = _promote()
        if _promoted:
            print(f"  Kanban: promoted {len(_promoted)} backlog task(s) to scheduled")
    except Exception as _promo_exc:  # noqa: BLE001 — never block the cycle
        logger.warning("backlog->scheduled promotion failed: %s", _promo_exc)

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

    # 0c-bis. Heartbeat refresh — stamp last_heartbeat_at for every live
    # subprocess. Must run BEFORE the reaper below so a task that started this
    # cycle already has a beat on record. Cheap: at most MAX_IN_PROGRESS rows.
    try:
        _refresh_running_heartbeats()
    except Exception as _hb_exc:
        logger.warning("heartbeat refresh failed: %s", _hb_exc)

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
    # 0d-bis. Reconcile pre-PR tasks that already have an open PR (rem-hyg-18).
    # EVERY cycle, not sampled like the reaper above: the open-PR branch set is
    # already cached per cycle (`_open_pr_head_branches`), so this costs one
    # indexed SELECT over the handful of scheduled/backlog rows. Sampling it at
    # 1-in-30 would leave the board misreporting for up to half an hour, which
    # is the defect rather than a cheaper version of the fix.
    try:
        _reconcile_pr_opened()
    except Exception as _pro_exc:
        logger.warning("pr-opened reconciler failed: %s", _pro_exc)
    # 0e. GA completion poller — move completed GA runs to done/backlog and free slots.
    try:
        _poll_github_actions_completions()
    except Exception as _gp_exc:
        logger.warning("GA completion poll failed: %s", _gp_exc)

    # 0f. CI failure detector — every ~10 cycles (≈10 min) scan watched workflows
    # for failures and enqueue ci-fix-{run_id} tasks into the backlog.
    try:
        import random as _rcf  # noqa: PLC0415
        if _rcf.random() < 0.10:  # ~1-in-10 ≈ once per 10 min  # noqa: S311
            _detect_and_queue_ci_failures()
    except Exception as _cf_exc:
        logger.warning("CI failure detection failed: %s", _cf_exc)

    # Suggested-recovery sweep, on a deterministic interval (was a 0.4% coin
    # flip per cycle — an expected ~4 h wait with no upper bound, for tasks that
    # were never attempted). See _SUGGESTED_SWEEP_INTERVAL_SEC.
    try:
        if _suggested_sweep_due():
            _promote_stale_suggested()
    except Exception as _pss_exc:
        logger.warning("suggested-decay sweep failed: %s", _pss_exc)

    # 0g. Zombie reclaim — demote in_progress tasks that sent heartbeats but
    # have gone silent for >KANBAN_ZOMBIE_SILENCE_HOURS hours (default 2).
    # Only fires for tasks that have registered at least one heartbeat — tasks
    # dispatched before heartbeat support was added are left to the existing
    # stale-in_progress reaper (0d).
    try:
        _reclaim_zombie_tasks()
    except Exception as _zr_exc:
        logger.warning("zombie reclaim sweep failed: %s", _zr_exc)

    # 0h. Triage sweep — decompose any tasks in 'triage' status via LLM.
    # Runs every cycle (triage tasks are rare and decomposition is quick).
    try:
        conn = get_connection()
        try:
            triage_rows = conn.execute(
                "SELECT id, title, description, triage_prompt FROM kanban_tasks "
                "WHERE status = 'triage' LIMIT 3"
            ).fetchall()
        finally:
            conn.close()
        for trow in triage_rows:
            try:
                _decompose_triage_task(dict(trow))
            except Exception as _td_exc:
                logger.warning("triage decompose failed for %s: %s", dict(trow).get("id"), _td_exc)
    except Exception as _ts_exc:
        logger.warning("triage sweep failed: %s", _ts_exc)

    # 1. Check for completed claude subprocesses
    completed = _check_completed()

    # 2. Poll all channels for new commands
    tg_results = _poll_all_channels()
    if tg_results:
        print(f"  Kanban: {len(tg_results)} channel commands")

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
                with get_connection() as task_conn:
                    row = task_conn.execute(
                        "SELECT status FROM kanban_tasks WHERE id = %s", (tid,),
                    ).fetchone()
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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
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
                    with get_connection() as _fc_conn:
                        _fc_conn.execute(
                            "UPDATE kanban_tasks SET "
                            "  last_failure_reason = %s, "
                            "  last_failure_at = %s, "
                            "  updated_at = %s "
                            "WHERE id = %s",
                            (reason, now_iso, now_iso, tid),
                        )
                except Exception as _fc_exc:
                    logger.warning(
                        "stale-cleanup failure-write failed for %s: %s",
                        tid, _fc_exc,
                    )
                # ── LESSONS LEARNED: stale cleanup ──────────────────────────
                try:
                    from tools.workflow.lesson_learned import analyze_task, write_lesson, maybe_create_remediation_card
                    lesson = analyze_task(tid, outcome="stale_cleanup")
                    write_lesson(lesson)
                    maybe_create_remediation_card(lesson)
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)

                # Build a task dict for the local alert queue.
                task_dict = {"id": tid, "title": tid}
                try:
                    with get_connection() as _tc:
                        _tr = _tc.execute(
                            "SELECT title, task_type, priority FROM kanban_tasks "
                            "WHERE id = %s", (tid,),
                        ).fetchone()
                    if _tr:
                        _tr_d = dict(_tr)
                        task_dict = {
                            "id": tid,
                            "title": _tr_d.get("title") or tid,
                            "task_type": _tr_d.get("task_type"),
                            "priority": _tr_d.get("priority"),
                        }
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)

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
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)

        # Stale cleanup happened. This used to `return` here, deferring ALL
        # promotion to the next cycle so the quarantine state written by
        # check_and_diagnose could settle before the cleaned task was eligible
        # for re-dispatch again.
        #
        # The settling is already guaranteed, and more precisely, by the backlog
        # cooldown in _get_due_tasks: a task whose updated_at is within the last
        # 2 minutes is not selectable, and a task that was just cleaned always
        # is. (Verified against the live PostgreSQL board — the predicate
        # discriminates correctly rather than being a SQLite-ism that no-ops.)
        #
        # So the return bought nothing for the cleaned task and cost a full 60s
        # dispatch slot for every OTHER task on the board — which is the wrong
        # trade on a queue that is already idle 75% of the time. Cleanup is
        # reported; the cycle continues.
        if stale_info:
            print(f"  Kanban: stale-cleanup finished ({len(stale_info)} task(s)): "
                  f"{', '.join(t for t, _ in stale_info)} — continuing this cycle")

    # 3b. Concurrency gate — only block when we are at MAX_IN_PROGRESS.
    # With worktree isolation, multiple Claude CLI subprocesses can run
    # concurrently (each in its own directory).  The stale-cleanup sweep
    # above already removed any entries whose DB status changed, so
    # _running only contains genuinely live tasks.
    if len(_running) >= MAX_IN_PROGRESS:
        print(f"  Kanban: {len(_running)} task(s) executing (max {MAX_IN_PROGRESS}), waiting...")
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

    # 3c. Check for token-exhausted tasks ready for retry.
    # We retry ONE task here, but we do NOT return — the function continues
    # to normal promotion so remaining slots can be filled in the same cycle.
    token_retry_dispatched = False
    token_retry_tasks = _check_token_exhausted_tasks()
    if token_retry_tasks:
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
                _move_task(task["id"], "in_progress",
                           reason="token-retry: resume_at reached, re-dispatched to claude CLI")
                _send_notification(task, event="in_progress")
                token_retry_dispatched = True
            else:
                # A failed dispatch used to change nothing at all: the task
                # stayed token_exhausted with its resume_at already in the past,
                # so _check_token_exhausted_tasks handed back the SAME task on
                # the very next cycle, and the one token-retry this cycle allows
                # was spent on it again. One task did that 212 times in the
                # current log while every other parked task waited behind it.
                # Push resume_at out so the retry backs off and the queue moves.
                _token_retry_backoff(task["id"], retry_count)
        except Exception as e:
            print(f"  Kanban: token retry error for {task['id']}: {e}")
            try:
                _token_retry_backoff(task["id"], retry_count)
            except Exception:  # noqa: BLE001 — backoff must never break the cycle
                pass

    # If a token retry consumed the last available slot, skip normal promotion.
    if len(_running) >= MAX_IN_PROGRESS:
        return {
            "success": True,
            "metric_value": (1 if token_retry_dispatched else 0) + len(completed),
            "details": {
                "status": "token_retry" if token_retry_dispatched else "executing",
                "running": list(_running.keys()),
                "completed_this_cycle": completed,
                "telegram_commands": len(tg_results),
            },
        }

    # 3c. Auto-decompose tasks stuck at needs_decomposition
    try:
        decomposed = _auto_decompose_stalled_tasks()
        if decomposed:
            print(f"  Kanban: auto-decomposed {len(decomposed)} stalled task(s): {decomposed}")
            # ── LESSONS LEARNED: stalled auto-decompose ─────────────────
            for _tid in decomposed:
                try:
                    from tools.workflow.lesson_learned import analyze_task, write_lesson
                    lesson = analyze_task(_tid, outcome="auto_decomposed")
                    write_lesson(lesson)
                except Exception as _ll_exc:
                    logger.warning("lesson_learned hook failed: %s", _ll_exc)
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

    # Global runner-pause: if an interactive CLI session holds the pause lease
    # (`python -m tools.kanban.cli --pause-runner`), skip dispatch this cycle so
    # the autonomous runner and a human never build in parallel. holder() is a
    # non-mutating peek — the runner does not want to hold the pause, only detect
    # it. This is the clean answer to switching between kanban and CLI: exactly
    # one authority at a time, arbitrated by a lease that survives a model swap.
    try:
        from tools.coordination import leases as _leases
        from tools.coordination.constants import get_session_id as _gsid
        _pause = _leases.holder("kanban:runner:global")
        if _pause and _pause.get("holder_session") != _gsid():
            logger.info(
                "kanban runner paused by session %s — skipping dispatch this cycle",
                _pause.get("holder_session"),
            )
            return {
                "success": True,
                "metric_value": len(completed),
                "details": {
                    "status": "paused_by_session",
                    "holder": _pause.get("holder_session"),
                    "completed_this_cycle": completed,
                },
            }
    except Exception as _pause_exc:
        logger.debug("runner-pause check failed (continuing): %s", _pause_exc)

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
        # LEASE OWNERSHIP (kpr-stale-03). The per-task lease acquired below is
        # released by _move_task on a terminal/re-queue transition -- which only
        # happens if the task was ACTUALLY DISPATCHED. Every path that abandons
        # dispatch after the acquire used to leak it for the full 3600s TTL, and
        # the next cycle then refused the task through _drop_respawn_guarded
        # ("claimed by a live session") -- the scheduler starving itself.
        # Measured 2026-09-02: 20 tasks across three projects, board idle 8h+.
        #
        # The release is CONDITIONAL, never unconditional: a dispatched task must
        # KEEP its lease while the worker runs, or the double-build race that
        # rem-hyg-15 and kpr-dup-07 exist to prevent comes straight back.
        _task_lease = None
        _dispatch_started = False
        try:
            # Per-task coordination lease: claim exclusive ownership before spending
            # any tokens. If another session already owns this task (e.g. an
            # interactive CLI session working it out-of-band via `--claim`), skip it
            # — this is what prevents the runner and a human from double-building the
            # same task into divergent branches. The lease is released in _move_task
            # on terminal/re-queue transitions; its TTL is a backstop if the task
            # never terminates.
            try:
                from tools.coordination import leases as _leases
                _task_lease = _leases.acquire(
                    f"kanban:task:{task['id']}", intent="kanban-runner",
                    ttl_seconds=3600, block=False,
                )
                if _task_lease is None:
                    logger.info(
                        "kanban: task %s owned by another session — skipping", task["id"],
                    )
                    continue
            except Exception as _lease_exc:
                logger.debug(
                    "task-lease acquire failed for %s (continuing): %s", task["id"], _lease_exc,
                )

            # Pre-dispatch landed check (trust-disc-05): is this task id ALREADY on
            # origin/<default>? The board tracks task -> PR and nothing checked
            # task -> main, so a task whose work merged under a different PR number
            # got dispatched again and produced a second PR that could only land as
            # a revert. Advisory by default — it prints, logs, and goes into the
            # prompt — and refuses only under KANBAN_LANDED_CHECK=enforce.
            try:
                _landed = _landed_preflight(task["id"])
                if _landed.get("landed") or (_landed.get("prs") or {}).get("settles") is False:
                    from tools.kanban.landed_check import format_warning as _fmt_landed
                    _msg = _fmt_landed(_landed)
                    logger.warning("pre-dispatch landed check for %s:\n%s", task["id"], _msg)
                    print(f"  Kanban: pre-dispatch landed check fired for {task['id']!r}\n"
                          + "\n".join(f"    {ln}" for ln in _msg.splitlines()))
                    if _landed.get("blocking"):
                        # Skip the dispatch, and deliberately do NOT change the
                        # task's status. There is no board status meaning "held
                        # pending human verification": kanban_tasks' CHECK constraint
                        # has no `blocked` (state_machine.py carries that migration as
                        # an open TODO), and the statuses that do exist all lie about
                        # what happened here — `failed` says the work was attempted,
                        # `backlog` says nobody has got to it. So the task stays put
                        # and says so every cycle, which is the correct amount of
                        # noise for work that must not be built until a human looks.
                        print(f"  Kanban: REFUSING to dispatch {task['id']!r} — its id is "
                              f"already on the default branch (KANBAN_LANDED_CHECK=enforce); "
                              f"status left unchanged for a human to reconcile")
                        logger.warning(
                            "landed check REFUSED dispatch of %s (%s) — already on %s",
                            task["id"], _landed.get("confidence"), _landed.get("ref"),
                        )
                        continue
            except Exception as _lc_exc:  # noqa: BLE001 — advisory, never blocks dispatch
                logger.debug("pre-dispatch landed check failed for %s: %s",
                             task["id"], _lc_exc)

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
                    _decompose_one_task(task, ai_narrative=True)
                except Exception as _pd_exc:
                    logger.warning(
                        "pre-dispatch decompose failed for %s (%s) — dispatching anyway",
                        task["id"], _pd_exc,
                    )
                else:
                    # Decomposed successfully — skip dispatch for this task;
                    # children will be picked up next cycle. Continue to next task.
                    decomposed_this_cycle.append(task["id"])
                    # ── LESSONS LEARNED: pre-dispatch complexity decompose ────
                    try:
                        from tools.workflow.lesson_learned import analyze_task, write_lesson
                        lesson = analyze_task(task["id"], outcome="auto_decomposed")
                        write_lesson(lesson)
                    except Exception as _ll_exc:
                        logger.warning("lesson_learned hook failed: %s", _ll_exc)
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
                    _move_task(task["id"], "done",
                               reason="Ollama synchronous dispatch completed in-cycle")
                    # Ollama sync: the task reached a TERMINAL state, so _move_task already
                    # released the lease; flag it so `finally` does not double-release.
                    _dispatch_started = True
                    _send_notification(task, event="done")
                    processed.append(
                        {
                            "id": task["id"],
                            "title": task["title"],
                            "prompt_file": prompt_path,
                        }
                    )
                    print(f"  Kanban: {task['id']} '{task['title']}' -> done (Ollama sync)")
                elif task["id"] in _github_actions_dispatched:
                    # Async GitHub Actions dispatch — move to in_progress;
                    # completion is tracked externally (GitHub Actions run).
                    _github_actions_dispatched.discard(task["id"])
                    _move_task(task["id"], "in_progress",
                               reason="dispatched via GitHub Actions (completion tracked externally)")
                    # Dispatched: the run is tracked externally and the lease must be HELD
                    # for its duration -- releasing here reopens the double-build race.
                    _dispatch_started = True
                    _send_notification(task)
                    processed.append(
                        {
                            "id": task["id"],
                            "title": task["title"],
                            "prompt_file": prompt_path,
                        }
                    )
                    print(f"  Kanban: {task['id']} '{task['title']}' -> in_progress (GitHub Actions)")
                elif task["id"] in _running:
                    # Async Claude/LLM subprocess launched — move to in_progress
                    _move_task(task["id"], "in_progress",
                               reason="dispatched: agent subprocess launched")
                    # Dispatched: a worker subprocess owns this task now and must keep the
                    # lease until it terminates.
                    _dispatch_started = True
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

        finally:
            if _task_lease is not None and not _dispatch_started:
                try:
                    from tools.coordination import leases as _leases_rel
                    _leases_rel.release(f"kanban:task:{task['id']}")
                    logger.debug(
                        "released lease for %s -- dispatch was not started", task["id"],
                    )
                except Exception as _rel_exc:  # noqa: BLE001 -- never wedge the loop
                    logger.warning(
                        "could not release lease for %s: %s", task["id"], _rel_exc,
                    )
    return {
        "success": errors == 0,
        "metric_value": len(processed),
        "details": {
            "tasks_activated": len(processed),
            # mfx-sib-01: cards that yielded their slot to avoid building on top
            # of an in-flight sibling. Reported per cycle so a serialization hold
            # is legible as a WAIT rather than as an idle board. NULL — never 0 —
            # when the admission did not run (stood down, or the board was at
            # capacity before the selection window was ever filtered).
            "sibling_holds": (
                None if _LAST_SIBLING_HOLDS is None else len(_LAST_SIBLING_HOLDS)),
            "sibling_hold_detail": (
                None if _LAST_SIBLING_HOLDS is None else list(_LAST_SIBLING_HOLDS)),
            "telegram_commands": len(tg_results),
            "completed_this_cycle": completed,
            "decomposed_this_cycle": decomposed_this_cycle,
            "errors": errors,
            "tasks": processed,
        },
    }
