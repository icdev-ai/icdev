# CUI // SP-CTI
"""WriteGuard STRIDE Coverage Checker (WG 25).

Deterministic keyword-based coverage assessment for STRIDE threat models.

STRIDE categories:
    S — Spoofing
    T — Tampering
    R — Repudiation
    I — Information Disclosure
    D — Denial of Service
    E — Elevation of Privilege

For each present category we additionally check for:
    - Threat description (attacker / adversary / scenario language)
    - Mitigation (mitigate / control / defense / countermeasure / prevent)
    - Residual risk (residual risk / acceptable / accept risk / low risk / high risk)

Usage:
    from tools.writing.stride_checker import stride_coverage

    result = stride_coverage(text)

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
# Category catalogues
# ---------------------------------------------------------------------------

_STRIDE: Dict[str, List[str]] = {
    "Spoofing": [
        "spoofing", "identity", "authentication bypass", "credential theft", "session hijack",
    ],
    "Tampering": [
        "tampering", "modification", "data integrity", "checksum", "mitm", "man-in-the-middle",
    ],
    "Repudiation": [
        "repudiation", "non-repudiation", "audit log", "signed log", "accountability", "deniability",
    ],
    "Information Disclosure": [
        "information disclosure", "data leak", "exfiltration", "eavesdropping", "sniffing",
    ],
    "Denial of Service": [
        "denial of service", "dos", "ddos", "availability", "flooding", "resource exhaustion",
    ],
    "Elevation of Privilege": [
        "privilege escalation", "elevation of privilege", "eop", "root access", "admin takeover",
    ],
}

_THREAT_MARKERS = ("attacker", "adversary", "bad actor", "threat actor", "scenario")
_MITIGATION_MARKERS = ("mitigate", "control", "defense", "countermeasure", "prevent")
_RESIDUAL_MARKERS = ("residual risk", "acceptable", "accept risk", "low risk", "high risk")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _category_hits(text_lower: str, keywords: List[str]) -> int:
    hits = 0
    for kw in keywords:
        # word-boundary for short tokens, substring for multi-word/punct tokens
        if " " in kw or "-" in kw:
            if kw in text_lower:
                hits += 1
        else:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                hits += 1
    return hits


def _has_any(text_lower: str, markers: tuple) -> bool:
    return any(m in text_lower for m in markers)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def stride_coverage(text: str) -> Dict[str, Any]:
    """Assess STRIDE coverage and completeness."""
    text_lower = text.lower()

    # For completeness markers we want to evaluate per-category by looking at the
    # paragraph/window around each hit. Simple approach: compute global markers;
    # a category is "complete" if it is present AND the doc contains at least one
    # of each marker family within a 400-char window of any category keyword.
    paragraphs = re.split(r"\n\s*\n", text_lower)

    present: List[str] = []
    missing: List[str] = []
    per_category: Dict[str, Dict[str, Any]] = {}

    for category, keywords in _STRIDE.items():
        hits = _category_hits(text_lower, keywords)
        if hits == 0:
            missing.append(category)
            per_category[category] = {
                "present": False,
                "keyword_hits": 0,
                "threat_described": False,
                "mitigation_described": False,
                "residual_risk_described": False,
                "complete": False,
            }
            continue

        # Find paragraphs that mention any keyword and union their text
        relevant_text = ""
        for para in paragraphs:
            if any((kw in para) for kw in keywords):
                relevant_text += " " + para

        threat = _has_any(relevant_text, _THREAT_MARKERS)
        mitigation = _has_any(relevant_text, _MITIGATION_MARKERS)
        residual = _has_any(relevant_text, _RESIDUAL_MARKERS)
        complete = threat and mitigation and residual

        present.append(category)
        per_category[category] = {
            "present": True,
            "keyword_hits": hits,
            "threat_described": threat,
            "mitigation_described": mitigation,
            "residual_risk_described": residual,
            "complete": complete,
        }

    # Score
    score = 100
    score -= len(missing) * 15
    incomplete_count = sum(
        1 for c, info in per_category.items()
        if info["present"] and not info["complete"]
    )
    score -= incomplete_count * 5
    score = max(0, min(100, score))

    # Issues list
    issues: List[Dict[str, Any]] = []
    for cat in missing:
        issues.append({
            "category": cat,
            "issue": "missing_category",
            "detail": f"STRIDE category '{cat}' has no keyword hits",
        })
    for cat, info in per_category.items():
        if info["present"] and not info["complete"]:
            gaps = []
            if not info["threat_described"]:
                gaps.append("threat description")
            if not info["mitigation_described"]:
                gaps.append("mitigation")
            if not info["residual_risk_described"]:
                gaps.append("residual risk")
            issues.append({
                "category": cat,
                "issue": "incomplete_category",
                "detail": f"Missing: {', '.join(gaps)}",
            })

    return {
        "present_categories": present,
        "missing_categories": missing,
        "per_category_completeness": per_category,
        "overall_coverage_score": score,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WriteGuard STRIDE Coverage Checker (WG 25)")
    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    result = stride_coverage(text)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Coverage score: {result['overall_coverage_score']}/100")
        print(f"Present: {result['present_categories']}")
        print(f"Missing: {result['missing_categories']}")
        for cat, info in result["per_category_completeness"].items():
            if info["present"]:
                print(f"  {cat}: complete={info['complete']} "
                      f"(threat={info['threat_described']}, "
                      f"mitigation={info['mitigation_described']}, "
                      f"residual={info['residual_risk_described']})")
        for i in result["issues"]:
            print(f"  [{i['issue']}] {i['category']}: {i['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
