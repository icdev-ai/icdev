# CUI // SP-CTI
"""WriteGuard WG 27 — Neutrality and professional dissent scorer.

Scores ARB/ERB review comments for neutrality, detecting pre-decided language
and personal attacks, while rewarding professional dissent markers.
Deterministic (regex-based). No LLM.

Returns:
    {
      score: 0-100,
      pre_decided_flags: [{phrase, line}],
      attack_flags: [{phrase, line, category}],
      dissent_markers_found: [str],
      dissent_quality: 0-100,
      issues: [str]
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Pattern lists
# ---------------------------------------------------------------------------

PRE_DECIDED_PHRASES = [
    "obviously wrong",
    "clearly missing",
    "trivially broken",
    "should have known",
    "everyone knows",
    "definitely not",
    "cannot possibly",
    "wasted effort",
    "fundamentally flawed",
    "completely wrong",
    "no way this works",
    "this won't work",
    "already decided",
    "predetermined",
]

ATTACK_PHRASES = [
    "the author failed to",
    "they didn't bother",
    "sloppy work",
    "half-baked",
    "amateur",
    "lazy approach",
    "careless",
    "obviously incompetent",
    "doesn't understand",
    "ignorant of",
]

DISSENT_MARKERS = [
    "i respectfully disagree because",
    "while i appreciate",
    "the concern remains",
    "an alternative view",
    "with respect to",
    "i would argue",
    "consideration should be given",
    "we might consider",
    "my concern is",
]

DISSENT_INDICATOR_WORDS = ["disagree", "oppose", "dissent", "minority"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_phrase_hits(text: str, phrases: List[str]) -> List[Dict[str, Any]]:
    """Return list of {phrase, line} hits (case-insensitive, whole-phrase)."""
    lines = text.splitlines() or [text]
    hits: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        lower = line.lower()
        for phrase in phrases:
            if phrase.lower() in lower:
                hits.append({"phrase": phrase, "line": idx})
    return hits


def _find_dissent_markers(text: str) -> List[str]:
    lower = text.lower()
    found: List[str] = []
    for marker in DISSENT_MARKERS:
        if marker in lower:
            found.append(marker)
    return found


def _has_dissent_indicator(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", lower) for w in DISSENT_INDICATOR_WORDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_neutrality(text: str) -> Dict[str, Any]:
    """Score neutrality and professional dissent tone.

    Returns a dict with score (0-100), flags, dissent markers, issues.
    """
    if not isinstance(text, str):
        text = str(text or "")

    pre_decided_hits_raw = _find_phrase_hits(text, PRE_DECIDED_PHRASES)
    attack_hits_raw = _find_phrase_hits(text, ATTACK_PHRASES)

    pre_decided_flags: List[Dict[str, Any]] = [
        {"phrase": h["phrase"], "line": h["line"]} for h in pre_decided_hits_raw
    ]
    attack_flags: List[Dict[str, Any]] = [
        {"phrase": h["phrase"], "line": h["line"], "category": "attack"}
        for h in attack_hits_raw
    ]

    dissent_markers_found = _find_dissent_markers(text)
    has_indicator = _has_dissent_indicator(text)

    # Scoring
    score = 100
    score -= 15 * len(pre_decided_flags)
    score -= 20 * len(attack_flags)

    dissent_bonus = 0
    if has_indicator and len(dissent_markers_found) >= 2:
        dissent_bonus = 10
        dissent_quality = 90
    elif has_indicator and len(dissent_markers_found) >= 1:
        dissent_quality = 60
    elif has_indicator:
        dissent_quality = 30
    else:
        # No dissent expressed — quality not applicable but report neutral
        dissent_quality = 0 if not dissent_markers_found else 50

    score += dissent_bonus
    score = max(0, min(100, score))

    # Category classification of the text overall
    if attack_flags:
        category = "attack"
    elif pre_decided_flags:
        category = "pre_decided"
    elif has_indicator and len(dissent_markers_found) >= 1:
        category = "professional_dissent"
    else:
        category = "neutral"

    # Issues
    issues: List[str] = []
    for f in pre_decided_flags:
        issues.append(
            f"Pre-decided language on line {f['line']}: '{f['phrase']}'"
        )
    for f in attack_flags:
        issues.append(
            f"Personal attack on line {f['line']}: '{f['phrase']}'"
        )
    if has_indicator and len(dissent_markers_found) == 0:
        issues.append(
            "Dissent expressed without professional dissent markers "
            "(e.g., 'I respectfully disagree because', 'my concern is')"
        )

    return {
        "score": score,
        "category": category,
        "pre_decided_flags": pre_decided_flags,
        "attack_flags": attack_flags,
        "dissent_markers_found": dissent_markers_found,
        "dissent_quality": dissent_quality,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WriteGuard WG 27 — Neutrality and professional dissent scorer"
    )
    parser.add_argument("--text", help="Inline text to score")
    parser.add_argument("--file", help="Path to text file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()

    result = score_neutrality(text)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Neutrality score: {result['score']}/100 [{result['category']}]")
        print(f"Dissent quality: {result['dissent_quality']}/100")
        if result["pre_decided_flags"]:
            print("Pre-decided flags:")
            for f in result["pre_decided_flags"]:
                print(f"  line {f['line']}: {f['phrase']}")
        if result["attack_flags"]:
            print("Attack flags:")
            for f in result["attack_flags"]:
                print(f"  line {f['line']}: {f['phrase']}")
        if result["dissent_markers_found"]:
            print("Dissent markers found:")
            for m in result["dissent_markers_found"]:
                print(f"  - {m}")
        if result["issues"]:
            print("Issues:")
            for i in result["issues"]:
                print(f"  - {i}")


if __name__ == "__main__":
    main()
