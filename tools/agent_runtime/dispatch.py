# CUI // SP-CTI
"""Handler dispatch for the SAG runtime (sag-reg-02).

Discovery (sag-reg-01) produces :class:`~tools.agent_runtime.discovery.ToolSpec`
objects carrying an OpenAI schema and ``module``/``handler`` dispatch coordinates.
This module turns those coordinates into agent-loop handlers matching the
:data:`icdev.tools.llm.agent_loop.ToolHandler` contract
``handler(input_dict, stop_event) -> str``, wiring three concerns:

1. **Source-aware invocation.** MCP registry handlers have signature
   ``handle_x(args: dict) -> Any``; decorated tools take named keyword arguments;
   built-in starter tools already match the loop contract. Each is called the
   right way and its result normalised to a string.
2. **Runtime injection.** Where a handler's signature accepts ``stop_event`` or
   ``task_id``, those are injected (matching ``run_agent_loop``'s plumbing) — a
   handler that does not declare them never sees them.
3. **The safety hook point.** Every *mutating* tool is routed through a
   :data:`SafetyGate` before execution. sag-safe-01 injects the real approval UX
   here; until then :func:`default_safety_gate` fails closed unless
   ``ICDEV_SAG_ALLOW_MUTATION`` is set, so file writes / terminal execution can
   never run unguarded by accident.
4. **The telemetry point (hgx-obs-01).** Every call — allowed, blocked or failed
   — is recorded to ``runtime_invocations`` with ``surface="agent"``, so a SAG
   tool call is visible to ``icdev runtime top --surface agent`` exactly like an
   MCP one. This is the ONLY place a SAG tool call can be observed: a tool that
   happens to route through the MCP unified server is recorded there, but a
   built-in or decorated tool never touches that server, and every one of them
   passes through here.

   Recording is a wrapper around the whole handler body, gate included, because
   "the model asked for a tool it was not allowed to run" is exactly the kind of
   thing a run needs to show. A blocked call is recorded with status ``error``
   and the gate's reason, not silently dropped.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import threading
import time
from typing import Any, Callable, Optional

from tools.agent_runtime.discovery import ToolSpec
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.dispatch")

ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]

# A safety gate decides whether a tool call may proceed.
#   gate(tool_name, tool_input, read_only) -> (allowed, reason)
SafetyGate = Callable[[str, dict[str, Any], bool], "tuple[bool, str]"]

_MAX_RESULT_BYTES = 200_000
_TRUTHY = {"1", "true", "yes", "on"}

#: How ``error_recovery.ToolResult.render()`` opens an unsuccessful result. The
#: telemetry wrapper uses it to tell a genuine failure from a retry that worked.
_FAILURE_PREFIX = "error ["


# ---------------------------------------------------------------------------
# Safety gate (seam for sag-safe-01)
# ---------------------------------------------------------------------------
def default_safety_gate(
    tool_name: str, tool_input: dict[str, Any], read_only: bool
) -> "tuple[bool, str]":
    """Fail-closed default gate used until sag-safe-01 wires an approval UX.

    Read-only tools always pass. Mutating tools are refused unless the operator
    opts in with ``ICDEV_SAG_ALLOW_MUTATION`` in the environment.
    """
    if read_only:
        return True, ""
    if os.environ.get("ICDEV_SAG_ALLOW_MUTATION", "").strip().lower() in _TRUTHY:
        return True, ""
    return (
        False,
        f"tool {tool_name!r} mutates state and the SAG safety layer (sag-safe-01) "
        "is not yet wired. Set ICDEV_SAG_ALLOW_MUTATION=1 to allow, or supply a "
        "safety_gate to build_handlers().",
    )


# ---------------------------------------------------------------------------
# Result normalisation
# ---------------------------------------------------------------------------
def _stringify(result: Any) -> str:
    if isinstance(result, str):
        return result[:_MAX_RESULT_BYTES]
    try:
        return json.dumps(result, default=str)[:_MAX_RESULT_BYTES]
    except Exception:  # noqa: BLE001
        return str(result)[:_MAX_RESULT_BYTES]


# ---------------------------------------------------------------------------
# Callable resolution + signature-aware invocation
# ---------------------------------------------------------------------------
_resolve_cache: dict[str, Callable[..., Any]] = {}


def _resolve(module: str, handler: str) -> Optional[Callable[..., Any]]:
    key = f"{module}.{handler}"
    if key in _resolve_cache:
        return _resolve_cache[key]
    try:
        mod = importlib.import_module(module)
        fn = getattr(mod, handler)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dispatch: cannot resolve %s: %s", key, exc)
        return None
    _resolve_cache[key] = fn
    return fn


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _invoke_mcp(
    fn: Callable[..., Any],
    tool_input: dict[str, Any],
    stop: "threading.Event | None",
    task_id: Optional[str],
) -> Any:
    """Call an MCP-style ``handle_x(args) -> Any`` handler, injecting plumbing."""
    kwargs: dict[str, Any] = {}
    if _accepts(fn, "stop_event"):
        kwargs["stop_event"] = stop
    if _accepts(fn, "task_id"):
        kwargs["task_id"] = task_id
    return fn(tool_input, **kwargs)


def _invoke_decorated(
    fn: Callable[..., Any],
    tool_input: dict[str, Any],
    stop: "threading.Event | None",
    task_id: Optional[str],
) -> Any:
    """Call a decorated tool with named kwargs drawn from ``tool_input``."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {k: v for k, v in tool_input.items() if k in params}
    if "stop_event" in params:
        kwargs["stop_event"] = stop
    if "task_id" in params:
        kwargs["task_id"] = task_id
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Failure policy (arr-tax-01, arr-res-01, arr-res-02, arr-deg-01, arr-esc-01)
# ---------------------------------------------------------------------------
def _handle_failure(
    spec: ToolSpec,
    exc: Exception,
    tool_input: dict[str, Any],
    stop: "threading.Event | None",
    execute: Callable[[dict[str, Any], "threading.Event | None"], str],
    task_id: Optional[str],
) -> str:
    """Classify a tool failure and act on it, returning the string the loop sees.

    Every failure used to arrive at the model as ``error executing X: <exc>``,
    so a network blip, an absent library, a denied permission and a genuine bug
    were indistinguishable. Now each carries its disposition.

    **A retry is only ever attempted for a read-only tool.** A mutating tool
    that failed part-way may already have applied its side effect; replaying it
    could write a file twice or re-run a command whose first attempt actually
    succeeded before erroring. Transience is not a licence to duplicate a
    mutation, so mutating tools are classified and reported but never replayed.
    """
    from tools.agent_runtime.error_recovery import (
        DEGRADE,
        ESCALATE,
        RETRY_SAFE,
        ToolResult,
        classify,
        file_escalation_card,
        retry_delay_seconds,
    )

    classification = classify(exc)
    retried = False

    stop_requested = bool(stop is not None and stop.is_set())
    if (
        classification.disposition == RETRY_SAFE
        and spec.read_only
        and not stop_requested
    ):
        delay = retry_delay_seconds()
        logger.info(
            "dispatch: %s failed with %s (transient); retrying once after %.2fs",
            spec.name, classification.error_type, delay,
        )
        time.sleep(delay)
        try:
            return execute(tool_input, stop)
        except Exception as retry_exc:  # noqa: BLE001
            logger.warning("dispatch: %s retry also failed: %s", spec.name, retry_exc)
            exc = retry_exc
            classification = classify(retry_exc)
            retried = True
    elif classification.disposition == RETRY_SAFE and not spec.read_only:
        logger.info(
            "dispatch: %s failed with %s (transient) but is a mutating tool — "
            "not replayed; a partial side effect must not be duplicated",
            spec.name, classification.error_type,
        )

    if classification.disposition == ESCALATE:
        file_escalation_card(
            tool_name=spec.name,
            classification=classification,
            error_message=str(exc),
            tool_input=tool_input,
            task_id=task_id,
        )

    result = ToolResult(
        success=False,
        output=f"{spec.name}: {exc}",
        error_type=classification.error_type,
        disposition=classification.disposition,
        remediation_hint=classification.remediation_hint,
        missing_capability=classification.missing_capability,
        retried=retried,
    )
    if classification.disposition == DEGRADE:
        logger.info(
            "dispatch: %s degraded — capability %r unavailable (no install attempted)",
            spec.name, classification.missing_capability,
        )
    return result.render()


# ---------------------------------------------------------------------------
# Handler construction
# ---------------------------------------------------------------------------
def make_handler(
    spec: ToolSpec,
    *,
    gate: SafetyGate,
    task_id: Optional[str] = None,
    builtin_handlers: Optional[dict[str, ToolHandler]] = None,
) -> ToolHandler:
    """Build one agent-loop handler for ``spec``, wrapping it in the safety gate."""

    def _execute(tool_input: dict[str, Any], stop: "threading.Event | None") -> str:
        """Run the tool once. Raises — the caller owns failure policy."""
        if spec.source == "builtin":
            bh = (builtin_handlers or {}).get(spec.name)
            if bh is None:
                return f"error: no built-in handler for {spec.name!r}"
            return bh(tool_input, stop)

        if spec.source == "decorated":
            fn = spec.callable or (
                _resolve(spec.module, spec.handler)
                if spec.module and spec.handler
                else None
            )
            if fn is None:
                return f"error: cannot resolve decorated tool {spec.name!r}"
            return _stringify(_invoke_decorated(fn, tool_input, stop, task_id))

        # default: MCP-registry tool
        if not (spec.module and spec.handler):
            return f"error: {spec.name!r} has no dispatch coordinates"
        fn = _resolve(spec.module, spec.handler)
        if fn is None:
            return f"error: handler unavailable for {spec.name!r}"
        return _stringify(_invoke_mcp(fn, tool_input, stop, task_id))

    def _run(tool_input: dict[str, Any], stop: "threading.Event | None",
             inv: Any) -> str:
        """Gate, execute, and annotate the invocation record with the outcome."""
        allowed, reason = gate(spec.name, tool_input, spec.read_only)
        if not allowed:
            _annotate(inv, status="error", error_class="SafetyGateBlocked",
                      error_message=reason)
            blocked = f"blocked: {reason}"
            # Recorded like any other result so a replay shows what the model
            # actually saw — the refusal is part of the run, not a gap in it.
            _record_result(inv, blocked)
            return blocked
        try:
            out = _execute(tool_input, stop)
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            logger.exception("dispatch: %s failed", spec.name)
            out = _handle_failure(spec, exc, tool_input, stop, _execute, task_id)
            # _handle_failure retries transient read-only failures, and a retry
            # that succeeded returns the tool's real output — that call did NOT
            # fail and must not be counted as an error. Only its own rendered
            # failure does, which ToolResult.render() always prefixes.
            if out.startswith(_FAILURE_PREFIX):
                _annotate(inv, status="error", error_class=type(exc).__name__,
                          error_message=str(exc))
        _record_result(inv, out)
        return out

    def _handler(tool_input: dict[str, Any], stop: "threading.Event | None") -> str:
        if not isinstance(tool_input, dict):
            tool_input = {}
        recorder = _recorder()
        if recorder is None:
            return _run(tool_input, stop, None)
        with recorder.record(
            recorder.SURFACE_AGENT, spec.name, arg_keys=tool_input,
            session_id=task_id or "",
        ) as inv:
            return _run(tool_input, stop, inv)

    return _handler


# ---------------------------------------------------------------------------
# Invocation telemetry (hgx-obs-01)
# ---------------------------------------------------------------------------
def _recorder() -> Any:
    """The invocation recorder module, or None if it cannot be imported.

    Imported lazily and never fatally: dispatch predates the recorder and must
    keep working in a checkout where the observability package is unavailable.
    """
    try:
        from tools.observability import invocation_recorder

        return invocation_recorder
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch: invocation telemetry unavailable: %s", exc)
        return None


def _annotate(inv: Any, *, status: str, error_class: str,
              error_message: str) -> None:
    """Mark an invocation handle as failed. Safe with None and with anything."""
    if inv is None:
        return
    try:
        inv.status = status
        inv.error_class = error_class
        inv.error_message = error_message
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch: invocation annotation failed: %s", exc)


def _record_result(inv: Any, out: str) -> None:
    """Offer the tool result to the recorder.

    ``record_result`` stores nothing unless the operator has explicitly enabled
    replay; with the flag off this is a call that returns None and persists
    nothing. The decision lives in the recorder, not here, so there is exactly
    one place that decides whether a tool result may be written down.
    """
    if inv is None:
        return
    try:
        inv.record_result(out)
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch: invocation result capture failed: %s", exc)


def build_handlers(
    registry: dict[str, ToolSpec],
    *,
    safety_gate: Optional[SafetyGate] = None,
    task_id: Optional[str] = None,
) -> dict[str, ToolHandler]:
    """Build ``{tool_name: handler}`` for every spec in ``registry``.

    Args:
        registry: ``{name: ToolSpec}`` from ``discovery.build_registry``.
        safety_gate: Gate applied to mutating tools; defaults to the fail-closed
            :func:`default_safety_gate`. sag-safe-01 injects the approval gate.
        task_id: Optional task id injected into handlers that accept one.
    """
    gate = safety_gate or default_safety_gate
    builtin_handlers: dict[str, ToolHandler] = {}
    if any(s.source == "builtin" for s in registry.values()):
        try:
            from tools.agent_runtime.builtin_tools import build_builtin_toolset

            _tools, builtin_handlers = build_builtin_toolset()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dispatch: built-in toolset unavailable: %s", exc)

    handlers: dict[str, ToolHandler] = {}
    for name, spec in registry.items():
        handlers[name] = make_handler(
            spec, gate=gate, task_id=task_id, builtin_handlers=builtin_handlers
        )
    return handlers
