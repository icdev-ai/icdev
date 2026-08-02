# CUI // SP-CTI
"""ACE ToolRunner — allowlisted ICDEV tool execution for co-worker agents.

Executes Python ICDEV tools declared in a role's ``icdev_tools`` list via
subprocess, capturing stdout/stderr as an artifact.  Hard-blocks destructive
flags.  Non-green trust tiers require HITL approval before execution.

Usage (from StepExecutor)::

    runner = ToolRunner(spec.icdev_tools)
    result = runner.run(
        "python tools/testing/health_check.py --json",
        coworker_id=spec.coworker_id,
        instance_id=instance_id,
        trust_tier=spec.trust_tier,
    )
    # result: {command, returncode, stdout, stderr, artifact_id, success}

Role YAML declares allowed tools via::

    icdev_tools:
      - "python tools/testing/health_check.py --json"
      - "python tools/db/storage.py --health"
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.tool_runner")

def _resolve_repo_root() -> Path:
    """Directory the allowlisted commands are executed in.

    Every allowed prefix names a RELATIVE path (``python tools/…``,
    ``python icdev/…``), so this decides whether they resolve at all.

    ``Path(__file__).parents[4]`` was two levels above the repository in BOTH
    layouts — ``tools/ace/`` and the ``icdev/tools/ace/`` mirror — so the
    subprocess ran somewhere containing neither ``tools/`` nor ``icdev/`` and
    every path-form command died with "can't open file". It stayed invisible
    because both production call sites take the default while every test passes
    ``repo_root=tmp_path``, so nothing exercised it.

    Resolved from ``__file__`` via a repo sentinel rather than by counting
    parents, so it survives the mirror, a worktree, and any cwd. With no
    sentinel above it — a pip-installed wheel under site-packages — the cwd is
    used instead: that is the user's project, where ``data/`` and ``.env`` live,
    and it is where a tool's output belongs. In that environment the module
    form (``python -m icdev.tools.…``) is the one that resolves; it is already
    permitted below.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() or (parent / ".git").exists():
            return parent
    return Path.cwd()


_REPO_ROOT = _resolve_repo_root()
_DB_ENV = "ICDEV_ACE_DB_URL"

# Commands must start with one of these prefixes to prevent arbitrary execution.
# Both namespaces are permitted deliberately: `tools.`/`tools/` is the source
# checkout, `icdev.`/`icdev/` is the installed wheel, which ships only `icdev`.
# `python -m icdev.` already covers `python -m icdev.tools.…`, so the packaged
# form of any tool here needs no additional entry.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "python tools/",
    "python -m tools.",
    "python icdev/",
    "python -m icdev.",
)

# Patterns that indicate destructive intent — hard-blocked regardless of trust tier.
_BLOCKED_RE = re.compile(
    r"""
    --force\b | --drop\b | --delete\b   # common destructive flags
    | \brm\b                            # shell remove
    | DROP\s+TABLE                      # SQL DDL
    | --truncate\b | TRUNCATE\s+TABLE
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TIMEOUT_SECONDS = 120


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NotAllowlistedError(PermissionError):
    """Command is not in the role's icdev_tools allowlist."""


class DestructivePatternError(PermissionError):
    """Command contains a hard-blocked destructive flag or keyword."""


class InvalidPrefixError(PermissionError):
    """Command does not start with an approved ICDEV tool prefix."""


class ToolRunnerError(RuntimeError):
    """Generic tool execution failure (timeout, non-zero exit, etc.)."""


# ---------------------------------------------------------------------------
# ToolRunner
# ---------------------------------------------------------------------------


class ToolRunner:
    """Executes allowlisted ICDEV Python tools as subprocesses.

    Args:
        icdev_tools: Allowlisted command strings from the role YAML.
        repo_root:   Override for the repository root (test injection).
    """

    def __init__(
        self,
        icdev_tools: list[str],
        repo_root: Path | None = None,
    ) -> None:
        self._allowlist: frozenset[str] = frozenset(icdev_tools or [])
        self._root = repo_root or _REPO_ROOT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        command: str,
        *,
        coworker_id: str,
        instance_id: str,
        trust_tier: str,
    ) -> dict[str, Any]:
        """Execute *command* with all guard checks applied.

        Args:
            command:     The exact command string (must be in allowlist).
            coworker_id: Audit identity for the requesting co-worker.
            instance_id: ACE instance ID for audit trail.
            trust_tier:  Co-worker trust band.  Non-green tiers trigger HITL.

        Returns:
            Dict with keys: command, returncode, stdout, stderr, artifact_id, success.

        Raises:
            NotAllowlistedError:    Command not in role's icdev_tools.
            DestructivePatternError: Command contains blocked pattern.
            InvalidPrefixError:     Command doesn't start with approved prefix.
            TrustKernelDeniedError: trust_tier is not 'green' (HITL required).
            ToolRunnerError:        Subprocess timed out.
        """
        cmd = (command or "").strip()

        # 1. Allowlist check — must be declared in role YAML icdev_tools
        if cmd not in self._allowlist:
            raise NotAllowlistedError(
                f"Command not in role's icdev_tools allowlist: {cmd!r}"
            )

        # 2. Hard-block destructive patterns (non-negotiable)
        if _BLOCKED_RE.search(cmd):
            raise DestructivePatternError(
                f"Destructive pattern blocked in command: {cmd!r}"
            )

        # 3. Enforce allowed prefix (belt-and-suspenders beyond allowlist)
        if not any(cmd.startswith(p) for p in _ALLOWED_PREFIXES):
            raise InvalidPrefixError(
                f"Command must start with an approved ICDEV prefix "
                f"({', '.join(_ALLOWED_PREFIXES)}): {cmd!r}"
            )

        # 4. Trust tier gate — only green executes autonomously
        if trust_tier != "green":
            self._audit_hitl(cmd, coworker_id, instance_id)
            # Import here to avoid circular — step_executor defines this exception
            from icdev.tools.ace.step_executor import TrustKernelDeniedError

            raise TrustKernelDeniedError(
                f"RunTool requires green trust tier; co-worker trust_tier='{trust_tier}' "
                f"— HITL approval requested for: {cmd!r}"
            )

        # 5. Execute
        env = {**os.environ, "PYTHONPATH": str(self._root)}
        try:
            proc = subprocess.run(
                cmd,
                shell=True,  # nosec B602 — command is allowlisted before reaching here
                capture_output=True,
                text=True,
                cwd=str(self._root),
                env=env,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolRunnerError(
                f"Command timed out after {_TIMEOUT_SECONDS}s: {cmd!r}"
            ) from exc

        artifact_id = self._store_artifact(cmd, proc.stdout, proc.stderr, coworker_id, instance_id)

        return {
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[:4096],
            "stderr": proc.stderr[:2048],
            "artifact_id": artifact_id,
            "success": proc.returncode == 0,
        }

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    def _audit_hitl(self, command: str, coworker_id: str, instance_id: str) -> None:
        """Emit a hitl_pending audit row so the HITL UI shows the pending action."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_audit_log "
                    "(instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES (%s, %s, 'hitl_pending', %s, 'tool_runner', %s)",
                    (instance_id, coworker_id, f"RunTool: {command}", now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit_hitl: best-effort INSERT into ace_audit_log failed (non-blocking): %s", exc)

    def _store_artifact(
        self,
        command: str,
        stdout: str,
        stderr: str,
        coworker_id: str,
        instance_id: str,
    ) -> str:
        """Persist stdout/stderr to ace_artifacts (if table exists) and return artifact_id."""
        artifact_id = str(uuid.uuid4())
        try:
            from icdev.tools.db.storage import get_canvas_connection
            import json

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    """
                    -- ace_artifacts splits content_md / content_json; there is no
                    -- bare `content`. The payload below is JSON, so it belongs in
                    -- content_json (swp-scan-01).
                    INSERT INTO ace_artifacts
                        (id, instance_id, coworker_id, artifact_type, content_json, created_at)
                    VALUES (%s, %s, %s, 'tool_output', %s, %s)
                    """,
                    (
                        artifact_id,
                        instance_id,
                        coworker_id,
                        json.dumps({"command": command, "stdout": stdout[:4096], "stderr": stderr[:2048]}),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # artifact storage is best-effort; never crash the caller
            logger.warning("_store_artifact: best-effort INSERT into ace_artifacts failed (non-blocking): %s", exc)
        return artifact_id
