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
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import threading
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

    def _handler(tool_input: dict[str, Any], stop: "threading.Event | None") -> str:
        if not isinstance(tool_input, dict):
            tool_input = {}
        allowed, reason = gate(spec.name, tool_input, spec.read_only)
        if not allowed:
            return f"blocked: {reason}"
        try:
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
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            logger.exception("dispatch: %s failed", spec.name)
            return f"error executing {spec.name}: {exc}"

    return _handler


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
