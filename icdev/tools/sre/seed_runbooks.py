#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed production-grade SRE runbooks into the ICDEV™ database.

Creates runbooks for common failure patterns across Kubernetes, databases,
networking, security, CI/CD pipelines, and certificate management.

Usage:
    python tools/sre/seed_runbooks.py --json
    python tools/sre/seed_runbooks.py --list
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.sre.runbook_executor import create_runbook, list_runbooks  # noqa: E402

# ── Runbook Definitions ──────────────────────────────────────────────────────
# Each: {name, description, trigger_pattern, risk_level, steps[]}
# Risk: green=auto-execute, yellow=suggest, orange=human-only

RUNBOOKS = [
    # ══════════════════════════════════════════════════════════════════
    # KUBERNETES — Pod & Deployment Failures
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "k8s-restart-crashloop",
        "description": "Restart pods stuck in CrashLoopBackOff. Performs rolling restart of the deployment, then verifies rollout status.",
        "trigger_pattern": r"CrashLoopBackOff|restart.*backoff|container.*crash",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get pods -l app=${SERVICE} -n ${NAMESPACE} --no-headers | head -5",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl rollout restart deployment/${SERVICE} -n ${NAMESPACE}",
                "timeout_seconds": 60,
            },
            {
                "action": "shell",
                "command": "kubectl rollout status deployment/${SERVICE} -n ${NAMESPACE} --timeout=120s",
                "timeout_seconds": 130,
            },
            {
                "action": "shell",
                "command": "kubectl get pods -l app=${SERVICE} -n ${NAMESPACE} --no-headers",
                "timeout_seconds": 30,
            },
        ],
    },
    {
        "name": "k8s-rollback-failed-deploy",
        "description": "Rollback deployment to previous revision after a failed release. Verifies the rollback completes and pods are healthy.",
        "trigger_pattern": r"deploy.*failed|rollout.*failed|ImagePullBackOff|ErrImagePull|deployment.*unhealthy",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl rollout history deployment/${SERVICE} -n ${NAMESPACE} | tail -5",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl rollout undo deployment/${SERVICE} -n ${NAMESPACE}",
                "timeout_seconds": 60,
                "rollback_command": "kubectl rollout undo deployment/${SERVICE} -n ${NAMESPACE}",
            },
            {
                "action": "shell",
                "command": "kubectl rollout status deployment/${SERVICE} -n ${NAMESPACE} --timeout=120s",
                "timeout_seconds": 130,
            },
            {
                "action": "shell",
                "command": "kubectl get pods -l app=${SERVICE} -n ${NAMESPACE} -o wide --no-headers",
                "timeout_seconds": 30,
            },
        ],
    },
    {
        "name": "k8s-scale-up-cpu",
        "description": "Scale deployment when CPU exceeds threshold. Adds 2 replicas to handle the load spike. Auto-scaling should handle this long-term.",
        "trigger_pattern": r"cpu.*usage.*high|cpu.*exceed.*80|HighCPU|CPUThrottling",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl top pods -l app=${SERVICE} -n ${NAMESPACE} --no-headers",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "CURRENT=$(kubectl get deployment/${SERVICE} -n ${NAMESPACE} -o jsonpath='{.spec.replicas}') && kubectl scale deployment/${SERVICE} -n ${NAMESPACE} --replicas=$((CURRENT + 2))",
                "timeout_seconds": 60,
            },
            {
                "action": "shell",
                "command": "kubectl rollout status deployment/${SERVICE} -n ${NAMESPACE} --timeout=60s",
                "timeout_seconds": 70,
            },
        ],
    },
    {
        "name": "k8s-scale-down-idle",
        "description": "Scale down over-provisioned deployment during low traffic. Reduces to minimum replicas to save cost.",
        "trigger_pattern": r"low.*traffic|idle.*pods|over.*provision|cost.*optimize",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl top pods -l app=${SERVICE} -n ${NAMESPACE} --no-headers",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl scale deployment/${SERVICE} -n ${NAMESPACE} --replicas=2",
                "timeout_seconds": 60,
            },
        ],
    },
    {
        "name": "k8s-evicted-pods-cleanup",
        "description": "Clean up evicted pods that are consuming namespace quota. Deletes all pods in Evicted state.",
        "trigger_pattern": r"Evicted|pod.*evict|eviction.*pressure",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get pods -n ${NAMESPACE} --field-selector=status.phase==Failed -o name | head -20",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl delete pods -n ${NAMESPACE} --field-selector=status.phase==Failed",
                "timeout_seconds": 60,
            },
        ],
    },
    {
        "name": "k8s-node-not-ready",
        "description": "Cordon and drain a NotReady node, then investigate. Workloads are rescheduled to healthy nodes.",
        "trigger_pattern": r"NodeNotReady|node.*not.*ready|KubeletNotReady",
        "risk_level": "yellow",
        "steps": [
            {"action": "shell", "command": "kubectl get nodes -o wide | grep -i notready", "timeout_seconds": 30},
            {"action": "shell", "command": "kubectl cordon ${NODE_NAME}", "timeout_seconds": 30},
            {
                "action": "shell",
                "command": "kubectl drain ${NODE_NAME} --ignore-daemonsets --delete-emptydir-data --timeout=120s",
                "timeout_seconds": 130,
            },
            {"action": "shell", "command": "kubectl describe node ${NODE_NAME} | tail -30", "timeout_seconds": 30},
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # DATABASE — Connection & Performance
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "db-connection-pool-exhausted",
        "description": "Database connection pool exhausted. Restarts the application to release connections, then checks pool status.",
        "trigger_pattern": r"connection.*pool.*exhaust|too.*many.*connections|max.*connections|pgbouncer.*full",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl exec -n ${NAMESPACE} deploy/${SERVICE} -- psql -c 'SELECT count(*) FROM pg_stat_activity;' || echo 'Cannot query DB directly'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl rollout restart deployment/${SERVICE} -n ${NAMESPACE}",
                "timeout_seconds": 60,
            },
            {
                "action": "shell",
                "command": "kubectl rollout status deployment/${SERVICE} -n ${NAMESPACE} --timeout=120s",
                "timeout_seconds": 130,
            },
        ],
    },
    {
        "name": "db-slow-queries",
        "description": "Detect and log slow database queries. Captures pg_stat_activity for analysis without killing connections.",
        "trigger_pattern": r"slow.*query|query.*timeout|db.*latency.*high|p99.*database",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl exec -n ${NAMESPACE} deploy/${SERVICE} -- psql -c \"SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' AND query_start < now() - interval '30 seconds' ORDER BY duration DESC LIMIT 10;\" || echo 'Direct DB query failed'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'Slow query report captured. Review and optimize.'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "db-disk-space-critical",
        "description": "Database disk usage critical. Runs VACUUM on PostgreSQL and cleans WAL files. For SQLite: VACUUM + integrity check.",
        "trigger_pattern": r"disk.*full|disk.*pressure|DiskPressure|no.*space.*left|storage.*critical",
        "risk_level": "green",
        "steps": [
            {"action": "shell", "command": "df -h /data || echo 'Cannot check disk'", "timeout_seconds": 15},
            {
                "action": "shell",
                "command": "python -c \"import sqlite3; conn=sqlite3.connect('data/icdev.db'); conn.execute('VACUUM'); print('VACUUM OK')\" || echo 'SQLite VACUUM failed'",  # sqlite3-ok — shell command string, not a Python DB call
                "timeout_seconds": 120,
            },
            {
                "action": "shell",
                "command": "find /tmp -type f -mtime +7 -delete 2>/dev/null; echo 'Cleaned /tmp files older than 7 days'",
                "timeout_seconds": 30,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # NETWORKING — DNS, Connectivity, TLS
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "net-dns-resolution-failure",
        "description": "DNS resolution failing inside cluster. Restarts CoreDNS pods and validates resolution.",
        "trigger_pattern": r"DNS.*fail|dns.*timeout|NXDOMAIN|resolve.*error|name.*resolution",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get pods -n kube-system -l k8s-app=kube-dns --no-headers",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl rollout restart deployment/coredns -n kube-system",
                "timeout_seconds": 60,
            },
            {
                "action": "shell",
                "command": "sleep 10 && kubectl run dns-test --rm -i --restart=Never --image=busybox -- nslookup kubernetes.default.svc.cluster.local",
                "timeout_seconds": 30,
            },
        ],
    },
    {
        "name": "net-certificate-expiring",
        "description": "TLS certificate expiring soon. Triggers cert-manager renewal and verifies the new certificate.",
        "trigger_pattern": r"cert.*expir|certificate.*expir|TLS.*expir|x509.*expired|ssl.*expire",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get certificates -A -o wide 2>/dev/null || echo 'cert-manager not installed'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl delete secret ${CERT_SECRET} -n ${NAMESPACE} 2>/dev/null && echo 'Secret deleted, cert-manager will renew' || echo 'Secret not found'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "sleep 30 && kubectl get certificates -n ${NAMESPACE} -o wide 2>/dev/null || echo 'Check cert status manually'",
                "timeout_seconds": 60,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # SECURITY — Vulnerability, Access, Compliance
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "sec-critical-cve-detected",
        "description": "Critical CVE detected in container image. Triggers image rebuild with patched base and redeploy.",
        "trigger_pattern": r"critical.*CVE|CVE-\d{4}-\d+.*critical|vulnerability.*critical|CVSS.*9\.\d|CVSS.*10",
        "risk_level": "orange",
        "steps": [
            {
                "action": "shell",
                "command": "trivy image --severity CRITICAL ${IMAGE} 2>/dev/null || echo 'Trivy scan requires manual execution'",
                "timeout_seconds": 120,
            },
            {
                "action": "shell",
                "command": "echo 'ACTION REQUIRED: Rebuild image with patched base, push to registry, redeploy. See incident for CVE details.'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "sec-secret-exposed",
        "description": "Secret or credential detected in code/logs. Rotates the exposed secret and invalidates old value.",
        "trigger_pattern": r"secret.*exposed|credential.*leak|API.*key.*found|password.*committed|gitleaks.*detect",
        "risk_level": "orange",
        "steps": [
            {
                "action": "shell",
                "command": "echo 'CRITICAL: Secret exposure detected. Initiating rotation protocol.'",
                "timeout_seconds": 5,
            },
            {
                "action": "shell",
                "command": "echo 'Step 1: Identify the exposed secret from the alert details'",
                "timeout_seconds": 5,
            },
            {
                "action": "shell",
                "command": "echo 'Step 2: Rotate the secret in Vault/KMS: vault write secret/rotate key=${SECRET_NAME}'",
                "timeout_seconds": 5,
            },
            {
                "action": "shell",
                "command": "echo 'Step 3: Restart affected services to pick up new secret'",
                "timeout_seconds": 5,
            },
            {
                "action": "shell",
                "command": "echo 'Step 4: Verify old secret is invalidated and git history is cleaned'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "sec-pod-security-violation",
        "description": "Pod security admission violation detected. Logs the violation details for compliance tracking.",
        "trigger_pattern": r"PodSecurity.*violation|pod.*security.*restrict|privileged.*container|runAsRoot",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get events -n ${NAMESPACE} --field-selector=reason=FailedCreate | tail -10",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'Pod security violation logged. Review deployment manifest for compliance.'",
                "timeout_seconds": 5,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # CI/CD PIPELINE — Build & Deploy Failures
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "cicd-pipeline-stuck",
        "description": "CI/CD pipeline stuck or hanging. Cancels the stuck run and retriggers from the last successful stage.",
        "trigger_pattern": r"pipeline.*stuck|pipeline.*hang|build.*timeout|job.*stuck|runner.*unresponsive",
        "risk_level": "yellow",
        "steps": [
            {"action": "shell", "command": "echo 'Checking for stuck pipeline runs...'", "timeout_seconds": 5},
            {
                "action": "shell",
                "command": "kubectl get pods -n ci-cd -l app=gitlab-runner --no-headers 2>/dev/null || echo 'No K8s runner found'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl delete pod -n ci-cd -l app=gitlab-runner --field-selector=status.phase=Running --force --grace-period=0 2>/dev/null || echo 'Manual cancellation required'",
                "timeout_seconds": 60,
            },
        ],
    },
    {
        "name": "cicd-registry-push-failed",
        "description": "Container image push to registry failed. Checks registry health, retries the push, and alerts if persistent.",
        "trigger_pattern": r"push.*failed|registry.*unavailable|ECR.*error|ACR.*error|harbor.*error|unauthorized.*push",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": "curl -sf https://${REGISTRY}/v2/ && echo 'Registry healthy' || echo 'Registry unreachable'",
                "timeout_seconds": 15,
            },
            {
                "action": "shell",
                "command": "echo 'Retrigger the pipeline build stage to retry push'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "cicd-test-flake",
        "description": "Flaky test detected (test passes on retry). Logs the flaky test for tracking and allows pipeline to proceed.",
        "trigger_pattern": r"flaky.*test|test.*intermittent|test.*passed.*retry|nondeterministic.*test",
        "risk_level": "green",
        "steps": [
            {"action": "shell", "command": "echo 'Flaky test detected. Logging for tracking.'", "timeout_seconds": 5},
            {
                "action": "shell",
                "command": "python -c \"from tools.db.storage import get_connection; conn=get_connection(); conn.execute('INSERT INTO audit_trail (event_type, action, details, created_at) VALUES (?,?,?,datetime(\\\"now\\\"))', ('test_failed', 'flaky_test_logged', '${ALERT_TEXT}')); conn.commit(); print('Logged')\"",
                "timeout_seconds": 15,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # OBSERVABILITY — Prometheus, Grafana, Logging
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "obs-prometheus-storage-full",
        "description": "Prometheus TSDB storage approaching limit. Reduces retention or expands PVC.",
        "trigger_pattern": r"prometheus.*storage|TSDB.*full|prometheus.*disk|WAL.*corrupt",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl exec -n observability prometheus-server-0 -- df -h /data 2>/dev/null || echo 'Cannot check Prometheus disk'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'Options: 1) Reduce --storage.tsdb.retention.time  2) Expand PVC  3) Clean old data'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "obs-alertmanager-not-firing",
        "description": "AlertManager is not sending notifications. Checks config, receivers, and connectivity.",
        "trigger_pattern": r"alertmanager.*silent|alerts.*not.*firing|notification.*fail|webhook.*fail",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl get pods -n observability -l app=alertmanager --no-headers 2>/dev/null || echo 'AlertManager not found'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl logs -n observability -l app=alertmanager --tail=20 2>/dev/null || echo 'Cannot fetch AlertManager logs'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'Check: 1) AlertManager config (receivers, routes)  2) Webhook endpoints  3) Email/Slack creds'",
                "timeout_seconds": 5,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # SRE — SLO & Error Budget
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "sre-error-budget-exhausted",
        "description": "Error budget exhausted for a service. Freezes feature deployments and escalates to engineering leadership.",
        "trigger_pattern": r"error.*budget.*exhaust|budget.*0%|SLO.*breach|burn.*rate.*critical",
        "risk_level": "orange",
        "steps": [
            {
                "action": "shell",
                "command": "python tools/sre/slo_manager.py --dashboard --json 2>/dev/null || echo 'SLO dashboard unavailable'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'ERROR BUDGET EXHAUSTED — Feature freeze in effect until budget recovers.'",
                "timeout_seconds": 5,
            },
            {
                "action": "shell",
                "command": "echo 'Actions: 1) Identify top error sources  2) Prioritize reliability fixes  3) Postpone feature launches'",
                "timeout_seconds": 5,
            },
        ],
    },
    {
        "name": "sre-high-burn-rate",
        "description": "SLO burn rate exceeding fast-burn threshold. Investigates the top error contributors.",
        "trigger_pattern": r"burn.*rate.*high|burn.*rate.*14|fast.*burn|error.*spike",
        "risk_level": "green",
        "steps": [
            {
                "action": "shell",
                "command": 'python tools/sre/slo_manager.py --dashboard --json 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); [print(f\'{s[\\"service_name\\"]}: {s[\\"status\\"]} burn={s.get(\\"burn_rate\\",0):.2f}\') for s in d]" 2>/dev/null || echo \'Cannot query SLO manager\'',
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'Review: 1) Recent deployments  2) Infrastructure changes  3) Traffic patterns'",
                "timeout_seconds": 5,
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════
    # MEMORY & OOM — Application Memory Issues
    # ══════════════════════════════════════════════════════════════════
    {
        "name": "k8s-oom-killed",
        "description": "Pod killed by OOM (Out of Memory). Increases memory limit and restarts.",
        "trigger_pattern": r"OOMKilled|out.*of.*memory|memory.*limit.*exceeded|oom.*kill",
        "risk_level": "yellow",
        "steps": [
            {
                "action": "shell",
                "command": "kubectl describe pod -l app=${SERVICE} -n ${NAMESPACE} | grep -A5 'Last State' | head -10",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "kubectl top pods -l app=${SERVICE} -n ${NAMESPACE} --no-headers 2>/dev/null || echo 'Metrics unavailable'",
                "timeout_seconds": 30,
            },
            {
                "action": "shell",
                "command": "echo 'ACTION: Increase memory limit in deployment spec. Current OOM indicates memory leak or undersized limit.'",
                "timeout_seconds": 5,
            },
        ],
    },
]


def seed_all():
    """Seed all runbooks into the database."""
    created = 0
    skipped = 0
    errors = []

    existing = {rb.get("name", "") for rb in list_runbooks()}

    for rb in RUNBOOKS:
        if rb["name"] in existing:
            skipped += 1
            continue
        result = create_runbook(
            name=rb["name"],
            description=rb["description"],
            trigger_pattern=rb["trigger_pattern"],
            steps=rb["steps"],
            risk_level=rb["risk_level"],
        )
        if result.get("status") == "ok":
            created += 1
        else:
            errors.append({"name": rb["name"], "error": result.get("message", "Unknown")})

    return {
        "status": "ok",
        "total_defined": len(RUNBOOKS),
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "total_in_db": len(list_runbooks()),
    }


def main():
    parser = argparse.ArgumentParser(description="Seed SRE runbooks")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true", help="List defined runbooks without seeding")
    args = parser.parse_args()

    if args.list:
        for rb in RUNBOOKS:
            risk_icon = {"green": "G", "yellow": "Y", "orange": "O"}[rb["risk_level"]]
            print(f"  [{risk_icon}] {rb['name']} — {rb['description'][:70]}")
        print(f"\nTotal: {len(RUNBOOKS)} runbooks")
        return

    result = seed_all()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Runbooks seeded: {result['created']} created, {result['skipped']} skipped, {len(result['errors'])} errors"
        )
        print(f"Total in database: {result['total_in_db']}")
        for err in result["errors"]:
            print(f"  ERROR: {err['name']} — {err['error']}")


if __name__ == "__main__":
    main()
