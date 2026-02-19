#!/usr/bin/env python3
# CUI // SP-CTI
"""Container Scanner — scan container images and Dockerfiles for vulnerabilities.

Implements:
- scan_image(image_name) -> subprocess trivy, parse JSON
- scan_dockerfile(dockerfile_path) -> analyze Dockerfile for security issues
- evaluate_gate(findings, thresholds) -> check against security_gates.yaml
- CLI: python tools/security/container_scanner.py --image IMAGE_NAME [--gate]
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
GATES_PATH = BASE_DIR / "args" / "security_gates.yaml"

# Dockerfile security checks
DOCKERFILE_CHECKS = [
    {
        "id": "DS001",
        "name": "Running as root",
        "description": "Container runs as root user. Use USER directive to switch to a non-root user.",
        "severity": "HIGH",
        "pattern": None,  # Custom check logic
        "check_type": "no_user_directive",
    },
    {
        "id": "DS002",
        "name": "Using latest tag",
        "description": "Base image uses ':latest' or no tag. Pin to a specific version.",
        "severity": "MEDIUM",
        "pattern": r"^FROM\s+\S+\s*$|^FROM\s+\S+:latest",
        "check_type": "regex",
    },
    {
        "id": "DS003",
        "name": "Using ADD instead of COPY",
        "description": "ADD has extra functionality that can be a security risk. Use COPY unless you need ADD features.",
        "severity": "LOW",
        "pattern": r"^ADD\s+",
        "check_type": "regex",
    },
    {
        "id": "DS004",
        "name": "Using sudo",
        "description": "Avoid using sudo in Dockerfiles. It can lead to unpredictable behavior.",
        "severity": "MEDIUM",
        "pattern": r"sudo\s+",
        "check_type": "regex",
    },
    {
        "id": "DS005",
        "name": "No HEALTHCHECK",
        "description": "No HEALTHCHECK instruction found. Add a HEALTHCHECK for container orchestration.",
        "severity": "LOW",
        "pattern": None,
        "check_type": "no_healthcheck",
    },
    {
        "id": "DS006",
        "name": "Exposing sensitive port",
        "description": "Exposing SSH port (22) or other management ports.",
        "severity": "HIGH",
        "pattern": r"EXPOSE\s+22(?:\s|$)",
        "check_type": "regex",
    },
    {
        "id": "DS007",
        "name": "Secrets in ENV",
        "description": "Potential secrets found in ENV directives. Use build secrets or runtime environment variables instead.",
        "severity": "HIGH",
        "pattern": r"ENV\s+\S*(PASSWORD|SECRET|KEY|TOKEN|CREDENTIAL)\S*\s*=",
        "check_type": "regex",
    },
    {
        "id": "DS008",
        "name": "Using curl/wget with pipe",
        "description": "Piping curl/wget to shell is risky. Download, verify, then execute.",
        "severity": "HIGH",
        "pattern": r"(curl|wget)\s+.*\|\s*(sh|bash|zsh)",
        "check_type": "regex",
    },
    {
        "id": "DS009",
        "name": "Package manager cache not cleaned",
        "description": "Package manager cache not cleaned. Add cleanup to reduce image size and attack surface.",
        "severity": "LOW",
        "pattern": r"(apt-get install|yum install|apk add)(?!.*&&.*(?:rm -rf|clean|--no-cache))",
        "check_type": "regex",
    },
    {
        "id": "DS010",
        "name": "Missing security context",
        "description": "No capabilities drop found. Consider dropping all capabilities and adding only needed ones.",
        "severity": "MEDIUM",
        "pattern": None,
        "check_type": "info_only",
    },
]


def _load_thresholds() -> Dict:
    """Load container gate thresholds from args/security_gates.yaml."""
    defaults = {"max_critical": 0, "max_high": 0}
    if not GATES_PATH.exists():
        return defaults

    try:
        content = GATES_PATH.read_text(encoding="utf-8")
        in_container = False
        thresholds = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "container:":
                in_container = True
                continue
            if in_container:
                if stripped and not stripped.startswith("#"):
                    if ":" in stripped and stripped[0] != " " and not stripped.startswith("max_"):
                        in_container = False
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


def scan_image(
    image_name: str,
    output_file: Optional[str] = None,
    severity_filter: str = "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL",
) -> Dict:
    """Scan a container image using Trivy.

    Args:
        image_name: Docker image name/tag to scan (e.g., "myapp:latest").
        output_file: Optional path to write JSON report.
        severity_filter: Comma-separated severity levels to include.

    Returns:
        Dict with keys: success, tool, findings, summary, raw_output.
    """
    result = {
        "tool": "trivy",
        "image": image_name,
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if trivy is available
    try:
        version_check = subprocess.run(
            ["trivy", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "trivy not installed. See: https://trivy.dev/latest/getting-started/installation/"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "trivy not found. See: https://trivy.dev/latest/getting-started/installation/"
        return result

    # Run trivy scan
    cmd = [
        "trivy", "image",
        "--format", "json",
        "--severity", severity_filter,
        "--no-progress",
        image_name,
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        result["raw_output"] = proc.stdout + proc.stderr

        if proc.stdout:
            findings = _parse_trivy_output(proc.stdout)
            result["findings"] = findings

        result["success"] = proc.returncode == 0

        # Summary
        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        for f in result["findings"]:
            sev = f.get("severity", "UNKNOWN").upper()
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
        result["raw_output"] = "trivy timed out after 600 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running trivy: {str(e)}"

    return result


def _parse_trivy_output(json_str: str) -> List[Dict]:
    """Parse trivy JSON output into structured findings."""
    findings = []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return findings

    # Trivy output has "Results" array with per-target results
    results_list = data.get("Results", [])
    for target_result in results_list:
        target = target_result.get("Target", "")
        target_type = target_result.get("Type", "")
        vulnerabilities = target_result.get("Vulnerabilities", []) or []

        for vuln in vulnerabilities:
            findings.append({
                "target": target,
                "target_type": target_type,
                "vulnerability_id": vuln.get("VulnerabilityID", ""),
                "package": vuln.get("PkgName", ""),
                "installed_version": vuln.get("InstalledVersion", ""),
                "fixed_version": vuln.get("FixedVersion", ""),
                "severity": vuln.get("Severity", "UNKNOWN"),
                "title": vuln.get("Title", ""),
                "description": vuln.get("Description", "")[:200],
                "primary_url": vuln.get("PrimaryURL", ""),
                "cvss_score": _extract_cvss(vuln.get("CVSS", {})),
            })

    return findings


def _extract_cvss(cvss_data: dict) -> Optional[float]:
    """Extract the highest CVSS score from trivy CVSS data."""
    if not cvss_data:
        return None
    max_score = 0.0
    for source, scores in cvss_data.items():
        if isinstance(scores, dict):
            score = scores.get("V3Score") or scores.get("V2Score") or 0.0
            max_score = max(max_score, float(score))
    return max_score if max_score > 0 else None


def scan_dockerfile(
    dockerfile_path: str,
) -> Dict:
    """Analyze a Dockerfile for security issues.

    Performs static analysis checking for common security anti-patterns.

    Args:
        dockerfile_path: Path to the Dockerfile to analyze.

    Returns:
        Dict with keys: success, findings, summary.
    """
    path = Path(dockerfile_path)
    result = {
        "tool": "dockerfile-analyzer",
        "file": str(path),
        "success": True,
        "findings": [],
        "summary": {},
    }

    if not path.exists():
        result["success"] = False
        result["raw_output"] = f"Dockerfile not found: {dockerfile_path}"
        return result

    try:
        content = path.read_text(encoding="utf-8")
    except IOError as e:
        result["success"] = False
        result["raw_output"] = f"Error reading Dockerfile: {str(e)}"
        return result

    lines = content.splitlines()

    # Run each check
    has_user = False
    has_healthcheck = False

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Track directives
        if stripped.upper().startswith("USER "):
            user_val = stripped.split(None, 1)[1].strip() if len(stripped.split(None, 1)) > 1 else ""
            if user_val and user_val.lower() not in ("root", "0"):
                has_user = True

        if stripped.upper().startswith("HEALTHCHECK "):
            has_healthcheck = True

        # Run regex-based checks
        for check in DOCKERFILE_CHECKS:
            if check["check_type"] == "regex" and check["pattern"]:
                if re.search(check["pattern"], stripped, re.IGNORECASE):
                    result["findings"].append({
                        "check_id": check["id"],
                        "name": check["name"],
                        "description": check["description"],
                        "severity": check["severity"],
                        "line": line_num,
                        "line_content": stripped[:100],
                    })

    # Post-scan checks
    if not has_user:
        for check in DOCKERFILE_CHECKS:
            if check["check_type"] == "no_user_directive":
                result["findings"].append({
                    "check_id": check["id"],
                    "name": check["name"],
                    "description": check["description"],
                    "severity": check["severity"],
                    "line": 0,
                    "line_content": "(no USER directive found)",
                })

    if not has_healthcheck:
        for check in DOCKERFILE_CHECKS:
            if check["check_type"] == "no_healthcheck":
                result["findings"].append({
                    "check_id": check["id"],
                    "name": check["name"],
                    "description": check["description"],
                    "severity": check["severity"],
                    "line": 0,
                    "line_content": "(no HEALTHCHECK directive found)",
                })

    # Summary
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in result["findings"]:
        sev = f.get("severity", "LOW").upper()
        if sev in severity_counts:
            severity_counts[sev] += 1
    result["summary"] = {
        "total": len(result["findings"]),
        **severity_counts,
    }

    return result


def evaluate_gate(
    findings: List[Dict],
    thresholds: Optional[Dict] = None,
) -> Dict:
    """Evaluate container findings against security gate thresholds.

    Args:
        findings: List of vulnerability findings.
        thresholds: Override thresholds (defaults loaded from security_gates.yaml).

    Returns:
        Dict with keys: passed, violations, severity_counts.
    """
    if thresholds is None:
        thresholds = _load_thresholds()

    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        sev = f.get("severity", "LOW").upper()
        if sev in severity_counts:
            severity_counts[sev] += 1

    violations = []
    max_critical = thresholds.get("max_critical", 0)
    max_high = thresholds.get("max_high", 0)

    if severity_counts["CRITICAL"] > max_critical:
        violations.append(
            f"CRITICAL vulnerabilities: {severity_counts['CRITICAL']} (max: {max_critical})"
        )
    if severity_counts["HIGH"] > max_high:
        violations.append(
            f"HIGH vulnerabilities: {severity_counts['HIGH']} (max: {max_high})"
        )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "severity_counts": severity_counts,
        "thresholds": thresholds,
    }


def main():
    parser = argparse.ArgumentParser(description="Container security scanning")
    parser.add_argument("--image", help="Docker image to scan with Trivy")
    parser.add_argument("--dockerfile", help="Dockerfile to analyze for security issues")
    parser.add_argument("--output", help="Write JSON report to this path")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not args.image and not args.dockerfile:
        parser.error("Either --image or --dockerfile is required")

    results = {}
    all_findings = []

    if args.image:
        image_result = scan_image(args.image, output_file=args.output)
        results["image_scan"] = image_result
        all_findings.extend(image_result.get("findings", []))

    if args.dockerfile:
        df_result = scan_dockerfile(args.dockerfile)
        results["dockerfile_scan"] = df_result
        all_findings.extend(df_result.get("findings", []))

    if args.gate:
        gate_result = evaluate_gate(all_findings)
        results["gate"] = gate_result

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if "image_scan" in results:
            r = results["image_scan"]
            print(f"Container Image Scan: {r.get('image', '?')}")
            print(f"  Tool: {r.get('tool', '?')}")
            total = r.get("summary", {}).get("total", 0)
            print(f"  Total vulnerabilities: {total}")
            for f in r.get("findings", [])[:10]:
                sev = f.get("severity", "?")
                pkg = f.get("package", "?")
                vuln_id = f.get("vulnerability_id", "?")
                print(f"    [{sev}] {pkg}: {vuln_id}")
            remaining = len(r.get("findings", [])) - 10
            if remaining > 0:
                print(f"    ... and {remaining} more")

        if "dockerfile_scan" in results:
            r = results["dockerfile_scan"]
            print(f"\nDockerfile Analysis: {r.get('file', '?')}")
            total = r.get("summary", {}).get("total", 0)
            print(f"  Total issues: {total}")
            for f in r.get("findings", []):
                sev = f.get("severity", "?")
                check_id = f.get("check_id", "?")
                name = f.get("name", "?")
                line = f.get("line", "?")
                print(f"    [{sev}] {check_id} line {line}: {name}")

        if args.gate and "gate" in results:
            gate = results["gate"]
            status = "PASSED" if gate["passed"] else "FAILED"
            print(f"\n  Security Gate: {status}")
            for v in gate.get("violations", []):
                print(f"    VIOLATION: {v}")


if __name__ == "__main__":
    main()
