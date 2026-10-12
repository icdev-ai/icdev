# CUI // SP-CTI
"""exa-bench-03: declared-versus-actual capability probe for the agent adapters.

ICDEV's harness-parity claims are hand-written, and hand-written claims rot: the
untracked plan docs listed standing goals, cron, checkpoints and session search
as missing while ``tools/agent_runtime/`` had already shipped a module for each
of the four. This module replaces the claim with a measurement. It probes every
adapter registered in :mod:`tools.agents.registry` across a FIXED capability set
and reports, per (adapter, capability), what the config DECLARES next to what the
probe could ACTUALLY observe.

Not the same question as ``tools/workflow/executor_parity.py``
-------------------------------------------------------------
Do not merge these two modules. ``executor_parity`` answers **outcome parity**:
it replays a corpus of merged kanban tasks in disposable worktrees through two
executors and grades the resulting trees with the real delivery gates, producing
a pass rate per executor. It needs live model calls, minutes of wall clock, and a
corpus. This module answers **capability parity**: which features the adapter
SEAM can actually deliver, measured offline in milliseconds with no model call,
no worktree and no corpus. One tells you whether an executor can finish a job;
the other tells you whether it can be handed a job that needs streaming, a
sandbox mode or a cancel button in the first place. They are complementary and
their outputs are not interchangeable.

What "actual" means here — the seam, not the brochure
-----------------------------------------------------
Every measurement is scoped to what a consumer of
:class:`~tools.agents.adapter_base.AgentAdapter` can request or observe. That is
deliberate and it is the whole point. The Claude Code CLI runs sub-agents; the
``claude_cli`` adapter exposes no way to request one and no way to see one, so
the seam cannot deliver sub-agents and a router must not assume it can. A
capability the backend has but the adapter does not surface is a REAL gap for
anything routing through ``pick_default`` — which is every consumer the seam has.

Three statuses, and only two probe methods may assert ``present``
-----------------------------------------------------------------
``present``     the probe observed the capability.
``absent``      the probe observed its absence at the seam.
``unconfirmed`` the probe could not determine it. NEVER conflated with either.

    ``behavioral``      adapter code was executed and its real return value
                        inspected (``parse_response`` against fixtures,
                        ``build_argv`` under a differential). May assert
                        present or absent.
    ``interface``       the live adapter object was inspected — attribute
                        presence, callability, ``invoke`` signature. May assert
                        present or absent.
    ``source_evidence`` the adapter's module source documents a contract that
                        only a live run could exercise (a ``stop_event``
                        metadata key, an ``AgentResult.structured`` populated
                        inside ``invoke``). This method may ONLY produce
                        ``unconfirmed`` — grep is a lead, not a measurement.

So a declared capability the probe cannot confirm comes back ``unconfirmed``,
never ``present``. An operator reading this matrix can tell "we checked and it is
not there" apart from "we could not check", which is the distinction a
hand-written parity table structurally cannot make.

Out of scope: live probing. Nothing here starts a subprocess, opens a socket or
calls a model. A ``--live`` tier that actually runs each available adapter with a
trivial prompt would confirm several of the ``unconfirmed`` cells, and is the
obvious next task — it is not this one, because a probe that needs the network is
a probe that stops running in the air-gap deployments this matrix exists for.

Consuming the matrix
--------------------
    from tools.agents import pick_default
    adapter = pick_default("build", require=["sandbox_passthrough"])

``require`` filters candidates to adapters whose ACTUAL status is ``present``.
``unconfirmed`` does not satisfy a requirement — routing on an unverified claim
is the failure mode this module exists to end. With ``require`` unset, selection
is byte-identical to what it was before this module existed.

CLI::

    python tools/agents/capability_matrix.py --json
    python tools/agents/capability_matrix.py --adapter claude_cli --json
    python tools/agents/capability_matrix.py --capability streaming --json
    python tools/agents/capability_matrix.py --gate          # exit 1 on overclaim

OS-agnostic
-----------
No subprocess, no path construction beyond ``pathlib`` from ``__file__`` (never
``os.getcwd()`` — this runs from disposable worktrees), and every file read is
``encoding="utf-8"``. Adapters are probed on a FRESH instance
(``type(adapter)()``) wherever a probe needs to stub a method, so the shared
module-level ``ADAPTER`` singleton is never mutated by a measurement.
"""
from __future__ import annotations

import argparse
import inspect
import json
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Documented as `python tools/agents/capability_matrix.py`, and running a script
# BY PATH puts the script's own directory on sys.path, never the repo root — so
# `import tools...` fails unless the root is added here. It works without this
# under pytest and under an editable install only because those arrange the path
# ambiently; an operator following the docs gets neither.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402 — after the sys.path bootstrap above

from tools.agents import registry  # noqa: E402
from tools.agents.adapter_base import AgentAdapter, AgentSession  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402


logger = get_logger(__name__)

CONFIG_PATH = ROOT / "args" / "agent_capabilities.yaml"

# ── statuses ────────────────────────────────────────────────────────────────
PRESENT = "present"
ABSENT = "absent"
UNCONFIRMED = "unconfirmed"

# ── probe methods. Only the first two may assert present/absent. ────────────
BEHAVIORAL = "behavioral"
INTERFACE = "interface"
SOURCE_EVIDENCE = "source_evidence"

# ── verdicts (declared vs actual) ───────────────────────────────────────────
CONFIRMED = "confirmed"            # declared true,  observed present
OVERCLAIMED = "overclaimed"        # declared true,  observed absent
UNVERIFIED = "unverified"          # declared true,  could not observe
UNDECLARED_PRESENT = "undeclared_present"   # declared false, observed present
AGREED_ABSENT = "agreed_absent"    # declared false, observed absent
NOT_DECLARED = "not_declared"      # declared false, could not observe

# The capability set is FIXED. Adding one means adding a probe that can actually
# measure it; a name with no probe would be another hand-written claim wearing a
# measurement's clothes, which is the thing this module exists to remove.
CAPABILITIES: Dict[str, str] = {
    "streaming": (
        "Output can be surfaced incrementally while the run is in flight, "
        "rather than only in the returned AgentResult."
    ),
    "tool_calling": (
        "Tool invocations made during the run are reported back through the "
        "seam, so a consumer can see what the agent did."
    ),
    "sub_agents": (
        "A nested agent session can be requested or observed through the "
        "seam."
    ),
    "interruption": (
        "An in-flight run can be cancelled by the caller. A timeout the "
        "adapter enforces on itself does not count — the caller cannot "
        "change its mind."
    ),
    "sandbox_passthrough": (
        "A caller-chosen sandbox / permission mode reaches the backend "
        "instead of being fixed by the adapter."
    ),
    "context_budget": (
        "AgentSession.max_turns / max_tokens reach the backend rather than "
        "being accepted and dropped."
    ),
    "structured_output": (
        "Machine-readable fields are recovered from a raw backend response "
        "through parse_response(), not just the text echoed back."
    ),
    # omx-adapt-01. Measured through the adapter's own verify_guard()
    # self-test; an adapter without one is unconfirmed, never present.
    "guard_wired": (
        "ICDEV's PreToolUse guard (tools/airgap/hook_compat.run_pre_tool_check) "
        "sees the backend's tool calls and can refuse them, as reported by the "
        "adapter's verify_guard() self-test."
    ),
    # omx-guard-04 (L2). Two measurements must agree: the harness was seen
    # resolving its shell through $SHELL or PATH (args/guard_shell_resolution
    # .yaml, a live run), AND the shim the runner's env puts there -- passed
    # through the adapter's own build_env() -- refuses a known-bad command.
    "guard_shell": (
        "Every shell command the backend runs passes ICDEV's shared checks "
        "through the guarded shell the runner puts in front of $SHELL / PATH "
        "(tools/hooks/guard_shell.py)."
    ),
    # omx-adapt-04. DECLARED as a route, not a boolean:
    # native_subscription | cli_bridge | none. Present means the declared route
    # was measured: the adapter is a native claude.ai login, or ICDEV can point
    # its harness at the loopback claude-bridge -- AND no metered Anthropic key
    # survives the adapter's spawn env.
    "claude_route": (
        "Claude is reached on the operator's claude.ai subscription by the route "
        "declared (native_subscription | cli_bridge), and a metered Anthropic API "
        "key never reaches the backend's environment."
    ),
    # omx-guard-05 (L3). Measured by real writes: the sandbox is taken from the
    # adapter's OWN build_argv, then a write inside the worktree and into the
    # adapter's declared state dir must land and one outside must not.
    "sandboxed": (
        "An autonomous run can only write inside its task worktree (plus the "
        "harness's declared state dirs): the OS write sandbox the runner asks "
        "for wraps the adapter's command line (tools/hooks/write_sandbox.py)."
    ),
}

#: Capabilities declared as an enum rather than a boolean; "none" means false.
ROUTE_CAPABILITIES = {"claude_route"}

# ── probe fixtures ──────────────────────────────────────────────────────────
# Every fixture is fed to every adapter and the best result wins: an adapter is
# only expected to parse its OWN backend's format, and feeding it a foreign one
# must not be scored against it.
_FIXTURE_CLAUDE_ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 3,
        "session_id": "probe-session",
        "result": "Task completed by the probe fixture.",
        "total_cost_usd": 0.0123,
        "duration_api_ms": 4321,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "tool_calls": [{"name": "Edit", "input": {"file_path": "a.py"}}],
    }
)

_FIXTURE_CODEX_JSONL = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "session.created", "session_id": "probe-session"},
        {"msg": {"type": "exec_command", "command": "ruff check ."}},
        {"msg": {"type": "exec_command", "command": "pytest -q"}},
        {"msg": {"type": "patch_apply", "unified_diff": "--- a\n+++ b\n+probe\n"}},
        {"msg": {"type": "agent_message", "message": "Applied the probe patch."}},
        {"msg": {"type": "task_complete", "last_agent_message": "Task completed."}},
    )
)

_FIXTURE_PLAIN_TEXT = (
    "I inspected the repository and applied one change.\n\n"
    '```json\n{"tool_calls": [{"name": "write_file", "path": "a.py"}]}\n```\n\n'
    "--- a/a.py\n+++ b/a.py\n+probe\n\nTask completed.\n"
)

# omx-adapt-01. The ``opencode run --format json`` stream shape recorded in
# docs/research/omx-spike-01/evidence/ -- one completed and one guard-refused
# tool call, then the final text and a ``stop`` step.
_FIXTURE_OPENCODE_JSONL = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "step_start", "sessionID": "ses_probe", "part": {"type": "step-start"}},
        {"type": "tool_use", "sessionID": "ses_probe", "part": {
            "tool": "bash", "callID": "call_1",
            "state": {"status": "completed", "input": {"command": "ls"},
                      "output": "a.py"}}},
        {"type": "tool_use", "sessionID": "ses_probe", "part": {
            "tool": "bash", "callID": "call_2",
            "state": {"status": "error", "input": {"command": "rm ../victim/a"},
                      "error": "ICDEV guard: BLOCKED"}}},
        {"type": "step_finish", "sessionID": "ses_probe", "part": {
            "reason": "tool-calls", "tokens": {"total": 120, "input": 100,
                                              "output": 20}, "cost": 0}},
        {"type": "text", "sessionID": "ses_probe", "part": {
            "type": "text", "text": "Task completed by the probe fixture."}},
        {"type": "step_finish", "sessionID": "ses_probe", "part": {
            "reason": "stop", "tokens": {"total": 60, "input": 50,
                                        "output": 10}, "cost": 0}},
    )
)

# omx-adapt-02. The ``pi --mode json`` record shape recorded in
# docs/research/omx-spike-01/evidence/pi-icdev.txt -- one completed and one
# guard-refused tool call, then a final ``stop`` message and ``agent_settled``.
_FIXTURE_PI_JSONL = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "session", "version": 3, "id": "pi_probe", "cwd": "."},
        {"type": "agent_start"},
        {"type": "tool_execution_start", "toolCallId": "call_1",
         "toolName": "bash", "args": {"command": "ls"}},
        {"type": "tool_execution_end", "toolCallId": "call_1",
         "toolName": "bash", "isError": False,
         "result": {"content": [{"type": "text", "text": "a.py"}]}},
        {"type": "tool_execution_start", "toolCallId": "call_2",
         "toolName": "bash", "args": {"command": "rm ../victim/a"}},
        {"type": "tool_execution_end", "toolCallId": "call_2",
         "toolName": "bash", "isError": True,
         "result": {"content": [{"type": "text", "text": "ICDEV guard: BLOCKED"}]}},
        {"type": "message_end", "message": {
            "role": "assistant", "stopReason": "stop",
            "content": [{"type": "text",
                         "text": "Task completed by the probe fixture."}],
            "usage": {"input": 50, "output": 10, "totalTokens": 60,
                      "cost": {"total": 0}}}},
        {"type": "agent_settled", "aborted": False},
    )
)

_FIXTURES: Dict[str, str] = {
    "claude_json_envelope": _FIXTURE_CLAUDE_ENVELOPE,
    "codex_jsonl": _FIXTURE_CODEX_JSONL,
    "opencode_jsonl": _FIXTURE_OPENCODE_JSONL,
    "pi_jsonl": _FIXTURE_PI_JSONL,
    "plain_text": _FIXTURE_PLAIN_TEXT,
}

# Values chosen so they cannot collide with anything an adapter would put on an
# argv of its own accord.
_SENTINEL_EXE = "icdev-capability-probe-executable"
_SENTINEL_SANDBOX = "icdev-probe-sandbox-mode"
_SENTINEL_TURNS = 97
_SENTINEL_TOKENS = 4242

# Names a streaming / delegation / cancellation entry point would plausibly use.
# Matching is on the PUBLIC surface only: a private helper is not a contract a
# consumer of the seam is allowed to call.
_STREAMING_ENTRY_POINTS = ("spawn", "stream", "iter_events", "invoke_streaming")
_STREAMING_CALLBACK_PARAMS = ("on_output", "on_event", "on_delta", "on_token", "stream")
_SUB_AGENT_ENTRY_POINTS = (
    "spawn_sub_agent", "sub_agent", "delegate", "fork_session", "run_sub_agent",
)
_CANCEL_PARAMS = ("stop_event", "cancel_event", "cancel", "abort_event")


@dataclass
class ProbeOutcome:
    """One measured cell of the matrix."""

    status: str
    method: str
    evidence: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "actual": self.status,
            "method": self.method,
            "evidence": self.evidence,
        }
        if self.detail:
            out["detail"] = self.detail
        return out


def _unconfirmed(evidence: str, method: str = SOURCE_EVIDENCE, **detail: Any) -> ProbeOutcome:
    return ProbeOutcome(UNCONFIRMED, method, evidence, detail)


# ── configuration (the DECLARED side) ───────────────────────────────────────
def load_declarations(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Read the hand-written capability claims.

    This file is the thing under test. It is loaded, never validated against
    reality — comparing it to reality is what the rest of the module does.
    """
    target = path or CONFIG_PATH
    if not target.exists():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 — a broken claims file is not fatal
        logger.warning("agent capability declarations parse failed: %s", exc)
        return {}


def _as_claim(capability: str, value: Any) -> bool:
    """A declared value as the boolean the verdict table compares.

    A route capability is claimed unless it says ``none`` -- ``bool("none")``
    would be True, which is how a route-shaped claim would silently overclaim.
    """
    if capability in ROUTE_CAPABILITIES:
        return str(value or "none").strip().lower() != "none"
    return bool(value)


def declared_route(adapter_name: str, config: Optional[Dict[str, Any]] = None) -> str:
    """The raw ``claude_route`` claim for one adapter (``none`` when absent)."""
    cfg = config if config is not None else load_declarations()
    raw = (((cfg.get("declared") or {}).get(adapter_name) or {}).get("claude_route"))
    return str(raw or "none").strip().lower()


def declared_for(adapter_name: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Declared capabilities for one adapter, plus where the claim came from.

    An adapter object carrying its own ``CAPABILITIES`` mapping wins over the
    YAML — that is the migration path for an adapter that would rather own its
    claim in code. Nothing in the tree does this yet.
    """
    cfg = config if config is not None else load_declarations()
    try:
        adapter = registry.get_adapter(adapter_name)
    except KeyError:
        adapter = None

    own = getattr(adapter, "CAPABILITIES", None) if adapter is not None else None
    if isinstance(own, dict):
        return {
            "source": f"{adapter_name}.CAPABILITIES",
            "values": {k: _as_claim(k, v) for k, v in own.items() if k in CAPABILITIES},
        }

    declared = ((cfg.get("declared") or {}).get(adapter_name) or {})
    values = {k: _as_claim(k, v) for k, v in declared.items() if k in CAPABILITIES}
    return {
        "source": str(CONFIG_PATH.relative_to(ROOT)).replace("\\", "/") if values else "none",
        "values": values,
    }


# ── probe helpers ───────────────────────────────────────────────────────────
def _public_callable(adapter: Any, name: str) -> bool:
    """True when ``name`` is a public, callable attribute of the adapter."""
    if name.startswith("_"):
        return False
    attr = getattr(adapter, name, None)
    return callable(attr)


def _invoke_params(adapter: Any) -> List[str]:
    """Parameter names of ``adapter.invoke``, or [] if it cannot be inspected."""
    try:
        return list(inspect.signature(adapter.invoke).parameters)
    except (TypeError, ValueError):  # builtins / C-implemented callables
        return []


def _adapter_source(adapter: Any) -> str:
    """The adapter class's module source, or "" when it cannot be read.

    Used only by ``source_evidence`` probes, which may never assert present.
    """
    try:
        path = inspect.getsourcefile(type(adapter))
        if not path:
            return ""
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return ""


def _probe_instance(adapter: Any) -> Optional[Any]:
    """A throwaway instance of the adapter's class, or None.

    Probes that stub a method must never mutate the module-level ``ADAPTER``
    singleton — another session's dispatch is holding the same object.
    """
    try:
        return type(adapter)()
    except Exception:  # noqa: BLE001 — an adapter needing constructor args opts out
        return None


def _session(**overrides: Any) -> AgentSession:
    base: Dict[str, Any] = {
        "task_id": "capability-probe",
        "prompt": "probe",
        "working_dir": str(ROOT),
    }
    base.update(overrides)
    return AgentSession(**base)


def _build_argv(adapter: Any, metadata: Dict[str, Any], **session_kwargs: Any) -> List[str]:
    """Construct an argv with the executable resolver stubbed out.

    The measurement is of the adapter's OWN command construction, so it must not
    depend on whether the backend binary happens to be installed on this host —
    otherwise the matrix would silently change shape between a developer laptop
    and an air-gapped runner. ``resolve`` is stubbed on a throwaway instance.
    """
    probe = _probe_instance(adapter)
    if probe is None or not hasattr(probe, "build_argv"):
        raise AttributeError("no build_argv on a constructible instance")
    if hasattr(probe, "resolve"):
        probe.resolve = lambda: _SENTINEL_EXE  # type: ignore[method-assign]
    return [str(a) for a in probe.build_argv(_session(metadata=metadata, **session_kwargs))]


def _parse_all_fixtures(adapter: Any) -> Dict[str, Any]:
    """Run ``parse_response`` over every fixture; record results and errors."""
    results: Dict[str, Any] = {}
    for name, raw in _FIXTURES.items():
        try:
            parsed = adapter.parse_response(raw)
        except Exception as exc:  # noqa: BLE001 — a raising adapter is a finding
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        results[name] = {"parsed": parsed if isinstance(parsed, dict) else None}
    return results


def _reported_tool_calls(parsed: Dict[str, Any]) -> int:
    """How many tool calls this parse result reports, by either convention."""
    calls = parsed.get("tool_calls")
    if isinstance(calls, (list, tuple)):
        n = len(calls)
        if n:
            return n
    count = parsed.get("tool_call_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    return 0


# ── the probes ────────────────────────────────────────────────────────
def _probe_streaming(adapter: Any) -> ProbeOutcome:
    for name in _STREAMING_ENTRY_POINTS:
        if _public_callable(adapter, name):
            return ProbeOutcome(
                PRESENT, INTERFACE,
                f"public {name}() gives the caller the live run to read from "
                f"as it produces output",
                {"entry_point": name},
            )
    params = _invoke_params(adapter)
    hit = [p for p in params if p in _STREAMING_CALLBACK_PARAMS]
    if hit:
        return ProbeOutcome(
            PRESENT, INTERFACE,
            f"invoke() accepts a streaming callback: {', '.join(hit)}",
            {"params": hit},
        )
    return ProbeOutcome(
        ABSENT, INTERFACE,
        "no incremental-output entry point: no public spawn/stream method and "
        "invoke() takes no output callback, so output is only visible once the "
        "run has finished",
        {"invoke_params": params},
    )


def _probe_tool_calling(adapter: Any) -> ProbeOutcome:
    results = _parse_all_fixtures(adapter)
    errors = {k: v["error"] for k, v in results.items() if "error" in v}
    best_fixture, best_count = "", 0
    for name, res in results.items():
        parsed = res.get("parsed")
        if not isinstance(parsed, dict):
            continue
        count = _reported_tool_calls(parsed)
        if count > best_count:
            best_fixture, best_count = name, count

    if best_count:
        return ProbeOutcome(
            PRESENT, BEHAVIORAL,
            f"parse_response() reported {best_count} tool call(s) from the "
            f"{best_fixture} fixture",
            {"fixture": best_fixture, "tool_calls": best_count},
        )
    if errors and len(errors) == len(results):
        return _unconfirmed(
            "parse_response() raised on every fixture; nothing could be "
            "measured through the seam",
            method=BEHAVIORAL, errors=errors,
        )
    return ProbeOutcome(
        ABSENT, BEHAVIORAL,
        "parse_response() reported zero tool calls for every fixture, "
        "including one carrying explicit tool-call events — the backend may "
        "call tools, but the seam does not surface them",
        {"fixtures": sorted(results)},
    )


def _probe_sub_agents(adapter: Any) -> ProbeOutcome:
    for name in _SUB_AGENT_ENTRY_POINTS:
        if _public_callable(adapter, name):
            return ProbeOutcome(
                PRESENT, INTERFACE,
                f"public {name}() lets a caller request a nested session",
                {"entry_point": name},
            )
    return ProbeOutcome(
        ABSENT, INTERFACE,
        "no delegation entry point on the adapter: a nested session can "
        "neither be requested nor observed through the seam, whatever the "
        "backend does internally",
    )


def _probe_interruption(adapter: Any) -> ProbeOutcome:
    if _public_callable(adapter, "spawn"):
        return ProbeOutcome(
            PRESENT, INTERFACE,
            "public spawn() hands the caller the live process handle, so the "
            "caller owns poll/kill and can cancel the run",
            {"entry_point": "spawn"},
        )
    params = _invoke_params(adapter)
    hit = [p for p in params if p in _CANCEL_PARAMS]
    if hit:
        return ProbeOutcome(
            PRESENT, INTERFACE,
            f"invoke() accepts a cancellation handle: {', '.join(hit)}",
            {"params": hit},
        )

    source = _adapter_source(adapter)
    for key in _CANCEL_PARAMS:
        if f'"{key}"' in source or f"'{key}'" in source:
            return _unconfirmed(
                f"the module documents a {key!r} session-metadata contract, "
                f"but nothing on the seam exposes it and only a live run "
                f"could show whether it actually stops the work",
                metadata_key=key,
            )
    return ProbeOutcome(
        ABSENT, INTERFACE,
        "nothing to cancel with: no spawn(), no cancellation parameter and no "
        "stop-event metadata contract. A caller can only wait for the "
        "adapter's own timeout",
    )


def _probe_sandbox_passthrough(adapter: Any) -> ProbeOutcome:
    try:
        baseline = _build_argv(adapter, {})
        probed = _build_argv(adapter, {"sandbox": _SENTINEL_SANDBOX})
    except AttributeError:
        source = _adapter_source(adapter)
        if "sandbox" in source.lower():
            return _unconfirmed(
                "the module mentions a sandbox but builds no command line, so "
                "whether a caller-chosen mode reaches the backend can only be "
                "seen in a live run",
            )
        return ProbeOutcome(
            ABSENT, INTERFACE,
            "no command line to carry a sandbox mode and no sandbox contract "
            "in the module; a caller cannot choose one",
        )
    except Exception as exc:  # noqa: BLE001 — an argv that will not build is a finding
        return _unconfirmed(
            f"build_argv() raised during the probe: {type(exc).__name__}: {exc}",
            method=BEHAVIORAL,
        )

    if _SENTINEL_SANDBOX in probed and _SENTINEL_SANDBOX not in baseline:
        return ProbeOutcome(
            PRESENT, BEHAVIORAL,
            "a caller-supplied sandbox mode appears on the constructed command "
            "line and changes it",
            {"baseline": _redact_exe(baseline), "probed": _redact_exe(probed)},
        )
    return ProbeOutcome(
        ABSENT, BEHAVIORAL,
        "the constructed command line is byte-identical with and without a "
        "caller-supplied sandbox mode — the adapter fixes the backend's "
        "permission posture and the caller cannot change it",
        {"baseline": _redact_exe(baseline)},
    )


def _probe_context_budget(adapter: Any) -> ProbeOutcome:
    try:
        argv = _build_argv(
            adapter, {}, max_turns=_SENTINEL_TURNS, max_tokens=_SENTINEL_TOKENS
        )
    except AttributeError:
        source = _adapter_source(adapter)
        hits = [
            expr for expr in ("session.max_turns", "session.max_tokens")
            if expr in source
        ]
        if hits:
            return _unconfirmed(
                "the module passes " + " and ".join(hits) + " into its backend "
                "call, but there is no command line to inspect and only a live "
                "run could show the budget being enforced",
                referenced=hits,
            )
        return ProbeOutcome(
            ABSENT, INTERFACE,
            "no command line and no reference to the session's turn or token "
            "budget; both fields are accepted and dropped",
        )
    except Exception as exc:  # noqa: BLE001
        return _unconfirmed(
            f"build_argv() raised during the probe: {type(exc).__name__}: {exc}",
            method=BEHAVIORAL,
        )

    carried = [
        label for label, value in (
            ("max_turns", str(_SENTINEL_TURNS)),
            ("max_tokens", str(_SENTINEL_TOKENS)),
        ) if value in argv
    ]
    if carried:
        return ProbeOutcome(
            PRESENT, BEHAVIORAL,
            f"the session's {' and '.join(carried)} reach the backend on the "
            f"constructed command line",
            {"carried": carried, "argv": _redact_exe(argv)},
        )
    return ProbeOutcome(
        ABSENT, BEHAVIORAL,
        "neither max_turns nor max_tokens appears on the constructed command "
        "line — the session's budget is accepted and dropped",
        {"argv": _redact_exe(argv)},
    )


def _probe_structured_output(adapter: Any) -> ProbeOutcome:
    results = _parse_all_fixtures(adapter)
    errors = {k: v["error"] for k, v in results.items() if "error" in v}
    for name, res in results.items():
        parsed = res.get("parsed")
        if not isinstance(parsed, dict):
            continue
        raw = _FIXTURES[name]
        extracted = [
            key for key, value in parsed.items()
            if key != "content" and value not in (None, "", [], {}, 0, False)
        ]
        content = parsed.get("content")
        recovered_text = isinstance(content, str) and content.strip() and content != raw
        if extracted or recovered_text:
            return ProbeOutcome(
                PRESENT, BEHAVIORAL,
                f"parse_response() recovered machine-readable fields from the "
                f"{name} fixture",
                {
                    "fixture": name,
                    "fields": sorted(extracted),
                    "recovered_text": bool(recovered_text),
                },
            )

    if errors and len(errors) == len(results):
        return _unconfirmed(
            "parse_response() raised on every fixture",
            method=BEHAVIORAL, errors=errors,
        )

    source = _adapter_source(adapter)
    if "structured=" in source or "structured:" in source:
        return _unconfirmed(
            "parse_response() echoes the raw response unchanged, but invoke() "
            "does populate AgentResult.structured from the backend's own "
            "envelope — that path needs a live run, so the seam's offline "
            "entry point cannot confirm it",
        )
    return ProbeOutcome(
        ABSENT, BEHAVIORAL,
        "parse_response() echoes the raw response unchanged for every fixture "
        "and nothing else emits structure",
        {"fixtures": sorted(results)},
    )


def _probe_guard_wired(adapter: Any) -> ProbeOutcome:
    """Ask the adapter's own guard self-test, never its source.

    A plugin file existing is not the guard working (omx-spike-01: ``--pure``
    loaded no plugin while the file sat on disk), so the only thing that may
    assert ``present`` is a ``verify_guard()`` that reports ``wired`` -- and
    omx-guard-01 makes that a live refusal of a known-bad input through the
    adapter's own invocation. No self-test is ``unconfirmed``: claude_cli's
    guard lives in .claude/settings.json, outside the seam.
    """
    if not _public_callable(adapter, "verify_guard"):
        return _unconfirmed(
            "no verify_guard() self-test on the adapter; whether ICDEV's guard "
            "sees the backend's tool calls can only be shown by a live run",
        )
    try:
        report = adapter.verify_guard()
    except Exception as exc:  # noqa: BLE001 -- a raising self-test is a finding
        return _unconfirmed(
            f"verify_guard() raised: {type(exc).__name__}: {exc}",
            method=BEHAVIORAL,
        )
    if not isinstance(report, dict) or not isinstance(report.get("wired"), bool):
        return _unconfirmed(
            "verify_guard() returned no boolean 'wired' field",
            method=BEHAVIORAL,
        )
    reason = str(report.get("reason") or "")
    suffix = f": {reason}" if reason else ""
    if report["wired"]:
        return ProbeOutcome(
            PRESENT, BEHAVIORAL,
            "verify_guard() reports the guard refusing a known-bad tool call"
            + suffix,
        )
    return ProbeOutcome(
        ABSENT, BEHAVIORAL,
        "verify_guard() reports the guard is not wired" + suffix,
    )


def _probe_guard_shell(adapter: Any) -> ProbeOutcome:
    """L2: does the guarded shell sit in front of the shell this harness runs?

    ``absolute`` in the resolution record is a measured absence -- no PATH or
    $SHELL shim can see a shell exec'd by absolute path. ``unmeasured`` and an
    OS where the shim cannot be wired are unconfirmed, never present.
    """
    from tools.hooks import guard_shell  # noqa: PLC0415

    name = getattr(adapter, "name", "")
    record = guard_shell.load_resolution().get(name) or {}
    resolves = record.get("resolves", "unmeasured")
    where = f"{record.get('harness', name)} on {record.get('os', '?')}, " \
            f"{record.get('measured_at', '?')}"
    if resolves == "absolute":
        return ProbeOutcome(
            ABSENT, BEHAVIORAL,
            f"measured ({where}): the harness execs {record.get('shell', 'its shell')} "
            "by absolute path, so no $SHELL/PATH shim sees its commands",
        )
    if resolves not in (guard_shell.VIA_SHELL, guard_shell.VIA_PATH):
        return _unconfirmed(
            "no live measurement of how this harness finds its shell: "
            + str(record.get("evidence") or "not in args/guard_shell_resolution.yaml"),
        )
    wired = guard_shell.wiring()
    if not wired["supported"]:
        return _unconfirmed(wired["reason"], method=BEHAVIORAL)
    env = dict(wired["env"])
    if _public_callable(adapter, "build_env"):
        env = adapter.build_env(_session(metadata={"env": wired["env"]}))
    report = guard_shell.self_test(env, resolves, record.get("shell", "bash"))
    status = PRESENT if report["wired"] else ABSENT
    return ProbeOutcome(
        status, BEHAVIORAL,
        f"measured ({where}) to resolve its shell via {resolves}; through the "
        f"adapter's spawn env: {report['reason']}",
    )


_METERED_SENTINEL = "icdev-capability-probe-metered-key"


def _probe_claude_route(adapter: Any, config: Optional[Dict[str, Any]] = None) -> ProbeOutcome:
    """Measure the route this adapter reaches Claude by, against its claim.

    Two facts are measured. (1) The route: a native claude.ai login
    (``http_bridge.NATIVE_SUBSCRIPTION_ADAPTERS``, from omx-spike-02's
    ``claude auth status``) or a harness ``icdev omarchy setup`` can point at
    the bridge -- its config is RENDERED here and must target the loopback
    URL. (2) The spawn env: a metered key planted in ``metadata['env']`` must
    not survive ``build_env()``; if it does, no route is subscription-only.
    """
    from tools.llm.cli_bridge import http_bridge  # noqa: PLC0415

    name = getattr(adapter, "name", "")
    declared = declared_route(name, config)
    leaks: Optional[bool] = None
    if _public_callable(adapter, "build_env"):
        planted = {var: _METERED_SENTINEL for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        env = adapter.build_env(_session(metadata={"env": planted}))
        leaks = any(env.get(var) == _METERED_SENTINEL for var in planted)

    measured, how = "none", "no native claude.ai login and no bridge harness config"
    if name in http_bridge.NATIVE_SUBSCRIPTION_ADAPTERS:
        measured, how = "native_subscription", "the backend is Claude Code under its own claude.ai login"
    elif name in http_bridge.BRIDGE_HARNESSES:
        harness = http_bridge.BRIDGE_HARNESSES[name]
        url = http_bridge.bridge_url(http_bridge.DEFAULT_PORT)
        rendered = http_bridge.render_harness_config(
            harness, {}, url=url, token_file=pathlib.Path("probe.token"), token="probe",
            models=list(http_bridge.DEFAULT_MODELS))
        text = json.dumps(rendered)
        if http_bridge.PROVIDER_ID in text and url in text:
            measured, how = "cli_bridge", f"rendered {harness} config targets {url}"
        else:
            how = f"rendered {harness} config does not target {url}"

    detail = {"declared_route": declared, "measured_route": measured,
              "metered_key_reaches_backend": leaks}
    if measured == "none":
        # A backend with no Claude route cannot bill Claude, metered key or not.
        return ProbeOutcome(ABSENT, BEHAVIORAL, f"measured route none: {how}", detail)
    if leaks:
        return ProbeOutcome(ABSENT, BEHAVIORAL,
                            "a metered Anthropic key planted in metadata['env'] survives "
                            "build_env(): the backend could bill the API", detail)
    if leaks is None:
        return _unconfirmed(f"{how}, but the adapter has no build_env() to check for a "
                            "metered key", method=BEHAVIORAL, **detail)
    if declared == "none":
        return ProbeOutcome(PRESENT, BEHAVIORAL, f"measured route {measured}: {how}", detail)
    status = PRESENT if measured == declared else ABSENT
    return ProbeOutcome(status, BEHAVIORAL,
                        f"declared {declared}, measured {measured}: {how}", detail)


def _probe_sandboxed(adapter: Any) -> ProbeOutcome:
    """L3: does the adapter's own command line confine its writes?

    An adapter with no command line runs in-process -- there is no OS process
    to sandbox, a measured absence. An OS with no sandbox backend is
    unconfirmed, with the reason (Windows says so explicitly).
    """
    from tools.hooks import write_sandbox  # noqa: PLC0415

    name = getattr(adapter, "name", "")
    try:
        _build_argv(adapter, {})
    except AttributeError:
        return ProbeOutcome(
            ABSENT, INTERFACE,
            "no build_argv: the adapter runs in-process, so there is no OS "
            "process for the write sandbox to wrap",
        )
    except Exception as exc:  # noqa: BLE001 -- unconstructible is unconfirmed
        return _unconfirmed(f"build_argv raised {type(exc).__name__}: {exc}")
    report = write_sandbox.probe(
        name,
        build_argv=lambda wt, meta: _build_argv(adapter, meta, working_dir=wt),
    )
    if report["sandboxed"] is None:
        return _unconfirmed(report["reason"], method=BEHAVIORAL)
    return ProbeOutcome(PRESENT if report["sandboxed"] else ABSENT, BEHAVIORAL,
                        f"{report['status'].get('backend')}: {report['reason']}")


_PROBES = {
    "streaming": _probe_streaming,
    "tool_calling": _probe_tool_calling,
    "sub_agents": _probe_sub_agents,
    "interruption": _probe_interruption,
    "sandbox_passthrough": _probe_sandbox_passthrough,
    "context_budget": _probe_context_budget,
    "structured_output": _probe_structured_output,
    "guard_wired": _probe_guard_wired,
    "guard_shell": _probe_guard_shell,
    "claude_route": _probe_claude_route,
    "sandboxed": _probe_sandboxed,
}
#: Probes that need the declarations, not just the adapter.
_CONFIG_PROBES = {"claude_route"}


def _redact_exe(argv: Sequence[str]) -> List[str]:
    """Replace the stubbed executable so evidence never carries a host path."""
    return ["<executable>" if a == _SENTINEL_EXE else a for a in argv]


def _verdict(declared: Optional[bool], actual: str) -> str:
    if declared:
        return {
            PRESENT: CONFIRMED, ABSENT: OVERCLAIMED, UNCONFIRMED: UNVERIFIED,
        }[actual]
    return {
        PRESENT: UNDECLARED_PRESENT, ABSENT: AGREED_ABSENT,
        UNCONFIRMED: NOT_DECLARED,
    }[actual]


# ── matrix ──────────────────────────────────────────────────────────────────
def probe_adapter(
    adapter_name: str,
    config: Optional[Dict[str, Any]] = None,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Probe one adapter across the capability set (or the ``only`` subset)."""
    adapter = registry.get_adapter(adapter_name)
    decl = declared_for(adapter_name, config)
    declared_values = decl["values"]

    try:
        available = bool(adapter.available())
    except Exception as exc:  # noqa: BLE001 — an availability probe must not raise
        logger.debug("adapter %s availability check failed: %s", adapter_name, exc)
        available = False

    wanted = set(only) if only else set(_PROBES)
    capabilities: Dict[str, Any] = {}
    for cap, probe in _PROBES.items():
        if cap not in wanted:
            continue
        try:
            outcome = probe(adapter, config) if cap in _CONFIG_PROBES else probe(adapter)
        except Exception as exc:  # noqa: BLE001 — one broken probe is not a crash
            logger.warning("probe %s failed for %s: %s", cap, adapter_name, exc)
            outcome = _unconfirmed(
                f"the probe itself failed: {type(exc).__name__}: {exc}"
            )
        declared = declared_values.get(cap)
        cell = {"declared": declared if declared is not None else False}
        cell.update(outcome.as_dict())
        cell["declared_explicitly"] = cap in declared_values
        cell["verdict"] = _verdict(declared, outcome.status)
        capabilities[cap] = cell

    counts: Dict[str, int] = {}
    for cell in capabilities.values():
        counts[cell["actual"]] = counts.get(cell["actual"], 0) + 1
        counts[cell["verdict"]] = counts.get(cell["verdict"], 0) + 1

    return {
        "adapter": adapter_name,
        "available": available,
        "satisfies_protocol": isinstance(adapter, AgentAdapter),
        "declared_source": decl["source"],
        "capabilities": capabilities,
        "counts": counts,
    }


_MATRIX_CACHE: Optional[Dict[str, Any]] = None


def build_matrix(
    adapters: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    use_cache: bool = False,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Probe every registered adapter and return the full matrix.

    ``use_cache`` memoises the whole-registry matrix in-process. The probes are
    offline and cheap, so callers on a hot path (``pick_default``) can afford to
    cache; a caller reporting to a human should not, because an adapter's
    availability is a live fact.
    """
    global _MATRIX_CACHE
    whole = adapters is None and config is None and only is None
    if use_cache and whole and _MATRIX_CACHE is not None:
        return _MATRIX_CACHE

    names = list(adapters) if adapters is not None else registry.list_adapters()
    cfg = config if config is not None else load_declarations()
    probed = list(only) if only else list(CAPABILITIES)

    results: Dict[str, Any] = {}
    for name in names:
        try:
            results[name] = probe_adapter(name, cfg, only=probed)
        except KeyError as exc:
            results[name] = {"adapter": name, "error": str(exc)}
            continue
        coverage = _guard_coverage(name, results[name])
        if coverage is not None:
            results[name]["guard_coverage"] = coverage

    totals: Dict[str, int] = {}
    for entry in results.values():
        for key, value in (entry.get("counts") or {}).items():
            totals[key] = totals.get(key, 0) + value

    matrix = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": {cap: CAPABILITIES[cap] for cap in probed},
        "probe_methods": {
            BEHAVIORAL: "adapter code was executed and its return value inspected",
            INTERFACE: "the live adapter object was inspected",
            SOURCE_EVIDENCE: (
                "the module source documents a contract only a live run could "
                "exercise — may only produce 'unconfirmed'"
            ),
        },
        "adapters": results,
        # omx-adapt-06: which LLM provider families are exhausted right now,
        # until when, and what each fails over to -- a dispatch reads the same.
        "provider_exhaustion": _provider_exhaustion_state(),
        "summary": {
            "adapters": len(results),
            "capabilities": len(probed),
            "cells": sum(
                len(e.get("capabilities") or {}) for e in results.values()
            ),
            **totals,
        },
        "notes": [
            "Statuses are scoped to the AgentAdapter seam, not to the backend "
            "product: a capability the backend has but the adapter does not "
            "surface is unusable by everything routing through pick_default.",
            "'unconfirmed' means the probe could not determine the answer "
            "offline. It never satisfies a pick_default(require=...) filter.",
            "Complements tools/workflow/executor_parity.py (outcome parity on "
            "a replayed corpus); it does not replace it.",
        ],
    }
    if use_cache and adapters is None and config is None:
        _MATRIX_CACHE = matrix
    return matrix


def _guard_coverage(name: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """omx-guard-06: the guard layers live for this adapter, and the gate's verdict.

    Read from the cells just probed -- the autonomous-dispatch gate
    (tools/agents/guard_parity.py) measures the same three. claude_cli's L1 is
    its settings.json hook, which the adapter seam cannot see.
    """
    from tools.agents import guard_parity  # noqa: PLC0415 -- it imports this module

    cells = entry.get("capabilities") or {}
    caps = guard_parity.LAYER_CAPABILITY
    if not all(cap in cells for cap in caps.values()):
        return None
    layers = {layer: cells[cap]["actual"] for layer, cap in caps.items()}
    if name == "claude_cli":
        settings = guard_parity.claude_settings_verdict()
        layers[guard_parity.L1] = PRESENT if settings.guarded else ABSENT
    verdict = guard_parity.coverage_verdict(name, layers)
    return {"layers": layers, "live": verdict.live,
            "admitted": verdict.guarded, "reason": verdict.reason}


def _provider_exhaustion_state() -> Dict[str, Any]:
    try:
        from tools.llm import provider_exhaustion  # noqa: PLC0415

        return provider_exhaustion.snapshot()
    except Exception as exc:  # noqa: BLE001 -- unreadable state is reported, not hidden
        return {"error": f"{type(exc).__name__}: {exc}"}


def reset_cache() -> None:
    """Drop the memoised matrix — tests and long-lived daemons use this."""
    global _MATRIX_CACHE
    _MATRIX_CACHE = None


# ── consumption API (registry.pick_default and per-node routing) ────────────
def capability_status(adapter_name: str, capability: str) -> str:
    """The measured status of one capability for one adapter.

    Returns ``unconfirmed`` for an unknown capability name rather than raising:
    a router asking about something this matrix does not measure has, precisely,
    not been told the capability is present.
    """
    if capability not in CAPABILITIES:
        return UNCONFIRMED
    matrix = build_matrix(use_cache=True)
    entry = (matrix.get("adapters") or {}).get(adapter_name) or {}
    cell = (entry.get("capabilities") or {}).get(capability) or {}
    return cell.get("actual", UNCONFIRMED)


def supports(adapter_name: str, capabilities: Sequence[str]) -> bool:
    """True only when EVERY requested capability measured ``present``.

    ``unconfirmed`` deliberately fails. Routing work to an adapter on the
    strength of a claim nobody verified is the failure this module exists to
    end, so the filter is fail-closed.
    """
    return all(
        capability_status(adapter_name, cap) == PRESENT for cap in capabilities
    )


def adapters_with(*capabilities: str) -> List[str]:
    """Registered adapter names measured ``present`` for every capability."""
    return [
        name for name in registry.list_adapters()
        if supports(name, capabilities)
    ]


# ── CLI ─────────────────────────────────────────────────────────────────────
def _render_text(matrix: Dict[str, Any]) -> str:
    caps = list(matrix.get("capabilities") or CAPABILITIES)
    lines: List[str] = ["Agent adapter capability matrix (declared -> actual)", ""]
    width = max((len(c) for c in caps), default=10) + 2
    for name, entry in sorted(matrix.get("adapters", {}).items()):
        if entry.get("error"):
            lines.append(f"{name}: ERROR {entry['error']}")
            continue
        avail = "available" if entry.get("available") else "not installed"
        lines.append(f"{name}  [{avail}]  declared-by: {entry['declared_source']}")
        for cap in caps:
            cell = entry["capabilities"][cap]
            declared = "yes" if cell["declared"] else "no "
            lines.append(
                f"    {cap:<{width}} declared={declared}  actual="
                f"{cell['actual']:<12} {cell['verdict']:<18} "
                f"[{cell['method']}]"
            )
            lines.append(f"        {cell['evidence']}")
        coverage = entry.get("guard_coverage")
        if coverage:
            tag = "ADMITTED" if coverage["admitted"] else "REFUSED"
            lines.append(f"    guard coverage: {tag} for autonomous dispatch -- "
                         f"{coverage['reason']}")
        lines.append("")
    s = matrix["summary"]
    lines.append(
        f"{s['cells']} cells: {s.get(PRESENT, 0)} present, "
        f"{s.get(ABSENT, 0)} absent, {s.get(UNCONFIRMED, 0)} unconfirmed"
    )
    lines.append(
        f"verdicts: {s.get(CONFIRMED, 0)} confirmed, "
        f"{s.get(OVERCLAIMED, 0)} OVERCLAIMED, {s.get(UNVERIFIED, 0)} unverified, "
        f"{s.get(UNDECLARED_PRESENT, 0)} undeclared-but-present"
    )
    for family, st in sorted((matrix.get("provider_exhaustion") or {}).items()):
        if isinstance(st, dict):
            lines.append(
                f"provider {family}: "
                + (f"EXHAUSTED ({st.get('kind')}) until {st.get('resume_at')}"
                   if st.get("exhausted") else "available")
                + f" -> fails over to {st.get('failover_to')}"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe every agent adapter for declared-versus-actual capabilities."
        )
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--adapter", help="restrict the matrix to one adapter")
    parser.add_argument("--capability", help="restrict the matrix to one capability")
    parser.add_argument(
        "--gate", action="store_true",
        help="exit 1 when any capability is declared but measured absent",
    )
    args = parser.parse_args(argv)

    if args.capability and args.capability not in CAPABILITIES:
        print(
            f"unknown capability {args.capability!r}; known: "
            f"{', '.join(sorted(CAPABILITIES))}",
            file=sys.stderr,
        )
        return 2

    names: Optional[List[str]] = None
    if args.adapter:
        known = registry.list_adapters()
        if args.adapter not in known:
            print(
                f"unknown adapter {args.adapter!r}; registered: {', '.join(known)}",
                file=sys.stderr,
            )
            return 2
        names = [args.adapter]

    matrix = build_matrix(
        adapters=names,
        only=[args.capability] if args.capability else None,
    )

    overclaimed = [
        (name, cap)
        for name, entry in matrix["adapters"].items()
        for cap, cell in (entry.get("capabilities") or {}).items()
        if cell["verdict"] == OVERCLAIMED
    ]

    if args.json:
        matrix["overclaimed"] = [
            {"adapter": a, "capability": c} for a, c in overclaimed
        ]
        print(json.dumps(matrix, indent=2, default=str))
    else:
        print(_render_text(matrix))
        if overclaimed:
            print("\nOverclaimed (declared, measured absent):")
            for name, cap in overclaimed:
                print(f"  {name}.{cap}")

    if args.gate and overclaimed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
