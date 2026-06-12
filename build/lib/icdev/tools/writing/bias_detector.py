# CUI // SP-CTI
"""WriteGuard Bias Detector (WG 17).

Context-aware deterministic detection of potentially biased language.

Supported contexts:
    general          — flags ability-coded language only
    perf_review      — plus gender-coded and age-coded language (LOW/MEDIUM)
    job_description  — plus gender-coded and age-coded language (MEDIUM)

Usage:
    from tools.writing.bias_detector import detect_bias

    result = detect_bias(text, context="job_description")
    # result = {issues: [...], score: 0-100, by_category: {...}, context_used: str}

Scanner-tier only (zero LLM). Stdlib only.

CUI // SP-CTI
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Word catalogues
# ---------------------------------------------------------------------------

_GENDER_FEMININE = [
    "nurturing", "supportive", "emotional", "collaborative", "gentle",
    "modest", "polite", "warm", "committed",
]
_GENDER_MASCULINE = [
    "aggressive", "ambitious", "assertive", "competitive", "dominant",
    "forceful", "decisive", "driven", "stubborn",
]
_AGE_CODED = [
    "digital native", "seasoned", "energetic", "overqualified",
    "fresh thinking", "old-school", "traditional", "young team", "mature employee",
]
_ABILITY_CODED = [
    "crazy", "insane", "blind spot", "tone-deaf", "crippled",
    "lame", "dumb", "psycho", "schizo",
]

_SUGGESTIONS: Dict[str, str] = {
    # Gender feminine-leaning
    "nurturing": "supportive of team growth",
    "supportive": "collaborative",
    "emotional": "empathetic",
    "collaborative": "team-oriented",
    "gentle": "measured",
    "modest": "understated",
    "polite": "respectful",
    "warm": "approachable",
    "committed": "dedicated",
    # Gender masculine-leaning
    "aggressive": "proactive",
    "ambitious": "goal-oriented",
    "assertive": "confident",
    "competitive": "results-oriented",
    "dominant": "leading",
    "forceful": "persuasive",
    "decisive": "clear-minded",
    "driven": "motivated",
    "stubborn": "persistent",
    # Age-coded
    "digital native": "technology-fluent",
    "seasoned": "experienced",
    "energetic": "motivated",
    "overqualified": "highly experienced",
    "fresh thinking": "innovative thinking",
    "old-school": "experienced",
    "traditional": "established",
    "young team": "growing team",
    "mature employee": "experienced employee",
    # Ability-coded
    "crazy": "unexpected",
    "insane": "remarkable",
    "blind spot": "gap in awareness",
    "tone-deaf": "unaware",
    "crippled": "impaired",
    "lame": "weak",
    "dumb": "unwise",
    "psycho": "erratic",
    "schizo": "inconsistent",
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _scan_words(text: str, words: List[str], category: str,
                severity: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        lower = line.lower()
        for w in words:
            pattern = r"\b" + re.escape(w) + r"\b"
            for _m in re.finditer(pattern, lower):
                issues.append({
                    "word": w,
                    "category": category,
                    "suggestion": _SUGGESTIONS.get(w, "consider neutral phrasing"),
                    "line": lineno,
                    "severity": severity,
                })
    return issues


def detect_bias(text: str, context: str = "general") -> Dict[str, Any]:
    """Detect biased language. ``context`` is one of: general, perf_review, job_description."""
    context = context if context in ("general", "perf_review", "job_description") else "general"
    issues: List[Dict[str, Any]] = []

    # Ability-coded: always flagged, HIGH severity
    issues.extend(_scan_words(text, _ABILITY_CODED, "ability_coded", "HIGH"))

    if context == "job_description":
        issues.extend(_scan_words(text, _GENDER_FEMININE, "gender_feminine", "MEDIUM"))
        issues.extend(_scan_words(text, _GENDER_MASCULINE, "gender_masculine", "MEDIUM"))
        issues.extend(_scan_words(text, _AGE_CODED, "age_coded", "MEDIUM"))
    elif context == "perf_review":
        issues.extend(_scan_words(text, _GENDER_FEMININE, "gender_feminine", "LOW"))
        issues.extend(_scan_words(text, _GENDER_MASCULINE, "gender_masculine", "LOW"))
        issues.extend(_scan_words(text, _AGE_CODED, "age_coded", "LOW"))

    by_category: Dict[str, int] = {}
    for i in issues:
        by_category[i["category"]] = by_category.get(i["category"], 0) + 1

    penalty = 0
    for i in issues:
        if i["severity"] == "HIGH":
            penalty += 10
        elif i["severity"] == "MEDIUM":
            penalty += 5
        else:
            penalty += 2
    score = max(0, 100 - penalty)

    return {
        "issues": issues,
        "score": score,
        "by_category": by_category,
        "context_used": context,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WriteGuard Bias Detector (WG 17)")
    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--context", type=str, default="general",
                        choices=["general", "perf_review", "job_description"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    result = detect_bias(text, context=args.context)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Context: {result['context_used']}")
        print(f"Score: {result['score']}/100")
        print(f"By category: {result['by_category']}")
        for i in result["issues"]:
            print(f"  [{i['severity']}] line {i['line']}: '{i['word']}' ({i['category']}) -> {i['suggestion']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
