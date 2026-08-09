"""Agent Executor — runs an agent loop as a Studio workflow step (hgx-agent-01).

Studio steps could already be a subprocess (``node_type: tool``), a registry
tool (``node_type: mcp``) or a human gate (``human`` / ``approval``). Nothing
ran an *agent loop* as a node. This executor is that fourth kind of step, and
it is deliberately shaped like ``executors/mcp_executor.py``: a subprocess
taking ``--step-id`` / ``--project-id`` / ``--run-id`` / ``--json``, reading and
writing run memory, emitting a single-line JSON object on stdout.

Usage::

    python tools/studio/executors/agent_executor.py \\
        --prompt "Add a docstring to tools/foo.py" \\
        --agent-tools worktree_build --run-id run-abc --step-id build

Contract (matches ``workflow_runner._exec_step``):
  stdout  = single-line JSON object
  exit 0  = the loop ran, **or** the step degraded (see "Degradation")
  exit 1  = the step could not be set up (bad toolset, unusable arguments) or
            the loop raised

The toolset (hgx-exec-01)
-------------------------
Tools come from ``tools/genesis/rubric_build_tools.py::build_worktree_toolset``
— read / list / grep / glob / ``git diff`` / write / patch / allowlisted
``run_command`` / ``done``, every path resolved and traversal-guarded inside the
step's ``work_dir``. That toolset is the one an ICDEV agent already builds with;
this executor does not grow a second one.

Per-step allowlist
------------------
A step declares ``agent_tools:`` — a list of bundle names from
``args/agent_toolsets.yaml`` — and the offered toolset is the worktree toolset
**intersected** with ``toolsets.resolve_bundles()``. The bundle file is the one
place tool bundles are declared, so an agent node and the standalone agent
runtime are bounded by the same data.

Default-deny: a step that declares no bundle gets no tools and is refused rather
than handed the full surface. A bundle name that resolves to a tool this
executor cannot offer (e.g. the registry-backed ``compliance`` bundle) is
reported in ``unavailable_tools`` rather than silently dropped — an agent node's
reach is the worktree, and a bundle promising more than that is an authoring
error worth naming.

Authorization gate (hgx-agent-02, AGENT-WF-001)
-----------------------------------------------
Declaring a bundle says what the STEP wants; it does not say what the CALLER may
have. ``executors/agent_tool_gate.py`` decides that, from the
``agent_workflow_tools`` section of ``args/security_gates.yaml`` — default-deny,
per-tool ``min_il`` / ``required_roles``, and a human gate on anything mutating.
It is enforced twice:

* :func:`apply_tool_gate` filters the offered toolset before the model sees it,
  so an unauthorized tool is never described and cannot be asked for.
* :func:`_build_approval_hook` re-checks every call before the handler runs,
  because "not offered" is weaker than "not authorized" — a model can name a
  tool it was never given.

Every decision — authorized, refused, or parked on an undecided human gate —
appends a row to ``studio_mcp_dispatch_audit``, the same append-only table the
mcp surface writes to.

Reversibility gate
------------------
After authorization, every tool call also goes through
``tools/agent_runtime/approval_gate.py::build_approval_hook`` — the same
reversibility gate the standalone runtime uses, with the same
``args/agent_approval_policy.yaml`` tiers and the same append-only
``agent_approval_log``. The two gates answer different questions (*may this
caller use this tool* versus *is this call recoverable*) and a call must clear
both. It is passed explicitly rather than left to
``ICDEV_AGENT_APPROVAL_MODE``, so an agent node is gated whether or not the
deployment set that variable. The default approver is the console prompt, which
denies on EOF — and a step subprocess has ``stdin=DEVNULL``, so an unattended
run fails closed on anything the policy calls irreversible or unknown.

LLM-agnostic
------------
The step names an ``llm_function``, never a model. Routing is
``args/llm_config.yaml``'s job. ``run_agent_loop`` needs native tool use, and
the CLI-bridge provider (and any model declaring ``supports_tools: false``)
cannot serve it — that raises :class:`AgentLoopUnsupported`.

Degradation
-----------
An unsupported provider does **not** fail the run. The step exits 0 with
``degraded: true`` and a human-readable ``degrade_reason``; ``workflow_runner``
records it as ``skipped`` with that reason as the step's stderr. A ``skipped``
step is not a failure, so its dependents still run and can branch on it with
``when: {field: output.degraded, ...}`` (hgx-cond-01). The alternative — failing
the run — would make a whole workflow undeployable on an air-gapped box whose
local model cannot do tool use, which is precisely the deployment ICDEV targets.

Governance profile (hgx-gov-01)
------------------------------
A step may name a ``governance_profile:`` — a gate subset declared under
``governance.profiles`` in ``args/cortex_config.yaml``. When it does, the step's
prompt and the loop's final content are run through
``tools/cortex/governance.py::GovernancePipeline`` under that profile, so a node
doing internal diligence need not pay the same seven gates as one emitting a
customer-facing artifact. ``output_redaction`` and ``provenance`` are in the
pipeline's MANDATORY_GATES and no profile can drop them, so naming a cheap
profile never costs the egress mask or the NIST-AU audit row.

A step that names NO profile runs exactly as agent nodes always have: no Cortex
pipeline, no governance report. Naming one is opting in, and the opt-in is
fail-closed — a blocked prompt, an unknown profile name, or an unreadable
governance config fails the step rather than running it ungoverned.

Run memory (dwo-mem-01 / dwo-mem-02)
------------------------------------
The step's payload is written to ``step:<step_id>``, and every file the agent
wrote or patched is declared in the payload's ``artifacts`` list, which the
runner lifts into run memory's ``artifacts`` key under this step's name.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Reserved key prefix for step results in run memory (dwo-mem-01). Same prefix
# mcp_executor writes under — one vocabulary for "what did step X produce".
MEMORY_KEY_PREFIX = "step:"

#: Routing function key when the step names none. A FUNCTION, never a model id:
#: which provider serves it is `args/llm_config.yaml`'s decision.
DEFAULT_LLM_FUNCTION = "code_generation"

#: Turn budget for one agent step.
DEFAULT_MAX_ITERATIONS = 12

#: Per-turn output token cap.
DEFAULT_MAX_TOKENS = 4096

#: Router effort tier.
DEFAULT_EFFORT = "medium"

#: System prompt when the step authors none. Says what the agent is (a step in a
#: larger workflow) rather than restating the task, which the step's own prompt
#: carries.
DEFAULT_SYSTEM_PROMPT = (
    "You are an ICDEV build agent running as one step of a durable workflow. "
    "Work only inside the worktree you were given, using the tools offered. "
    "Tools outside your allowlist are not available and asking for them will "
    "not produce them. When the task is complete, call the 'done' tool if you "
    "have it, otherwise end your turn with a short summary of what you changed."
)

#: Tools whose successful calls declare an artifact. Both take a ``path``.
_ARTIFACT_TOOLS = ("write_file", "patch_file")

#: ``operation`` recorded on this step's cortex_audit row when it names a
#: governance profile. One vocabulary for "an agent node ran governed".
GOVERNANCE_OPERATION = "studio.agent_step"


class AgentStepError(RuntimeError):
    """The step could not be set up. ``reason`` is the CLI's ``error_type``."""

    def __init__(self, message: str, *, reason: str = "agent_step_error"):
        super().__init__(message)
        self.reason = reason


# ── Per-step toolset ───────────────────────────────────────────────────────

def parse_bundles(raw) -> list[str]:
    """Normalise a step's ``agent_tools`` into an ordered list of bundle names.

    Accepts a list (how a template authors it) or a comma-separated string (how
    it survives the trip through argv). Blanks and duplicates are dropped,
    author order is kept so a refusal message reads back the way it was written.
    """
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    seen: list[str] = []
    for item in items:
        name = str(item).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def build_step_toolset(bundles: list[str], work_dir: str) -> tuple[list, dict, list]:
    """Return ``(tools, handlers, unavailable)`` for this step.

    The worktree toolset (hgx-exec-01) intersected with the tool names the
    declared bundles resolve to. ``unavailable`` names tools a bundle asked for
    that this executor cannot offer — reported, never silently dropped.

    Raises:
        AgentStepError: no bundle declared (default-deny), an unknown bundle
            name, or a declared bundle that resolves to nothing this executor
            can offer. In each case the step is unrunnable as authored, and
            saying so beats running an agent with an empty toolbox.
    """
    if not bundles:
        raise AgentStepError(
            "An agent step must declare its tools: set `agent_tools:` to one or "
            "more bundle names from args/agent_toolsets.yaml (e.g. "
            "`agent_tools: [worktree_build]`). The allowlist is default-deny — "
            "a step with no declared bundle is refused, not handed every tool.",
            reason="agent_step_no_toolset",
        )

    from tools.agent_runtime.toolsets import ToolsetError, resolve_bundles

    try:
        allowed = resolve_bundles(bundles)
    except ToolsetError as exc:
        raise AgentStepError(str(exc), reason="agent_step_unknown_bundle") from exc

    from tools.genesis.rubric_build_tools import build_worktree_toolset

    schemas, handlers = build_worktree_toolset(work_dir)
    tools = [s for s in schemas if s.get("function", {}).get("name") in allowed]
    offered = {s["function"]["name"] for s in tools}
    kept = {name: fn for name, fn in handlers.items() if name in offered}
    unavailable = sorted(allowed - offered)

    if not tools:
        raise AgentStepError(
            f"Bundles {', '.join(bundles)} resolve to "
            f"{', '.join(unavailable) or '(no tools)'}, none of which this "
            f"executor can offer. An agent node's tools come from the worktree "
            f"toolset (tools/genesis/rubric_build_tools.py); registry-backed "
            f"tools are not dispatchable from an agent node. Declare a worktree "
            f"bundle instead.",
            reason="agent_step_no_tools",
        )
    return tools, kept, unavailable


# ── Authorization gate (hgx-agent-02, AGENT-WF-001) ────────────────────────

def apply_tool_gate(
    tools: list,
    handlers: dict,
    *,
    caller: dict | None = None,
    policy: dict | None = None,
    run_id: str = "",
    step_id: str = "",
    registry=None,
) -> tuple[list, dict, list]:
    """Narrow an offered toolset to what AGENT-WF-001 authorizes this caller.

    The bundle intersection in :func:`build_step_toolset` says what the step
    *wants*; this says what the caller may *have*. Returns
    ``(tools, handlers, refusals)`` — the surviving subset plus one entry per
    withheld tool, each already audited to ``studio_mcp_dispatch_audit``.

    Withholding rather than refusing the call later is deliberate: a tool the
    model is never told about cannot be asked for, so a step running below a
    tool's declared impact level does not spend turns being refused.

    Raises:
        AgentStepError: the policy is unreadable (``agent_step_gate_unavailable``
            — fail-closed, no policy means no toolset) or every tool the step
            declared was withheld (``agent_step_all_tools_refused``, so the
            refusal is reported instead of an agent being handed an empty
            toolbox and left to discover it).
    """
    from tools.studio.executors import agent_tool_gate  # noqa: PLC0415

    names = [t.get("function", {}).get("name", "") for t in tools]
    try:
        authorized, refusals = agent_tool_gate.authorize_toolset(
            names,
            caller=caller,
            policy=policy,
            run_id=run_id,
            step_id=step_id,
            registry=registry,
        )
    except agent_tool_gate.AgentToolGateError as exc:
        raise AgentStepError(str(exc), reason="agent_step_gate_unavailable") from exc

    kept_tools = [
        t for t in tools if t.get("function", {}).get("name") in authorized
    ]
    kept_handlers = {n: fn for n, fn in handlers.items() if n in authorized}

    if not kept_tools:
        detail = "; ".join(f"{r['tool']}: {r['reason']}" for r in refusals)
        raise AgentStepError(
            f"AGENT-WF-001 withheld every tool this step declared ({detail}). "
            f"The agent_workflow_tools policy in args/security_gates.yaml is "
            f"default-deny; either allowlist these tools, or run the step as a "
            f"caller that meets their declared limits.",
            reason="agent_step_all_tools_refused",
        )
    return kept_tools, kept_handlers, refusals


# ── Artifacts ──────────────────────────────────────────────────────────────

def _artifact_path(work_dir: str, rel: str) -> str:
    """Render an agent-written path for the artifact contract.

    Repo-relative when the file is inside the checkout (what every other
    executor declares), the resolved posix path otherwise — a worktree outside
    the repo is the normal case for a build agent, and a path relative to a root
    it does not share would be unresolvable for whoever reads it later.
    """
    target = (Path(work_dir) / rel).resolve()
    try:
        return target.relative_to(_ROOT).as_posix()
    except ValueError:
        return target.as_posix()


def artifacts_from_log(tool_call_log: list, work_dir: str) -> list:
    """Files the agent wrote or patched, in the artifact contract's shape.

    Read from the loop's own record of what it did rather than by scanning the
    worktree: a scan cannot tell this step's writes from the tree it started
    with. A call the handler refused is skipped — every worktree handler reports
    failure as a string starting ``error:`` or ``refused:`` and never raises.
    """
    seen: dict[str, dict] = {}
    for call in tool_call_log or []:
        if call.get("name") not in _ARTIFACT_TOOLS or call.get("error"):
            continue
        result = str(call.get("result") or "")
        if result.startswith(("error:", "refused:", "BLOCKED")):
            continue
        rel = str((call.get("input") or {}).get("path") or "").strip()
        if not rel:
            continue
        path = _artifact_path(work_dir, rel)
        seen[path] = {
            "name": Path(rel).name,
            "path": path,
            "type": Path(rel).suffix.lstrip(".") or "file",
        }
    return list(seen.values())


# ── Run memory (dwo-mem-01) ────────────────────────────────────────────────

def write_run_memory(run_id: str, step_id: str, value: dict) -> tuple[bool, str]:
    """Persist the step result under ``step:<step_id>``. Never fails the step."""
    if not run_id:
        return False, "no --run-id supplied"
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return False, "tools.studio.run_memory not available"
    try:
        run_memory.set(run_id, f"{MEMORY_KEY_PREFIX}{step_id}", value)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — memory must never fail the step
        return False, str(exc)


# ── Governance profile (hgx-gov-01) ────────────────────────────────────────

def build_governance(profile: str, *, run_id: str = "", step_id: str = ""):
    """``(pipeline, ctx)`` for a step naming ``profile``; ``(None, None)`` for none.

    Resolving the profile HERE — before the router is built or a tool is offered
    — means a typo'd or non-conforming profile name fails the step at setup
    rather than after an agent has already spent turns editing a worktree.

    Raises:
        AgentStepError: the profile is not declared, the ``governance.profiles``
            block cannot load, or the Cortex governance package is not importable
            in this deployment. All three are fail-closed: a step that ASKED to be
            governed must not quietly run ungoverned.
    """
    profile = (profile or "").strip()
    if not profile:
        return None, None
    try:
        from tools.cortex.governance import (  # noqa: PLC0415
            GovernancePipeline,
            GovernanceProfileError,
            resolve_profile,
        )
        from tools.cortex.schemas import CortexContext  # noqa: PLC0415
    except ImportError as exc:
        raise AgentStepError(
            f"Step names governance_profile '{profile}' but the Cortex "
            f"governance package is not importable here ({exc}). Remove the "
            f"profile to run the step ungoverned, or install the package.",
            reason="agent_step_governance_unavailable",
        ) from exc

    try:
        resolve_profile(profile)
    except GovernanceProfileError as exc:
        raise AgentStepError(
            str(exc), reason="agent_step_unknown_governance_profile"
        ) from exc

    pipeline = GovernancePipeline(
        operation=GOVERNANCE_OPERATION,
        agent_id=step_id or "agent",
        profile=profile,
    )
    return pipeline, CortexContext(session_id=run_id or "")


# ── The loop ───────────────────────────────────────────────────────────────

def _build_router():
    """Construct the LLM router, or say the step cannot run a loop here.

    A deployment with no reachable provider is the same condition as a provider
    that cannot do tool use — the step degrades, it does not fail the run. So
    this is re-raised as ``AgentLoopUnsupported`` rather than as a step error.
    """
    from icdev.tools.llm.agent_loop import AgentLoopUnsupported  # noqa: PLC0415

    try:
        from tools.llm.router import LLMRouter  # noqa: PLC0415

        return LLMRouter()
    except Exception as exc:  # noqa: BLE001 — see docstring
        raise AgentLoopUnsupported(
            f"No usable LLM router in this deployment "
            f"({type(exc).__name__}: {exc})."
        ) from exc


def run(
    prompt: str,
    *,
    bundles: list[str],
    work_dir: str = "",
    run_id: str = "",
    step_id: str = "",
    system_prompt: str = "",
    llm_function: str = DEFAULT_LLM_FUNCTION,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str = DEFAULT_EFFORT,
    approval_mode: str = "",
    router=None,
    caller: dict | None = None,
    approval_wait: float | None = None,
    governance_profile: str = "",
) -> dict:
    """Run one agent step and return its payload.

    Args:
        prompt: The task. Required — an agent node with nothing to do is an
            authoring error, not an empty run.
        bundles: Bundle names from ``args/agent_toolsets.yaml`` bounding the
            step's tools.
        work_dir: Root the worktree tools are confined to (default: the repo).
        llm_function: Router routing key. Never a model id.
        approval_mode: ``enforce`` (default) | ``dry_run`` | ``off``, passed to
            the reversibility gate.
        router: Injectable ``LLMRouter`` — the seam the tests drive.
        caller: Principal the step runs as, bounding which tools AGENT-WF-001
            authorizes. Resolved from the run's context when omitted (see
            ``mcp_executor.resolve_caller``).
        approval_wait: Seconds to wait on a mutating tool's human gate. ``0``
            parks the gate without blocking.
        governance_profile: Gate subset from ``governance.profiles`` in
            ``args/cortex_config.yaml`` to run the prompt and final content
            through. Empty (the default) = no Cortex pipeline, exactly as agent
            nodes have always run.

    Returns the step payload. ``degraded`` is True when the resolved provider
    cannot serve native tool use; the loop did not run and ``degrade_reason``
    says why. Never raises :class:`AgentLoopUnsupported` — that is the whole
    point of degrading.

    Raises:
        AgentStepError: the step is unrunnable as authored (no prompt, no
            declared bundle, an unknown bundle), AGENT-WF-001 withheld every
            tool it declared, or a named governance profile could not be
            resolved / blocked the prompt.
    """
    from icdev.tools.llm.agent_loop import (  # noqa: PLC0415
        AgentLoopUnsupported,
        run_agent_loop,
    )

    prompt = (prompt or "").strip()
    if not prompt:
        raise AgentStepError(
            "An agent step must declare a `prompt:` — the task the agent is "
            "being asked to do.",
            reason="agent_step_no_prompt",
        )

    work_dir = str(Path(work_dir).resolve()) if work_dir else str(_ROOT)
    step_id = step_id or "agent"
    # Before the toolset, the router or a single turn: an unresolvable profile is
    # an authoring error and should cost nothing to discover.
    governance_profile = (governance_profile or "").strip()
    pipeline, gov_ctx = build_governance(
        governance_profile, run_id=run_id, step_id=step_id
    )
    tools, handlers, unavailable = build_step_toolset(bundles, work_dir)

    # AGENT-WF-001 before anything is described to the model: the bundles said
    # what the step wants, the gate says what this caller may have.
    caller = caller if caller is not None else _resolve_caller(run_id)
    tools, handlers, refusals = apply_tool_gate(
        tools, handlers, caller=caller, run_id=run_id, step_id=step_id
    )
    offered = sorted(handlers)

    payload: dict = {
        "step_id": step_id,
        "llm_function": llm_function,
        "agent_tools": list(bundles),
        "tools_offered": offered,
        "caller_il": caller.get("impact_level", ""),
        "caller_source": caller.get("source", ""),
        "work_dir": work_dir,
        "memory_key": f"{MEMORY_KEY_PREFIX}{step_id}",
    }
    if governance_profile:
        payload["governance_profile"] = governance_profile
    if unavailable:
        payload["unavailable_tools"] = unavailable
    if refusals:
        # Named, not silently dropped: "the step wanted this and could not have
        # it" is what an operator needs when the output looks thin.
        payload["tools_refused"] = [
            {"tool": r["tool"], "reason": r["reason"]} for r in refusals
        ]

    governance_report = None
    try:
        router = router if router is not None else _build_router()
        gate = _build_approval_hook(
            run_id, approval_mode,
            caller=caller, step_id=step_id, approval_wait=approval_wait,
        )

        def _loop(loop_prompt: str):
            return run_agent_loop(
                router,
                system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
                user_prompt=loop_prompt,
                tools=tools,
                tool_handlers=handlers,
                llm_function=llm_function,
                max_iterations=max_iterations,
                max_tokens=max_tokens,
                effort=effort,
                approval_gate=gate,
            )

        if pipeline is None:
            result = _loop(prompt)
        else:
            result, governance_report, final_content = _run_governed(
                _loop, pipeline, gov_ctx, prompt
            )
    except AgentLoopUnsupported as exc:
        # The one condition that degrades rather than fails: this deployment's
        # provider for `llm_function` cannot do native tool use.
        payload.update({
            "degraded": True,
            "degrade_reason": (
                f"Agent step degraded: the provider routed for llm_function "
                f"'{llm_function}' cannot serve native tool use — {exc}. The "
                f"step did not run; the workflow continues."
            ),
            "artifacts": [],
        })
        written, why = write_run_memory(run_id, step_id, payload)
        payload["memory_written"] = written
        if not written:
            payload["memory_skipped"] = why
        return payload

    artifacts = artifacts_from_log(result.tool_call_log, work_dir)
    payload.update({
        "degraded": False,
        "done": result.done,
        "truncated": result.truncated,
        "turns": result.turns,
        "result_subtype": result.result_subtype,
        "truncation_reason": result.truncation_reason,
        "session_id": result.session_id,
        # The governed (egress-masked) text when a profile ran — the pipeline's
        # output_redaction is not skippable, so publishing the raw final_content
        # here would leak past a gate that just did its job.
        "final_content": (
            result.final_content if governance_report is None else final_content
        ),
        "tool_calls": len(result.tool_call_log or []),
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_cost_usd": result.total_cost_usd,
        "artifacts": artifacts,
    })
    if governance_report is not None:
        payload["governance"] = governance_report.to_dict()

    written, why = write_run_memory(run_id, step_id, payload)
    payload["memory_written"] = written
    if not written:
        payload["memory_skipped"] = why
    return payload


def _run_governed(loop, pipeline, ctx, prompt: str):
    """Run ``loop`` inside ``pipeline`` and return ``(result, report, text)``.

    The loop is the pipeline's ``operation`` gate, so the prompt is screened and
    input-redacted BEFORE the provider sees it and the loop's final content is
    egress-redacted and provenance-recorded after. ``loop`` is handed the
    governed prompt and its :class:`AgentLoopResult` is carried out through a
    closure, because the pipeline governs *text* — handing it the result object
    would hash and redact a repr instead of the answer.

    ``retrieval=False``: an agent step is free-form work with no injected
    evidence set, so the grounding gates have nothing to attest against and skip
    themselves regardless of profile. ``attach=False``: an AgentLoopResult is not
    a CortexResult and the report belongs on the step payload.

    Raises:
        AgentStepError: a gate blocked the call. A step that named a profile
            asked to be governed, so a block fails the step rather than
            degrading it — unlike an unsupported provider, this is a refusal,
            not an unavailability.
    """
    from tools.cortex.governance import GovernanceBlockedError  # noqa: PLC0415

    carried: dict = {}

    def _operation(governed_prompt: str) -> str:
        carried["result"] = loop(governed_prompt)
        return carried["result"].final_content or ""

    try:
        text, report = pipeline.wrap(
            _operation, ctx, prompt=prompt, retrieval=False, attach=False
        )
    except GovernanceBlockedError as exc:
        raise AgentStepError(
            f"Governance profile '{pipeline.profile}' blocked this step at gate "
            f"'{exc.gate}': {exc.reason}",
            reason="agent_step_governance_blocked",
        ) from exc
    return carried["result"], report, text


def _resolve_caller(run_id: str) -> dict:
    """Principal this step's tool calls are authorized as.

    Reuses ``mcp_executor.resolve_caller`` so both node types read one caller
    vocabulary (run memory's ``caller`` key, then ``ICDEV_MCP_CALLER_IL`` /
    ``ICDEV_IMPACT_LEVEL`` / ``ICDEV_MCP_CALLER_ROLES``, then the IL4 baseline).
    An unresolvable caller is the baseline, not a bypass: the gate then compares
    IL4 against each tool's declared minimum, which is a real refusal for
    anything held above it.
    """
    try:
        from tools.studio.executors.mcp_executor import resolve_caller  # noqa: PLC0415

        return resolve_caller(run_id)
    except Exception:  # noqa: BLE001 — see docstring
        return {"impact_level": "", "roles": (), "source": "unresolved"}


def _build_approval_hook(
    run_id: str,
    approval_mode: str,
    *,
    caller: dict | None = None,
    step_id: str = "",
    approval_wait: float | None = None,
):
    """The two gates every tool call passes, chained.

    1. AGENT-WF-001 (hgx-agent-02) — is this caller authorized to call this tool
       at all, and if it mutates, has a human approved it in this run? Audited to
       ``studio_mcp_dispatch_audit``.
    2. The ars-appr-01 reversibility gate — is this particular call recoverable?
       Audited to ``agent_approval_log``.

    Authorization runs first and short-circuits: an unauthorized tool is refused
    without asking anyone to review its arguments. Both are passed to
    ``run_agent_loop`` explicitly rather than left to
    ``ICDEV_AGENT_APPROVAL_MODE``, so an agent node is gated in a deployment that
    never set that variable. An unbuildable gate is not an excuse to run ungated
    — ``run_agent_loop`` turns a ``None`` hook into a deny-all one.
    """
    from tools.agent_runtime.approval_gate import build_approval_hook  # noqa: PLC0415
    from tools.studio.executors import agent_tool_gate  # noqa: PLC0415

    reversibility = build_approval_hook(
        mode=approval_mode or None,
        session_id=run_id,
    )
    try:
        return agent_tool_gate.build_gate_hook(
            caller=caller,
            run_id=run_id,
            step_id=step_id,
            approval_wait=approval_wait,
            chain=reversibility,
        )
    except agent_tool_gate.AgentToolGateError as exc:
        # The policy is read (and cached) by apply_tool_gate before this runs, so
        # reaching here means it became unreadable mid-step. Fail the step with
        # the gate's own reason rather than letting it surface as a loop error —
        # running with only the reversibility gate is not the fallback.
        raise AgentStepError(str(exc), reason="agent_step_gate_unavailable") from exc


def _positive_int(raw, default: int) -> int:
    """Parse a bounded CLI integer, falling back rather than failing the step."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Executor (Studio agent node)")
    parser.add_argument("--prompt", default="", help="The task for the agent")
    parser.add_argument("--system-prompt", default="")
    parser.add_argument("--agent-tools", default="",
                        help="Comma-separated bundle names from args/agent_toolsets.yaml")
    parser.add_argument("--llm-function", default=DEFAULT_LLM_FUNCTION,
                        help="Router routing key — a FUNCTION, never a model id")
    parser.add_argument("--work-dir", default="",
                        help="Root the worktree tools are confined to (default: repo root)")
    parser.add_argument("--max-iterations", default=str(DEFAULT_MAX_ITERATIONS))
    parser.add_argument("--max-tokens", default=str(DEFAULT_MAX_TOKENS))
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--approval-mode", default="",
                        help="enforce (default) | dry_run | off")
    parser.add_argument("--governance-profile", default="",
                        help="Gate subset from governance.profiles in "
                             "args/cortex_config.yaml; empty = ungoverned, as "
                             "agent nodes have always run")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-id", default="", help="Run-memory key suffix")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true", help="Accepted for runner parity")
    # AGENT-WF-001 caller context. Same flags and same meaning as
    # mcp_executor's, because both node types resolve one caller vocabulary.
    parser.add_argument("--caller-il", default="",
                        help="Caller impact level (IL2|IL4|IL5|IL6); overrides run context")
    parser.add_argument("--caller-roles", default="",
                        help="Comma-separated caller roles; overrides run context")
    parser.add_argument("--caller-id", default="", help="Caller principal id")
    parser.add_argument("--tenant-id", default="", help="Caller tenant id")
    parser.add_argument("--approval-wait", default="",
                        help="Seconds to wait on a mutating tool's human gate; "
                             "0 parks the gate without blocking")
    args = parser.parse_args(argv)

    caller = None
    if any((args.caller_il, args.caller_roles, args.caller_id, args.tenant_id)):
        from tools.studio.executors.mcp_executor import resolve_caller  # noqa: PLC0415

        caller = resolve_caller(args.run_id, {
            "impact_level": args.caller_il,
            "roles": args.caller_roles,
            "principal_id": args.caller_id,
            "tenant_id": args.tenant_id,
        })

    try:
        payload = run(
            args.prompt,
            bundles=parse_bundles(args.agent_tools),
            work_dir=args.work_dir,
            run_id=args.run_id,
            step_id=args.step_id,
            system_prompt=args.system_prompt,
            llm_function=args.llm_function,
            max_iterations=_positive_int(args.max_iterations, DEFAULT_MAX_ITERATIONS),
            max_tokens=_positive_int(args.max_tokens, DEFAULT_MAX_TOKENS),
            effort=args.effort,
            approval_mode=args.approval_mode,
            governance_profile=args.governance_profile,
            caller=caller,
            approval_wait=(
                float(args.approval_wait)
                if str(args.approval_wait).strip()
                else None
            ),
        )
    except AgentStepError as exc:
        print(json.dumps({"status": "failed", "error_type": exc.reason,
                          "error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error_type": "agent_loop_error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    # Exit 0 on both paths. A degraded step is not a failed one — the runner
    # reads `degraded` off this payload and records the step as `skipped`.
    status = "skipped" if payload.get("degraded") else "success"
    print(json.dumps({"status": status, **payload}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
