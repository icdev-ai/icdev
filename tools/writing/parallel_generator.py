# CUI // SP-CTI
"""WriteGuard WG 28 — Parallel technical/executive version generator.

Generates a 5-line BLUF executive summary alongside the original technical text.
Fully deterministic: regex-based extraction of numbers, imperative sentences,
risk level, business impact, and next action. No LLM.

Public API:
    extract_key_facts(text)      -> dict
    generate_executive_summary(text, max_lines=5) -> str
    generate_parallel(text)      -> dict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

NUMBER_PATTERN = re.compile(
    r"\b(\$?\d+(?:,\d{3})*(?:\.\d+)?)\s*(M|B|K|%|years?|months?|days?)?\b",
    re.IGNORECASE,
)

IMPERATIVE_VERBS = (
    "recommend",
    "approve",
    "reject",
    "require",
    "must",
    "shall",
    "propose",
    "implement",
    "deploy",
    "migrate",
)

RISK_KEYWORDS = {
    "CRITICAL": ["critical risk"],
    "HIGH": ["high risk"],
    "MEDIUM": ["medium risk", "moderate risk"],
    "LOW": ["low risk"],
}

IMPACT_KEYWORDS = [
    "cost",
    "revenue",
    "savings",
    "impact",
    "risk",
    "customer",
    "user",
]

NEXT_ACTION_KEYWORDS = [
    "next steps",
    "next step",
    "action required",
    "action item",
    "to do",
    "todo",
    "deadline",
]

COST_KEYWORDS = ["cost", "budget", "price", "$", "spend"]
TIMELINE_KEYWORDS = ["deadline", "timeline", "by q", "quarter", "months", "weeks", "days", "year"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> List[str]:
    # Split on sentence terminators and newlines so headings / bullets don't
    # stick to the following sentence.
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_numbers(text: str) -> List[Dict[str, Any]]:
    """Extract numbers with optional units and surrounding context."""
    out: List[Dict[str, Any]] = []
    for m in NUMBER_PATTERN.finditer(text):
        value = m.group(1)
        unit = (m.group(2) or "").strip()
        start = max(0, m.start() - 30)
        end = min(len(text), m.end() + 30)
        context = text[start:end].replace("\n", " ").strip()
        out.append({"value": value, "unit": unit, "context_phrase": context})
    return out


def _find_imperative_sentence(sentences: List[str]) -> str:
    for s in sentences:
        low = s.strip().lower()
        # Strip leading markdown heading hashes / bullets
        low = re.sub(r"^[#\-\*\d\.\)\s]+", "", low)
        first_word = low.split(" ", 1)[0] if low else ""
        if first_word in IMPERATIVE_VERBS:
            return s.strip()
    return ""


def _find_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _find_risk_level(text: str) -> str:
    low = text.lower()
    for level, keywords in RISK_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                return level
    return "UNSPECIFIED"


def _find_impact_sentence(sentences: List[str]) -> str:
    for s in sentences:
        low = s.lower()
        if any(k in low for k in IMPACT_KEYWORDS):
            return s.strip()
    return ""


def _keyword_hit(text_lower: str, keywords: List[str]) -> bool:
    for k in keywords:
        pattern = r"\b" + re.escape(k) + r"\b"
        if re.search(pattern, text_lower):
            return True
    return False


def _find_next_action(text: str, sentences: List[str]) -> str:
    # Look for keyword-anchored line first (word-boundary match)
    for line in text.splitlines():
        low = line.lower()
        if _keyword_hit(low, NEXT_ACTION_KEYWORDS):
            cleaned = re.sub(r"^[#\-\*\d\.\)\s]+", "", line).strip()
            if cleaned:
                return cleaned
    # Fallback: any sentence containing next-action keyword
    for s in sentences:
        if _keyword_hit(s.lower(), NEXT_ACTION_KEYWORDS):
            return s.strip()
    return ""


def _find_cost_timeline(sentences: List[str]) -> str:
    cost_line = ""
    timeline_line = ""
    for s in sentences:
        low = s.lower()
        if not cost_line and any(k in low for k in COST_KEYWORDS):
            cost_line = s.strip()
        if not timeline_line and any(k in low for k in TIMELINE_KEYWORDS):
            timeline_line = s.strip()
    if cost_line and timeline_line and cost_line != timeline_line:
        return f"{cost_line} {timeline_line}"
    return cost_line or timeline_line


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_key_facts(text: str) -> Dict[str, Any]:
    """Deterministic extraction of facts from technical text."""
    if not isinstance(text, str):
        text = str(text or "")

    sentences = _split_sentences(text)
    key_numbers = _extract_numbers(text)

    main_decision = _find_imperative_sentence(sentences)
    if not main_decision:
        main_decision = _find_heading(text)

    business_impact = _find_impact_sentence(sentences)
    risk_level = _find_risk_level(text)
    next_action = _find_next_action(text, sentences)

    return {
        "key_numbers": key_numbers,
        "main_decision": main_decision,
        "business_impact": business_impact,
        "risk_level": risk_level,
        "next_action": next_action,
    }


def generate_executive_summary(text: str, max_lines: int = 5) -> str:
    """Deterministic 5-line BLUF version."""
    facts = extract_key_facts(text)
    sentences = _split_sentences(text)

    # Line 1 — decision / ask
    line1 = facts["main_decision"] or "[specify: decision or ask]"

    # Line 2 — business impact (preserves numbers if found in that sentence)
    line2 = facts["business_impact"] or "[specify: business impact]"

    # Line 3 — risk level + rationale
    risk = facts["risk_level"]
    rationale = ""
    if risk != "UNSPECIFIED":
        low_kw = risk.lower() + " risk"
        for s in sentences:
            if low_kw in s.lower():
                rationale = s.strip()
                break
    if risk == "UNSPECIFIED" and not rationale:
        line3 = "Risk: [specify: LOW/MEDIUM/HIGH/CRITICAL]"
    else:
        line3 = f"Risk: {risk}" + (f" - {rationale}" if rationale else "")

    # Line 4 — cost/timeline (prefer sentence distinct from business impact)
    impact_sentence = facts["business_impact"]
    cost_sentences = [s for s in sentences if s != impact_sentence]
    cost_timeline = _find_cost_timeline(cost_sentences) or _find_cost_timeline(sentences)
    line4 = cost_timeline or "[specify: cost and timeline]"

    # Line 5 — next action
    line5 = facts["next_action"] or "[specify: next action required]"

    lines = [line1, line2, line3, line4, line5][:max_lines]
    return "\n".join(lines)


def generate_parallel(text: str) -> Dict[str, Any]:
    """Return side-by-side technical and executive versions."""
    if not isinstance(text, str):
        text = str(text or "")

    # Clean technical version: trim whitespace, collapse excess blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    exec_summary = generate_executive_summary(text)
    facts = extract_key_facts(text)

    key_numbers_preserved: List[Dict[str, Any]] = []
    for num in facts["key_numbers"]:
        token = (num["value"] + (num["unit"] or "")).lower().replace(" ", "")
        in_both = token in exec_summary.lower().replace(" ", "")
        key_numbers_preserved.append(
            {
                "value": num["value"],
                "unit": num["unit"],
                "in_both": in_both,
            }
        )

    translation_notes: List[str] = []
    tech_len = len(text.split())
    exec_len = len(exec_summary.split())
    if tech_len > exec_len * 2:
        translation_notes.append(
            f"Compressed {tech_len} words of technical detail into {exec_len}-word BLUF"
        )
    if facts["risk_level"] == "UNSPECIFIED":
        translation_notes.append("Risk level not explicit in source — placeholder inserted")
    if not facts["next_action"]:
        translation_notes.append("No explicit next-action language found — placeholder inserted")
    missing_numbers = [n for n in key_numbers_preserved if not n["in_both"]]
    if missing_numbers:
        translation_notes.append(
            f"{len(missing_numbers)} source numbers omitted from executive summary"
        )
    if not facts["business_impact"]:
        translation_notes.append("Business impact sentence not detected — placeholder inserted")

    return {
        "technical_version": cleaned,
        "executive_version": exec_summary,
        "key_numbers_preserved": key_numbers_preserved,
        "translation_notes": translation_notes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WriteGuard WG 28 — Parallel technical/executive generator"
    )
    parser.add_argument("--text", help="Inline text to convert")
    parser.add_argument("--file", help="Path to text file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--exec-only", action="store_true", help="Print only the executive summary"
    )
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

    if args.exec_only:
        summary = generate_executive_summary(text)
        if args.json:
            print(json.dumps({"executive_version": summary}, indent=2))
        else:
            print(summary)
        return

    result = generate_parallel(text)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=== TECHNICAL VERSION ===")
        print(result["technical_version"])
        print()
        print("=== EXECUTIVE SUMMARY (BLUF) ===")
        print(result["executive_version"])
        print()
        print("=== KEY NUMBERS ===")
        for n in result["key_numbers_preserved"]:
            flag = "OK" if n["in_both"] else "MISSING"
            print(f"  [{flag}] {n['value']}{n['unit']}")
        if result["translation_notes"]:
            print()
            print("=== TRANSLATION NOTES ===")
            for note in result["translation_notes"]:
                print(f"  - {note}")


if __name__ == "__main__":
    main()
