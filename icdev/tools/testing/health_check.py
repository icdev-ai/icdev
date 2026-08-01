# CUI // SP-CTI
"""ICDEV™ system health check.

Runs eight diagnostic checks against the local ICDEV install and
reports a single aggregate verdict. Designed to be invoked by an
operator before doing anything risky, and by CI as a smoke test of
the bundled tooling.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/health_check.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# data_types is the canonical source for these models. Fall back only
# if the rewrite is run in a stripped-down environment without the
# package — the structure of CheckResult is small enough to inline.
try:
    from tools.testing.data_types import CheckResult, HealthCheckResult
except ImportError:  # pragma: no cover - shim path
    class CheckResult:  # type: ignore[no-redef]
        def __init__(self, success, error=None, warning=None, details=None):
            self.success = success
            self.error = error
            self.warning = warning
            self.details = details or {}

    class HealthCheckResult:  # type: ignore[no-redef]
        def __init__(self, success, timestamp, checks=None,
                     warnings=None, errors=None):
            self.success = success
            self.timestamp = timestamp
            self.checks = checks or {}
            self.warnings = warnings or []
            self.errors = errors or []


_logger = get_logger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Individual checks
# ────────────────────────────────────────────────────────────────────────────


def check_env_vars() -> CheckResult:
    required_vars = {
        "ICDEV_DB_PATH": "Path to ICDEV™ database (default: data/icdev.db)",
    }
    optional_vars = {
        "ANTHROPIC_API_KEY": "Anthropic API Key (for Claude Code CLI)",
        "AWS_ACCESS_KEY_ID": "AWS GovCloud access key",
        "AWS_SECRET_ACCESS_KEY": "AWS GovCloud secret key",
        "AWS_DEFAULT_REGION": "AWS region (default: us-gov-west-1)",
        "GITLAB_TOKEN": "GitLab API token for CI/CD",
        "CLAUDE_CODE_PATH": "Path to Claude Code CLI (default: claude)",
    }

    missing_required: List[str] = []
    for var, desc in required_vars.items():
        if os.getenv(var):
            continue
        # Allow ICDEV_DB_PATH to be unset when the default file exists.
        if var == "ICDEV_DB_PATH":
            default_path = PROJECT_ROOT / "data" / "icdev.db"
            if default_path.exists():
                continue
        missing_required.append(f"{var} ({desc})")

    missing_optional = [
        f"{var} ({desc})"
        for var, desc in optional_vars.items()
        if not os.getenv(var)
    ]

    success = not missing_required
    return CheckResult(
        success=success,
        error="Missing required environment variables" if not success else None,
        details={
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        },
    )


_EXPECTED_TABLES = (
    "a2a_task_artifacts",
    "a2a_task_history",
    "a2a_tasks",
    "agents",
    "alerts",
    "audit_trail",
    "code_reviews",
    "compliance_controls",
    "deployments",
    "failure_log",
    "knowledge_patterns",
    "metric_snapshots",
    "poam_items",
    "project_controls",
    "projects",
    "sbom_records",
    "self_healing_events",
    "ssp_documents",
    "stig_findings",
)


def _list_tables(conn) -> List[str]:
    """Return every table name reachable from the connection. Tries the
    SQLite catalog first, falls back to the Postgres catalog if the
    first query fails."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception:
        pass
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
            "ORDER BY table_name"
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception:
        return []


def check_database() -> CheckResult:
    db_path = os.getenv(
        "ICDEV_DB_PATH",
        str(PROJECT_ROOT / "data" / "icdev.db"),
    )

    # When using the default sqlite path, require the file to exist.
    if db_path.endswith(".db") and not Path(db_path).exists():
        return CheckResult(
            success=False,
            error=(
                f"Database not found at {db_path}. "
                f"Run: python tools/db/init_icdev_db.py"
            ),
        )

    try:
        from tools.db.storage import get_connection
    except ImportError as exc:
        return CheckResult(
            success=False,
            error=f"tools.db.storage import failed: {exc}",
        )

    try:
        conn = get_connection()
    except Exception as exc:
        return CheckResult(
            success=False,
            error=f"Database connection failed: {exc}",
        )

    try:
        tables = _list_tables(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    missing = [t for t in _EXPECTED_TABLES if t not in tables]
    if missing:
        return CheckResult(
            success=False,
            error=f"Missing {len(missing)} tables",
            details={
                "tables_found": len(tables),
                "missing": missing,
            },
        )

    return CheckResult(
        success=True,
        details={
            "tables_found": len(tables),
            "db_path": db_path,
        },
    )


def check_db_observability() -> CheckResult:
    """Non-fatal DB observability snapshot (crx-db-02).

    Surfaces slow-query and connection-pool health on the health-check output.
    This is informational only — it NEVER fails the overall health check: a
    missing pg_stat_statements extension or a SQLite backend both report as
    ``success=True`` with an explanatory detail, never an error. Any exception
    is caught and reported so a stats hiccup can't crash health_check.
    """
    try:
        from tools.db.query_health import collect
    except ImportError as exc:  # pragma: no cover - defensive
        return CheckResult(
            success=True,
            warning=f"DB observability unavailable (import failed: {exc})",
            details={"available": False},
        )

    try:
        report = collect()
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(
            success=True,
            warning=f"DB observability collection error: {exc}",
            details={"available": False},
        )

    backend = report.get("backend", "unknown")
    if not report.get("available"):
        # SQLite / PG-unreachable / extension-absent — informational, not a failure.
        return CheckResult(
            success=True,
            details={
                "available": False,
                "backend": backend,
                "note": report.get("reason", "unavailable"),
            },
        )

    pool = report.get("pool_health", {}).get("server", {})
    slow = report.get("slow_queries", {})
    alerts = report.get("alerts", [])
    details = {
        "available": True,
        "backend": backend,
        "active_connections": pool.get("active"),
        "idle_connections": pool.get("idle"),
        "idle_in_transaction": pool.get("idle_in_transaction"),
        "waiting_on_lock": pool.get("waiting_on_lock"),
        "oldest_idle_in_txn_seconds": pool.get("oldest_idle_in_txn_seconds"),
        "slow_query_stats": "available" if slow.get("available") else "unavailable",
        "alerts": alerts,
    }
    warning = None
    if alerts:
        warning = "; ".join(a.get("message", "") for a in alerts)
    return CheckResult(success=True, warning=warning, details=details)


_REQUIRED_PYTHON_PKGS = {
    "sqlite3": "Database access (stdlib)",
    "pathlib": "File paths (stdlib)",
    "json": "JSON parsing (stdlib)",
    "argparse": "CLI arguments (stdlib)",
}

_OPTIONAL_PYTHON_PKGS = {
    "yaml": "YAML config parsing (pyyaml)",
    "jinja2": "Template rendering",
    "flask": "Web dashboard",
    "pytest": "Test runner",
    "behave": "BDD test runner",
    "pydantic": "Data validation",
}


def check_python_deps() -> CheckResult:
    def _try_import(name: str) -> bool:
        try:
            importlib.import_module(name)
            return True
        except ImportError:
            return False

    missing_required = [
        f"{pkg} ({desc})"
        for pkg, desc in _REQUIRED_PYTHON_PKGS.items()
        if not _try_import(pkg)
    ]
    missing_optional = [
        f"{pkg} ({desc})"
        for pkg, desc in _OPTIONAL_PYTHON_PKGS.items()
        if not _try_import(pkg)
    ]
    success = not missing_required
    return CheckResult(
        success=success,
        error="Missing required Python packages" if not success else None,
        warning=(
            f"Missing optional packages: {', '.join(missing_optional)}"
            if missing_optional else None
        ),
        details={
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        },
    )


_TOOL_PROBE_MODULES = {
    "tools.db.init_icdev_db": "Database initialization",
    "tools.audit.audit_logger": "Audit trail",
    "tools.compliance.nist_lookup": "NIST control lookup",
    "tools.security.sast_runner": "SAST scanning",
    "tools.builder.scaffolder": "Project scaffolding",
}


def check_tools() -> CheckResult:
    available: List[str] = []
    unavailable: List[str] = []
    for module, desc in _TOOL_PROBE_MODULES.items():
        try:
            importlib.import_module(module)
            available.append(module)
        except Exception:
            unavailable.append(f"{module} ({desc})")
    return CheckResult(
        success=len(available) > 0,
        warning=(
            f"{len(unavailable)} tool modules unavailable"
            if unavailable else None
        ),
        details={
            "available": len(available),
            "unavailable": unavailable,
        },
    )


def check_mcp_servers() -> CheckResult:
    config_path = PROJECT_ROOT / ".mcp.json"
    if not config_path.exists():
        return CheckResult(
            success=False,
            error=".mcp.json not found at project root",
        )
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except json.JSONDecodeError as exc:
        return CheckResult(
            success=False,
            error=f".mcp.json parse error: {exc}",
        )
    except OSError as exc:
        return CheckResult(
            success=False,
            error=f".mcp.json read error: {exc}",
        )

    servers = (config or {}).get("mcpServers") or {}
    valid: List[str] = []
    invalid: List[str] = []

    for name, server_config in servers.items():
        if not isinstance(server_config, dict):
            invalid.append(f"{name} (not an object)")
            continue
        cmd = server_config.get("command")
        args = server_config.get("args") or []
        if not cmd or not args:
            invalid.append(f"{name} (missing command or args)")
            continue
        if cmd == "python":
            script_path = PROJECT_ROOT / args[0]
            if script_path.exists():
                valid.append(name)
            else:
                invalid.append(f"{name} (script not found: {args[0]})")
        else:
            valid.append(name)

    return CheckResult(
        success=len(valid) > 0,
        warning=(
            f"{len(invalid)} servers have issues" if invalid else None
        ),
        details={
            "valid_servers": valid,
            "invalid_servers": invalid,
        },
    )


def check_git_repo() -> CheckResult:
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
        )
    except FileNotFoundError:
        return CheckResult(success=False, error="Git is not installed")
    if proc.returncode != 0:
        return CheckResult(
            success=True,
            warning="No git remote 'origin' configured",
            details={"has_remote": False},
        )
    return CheckResult(
        success=True,
        details={
            "repo_url": (proc.stdout or "").strip(),
            "has_remote": True,
        },
    )


def check_claude_code() -> CheckResult:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return CheckResult(
            success=True,
            warning="ANTHROPIC_API_KEY not set, skipping Claude Code check",
            details={"skipped": True},
        )
    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    try:
        proc = subprocess.run(
            [claude_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError:
        return CheckResult(
            success=False,
            error=f"Claude Code CLI not found at '{claude_path}'",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(success=False, error="Claude Code CLI timed out")

    if proc.returncode != 0:
        return CheckResult(
            success=False,
            error=f"Claude Code CLI not functional at '{claude_path}'",
        )
    return CheckResult(
        success=True,
        details={"version": (proc.stdout or "").strip(), "path": claude_path},
    )


def check_playwright() -> CheckResult:
    try:
        from tools.compat.platform_utils import get_npx_cmd
        npx = get_npx_cmd()
    except Exception:
        return CheckResult(
            success=True,
            warning="npx helper unavailable — Playwright not probed",
            details={"installed": False},
        )

    try:
        proc = subprocess.run(
            [npx, "playwright", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=str(PROJECT_ROOT),
        )
    except FileNotFoundError:
        return CheckResult(
            success=True,
            warning=(
                "npx not found — Playwright unavailable "
                "(E2E tests will use MCP fallback)"
            ),
            details={"installed": False},
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            success=True,
            warning="Playwright version check timed out",
            details={"installed": False},
        )

    if proc.returncode != 0:
        return CheckResult(
            success=True,
            warning="Playwright CLI not available (E2E tests will use MCP fallback)",
            details={"installed": False},
        )

    version = (proc.stdout or "").strip()
    e2e_dir = PROJECT_ROOT / "tests" / "e2e"
    native_tests = list(e2e_dir.glob("*.spec.ts")) if e2e_dir.exists() else []
    return CheckResult(
        success=True,
        details={
            "installed": True,
            "version": version,
            "native_test_count": len(native_tests),
            "mode": "native" if native_tests else "mcp",
        },
    )


# ────────────────────────────────────────────────────────────────────────────
# OSV-Scanner SCA check
# ────────────────────────────────────────────────────────────────────────────


def check_osv_scanner() -> CheckResult:
    """SCA vulnerability scan via osv-scanner binary."""
    try:
        from tools.security.osv_scanner import OsvScanner
    except ImportError as exc:
        return CheckResult(success=True, warning=f"osv_scanner module unavailable: {exc}", details={"status": "unavailable"})

    scanner = OsvScanner()
    if not scanner.available:
        return CheckResult(
            success=True,
            warning="osv-scanner binary not in PATH — install from github.com/google/osv-scanner",
            details={"status": "unavailable"},
        )

    result = scanner.run("requirements.txt")
    if result.status == "error":
        return CheckResult(success=False, error=f"osv-scanner error: {result.error}", details={"status": "error"})
    if result.status == "clean":
        return CheckResult(success=True, details={"status": "clean", "vuln_count": 0})

    # vulnerabilities_found — block on critical, warn on high
    details = {
        "status": result.status,
        "vuln_count": result.vuln_count,
        "critical": result.critical,
        "high": result.high,
        "medium": result.medium,
        "low": result.low,
    }
    if result.critical > 0:
        return CheckResult(
            success=False,
            error=f"{result.critical} CRITICAL vulnerabilities found by osv-scanner",
            details=details,
        )
    return CheckResult(
        success=True,
        warning=f"{result.vuln_count} vulnerabilities found (crit=0 high={result.high})",
        details=details,
    )


# ────────────────────────────────────────────────────────────────────────────
# Aggregate driver
# ────────────────────────────────────────────────────────────────────────────


_HEALTH_CHECKS: Dict[str, Callable[[], CheckResult]] = {
    "environment": check_env_vars,
    "database": check_database,
    "db_observability": check_db_observability,
    "python_deps": check_python_deps,
    "tools": check_tools,
    "mcp_servers": check_mcp_servers,
    "git_repository": check_git_repo,
    "claude_code": check_claude_code,
    "playwright": check_playwright,
    "osv_scanner": check_osv_scanner,
}


def run_health_check() -> HealthCheckResult:
    """Run every check and aggregate results into a HealthCheckResult."""
    result = HealthCheckResult(
        success=True,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks={},
        warnings=[],
        errors=[],
    )

    for name, fn in _HEALTH_CHECKS.items():
        try:
            check_result = fn()
        except Exception as exc:
            check_result = CheckResult(
                success=False,
                error=f"Check crashed: {exc}",
            )
        result.checks[name] = check_result

        if not check_result.success:
            result.success = False
            if check_result.error:
                result.errors.append(f"[{name}] {check_result.error}")
        if check_result.warning:
            result.warnings.append(f"[{name}] {check_result.warning}")

    return result


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _print_human(result: HealthCheckResult) -> None:
    status = "HEALTHY" if result.success else "UNHEALTHY"
    print(f"{'PASS' if result.success else 'FAIL'} Overall Status: {status}")
    print(f"Timestamp: {result.timestamp}\n")
    print("Check Results:")
    print("-" * 50)

    skip_keys = {
        "missing_required",
        "missing_optional",
        "unavailable",
        "invalid_servers",
    }
    for check_name, check_result in result.checks.items():
        status_str = "PASS" if check_result.success else "FAIL"
        print(f"\n  [{status_str}] {check_name.replace('_', ' ').title()}")
        for key, value in (check_result.details or {}).items():
            if value is None or key in skip_keys:
                continue
            print(f"       {key}: {value}")
        if check_result.error:
            print(f"       Error: {check_result.error}")
        if check_result.warning:
            print(f"       Warning: {check_result.warning}")

    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  - {error}")


def _print_json(result: HealthCheckResult) -> None:
    output = {
        "success": result.success,
        "timestamp": result.timestamp,
        "checks": {
            name: {
                "success": chk.success,
                "error": chk.error,
                "warning": chk.warning,
                "details": chk.details,
            }
            for name, chk in result.checks.items()
        },
        "warnings": result.warnings,
        "errors": result.errors,
    }
    print(json.dumps(output, indent=2))


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="ICDEV™ system health check")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON to stdout")
    parser.add_argument("--project-id", help="Reserved for future scoped checks")
    args = parser.parse_args(argv)

    result = run_health_check()
    if args.json:
        _print_json(result)
    else:
        _print_human(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
