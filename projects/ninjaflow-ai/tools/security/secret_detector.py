#!/usr/bin/env python3
# CUI // SP-CTI
"""Secret Detector — scan codebases for accidentally committed secrets.

Implements:
- scan(project_path) -> subprocess detect-secrets, parse JSON
- evaluate_gate(findings) -> any secrets found = FAIL
- CLI: python tools/security/secret_detector.py --project-path PATH [--gate]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Patterns for built-in fallback scanning when detect-secrets is not available
BUILTIN_PATTERNS = [
    {
        "name": "AWS Access Key",
        "pattern": r"(?:AKIA|ASIA)[A-Z0-9]{16}",
        "severity": "critical",
    },
    {
        "name": "AWS Secret Key",
        "pattern": r"(?:aws_secret_access_key|aws_secret)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}",
        "severity": "critical",
    },
    {
        "name": "Generic API Key",
        "pattern": r"(?:api[_-]?key|apikey)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}['\"]?",
        "severity": "high",
    },
    {
        "name": "Generic Secret",
        "pattern": r"(?:secret|password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
        "severity": "high",
    },
    {
        "name": "Private Key",
        "pattern": r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----",
        "severity": "critical",
    },
    {
        "name": "GitHub Token",
        "pattern": r"gh[pousr]_[A-Za-z0-9_]{36,}",
        "severity": "critical",
    },
    {
        "name": "Slack Token",
        "pattern": r"xox[baprs]-[A-Za-z0-9\-]{10,}",
        "severity": "high",
    },
    {
        "name": "JWT Token",
        "pattern": r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "severity": "medium",
    },
    {
        "name": "Database Connection String",
        "pattern": r"(?:postgres|mysql|mongodb|redis)://[^\s'\"]{10,}",
        "severity": "high",
    },
    {
        "name": "Bearer Token",
        "pattern": r"[Bb]earer\s+[A-Za-z0-9_\-.]{20,}",
        "severity": "high",
    },
]

# File extensions to skip
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg", ".bmp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}

SKIP_DIRS = {
    "venv", "node_modules", ".git", "__pycache__", "build", "dist",
    ".eggs", ".tox", ".mypy_cache", ".pytest_cache",
}


def scan(
    project_path: str,
    output_file: Optional[str] = None,
    use_builtin: bool = False,
) -> Dict:
    """Scan a project for accidentally committed secrets.

    Attempts to use detect-secrets first. Falls back to built-in
    pattern matching if detect-secrets is not available.

    Args:
        project_path: Root path of the project to scan.
        output_file: Optional path to write JSON report.
        use_builtin: Force use of built-in scanner (skip detect-secrets).

    Returns:
        Dict with keys: success, tool, findings, summary, raw_output.
    """
    if not use_builtin:
        result = _run_detect_secrets(project_path)
        if result["success"] or result.get("tool_available"):
            if output_file:
                _write_report(result, output_file)
            return result

    # Fallback to built-in scanning
    result = _run_builtin_scan(project_path)
    if output_file:
        _write_report(result, output_file)
    return result


def _run_detect_secrets(project_path: str) -> Dict:
    """Run detect-secrets on the project."""
    root = Path(project_path)
    result = {
        "tool": "detect-secrets",
        "tool_available": False,
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "",
    }

    # Check if detect-secrets is available
    try:
        version_check = subprocess.run(
            [sys.executable, "-m", "detect_secrets", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            # Try direct command
            version_check = subprocess.run(
                ["detect-secrets", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if version_check.returncode != 0:
                return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return result

    result["tool_available"] = True

    # Run detect-secrets scan
    cmd = [
        sys.executable, "-m", "detect_secrets", "scan",
        "--all-files",
        "--exclude-files", r"(venv|node_modules|\.git|__pycache__|build|dist|\.lock)",
        str(root),
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        result["raw_output"] = proc.stdout + proc.stderr

        if proc.stdout:
            findings = _parse_detect_secrets_output(proc.stdout)
            result["findings"] = findings

        result["success"] = proc.returncode == 0

        result["summary"] = {
            "total": len(result["findings"]),
            "files_with_secrets": len(set(f["file"] for f in result["findings"])),
        }

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "detect-secrets timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running detect-secrets: {str(e)}"

    return result


def _parse_detect_secrets_output(json_str: str) -> List[Dict]:
    """Parse detect-secrets JSON output into structured findings."""
    findings = []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return findings

    results = data.get("results", {})
    for file_path, secrets in results.items():
        for secret in secrets:
            findings.append({
                "file": file_path,
                "line": secret.get("line_number", 0),
                "type": secret.get("type", "Unknown"),
                "hashed_secret": secret.get("hashed_secret", ""),
                "is_verified": secret.get("is_verified", False),
                "severity": "critical",
            })

    return findings


def _run_builtin_scan(project_path: str) -> Dict:
    """Run built-in pattern-based secret detection."""
    root = Path(project_path)
    result = {
        "tool": "builtin-scanner",
        "tool_available": True,
        "success": True,
        "findings": [],
        "summary": {},
        "raw_output": "Using built-in pattern scanner (detect-secrets not available)",
    }

    compiled_patterns = [
        (re.compile(p["pattern"], re.IGNORECASE), p["name"], p["severity"])
        for p in BUILTIN_PATTERNS
    ]

    files_scanned = 0
    for file_path in _walk_files(root):
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1

            for line_num, line in enumerate(content.splitlines(), 1):
                for pattern, name, severity in compiled_patterns:
                    if pattern.search(line):
                        # Avoid false positives in comments about patterns
                        if "example" in line.lower() or "placeholder" in line.lower():
                            continue
                        # Avoid matching variable declarations without values
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            # Still flag if it looks like an actual secret in a comment
                            if "AKIA" not in line and "BEGIN PRIVATE KEY" not in line:
                                continue

                        result["findings"].append({
                            "file": str(file_path.relative_to(root)),
                            "line": line_num,
                            "type": name,
                            "severity": severity,
                            "match_preview": _redact_line(line.strip(), 80),
                        })
        except (IOError, UnicodeDecodeError):
            continue

    result["summary"] = {
        "total": len(result["findings"]),
        "files_scanned": files_scanned,
        "files_with_secrets": len(set(f["file"] for f in result["findings"])),
    }

    return result


def _walk_files(root: Path):
    """Walk project files, skipping binary and ignored directories."""
    for item in root.iterdir():
        if item.name in SKIP_DIRS:
            continue
        if item.is_dir():
            yield from _walk_files(item)
        elif item.is_file():
            if item.suffix.lower() not in SKIP_EXTENSIONS:
                yield item


def _redact_line(line: str, max_len: int = 80) -> str:
    """Redact potentially sensitive values from a line for safe display."""
    # Replace anything that looks like a secret value
    redacted = re.sub(
        r"([=:]\s*['\"]?)[A-Za-z0-9/+=_\-]{10,}(['\"]?)",
        r"\1[REDACTED]\2",
        line,
    )
    if len(redacted) > max_len:
        redacted = redacted[:max_len] + "..."
    return redacted


def _write_report(result: Dict, output_file: str) -> None:
    """Write scan results to a JSON file."""
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["report_path"] = str(path)


def evaluate_gate(findings: List[Dict]) -> Dict:
    """Evaluate secret findings against security gates.

    Any secrets found = FAIL. Secrets should never be committed.

    Args:
        findings: List of secret findings.

    Returns:
        Dict with keys: passed, violations, total_secrets.
    """
    violations = []
    if findings:
        violations.append(f"Secrets detected: {len(findings)} finding(s)")

        # Count by type
        type_counts = {}
        for f in findings:
            t = f.get("type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        for secret_type, count in type_counts.items():
            violations.append(f"  {secret_type}: {count}")

    return {
        "passed": len(findings) == 0,
        "violations": violations,
        "total_secrets": len(findings),
    }


def main():
    parser = argparse.ArgumentParser(description="Secret detection scanner")
    parser.add_argument("--project-path", required=True, help="Project path to scan")
    parser.add_argument("--output", help="Write JSON report to this path")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument("--builtin", action="store_true", help="Force built-in scanner")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = scan(args.project_path, output_file=args.output, use_builtin=args.builtin)
    findings = result.get("findings", [])

    if args.gate:
        gate_result = evaluate_gate(findings)
        result["gate"] = gate_result

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        tool = result.get("tool", "unknown")
        print(f"Secret Detection ({tool})")
        print(f"  Total secrets found: {len(findings)}")

        if findings:
            for f in findings[:20]:
                sev = f.get("severity", "?")
                file_path = f.get("file", "?")
                line = f.get("line", "?")
                secret_type = f.get("type", "?")
                print(f"  [{sev}] {file_path}:{line} - {secret_type}")
            if len(findings) > 20:
                print(f"  ... and {len(findings) - 20} more")
        else:
            print("  No secrets detected.")

        if args.gate:
            gate = result["gate"]
            status = "PASSED" if gate["passed"] else "FAILED"
            print(f"\n  Security Gate: {status}")
            for v in gate.get("violations", []):
                print(f"    VIOLATION: {v}")


if __name__ == "__main__":
    main()
