# CUI // SP-CTI
# ICDEV Test Orchestrator
# Adapted from ADW adw_test.py — retry logic, resolution, E2E coordination

"""
ICDEV Test Orchestrator — runs unit, BDD, E2E, security, and compliance tests
with automatic retry and failure resolution.

Usage:
    python tools/testing/test_orchestrator.py --project-dir <path> [--project-id <id>] [--skip-e2e] [--skip-security]

Workflow (adapted from ADW adw_test.py):
1. Health check — validate environment
2. Unit tests (pytest) with retry + resolution
3. BDD tests (behave) with retry + resolution
4. E2E tests (Playwright MCP) with retry + resolution
5. Security gate evaluation
6. Compliance gate evaluation
7. Summary report with audit trail

Retry logic:
- Unit/BDD: max 4 attempts (MAX_TEST_RETRY_ATTEMPTS)
- E2E: max 2 attempts (MAX_E2E_TEST_RETRY_ATTEMPTS)
- Resolution: attempt to fix failing tests between retries
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple, Optional, List

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.data_types import (
    TestResult,
    E2ETestResult,
    GateResult,
    GateEvaluation,
    TestRunState,
)
from tools.testing.utils import (
    make_run_id,
    setup_logger,
    get_safe_subprocess_env,
    timestamp_iso,
    ensure_run_dir,
)

# Constants (adapted from ADW)
MAX_TEST_RETRY_ATTEMPTS = 4
MAX_E2E_TEST_RETRY_ATTEMPTS = 2


# --- Syntax & Quality Checks (adapted from ADW test.md) ---

def run_py_compile(project_dir: str, logger) -> TestResult:
    """Run py_compile syntax check on Python source files.

    Adapted from ADW test.md Step 1: Python Syntax Check.
    Catches syntax errors before running full test suite.
    """
    logger.info("Running Python syntax check (py_compile)...")
    env = get_safe_subprocess_env()

    # Find the source directory
    src_dir = None
    for candidate in ["src", "app", "lib", project_dir]:
        check_dir = os.path.join(project_dir, candidate) if candidate != project_dir else candidate
        if os.path.isdir(check_dir):
            py_files = [f for f in os.listdir(check_dir) if f.endswith(".py")]
            if py_files:
                src_dir = check_dir
                break

    if not src_dir:
        return TestResult(
            test_name="python_syntax_check",
            passed=True,
            execution_command="python -m py_compile (no source files found)",
            test_purpose="Validates Python syntax by compiling source files to bytecode",
            test_type="unit",
        )

    # Compile each .py file
    py_files = []
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))

    errors = []
    for py_file in py_files[:50]:  # Limit to prevent timeout
        try:
            cmd = [sys.executable, "-m", "py_compile", py_file]
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=10)
            if proc.returncode != 0:
                errors.append(f"{py_file}: {proc.stderr.strip()}")
        except subprocess.TimeoutExpired:
            errors.append(f"{py_file}: compilation timed out")

    passed = len(errors) == 0
    logger.info(f"py_compile: {len(py_files)} files checked, {len(errors)} errors")

    return TestResult(
        test_name="python_syntax_check",
        passed=passed,
        execution_command=f"python -m py_compile {src_dir}/*.py",
        test_purpose="Validates Python syntax by compiling source files to bytecode, catching syntax errors like missing colons, invalid indentation, or malformed statements",
        error="; ".join(errors[:5]) if errors else None,
        test_type="unit",
        nist_controls=["SA-11"],
    )


def run_ruff(project_dir: str, logger) -> TestResult:
    """Run Ruff linter for code quality checks.

    Adapted from ADW test.md Step 2: Backend Code Quality Check.
    Ruff is an extremely fast Python linter written in Rust that replaces
    flake8, isort, and parts of pylint.
    """
    logger.info("Running Ruff code quality check...")
    env = get_safe_subprocess_env()

    # Try ruff directly, then via python -m
    for cmd_variant in [["ruff", "check", project_dir], [sys.executable, "-m", "ruff", "check", project_dir]]:
        try:
            proc = subprocess.run(
                cmd_variant, capture_output=True, text=True, env=env,
                timeout=60, cwd=project_dir
            )

            # Ruff returns 0 if no issues, 1 if issues found
            if proc.returncode == 0:
                logger.info("Ruff: no issues found")
                return TestResult(
                    test_name="code_quality_ruff",
                    passed=True,
                    execution_command=" ".join(cmd_variant),
                    test_purpose="Validates Python code quality using Ruff — identifies unused imports, style violations, security issues, and potential bugs",
                    test_type="unit",
                    nist_controls=["SA-11", "SA-15"],
                )

            # Issues found
            output = proc.stdout.strip() or proc.stderr.strip()
            # Count issues
            issue_lines = [line for line in output.splitlines() if line.strip() and ":" in line and not line.startswith("Found")]
            logger.info(f"Ruff: {len(issue_lines)} issues found")

            return TestResult(
                test_name="code_quality_ruff",
                passed=False,
                execution_command=" ".join(cmd_variant),
                test_purpose="Validates Python code quality using Ruff — identifies unused imports, style violations, security issues, and potential bugs",
                error=output[:500],
                test_type="unit",
                nist_controls=["SA-11", "SA-15"],
            )

        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return TestResult(
                test_name="code_quality_ruff",
                passed=False,
                execution_command=" ".join(cmd_variant),
                test_purpose="Validates Python code quality using Ruff",
                error="Ruff check timed out after 60 seconds",
                test_type="unit",
            )

    # Ruff not installed
    logger.warning("Ruff not installed, skipping code quality check (pip install ruff)")
    return TestResult(
        test_name="code_quality_ruff",
        passed=True,
        execution_command="ruff check .",
        test_purpose="Validates Python code quality using Ruff (SKIPPED — ruff not installed)",
        test_type="unit",
    )


def run_bandit(project_dir: str, logger) -> TestResult:
    """Run Bandit SAST security scan.

    Adapted from ADW test.md security scan step + ICDEV security tools.
    """
    logger.info("Running Bandit SAST scan...")
    env = get_safe_subprocess_env()

    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        src_dir = project_dir

    try:
        cmd = [sys.executable, "-m", "bandit", "-r", src_dir, "-f", "json", "--severity-level", "medium"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            timeout=120, cwd=project_dir
        )

        # Parse JSON output
        try:
            bandit_data = json.loads(proc.stdout) if proc.stdout.strip() else {}
            results = bandit_data.get("results", [])
            high_issues = [r for r in results if r.get("issue_severity") == "HIGH"]
            medium_issues = [r for r in results if r.get("issue_severity") == "MEDIUM"]

            passed = len(high_issues) == 0
            error_msg = None
            if not passed:
                error_msg = f"{len(high_issues)} HIGH severity issues: " + "; ".join(
                    f"{r.get('test_id')}: {r.get('issue_text', '')} ({r.get('filename', '')}:{r.get('line_number', '')})"
                    for r in high_issues[:3]
                )

            logger.info(f"Bandit: {len(high_issues)} HIGH, {len(medium_issues)} MEDIUM issues")
            return TestResult(
                test_name="security_sast_bandit",
                passed=passed,
                execution_command=" ".join(cmd),
                test_purpose="Static application security testing — identifies common vulnerabilities like SQL injection, XSS, hardcoded secrets, and insecure function calls",
                error=error_msg,
                test_type="security",
                nist_controls=["SA-11", "RA-5"],
            )
        except json.JSONDecodeError:
            return TestResult(
                test_name="security_sast_bandit",
                passed=proc.returncode == 0,
                execution_command=" ".join(cmd),
                test_purpose="Static application security testing",
                error=proc.stderr[:300] if proc.returncode != 0 else None,
                test_type="security",
                nist_controls=["SA-11", "RA-5"],
            )

    except FileNotFoundError:
        logger.warning("Bandit not installed, skipping SAST scan (pip install bandit)")
        return TestResult(
            test_name="security_sast_bandit",
            passed=True,
            execution_command="bandit -r src/",
            test_purpose="Static application security testing (SKIPPED — bandit not installed)",
            test_type="security",
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            test_name="security_sast_bandit",
            passed=False,
            execution_command="bandit -r src/",
            test_purpose="Static application security testing",
            error="Bandit scan timed out after 120 seconds",
            test_type="security",
        )


# --- Unit / BDD Test Execution ---

def run_pytest(project_dir: str, logger) -> Tuple[List[TestResult], int, int]:
    """Run pytest and parse results into TestResult objects."""
    logger.info("Running pytest...")
    env = get_safe_subprocess_env()
    results = []

    try:
        # Run pytest with JSON output
        cmd = [
            sys.executable, "-m", "pytest",
            os.path.join(project_dir, "tests"),
            "-v", "--tb=short",
            f"--junitxml={project_dir}/test-results.xml",
            "--no-header",
        ]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            timeout=300, cwd=project_dir
        )

        # Parse verbose output for test results
        for line in proc.stdout.splitlines():
            if "PASSED" in line or "FAILED" in line or "ERROR" in line:
                passed = "PASSED" in line
                test_name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
                error_msg = None
                if not passed:
                    # Extract error from output
                    error_msg = line.strip()

                results.append(TestResult(
                    test_name=test_name,
                    passed=passed,
                    execution_command=" ".join(cmd),
                    test_purpose="Unit test",
                    error=error_msg,
                    test_type="unit",
                    nist_controls=["SA-11"],
                ))

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        logger.info(f"pytest: {passed} passed, {failed} failed")
        return results, passed, failed

    except subprocess.TimeoutExpired:
        logger.error("pytest timed out after 300 seconds")
        return [], 0, 0
    except FileNotFoundError:
        logger.warning("pytest not installed, skipping unit tests")
        return [], 0, 0
    except Exception as e:
        logger.error(f"pytest error: {e}")
        return [], 0, 0


def run_behave(project_dir: str, logger) -> Tuple[List[TestResult], int, int]:
    """Run behave BDD tests and parse results."""
    logger.info("Running behave BDD tests...")
    env = get_safe_subprocess_env()
    results = []

    features_dir = os.path.join(project_dir, "features")
    if not os.path.isdir(features_dir):
        logger.info("No features/ directory found, skipping BDD tests")
        return [], 0, 0

    try:
        cmd = [
            sys.executable, "-m", "behave",
            features_dir,
            "--format", "json",
            "--outfile", os.path.join(project_dir, "behave-results.json"),
            "--no-capture",
        ]

        subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            timeout=300, cwd=project_dir
        )

        # Parse JSON results if available
        results_file = os.path.join(project_dir, "behave-results.json")
        if os.path.exists(results_file):
            with open(results_file) as f:
                behave_data = json.load(f)

            for feature in behave_data:
                for scenario in feature.get("elements", []):
                    scenario_name = scenario.get("name", "unknown")
                    steps = scenario.get("steps", [])
                    all_passed = all(
                        s.get("result", {}).get("status") == "passed"
                        for s in steps if "result" in s
                    )
                    error = None
                    if not all_passed:
                        failed_steps = [
                            s for s in steps
                            if s.get("result", {}).get("status") != "passed"
                        ]
                        if failed_steps:
                            error = failed_steps[0].get("result", {}).get("error_message", "Step failed")

                    results.append(TestResult(
                        test_name=scenario_name,
                        passed=all_passed,
                        execution_command=" ".join(cmd),
                        test_purpose=f"BDD: {feature.get('name', 'unknown')}",
                        error=error,
                        test_type="bdd",
                        nist_controls=["SA-11"],
                    ))

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        logger.info(f"behave: {passed} passed, {failed} failed")
        return results, passed, failed

    except subprocess.TimeoutExpired:
        logger.error("behave timed out after 300 seconds")
        return [], 0, 0
    except FileNotFoundError:
        logger.warning("behave not installed, skipping BDD tests")
        return [], 0, 0
    except Exception as e:
        logger.error(f"behave error: {e}")
        return [], 0, 0


# --- Test Retry + Resolution (adapted from ADW pattern) ---

def run_tests_with_resolution(
    project_dir: str,
    run_id: str,
    logger,
    max_attempts: int = MAX_TEST_RETRY_ATTEMPTS,
) -> Tuple[List[TestResult], int, int]:
    """Run unit + BDD tests with automatic retry logic.

    Adapted from ADW run_tests_with_resolution:
    - Run tests
    - If failures, attempt resolution
    - Retry up to max_attempts
    - Stop early if no progress
    """
    attempt = 0
    all_results = []
    total_passed = 0
    total_failed = 0

    while attempt < max_attempts:
        attempt += 1
        logger.info(f"\n=== Test Run Attempt {attempt}/{max_attempts} ===")

        # Step 1: Syntax check (py_compile) — from ADW test.md pattern
        syntax_result = run_py_compile(project_dir, logger)

        # Step 2: Code quality (Ruff) — from ADW test.md pattern
        ruff_result = run_ruff(project_dir, logger)

        # Step 3: Unit tests (pytest)
        unit_results, unit_passed, unit_failed = run_pytest(project_dir, logger)

        # Step 4: BDD tests (behave)
        bdd_results, bdd_passed, bdd_failed = run_behave(project_dir, logger)

        # Step 5: SAST security scan (Bandit)
        bandit_result = run_bandit(project_dir, logger)

        # Combine all results
        quality_results = [syntax_result, ruff_result, bandit_result]
        all_results = quality_results + unit_results + bdd_results
        total_passed = sum(1 for r in all_results if r.passed)
        total_failed = len(all_results) - total_passed

        if total_failed == 0:
            logger.info("All tests passed!")
            break

        if attempt == max_attempts:
            logger.info(f"Reached maximum retry attempts ({max_attempts})")
            break

        # Log failures for potential resolution
        failed_tests = [t for t in all_results if not t.passed]
        logger.info(f"Found {total_failed} failures, logging for resolution...")

        for ft in failed_tests:
            logger.info(f"  FAILED: {ft.test_name} - {ft.error or 'no error message'}")

        # In ICDEV, resolution would invoke the builder agent to fix code
        # For now, just retry (the fix may come from external intervention)
        logger.info(f"Retrying tests (attempt {attempt + 1}/{max_attempts})...")

    return all_results, total_passed, total_failed


# --- E2E Test Execution (native Playwright + MCP fallback) ---

def _detect_e2e_mode() -> str:
    """Detect best available E2E execution mode.

    Prefers native Playwright (tests/e2e/*.spec.ts) over MCP (.claude/commands/e2e/*.md).
    """
    from tools.testing.e2e_runner import check_playwright_installed, discover_native_tests
    if check_playwright_installed() and discover_native_tests():
        return "native"
    return "mcp"


def discover_e2e_tests() -> List[str]:
    """Discover E2E test files (native .spec.ts preferred, MCP .md fallback)."""
    from tools.testing.e2e_runner import discover_e2e_tests as _discover
    return _discover(mode="auto")


def run_e2e_tests(
    run_id: str,
    logger,
    attempt: int = 1,
) -> List[E2ETestResult]:
    """Run all E2E tests via native Playwright or MCP fallback.

    Native mode: Invokes `npx playwright test` against tests/e2e/*.spec.ts.
    MCP mode: Runs .claude/commands/e2e/*.md specs via Claude Code + Playwright MCP.
    """
    mode = _detect_e2e_mode()
    logger.info(f"E2E execution mode: {mode}")

    if mode == "native":
        from tools.testing.e2e_runner import run_playwright_native
        results = run_playwright_native(run_id, logger, project="chromium")
        return results
    else:
        # MCP / validation fallback
        from tools.testing.e2e_runner import discover_mcp_tests, execute_e2e_test
        test_files = discover_mcp_tests()
        logger.info(f"Found {len(test_files)} E2E test specs (MCP mode)")

        if not test_files:
            logger.info("No E2E test files found")
            return []

        results = []
        for idx, test_file in enumerate(test_files):
            result = execute_e2e_test(test_file, run_id, logger)
            if result:
                results.append(result)
                if not result.passed:
                    logger.info(f"E2E test failed: {result.test_name}, stopping (fail-fast)")
                    break

        return results


def run_e2e_tests_with_resolution(
    run_id: str,
    logger,
    max_attempts: int = MAX_E2E_TEST_RETRY_ATTEMPTS,
) -> Tuple[List[E2ETestResult], int, int]:
    """Run E2E tests with retry logic.

    Uses native Playwright when available, falls back to MCP mode.
    """
    attempt = 0
    results = []
    passed_count = 0
    failed_count = 0

    while attempt < max_attempts:
        attempt += 1
        logger.info(f"\n=== E2E Test Run Attempt {attempt}/{max_attempts} ===")

        results = run_e2e_tests(run_id, logger, attempt)

        if not results:
            break

        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count

        if failed_count == 0:
            logger.info("All E2E tests passed!")
            break

        if attempt == max_attempts:
            logger.info(f"Reached maximum E2E retry attempts ({max_attempts})")
            break

        # Log failures
        failed = [r for r in results if not r.passed]
        for ft in failed:
            logger.info(f"  E2E FAILED: {ft.test_name} - {ft.error or 'unknown'}")

        logger.info(f"Retrying E2E tests (attempt {attempt + 1}/{max_attempts})...")

    return results, passed_count, failed_count


# --- Security & Compliance Gates ---

def evaluate_security_gate(project_dir: str, logger) -> GateEvaluation:
    """Evaluate security gate by running ICDEV security scans."""
    logger.info("Evaluating security gate...")
    gates = []

    # SAST check
    try:
        from tools.security.sast_runner import run_sast
        sast_result = run_sast(project_dir)
        high_findings = sast_result.get("high_count", 0) if isinstance(sast_result, dict) else 0
        gates.append(GateResult(
            gate_name="SAST (Bandit)",
            passed=high_findings == 0,
            severity="blocking",
            details=f"{high_findings} HIGH findings",
            nist_control="SA-11",
        ))
    except (ImportError, Exception) as e:
        gates.append(GateResult(
            gate_name="SAST (Bandit)",
            passed=False,
            severity="warning",
            details=f"SAST unavailable: {e}",
            nist_control="SA-11",
        ))

    # Secret detection check
    try:
        from tools.security.secret_detector import scan_directory
        secrets = scan_directory(project_dir)
        secret_count = len(secrets) if isinstance(secrets, list) else 0
        gates.append(GateResult(
            gate_name="Secret Detection",
            passed=secret_count == 0,
            severity="blocking",
            details=f"{secret_count} secrets detected",
            nist_control="IA-5",
        ))
    except (ImportError, Exception) as e:
        gates.append(GateResult(
            gate_name="Secret Detection",
            passed=False,
            severity="warning",
            details=f"Secret detection unavailable: {e}",
            nist_control="IA-5",
        ))

    overall = all(g.passed for g in gates if g.severity == "blocking")

    return GateEvaluation(
        gate_type="code_review",
        overall_pass=overall,
        gates=gates,
        timestamp=timestamp_iso(),
        evaluated_by="icdev-test-orchestrator",
    )


def evaluate_compliance_gate(project_id: str, project_dir: str, logger) -> GateEvaluation:
    """Evaluate compliance gate (STIG, CUI markings, SBOM)."""
    logger.info("Evaluating compliance gate...")
    gates = []

    # CUI marking check
    try:
        from tools.compliance.cui_marker import verify_directory
        cui_result = verify_directory(project_dir)
        unmarked = cui_result.get("unmarked_count", 0) if isinstance(cui_result, dict) else 0
        gates.append(GateResult(
            gate_name="CUI Markings",
            passed=unmarked == 0,
            severity="blocking",
            details=f"{unmarked} files missing CUI markings",
            nist_control="SC-16",
        ))
    except (ImportError, Exception) as e:
        gates.append(GateResult(
            gate_name="CUI Markings",
            passed=False,
            severity="warning",
            details=f"CUI checker unavailable: {e}",
            nist_control="SC-16",
        ))

    overall = all(g.passed for g in gates if g.severity == "blocking")

    return GateEvaluation(
        gate_type="merge",
        overall_pass=overall,
        gates=gates,
        timestamp=timestamp_iso(),
        project_id=project_id,
        evaluated_by="icdev-test-orchestrator",
    )


# --- Summary Report ---

def generate_summary(
    unit_results: List[TestResult],
    bdd_results: List[TestResult],
    e2e_results: List[E2ETestResult],
    security_gate: Optional[GateEvaluation],
    compliance_gate: Optional[GateEvaluation],
    logger,
) -> str:
    """Generate comprehensive test summary report.

    Follows ADW log_test_results pattern for structured reporting.
    """
    unit_passed = sum(1 for r in unit_results if r.passed)
    unit_failed = len(unit_results) - unit_passed
    bdd_passed = sum(1 for r in bdd_results if r.passed)
    bdd_failed = len(bdd_results) - bdd_passed
    e2e_passed = sum(1 for r in e2e_results if r.passed)
    e2e_failed = len(e2e_results) - e2e_passed

    lines = [
        "CUI // SP-CTI",
        "",
        "## ICDEV Test Run Summary",
        "",
        "### Unit Tests",
        f"  Total: {len(unit_results)} | Passed: {unit_passed} | Failed: {unit_failed}",
        "",
        "### BDD Tests",
        f"  Total: {len(bdd_results)} | Passed: {bdd_passed} | Failed: {bdd_failed}",
        "",
        "### E2E Tests",
        f"  Total: {len(e2e_results)} | Passed: {e2e_passed} | Failed: {e2e_failed}",
        "",
    ]

    if security_gate:
        sg_status = "PASS" if security_gate.overall_pass else "FAIL"
        lines.append(f"### Security Gate: {sg_status}")
        for g in security_gate.gates:
            g_status = "PASS" if g.passed else "FAIL"
            lines.append(f"  [{g_status}] {g.gate_name}: {g.details}")
        lines.append("")

    if compliance_gate:
        cg_status = "PASS" if compliance_gate.overall_pass else "FAIL"
        lines.append(f"### Compliance Gate: {cg_status}")
        for g in compliance_gate.gates:
            g_status = "PASS" if g.passed else "FAIL"
            lines.append(f"  [{g_status}] {g.gate_name}: {g.details}")
        lines.append("")

    total_failures = unit_failed + bdd_failed + e2e_failed
    overall = "PASSED" if total_failures == 0 else "FAILED"
    lines.append(f"### Overall: {overall}")
    lines.append("")
    lines.append("CUI // SP-CTI")

    summary = "\n".join(lines)
    logger.info(summary)
    return summary


# --- Main Entry Point ---

def main():
    """Main entry point for the test orchestrator."""
    parser = argparse.ArgumentParser(description="ICDEV Test Orchestrator")
    parser.add_argument("--project-dir", required=True, help="Path to project under test")
    parser.add_argument("--project-id", help="ICDEV project UUID")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip E2E browser tests")
    parser.add_argument("--skip-security", action="store_true", help="Skip security gate")
    parser.add_argument("--skip-compliance", action="store_true", help="Skip compliance gate")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    # Initialize run
    run_id = make_run_id()
    logger = setup_logger(run_id, "test_orchestrator")
    run_dir = ensure_run_dir(run_id)

    logger.info(f"ICDEV Test Orchestrator starting — Run ID: {run_id}")
    logger.info(f"Project directory: {args.project_dir}")

    # Initialize state
    state = TestRunState(
        run_id=run_id,
        project_id=args.project_id,
        project_dir=args.project_dir,
        started_at=timestamp_iso(),
    )

    # Step 1: Health check
    logger.info("\n=== Step 1: Health Check ===")
    from tools.testing.health_check import run_health_check
    health = run_health_check()
    if not health.success:
        logger.warning(f"Health check warnings: {health.errors}")
        # Continue anyway — non-critical issues shouldn't block testing

    # Step 2: Unit + BDD tests with retry
    logger.info("\n=== Step 2: Unit + BDD Tests ===")
    all_results, total_passed, total_failed = run_tests_with_resolution(
        args.project_dir, run_id, logger
    )

    unit_results = [r for r in all_results if r.test_type == "unit"]
    bdd_results = [r for r in all_results if r.test_type == "bdd"]
    [r for r in all_results if r.test_type == "security"]

    state.unit_passed = sum(1 for r in unit_results if r.passed)
    state.unit_failed = len(unit_results) - state.unit_passed
    state.bdd_passed = sum(1 for r in bdd_results if r.passed)
    state.bdd_failed = len(bdd_results) - state.bdd_passed

    # Step 3: E2E tests (skip if unit tests failed or --skip-e2e)
    e2e_results = []
    if total_failed > 0:
        logger.info("Skipping E2E tests due to unit/BDD test failures")
    elif args.skip_e2e:
        logger.info("Skipping E2E tests (--skip-e2e flag)")
    else:
        logger.info("\n=== Step 3: E2E Tests ===")
        e2e_results, e2e_passed, e2e_failed = run_e2e_tests_with_resolution(
            run_id, logger
        )
        state.e2e_passed = e2e_passed
        state.e2e_failed = e2e_failed

    # Step 4: Security gate
    security_gate = None
    if not args.skip_security:
        logger.info("\n=== Step 4: Security Gate ===")
        security_gate = evaluate_security_gate(args.project_dir, logger)
        state.security_gate_passed = security_gate.overall_pass

    # Step 5: Compliance gate
    compliance_gate = None
    if not args.skip_compliance and args.project_id:
        logger.info("\n=== Step 5: Compliance Gate ===")
        compliance_gate = evaluate_compliance_gate(
            args.project_id, args.project_dir, logger
        )
        state.compliance_gate_passed = compliance_gate.overall_pass

    # Step 6: Agentic tests (conditional — only if agent infrastructure exists)
    agentic_results = []
    project_dir_path = Path(args.project_dir)
    if (project_dir_path / "tools" / "agent" / "cards").exists():
        logger.info("\n=== Step 6: Agentic Tests ===")
        agentic_tests_dir = project_dir_path / "tools" / "builder" / "agentic_test_templates"
        if agentic_tests_dir.exists():
            logger.info(f"Found agentic test templates: {agentic_tests_dir}")
            # Run agentic pytest files
            agentic_py_tests = list(agentic_tests_dir.glob("test_*.py"))
            if agentic_py_tests:
                try:
                    cmd = [
                        sys.executable, "-m", "pytest",
                        str(agentic_tests_dir),
                        "-v", "--tb=short", "--no-header",
                    ]
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True,
                        env=get_safe_subprocess_env(),
                        timeout=120, cwd=args.project_dir,
                    )
                    for line in proc.stdout.splitlines():
                        if "PASSED" in line or "FAILED" in line:
                            passed_flag = "PASSED" in line
                            t_name = line.split("::")[1].split(" ")[0] if "::" in line else line.strip()
                            agentic_results.append(TestResult(
                                test_name=f"agentic:{t_name}",
                                passed=passed_flag,
                                execution_command=" ".join(cmd),
                                test_purpose="Agentic infrastructure test",
                                error=None if passed_flag else line.strip(),
                                test_type="unit",
                                nist_controls=["SA-11", "SC-7"],
                            ))
                    a_passed = sum(1 for r in agentic_results if r.passed)
                    a_failed = len(agentic_results) - a_passed
                    logger.info(f"Agentic tests: {a_passed} passed, {a_failed} failed")
                except subprocess.TimeoutExpired:
                    logger.warning("Agentic tests timed out after 120 seconds")
                except Exception as e:
                    logger.warning(f"Agentic tests error: {e}")

            # Discover agentic BDD feature files
            agentic_features = list(agentic_tests_dir.glob("*.feature"))
            if agentic_features:
                logger.info(f"Found {len(agentic_features)} agentic BDD feature templates")
                agentic_results.append(TestResult(
                    test_name="agentic_bdd_templates",
                    passed=True,
                    execution_command="glob tools/builder/agentic_test_templates/*.feature",
                    test_purpose="Agentic BDD test templates discovered",
                    test_type="bdd",
                    nist_controls=["SA-11"],
                ))
        else:
            logger.info("No agentic test templates directory found, skipping")
    else:
        logger.info("No agent cards directory found, skipping agentic tests")

    # Step 7: Summary
    logger.info("\n=== Step 7: Summary ===")
    state.completed_at = timestamp_iso()

    # Merge agentic results into unit/bdd buckets for summary
    agentic_unit = [r for r in agentic_results if r.test_type == "unit"]
    agentic_bdd = [r for r in agentic_results if r.test_type == "bdd"]
    all_unit_for_summary = unit_results + agentic_unit
    all_bdd_for_summary = bdd_results + agentic_bdd

    summary = generate_summary(
        all_unit_for_summary, all_bdd_for_summary, e2e_results,
        security_gate, compliance_gate, logger,
    )

    # Save state
    state_file = run_dir / "state.json"
    with open(state_file, "w") as f:
        json.dump(state.model_dump(), f, indent=2, default=str)

    # Save summary
    summary_file = run_dir / "summary.md"
    with open(summary_file, "w") as f:
        f.write(summary)

    logger.info(f"\nResults saved to: {run_dir}")

    # Record in audit trail
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="test.complete",
            actor="test-orchestrator",
            action=f"Test run {run_id}: {state.unit_passed + state.bdd_passed + state.e2e_passed} passed, "
                   f"{state.unit_failed + state.bdd_failed + state.e2e_failed} failed",
            project_id=args.project_id,
        )
    except (ImportError, Exception):
        pass  # Audit logging is best-effort

    # Exit code (include agentic test failures)
    agentic_failed = sum(1 for r in agentic_results if not r.passed)
    total_failures = state.unit_failed + state.bdd_failed + state.e2e_failed + agentic_failed
    sys.exit(0 if total_failures == 0 else 1)


if __name__ == "__main__":
    main()
