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
reported in ``unavailable_tools`` rather than silently dropped — registry tools
in an agent node arrive with hgx-agent-02's ``agent_workflow_tools`` gate, and
until then an agent node's reach is the worktree.

Approval gate
-------------
Every tool call goes through
``tools/agent_runtime/approval_gate.py::build_approval_hook`` — the same
reversibility gate the standalone runtime uses, with the same
``args/agent_approval_policy.yaml`` tiers and the same append-only
``agent_approval_log``. It is passed explicitly rather than left to
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
            f"tools reach agent nodes with hgx-agent-02's agent_workflow_tools "
            f"gate. Declare a worktree bundle instead.",
            reason="agent_step_no_tools",
        )
    return tools, kept, unavailable


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

    Returns the step payload. ``degraded`` is True when the resolved provider
    cannot serve native tool use; the loop did not run and ``degrade_reason``
    says why. Never raises :class:`AgentLoopUnsupported` — that is the whole
    point of degrading.

    Raises:
        AgentStepError: the step is unrunnable as authored (no prompt, no
            declared bundle, an unknown bundle).
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
    tools, handlers, unavailable = build_step_toolset(bundles, work_dir)
    offered = sorted(handlers)

    payload: dict = {
        "step_id": step_id,
        "llm_function": llm_function,
        "agent_tools": list(bundles),
        "tools_offered": offered,
        "work_dir": work_dir,
        "memory_key": f"{MEMORY_KEY_PREFIX}{step_id}",
    }
    if unavailable:
        payload["unavailable_tools"] = unavailable

    try:
        router = router if router is not None else _build_router()
        gate = _build_approval_hook(run_id, approval_mode)
        result = run_agent_loop(
            router,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            user_prompt=prompt,
            tools=tools,
            tool_handlers=handlers,
            llm_function=llm_function,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            effort=effort,
            approval_gate=gate,
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
        "final_content": result.final_content,
        "tool_calls": len(result.tool_call_log or []),
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_cost_usd": result.total_cost_usd,
        "artifacts": artifacts,
    })

    written, why = write_run_memory(run_id, step_id, payload)
    payload["memory_written"] = written
    if not written:
        payload["memory_skipped"] = why
    return payload


def _build_approval_hook(run_id: str, approval_mode: str):
    """The reversibility gate every tool call passes (ars-appr-01).

    Passed to ``run_agent_loop`` explicitly rather than left to the
    ``ICDEV_AGENT_APPROVAL_MODE`` default, so an agent node is gated in a
    deployment that never set that variable. An unbuildable gate is not an
    excuse to run ungated — ``run_agent_loop`` turns that into a deny-all hook.
    """
    from tools.agent_runtime.approval_gate import build_approval_hook  # noqa: PLC0415

    return build_approval_hook(
        mode=approval_mode or None,
        session_id=run_id,
    )


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
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-id", default="", help="Run-memory key suffix")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true", help="Accepted for runner parity")
    args = parser.parse_args(argv)

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
