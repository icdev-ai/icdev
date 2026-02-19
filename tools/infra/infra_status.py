#!/usr/bin/env python3
# CUI // SP-CTI
"""Infrastructure status report. Queries deployments table, checks K8s pod status
if available, and produces a comprehensive status summary."""

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def get_status(project_id: str, db_path: Path = None) -> dict:
    """Get comprehensive infrastructure status for a project."""
    conn = _get_db(db_path)
    status = {
        "project_id": project_id,
        "generated_at": datetime.utcnow().isoformat(),
        "environments": {},
        "recent_deployments": [],
        "active_alerts": [],
        "recent_failures": [],
        "metrics_summary": {},
        "k8s_status": {},
    }

    try:
        # ------------------------------------------------------------------
        # 1. Per-environment deployment status
        # ------------------------------------------------------------------
        for env in ("dev", "staging", "prod"):
            deployment = conn.execute(
                """SELECT id, version, status, deployed_by, health_check_passed,
                          created_at, completed_at
                   FROM deployments
                   WHERE project_id = ? AND environment = ?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (project_id, env),
            ).fetchone()

            if deployment:
                status["environments"][env] = {
                    "deployment_id": deployment["id"],
                    "version": deployment["version"],
                    "status": deployment["status"],
                    "deployed_by": deployment["deployed_by"],
                    "health_check_passed": bool(deployment["health_check_passed"])
                    if deployment["health_check_passed"] is not None
                    else None,
                    "deployed_at": deployment["created_at"],
                    "completed_at": deployment["completed_at"],
                }
            else:
                status["environments"][env] = {"status": "no_deployments"}

        # ------------------------------------------------------------------
        # 2. Recent deployments (last 10)
        # ------------------------------------------------------------------
        rows = conn.execute(
            """SELECT id, environment, version, status, deployed_by,
                      health_check_passed, created_at, completed_at
               FROM deployments
               WHERE project_id = ?
               ORDER BY created_at DESC
               LIMIT 10""",
            (project_id,),
        ).fetchall()

        for row in rows:
            status["recent_deployments"].append({
                "id": row["id"],
                "environment": row["environment"],
                "version": row["version"],
                "status": row["status"],
                "deployed_by": row["deployed_by"],
                "health_check_passed": bool(row["health_check_passed"])
                if row["health_check_passed"] is not None
                else None,
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })

        # ------------------------------------------------------------------
        # 3. Active alerts
        # ------------------------------------------------------------------
        alert_rows = conn.execute(
            """SELECT id, severity, source, title, description, status, created_at
               FROM alerts
               WHERE project_id = ? AND status IN ('firing', 'active', 'pending')
               ORDER BY
                 CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                               WHEN 'warning' THEN 3 ELSE 4 END,
                 created_at DESC
               LIMIT 20""",
            (project_id,),
        ).fetchall()

        for row in alert_rows:
            status["active_alerts"].append({
                "id": row["id"],
                "severity": row["severity"],
                "source": row["source"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "created_at": row["created_at"],
            })

        # ------------------------------------------------------------------
        # 4. Recent failures (last 24h)
        # ------------------------------------------------------------------
        failure_rows = conn.execute(
            """SELECT id, source, error_type, error_message, resolved, created_at
               FROM failure_log
               WHERE project_id = ?
                 AND created_at > datetime('now', '-24 hours')
               ORDER BY created_at DESC
               LIMIT 20""",
            (project_id,),
        ).fetchall()

        for row in failure_rows:
            status["recent_failures"].append({
                "id": row["id"],
                "source": row["source"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
                "resolved": bool(row["resolved"]) if row["resolved"] is not None else False,
                "created_at": row["created_at"],
            })

        # ------------------------------------------------------------------
        # 5. Latest metrics snapshot
        # ------------------------------------------------------------------
        metric_rows = conn.execute(
            """SELECT metric_name, metric_value, labels, collected_at
               FROM metric_snapshots
               WHERE project_id = ?
               ORDER BY collected_at DESC
               LIMIT 50""",
            (project_id,),
        ).fetchall()

        for row in metric_rows:
            name = row["metric_name"]
            if name not in status["metrics_summary"]:
                status["metrics_summary"][name] = {
                    "value": row["metric_value"],
                    "labels": row["labels"],
                    "collected_at": row["collected_at"],
                }

        # ------------------------------------------------------------------
        # 6. Deployment statistics
        # ------------------------------------------------------------------
        deploy_stats = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) as succeeded,
                 SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                 SUM(CASE WHEN status = 'rolled_back' THEN 1 ELSE 0 END) as rolled_back
               FROM deployments
               WHERE project_id = ?
                 AND created_at > datetime('now', '-30 days')""",
            (project_id,),
        ).fetchone()

        status["deployment_statistics"] = {
            "period": "last_30_days",
            "total": deploy_stats["total"] or 0,
            "succeeded": deploy_stats["succeeded"] or 0,
            "failed": deploy_stats["failed"] or 0,
            "rolled_back": deploy_stats["rolled_back"] or 0,
            "success_rate": (
                round((deploy_stats["succeeded"] or 0) / deploy_stats["total"] * 100, 1)
                if deploy_stats["total"]
                else 0
            ),
        }

        # ------------------------------------------------------------------
        # 7. K8s pod status (if kubectl available)
        # ------------------------------------------------------------------
        status["k8s_status"] = _check_k8s_status(project_id)

        # ------------------------------------------------------------------
        # 8. Overall health assessment
        # ------------------------------------------------------------------
        status["health"] = _assess_health(status)

    finally:
        conn.close()

    return status


def _check_k8s_status(project_id: str) -> dict:
    """Try to get Kubernetes pod status via kubectl. Returns empty dict if not available."""
    k8s = {}
    for env in ("staging", "prod"):
        namespace = f"{project_id}-{env}"
        try:
            proc = subprocess.run(
                [
                    "kubectl", "get", "pods",
                    "-n", namespace,
                    "-l", f"app.kubernetes.io/name={project_id}",
                    "-o", "json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0:
                pods_json = json.loads(proc.stdout)
                pods = []
                for item in pods_json.get("items", []):
                    pod_name = item.get("metadata", {}).get("name", "unknown")
                    phase = item.get("status", {}).get("phase", "Unknown")
                    conditions = item.get("status", {}).get("conditions", [])
                    ready = any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in conditions
                    )
                    restarts = 0
                    for cs in item.get("status", {}).get("containerStatuses", []):
                        restarts += cs.get("restartCount", 0)

                    pods.append({
                        "name": pod_name,
                        "phase": phase,
                        "ready": ready,
                        "restarts": restarts,
                    })

                total = len(pods)
                ready_count = sum(1 for p in pods if p["ready"])
                k8s[env] = {
                    "total_pods": total,
                    "ready_pods": ready_count,
                    "pods": pods,
                    "status": "healthy" if ready_count == total and total > 0 else "degraded",
                }
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            k8s[env] = {"status": "unavailable", "reason": "kubectl not available or timed out"}

    return k8s


def _assess_health(status: dict) -> dict:
    """Produce an overall health assessment from gathered data."""
    issues = []
    severity = "healthy"

    # Check for failed deployments
    for env, env_status in status.get("environments", {}).items():
        if isinstance(env_status, dict) and env_status.get("status") == "failed":
            issues.append(f"Failed deployment in {env}")
            if env == "prod":
                severity = "critical"
            elif severity != "critical":
                severity = "warning"

    # Check for active critical alerts
    critical_alerts = [a for a in status.get("active_alerts", []) if a.get("severity") == "critical"]
    if critical_alerts:
        severity = "critical"
        issues.append(f"{len(critical_alerts)} critical alerts active")

    high_alerts = [a for a in status.get("active_alerts", []) if a.get("severity") == "high"]
    if high_alerts and severity != "critical":
        severity = "warning"
        issues.append(f"{len(high_alerts)} high-severity alerts active")

    # Check recent failures
    unresolved = [f for f in status.get("recent_failures", []) if not f.get("resolved")]
    if len(unresolved) > 5:
        if severity == "healthy":
            severity = "warning"
        issues.append(f"{len(unresolved)} unresolved failures in last 24h")

    # Check deployment success rate
    stats = status.get("deployment_statistics", {})
    if stats.get("total", 0) > 0 and stats.get("success_rate", 100) < 80:
        if severity == "healthy":
            severity = "warning"
        issues.append(f"Low deployment success rate: {stats['success_rate']}%")

    # Check K8s
    for env, k8s in status.get("k8s_status", {}).items():
        if isinstance(k8s, dict) and k8s.get("status") == "degraded":
            issues.append(f"K8s pods degraded in {env}")
            if env == "prod":
                severity = "critical"
            elif severity == "healthy":
                severity = "warning"

    return {
        "overall": severity,
        "issues": issues,
        "issue_count": len(issues),
    }


def _format_report(status: dict) -> str:
    """Format status as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  INFRASTRUCTURE STATUS REPORT — {status['project_id']}")
    lines.append(f"  Generated: {status['generated_at']}")
    lines.append("=" * 70)

    # Health
    health = status.get("health", {})
    overall = health.get("overall", "unknown").upper()
    lines.append(f"\n  Overall Health: {overall}")
    for issue in health.get("issues", []):
        lines.append(f"    - {issue}")

    # Environments
    lines.append("\n  ENVIRONMENTS:")
    lines.append("  " + "-" * 60)
    for env, info in status.get("environments", {}).items():
        if isinstance(info, dict):
            ver = info.get("version", "N/A")
            st = info.get("status", "N/A")
            hc = info.get("health_check_passed", "N/A")
            lines.append(f"  {env:>10s}:  version={ver}  status={st}  health_check={hc}")

    # Deployment stats
    stats = status.get("deployment_statistics", {})
    if stats:
        lines.append(f"\n  DEPLOYMENT STATISTICS ({stats.get('period', 'N/A')}):")
        lines.append(f"    Total: {stats.get('total', 0)}  |  "
                     f"Success: {stats.get('succeeded', 0)}  |  "
                     f"Failed: {stats.get('failed', 0)}  |  "
                     f"Rolled back: {stats.get('rolled_back', 0)}")
        lines.append(f"    Success rate: {stats.get('success_rate', 0)}%")

    # Active alerts
    alerts = status.get("active_alerts", [])
    if alerts:
        lines.append(f"\n  ACTIVE ALERTS ({len(alerts)}):")
        for a in alerts[:10]:
            lines.append(f"    [{a['severity']:>8s}] {a['title']} ({a['source']})")

    # Recent failures
    failures = status.get("recent_failures", [])
    if failures:
        lines.append(f"\n  RECENT FAILURES ({len(failures)} in last 24h):")
        for f in failures[:10]:
            resolved = "resolved" if f.get("resolved") else "OPEN"
            lines.append(f"    [{resolved:>8s}] {f['error_type']}: {f['error_message'][:60]}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Infrastructure status report")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    status = get_status(args.project, db_path)

    if args.format == "json":
        print(json.dumps(status, indent=2))
    else:
        print(_format_report(status))


if __name__ == "__main__":
    main()
