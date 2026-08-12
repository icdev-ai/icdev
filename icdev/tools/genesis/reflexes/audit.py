#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Audit Reflex — self-scan: code quality, security, compliance.

Runs existing ICDEV™ analysis tools against the codebase and aggregates
findings into an audit report.  Non-destructive, read-only (GREEN tier).

Uses LLM-based anomaly detection (AI-ify opp-5255) to interpret findings
instead of hardcoded field priorities or numeric thresholds.  Gracefully
degrades to raw-findings mode when the LLM is unavailable.
"""
IMPLEMENTATION_STATUS = "full"

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _check_chain_integrity() -> Dict[str, Any]:
    """Sweep the audit_trail hash chain (exa-audit-04).

    Rides this reflex's existing daily cadence rather than adding a scheduler:
    the daemon already dispatches ``audit`` from ``REFLEX_NAMES``, so a check
    registered here actually runs, whereas a new entry in ``reflex_registry.py``
    would schedule nothing.

    ``broken`` is the only field that means tampering. ``pre_cutover`` and
    ``unchained`` are reported so the anomaly detector can see the ratio, but a
    non-zero count in either is the expected steady state on this deployment and
    must not read as a finding.
    """
    print("  Audit: audit-chain integrity sweep...")
    try:
        from tools.audit.chain_sweep import sweep_chain

        report = sweep_chain()
        counts = report.get("counts", {})
        return {
            "check": "chain_integrity",
            "status": "completed",
            "findings": {
                "chain_health": report.get("chain_health", "unknown"),
                "broken": counts.get("broken", 0),
                "verified": counts.get("verified", 0),
                "pre_cutover": counts.get("pre_cutover", 0),
                "unchained": counts.get("unchained", 0),
                "cutover_authoritative": (report.get("cutover") or {}).get("authoritative", False),
            },
        }
    except Exception as e:
        return {"check": "chain_integrity", "status": "failed", "error": str(e)}


_ANOMALY_PROMPT = """\
You are an expert code-quality and security analyst reviewing an automated
self-audit of the ICDEV™ platform codebase.

Below are the raw findings from each scanner. Identify anomalies: values
that are unusually high, trending in the wrong direction, or that represent
an immediate risk. For each anomaly, assess its severity.

Findings (JSON):
{findings_json}

Respond with a JSON object — no markdown, no extra text:
{{
  "overall_health": "healthy|degraded|critical",
  "anomalies": [
    {{
      "check": "<check name>",
      "field": "<metric name>",
      "value": <numeric or string value>,
      "severity": "low|medium|high|critical",
      "reason": "<one sentence>"
    }}
  ],
  "summary": "<two-sentence executive summary>",
  "top_recommendation": "<single most important action to take>"
}}
"""


def _llm_assess_findings(checks: List[Dict]) -> Optional[Dict]:
    """Use LLM anomaly detection to interpret audit findings.

    Returns None when the LLM is unavailable (air-gap / token-free tier).
    """
    findings_for_llm = [
        {"check": c.get("check"), "findings": c.get("findings", {})}
        for c in checks
        if c.get("status") == "completed"
    ]
    if not findings_for_llm:
        return None

    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": _ANOMALY_PROMPT.format(
                        findings_json=json.dumps(findings_for_llm, indent=2)
                    ),
                }
            ],
            system_prompt=(
                "You are a security and code-quality analyst. "
                "Reply only with a JSON object as specified — no prose."
            ),
            agent_id="audit-anomaly-detector",
            classification="CUI",
            max_tokens=1024,
            effort="medium",
        )
        response = router.invoke("anomaly_detection", request)
        raw = response.content or ""
        text = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    pass
        return None
    except Exception:
        return None


def _generate_audit_report(checks: List[Dict], ai_assessment: Optional[Dict] = None) -> str:
    """Generate markdown audit report, optionally enriched with AI anomaly analysis."""
    now = _utcnow()
    completed = [c for c in checks if c.get("status") == "completed"]
    _failed = [c for c in checks if c.get("status") == "failed"]  # noqa: F841

    # Build a fast anomaly index: (check, field) -> severity
    anomaly_index: Dict[tuple, str] = {}
    if ai_assessment:
        for a in ai_assessment.get("anomalies", []):
            anomaly_index[(a.get("check", ""), a.get("field", ""))] = a.get("severity", "")

    lines = [
        "# Genesis Self-Audit Report",
        "",
        f"**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Checks Completed:** {len(completed)}/{len(checks)}",
        "**Classification:** CUI // SP-CTI",
    ]

    if ai_assessment:
        health = ai_assessment.get("overall_health", "unknown").upper()
        lines += [
            f"**AI Health Assessment:** {health}",
            f"**Summary:** {ai_assessment.get('summary', '')}",
        ]

    lines += [
        "",
        "---",
        "",
        "## Results",
        "",
        "| Check | Status | Key Finding | Anomalies |",
        "|-------|--------|-------------|-----------|",
    ]

    for check in checks:
        name = check.get("check", "unknown")
        status = check.get("status", "unknown")
        findings = check.get("findings", {})

        # AI-identified anomalies for this check
        check_anomalies = [
            a for a in (ai_assessment.get("anomalies", []) if ai_assessment else [])
            if a.get("check") == name
        ]
        anomaly_str = "; ".join(
            f"{a['field']}={a['value']} [{a['severity']}]"
            for a in check_anomalies
        ) if check_anomalies else "—"

        # Key finding: prefer AI-flagged fields, fall back to all non-zero metrics
        key = ""
        if findings:
            if check_anomalies:
                top = check_anomalies[0]
                key = f"{top['field']}: {top['value']}"
            else:
                for k, v in findings.items():
                    if v:
                        key = f"{k}: {v}"
                        break
            if not key:
                key = json.dumps(findings)[:60]
        elif check.get("error"):
            key = f"Error: {check['error'][:40]}"

        status_icon = "PASS" if status == "completed" else "FAIL"
        lines.append(f"| {name} | {status_icon} | {key} | {anomaly_str} |")

    lines.extend(["", "---", ""])

    # Details per check
    for check in checks:
        lines.append(f"### {check.get('check', 'unknown')}")
        if check.get("findings"):
            for k, v in check["findings"].items():
                sev = anomaly_index.get((check.get("check", ""), k), "")
                flag = f" ⚠ **{sev.upper()}**" if sev else ""
                lines.append(f"- **{k}:** {v}{flag}")
        elif check.get("error"):
            lines.append(f"- **Error:** {check['error']}")
        lines.append("")

    if ai_assessment and ai_assessment.get("top_recommendation"):
        lines += [
            "---",
            "",
            "## AI Recommendation",
            "",
            ai_assessment["top_recommendation"],
            "",
        ]

    lines.extend(["---", "", "*Generated by Genesis Audit Reflex (anomaly_detection)*"])
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
        "chain_integrity": _check_chain_integrity,
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

    # LLM anomaly detection — interpret findings without hardcoded thresholds
    print("  Audit: running AI anomaly detection on findings...")
    ai_assessment = _llm_assess_findings(checks)

    # Generate report
    report_md = _generate_audit_report(checks, ai_assessment)
    reports_dir = BASE_DIR / "data" / "genesis" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = _utcnow().strftime("%Y-%m-%d")
    report_file = reports_dir / f"audit-{date_str}.md"
    report_file.write_text(report_md, encoding="utf-8", newline="")

    completed = len([c for c in checks if c.get("status") == "completed"])

    return {
        "success": True,  # Audit always "succeeds" — it's informational
        "metric_value": float(completed),
        "details": {
            "checks_completed": completed,
            "checks_failed": len(checks) - completed,
            "report_file": str(report_file),
            "checks": checks,
            "ai_assessment": ai_assessment,
        },
    }
