#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Market Reflex — track marketplace module usage and suggest improvements.

Queries marketplace license usage, feedback data, and module health to
generate improvement suggestions as GKP artifacts.

GREEN tier (read-only analytics).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_module_stats() -> List[Dict[str, Any]]:
    """Query marketplace module usage statistics."""
    conn = get_connection()
    try:
        # Check if marketplace tables exist
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mkt_%'").fetchall()
        if not tables:
            return []

        rows = conn.execute("""
            SELECT module_slug, COUNT(*) as install_count
            FROM mkt_licenses
            GROUP BY module_slug
            ORDER BY install_count DESC
        """).fetchall()
        return [
            {
                "slug": r["module_slug"] if isinstance(r, dict) else r[0],
                "installs": r["install_count"] if isinstance(r, dict) else r[1],
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


def _get_feedback_summary() -> Dict[str, Any]:
    """Aggregate marketplace feedback."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT module_slug, AVG(rating) as avg_rating, COUNT(*) as feedback_count
            FROM mkt_feedback
            GROUP BY module_slug
        """).fetchall()
        return {
            "modules_with_feedback": len(rows),
            "details": [
                {
                    "slug": r["module_slug"] if isinstance(r, dict) else r[0],
                    "avg_rating": round(r["avg_rating"] if isinstance(r, dict) else r[1], 2),
                    "count": r["feedback_count"] if isinstance(r, dict) else r[2],
                }
                for r in rows
            ],
        }
    except Exception:
        return {"modules_with_feedback": 0, "details": []}
    finally:
        conn.close()


def _generate_suggestions(stats: List[Dict], feedback: Dict) -> List[Dict[str, str]]:
    """Generate improvement suggestions based on usage and feedback."""
    suggestions = []

    # Suggest improvements for low-rated modules
    for mod in feedback.get("details", []):
        if mod.get("avg_rating", 5) < 3.5 and mod.get("count", 0) >= 2:
            suggestions.append(
                {
                    "type": "quality_improvement",
                    "module": mod["slug"],
                    "reason": f"Low average rating ({mod['avg_rating']}) across {mod['count']} reviews",
                    "action": "Review feedback comments, prioritize fixes",
                }
            )

    # Suggest promotion for high-install modules without feedback
    slugs_with_feedback = {m["slug"] for m in feedback.get("details", [])}
    for mod in stats:
        if mod["slug"] not in slugs_with_feedback and mod["installs"] > 3:
            suggestions.append(
                {
                    "type": "feedback_gap",
                    "module": mod["slug"],
                    "reason": f"{mod['installs']} installs but no feedback collected",
                    "action": "Add feedback prompt to renewal flow",
                }
            )

    # If no marketplace data exists, suggest bootstrapping
    if not stats:
        suggestions.append(
            {
                "type": "bootstrap",
                "module": "all",
                "reason": "No marketplace activity detected",
                "action": "Enable marketplace and register initial modules",
            }
        )

    return suggestions


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Market Reflex."""
    stats = _get_module_stats()
    feedback = _get_feedback_summary()
    suggestions = _generate_suggestions(stats, feedback)

    # Export suggestions as GKP artifacts
    exported = 0
    try:
        from tools.genesis.promoter import export_gkp

        for suggestion in suggestions[:5]:  # Cap at 5 per cycle
            result = export_gkp(
                reflex="market",
                artifact_type="capability_update",
                payload={
                    "title": f"Marketplace: {suggestion['type']} — {suggestion['module']}",
                    "description": suggestion["reason"],
                    "action": suggestion["action"],
                    "source": "genesis_market",
                },
                confidence=0.6,
                evidence={"stats_count": len(stats), "feedback_count": feedback.get("modules_with_feedback", 0)},
            )
            if result.get("status") == "exported":
                exported += 1
    except Exception as e:
        print(f"  WARN: GKP export failed: {e}")

    return {
        "success": True,  # Market is informational — always succeeds
        "metric_value": float(len(suggestions)),
        "details": {
            "modules_tracked": len(stats),
            "modules_with_feedback": feedback.get("modules_with_feedback", 0),
            "suggestions_generated": len(suggestions),
            "suggestions_exported": exported,
            "suggestions": suggestions,
        },
    }
