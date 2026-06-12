#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Portion Marking Checker — DoDM 5200.01 compliance validation.

Deterministic (no LLM). Validates classification portion marks in documents:
  - If any paragraph marked, ALL must be marked (DoDM 5200.01 Vol 2 Ch 3)
  - Derives document banner from highest portion mark (high-water mark)
  - Auto-generates Classification Authority Block (designation indicator)
  - Flags contradictions (U paragraph referencing classified content)

Entry point: check_portion_markings(text: str) -> dict
"""

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Classification hierarchy — ordered lowest → highest
# ---------------------------------------------------------------------------

CLASSIFICATION_ORDER = [
    "U",       # Unclassified
    "FOUO",    # For Official Use Only (legacy, superseded by CUI)
    "CUI",     # Controlled Unclassified Information
    "C",       # Confidential
    "S",       # Secret
    "TS",      # Top Secret
    "TS/SCI",  # Top Secret // Sensitive Compartmented Information
]

CLASSIFICATION_BANNERS = {
    "U":      "UNCLASSIFIED",
    "FOUO":   "CUI // FOUO",
    "CUI":    "CUI // SP-CTI",
    "C":      "CONFIDENTIAL",
    "S":      "SECRET",
    "TS":     "TOP SECRET",
    "TS/SCI": "TOP SECRET//SCI",
}

# Portion mark at start of paragraph: (U), (C), (S), (TS), (CUI), (FOUO),
# (TS//SCI), (S//NF), (CUI//SP-CTI), etc.
# We capture the base level; suffixes like //NF, //SP-CTI are stored separately.
_PORTION_RE = re.compile(
    r"^\s*\((?P<level>TS(?://SCI)?|S|C|CUI(?://[A-Z\-]+)?|FOUO|U)(?P<suffix>//[A-Z0-9/\-]+)?\)",
    re.IGNORECASE,
)

# Keyword patterns that indicate classified content (used for contradiction check)
_CLASSIFIED_INDICATORS: dict[str, list[str]] = {
    "C": [r"\bCONFIDENTIAL\b"],
    "S": [r"\bSECRET\b(?!\s*//)", r"\b(?:classified|NOFORN)\b"],
    "TS": [r"\bTOP\s+SECRET\b", r"\b(?:TS|SI|TK)\b"],
    "TS/SCI": [r"\bSCI\b", r"\bSAP\b", r"\bSPECIAL\s+ACCESS\b", r"\bCOMPARTMENT(?:ED|AL)?\b"],
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_level(raw: str) -> str:
    """Canonicalize a raw portion mark level string."""
    upper = raw.upper().strip()
    # Normalize TS//SCI variants
    if upper.startswith("TS") and "SCI" in upper:
        return "TS/SCI"
    # Normalize CUI variants (CUI//SP-CTI → CUI)
    if upper.startswith("CUI"):
        return "CUI"
    return upper


def _level_rank(level: str | None) -> int:
    """Return sort index for a classification level (higher = more sensitive)."""
    if level is None:
        return -1
    norm = _normalize_level(level)
    try:
        return CLASSIFICATION_ORDER.index(norm)
    except ValueError:
        return 0  # Unknown marking — treat as Unclassified


def _split_paragraphs(text: str) -> list[str]:
    """Split document text into non-empty paragraphs on blank lines."""
    parts = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_mark(para: str) -> tuple[bool, str | None]:
    """Extract portion mark from beginning of a paragraph.

    Returns:
        (has_mark, normalized_level | None)
    """
    m = _PORTION_RE.match(para)
    if not m:
        return False, None
    return True, _normalize_level(m.group("level"))


def _find_contradictions(para_text: str, marked_level: str | None) -> list[str]:
    """Detect content keywords that exceed the paragraph's stated classification.

    A (U) paragraph that contains "SECRET" is a contradiction.  Only fires
    when content keywords imply a HIGHER level than the portion mark.
    """
    if marked_level is None:
        return []

    marked_rank = _level_rank(marked_level)
    found = []

    for content_level, patterns in _CLASSIFIED_INDICATORS.items():
        content_rank = _level_rank(content_level)
        if content_rank <= marked_rank:
            continue  # Content level is not higher than mark — no contradiction
        for pat in patterns:
            if re.search(pat, para_text, re.IGNORECASE):
                clean_pat = pat.replace("\\b", "").replace("\\s+", " ").strip()
                found.append(
                    f"Marked ({marked_level}) but contains apparent "
                    f"{content_level}-level keyword (pattern: {clean_pat})"
                )
                break  # One hit per content level is sufficient

    return found


def _build_designation_block(highest_level: str | None, derived_banner: str) -> str:
    """Generate a Classification Authority Block (CAB / designation indicator).

    Required on all classified documents per DoDM 5200.01 Vol 2, Para 4.
    Returns empty string for Unclassified documents.
    """
    if highest_level is None or highest_level in ("U", "FOUO", "CUI"):
        return ""

    year = datetime.now(timezone.utc).year

    if highest_level in ("TS", "TS/SCI"):
        declassify_year = year + 50
        reason_code = "1.4(a)(d)"
    elif highest_level == "S":
        declassify_year = year + 25
        reason_code = "1.4(a)"
    else:  # C
        declassify_year = year + 10
        reason_code = "1.4(a)"

    lines = [
        f"Overall Classification: {derived_banner}",
        "Classified By: [ORIGINATOR — fill in]",
        "Derived From: Multiple Sources",
        f"Declassify On: {declassify_year}0101",
        f"Reason: E.O. 13526, Section {reason_code}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_portion_markings(text: str) -> dict:
    """Run DoDM 5200.01 portion marking validation on document text.

    Args:
        text: Full document text, optionally containing portion marks such as
              (U), (S), (TS), (CUI), (FOUO), (TS//SCI) at paragraph starts.

    Returns:
        dict with WriteGuard dimension schema keys:
          status          — "ok" | "violations" | "no_marks"
          score           — int 0-100 compliance score
          findings        — list[dict] in WriteGuard unified format
          violations      — list[dict] with rule, severity, message, paragraphs
          paragraphs      — list[dict] per-paragraph analysis
          derived_banner  — str  highest-level banner e.g. "SECRET"
          designation_block — str  Classification Authority Block text
          has_marks       — bool  at least one paragraph has a portion mark
          all_marked      — bool  all paragraphs are marked (when has_marks)
          highest_level   — str | None  normalized highest classification found
          marked_count    — int
          unmarked_count  — int
          total_paragraphs — int
    """
    empty_result = {
        "status": "no_marks",
        "score": 100,
        "findings": [],
        "violations": [],
        "paragraphs": [],
        "derived_banner": "UNCLASSIFIED",
        "designation_block": "",
        "has_marks": False,
        "all_marked": True,
        "highest_level": None,
        "marked_count": 0,
        "unmarked_count": 0,
        "total_paragraphs": 0,
    }

    if not text or not text.strip():
        return empty_result

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return empty_result

    # ── Per-paragraph analysis ───────────────────────────────────────────────
    para_results: list[dict] = []
    for idx, para in enumerate(paragraphs, start=1):
        has_mark, level = _extract_mark(para)
        contradictions = _find_contradictions(para, level) if has_mark else []
        para_results.append(
            {
                "index": idx,
                "text": (para[:120] + "...") if len(para) > 120 else para,
                "marked": has_mark,
                "level": level,
                "contradictions": contradictions,
            }
        )

    marked_paras = [p for p in para_results if p["marked"]]
    unmarked_paras = [p for p in para_results if not p["marked"]]
    has_marks = len(marked_paras) > 0

    # ── High-water mark ──────────────────────────────────────────────────────
    levels_found = [p["level"] for p in marked_paras if p["level"]]
    if levels_found:
        highest_level = max(levels_found, key=_level_rank)
    else:
        highest_level = None

    derived_banner = CLASSIFICATION_BANNERS.get(highest_level or "U", "UNCLASSIFIED")

    # ── Violation detection ──────────────────────────────────────────────────
    violations: list[dict] = []

    # Rule 1 — DoDM 5200.01 Vol 2 Ch 3: all-or-nothing portion marking
    if has_marks and unmarked_paras:
        violations.append(
            {
                "rule": "DoDM 5200.01 Vol 2 Ch 3",
                "severity": "critical",
                "message": (
                    f"Inconsistent portion marking: {len(marked_paras)} paragraph(s) carry "
                    f"marks but {len(unmarked_paras)} are unmarked. "
                    "When any paragraph is marked, all paragraphs must be marked."
                ),
                "affected_paragraphs": [p["index"] for p in unmarked_paras],
            }
        )

    # Rule 2 — Contradiction: content exceeds paragraph's classification level
    for para in para_results:
        for contradiction in para["contradictions"]:
            violations.append(
                {
                    "rule": "DoDM 5200.01 — Classification Contradiction",
                    "severity": "high",
                    "message": f"Paragraph {para['index']}: {contradiction}",
                    "affected_paragraphs": [para["index"]],
                }
            )

    # ── WriteGuard unified findings format ───────────────────────────────────
    findings: list[dict] = []
    for v in violations:
        findings.append(
            {
                "category": "portion_marking",
                "severity": v["severity"],
                "message": v["message"],
                "suggestion": (
                    "Add portion marks to every paragraph per DoDM 5200.01 Vol 2 Ch 3"
                    if v["severity"] == "critical"
                    else "Verify paragraph content does not reveal information above its stated classification level"
                ),
            }
        )

    # ── Compliance score ─────────────────────────────────────────────────────
    if not has_marks:
        score = 100
        status = "no_marks"
    elif not violations:
        score = 100
        status = "ok"
    else:
        critical_count = sum(1 for v in violations if v["severity"] == "critical")
        high_count = sum(1 for v in violations if v["severity"] == "high")
        deduction = (critical_count * 30) + (high_count * 15)
        score = max(0, 100 - deduction)
        status = "violations"

    designation_block = _build_designation_block(highest_level, derived_banner)

    return {
        "status": status,
        "score": score,
        "findings": findings,
        "violations": violations,
        "paragraphs": para_results,
        "derived_banner": derived_banner,
        "designation_block": designation_block,
        "has_marks": has_marks,
        "all_marked": len(unmarked_paras) == 0,
        "highest_level": highest_level,
        "marked_count": len(marked_paras),
        "unmarked_count": len(unmarked_paras),
        "total_paragraphs": len(paragraphs),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="DoDM 5200.01 portion marking validator"
    )
    parser.add_argument("--file", "-f", help="Path to text file to analyze")
    parser.add_argument("--text", "-t", help="Text string to analyze")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 if violations found",
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            content = fh.read()
    elif args.text:
        content = args.text
    else:
        content = sys.stdin.read()

    result = check_portion_markings(content)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Status         : {result['status']}")
        print(f"Score          : {result['score']}/100")
        print(f"Has marks      : {result['has_marks']}")
        print(f"All marked     : {result['all_marked']}")
        print(f"Derived banner : {result['derived_banner']}")
        print(f"Violations     : {len(result['violations'])}")
        for v in result["violations"]:
            print(f"  [{v['severity'].upper()}] {v['message']}")
        if result["designation_block"]:
            print("\nDesignation Indicator Block:")
            print(result["designation_block"])

    if args.gate and result["violations"]:
        sys.exit(1)
