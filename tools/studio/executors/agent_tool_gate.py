# CUI // SP-CTI
"""Agent tool authorization gate — AGENT-WF-001 (hgx-agent-02).

``node_type: mcp`` steps have been default-deny since dwo-mcp-02: a step may
dispatch a tool only if ``mcp_workflow_tools`` in ``args/security_gates.yaml``
names it, a mutating tool waits on an approved ``node_type: human`` gate in the
same run, the caller's impact level and roles are checked, and every attempt
lands in append-only ``studio_mcp_dispatch_audit``. ``node_type: agent`` steps
(hgx-agent-01) had no equivalent. This module is that equivalent, and it is
deliberately the SAME mechanism — the policy loader, the human gate, the caller
resolution and the audit writer are all imported from
:mod:`tools.studio.executors.mcp_executor` rather than reimplemented. What
differs is the policy section (``agent_workflow_tools``), the refusal
vocabulary, and the gate-id namespace.

Why the gate is enforced twice
------------------------------
An mcp step names one tool at authoring time, so a reviewer can read the
authorization question off the template. An agent step names *bundles* and a
model picks, per turn, which tool to call. So:

  offer time  — :func:`authorize_toolset` filters the tools the step is about to
                describe to the model down to those this caller may use. A tool
                the caller cannot use is never named in the request, so the model
                cannot ask for it and no turn is wasted being refused.
  call time   — :func:`build_gate_hook` re-checks every call before the handler
                runs. "Not offered" is a weaker claim than "not authorized": a
                model can emit a tool name it was never given, and a future
                caller of ``build_step_toolset`` could get the offer wrong. The
                call-time check is the one that actually decides.

Composition, not replacement
----------------------------
The hook returned by :func:`build_gate_hook` chains to
``tools/agent_runtime/approval_gate.py``'s reversibility hook rather than
displacing it. They answer different questions — *may this caller use this tool
at all* (AC-3/AC-6) versus *is this particular call recoverable* (ars-appr-01) —
and a call must clear both. The authorization gate runs first: an unauthorized
tool is refused without asking anyone to review its arguments.

Usage (operator check, no loop, no LLM)::

    python tools/studio/executors/agent_tool_gate.py --tool run_command \\
        --caller-il IL4 --json

Exit 0 = authorized (``disposition`` says whether a human gate is also needed),
exit 1 = refused, with ``error_type`` naming the AGENT-WF-001 block condition.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.studio.executors import mcp_executor  # noqa: E402

#: Section of ``args/security_gates.yaml`` holding this surface's allowlist.
GATE_POLICY_KEY = "agent_workflow_tools"

#: Gate-id namespace for an agent tool's human gate. Distinct from the mcp
#: surface's ``approval:`` so approving a reviewed mcp step's ``run_command``
#: does not silently authorize an agent loop to run whatever it chooses.
APPROVAL_STEP_PREFIX = "approval:agent:"

#: What an approver reads in the pending-approvals list.
APPROVAL_LABEL = "Approve agent tool"

#: How refusal messages name this surface.
SURFACE = "Agent tool"

#: Impact level a tool runs at when the policy declares neither a per-tool
#: ``min_il`` nor a ``default_min_il``. The platform baseline is CUI/IL4.
DEFAULT_MIN_IL = "IL4"

# ── AGENT-WF-001 block conditions ──────────────────────────────────────────
# These strings are the gate's contract: they appear in `block_on` in
# args/security_gates.yaml, as the CLI's `error_type`, and as the `reason`
# column of every audit row. One vocabulary, so a refusal in the audit table and
# a refusal on the step's stdout never have to be correlated by hand.

REASON_NOT_ALLOWLISTED = "agent_tool_not_allowlisted"
REASON_EXCEEDS_IL = "agent_tool_exceeds_caller_il"
REASON_MISSING_ROLE = "agent_tool_missing_required_role"
REASON_AWAITING_APPROVAL = "agent_tool_awaiting_human_approval"
REASON_APPROVAL_REJECTED = "agent_tool_approval_rejected"
REASON_GATE_UNAVAILABLE = "agent_tool_approval_gate_unavailable"
REASON_POLICY_UNAVAILABLE = "agent_gate_policy_unavailable"

#: Warn condition: a tool survived the offer filter but was refused at call
#: time. Not expected — it means the two layers disagreed — so it is recorded
#: under its own reason rather than silently folded into the refusal above.
REASON_OFFERED_BUT_UNAUTHORIZED = "agent_tool_offered_but_unauthorized"

#: Audit reason for a call the gate authorized.
REASON_CALLED = "agent_tool_called"

#: Audit reason for a tool withheld from the model at offer time.
REASON_NOT_OFFERED = "agent_tool_not_offered"

#: Reasons that mean "parked, not denied": a human decision will settle it.
_PENDING_REASONS = frozenset({REASON_AWAITING_APPROVAL})

#: mcp refusal reason → this surface's reason. The human gate is reused wholesale
#: (one approval mechanism, per the card), so its errors arrive in the mcp
#: vocabulary and are translated once, here.
_REASON_FROM_MCP = {
    "mcp_tool_awaiting_human_approval": REASON_AWAITING_APPROVAL,
    "mcp_tool_approval_rejected": REASON_APPROVAL_REJECTED,
    "mcp_tool_approval_gate_unavailable": REASON_GATE_UNAVAILABLE,
    "gate_policy_unavailable": REASON_POLICY_UNAVAILABLE,
}

#: Disposition: offered and called with no human gate.
DISPOSITION_ALLOWED = mcp_executor.DISPOSITION_ALLOWED

#: Disposition: offered, but the call blocks on an approved human gate.
DISPOSITION_REQUIRES_APPROVAL = mcp_executor.DISPOSITION_REQUIRES_APPROVAL


class AgentToolGateError(RuntimeError):
    """A tool call was refused by AGENT-WF-001, or the policy is unusable.

    ``reason`` carries the block condition so the CLI can report it as
    ``error_type`` and the audit row can record it without re-parsing the
    message. ``step_run_id`` is the gate a refusal is parked on, when there is
    one, so an operator can approve it from the step's stdout alone.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str = "",
        reason: str = REASON_NOT_ALLOWLISTED,
        step_run_id: str = "",
    ):
        super().__init__(message)
        self.tool = tool
        self.reason = reason
        self.step_run_id = step_run_id


def _translate(exc: mcp_executor.MCPWorkflowGateError) -> AgentToolGateError:
    """Re-raise an mcp-surface gate error in this surface's vocabulary."""
    return AgentToolGateError(
        str(exc),
        tool=exc.tool,
        reason=_REASON_FROM_MCP.get(exc.reason, REASON_GATE_UNAVAILABLE),
        step_run_id=exc.step_run_id,
    )


# ── Policy ─────────────────────────────────────────────────────────────────

def load_policy(path: str | Path | None = None, *, refresh: bool = False) -> dict:
    """Return the ``agent_workflow_tools`` policy.

    Read by ``mcp_executor.load_gate_policy``, which probes the checkout, the
    ``icdev/`` mirror and a pip-installed wheel, and refuses a section whose
    ``default`` is not ``deny``.

    Raises:
        AgentToolGateError: no readable default-deny policy. Fail-closed —
            without a policy no tool is offered and no call is authorized.
    """
    try:
        return mcp_executor.load_gate_policy(
            path, refresh=refresh, key=GATE_POLICY_KEY
        )
    except mcp_executor.MCPWorkflowGateError as exc:
        raise AgentToolGateError(
            str(exc), reason=REASON_POLICY_UNAVAILABLE
        ) from exc


def _policy(policy: dict | None) -> dict:
    return policy if policy is not None else load_policy()


def allowed_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools an agent node may call with no human gate."""
    return frozenset(str(t) for t in (_policy(policy).get("allowed") or []))


def approval_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools whose first call in a run blocks on an approved human gate."""
    return frozenset(str(t) for t in (_policy(policy).get("requires_approval") or []))


def check_tool_allowed(tool: str, policy: dict | None = None) -> str:
    """Refuse ``tool`` unless the allowlist names it; return how it dispatches.

    Returns :data:`DISPOSITION_ALLOWED` or
    :data:`DISPOSITION_REQUIRES_APPROVAL`. Passing is necessary, not sufficient:
    a ``requires_approval`` tool is still uncallable until :func:`await_approval`
    clears its gate, and the caller's IL and roles are a separate check.

    Raises:
        AgentToolGateError: ``agent_tool_not_allowlisted``.
    """
    pol = _policy(policy)
    if tool in allowed_tools(pol):
        return DISPOSITION_ALLOWED
    if tool in approval_tools(pol):
        return DISPOSITION_REQUIRES_APPROVAL

    close = mcp_executor._closest(tool, sorted(allowed_tools(pol)))
    hint = f" Closest allowlisted tools: {', '.join(close)}." if close else ""
    raise AgentToolGateError(
        f"{SURFACE} '{tool}' is not allowlisted for agent workflow steps. The "
        f"{GATE_POLICY_KEY} policy is default-deny: add '{tool}' to its "
        f"'allowed' list in {mcp_executor.GATES_FILENAME} (read-only tools only) "
        f"or to 'requires_approval' (anything that writes, executes, or "
        f"egresses) to make it callable." + hint,
        tool=tool,
        reason=REASON_NOT_ALLOWLISTED,
    )


# ── Per-tool IL and role limits ────────────────────────────────────────────

#: Registry-derived limits already looked up, keyed by tool name. The lookup
#: costs a ``tools.mcp.tool_registry`` import; a step re-checks the same handful
#: of tools every turn and must not pay it each time.
_INHERITED_CACHE: dict[str, dict | None] = {}


def _inherited_limits(tool: str, registry=None) -> dict | None:
    """Limits ``tool`` inherits from the registry component owning its handler.

    ``None`` when the tool is not an MCP registry tool, which is the normal case:
    the worktree toolset's tools have no owning component, which is exactly why
    ``tool_limits`` exists. Consulted anyway so that a tool reachable from BOTH
    surfaces cannot be called at a lower impact level through this one.
    """
    if registry is None and tool in _INHERITED_CACHE:
        return _INHERITED_CACHE[tool]
    try:
        limits = mcp_executor.tool_requirements(tool, registry=registry)
    except Exception:  # noqa: BLE001 — an unknown tool simply has no owner here
        limits = None
    if registry is None:
        _INHERITED_CACHE[tool] = limits
    return limits


def tool_limits(tool: str, policy: dict | None = None, registry=None) -> dict:
    """Return the IL and role limits ``tool`` is called under.

    Declared in the policy's ``tool_limits``, falling back to its
    ``default_min_il``. Where the tool is also an MCP registry tool, the
    STRICTER impact level of the two wins, so the agent surface can never be a
    cheaper route to a tool the mcp surface holds at IL5.

    Roles do not merge: a declared ``required_roles`` replaces the inherited one
    rather than being unioned with it, because "hold any one of these" gets
    WEAKER as the set grows — a union of two role lists authorizes callers
    neither list did.

    Returns:
        ``{"min_il", "required_roles", "source", "component", "component_name"}``.
    """
    pol = _policy(policy)
    declared = (pol.get("tool_limits") or {}).get(tool) or {}

    min_il = str(
        declared.get("min_il") or pol.get("default_min_il") or DEFAULT_MIN_IL
    ).strip().upper()
    source = (
        f"{GATE_POLICY_KEY}.tool_limits.{tool}"
        if declared.get("min_il")
        else f"{GATE_POLICY_KEY}.default_min_il"
    )
    roles = mcp_executor._normalize_roles(declared.get("required_roles"))
    component = component_name = ""

    inherited = _inherited_limits(tool, registry)
    if inherited and inherited.get("component"):
        order = mcp_executor._il_order()
        inherited_il = str(inherited["min_il"]).upper()
        if order.get(inherited_il, -1) > order.get(min_il, -1):
            min_il = inherited_il
            source = f"component_registry:{inherited['component']}"
        roles = roles or tuple(inherited.get("required_roles") or ())
        component = inherited["component"]
        component_name = inherited.get("component_name") or component

    return {
        "min_il": min_il,
        "required_roles": roles,
        "source": source,
        "component": component,
        "component_name": component_name,
    }


def check_caller_authorized(
    tool: str,
    caller: dict | None = None,
    policy: dict | None = None,
    registry=None,
) -> dict:
    """Refuse ``tool`` unless the caller clears its IL and role limits.

    The impact-level ordering is imported from ``tools.security.canvas_access``
    by way of ``mcp_executor``, so raising a level in one place cannot leave this
    gate enforcing the old order.

    Returns:
        The limits the caller cleared, for the step payload and the audit row.

    Raises:
        AgentToolGateError: ``agent_tool_exceeds_caller_il`` or
            ``agent_tool_missing_required_role``.
    """
    caller = caller if caller is not None else mcp_executor.resolve_caller()
    limits = tool_limits(tool, policy=policy, registry=registry)

    try:
        order = mcp_executor._il_order()
    except mcp_executor.MCPWorkflowGateError as exc:
        raise _translate(exc) from exc

    required_il = limits["min_il"]
    caller_il = str(caller.get("impact_level") or "").upper()
    required_rank = order.get(required_il)
    caller_rank = order.get(caller_il)

    if required_rank is None:
        raise AgentToolGateError(
            f"{SURFACE} '{tool}' declares min_il {required_il!r} ({limits['source']}), "
            f"which is not a known impact level ({', '.join(sorted(order))}). "
            f"Refusing — the gate will not guess what an unrecognized level permits.",
            tool=tool,
            reason=REASON_EXCEEDS_IL,
        )
    if caller_rank is None or caller_rank < required_rank:
        detail = (
            f"caller impact level {caller_il!r} is not a known level "
            f"({', '.join(sorted(order))})"
            if caller_rank is None
            else f"caller is {caller_il}, tool requires {required_il}"
        )
        raise AgentToolGateError(
            f"{SURFACE} '{tool}' requires impact level {required_il} "
            f"(declared in {limits['source']}), but the caller cannot meet it: "
            f"{detail}. Caller IL resolved from "
            f"{caller.get('source') or 'unknown'}. Raise the run's caller context "
            f"or run this step at {required_il}.",
            tool=tool,
            reason=REASON_EXCEEDS_IL,
        )

    required_roles = limits["required_roles"]
    if required_roles:
        held = set(caller.get("roles") or ())
        if not held & set(required_roles):
            raise AgentToolGateError(
                f"{SURFACE} '{tool}' requires one of these roles: "
                f"{', '.join(sorted(required_roles))} (declared in "
                f"{limits['source']}). The caller holds "
                f"{', '.join(sorted(held)) or '(no roles)'}. Run the step as a "
                f"principal that holds one of them.",
                tool=tool,
                reason=REASON_MISSING_ROLE,
            )

    return limits


def authorize(
    tool: str,
    caller: dict | None = None,
    policy: dict | None = None,
    registry=None,
) -> dict:
    """Full authorization decision for one tool: allowlist, then IL and roles.

    Returns ``{"tool", "disposition", **limits}``. Raises
    :class:`AgentToolGateError` on any refusal — the allowlist is checked first,
    so a tool nobody allowlisted is never looked up in the component registry.
    """
    pol = _policy(policy)
    disposition = check_tool_allowed(tool, pol)
    limits = check_caller_authorized(tool, caller, pol, registry)
    return {"tool": tool, "disposition": disposition, **limits}


# ── Human gate (reuses the mcp surface's, namespaced) ──────────────────────

def await_approval(
    tool: str,
    run_id: str,
    *,
    wait_seconds: float | None = None,
    policy: dict | None = None,
    poll_seconds: float = mcp_executor.APPROVAL_POLL_SECONDS,
) -> dict:
    """Block ``tool``'s call until a human approves its gate in ``run_id``.

    The gate is a ``studio_workflow_run_steps`` row with status
    ``awaiting_approval`` — the same representation an authored ``node_type:
    human`` step writes — so ``workflow_runner.approve_step`` / ``reject_step``
    / ``get_pending_approvals``, the Details modal and the Telegram listener all
    decide it unchanged. One gate per (run, tool): an agent that writes ten
    files asks once.

    Raises:
        AgentToolGateError: rejected, undecided within the wait window, or no
            run to park a gate on. Fail-closed in every direction.
    """
    try:
        return mcp_executor.await_approval(
            tool,
            run_id,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            policy=_policy(policy),
            prefix=APPROVAL_STEP_PREFIX,
            label=APPROVAL_LABEL,
            surface=SURFACE,
            policy_key=GATE_POLICY_KEY,
        )
    except mcp_executor.MCPWorkflowGateError as exc:
        raise _translate(exc) from exc


def approval_step_id(tool: str) -> str:
    """Step id of ``tool``'s gate within a run, in this surface's namespace."""
    return mcp_executor.approval_step_id(tool, prefix=APPROVAL_STEP_PREFIX)


# ── Audit (the mcp surface's append-only table) ─────────────────────────────

def record(
    tool: str,
    tool_input,
    decision: str,
    reason: str,
    *,
    run_id: str = "",
    step_id: str = "",
    caller: dict | None = None,
    detail: str = "",
) -> tuple[bool, str]:
    """Append one row describing this attempt. Returns ``(written, why_not)``.

    Writes to ``studio_mcp_dispatch_audit`` — the same table the mcp surface
    uses, not a second one, so "what did this run try to do" stays one query.
    The tool's arguments are digested, never stored: an agent's ``write_file``
    call carries file content, and an audit row is not the place for it.

    Best-effort, like the mcp surface's: the call has already been decided by
    the gate and an unreachable audit store must not overturn that decision in
    either direction.
    """
    return mcp_executor.record_dispatch_audit(
        tool,
        tool_input,
        decision,
        reason,
        run_id=run_id,
        step_id=step_id,
        caller=caller,
        detail=detail,
    )


def _decision_for(reason: str) -> str:
    """Audit decision for a refusal reason: parked gates are pending, not denied."""
    if reason in _PENDING_REASONS:
        return mcp_executor.DECISION_PENDING_APPROVAL
    return mcp_executor.DECISION_REFUSED


# ── Offer-time filter ──────────────────────────────────────────────────────

def authorize_toolset(
    names,
    *,
    caller: dict | None = None,
    policy: dict | None = None,
    run_id: str = "",
    step_id: str = "",
    registry=None,
) -> tuple[dict, list]:
    """Split ``names`` into what this caller may be offered and what it may not.

    Returns ``({tool: disposition}, refusals)``, where each refusal is
    ``{"tool", "reason", "message"}`` and has been audited. A withheld tool is
    never described to the model, so the model cannot ask for it — but the
    refusal is still recorded, because "the step wanted this and could not have
    it" is the fact an operator needs when the step's output looks thin.

    Raises:
        AgentToolGateError: the policy is unreadable. Fail-closed: no policy
            means no toolset, not an unbounded one.
    """
    pol = _policy(policy)
    authorized: dict[str, str] = {}
    refusals: list[dict] = []
    for tool in names:
        try:
            decision = authorize(tool, caller, pol, registry)
        except AgentToolGateError as exc:
            refusals.append({
                "tool": tool,
                "reason": exc.reason,
                "message": str(exc),
            })
            record(
                tool, {}, _decision_for(exc.reason), REASON_NOT_OFFERED,
                run_id=run_id, step_id=step_id, caller=caller,
                detail=f"withheld at offer time ({exc.reason}): {exc}",
            )
            continue
        authorized[tool] = decision["disposition"]
    return authorized, refusals


# ── Call-time hook ─────────────────────────────────────────────────────────

def _blocked_message(exc: AgentToolGateError) -> str:
    """What the model is told when the gate stops a call.

    Prefixed ``BLOCKED`` to match the reversibility gate's convention, and
    explicit that retrying is pointless: a refused tool stays refused for the
    whole step, and a model that keeps retrying burns its turn budget instead of
    finding another way to finish.
    """
    return (
        f"BLOCKED by the agent tool gate (AGENT-WF-001, {exc.reason}): {exc}. "
        f"Do not retry this call — the decision does not change within this "
        f"step. Use a tool you were offered, or stop and report what is missing."
    )


def build_gate_hook(
    *,
    caller: dict | None = None,
    run_id: str = "",
    step_id: str = "",
    policy: dict | None = None,
    approval_wait: float | None = None,
    chain=None,
    registry=None,
):
    """Build the ``PreToolUseHook`` enforcing AGENT-WF-001 on every call.

    The loop's contract: return ``None`` to let the call run, or a string to halt
    it (the string is fed back to the model as the tool result, so it says why).

    Args:
        chain: A second hook consulted only after this gate authorizes the call —
            in the executor, the ars-appr-01 reversibility gate. Authorization
            first: an unauthorized tool is refused without asking anyone to
            review its arguments.

    Every evaluation appends one audit row: the authorized calls as ``allowed``,
    the refusals as ``refused``, and a call parked on an undecided human gate as
    ``pending_approval``.
    """
    pol = _policy(policy)

    def hook(tool_name: str, tool_input):
        if not isinstance(tool_input, dict):
            tool_input = {} if tool_input is None else {"value": tool_input}
        try:
            decision = authorize(tool_name, caller, pol, registry)
            approval: dict = {}
            if decision["disposition"] == DISPOSITION_REQUIRES_APPROVAL:
                # Re-read the gate per call rather than caching the approval in
                # memory: the database row is the authorization of record, and a
                # decided gate returns immediately anyway.
                approval = await_approval(
                    tool_name, run_id, wait_seconds=approval_wait, policy=pol
                )
        except AgentToolGateError as exc:
            record(
                tool_name, tool_input, _decision_for(exc.reason), exc.reason,
                run_id=run_id, step_id=step_id, caller=caller, detail=str(exc),
            )
            return _blocked_message(exc)

        record(
            tool_name, tool_input, mcp_executor.DECISION_ALLOWED, REASON_CALLED,
            run_id=run_id, step_id=step_id, caller=caller,
            detail=(
                f"{decision['disposition']} at {decision['min_il']}"
                + (
                    f"; approved by gate {approval['step_run_id']}"
                    if approval else ""
                )
            ),
        )
        return chain(tool_name, tool_input) if chain is not None else None

    return hook


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check an agent tool against AGENT-WF-001 (no loop, no LLM)"
    )
    parser.add_argument("--tool", help="Tool name to check")
    parser.add_argument("--list", action="store_true",
                        help="Print the policy's allowlist instead of checking a tool")
    parser.add_argument("--caller-il", default="",
                        help="Caller impact level (IL2|IL4|IL5|IL6)")
    parser.add_argument("--caller-roles", default="", help="Comma-separated roles")
    parser.add_argument("--caller-id", default="", help="Caller principal id")
    parser.add_argument("--tenant-id", default="", help="Caller tenant id")
    parser.add_argument("--run-id", default="",
                        help="Run whose caller context and human gates apply")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    def emit(payload: dict) -> None:
        if args.json:
            print(json.dumps(payload))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")

    try:
        policy = load_policy()
    except AgentToolGateError as exc:
        emit({"status": "failed", "error_type": exc.reason, "error": str(exc)})
        return 1

    if args.list or not args.tool:
        emit({
            "status": "ok",
            "policy": GATE_POLICY_KEY,
            "source": policy.get("_source", ""),
            "default": policy.get("default", ""),
            "allowed": sorted(allowed_tools(policy)),
            "requires_approval": sorted(approval_tools(policy)),
        })
        return 0

    caller = mcp_executor.resolve_caller(args.run_id, {
        "impact_level": args.caller_il,
        "roles": args.caller_roles,
        "principal_id": args.caller_id,
        "tenant_id": args.tenant_id,
    })
    try:
        decision = authorize(args.tool, caller, policy)
    except AgentToolGateError as exc:
        emit({
            "status": "refused",
            "error_type": exc.reason,
            "tool": args.tool,
            "caller_il": caller.get("impact_level", ""),
            "error": str(exc),
        })
        return 1

    emit({
        "status": "authorized",
        "tool": decision["tool"],
        "disposition": decision["disposition"],
        "min_il": decision["min_il"],
        "required_roles": ",".join(decision["required_roles"]),
        "caller_il": caller.get("impact_level", ""),
        "limits_source": decision["source"],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
