# CUI // SP-CTI
"""ACE StepExecutor — dynamic tool invocation with trust enforcement.

Usage::

    from icdev.tools.ace.step_executor import StepExecutor
    from icdev.tools.ace.team_assembler import CoWorkerSpec
    from icdev.tools.daemon.base import TrustKernelBase

    executor = StepExecutor()
    result = executor.run(step, context, spec, trust_kernel)
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from icdev.tools.ace.team_assembler import CoWorkerSpec
from icdev.tools.daemon.base import TrustKernelBase


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolPermissionDeniedError(PermissionError):
    """Raised when step['tool'] is not in spec.tool_permissions."""


class TrustKernelDeniedError(PermissionError):
    """Raised when trust_kernel.can_execute() returns False."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
}


def _substitute(value: Any, context: dict[str, Any]) -> Any:
    """Replace $variable references in string values with context values."""
    if not isinstance(value, str):
        return value
    # Whole-value substitution: "$varname" → context[varname] (preserves type)
    m = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", value)
    if m:
        return context.get(m.group(1), value)
    # Inline substitution: "prefix_$var_suffix" → string replacement
    return _VAR_RE.sub(lambda mo: str(context.get(mo.group(1), mo.group(0))), value)


def _eval_condition(condition: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition string against context. Returns False on error."""
    try:
        return bool(eval(condition, {"__builtins__": _SAFE_BUILTINS}, dict(context)))  # noqa: S307
    except Exception:
        return False


def _resolve_tool(tool_path: str):
    """Import and return the callable at a dotted module path."""
    module_path, _, func_name = tool_path.rpartition(".")
    if not module_path or not func_name:
        raise ImportError(f"'{tool_path}' is not a valid dotted function path")
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


# ---------------------------------------------------------------------------
# StepExecutor
# ---------------------------------------------------------------------------


class StepExecutor:
    """Executes a single workflow step with trust enforcement and audit logging.

    Step dict keys:
        id (str):           Unique step identifier.
        tool (str):         Dotted function path, e.g. 'icdev.tools.foo.bar'.
        args (dict):        Keyword args; string values may contain $var refs.
        output_var (str):   Context key for storing the result (optional).
        condition (str):    Python expression; step is skipped when falsy (optional).
        instance_id (str):  ACE instance ID for audit row (optional).
    """

    def run(
        self,
        step: dict[str, Any],
        context: dict[str, Any],
        spec: CoWorkerSpec,
        trust_kernel: TrustKernelBase,
    ) -> Any:
        """Execute one workflow step.

        Returns:
            Result of the tool call, or None if the step was skipped.

        Raises:
            ToolPermissionDeniedError: Tool not in spec.tool_permissions.
            TrustKernelDeniedError:    trust_kernel.can_execute() returned False.
            ImportError / AttributeError: tool_path cannot be resolved.
        """
        step_id = step.get("id", "<unknown>")
        tool_path = step.get("tool", "")

        # 1. Handle condition — skip the entire step if it evaluates False
        condition = step.get("condition")
        if condition is not None and not _eval_condition(condition, context):
            self._audit(step, spec, "step_skipped", f"condition={condition!r} evaluated False")
            return None

        # 2. Check tool_permissions — raise before touching importlib
        if tool_path not in spec.tool_permissions:
            self._audit(
                step, spec, "tool_permission_denied",
                f"tool={tool_path!r} not in spec.tool_permissions",
            )
            raise ToolPermissionDeniedError(
                f"Tool '{tool_path}' is not permitted for coworker '{spec.coworker_id}'"
            )

        # 3. Resolve tool via importlib
        try:
            tool_fn = _resolve_tool(tool_path)
        except (ImportError, AttributeError) as exc:
            self._audit(step, spec, "tool_resolve_error", str(exc))
            raise

        # 4. Substitute $variable references in args
        raw_args: dict[str, Any] = step.get("args") or {}
        resolved_args = {k: _substitute(v, context) for k, v in raw_args.items()}

        # 5. Trust kernel check
        allowed, reason = trust_kernel.can_execute(spec.trust_tier, step_id)
        if not allowed:
            self._audit(step, spec, "trust_kernel_denied", reason)
            raise TrustKernelDeniedError(
                f"TrustKernel denied step '{step_id}' for tier '{spec.trust_tier}': {reason}"
            )

        # 6. Execute tool
        result = tool_fn(**resolved_args)

        # 7. Store result in context
        output_var = step.get("output_var")
        if output_var:
            context[output_var] = result

        # 8. Emit audit log
        self._audit(
            step, spec, "step_executed",
            json.dumps({"output_var": output_var, "result_type": type(result).__name__}),
        )

        return result

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _audit(
        step: dict[str, Any],
        spec: CoWorkerSpec,
        action: str,
        detail: str = "",
    ) -> None:
        """Write one row to ace_audit_log (append-only, best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            instance_id = step.get("instance_id") or ""
            conn = get_canvas_connection("ICDEV_ACE_DB_URL")
            try:
                conn.execute(
                    "INSERT INTO ace_audit_log "
                    "(instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (instance_id, spec.coworker_id, action, detail, "step_executor", now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # audit is best-effort; never crash the executor
