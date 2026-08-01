#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Market Reflex — track marketplace module usage and suggest improvements.

Queries marketplace license usage, feedback data, and module health to
generate improvement suggestions as GKP artifacts.

Anomaly detection replaces hardcoded thresholds (aiify-opp-5308):
  - Low-rating detection uses z-score against the observed rating distribution.
  - Feedback-gap detection uses median installs as the adaptive threshold.
  - Optional LLM triage via LLMRouter("anomaly_detection") enriches findings.
  - Graceful statistical fallback keeps this GREEN / air-gap safe.

GREEN tier (read-only analytics).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import json
import re
import sys
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, list_tables  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_module_stats() -> List[Dict[str, Any]]:
    """Query marketplace module usage statistics."""
    conn = get_connection()
    try:
        # Check if marketplace tables exist. Uses the shared backend-aware
        # list_tables() + a Python prefix filter: the previous inline
        # ``sqlite_master ... LIKE 'mkt_%'`` bypassed translate_sql on PG (the
        # rewritten query referenced a non-existent ``name`` column and raised),
        # so this probe silently returned [] on every PostgreSQL stack.
        tables = [t for t in list_tables(conn) if t.startswith("mkt_")]
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


def _distribution_stats(values: List[float]) -> Dict[str, float]:
    """Return mean and population std-dev for a numeric list (empty-safe)."""
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "median": 0.0, "n": 0}
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    sorted_v = sorted(values)
    mid = n // 2
    median = sorted_v[mid] if n % 2 == 1 else (sorted_v[mid - 1] + sorted_v[mid]) / 2.0
    return {"mean": mean, "std": sqrt(variance), "median": median, "n": n}


def _adaptive_thresholds(stats: List[Dict], feedback: Dict) -> Dict[str, float]:
    """Compute adaptive anomaly thresholds from observed data distributions.

    Returns:
        rating_threshold   — modules below this are flagged as low-rated
        min_feedback_count — minimum reviews before a module is judged
        installs_threshold — installs above this trigger feedback-gap flag
        method             — how thresholds were derived
    """
    ratings = [
        m["avg_rating"]
        for m in feedback.get("details", [])
        if m.get("count", 0) >= 1
    ]
    installs = [m["installs"] for m in stats]

    r_stats = _distribution_stats(ratings)
    i_stats = _distribution_stats([float(x) for x in installs])

    if r_stats["n"] >= 3:
        # Z-score: flag anything more than 1 std-dev below the mean
        rating_threshold = max(1.0, r_stats["mean"] - r_stats["std"])
        min_feedback_count = max(1, round(r_stats["n"] * 0.1))
        method = "z_score"
    elif r_stats["n"] >= 1:
        # Too few points for z-score — use 80 % of the observed mean
        rating_threshold = max(1.0, r_stats["mean"] * 0.8)
        min_feedback_count = 1
        method = "relative"
    else:
        # No rating data at all — neutral defaults (no false positives)
        rating_threshold = 1.0
        min_feedback_count = 1
        method = "fallback"

    if i_stats["n"] >= 1:
        # Modules above the median install count are "popular" — expect feedback
        installs_threshold = max(1.0, i_stats["median"])
    else:
        installs_threshold = 1.0

    return {
        "rating_threshold": round(rating_threshold, 3),
        "min_feedback_count": min_feedback_count,
        "installs_threshold": round(installs_threshold, 3),
        "method": method,
        "rating_stats": r_stats,
        "install_stats": i_stats,
    }


def _llm_triage(anomalies: List[Dict], thresholds: Dict) -> Optional[List[Dict]]:
    """Optional LLM enrichment of detected anomalies via anomaly_detection routing.

    Returns enriched anomaly list on success, None on any failure (air-gap safe).
    """
    if not anomalies:
        return None
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        prompt = (
            "You are a marketplace health analyst.\n\n"
            "Adaptive thresholds (derived from live data):\n"
            f"{json.dumps(thresholds, indent=2)}\n\n"
            "Detected anomalies:\n"
            f"{json.dumps(anomalies, indent=2)}\n\n"
            "For each anomaly output a JSON array of objects with keys:\n"
            "  module, severity (low/medium/high), root_cause (one sentence), "
            "  recommended_action (one sentence).\n"
            "Reply ONLY with the JSON array — no markdown, no extra text."
        )
        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a marketplace anomaly triage engine. Reply only with JSON.",
            agent_id="market-reflex-triage",
            classification="CUI",
            max_tokens=1024,
            effort="low",
        )
        response = router.invoke("anomaly_detection", request)
        raw = response.content or ""
        text = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return None


def _generate_suggestions(stats: List[Dict], feedback: Dict) -> List[Dict[str, str]]:
    """Generate improvement suggestions using adaptive anomaly detection."""
    suggestions = []
    thresholds = _adaptive_thresholds(stats, feedback)

    rating_threshold = thresholds["rating_threshold"]
    min_feedback_count = thresholds["min_feedback_count"]
    installs_threshold = thresholds["installs_threshold"]
    method = thresholds["method"]

    # --- Low-rating anomalies ---
    raw_anomalies: List[Dict] = []
    for mod in feedback.get("details", []):
        if mod.get("avg_rating", 5.0) < rating_threshold and mod.get("count", 0) >= min_feedback_count:
            raw_anomalies.append({"type": "low_rating", "module": mod["slug"],
                                   "avg_rating": mod["avg_rating"], "count": mod["count"]})

    # --- Feedback-gap anomalies ---
    slugs_with_feedback = {m["slug"] for m in feedback.get("details", [])}
    for mod in stats:
        if mod["slug"] not in slugs_with_feedback and mod["installs"] > installs_threshold:
            raw_anomalies.append({"type": "feedback_gap", "module": mod["slug"],
                                   "installs": mod["installs"]})

    # Optional LLM enrichment
    enriched = _llm_triage(raw_anomalies, {k: v for k, v in thresholds.items()
                                            if k not in ("rating_stats", "install_stats")})

    if enriched:
        _TYPE_MAP = {"low_rating": "quality_improvement", "feedback_gap": "feedback_gap"}
        anom_type_by_module = {
            a["module"]: _TYPE_MAP.get(a.get("type", ""), a.get("type", "anomaly"))
            for a in raw_anomalies
        }
        for item in enriched:
            mod = item.get("module", "unknown")
            suggestions.append({
                "type": anom_type_by_module.get(mod, "anomaly"),
                "module": mod,
                "reason": item.get("root_cause", "Anomaly detected"),
                "action": item.get("recommended_action", "Investigate"),
                "severity": item.get("severity", "medium"),
                "detection_method": f"{method}+llm",
            })
    else:
        # Pure statistical suggestions
        for anom in raw_anomalies:
            if anom["type"] == "low_rating":
                suggestions.append({
                    "type": "quality_improvement",
                    "module": anom["module"],
                    "reason": (
                        f"Rating {anom['avg_rating']} is below the adaptive threshold "
                        f"{rating_threshold:.2f} (method: {method})"
                    ),
                    "action": "Review feedback comments, prioritize fixes",
                    "detection_method": method,
                })
            else:
                suggestions.append({
                    "type": "feedback_gap",
                    "module": anom["module"],
                    "reason": (
                        f"{anom['installs']} installs exceeds adaptive threshold "
                        f"{installs_threshold:.1f} but no feedback collected"
                    ),
                    "action": "Add feedback prompt to renewal flow",
                    "detection_method": method,
                })

    # If no marketplace data exists, suggest bootstrapping
    if not stats:
        suggestions.append({
            "type": "bootstrap",
            "module": "all",
            "reason": "No marketplace activity detected",
            "action": "Enable marketplace and register initial modules",
            "detection_method": "fallback",
        })

    return suggestions


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Market Reflex."""
    stats = _get_module_stats()
    feedback = _get_feedback_summary()
    thresholds = _adaptive_thresholds(stats, feedback)
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
            "anomaly_detection": {
                "method": thresholds["method"],
                "rating_threshold": thresholds["rating_threshold"],
                "installs_threshold": thresholds["installs_threshold"],
                "min_feedback_count": thresholds["min_feedback_count"],
            },
        },
    }
