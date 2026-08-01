"""Adapters registering ICDEV's EXISTING reasoning implementations as AGX
architectures behind the uniform envelope.

Nothing here reimplements CoT / CoD / council / ReAct — each adapter *wraps* the
production implementation (``ChainOrchestrator`` and
``icdev.tools.llm.agent_loop.run_agent_loop``) and maps its native result into
:class:`ArchitectureResult`. See the coverage table in
``docs/spikes/agx-00-agentic-architectures-adaptation.md`` for the 22
architectures ICDEV already runs.

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). Patterns only; no upstream code vendored.

LLM-agnostic: all inference flows through ``LLMRouter`` inside the wrapped
implementations. This module imports no vendor SDKs and hardcodes no model IDs.
"""
from __future__ import annotations

import copy
from typing import Any, List, Optional

from tools.llm.architectures.envelope import (
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import register
from tools.llm.provider import LLMRequest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _coerce_request(task: Any) -> LLMRequest:
    """Turn ``task`` into an LLMRequest.

    ``task`` may already be an LLMRequest (used as-is, deep-copied so the caller's
    object is never mutated) or a plain string (wrapped as a single user message).
    """
    if isinstance(task, LLMRequest):
        return copy.deepcopy(task)
    if isinstance(task, str):
        return LLMRequest(messages=[{"role": "user", "content": task}])
    raise TypeError(
        f"task must be str or LLMRequest, got {type(task).__name__}"
    )


def _steps_from_rounds(rounds: List[dict]) -> List[ArchitectureStep]:
    """Map ChainResult.rounds (list of loosely-shaped dicts) to steps.

    Defensive: the round dict shape varies across CoT / CoD / council, so we read
    common keys and never assume presence.
    """
    steps: List[ArchitectureStep] = []
    for i, rnd in enumerate(rounds or []):
        if not isinstance(rnd, dict):
            steps.append(ArchitectureStep(name=f"round_{i}", detail={"raw": str(rnd)}))
            continue
        name = str(rnd.get("step_name") or rnd.get("name") or rnd.get("role") or f"round_{i}")
        model = rnd.get("model_id") or rnd.get("model")
        model_ids = [model] if model else list(rnd.get("models_used", []) or [])
        steps.append(
            ArchitectureStep(
                name=name,
                model_ids=[m for m in model_ids if m],
                input_tokens=int(rnd.get("input_tokens", 0) or 0),
                output_tokens=int(rnd.get("output_tokens", 0) or 0),
                cost_usd=float(rnd.get("cost_usd", 0.0) or 0.0),
                duration_ms=int(rnd.get("duration_ms", 0) or 0),
                detail={
                    k: v
                    for k, v in rnd.items()
                    if k
                    not in {
                        "step_name",
                        "name",
                        "role",
                        "model_id",
                        "model",
                        "models_used",
                        "input_tokens",
                        "output_tokens",
                        "cost_usd",
                        "duration_ms",
                    }
                },
            )
        )
    return steps


def _from_chain_result(architecture: str, mode: str, chain_result: Any) -> ArchitectureResult:
    """Convert a ChainResult into the uniform envelope without loss."""
    cr = chain_result
    steps = _steps_from_rounds(getattr(cr, "rounds", []) or [])
    models = list(getattr(cr, "models_used", []) or [])
    stop_reason = getattr(cr, "stop_reason", "") or ""
    content = getattr(cr, "content", "") or ""
    # An empty-content degrade (e.g. council "all_advisors_failed") is honestly
    # marked degraded rather than presented as a real verdict.
    degraded = (not content) or stop_reason in {
        "all_advisors_failed",
        "budget_exceeded",
        "error",
    }
    return ArchitectureResult(
        architecture=architecture,
        output=content,
        steps=steps,
        model_ids_used=models,
        input_tokens=int(getattr(cr, "total_input_tokens", 0) or 0),
        output_tokens=int(getattr(cr, "total_output_tokens", 0) or 0),
        cost_usd=float(getattr(cr, "total_cost_usd", 0.0) or 0.0),
        duration_ms=int(getattr(cr, "total_duration_ms", 0) or 0),
        method=f"wrapped:{mode}",
        degraded=degraded,
        stop_reason=stop_reason or ("empty_output" if not content else "completed"),
        trace_id=getattr(cr, "trace_id", "") or "",
        metadata={"chain_mode": getattr(cr, "chain_mode", mode)},
    )


def _apply_budget_to_orchestrator(orch: Any, budget: Optional[ArchitectureBudget]) -> None:
    """Inject caller budget into the orchestrator config so the existing
    BudgetExceededError path enforces it. No-op when budget is None."""
    if budget is None:
        return
    # _get_function_config reads these top-level keys as defaults.
    if budget.max_cost_usd is not None:
        orch._config["cost_cap_usd"] = budget.max_cost_usd
    if budget.max_tokens is not None:
        orch._config["token_cap"] = budget.max_tokens
    if budget.max_seconds is not None:
        orch._config["timeout_seconds"] = budget.max_seconds


def _degraded_envelope(architecture: str, mode: str, exc: Exception, reason: str) -> ArchitectureResult:
    """Honest degraded envelope for a failed run — no fabricated output."""
    return ArchitectureResult(
        architecture=architecture,
        output="",
        method=f"wrapped:{mode}",
        degraded=True,
        stop_reason=reason,
        metadata={"error": f"{type(exc).__name__}: {exc}"},
    )


# ---------------------------------------------------------------------------
# ChainOrchestrator-backed architectures (CoT / CoD / council)
# ---------------------------------------------------------------------------
def _run_chain(mode: str, method_name: str, name: str, task, *, router, budget, function, **kwargs):
    # Local imports keep the package import-light and avoid importing the whole
    # orchestrator (and its DB deps) unless an architecture is actually run.
    from tools.llm.chain_orchestrator import BudgetExceededError, ChainOrchestrator

    request = _coerce_request(task)
    orch = ChainOrchestrator(router=router)
    _apply_budget_to_orchestrator(orch, budget)
    invoke = getattr(orch, method_name)
    try:
        chain_result = invoke(function, request)
    except BudgetExceededError as exc:
        return _degraded_envelope(name, mode, exc, "budget_exceeded")
    except Exception as exc:  # LLMUnavailable / RuntimeError / Timeout — degrade, never crash
        # Preserve genuine programming errors; only degrade on the known
        # runtime failure classes (air-gap parity requires completion).
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded_envelope(name, mode, exc, "unavailable")
    return _from_chain_result(name, mode, chain_result)


def chain_of_thought(task, *, router=None, budget=None, function="architecture_run", **kwargs):
    """Wrap ChainOrchestrator.invoke_chain_of_thought (reason -> critic -> synthesize)."""
    return _run_chain("cot", "invoke_chain_of_thought", "chain_of_thought",
                      task, router=router, budget=budget, function=function, **kwargs)


def chain_of_debate(task, *, router=None, budget=None, function="architecture_run", **kwargs):
    """Wrap ChainOrchestrator.invoke_chain_of_debate (parallel debate -> judge)."""
    return _run_chain("cod", "invoke_chain_of_debate", "chain_of_debate",
                      task, router=router, budget=budget, function=function, **kwargs)


def council(task, *, router=None, budget=None, function="architecture_run", **kwargs):
    """Wrap ChainOrchestrator.invoke_council (fixed-perspective advisors + chairman)."""
    return _run_chain("council", "invoke_council", "council",
                      task, router=router, budget=budget, function=function, **kwargs)


# ---------------------------------------------------------------------------
# agent_loop-backed architecture (ReAct)
# ---------------------------------------------------------------------------
def react(
    task,
    *,
    router=None,
    budget=None,
    function="code_generation",
    tools=None,
    tool_handlers=None,
    system_prompt="",
    max_iterations=12,
    **kwargs,
):
    """Wrap icdev.tools.llm.agent_loop.run_agent_loop (native tool-use ReAct loop).

    With no tools supplied this degenerates to a single-turn reasoning call; pass
    ``tools`` + ``tool_handlers`` for a full ReAct trajectory. Budget ceilings map
    onto run_agent_loop's ``max_total_tokens`` / ``max_cost_usd``.
    """
    from tools.llm.agent_loop import run_agent_loop
    from tools.llm.router import LLMRouter

    request = _coerce_request(task)
    user_prompt = ""
    if request.messages and isinstance(request.messages[0], dict):
        user_prompt = str(request.messages[0].get("content", ""))
    sys_prompt = system_prompt or request.system_prompt or ""

    router = router or LLMRouter()
    max_tokens_budget = budget.max_tokens if budget else None
    max_cost_budget = budget.max_cost_usd if budget else None

    try:
        result = run_agent_loop(
            router,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            tools=tools or [],
            tool_handlers=tool_handlers or {},
            llm_function=function,
            max_iterations=max_iterations,
            max_total_tokens=max_tokens_budget,
            max_cost_usd=max_cost_budget,
        )
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded_envelope("react", "react", exc, "unavailable")

    steps = [
        ArchitectureStep(
            name=str(call.get("name", f"tool_call_{i}")),
            detail={k: v for k, v in call.items() if k != "name"},
        )
        for i, call in enumerate(getattr(result, "tool_call_log", []) or [])
    ]
    content = getattr(result, "final_content", "") or ""
    truncated = bool(getattr(result, "truncated", False))
    model_id = getattr(result, "model_id", "") or ""
    return ArchitectureResult(
        architecture="react",
        output=content,
        steps=steps,
        model_ids_used=[model_id] if model_id else [],
        input_tokens=int(getattr(result, "total_input_tokens", 0) or 0),
        output_tokens=int(getattr(result, "total_output_tokens", 0) or 0),
        cost_usd=float(getattr(result, "total_cost_usd", 0.0) or 0.0),
        duration_ms=0,
        method="wrapped:react",
        degraded=truncated or (not content),
        stop_reason=getattr(result, "truncation_reason", "") or getattr(result, "stop_reason", "") or "completed",
        trace_id=getattr(result, "trace_id", "") or getattr(result, "session_id", "") or "",
        metadata={"turns": getattr(result, "turns", 0), "truncated": truncated},
    )


# ---------------------------------------------------------------------------
# Self-registration on import
# ---------------------------------------------------------------------------
for _name, _fn in (
    ("chain_of_thought", chain_of_thought),
    ("chain_of_debate", chain_of_debate),
    ("council", council),
    ("react", react),
):
    register(_name, _fn, overwrite=True)
