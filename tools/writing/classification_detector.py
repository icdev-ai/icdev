# CUI // SP-CTI
"""WriteGuard Classification Indicator Scanner (WG 11a).

Deterministic DoD/IC classification detection per DoDM 5200.01 + EO 13526 + CUI Registry.

DISCLAIMER: Classification detection is an aid only. All classification decisions
require Original Classification Authority (OCA) or trained Derivative Classifier
review per EO 13526.

Usage:
    from tools.writing.classification_detector import scan_indicators, classify_paragraph

    result = scan_indicators(text)
    # result = {indicators_found: [...], suggested_classification: str,
    #           suggested_banner: str, confidence_score: float, disclaimer: str}

    para_result = classify_paragraph("The system has an RCS of -20 dBsm.")
    # para_result = {suggested_mark: "(CUI)", level: "CUI", subcategory: "SP-CTI",
    #                indicators: [...], confidence: "HIGH"}

CUI // SP-CTI
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "args" / "writeguard_classification.yaml"

_LEVEL_RANK = {"U": 0, "CUI": 1, "C": 2, "S": 3, "TS": 4, "TS//SCI": 5}
_LEVEL_BANNER = {
    "U": "UNCLASSIFIED",
    "CUI": "CUI",
    "C": "CONFIDENTIAL",
    "S": "SECRET",
    "TS": "TOP SECRET",
    "TS//SCI": "TOP SECRET//SCI",
}
_LEVEL_MARK = {
    "U": "(U)",
    "CUI": "(CUI)",
    "C": "(C)",
    "S": "(S)",
    "TS": "(TS)",
    "TS//SCI": "(TS//SCI)",
}

DISCLAIMER = (
    "Classification detection is an aid only. All classification decisions require "
    "Original Classification Authority (OCA) or trained Derivative Classifier review "
    "per EO 13526."
)


def _load_config() -> Dict[str, Any]:
    """Load classification indicator config from YAML."""
    try:
        import yaml
    except ImportError:
        logger.warning("pyyaml not installed — using built-in patterns only")
        return {"indicators": []}
    if not _CONFIG_PATH.exists():
        logger.warning("Classification config not found: %s", _CONFIG_PATH)
        return {"indicators": []}
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to load classification config: %s", exc)
        return {"indicators": []}


def _highest_level(levels: List[str]) -> str:
    """Return the highest-rank level from a list. Returns 'U' if empty."""
    if not levels:
        return "U"
    return max(levels, key=lambda lv: _LEVEL_RANK.get(lv, 0))


def scan_indicators(text: str) -> Dict[str, Any]:
    """Scan text for classification indicators.

    Returns:
        dict with keys:
            indicators_found: list of {category, match, line, suggested_level,
                                       subcategory, confidence, reason}
            suggested_classification: str (highest level detected)
            suggested_banner: str (full banner string)
            confidence_score: float (0-1, mean of match confidences)
            disclaimer: str
    """
    if not text:
        return {
            "indicators_found": [],
            "indicator_count": 0,
            "suggested_classification": "U",
            "suggested_banner": "UNCLASSIFIED",
            "confidence_score": 1.0,
            "confidence_breakdown": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "human_review_required": False,
            "disclaimer": DISCLAIMER,
        }

    config = _load_config()
    indicators = config.get("indicators", []) + config.get("custom_indicators", [])

    findings: List[Dict[str, Any]] = []
    levels_found: List[str] = []
    confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for ind in indicators:
        pattern = ind.get("pattern")
        if not pattern:
            continue
        try:
            regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            logger.warning("Invalid pattern in config: %s — %s", pattern, exc)
            continue
        for m in regex.finditer(text):
            # Find line number
            line_no = text[: m.start()].count("\n") + 1
            suggested_level = ind.get("suggested_level")
            findings.append(
                {
                    "category": ind.get("category", "UNKNOWN"),
                    "match": m.group(0)[:80],
                    "line": line_no,
                    "position": m.start(),
                    "suggested_level": suggested_level,
                    "subcategory": ind.get("subcategory"),
                    "confidence": ind.get("confidence", "MEDIUM"),
                    "reason": ind.get("reason", ""),
                }
            )
            if suggested_level and suggested_level in _LEVEL_RANK:
                levels_found.append(suggested_level)
                conf = ind.get("confidence", "MEDIUM")
                confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

    # Determine highest suggested classification
    suggested_class = _highest_level(levels_found)

    # Build banner (subcategory appended for CUI)
    banner = _LEVEL_BANNER.get(suggested_class, "UNCLASSIFIED")
    if suggested_class == "CUI":
        # Find most specific CUI subcategory
        cui_subs = [
            f.get("subcategory")
            for f in findings
            if f.get("suggested_level") == "CUI" and f.get("subcategory")
        ]
        if cui_subs:
            # Use most frequent subcategory
            from collections import Counter

            top_sub = Counter(cui_subs).most_common(1)[0][0]
            banner = f"CUI//{top_sub}"

    # Confidence: weighted by confidence level
    total = sum(confidence_counts.values())
    if total > 0:
        confidence_score = (
            confidence_counts["HIGH"] * 1.0
            + confidence_counts["MEDIUM"] * 0.6
            + confidence_counts["LOW"] * 0.3
        ) / total
    else:
        confidence_score = 1.0  # No indicators = UNCLASSIFIED with high confidence

    return {
        "indicators_found": findings,
        "indicator_count": len(findings),
        "suggested_classification": suggested_class,
        "suggested_banner": banner,
        "confidence_score": round(confidence_score, 2),
        "confidence_breakdown": confidence_counts,
        "human_review_required": confidence_counts["LOW"] > 0
        or suggested_class in ("S", "TS", "TS//SCI"),
        "disclaimer": DISCLAIMER,
    }


def classify_paragraph(paragraph: str) -> Dict[str, Any]:
    """Classify a single paragraph.

    Returns:
        dict with: suggested_mark, level, subcategory, indicators, confidence
    """
    if not paragraph or not paragraph.strip():
        return {
            "suggested_mark": "(U)",
            "level": "U",
            "subcategory": None,
            "indicators": [],
            "confidence": "HIGH",
        }

    result = scan_indicators(paragraph)
    level = result["suggested_classification"]

    # Determine overall confidence for this paragraph
    indicators = result["indicators_found"]
    if not indicators:
        confidence = "HIGH"  # no indicators = confident UNCLASSIFIED
    else:
        # Worst-case confidence
        confs = [i["confidence"] for i in indicators if i.get("suggested_level") == level]
        if "HIGH" in confs:
            confidence = "HIGH"
        elif "MEDIUM" in confs:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    # Find subcategory (most specific CUI subcategory if CUI)
    subcategory = None
    if level == "CUI":
        cui_subs = [
            i.get("subcategory")
            for i in indicators
            if i.get("suggested_level") == "CUI" and i.get("subcategory")
        ]
        if cui_subs:
            subcategory = cui_subs[0]

    mark = _LEVEL_MARK.get(level, "(U)")
    if subcategory:
        mark = f"(CUI//{subcategory})"

    return {
        "suggested_mark": mark,
        "level": level,
        "subcategory": subcategory,
        "indicators": indicators,
        "confidence": confidence,
    }


def main():
    """CLI for testing."""
    import argparse
    import json
    import sys

    # Cross-platform UTF-8 stdout for Windows consoles
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, Exception):
        pass

    parser = argparse.ArgumentParser(description="WriteGuard Classification Detector")
    parser.add_argument("--text", help="Text to scan")
    parser.add_argument("--file", help="File to scan")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()

    result = scan_indicators(text)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Suggested: {result['suggested_banner']}")
        print(f"Confidence: {result['confidence_score']:.2f}")
        print(f"Indicators: {result['indicator_count']}")
        for ind in result["indicators_found"][:10]:
            print(
                f"  [{ind['confidence']}] {ind['category']}: {ind['match'][:50]} "
                f"→ suggest ({ind['suggested_level']})"
            )
        print(f"\n{result['disclaimer']}")


if __name__ == "__main__":
    main()
