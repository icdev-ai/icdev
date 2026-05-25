#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Audit Reflex — self-scan: code quality, security, compliance.

Runs existing ICDEV™ analysis tools against the codebase and aggregates
findings into an audit report.  Non-destructive, read-only (GREEN tier).

Scanner-tier only (zero Claude tokens).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_tool(cmd: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Run a tool as subprocess, capture JSON output."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        # Try to parse JSON from stdout
        stdout = result.stdout.strip()
        # Skip PostgreSQL fallback messages
        json_start = stdout.find("{")
        if json_start >= 0:
            try:
                return {"success": True, "data": json.loads(stdout[json_start:])}
            except json.JSONDecodeError:
                pass
        # Try finding JSON array
        json_start = stdout.find("[")
        if json_start >= 0:
            try:
                return {"success": True, "data": json.loads(stdout[json_start:])}
            except json.JSONDecodeError:
                pass
        return {"success": True, "data": {"raw_output": stdout[:2000]}}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _check_code_quality() -> Dict[str, Any]:
    """Run code_analyzer.py on tools/ directory."""
    print("  Audit: code quality scan...")
    result = _run_tool(
        [
            sys.executable,
            "tools/analysis/code_analyzer.py",
            "--project-dir",
            "tools/",
            "--json",
        ],
        timeout=180,
    )

    if result.get("success") and isinstance(result.get("data"), dict):
        data = result["data"]
        return {
            "check": "code_quality",
            "status": "completed",
            "findings": {
                "total_files": data.get("total_files", 0),
                "total_functions": data.get("total_functions", 0),
                "avg_complexity": data.get("avg_cyclomatic", 0),
                "maintainability": data.get("maintainability_score", 0),
                "smells": data.get("total_smells", 0),
            },
        }
    return {"check": "code_quality", "status": "failed", "error": result.get("error", "unknown")}


def _check_security() -> Dict[str, Any]:
    """Run SAST scanner on tools/ directory."""
    print("  Audit: security scan...")
    result = _run_tool(
        [
            sys.executable,
            "tools/security/sast_runner.py",
            "--project-dir",
            "tools/",
            "--json",
        ],
        timeout=180,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "security",
            "status": "completed",
            "findings": {
                "total_issues": data.get("total_issues", 0),
                "critical": data.get("critical", 0),
                "high": data.get("high", 0),
                "medium": data.get("medium", 0),
                "low": data.get("low", 0),
            },
        }
    return {"check": "security", "status": "failed", "error": result.get("error", "unknown")}


def _check_secret_detection() -> Dict[str, Any]:
    """Run secret detector on the project."""
    print("  Audit: secret detection...")
    result = _run_tool(
        [
            sys.executable,
            "tools/security/secret_detector.py",
            "--project-dir",
            str(BASE_DIR),
            "--json",
        ],
        timeout=120,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "secret_detection",
            "status": "completed",
            "findings": {
                "secrets_found": data.get("total_findings", data.get("secrets_found", 0)),
            },
        }
    return {"check": "secret_detection", "status": "failed", "error": result.get("error", "unknown")}


def _check_dependency_audit() -> Dict[str, Any]:
    """Run dependency auditor."""
    print("  Audit: dependency audit...")
    result = _run_tool(
        [
            sys.executable,
            "tools/security/dependency_auditor.py",
            "--project-dir",
            str(BASE_DIR),
            "--json",
        ],
        timeout=120,
    )

    if result.get("success"):
        data = result.get("data", {})
        return {
            "check": "dependency_audit",
            "status": "completed",
            "findings": {
                "total_deps": data.get("total_dependencies", 0),
                "vulnerable": data.get("vulnerable_count", 0),
                "outdated": data.get("outdated_count", 0),
            },
        }
    return {"check": "dependency_audit", "status": "failed", "error": result.get("error", "unknown")}


def _check_coherence() -> Dict[str, Any]:
    """Run implementation coherence checker (D-WF-8)."""
    print("  Audit: coherence check...")
    try:
        from tools.workflow.coherence_checker import run_checks as coherence_check

        coherence_report = coherence_check()
        return {
            "check": "coherence",
            "status": "completed",
            "findings": {
                "overall_pass": coherence_report.overall_pass,
                "failed": coherence_report.failed_checks,
                "warned": coherence_report.warned_checks,
                "passed": coherence_report.passed_checks,
                "total": coherence_report.total_checks,
            },
        }
    except Exception as e:
        return {"check": "coherence", "status": "failed", "error": str(e)}


def _generate_audit_report(checks: List[Dict]) -> str:
    """Generate markdown audit report."""
    now = _utcnow()
    completed = [c for c in checks if c.get("status") == "completed"]
    _failed = [c for c in checks if c.get("status") == "failed"]  # noqa: F841

    lines = [
        "# Genesis Self-Audit Report",
        "",
        f"**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Checks Completed:** {len(completed)}/{len(checks)}",
        "**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
        "## Results",
        "",
        "| Check | Status | Key Finding |",
        "|-------|--------|-------------|",
    ]

    for check in checks:
        name = check.get("check", "unknown")
        status = check.get("status", "unknown")
        findings = check.get("findings", {})
        key = ""
        if findings:
            # Pick the most important finding
            for k in ["secrets_found", "critical", "smells", "vulnerable", "total_issues"]:
                if k in findings and findings[k]:
                    key = f"{k}: {findings[k]}"
                    break
            if not key:
                key = json.dumps(findings)[:60]
        elif check.get("error"):
            key = f"Error: {check['error'][:40]}"

        status_icon = "PASS" if status == "completed" else "FAIL"
        lines.append(f"| {name} | {status_icon} | {key} |")

    lines.extend(["", "---", ""])

    # Details per check
    for check in checks:
        lines.append(f"### {check.get('check', 'unknown')}")
        if check.get("findings"):
            for k, v in check["findings"].items():
                lines.append(f"- **{k}:** {v}")
        elif check.get("error"):
            lines.append(f"- **Error:** {check['error']}")
        lines.append("")

    lines.extend(["---", "", "*Generated by Genesis Audit Reflex*"])
    return "\n".join(lines)


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Audit Reflex."""
    enabled_checks = config.get(
        "checks",
        [
            "code_quality",
            "security_scan",
            "secret_detection",
            "dependency_audit",
            "coherence",
        ],
    )

    check_map = {
        "code_quality": _check_code_quality,
        "security_scan": _check_security,
        "secret_detection": _check_secret_detection,
        "dependency_audit": _check_dependency_audit,
        "coherence": _check_coherence,
    }

    checks = []
    for check_name in enabled_checks:
        # Normalize name
        normalized = check_name.replace("_check", "").replace("_scan", "_scan")
        func = check_map.get(normalized) or check_map.get(check_name)
        if func:
            try:
                result = func()
                checks.append(result)
            except Exception as e:
                checks.append({"check": check_name, "status": "failed", "error": str(e)})

    # Generate report
    report_md = _generate_audit_report(checks)
    reports_dir = BASE_DIR / "data" / "genesis" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = _utcnow().strftime("%Y-%m-%d")
    report_file = reports_dir / f"audit-{date_str}.md"
    report_file.write_text(report_md, encoding="utf-8")

    completed = len([c for c in checks if c.get("status") == "completed"])

    return {
        "success": True,  # Audit always "succeeds" — it's informational
        "metric_value": float(completed),
        "details": {
            "checks_completed": completed,
            "checks_failed": len(checks) - completed,
            "report_file": str(report_file),
            "checks": checks,
        },
    }
