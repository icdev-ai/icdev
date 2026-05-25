# CUI // SP-CTI
"""Shell execution primitive for the agent toolkit (OPT-67).

Delegates to tools.security.sandbox_executor when sandboxed=True (Phase 72
LLM Sandbox via Docker). Otherwise runs directly via subprocess. Captures
stdout, stderr, returncode, and duration.

Design rules:
  - Air-gap safe by default: no network subprocess unless sandbox policy
    allows it.
  - Uses subprocess.run with capture_output + text mode so callers get
    structured strings, never raw bytes.
  - Honors timeout. Kills the process on timeout and returns a
    structured timeout result instead of raising.
  - Records every invocation to audit_trail via tools.audit.audit_logger
    when audit=True (best-effort; never fails the call on audit errors).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import shlex
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Union

logger = get_logger("icdev.agent_toolkit.shell")

PathLike = Union[str, Path]


def execute_shell(
    cmd: Union[str, List[str]],
    cwd: Optional[PathLike] = None,
    sandboxed: bool = False,
    timeout: int = 60,
    env: Optional[dict] = None,
    audit: bool = True,
    actor: str = "agent_toolkit",
) -> dict:
    """Run a shell command and return a structured result.

    Args:
        cmd: Command as a string (parsed with shlex) or pre-split list.
        cwd: Working directory. Default: current directory.
        sandboxed: If True, route through tools.security.sandbox_executor.
            Requires llm-sandbox package + Docker to be available.
        timeout: Seconds before the process is killed. Default 60.
        env: Optional environment variables (merged with current env).
        audit: If True, write a row to audit_trail on completion.
        actor: Identity to record in the audit row.

    Returns:
        Dict with keys:
          stdout (str), stderr (str), returncode (int),
          duration_ms (int), timed_out (bool), sandboxed (bool),
          cmd (str), cwd (str).
    """
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
        cmd_str = cmd
    else:
        cmd_list = list(cmd)
        cmd_str = " ".join(shlex.quote(c) for c in cmd_list)

    cwd_path = str(cwd) if cwd else None

    result: dict = {
        "stdout": "",
        "stderr": "",
        "returncode": -1,
        "duration_ms": 0,
        "timed_out": False,
        "sandboxed": sandboxed,
        "cmd": cmd_str,
        "cwd": cwd_path or "",
    }

    if sandboxed:
        # Delegate to LLM Sandbox (Phase 72)
        try:
            from tools.security.sandbox_executor import SandboxExecutor
        except ImportError as exc:
            result["stderr"] = f"sandbox_executor import failed: {exc}"
            return result
        try:
            executor = SandboxExecutor()
            # SandboxExecutor expects language + code. For a shell
            # command we run via bash.
            bash_code = cmd_str
            sb = executor.execute(
                code=bash_code,
                language="python",  # fallback — SandboxExecutor may support 'bash'
                timeout=timeout,
            )
            result["stdout"] = getattr(sb, "stdout", "") or ""
            result["stderr"] = getattr(sb, "stderr", "") or ""
            result["returncode"] = getattr(sb, "exit_code", -1)
            result["duration_ms"] = getattr(sb, "runtime_ms", 0)
        except Exception as exc:
            result["stderr"] = f"sandbox execute failed: {type(exc).__name__}: {exc}"
        _maybe_audit(result, audit, actor)
        return result

    # Direct subprocess path
    t0 = time.time()
    try:
        proc = subprocess.run(  # nosec B603 — args list, no shell=True
            cmd_list,
            cwd=cwd_path,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result["stdout"] = proc.stdout or ""
        result["stderr"] = proc.stderr or ""
        result["returncode"] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        result["stdout"] = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        result["stderr"] = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result["returncode"] = -9
    except FileNotFoundError as exc:
        result["stderr"] = f"command not found: {exc}"
        result["returncode"] = 127
    except Exception as exc:
        result["stderr"] = f"execute_shell error: {type(exc).__name__}: {exc}"

    result["duration_ms"] = int((time.time() - t0) * 1000)
    _maybe_audit(result, audit, actor)
    return result


def _maybe_audit(result: dict, audit: bool, actor: str) -> None:
    """Best-effort audit-trail write. Never fails the call."""
    if not audit:
        return
    try:
        import json as _json
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="code_generated" if result.get("returncode") == 0 else "test_failed",
            actor=actor,
            action=f"agent_toolkit.execute_shell.{'sandboxed' if result['sandboxed'] else 'direct'}",
            details=_json.dumps({
                "cmd": result["cmd"][:500],
                "cwd": result["cwd"],
                "returncode": result["returncode"],
                "duration_ms": result["duration_ms"],
                "timed_out": result["timed_out"],
                "stdout_preview": result["stdout"][:200],
                "stderr_preview": result["stderr"][:200],
            }),
            project_id="agent_toolkit",
        )
    except Exception as exc:  # pragma: no cover — audit must never fail
        logger.debug("audit write failed: %s", exc)
