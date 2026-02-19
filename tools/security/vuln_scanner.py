#!/usr/bin/env python3
# CUI // SP-CTI
"""Vulnerability Scanner — orchestrates all security scans.

Implements:
- run_all_scans(project_path) -> runs SAST + dependency + secrets + container
- Aggregates results from all scanners
- Evaluates security gates (from args/security_gates.yaml)
- Stores findings in failure_log table
- Logs audit trail events
- CLI: python tools/security/vuln_scanner.py --project-path PATH [--gate]
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
GATES_PATH = BASE_DIR / "args" / "security_gates.yaml"

# Import sibling scanners
sys.path.insert(0, str(BASE_DIR))
from tools.security.sast_runner import run_bandit, evaluate_gate as sast_gate
from tools.security.dependency_auditor import (
    audit_python,
    audit_javascript,
    evaluate_gate as dep_gate,
)
from tools.security.secret_detector import scan as scan_secrets, evaluate_gate as secret_gate
from tools.security.container_scanner import (
    scan_image,
    scan_dockerfile,
    evaluate_gate as container_gate,
)


def _detect_project_languages(project_path: str) -> List[str]:
    """Detect languages in the project."""
    root = Path(project_path)
    languages = []
    if any((root / f).exists() for f in ["pyproject.toml", "setup.py", "requirements.txt"]):
        languages.append("python")
    if (root / "package.json").exists():
        languages.append("javascript")
    # Fallback: check for source files
    if "python" not in languages and list(root.glob("**/*.py"))[:1]:
        languages.append("python")
    if "javascript" not in languages and list(root.glob("**/*.js"))[:1]:
        languages.append("javascript")
    return languages


def _find_dockerfiles(project_path: str) -> List[str]:
    """Find Dockerfiles in the project."""
    root = Path(project_path)
    dockerfiles = []
    for name in ["Dockerfile", "dockerfile", "Dockerfile.dev", "Dockerfile.prod"]:
        path = root / name
        if path.exists():
            dockerfiles.append(str(path))
    # Also check subdirectories
    for df in root.glob("**/Dockerfile*"):
        s = str(df)
        if s not in dockerfiles and "node_modules" not in s and "venv" not in s:
            dockerfiles.append(s)
    return dockerfiles


def run_all_scans(
    project_path: str,
    project_id: Optional[str] = None,
    image_name: Optional[str] = None,
    skip_container: bool = False,
    output_dir: Optional[str] = None,
) -> Dict:
    """Run all security scans and aggregate results.

    Executes in order:
    1. SAST (Bandit for Python)
    2. Dependency audit (pip-audit, npm audit)
    3. Secret detection
    4. Container scanning (Dockerfile analysis + Trivy if image provided)

    Args:
        project_path: Root path of the project to scan.
        project_id: Optional project ID for audit trail.
        image_name: Optional Docker image name for container scanning.
        skip_container: Skip container scanning.
        output_dir: Optional directory for scan reports.

    Returns:
        Aggregated scan results dict.
    """
    root = Path(project_path)
    languages = _detect_project_languages(project_path)
    report_dir = Path(output_dir) if output_dir else root / ".tmp" / "security-reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    aggregated = {
        "project_path": project_path,
        "project_id": project_id,
        "scan_timestamp": datetime.utcnow().isoformat() + "Z",
        "languages_detected": languages,
        "scans": {},
        "total_findings": 0,
        "severity_summary": {
            "critical": 0, "high": 0, "medium": 0, "low": 0,
        },
    }

    # ── 1. SAST ────────────────────────────────────────────────────
    print("=== Running SAST scan ===")
    if "python" in languages:
        sast_result = run_bandit(
            project_path,
            output_file=str(report_dir / "sast_report.json"),
        )
        aggregated["scans"]["sast"] = sast_result
        _add_to_severity_summary(
            aggregated["severity_summary"],
            sast_result.get("findings", []),
            key_field="severity",
        )
    else:
        aggregated["scans"]["sast"] = {
            "tool": "bandit",
            "success": True,
            "findings": [],
            "summary": {"total": 0},
            "raw_output": "No Python source detected; SAST skipped",
        }

    # ── 2. Dependency Audit ────────────────────────────────────────
    print("=== Running dependency audit ===")
    dep_results = {}

    if "python" in languages:
        py_audit = audit_python(
            project_path,
            output_file=str(report_dir / "dep_audit_python.json"),
        )
        dep_results["python"] = py_audit
        _add_to_severity_summary(
            aggregated["severity_summary"],
            py_audit.get("findings", []),
            key_field="severity",
        )

    if "javascript" in languages:
        js_audit = audit_javascript(
            project_path,
            output_file=str(report_dir / "dep_audit_javascript.json"),
        )
        dep_results["javascript"] = js_audit
        _add_to_severity_summary(
            aggregated["severity_summary"],
            js_audit.get("findings", []),
            key_field="severity",
        )

    if not dep_results:
        dep_results["info"] = {
            "tool": "dependency-audit",
            "success": True,
            "findings": [],
            "raw_output": "No dependency files found",
        }

    aggregated["scans"]["dependency"] = dep_results

    # ── 3. Secret Detection ────────────────────────────────────────
    print("=== Running secret detection ===")
    secret_result = scan_secrets(
        project_path,
        output_file=str(report_dir / "secrets_report.json"),
    )
    aggregated["scans"]["secrets"] = secret_result
    # Secrets are always critical
    secret_count = len(secret_result.get("findings", []))
    aggregated["severity_summary"]["critical"] += secret_count

    # ── 4. Container Scanning ──────────────────────────────────────
    if not skip_container:
        print("=== Running container scanning ===")
        container_results = {}

        # Dockerfile analysis
        dockerfiles = _find_dockerfiles(project_path)
        if dockerfiles:
            df_findings = []
            for df_path in dockerfiles:
                df_result = scan_dockerfile(df_path)
                container_results[f"dockerfile:{Path(df_path).name}"] = df_result
                df_findings.extend(df_result.get("findings", []))
            _add_to_severity_summary(
                aggregated["severity_summary"],
                df_findings,
                key_field="severity",
            )

        # Trivy image scan
        if image_name:
            img_result = scan_image(
                image_name,
                output_file=str(report_dir / "container_report.json"),
            )
            container_results["image_scan"] = img_result
            _add_to_severity_summary(
                aggregated["severity_summary"],
                img_result.get("findings", []),
                key_field="severity",
            )

        if not container_results:
            container_results["info"] = {
                "success": True,
                "findings": [],
                "raw_output": "No Dockerfiles found and no image specified",
            }

        aggregated["scans"]["container"] = container_results
    else:
        aggregated["scans"]["container"] = {
            "info": {"success": True, "findings": [], "raw_output": "Container scanning skipped"},
        }

    # ── Compute totals ─────────────────────────────────────────────
    aggregated["total_findings"] = sum(aggregated["severity_summary"].values())

    # ── Store findings in failure_log ──────────────────────────────
    _store_findings_in_db(aggregated, project_id)

    # ── Log audit trail ────────────────────────────────────────────
    _log_audit(project_id, aggregated)

    print(f"\n=== Scan complete: {aggregated['total_findings']} total findings ===")
    return aggregated


def _add_to_severity_summary(summary: Dict, findings: List[Dict], key_field: str = "severity"):
    """Add findings to the aggregated severity summary."""
    for f in findings:
        sev = f.get(key_field, "low").upper()
        if sev in ("CRITICAL",):
            summary["critical"] += 1
        elif sev in ("HIGH",):
            summary["high"] += 1
        elif sev in ("MEDIUM", "MODERATE"):
            summary["medium"] += 1
        elif sev in ("LOW", "INFO", "UNKNOWN"):
            summary["low"] += 1


def evaluate_gates(aggregated: Dict) -> Dict:
    """Evaluate all security gates on aggregated scan results.

    Args:
        aggregated: Aggregated scan results from run_all_scans.

    Returns:
        Dict with gate results for each scan type and overall pass/fail.
    """
    gates = {
        "overall_passed": True,
        "gates": {},
    }

    # SAST gate
    sast_findings = aggregated.get("scans", {}).get("sast", {}).get("findings", [])
    sast_gate_result = sast_gate(sast_findings)
    gates["gates"]["sast"] = sast_gate_result
    if not sast_gate_result["passed"]:
        gates["overall_passed"] = False

    # Dependency gate
    dep_scans = aggregated.get("scans", {}).get("dependency", {})
    all_dep_findings = []
    for lang_key, lang_result in dep_scans.items():
        if isinstance(lang_result, dict) and "findings" in lang_result:
            all_dep_findings.extend(lang_result["findings"])
    dep_gate_result = dep_gate(all_dep_findings)
    gates["gates"]["dependency"] = dep_gate_result
    if not dep_gate_result["passed"]:
        gates["overall_passed"] = False

    # Secrets gate (any secrets = FAIL)
    secret_findings = aggregated.get("scans", {}).get("secrets", {}).get("findings", [])
    secret_gate_result = secret_gate(secret_findings)
    gates["gates"]["secrets"] = secret_gate_result
    if not secret_gate_result["passed"]:
        gates["overall_passed"] = False

    # Container gate
    container_scans = aggregated.get("scans", {}).get("container", {})
    all_container_findings = []
    for key, scan_result in container_scans.items():
        if isinstance(scan_result, dict) and "findings" in scan_result:
            all_container_findings.extend(scan_result["findings"])
    if all_container_findings:
        container_gate_result = container_gate(all_container_findings)
        gates["gates"]["container"] = container_gate_result
        if not container_gate_result["passed"]:
            gates["overall_passed"] = False
    else:
        gates["gates"]["container"] = {"passed": True, "violations": [], "note": "No container findings"}

    return gates


def _store_findings_in_db(aggregated: Dict, project_id: Optional[str]) -> None:
    """Store critical/high findings in the failure_log table."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()

        # Collect all findings across all scans
        all_findings = _collect_all_findings(aggregated)

        for finding in all_findings:
            sev = finding.get("severity", "LOW").upper()
            if sev not in ("CRITICAL", "HIGH"):
                continue  # Only store critical/high in failure_log

            source = finding.get("source", "security_scan")
            error_type = finding.get("type", sev)
            error_message = finding.get("message", "")[:500]
            context = json.dumps({
                k: v for k, v in finding.items()
                if k not in ("message", "source", "type")
            })

            c.execute(
                """INSERT INTO failure_log
                   (project_id, source, error_type, error_message, context)
                   VALUES (?, ?, ?, ?, ?)""",
                (project_id, source, error_type, error_message, context),
            )

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: failed to store findings in DB: {e}")


def _collect_all_findings(aggregated: Dict) -> List[Dict]:
    """Collect and normalize all findings from aggregated results."""
    findings = []

    # SAST
    for f in aggregated.get("scans", {}).get("sast", {}).get("findings", []):
        findings.append({
            "source": "sast/bandit",
            "type": f.get("test_id", "SAST"),
            "severity": f.get("severity", "LOW"),
            "message": f.get("issue_text", ""),
            "file": f.get("file", ""),
            "line": f.get("line", 0),
        })

    # Dependency
    dep_scans = aggregated.get("scans", {}).get("dependency", {})
    for lang_key, lang_result in dep_scans.items():
        if isinstance(lang_result, dict):
            for f in lang_result.get("findings", []):
                findings.append({
                    "source": f"dependency/{lang_key}",
                    "type": f.get("vulnerability_id", "DEP"),
                    "severity": f.get("severity", "LOW"),
                    "message": f.get("title", f.get("description", "")),
                    "package": f.get("package", ""),
                })

    # Secrets
    for f in aggregated.get("scans", {}).get("secrets", {}).get("findings", []):
        findings.append({
            "source": "secrets",
            "type": f.get("type", "SECRET"),
            "severity": "CRITICAL",
            "message": f"Secret detected: {f.get('type', 'unknown')} in {f.get('file', '?')}",
            "file": f.get("file", ""),
            "line": f.get("line", 0),
        })

    # Container
    container_scans = aggregated.get("scans", {}).get("container", {})
    for key, scan_result in container_scans.items():
        if isinstance(scan_result, dict):
            for f in scan_result.get("findings", []):
                findings.append({
                    "source": f"container/{key}",
                    "type": f.get("vulnerability_id", f.get("check_id", "CONTAINER")),
                    "severity": f.get("severity", "LOW"),
                    "message": f.get("title", f.get("name", f.get("description", ""))),
                })

    return findings


def _log_audit(project_id: Optional[str], aggregated: Dict) -> None:
    """Log security scan to audit trail."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "security_scan",
                "security/vuln_scanner",
                f"Security scan completed: {aggregated['total_findings']} findings",
                json.dumps({
                    "severity_summary": aggregated["severity_summary"],
                    "languages": aggregated["languages_detected"],
                    "scan_timestamp": aggregated["scan_timestamp"],
                }),
                "CUI",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: audit logging failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Orchestrate all security scans")
    parser.add_argument("--project-path", required=True, help="Project path to scan")
    parser.add_argument("--project-id", help="Project ID for audit trail")
    parser.add_argument("--image", help="Docker image name for container scanning")
    parser.add_argument("--skip-container", action="store_true", help="Skip container scanning")
    parser.add_argument("--output-dir", help="Directory for scan reports")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Run all scans
    aggregated = run_all_scans(
        project_path=args.project_path,
        project_id=args.project_id,
        image_name=args.image,
        skip_container=args.skip_container,
        output_dir=args.output_dir,
    )

    # Evaluate gates if requested
    if args.gate:
        gate_results = evaluate_gates(aggregated)
        aggregated["gates"] = gate_results

    if args.json:
        # Remove raw_output for cleaner JSON
        _clean_raw_output(aggregated)
        print(json.dumps(aggregated, indent=2))
    else:
        _print_summary(aggregated)


def _clean_raw_output(data):
    """Remove raw_output fields for cleaner display."""
    if isinstance(data, dict):
        data.pop("raw_output", None)
        for v in data.values():
            _clean_raw_output(v)
    elif isinstance(data, list):
        for item in data:
            _clean_raw_output(item)


def _print_summary(aggregated: Dict) -> None:
    """Print a human-readable summary of all scan results."""
    print("\n" + "=" * 60)
    print("  ICDEV SECURITY SCAN SUMMARY")
    print("=" * 60)
    print(f"  Project: {aggregated['project_path']}")
    print(f"  Timestamp: {aggregated['scan_timestamp']}")
    print(f"  Languages: {', '.join(aggregated['languages_detected'])}")
    print()

    sev = aggregated["severity_summary"]
    print(f"  Total Findings: {aggregated['total_findings']}")
    print(f"    Critical: {sev['critical']}")
    print(f"    High:     {sev['high']}")
    print(f"    Medium:   {sev['medium']}")
    print(f"    Low:      {sev['low']}")
    print()

    # Per-scan summaries
    scans = aggregated.get("scans", {})

    if "sast" in scans:
        s = scans["sast"]
        total = s.get("summary", {}).get("total", 0)
        print(f"  SAST ({s.get('tool', '?')}): {total} findings")

    if "dependency" in scans:
        dep = scans["dependency"]
        for lang, res in dep.items():
            if isinstance(res, dict) and "summary" in res:
                total = res["summary"].get("total", 0)
                print(f"  Dependency Audit ({lang}): {total} vulnerabilities")

    if "secrets" in scans:
        s = scans["secrets"]
        total = s.get("summary", {}).get("total", 0)
        print(f"  Secret Detection ({s.get('tool', '?')}): {total} secrets found")

    if "container" in scans:
        cont = scans["container"]
        for key, res in cont.items():
            if isinstance(res, dict) and "summary" in res:
                total = res["summary"].get("total", 0)
                print(f"  Container ({key}): {total} issues")

    # Gate results
    if "gates" in aggregated:
        gates = aggregated["gates"]
        print()
        print("  SECURITY GATES:")
        overall = "PASSED" if gates["overall_passed"] else "FAILED"
        for gate_name, gate_result in gates.get("gates", {}).items():
            status = "PASS" if gate_result.get("passed", True) else "FAIL"
            print(f"    {gate_name}: {status}")
            for v in gate_result.get("violations", []):
                print(f"      - {v}")
        print(f"\n  OVERALL: {overall}")

    print("=" * 60)


if __name__ == "__main__":
    main()
