#!/usr/bin/env python3
"""Deployment rollback manager. Queries deployment history, executes kubectl rollout undo,
records actions in audit trail and deployments table."""

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def get_rollback_target(project_id: str, environment: str, db_path: Path = None) -> dict:
    """Query deployments table for the last successful version before the current one.
    Returns dict with version, deployment_id, deployed_by, completed_at."""
    conn = _get_db(db_path)
    try:
        # Get the current deployment (most recent)
        current = conn.execute(
            """SELECT id, version, status, deployed_by, completed_at
               FROM deployments
               WHERE project_id = ? AND environment = ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (project_id, environment),
        ).fetchone()

        if not current:
            return {"error": f"No deployments found for {project_id} in {environment}"}

        # Get the last successful deployment before the current one
        target = conn.execute(
            """SELECT id, version, status, deployed_by, completed_at
               FROM deployments
               WHERE project_id = ? AND environment = ?
                 AND status = 'succeeded'
                 AND id < ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (project_id, environment, current["id"]),
        ).fetchone()

        if not target:
            # If current is failed, check if there's any successful deployment
            target = conn.execute(
                """SELECT id, version, status, deployed_by, completed_at
                   FROM deployments
                   WHERE project_id = ? AND environment = ?
                     AND status = 'succeeded'
                     AND id != ?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (project_id, environment, current["id"]),
            ).fetchone()

        if not target:
            return {
                "error": "No previous successful deployment found to roll back to",
                "current": {
                    "deployment_id": current["id"],
                    "version": current["version"],
                    "status": current["status"],
                },
            }

        return {
            "current": {
                "deployment_id": current["id"],
                "version": current["version"],
                "status": current["status"],
            },
            "rollback_target": {
                "deployment_id": target["id"],
                "version": target["version"],
                "deployed_by": target["deployed_by"],
                "completed_at": target["completed_at"],
            },
        }
    finally:
        conn.close()


def execute_rollback(
    project_id: str,
    environment: str,
    dry_run: bool = False,
    db_path: Path = None,
    k8s_namespace: str = None,
    deployment_name: str = None,
) -> dict:
    """Execute a rollback: kubectl rollout undo, record in audit trail.
    Returns result dict with status, details, and any errors."""
    conn = _get_db(db_path)
    result = {
        "project_id": project_id,
        "environment": environment,
        "timestamp": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
    }

    try:
        # 1. Find rollback target
        target_info = get_rollback_target(project_id, environment, db_path)
        if "error" in target_info:
            result["status"] = "failed"
            result["error"] = target_info["error"]
            return result

        result["current_version"] = target_info["current"]["version"]
        result["target_version"] = target_info["rollback_target"]["version"]

        if dry_run:
            result["status"] = "dry_run"
            result["message"] = (
                f"Would roll back {project_id} in {environment} "
                f"from {result['current_version']} to {result['target_version']}"
            )
            return result

        # 2. Execute kubectl rollout undo
        namespace = k8s_namespace or f"{project_id}-{environment}"
        deploy_name = deployment_name or project_id

        kubectl_cmd = [
            "kubectl", "rollout", "undo",
            f"deployment/{deploy_name}",
            "-n", namespace,
        ]

        try:
            proc = subprocess.run(
                kubectl_cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            result["kubectl_stdout"] = proc.stdout.strip()
            result["kubectl_stderr"] = proc.stderr.strip()
            result["kubectl_returncode"] = proc.returncode

            if proc.returncode != 0:
                result["status"] = "failed"
                result["error"] = f"kubectl rollout undo failed: {proc.stderr.strip()}"
                # Still record the failed attempt
                _record_rollback(conn, project_id, environment, target_info, "failed", result)
                return result

        except FileNotFoundError:
            result["status"] = "failed"
            result["error"] = "kubectl not found. Ensure kubectl is installed and in PATH."
            _record_rollback(conn, project_id, environment, target_info, "failed", result)
            return result
        except subprocess.TimeoutExpired:
            result["status"] = "failed"
            result["error"] = "kubectl rollout undo timed out after 120s"
            _record_rollback(conn, project_id, environment, target_info, "failed", result)
            return result

        # 3. Wait for rollout to complete
        wait_cmd = [
            "kubectl", "rollout", "status",
            f"deployment/{deploy_name}",
            "-n", namespace,
            "--timeout=300s",
        ]

        try:
            wait_proc = subprocess.run(
                wait_cmd,
                capture_output=True,
                text=True,
                timeout=330,
            )
            result["rollout_status"] = wait_proc.stdout.strip()

            if wait_proc.returncode != 0:
                result["status"] = "partial"
                result["warning"] = f"Rollback initiated but rollout status check failed: {wait_proc.stderr.strip()}"
            else:
                result["status"] = "succeeded"

        except (FileNotFoundError, subprocess.TimeoutExpired):
            result["status"] = "partial"
            result["warning"] = "Rollback initiated but could not verify rollout status"

        # 4. Record in database
        _record_rollback(conn, project_id, environment, target_info, result["status"], result)
        result["message"] = (
            f"Rolled back {project_id} in {environment} "
            f"from {result['current_version']} to {result['target_version']}"
        )

        return result

    finally:
        conn.close()


def _record_rollback(
    conn: sqlite3.Connection,
    project_id: str,
    environment: str,
    target_info: dict,
    status: str,
    result: dict,
) -> None:
    """Record rollback in deployments table and audit trail."""
    now = datetime.utcnow().isoformat()
    target_version = target_info["rollback_target"]["version"]

    # Insert deployment record for the rollback
    try:
        conn.execute(
            """INSERT INTO deployments
               (project_id, environment, version, status, deployed_by,
                rollback_version, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                environment,
                target_version,
                status,
                "icdev-rollback",
                target_info["current"]["version"],
                now,
                now if status in ("succeeded", "failed") else None,
            ),
        )
    except Exception:
        pass  # Table may not have all columns; non-critical

    # Insert audit trail entry
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "rollback_executed",
                "icdev-rollback",
                f"Rollback in {environment}: {target_info['current']['version']} -> {target_version}",
                json.dumps({
                    "environment": environment,
                    "from_version": target_info["current"]["version"],
                    "to_version": target_version,
                    "status": status,
                    "kubectl_output": result.get("kubectl_stdout", ""),
                }),
                "CUI",
            ),
        )
    except Exception:
        pass  # audit_trail may not exist; non-critical

    conn.commit()


def auto_rollback_check(deployment_id: int, db_path: Path = None) -> dict:
    """Check if a deployment warrants auto-rollback.
    Conditions: health_check_passed=False, or status='failed'."""
    conn = _get_db(db_path)
    try:
        deployment = conn.execute(
            """SELECT id, project_id, environment, version, status,
                      health_check_passed, created_at, completed_at
               FROM deployments WHERE id = ?""",
            (deployment_id,),
        ).fetchone()

        if not deployment:
            return {"should_rollback": False, "reason": f"Deployment {deployment_id} not found"}

        should_rollback = False
        reasons = []

        # Check if deployment failed
        if deployment["status"] == "failed":
            should_rollback = True
            reasons.append("Deployment status is 'failed'")

        # Check if health check failed
        if deployment["health_check_passed"] is not None and not deployment["health_check_passed"]:
            should_rollback = True
            reasons.append("Health check did not pass")

        # Check if deployment is taking too long (stuck)
        if deployment["status"] == "in_progress" and deployment["completed_at"] is None:
            try:
                created = datetime.fromisoformat(deployment["created_at"])
                elapsed = (datetime.utcnow() - created).total_seconds()
                if elapsed > 600:  # 10 minutes
                    should_rollback = True
                    reasons.append(f"Deployment stuck for {int(elapsed)}s (> 600s threshold)")
            except (ValueError, TypeError):
                pass

        # Check recent failure rate
        recent_failures = conn.execute(
            """SELECT COUNT(*) FROM failure_log
               WHERE project_id = ? AND created_at > datetime('now', '-5 minutes')""",
            (deployment["project_id"],),
        ).fetchone()[0]

        if recent_failures > 5:
            should_rollback = True
            reasons.append(f"{recent_failures} failures in last 5 minutes")

        return {
            "deployment_id": deployment_id,
            "project_id": deployment["project_id"],
            "environment": deployment["environment"],
            "version": deployment["version"],
            "current_status": deployment["status"],
            "should_rollback": should_rollback,
            "reasons": reasons,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Deployment rollback manager")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument(
        "--environment",
        required=True,
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    parser.add_argument("--check-deployment", type=int, help="Check if a specific deployment ID needs rollback")
    parser.add_argument("--target-only", action="store_true", help="Only show rollback target, don't execute")
    parser.add_argument("--namespace", help="Kubernetes namespace override")
    parser.add_argument("--deployment-name", help="Kubernetes deployment name override")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    if args.check_deployment:
        result = auto_rollback_check(args.check_deployment, db_path)
        print(json.dumps(result, indent=2))
        if result.get("should_rollback"):
            print(f"\n[rollback] AUTO-ROLLBACK RECOMMENDED for deployment {args.check_deployment}")
            for reason in result.get("reasons", []):
                print(f"  - {reason}")
        else:
            print(f"\n[rollback] Deployment {args.check_deployment} does not need rollback")
        return

    if args.target_only:
        target = get_rollback_target(args.project, args.environment, db_path)
        print(json.dumps(target, indent=2))
        return

    result = execute_rollback(
        project_id=args.project,
        environment=args.environment,
        dry_run=args.dry_run,
        db_path=db_path,
        k8s_namespace=args.namespace,
        deployment_name=args.deployment_name,
    )
    print(json.dumps(result, indent=2))

    if result.get("status") == "succeeded":
        print(f"\n[rollback] SUCCESS: {result.get('message')}")
    elif result.get("status") == "dry_run":
        print(f"\n[rollback] DRY RUN: {result.get('message')}")
    elif result.get("status") == "partial":
        print(f"\n[rollback] PARTIAL: {result.get('warning')}")
    else:
        print(f"\n[rollback] FAILED: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
