# CUI // SP-CTI
"""NOVA SELA — Fitness Scorer (LLM Judge).

Multi-dimensional fitness scoring for evolved skill candidates.

Dimensions (Hermes-inspired):
  correctness        (0–1): Did the output address the task correctly?
  procedure_following (0–1): Did it follow the skill instructions?
  conciseness        (0–1): Appropriate length vs. outcome ratio?

Composite score: 0.5*correctness + 0.3*procedure + 0.2*conciseness

Two scoring modes:
  fast  — heuristic keyword-overlap proxy (used during candidate mutation)
  full  — LLM-as-judge with structured rubric (used for final holdout eval)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


@dataclass
class FitnessScore:
    correctness: float = 0.0
    procedure_following: float = 0.0
    conciseness: float = 0.0
    length_penalty: float = 0.0
    feedback: str = ""
    # Provenance: which categorical vocabulary produced this score. Empty for
    # heuristic (score_fast) results, which do not use the LLM enum path.
    # A change to this constant is a GATED baseline transition — see
    # docs/audits/agx-pick-02-baseline-transition.md.
    vocabulary_version: str = ""

    @property
    def composite(self) -> float:
        raw = (
            0.5 * self.correctness
            + 0.3 * self.procedure_following
            + 0.2 * self.conciseness
            - self.length_penalty
        )
        return round(max(0.0, min(1.0, raw)), 4)


# ────────────────────────────────────────────────────────────────────────────
# Fast heuristic scorer
# ────────────────────────────────────────────────────────────────────────────

_FAILURE_MARKERS = [
    "error:", "exception:", "traceback", "not found", "failed to",
    "importerror", "keyerror", "typeerror", "valueerror",
]

_SUCCESS_MARKERS = [
    "success", "completed", "created", "written", "passed", "done",
    "✓", "ok", "saved", "generated",
]


def score_fast(
    task_input: str,
    expected_behavior: str,
    actual_output: str,
    skill_text: str,
    max_length: int = 4000,
) -> FitnessScore:
    """
    Heuristic fitness score — fast, no LLM calls.
    Used during candidate mutation inner loop.
    """
    if not actual_output:
        return FitnessScore(feedback="empty output")

    out_lower = actual_output.lower()

    # Correctness: keyword overlap with expected_behavior
    exp_words = set(expected_behavior.lower().split())
    out_words = set(out_lower.split())
    overlap = len(exp_words & out_words) / max(len(exp_words), 1)
    failure_hits = sum(1 for m in _FAILURE_MARKERS if m in out_lower)
    success_hits = sum(1 for m in _SUCCESS_MARKERS if m in out_lower)
    correctness = min(1.0, overlap + (0.2 if success_hits > 0 else 0) - (0.3 * failure_hits))

    # Procedure following: skill keywords appear in output
    skill_words = set(skill_text.lower().split()[:200])
    proc_overlap = len(skill_words & out_words) / max(len(skill_words), 1)
    procedure_following = min(1.0, proc_overlap * 2.5)

    # Conciseness: penalize very long outputs
    length_ratio = len(actual_output) / max_length
    conciseness = max(0.0, 1.0 - max(0.0, length_ratio - 0.5))

    # Length penalty (Hermes: penalize > 90% of max)
    length_penalty = max(0.0, (length_ratio - 0.9) * 0.3) if length_ratio > 0.9 else 0.0

    return FitnessScore(
        correctness=round(max(0.0, min(1.0, correctness)), 3),
        procedure_following=round(max(0.0, min(1.0, procedure_following)), 3),
        conciseness=round(conciseness, 3),
        length_penalty=round(length_penalty, 3),
        feedback="heuristic",
    )


# ────────────────────────────────────────────────────────────────────────────
# Full LLM judge scorer
# ────────────────────────────────────────────────────────────────────────────


def score_full(
    task_input: str,
    expected_behavior: str,
    actual_output: str,
    skill_text: str,
) -> FitnessScore:
    """
    LLM-as-judge fitness score — expensive, used for holdout evaluation.
    Falls back to heuristic on LLM failure.
    """
    if not actual_output:
        return FitnessScore(feedback="empty output")

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        from tools.quality.categorical_scoring import compose_fitness

        # Deterministic-picker (agx-pick-02): the LLM commits only to a 3-value
        # enum per dimension; Python (compose_fitness) composes the float and the
        # composite. A 3-token vocabulary is small enough for a 7B local model to
        # hit reliably and yields the same token across model families — the
        # portability property the LLM-agnostic contract (agx-core-02) requires.
        prompt = (
            "You are a strict AI output evaluator. Judge the following agent output.\n\n"
            f"TASK INPUT:\n{task_input[:600]}\n\n"
            f"EXPECTED BEHAVIOR (rubric):\n{expected_behavior[:400]}\n\n"
            f"SKILL INSTRUCTIONS (excerpt):\n{skill_text[:600]}\n\n"
            f"ACTUAL OUTPUT:\n{actual_output[:800]}\n\n"
            "For each dimension choose EXACTLY ONE label:\n"
            "  correctness: did the output correctly address the task?\n"
            "    -> one of: correct | partially_correct | incorrect\n"
            "  procedure_following: did the agent follow the skill instructions?\n"
            "    -> one of: followed | partial | violated\n"
            "  conciseness: was the output appropriately brief (not padded, not truncated)?\n"
            "    -> one of: concise | acceptable | verbose\n\n"
            "Also give one sentence of feedback on the main shortcoming (or 'none').\n\n"
            "Respond as JSON with ONLY the labels: "
            "{\"correctness\": \"...\", \"procedure_following\": \"...\", "
            "\"conciseness\": \"...\", \"feedback\": \"...\"}"
        )
        router = LLMRouter()
        req = LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=200, temperature=0.2)
        resp = router.invoke("code_generation", req)

        if resp and resp.content:
            import json
            import re
            match = re.search(r"\{.*?\}", resp.content, re.DOTALL)
            if match:
                data = json.loads(match.group())
                length_ratio = len(actual_output) / 4000
                length_penalty = max(0.0, (length_ratio - 0.9) * 0.3) if length_ratio > 0.9 else 0.0
                # Python composes every number from the enums; unknown/malformed
                # tokens degrade to the neutral midpoint inside compose_fitness.
                composed = compose_fitness(
                    data.get("correctness", ""),
                    data.get("procedure_following", ""),
                    data.get("conciseness", ""),
                    length_penalty=length_penalty,
                )
                return FitnessScore(
                    correctness=composed["correctness"],
                    procedure_following=composed["procedure_following"],
                    conciseness=composed["conciseness"],
                    length_penalty=composed["length_penalty"],
                    feedback=data.get("feedback", ""),
                    vocabulary_version=composed["vocabulary_version"],
                )
    except Exception as exc:
        logger.warning("[fitness] LLM judge failed, falling back to heuristic: %s", exc)

    return score_fast(task_input, expected_behavior, actual_output, skill_text)


# ────────────────────────────────────────────────────────────────────────────
# Dataset-level scoring
# ────────────────────────────────────────────────────────────────────────────


def score_examples(
    examples: list[Any],  # list[EvalExample]
    candidate_skill_text: str,
    mode: str = "fast",
) -> tuple[float, list[FitnessScore]]:
    """
    Score a list of EvalExamples using the candidate skill text as the "agent response".

    In the evolution loop the candidate skill IS the output being evaluated
    (we're measuring how well the skill text would guide an agent to succeed
    on the eval tasks). This is a proxy evaluation — not a live agent call.

    Returns (mean_composite, [FitnessScore, ...]).
    """
    scores = []
    score_fn = score_fast if mode == "fast" else score_full

    for ex in examples:
        # Use candidate_skill_text as the "actual output" proxy
        # (tests how well the skill covers the expected behavior)
        s = score_fn(
            task_input=ex.task_input,
            expected_behavior=ex.expected_behavior,
            actual_output=candidate_skill_text,
            skill_text=candidate_skill_text,
        )
        scores.append(s)

    mean = round(sum(s.composite for s in scores) / len(scores), 4) if scores else 0.0
    return mean, scores
