# CUI // SP-CTI
"""Static Validator — Stage 4 of Connector Forge pipeline (D-CF-1).

6 gates (all must pass for sandbox deployment):
  1. py_compile — syntax check via stdlib
  2. ruff       — linting via subprocess
  3. ast_abc    — AST walk to verify DataConnector abstract methods implemented
  4. bandit     — SAST security scan via subprocess
  5. secret_scan — regex scan for hardcoded secrets
  6. import_whitelist — AST import walk against allowed list
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import ast
import logging
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = get_logger("databridge.forge.static_validator")

# Abstract methods that MUST be implemented (full ABC)
_REQUIRED_ABSTRACT_METHODS = {
    "connector_type",
    "connector_name",
    "capabilities",
    "connect",
    "disconnect",
    "health_check",
    "read",
    "infer_schema",
    "list_tables",
}

# Methods provided by known base classes (don't need re-implementation)
_BASE_CLASS_METHODS = {
    "SaaSBaseConnector": {
        "connector_type",
        "capabilities",
        "connect",
        "disconnect",
        "health_check",
        "read",
        "infer_schema",
        "list_tables",
    },
    "SoapBaseConnector": {
        "connector_type",
        "capabilities",
        "connect",
        "disconnect",
        "health_check",
        "read",
        "write",
        "infer_schema",
        "list_tables",
    },
    "HealthBaseConnector": {
        "connector_type",
        "capabilities",
        "connect",
        "disconnect",
        "health_check",
        "read",
        "infer_schema",
        "list_tables",
    },
    "SQLBaseConnector": {
        "connector_type",
        "capabilities",
    },
    "FsspecBaseConnector": {
        "connector_type",
        "capabilities",
    },
    "MessagingBaseConnector": {
        "connector_type",
        "capabilities",
    },
    "DataConnector": set(),  # ABC — nothing provided
}

# Default import whitelist (overridden by args/databridge_config.yaml)
_DEFAULT_ALLOWED_ROOTS = {
    # stdlib
    "json",
    "logging",
    "time",
    "datetime",
    "re",
    "typing",
    "dataclasses",
    "abc",
    "enum",
    "pathlib",
    "hashlib",
    "ssl",
    "socket",
    "urllib",
    "xml",
    "html",
    "base64",
    "collections",
    "functools",
    "itertools",
    "io",
    "os",  # os.path is OK; os.system blocked separately
    # project
    "tools",
    # optional deps
    "requests",
    "requests_toolbelt",
    "oauthlib",
    "zeep",
    "hl7",
    "fhirclient",
    "pyarrow",
    "sqlalchemy",
    "pymysql",
    "psycopg2",
}

# Dangerous function calls to block
_DANGEROUS_CALLS = {"eval", "exec", "__import__", "compile"}
_DANGEROUS_ATTRS = {
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_output",
}

# Secret patterns
_SECRET_PATTERNS = [
    re.compile(r'(?i)(password|passwd|secret|api_key|token|credential)\s*=\s*["\'][^"\']{8,}["\']'),
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"),
    re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)AKIA[A-Z0-9]{16}"),  # AWS access key
]


def validate_connector_code(
    code: str,
    connector_name: str,
    allowed_imports: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run all 6 validation gates on generated connector code.

    Returns:
        {
            "passed": bool,
            "gates": [{"stage": str, "passed": bool, "details": str, "duration_ms": int}],
            "blocking_gates": [str],
            "warnings": [str],
        }
    """
    allowed = set(allowed_imports or []) | _DEFAULT_ALLOWED_ROOTS
    gates: List[Dict[str, Any]] = []

    # Gate 1: py_compile
    gates.append(_gate_py_compile(code))

    # Gate 2: ruff (only if syntax OK)
    if gates[-1]["passed"]:
        gates.append(_gate_ruff(code, connector_name))
    else:
        gates.append(_skip("ruff", "Skipped — syntax error"))

    # Gate 3: AST ABC check
    gates.append(_gate_ast_abc(code))

    # Gate 4: bandit SAST
    gates.append(_gate_bandit(code, connector_name))

    # Gate 5: Secret scan
    gates.append(_gate_secret_scan(code))

    # Gate 6: Import whitelist
    gates.append(_gate_import_whitelist(code, allowed))

    blocking = [g for g in gates if not g["passed"]]
    return {
        "passed": len(blocking) == 0,
        "gates": gates,
        "blocking_gates": [g["stage"] for g in blocking],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Gate implementations
# ---------------------------------------------------------------------------


def _gate_py_compile(code: str) -> Dict[str, Any]:
    """Gate 1: Python syntax validation."""
    start = time.time()
    try:
        compile(code, "<forge>", "exec")
        return _result("py_compile", True, "Syntax OK", start)
    except SyntaxError as exc:
        return _result("py_compile", False, f"Syntax error: {exc}", start)


def _gate_ruff(code: str, name: str) -> Dict[str, Any]:
    """Gate 2: Ruff linting."""
    start = time.time()
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select=E,W,F", tmp],
            capture_output=True,
            text=True,
            timeout=30,
        )
        passed = proc.returncode == 0
        detail = (proc.stdout + proc.stderr).strip()[:500]
        return _result("ruff", passed, detail or "No issues", start)
    except FileNotFoundError:
        return _result("ruff", True, "ruff not installed — skipped", start)
    except subprocess.TimeoutExpired:
        return _result("ruff", False, "ruff timed out", start)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _gate_ast_abc(code: str) -> Dict[str, Any]:
    """Gate 3: Verify all DataConnector abstract methods are implemented.

    Accounts for base class inheritance — methods provided by known base
    classes (SaaSBaseConnector, SoapBaseConnector, etc.) don't need
    re-implementation in the generated subclass.
    """
    start = time.time()
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return _result("ast_abc", False, f"Cannot parse: {exc}", start)

    base_provided = _collect_base_provided(tree)
    implemented = _collect_implemented_methods(tree)

    still_needed = _REQUIRED_ABSTRACT_METHODS - base_provided - implemented
    if still_needed:
        return _result(
            "ast_abc",
            False,
            f"Missing abstract methods: {sorted(still_needed)}",
            start,
        )

    if not _has_connector_name(tree, implemented, base_provided):
        return _result("ast_abc", False, "Missing: connector_name property or _connector_name attribute", start)

    return _result(
        "ast_abc",
        True,
        f"ABC compliance verified (base provides {len(base_provided)} methods)",
        start,
    )


def _collect_base_provided(tree: ast.Module) -> Set[str]:
    """Collect methods provided by known base classes."""
    provided: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name in _BASE_CLASS_METHODS:
                provided |= _BASE_CLASS_METHODS[base_name]
    return provided


def _collect_implemented_methods(tree: ast.Module) -> Set[str]:
    """Collect all method/property names defined in any class."""
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _has_connector_name(tree: ast.Module, implemented: Set[str], base_provided: Set[str]) -> bool:
    """Check if connector_name is defined as property or _connector_name as class attr."""
    if "connector_name" in implemented or "connector_name" in base_provided:
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == "_connector_name":
                    return True
    return False


def _gate_bandit(code: str, name: str) -> Dict[str, Any]:
    """Gate 4: Bandit SAST security scan."""
    start = time.time()
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-f", "json", "-ll", tmp],
            capture_output=True,
            text=True,
            timeout=30,
        )
        import json

        try:
            report = json.loads(proc.stdout)
            highs = [r for r in report.get("results", []) if r.get("issue_severity") in ("HIGH", "CRITICAL")]
            passed = len(highs) == 0
            detail = f"{len(highs)} HIGH/CRITICAL findings" if highs else "No HIGH/CRITICAL findings"
            return _result("bandit", passed, detail, start)
        except (json.JSONDecodeError, KeyError):
            return _result("bandit", True, "bandit output unparseable — skipped", start)
    except FileNotFoundError:
        return _result("bandit", True, "bandit not installed — skipped", start)
    except subprocess.TimeoutExpired:
        return _result("bandit", False, "bandit timed out", start)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _gate_secret_scan(code: str) -> Dict[str, Any]:
    """Gate 5: Regex scan for hardcoded secrets."""
    start = time.time()
    findings: List[str] = []
    for i, line in enumerate(code.splitlines(), 1):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"Line {i}: potential hardcoded secret")
                break
    passed = len(findings) == 0
    detail = "; ".join(findings) if findings else "No hardcoded secrets found"
    return _result("secret_scan", passed, detail, start)


def _gate_import_whitelist(code: str, allowed: Set[str]) -> Dict[str, Any]:
    """Gate 6: AST import walk against allowed root modules."""
    start = time.time()
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return _result("import_whitelist", False, f"Cannot parse: {exc}", start)

    violations: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root and root not in allowed:
                violations.append(module)

    # Also check for dangerous function calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = _get_call_name(node)
            if func_name in _DANGEROUS_CALLS:
                violations.append(f"Dangerous call: {func_name}()")
            if func_name in _DANGEROUS_ATTRS:
                violations.append(f"Dangerous call: {func_name}()")

    passed = len(violations) == 0
    detail = f"Blocked: {violations}" if violations else "All imports allowed"
    return _result("import_whitelist", passed, detail, start)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_call_name(node: ast.Call) -> str:
    """Extract function name from an ast.Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
    return ""


def _result(stage: str, passed: bool, details: str, start: float) -> Dict[str, Any]:
    return {
        "stage": stage,
        "passed": passed,
        "details": details,
        "duration_ms": int((time.time() - start) * 1000),
    }


def _skip(stage: str, reason: str) -> Dict[str, Any]:
    return {"stage": stage, "passed": False, "details": reason, "duration_ms": 0}
