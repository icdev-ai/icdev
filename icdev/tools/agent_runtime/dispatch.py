# CUI // SP-CTI
"""Handler dispatch for the SAG runtime (sag-reg-02).

Discovery (sag-reg-01) produces :class:`~tools.agent_runtime.discovery.ToolSpec`
objects carrying an OpenAI schema and ``module``/``handler`` dispatch coordinates.
This module turns those coordinates into agent-loop handlers matching the
:data:`icdev.tools.llm.agent_loop.ToolHandler` contract
``handler(input_dict, stop_event) -> str``, wiring three concerns:

1. **Source-aware invocation.** MCP registry handlers have signature
   ``handle_x(args: dict) -> Any``; decorated tools take named keyword arguments;
   built-in starter tools already match the loop contract; ``external`` tools go
   out through ``tools.mcp_client``'s gate rather than being imported at all.
   Each is called the right way and its result normalised to a string.
2. **Runtime injection.** Where a handler's signature accepts ``stop_event`` or
   ``task_id``, those are injected (matching ``run_agent_loop``'s plumbing) — a
   handler that does not declare them never sees them.

   ``stop_event`` is the run's **cancellation token** (hgx-ctxw-03), and a
   handler that declares it is expected to *poll* it — in any loop, before each
   subprocess launch, and between phases of a long job — returning promptly once
   it is set. This is cooperative by necessity: Python cannot kill a thread, so
   the agent loop stops *waiting* on a handler as soon as the token fires but
   the handler itself keeps running until it notices. A handler that ignores the
   token cannot hang a turn any more, but it can still hold a worker thread (and
   delay process exit) for as long as it runs. Declaring ``stop_event`` and then
   discarding it is therefore a bug, not a formality; see
   ``mutating_tools.run_command`` and ``builtin_tools._handle_search_files`` for
   the two shapes this takes.
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
5. **The extension point (hcx-live-01).**
   :attr:`~tools.extensions.extension_manager.ExtensionPoint.TOOL_EXECUTE_BEFORE`
   is dispatched here, immediately before the safety gate. It is the *gating*
   hook point and the reason the extension manager has a behavioral tier at
   all, and until this task nothing in production dispatched it — a descriptive
   registry sitting beside an imperative hardcoded list, where the descriptive
   one silently did nothing. See :func:`_dispatch_before` for the composition
   rule, which is the security-relevant half.
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
def mutation_allowed() -> bool:
    """Whether the fail-closed default gate lets a mutating tool through.

    ``ICDEV_SAG_ALLOW_MUTATION`` → ``args/agent_runtime.yaml`` → the selected
    permission posture → ``False``. The env var is read first and still wins
    (hgx-cfg-01); the posture (hcx-post-01) is the bottom-most layer and only
    supplies a default. The final default is ``False``, so the gate stays
    fail-closed when both files are missing, empty or malformed.

    The resolution itself lives on
    :attr:`~tools.agent_runtime.config.AgentRuntimeConfig.allow_mutation`
    (hcx-post-02) so that a caller holding a ``posture_override`` copy of the
    config reads this knob through the same chain as the other three. This
    function stays the public reader — every existing call site goes through it,
    including the ``except`` fallback below, which the config layer cannot serve
    because it is precisely the case where the config layer did not import.
    """
    try:
        from tools.agent_runtime.config import load_config

        return load_config().allow_mutation
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("dispatch: config layer unavailable: %s", exc)
        return os.environ.get("ICDEV_SAG_ALLOW_MUTATION", "").strip().lower() in _TRUTHY


def default_safety_gate(
    tool_name: str, tool_input: dict[str, Any], read_only: bool
) -> "tuple[bool, str]":
    """Fail-closed default gate used until sag-safe-01 wires an approval UX.

    Read-only tools always pass. Mutating tools are refused unless the operator
    opts in with ``ICDEV_SAG_ALLOW_MUTATION`` (or ``subsystems.mutation.allow``
    in ``args/agent_runtime.yaml``).
    """
    if read_only:
        return True, ""
    if mutation_allowed():
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
# External MCP tools (hgx-fed-01)
# ---------------------------------------------------------------------------
#: Documented default for ``ICDEV_CLASSIFICATION`` (docs/operations/cicd-env-vars.md).
_FALLBACK_CLASSIFICATION = "CUI"


def default_classification() -> str:
    """Sensitivity to declare for arguments leaving for an external MCP server.

    This is the *deployment's* classification (``ICDEV_CLASSIFICATION`` — the
    same variable core profiles set), not ``UNCLASSIFIED``. An agent running on
    a CUI deployment can put CUI into a tool argument, and declaring those
    arguments unclassified would make the per-server ``classification_ceiling``
    inert in exactly the case it exists for. Unset falls back to ``CUI``,
    matching the documented default; an operator enabling an external server on
    an UNCLASSIFIED deployment sets the variable, and one sending CUI raises the
    server's ceiling — either way the declaration is a deliberate act.
    """
    value = os.environ.get("ICDEV_CLASSIFICATION", "").strip()
    return value.upper() if value else _FALLBACK_CLASSIFICATION


def _invoke_external(
    spec: ToolSpec, tool_input: dict[str, Any], classification: str
) -> str:
    """Call a third-party MCP tool through the external registry's gate.

    The registry owns every control and none is duplicated here: the tool must
    be on its server's allowlist (an unknown namespaced name is refused), the
    classification ceiling is checked before the transport dials, and air-gap
    mode leaves no server to reach. ``call`` never raises — it returns a dict —
    so a remote failure arrives as a tool result the model can read rather than
    as an exception the loop has to survive.

    Resolved from ``icdev.tools.mcp_client`` for the reason
    :func:`~tools.agent_runtime.discovery._external_registry_module` documents:
    ``tools/mcp_client/`` is a physical copy, so the two import paths hold
    different singletons, and dispatching through the other one would mean
    calling a registry that has never connected.
    """
    from icdev.tools.mcp_client.registry import get_external_registry

    result = get_external_registry().call(
        spec.name, tool_input, classification=classification
    )
    if not isinstance(result, dict):
        return _stringify(result)
    if not result.get("ok"):
        return f"error: {result.get('error') or 'external MCP call failed'}"
    return _stringify(result.get("result"))


# ---------------------------------------------------------------------------
# TOOL_EXECUTE_BEFORE extension point (hcx-live-01)
# ---------------------------------------------------------------------------
#: Cached ``(manager, ExtensionPoint)``; ``False`` once the import is known to
#: fail, so an unavailable extension package costs one failed import per
#: process rather than one per tool call.
_ext_point: Any = None


def _extension_point() -> Any:
    """The extension manager and its enum, or None when unavailable.

    Resolved from ``tools.extensions.extension_manager`` — the same import
    ``chat_manager``, ``awareness.hooks`` and ``.claude/hooks/post_tool_use.py``
    use. ``tools/extensions/`` and ``icdev/tools/extensions/`` are physically
    distinct copies holding **distinct singletons**, so dispatching through the
    other one would consult a registry no extension has ever registered with.
    Both copies of this module name ``tools.`` for that reason.
    """
    global _ext_point
    if _ext_point is False:
        return None
    if _ext_point is not None:
        return _ext_point
    try:
        from tools.extensions.extension_manager import (
            ExtensionPoint,
            extension_manager,
        )
    except Exception as exc:  # noqa: BLE001 — extensions are a layer, not a dep
        logger.debug("dispatch: extension manager unavailable: %s", exc)
        _ext_point = False
        return None
    # The ENUM, not one member: this dispatcher owns BOTH halves of the tool
    # call, and caching a single point is how TOOL_EXECUTE_AFTER ended up with
    # no dispatcher at all (autonomy-wire-01).
    _ext_point = (extension_manager, ExtensionPoint)
    return _ext_point


def _dispatch_before(
    spec: ToolSpec,
    tool_input: dict[str, Any],
    task_id: Optional[str],
) -> "tuple[dict[str, Any], str]":
    """Run TOOL_EXECUTE_BEFORE. Returns ``(tool_input, refusal)``.

    ``tool_input`` is what the tool should actually run with — a *behavioral*
    extension (``allow_modification=True``) may have rewritten it. ``refusal``
    is an extension's reason for denying the call, or ``""``.

    **Composition — an extension may deny, and may never permit.** The caller
    runs this *before* the safety gate, and the gate is then evaluated on
    whatever comes back. Two properties follow, and both are load-bearing:

    * The gate judges exactly the input the tool receives. Dispatching *after*
      the gate would let a drop-in extension file swap in a payload the gate
      never saw, which is a one-line permission bypass wearing the clothes of
      the behavioral tier.
    * Nothing runs between the gate's verdict and execution, so no extension
      can un-block what the gate blocked. A refusal here short-circuits ahead
      of the gate — strictly *more* blocking, and it spares a human approver
      being prompted to authorise a call that was already refused.

    Only three things are read back out of the returned context: ``tool_input``
    (when it is a dict), ``deny`` and ``deny_reason``. ``tool_name`` and
    ``read_only`` are taken from the :class:`ToolSpec` by the caller and are
    never re-read from here — the default gate waves every read-only tool
    through, so a context key an extension controls deciding that flag would
    skip the mutation gate outright.

    Fail-open, deliberately: with no extension manager there are no extensions,
    and extensions can only ever *add* a refusal. The safety gate is unaffected
    either way, and :meth:`ExtensionManager.dispatch` already contains a
    handler that raises.
    """
    loaded = _extension_point()
    if loaded is None:
        return tool_input, ""
    manager, points = loaded

    context = {
        "tool_name": spec.name,
        "tool_input": dict(tool_input),
        "read_only": spec.read_only,
        "source": spec.source,
        "task_id": task_id or "",
    }
    try:
        result = manager.dispatch(points.TOOL_EXECUTE_BEFORE, context)
    except Exception as exc:  # noqa: BLE001 — never crash the agent loop
        logger.warning("dispatch: TOOL_EXECUTE_BEFORE dispatch failed: %s", exc)
        return tool_input, ""

    if not isinstance(result, dict):
        return tool_input, ""

    rewritten = result.get("tool_input")
    out_input = rewritten if isinstance(rewritten, dict) else tool_input

    deny = result.get("deny")
    if not deny:
        return out_input, ""

    reason = result.get("deny_reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = deny if isinstance(deny, str) and deny.strip() else (
            f"{spec.name} refused by a TOOL_EXECUTE_BEFORE extension"
        )
    return out_input, reason


def _dispatch_after(
    spec: ToolSpec,
    tool_input: dict[str, Any],
    output: str,
    task_id: Optional[str],
) -> None:
    """Run TOOL_EXECUTE_AFTER. Observes; returns nothing; changes nothing.

    THE DEFECT THIS CLOSES (autonomy-wire-01). ``tools/awareness/hooks.py``
    subscribes a handler to this point at process start — the auto-reindex that
    refreshes ``kg_nodes`` after every Edit/Write — and NOTHING DISPATCHED IT.
    Measured 2026-08-21: of the six enabled hook points, this one had a
    registered handler and no dispatcher anywhere in the tree, so the handler
    had never fired and the feature was silently dead.
    ``capability_consumption`` reported the class 6 declared / 0 consumed, and
    that zero was the only trace.

    OBSERVE-ONLY, WHICH IS THE DIFFERENCE FROM :func:`_dispatch_before`.
    ``args/extension_config.yaml`` sets ``allow_modification: false`` here, so
    the return value is deliberately DISCARDED: nothing an after-handler returns
    can rewrite the output the model sees or deny a call that already ran. A
    post-hook able to change the result would be a second, unaudited place to
    edit tool output, reachable by dropping in a file.

    Dispatched ONLY when the tool actually ran. A call blocked by an extension
    or by the safety gate never executed, so there is no "after" for it — firing
    here would tell a handler an Edit had happened when nothing was written.

    Fail-open and never fatal: extensions are a layer, not a dependency, and an
    exception in an observer must not fail a tool call that already succeeded.
    """
    loaded = _extension_point()
    if loaded is None:
        return
    manager, points = loaded

    context = {
        "tool_name": spec.name,
        "tool_input": dict(tool_input),
        "read_only": spec.read_only,
        "source": spec.source,
        "task_id": task_id or "",
        "output": output,
    }
    try:
        manager.dispatch(points.TOOL_EXECUTE_AFTER, context)
    except Exception as exc:  # noqa: BLE001 — an observer must not fail the call
        logger.warning("dispatch: TOOL_EXECUTE_AFTER dispatch failed: %s", exc)


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
    classification: Optional[str] = None,
) -> ToolHandler:
    """Build one agent-loop handler for ``spec``, wrapping it in the safety gate.

    ``classification`` is the sensitivity declared for arguments sent to an
    ``external`` tool; it is resolved per call from :func:`default_classification`
    when not supplied, and ignored for every other source.
    """

    def _execute(tool_input: dict[str, Any], stop: "threading.Event | None") -> str:
        """Run the tool once. Raises — the caller owns failure policy."""
        if spec.source == "external":
            return _invoke_external(
                spec, tool_input, classification or default_classification()
            )

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
        """Extend, gate, execute, and annotate the record with the outcome."""
        # TOOL_EXECUTE_BEFORE runs first so the gate below judges the input the
        # tool will actually receive; see _dispatch_before for why that order
        # is the safe one. A refusal here never reaches the gate — it is an
        # additional block, never a substitute for one.
        tool_input, refusal = _dispatch_before(spec, tool_input, task_id)
        if refusal:
            _annotate(inv, status="error", error_class="ExtensionDenied",
                      error_message=refusal)
            blocked = f"blocked: {refusal}"
            _record_result(inv, blocked)
            return blocked

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
        # The tool RAN — a blocked call returned above and has no "after".
        # Observe-only: whatever a handler returns is discarded (wire-01).
        _dispatch_after(spec, tool_input, out, task_id)
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
    classification: Optional[str] = None,
) -> dict[str, ToolHandler]:
    """Build ``{tool_name: handler}`` for every spec in ``registry``.

    Args:
        registry: ``{name: ToolSpec}`` from ``discovery.build_registry``.
        safety_gate: Gate applied to mutating tools; defaults to the fail-closed
            :func:`default_safety_gate`. sag-safe-01 injects the approval gate.
        task_id: Optional task id injected into handlers that accept one.
        classification: Sensitivity declared for arguments sent to ``external``
            tools; defaults to :func:`default_classification`.
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
            spec,
            gate=gate,
            task_id=task_id,
            builtin_handlers=builtin_handlers,
            classification=classification,
        )
    return handlers
