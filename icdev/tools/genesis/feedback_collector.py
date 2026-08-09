#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Feedback Collector — pulls v1.x telemetry for v2.0 consumption (D-GEN-11).

Pull-based: v2.0 queries v1.x tables to understand what's failing,
degrading, under-tested, or in high demand.  v1.x doesn't need to
know v2.0 exists.

Usage:
    python tools/genesis/feedback_collector.py --collect --json
    python tools/genesis/feedback_collector.py --latest --json
    python tools/genesis/feedback_collector.py --summary --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.genesis.feedback_collector")

FEEDBACK_DIR = BASE_DIR / "data" / "genesis" / "feedback"

# Static fallback thresholds — used only when < 3 historical data points exist.
# Production values are computed dynamically via compute_anomaly_thresholds().
_THRESHOLD_DEFAULTS: Dict[str, float] = {
    "runtime_failures": 5.0,
    "coverage_gaps": 10.0,
    "high_demand_signals": 3.0,
    "trace_recommendations": 0.0,
    "maintainability_score_max": 50.0,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_query(query: str, params: tuple = ()) -> List[Dict]:
    """Execute a query, returning empty list on error (table may not exist)."""
    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def collect_runtime_failures(lookback_hours: int = 24) -> List[Dict]:
    """Collect recent error events from audit trail."""
    since = (_utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _safe_query(
        "SELECT id, event_type, details, created_at FROM audit_trail "
        "WHERE event_type LIKE 'error.%' AND created_at > ? ORDER BY created_at DESC LIMIT 100",
        (since,),
    )


def collect_quality_trends(lookback_hours: int = 168) -> List[Dict]:
    """Collect code quality metrics for trend analysis."""
    since = (_utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _safe_query(
        "SELECT file_path, language, avg_cyclomatic, avg_cognitive, "
        "maintainability_score, smells, created_at FROM code_quality_metrics "
        "WHERE created_at > ? ORDER BY maintainability_score ASC LIMIT 50",
        (since,),
    )


def collect_coverage_gaps(max_score: Optional[float] = None) -> List[Dict]:
    """Identify tools with anomalously low maintainability scores.

    When max_score is omitted, the threshold is computed dynamically via
    compute_anomaly_thresholds() — mean - z*std of historical DB scores —
    falling back to _THRESHOLD_DEFAULTS when history is thin.
    """
    if max_score is None:
        max_score = compute_anomaly_thresholds().get(
            "maintainability_score_max", _THRESHOLD_DEFAULTS["maintainability_score_max"]
        )
    return _safe_query(
        "SELECT file_path, maintainability_score, avg_cyclomatic, smells "
        "FROM code_quality_metrics WHERE maintainability_score < ? "
        "ORDER BY maintainability_score ASC LIMIT 30",
        (max_score,),
    )


def collect_heal_outcomes(lookback_hours: int = 168) -> List[Dict]:
    """Collect self-healing event outcomes for pattern learning."""
    since = (_utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _safe_query(
        "SELECT id, pattern_id, event_type, action_taken, outcome, "
        "created_at FROM self_healing_events "
        "WHERE created_at > ? ORDER BY created_at DESC LIMIT 50",
        (since,),
    )


def collect_demand_signals() -> List[Dict]:
    """Collect high-demand capability signals."""
    return _safe_query(
        "SELECT pain_point_text, domain_category, keywords, frequency, "
        "is_high_demand, created_at FROM pulse_demand_signals "
        "WHERE is_high_demand = 1 ORDER BY frequency DESC LIMIT 30"
    )


def collect_trace_recommendations() -> List[Dict]:
    """Collect harness trace recommendations (loop detection, etc.)."""
    return _safe_query(
        "SELECT session_id, recommendation_type, severity, message, "
        "created_at FROM harness_trace_recommendations "
        "ORDER BY created_at DESC LIMIT 20"
    )


def _read_genesis_anomaly_config() -> Dict[str, Any]:
    """Read anomaly_detection settings from args/genesis_config.yaml.

    Returns the convergence.anomaly_detection sub-dict, or {} on any failure.
    """
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    if _yaml is None or not config_path.exists():
        return {}
    try:
        data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return data.get("convergence", {}).get("anomaly_detection", {})
    except Exception:
        return {}


def compute_anomaly_thresholds(lookback_days: int = 7, z_score: float = 2.0, min_samples: int = 5) -> Dict[str, float]:
    """Compute data-driven anomaly thresholds from historical feedback files.

    Uses mean + z_score * std per metric over the last ``lookback_days`` days.
    Falls back to ``_THRESHOLD_DEFAULTS`` when fewer than ``min_samples`` data
    points exist.  Config values from ``args/genesis_config.yaml``
    (convergence.anomaly_detection) override the defaults when present.
    """
    cfg = _read_genesis_anomaly_config()
    z_score = float(cfg.get("z_score", z_score))
    min_samples = int(cfg.get("min_samples", min_samples))

    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    history = sorted(FEEDBACK_DIR.glob("feedback-*.json"))[-lookback_days:]

    buckets: Dict[str, List[float]] = {k: [] for k in _THRESHOLD_DEFAULTS if k != "maintainability_score_max"}

    for f in history:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            for key in buckets:
                val = s.get(key)
                if val is not None:
                    buckets[key].append(float(val))
        except Exception:
            continue

    thresholds: Dict[str, float] = {}
    for key, values in buckets.items():
        if len(values) >= min_samples:
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            thresholds[key] = mean + z_score * (variance ** 0.5)
        else:
            thresholds[key] = _THRESHOLD_DEFAULTS[key]

    # Compute maintainability_score_max dynamically from actual DB scores.
    # Anomalously LOW scores use mean - z*std; clamped to >= 0.0.
    # Falls back to _THRESHOLD_DEFAULTS when fewer than min_samples rows exist.
    ms_rows = _safe_query(
        "SELECT maintainability_score FROM code_quality_metrics "
        "WHERE maintainability_score IS NOT NULL ORDER BY created_at DESC LIMIT 200"
    )
    ms_values = [float(r["maintainability_score"]) for r in ms_rows if r.get("maintainability_score") is not None]
    if len(ms_values) >= min_samples:
        mean_ms = sum(ms_values) / len(ms_values)
        var_ms = sum((v - mean_ms) ** 2 for v in ms_values) / len(ms_values)
        thresholds["maintainability_score_max"] = max(0.0, mean_ms - z_score * (var_ms ** 0.5))
    else:
        thresholds["maintainability_score_max"] = _THRESHOLD_DEFAULTS["maintainability_score_max"]
    return thresholds


def collect_all() -> Dict[str, Any]:
    """Run all collectors and write combined feedback file."""
    feedback = {
        "collected_at": _utcnow_iso(),
        "runtime_failures": collect_runtime_failures(),
        "quality_trends": collect_quality_trends(),
        "coverage_gaps": collect_coverage_gaps(),
        "heal_outcomes": collect_heal_outcomes(),
        "demand_signals": collect_demand_signals(),
        "trace_recommendations": collect_trace_recommendations(),
    }

    # Summary counts
    feedback["summary"] = {
        "runtime_failures": len(feedback["runtime_failures"]),
        "quality_degradations": len(feedback["quality_trends"]),
        "coverage_gaps": len(feedback["coverage_gaps"]),
        "heal_events": len(feedback["heal_outcomes"]),
        "high_demand_signals": len(feedback["demand_signals"]),
        "trace_recommendations": len(feedback["trace_recommendations"]),
    }

    # Write to feedback directory
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    date_str = _utcnow().strftime("%Y-%m-%d")
    feedback_file = FEEDBACK_DIR / f"feedback-{date_str}.json"
    feedback_file.write_text(json.dumps(feedback, indent=2, default=str), encoding="utf-8", newline="")

    # Log to genesis audit
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO genesis_audit
                (id, event_type, details, created_at)
            VALUES (%s, %s, %s, %s)
        """,
            (
                f"aud-{uuid.uuid4().hex[:10]}",
                "genesis.feedback.collected",
                json.dumps(feedback["summary"]),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("collect_all: best-effort INSERT into genesis_audit failed (non-blocking): %s", exc)

    return feedback


def get_latest() -> Dict[str, Any]:
    """Read the most recent feedback file."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(FEEDBACK_DIR.glob("feedback-*.json"), reverse=True)
    if not files:
        return {"error": "No feedback collected yet"}
    return json.loads(files[0].read_text(encoding="utf-8"))


def get_summary() -> Dict[str, Any]:
    """Summarize all feedback files."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(FEEDBACK_DIR.glob("feedback-*.json"))
    if not files:
        return {"error": "No feedback collected yet", "total_files": 0}

    total_failures = 0
    total_gaps = 0
    total_heals = 0

    for f in files[-7:]:  # Last 7 days
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            total_failures += s.get("runtime_failures", 0)
            total_gaps += s.get("coverage_gaps", 0)
            total_heals += s.get("heal_events", 0)
        except Exception:
            continue

    return {
        "files_analyzed": min(len(files), 7),
        "total_files": len(files),
        "week_summary": {
            "runtime_failures": total_failures,
            "coverage_gaps": total_gaps,
            "heal_events": total_heals,
        },
        "latest_file": str(files[-1].name),
        "timestamp": _utcnow_iso(),
    }


def prioritize_reflexes() -> Dict[str, Any]:
    """Analyze feedback to recommend reflex priority adjustments.

    Returns a dict mapping reflex names to priority signals:
    - "boost": reflex should run more frequently
    - "normal": no change needed
    - "reduce": reflex can run less frequently

    This is advisory — daemon reads this to adjust scheduling.
    """
    latest = get_latest()
    if "error" in latest:
        return {"priorities": {}, "reason": "no_feedback_data"}

    summary = latest.get("summary", {})
    priorities = {}
    thresholds = compute_anomaly_thresholds()

    # High runtime failures → boost heal and audit
    failures = summary.get("runtime_failures", 0)
    if failures > thresholds["runtime_failures"]:
        priorities["heal"] = {"priority": "boost", "reason": f"{failures} failures exceeds anomaly threshold ({thresholds['runtime_failures']:.1f})"}
        priorities["audit"] = {"priority": "boost", "reason": "High failure rate warrants audit"}

    # Coverage gaps → boost test and evolve
    gaps = summary.get("coverage_gaps", 0)
    if gaps > thresholds["coverage_gaps"]:
        priorities["test"] = {"priority": "boost", "reason": f"{gaps} low-quality modules exceeds anomaly threshold ({thresholds['coverage_gaps']:.1f})"}
        priorities["evolve"] = {"priority": "boost", "reason": "Many quality gaps to address"}

    # High demand signals → boost publish and research
    demand = summary.get("high_demand_signals", 0)
    if demand > thresholds["high_demand_signals"]:
        priorities["publish"] = {"priority": "boost", "reason": f"{demand} high-demand topics exceeds anomaly threshold ({thresholds['high_demand_signals']:.1f})"}
        priorities["research"] = {"priority": "boost", "reason": "Active demand requires fresh research"}

    # Trace recommendations → boost learn
    traces = summary.get("trace_recommendations", 0)
    if traces > thresholds["trace_recommendations"]:
        priorities["learn"] = {"priority": "boost", "reason": "Harness recommendations available for learning"}

    # Default: all others normal
    all_reflexes = [
        "research",
        "scout",
        "audit",
        "report",
        "comply",
        "ingest",
        "market",
        "publish",
        "test",
        "learn",
        "heal",
        "evolve",
    ]
    for r in all_reflexes:
        if r not in priorities:
            priorities[r] = {"priority": "normal", "reason": "No feedback signals"}

    return {
        "priorities": priorities,
        "thresholds": thresholds,
        "feedback_date": latest.get("collected_at", ""),
        "boost_count": len([p for p in priorities.values() if p["priority"] == "boost"]),
        "timestamp": _utcnow_iso(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Genesis Feedback Collector — v1.x telemetry for v2.0")
    parser.add_argument("--collect", action="store_true", help="Collect all feedback now")
    parser.add_argument("--latest", action="store_true", help="Show latest feedback")
    parser.add_argument("--summary", action="store_true", help="Show 7-day summary")
    parser.add_argument("--priorities", action="store_true", help="Show reflex priority recommendations")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.collect:
        result = collect_all()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            s = result["summary"]
            print(f"Feedback collected at {result['collected_at']}")
            for k, v in s.items():
                print(f"  {k}: {v}")
        return

    if args.latest:
        result = get_latest()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            s = result.get("summary", {})
            print(f"Latest feedback: {result.get('collected_at', 'unknown')}")
            for k, v in s.items():
                print(f"  {k}: {v}")
        return

    if args.summary:
        result = get_summary()
        print(
            json.dumps(result, indent=2)
            if args.json
            else f"7-day summary ({result.get('files_analyzed', 0)} files):\n"
            f"  Failures: {result.get('week_summary', {}).get('runtime_failures', 0)}\n"
            f"  Gaps: {result.get('week_summary', {}).get('coverage_gaps', 0)}\n"
            f"  Heals: {result.get('week_summary', {}).get('heal_events', 0)}"
        )
        return

    if args.priorities:
        result = prioritize_reflexes()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Reflex priorities (boosted: {result.get('boost_count', 0)}):")
            for name, info in result.get("priorities", {}).items():
                marker = ">>>" if info["priority"] == "boost" else "   "
                print(f"  {marker} {name}: {info['priority']} -- {info['reason']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
