"""Readiness Checker — MDC Workflow Step 2.

Validates migration readiness: landing zone, compliance gate, prereqs.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "mdc"

_DEFAULT_CHECKS = {
    "landing_zone_vpc": True,
    "landing_zone_subnets": True,
    "landing_zone_dns": False,
    "dms_subnet_group": False,
    "migration_user_iam": True,
    "discovery_agent_deployed": False,
    "compliance_gate_status": "partial",
    "aws_sct_installed": True,
    "target_engine": "aurora-postgresql",
}


def run_readiness_checks(project_id: str) -> dict:
    """Load readiness check state from DB; fall back to defaults if absent."""
    checks = dict(_DEFAULT_CHECKS)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM mdc_readiness WHERE project_id = %s ORDER BY created_at DESC LIMIT 20",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    if key in ("landing_zone_vpc", "landing_zone_subnets", "landing_zone_dns",
                               "dms_subnet_group", "migration_user_iam", "discovery_agent_deployed",
                               "aws_sct_installed"):
                        checks[key] = str(val).lower() in ("1", "true", "yes")
                    else:
                        checks[key] = val
    except Exception:
        pass  # table may not exist — use defaults

    findings = []

    def _chk(key, label, severity="fail"):
        val = checks.get(key, False)
        if not val:
            findings.append({"severity": severity, "check": key, "message": f"{label} not confirmed"})
        return val

    _chk("landing_zone_vpc", "AWS Landing Zone VPC (172.16.0.0/16)", "fail")
    _chk("landing_zone_subnets", "Landing Zone private subnets", "fail")
    _chk("landing_zone_dns", "Landing Zone DNS resolution", "warn")
    _chk("dms_subnet_group", "DMS replication subnet group", "fail")
    _chk("migration_user_iam", "Migration IAM user/role", "fail")
    _chk("discovery_agent_deployed", "Discovery agent deployed on source", "warn")
    _chk("aws_sct_installed", "AWS SCT installed on migration host", "warn")

    compliance = checks.get("compliance_gate_status", "unknown")
    if compliance not in ("pass", "complete"):
        findings.append({
            "severity": "warn",
            "check": "compliance_gate",
            "message": f"Compliance gate status is '{compliance}' — must be 'pass' before go-live",
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
        "# Migration Readiness Report — MDC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'PASS' if gate == 'PASS' else 'FAIL'}",
        "",
        "## Landing Zone Checks",
        "| Check | Status |",
        "|-------|--------|",
        f"| VPC (172.16.0.0/16) present | {yn('landing_zone_vpc')} |",
        f"| Private subnets present | {yn('landing_zone_subnets')} |",
        f"| DNS resolution configured | {yn('landing_zone_dns')} |",
        "",
        "## DMS & Migration Checks",
        "| Check | Status |",
        "|-------|--------|",
        f"| DMS replication subnet group | {yn('dms_subnet_group')} |",
        f"| Migration IAM user/role | {yn('migration_user_iam')} |",
        f"| Discovery agent deployed | {yn('discovery_agent_deployed')} |",
        f"| AWS SCT installed | {yn('aws_sct_installed')} |",
        f"| Target engine | {checks.get('target_engine', 'unknown')} |",
        f"| Compliance gate | {checks.get('compliance_gate_status', 'unknown')} |",
        "",
    ]
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
        lines += ["## All Checks Passed", "", "Migration landing zone is ready."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Readiness Checker — MDC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="mdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_readiness_checks(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"readiness_report_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": result["gate"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": [
                {"name": "Readiness Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
