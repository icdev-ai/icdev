"""ACE step executor — dynamic tool invocation with trust-kernel gating."""

from __future__ import annotations

import importlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from icdev.tools.daemon.base import TrustKernelBase
from icdev.tools.db.storage import get_connection, sql_placeholder
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


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

# Intrinsic engine capabilities — always callable, never require an explicit
# grant in ``spec.tool_permissions``.
#
# ``llm_step.invoke`` is the engine asking its OWN configured LLM to carry out a
# step. It opens no file, spawns no process, and reaches nothing outside the
# router that the co-worker was already constructed with, so gating it behind a
# per-role grant protects nothing.
#
# Treating it as permissioned was a latent deadlock: CoWorkerThread._normalise_step
# rewrites every bare-string step into a ``llm_step.invoke`` call, but only two of
# the ninety shipped role YAMLs listed that dotted path in ``tool_permissions``.
# For the other eighty-eight, run() raised ToolPermissionDeniedError on every
# step; because _normalise_step also marks those steps ``required: False``, the
# error was swallowed as ``step_failed_optional`` and the co-worker finished
# ``state='complete'`` having produced nothing at all.
#
# Real agency is unaffected and still gated elsewhere: filesystem writes go
# through ``folder_access`` (FileAccessBroker) and process execution through
# ``icdev_tools`` (ToolRunner). Read/Write/Edit/Bash remain permissioned here.
_INTRINSIC_TOOLS: frozenset[str] = frozenset({
    "icdev.tools.ace.llm_step.invoke",
    "tools.ace.llm_step.invoke",
})

_AUDIT_TABLE_CREATED: set[str] = set()

# Step-level execution audit trail. This table is DISTINCT from the ACE canvas
# ``ace_audit_log`` (instance_id/coworker_id/action/detail/... created by
# tools/ace/db/init_db.py via get_canvas_connection). This one is written via
# get_connection() (the platform DB) and records per-step tool invocations
# (step_id/tool/trust_tier/success/skipped/error/duration_ms). The two tables
# previously shared the name ``ace_audit_log`` and collided; renamed to
# ``ace_step_audit_log`` so both can coexist on the same PostgreSQL backend.
_AUDIT_TABLE = "ace_step_audit_log"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS ace_step_audit_log (
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

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS ace_step_audit_log (
    id          TEXT             PRIMARY KEY,
    step_id     TEXT             NOT NULL,
    tool        TEXT             NOT NULL,
    trust_tier  TEXT             NOT NULL,
    success     INTEGER          NOT NULL,
    skipped     INTEGER          NOT NULL DEFAULT 0,
    error       TEXT,
    duration_ms DOUBLE PRECISION,
    created_at  TEXT             NOT NULL
)
"""


def _ensure_audit_table(conn: Any) -> None:
    if _AUDIT_TABLE in _AUDIT_TABLE_CREATED:
        return
    from icdev.tools.db.storage import is_pg

    conn.execute(SCHEMA_PG if is_pg(conn) else SCHEMA_SQLITE)
    conn.commit()
    _AUDIT_TABLE_CREATED.add(_AUDIT_TABLE)


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
        ph = sql_placeholder(conn)
        conn.execute(
            f"""
            INSERT INTO ace_step_audit_log
                (id, step_id, tool, trust_tier, success, skipped, error, duration_ms, created_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
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
    except Exception as exc:
        logger.debug("ace_step_audit_log write failed: %s", exc)
        pass  # audit must never crash the caller


def _substitute(value: Any, context: dict) -> Any:
    """Replace $var references in *value* with context values.

    Both exact ``$var`` references and inline substitutions are stringified,
    matching the JSON/YAML-originated step argument semantics where all values
    are text before a tool receives them.
    """
    if not isinstance(value, str):
        return value
    m = _VAR_RE.fullmatch(value)
    if m:
        key = m.group(1)
        if key in context:
            return str(context[key])
        return value
    return _VAR_RE.sub(lambda match: str(context.get(match.group(1), match.group(0))), value)


def _substitute_vars(value: Any, context: dict) -> Any:
    """Recursively replace $var references with context values."""
    if isinstance(value, str):
        return _substitute(value, context)
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
        logger.error("Cannot resolve tool path: %r", dotted)
        raise ImportError(f"Cannot resolve tool path: {dotted!r}")
    module_path, attr = parts
    try:
        mod = importlib.import_module(module_path)
        obj = getattr(mod, attr)
    except Exception as exc:
        logger.error("Failed to resolve tool %r: %s: %s", dotted, type(exc).__name__, exc)
        raise
    if not callable(obj):
        logger.error("Tool %r is not callable", dotted)
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


def normalise_step(raw_step: Any) -> dict[str, Any]:
    """Convert a dict, dataclass, or bare step name into a canonical step dict.

    Dicts pass through unchanged. Dataclass instances expose ``name``,
    ``tool``, ``params``, and ``condition``. Bare strings/ints become an
    LLM-invoke step so that role YAMLs can list simple step names.
    """
    if isinstance(raw_step, dict):
        return raw_step

    if hasattr(raw_step, "__dataclass_fields__"):
        step_id: str = getattr(raw_step, "name", str(raw_step))
        tool: str = getattr(raw_step, "tool", "") or ""
        params: dict = getattr(raw_step, "params", {}) or {}
        condition: str | None = getattr(raw_step, "condition", None)
        step: dict[str, Any] = {
            "id": step_id,
            "tool": tool or "icdev.tools.ace.llm_step.invoke",
            "args": dict(params),
        }
        if condition is not None:
            step["condition"] = condition
        if not tool:
            step["args"]["step_name"] = step_id
            step["output_var"] = f"{step_id}_result"
        return step

    step_name = str(raw_step)
    return {
        "id": step_name,
        "tool": "icdev.tools.ace.llm_step.invoke",
        "args": {"step_name": step_name},
        "output_var": f"{step_name}_result",
    }


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
        - Appends one row to ``ace_step_audit_log`` after each call (including
          skipped and failed steps).

        Raises:
            ToolPermissionDeniedError: tool not in ``spec.tool_permissions``.
            TrustKernelDeniedError: trust kernel rejected the step.
        """
        step = normalise_step(step)

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

        # 2. Verify tool is whitelisted in spec (intrinsic engine calls exempt)
        if tool_path not in _INTRINSIC_TOOLS and tool_path not in spec.tool_permissions:
            err = f"Tool '{tool_path}' not in spec.tool_permissions for '{getattr(spec, 'name', 'ace-coworker')}'"
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
                logger.error("Built-in step %r failed: %s", tool_path, err)
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
                logger.error("Step %r failed: %s", step_id, err)
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

    @staticmethod
    def _audit(step: dict, spec: CoWorkerSpec, event_type: str, detail: str) -> None:
        """Best-effort audit hook that tests may patch.

        Production code uses the module-level :func:`_emit_audit` directly;
        this static method exists so the existing test suite can intercept
        audit calls without changing call sites.
        """
        try:
            _emit_audit(
                step_id=step.get("id", "unknown"),
                tool=step.get("tool", ""),
                trust_tier=getattr(spec, "trust_tier", "green"),
                success=event_type in {"step_executed", "step_complete"},
                skipped=False,
                error=detail if event_type in {"step_failed", "import_error"} else None,
                duration_ms=0.0,
            )
        except Exception:
            pass

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
