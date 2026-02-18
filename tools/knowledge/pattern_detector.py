#!/usr/bin/env python3
"""Detect patterns in failures using statistical methods (no GPU required).
Includes feature extraction, pattern matching via string similarity,
frequency anomaly detection, and deployment correlation analysis."""

import argparse
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
def extract_features(error_data: dict) -> dict:
    """Extract features from error data for pattern matching.

    Input error_data keys: error_type, error_message, service_name, timestamp,
                           stack_trace, context
    Returns dict of extracted features.
    """
    features = {}

    # Core features
    features["error_type"] = error_data.get("error_type", "unknown")
    features["service_name"] = error_data.get("service_name", error_data.get("source", "unknown"))

    # Time features
    ts = error_data.get("timestamp") or error_data.get("created_at")
    if ts:
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
            else:
                dt = ts
            features["time_of_day"] = dt.hour
            features["day_of_week"] = dt.weekday()  # 0=Monday
            features["is_business_hours"] = 1 if 9 <= dt.hour <= 17 and dt.weekday() < 5 else 0
        except (ValueError, AttributeError):
            features["time_of_day"] = -1
            features["day_of_week"] = -1
            features["is_business_hours"] = -1
    else:
        features["time_of_day"] = -1
        features["day_of_week"] = -1
        features["is_business_hours"] = -1

    # Message features
    message = error_data.get("error_message", "")
    features["message_length"] = len(message)
    features["has_timeout"] = 1 if re.search(r"timeout|timed?\s*out", message, re.I) else 0
    features["has_connection"] = 1 if re.search(r"connect|connection|refused|reset", message, re.I) else 0
    features["has_memory"] = 1 if re.search(r"memory|oom|out.of.memory|heap", message, re.I) else 0
    features["has_permission"] = 1 if re.search(r"permission|denied|forbidden|unauthorized|403|401", message, re.I) else 0
    features["has_not_found"] = 1 if re.search(r"not.found|404|missing|no.such", message, re.I) else 0
    features["has_disk"] = 1 if re.search(r"disk|storage|space|no.space|quota", message, re.I) else 0
    features["has_database"] = 1 if re.search(r"database|db|sql|postgres|mysql|connection.pool", message, re.I) else 0

    # Stack trace features
    stack = error_data.get("stack_trace", "")
    features["has_stack_trace"] = 1 if stack else 0
    features["stack_depth"] = stack.count("\n") if stack else 0

    # Context
    ctx = error_data.get("context", {})
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (json.JSONDecodeError, TypeError):
            ctx = {}
    features["environment"] = ctx.get("environment", "unknown")
    features["recent_deployment"] = 1 if ctx.get("recent_deployment") else 0

    # Generate a feature signature for matching
    sig_parts = [
        features["error_type"],
        features["service_name"],
        "timeout" if features["has_timeout"] else "",
        "connection" if features["has_connection"] else "",
        "memory" if features["has_memory"] else "",
        "permission" if features["has_permission"] else "",
        "database" if features["has_database"] else "",
        "disk" if features["has_disk"] else "",
    ]
    features["signature"] = "|".join(p for p in sig_parts if p)

    return features


# ---------------------------------------------------------------------------
# String similarity (Jaccard on character n-grams)
# ---------------------------------------------------------------------------
def _ngrams(text: str, n: int = 3) -> set:
    """Generate character n-grams from text."""
    text = text.lower().strip()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between two strings using character trigrams."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_grams = _ngrams(a)
    b_grams = _ngrams(b)
    intersection = len(a_grams & b_grams)
    union = len(a_grams | b_grams)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Match against known patterns
# ---------------------------------------------------------------------------
def match_known_pattern(features: dict, db_path: Path = None) -> list:
    """Match extracted features against knowledge_patterns table.
    Returns list of matches sorted by combined score (similarity * confidence)."""
    conn = _get_db(db_path)
    try:
        patterns = conn.execute(
            """SELECT id, pattern_type, pattern_signature, description,
                      root_cause, remediation, confidence, auto_healable
               FROM knowledge_patterns
               ORDER BY confidence DESC"""
        ).fetchall()

        matches = []
        feature_sig = features.get("signature", "")

        for p in patterns:
            p_sig = p["pattern_signature"] or ""

            # Score based on signature similarity
            sig_score = _similarity(feature_sig, p_sig)

            # Boost score if error_type matches pattern_type keywords
            type_boost = 0.0
            if features.get("error_type", "").lower() in (p["description"] or "").lower():
                type_boost = 0.15
            if features.get("error_type", "").lower() in (p["pattern_signature"] or "").lower():
                type_boost = 0.25

            # Boost if key features match description
            desc = (p["description"] or "").lower()
            if features.get("has_timeout") and "timeout" in desc:
                type_boost += 0.1
            if features.get("has_connection") and "connection" in desc:
                type_boost += 0.1
            if features.get("has_memory") and "memory" in desc:
                type_boost += 0.1
            if features.get("has_database") and ("database" in desc or "pool" in desc):
                type_boost += 0.1

            combined_score = min((sig_score + type_boost) * p["confidence"], 1.0)

            if combined_score > 0.1:  # Minimum threshold
                matches.append({
                    "pattern_id": p["id"],
                    "pattern_type": p["pattern_type"],
                    "description": p["description"],
                    "root_cause": p["root_cause"],
                    "remediation": p["remediation"],
                    "confidence": p["confidence"],
                    "similarity_score": round(sig_score, 3),
                    "combined_score": round(combined_score, 3),
                    "auto_healable": bool(p["auto_healable"]),
                })

        matches.sort(key=lambda m: m["combined_score"], reverse=True)
        return matches

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Frequency anomaly detection
# ---------------------------------------------------------------------------
def detect_frequency_anomaly(
    project_id: str,
    window_hours: int = 1,
    threshold: int = 3,
    db_path: Path = None,
) -> list:
    """Check if the same error type occurs more than `threshold` times in `window_hours`.
    Returns list of anomalies found."""
    conn = _get_db(db_path)
    try:
        window_start = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat()

        rows = conn.execute(
            """SELECT error_type, error_message, COUNT(*) as count
               FROM failure_log
               WHERE project_id = ? AND created_at > ?
               GROUP BY error_type
               HAVING COUNT(*) > ?
               ORDER BY count DESC""",
            (project_id, window_start, threshold),
        ).fetchall()

        anomalies = []
        for row in rows:
            # Get details of recent occurrences
            recent = conn.execute(
                """SELECT id, error_message, source, created_at
                   FROM failure_log
                   WHERE project_id = ? AND error_type = ? AND created_at > ?
                   ORDER BY created_at DESC
                   LIMIT 5""",
                (project_id, row["error_type"], window_start),
            ).fetchall()

            anomalies.append({
                "error_type": row["error_type"],
                "count": row["count"],
                "window_hours": window_hours,
                "threshold": threshold,
                "severity": "critical" if row["count"] > threshold * 3 else
                           "high" if row["count"] > threshold * 2 else "warning",
                "recent_occurrences": [
                    {
                        "id": r["id"],
                        "message": r["error_message"][:200],
                        "source": r["source"],
                        "timestamp": r["created_at"],
                    }
                    for r in recent
                ],
            })

        return anomalies

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Deployment correlation
# ---------------------------------------------------------------------------
def detect_deployment_correlation(
    project_id: str,
    window_minutes: int = 30,
    db_path: Path = None,
) -> list:
    """Check if errors cluster around recent deployments.
    Returns list of correlations found."""
    conn = _get_db(db_path)
    try:
        # Get recent deployments
        deployments = conn.execute(
            """SELECT id, environment, version, status, created_at, completed_at
               FROM deployments
               WHERE project_id = ?
                 AND created_at > datetime('now', '-24 hours')
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()

        correlations = []

        for dep in deployments:
            dep_time = dep["created_at"]

            # Count failures in window around deployment
            failures_after = conn.execute(
                """SELECT COUNT(*) as count
                   FROM failure_log
                   WHERE project_id = ?
                     AND created_at BETWEEN ? AND datetime(?, '+' || ? || ' minutes')""",
                (project_id, dep_time, dep_time, window_minutes),
            ).fetchone()["count"]

            # Count baseline failures (same window, day before)
            baseline = conn.execute(
                """SELECT COUNT(*) as count
                   FROM failure_log
                   WHERE project_id = ?
                     AND created_at BETWEEN datetime(?, '-1 day')
                     AND datetime(?, '-1 day', '+' || ? || ' minutes')""",
                (project_id, dep_time, dep_time, window_minutes),
            ).fetchone()["count"]

            if failures_after > 0:
                # Compute spike ratio
                spike_ratio = failures_after / max(baseline, 1)

                # Get failure details
                failure_details = conn.execute(
                    """SELECT error_type, COUNT(*) as count
                       FROM failure_log
                       WHERE project_id = ?
                         AND created_at BETWEEN ? AND datetime(?, '+' || ? || ' minutes')
                       GROUP BY error_type
                       ORDER BY count DESC""",
                    (project_id, dep_time, dep_time, window_minutes),
                ).fetchall()

                is_correlated = spike_ratio > 2.0 or (failures_after > 3 and baseline == 0)

                correlations.append({
                    "deployment_id": dep["id"],
                    "version": dep["version"],
                    "environment": dep["environment"],
                    "deployment_status": dep["status"],
                    "deployed_at": dep["created_at"],
                    "failures_after_deploy": failures_after,
                    "baseline_failures": baseline,
                    "spike_ratio": round(spike_ratio, 2),
                    "is_correlated": is_correlated,
                    "failure_types": [
                        {"error_type": r["error_type"], "count": r["count"]}
                        for r in failure_details
                    ],
                })

        return correlations

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Update pattern confidence
# ---------------------------------------------------------------------------
def update_pattern_confidence(
    pattern_id: int,
    outcome: str,
    db_path: Path = None,
) -> dict:
    """Update a pattern's confidence based on outcome.
    outcome='success' → +0.1, outcome='failure' → -0.2.
    Clamps between 0.0 and 1.0."""
    conn = _get_db(db_path)
    try:
        current = conn.execute(
            "SELECT confidence FROM knowledge_patterns WHERE id = ?",
            (pattern_id,),
        ).fetchone()

        if not current:
            return {"error": f"Pattern {pattern_id} not found"}

        old_conf = current["confidence"]

        if outcome == "success":
            delta = 0.1
        elif outcome == "failure":
            delta = -0.2
        else:
            return {"error": f"Invalid outcome '{outcome}'. Use 'success' or 'failure'."}

        new_conf = max(0.0, min(1.0, old_conf + delta))

        conn.execute(
            "UPDATE knowledge_patterns SET confidence = ?, updated_at = ? WHERE id = ?",
            (new_conf, datetime.utcnow().isoformat(), pattern_id),
        )
        conn.commit()

        return {
            "pattern_id": pattern_id,
            "old_confidence": round(old_conf, 3),
            "new_confidence": round(new_conf, 3),
            "delta": round(delta, 3),
            "outcome": outcome,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------
def analyze_project(project_id: str, db_path: Path = None) -> dict:
    """Run full pattern analysis on a project."""
    result = {
        "project_id": project_id,
        "analyzed_at": datetime.utcnow().isoformat(),
        "frequency_anomalies": [],
        "deployment_correlations": [],
        "pattern_matches": [],
    }

    # 1. Check frequency anomalies
    result["frequency_anomalies"] = detect_frequency_anomaly(project_id, db_path=db_path)

    # 2. Check deployment correlations
    result["deployment_correlations"] = detect_deployment_correlation(project_id, db_path=db_path)

    # 3. Match recent failures against known patterns
    conn = _get_db(db_path)
    try:
        recent_failures = conn.execute(
            """SELECT id, error_type, error_message, source, stack_trace, context, created_at
               FROM failure_log
               WHERE project_id = ? AND resolved = 0
               ORDER BY created_at DESC
               LIMIT 20""",
            (project_id,),
        ).fetchall()

        for failure in recent_failures:
            error_data = {
                "error_type": failure["error_type"],
                "error_message": failure["error_message"],
                "source": failure["source"],
                "stack_trace": failure["stack_trace"],
                "context": failure["context"],
                "created_at": failure["created_at"],
            }
            features = extract_features(error_data)
            matches = match_known_pattern(features, db_path)

            if matches:
                result["pattern_matches"].append({
                    "failure_id": failure["id"],
                    "error_type": failure["error_type"],
                    "error_message": failure["error_message"][:100],
                    "top_match": matches[0],
                    "total_matches": len(matches),
                })
    finally:
        conn.close()

    # Summary
    result["summary"] = {
        "total_anomalies": len(result["frequency_anomalies"]),
        "critical_anomalies": sum(
            1 for a in result["frequency_anomalies"] if a["severity"] == "critical"
        ),
        "deployment_related": sum(
            1 for c in result["deployment_correlations"] if c["is_correlated"]
        ),
        "pattern_matches_found": len(result["pattern_matches"]),
        "auto_healable": sum(
            1 for m in result["pattern_matches"]
            if m.get("top_match", {}).get("auto_healable")
        ),
    }

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Detect patterns in failures")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--analyze", action="store_true", help="Run full analysis")
    parser.add_argument("--frequency", action="store_true", help="Check frequency anomalies only")
    parser.add_argument("--correlation", action="store_true", help="Check deployment correlation only")
    parser.add_argument("--window-hours", type=int, default=1, help="Window for frequency check (hours)")
    parser.add_argument("--threshold", type=int, default=3, help="Frequency threshold")
    parser.add_argument("--update-confidence", type=int, help="Pattern ID to update confidence")
    parser.add_argument("--outcome", choices=["success", "failure"], help="Outcome for confidence update")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    if args.update_confidence and args.outcome:
        result = update_pattern_confidence(args.update_confidence, args.outcome, db_path)
        print(json.dumps(result, indent=2))
        return

    if args.analyze or (not args.frequency and not args.correlation):
        result = analyze_project(args.project, db_path)
    elif args.frequency:
        result = detect_frequency_anomaly(
            args.project, args.window_hours, args.threshold, db_path
        )
    elif args.correlation:
        result = detect_deployment_correlation(args.project, db_path=db_path)
    else:
        result = analyze_project(args.project, db_path)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        if isinstance(result, dict) and "summary" in result:
            print(f"\n=== Pattern Analysis: {result['project_id']} ===")
            s = result["summary"]
            print(f"  Frequency anomalies: {s['total_anomalies']} ({s['critical_anomalies']} critical)")
            print(f"  Deployment-related:  {s['deployment_related']}")
            print(f"  Pattern matches:     {s['pattern_matches_found']}")
            print(f"  Auto-healable:       {s['auto_healable']}")

            if result["frequency_anomalies"]:
                print("\n  FREQUENCY ANOMALIES:")
                for a in result["frequency_anomalies"]:
                    print(f"    [{a['severity']:>8s}] {a['error_type']}: {a['count']}x in {a['window_hours']}h")

            if result["deployment_correlations"]:
                corr = [c for c in result["deployment_correlations"] if c["is_correlated"]]
                if corr:
                    print("\n  DEPLOYMENT CORRELATIONS:")
                    for c in corr:
                        print(f"    Deploy {c['version']} ({c['environment']}): "
                              f"{c['failures_after_deploy']} failures (spike: {c['spike_ratio']}x)")

            if result["pattern_matches"]:
                print("\n  PATTERN MATCHES:")
                for m in result["pattern_matches"]:
                    top = m["top_match"]
                    heal = " [auto-healable]" if top["auto_healable"] else ""
                    print(f"    Failure #{m['failure_id']}: {top['description'][:50]} "
                          f"(score: {top['combined_score']}){heal}")
        else:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
