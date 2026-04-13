# CUI // SP-CTI
"""WriteGuard Auto-Portion-Marker (WG 11b).

Automatically apply DoD/IC portion marks to each paragraph based on
classification_detector results. Derives high-water-mark banner per
DoDM 5200.01 Vol 2.

DISCLAIMER: Classification detection is an aid only. All classification
decisions require OCA / Derivative Classifier review per EO 13526.

Usage:
    from tools.writing.auto_marker import auto_mark_document

    result = auto_mark_document(text)
    # result = {
    #   paragraphs: [{text, detected_level, mark, indicators, confidence}],
    #   banner: "SECRET//NOFORN",
    #   designation_block: "Classified By: ...",
    #   compilation_warning: bool,
    #   confidence_summary: {HIGH: N, MEDIUM: N, LOW: N},
    #   human_review_required: bool,
    # }

CUI // SP-CTI
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from tools.writing.classification_detector import (
    DISCLAIMER,
    _LEVEL_BANNER,
    _LEVEL_RANK,
    classify_paragraph,
)

# Compilation-risk threshold: N+ CUI//SP-CTI paragraphs on same system
# may aggregate to CONFIDENTIAL or SECRET (document-specific)
_COMPILATION_THRESHOLD = 3


def _split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs (blank line separated)."""
    paras = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paras if p.strip()]


def _highest_level_of(levels: List[str]) -> str:
    """Return the highest-rank level from a list; default 'U'."""
    if not levels:
        return "U"
    return max(levels, key=lambda lv: _LEVEL_RANK.get(lv, 0))


def _derive_banner(paragraph_results: List[Dict[str, Any]]) -> str:
    """Derive high-water-mark banner + aggregate dissemination controls.

    Per DoDM 5200.01 Vol 2: banner = highest classification of any portion +
    ALL dissemination controls that appear anywhere in the document.
    """
    levels = [p.get("level", "U") for p in paragraph_results]
    highest = _highest_level_of(levels)
    banner = _LEVEL_BANNER.get(highest, "UNCLASSIFIED")

    # Collect CUI subcategories
    subcats: List[str] = []
    for p in paragraph_results:
        if p.get("level") == "CUI" and p.get("subcategory"):
            subcats.append(p["subcategory"])

    if highest == "CUI" and subcats:
        top_sub = Counter(subcats).most_common(1)[0][0]
        banner = f"CUI//{top_sub}"
    elif highest in ("C", "S", "TS", "TS//SCI"):
        # Check for dissemination controls in detected indicators
        all_indicators = []
        for p in paragraph_results:
            all_indicators.extend(p.get("indicators", []))
        controls = set()
        combined = " ".join(ind.get("match", "") for ind in all_indicators).upper()
        if "NOFORN" in combined or "//NF" in combined:
            controls.add("NOFORN")
        if "ORCON" in combined:
            controls.add("ORCON")
        if "FISA" in combined:
            controls.add("FISA")
        if controls:
            banner = f"{banner}//{'/'.join(sorted(controls))}"

    return banner


def _check_compilation_risk(
    paragraph_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check if aggregation of CUI portions could constitute classified.

    Heuristic: 3+ CUI//SP-CTI paragraphs on related topics, or 3+ CUI//PROCURE
    covering the same acquisition.
    """
    cui_sp_cti = sum(
        1 for p in paragraph_results
        if p.get("level") == "CUI" and p.get("subcategory") == "SP-CTI"
    )
    cui_procure = sum(
        1 for p in paragraph_results
        if p.get("level") == "CUI" and p.get("subcategory") == "PROCURE"
    )

    at_risk = cui_sp_cti >= _COMPILATION_THRESHOLD or cui_procure >= _COMPILATION_THRESHOLD

    reasons = []
    if cui_sp_cti >= _COMPILATION_THRESHOLD:
        reasons.append(
            f"{cui_sp_cti} CUI//SP-CTI portions — aggregation may warrant "
            "CONFIDENTIAL/SECRET review (DoDD 5200.39)"
        )
    if cui_procure >= _COMPILATION_THRESHOLD:
        reasons.append(
            f"{cui_procure} CUI//PROCURE portions — compiled source selection "
            "may be more sensitive (FAR 3.104)"
        )

    return {
        "compilation_warning": at_risk,
        "cui_sp_cti_count": cui_sp_cti,
        "cui_procure_count": cui_procure,
        "threshold": _COMPILATION_THRESHOLD,
        "reasons": reasons,
    }


def _build_designation_block(banner: str, has_classified: bool) -> str:
    """Build Classification Authority Block template (first page bottom).

    Per DoDM 5200.01 Vol 2, classified documents require:
      Classified By: [Name/Title]
      Derived From: [SCG title + date OR source document]
      Declassify On: [date YYYYMMDD OR event marker e.g., 25X1]
    """
    if not has_classified:
        return ""
    return (
        "Classified By: [NAME / POSITION / OFFICE]\n"
        "Derived From: [Security Classification Guide or Source Document]\n"
        "Declassify On: [YYYYMMDD or declassification event marker]"
    )


def auto_mark_document(text: str) -> Dict[str, Any]:
    """Auto-classify and portion-mark a document.

    Returns
    -------
    dict
        paragraphs : list of {index, text, detected_level, mark, indicators,
                              confidence, subcategory}
        banner : str (high-water-mark banner for header/footer)
        all_indicators : list (aggregate of all paragraph indicators)
        confidence_summary : {HIGH: N, MEDIUM: N, LOW: N}
        compilation_warning : bool
        compilation_details : dict
        designation_block : str (empty if UNCLASSIFIED only)
        human_review_required : bool
        disclaimer : str
        total_paragraphs : int
    """
    if not text or not text.strip():
        return {
            "paragraphs": [],
            "banner": "UNCLASSIFIED",
            "all_indicators": [],
            "confidence_summary": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "compilation_warning": False,
            "compilation_details": {},
            "designation_block": "",
            "human_review_required": False,
            "disclaimer": DISCLAIMER,
            "total_paragraphs": 0,
        }

    paragraphs = _split_paragraphs(text)
    para_results: List[Dict[str, Any]] = []
    all_indicators: List[Dict[str, Any]] = []
    conf_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for i, para in enumerate(paragraphs):
        cls = classify_paragraph(para)
        mark = cls.get("suggested_mark", "(U)")
        level = cls.get("level", "U")
        confidence = cls.get("confidence", "HIGH")
        indicators = cls.get("indicators", [])
        subcategory = cls.get("subcategory")

        para_results.append({
            "index": i,
            "text": para,
            "detected_level": level,
            "level": level,
            "mark": mark,
            "subcategory": subcategory,
            "confidence": confidence,
            "indicators": indicators,
            "indicator_count": len(indicators),
        })

        for ind in indicators:
            all_indicators.append({**ind, "paragraph_index": i})
        conf_counts[confidence] = conf_counts.get(confidence, 0) + 1

    # High-water-mark banner
    banner = _derive_banner(para_results)

    # Compilation risk
    compilation = _check_compilation_risk(para_results)

    # Designation block required for classified content
    levels = [p["level"] for p in para_results]
    has_classified = any(lv in ("C", "S", "TS", "TS//SCI") for lv in levels)
    designation_block = _build_designation_block(banner, has_classified)

    # Human review triggers
    human_review = (
        conf_counts["LOW"] > 0
        or has_classified
        or compilation["compilation_warning"]
    )

    return {
        "paragraphs": para_results,
        "banner": banner,
        "all_indicators": all_indicators,
        "confidence_summary": conf_counts,
        "compilation_warning": compilation["compilation_warning"],
        "compilation_details": compilation,
        "designation_block": designation_block,
        "human_review_required": human_review,
        "disclaimer": DISCLAIMER,
        "total_paragraphs": len(paragraphs),
    }


def format_marked_document(result: Dict[str, Any]) -> str:
    """Render a marked document as plain text with banner headers/footers."""
    if not result.get("paragraphs"):
        return ""
    banner = result.get("banner", "UNCLASSIFIED")
    lines = [banner, ""]
    for p in result["paragraphs"]:
        lines.append(f"{p['mark']} {p['text']}")
        lines.append("")
    lines.append(banner)
    if result.get("designation_block"):
        lines.append("")
        lines.append(result["designation_block"])
    return "\n".join(lines)


def main():
    """CLI entry point."""
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="WriteGuard Auto-Portion-Marker")
    parser.add_argument("--text", help="Text to mark")
    parser.add_argument("--file", help="File to mark")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--format", action="store_true", help="Output formatted marked document")
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

    result = auto_mark_document(text)

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.format:
        print(format_marked_document(result))
    else:
        print(f"Banner: {result['banner']}")
        print(f"Paragraphs: {result['total_paragraphs']}")
        print(f"Confidence: {result['confidence_summary']}")
        print(f"Compilation risk: {result['compilation_warning']}")
        print(f"Human review required: {result['human_review_required']}")
        print()
        for p in result["paragraphs"]:
            preview = p["text"][:70].replace("\n", " ")
            print(f"  [{p['mark']:>12}] {preview}{'...' if len(p['text']) > 70 else ''}")
        if result["compilation_details"].get("reasons"):
            print("\nCompilation flags:")
            for r in result["compilation_details"]["reasons"]:
                print(f"  - {r}")
        print(f"\n{result['disclaimer']}")


if __name__ == "__main__":
    main()
