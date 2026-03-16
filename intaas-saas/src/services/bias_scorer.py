#!/usr/bin/env python3
"""Deterministic bias scorer — multi-axis article-level bias forensics.

Scores 5 dimensions (1-5 scale) using keyword/pattern analysis.
No LLM required — deterministic, reproducible, air-gap safe.

Dimensions:
  1. loaded_language   — Emotionally charged words, adjectives, superlatives
  2. framing_balance   — Does the article present multiple viewpoints?
  3. statistical_honesty — Are statistics contextualised with base rates?
  4. source_transparency — Are sources named and diverse?
  5. omission_severity  — Are key counterpoints missing?
"""
import re
import logging

from src import config

logger = logging.getLogger("intaas.services.bias_scorer")

# Loaded language indicators (higher count = more bias)
LOADED_WORDS = {
    "high": [
        "radical", "extreme", "dangerous", "outrageous", "shocking", "devastating",
        "catastrophic", "disaster", "crisis", "horrific", "terrifying", "propaganda",
        "brainwashed", "corrupt", "rigged", "tyranny", "fascist", "socialist",
        "woke", "snowflake", "elitist", "puppet", "regime", "hoax",
    ],
    "medium": [
        "alarming", "controversial", "unprecedented", "explosive", "stunning",
        "bombshell", "slam", "blast", "rip", "destroy", "crush", "dominate",
        "massive", "huge", "incredible", "unbelievable", "insane",
    ],
}

# Balance indicators
BALANCE_PHRASES = [
    "on the other hand", "however", "critics argue", "supporters say",
    "opponents note", "proponents claim", "alternatively", "in contrast",
    "some argue", "others believe", "while some", "despite this",
    "according to", "research shows", "studies suggest", "experts disagree",
]

# Statistical context indicators
STAT_PATTERNS = [
    r"\d+%",  # Percentages
    r"\$[\d,.]+\s*(million|billion|trillion)?",  # Dollar amounts
    r"\d+\s*(times|fold|x)\s*(more|less|higher|lower)",  # Multipliers
]

STAT_CONTEXT_PHRASES = [
    "compared to", "relative to", "per capita", "as a percentage of",
    "adjusted for", "in context", "base rate", "year over year",
    "baseline", "control group", "sample size", "margin of error",
]


def score_article(content: str, title: str = "") -> dict:
    """Score an article across 5 bias dimensions.

    Returns dict with dimensions (each 1-5), composite, color, and findings.
    """
    text = f"{title} {content}".lower()
    word_count = max(len(text.split()), 1)
    findings = []

    # 1. Loaded Language (inverse — more loaded words = lower score)
    high_count = sum(1 for w in LOADED_WORDS["high"] if w in text)
    med_count = sum(1 for w in LOADED_WORDS["medium"] if w in text)
    loaded_density = (high_count * 2 + med_count) / (word_count / 100)

    if loaded_density > 5:
        loaded_score = 1.0
    elif loaded_density > 3:
        loaded_score = 2.0
    elif loaded_density > 1.5:
        loaded_score = 3.0
    elif loaded_density > 0.5:
        loaded_score = 4.0
    else:
        loaded_score = 5.0

    if high_count > 0:
        found = [w for w in LOADED_WORDS["high"] if w in text][:3]
        findings.append(f"Loaded language: {', '.join(found)}")

    # 2. Framing Balance (more balance phrases = higher score)
    balance_count = sum(1 for p in BALANCE_PHRASES if p in text)
    if balance_count >= 5:
        balance_score = 5.0
    elif balance_count >= 3:
        balance_score = 4.0
    elif balance_count >= 2:
        balance_score = 3.0
    elif balance_count >= 1:
        balance_score = 2.0
    else:
        balance_score = 1.0
        findings.append("No balancing language detected — single viewpoint")

    # 3. Statistical Honesty
    stat_count = sum(len(re.findall(p, text)) for p in STAT_PATTERNS)
    context_count = sum(1 for p in STAT_CONTEXT_PHRASES if p in text)

    if stat_count == 0:
        stat_score = 3.0  # Neutral — no stats to evaluate
    elif context_count >= stat_count * 0.5:
        stat_score = 5.0
    elif context_count >= stat_count * 0.3:
        stat_score = 4.0
    elif context_count > 0:
        stat_score = 3.0
    else:
        stat_score = 2.0
        findings.append(f"{stat_count} statistics without context")

    # 4. Source Transparency
    source_patterns = [
        r"according to\s+\w+", r"said\s+\w+", r'"\w+[^"]*"\s*,?\s*said',
        r"a?\s*spokesperson", r"official\s+statement", r"press release",
        r"study\s+by", r"report\s+from", r"data\s+from",
    ]
    named_sources = sum(len(re.findall(p, text)) for p in source_patterns)

    anon_patterns = ["sources say", "insiders claim", "reportedly", "allegedly", "rumored"]
    anon_count = sum(1 for p in anon_patterns if p in text)

    if named_sources >= 4 and anon_count == 0:
        transparency_score = 5.0
    elif named_sources >= 2:
        transparency_score = 4.0
    elif named_sources >= 1:
        transparency_score = 3.0
    elif anon_count > 0:
        transparency_score = 2.0
        findings.append(f"{anon_count} anonymous/unverified source references")
    else:
        transparency_score = 2.0
        findings.append("No identifiable sources cited")

    # 5. Omission Severity (heuristic — checks for question-begging)
    one_sided_indicators = [
        "no one disputes", "everyone knows", "clearly", "obviously",
        "it goes without saying", "needless to say", "the fact is",
        "the truth is", "common sense", "without question",
    ]
    one_sided_count = sum(1 for p in one_sided_indicators if p in text)

    if one_sided_count == 0 and balance_count >= 2:
        omission_score = 5.0
    elif one_sided_count == 0:
        omission_score = 4.0
    elif one_sided_count <= 1:
        omission_score = 3.0
    elif one_sided_count <= 3:
        omission_score = 2.0
        findings.append(f"Assumptive language: {one_sided_count} instances")
    else:
        omission_score = 1.0
        findings.append("Heavy use of assumptive/absolutist language")

    # Composite (equal weight)
    dimensions = {
        "loaded_language": loaded_score,
        "framing_balance": balance_score,
        "statistical_honesty": stat_score,
        "source_transparency": transparency_score,
        "omission_severity": omission_score,
    }
    composite = round(sum(dimensions.values()) / len(dimensions), 2)

    # Color rating
    color, color_label = _score_to_color(composite)

    return {
        "dimensions": dimensions,
        "composite": composite,
        "color": color,
        "color_label": color_label,
        "findings": findings,
        "word_count": word_count,
    }


def _score_to_color(score: float) -> tuple[str, str]:
    """Map composite score to FAR 15.305 color rating."""
    for color_name, rating in config.COLOR_RATINGS.items():
        if score >= rating["min_score"]:
            return color_name, rating["label"]
    return "red", "Unacceptable"
