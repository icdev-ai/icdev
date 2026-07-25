# CUI // SP-CTI
"""Genesis Reflex — FathomDesk News Pattern Detection.

Runs NewsPatternAnalyzer on a cadence to surface regime_shift and crackdown
signals from the FathomDesk news pipeline.  Detected patterns are promoted
to GKP artifacts for kanban suggestion.

GREEN tier (read+write patterns, no LLM).  Air-gap safe.
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"


import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.trading.news.pattern_analyzer import NewsPatternAnalyzer  # noqa: E402
from tools.trading.news.pattern_db import list_patterns  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the FathomDesk News Patterns reflex."""
    try:
        analyzer = NewsPatternAnalyzer()
        new_patterns = analyzer.analyze_all()
    except Exception as e:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(e), "patterns_emitted": 0},
        }

    # Export patterns as GKP artifacts
    exported = 0
    try:
        from tools.genesis.promoter import export_gkp  # noqa: E402

        for p in new_patterns[:5]:
            result = export_gkp(
                reflex="fathomdesk_news_patterns",
                artifact_type="capability_update",
                payload={
                    "title": f"News Pattern: {p['pattern_type']} / {p['category']} [{p['severity']}]",
                    "description": p.get("recommendation", ""),
                    "source": "fathomdesk_news_patterns",
                },
                confidence=0.75,
                evidence={"pattern_type": p["pattern_type"], "category": p["category"]},
            )
            if result.get("status") == "exported":
                exported += 1
    except Exception:
        pass

    recent = list_patterns(limit=20)
    return {
        "success": True,
        "metric_value": float(len(new_patterns)),
        "details": {
            "patterns_emitted": len(new_patterns),
            "patterns_exported": exported,
            "recent_pattern_count": len(recent),
            "run_at": _utcnow_iso(),
        },
    }
