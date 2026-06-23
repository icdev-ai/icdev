"""ACE step executor — dynamic tool invocation with trust-kernel gating."""

from __future__ import annotations

import importlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from icdev.tools.daemon.base import TrustKernelBase
from icdev.tools.db.storage import get_connection


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolPermissionDeniedError(PermissionError):
    """Raised when a step's tool is not in spec.tool_permissions."""


class TrustKernelDeniedError(PermissionError):
    """Raised when trust_kernel.can_execute() returns False."""


# ---------------------------------------------------------------------------
# CoWorkerSpec
# ---------------------------------------------------------------------------


@dataclass
class CoWorkerSpec:
    """Declarative description of what an ACE co-worker is allowed to do.

    Attributes:
        tool_permissions: Dotted import paths or built-in step type names
            (e.g. ``["icdev.tools.rag.retriever.retrieve", "FileRead"]``).
        trust_tier: Risk tier key passed to the trust kernel
            (e.g. ``"green"``, ``"yellow"``, ``"orange"``).
        name: Human-readable co-worker name (informational only).
        folder_access: Phase 2 filesystem scope from role YAML
            (e.g. ``[{"path": "reports/", "mode": "r"}]``).
        icdev_tools: Phase 2 allowlisted tool commands from role YAML.
    """

    tool_permissions: list[str] = field(default_factory=list)
    trust_tier: str = "green"
    name: str = "ace-coworker"
    folder_access: list[dict] = field(default_factory=list)
    icdev_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_AUDIT_TABLE_CREATED: set[str] = set()

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS ace_audit_log (
    id          TEXT    PRIMARY KEY,
    step_id     TEXT    NOT NULL,
    tool        TEXT    NOT NULL,
    trust_tier  TEXT    NOT NULL,
    success     INTEGER NOT NULL,
    skipped     INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    duration_ms REAL,
    created_at  TEXT    NOT NULL
)
"""


def _ensure_audit_table(conn: Any) -> None:
    if "ace_audit_log" in _AUDIT_TABLE_CREATED:
        return
    conn.execute(_AUDIT_DDL)
    conn.commit()
    _AUDIT_TABLE_CREATED.add("ace_audit_log")


def _emit_audit(
    *,
    step_id: str,
    tool: str,
    trust_tier: str,
    success: bool,
    skipped: bool,
    error: str | None,
    duration_ms: float,
) -> None:
    import uuid
    from datetime import datetime, timezone

    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_connection()
        _ensure_audit_table(conn)
        conn.execute(
            """
            INSERT INTO ace_audit_log
                (id, step_id, tool, trust_tier, success, skipped, error, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                step_id,
                tool,
                trust_tier,
                int(success),
                int(skipped),
                error,
                duration_ms,
                now,
            ),
        )
        conn.commit()
    except Exception:
        pass  # audit must never crash the caller


def _substitute_vars(value: Any, context: dict) -> Any:
    """Recursively replace $var references with context values."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            return str(context.get(m.group(1), m.group(0)))

        return _VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_vars(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_vars(item, context) for item in value]
    return value


# Built-in step types handled directly by StepExecutor (not resolved as imports)
_BUILT_IN_STEP_TYPES: frozenset[str] = frozenset({"FileRead", "FileWrite", "RunTool"})


def _resolve_tool(dotted: str) -> Any:
    """Import a callable from a dotted path (``module.submod.func``)."""
    parts = dotted.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(f"Cannot resolve tool path: {dotted!r}")
    module_path, attr = parts
    mod = importlib.import_module(module_path)
    obj = getattr(mod, attr)
    if not callable(obj):
        raise TypeError(f"{dotted!r} is not callable")
    return obj


def _eval_condition(condition: str, context: dict) -> bool:
    """Evaluate a condition expression against the execution context.

    Substitutes $var references first, then evaluates the resulting
    expression with a restricted namespace (only context locals +
    safe builtins).
    """
    if not condition:
        return True
    substituted = _substitute_vars(condition, context)
    safe_builtins = {
        "True": True,
        "False": False,
        "None": None,
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
    }
    try:
        result = eval(substituted, {"__builtins__": safe_builtins}, dict(context))  # noqa: S307  # nosec B307 — builtins restricted to safe subset; no user-controlled code path
        return bool(result)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# StepExecutor
# ---------------------------------------------------------------------------


class StepExecutor:
    """Execute a single ACE workflow step with trust-kernel gating.

    Expected step schema::

        {
            "id":         str,         # unique step identifier
            "tool":       str,         # dotted import path to callable
            "args":       dict,        # keyword arguments; values may use $var
            "condition":  str | None,  # skip if evaluates to False
            "output_var": str | None,  # store result under this context key
        }
    """

    def run(
        self,
        step: dict,
        context: dict,
        spec: CoWorkerSpec,
        trust_kernel: TrustKernelBase,
    ) -> Any:
        """Execute *step* and return its result (or ``None`` if skipped).

        Side effects:
        - Mutates *context* by storing the result under ``step['output_var']``
          if that key is present and the step was not skipped.
        - Appends one row to ``ace_audit_log`` after each call (including
          skipped and failed steps).

        Raises:
            ToolPermissionDeniedError: tool not in ``spec.tool_permissions``.
            TrustKernelDeniedError: trust kernel rejected the step.
        """
        step_id: str = step.get("id", "unknown")
        tool_path: str = step.get("tool", "")
        raw_args: dict = step.get("args") or {}
        condition: str | None = step.get("condition")
        output_var: str | None = step.get("output_var")

        start = time.monotonic()

        # 1. Check condition — skip early (no audit penalty for condition skip)
        if condition is not None and not _eval_condition(condition, context):
            _emit_audit(
                step_id=step_id,
                tool=tool_path,
                trust_tier=spec.trust_tier,
                success=True,
                skipped=True,
                error=None,
                duration_ms=0.0,
            )
            return None

        # 2. Verify tool is whitelisted in spec
        if tool_path not in spec.tool_permissions:
            err = f"Tool '{tool_path}' not in spec.tool_permissions for '{spec.name}'"
            _emit_audit(
                step_id=step_id,
                tool=tool_path,
                trust_tier=spec.trust_tier,
                success=False,
                skipped=False,
                error=err,
                duration_ms=(time.monotonic() - start) * 1000,
            )
            raise ToolPermissionDeniedError(err)

        # 3. Trust kernel gate
        allowed, reason = trust_kernel.can_execute(spec.trust_tier, step_id)
        if not allowed:
            err = f"Trust kernel denied step '{step_id}': {reason}"
            _emit_audit(
                step_id=step_id,
                tool=tool_path,
                trust_tier=spec.trust_tier,
                success=False,
                skipped=False,
                error=err,
                duration_ms=(time.monotonic() - start) * 1000,
            )
            raise TrustKernelDeniedError(err)

        # 4. Substitute $variable references in args
        resolved_args = _substitute_vars(raw_args, context)

        # 5a. Built-in step types (FileRead / FileWrite / RunTool)
        if tool_path in _BUILT_IN_STEP_TYPES:
            try:
                result = self._run_built_in(tool_path, resolved_args, spec, context)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                _emit_audit(
                    step_id=step_id,
                    tool=tool_path,
                    trust_tier=spec.trust_tier,
                    success=False,
                    skipped=False,
                    error=err,
                    duration_ms=(time.monotonic() - start) * 1000,
                )
                raise
        else:
            # 5b. Resolve and call a Python callable
            fn = _resolve_tool(tool_path)

            try:
                result = fn(**resolved_args)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                _emit_audit(
                    step_id=step_id,
                    tool=tool_path,
                    trust_tier=spec.trust_tier,
                    success=False,
                    skipped=False,
                    error=err,
                    duration_ms=(time.monotonic() - start) * 1000,
                )
                raise

        duration_ms = (time.monotonic() - start) * 1000

        # 6. Store output in context
        if output_var:
            context[output_var] = result

        # 7. Audit log
        _emit_audit(
            step_id=step_id,
            tool=tool_path,
            trust_tier=spec.trust_tier,
            success=True,
            skipped=False,
            error=None,
            duration_ms=duration_ms,
        )

        return result

    # ------------------------------------------------------------------
    # Built-in step dispatch
    # ------------------------------------------------------------------

    def _run_built_in(
        self,
        step_type: str,
        args: dict,
        spec: "CoWorkerSpec",
        context: dict,
    ) -> Any:
        """Dispatch FileRead / FileWrite / RunTool to their respective modules.

        *spec* may be the local CoWorkerSpec (tests) or the richer
        team_assembler.CoWorkerSpec (production).  Both carry the required
        ``folder_access``, ``icdev_tools``, and ``trust_tier`` attributes via
        duck typing.
        """
        coworker_id: str = getattr(spec, "coworker_id", getattr(spec, "name", "unknown"))
        instance_id: str = str(context.get("instance_id", ""))
        folder_access: list = getattr(spec, "folder_access", [])
        icdev_tools: list = getattr(spec, "icdev_tools", [])

        if step_type == "FileRead":
            from icdev.tools.ace.file_access_broker import FileAccessBroker

            path = args.get("path", "")
            if not path:
                raise ValueError("FileRead step requires 'path' in args")
            broker = FileAccessBroker(folder_access)
            return broker.read(path, coworker_id=coworker_id, instance_id=instance_id)

        if step_type == "FileWrite":
            from icdev.tools.ace.file_access_broker import FileAccessBroker

            path = args.get("path", "")
            content = args.get("content", "")
            if not path:
                raise ValueError("FileWrite step requires 'path' in args")
            broker = FileAccessBroker(folder_access)
            return broker.write(path, content, coworker_id=coworker_id, instance_id=instance_id)

        if step_type == "RunTool":
            from icdev.tools.ace.tool_runner import ToolRunner

            command = args.get("command", "")
            if not command:
                raise ValueError("RunTool step requires 'command' in args")
            runner = ToolRunner(icdev_tools)
            return runner.run(
                command,
                coworker_id=coworker_id,
                instance_id=instance_id,
                trust_tier=spec.trust_tier,
            )

        raise ValueError(f"Unknown built-in step type: {step_type!r}")
