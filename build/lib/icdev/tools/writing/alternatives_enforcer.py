# CUI // SP-CTI
"""WriteGuard alternatives enforcer (WG 24).

Checks ADR / design documents for the "alternatives considered" section and
scores trade-off quality. Deterministic regex — no LLM.

CLI:
    python -m tools.writing.alternatives_enforcer --text "..." --json
    python -m tools.writing.alternatives_enforcer --file path/to/adr.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


_DECISION_HEADINGS = ("context", "decision", "consequences")
_ALT_HEADING_PATTERNS = [
    r"alternatives",
    r"options\s+considered",
    r"rejected\s+alternatives",
    r"other\s+approaches",
]
_TRADEOFF_KEYWORDS = [
    "trade-off", "tradeoff", "trade off", "pro", "con", "pros", "cons",
    "advantage", "disadvantage", "cost", "benefit",
    "rejected because", "not chosen because", "avoided due to",
]


def _has_heading(text: str, term: str) -> bool:
    # Match markdown heading (# ...) or bold label forms containing ``term``.
    # Allow trailing words (e.g. "Alternatives Considered") up to EOL.
    patterns = [
        r"(?:^|\n)\s*#{1,6}\s+[^\n]*?\b" + term + r"\b[^\n]*",
        r"(?:^|\n)\s*(?:\*\*|__)\s*[^\n]*?\b" + term + r"\b[^\n]*?(?:\*\*|__)",
        r"(?:^|\n)\s*" + term + r"\s*:\s*(?:\n|$)",
    ]
    for p in patterns:
        if re.search(p, text, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def _is_decision_doc(text: str, title_hint: str = "") -> bool:
    title = (title_hint or "").lower()
    if "adr" in title or "decision record" in title:
        return True
    found = sum(1 for h in _DECISION_HEADINGS if _has_heading(text, h))
    return found >= 3  # require all three (context, decision, consequences)


def _has_alternatives_section(text: str) -> bool:
    return any(_has_heading(text, pat) for pat in _ALT_HEADING_PATTERNS)


def _count_alternatives(text: str) -> List[str]:
    names: List[str] = []
    seen: set = set()

    # Patterns to match named alternatives
    patterns = [
        r"\b(Option\s+[A-Z0-9][\w\-]*)\b",
        r"\b(Alternative\s+\d+)\b",
        r"\b(Alternative\s+[A-Z])\b",
        r"\b((?:Option|Alternative|Approach)\s*\d+)\s*:",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            name = m.group(1).strip()
            key = name.lower()
            if key not in seen:
                seen.add(key)
                names.append(name)

    # Numbered list items within an Alternatives section (heading to next heading)
    alt_block = _extract_alternatives_block(text)
    if alt_block:
        for m in re.finditer(r"(?:^|\n)\s*(\d+)[\.\)]\s+([^\n]{3,120})", alt_block):
            label = f"Item {m.group(1)}"
            key = label.lower() + ":" + m.group(2).strip().lower()[:40]
            if key not in seen:
                seen.add(key)
                names.append(label + ": " + m.group(2).strip()[:80])
    return names


def _extract_alternatives_block(text: str) -> str:
    """Return substring from alternatives heading to next heading (or EOF)."""
    # Find heading start
    start_match = None
    for pat in _ALT_HEADING_PATTERNS:
        m = re.search(
            r"(?:^|\n)\s*#{1,6}\s+[^\n]*?\b" + pat + r"\b[^\n]*\n",
            text,
            re.IGNORECASE,
        )
        if m and (start_match is None or m.start() < start_match.start()):
            start_match = m
    if not start_match:
        return ""
    start = start_match.end()
    # Find next heading at same-or-higher level (any # heading)
    rest = text[start:]
    next_m = re.search(r"\n\s*#{1,6}\s+\S", rest)
    end = start + (next_m.start() if next_m else len(rest))
    return text[start:end]


def _tradeoff_score(text: str, alt_count: int) -> int:
    if alt_count == 0:
        return 0
    low = text.lower()
    hits = sum(1 for kw in _TRADEOFF_KEYWORDS if kw in low)
    # Score per alternative: cap the contribution
    per_alt = min(hits, alt_count * 3)
    return min(100, int((per_alt / max(1, alt_count * 3)) * 100))


def enforce_alternatives(text: str, title_hint: str = "") -> Dict[str, Any]:
    """Detect decision docs and check for alternatives + trade-offs."""
    issues: List[str] = []

    is_doc = _is_decision_doc(text, title_hint=title_hint)
    has_alts = _has_alternatives_section(text)
    names = _count_alternatives(text) if has_alts or is_doc else []
    alt_count = len(names)

    if not is_doc:
        return {
            "is_decision_doc": False,
            "has_alternatives_section": has_alts,
            "alternative_count": alt_count,
            "alternative_names": names,
            "trade_off_quality_score": 100,
            "missing_alternatives_flag": False,
            "issues": issues,
        }

    # Scoring per spec
    score = 0
    if not has_alts:
        score = 20
        issues.append("Decision document missing 'Alternatives Considered' section")
    else:
        if alt_count >= 2:
            score += 40
        else:
            issues.append(f"Only {alt_count} alternative(s) named; need >= 2")

        alt_block = _extract_alternatives_block(text) or text
        low = alt_block.lower()
        per_alt_bonus = 0
        for _ in range(alt_count):
            per_alt_bonus += 10
        # only award bonus if any trade-off keyword found
        if any(kw in low for kw in _TRADEOFF_KEYWORDS):
            score += per_alt_bonus
        else:
            issues.append("No trade-off / pros-cons discussion detected")
        score = min(100, score)

    tradeoff_score = _tradeoff_score(_extract_alternatives_block(text) or text, alt_count)
    missing_flag = is_doc and (not has_alts or alt_count < 2)

    return {
        "is_decision_doc": is_doc,
        "has_alternatives_section": has_alts,
        "alternative_count": alt_count,
        "alternative_names": names,
        "trade_off_quality_score": max(tradeoff_score, score if has_alts else 0),
        "missing_alternatives_flag": missing_flag,
        "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="WriteGuard alternatives enforcer (WG 24)")
    ap.add_argument("--text")
    ap.add_argument("--file")
    ap.add_argument("--title", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
        text = path.read_text(encoding="utf-8")
        title = args.title or path.stem
    elif args.text:
        text = args.text
        title = args.title
    else:
        ap.error("Provide --text or --file")
        return 2

    result = enforce_alternatives(text, title_hint=title)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
