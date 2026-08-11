#!/usr/bin/env python3
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Claude Code hook compatibility layer.

ICDEV™ hooks (in .claude/hooks/) depend on Claude Code environment variables
and session lifecycle.  This module provides standalone equivalents that
work without Claude Code CLI.

Covers:
- send_event: session ID generation without CLAUDE_SESSION_ID
- pre_tool_use / post_tool_use: append-only table protection
- stop hook: auto-commit without Claude Code session stop event
- user_prompt_submit: prompt logging for non-Claude interfaces

Usage::

    from tools.airgap.hook_compat import (
        get_session_id,
        store_event,
        run_pre_tool_check,
        run_auto_commit,
    )
"""

import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from tools.db.storage import get_connection
from tools.hooks import shared_checks

logger = get_logger("icdev.airgap.hook_compat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ── Session ────────────────────────────────────────────────────────────

_session_id: Optional[str] = None


def get_session_id() -> str:
    """Get or generate a session ID.

    Priority:
    1. CLAUDE_SESSION_ID env var (Claude Code is running)
    2. ICDEV_SESSION_ID env var (external orchestrator set it)
    3. Module-level cached UUID (generated once per process)
    """
    global _session_id

    for env_var in ("CLAUDE_SESSION_ID", "ICDEV_SESSION_ID"):
        val = os.environ.get(env_var)
        if val:
            return val

    if _session_id is None:
        _session_id = f"local-{uuid.uuid4().hex[:12]}"
        # Also set it in env so child processes and hooks see it
        os.environ["CLAUDE_SESSION_ID"] = _session_id
        os.environ["ICDEV_SESSION_ID"] = _session_id

    return _session_id


def get_project_dir() -> Path:
    """Get project directory.

    Priority:
    1. CLAUDE_PROJECT_DIR env var
    2. ICDEV_PROJECT_DIR env var
    3. Detected from this file's location
    """
    for env_var in ("CLAUDE_PROJECT_DIR", "ICDEV_PROJECT_DIR"):
        val = os.environ.get(env_var)
        if val:
            return Path(val)
    return BASE_DIR


# ── Event Storage ──────────────────────────────────────────────────────


def compute_hmac(payload: str, secret: str) -> str:
    """Compute HMAC-SHA256 for tamper detection."""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def store_event(
    session_id: str,
    hook_type: str,
    tool_name: Optional[str] = None,
    payload: Optional[dict] = None,
    classification: str = "CUI",
) -> int:
    """Store hook event in SQLite. Drop-in replacement for .claude/hooks/send_event.py.

    Returns event ID or -1 on failure.
    """
    payload_str = json.dumps(payload) if payload else None
    secret = os.environ.get("ICDEV_HOOK_HMAC_SECRET", "icdev-default-hmac-key")
    signature = compute_hmac(payload_str or "", secret)

    # NEVER let an audit failure escape.
    #
    # This used to catch only sqlite3.OperationalError. On PostgreSQL the
    # `hook_events_id_seq` sequence was left at 1 by a bulk import that inserted
    # explicit ids up to 900004, so EVERY insert raised psycopg2 UniqueViolation
    # — which that clause did not catch. The exception propagated out of
    # run_pre_tool_check, so the act of AUDITING a block took the guard down with
    # it. A safety check must always return a decision.
    #
    # The audit row still matters (NIST AU), so a failure is logged at WARNING
    # rather than swallowed silently. The sequence itself is repaired by the
    # accompanying migration.
    try:
        # The DB_PATH existence gate is a SQLITE-ONLY precondition. It used to
        # run unconditionally, so on a PostgreSQL deployment — or from any git
        # worktree, where the local data/icdev.db does not exist — this returned
        # -1 and the audit row was NEVER written, even though get_connection()
        # would have worked. Combined with the desynced sequence, the headless
        # hook's NIST AU trail had been silently empty.
        if _sqlite_backend() and not DB_PATH.exists():
            return -1
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """INSERT INTO hook_events
               (session_id, hook_type, tool_name, payload, classification, signature)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (session_id, hook_type, tool_name, payload_str, classification, signature),
        )
        conn.commit()
        event_id = c.lastrowid
        conn.close()
        return event_id
    except Exception as exc:  # noqa: BLE001 — a guard must not die auditing itself
        logger.warning(
            "hook_compat: hook_events audit write FAILED (%s: %s). The safety "
            "decision still stands; the audit row is lost.",
            type(exc).__name__, exc,
        )
        return -1


def forward_to_dashboard(event_data: dict) -> None:
    """Best-effort HTTP POST to dashboard SSE ingest endpoint."""
    try:
        import urllib.request

        port = os.environ.get("ICDEV_DASHBOARD_PORT", "5000")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/events/ingest",
            data=json.dumps(event_data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)  # nosec B310 localhost only
    except Exception:
        pass


# ── Pre-Tool-Use Guard ─────────────────────────────────────────────────

# Append-only tables that must never be UPDATE/DELETE'd (NIST AU).
#
# NOT the canonical list. That one lives in APPEND_ONLY_TABLES in
# .claude/hooks/pre_tool_use.py and carries 361 tables; this one carries 22, of
# which 17 are absent from the canonical list — so 356 audit tables the Claude
# Code path protects are unprotected here, and neither list is a superset.
# That drift is exactly what hgx-guard-02 reconciles; left verbatim for now so
# this task changes no verdict on either side.
APPEND_ONLY_TABLES = [
    "audit_trail",
    "hook_events",
    "activity_log",
    "compliance_evidence",
    "security_scan_results",
    "deployment_log",
    "access_log",
    "ai_telemetry",
    "redaction_audit",
    "prompt_injection_log",
    "merge_audit",
    "deploy_audit",
    "fedramp_audit",
    "cmmc_audit",
    "cato_audit",
    "sbom_snapshots",
    "container_scan_results",
    "stig_results",
    "supply_chain_events",
    "mfa_events",
    "zta_policy_decisions",
    "fips_assessment_log",
]


# OPT-51 — git destructive-command blocklist (adapted from
# mattpocock/skills/git-guardrails-claude-code, MIT). See
# _ATTRIBUTION_REGISTRY in tools/workflow/coherence_checker.py.
#
# These commands can silently destroy work in a non-recoverable way.
# Hooked through run_pre_tool_check so any orchestrator (Claude Code,
# LLMRouter, MCP gateway) gets the same protection.
#
# hgx-guard-01: the patterns and the matcher now live in
# tools/hooks/shared_checks.py, which .claude/hooks/pre_tool_use.py also
# imports — the two guard paths cannot drift apart again. Re-exported here
# under their historical names so existing importers keep working.
_GIT_DANGER_PATTERNS = list(shared_checks.GIT_DANGER_PATTERNS)
_check_git_danger = shared_checks.git_danger_reason


def _sqlite_backend() -> bool:
    """True when the storage backend is SQLite (so DB_PATH is meaningful)."""
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").strip().lower() != "postgresql"


#: Every blocking check the headless path runs, in evaluation order. Cheap,
#: high-certainty checks first so an obvious block short-circuits the rest.
#:
#: A test asserts this covers every ``check_*`` in shared_checks — the failure
#: mode this slice exists to fix was a check that existed but was never called.
HEADLESS_CHECKS = (
    "check_env_file_access",
    "check_dangerous_rm",
    "check_git_danger",
    "check_append_only_write",
    "check_direct_sqlite_usage",
    "check_file_access_tiers",
    "check_branch_deletion",
    "check_worktree_path",
    "check_review_loop_precommit",
    # LAST on purpose (agov-det-06). The AGOV rule engine is additive: the eight
    # hardcoded checks above decide first, so a data-driven rule can only add a
    # refusal to a call they already allowed — it can never wave one through.
    "check_agent_rules",
)

#: Extra arguments for the checks that need more than (tool_name, tool_input).
#: Kept as data next to the list above so adding a check is one edit, and a
#: missing argument is a KeyError here rather than a silently skipped check.
_CHECK_EXTRA_ARGS = {
    "check_append_only_write": lambda: (APPEND_ONLY_TABLES,),
}


def run_pre_tool_check(
    tool_name: str,
    tool_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the pre-tool-use safety checks — the SAME ones the Claude Code hook
    runs, from the same implementation in tools/hooks/shared_checks.py.

    Callable from any orchestrator, so an agent running outside Claude Code is
    guarded identically to one inside it. Before hgx-guard-02 this ran two of the
    eight checks and waved through any tool whose name was not in a six-name
    list, which made the headless path — the one used unattended — the WEAKER of
    the two.

    Returns:
        {"allowed": True/False, "reason": str}
    """
    if not tool_input:
        return {"allowed": True, "reason": "no input to check"}

    # NO TOOL-NAME SHORT-CIRCUIT.
    #
    # There used to be one: anything outside ("Bash","bash","shell","sql",
    # "Write","Edit") returned allowed without looking at the payload, so a new
    # or renamed mutating tool bypassed every check by virtue of its name. Each
    # check decides for itself which tools it applies to — that judgement belongs
    # with the check, not with a list here that nobody updates.
    for check in HEADLESS_CHECKS:
        try:
            extra = _CHECK_EXTRA_ARGS.get(check)
            reason = getattr(shared_checks, check)(
                tool_name, tool_input, *(extra() if extra else ())
            )
        except Exception as exc:  # noqa: BLE001
            # A broken check must not wave the call through, but neither should
            # it stall every orchestrator. Report loudly and keep checking.
            logger.warning(
                "hook_compat: safety check %s errored (%s: %s); continuing with "
                "the remaining checks", check, type(exc).__name__, exc,
            )
            continue
        if not reason:
            continue

        message = reason if reason.startswith("BLOCKED") else f"BLOCKED: {reason}"
        logger.warning("hook_compat: %s", message)
        snippet = ""
        if isinstance(tool_input, dict):
            for key in ("command", "content", "query", "file_path"):
                value = tool_input.get(key)
                if isinstance(value, str) and value:
                    snippet = value[:200]
                    break
        # Belt and braces: store_event already swallows backend errors, but the
        # DECISION must not depend on the audit succeeding under any
        # circumstance — that coupling is precisely what let a UniqueViolation
        # on hook_events take the whole guard down.
        try:
            store_event(
                get_session_id(),
                "pre_tool_use",
                tool_name,
                {
                    "blocked": True,
                    "reason": message,
                    "command_snippet": snippet,
                    "rule": check,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hook_compat: audit of a BLOCK failed (%s: %s); the block still "
                "stands", type(exc).__name__, exc,
            )
        return {"allowed": False, "reason": message}

    return {"allowed": True, "reason": "passed safety checks"}


# ── Auto-Commit ────────────────────────────────────────────────────────


def run_auto_commit(message: Optional[str] = None) -> Dict[str, Any]:
    """Run auto-commit if ICDEV_AUTO_COMMIT is enabled.

    Equivalent to .claude/hooks/stop.py auto-commit logic but works
    without Claude Code session stop event.

    Returns:
        {"committed": bool, "message": str}
    """
    import subprocess

    if os.environ.get("ICDEV_AUTO_COMMIT", "").lower() not in ("true", "1", "yes"):
        return {"committed": False, "message": "ICDEV_AUTO_COMMIT not enabled"}

    try:
        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=10,
        )
        if not result.stdout.strip():
            return {"committed": False, "message": "no changes to commit"}

        # Stage and commit
        commit_msg = message or "chore: auto-commit from ICDEV air-gap session"
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=30,
        )
        return {"committed": True, "message": commit_msg}
    except Exception as exc:
        return {"committed": False, "message": str(exc)}


# ── OPT-61: Agent-loop middleware ────────────────────────────────────────
#
# Three deterministic middlewares that wrap the LLM agent loop with
# cross-cutting behaviors. Inspired by langchain-ai/open-swe (MIT) —
# see _ATTRIBUTION_REGISTRY in tools/workflow/coherence_checker.py.
# ICDEV implementation is independent; no upstream runtime dep.
#
#   check_message_queue    — mid-run message injection (OPT-62 primitive)
#   safety_net_pr          — after-agent commit + PR creation
#   tool_error_middleware  — decorator that catches tool exceptions and
#                            reports them structured instead of crashing
#                            the loop

# Queue directory for mid-run messages. Each task id gets a .jsonl file;
# each line is one queued message. check_message_queue drains the file.
MESSAGE_QUEUE_DIR = BASE_DIR / ".tmp" / "kanban" / "messages"


def check_message_queue(task_id: str) -> list[dict]:
    """Drain the pending-message queue for a task.

    Returns a list of queued messages (most common shape: {"role": "user",
    "content": str, "sender": str, "ts": str}) and deletes the file.

    Used by the kanban LocalPythonExecutor to inject mid-run user
    messages before the next LLM call. If the queue is empty OR the
    file doesn't exist, returns []. Never raises — queue failures
    are soft.

    Args:
        task_id: The kanban_tasks.id of the running task.

    Returns:
        List of message dicts in FIFO order.
    """
    if not task_id:
        return []
    queue_file = MESSAGE_QUEUE_DIR / f"{task_id}.jsonl"
    if not queue_file.exists():
        return []

    messages: list[dict] = []
    try:
        with open(queue_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "check_message_queue: malformed JSON in %s: %s",
                        queue_file, exc,
                    )
    except OSError as exc:
        logger.warning("check_message_queue: read failed for %s: %s", queue_file, exc)
        return []

    # Drain: delete the file so these messages aren't re-delivered next
    # poll. If the rename fails (e.g. the agent is still writing to
    # it), leave the file alone and return what we have.
    try:
        queue_file.unlink()
    except OSError:
        pass

    return messages


def queue_message(task_id: str, content: str, sender: str = "user") -> dict:
    """Append a message to a task's mid-run queue.

    Used by the dashboard /api/kanban/tasks/<id>/message POST endpoint
    (OPT-62) and by any external orchestrator that wants to interrupt
    a running task.

    Args:
        task_id: The kanban_tasks.id of the running task.
        content: Text of the message.
        sender: Who sent it (default 'user').

    Returns:
        Dict with: queued (bool), path (str), ts (ISO string).
    """
    from datetime import datetime, timezone
    if not task_id or not content:
        return {"queued": False, "path": "", "ts": "", "error": "task_id and content required"}

    MESSAGE_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    queue_file = MESSAGE_QUEUE_DIR / f"{task_id}.jsonl"
    ts = datetime.now(timezone.utc).isoformat()
    entry = {
        "role": "user",
        "content": content,
        "sender": sender,
        "ts": ts,
    }
    try:
        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return {"queued": True, "path": str(queue_file), "ts": ts}
    except OSError as exc:
        return {"queued": False, "path": str(queue_file), "ts": ts, "error": str(exc)}


def safety_net_pr(
    work_dir: Path | str,
    task: dict,
    branch_prefix: str = "auto-remediate/",
    auto_open_pr: bool = True,
) -> dict:
    """After-agent safety net: commit any uncommitted changes in work_dir
    and (optionally) open a draft PR.

    Catches the case where an agent finished producing files but didn't
    run `git add + git commit` itself. Runs `git status --porcelain` in
    work_dir, commits any dirty state with a task-referenced message,
    and — when gh CLI is available — opens a draft PR.

    Args:
        work_dir: Working directory the agent ran in. Usually a git
            worktree checked out under .tmp/worktrees/<task_id>/.
        task: The kanban task dict (uses id + title for commit message).
        branch_prefix: Prefix for the branch name if we need to create one.
        auto_open_pr: If True AND `gh` CLI is on PATH AND the repo has
            a github remote, open a draft PR. If False, just commit.

    Returns:
        Dict with: committed (bool), commit_sha (str), pr_created (bool),
        pr_url (str), work_dir (str), reason (str).
    """
    import subprocess
    from datetime import datetime, timezone

    result = {
        "committed": False,
        "commit_sha": "",
        "pr_created": False,
        "pr_url": "",
        "work_dir": str(work_dir) if work_dir else "",
        "reason": "",
    }

    work = Path(work_dir) if work_dir else BASE_DIR
    if not work.exists():
        result["reason"] = f"work_dir does not exist: {work}"
        return result

    task_id = task.get("id", "unknown")
    task_title = (task.get("title") or "untitled")[:60]

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(work), timeout=10,
        )
    except Exception as exc:
        result["reason"] = f"git status failed: {exc}"
        return result

    dirty_lines = [line for line in (status.stdout or "").splitlines() if line.strip()]
    if not dirty_lines:
        result["reason"] = "work_dir is clean — agent already committed or did nothing"
        return result

    # Stage + commit
    commit_msg = (
        f"auto-commit: {task_title}\n\n"
        f"Safety-net commit for task {task_id}. Agent produced files "
        f"but did not commit them. See tools/airgap/hook_compat.safety_net_pr.\n\n"
        f"Files changed:\n"
        + "\n".join(f"  {line}" for line in dirty_lines[:20])
        + (f"\n  ... and {len(dirty_lines) - 20} more" if len(dirty_lines) > 20 else "")
    )
    try:
        subprocess.run(
            ["git", "add", "-A"], cwd=str(work), timeout=30,
            capture_output=True, text=True,
        )
        commit_proc = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(work), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if commit_proc.returncode != 0:
            result["reason"] = f"git commit failed: {(commit_proc.stderr or commit_proc.stdout)[:200]}"
            return result
        # Extract the short sha
        sha_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(work), capture_output=True, text=True, timeout=10,
        )
        result["commit_sha"] = (sha_proc.stdout or "").strip()
        result["committed"] = True
    except Exception as exc:
        result["reason"] = f"git add/commit raised: {exc}"
        return result

    # Optionally open a PR via gh
    if auto_open_pr:
        import shutil
        if shutil.which("gh"):
            try:
                pr_proc = subprocess.run(
                    [
                        "gh", "pr", "create",
                        "--draft",
                        "--title", f"[agent] {task_title}",
                        "--body", f"Auto-generated PR from task {task_id} via "
                                  f"safety_net_pr middleware. "
                                  f"Commit {result['commit_sha']}.",
                    ],
                    cwd=str(work), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30,
                )
                if pr_proc.returncode == 0:
                    result["pr_created"] = True
                    result["pr_url"] = (pr_proc.stdout or "").strip().splitlines()[-1] if pr_proc.stdout else ""
                else:
                    result["reason"] = f"gh pr create warned: {(pr_proc.stderr or '')[:200]}"
            except Exception as exc:
                result["reason"] = f"gh pr create raised: {exc}"
        else:
            result["reason"] = "gh CLI not on PATH — commit succeeded but PR skipped"

    # Audit trail
    try:
        store_event({
            "event_type": "safety_net_pr",
            "task_id": task_id,
            "committed": result["committed"],
            "commit_sha": result["commit_sha"],
            "pr_created": result["pr_created"],
            "pr_url": result["pr_url"],
            "reason": result["reason"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return result


def tool_error_middleware(fn):
    """Decorator: catches exceptions in a tool call and returns a
    structured error dict instead of crashing the agent loop.

    Used by tools/canvas/auto_remediator.py handlers and any other
    agent-dispatched tool that benefits from loop-safe error handling.
    The wrapped function's return value is passed through unchanged
    on success.

    Logs failures to audit_trail via store_event with
    event_type='tool_error' — best-effort, never fails the call.

    Example:
        @tool_error_middleware
        def risky_handler(graph, finding):
            ...
            return (graph, "done")

        result = risky_handler(graph, finding)
        if isinstance(result, dict) and result.get("error"):
            # handler raised; result has error, error_type, tool_name
            ...
    """
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            tb = traceback.format_exc(limit=5)
            err = {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "tool_name": getattr(fn, "__name__", "unknown"),
                "traceback": tb,
            }
            try:
                store_event({
                    "event_type": "tool_error",
                    "tool_name": err["tool_name"],
                    "error_type": err["error_type"],
                    "error_message": str(exc)[:500],
                    "traceback_preview": tb[:1000],
                })
            except Exception:
                pass
            logger.warning(
                "tool_error_middleware: %s raised %s: %s",
                err["tool_name"], err["error_type"], exc,
            )
            return err

    return wrapper


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Session ID: {get_session_id()}")
    print(f"Project dir: {get_project_dir()}")

    # Test pre-tool check
    result = run_pre_tool_check("Bash", {"command": "SELECT * FROM audit_trail"})
    print(f"Pre-tool (safe): {result}")

    result = run_pre_tool_check("Bash", {"command": "DELETE FROM audit_trail WHERE id=1"})
    print(f"Pre-tool (blocked): {result}")
