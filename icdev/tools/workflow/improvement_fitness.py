# CUI // SP-CTI
"""Shared fitness scoring for agent improvement artifacts.

`agent_improvement_artifacts` has two score columns and they mean different
things:

  * ``baseline_score``  — fitness of the STATUS QUO, measured from execution
    traces (how well the skill performs today).
  * ``composite_score`` — fitness of the CANDIDATE improvement text, measured
    by the rubric below.

GEPA (`tools/skills/gepa_optimizer.py`) promotes a candidate only when
``composite_score - baseline_score >= 0.05``, so writing the same number into
both columns makes the optimizer structurally incapable of ever selecting
anything. Both writers — NOVA SELA (`tools/genesis/reflexes/evolution.py`) and
the Reflexion agent (`tools/workflow/reflexion_agent.py`) — must therefore
score the candidate independently of the baseline, which is what this module
is for.

Deterministic on purpose: the same candidate text must score the same on every
run, air-gapped or not, so a promotion decision is reproducible.
"""
from __future__ import annotations

# Default fitness weights (mirrors args/nova_sela_config.yaml `sela.fitness`).
DEFAULT_FITNESS_WEIGHTS: dict[str, float] = {
    "correctness": 0.5,
    "procedure": 0.3,
    "conciseness": 0.2,
}

# Outcome → baseline credit. `partial` is genuinely better than `failure`;
# collapsing both to 0.0 is what makes a trace-derived baseline lumpy.
OUTCOME_WEIGHTS: dict[str, float] = {
    "success": 1.0,
    "partial": 0.5,
    "timeout": 0.0,
    "token_exhaustion": 0.0,
    "failure": 0.0,
}

_GENERIC_TERMS = {"improve", "better", "enhance", "fix", "update", "change", "modify"}
_ACTION_WORDS = {"add", "remove", "replace", "validate", "retry", "cache",
                 "log", "check", "verify", "handle", "skip", "limit", "timeout"}


def score_improvement(
    text: str,
    skill: str,
    fitness_weights: dict[str, float] | None = None,
) -> dict[str, float | str]:
    """Score a candidate improvement/mutation text deterministically.

    Uses heuristics to estimate correctness, procedure, and conciseness, then
    applies the weighted fitness function.

    Returns ``{"mutation": str, "correctness": float, "procedure": float,
    "conciseness": float, "composite_score": float}``.
    """
    weights = fitness_weights or DEFAULT_FITNESS_WEIGHTS
    body = (text or "").strip()
    words = body.lower().split()
    word_count = len(body.split())

    # Conciseness: shorter is better, 50-word sweet-spot
    conciseness = max(0.0, min(1.0, 1.0 - max(0, word_count - 50) / 100))

    # Procedure: penalise vague/generic mutations
    specific_words = set(words) - _GENERIC_TERMS
    procedure = min(1.0, len(specific_words) / max(1, word_count) * 3)

    # Correctness: favour candidates that explicitly reference the skill name or
    # concrete action words
    action_hits = sum(1 for w in words if w in _ACTION_WORDS)
    skill_mention = 1.0 if skill and skill.lower() in body.lower() else 0.0
    correctness = min(1.0, (action_hits * 0.2 + skill_mention * 0.3 + 0.5))

    w_c = weights.get("correctness", 0.5)
    w_p = weights.get("procedure", 0.3)
    w_n = weights.get("conciseness", 0.2)
    composite = round(w_c * correctness + w_p * procedure + w_n * conciseness, 4)

    return {
        "mutation": body,
        "correctness": round(correctness, 4),
        "procedure": round(procedure, 4),
        "conciseness": round(conciseness, 4),
        "composite_score": composite,
    }


def score_traces(traces: list[dict]) -> float:
    """Baseline fitness of the status quo: outcome-weighted mean over traces.

    Returns 0.0 for an empty trace list.
    """
    if not traces:
        return 0.0
    total = sum(
        OUTCOME_WEIGHTS.get(str(t.get("outcome") or "").strip().lower(), 0.0)
        for t in traces
    )
    return round(total / len(traces), 3)


def dominant_skill(traces: list[dict]) -> str:
    """Most frequent non-empty ``skill_used`` across traces ('' if none).

    Ties break on the first-seen skill so the result is stable for a fixed
    trace ordering.
    """
    counts: dict[str, int] = {}
    for t in traces:
        skill = str(t.get("skill_used") or "").strip()
        if skill:
            counts[skill] = counts.get(skill, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda k: counts[k])
