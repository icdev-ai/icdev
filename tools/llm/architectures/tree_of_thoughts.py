# CUI // SP-CTI
"""Tree of Thoughts (ToT) architecture — budget-capped beam search (agx-search-02).

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan) and Yao et al. "Tree of Thoughts" (2023).
Pattern only; no upstream code vendored.

ToT explores multiple reasoning paths and keeps the most promising via beam
search. Its cost is (beam_width x branching_factor x depth) LLM calls, so it is
justified ONLY where the solution space is wide and a wrong early commitment is
expensive — COA generation, migration wave planning, options-strategy selection.
It is NEVER a default: it is an opt-in registry architecture, and upstream's LATS
(MCTS with reward propagation) was rejected precisely because its per-node cost
is not defensible against beam-capped ToT.

Two hard-safety properties, both tested:
  1. BUDGET IS A CEILING, NOT A SUGGESTION. Exceeding the call/token/cost/time
     budget returns the best-so-far with ``degraded=True`` and
     ``stop_reason="budget_exceeded"`` — never a silently-truncated result
     presented as complete.
  2. EVALUATION IS DETERMINISTIC-PICKER. The LLM emits a 3-value enum per
     candidate branch ({promising, maybe, dead_end}); Python composes the beam
     ordering. Unknown tokens fail closed to ``dead_end``.

LLM-agnostic: all inference via ``LLMRouter``; zero vendor-SDK imports, no
hardcoded model IDs. The result envelope carries an honest per-invocation cost
report (llm_calls, tokens, cost).
"""
from __future__ import annotations

import copy
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from tools.llm.architectures.envelope import (
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import register
from tools.llm.provider import LLMRequest

# Branch-evaluation vocabulary (deterministic-picker). Small enough for a 7B
# local model to hit reliably; unknown tokens fail closed to dead_end.
EVAL_VOCAB = {"promising": 1.0, "maybe": 0.5, "dead_end": 0.0}
VOCABULARY_VERSION = "tot-1.0"


def classify_branch(token: Any) -> str:
    t = str(token or "").strip().lower()
    return t if t in EVAL_VOCAB else "dead_end"


def branch_score(token: str) -> float:
    return EVAL_VOCAB[classify_branch(token)]


def _coerce_request(task: Any) -> LLMRequest:
    if isinstance(task, LLMRequest):
        return copy.deepcopy(task)
    if isinstance(task, str):
        return LLMRequest(messages=[{"role": "user", "content": task}])
    raise TypeError(f"task must be str or LLMRequest, got {type(task).__name__}")


def _user_content(request: LLMRequest) -> str:
    for msg in request.messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _content(resp) -> str:
    return (getattr(resp, "content", "") or "").strip()


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else raw)
    except Exception:
        return None


class _BudgetTracker:
    """Enforces the hard ceiling across calls/tokens/cost/time."""

    def __init__(self, budget: Optional[ArchitectureBudget], max_llm_calls: int, start: float):
        self.budget = budget
        self.max_llm_calls = max_llm_calls
        self.start = start
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def record(self, resp) -> None:
        self.llm_calls += 1
        self.input_tokens += int(getattr(resp, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(resp, "output_tokens", 0) or 0)
        self.cost_usd += float(getattr(resp, "cost_usd", 0.0) or 0.0)

    def exceeded(self) -> bool:
        if self.llm_calls >= self.max_llm_calls:
            return True
        b = self.budget
        if b is None:
            return False
        if b.max_tokens is not None and (self.input_tokens + self.output_tokens) >= b.max_tokens:
            return True
        if b.max_cost_usd is not None and self.cost_usd >= b.max_cost_usd:
            return True
        if b.max_seconds is not None and (time.time() - self.start) >= b.max_seconds:
            return True
        return False

    def report(self) -> Dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "duration_ms": int((time.time() - self.start) * 1000),
        }


def _expand(router, function, task_text, path, branching_factor, tracker) -> List[str]:
    """Generate up to ``branching_factor`` candidate next thoughts for a path."""
    prefix = "\n".join(f"- {t}" for t in path) if path else "(none yet)"
    prompt = (
        "You are exploring solution paths for the TASK by proposing the NEXT "
        "reasoning step. Given the steps so far, propose "
        f"{branching_factor} DISTINCT candidate next steps. Return STRICT JSON "
        'only:\n{"candidates": ["<next step>", ...]}\n\n'
        f"TASK:\n{task_text}\n\nSTEPS SO FAR:\n{prefix}"
    )
    resp = router.invoke(function, LLMRequest(
        messages=[{"role": "user", "content": prompt}], max_tokens=400, temperature=0.7,
    ))
    tracker.record(resp)
    data = _extract_json(_content(resp)) or {}
    cands = [str(c).strip() for c in (data.get("candidates") or []) if str(c).strip()]
    return cands[:branching_factor]


def _evaluate(router, eval_function, task_text, path, candidate, tracker) -> str:
    """Return an enum verdict for a candidate branch (deterministic-picker)."""
    prefix = "\n".join(f"- {t}" for t in path) if path else "(none yet)"
    prompt = (
        "Judge whether the CANDIDATE next step moves toward solving the TASK. "
        "Return STRICT JSON only:\n"
        '{"verdict": "promising"|"maybe"|"dead_end"}\n\n'
        f"TASK:\n{task_text}\n\nSTEPS SO FAR:\n{prefix}\n\nCANDIDATE:\n{candidate}"
    )
    resp = router.invoke(eval_function, LLMRequest(
        messages=[{"role": "user", "content": prompt}], max_tokens=60, temperature=0.0,
    ))
    tracker.record(resp)
    data = _extract_json(_content(resp)) or {}
    return classify_branch(data.get("verdict"))


def _synthesize(router, function, task_text, path, tracker) -> str:
    """Produce the final answer from the best reasoning path."""
    steps_text = "\n".join(f"- {t}" for t in path) if path else "(no steps)"
    prompt = (
        "Using the reasoning path below, give the final answer to the TASK.\n\n"
        f"TASK:\n{task_text}\n\nREASONING PATH:\n{steps_text}"
    )
    resp = router.invoke(function, LLMRequest(
        messages=[{"role": "user", "content": prompt}], max_tokens=1200, temperature=0.2,
    ))
    tracker.record(resp)
    return _content(resp)


def tree_of_thoughts(
    task,
    *,
    router=None,
    budget: Optional[ArchitectureBudget] = None,
    function: str = "architecture_run",
    eval_function: Optional[str] = None,
    beam_width: int = 2,
    branching_factor: int = 3,
    max_depth: int = 2,
    max_llm_calls: int = 24,
    **kwargs,
) -> ArchitectureResult:
    """Budget-capped beam search over reasoning paths (opt-in; never a default).

    Args:
        beam_width: paths kept between depths.
        branching_factor: candidate next-steps generated per path per depth.
        max_depth: reasoning depth (beam iterations).
        max_llm_calls: HARD ceiling on total LLM calls regardless of budget —
            the always-present guard so ToT cannot run away even if ``budget``
            is None.
        budget: optional token/cost/time ceilings, enforced alongside the call cap.
    """
    from tools.llm.router import LLMRouter

    router = router or LLMRouter()
    eval_function = eval_function or function
    request = _coerce_request(task)
    task_text = _user_content(request)
    tracker = _BudgetTracker(budget, max_llm_calls, time.time())
    steps: List[ArchitectureStep] = []

    # Beam of (path, cumulative_score). Start from the empty path.
    beam: List[Tuple[List[str], float]] = [([], 0.0)]
    best_path: List[str] = []
    budget_hit = False

    for depth in range(max_depth):
        scored: List[Tuple[List[str], float]] = []
        for path, cum in beam:
            if tracker.exceeded():
                budget_hit = True
                break
            try:
                candidates = _expand(router, function, task_text, path, branching_factor, tracker)
            except Exception as exc:
                if isinstance(exc, (TypeError, ValueError, AttributeError)):
                    raise
                budget_hit = budget_hit or False
                continue
            for cand in candidates:
                if tracker.exceeded():
                    budget_hit = True
                    break
                try:
                    verdict = _evaluate(router, eval_function, task_text, path, cand, tracker)
                except Exception as exc:
                    if isinstance(exc, (TypeError, ValueError, AttributeError)):
                        raise
                    verdict = "dead_end"
                new_path = path + [cand]
                scored.append((new_path, cum + branch_score(verdict)))
        if scored:
            # Python composes the ordering; keep the top beam_width paths.
            scored.sort(key=lambda ps: ps[1], reverse=True)
            beam = scored[:beam_width]
            best_path = beam[0][0]
        steps.append(ArchitectureStep(
            name=f"depth_{depth}",
            detail={"expanded": len(scored), "beam_kept": len(beam), "llm_calls": tracker.llm_calls},
        ))
        if budget_hit or tracker.exceeded():
            budget_hit = True
            break

    # Synthesize the final answer from the best path — unless the budget is
    # already spent, in which case we honestly return best-so-far as degraded.
    output = ""
    synth_ok = False
    if best_path and not tracker.exceeded():
        try:
            output = _synthesize(router, function, task_text, best_path, tracker)
            synth_ok = bool(output)
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, AttributeError)):
                raise

    cost = tracker.report()
    degraded = budget_hit or not synth_ok
    if budget_hit:
        stop_reason = "budget_exceeded"
    elif synth_ok:
        stop_reason = "completed"
    else:
        stop_reason = "no_path" if not best_path else "synthesis_unavailable"

    return ArchitectureResult(
        architecture="tree_of_thoughts",
        output=output or (" > ".join(best_path) if best_path else ""),
        steps=steps,
        input_tokens=cost["input_tokens"],
        output_tokens=cost["output_tokens"],
        cost_usd=cost["cost_usd"],
        duration_ms=cost["duration_ms"],
        method="tree_of_thoughts",
        degraded=degraded,
        stop_reason=stop_reason,
        metadata={
            "best_path": best_path,
            "beam_width": beam_width,
            "branching_factor": branching_factor,
            "max_depth": max_depth,
            "cost_report": cost,
            "vocabulary_version": VOCABULARY_VERSION,
            "budget_exceeded": budget_hit,
        },
    )


register("tree_of_thoughts", tree_of_thoughts, overwrite=True)
