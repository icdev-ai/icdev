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
}

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

_FIXTURES: Dict[str, str] = {
    "claude_json_envelope": _FIXTURE_CLAUDE_ENVELOPE,
    "codex_jsonl": _FIXTURE_CODEX_JSONL,
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
            "values": {k: bool(v) for k, v in own.items() if k in CAPABILITIES},
        }

    declared = ((cfg.get("declared") or {}).get(adapter_name) or {})
    values = {k: bool(v) for k, v in declared.items() if k in CAPABILITIES}
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


# ── the seven probes ────────────────────────────────────────────────────────
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


_PROBES = {
    "streaming": _probe_streaming,
    "tool_calling": _probe_tool_calling,
    "sub_agents": _probe_sub_agents,
    "interruption": _probe_interruption,
    "sandbox_passthrough": _probe_sandbox_passthrough,
    "context_budget": _probe_context_budget,
    "structured_output": _probe_structured_output,
}


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
            outcome = probe(adapter)
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
