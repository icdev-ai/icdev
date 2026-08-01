# CUI // SP-CTI
"""FORGE IGNITE intake engine — Socratic questionnaire + AI Opportunity Brief generator."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger("icdev.innovation.intake")

_CFG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "innovation_scoring.yaml"


def get_intake_config() -> dict:
    try:
        with open(_CFG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("intake", {})
    except Exception:
        return {}


def get_rounds() -> list:
    return get_intake_config().get("rounds", [])


# ---------------------------------------------------------------------------
# AI Opportunity Brief generator
# ---------------------------------------------------------------------------

_BRIEF_PROMPT = """You are FORGE IGNITE, an AI innovation advisor for a government/DoD software team.

Based on the following answers to an innovation intake questionnaire, generate a concise
AI Opportunity Brief in markdown format. Be direct, practical, and realistic.

IDEA TITLE: {title}
SUBMITTER ROLE: {role}

INTAKE ANSWERS:
{answers_text}

Generate the brief with these exact sections:
## Problem Statement
(1-2 sentences capturing the core problem)

## AI Opportunity Hypothesis
(1-2 sentences: if we apply [approach], then [outcome] because [reason])

## Recommended Approach
(One of: Prompt-Only / RAG / Agentic / Fine-Tuning / Hybrid / Non-AI Recommended)
Brief rationale (2-3 sentences).

## Minimum Viable Pilot
(What a 2-week proof-of-concept looks like. Be specific.)

## Suggested Learning Path
(2-3 FORGE Academy missions most relevant to building this)

## Risk Flags
(Bullet list of 1-3 key risks. If non-AI recommended, explain why AI may overcomplicate.)

Keep the entire brief under 400 words. Be honest — if a simpler solution works, say so."""

_BRIEF_FALLBACK = """## Problem Statement
{problem}

## AI Opportunity Hypothesis
If we apply {approach} to this workflow, we may reduce manual effort and improve
consistency — provided sufficient data and team readiness exist.

## Recommended Approach
**{approach}** — Based on your answers, this appears to be the most suitable
starting point. Verify data availability and team skills before committing.

## Minimum Viable Pilot
Build a minimal prototype in 2 weeks using existing tools. Define one measurable
success metric before starting. Stop if the metric isn't moving.

## Suggested Learning Path
- FORGE Academy Tier 1: LLM Fundamentals (M01)
- FORGE Academy Tier 1: RAG Basics (M03)
- Role-specific Tier 2 missions

## Risk Flags
- Data may not be ready or clean enough for AI use
- Team AI skills may require Academy upskilling first
- Validate that a simpler rule-based solution won't achieve the same outcome"""


def _infer_approach(answers: dict) -> str:
    ai_fit = answers.get("q7_simple_solution", "")
    data_avail = answers.get("q10_data_available", "")
    pattern = answers.get("q5_pattern", "")

    if "Yes — simpler" in ai_fit:
        return "Non-AI Recommended"
    if "clean, labeled data exists" in data_avail:
        if "Highly predictable" in pattern:
            return "Fine-Tuning"
        return "RAG"
    if "Partial" in data_avail:
        return "RAG"
    if "judgment" in answers.get("q8_judgment", "").lower():
        return "Agentic"
    return "Prompt-Only"


def _format_answers(answers: dict) -> str:
    q_labels = {
        "q1_bottleneck": "What's slow/manual/error-prone?",
        "q2_frequency": "How often does this happen?",
        "q3_impact": "Who is affected and what does it cost?",
        "q4_improvement": "What would 2x vs 10x improvement look like?",
        "q5_pattern": "Is this a repeating pattern?",
        "q6_data": "What data exists today?",
        "q7_simple_solution": "Could a non-AI solution solve 80%?",
        "q8_judgment": "What judgment makes this hard to automate?",
        "q9_failure_cost": "What does a wrong answer cost?",
        "q10_data_available": "Do we have training data?",
        "q11_mvp": "What's the minimum viable version?",
        "q12_integration": "What needs to change in the existing system?",
    }
    lines = []
    for key, label in q_labels.items():
        val = answers.get(key, "").strip()
        if val:
            lines.append(f"Q: {label}\nA: {val}\n")
    return "\n".join(lines)


def generate_brief(title: str, submitter_role: str, answers: dict) -> str:
    """Generate AI Opportunity Brief via LLM, fallback to template if unavailable."""
    approach = _infer_approach(answers)
    answers_text = _format_answers(answers)
    problem = answers.get("q1_bottleneck", "Not specified")

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        prompt = _BRIEF_PROMPT.format(
            title=title,
            role=submitter_role,
            answers_text=answers_text,
        )
        req = LLMRequest(
            function="idea_brief_generation",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        result = router.invoke("idea_brief_generation", req)
        brief_text = result.content if hasattr(result, "content") else str(result)
        if brief_text and len(brief_text) > 100:
            return brief_text
    except Exception as e:
        logger.debug("LLM brief generation failed (%s), using template", e)

    return _BRIEF_FALLBACK.format(problem=problem, approach=approach)
