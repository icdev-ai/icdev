#!/usr/bin/env python3
# CUI // SP-CTI
"""Claim Validator (WG 15) — deterministic evidence density scoring.

Flags unsupported claims that need citations. Scanner-tier only (no LLM).

Detects:
  - Numbers without context (percentages, multipliers, dollar amounts)
  - Superlatives ("best", "fastest", "leading")
  - Comparisons ("faster than", "outperforms")
  - Universal quantifiers ("all", "never", "always")
  - Trend claims ("growing", "accelerating")

Citation markers looked for in the same paragraph:
  - [Source: ...], (per ...), "according to", cite: ...
  - URLs, footnote markers [^1] or [1], DOIs

Entry points:
    find_claims(text) -> list[dict]
    score_evidence_density(text) -> dict
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Claim detection patterns
# ---------------------------------------------------------------------------

CLAIM_PATTERNS: Dict[str, re.Pattern[str]] = {
    "percentage": re.compile(r"\b\d+(?:\.\d+)?%"),
    "multiplier": re.compile(r"\b\d+x\s+(?:faster|slower|better|more)\b", re.IGNORECASE),
    "dollar_amount": re.compile(
        r"\$\s?\d+(?:[,\.]\d+)*(?:\s?(?:billion|million|thousand|B|M|K))?\b",
        re.IGNORECASE,
    ),
    "superlative": re.compile(
        r"\b(best|worst|fastest|largest|smallest|most|least|only|unique|first|"
        r"leading|industry-leading|top|premier)\b",
        re.IGNORECASE,
    ),
    "comparison": re.compile(
        r"\b(better than|faster than|outperforms|surpasses|exceeds|vs\.|versus|"
        r"compared to)\b",
        re.IGNORECASE,
    ),
    "universal": re.compile(
        r"\b(all|every|none|never|always|nobody|everyone)\b",
        re.IGNORECASE,
    ),
    "trend": re.compile(
        r"\b(growing|increasing|declining|accelerating|surging|skyrocketing)\b",
        re.IGNORECASE,
    ),
}

CITATION_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\[Source:\s*[^\]]+\]", re.IGNORECASE),
    re.compile(r"\(per\s+[^)]+\)", re.IGNORECASE),
    re.compile(r"\baccording to\b", re.IGNORECASE),
    re.compile(r"\bcite:\s*\S+", re.IGNORECASE),
    re.compile(r"\bhttps?://\S+"),
    re.compile(r"\[\^\d+\]"),
    re.compile(r"\[\d+\]"),
    re.compile(r"\bdoi:\s*\S+", re.IGNORECASE),
    re.compile(r"\b10\.\d{4,9}/\S+"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> List[str]:
    """Split on blank lines; keep non-empty paragraphs."""
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _paragraph_has_citation(paragraph: str) -> bool:
    return any(p.search(paragraph) for p in CITATION_PATTERNS)


def _line_number_of(text: str, offset: int) -> int:
    """1-indexed line number for the given character offset."""
    return text.count("\n", 0, offset) + 1


def _confidence_for(claim_type: str, has_citation: bool) -> float:
    """Higher = more confident this really needs a citation."""
    base = {
        "percentage": 0.80,
        "multiplier": 0.90,
        "dollar_amount": 0.75,
        "superlative": 0.70,
        "comparison": 0.85,
        "universal": 0.65,
        "trend": 0.70,
    }.get(claim_type, 0.60)
    if has_citation:
        return round(base * 0.25, 2)
    return round(base, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_claims(text: str) -> List[Dict[str, Any]]:
    """Find claim occurrences. Returns list of dicts with keys:
    claim_text, type, line, has_citation, confidence.
    """
    claims: List[Dict[str, Any]] = []
    if not text:
        return claims

    # Build paragraph span map so we can decide citation presence per paragraph.
    paragraphs: List[Dict[str, Any]] = []
    cursor = 0
    for raw in re.split(r"(\n\s*\n)", text):
        span_start = cursor
        span_end = cursor + len(raw)
        cursor = span_end
        if raw.strip():
            paragraphs.append(
                {
                    "start": span_start,
                    "end": span_end,
                    "has_citation": _paragraph_has_citation(raw),
                }
            )

    def _paragraph_citation(pos: int) -> bool:
        for p in paragraphs:
            if p["start"] <= pos < p["end"]:
                return bool(p["has_citation"])
        return False

    for claim_type, pattern in CLAIM_PATTERNS.items():
        for match in pattern.finditer(text):
            pos = match.start()
            has_cite = _paragraph_citation(pos)
            claims.append(
                {
                    "claim_text": match.group(0),
                    "type": claim_type,
                    "line": _line_number_of(text, pos),
                    "has_citation": has_cite,
                    "confidence": _confidence_for(claim_type, has_cite),
                }
            )

    claims.sort(key=lambda c: (c["line"], c["type"]))
    return claims


def score_evidence_density(text: str) -> Dict[str, Any]:
    """Returns {evidence_score 0-100, total_claims, supported, unsupported, by_type}."""
    claims = find_claims(text)
    total = len(claims)
    supported = sum(1 for c in claims if c["has_citation"])
    unsupported = total - supported

    by_type: Dict[str, Dict[str, int]] = {}
    for c in claims:
        slot = by_type.setdefault(
            c["type"], {"total": 0, "supported": 0, "unsupported": 0}
        )
        slot["total"] += 1
        if c["has_citation"]:
            slot["supported"] += 1
        else:
            slot["unsupported"] += 1

    if total == 0:
        score = 100.0
    else:
        # Weight by confidence so high-confidence unsupported claims hurt more.
        penalty = sum(c["confidence"] for c in claims if not c["has_citation"])
        max_penalty = sum(
            _confidence_for(c["type"], False) for c in claims
        ) or 1.0
        score = max(0.0, 100.0 * (1.0 - penalty / max_penalty))

    return {
        "evidence_score": round(score, 1),
        "total_claims": total,
        "supported": supported,
        "unsupported": unsupported,
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="WG 15 Claim Validator — flag unsupported claims"
    )
    parser.add_argument("--file", "-f", help="Path to text file to analyze")
    parser.add_argument("--text", "-t", help="Text string to analyze")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    else:
        content = sys.stdin.read()

    density = score_evidence_density(content)
    claims = find_claims(content)
    result = {"density": density, "claims": claims}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Evidence score : {density['evidence_score']}/100")
        print(f"Total claims   : {density['total_claims']}")
        print(f"  supported    : {density['supported']}")
        print(f"  unsupported  : {density['unsupported']}")
        print("By type:")
        for t, stats in density["by_type"].items():
            print(
                f"  {t:14s} total={stats['total']:3d} "
                f"supported={stats['supported']:3d} "
                f"unsupported={stats['unsupported']:3d}"
            )
        print("\nClaims:")
        for c in claims:
            marker = "OK " if c["has_citation"] else "!! "
            print(
                f"  {marker}line {c['line']:4d} [{c['type']:11s}] "
                f"conf={c['confidence']:.2f}  {c['claim_text']!r}"
            )
