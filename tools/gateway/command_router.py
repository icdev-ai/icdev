#!/usr/bin/env python3
# CUI // SP-CTI
"""Command Router — Parse, validate, and dispatch ICDEV commands.

Receives a security-chain-validated CommandEnvelope, checks the
command against the allowlist, and dispatches to the appropriate
ICDEV tool via subprocess execution.

Decision D137: Command allowlist is YAML-driven with per-channel overrides.
Decision D138: Deploy commands disabled by default on all remote channels.
"""

import json
import logging
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.event_envelope import CommandEnvelope
from tools.gateway.response_filter import (
    filter_response, truncate_response, format_response
)

logger = logging.getLogger("icdev.gateway.command_router")

DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping: %s", kwargs.get("action", ""))


# ---------------------------------------------------------------------------
# Command → Tool Mapping
# ---------------------------------------------------------------------------

# Maps ICDEV command names to the Python tool script that implements them
COMMAND_TOOL_MAP = {
    "icdev-status": {
        "script": "tools/project/project_status.py",
        "args_template": "--project-id {project_id} --json",
        "description": "Project status dashboard",
    },
    "icdev-test": {
        "script": "tools/testing/test_orchestrator.py",
        "args_template": "--project-dir {project_id} --json",
        "description": "Run test suite",
    },
    "icdev-comply": {
        "script": "tools/compliance/control_mapper.py",
        "args_template": "--project-id {project_id} --json",
        "description": "Compliance status",
    },
    "icdev-secure": {
        "script": "tools/security/sast_runner.py",
        "args_template": "--project-dir {project_id} --json",
        "description": "Security scan",
    },
    "icdev-monitor": {
        "script": "tools/monitor/health_checker.py",
        "args_template": "--json",
        "description": "System health check",
    },
    "icdev-knowledge": {
        "script": "tools/knowledge/recommendation_engine.py",
        "args_template": "--project-id {project_id} --json",
        "description": "Knowledge base query",
    },
    "icdev-query": {
        "script": "tools/dashboard/nlq_handler.py",
        "args_template": "--query {query} --json",
        "description": "Natural language compliance query",
    },
    "icdev-intake": {
        "script": "tools/requirements/intake_engine.py",
        "args_template": "--project-id {project_id} --json",
        "description": "Requirements intake",
    },
    "icdev-build": {
        "script": "tools/builder/code_generator.py",
        "args_template": "--project-dir {project_id} --json",
        "description": "Build code (TDD)",
    },
}


def is_command_allowed(command: str, channel: str,
                       allowlist: List[Dict]) -> Tuple[bool, Optional[Dict]]:
    """Check if a command is allowed on a given channel.

    Args:
        command: ICDEV command name (e.g., "icdev-status")
        channel: Channel name (e.g., "telegram")
        allowlist: Command allowlist from config

    Returns:
        (allowed, entry) — entry is the matching allowlist entry if found
    """
    for entry in allowlist:
        if entry.get("command") == command:
            channels = entry.get("channels", "")
            if not channels:
                # Empty channels = disabled on all remote
                return (False, entry)
            if channels == "*" or channel in [c.strip() for c in channels.split(",")]:
                return (True, entry)
            return (False, entry)

    return (False, None)


def requires_confirmation(command: str, allowlist: List[Dict]) -> bool:
    """Check if a command requires user confirmation before execution."""
    for entry in allowlist:
        if entry.get("command") == command:
            return entry.get("requires_confirmation", False)
    return False


def execute_command(envelope: CommandEnvelope,
                    channel_config: Dict,
                    gateway_config: Dict) -> Dict[str, Any]:
    """Execute an ICDEV command and return the result.

    Args:
        envelope: Validated CommandEnvelope
        channel_config: Config for the source channel
        gateway_config: Full gateway config

    Returns:
        {
            "success": bool,
            "output": str (filtered response text),
            "raw_output": str (unfiltered — for audit only),
            "filtered": bool,
            "detected_il": str,
            "execution_time_ms": int,
            "audit_id": str,
        }
    """
    start_time = time.time()
    audit_id = str(uuid.uuid4())
    channel_max_il = channel_config.get("max_il", "IL4")

    # Look up the tool
    tool_info = COMMAND_TOOL_MAP.get(envelope.command)
    if not tool_info:
        return _result(False, f"Unknown command: {envelope.command}",
                       audit_id=audit_id, start_time=start_time)

    # Build command line
    script_path = BASE_DIR / tool_info["script"]
    if not script_path.exists():
        return _result(False, f"Tool not found: {tool_info['script']}",
                       audit_id=audit_id, start_time=start_time)

    # Format args
    args_str = tool_info["args_template"]
    project_id = envelope.project_id or envelope.args.get("project_id", "")
    query = envelope.args.get("query", envelope.raw_text)
    args_str = args_str.format(
        project_id=project_id,
        query=query,
    )

    cmd = [sys.executable, str(script_path)] + args_str.split()

    logger.info("Executing: %s", " ".join(cmd))

    # Execute via subprocess (safe env, no shell)
    try:
        env = _safe_env()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 min timeout
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=str(BASE_DIR),
        )

        raw_output = result.stdout.strip()
        if result.returncode != 0 and not raw_output:
            raw_output = result.stderr.strip() or f"Command failed (exit code {result.returncode})"

    except subprocess.TimeoutExpired:
        raw_output = "Command timed out after 120 seconds"
        return _result(False, raw_output, audit_id=audit_id, start_time=start_time)
    except Exception as e:
        raw_output = f"Execution error: {e}"
        return _result(False, raw_output, audit_id=audit_id, start_time=start_time)

    # Filter response by classification
    filtered_output, was_filtered, detected_il = filter_response(
        raw_output, channel_max_il, envelope.id
    )

    # Truncate for channel limits
    response_config = gateway_config.get("gateway", {}).get("response", {})
    max_length = response_config.get("max_length", 4000)
    filtered_output = truncate_response(filtered_output, max_length)

    # Format with metadata
    exec_time_ms = int((time.time() - start_time) * 1000)
    include_timing = response_config.get("include_timing", True)
    include_audit = response_config.get("include_audit_id", True)

    final_output = format_response(
        filtered_output, envelope.command,
        execution_time_ms=exec_time_ms,
        audit_id=audit_id,
        include_timing=include_timing,
        include_audit_id=include_audit,
    )

    # Log to DB
    _log_command(envelope, audit_id, raw_output, detected_il,
                 was_filtered, exec_time_ms,
                 "completed" if result.returncode == 0 else "failed")

    audit_log_event(
        event_type="remote_command_completed",
        actor=envelope.icdev_user_id or envelope.channel_user_id,
        action=f"Command '{envelope.command}' executed via {envelope.channel}",
        details=str({
            "audit_id": audit_id,
            "command": envelope.command,
            "channel": envelope.channel,
            "execution_time_ms": exec_time_ms,
            "response_filtered": was_filtered,
            "detected_il": detected_il,
        }),
    )

    return {
        "success": result.returncode == 0,
        "output": final_output,
        "raw_output": raw_output,
        "filtered": was_filtered,
        "detected_il": detected_il,
        "execution_time_ms": exec_time_ms,
        "audit_id": audit_id,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(success: bool, output: str, audit_id: str = "",
            start_time: float = 0) -> Dict[str, Any]:
    """Build a result dict for error cases."""
    exec_time = int((time.time() - start_time) * 1000) if start_time else 0
    return {
        "success": success,
        "output": output,
        "raw_output": output,
        "filtered": False,
        "detected_il": "IL2",
        "execution_time_ms": exec_time,
        "audit_id": audit_id,
    }


def _safe_env() -> Dict[str, str]:
    """Build a safe environment for subprocess execution.

    Strips potentially dangerous env vars while keeping necessary ones.
    """
    import os
    safe = {}
    # Keep essential vars
    for key in ("PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV",
                "ICDEV_GATEWAY_HMAC_SECRET", "ICDEV_MAILBOX_SECRET",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "AWS_DEFAULT_REGION", "AWS_REGION",
                "OLLAMA_BASE_URL", "SYSTEMROOT", "TEMP", "TMP"):
        val = os.environ.get(key)
        if val:
            safe[key] = val
    return safe


def _log_command(envelope: CommandEnvelope, audit_id: str,
                 raw_output: str, detected_il: str,
                 was_filtered: bool, exec_time_ms: int,
                 status: str):
    """Log command execution to remote_command_log table."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO remote_command_log "
            "(id, binding_id, channel, raw_command, parsed_tool, parsed_args, "
            " gate_results, execution_status, response_classification, "
            " response_filtered, execution_time_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                audit_id,
                envelope.binding_id or "",
                envelope.channel,
                envelope.raw_text,
                envelope.command,
                json.dumps(envelope.args),
                json.dumps(envelope.gate_results),
                status,
                detected_il,
                1 if was_filtered else 0,
                exec_time_ms,
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to log command: %s", e)
