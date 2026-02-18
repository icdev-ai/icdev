# CUI // SP-CTI
# ICDEV System Health Check
# Adapted from ADW health_check.py for Gov/DoD environment validation

"""
ICDEV Health Check — validates the entire ICDEV system is operational.

Usage:
    python tools/testing/health_check.py [--json] [--project-id <id>]

Checks performed:
1. Environment variables (ICDEV, AWS, optional keys)
2. Database connectivity (icdev.db — 28 tables)
3. Python dependencies (stdlib + optional packages)
4. Tool availability (audit, compliance, security, builder, etc.)
5. MCP server syntax validation
6. Git repository configuration
7. Claude Code CLI (if ANTHROPIC_API_KEY set)

Exit codes: 0 = healthy, 1 = unhealthy
"""

import argparse
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.testing.data_types import CheckResult, HealthCheckResult
except ImportError:
    # Inline fallback if data_types not available
    class CheckResult:
        def __init__(self, success, error=None, warning=None, details=None):
            self.success = success
            self.error = error
            self.warning = warning
            self.details = details or {}

    class HealthCheckResult:
        def __init__(self, success, timestamp, checks=None, warnings=None, errors=None):
            self.success = success
            self.timestamp = timestamp
            self.checks = checks or {}
            self.warnings = warnings or []
            self.errors = errors or []


def check_env_vars() -> CheckResult:
    """Check required and optional environment variables."""
    required_vars = {
        "ICDEV_DB_PATH": "Path to ICDEV database (default: data/icdev.db)",
    }

    optional_vars = {
        "ANTHROPIC_API_KEY": "Anthropic API Key (for Claude Code CLI)",
        "AWS_ACCESS_KEY_ID": "AWS GovCloud access key",
        "AWS_SECRET_ACCESS_KEY": "AWS GovCloud secret key",
        "AWS_DEFAULT_REGION": "AWS region (default: us-gov-west-1)",
        "GITLAB_TOKEN": "GitLab API token for CI/CD",
        "CLAUDE_CODE_PATH": "Path to Claude Code CLI (default: claude)",
    }

    missing_required = []
    missing_optional = []

    for var, desc in required_vars.items():
        val = os.getenv(var)
        if not val:
            # Check if default path exists
            if var == "ICDEV_DB_PATH":
                default_path = PROJECT_ROOT / "data" / "icdev.db"
                if default_path.exists():
                    continue  # Default path works
            missing_required.append(f"{var} ({desc})")

    for var, desc in optional_vars.items():
        if not os.getenv(var):
            missing_optional.append(f"{var} ({desc})")

    success = len(missing_required) == 0

    return CheckResult(
        success=success,
        error="Missing required environment variables" if not success else None,
        details={
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        },
    )


def check_database() -> CheckResult:
    """Check ICDEV database connectivity and table structure."""
    db_path = os.getenv("ICDEV_DB_PATH", str(PROJECT_ROOT / "data" / "icdev.db"))

    if not Path(db_path).exists():
        return CheckResult(
            success=False,
            error=f"Database not found at {db_path}. Run: python tools/db/init_icdev_db.py",
        )

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]

        expected_tables = [
            "a2a_task_artifacts", "a2a_task_history", "a2a_tasks", "agents",
            "alerts", "audit_trail", "code_reviews", "compliance_controls",
            "deployments", "failure_log", "knowledge_patterns", "metric_snapshots",
            "poam_items", "project_controls", "projects", "sbom_records",
            "self_healing_events", "ssp_documents", "stig_findings",
        ]

        missing_tables = [t for t in expected_tables if t not in tables]

        conn.close()

        if missing_tables:
            return CheckResult(
                success=False,
                error=f"Missing {len(missing_tables)} tables",
                details={"tables_found": len(tables), "missing": missing_tables},
            )

        return CheckResult(
            success=True,
            details={"tables_found": len(tables), "db_path": db_path},
        )
    except Exception as e:
        return CheckResult(success=False, error=f"Database error: {str(e)}")


def check_python_deps() -> CheckResult:
    """Check that required Python packages are importable."""
    required = {
        "sqlite3": "Database access (stdlib)",
        "pathlib": "File paths (stdlib)",
        "json": "JSON parsing (stdlib)",
        "argparse": "CLI arguments (stdlib)",
    }

    optional = {
        "yaml": "YAML config parsing (pyyaml)",
        "jinja2": "Template rendering",
        "flask": "Web dashboard",
        "pytest": "Test runner",
        "behave": "BDD test runner",
        "pydantic": "Data validation",
    }

    missing_required = []
    missing_optional = []

    for pkg, desc in required.items():
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing_required.append(f"{pkg} ({desc})")

    for pkg, desc in optional.items():
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing_optional.append(f"{pkg} ({desc})")

    success = len(missing_required) == 0

    return CheckResult(
        success=success,
        error="Missing required Python packages" if not success else None,
        warning=f"Missing optional packages: {', '.join(missing_optional)}" if missing_optional else None,
        details={
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        },
    )


def check_tools() -> CheckResult:
    """Check that core ICDEV tool modules are importable."""
    tool_modules = {
        "tools.db.init_icdev_db": "Database initialization",
        "tools.audit.audit_logger": "Audit trail",
        "tools.compliance.nist_lookup": "NIST control lookup",
        "tools.security.sast_runner": "SAST scanning",
        "tools.builder.scaffolder": "Project scaffolding",
    }

    available = []
    unavailable = []

    for module, desc in tool_modules.items():
        try:
            importlib.import_module(module)
            available.append(module)
        except (ImportError, Exception):
            unavailable.append(f"{module} ({desc})")

    return CheckResult(
        success=len(available) > 0,
        warning=f"{len(unavailable)} tool modules unavailable" if unavailable else None,
        details={
            "available": len(available),
            "unavailable": unavailable,
        },
    )


def check_mcp_servers() -> CheckResult:
    """Check that MCP server configurations are valid."""
    mcp_config_path = PROJECT_ROOT / ".mcp.json"

    if not mcp_config_path.exists():
        return CheckResult(
            success=False,
            error=".mcp.json not found at project root",
        )

    try:
        with open(mcp_config_path) as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        valid_servers = []
        invalid_servers = []

        for name, server_config in servers.items():
            cmd = server_config.get("command")
            args = server_config.get("args", [])

            if cmd and args:
                # Check if the script file exists (for python servers)
                if cmd == "python" and args:
                    script_path = PROJECT_ROOT / args[0]
                    if script_path.exists():
                        valid_servers.append(name)
                    else:
                        invalid_servers.append(f"{name} (script not found: {args[0]})")
                else:
                    valid_servers.append(name)
            else:
                invalid_servers.append(f"{name} (missing command or args)")

        return CheckResult(
            success=len(valid_servers) > 0,
            warning=f"{len(invalid_servers)} servers have issues" if invalid_servers else None,
            details={
                "valid_servers": valid_servers,
                "invalid_servers": invalid_servers,
            },
        )
    except json.JSONDecodeError as e:
        return CheckResult(success=False, error=f".mcp.json parse error: {e}")


def check_git_repo() -> CheckResult:
    """Check git repository configuration."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )

        if result.returncode != 0:
            return CheckResult(
                success=True,
                warning="No git remote 'origin' configured",
                details={"has_remote": False},
            )

        repo_url = result.stdout.strip()
        return CheckResult(
            success=True,
            details={"repo_url": repo_url, "has_remote": True},
        )
    except FileNotFoundError:
        return CheckResult(
            success=False,
            error="Git is not installed",
        )


def check_claude_code() -> CheckResult:
    """Test Claude Code CLI functionality (only if ANTHROPIC_API_KEY is set)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return CheckResult(
            success=True,
            warning="ANTHROPIC_API_KEY not set, skipping Claude Code check",
            details={"skipped": True},
        )

    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")

    try:
        result = subprocess.run(
            [claude_path, "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return CheckResult(
                success=False,
                error=f"Claude Code CLI not functional at '{claude_path}'",
            )

        return CheckResult(
            success=True,
            details={"version": result.stdout.strip(), "path": claude_path},
        )
    except FileNotFoundError:
        return CheckResult(
            success=False,
            error=f"Claude Code CLI not found at '{claude_path}'",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            success=False,
            error="Claude Code CLI timed out",
        )


def check_playwright() -> CheckResult:
    """Check if Playwright is installed and browsers are available."""
    import platform
    npx = "npx.cmd" if platform.system() == "Windows" else "npx"
    try:
        result = subprocess.run(
            [npx, "playwright", "--version"],
            capture_output=True, text=True, timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return CheckResult(
                success=True,
                warning="Playwright CLI not available (E2E tests will use MCP fallback)",
                details={"installed": False},
            )

        version = result.stdout.strip()

        # Check for native test files
        native_tests = list((PROJECT_ROOT / "tests" / "e2e").glob("*.spec.ts")) if (PROJECT_ROOT / "tests" / "e2e").exists() else []

        return CheckResult(
            success=True,
            details={
                "installed": True,
                "version": version,
                "native_test_count": len(native_tests),
                "mode": "native" if native_tests else "mcp",
            },
        )
    except FileNotFoundError:
        return CheckResult(
            success=True,
            warning="npx not found — Playwright unavailable (E2E tests will use MCP fallback)",
            details={"installed": False},
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            success=True,
            warning="Playwright version check timed out",
            details={"installed": False},
        )


def run_health_check() -> HealthCheckResult:
    """Run all health checks and return aggregate results.

    Follows ADW health_check.py pattern: run each check, aggregate into
    HealthCheckResult with overall success/failure.
    """
    result = HealthCheckResult(
        success=True,
        timestamp=datetime.now().isoformat(),
        checks={},
        warnings=[],
        errors=[],
    )

    checks = {
        "environment": check_env_vars,
        "database": check_database,
        "python_deps": check_python_deps,
        "tools": check_tools,
        "mcp_servers": check_mcp_servers,
        "git_repository": check_git_repo,
        "claude_code": check_claude_code,
        "playwright": check_playwright,
    }

    for name, check_fn in checks.items():
        try:
            check_result = check_fn()
        except Exception as e:
            check_result = CheckResult(success=False, error=f"Check crashed: {str(e)}")

        result.checks[name] = check_result

        if not check_result.success:
            result.success = False
            if check_result.error:
                result.errors.append(f"[{name}] {check_result.error}")
        if check_result.warning:
            result.warnings.append(f"[{name}] {check_result.warning}")

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="ICDEV System Health Check")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--project-id", help="Optional project ID for scoped checks")
    args = parser.parse_args()

    result = run_health_check()

    if args.json:
        # Serialize to JSON
        output = {
            "success": result.success,
            "timestamp": result.timestamp,
            "checks": {},
            "warnings": result.warnings,
            "errors": result.errors,
        }
        for name, check in result.checks.items():
            output["checks"][name] = {
                "success": check.success,
                "error": check.error,
                "warning": check.warning,
                "details": check.details,
            }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        status = "HEALTHY" if result.success else "UNHEALTHY"
        print(f"{'PASS' if result.success else 'FAIL'} Overall Status: {status}")
        print(f"Timestamp: {result.timestamp}\n")

        print("Check Results:")
        print("-" * 50)

        for check_name, check_result in result.checks.items():
            status_str = "PASS" if check_result.success else "FAIL"
            print(f"\n  [{status_str}] {check_name.replace('_', ' ').title()}")

            for key, value in check_result.details.items():
                if value is not None and key not in ["missing_required", "missing_optional", "unavailable", "invalid_servers"]:
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

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
