#!/usr/bin/env python3
"""SAST Runner — Static Application Security Testing wrapper.

Implements:
- run_bandit(project_path) -> subprocess bandit, parse JSON output
- parse_report(report_path) -> parse bandit JSON report into structured findings
- evaluate_gate(findings, thresholds) -> check against security_gates.yaml
- CLI: python tools/security/sast_runner.py --project-path PATH [--report REPORT_PATH] [--gate]
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
    """Load security gate thresholds from args/security_gates.yaml."""
    defaults = {
        "max_critical": 0,
        "max_high": 0,
        "max_medium": 10,
    }
    if not GATES_PATH.exists():
        return defaults

    try:
        # Parse YAML manually to avoid pyyaml dependency
        content = GATES_PATH.read_text(encoding="utf-8")
        in_sast = False
        thresholds = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "sast:":
                in_sast = True
                continue
            if in_sast:
                if stripped and not stripped.startswith("#"):
                    if ":" in stripped and stripped[0] != " " and not stripped.startswith("max_"):
                        in_sast = False
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


def run_bandit(
    project_path: str,
    output_file: Optional[str] = None,
    severity_level: str = "low",
    confidence_level: str = "low",
) -> Dict:
    """Run Bandit SAST scanner on a Python project.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.
        severity_level: Minimum severity to report (low, medium, high).
        confidence_level: Minimum confidence to report (low, medium, high).

    Returns:
        Dict with keys: success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "bandit",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if bandit is available
    try:
        version_check = subprocess.run(
            [sys.executable, "-m", "bandit", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "bandit not installed. Install with: pip install bandit"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "bandit not found. Install with: pip install bandit"
        return result

    # Build bandit command
    cmd = [
        sys.executable, "-m", "bandit",
        "-r",  # Recursive
        "-f", "json",  # JSON output
        "-ll" if severity_level == "medium" else "-l" if severity_level == "low" else "-lll",
        "--exclude", "venv,node_modules,.git,__pycache__,build,dist,tests",
        str(root),
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )

        # Bandit returns exit code 1 when it finds issues
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse JSON output
        if proc.stdout:
            findings = parse_report_json(proc.stdout)
            result["findings"] = findings
        else:
            result["findings"] = []

        # Success means the tool ran (even if it found issues)
        result["success"] = proc.returncode in (0, 1)

        # Build summary
        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in result["findings"]:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {
            "total": len(result["findings"]),
            **severity_counts,
        }

        # Write report file if requested
        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "bandit timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running bandit: {str(e)}"

    return result


def parse_report_json(json_str: str) -> List[Dict]:
    """Parse bandit JSON output into structured findings.

    Args:
        json_str: Raw JSON string from bandit output.

    Returns:
        List of finding dicts with file, line, severity, confidence, etc.
    """
    findings = []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return findings

    for result in data.get("results", []):
        findings.append({
            "file": result.get("filename", ""),
            "line": result.get("line_number", 0),
            "col_offset": result.get("col_offset", 0),
            "end_col_offset": result.get("end_col_offset", 0),
            "severity": result.get("issue_severity", "LOW"),
            "confidence": result.get("issue_confidence", "LOW"),
            "test_id": result.get("test_id", ""),
            "test_name": result.get("test_name", ""),
            "issue_text": result.get("issue_text", ""),
            "issue_cwe": result.get("issue_cwe", {}),
            "more_info": result.get("more_info", ""),
            "line_range": result.get("line_range", []),
            "code": result.get("code", ""),
        })

    return findings


def parse_report(report_path: str) -> List[Dict]:
    """Parse a bandit JSON report file into structured findings.

    Args:
        report_path: Path to the bandit JSON report file.

    Returns:
        List of finding dicts.
    """
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"Report file not found: {report_path}")

    content = path.read_text(encoding="utf-8")

    # The report might be our wrapper format or raw bandit output
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    # If it's our wrapper format
    if "findings" in data:
        return data["findings"]

    # If it's raw bandit output
    return parse_report_json(content)


def run_spotbugs(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run SpotBugs SAST scanner on a Java project via Maven.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "spotbugs",
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
            result["raw_output"] = "Maven not found. Install Maven and SpotBugs plugin."
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "Maven not found. Install Maven and SpotBugs plugin."
        return result

    cmd = ["mvn", "spotbugs:check", "-q"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout + proc.stderr
        result["raw_output"] = raw

        # Parse SpotBugs output from Maven
        findings = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # SpotBugs Maven output: [ERROR]/[WARNING] at File.java:[line,col] message
            for prefix, severity in [("[ERROR]", "HIGH"), ("[WARNING]", "MEDIUM")]:
                if line.startswith(prefix):
                    rest = line[len(prefix):].strip()
                    file_path = ""
                    line_num = 0
                    message = rest
                    # Try to parse file and line info
                    if ":" in rest:
                        parts = rest.split(":", 1)
                        file_path = parts[0].strip()
                        message = parts[1].strip() if len(parts) > 1 else rest
                        # Try to extract line number from file_path
                        bracket_idx = file_path.find("[")
                        if bracket_idx >= 0:
                            try:
                                line_str = file_path[bracket_idx + 1:].split(",")[0].split("]")[0]
                                line_num = int(line_str)
                            except (ValueError, IndexError):
                                pass
                            file_path = file_path[:bracket_idx]

                    findings.append({
                        "file": file_path,
                        "line": line_num,
                        "severity": severity,
                        "confidence": "MEDIUM",
                        "message": message,
                        "cwe": "",
                    })
                    break

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "SpotBugs timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running SpotBugs: {str(e)}"

    return result


def run_gosec(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run gosec SAST scanner on a Go project.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "gosec",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if gosec is available
    try:
        version_check = subprocess.run(
            ["gosec", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "gosec not found. Install with: go install github.com/securego/gosec/v2/cmd/gosec@latest"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "gosec not found. Install with: go install github.com/securego/gosec/v2/cmd/gosec@latest"
        return result

    cmd = ["gosec", "-fmt=json", "./..."]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse gosec JSON output
        findings = []
        if proc.stdout:
            try:
                data = json.loads(proc.stdout)
                for issue in data.get("Issues", []):
                    severity = issue.get("severity", "LOW").upper()
                    findings.append({
                        "file": issue.get("file", ""),
                        "line": int(issue.get("line", 0)) if issue.get("line", "").isdigit() else 0,
                        "severity": severity,
                        "confidence": issue.get("confidence", "LOW").upper(),
                        "message": issue.get("details", ""),
                        "cwe": issue.get("cwe", {}).get("id", "") if isinstance(issue.get("cwe"), dict) else str(issue.get("cwe", "")),
                    })
            except (json.JSONDecodeError, ValueError):
                pass

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "gosec timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running gosec: {str(e)}"

    return result


def run_cargo_audit_sast(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run cargo clippy with security-related lints on a Rust project.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "cargo-clippy-security",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if cargo is available
    try:
        version_check = subprocess.run(
            ["cargo", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "cargo not found. Install Rust toolchain from https://rustup.rs"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "cargo not found. Install Rust toolchain from https://rustup.rs"
        return result

    cmd = ["cargo", "clippy", "--message-format=json", "--", "-D", "warnings"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse cargo clippy JSON output (one JSON object per line)
        findings = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg_data = json.loads(line)
                if msg_data.get("reason") != "compiler-message":
                    continue
                message = msg_data.get("message", {})
                level = message.get("level", "warning")
                severity = "MEDIUM"
                if level == "error":
                    severity = "HIGH"
                elif level in ("note", "help"):
                    severity = "LOW"

                spans = message.get("spans", [])
                file_path = ""
                line_num = 0
                if spans:
                    primary = spans[0]
                    file_path = primary.get("file_name", "")
                    line_num = primary.get("line_start", 0)

                code_info = message.get("code", {}) or {}
                code_str = code_info.get("code", "") if isinstance(code_info, dict) else ""

                findings.append({
                    "file": file_path,
                    "line": line_num,
                    "severity": severity,
                    "confidence": "MEDIUM",
                    "message": message.get("message", ""),
                    "cwe": code_str,
                })
            except json.JSONDecodeError:
                continue

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "cargo clippy timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running cargo clippy: {str(e)}"

    return result


def run_security_code_scan(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run SecurityCodeScan via dotnet build on a C# project.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "security-code-scan",
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

    # SecurityCodeScan runs as a Roslyn analyzer during build
    cmd = ["dotnet", "build", "--no-restore"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout + proc.stderr
        result["raw_output"] = raw

        # Parse MSBuild output for SecurityCodeScan warnings (SCS prefixed codes)
        findings = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Look for security warnings: path(line,col): warning SCS0001: message
            if ": warning SCS" in line or ": error SCS" in line:
                for sev_tag, severity in [("error", "HIGH"), ("warning", "MEDIUM")]:
                    tag = f": {sev_tag} "
                    if tag in line:
                        parts = line.split(tag, 1)
                        if len(parts) == 2:
                            location = parts[0].strip()
                            rest = parts[1].strip()
                            file_path = location
                            line_num = 0
                            paren_idx = location.rfind("(")
                            if paren_idx >= 0:
                                file_path = location[:paren_idx]
                                loc_str = location[paren_idx + 1:].rstrip(")")
                                loc_parts = loc_str.split(",")
                                try:
                                    line_num = int(loc_parts[0].strip())
                                except (ValueError, IndexError):
                                    pass
                            code = ""
                            message = rest
                            colon_idx = rest.find(":")
                            if colon_idx > 0:
                                code = rest[:colon_idx].strip()
                                message = rest[colon_idx + 1:].strip()

                            findings.append({
                                "file": file_path,
                                "line": line_num,
                                "severity": severity,
                                "confidence": "MEDIUM",
                                "message": message,
                                "cwe": code,
                            })
                        break

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "dotnet build timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running SecurityCodeScan: {str(e)}"

    return result


def run_eslint_security(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run eslint with security plugin on a JavaScript/TypeScript project.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.

    Returns:
        Dict with keys: tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "tool": "eslint-security",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if npx/eslint is available
    try:
        version_check = subprocess.run(
            ["npx", "eslint", "--version"],
            capture_output=True, text=True, timeout=30, cwd=str(root),
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "eslint not found. Install with: npm install eslint eslint-plugin-security"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "eslint not found. Install with: npm install eslint eslint-plugin-security"
        return result

    cmd = [
        "npx", "eslint",
        "--plugin", "security",
        "--ext", ".js,.ts,.jsx,.tsx",
        "--format", "json",
        str(root / "src") if (root / "src").is_dir() else str(root),
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        raw = proc.stdout if proc.stdout else proc.stderr
        result["raw_output"] = raw

        # Parse eslint JSON output
        findings = []
        if proc.stdout:
            try:
                data = json.loads(proc.stdout)
                for file_result in data:
                    file_path = file_result.get("filePath", "")
                    for msg in file_result.get("messages", []):
                        rule_id = msg.get("ruleId", "")
                        # Focus on security-related rules
                        severity_map = {2: "HIGH", 1: "MEDIUM", 0: "LOW"}
                        findings.append({
                            "file": file_path,
                            "line": msg.get("line", 0),
                            "severity": severity_map.get(msg.get("severity", 1), "MEDIUM"),
                            "confidence": "MEDIUM",
                            "message": msg.get("message", ""),
                            "cwe": rule_id,
                        })
            except json.JSONDecodeError:
                pass

        result["findings"] = findings
        result["success"] = proc.returncode in (0, 1)

        severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = f.get("severity", "LOW").upper()
            if sev in severity_counts:
                severity_counts[sev] += 1
        result["summary"] = {"total": len(findings), **severity_counts}

        if output_file:
            report_path = Path(output_file)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["report_path"] = str(report_path)

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "eslint-security timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running eslint-security: {str(e)}"

    return result


# SAST runner dispatch map (Phase 16)
SAST_RUNNERS = {
    "python": run_bandit,
    "java": run_spotbugs,
    "go": run_gosec,
    "rust": run_cargo_audit_sast,
    "csharp": run_security_code_scan,
    "javascript": run_eslint_security,
    "typescript": run_eslint_security,
}


def run_sast(
    project_path: str,
    output_file: Optional[str] = None,
) -> Dict:
    """Run SAST scanning across all detected languages.

    Auto-detects languages and runs the appropriate SAST tool for each.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write combined JSON report.

    Returns:
        Dict with results for each detected language, combined findings, and summary.
    """
    # Detect languages
    if detect_languages is not None:
        languages = detect_languages(project_path)
    else:
        # Fallback: check for common files
        root = Path(project_path)
        languages = []
        if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            languages.append("python")
        if (root / "package.json").exists():
            languages.append("javascript")
        if (root / "pom.xml").exists() or (root / "build.gradle").exists():
            languages.append("java")
        if (root / "go.mod").exists():
            languages.append("go")
        if (root / "Cargo.toml").exists():
            languages.append("rust")
        if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
            languages.append("csharp")
        if (root / "tsconfig.json").exists():
            languages.append("typescript")

    combined = {
        "project_path": project_path,
        "languages_detected": languages,
        "results": {},
        "all_findings": [],
        "summary": {"total": 0, "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "overall_success": True,
    }

    for lang in languages:
        runner_fn = SAST_RUNNERS.get(lang)
        if runner_fn:
            lang_result = runner_fn(project_path, output_file=None)
            combined["results"][lang] = lang_result
            combined["all_findings"].extend(lang_result.get("findings", []))
            if not lang_result.get("success", False):
                combined["overall_success"] = False

    # Build combined summary
    for f in combined["all_findings"]:
        sev = f.get("severity", "LOW").upper()
        if sev in combined["summary"]:
            combined["summary"][sev] += 1
    combined["summary"]["total"] = len(combined["all_findings"])

    if output_file:
        report_path = Path(output_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
        combined["report_path"] = str(report_path)

    return combined


def evaluate_gate(
    findings: List[Dict],
    thresholds: Optional[Dict] = None,
) -> Dict:
    """Evaluate findings against security gate thresholds.

    Args:
        findings: List of finding dicts from bandit.
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
    max_medium = thresholds.get("max_medium", 10)

    if severity_counts["CRITICAL"] > max_critical:
        violations.append(
            f"CRITICAL findings: {severity_counts['CRITICAL']} (max: {max_critical})"
        )
    if severity_counts["HIGH"] > max_high:
        violations.append(
            f"HIGH findings: {severity_counts['HIGH']} (max: {max_high})"
        )
    if severity_counts["MEDIUM"] > max_medium:
        violations.append(
            f"MEDIUM findings: {severity_counts['MEDIUM']} (max: {max_medium})"
        )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "severity_counts": severity_counts,
        "thresholds": thresholds,
    }


def main():
    parser = argparse.ArgumentParser(description="SAST scanning (Bandit)")
    parser.add_argument("--project-path", help="Project path to scan")
    parser.add_argument("--report", help="Path to existing bandit report to parse")
    parser.add_argument("--output", help="Write JSON report to this path")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.report:
        # Parse existing report
        findings = parse_report(args.report)
        result = {
            "tool": "bandit",
            "findings": findings,
            "summary": {"total": len(findings)},
        }
    elif args.project_path:
        # Run bandit
        result = run_bandit(args.project_path, output_file=args.output)
        findings = result.get("findings", [])
    else:
        parser.error("Either --project-path or --report is required")
        return

    if args.gate:
        gate_result = evaluate_gate(findings)
        result["gate"] = gate_result

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"SAST Scan Results ({result.get('tool', 'bandit')})")
        print(f"  Total findings: {result.get('summary', {}).get('total', len(findings))}")
        for f in findings[:10]:
            sev = f.get("severity", "?")
            print(f"  [{sev}] {f.get('file', '?')}:{f.get('line', '?')} - {f.get('issue_text', '')}")
        if len(findings) > 10:
            print(f"  ... and {len(findings) - 10} more findings")

        if args.gate:
            gate = result["gate"]
            status = "PASSED" if gate["passed"] else "FAILED"
            print(f"\n  Security Gate: {status}")
            for v in gate.get("violations", []):
                print(f"    VIOLATION: {v}")


if __name__ == "__main__":
    main()
