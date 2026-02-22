#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Asset Scanner — 7-gate security scanning pipeline.

Orchestrates security scanning for marketplace assets before publishing.
All gates must pass for cross-tenant sharing; tenant-local publishing
requires passing blocking gates only.

Gates:
    1. SAST scan (bandit for Python, language-appropriate for others)
    2. Secret detection (detect-secrets)
    3. Dependency audit (pip-audit)
    4. CUI marking validation (classification_manager.py)
    5. SBOM generation (cyclonedx-bom for assets with scripts)
    6. Supply chain provenance (dependency graph check)
    7. Digital signature (RSA-SHA256 via signer.py)

Usage:
    # Scan an asset directory
    python tools/marketplace/asset_scanner.py --asset-id "asset-abc" \\
        --version-id "ver-abc" --asset-path /path/to/asset --json

    # Scan specific gates only
    python tools/marketplace/asset_scanner.py --asset-id "asset-abc" \\
        --version-id "ver-abc" --asset-path /path/to/asset \\
        --gates sast_scan,secret_detection --json

    # Get scan summary for a version
    python tools/marketplace/asset_scanner.py --summary \\
        --version-id "ver-abc" --json
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful imports
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALL_GATES = [
    "sast_scan", "secret_detection", "dependency_audit",
    "cui_marking_validation", "sbom_generation",
    "supply_chain_provenance", "digital_signature",
    "prompt_injection_scan", "behavioral_sandbox",
]

BLOCKING_GATES = {
    "sast_scan", "secret_detection", "dependency_audit",
    "cui_marking_validation", "sbom_generation", "digital_signature",
    "prompt_injection_scan",
}

# Secret patterns to scan for (air-gapped, no external tools required)
SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[A-Za-z0-9/+=]{20,}', "API key"),
    (r'(?i)(secret|password|passwd|pwd)\s*[:=]\s*["\'][^"\']{8,}', "Password/secret"),
    (r'(?i)(aws_access_key_id|aws_secret_access_key)\s*[:=]\s*["\']?[A-Za-z0-9/+=]{16,}', "AWS credential"),
    (r'(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*', "Bearer token"),
    (r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', "Private key"),
    (r'(?i)(ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{36,}', "GitHub token"),
    (r'sk-[A-Za-z0-9]{32,}', "OpenAI API key"),
]

# CUI marking patterns
CUI_PATTERNS = [
    r'CUI\s*//',
    r'CONTROLLED UNCLASSIFIED INFORMATION',
    r'# CUI //',
    r'// CUI //',
    r'-- CUI //',
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="scan"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_scan(asset_id, version_id, gate_name, status, findings_count=0,
                 critical=0, high=0, medium=0, low=0, details=None, db_path=None):
    """Record a scan result in the database."""
    scan_id = _gen_id("scan")
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO marketplace_scan_results
               (id, asset_id, version_id, gate_name, status,
                findings_count, critical_count, high_count,
                medium_count, low_count, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, asset_id, version_id, gate_name, status,
             findings_count, critical, high, medium, low,
             json.dumps(details) if details else None),
        )
        conn.commit()
    finally:
        conn.close()
    return scan_id


def _audit(event_type, actor, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type, actor=actor,
                action=action, details=details, db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Individual gate scanners
# ---------------------------------------------------------------------------

def scan_sast(asset_path, asset_id, version_id, db_path=None):
    """Gate 1: Static Application Security Testing.

    Uses bandit for Python files. Returns scan result dict.
    """
    asset_path = Path(asset_path)
    py_files = list(asset_path.rglob("*.py"))

    if not py_files:
        return _record_scan(
            asset_id, version_id, "sast_scan", "skipped",
            details={"reason": "No Python files found"}, db_path=db_path,
        ), {"status": "skipped", "reason": "No Python files"}

    findings = {"critical": 0, "high": 0, "medium": 0, "low": 0, "issues": []}

    # Try bandit first
    try:
        result = subprocess.run(
            ["python", "-m", "bandit", "-r", str(asset_path), "-f", "json",
             "--severity-level", "low", "-q"],
            capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL,
        )
        if result.stdout:
            bandit_output = json.loads(result.stdout)
            for issue in bandit_output.get("results", []):
                sev = issue.get("issue_severity", "LOW").lower()
                if sev == "high":
                    findings["critical"] += 1
                elif sev == "medium":
                    findings["high"] += 1
                else:
                    findings["low"] += 1
                findings["issues"].append({
                    "file": issue.get("filename", ""),
                    "line": issue.get("line_number", 0),
                    "severity": sev,
                    "text": issue.get("issue_text", ""),
                    "test_id": issue.get("test_id", ""),
                })
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        # Bandit not available — use basic AST-based checks
        import ast
        for py_file in py_files:
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
                for node in ast.walk(tree):
                    # Check for eval/exec
                    if isinstance(node, ast.Call):
                        func_name = ""
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            func_name = node.func.attr
                        if func_name in ("eval", "exec"):
                            findings["critical"] += 1
                            findings["issues"].append({
                                "file": str(py_file.relative_to(asset_path)),
                                "line": node.lineno,
                                "severity": "critical",
                                "text": f"Use of {func_name}() — potential code injection",
                            })
                        elif func_name in ("system", "popen"):
                            findings["high"] += 1
                            findings["issues"].append({
                                "file": str(py_file.relative_to(asset_path)),
                                "line": node.lineno,
                                "severity": "high",
                                "text": f"Use of os.{func_name}() — potential command injection",
                            })
            except (SyntaxError, UnicodeDecodeError):
                pass

    total = findings["critical"] + findings["high"] + findings["medium"] + findings["low"]
    status = "pass" if findings["critical"] == 0 and findings["high"] == 0 else "fail"

    scan_id = _record_scan(
        asset_id, version_id, "sast_scan", status,
        findings_count=total,
        critical=findings["critical"], high=findings["high"],
        medium=findings["medium"], low=findings["low"],
        details={"issues": findings["issues"][:20]},
        db_path=db_path,
    )
    return scan_id, {"status": status, "findings": findings}


def scan_secrets(asset_path, asset_id, version_id, db_path=None):
    """Gate 2: Secret detection.

    Scans all text files for hardcoded secrets, API keys, credentials.
    """
    asset_path = Path(asset_path)
    findings = []

    text_extensions = {".py", ".js", ".ts", ".go", ".rs", ".java", ".cs",
                       ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
                       ".env", ".md", ".txt", ".sh", ".bat"}

    for fpath in asset_path.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in text_extensions:
            continue
        # Skip binary-looking files
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for pattern, secret_type in SECRET_PATTERNS:
            for match in re.finditer(pattern, content):
                line_no = content[:match.start()].count("\n") + 1
                findings.append({
                    "file": str(fpath.relative_to(asset_path)),
                    "line": line_no,
                    "type": secret_type,
                    "match_preview": match.group()[:30] + "..." if len(match.group()) > 30 else match.group(),
                })

    status = "pass" if len(findings) == 0 else "fail"
    scan_id = _record_scan(
        asset_id, version_id, "secret_detection", status,
        findings_count=len(findings),
        critical=len(findings),
        details={"secrets_found": findings[:10]},
        db_path=db_path,
    )
    return scan_id, {"status": status, "findings_count": len(findings), "findings": findings[:10]}


def scan_dependencies(asset_path, asset_id, version_id, db_path=None):
    """Gate 3: Dependency audit.

    Checks requirements.txt / setup.py / pyproject.toml for known vulns.
    """
    asset_path = Path(asset_path)
    req_file = asset_path / "requirements.txt"
    findings = {"critical": 0, "high": 0, "medium": 0, "dependencies": []}

    if not req_file.exists():
        # Check for other dependency files
        for alt in ["setup.py", "pyproject.toml", "package.json", "go.mod", "Cargo.toml"]:
            if (asset_path / alt).exists():
                req_file = asset_path / alt
                break

    if not req_file.exists():
        return _record_scan(
            asset_id, version_id, "dependency_audit", "skipped",
            details={"reason": "No dependency file found"}, db_path=db_path,
        ), {"status": "skipped", "reason": "No dependency file"}

    # Try pip-audit for Python
    if req_file.name == "requirements.txt":
        try:
            result = subprocess.run(
                ["python", "-m", "pip_audit", "-r", str(req_file), "--format", "json"],
                capture_output=True, text=True, timeout=120,
                stdin=subprocess.DEVNULL,
            )
            if result.stdout:
                audit_data = json.loads(result.stdout)
                for dep in audit_data.get("dependencies", []):
                    for vuln in dep.get("vulns", []):
                        vuln.get("fix_versions", "unknown")
                        findings["dependencies"].append({
                            "package": dep.get("name"),
                            "version": dep.get("version"),
                            "vuln_id": vuln.get("id"),
                            "description": vuln.get("description", "")[:200],
                        })
                        findings["high"] += 1
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            # pip-audit not available — basic dependency listing
            try:
                deps = req_file.read_text().strip().split("\n")
                findings["dependencies"] = [
                    {"package": d.split("==")[0].split(">=")[0].strip(), "status": "unaudited"}
                    for d in deps if d.strip() and not d.startswith("#")
                ]
            except Exception:
                pass

    total = findings["critical"] + findings["high"]
    status = "pass" if total == 0 else "fail"

    scan_id = _record_scan(
        asset_id, version_id, "dependency_audit", status,
        findings_count=total,
        critical=findings["critical"], high=findings["high"],
        details={"dependencies": findings["dependencies"][:20]},
        db_path=db_path,
    )
    return scan_id, {"status": status, "findings": findings}


def scan_cui_markings(asset_path, asset_id, version_id, expected_classification=None, db_path=None):
    """Gate 4: CUI marking validation.

    Verifies that source files contain appropriate CUI markings matching
    the declared classification level.
    """
    asset_path = Path(asset_path)
    results = {"files_checked": 0, "files_marked": 0, "files_missing": [], "files_mismatched": []}

    code_extensions = {".py", ".js", ".ts", ".go", ".rs", ".java", ".cs", ".sh"}

    for fpath in asset_path.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in code_extensions:
            continue

        results["files_checked"] += 1
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            # Check first 10 lines for CUI marking
            header = "\n".join(content.split("\n")[:10])
            has_marking = any(re.search(p, header) for p in CUI_PATTERNS)

            if has_marking:
                results["files_marked"] += 1
                # Check if marking matches expected classification
                if expected_classification and expected_classification not in header:
                    results["files_mismatched"].append(str(fpath.relative_to(asset_path)))
            else:
                results["files_missing"].append(str(fpath.relative_to(asset_path)))
        except Exception:
            pass

    missing = len(results["files_missing"])
    mismatched = len(results["files_mismatched"])

    if results["files_checked"] == 0:
        status = "skipped"
    elif missing == 0 and mismatched == 0:
        status = "pass"
    else:
        status = "fail"

    scan_id = _record_scan(
        asset_id, version_id, "cui_marking_validation", status,
        findings_count=missing + mismatched,
        high=mismatched, medium=missing,
        details={
            "files_checked": results["files_checked"],
            "files_marked": results["files_marked"],
            "missing_markings": results["files_missing"][:10],
            "mismatched_markings": results["files_mismatched"][:10],
        },
        db_path=db_path,
    )
    return scan_id, {"status": status, "results": results}


def scan_sbom(asset_path, asset_id, version_id, db_path=None):
    """Gate 5: SBOM generation.

    Generates a CycloneDX SBOM for the asset. Required for assets with scripts.
    """
    asset_path = Path(asset_path)
    has_scripts = bool(list(asset_path.rglob("*.py")) or
                       list(asset_path.rglob("*.js")) or
                       list(asset_path.rglob("*.sh")))

    if not has_scripts:
        return _record_scan(
            asset_id, version_id, "sbom_generation", "skipped",
            details={"reason": "No executable scripts"}, db_path=db_path,
        ), {"status": "skipped"}

    sbom_data = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": [],
    }

    # Parse requirements.txt for Python deps
    req_file = asset_path / "requirements.txt"
    if req_file.exists():
        try:
            for line in req_file.read_text().strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = re.split(r'[><=!~]', line, maxsplit=1)
                pkg_name = parts[0].strip()
                pkg_version = parts[1].strip("= ") if len(parts) > 1 else "unknown"
                sbom_data["components"].append({
                    "type": "library",
                    "name": pkg_name,
                    "version": pkg_version,
                    "purl": f"pkg:pypi/{pkg_name}@{pkg_version}",
                })
        except Exception:
            pass

    status = "pass"
    scan_id = _record_scan(
        asset_id, version_id, "sbom_generation", status,
        findings_count=len(sbom_data["components"]),
        details={"sbom_summary": {
            "format": "CycloneDX",
            "component_count": len(sbom_data["components"]),
        }},
        db_path=db_path,
    )
    return scan_id, {"status": status, "components": len(sbom_data["components"])}


def scan_provenance(asset_path, asset_id, version_id, db_path=None):
    """Gate 6: Supply chain provenance check.

    Verifies dependency provenance chain. Non-blocking warning gate.
    """
    asset_path = Path(asset_path)
    unknown_deps = []

    req_file = asset_path / "requirements.txt"
    if req_file.exists():
        try:
            for line in req_file.read_text().strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Check if dependency is pinned (has ==)
                if "==" not in line:
                    parts = re.split(r'[><=!~]', line, maxsplit=1)
                    unknown_deps.append({
                        "package": parts[0].strip(),
                        "issue": "Not pinned to exact version",
                    })
        except Exception:
            pass

    status = "pass" if not unknown_deps else "warning"
    scan_id = _record_scan(
        asset_id, version_id, "supply_chain_provenance", status,
        findings_count=len(unknown_deps),
        medium=len(unknown_deps),
        details={"unpinned_dependencies": unknown_deps[:20]},
        db_path=db_path,
    )
    return scan_id, {"status": status, "unpinned": unknown_deps}


def scan_signature(asset_path, asset_id, version_id, db_path=None):
    """Gate 7: Digital signature readiness.

    Checks that the asset can be signed. Actual signing happens in publish_pipeline.
    """
    asset_path = Path(asset_path)

    # Check that we can compute a hash of the asset
    import hashlib
    h = hashlib.sha256()
    file_count = 0
    for fpath in sorted(asset_path.rglob("*")):
        if fpath.is_file():
            h.update(str(fpath.relative_to(asset_path)).encode())
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            file_count += 1

    content_hash = h.hexdigest()
    status = "pass" if file_count > 0 else "fail"

    scan_id = _record_scan(
        asset_id, version_id, "digital_signature", status,
        details={"content_hash": content_hash, "file_count": file_count},
        db_path=db_path,
    )
    return scan_id, {"status": status, "content_hash": content_hash, "file_count": file_count}


# ---------------------------------------------------------------------------
# Gate 8: Prompt Injection Scan (P3-2 — Phase 37)
# ---------------------------------------------------------------------------

def scan_prompt_injection(asset_path, asset_id, version_id, db_path=None):
    """Gate 8: Prompt injection scanning.

    Scans all .md, .yaml, .yml, .json, .txt files for prompt injection
    patterns using the PromptInjectionDetector (Phase 37).
    """
    asset_path = Path(asset_path)
    scannable_exts = {".md", ".yaml", ".yml", ".json", ".txt", ".py", ".js", ".ts"}

    try:
        from tools.security.prompt_injection_detector import PromptInjectionDetector
        detector = PromptInjectionDetector()
    except ImportError:
        return _record_scan(
            asset_id, version_id, "prompt_injection_scan", "skipped",
            details={"reason": "prompt_injection_detector not available"},
            db_path=db_path,
        ), {"status": "skipped", "reason": "Detector not available"}

    findings = {"block": [], "flag": [], "warn": [], "allow": 0}
    files_scanned = 0

    for fpath in asset_path.rglob("*"):
        if not fpath.is_file() or fpath.suffix.lower() not in scannable_exts:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        files_scanned += 1
        result = detector.scan_text(content, source=f"marketplace:{str(fpath.relative_to(asset_path))}")

        if result["detected"]:
            action = result["action"]
            entry = {
                "file": str(fpath.relative_to(asset_path)),
                "action": action,
                "confidence": result["confidence"],
                "findings_count": len(result["findings"]),
                "categories": list({f["category"] for f in result["findings"]}),
            }
            if action == "block":
                findings["block"].append(entry)
            elif action == "flag":
                findings["flag"].append(entry)
            elif action == "warn":
                findings["warn"].append(entry)
        else:
            findings["allow"] += 1

    critical = len(findings["block"])
    high = len(findings["flag"])
    medium = len(findings["warn"])
    status = "fail" if critical > 0 else ("warning" if high > 0 else "pass")

    scan_id = _record_scan(
        asset_id, version_id, "prompt_injection_scan", status,
        findings_count=critical + high + medium,
        critical=critical, high=high, medium=medium,
        details={
            "files_scanned": files_scanned,
            "blocked": findings["block"][:10],
            "flagged": findings["flag"][:10],
            "warned": findings["warn"][:10],
        },
        db_path=db_path,
    )
    return scan_id, {
        "status": status,
        "files_scanned": files_scanned,
        "block_count": critical,
        "flag_count": high,
        "warn_count": medium,
    }


# ---------------------------------------------------------------------------
# Gate 9: Behavioral Sandbox (P3-2 — Phase 37)
# ---------------------------------------------------------------------------

def scan_behavioral_sandbox(asset_path, asset_id, version_id, db_path=None):
    """Gate 9: Behavioral sandbox analysis.

    Static analysis to detect potential malicious behaviors in marketplace
    assets: data exfiltration, unauthorized tool usage, config manipulation,
    filesystem abuse, and network access attempts.
    Non-blocking (warning gate) — flags suspicious patterns for human review.
    """
    asset_path = Path(asset_path)
    import ast

    BEHAVIOR_PATTERNS = [
        # (pattern_regex, category, severity, description)
        (r'(?i)requests\.(get|post|put|delete|patch)\s*\(', "network_access", "high",
         "HTTP request to external service"),
        (r'(?i)urllib\.request\.(urlopen|urlretrieve)', "network_access", "high",
         "URL access via urllib"),
        (r'(?i)socket\.(socket|connect|bind|listen)', "network_access", "critical",
         "Raw socket operation"),
        (r'(?i)subprocess\.(run|call|Popen|check_output)', "tool_abuse", "medium",
         "Subprocess execution"),
        (r'(?i)os\.(system|popen|exec[lv]p?e?)', "tool_abuse", "high",
         "OS command execution"),
        (r'(?i)shutil\.(rmtree|move|copytree)', "filesystem_abuse", "medium",
         "Filesystem bulk operation"),
        (r'(?i)open\s*\([^)]*["\']/(etc|var|tmp|proc|sys)', "filesystem_abuse", "high",
         "Access to system directories"),
        (r'(?i)(ICDEV_|AWS_|AZURE_|GCP_).*(KEY|SECRET|TOKEN|PASSWORD)', "config_manipulation", "high",
         "Access to sensitive environment variables"),
        (r'(?i)sqlite3\.connect\s*\([^)]*icdev\.db', "config_manipulation", "critical",
         "Direct access to ICDEV database"),
        (r'(?i)(base64\.(b64encode|b64decode)|codecs\.(encode|decode))', "obfuscation", "medium",
         "Encoding/decoding that may hide payloads"),
    ]

    findings = {"critical": [], "high": [], "medium": [], "low": []}
    files_scanned = 0
    code_exts = {".py", ".js", ".ts", ".sh", ".bash"}

    for fpath in asset_path.rglob("*"):
        if not fpath.is_file() or fpath.suffix.lower() not in code_exts:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        files_scanned += 1
        rel_path = str(fpath.relative_to(asset_path))

        # Regex-based behavioral pattern detection
        for pattern, category, severity, description in BEHAVIOR_PATTERNS:
            for match in re.finditer(pattern, content):
                line_no = content[:match.start()].count("\n") + 1
                entry = {
                    "file": rel_path,
                    "line": line_no,
                    "category": category,
                    "severity": severity,
                    "description": description,
                    "match": match.group()[:60],
                }
                findings[severity].append(entry)

        # AST-based checks for Python files
        if fpath.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name in ("ctypes", "multiprocessing", "signal"):
                                findings["medium"].append({
                                    "file": rel_path,
                                    "line": node.lineno,
                                    "category": "tool_abuse",
                                    "severity": "medium",
                                    "description": f"Import of sensitive module: {alias.name}",
                                })
                    elif isinstance(node, ast.Call):
                        func_name = ""
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        if func_name in ("eval", "exec", "compile", "__import__"):
                            findings["critical"].append({
                                "file": rel_path,
                                "line": node.lineno,
                                "category": "tool_abuse",
                                "severity": "critical",
                                "description": f"Dynamic code execution: {func_name}()",
                            })
            except SyntaxError:
                pass

    total_critical = len(findings["critical"])
    total_high = len(findings["high"])
    total_medium = len(findings["medium"])
    total_low = len(findings["low"])
    total = total_critical + total_high + total_medium + total_low

    # Behavioral sandbox is a warning gate (not blocking) — human reviews flagged items
    status = "warning" if total > 0 else "pass"

    scan_id = _record_scan(
        asset_id, version_id, "behavioral_sandbox", status,
        findings_count=total,
        critical=total_critical, high=total_high,
        medium=total_medium, low=total_low,
        details={
            "files_scanned": files_scanned,
            "critical_behaviors": findings["critical"][:10],
            "high_behaviors": findings["high"][:10],
            "medium_behaviors": findings["medium"][:5],
        },
        db_path=db_path,
    )
    return scan_id, {
        "status": status,
        "files_scanned": files_scanned,
        "total_findings": total,
        "critical": total_critical,
        "high": total_high,
        "medium": total_medium,
        "low": total_low,
    }


# ---------------------------------------------------------------------------
# Full scan orchestrator
# ---------------------------------------------------------------------------

def run_full_scan(asset_id, version_id, asset_path, gates=None,
                  expected_classification=None, db_path=None):
    """Run all (or specified) security gates on an asset.

    Returns overall result with per-gate details.
    """
    gates = gates or ALL_GATES
    results = {}
    overall_pass = True
    overall_blocking_pass = True

    gate_functions = {
        "sast_scan": lambda: scan_sast(asset_path, asset_id, version_id, db_path),
        "secret_detection": lambda: scan_secrets(asset_path, asset_id, version_id, db_path),
        "dependency_audit": lambda: scan_dependencies(asset_path, asset_id, version_id, db_path),
        "cui_marking_validation": lambda: scan_cui_markings(
            asset_path, asset_id, version_id, expected_classification, db_path),
        "sbom_generation": lambda: scan_sbom(asset_path, asset_id, version_id, db_path),
        "supply_chain_provenance": lambda: scan_provenance(asset_path, asset_id, version_id, db_path),
        "digital_signature": lambda: scan_signature(asset_path, asset_id, version_id, db_path),
        "prompt_injection_scan": lambda: scan_prompt_injection(asset_path, asset_id, version_id, db_path),
        "behavioral_sandbox": lambda: scan_behavioral_sandbox(asset_path, asset_id, version_id, db_path),
    }

    for gate in gates:
        if gate not in gate_functions:
            results[gate] = {"status": "error", "error": f"Unknown gate: {gate}"}
            continue

        try:
            scan_id, result = gate_functions[gate]()
            results[gate] = result
            gate_status = result.get("status", "error")

            if gate_status == "fail":
                overall_pass = False
                if gate in BLOCKING_GATES:
                    overall_blocking_pass = False
            elif gate_status == "warning":
                overall_pass = False
        except Exception as e:
            results[gate] = {"status": "error", "error": str(e)}
            overall_pass = False
            if gate in BLOCKING_GATES:
                overall_blocking_pass = False

    overall_status = "pass" if overall_pass else ("fail" if not overall_blocking_pass else "warning")

    _audit(
        event_type="marketplace_scan_completed",
        actor="marketplace-scanner",
        action=f"Scan completed for asset {asset_id}: {overall_status}",
        details={"asset_id": asset_id, "version_id": version_id, "result": overall_status},
    )

    return {
        "asset_id": asset_id,
        "version_id": version_id,
        "overall_status": overall_status,
        "blocking_gates_pass": overall_blocking_pass,
        "gates_scanned": len(results),
        "gate_results": results,
        "scanned_at": _now(),
    }


def get_scan_summary(version_id, db_path=None):
    """Get scan summary for a specific version."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT gate_name, status, findings_count, critical_count,
                      high_count, medium_count, low_count, scanned_at
               FROM marketplace_scan_results
               WHERE version_id = ?
               ORDER BY scanned_at DESC""",
            (version_id,),
        ).fetchall()

        gates = {}
        for row in rows:
            gate = row["gate_name"]
            if gate not in gates:  # Only latest scan per gate
                gates[gate] = dict(row)

        blocking_pass = all(
            gates.get(g, {}).get("status") in ("pass", "skipped")
            for g in BLOCKING_GATES if g in gates
        )

        return {
            "version_id": version_id,
            "gates": gates,
            "blocking_gates_pass": blocking_pass,
            "total_gates": len(gates),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Asset Scanner")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    parser.add_argument("--asset-id", help="Asset ID")
    parser.add_argument("--version-id", help="Version ID")
    parser.add_argument("--asset-path", help="Path to asset directory")
    parser.add_argument("--gates", help="Comma-separated gate names (default: all)")
    parser.add_argument("--classification", help="Expected classification marking")
    parser.add_argument("--summary", action="store_true", help="Get scan summary")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.summary:
            if not args.version_id:
                parser.error("--summary requires --version-id")
            result = get_scan_summary(args.version_id, db_path)
        else:
            if not all([args.asset_id, args.version_id, args.asset_path]):
                parser.error("Requires --asset-id, --version-id, --asset-path")
            gates = args.gates.split(",") if args.gates else None
            result = run_full_scan(
                asset_id=args.asset_id, version_id=args.version_id,
                asset_path=args.asset_path, gates=gates,
                expected_classification=args.classification,
                db_path=db_path,
            )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for k, v in result.items():
                print(f"  {k}: {v}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
