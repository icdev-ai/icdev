#!/usr/bin/env python3
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
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("icdev.airgap.hook_compat")

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
    return hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


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
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT INTO hook_events
               (session_id, hook_type, tool_name, payload, classification, signature)
               VALUES (?, ?, ?, ?, ?, ?)""",
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


# ── Pre-Tool-Use Guard ─────────────────────────────────────────────────

# Append-only tables that must never be UPDATE/DELETE'd (NIST AU)
APPEND_ONLY_TABLES = [
    "audit_trail", "hook_events", "activity_log", "compliance_evidence",
    "security_scan_results", "deployment_log", "access_log",
    "ai_telemetry", "redaction_audit", "prompt_injection_log",
    "merge_audit", "deploy_audit", "fedramp_audit", "cmmc_audit",
    "cato_audit", "sbom_snapshots", "container_scan_results",
    "stig_results", "supply_chain_events", "mfa_events",
    "zta_policy_decisions", "fips_assessment_log",
]


def run_pre_tool_check(
    tool_name: str,
    tool_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run pre-tool-use safety checks (append-only table protection).

    Equivalent to .claude/hooks/pre_tool_use.py but callable from any
    orchestrator (not just Claude Code).

    Returns:
        {"allowed": True/False, "reason": str}
    """
    import re

    if tool_name not in ("Bash", "bash", "shell", "sql", "Write", "Edit"):
        return {"allowed": True, "reason": "non-destructive tool"}

    if not tool_input:
        return {"allowed": True, "reason": "no input to check"}

    # Extract command text from tool input
    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
        if not command:
            command = tool_input.get("content", "")
        if not command:
            command = tool_input.get("query", "")

    if not command or not isinstance(command, str):
        return {"allowed": True, "reason": "no command to check"}

    command_lower = command.lower()

    # Check for destructive operations on append-only tables
    for table in APPEND_ONLY_TABLES:
        if re.search(
            rf"(update|delete)\s+(from\s+)?{re.escape(table)}",
            command_lower,
        ):
            reason = (
                f"BLOCKED: destructive operation on append-only table '{table}'. "
                f"NIST AU compliance requires append-only audit trail."
            )
            logger.warning(reason)
            store_event(
                get_session_id(),
                "pre_tool_use",
                tool_name,
                {"blocked": True, "reason": reason, "command_snippet": command[:200]},
            )
            return {"allowed": False, "reason": reason}

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
            capture_output=True, text=True, cwd=str(BASE_DIR), timeout=10,
        )
        if not result.stdout.strip():
            return {"committed": False, "message": "no changes to commit"}

        # Stage and commit
        commit_msg = message or "chore: auto-commit from ICDEV air-gap session"
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, cwd=str(BASE_DIR), timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, cwd=str(BASE_DIR), timeout=30,
        )
        return {"committed": True, "message": commit_msg}
    except Exception as exc:
        return {"committed": False, "message": str(exc)}


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    print(f"Session ID: {get_session_id()}")
    print(f"Project dir: {get_project_dir()}")

    # Test pre-tool check
    result = run_pre_tool_check("Bash", {"command": "SELECT * FROM audit_trail"})
    print(f"Pre-tool (safe): {result}")

    result = run_pre_tool_check("Bash", {"command": "DELETE FROM audit_trail WHERE id=1"})
    print(f"Pre-tool (blocked): {result}")
