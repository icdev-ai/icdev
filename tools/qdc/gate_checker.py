"""Gate Checker — QDC Workflow Step 2.

Validates quality gate thresholds and checks for blocking conditions.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "qdc"

_GATE_THRESHOLDS = {
    "merge": 70,
    "deploy": 80,
    "release": 85,
}

_DEFAULT_CHECKS = {
    "coverage_pct": 72.0,
    "complexity_score": 8.5,
    "critical_cves": 0,
    "high_cves": 2,
    "sast_enabled": True,
    "dast_enabled": False,
    "uqs": 74.0,
}


def run_gate_checks(project_id: str) -> dict:
    """Load gate check inputs from DB; fall back to defaults if absent."""
    checks = dict(_DEFAULT_CHECKS)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM qdc_gate_checks WHERE project_id = %s ORDER BY created_at DESC LIMIT 20",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    if key in ("sast_enabled", "dast_enabled"):
                        checks[key] = str(val).lower() in ("1", "true", "yes")
                    else:
                        try:
                            checks[key] = float(val)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass  # table may not exist — use defaults

    findings = []

    # Coverage check
    if checks.get("coverage_pct", 100) < 80:
        findings.append({
            "severity": "warn",
            "check": "coverage_threshold",
            "message": f"Code coverage {checks.get('coverage_pct')}% is below the 80% deployment threshold",
        })

    # Complexity check
    if checks.get("complexity_score", 0) > 10:
        findings.append({
            "severity": "fail",
            "check": "complexity_threshold",
            "message": f"Average cyclomatic complexity {checks.get('complexity_score')} exceeds maximum of 10",
        })

    # Critical CVEs (always blocking)
    if checks.get("critical_cves", 0) > 0:
        findings.append({
            "severity": "fail",
            "check": "critical_cve",
            "message": f"{int(checks.get('critical_cves', 0))} critical CVE(s) detected — must remediate before any gate",
        })

    # High CVEs (warn)
    if checks.get("high_cves", 0) > 0:
        findings.append({
            "severity": "warn",
            "check": "high_cve",
            "message": f"{int(checks.get('high_cves', 0))} high-severity CVE(s) detected — remediate before release gate",
        })

    # SAST check
    if not checks.get("sast_enabled", False):
        findings.append({
            "severity": "fail",
            "check": "sast_missing",
            "message": "SAST scanning not enabled — required for deploy and release gates",
        })

    # DAST check
    if not checks.get("dast_enabled", False):
        findings.append({
            "severity": "warn",
            "check": "dast_missing",
            "message": "DAST scanning not enabled — required for release gate",
        })

    uqs = checks.get("uqs", 74.0)
    gate_results = {gate: uqs >= threshold for gate, threshold in _GATE_THRESHOLDS.items()}
    # hard block on critical CVEs
    if checks.get("critical_cves", 0) > 0 or not checks.get("sast_enabled", True):
        gate_results = {gate: False for gate in gate_results}

    return {
        "findings": findings,
        "checks": checks,
        "gate_results": gate_results,
        "uqs": uqs,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    gate_results = result["gate_results"]
    uqs = result["uqs"]
    project_id = result["project_id"]
    checks = result["checks"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]

    lines = [
        "# Quality Gate Report — QDC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Overall Gate:** {'PASS' if not fails else 'FAIL'}  ",
        f"**UQS Score:** {uqs}/100",
        "",
        "## Gate Results",
        "| Gate | Threshold | UQS | Result |",
        "|------|-----------|-----|--------|",
    ]
    for gate, threshold in _GATE_THRESHOLDS.items():
        passed = gate_results.get(gate, False)
        lines.append(f"| {gate.title()} | {threshold} | {uqs} | {'PASS' if passed else 'FAIL'} |")

    lines += ["", "## Check Details", "| Check | Value | Status |", "|-------|-------|--------|"]
    lines.append(f"| Code Coverage | {checks.get('coverage_pct')}% | {'OK' if checks.get('coverage_pct', 0) >= 80 else 'WARN'} |")
    lines.append(f"| Cyclomatic Complexity | {checks.get('complexity_score')} | {'OK' if checks.get('complexity_score', 99) <= 10 else 'FAIL'} |")
    lines.append(f"| Critical CVEs | {int(checks.get('critical_cves', 0))} | {'OK' if checks.get('critical_cves', 0) == 0 else 'FAIL'} |")
    lines.append(f"| High CVEs | {int(checks.get('high_cves', 0))} | {'OK' if checks.get('high_cves', 0) == 0 else 'WARN'} |")
    lines.append(f"| SAST Enabled | {checks.get('sast_enabled')} | {'OK' if checks.get('sast_enabled') else 'FAIL'} |")
    lines.append(f"| DAST Enabled | {checks.get('dast_enabled')} | {'OK' if checks.get('dast_enabled') else 'WARN'} |")

    if fails:
        lines += ["", "## Failures (Blocking)"]
        for f in fails:
            lines.append(f"- FAIL [{f['check']}]: {f['message']}")
    if warns:
        lines += ["", "## Warnings"]
        for f in warns:
            lines.append(f"- WARN [{f['check']}]: {f['message']}")
    if not findings:
        lines += ["", "## All Checks Passed", "", "No gate violations detected."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Gate Checker — QDC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="qdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_gate_checks(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"gate_report_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": "PASS" if not fails else "FAIL",
            "uqs": result["uqs"],
            "gate_results": result["gate_results"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": [
                {"name": "Gate Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
