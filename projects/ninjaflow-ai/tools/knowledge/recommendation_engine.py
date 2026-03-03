#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate recommendations based on patterns, failures, and project context.
Uses rule-based analysis — no LLM call needed for the base version.
Recommendations are ranked by confidence and relevance."""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Recommendation rules
# ---------------------------------------------------------------------------
def _check_frequent_failures(conn, project_id: str) -> list:
    """Rule: Recommend investigation if same error type keeps recurring."""
    recs = []

    rows = conn.execute(
        """SELECT error_type, COUNT(*) as count, MAX(created_at) as last_seen
           FROM failure_log
           WHERE project_id = ? AND created_at > datetime('now', '-7 days')
           GROUP BY error_type
           HAVING COUNT(*) >= 3
           ORDER BY count DESC""",
        (project_id,),
    ).fetchall()

    for row in rows:
        severity = "high" if row["count"] > 10 else "medium" if row["count"] > 5 else "low"
        recs.append({
            "type": "recurring_failure",
            "severity": severity,
            "confidence": min(0.5 + (row["count"] / 20.0), 0.95),
            "title": f"Recurring failure: {row['error_type']}",
            "description": (
                f"The error '{row['error_type']}' has occurred {row['count']} times "
                f"in the last 7 days. Last seen: {row['last_seen']}. "
                f"Consider investigating root cause and adding a known pattern."
            ),
            "action": "investigate_root_cause",
            "details": {
                "error_type": row["error_type"],
                "occurrences": row["count"],
                "last_seen": row["last_seen"],
            },
        })

    return recs


def _check_deployment_health(conn, project_id: str) -> list:
    """Rule: Recommend improvements based on deployment success rate."""
    recs = []

    stats = conn.execute(
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) as succeeded,
             SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
           FROM deployments
           WHERE project_id = ?
             AND created_at > datetime('now', '-30 days')""",
        (project_id,),
    ).fetchone()

    total = stats["total"] or 0
    failed = stats["failed"] or 0

    if total > 0:
        success_rate = ((stats["succeeded"] or 0) / total) * 100

        if success_rate < 50:
            recs.append({
                "type": "deployment_health",
                "severity": "critical",
                "confidence": 0.9,
                "title": f"Critical: Deployment success rate is {success_rate:.0f}%",
                "description": (
                    f"Only {success_rate:.0f}% of deployments succeeded in the last 30 days "
                    f"({stats['succeeded']}/{total}). This indicates systemic issues in the "
                    f"deployment pipeline. Review CI/CD configuration, test coverage, and "
                    f"pre-deployment checks."
                ),
                "action": "review_pipeline",
                "details": {
                    "total": total,
                    "succeeded": stats["succeeded"] or 0,
                    "failed": failed,
                    "success_rate": round(success_rate, 1),
                },
            })
        elif success_rate < 80:
            recs.append({
                "type": "deployment_health",
                "severity": "medium",
                "confidence": 0.75,
                "title": f"Deployment success rate below target: {success_rate:.0f}%",
                "description": (
                    f"Deployment success rate is {success_rate:.0f}% (target: 95%+). "
                    f"Review failed deployments for common patterns and strengthen "
                    f"pre-deployment validation."
                ),
                "action": "improve_deployment_validation",
                "details": {
                    "total": total,
                    "succeeded": stats["succeeded"] or 0,
                    "failed": failed,
                    "success_rate": round(success_rate, 1),
                },
            })

    # Check for deployments without health checks
    no_healthcheck = conn.execute(
        """SELECT COUNT(*) FROM deployments
           WHERE project_id = ?
             AND health_check_passed IS NULL
             AND created_at > datetime('now', '-30 days')""",
        (project_id,),
    ).fetchone()[0]

    if no_healthcheck > 0 and total > 0:
        ratio = no_healthcheck / total
        if ratio > 0.5:
            recs.append({
                "type": "missing_health_checks",
                "severity": "medium",
                "confidence": 0.8,
                "title": f"{no_healthcheck}/{total} deployments lack health checks",
                "description": (
                    "Most deployments are missing health check validation. "
                    "Add health check endpoints and configure post-deployment verification "
                    "to catch issues before they reach users."
                ),
                "action": "add_health_checks",
            })

    return recs


def _check_unresolved_failures(conn, project_id: str) -> list:
    """Rule: Recommend action on unresolved failures."""
    recs = []

    unresolved = conn.execute(
        """SELECT COUNT(*) FROM failure_log
           WHERE project_id = ? AND resolved = 0
             AND created_at > datetime('now', '-7 days')""",
        (project_id,),
    ).fetchone()[0]

    old_unresolved = conn.execute(
        """SELECT COUNT(*) FROM failure_log
           WHERE project_id = ? AND resolved = 0
             AND created_at < datetime('now', '-7 days')""",
        (project_id,),
    ).fetchone()[0]

    if unresolved > 10:
        recs.append({
            "type": "unresolved_failures",
            "severity": "high",
            "confidence": 0.85,
            "title": f"{unresolved} unresolved failures in the last 7 days",
            "description": (
                f"There are {unresolved} unresolved failures from the past week. "
                f"Prioritize triage and resolution to prevent cascading issues."
            ),
            "action": "triage_failures",
            "details": {"unresolved_recent": unresolved, "unresolved_old": old_unresolved},
        })

    if old_unresolved > 5:
        recs.append({
            "type": "stale_failures",
            "severity": "medium",
            "confidence": 0.7,
            "title": f"{old_unresolved} stale unresolved failures (older than 7 days)",
            "description": (
                f"There are {old_unresolved} unresolved failures older than 7 days. "
                f"Review and close resolved ones, or escalate persistent issues."
            ),
            "action": "cleanup_failures",
        })

    return recs


def _check_pattern_coverage(conn, project_id: str) -> list:
    """Rule: Recommend adding patterns for unmatched failures."""
    recs = []

    # Failures without a matched pattern
    unmatched = conn.execute(
        """SELECT COUNT(*) FROM failure_log
           WHERE project_id = ? AND pattern_id IS NULL
             AND created_at > datetime('now', '-30 days')""",
        (project_id,),
    ).fetchone()[0]

    total_failures = conn.execute(
        """SELECT COUNT(*) FROM failure_log
           WHERE project_id = ?
             AND created_at > datetime('now', '-30 days')""",
        (project_id,),
    ).fetchone()[0]

    total_patterns = conn.execute(
        "SELECT COUNT(*) FROM knowledge_patterns"
    ).fetchone()[0]

    if total_failures > 0 and unmatched > 0:
        match_rate = ((total_failures - unmatched) / total_failures) * 100
        if match_rate < 50:
            recs.append({
                "type": "low_pattern_coverage",
                "severity": "medium",
                "confidence": 0.7,
                "title": f"Low pattern coverage: {match_rate:.0f}% of failures have patterns",
                "description": (
                    f"Only {match_rate:.0f}% of recent failures match known patterns. "
                    f"There are {unmatched} unmatched failures. "
                    f"Ingest common failure patterns to improve self-healing coverage."
                ),
                "action": "ingest_patterns",
                "details": {
                    "total_failures": total_failures,
                    "matched": total_failures - unmatched,
                    "unmatched": unmatched,
                    "total_patterns": total_patterns,
                    "match_rate": round(match_rate, 1),
                },
            })

    # Check patterns with low confidence
    low_conf_patterns = conn.execute(
        """SELECT COUNT(*) FROM knowledge_patterns
           WHERE confidence < 0.3 AND auto_healable = 1"""
    ).fetchone()[0]

    if low_conf_patterns > 0:
        recs.append({
            "type": "low_confidence_patterns",
            "severity": "low",
            "confidence": 0.6,
            "title": f"{low_conf_patterns} auto-healable patterns have low confidence",
            "description": (
                f"There are {low_conf_patterns} patterns marked as auto-healable but with "
                f"confidence below 0.3. Review these patterns — they may be producing "
                f"incorrect remediations. Consider disabling auto-heal or improving accuracy."
            ),
            "action": "review_patterns",
        })

    return recs


def _check_alert_fatigue(conn, project_id: str) -> list:
    """Rule: Detect potential alert fatigue."""
    recs = []

    total_alerts = conn.execute(
        """SELECT COUNT(*) FROM alerts
           WHERE project_id = ?
             AND created_at > datetime('now', '-7 days')""",
        (project_id,),
    ).fetchone()[0]

    unacked_alerts = conn.execute(
        """SELECT COUNT(*) FROM alerts
           WHERE project_id = ?
             AND acknowledged_by IS NULL
             AND created_at > datetime('now', '-7 days')""",
        (project_id,),
    ).fetchone()[0]

    if total_alerts > 50 and unacked_alerts > total_alerts * 0.5:
        recs.append({
            "type": "alert_fatigue",
            "severity": "medium",
            "confidence": 0.75,
            "title": f"Potential alert fatigue: {unacked_alerts}/{total_alerts} alerts unacknowledged",
            "description": (
                f"Over {unacked_alerts} alerts are unacknowledged out of {total_alerts} total "
                f"in the last 7 days. This suggests alert fatigue. Review alert thresholds, "
                f"consolidate noisy alerts, and ensure critical alerts are prioritized."
            ),
            "action": "tune_alerts",
            "details": {
                "total_alerts": total_alerts,
                "unacknowledged": unacked_alerts,
            },
        })

    return recs


def _check_self_healing_effectiveness(conn, project_id: str) -> list:
    """Rule: Evaluate self-healing effectiveness."""
    recs = []

    events = conn.execute(
        """SELECT outcome, COUNT(*) as count
           FROM self_healing_events
           WHERE project_id = ?
             AND created_at > datetime('now', '-30 days')
           GROUP BY outcome""",
        (project_id,),
    ).fetchall()

    outcome_map = {row["outcome"]: row["count"] for row in events}
    total = sum(outcome_map.values())
    succeeded = outcome_map.get("succeeded", 0)
    failed = outcome_map.get("failed", 0)

    if total > 5:
        success_rate = (succeeded / total) * 100
        if success_rate < 50:
            recs.append({
                "type": "self_healing_ineffective",
                "severity": "high",
                "confidence": 0.8,
                "title": f"Self-healing success rate is low: {success_rate:.0f}%",
                "description": (
                    f"Only {success_rate:.0f}% of self-healing attempts succeeded "
                    f"({succeeded}/{total}). Review remediation actions and pattern accuracy."
                ),
                "action": "improve_self_healing",
                "details": {
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "escalated": outcome_map.get("escalated", 0),
                    "success_rate": round(success_rate, 1),
                },
            })

    return recs


# ---------------------------------------------------------------------------
# Main recommendation engine
# ---------------------------------------------------------------------------
def get_recommendations(project_id: str, context: dict = None, db_path: Path = None) -> dict:
    """Generate all recommendations for a project.
    Returns dict with recommendations sorted by confidence * severity weight."""
    conn = _get_db(db_path)

    severity_weights = {
        "critical": 1.0,
        "high": 0.8,
        "medium": 0.5,
        "low": 0.3,
    }

    all_recs = []

    try:
        # Run all rule checks
        all_recs.extend(_check_frequent_failures(conn, project_id))
        all_recs.extend(_check_deployment_health(conn, project_id))
        all_recs.extend(_check_unresolved_failures(conn, project_id))
        all_recs.extend(_check_pattern_coverage(conn, project_id))
        all_recs.extend(_check_alert_fatigue(conn, project_id))
        all_recs.extend(_check_self_healing_effectiveness(conn, project_id))

    finally:
        conn.close()

    # Score and sort recommendations
    for rec in all_recs:
        sev_weight = severity_weights.get(rec.get("severity", "low"), 0.3)
        rec["relevance_score"] = round(rec.get("confidence", 0.5) * sev_weight, 3)

    all_recs.sort(key=lambda r: r["relevance_score"], reverse=True)

    # Summary
    severity_counts = Counter(r["severity"] for r in all_recs)

    return {
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_recommendations": len(all_recs),
        "summary": {
            "critical": severity_counts.get("critical", 0),
            "high": severity_counts.get("high", 0),
            "medium": severity_counts.get("medium", 0),
            "low": severity_counts.get("low", 0),
        },
        "recommendations": all_recs,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate recommendations for a project")
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--severity", choices=["critical", "high", "medium", "low"], help="Filter by severity")
    parser.add_argument("--limit", type=int, default=20, help="Max recommendations to show")
    parser.add_argument("--db-path", help="Database path override")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    result = get_recommendations(args.project_id, db_path=db_path)

    # Filter
    recs = result["recommendations"]
    if args.severity:
        recs = [r for r in recs if r["severity"] == args.severity]
    recs = recs[:args.limit]

    if args.format == "json":
        result["recommendations"] = recs
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*70}")
        print(f"  RECOMMENDATIONS — {result['project_id']}")
        print(f"  Generated: {result['generated_at']}")
        print(f"{'='*70}")

        s = result["summary"]
        print(f"\n  Summary: {result['total_recommendations']} total — "
              f"Critical: {s['critical']}, High: {s['high']}, "
              f"Medium: {s['medium']}, Low: {s['low']}")

        if not recs:
            print("\n  No recommendations at this time. System looks healthy.")
        else:
            for i, rec in enumerate(recs, 1):
                print(f"\n  {i}. [{rec['severity'].upper():>8s}] {rec['title']}")
                print(f"     Score: {rec['relevance_score']} | Confidence: {rec.get('confidence', 'N/A')}")
                # Wrap description
                desc = rec.get("description", "")
                while desc:
                    print(f"     {desc[:70]}")
                    desc = desc[70:]
                print(f"     Action: {rec.get('action', 'N/A')}")

        print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
