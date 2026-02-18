#!/usr/bin/env python3
"""Linting Wrapper — runs language-specific linters and returns structured findings.

Implements:
- lint_python(project_path) -> run flake8 via subprocess
- lint_javascript(project_path) -> run eslint via subprocess
- detect_language(project_path) -> check for pyproject.toml or package.json
- Parse output, return structured findings
- CLI: python tools/builder/linter.py --project-path PATH [--fix]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Language support (Phase 16)
try:
    import importlib.util as _ilu
    _ls_path = Path(__file__).parent / "language_support.py"
    if _ls_path.exists():
        _ls_spec = _ilu.spec_from_file_location("language_support", _ls_path)
        _ls_mod = _ilu.module_from_spec(_ls_spec)
        _ls_spec.loader.exec_module(_ls_mod)
        detect_languages = _ls_mod.detect_languages
    else:
        detect_languages = None
except Exception:
    detect_languages = None


def detect_language(project_path: str) -> List[str]:
    """Detect project languages by checking for config files.

    Args:
        project_path: Root path of the project.

    Returns:
        List of detected languages (e.g., ["python"], ["javascript"], or both).
    """
    root = Path(project_path)
    languages = []

    # Python indicators
    python_indicators = [
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "Pipfile", "poetry.lock", "tox.ini",
    ]
    for indicator in python_indicators:
        if (root / indicator).exists():
            languages.append("python")
            break

    # If no config file found, check for .py files
    if "python" not in languages:
        py_files = list(root.glob("**/*.py"))
        # Exclude venv/node_modules
        py_files = [f for f in py_files if "venv" not in str(f) and "node_modules" not in str(f)]
        if py_files:
            languages.append("python")

    # JavaScript indicators
    js_indicators = ["package.json", ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml"]
    for indicator in js_indicators:
        if (root / indicator).exists():
            languages.append("javascript")
            break

    if "javascript" not in languages:
        js_files = list(root.glob("**/*.js")) + list(root.glob("**/*.ts"))
        js_files = [f for f in js_files if "node_modules" not in str(f)]
        if js_files:
            languages.append("javascript")

    return languages


def lint_python(project_path: str, fix: bool = False) -> Dict:
    """Run flake8 on a Python project.

    Args:
        project_path: Root path of the project.
        fix: If True, run autopep8 to auto-fix issues (best-effort).

    Returns:
        Dict with keys: success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "python",
        "tool": "flake8",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if flake8 is available
    try:
        version_check = subprocess.run(
            [sys.executable, "-m", "flake8", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "flake8 not installed. Install with: pip install flake8"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "flake8 not found. Install with: pip install flake8"
        return result

    # Run flake8
    cmd = [
        sys.executable, "-m", "flake8",
        "--format=json" if _flake8_supports_json() else "--format=default",
        "--max-line-length=100",
        "--exclude=venv,node_modules,.git,__pycache__,.eggs,build,dist",
        str(root),
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse flake8 output (default format: file:line:col: CODE message)
        findings = _parse_flake8_output(proc.stdout)
        result["findings"] = findings
        result["success"] = proc.returncode == 0

        # Summary
        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "flake8 timed out after 120 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running flake8: {str(e)}"

    # Auto-fix attempt
    if fix and not result["success"]:
        result["fix_attempted"] = True
        try:
            subprocess.run(
                [sys.executable, "-m", "autopep8", "--in-place", "--recursive",
                 "--max-line-length=100", str(root)],
                capture_output=True, text=True, timeout=120,
            )
            result["fix_output"] = "autopep8 applied (re-run lint to verify)"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            result["fix_output"] = "autopep8 not available. Install with: pip install autopep8"

    return result


def lint_javascript(project_path: str, fix: bool = False) -> Dict:
    """Run eslint on a JavaScript project.

    Args:
        project_path: Root path of the project.
        fix: If True, run eslint --fix to auto-fix issues.

    Returns:
        Dict with keys: success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "javascript",
        "tool": "eslint",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Determine eslint command (npx or global)
    eslint_cmd = None
    for cmd in ["npx eslint", "eslint"]:
        try:
            parts = cmd.split()
            check = subprocess.run(
                parts + ["--version"],
                capture_output=True, text=True, timeout=30, cwd=str(root),
            )
            if check.returncode == 0:
                eslint_cmd = parts
                break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    if not eslint_cmd:
        result["success"] = False
        result["raw_output"] = "eslint not found. Install with: npm install -g eslint"
        return result

    # Build eslint command
    cmd = eslint_cmd + [
        "--format=json",
        "--ext", ".js,.jsx,.ts,.tsx",
    ]
    if fix:
        cmd.append("--fix")
    cmd.append(str(root / "src"))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse eslint JSON output
        findings = _parse_eslint_output(proc.stdout)
        result["findings"] = findings
        result["success"] = proc.returncode == 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "eslint timed out after 120 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running eslint: {str(e)}"

    return result


def lint_java(project_path: str, fix: bool = False) -> Dict:
    """Run checkstyle (via Maven) on a Java project.

    Args:
        project_path: Root path of the project.
        fix: If True, attempt auto-fix (not supported for checkstyle).

    Returns:
        Dict with keys: language, tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "java",
        "tool": "checkstyle",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Try Maven checkstyle plugin
    mvn_cmd = "mvn"
    try:
        version_check = subprocess.run(
            [mvn_cmd, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "Maven not found. Install Maven and checkstyle plugin."
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "Maven not found. Install Maven and checkstyle plugin."
        return result

    cmd = [mvn_cmd, "checkstyle:check", "-q"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse checkstyle output from Maven (line format: [WARNING] file:line:col: message)
        findings = []
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            # Maven checkstyle format: [SEVERITY] file.java:[line,col] (or file:line:col:) message
            for prefix, severity in [("[ERROR]", "error"), ("[WARNING]", "warning"), ("[INFO]", "info")]:
                if line.startswith(prefix):
                    rest = line[len(prefix):].strip()
                    # Try to parse file:line:col: message or file:[line,col] message
                    parts = rest.split(":", 3)
                    if len(parts) >= 3:
                        file_path = parts[0].strip()
                        try:
                            line_num = int(parts[1].strip().strip("[").strip("]"))
                            col_num = int(parts[2].strip().split("]")[0].strip()) if parts[2].strip() else 0
                        except (ValueError, IndexError):
                            line_num = 0
                            col_num = 0
                        message = parts[3].strip() if len(parts) > 3 else rest
                        findings.append({
                            "file": file_path,
                            "line": line_num,
                            "column": col_num,
                            "code": "checkstyle",
                            "message": message,
                            "severity": severity,
                        })
                    break

        result["findings"] = findings
        result["success"] = proc.returncode == 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "Maven checkstyle timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running checkstyle: {str(e)}"

    return result


def lint_go(project_path: str, fix: bool = False) -> Dict:
    """Run golangci-lint on a Go project.

    Args:
        project_path: Root path of the project.
        fix: If True, run golangci-lint with --fix.

    Returns:
        Dict with keys: language, tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "go",
        "tool": "golangci-lint",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if golangci-lint is available
    try:
        version_check = subprocess.run(
            ["golangci-lint", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "golangci-lint not found. Install with: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest"
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "golangci-lint not found. Install with: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest"
        return result

    cmd = ["golangci-lint", "run", "--out-format", "json"]
    if fix:
        cmd.append("--fix")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse golangci-lint JSON output
        findings = []
        try:
            data = json.loads(proc.stdout) if proc.stdout else {}
            issues = data.get("Issues", [])
            for issue in issues:
                pos = issue.get("Pos", {})
                severity = "warning"
                sev_str = issue.get("Severity", "").lower()
                if sev_str in ("error", "warning", "info"):
                    severity = sev_str
                findings.append({
                    "file": pos.get("Filename", ""),
                    "line": pos.get("Line", 0),
                    "column": pos.get("Column", 0),
                    "code": issue.get("FromLinter", ""),
                    "message": issue.get("Text", ""),
                    "severity": severity,
                })
        except json.JSONDecodeError:
            # If JSON parsing fails, tool still ran
            pass

        result["findings"] = findings
        result["success"] = proc.returncode == 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "golangci-lint timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running golangci-lint: {str(e)}"

    return result


def lint_rust(project_path: str, fix: bool = False) -> Dict:
    """Run cargo clippy on a Rust project.

    Args:
        project_path: Root path of the project.
        fix: If True, run cargo clippy --fix.

    Returns:
        Dict with keys: language, tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "rust",
        "tool": "clippy",
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

    cmd = ["cargo", "clippy", "--message-format=json"]
    if fix:
        cmd = ["cargo", "clippy", "--fix", "--allow-dirty", "--message-format=json"]

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
                severity = "warning"
                if level == "error":
                    severity = "error"
                elif level in ("note", "help"):
                    severity = "info"

                spans = message.get("spans", [])
                file_path = ""
                line_num = 0
                col_num = 0
                if spans:
                    primary = spans[0]
                    file_path = primary.get("file_name", "")
                    line_num = primary.get("line_start", 0)
                    col_num = primary.get("column_start", 0)

                code_info = message.get("code", {}) or {}
                code_str = code_info.get("code", "") if isinstance(code_info, dict) else ""

                findings.append({
                    "file": file_path,
                    "line": line_num,
                    "column": col_num,
                    "code": code_str,
                    "message": message.get("message", ""),
                    "severity": severity,
                })
            except json.JSONDecodeError:
                continue

        result["findings"] = findings
        result["success"] = proc.returncode == 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "cargo clippy timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running cargo clippy: {str(e)}"

    return result


def lint_csharp(project_path: str, fix: bool = False) -> Dict:
    """Run dotnet build to check for C# warnings and errors.

    Args:
        project_path: Root path of the project.
        fix: If True, attempt auto-fix (not directly supported).

    Returns:
        Dict with keys: language, tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "csharp",
        "tool": "dotnet-build",
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

    cmd = ["dotnet", "build", "--no-restore", "/p:TreatWarningsAsErrors=false"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        # Parse MSBuild output for warnings/errors
        # Format: path(line,col): severity CODE: message
        findings = []
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            # MSBuild format: File.cs(line,col): warning CS1234: message
            # or: File.cs(line,col): error CS1234: message
            for sev_tag in ["warning", "error"]:
                tag = f": {sev_tag} "
                if tag in line:
                    parts = line.split(tag, 1)
                    if len(parts) == 2:
                        location = parts[0].strip()
                        rest = parts[1].strip()
                        # Parse location: path(line,col)
                        file_path = location
                        line_num = 0
                        col_num = 0
                        paren_idx = location.rfind("(")
                        if paren_idx >= 0:
                            file_path = location[:paren_idx]
                            loc_str = location[paren_idx + 1:].rstrip(")")
                            loc_parts = loc_str.split(",")
                            try:
                                line_num = int(loc_parts[0].strip())
                                if len(loc_parts) > 1:
                                    col_num = int(loc_parts[1].strip())
                            except (ValueError, IndexError):
                                pass
                        # Parse code and message from rest: CS1234: message
                        code = ""
                        message = rest
                        colon_idx = rest.find(":")
                        if colon_idx > 0:
                            code = rest[:colon_idx].strip()
                            message = rest[colon_idx + 1:].strip()

                        findings.append({
                            "file": file_path,
                            "line": line_num,
                            "column": col_num,
                            "code": code,
                            "message": message,
                            "severity": sev_tag,
                        })
                    break

        result["findings"] = findings
        result["success"] = proc.returncode == 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            severity_counts[f.get("severity", "warning")] += 1
        result["summary"] = {
            "total": len(findings),
            **severity_counts,
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "dotnet build timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running dotnet build: {str(e)}"

    return result


def lint_typescript(project_path: str, fix: bool = False) -> Dict:
    """Run tsc --noEmit and eslint on a TypeScript project.

    Args:
        project_path: Root path of the project.
        fix: If True, run eslint --fix.

    Returns:
        Dict with keys: language, tool, success, findings, summary, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "typescript",
        "tool": "tsc+eslint",
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    all_findings = []
    outputs = []

    # Step 1: Run tsc --noEmit for type checking
    try:
        tsc_proc = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        outputs.append(f"=== tsc ===\n{tsc_proc.stdout}{tsc_proc.stderr}")

        # Parse tsc output: file(line,col): error TS1234: message
        for line in (tsc_proc.stdout + tsc_proc.stderr).splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: file.ts(line,col): error TS1234: message
            for sev_tag in ["error", "warning"]:
                tag = f": {sev_tag} "
                if tag in line:
                    parts = line.split(tag, 1)
                    if len(parts) == 2:
                        location = parts[0].strip()
                        rest = parts[1].strip()
                        file_path = location
                        line_num = 0
                        col_num = 0
                        paren_idx = location.rfind("(")
                        if paren_idx >= 0:
                            file_path = location[:paren_idx]
                            loc_str = location[paren_idx + 1:].rstrip(")")
                            loc_parts = loc_str.split(",")
                            try:
                                line_num = int(loc_parts[0].strip())
                                if len(loc_parts) > 1:
                                    col_num = int(loc_parts[1].strip())
                            except (ValueError, IndexError):
                                pass
                        code = ""
                        message = rest
                        colon_idx = rest.find(":")
                        if colon_idx > 0:
                            code = rest[:colon_idx].strip()
                            message = rest[colon_idx + 1:].strip()

                        all_findings.append({
                            "file": file_path,
                            "line": line_num,
                            "column": col_num,
                            "code": code,
                            "message": message,
                            "severity": sev_tag,
                        })
                    break

        if tsc_proc.returncode != 0:
            result["success"] = False

    except (subprocess.TimeoutExpired, FileNotFoundError):
        outputs.append("=== tsc ===\ntsc not found or timed out")

    # Step 2: Run eslint on TypeScript files
    try:
        eslint_cmd = ["npx", "eslint", "--ext", ".ts,.tsx", "--format", "json"]
        if fix:
            eslint_cmd.append("--fix")
        eslint_cmd.append(str(root / "src") if (root / "src").is_dir() else str(root))

        eslint_proc = subprocess.run(
            eslint_cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        outputs.append(f"=== eslint ===\n{eslint_proc.stdout}{eslint_proc.stderr}")

        # Parse eslint JSON output
        eslint_findings = _parse_eslint_output(eslint_proc.stdout)
        all_findings.extend(eslint_findings)

        if eslint_proc.returncode != 0:
            result["success"] = False

    except (subprocess.TimeoutExpired, FileNotFoundError):
        outputs.append("=== eslint ===\neslint not found or timed out")

    result["findings"] = all_findings
    result["raw_output"] = "\n".join(outputs)

    severity_counts = {"error": 0, "warning": 0, "info": 0}
    for f in all_findings:
        severity_counts[f.get("severity", "warning")] += 1
    result["summary"] = {
        "total": len(all_findings),
        **severity_counts,
    }

    return result


def _flake8_supports_json() -> bool:
    """Check if flake8 supports JSON output (requires flake8-json plugin)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "flake8", "--format=json", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return "json" not in result.stderr.lower()
    except Exception:
        return False


def _parse_flake8_output(output: str) -> List[Dict]:
    """Parse flake8 default output format into structured findings.

    Format: file:line:col: CODE message
    """
    findings = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Try to parse: path:line:col: CODE message
        parts = line.split(":", 3)
        if len(parts) >= 4:
            file_path = parts[0].strip()
            try:
                line_num = int(parts[1].strip())
                col_num = int(parts[2].strip())
            except ValueError:
                continue
            rest = parts[3].strip()
            # Extract code (e.g., E501, W503, F401)
            code = ""
            message = rest
            if rest and rest[0] in "EWFC":
                space_idx = rest.find(" ")
                if space_idx > 0:
                    code = rest[:space_idx]
                    message = rest[space_idx + 1:]

            severity = "warning"
            if code.startswith("E") or code.startswith("F"):
                severity = "error"
            elif code.startswith("W"):
                severity = "warning"
            elif code.startswith("C"):
                severity = "info"

            findings.append({
                "file": file_path,
                "line": line_num,
                "column": col_num,
                "code": code,
                "message": message,
                "severity": severity,
            })
    return findings


def _parse_eslint_output(output: str) -> List[Dict]:
    """Parse eslint JSON output into structured findings."""
    findings = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return findings

    for file_result in data:
        file_path = file_result.get("filePath", "")
        for msg in file_result.get("messages", []):
            severity_map = {2: "error", 1: "warning", 0: "info"}
            findings.append({
                "file": file_path,
                "line": msg.get("line", 0),
                "column": msg.get("column", 0),
                "code": msg.get("ruleId", ""),
                "message": msg.get("message", ""),
                "severity": severity_map.get(msg.get("severity", 1), "warning"),
            })
    return findings


def lint_project(project_path: str, fix: bool = False) -> Dict:
    """Lint a project, auto-detecting languages.

    Args:
        project_path: Root path of the project.
        fix: If True, attempt auto-fixes.

    Returns:
        Dict with results for each detected language.
    """
    # Use language_support.detect_languages if available, fall back to detect_language
    if detect_languages is not None:
        languages = detect_languages(project_path)
    else:
        languages = detect_language(project_path)

    LINTERS = {
        "python": lint_python,
        "javascript": lint_javascript,
        "typescript": lint_typescript,
        "java": lint_java,
        "go": lint_go,
        "rust": lint_rust,
        "csharp": lint_csharp,
    }

    results = {
        "project_path": project_path,
        "languages_detected": languages,
        "results": {},
        "overall_success": True,
    }

    if not languages:
        results["overall_success"] = True
        results["message"] = "No supported languages detected"
        return results

    for lang in languages:
        linter_fn = LINTERS.get(lang)
        if linter_fn:
            lang_result = linter_fn(project_path, fix=fix)
            results["results"][lang] = lang_result
            if not lang_result["success"]:
                results["overall_success"] = False

    return results


def main():
    parser = argparse.ArgumentParser(description="Run linters on a project")
    parser.add_argument("--project-path", required=True, help="Root path of the project")
    parser.add_argument("--fix", action="store_true", help="Attempt auto-fix")
    parser.add_argument(
        "--language",
        choices=["python", "javascript", "typescript", "java", "go", "rust", "csharp", "auto"],
        default="auto",
        help="Language to lint (default: auto-detect)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    LINTERS = {
        "python": lint_python,
        "javascript": lint_javascript,
        "typescript": lint_typescript,
        "java": lint_java,
        "go": lint_go,
        "rust": lint_rust,
        "csharp": lint_csharp,
    }

    if args.language == "auto":
        results = lint_project(args.project_path, fix=args.fix)
    elif args.language in LINTERS:
        results = LINTERS[args.language](args.project_path, fix=args.fix)
    else:
        results = lint_project(args.project_path, fix=args.fix)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Human-readable output
        if isinstance(results, dict) and "results" in results:
            # Multi-language result
            langs = results.get("languages_detected", [])
            print(f"Languages detected: {', '.join(langs) if langs else 'none'}")
            for lang, res in results.get("results", {}).items():
                _print_lint_result(res)
            status = "PASS" if results["overall_success"] else "FAIL"
            print(f"\nOverall: {status}")
        else:
            _print_lint_result(results)


def _print_lint_result(result: Dict) -> None:
    """Print a single lint result in human-readable format."""
    tool = result.get("tool", "unknown")
    lang = result.get("language", "unknown")
    findings = result.get("findings", [])
    summary = result.get("summary", {})

    print(f"\n--- {lang} ({tool}) ---")
    if not findings:
        if result.get("success"):
            print("  No issues found.")
        else:
            print(f"  Error: {result.get('raw_output', 'unknown error')[:200]}")
        return

    for f in findings[:50]:  # Limit display
        print(f"  {f['file']}:{f['line']}:{f['column']} [{f['severity']}] {f['code']} {f['message']}")

    if len(findings) > 50:
        print(f"  ... and {len(findings) - 50} more")

    total = summary.get("total", len(findings))
    errors = summary.get("error", 0)
    warnings = summary.get("warning", 0)
    print(f"  Total: {total} (errors: {errors}, warnings: {warnings})")


if __name__ == "__main__":
    main()
