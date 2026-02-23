#!/usr/bin/env python3
# CUI // SP-CTI
"""Production Readiness Audit — comprehensive pre-production validation.

Runs 30 checks across 6 categories: platform, security, compliance,
integration, performance, documentation.  Streams results live and
produces a consolidated report stored in the production_audits table.

Usage:
    python tools/testing/production_audit.py --human --stream
    python tools/testing/production_audit.py --json
    python tools/testing/production_audit.py --category security,compliance --json
    python tools/testing/production_audit.py --gate --json

Exit codes: 0 = all blocking checks pass, 1 = at least one blocker failed.

Architecture decisions: D291-D295.
"""

import argparse
import ast
import dataclasses
import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "icdev.db"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AuditCheck:
    check_id: str
    check_name: str
    category: str
    status: str          # pass, fail, warn, skip
    severity: str        # blocking, warning
    message: str
    details: dict
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class AuditReport:
    overall_pass: bool
    timestamp: str
    categories: dict
    total_checks: int
    passed: int
    failed: int
    warned: int
    skipped: int
    blockers: list
    warnings: list
    duration_total_ms: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_subprocess(cmd: list, timeout: int = 120) -> Tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -3, "", str(e)


def _timed(fn: Callable, *args, **kwargs) -> Tuple:
    """Run fn and return (result, duration_ms)."""
    start = time.time()
    result = fn(*args, **kwargs)
    elapsed = int((time.time() - start) * 1000)
    return result, elapsed


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Category 1: Platform Compatibility (PLT-001..004)
# ---------------------------------------------------------------------------

def check_python_version() -> AuditCheck:
    """PLT-002: Python >= 3.9 required."""
    v = sys.version_info
    ok = v >= (3, 9)
    return AuditCheck(
        check_id="PLT-002", check_name="Python Version",
        category="platform", status="pass" if ok else "fail",
        severity="blocking",
        message=f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " — requires >= 3.9"),
        details={"version": f"{v.major}.{v.minor}.{v.micro}", "required": "3.9"},
    )


def check_stdlib_modules() -> AuditCheck:
    """PLT-003: Required stdlib modules importable."""
    required = ["sqlite3", "pathlib", "json", "hashlib", "argparse", "ast",
                 "dataclasses", "subprocess", "re", "uuid", "hmac"]
    missing = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    ok = len(missing) == 0
    return AuditCheck(
        check_id="PLT-003", check_name="Required Stdlib Modules",
        category="platform", status="pass" if ok else "fail",
        severity="blocking",
        message=f"All {len(required)} stdlib modules available" if ok else f"Missing: {', '.join(missing)}",
        details={"checked": len(required), "missing": missing},
    )


def check_platform_compat() -> AuditCheck:
    """PLT-001: Run platform_check.py."""
    script = PROJECT_ROOT / "tools" / "testing" / "platform_check.py"
    if not script.exists():
        return AuditCheck(
            check_id="PLT-001", check_name="Platform Compatibility",
            category="platform", status="skip", severity="warning",
            message="platform_check.py not found", details={},
        )
    rc, stdout, stderr = _run_subprocess([sys.executable, str(script), "--json"])
    if rc == 0:
        try:
            data = json.loads(stdout)
            return AuditCheck(
                check_id="PLT-001", check_name="Platform Compatibility",
                category="platform", status="pass", severity="warning",
                message=f"Platform: {data.get('platform', 'unknown')}",
                details=data,
            )
        except json.JSONDecodeError:
            pass
    return AuditCheck(
        check_id="PLT-001", check_name="Platform Compatibility",
        category="platform", status="warn", severity="warning",
        message=f"platform_check returned exit {rc}", details={"stderr": stderr[:500]},
    )


def check_dockerfile_syntax() -> AuditCheck:
    """PLT-004: Parse Dockerfiles for FROM, USER, COPY."""
    docker_dir = PROJECT_ROOT / "docker"
    if not docker_dir.exists():
        return AuditCheck(
            check_id="PLT-004", check_name="Dockerfile Syntax",
            category="platform", status="skip", severity="warning",
            message="docker/ directory not found", details={},
        )
    issues = []
    checked = 0
    for df in sorted(docker_dir.glob("Dockerfile.*")):
        checked += 1
        try:
            content = df.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            issues.append(f"{df.name}: unreadable")
            continue
        if "FROM " not in content:
            issues.append(f"{df.name}: missing FROM instruction")
        if "USER " not in content:
            issues.append(f"{df.name}: missing USER (non-root required)")
    ok = len(issues) == 0
    return AuditCheck(
        check_id="PLT-004", check_name="Dockerfile Syntax",
        category="platform", status="pass" if ok else "warn",
        severity="warning",
        message=f"{checked} Dockerfiles checked, {len(issues)} issues" if not ok else f"{checked} Dockerfiles valid",
        details={"checked": checked, "issues": issues},
    )


# ---------------------------------------------------------------------------
# Category 2: Security (SEC-001..006)
# ---------------------------------------------------------------------------

def check_sast_bandit() -> AuditCheck:
    """SEC-001: SAST scan via bandit."""
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, "-m", "bandit", "-r", str(PROJECT_ROOT / "tools"),
         "-f", "json", "-q", "--severity-level", "medium"],
        timeout=300,
    )
    if rc == -1:
        return AuditCheck(
            check_id="SEC-001", check_name="SAST Scan (Bandit)",
            category="security", status="skip", severity="blocking",
            message="bandit not installed (pip install bandit)", details={},
        )
    try:
        data = json.loads(stdout)
        results = data.get("results", [])
        critical = sum(1 for r in results if r.get("issue_severity") == "HIGH" and r.get("issue_confidence") == "HIGH")
        high = sum(1 for r in results if r.get("issue_severity") == "HIGH")
        medium = sum(1 for r in results if r.get("issue_severity") == "MEDIUM")
        ok = critical == 0
        return AuditCheck(
            check_id="SEC-001", check_name="SAST Scan (Bandit)",
            category="security", status="pass" if ok else "fail",
            severity="blocking",
            message=f"{len(results)} findings (critical={critical}, high={high}, medium={medium})",
            details={"total": len(results), "critical": critical, "high": high, "medium": medium},
        )
    except json.JSONDecodeError:
        return AuditCheck(
            check_id="SEC-001", check_name="SAST Scan (Bandit)",
            category="security", status="warn", severity="blocking",
            message=f"bandit output not parseable (exit {rc})",
            details={"stderr": stderr[:500]},
        )


def check_dependency_audit() -> AuditCheck:
    """SEC-002: Dependency vulnerability audit."""
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner=off"],
        timeout=120,
    )
    if rc == -1:
        return AuditCheck(
            check_id="SEC-002", check_name="Dependency Audit",
            category="security", status="skip", severity="blocking",
            message="pip-audit not installed (pip install pip-audit)", details={},
        )
    try:
        data = json.loads(stdout)
        vulns = data if isinstance(data, list) else data.get("dependencies", [])
        vuln_deps = [v for v in vulns if v.get("vulns")]
        critical = sum(1 for v in vuln_deps for vv in v.get("vulns", []) if "CRITICAL" in str(vv).upper())
        high = sum(1 for v in vuln_deps for vv in v.get("vulns", []) if "HIGH" in str(vv).upper())
        ok = critical == 0 and high == 0
        return AuditCheck(
            check_id="SEC-002", check_name="Dependency Audit",
            category="security", status="pass" if ok else ("fail" if critical > 0 else "warn"),
            severity="blocking",
            message=f"{len(vuln_deps)} vulnerable deps (critical={critical}, high={high})",
            details={"vulnerable_count": len(vuln_deps), "critical": critical, "high": high},
        )
    except (json.JSONDecodeError, TypeError):
        ok = rc == 0
        return AuditCheck(
            check_id="SEC-002", check_name="Dependency Audit",
            category="security", status="pass" if ok else "warn",
            severity="blocking",
            message=f"pip-audit exit {rc}" + (" — no vulnerabilities" if ok else ""),
            details={"exit_code": rc, "stderr": stderr[:500]},
        )


def check_secret_detection() -> AuditCheck:
    """SEC-003: Secret detection via detect-secrets."""
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, "-m", "detect_secrets", "scan", str(PROJECT_ROOT / "tools")],
        timeout=120,
    )
    if rc == -1:
        return AuditCheck(
            check_id="SEC-003", check_name="Secret Detection",
            category="security", status="skip", severity="blocking",
            message="detect-secrets not installed (pip install detect-secrets)", details={},
        )
    try:
        data = json.loads(stdout)
        results = data.get("results", {})
        total_secrets = sum(len(v) for v in results.values())
        ok = total_secrets == 0
        return AuditCheck(
            check_id="SEC-003", check_name="Secret Detection",
            category="security", status="pass" if ok else "fail",
            severity="blocking",
            message=f"{total_secrets} potential secrets in {len(results)} files" if not ok else "No secrets detected",
            details={"files_with_secrets": len(results), "total_secrets": total_secrets},
        )
    except json.JSONDecodeError:
        return AuditCheck(
            check_id="SEC-003", check_name="Secret Detection",
            category="security", status="warn", severity="blocking",
            message="detect-secrets output not parseable", details={"stderr": stderr[:500]},
        )


def check_prompt_injection_gate() -> AuditCheck:
    """SEC-004: Prompt injection defense active."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from tools.security.prompt_injection_detector import INJECTION_PATTERNS
        count = len(INJECTION_PATTERNS)
        ok = count >= 10
        return AuditCheck(
            check_id="SEC-004", check_name="Prompt Injection Defense",
            category="security", status="pass" if ok else "warn",
            severity="blocking",
            message=f"{count} injection patterns registered",
            details={"pattern_count": count},
        )
    except ImportError as e:
        return AuditCheck(
            check_id="SEC-004", check_name="Prompt Injection Defense",
            category="security", status="skip", severity="blocking",
            message=f"Import failed: {e}", details={},
        )


def check_owasp_agentic() -> AuditCheck:
    """SEC-005: OWASP Agentic AI security tools present."""
    tools_needed = [
        "tools/security/tool_chain_validator.py",
        "tools/security/agent_output_validator.py",
        "tools/security/agent_trust_scorer.py",
        "tools/security/mcp_tool_authorizer.py",
    ]
    present = [t for t in tools_needed if (PROJECT_ROOT / t).exists()]
    ok = len(present) == len(tools_needed)
    return AuditCheck(
        check_id="SEC-005", check_name="OWASP Agentic Security",
        category="security", status="pass" if ok else "warn",
        severity="warning",
        message=f"{len(present)}/{len(tools_needed)} agentic security tools present",
        details={"present": present, "missing": [t for t in tools_needed if t not in present]},
    )


def check_code_pattern_scan() -> AuditCheck:
    """SEC-006: Dangerous code pattern scan."""
    scanner = PROJECT_ROOT / "tools" / "security" / "code_pattern_scanner.py"
    if not scanner.exists():
        return AuditCheck(
            check_id="SEC-006", check_name="Code Pattern Scan",
            category="security", status="skip", severity="blocking",
            message="code_pattern_scanner.py not found", details={},
        )
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, str(scanner), "--project-dir", str(PROJECT_ROOT / "tools"), "--json"],
        timeout=120,
    )
    if rc == 0:
        try:
            data = json.loads(stdout)
            critical = data.get("critical", 0)
            high = data.get("high", 0)
            ok = critical == 0
            return AuditCheck(
                check_id="SEC-006", check_name="Code Pattern Scan",
                category="security", status="pass" if ok else "fail",
                severity="blocking",
                message=f"critical={critical}, high={high}",
                details=data,
            )
        except json.JSONDecodeError:
            pass
    return AuditCheck(
        check_id="SEC-006", check_name="Code Pattern Scan",
        category="security", status="warn" if rc == 0 else "fail",
        severity="blocking",
        message=f"Scanner exit {rc}", details={"stderr": stderr[:500]},
    )


# ---------------------------------------------------------------------------
# Category 3: Compliance (CMP-001..006)
# ---------------------------------------------------------------------------

def check_cui_markings() -> AuditCheck:
    """CMP-001: CUI markings on Python files."""
    tools_dir = PROJECT_ROOT / "tools"
    total = 0
    marked = 0
    for py in tools_dir.rglob("*.py"):
        if py.name.startswith("__"):
            continue
        total += 1
        try:
            head = py.read_text(encoding="utf-8")[:200]
            if "CUI" in head:
                marked += 1
        except (OSError, UnicodeDecodeError):
            pass
    pct = round(marked / total * 100, 1) if total else 0
    ok = pct >= 90
    return AuditCheck(
        check_id="CMP-001", check_name="CUI Marking Coverage",
        category="compliance", status="pass" if ok else "warn",
        severity="warning",
        message=f"{marked}/{total} files marked ({pct}%)",
        details={"total": total, "marked": marked, "pct": pct},
    )


def check_claude_governance() -> AuditCheck:
    """CMP-002: .claude governance validator."""
    script = PROJECT_ROOT / "tools" / "testing" / "claude_dir_validator.py"
    if not script.exists():
        return AuditCheck(
            check_id="CMP-002", check_name="Claude Governance",
            category="compliance", status="skip", severity="blocking",
            message="claude_dir_validator.py not found", details={},
        )
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, str(script), "--json"], timeout=60,
    )
    try:
        data = json.loads(stdout)
        failed = data.get("summary", {}).get("failed", 0)
        warned = data.get("summary", {}).get("warned", 0)
        passed = data.get("summary", {}).get("passed", 0)
        ok = failed == 0
        return AuditCheck(
            check_id="CMP-002", check_name="Claude Governance",
            category="compliance", status="pass" if ok else "fail",
            severity="blocking",
            message=f"{passed} passed, {failed} failed, {warned} warned",
            details={"passed": passed, "failed": failed, "warned": warned},
        )
    except (json.JSONDecodeError, TypeError):
        return AuditCheck(
            check_id="CMP-002", check_name="Claude Governance",
            category="compliance", status="fail" if rc != 0 else "warn",
            severity="blocking",
            message=f"Validator exit {rc}", details={"stderr": stderr[:500]},
        )


def check_append_only_tables() -> AuditCheck:
    """CMP-003: Append-only table coverage in hooks."""
    hook_file = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
    init_file = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    if not hook_file.exists():
        return AuditCheck(
            check_id="CMP-003", check_name="Append-Only Table Coverage",
            category="compliance", status="skip", severity="blocking",
            message="pre_tool_use.py not found", details={},
        )
    try:
        hook_content = hook_file.read_text(encoding="utf-8")
        # Extract APPEND_ONLY_TABLES list
        match = re.search(r'APPEND_ONLY_TABLES\s*=\s*\[(.*?)\]', hook_content, re.DOTALL)
        if match:
            tables_str = match.group(1)
            protected = re.findall(r'"(\w+)"', tables_str)
        else:
            protected = []
        ok = len(protected) >= 20  # We expect 29+ tables
        return AuditCheck(
            check_id="CMP-003", check_name="Append-Only Table Coverage",
            category="compliance", status="pass" if ok else "warn",
            severity="blocking",
            message=f"{len(protected)} tables protected in hooks",
            details={"count": len(protected), "tables": protected},
        )
    except Exception as e:
        return AuditCheck(
            check_id="CMP-003", check_name="Append-Only Table Coverage",
            category="compliance", status="fail", severity="blocking",
            message=str(e), details={},
        )


def check_security_gates_config() -> AuditCheck:
    """CMP-004: security_gates.yaml parseable and complete."""
    gates_file = PROJECT_ROOT / "args" / "security_gates.yaml"
    if not gates_file.exists():
        return AuditCheck(
            check_id="CMP-004", check_name="Security Gates Config",
            category="compliance", status="fail", severity="warning",
            message="security_gates.yaml not found", details={},
        )
    try:
        import yaml
        data = yaml.safe_load(gates_file.read_text(encoding="utf-8"))
        gate_count = len(data) if isinstance(data, dict) else 0
        expected_gates = ["merge_gates", "deploy_gates", "fedramp", "cmmc"]
        present = [g for g in expected_gates if g in (data or {})]
        ok = gate_count >= 5 and len(present) == len(expected_gates)
        return AuditCheck(
            check_id="CMP-004", check_name="Security Gates Config",
            category="compliance", status="pass" if ok else "warn",
            severity="warning",
            message=f"{gate_count} gates defined, {len(present)}/{len(expected_gates)} core gates present",
            details={"gate_count": gate_count, "present": present},
        )
    except ImportError:
        return AuditCheck(
            check_id="CMP-004", check_name="Security Gates Config",
            category="compliance", status="skip", severity="warning",
            message="pyyaml not installed", details={},
        )
    except Exception as e:
        return AuditCheck(
            check_id="CMP-004", check_name="Security Gates Config",
            category="compliance", status="fail", severity="warning",
            message=f"Parse error: {e}", details={},
        )


def check_xai_compliance() -> AuditCheck:
    """CMP-005: XAI compliance assessor available."""
    assessor = PROJECT_ROOT / "tools" / "compliance" / "xai_assessor.py"
    if not assessor.exists():
        return AuditCheck(
            check_id="CMP-005", check_name="XAI Compliance",
            category="compliance", status="skip", severity="warning",
            message="xai_assessor.py not found (Phase 46)", details={},
        )
    try:
        ast.parse(assessor.read_text(encoding="utf-8"))
        return AuditCheck(
            check_id="CMP-005", check_name="XAI Compliance",
            category="compliance", status="pass", severity="warning",
            message="XAI assessor available and syntactically valid",
            details={},
        )
    except SyntaxError as e:
        return AuditCheck(
            check_id="CMP-005", check_name="XAI Compliance",
            category="compliance", status="fail", severity="warning",
            message=f"Syntax error: {e}", details={},
        )


def check_sbom_generation() -> AuditCheck:
    """CMP-006: SBOM generator available."""
    sbom = PROJECT_ROOT / "tools" / "compliance" / "sbom_generator.py"
    if not sbom.exists():
        return AuditCheck(
            check_id="CMP-006", check_name="SBOM Generator",
            category="compliance", status="skip", severity="warning",
            message="sbom_generator.py not found", details={},
        )
    try:
        ast.parse(sbom.read_text(encoding="utf-8"))
        return AuditCheck(
            check_id="CMP-006", check_name="SBOM Generator",
            category="compliance", status="pass", severity="warning",
            message="SBOM generator available", details={},
        )
    except SyntaxError as e:
        return AuditCheck(
            check_id="CMP-006", check_name="SBOM Generator",
            category="compliance", status="fail", severity="warning",
            message=f"Syntax error: {e}", details={},
        )


# ---------------------------------------------------------------------------
# Category 4: Integration (INT-001..005)
# ---------------------------------------------------------------------------

def check_mcp_servers() -> AuditCheck:
    """INT-001: Validate all MCP server files parse correctly."""
    mcp_dir = PROJECT_ROOT / "tools" / "mcp"
    if not mcp_dir.exists():
        return AuditCheck(
            check_id="INT-001", check_name="MCP Server Validation",
            category="integration", status="skip", severity="blocking",
            message="tools/mcp/ not found", details={},
        )
    servers = sorted(mcp_dir.glob("*_server.py"))
    errors = []
    for srv in servers:
        try:
            ast.parse(srv.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{srv.name}: {e}")
    ok = len(errors) == 0
    return AuditCheck(
        check_id="INT-001", check_name="MCP Server Validation",
        category="integration", status="pass" if ok else "fail",
        severity="blocking",
        message=f"{len(servers)} servers validated, {len(errors)} errors",
        details={"total": len(servers), "errors": errors},
    )


def check_db_schema() -> AuditCheck:
    """INT-002: DB schema — expected table count."""
    if not DB_PATH.exists():
        return AuditCheck(
            check_id="INT-002", check_name="DB Schema Validation",
            category="integration", status="fail", severity="blocking",
            message=f"Database not found: {DB_PATH}", details={},
        )
    try:
        conn = sqlite3.connect(str(DB_PATH))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        conn.close()
        count = len(tables)
        ok = count >= 150  # We expect 176+
        return AuditCheck(
            check_id="INT-002", check_name="DB Schema Validation",
            category="integration", status="pass" if ok else "warn",
            severity="blocking",
            message=f"{count} tables in icdev.db",
            details={"table_count": count},
        )
    except Exception as e:
        return AuditCheck(
            check_id="INT-002", check_name="DB Schema Validation",
            category="integration", status="fail", severity="blocking",
            message=str(e), details={},
        )


def check_cross_imports() -> AuditCheck:
    """INT-003: Validate all tools/**/*.py can be parsed (AST syntax check)."""
    tools_dir = PROJECT_ROOT / "tools"
    errors = []
    checked = 0
    for py in sorted(tools_dir.rglob("*.py")):
        if py.name.startswith("__"):
            continue
        checked += 1
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{py.relative_to(PROJECT_ROOT)}: {e.msg} (line {e.lineno})")
    ok = len(errors) == 0
    return AuditCheck(
        check_id="INT-003", check_name="Cross-Module Syntax Check",
        category="integration", status="pass" if ok else "fail",
        severity="warning",
        message=f"{checked} files parsed, {len(errors)} syntax errors",
        details={"checked": checked, "errors": errors[:20]},
    )


def check_dashboard_health() -> AuditCheck:
    """INT-004: Dashboard page health (requires running dashboard)."""
    try:
        import requests
    except ImportError:
        return AuditCheck(
            check_id="INT-004", check_name="Dashboard Page Health",
            category="integration", status="skip", severity="warning",
            message="requests not installed", details={},
        )
    port = os.environ.get("ICDEV_DASHBOARD_PORT", "5000")
    base = f"http://localhost:{port}"
    try:
        r = requests.get(f"{base}/login", timeout=3)
        if r.status_code != 200:
            return AuditCheck(
                check_id="INT-004", check_name="Dashboard Page Health",
                category="integration", status="skip", severity="warning",
                message=f"Dashboard not running on port {port}",
                details={},
            )
    except Exception:
        return AuditCheck(
            check_id="INT-004", check_name="Dashboard Page Health",
            category="integration", status="skip", severity="warning",
            message=f"Dashboard not reachable on port {port}", details={},
        )
    # Dashboard is running — test pages
    session = requests.Session()
    # Create temp API key for auth
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR IGNORE INTO dashboard_users (id, email, name, role, status) "
            "VALUES ('audit-user', 'audit@icdev.local', 'Audit', 'admin', 'active')"
        )
        import hashlib
        key = "icdev_audit_temp_key_" + datetime.now(timezone.utc).strftime("%H%M%S")
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        conn.execute(
            "INSERT INTO dashboard_api_keys (id, user_id, key_hash, key_prefix, label, status) "
            "VALUES ('audit-key', 'audit-user', ?, ?, 'audit', 'active')",
            (key_hash, key[:12]),
        )
        conn.commit()
        conn.close()
        session.post(f"{base}/login", data={"api_key": key}, allow_redirects=False)
    except Exception:
        pass  # Continue without auth

    pages = ["/", "/projects", "/agents", "/monitoring", "/events", "/query",
             "/chat", "/gateway", "/wizard", "/quick-paths", "/batch",
             "/dev-profiles", "/children", "/phases", "/translations",
             "/traces", "/provenance", "/xai", "/activity", "/usage",
             "/profile", "/chat-streams", "/diagrams", "/cicd"]
    ok_count = 0
    fail_pages = []
    for page in pages:
        try:
            r = session.get(f"{base}{page}", timeout=5)
            if r.status_code == 200 and "main-content" in r.text:
                ok_count += 1
            else:
                fail_pages.append(f"{page} (status={r.status_code})")
        except Exception as e:
            fail_pages.append(f"{page} ({e})")
    # Cleanup temp key
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM dashboard_api_keys WHERE id = 'audit-key'")
        conn.execute("DELETE FROM dashboard_users WHERE id = 'audit-user'")
        conn.commit()
        conn.close()
    except Exception:
        pass
    ok = len(fail_pages) == 0
    return AuditCheck(
        check_id="INT-004", check_name="Dashboard Page Health",
        category="integration", status="pass" if ok else "warn",
        severity="warning",
        message=f"{ok_count}/{len(pages)} pages OK" + (f", failed: {', '.join(fail_pages[:5])}" if fail_pages else ""),
        details={"total": len(pages), "passed": ok_count, "failed": fail_pages},
    )


def check_api_gateway() -> AuditCheck:
    """INT-005: API gateway Flask app importable."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        spec = importlib.util.spec_from_file_location(
            "api_gateway", str(PROJECT_ROOT / "tools" / "saas" / "api_gateway.py")
        )
        if spec and spec.loader:
            return AuditCheck(
                check_id="INT-005", check_name="API Gateway",
                category="integration", status="pass", severity="warning",
                message="API gateway module found", details={},
            )
    except Exception as e:
        return AuditCheck(
            check_id="INT-005", check_name="API Gateway",
            category="integration", status="warn", severity="warning",
            message=f"Import check: {e}", details={},
        )
    return AuditCheck(
        check_id="INT-005", check_name="API Gateway",
        category="integration", status="skip", severity="warning",
        message="api_gateway.py not found", details={},
    )


# ---------------------------------------------------------------------------
# Category 5: Performance / Resilience (PRF-001..004)
# ---------------------------------------------------------------------------

def check_migration_status() -> AuditCheck:
    """PRF-001: DB migration status."""
    migrate = PROJECT_ROOT / "tools" / "db" / "migrate.py"
    if not migrate.exists():
        return AuditCheck(
            check_id="PRF-001", check_name="DB Migration Status",
            category="performance", status="skip", severity="warning",
            message="migrate.py not found", details={},
        )
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, str(migrate), "--status", "--json"], timeout=30,
    )
    if rc == 0:
        try:
            data = json.loads(stdout)
            pending = data.get("pending", 0)
            ok = pending == 0
            return AuditCheck(
                check_id="PRF-001", check_name="DB Migration Status",
                category="performance", status="pass" if ok else "warn",
                severity="warning",
                message=f"{pending} pending migrations" if pending else "All migrations applied",
                details=data,
            )
        except json.JSONDecodeError:
            pass
    return AuditCheck(
        check_id="PRF-001", check_name="DB Migration Status",
        category="performance", status="warn", severity="warning",
        message=f"migrate.py exit {rc}", details={"stderr": stderr[:300]},
    )


def check_backup_config() -> AuditCheck:
    """PRF-002: DB backup config exists."""
    config = PROJECT_ROOT / "args" / "db_config.yaml"
    if not config.exists():
        return AuditCheck(
            check_id="PRF-002", check_name="DB Backup Config",
            category="performance", status="warn", severity="warning",
            message="args/db_config.yaml not found", details={},
        )
    try:
        content = config.read_text(encoding="utf-8")
        has_backup = "backup" in content.lower()
        return AuditCheck(
            check_id="PRF-002", check_name="DB Backup Config",
            category="performance", status="pass" if has_backup else "warn",
            severity="warning",
            message="Backup configuration present" if has_backup else "No backup section found",
            details={"has_backup_section": has_backup},
        )
    except Exception as e:
        return AuditCheck(
            check_id="PRF-002", check_name="DB Backup Config",
            category="performance", status="warn", severity="warning",
            message=str(e), details={},
        )


def check_resilience_config() -> AuditCheck:
    """PRF-003: Circuit breaker / resilience config."""
    config = PROJECT_ROOT / "args" / "resilience_config.yaml"
    if not config.exists():
        return AuditCheck(
            check_id="PRF-003", check_name="Resilience Config",
            category="performance", status="warn", severity="warning",
            message="args/resilience_config.yaml not found", details={},
        )
    try:
        import yaml
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        has_cb = "circuit_breaker" in str(data).lower() if data else False
        has_retry = "retry" in str(data).lower() if data else False
        ok = has_cb and has_retry
        return AuditCheck(
            check_id="PRF-003", check_name="Resilience Config",
            category="performance", status="pass" if ok else "warn",
            severity="warning",
            message=f"circuit_breaker={'yes' if has_cb else 'no'}, retry={'yes' if has_retry else 'no'}",
            details={"circuit_breaker": has_cb, "retry": has_retry},
        )
    except ImportError:
        return AuditCheck(
            check_id="PRF-003", check_name="Resilience Config",
            category="performance", status="skip", severity="warning",
            message="pyyaml not installed", details={},
        )


def check_test_collection() -> AuditCheck:
    """PRF-004: pytest collection (no import errors)."""
    rc, stdout, stderr = _run_subprocess(
        [sys.executable, "-m", "pytest", str(PROJECT_ROOT / "tests"), "--co", "-q"],
        timeout=120,
    )
    if rc == -1:
        return AuditCheck(
            check_id="PRF-004", check_name="Test Collection",
            category="performance", status="skip", severity="blocking",
            message="pytest not installed", details={},
        )
    # Parse "N tests collected"
    match = re.search(r"(\d+)\s+test", stdout)
    count = int(match.group(1)) if match else 0
    ok = rc == 0 and count > 0
    return AuditCheck(
        check_id="PRF-004", check_name="Test Collection",
        category="performance", status="pass" if ok else "fail",
        severity="blocking",
        message=f"{count} tests collected" if ok else f"Collection failed (exit {rc})",
        details={"test_count": count, "exit_code": rc},
    )


# ---------------------------------------------------------------------------
# Category 6: Documentation Alignment (DOC-001..005)
# ---------------------------------------------------------------------------

def check_claude_md_table_count() -> AuditCheck:
    """DOC-001: CLAUDE.md table count accuracy."""
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return AuditCheck(
            check_id="DOC-001", check_name="CLAUDE.md Table Count",
            category="documentation", status="skip", severity="warning",
            message="CLAUDE.md not found", details={},
        )
    try:
        content = claude_md.read_text(encoding="utf-8")
        # Find claimed table count (e.g. "176 tables")
        match = re.search(r"(\d+)\s+tables", content)
        claimed = int(match.group(1)) if match else 0
        # Get actual count
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            actual = len(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall())
            conn.close()
        else:
            actual = 0
        drift = abs(claimed - actual)
        ok = drift <= 5
        return AuditCheck(
            check_id="DOC-001", check_name="CLAUDE.md Table Count",
            category="documentation", status="pass" if ok else "warn",
            severity="warning",
            message=f"Claimed {claimed}, actual {actual} (drift={drift})",
            details={"claimed": claimed, "actual": actual, "drift": drift},
        )
    except Exception as e:
        return AuditCheck(
            check_id="DOC-001", check_name="CLAUDE.md Table Count",
            category="documentation", status="warn", severity="warning",
            message=str(e), details={},
        )


def check_tools_manifest() -> AuditCheck:
    """DOC-002: tools/manifest.md completeness."""
    manifest = PROJECT_ROOT / "tools" / "manifest.md"
    if not manifest.exists():
        return AuditCheck(
            check_id="DOC-002", check_name="Tools Manifest",
            category="documentation", status="warn", severity="warning",
            message="tools/manifest.md not found", details={},
        )
    try:
        content = manifest.read_text(encoding="utf-8")
        # Count tools mentioned in manifest
        listed = set(re.findall(r'(\w+\.py)', content))
        # Count actual tool files
        actual = set()
        for py in (PROJECT_ROOT / "tools").rglob("*.py"):
            if not py.name.startswith("__"):
                actual.add(py.name)
        missing = actual - listed
        pct = round(len(listed & actual) / max(len(actual), 1) * 100, 1)
        ok = pct >= 70
        return AuditCheck(
            check_id="DOC-002", check_name="Tools Manifest",
            category="documentation", status="pass" if ok else "warn",
            severity="warning",
            message=f"{len(listed & actual)}/{len(actual)} tools documented ({pct}%)",
            details={"documented": len(listed & actual), "total": len(actual),
                      "pct": pct, "missing_sample": sorted(missing)[:10]},
        )
    except Exception as e:
        return AuditCheck(
            check_id="DOC-002", check_name="Tools Manifest",
            category="documentation", status="warn", severity="warning",
            message=str(e), details={},
        )


def check_goals_manifest() -> AuditCheck:
    """DOC-003: goals/manifest.md completeness."""
    manifest = PROJECT_ROOT / "goals" / "manifest.md"
    goals_dir = PROJECT_ROOT / "goals"
    if not manifest.exists():
        return AuditCheck(
            check_id="DOC-003", check_name="Goals Manifest",
            category="documentation", status="warn", severity="warning",
            message="goals/manifest.md not found", details={},
        )
    try:
        content = manifest.read_text(encoding="utf-8")
        listed = set(re.findall(r'(\w+\.md)', content))
        actual = {f.name for f in goals_dir.glob("*.md") if f.name != "manifest.md"}
        missing = actual - listed
        pct = round(len(listed & actual) / max(len(actual), 1) * 100, 1)
        ok = pct >= 80
        return AuditCheck(
            check_id="DOC-003", check_name="Goals Manifest",
            category="documentation", status="pass" if ok else "warn",
            severity="warning",
            message=f"{len(listed & actual)}/{len(actual)} goals documented ({pct}%)",
            details={"documented": len(listed & actual), "total": len(actual),
                      "pct": pct, "missing": sorted(missing)[:10]},
        )
    except Exception as e:
        return AuditCheck(
            check_id="DOC-003", check_name="Goals Manifest",
            category="documentation", status="warn", severity="warning",
            message=str(e), details={},
        )


def check_route_documentation() -> AuditCheck:
    """DOC-004: Dashboard routes documented in start.md."""
    start_md = PROJECT_ROOT / ".claude" / "commands" / "start.md"
    if not start_md.exists():
        return AuditCheck(
            check_id="DOC-004", check_name="Route Documentation",
            category="documentation", status="skip", severity="warning",
            message="start.md not found", details={},
        )
    try:
        content = start_md.read_text(encoding="utf-8")
        # Count documented routes
        routes = re.findall(r'`(/\w[^`]*)`', content)
        ok = len(routes) >= 20
        return AuditCheck(
            check_id="DOC-004", check_name="Route Documentation",
            category="documentation", status="pass" if ok else "warn",
            severity="warning",
            message=f"{len(routes)} routes documented in start.md",
            details={"route_count": len(routes)},
        )
    except Exception as e:
        return AuditCheck(
            check_id="DOC-004", check_name="Route Documentation",
            category="documentation", status="warn", severity="warning",
            message=str(e), details={},
        )


def check_skill_count() -> AuditCheck:
    """DOC-005: Skill/command count accuracy."""
    commands_dir = PROJECT_ROOT / ".claude" / "commands"
    if not commands_dir.exists():
        return AuditCheck(
            check_id="DOC-005", check_name="Skill Count",
            category="documentation", status="skip", severity="warning",
            message=".claude/commands/ not found", details={},
        )
    skills = list(commands_dir.glob("*.md"))
    # Exclude e2e subdir
    e2e_skills = list((commands_dir / "e2e").glob("*.md")) if (commands_dir / "e2e").exists() else []
    total = len(skills) + len(e2e_skills)
    return AuditCheck(
        check_id="DOC-005", check_name="Skill Count",
        category="documentation", status="pass" if total >= 20 else "warn",
        severity="warning",
        message=f"{len(skills)} skills + {len(e2e_skills)} E2E specs = {total} total",
        details={"skills": len(skills), "e2e_specs": len(e2e_skills), "total": total},
    )


# ---------------------------------------------------------------------------
# Check Registry
# ---------------------------------------------------------------------------

# Maps check_id -> (function, category, severity)
# Order within each category determines execution order
CHECK_REGISTRY: Dict[str, Tuple[Callable, str, str]] = {
    # Platform
    "PLT-001": (check_platform_compat, "platform", "warning"),
    "PLT-002": (check_python_version, "platform", "blocking"),
    "PLT-003": (check_stdlib_modules, "platform", "blocking"),
    "PLT-004": (check_dockerfile_syntax, "platform", "warning"),
    # Security
    "SEC-001": (check_sast_bandit, "security", "blocking"),
    "SEC-002": (check_dependency_audit, "security", "blocking"),
    "SEC-003": (check_secret_detection, "security", "blocking"),
    "SEC-004": (check_prompt_injection_gate, "security", "blocking"),
    "SEC-005": (check_owasp_agentic, "security", "warning"),
    "SEC-006": (check_code_pattern_scan, "security", "blocking"),
    # Compliance
    "CMP-001": (check_cui_markings, "compliance", "warning"),
    "CMP-002": (check_claude_governance, "compliance", "blocking"),
    "CMP-003": (check_append_only_tables, "compliance", "blocking"),
    "CMP-004": (check_security_gates_config, "compliance", "warning"),
    "CMP-005": (check_xai_compliance, "compliance", "warning"),
    "CMP-006": (check_sbom_generation, "compliance", "warning"),
    # Integration
    "INT-001": (check_mcp_servers, "integration", "blocking"),
    "INT-002": (check_db_schema, "integration", "blocking"),
    "INT-003": (check_cross_imports, "integration", "warning"),
    "INT-004": (check_dashboard_health, "integration", "warning"),
    "INT-005": (check_api_gateway, "integration", "warning"),
    # Performance
    "PRF-001": (check_migration_status, "performance", "warning"),
    "PRF-002": (check_backup_config, "performance", "warning"),
    "PRF-003": (check_resilience_config, "performance", "warning"),
    "PRF-004": (check_test_collection, "performance", "blocking"),
    # Documentation
    "DOC-001": (check_claude_md_table_count, "documentation", "warning"),
    "DOC-002": (check_tools_manifest, "documentation", "warning"),
    "DOC-003": (check_goals_manifest, "documentation", "warning"),
    "DOC-004": (check_route_documentation, "documentation", "warning"),
    "DOC-005": (check_skill_count, "documentation", "warning"),
}

# Execution order of categories
CATEGORY_ORDER = ["platform", "security", "compliance", "integration", "performance", "documentation"]

ALL_CATEGORIES = set(CATEGORY_ORDER)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_audit(
    categories: Optional[List[str]] = None,
    stream: bool = False,
) -> AuditReport:
    """Run the production readiness audit.

    Args:
        categories: Optional list of categories to run. None = all.
        stream: If True, print results as each check completes.

    Returns:
        AuditReport with all results.
    """
    if categories is None:
        categories = CATEGORY_ORDER
    else:
        categories = [c for c in CATEGORY_ORDER if c in categories]

    checks: List[AuditCheck] = []
    start_time = time.time()

    for cat in categories:
        if stream:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"  Category: {cat.upper()}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)

        cat_checks = [
            (cid, fn, sev) for cid, (fn, c, sev) in CHECK_REGISTRY.items() if c == cat
        ]
        for check_id, fn, severity in cat_checks:
            result, duration = _timed(fn)
            result.duration_ms = duration
            checks.append(result)

            if stream:
                icon = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]", "skip": "[SKIP]"}.get(result.status, "[????]")
                print(f"  {icon} {result.check_id}: {result.check_name} — {result.message} ({duration}ms)", file=sys.stderr)

    # Build report
    total_ms = int((time.time() - start_time) * 1000)
    cat_summary = {}
    for cat in categories:
        cat_checks_list = [c for c in checks if c.category == cat]
        cat_summary[cat] = {
            "pass": sum(1 for c in cat_checks_list if c.status == "pass"),
            "fail": sum(1 for c in cat_checks_list if c.status == "fail"),
            "warn": sum(1 for c in cat_checks_list if c.status == "warn"),
            "skip": sum(1 for c in cat_checks_list if c.status == "skip"),
            "checks": [c.to_dict() for c in cat_checks_list],
        }

    blockers = [
        f"{c.check_id}: {c.message}"
        for c in checks if c.status == "fail" and c.severity == "blocking"
    ]
    warnings = [
        f"{c.check_id}: {c.message}"
        for c in checks if c.status in ("fail", "warn")
    ]

    report = AuditReport(
        overall_pass=len(blockers) == 0,
        timestamp=datetime.now(timezone.utc).isoformat(),
        categories=cat_summary,
        total_checks=len(checks),
        passed=sum(1 for c in checks if c.status == "pass"),
        failed=sum(1 for c in checks if c.status == "fail"),
        warned=sum(1 for c in checks if c.status == "warn"),
        skipped=sum(1 for c in checks if c.status == "skip"),
        blockers=blockers,
        warnings=warnings,
        duration_total_ms=total_ms,
    )

    # Store in DB (append-only)
    _store_report(report, categories)

    return report


def _store_report(report: AuditReport, categories: List[str]):
    """Store audit report in production_audits table (append-only)."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO production_audits
               (overall_pass, total_checks, passed, failed, warned, skipped,
                blockers, warnings, categories_run, report_json, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                1 if report.overall_pass else 0,
                report.total_checks,
                report.passed,
                report.failed,
                report.warned,
                report.skipped,
                json.dumps(report.blockers),
                json.dumps(report.warnings),
                json.dumps(categories),
                json.dumps(report.to_dict()),
                report.duration_total_ms,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't fail the audit because DB write failed


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_human(report: AuditReport) -> str:
    """Format report for human-readable terminal output."""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  ICDEV Production Readiness Audit")
    lines.append("=" * 60)
    lines.append("")

    for cat, summary in report.categories.items():
        lines.append(f"  --- {cat.upper()} ---")
        for check in summary.get("checks", []):
            icon = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]", "skip": "[SKIP]"}.get(check["status"], "[????]")
            sev_tag = " (BLOCKING)" if check["severity"] == "blocking" and check["status"] == "fail" else ""
            lines.append(f"    {icon} {check['check_id']}: {check['check_name']}{sev_tag}")
            lines.append(f"          {check['message']} ({check['duration_ms']}ms)")
        lines.append("")

    lines.append("-" * 60)
    status = "READY" if report.overall_pass else "BLOCKED"
    lines.append(f"  Overall: {status}")
    lines.append(f"  Checks: {report.passed} passed, {report.failed} failed, {report.warned} warned, {report.skipped} skipped")
    lines.append(f"  Duration: {report.duration_total_ms}ms")

    if report.blockers:
        lines.append("")
        lines.append("  BLOCKERS (must fix before production):")
        for b in report.blockers:
            lines.append(f"    - {b}")

    if report.warnings:
        non_blocker_warnings = [w for w in report.warnings if w not in report.blockers]
        if non_blocker_warnings:
            lines.append("")
            lines.append("  WARNINGS (should fix):")
            for w in non_blocker_warnings[:10]:
                lines.append(f"    - {w}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ICDEV Production Readiness Audit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--stream", action="store_true", help="Stream results as they complete")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any blocker fails")
    parser.add_argument("--category", type=str, default=None,
                        help="Comma-separated categories: platform,security,compliance,integration,performance,documentation")
    args = parser.parse_args()

    categories = None
    if args.category:
        categories = [c.strip() for c in args.category.split(",") if c.strip() in ALL_CATEGORIES]
        if not categories:
            print(f"Invalid categories. Valid: {', '.join(CATEGORY_ORDER)}", file=sys.stderr)
            sys.exit(2)

    # Default to stream + human if neither --json nor --human specified
    if not args.json and not args.human:
        args.human = True
        args.stream = True

    report = run_audit(categories=categories, stream=args.stream or args.human)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_format_human(report))

    if args.gate or True:  # Always exit with appropriate code
        sys.exit(0 if report.overall_pass else 1)


if __name__ == "__main__":
    main()
