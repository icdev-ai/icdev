# CUI // SP-CTI
"""FORGE IGNITE feasibility engine — LLM-driven feasibility study generator."""

from __future__ import annotations

import logging

logger = logging.getLogger("icdev.innovation.feasibility")

_STUDY_PROMPT = """You are a senior AI systems engineer conducting a feasibility study for a government software team.

IDEA: {title}
SUBMITTER ROLE: {role}
DEPARTMENT: {department}

PROBLEM STATEMENT:
{problem_statement}

SCORING CONTEXT:
- Business Value Score: {bv}/10
- Strategic Alignment Score: {sa}/10
- Technical Feasibility Score: {tf}/10 (derived from team Academy completion rate)
- Data Readiness Score: {dr}/10 (derived from existing datasets and RAG quality)
- Time to Value Score: {ttv}/10
- Risk Score: {risk}/10

PHASE TAG: {phase_label}

Generate a structured feasibility study with these sections:

## 1. Problem Clarity Assessment
Rate and explain: Is the problem well-defined? Is AI the right tool?

## 2. AI Fit Analysis
Justify the recommended approach ({approach}). What evidence supports this?
What are the 2 biggest risks of the chosen approach?

## 3. Data Assessment
What data does the team have? What's missing? What would it take to fill the gap?

## 4. Team Readiness Assessment
Based on an Academy readiness score of {tf}/10, what skills gaps exist?
What missions should the team complete before starting the pilot?

## 5. Risk Register
| Risk | Likelihood | Impact | Mitigation |
(3-5 rows in markdown table)

## 6. Recommended Approach
Restate the approach with specific implementation guidance.
What does the minimal viable pilot look like in concrete steps?

## 7. Next Steps
Numbered list of 5 concrete actions to move this idea to pilot stage.

Keep the study under 600 words total. Use plain language — the study will be
read by both technical leads and program managers."""

_STUDY_FALLBACK = """## 1. Problem Clarity Assessment
The problem statement indicates a workflow that may benefit from AI assistance.
Further clarification is recommended before committing to a pilot.

## 2. AI Fit Analysis
Recommended approach: **{approach}**. This is based on the intake answers provided.
Key risks: (1) Data may not be ready or labeled; (2) team skills may need development.

## 3. Data Assessment
Review existing data sources before starting. If labeled examples don't exist,
plan a 1-week data collection sprint before the pilot.

## 4. Team Readiness Assessment
Current Academy readiness: {tf}/10. Recommend completing relevant Tier 2 missions
before beginning pilot work.

## 5. Risk Register
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Data not ready | Medium | High | Audit data sources first |
| Skills gap | Medium | Medium | Academy upskilling sprint |
| Scope creep | Low | High | Time-box pilot to 2 weeks |

## 6. Recommended Approach
Build a minimal viable prototype first. Define one measurable success metric.
Stop after 2 weeks and evaluate before continuing.

## 7. Next Steps
1. Complete data audit — identify available labeled examples
2. Complete Academy prerequisites (check Learning Path in scorecard)
3. Define success metric and baseline measurement
4. Create Kanban pilot epic via "Move to Pilot" button
5. Run 2-week time-boxed prototype and measure against baseline"""


def generate_feasibility_study(idea: dict, scores: dict) -> str:
    """Generate a full feasibility study for an idea given its scores."""
    title = idea.get("title", "Unnamed Idea")
    role = idea.get("submitter_role", "unknown")
    department = idea.get("department", "Unknown")
    problem = idea.get("problem_statement", "Not specified")

    dims = scores.get("dimensions", {})
    approach_map = {
        "Yes — simpler": "Non-AI Recommended",
    }
    answers = {}
    try:
        import json
        raw = idea.get("intake_answers_json", "{}")
        if isinstance(raw, str):
            answers = json.loads(raw)
        elif isinstance(raw, dict):
            answers = raw
    except Exception:
        pass

    from .intake_engine import _infer_approach
    approach = _infer_approach(answers)

    bv = dims.get("business_value", {}).get("score", 5)
    sa = dims.get("strategic_alignment", {}).get("score", 5)
    tf = dims.get("technical_feasibility", {}).get("score", 5)
    dr = dims.get("data_readiness", {}).get("score", 5)
    ttv = dims.get("time_to_value", {}).get("score", 5)
    risk = dims.get("risk", {}).get("score", 5)
    phase_label = scores.get("phase_label", "Unknown Phase")

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        prompt = _STUDY_PROMPT.format(
            title=title, role=role, department=department,
            problem_statement=problem,
            bv=bv, sa=sa, tf=tf, dr=dr, ttv=ttv, risk=risk,
            phase_label=phase_label, approach=approach,
        )
        req = LLMRequest(
            function="feasibility_study",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
        )
        result = router.invoke("feasibility_study", req)
        study_text = result.content if hasattr(result, "content") else str(result)
        if study_text and len(study_text) > 200:
            return study_text
    except Exception as e:
        logger.debug("LLM feasibility study failed (%s), using template", e)

    return _STUDY_FALLBACK.format(approach=approach, tf=tf)
