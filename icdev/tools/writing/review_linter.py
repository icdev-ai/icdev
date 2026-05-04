# CUI // SP-CTI
"""WriteGuard WG 21 — ARB/ERB review comment linter.

Lints board-review comments so that each paragraph contains (1) a section
reference, (2) a decision verb, and (3) a recommendation. Deterministic, no LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

_SECTION_REF_PATTERNS = [
    re.compile(r"\bsection \d+(\.\d+)*\b", re.IGNORECASE),
    re.compile(r"\bline \d+\b", re.IGNORECASE),
    re.compile(r"\bfigure \d+\b", re.IGNORECASE),
    re.compile(r"\btable \d+\b", re.IGNORECASE),
    re.compile(r"\bpage \d+\b", re.IGNORECASE),
    re.compile(r"\bparagraph \d+\b", re.IGNORECASE),
]

_DECISION_VERBS = {
    "approve", "approved", "approval",
    "reject", "rejected",
    "defer", "deferred",
    "require", "required",
    "recommend", "recommended",
    "note", "noted",
    "flag", "flagged",
    "block", "blocked",
    "waive", "waived",
}

_RECOMMENDATION_WORDS = {
    "recommend", "recommended", "suggest", "suggested",
    "should", "must", "propose", "proposed",
}

_ACTION_VERBS_AT_START = {
    "add", "remove", "update", "replace", "clarify", "rewrite",
    "expand", "shorten", "delete", "include", "correct", "revise",
    "strengthen", "define", "cite", "provide",
}

_VAGUE_PHRASES = [
    "needs work", "unclear", "improve this", "not good",
    "fix this", "better",
]
_VAGUE_STANDALONE = {"review"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _has_section_ref(para: str) -> bool:
    return any(p.search(para) for p in _SECTION_REF_PATTERNS)


def _has_decision_verb(para: str) -> bool:
    words = re.findall(r"\b[a-zA-Z]+\b", para.lower())
    return any(w in _DECISION_VERBS for w in words)


def _has_recommendation(para: str) -> bool:
    low = para.lower()
    words = re.findall(r"\b[a-zA-Z]+\b", low)
    if any(w in _RECOMMENDATION_WORDS for w in words):
        return True
    # Action verb at start of any sentence in paragraph
    sentences = re.split(r"[.!?]\s+", para)
    for s in sentences:
        first = re.match(r"\s*([A-Za-z]+)", s)
        if first and first.group(1).lower() in _ACTION_VERBS_AT_START:
            return True
    return False


def _vague_language(para: str) -> List[str]:
    low = para.lower()
    hits: List[str] = []
    for phrase in _VAGUE_PHRASES:
        if phrase in low:
            hits.append(phrase)
    # Standalone "review" only if it's a bare standalone instruction
    stripped = low.strip().rstrip(".!?")
    if stripped in _VAGUE_STANDALONE:
        hits.append(stripped)
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lint_review_comments(text: str) -> Dict[str, Any]:
    """Lint ARB/ERB review comments for section ref + decision verb + recommendation.

    Returns: {score 0-100, issues, comment_count, with_section_ref,
              with_verb, with_recommendation}
    """
    if not text or not text.strip():
        return {
            "score": 0,
            "issues": ["empty input"],
            "comment_count": 0,
            "with_section_ref": 0,
            "with_verb": 0,
            "with_recommendation": 0,
        }

    paragraphs = _split_paragraphs(text)
    issues: List[Dict[str, Any]] = []

    with_ref = 0
    with_verb = 0
    with_rec = 0

    for idx, para in enumerate(paragraphs, start=1):
        has_ref = _has_section_ref(para)
        has_verb = _has_decision_verb(para)
        has_rec = _has_recommendation(para)
        vague = _vague_language(para)

        if has_ref:
            with_ref += 1
        if has_verb:
            with_verb += 1
        if has_rec:
            with_rec += 1

        missing = []
        if not has_ref:
            missing.append("section_reference")
        if not has_verb:
            missing.append("decision_verb")
        if not has_rec:
            missing.append("recommendation")

        if missing or vague:
            preview = para[:80].replace("\n", " ")
            issue: Dict[str, Any] = {
                "paragraph": idx,
                "preview": preview + ("..." if len(para) > 80 else ""),
                "missing": missing,
            }
            if vague:
                issue["vague_language"] = vague
            issues.append(issue)

    count = len(paragraphs)
    if count == 0:
        score = 0
    else:
        coverage = (with_ref + with_verb + with_rec) / (3.0 * count)
        vague_count = sum(1 for i in issues if i.get("vague_language"))
        penalty = min(20.0, vague_count * 5.0)
        score = max(0, int(round(coverage * 100.0 - penalty)))

    return {
        "score": score,
        "issues": issues,
        "comment_count": count,
        "with_section_ref": with_ref,
        "with_verb": with_verb,
        "with_recommendation": with_rec,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="WriteGuard WG 21 — ARB/ERB review comment linter")
    parser.add_argument("--text", help="Inline text to lint")
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

    result = lint_review_comments(text)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Score: {result['score']}/100")
        print(f"Comments: {result['comment_count']}")
        print(f"  with section ref: {result['with_section_ref']}")
        print(f"  with decision verb: {result['with_verb']}")
        print(f"  with recommendation: {result['with_recommendation']}")
        if result["issues"]:
            print("Issues:")
            for i in result["issues"]:
                miss = ",".join(i.get("missing", [])) or "-"
                vague = ",".join(i.get("vague_language", [])) or ""
                extra = f" vague=[{vague}]" if vague else ""
                print(f"  [para {i['paragraph']}] missing={miss}{extra}  {i['preview']}")


if __name__ == "__main__":
    main()
