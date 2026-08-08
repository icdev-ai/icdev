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
import importlib
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from tools.db.storage import get_connection

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

    try:
        if not DB_PATH.exists():
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
    except sqlite3.OperationalError:
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


# -- Pre-Tool-Use Guard -------------------------------------------------
#
# Every rule now lives in tools/hooks/shared_checks.py, which .claude/hooks/
# pre_tool_use.py loads too, so the Claude Code path and this one cannot drift
# (hgx-guard-01, hgx-guard-02). Before that, Claude Code ran eight checks and
# this path ran two -- an agent running OUTSIDE Claude Code was materially less
# guarded than one inside it, which for an IL5/IL6 platform is backwards.

from tools.hooks import shared_checks as _checks  # noqa: E402

# Back-compat re-exports. `APPEND_ONLY_TABLES` here used to be a hand-maintained
# 22-entry list that had drifted ~340 tables behind the hook's; it is now the
# same object the hook reads.
APPEND_ONLY_TABLES = _checks.APPEND_ONLY_TABLES
_GIT_DANGER_PATTERNS = _checks._GIT_DANGER_PATTERNS


def _check_git_danger(command: str) -> Optional[str]:
    """Return a block reason if the command matches a destructive git
    pattern, else None. Case-insensitive match on the raw command text."""
    reason = _checks.check_git_danger(_checks.ToolCall(name="Bash", command=command))
    return reason or None


def _tool_input_snippet(tool_input: Optional[Dict[str, Any]], limit: int = 200) -> str:
    """Short, log-safe rendering of a tool input for the audit row."""
    if not isinstance(tool_input, dict):
        return ""
    try:
        return json.dumps(tool_input, default=str)[:limit]
    except (TypeError, ValueError):
        return str(tool_input)[:limit]


def run_pre_tool_check(
    tool_name: str,
    tool_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run every pre-tool-use safety check registered for the headless path.

    Equivalent to .claude/hooks/pre_tool_use.py -- same module, same rules --
    but callable from any orchestrator. Blocks are audited to ``hook_events``.

    This used to short-circuit to *allowed* for any tool whose name was not one
    of six known strings, so an unrecognised mutating tool was waved through
    unscanned. Tool names are not a security boundary: a call is now classified
    by the shape of its input (does it carry text that will be executed, a path
    it will write) and scanned on that basis. Read-only traffic stays cheap
    because nothing is extracted from it, so every check exits on its first
    guard.

    Args:
        tool_name: Name of the tool about to run. May be unknown.
        tool_input: The tool's input dict.

    Returns:
        ``{"allowed": bool, "reason": str, "check": str, "warnings": [...]}``
    """
    outcome = _checks.evaluate(tool_name, tool_input, path=_checks.PATH_HEADLESS)

    if not outcome.allowed:
        logger.warning(outcome.reason)
        store_event(
            get_session_id(),
            "pre_tool_use",
            tool_name,
            {
                "blocked": True,
                "reason": outcome.reason,
                "rule": outcome.check,
                "command_snippet": _tool_input_snippet(tool_input),
            },
        )
    else:
        for warning in outcome.warnings:
            logger.warning("%s: %s", warning["check"], warning["reason"])

    return outcome.as_dict()


# -- Post-Tool-Use ------------------------------------------------------

# Tools the awareness subscriber actually handles -- must stay in sync with
# _TRACKED_TOOLS in tools/awareness/hooks.py.
_AWARENESS_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})


def _dispatch_tool_execute_after(
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_output: Any,
) -> bool:
    """Fire the TOOL_EXECUTE_AFTER extension point. Best effort."""
    # Only import the awareness subscriber for the tools it actually handles:
    # importing it for a Read or a Bash call paid ~90 ms to register a handler
    # that would immediately filter the event out.
    if tool_name in _AWARENESS_TOOLS:
        try:
            # import_module, not a bare `import`: the module is imported purely
            # for its registration side effect and a plain import reads as dead.
            importlib.import_module("tools.awareness.hooks")
        except Exception:
            pass  # Awareness hook optional

    try:
        from tools.extensions.extension_manager import ExtensionPoint, extension_manager

        # Fire-and-forget: observational hooks run in a background daemon thread
        # so tool execution is NEVER blocked waiting for them.
        extension_manager.dispatch_async(
            ExtensionPoint.TOOL_EXECUTE_AFTER,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_input_keys": list(tool_input.keys()),
                "output_length": len(str(tool_output)) if tool_output else 0,
            },
        )
        return True
    except (ImportError, AttributeError):
        return False  # Extension manager not available -- skip silently
    except Exception:
        return False  # Never block tool execution


def run_post_tool_check(
    tool_name: str,
    tool_input: Optional[Dict[str, Any]] = None,
    tool_output: Any = "",
    is_error: bool = False,
) -> Dict[str, Any]:
    """Headless equivalent of ``.claude/hooks/post_tool_use.py``.

    Records the call on the append-only ``hook_events`` trail and fires the
    ``TOOL_EXECUTE_AFTER`` extension point (which is what keeps the awareness
    component index current). Observational only -- it can never block, and it
    never raises: a failure here must not take down a tool call that already
    succeeded.

    Args:
        tool_name: Name of the tool that just ran.
        tool_input: The input it ran with.
        tool_output: Its result text. Truncated to 2000 chars before storage.
        is_error: True when the tool reported a failure.

    Returns:
        ``{"recorded": bool, "event_id": int, "dispatched": bool}``
    """
    result: Dict[str, Any] = {"recorded": False, "event_id": -1, "dispatched": False}
    data = tool_input if isinstance(tool_input, dict) else {}

    try:
        event_id = store_event(
            get_session_id(),
            "post_tool_use",
            tool_name,
            {
                "tool_input_keys": list(data.keys()),
                "output_length": len(str(tool_output)) if tool_output else 0,
                # Truncated to keep the audit trail from bloating on large reads.
                "output_summary": str(tool_output)[:2000] if tool_output else "",
                "is_error": bool(is_error),
            },
        )
        result["event_id"] = event_id
        result["recorded"] = event_id != -1
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_post_tool_check: audit write failed: %s", exc)

    result["dispatched"] = _dispatch_tool_execute_after(tool_name, data, tool_output)
    return result


# -- Stop ---------------------------------------------------------------


def run_stop_check(
    reason: str = "unknown",
    session_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    auto_commit_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Headless equivalent of ``.claude/hooks/stop.py``.

    Records the stop on ``hook_events`` and runs the same ICDEV_AUTO_COMMIT
    behaviour the Claude Code stop hook performs. Transcript capture is not
    mirrored: it reads a Claude Code ``.jsonl`` transcript that has no headless
    counterpart -- the agent loop persists its own history through
    ``session_store`` checkpoints instead.

    Never raises.

    Args:
        reason: Why the run ended (``result_subtype`` / ``truncation_reason``).
        session_id: Session to attribute the event to. Defaults to the ambient one.
        payload: Extra fields to merge into the audit row.
        auto_commit_message: Commit subject when ICDEV_AUTO_COMMIT is enabled.

    Returns:
        ``{"recorded": bool, "event_id": int, "auto_commit": {...}}``
    """
    sid = session_id or get_session_id()
    result: Dict[str, Any] = {"recorded": False, "event_id": -1}

    try:
        body: Dict[str, Any] = {"stop_reason": reason, "session_id": sid}
        if payload:
            body.update(payload)
        event_id = store_event(sid, "stop", None, body)
        result["event_id"] = event_id
        result["recorded"] = event_id != -1
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_stop_check: audit write failed: %s", exc)

    try:
        result["auto_commit"] = run_auto_commit(auto_commit_message)
    except Exception as exc:  # noqa: BLE001
        result["auto_commit"] = {"committed": False, "message": str(exc)}

    return result


# -- Agent-loop hook adapters -------------------------------------------


def agent_loop_hooks(
    auto_commit_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Guardrail hooks shaped for ``run_agent_loop``'s lifecycle slots.

    The loop has always exposed ``on_pre_tool_use`` / ``on_post_tool_use`` /
    ``on_stop``, but only the pre-tool slot had a headless implementation to
    plug into. These are the other two.

    Signatures match the loop's ``PreToolUseHook`` / ``PostToolUseHook`` /
    ``StopHook`` aliases exactly::

        from tools.airgap.hook_compat import agent_loop_hooks
        result = run_agent_loop(..., **agent_loop_hooks())

    Returns:
        ``{"on_pre_tool_use": fn, "on_post_tool_use": fn, "on_stop": fn}``
    """

    def on_pre_tool_use(name: str, tool_input: Dict[str, Any]) -> Optional[str]:
        outcome = run_pre_tool_check(name, tool_input)
        # The loop treats a returned string as the block message and feeds it
        # back to the model as the tool result; None allows the call.
        return None if outcome["allowed"] else outcome["reason"]

    def on_post_tool_use(
        name: str,
        tool_input: Dict[str, Any],
        result_text: str,
        is_error: bool,
    ) -> None:
        run_post_tool_check(name, tool_input, result_text, is_error)

    def on_stop(result: Any) -> None:
        run_stop_check(
            reason=getattr(result, "result_subtype", "")
            or getattr(result, "truncation_reason", "")
            or "unknown",
            session_id=getattr(result, "session_id", "") or None,
            payload={
                "turns": getattr(result, "turns", 0),
                "done": getattr(result, "done", False),
                "tool_calls": len(getattr(result, "tool_call_log", []) or []),
                "total_input_tokens": getattr(result, "total_input_tokens", 0),
                "total_output_tokens": getattr(result, "total_output_tokens", 0),
            },
            auto_commit_message=auto_commit_message,
        )

    return {
        "on_pre_tool_use": on_pre_tool_use,
        "on_post_tool_use": on_post_tool_use,
        "on_stop": on_stop,
    }


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
