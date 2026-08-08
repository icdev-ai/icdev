"""Runbook Checker — OHC Workflow Step 2.

Validates runbook completeness and automation readiness.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ohc"

_DEFAULT_CHECKS = {
    "runbooks_for_top_incidents": False,
    "runbook_test_coverage_pct": 20.0,
    "ssm_documents_deployed": False,
    "lambda_automation_present": False,
    "runbook_count": 8,
    "tested_runbook_count": 2,
    "ssm_document_count": 0,
    "automation_lambda_count": 0,
    "incident_playbook_linked": False,
    "chaos_test_runbook_present": False,
}

_TOP_INCIDENT_CATEGORIES = [
    "service_restart", "disk_full", "memory_leak", "db_connection_exhausted",
    "ssl_cert_expiry", "load_balancer_5xx", "lambda_timeout", "pod_crashloop",
    "api_gateway_throttle", "network_partition",
]


def run_runbook_checks(project_id: str) -> dict:
    """Load runbook check state from DB; fall back to defaults if absent."""
    checks = dict(_DEFAULT_CHECKS)
    try:
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM ohc_runbooks WHERE project_id = %s ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                bool_keys = {"runbooks_for_top_incidents", "ssm_documents_deployed",
                             "lambda_automation_present", "incident_playbook_linked",
                             "chaos_test_runbook_present"}
                numeric_keys = {"runbook_test_coverage_pct", "runbook_count", "tested_runbook_count",
                                "ssm_document_count", "automation_lambda_count"}
                for row in rows:
                    key, val = row[0], row[1]
                    if key in bool_keys:
                        checks[key] = str(val).lower() in ("1", "true", "yes")
                    elif key in numeric_keys:
                        try:
                            checks[key] = float(val)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass  # table may not exist — use defaults

    findings = []

    # Top 10 incident runbooks
    if not checks.get("runbooks_for_top_incidents", False):
        findings.append({
            "severity": "fail",
            "check": "runbooks_for_top_incidents",
            "message": f"Runbooks for top 10 incident categories not all present ({', '.join(_TOP_INCIDENT_CATEGORIES[:5])}...)",
        })

    # Test coverage
    coverage = checks.get("runbook_test_coverage_pct", 0)
    if coverage < 50:
        findings.append({
            "severity": "warn",
            "check": "runbook_test_coverage",
            "message": f"Runbook test coverage is {coverage}% — target is 50% minimum",
        })

    # SSM documents
    if not checks.get("ssm_documents_deployed", False):
        findings.append({
            "severity": "fail",
            "check": "ssm_documents_deployed",
            "message": "No SSM automation documents deployed — runbooks cannot execute automatically",
        })

    # Lambda automation
    if not checks.get("lambda_automation_present", False):
        findings.append({
            "severity": "warn",
            "check": "lambda_automation_present",
            "message": "No Lambda automation functions present — remediation relies on manual runbooks only",
        })

    # Incident playbook linking
    if not checks.get("incident_playbook_linked", False):
        findings.append({
            "severity": "warn",
            "check": "incident_playbook_linked",
            "message": "Runbooks not linked to incident management system (PagerDuty/OpsGenie)",
        })

    gate = "PASS" if not any(f["severity"] == "fail" for f in findings) else "FAIL"
    return {"findings": findings, "checks": checks, "gate": gate, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    checks = result["checks"]
    gate = result["gate"]
    project_id = result["project_id"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]

    def yn(key):
        return "YES" if checks.get(key) else "NO"

    lines = [
        "# Runbook Completeness Report — OHC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'PASS' if gate == 'PASS' else 'FAIL'}",
        "",
        "## Runbook Inventory",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Runbooks | {int(checks.get('runbook_count', 0))} |",
        f"| Tested Runbooks | {int(checks.get('tested_runbook_count', 0))} |",
        f"| Test Coverage | {checks.get('runbook_test_coverage_pct', 0)}% |",
        f"| SSM Documents | {int(checks.get('ssm_document_count', 0))} |",
        f"| Automation Lambdas | {int(checks.get('automation_lambda_count', 0))} |",
        "",
        "## Runbook Readiness Checks",
        "| Check | Status |",
        "|-------|--------|",
        f"| Runbooks for Top 10 Incidents | {yn('runbooks_for_top_incidents')} |",
        f"| SSM Documents Deployed | {yn('ssm_documents_deployed')} |",
        f"| Lambda Automation Present | {yn('lambda_automation_present')} |",
        f"| Incident Playbook Linked | {yn('incident_playbook_linked')} |",
        f"| Chaos Test Runbook | {yn('chaos_test_runbook_present')} |",
        "",
        "## Top 10 Incident Categories",
        "| # | Incident Type |",
        "|---|---------------|",
    ]
    for i, cat in enumerate(_TOP_INCIDENT_CATEGORIES, 1):
        lines.append(f"| {i} | {cat.replace('_', ' ').title()} |")

    lines.append("")
    if fails:
        lines += ["## Failures (Blocking)"]
        for f in fails:
            lines.append(f"- FAIL [{f['check']}]: {f['message']}")
        lines.append("")
    if warns:
        lines += ["## Warnings"]
        for f in warns:
            lines.append(f"- WARN [{f['check']}]: {f['message']}")
        lines.append("")
    if not findings:
        lines += ["## All Runbook Checks Passed", "", "Runbook automation is production-ready."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Runbook Checker — OHC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="ohc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_runbook_checks(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"runbook_report_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": result["gate"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": [
                {"name": "Runbook Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
