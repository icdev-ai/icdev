#!/usr/bin/env python3
"""Socratic dialogue engine — generates progressive critical thinking questions.

5-turn model (academically validated — Frontiers in Education, Georgia Tech):
  Turn 1: Assumption challenge — "What assumptions does Source A make?"
  Turn 2: Evidence demand — "What evidence supports this claim?"
  Turn 3: Contrarian probe — "What would a critic say?"
  Turn 4: Omission detection — "What are ALL sources ignoring?"
  Turn 5: Synthesis invitation — "Given all this, what's YOUR assessment?"

Deterministic first — uses template-based questions with article context.
LLM optional for deeper follow-up (via SageMaker Prometheus-2 or Bedrock).
"""
import logging
import random

import requests

from src import config

logger = logging.getLogger("intaas.services.socratic")

# Question templates by type (deterministic, no LLM needed)
QUESTION_TEMPLATES = {
    "assumption": [
        'Source "{source}" claims: "{claim}". What underlying assumptions does this rely on? '
        "What would need to be true for this claim to hold?",
        'Consider the framing in "{source}": they describe this as "{framing}". '
        "What alternative framing could present the same facts differently?",
        "This story is being told through the lens of {perspective}. "
        "How would the narrative change if told from {alt_perspective}?",
    ],
    "evidence": [
        '"{source}" cites {evidence_type} to support their claim. '
        "How strong is this evidence? What kind of evidence would you need to be fully convinced?",
        "{n_sources} sources cover this topic, but only {n_citing} cite specific data. "
        "Does the absence of data in {n_missing} sources concern you? Why or why not?",
        "The strongest factual claim here is: \"{claim}\". "
        "Can you identify the original source of this claim? Is it primary or secondary?",
    ],
    "contrarian": [
        "A thoughtful critic might argue the exact opposite: \"{contrarian_view}\". "
        "What evidence would support this contrarian position?",
        "Imagine you had to write a rebuttal to ALL of these sources. "
        "What's the strongest counter-argument you could make?",
        "If {expert_type} were reading these articles, what would they likely challenge first?",
    ],
    "omission": [
        "{total_sources} sources covered this story. None of them mentioned {omitted_fact}. "
        "Is this an oversight, irrelevant, or deliberately omitted? What's your assessment?",
        "Compare the coverage: {source_a} leads with {angle_a}, while {source_b} leads with {angle_b}. "
        "What topic do BOTH sources avoid discussing?",
        "What question would you ask that NONE of these articles answer? "
        "Why do you think no journalist has explored that angle?",
    ],
    "synthesis": [
        "You've examined {n_perspectives} perspectives, identified coverage gaps, and challenged assumptions. "
        "Now: what's YOUR informed assessment of this topic? What do you believe, and why?",
        "If you had to explain this topic to a friend in 60 seconds, giving them the MOST balanced view, "
        "what would you say? What would you warn them about?",
        "Rate your confidence in your understanding of this topic from 1-10. "
        "What single piece of information would most change your confidence?",
    ],
}

# Turn number to question type mapping (5-turn model)
TURN_SEQUENCE = ["assumption", "evidence", "contrarian", "omission", "synthesis"]


def generate_next_question(
    topic: dict,
    articles: list[dict],
    perspectives: list[dict],
    previous_turns: list[dict],
    user_response: str | None = None,
) -> dict:
    """Generate the next Socratic question based on conversation state.

    Deterministic template selection with article context injection.
    """
    ai_turn_count = sum(1 for t in previous_turns if t.get("role") == "ai")
    turn_index = min(ai_turn_count, len(TURN_SEQUENCE) - 1)
    question_type = TURN_SEQUENCE[turn_index]

    templates = QUESTION_TEMPLATES[question_type]
    template = random.choice(templates)

    # Build context variables from articles/perspectives
    context = _build_context(topic, articles, perspectives)

    # Format template with available context
    try:
        content = template.format(**context)
    except KeyError:
        # Fallback — use a generic version
        content = _generic_question(question_type, context)

    # If LLM is available, enhance the template question with real reasoning
    if config.LLM_JUDGE_ENABLED and config.ANTHROPIC_API_KEY:
        content = _enhance_with_llm(content, question_type, context)

    return {
        "content": content,
        "question_type": question_type,
        "turn_index": turn_index,
    }


def _build_context(topic: dict, articles: list[dict], perspectives: list[dict]) -> dict:
    """Extract context variables from available data."""
    titles = [a.get("title", "") for a in articles if a.get("title")]
    sources = [p.get("source_name", "") for p in perspectives if p.get("source_name")]

    return {
        "topic_title": topic.get("title", "this topic"),
        "source": sources[0] if sources else "the primary source",
        "source_a": sources[0] if len(sources) > 0 else "Source A",
        "source_b": sources[1] if len(sources) > 1 else "Source B",
        "claim": titles[0][:100] if titles else "the main claim",
        "framing": "a significant development",
        "perspective": "one particular viewpoint",
        "alt_perspective": "an opposing viewpoint",
        "evidence_type": "cited sources",
        "n_sources": str(len(articles)),
        "n_citing": str(max(1, len(articles) // 3)),
        "n_missing": str(max(1, len(articles) * 2 // 3)),
        "n_perspectives": str(len(perspectives)),
        "contrarian_view": "this narrative oversimplifies a complex situation",
        "expert_type": "a subject matter expert",
        "omitted_fact": "potential counter-evidence",
        "angle_a": "the human impact",
        "angle_b": "the policy implications",
        "total_sources": str(len(articles)),
    }


def _enhance_with_llm(
    template_question: str,
    question_type: str,
    context: dict,
) -> str:
    """Enhance template question with LLM reasoning.

    Takes the deterministic template and asks Claude to make it
    specific to the actual articles and perspectives.
    """
    titles = context.get("topic_title", "this topic")
    sources = [context.get("source_a", ""), context.get("source_b", "")]
    source_list = ", ".join(s for s in sources if s)

    prompt = f"""You are a Socratic questioning expert analyzing news coverage.

Topic: {titles}
Sources covering this topic: {source_list}
Question type: {question_type}
Template question: {template_question}

Rewrite this question to be MORE specific and thought-provoking.
Use the actual topic and source names.
NEVER give an answer — only ask a question that forces critical thinking.
Keep it under 3 sentences. Be direct and challenging.

Rewritten question:"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_JUDGE_MODEL,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        enhanced = data.get("content", [{}])[0].get("text", "").strip()
        if enhanced and len(enhanced) > 20:
            # Strip markdown artifacts from LLM response
            enhanced = _strip_markdown(enhanced)
            return enhanced
    except Exception as e:
        logger.warning("LLM enhancement failed: %s", e)

    return template_question


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting tags from LLM responses."""
    import re
    # Remove heading markers (# ## ###)
    text = re.sub(r"^#{1,4}\s*", "", text, flags=re.MULTILINE)
    # Remove "Rewritten Question:" prefix
    text = re.sub(
        r"^(Rewritten\s*(Socratic\s*)?Question\s*:?\s*)",
        "", text, flags=re.IGNORECASE,
    )
    # Remove bold/italic markers
    text = text.replace("**", "").replace("__", "")
    # Clean up leading/trailing whitespace
    return text.strip()


def _generic_question(question_type: str, context: dict) -> str:
    """Fallback generic questions when template formatting fails."""
    generics = {
        "assumption": f"What assumptions are the sources making about '{context.get('topic_title', 'this topic')}'? "
                      "Are these assumptions justified?",
        "evidence": "What evidence do the sources provide? Is it primary or secondary? "
                    "What additional evidence would strengthen or weaken these claims?",
        "contrarian": "What would someone who strongly disagrees with these sources argue? "
                      "Can you steelman the opposing position?",
        "omission": "What important aspects of this topic do ALL the sources fail to address? "
                    "Why might this angle be missing from the coverage?",
        "synthesis": "Having examined multiple perspectives, what is YOUR informed assessment? "
                     "What are you most and least confident about?",
    }
    return generics.get(question_type, "What do you think is the most important thing missing from this coverage?")
