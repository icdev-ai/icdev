# CUI // SP-CTI
"""Deterministic-picker composition for LLM-as-scorer surfaces (agx-pick-02).

The upstream rule (github.com/FareedKhan-dev/all-agentic-architectures, MIT):

    The LLM may only commit to CATEGORICAL features — booleans and enums.
    Python composes the final signal (the score, ranking, or gate verdict).

This module is that Python half: a small, versioned set of categorical
vocabularies plus PURE, side-effect-free composition functions. The LLM emits
one of 3 enum tokens per dimension; the arithmetic that turns those tokens into
a number lives here, is unit-tested against a full truth table, and is identical
across model families. That last property is the point — a free-form "rate
0.0-1.0" prompt yields incomparable numbers from a 70B and a 7B; a 3-value enum
yields the same token. Categorical outputs are the portability layer for the
LLM-agnostic contract (agx-core-02).

No LLM calls, no DB, no I/O here — every function is a deterministic map from
enums to a float in [0, 1]. Provenance: every consumer stamps
``VOCABULARY_VERSION`` onto the score it persists so a later change to these
vocabularies is a GATED transition (see docs/audits/agx-pick-02-baseline-
transition.md), never a silent distribution shift.
"""
from __future__ import annotations

from typing import Iterable, Mapping

# Bump this on ANY change to a vocabulary or a composition weight. Consumers
# persist it alongside each score; a mismatch between a stored baseline's
# version and the current version is the signal that a scoring change happened
# and comparisons across the boundary are invalid.
VOCABULARY_VERSION = "cat-1.0"

# The canonical 3-value ternary map every dimension collapses to. Keeping every
# vocabulary a relabeling of {1.0, 0.5, 0.0} is deliberate: it is the smallest
# vocabulary a 7B local model can hit reliably, and it makes the composition
# arithmetic trivially inspectable.
_HI, _MID, _LO = 1.0, 0.5, 0.0


def _clamp01(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 4)


# ---------------------------------------------------------------------------
# Surface #1 — NOVA SELA fitness (tools/evolution/fitness.py::score_full)
# ---------------------------------------------------------------------------
# Composite = 0.5*correctness + 0.3*procedure + 0.2*conciseness - length_penalty
# (weights unchanged from the free-form version; only the per-dimension EMISSION
# changes from a free float to a 3-value enum).
FITNESS_VOCAB: dict[str, dict[str, float]] = {
    "correctness": {"correct": _HI, "partially_correct": _MID, "incorrect": _LO},
    "procedure_following": {"followed": _HI, "partial": _MID, "violated": _LO},
    "conciseness": {"concise": _HI, "acceptable": _MID, "verbose": _LO},
}
_FITNESS_WEIGHTS = {"correctness": 0.5, "procedure_following": 0.3, "conciseness": 0.2}


def map_fitness_enum(dimension: str, value: str) -> float:
    """Map one fitness dimension enum to its float, or 0.5 on an unknown token.

    A small local model that returns an out-of-vocabulary token degrades to the
    neutral midpoint rather than crashing — the deterministic fallback the
    LLM-agnostic contract requires.
    """
    table = FITNESS_VOCAB.get(dimension, {})
    return table.get(str(value).strip().lower(), _MID)


def compose_fitness(
    correctness: str,
    procedure_following: str,
    conciseness: str,
    *,
    length_penalty: float = 0.0,
) -> dict:
    """Compose the fitness dimensions from enums into floats + composite.

    Returns a dict with the three per-dimension floats, the ``length_penalty``,
    the ``composite``, and ``vocabulary_version``. Pure function — no I/O.
    """
    c = map_fitness_enum("correctness", correctness)
    p = map_fitness_enum("procedure_following", procedure_following)
    n = map_fitness_enum("conciseness", conciseness)
    lp = max(0.0, float(length_penalty or 0.0))
    composite = _clamp01(
        _FITNESS_WEIGHTS["correctness"] * c
        + _FITNESS_WEIGHTS["procedure_following"] * p
        + _FITNESS_WEIGHTS["conciseness"] * n
        - lp
    )
    return {
        "correctness": c,
        "procedure_following": p,
        "conciseness": n,
        "length_penalty": round(lp, 4),
        "composite": composite,
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ---------------------------------------------------------------------------
# Surface #2 — ACE session grade (tools/ace/evaluator.py::grade_output_quality)
# ---------------------------------------------------------------------------
# The LLM emits one enum per dimension; Python composes ``overall``. A hard gate
# mirrors the upstream constitutional rule: an UNSUPPORTED faithfulness verdict
# means the output does not address the request, so ``overall`` is floored — a
# session cannot score well while being unfaithful, regardless of the others.
EVAL_VOCAB: dict[str, float] = {"supported": _HI, "partial": _MID, "unsupported": _LO}
EVAL_DIMENSIONS = (
    "faithfulness",
    "completeness",
    "reasoning_quality",
    "cod_quality",
    "error_adaptation",
)
_EVAL_WEIGHTS = {
    "faithfulness": 0.30,
    "completeness": 0.25,
    "reasoning_quality": 0.20,
    "cod_quality": 0.10,
    "error_adaptation": 0.15,
}
# An UNSUPPORTED faithfulness verdict caps overall here (the "constitutional"
# fail band) rather than letting strong secondary dimensions mask it.
_FAITHFULNESS_FAIL_CAP = 0.25


def map_eval_enum(value: str) -> float:
    """Map an ACE grade enum to its float; unknown token → 0.5 (neutral)."""
    return EVAL_VOCAB.get(str(value).strip().lower(), _MID)


def compose_eval_overall(dim_enums: Mapping[str, str]) -> dict:
    """Compose ACE ``overall`` from per-dimension enums.

    Args:
        dim_enums: mapping of dimension name -> enum in EVAL_VOCAB. Missing
            dimensions are treated as ``partial`` (neutral), never dropped.

    Returns a dict of the per-dimension floats, the composed ``overall``,
    ``faithfulness_failed`` (the hard-gate flag), and ``vocabulary_version``.
    """
    floats = {dim: map_eval_enum(dim_enums.get(dim, "partial")) for dim in EVAL_DIMENSIONS}
    weighted = sum(_EVAL_WEIGHTS[dim] * floats[dim] for dim in EVAL_DIMENSIONS)
    faithfulness_failed = str(dim_enums.get("faithfulness", "")).strip().lower() == "unsupported"
    overall = min(weighted, _FAITHFULNESS_FAIL_CAP) if faithfulness_failed else weighted
    result = {dim: floats[dim] for dim in EVAL_DIMENSIONS}
    result["overall"] = _clamp01(overall)
    result["faithfulness_failed"] = faithfulness_failed
    result["vocabulary_version"] = VOCABULARY_VERSION
    return result


# ---------------------------------------------------------------------------
# Surface #3 — content grounding (tools/quality/content_grounding.py, LLM path)
# ---------------------------------------------------------------------------
# Per-claim reflection: the LLM labels each sentence, Python composes the support
# ratio (mean of the claim floats). Conservative fallback: an unknown/malformed
# token is treated as UNGROUNDED (0.0), never silently grounded.
GROUNDING_VOCAB: dict[str, float] = {"grounded": _HI, "partial": _MID, "ungrounded": _LO}


def map_grounding_enum(value: str) -> float:
    """Map a per-claim grounding enum to its float; unknown token → 0.0.

    Fail-closed: when a small local model returns a token outside the
    vocabulary, the claim counts as UNGROUNDED so a malformed judge can never
    upgrade an unsupported claim into a supported one.
    """
    return GROUNDING_VOCAB.get(str(value).strip().lower(), _LO)


def compose_grounding(claim_enums: Iterable[str]) -> dict:
    """Compose a grounding support ratio from per-claim enums.

    Returns ``{score, claim_count, grounded, partial, ungrounded,
    vocabulary_version}`` where ``score`` is the mean claim float in [0, 1].
    An empty iterable returns score 0.0 (no supported claims), matching the
    module's "no evidence != fabricated" contract at the caller boundary.
    """
    vals: list[float] = []
    counts = {"grounded": 0, "partial": 0, "ungrounded": 0}
    for e in claim_enums:
        token = str(e).strip().lower()
        vals.append(map_grounding_enum(token))
        if token in counts:
            counts[token] += 1
        else:
            counts["ungrounded"] += 1
    score = _clamp01(sum(vals) / len(vals)) if vals else 0.0
    return {
        "score": score,
        "claim_count": len(vals),
        "grounded": counts["grounded"],
        "partial": counts["partial"],
        "ungrounded": counts["ungrounded"],
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ---------------------------------------------------------------------------
# Surface #4 — Divergence critic (tools/quality/divergence_critic.py, dvg-critic-01)
# ---------------------------------------------------------------------------
# The Focus half of divergent ideation. The generator (invoke_divergence) is a
# SEPARATE, opposing LLM call; this critic scores each candidate idea on three
# orthogonal dimensions. The model emits ONE 3-value enum per dimension; Python
# composes the composite AND the run ordering (the deterministic-picker rule:
# never ask a 70B and a 7B for a free-form 0-1 number and compare them, and
# never ask the model for a ranked list -- rank here, in code, off the enums).
#
# Weights: fit highest (an idea that does not fit the problem is worthless
# however novel), then viability, then novelty (novelty without viability is a
# TRAP -- scored separately in dvg-critic-02, not rewarded here). This addition
# is purely ADDITIVE -- it introduces a new independent vocabulary and changes
# no existing surface's composition, so stored baselines for surfaces #1-#3
# remain valid and VOCABULARY_VERSION is unchanged.
DIVERGENCE_VOCAB: dict[str, dict[str, float]] = {
    "novelty": {"breakthrough": _HI, "incremental": _MID, "derivative": _LO},
    "viability": {"viable": _HI, "risky": _MID, "unviable": _LO},
    "fit": {"on_target": _HI, "adjacent": _MID, "off_target": _LO},
}
_DIVERGENCE_WEIGHTS = {"fit": 0.40, "viability": 0.35, "novelty": 0.25}
DIVERGENCE_DIMENSIONS = ("novelty", "viability", "fit")


def map_divergence_enum(dimension: str, value: str) -> float:
    """Map one divergence-critic dimension enum to its float; unknown → 0.5.

    A local model returning an out-of-vocabulary token degrades to the neutral
    midpoint rather than crashing — the LLM-agnostic fallback contract.
    """
    table = DIVERGENCE_VOCAB.get(dimension, {})
    return table.get(str(value).strip().lower(), _MID)


def compose_divergence(novelty: str, viability: str, fit: str) -> dict:
    """Compose one idea's three dimension enums into floats + a composite.

    Pure function — no I/O. Returns the three per-dimension floats, the weighted
    ``composite`` in [0, 1], and ``vocabulary_version``. Callers order the pool
    by ``composite`` in Python; the model is never asked for a ranking.
    """
    n = map_divergence_enum("novelty", novelty)
    v = map_divergence_enum("viability", viability)
    f = map_divergence_enum("fit", fit)
    composite = _clamp01(
        _DIVERGENCE_WEIGHTS["novelty"] * n
        + _DIVERGENCE_WEIGHTS["viability"] * v
        + _DIVERGENCE_WEIGHTS["fit"] * f
    )
    return {
        "novelty": n,
        "viability": v,
        "fit": f,
        "composite": composite,
        "vocabulary_version": VOCABULARY_VERSION,
    }


# ---------------------------------------------------------------------------
# Surface #5 — Divergence trap detection (dvg-critic-02)
# ---------------------------------------------------------------------------
# The strongest result upstream reports for divergent ideation is surfacing
# SEDUCTIVE-BUT-BROKEN ideas — ones that look attractive but fail for a reason
# that is not obvious up front — before engineering effort is spent. The critic
# emits one trap enum per idea; Python composes the flag. The hard rule
# (composed here, not left to the model): an UNEXPLAINED trap flag is NOT
# actionable and cannot be reviewed, so a trap enum with no written rationale is
# demoted to non-actionable. This is purely additive — no existing surface's
# composition changes, so VOCABULARY_VERSION is unchanged.
TRAP_VOCAB: dict[str, float] = {"trap": _HI, "suspected_trap": _MID, "clear": _LO}
# A trap is "actionable" (worth surfacing to a gate) at or above this severity,
# AND only when accompanied by an explanation.
_TRAP_ACTIONABLE_THRESHOLD = _MID


def map_trap_enum(value: str) -> float:
    """Map a trap enum to a severity float; unknown token → 0.0 (clear).

    Fail-safe toward NOT crying wolf: an out-of-vocabulary token from a small
    model is treated as ``clear`` rather than fabricating a trap warning that
    would waste a reviewer's attention.
    """
    return TRAP_VOCAB.get(str(value).strip().lower(), _LO)


def compose_trap(trap: str, rationale: str) -> dict:
    """Compose one idea's trap enum + rationale into an advisory verdict.

    Returns ``{trap_flag, trap_level, has_rationale, is_trap, actionable,
    rationale, vocabulary_version}``. ``is_trap`` requires BOTH sufficient
    severity AND a non-empty rationale — an unexplained trap flag is demoted,
    because a flag a reviewer cannot evaluate is noise, not signal. Pure
    function, no I/O.
    """
    level = map_trap_enum(trap)
    has_rationale = bool(str(rationale or "").strip())
    severe = level >= _TRAP_ACTIONABLE_THRESHOLD
    is_trap = severe and has_rationale
    return {
        "trap_flag": str(trap or "clear").strip().lower() or "clear",
        "trap_level": level,
        "has_rationale": has_rationale,
        "is_trap": is_trap,
        # Actionable = surfaces to a gate as advisory input. Same condition as
        # is_trap today, kept as a distinct field so callers read intent clearly.
        "actionable": is_trap,
        "rationale": str(rationale or "").strip(),
        "vocabulary_version": VOCABULARY_VERSION,
    }
