#!/usr/bin/env python3
# CUI // SP-CTI
"""Dependency Auditor — audit Python and JavaScript dependencies for vulnerabilities.

Implements:
- audit_python(project_path) -> subprocess pip-audit, parse JSON output
- audit_javascript(project_path) -> subprocess npm audit, parse JSON output
- evaluate_gate(findings, thresholds) -> check against security_gates.yaml
- CLI: python tools/security/dependency_auditor.py --project-path PATH [--gate]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
GATES_PATH = BASE_DIR / "args" / "security_gates.yaml"

# Language support (Phase 16)
try:
    import importlib.util as _ilu
    _ls_path = BASE_DIR / "tools" / "builder" / "language_support.py"
    if _ls_path.exists():
        _ls_spec = _ilu.spec_from_file_location("language_support", _ls_path)
        _ls_mod = _ilu.module_from_spec(_ls_spec)
        _ls_spec.loader.exec_module(_ls_mod)
        detect_languages = _ls_mod.detect_languages
    else:
        detect_languages = None
except Exception:
    detect_languages = None


def _load_thresholds() -> Dict:
    """Load dependency gate thresholds from args/security_gates.yaml."""
    defaults = {"max_critical": 0, "max_high": 0}
    if not GATES_PATH.exists():
        return defaults

    try:
        content = GATES_PATH.read_text(encoding="utf-8")
        in_dep = False
        thresholds = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "dependency:":
                in_dep = True
                continue
            if in_dep:
                if stripped and not stripped.startswith("#"):
                    if ":" in stripped and stripped[0] != " " and not stripped.startswith("max_"):
                        in_dep = False
                        continue
                    parts = stripped.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip()
                        try:
                            thresholds[key] = int(val)
                        except ValueError:
                            pass
        return thresholds if thresholds else defaults
    except Exception:
        return defaults


def audit_python(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run pip-audit on a Python project.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "pip-audit",
        "language": "python",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if pip-audit is available
    try:
        version_check = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            # Try alternative command
            version_check = subprocess.run(
                ["pip-audit", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if version_check.returncode != 0:
                result["success"] = False
                result["raw_output"] = "pip-audit not installed. Install with: pip install pip-audit"
                return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "pip-audit not found. Install with: pip install pip-audit"
        return result

    # Build command
    cmd = [sys.executable, "-m", "pip_audit", "--format=json"]

    # Check for requirements.txt
    req_file = root / "requirements.txt"
    if req_file.exists():
        cmd.extend(["--requirement", str(req_file)])
    else:
        # Check for pyproject.toml
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            cmd.extend(["--requirement", str(pyproject)])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse JSON output
        if proc.stdout:
            findings = _parse_pip_audit_json(proc.stdout)
            result["findings"] = findings

        # pip-audit returns 1 if vulnerabilities found
        result["success"] = proc.returncode in (0, 1)

        # Summary
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
        for f in result["findings"]:
            sev = f.get("severity", "unknown").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["unknown"] += 1

        result["summary"] = {
            "total": len(result["findings"]),
            **severity_counts,
        }

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "pip-audit timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running pip-audit: {str(e)}"

    return result


def _parse_pip_audit_json(json_str: str) -> List[Dict]:
    """Parse pip-audit JSON output into structured findings."""
    findings = []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return findings

    # pip-audit output format: {"dependencies": [...]}
    dependencies = data if isinstance(data, list) else data.get("dependencies", [])

    for dep in dependencies:
        vulns = dep.get("vulns", [])
        for vuln in vulns:
            # Map CVSS to severity
            severity = "unknown"
            if "fix_versions" in vuln or "aliases" in vuln:
                severity = "high"  # Default if not specified

            findings.append({
                "package": dep.get("name", ""),
                "version": dep.get("version", ""),
                "vulnerability_id": vuln.get("id", ""),
                "aliases": vuln.get("aliases", []),
                "description": vuln.get("description", ""),
                "fix_versions": vuln.get("fix_versions", []),
                "severity": severity,
            })

    return findings


def audit_javascript(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run npm audit on a JavaScript project.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "npm-audit",
        "language": "javascript",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check for package.json
    if not (root / "package.json").exists():
        result["success"] = True
        result["raw_output"] = "No package.json found; skipping npm audit"
        return result

    # Check if npm is available
    try:
        version_check = subprocess.run(
            ["npm", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "npm not found"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "npm not found"
        return result

    # Run npm audit
    cmd = ["npm", "audit", "--json"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse JSON output
        if proc.stdout:
            findings = _parse_npm_audit_json(proc.stdout)
            result["findings"] = findings

        # npm audit returns non-zero if vulnerabilities found
        result["success"] = True  # Tool ran successfully

        # Summary
        severity_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "info": 0}
        for f in result["findings"]:
            sev = f.get("severity", "info").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {
            "total": len(result["findings"]),
            **severity_counts,
        }

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "npm audit timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running npm audit: {str(e)}"

    return result


def _parse_npm_audit_json(json_str: str) -> List[Dict]:
    """Parse npm audit JSON output into structured findings."""
    findings = []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return findings

    # npm audit v2 format
    vulns = data.get("vulnerabilities", {})
    for pkg_name, vuln_data in vulns.items():
        severity = vuln_data.get("severity", "info")
        via = vuln_data.get("via", [])
        for v in via:
            if isinstance(v, dict):
                findings.append({
                    "package": pkg_name,
                    "version": vuln_data.get("range", ""),
                    "vulnerability_id": v.get("url", ""),
                    "title": v.get("title", ""),
                    "severity": v.get("severity", severity),
                    "source": v.get("source", ""),
                    "cwe": v.get("cwe", []),
                    "fix_available": vuln_data.get("fixAvailable", False),
                })
            elif isinstance(v, str):
                findings.append({
                    "package": pkg_name,
                    "version": vuln_data.get("range", ""),
                    "vulnerability_id": "",
                    "title": f"Transitive dependency via {v}",
                    "severity": severity,
                    "fix_available": vuln_data.get("fixAvailable", False),
                })

    # npm audit v1 format fallback
    if not findings and "advisories" in data:
        for adv_id, adv in data["advisories"].items():
            findings.append({
                "package": adv.get("module_name", ""),
                "version": adv.get("vulnerable_versions", ""),
                "vulnerability_id": str(adv_id),
                "title": adv.get("title", ""),
                "severity": adv.get("severity", "info"),
                "overview": adv.get("overview", ""),
                "recommendation": adv.get("recommendation", ""),
                "cwe": adv.get("cwe", ""),
                "fix_available": bool(adv.get("patched_versions")),
            })

    return findings


def audit_java(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run OWASP Dependency-Check on a Java project via Maven.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, language, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "owasp-dependency-check",
        "language": "java",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if Maven is available
    try:
        version_check = subprocess.run(
            ["mvn", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "Maven not found. Install Maven and OWASP dependency-check plugin."
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "Maven not found. Install Maven and OWASP dependency-check plugin."
        return result

    cmd = [
        "mvn", "org.owasp:dependency-check-maven:check",
        "-DfailBuildOnCVSS=0", "-Dformat=JSON", "-q",
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout + proc.stderr
        result["raw_output"] = raw

        # Try to parse the JSON report if generated
        report_file = root / "target" / "dependency-check-report.json"
        findings = []
        if report_file.exists():
            try:
                report_data = json.loads(report_file.read_text(encoding="utf-8"))
                for dep in report_data.get("dependencies", []):
                    vulns = dep.get("vulnerabilities", [])
                    for vuln in vulns:
                        cvss_score = vuln.get("cvssv3", {}).get("baseScore", 0) if isinstance(vuln.get("cvssv3"), dict) else 0
                        if cvss_score == 0:
                            cvss_score = vuln.get("cvssv2", {}).get("score", 0) if isinstance(vuln.get("cvssv2"), dict) else 0
                        severity = "unknown"
                        if cvss_score >= 9.0:
                            severity = "critical"
                        elif cvss_score >= 7.0:
                            severity = "high"
                        elif cvss_score >= 4.0:
                            severity = "medium"
                        elif cvss_score > 0:
                            severity = "low"

                        findings.append({
                            "package": dep.get("fileName", ""),
                            "version": dep.get("version", ""),
                            "advisory": vuln.get("name", ""),
                            "severity": severity,
                            "description": vuln.get("description", "")[:200],
                            "fix_version": "",
                        })
            except (json.JSONDecodeError, OSError):
                pass

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
        for f in findings:
            sev = f.get("severity", "unknown").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["unknown"] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "OWASP Dependency-Check timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running OWASP Dependency-Check: {str(e)}"

    return result


def audit_go(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run govulncheck on a Go project.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, language, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "govulncheck",
        "language": "go",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if govulncheck is available
    try:
        subprocess.run(
            ["govulncheck", "-version"],
            capture_output=True, text=True, timeout=10,
        )
        # govulncheck may not support -version, try running it anyway
    except FileNotFoundError:
        result["success"] = False
        result["raw_output"] = "govulncheck not found. Install with: go install golang.org/x/vuln/cmd/govulncheck@latest"
        return result
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "govulncheck timed out during version check"
        return result

    cmd = ["govulncheck", "-json", "./..."]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse govulncheck JSON output (newline-delimited JSON messages)
        findings = []
        if proc.stdout:
            try:
                # govulncheck outputs newline-delimited JSON
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # Look for vulnerability entries
                        vuln = entry.get("vulnerability", entry.get("osv", None))
                        if vuln and isinstance(vuln, dict):
                            severity = "unknown"
                            # Try to extract severity from database_specific or severity field
                            db_specific = vuln.get("database_specific", {})
                            if isinstance(db_specific, dict):
                                sev = db_specific.get("severity", "").lower()
                                if sev in ("critical", "high", "medium", "low"):
                                    severity = sev
                            findings.append({
                                "package": vuln.get("module", vuln.get("id", "")),
                                "version": "",
                                "advisory": vuln.get("id", ""),
                                "severity": severity,
                                "description": vuln.get("summary", vuln.get("details", ""))[:200],
                                "fix_version": "",
                            })
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        result["findings"] = findings
        result["success"] = True  # Tool ran successfully

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
        for f in findings:
            sev = f.get("severity", "unknown").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["unknown"] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "govulncheck timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running govulncheck: {str(e)}"

    return result


def audit_rust(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run cargo audit on a Rust project.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, language, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "cargo-audit",
        "language": "rust",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if cargo-audit is available
    try:
        version_check = subprocess.run(
            ["cargo", "audit", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "cargo-audit not found. Install with: cargo install cargo-audit"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "cargo-audit not found. Install with: cargo install cargo-audit"
        return result

    cmd = ["cargo", "audit", "--json"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse cargo audit JSON output
        findings = []
        if proc.stdout:
            try:
                data = json.loads(proc.stdout)
                vulns = data.get("vulnerabilities", {})
                for vuln_entry in vulns.get("list", []):
                    advisory = vuln_entry.get("advisory", {})
                    package_info = vuln_entry.get("package", {})

                    # Map CVSS to severity
                    cvss = advisory.get("cvss", None)
                    severity = "unknown"
                    if cvss and isinstance(cvss, (int, float)):
                        if cvss >= 9.0:
                            severity = "critical"
                        elif cvss >= 7.0:
                            severity = "high"
                        elif cvss >= 4.0:
                            severity = "medium"
                        else:
                            severity = "low"
                    elif isinstance(cvss, str):
                        # CVSS vector string; default to medium
                        severity = "medium"

                    findings.append({
                        "package": package_info.get("name", ""),
                        "version": package_info.get("version", ""),
                        "advisory": advisory.get("id", ""),
                        "severity": severity,
                        "description": advisory.get("description", advisory.get("title", ""))[:200],
                        "fix_version": ", ".join(str(v) for v in advisory.get("patched_versions", [])),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

        result["findings"] = findings
        result["success"] = True  # Tool ran successfully

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
        for f in findings:
            sev = f.get("severity", "unknown").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["unknown"] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "cargo audit timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running cargo audit: {str(e)}"

    return result


def audit_csharp(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run dotnet list package --vulnerable on a C# project.

    Args:
        project_path: Root path of the project to audit.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, language, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "dotnet-audit",
        "language": "csharp",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if dotnet is available
    try:
        version_check = subprocess.run(
            ["dotnet", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "dotnet not found. Install .NET SDK from https://dotnet.microsoft.com"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "dotnet not found. Install .NET SDK from https://dotnet.microsoft.com"
        return result

    cmd = ["dotnet", "list", "package", "--vulnerable", "--format", "json"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse dotnet list package --vulnerable JSON output
        findings = []
        if proc.stdout:
            try:
                data = json.loads(proc.stdout)
                projects = data.get("projects", [])
                for project in projects:
                    frameworks = project.get("frameworks", [])
                    for framework in frameworks:
                        packages = framework.get("topLevelPackages", []) + framework.get("transitivePackages", [])
                        for pkg in packages:
                            for vuln in pkg.get("vulnerabilities", []):
                                severity = vuln.get("severity", "unknown").lower()
                                findings.append({
                                    "package": pkg.get("id", ""),
                                    "version": pkg.get("resolvedVersion", pkg.get("requestedVersion", "")),
                                    "advisory": vuln.get("advisoryurl", ""),
                                    "severity": severity,
                                    "description": f"Vulnerability in {pkg.get('id', '')}",
                                    "fix_version": "",
                                })
            except (json.JSONDecodeError, KeyError):
                pass

        # If JSON format not supported, fall back to text parsing
        if not findings and proc.stdout and not proc.stdout.strip().startswith("{"):
            # Text format fallback: parse lines for vulnerable packages
            current_package = ""
            for line in (proc.stdout + proc.stderr).splitlines():
                line = line.strip()
                if ">" in line and "has the following vulnerable" in line:
                    current_package = line.split(">")[1].strip().split()[0] if ">" in line else ""
                elif line.startswith(">") and current_package:
                    parts = line.strip("> ").split()
                    if len(parts) >= 2:
                        findings.append({
                            "package": parts[0],
                            "version": parts[1] if len(parts) > 1 else "",
                            "advisory": parts[3] if len(parts) > 3 else "",
                            "severity": parts[2].lower() if len(parts) > 2 else "unknown",
                            "description": " ".join(parts[4:]) if len(parts) > 4 else "",
                            "fix_version": "",
                        })

        result["findings"] = findings
        result["success"] = True  # Tool ran successfully

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
        for f in findings:
            sev = f.get("severity", "unknown").lower()
            if sev in severity_counts:
                severity_counts[sev] += 1
            else:
                severity_counts["unknown"] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "dotnet list package timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running dotnet list package: {str(e)}"

    return result


def evaluate_gate(
    findings: List[Dict],
    thresholds: Optional[Dict] = None,
) -> Dict:
    """Evaluate dependency findings against security gate thresholds.

    Args:
        findings: List of vulnerability findings.
        thresholds: Override thresholds dict.

    Returns:
        Dict with keys: passed, violations, severity_counts.
    """
    if thresholds is None:
        thresholds = _load_thresholds()

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "moderate": 0, "low": 0}
    for f in findings:
        sev = f.get("severity", "low").lower()
        if sev in severity_counts:
            severity_counts[sev] += 1

    # Merge moderate into medium for consistent thresholds
    severity_counts["medium"] = severity_counts.get("medium", 0) + severity_counts.get("moderate", 0)

    violations = []
    max_critical = thresholds.get("max_critical", 0)
    max_high = thresholds.get("max_high", 0)

    if severity_counts["critical"] > max_critical:
        violations.append(
            f"CRITICAL vulnerabilities: {severity_counts['critical']} (max: {max_critical})"
        )
    if severity_counts["high"] > max_high:
        violations.append(
            f"HIGH vulnerabilities: {severity_counts['high']} (max: {max_high})"
        )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "severity_counts": severity_counts,
        "thresholds": thresholds,
    }


def main():
    parser = argparse.ArgumentParser(description="Dependency vulnerability auditing")
    parser.add_argument("--project-path", required=True, help="Project path to audit")
    parser.add_argument("--output", help="Write JSON report to this path")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument(
        "--language",
        choices=["python", "javascript", "java", "go", "rust", "csharp", "auto"],
        default="auto",
        help="Language to audit (default: auto-detect)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    root = Path(args.project_path)
    all_findings = []
    results = {}

    # Language detection and audit dispatch
    AUDITORS = {
        "python": audit_python,
        "javascript": audit_javascript,
        "java": audit_java,
        "go": audit_go,
        "rust": audit_rust,
        "csharp": audit_csharp,
    }

    # Language presence checks for auto-detect mode
    LANGUAGE_CHECKS = {
        "python": lambda r: (
            (r / "pyproject.toml").exists()
            or (r / "requirements.txt").exists()
            or (r / "setup.py").exists()
        ),
        "javascript": lambda r: (r / "package.json").exists(),
        "java": lambda r: (r / "pom.xml").exists() or (r / "build.gradle").exists() or (r / "build.gradle.kts").exists(),
        "go": lambda r: (r / "go.mod").exists(),
        "rust": lambda r: (r / "Cargo.toml").exists(),
        "csharp": lambda r: bool(list(r.glob("*.csproj"))) or bool(list(r.glob("*.sln"))),
    }

    if args.language == "auto":
        # Use language_support if available, otherwise use LANGUAGE_CHECKS
        if detect_languages is not None:
            detected = detect_languages(args.project_path)
        else:
            detected = [lang for lang, check in LANGUAGE_CHECKS.items() if check(root)]

        for lang in detected:
            auditor_fn = AUDITORS.get(lang)
            if auditor_fn:
                lang_result = auditor_fn(args.project_path, output_file=args.output if lang == detected[0] else None)
                results[lang] = lang_result
                all_findings.extend(lang_result.get("findings", []))
    else:
        auditor_fn = AUDITORS.get(args.language)
        if auditor_fn:
            lang_result = auditor_fn(args.project_path, output_file=args.output)
            results[args.language] = lang_result
            all_findings.extend(lang_result.get("findings", []))

    combined = {
        "project_path": args.project_path,
        "results": results,
        "total_findings": len(all_findings),
    }

    if args.gate:
        gate_result = evaluate_gate(all_findings)
        combined["gate"] = gate_result

    if args.json:
        print(json.dumps(combined, indent=2))
    else:
        print(f"Dependency Audit: {args.project_path}")
        for lang, res in results.items():
            tool = res.get("tool", lang)
            total = res.get("summary", {}).get("total", 0)
            print(f"\n  [{lang}] {tool}: {total} vulnerabilities found")
            for f in res.get("findings", [])[:10]:
                sev = f.get("severity", "?")
                pkg = f.get("package", "?")
                title = f.get("title", f.get("description", ""))[:60]
                print(f"    [{sev}] {pkg}: {title}")
            remaining = len(res.get("findings", [])) - 10
            if remaining > 0:
                print(f"    ... and {remaining} more")

        if args.gate:
            gate = combined["gate"]
            status = "PASSED" if gate["passed"] else "FAILED"
            print(f"\n  Security Gate: {status}")
            for v in gate.get("violations", []):
                print(f"    VIOLATION: {v}")


if __name__ == "__main__":
    main()
