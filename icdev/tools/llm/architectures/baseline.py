"""The ``baseline`` architecture — a single direct model call, no reasoning strategy.

Registered so the benchmark (agx-bench-01) and leaderboard (agx-bench-02) have an
honest reference point: "current behavior" for a plain function call is one
``LLMRouter.invoke`` with no reasoning wrapper. Every other architecture is graded
*against* this, which is what lets the leaderboard answer the most important — and
most easily buried — question: **did any architecture actually beat doing nothing
special?**

``baseline`` is a MEASUREMENT reference only. It is never a routing default and
never changes any call site; selecting no architecture (config ``null``) already
yields this behavior in production. Adapted-pattern provenance:
github.com/FareedKhan-dev/all-agentic-architectures (MIT, (c) 2025 Fareed Khan) —
pattern only, no upstream code vendored.

LLM-agnostic: routes through ``LLMRouter``; no vendor SDK import, no hardcoded model.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

from tools.llm.architectures.envelope import (
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import register
from tools.llm.provider import LLMRequest


def _coerce_request(task: Any) -> LLMRequest:
    if isinstance(task, LLMRequest):
        return copy.deepcopy(task)
    if isinstance(task, str):
        return LLMRequest(messages=[{"role": "user", "content": task}])
    raise TypeError(f"task must be str or LLMRequest, got {type(task).__name__}")


def baseline(
    task: Any,
    *,
    router: Any = None,
    budget: Optional[ArchitectureBudget] = None,
    function: str = "architecture_run",
    **kwargs: Any,
) -> ArchitectureResult:
    """Run ``task`` as a single direct ``router.invoke`` and wrap the response.

    Honesty invariant: on any provider failure or empty response this returns a
    ``degraded=True`` envelope with an honest ``stop_reason`` — never a fabricated
    output. The benchmark treats a degraded/empty baseline as ``unmeasured``.
    """
    req = _coerce_request(task)
    if router is None:
        from tools.llm.router import LLMRouter

        router = LLMRouter()

    try:
        resp = router.invoke(function, req)
    except Exception as exc:  # provider unreachable / air-gap
        return ArchitectureResult(
            architecture="baseline",
            output="",
            method="baseline:direct",
            degraded=True,
            stop_reason=f"provider_error:{type(exc).__name__}",
            metadata={"error": str(exc)[:300]},
        )

    content = getattr(resp, "content", "") or ""
    model_id = getattr(resp, "model_id", "") or ""
    step = ArchitectureStep(
        name="direct_invoke",
        model_ids=[model_id] if model_id else [],
        input_tokens=int(getattr(resp, "input_tokens", 0) or 0),
        output_tokens=int(getattr(resp, "output_tokens", 0) or 0),
        cost_usd=float(getattr(resp, "cost_usd", 0.0) or 0.0),
        duration_ms=int(getattr(resp, "duration_ms", 0) or 0),
    )
    return ArchitectureResult(
        architecture="baseline",
        output=content,
        steps=[step],
        model_ids_used=[model_id] if model_id else [],
        input_tokens=step.input_tokens,
        output_tokens=step.output_tokens,
        cost_usd=step.cost_usd,
        duration_ms=step.duration_ms,
        method="baseline:direct",
        degraded=not content,
        stop_reason=getattr(resp, "stop_reason", "") or ("empty" if not content else "completed"),
    )


register("baseline", baseline, overwrite=True)
