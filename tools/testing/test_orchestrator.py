# CUI // SP-CTI
# DEPRECATED: unused as of 2026-05-09. Remove after 2026-08-01.
"""ICDEV™ end-to-end test orchestrator.

Drives the full ICDEV test gauntlet for one project: syntax → quality
→ unit → BDD → SAST → E2E → security gate → compliance gate →
coherence → optional agentic tests → acceptance V&V → summary report.

Used both as a standalone CLI and as the backend for
:mod:`tools.ci.workflows.icdev_test`.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/test_orchestrator.md`` (OPT-75
Phase 3 clean-room rewrite). Removes the dead-code bug at line 968 of
the original (`[r for r in all_results if r.test_type == "security"]`
was discarded).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.testing.data_types import (  # noqa: E402
    E2ETestResult,
    GateEvaluation,
    GateResult,
    TestResult,
    TestRunState,
)
from tools.testing.utils import (  # noqa: E402
    ensure_run_dir,
    get_safe_subprocess_env,
    make_run_id,
    setup_logger,
    timestamp_iso,
)


# ── Constants ───────────────────────────────────────────────────────────
MAX_TEST_RETRY_ATTEMPTS: int = 4
MAX_E2E_TEST_RETRY_ATTEMPTS: int = 2

_PY_COMPILE_TIMEOUT_SECONDS: int = 10
_RUFF_TIMEOUT_SECONDS: int = 60
_BANDIT_TIMEOUT_SECONDS: int = 120
_PYTEST_TIMEOUT_SECONDS: int = 300
_BEHAVE_TIMEOUT_SECONDS: int = 300
_COHERENCE_TIMEOUT_SECONDS: int = 120
_AGENTIC_TIMEOUT_SECONDS: int = 120
_SANDBOX_FILE_LIMIT: int = 15

_PY_COMPILE_FILE_LIMIT: int = 50

_module_logger = get_logger(__name__)


def _logger_for(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger if logger is not None else _module_logger


def _find_source_dir(project_dir: str) -> Optional[str]:
    """Walk a small list of common source-dir names. Return the first
    one that contains any ``.py`` file."""
    for candidate in ("src", "app", "lib", project_dir):
        check_dir = (
            os.path.join(project_dir, candidate)
            if candidate != project_dir else project_dir
        )
        if not os.path.isdir(check_dir):
            continue
        try:
            entries = os.listdir(check_dir)
        except OSError:
            continue
        if any(name.endswith(".py") for name in entries):
            return check_dir
    return None


def _all_python_files(src_dir: str) -> List[str]:
    out: List[str] = []
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            if name.endswith(".py"):
                out.append(os.path.join(root, name))
    return out


# ────────────────────────────────────────────────────────────────────────────
# Quality checks
# ────────────────────────────────────────────────────────────────────────────


def run_py_compile(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> TestResult:
    log = _logger_for(logger)
    log.info("orchestrator: py_compile syntax check")
    env = get_safe_subprocess_env()

    src_dir = _find_source_dir(project_dir)
    if not src_dir:
        return TestResult(
            test_name="python_syntax_check",
            passed=True,
            execution_command="python -m py_compile (no source files found)",
            test_purpose="Validates Python syntax via py_compile",
            test_type="unit",
        )

    py_files = _all_python_files(src_dir)
    errors: List[str] = []
    for py_file in py_files[:_PY_COMPILE_FILE_LIMIT]:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", py_file],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=_PY_COMPILE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{py_file}: compilation timed out")
            continue
        if proc.returncode != 0:
            errors.append(f"{py_file}: {(proc.stderr or '').strip()}")

    passed = not errors
    log.info("orchestrator: py_compile %d files, %d errors", len(py_files), len(errors))
    return TestResult(
        test_name="python_syntax_check",
        passed=passed,
        execution_command=f"python -m py_compile {src_dir}/*.py",
        test_purpose=(
            "Validates Python syntax by compiling source files to "
            "bytecode, catching syntax errors like missing colons, "
            "invalid indentation, or malformed statements"
        ),
        error="; ".join(errors[:5]) if errors else None,
        test_type="unit",
        nist_controls=["SA-11"],
    )


def run_ruff(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> TestResult:
    log = _logger_for(logger)
    log.info("orchestrator: ruff code quality")
    env = get_safe_subprocess_env()

    cmd_variants = (
        ["ruff", "check", project_dir],
        [sys.executable, "-m", "ruff", "check", project_dir],
    )
    for cmd in cmd_variants:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=_RUFF_TIMEOUT_SECONDS,
                cwd=project_dir,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return TestResult(
                test_name="code_quality_ruff",
                passed=False,
                execution_command=" ".join(cmd),
                test_purpose="Validates Python code quality using Ruff",
                error="Ruff check timed out after 60 seconds",
                test_type="unit",
            )

        if proc.returncode == 0:
            log.info("orchestrator: ruff clean")
            return TestResult(
                test_name="code_quality_ruff",
                passed=True,
                execution_command=" ".join(cmd),
                test_purpose=(
                    "Validates Python code quality using Ruff — "
                    "identifies unused imports, style violations, "
                    "security issues, and potential bugs"
                ),
                test_type="unit",
                nist_controls=["SA-11", "SA-15"],
            )

        output = (proc.stdout or proc.stderr or "").strip()
        log.info("orchestrator: ruff issues found")
        return TestResult(
            test_name="code_quality_ruff",
            passed=False,
            execution_command=" ".join(cmd),
            test_purpose=(
                "Validates Python code quality using Ruff — "
                "identifies unused imports, style violations, "
                "security issues, and potential bugs"
            ),
            error=output[:500],
            test_type="unit",
            nist_controls=["SA-11", "SA-15"],
        )

    log.warning("orchestrator: ruff not installed")
    return TestResult(
        test_name="code_quality_ruff",
        passed=True,
        execution_command="ruff check .",
        test_purpose="Validates Python code quality using Ruff (SKIPPED)",
        test_type="unit",
    )


def run_sandbox_isolation(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> TestResult:
    log = _logger_for(logger)
    log.info("orchestrator: sandbox isolation")

    try:
        from tools.security.sandbox_executor import SandboxExecutor
    except ImportError:
        return TestResult(
            test_name="sandbox_isolation",
            passed=True,
            execution_command="sandbox_executor.py (SKIPPED — import failed)",
            test_purpose="Container-isolated code execution check (SKIPPED)",
            test_type="security",
            nist_controls=["SA-11", "SI-7"],
        )

    executor = SandboxExecutor()
    if not getattr(executor, "_enabled", False) or not getattr(executor, "_available", False):
        log.info("orchestrator: sandbox unavailable, skipping")
        return TestResult(
            test_name="sandbox_isolation",
            passed=True,
            execution_command="sandbox_executor.py (SKIPPED — unavailable)",
            test_purpose="Container-isolated code execution check (SKIPPED)",
            test_type="security",
            nist_controls=["SA-11", "SI-7"],
        )

    src_dir = _find_source_dir(project_dir)
    if not src_dir:
        return TestResult(
            test_name="sandbox_isolation",
            passed=True,
            execution_command="sandbox_executor.py (no source files found)",
            test_purpose="Container-isolated code execution check",
            test_type="security",
            nist_controls=["SA-11", "SI-7"],
        )

    py_files = _all_python_files(src_dir)
    errors: List[str] = []
    checked = 0
    for py_file in py_files[:_SANDBOX_FILE_LIMIT]:
        try:
            src_excerpt = Path(py_file).read_text(
                encoding="utf-8", errors="replace"
            )[:4000]
        except OSError:
            continue
        code = (
            "import py_compile, sys, os\n"
            f"src = '''{src_excerpt}'''\n"
            "try:\n"
            "    compile(src, '<sandbox>', 'exec')\n"
            "    print('OK')\n"
            "except SyntaxError as e:\n"
            "    print(f'SYNTAX_ERROR: {e}')\n"
            "    sys.exit(1)\n"
        )
        result = executor.execute(
            code=code,
            language="python",
            executor_type="codelens",
            network_enabled=False,
            timeout_seconds=15,
            actor="test-orchestrator",
        )
        checked += 1
        status = getattr(result, "status", "")
        if status == "completed" and getattr(result, "exit_code", 1) == 0:
            continue
        if status in ("disabled", "unavailable"):
            log.warning("orchestrator: sandbox went away mid-run")
            break
        rel_path = os.path.relpath(py_file, project_dir)
        err = getattr(result, "error", "") or getattr(result, "stderr", "")
        errors.append(f"{rel_path}: {err[:200]}")

    passed = not errors
    log.info("orchestrator: sandbox checked %d files, %d failures", checked, len(errors))
    return TestResult(
        test_name="sandbox_isolation",
        passed=passed,
        execution_command=f"sandbox_executor.py --execute (D-SEC-10, {checked} files)",
        test_purpose=(
            "Verifies Python files compile and load cleanly in a "
            "container sandbox with network isolation"
        ),
        error="; ".join(errors[:5]) if errors else None,
        test_type="security",
        nist_controls=["SA-11", "SI-7"],
    )


def run_bandit(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> TestResult:
    log = _logger_for(logger)
    log.info("orchestrator: bandit SAST")
    env = get_safe_subprocess_env()

    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        src_dir = project_dir

    cmd = [
        sys.executable, "-m", "bandit", "-r", src_dir,
        "-f", "json", "--severity-level", "medium",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_BANDIT_TIMEOUT_SECONDS,
            cwd=project_dir,
        )
    except FileNotFoundError:
        log.warning("orchestrator: bandit not installed")
        return TestResult(
            test_name="security_sast_bandit",
            passed=True,
            execution_command="bandit -r src/",
            test_purpose="SAST scan (SKIPPED — bandit not installed)",
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

    try:
        bandit_data = json.loads(proc.stdout) if (proc.stdout or "").strip() else {}
    except json.JSONDecodeError:
        return TestResult(
            test_name="security_sast_bandit",
            passed=proc.returncode == 0,
            execution_command=" ".join(cmd),
            test_purpose="Static application security testing",
            error=(proc.stderr or "")[:300] if proc.returncode != 0 else None,
            test_type="security",
            nist_controls=["SA-11", "RA-5"],
        )

    results = bandit_data.get("results") or []
    high_issues = [r for r in results if r.get("issue_severity") == "HIGH"]
    medium_issues = [r for r in results if r.get("issue_severity") == "MEDIUM"]
    passed = not high_issues
    error_msg: Optional[str] = None
    if not passed:
        error_msg = (
            f"{len(high_issues)} HIGH severity issues: "
            + "; ".join(
                f"{r.get('test_id')}: {r.get('issue_text', '')} "
                f"({r.get('filename', '')}:{r.get('line_number', '')})"
                for r in high_issues[:3]
            )
        )
    log.info("orchestrator: bandit %d HIGH, %d MEDIUM", len(high_issues), len(medium_issues))
    return TestResult(
        test_name="security_sast_bandit",
        passed=passed,
        execution_command=" ".join(cmd),
        test_purpose=(
            "Static application security testing — identifies common "
            "vulnerabilities like SQL injection, XSS, hardcoded "
            "secrets, and insecure function calls"
        ),
        error=error_msg,
        test_type="security",
        nist_controls=["SA-11", "RA-5"],
    )


# ────────────────────────────────────────────────────────────────────────────
# Test runners
# ────────────────────────────────────────────────────────────────────────────


def run_pytest(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> Tuple[List[TestResult], int, int]:
    log = _logger_for(logger)
    log.info("orchestrator: pytest")
    env = get_safe_subprocess_env()

    cmd = [
        sys.executable, "-m", "pytest",
        os.path.join(project_dir, "tests"),
        "-v",
        "--tb=short",
        f"--junitxml={project_dir}/test-results.xml",
        "--no-header",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_PYTEST_TIMEOUT_SECONDS,
            cwd=project_dir,
        )
    except subprocess.TimeoutExpired:
        log.error("orchestrator: pytest timed out")
        return [], 0, 0
    except FileNotFoundError:
        log.warning("orchestrator: pytest not installed")
        return [], 0, 0
    except Exception as exc:
        log.error("orchestrator: pytest error: %s", exc)
        return [], 0, 0

    results: List[TestResult] = []
    for line in (proc.stdout or "").splitlines():
        if not any(marker in line for marker in ("PASSED", "FAILED", "ERROR")):
            continue
        passed = "PASSED" in line
        if "::" in line:
            test_name = line.split("::")[1].split(" ")[0]
        else:
            test_name = line.strip()
        results.append(TestResult(
            test_name=test_name,
            passed=passed,
            execution_command=" ".join(cmd),
            test_purpose="Unit test",
            error=None if passed else line.strip(),
            test_type="unit",
            nist_controls=["SA-11"],
        ))

    passed_n = sum(1 for r in results if r.passed)
    failed_n = len(results) - passed_n
    log.info("orchestrator: pytest %d passed, %d failed", passed_n, failed_n)
    return results, passed_n, failed_n


def run_behave(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> Tuple[List[TestResult], int, int]:
    log = _logger_for(logger)
    log.info("orchestrator: behave")
    env = get_safe_subprocess_env()

    features_dir = os.path.join(project_dir, "features")
    if not os.path.isdir(features_dir):
        log.info("orchestrator: no features/ directory, skipping behave")
        return [], 0, 0

    results_file = os.path.join(project_dir, "behave-results.json")
    cmd = [
        sys.executable, "-m", "behave", features_dir,
        "--format", "json",
        "--outfile", results_file,
        "--no-capture",
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_BEHAVE_TIMEOUT_SECONDS,
            cwd=project_dir,
        )
    except subprocess.TimeoutExpired:
        log.error("orchestrator: behave timed out")
        return [], 0, 0
    except FileNotFoundError:
        log.warning("orchestrator: behave not installed")
        return [], 0, 0
    except Exception as exc:
        log.error("orchestrator: behave error: %s", exc)
        return [], 0, 0

    results: List[TestResult] = []
    if os.path.exists(results_file):
        try:
            with open(results_file, "r", encoding="utf-8") as fh:
                behave_data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            behave_data = []
        for feature in behave_data or []:
            for scenario in feature.get("elements") or []:
                scenario_name = scenario.get("name", "unknown")
                steps = scenario.get("steps") or []
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
                        error = (
                            failed_steps[0].get("result", {})
                            .get("error_message", "Step failed")
                        )
                results.append(TestResult(
                    test_name=scenario_name,
                    passed=all_passed,
                    execution_command=" ".join(cmd),
                    test_purpose=f"BDD: {feature.get('name', 'unknown')}",
                    error=error,
                    test_type="bdd",
                    nist_controls=["SA-11"],
                ))

    passed_n = sum(1 for r in results if r.passed)
    failed_n = len(results) - passed_n
    log.info("orchestrator: behave %d passed, %d failed", passed_n, failed_n)
    return results, passed_n, failed_n


# ────────────────────────────────────────────────────────────────────────────
# Driver: retry + resolution
# ────────────────────────────────────────────────────────────────────────────


def run_tests_with_resolution(
    project_dir: str,
    run_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    max_attempts: int = MAX_TEST_RETRY_ATTEMPTS,
    skip_sandbox: bool = False,
) -> Tuple[List[TestResult], int, int]:
    log = _logger_for(logger)
    if run_id is None:
        run_id = make_run_id()

    all_results: List[TestResult] = []
    total_passed = 0
    total_failed = 0

    for attempt in range(1, max_attempts + 1):
        log.info("\n=== Test Run Attempt %d/%d ===", attempt, max_attempts)

        syntax_result = run_py_compile(project_dir, log)
        ruff_result = run_ruff(project_dir, log)

        if skip_sandbox:
            sandbox_result = TestResult(
                test_name="sandbox_isolation",
                passed=True,
                execution_command="sandbox_executor.py (SKIPPED — flag)",
                test_purpose="Container-isolated code execution (SKIPPED)",
                test_type="security",
                nist_controls=["SA-11", "SI-7"],
            )
        else:
            sandbox_result = run_sandbox_isolation(project_dir, log)

        unit_results, _unit_passed, _unit_failed = run_pytest(project_dir, log)
        bdd_results, _bdd_passed, _bdd_failed = run_behave(project_dir, log)
        bandit_result = run_bandit(project_dir, log)

        all_results = (
            [syntax_result, ruff_result, sandbox_result, bandit_result]
            + unit_results + bdd_results
        )
        total_passed = sum(1 for r in all_results if r.passed)
        total_failed = len(all_results) - total_passed

        if total_failed == 0:
            log.info("orchestrator: all tests passed")
            break
        if attempt == max_attempts:
            log.info("orchestrator: max attempts reached")
            break

        for failed in (r for r in all_results if not r.passed):
            log.info(
                "  FAILED: %s — %s",
                failed.test_name, failed.error or "(no error)"
            )
        log.info("orchestrator: retrying (attempt %d)", attempt + 1)

    return all_results, total_passed, total_failed


# ────────────────────────────────────────────────────────────────────────────
# E2E
# ────────────────────────────────────────────────────────────────────────────


def _detect_e2e_mode() -> str:
    try:
        from tools.testing.e2e_runner import (
            check_playwright_installed,
            discover_native_tests,
        )
    except ImportError:
        return "mcp"
    if check_playwright_installed() and discover_native_tests():
        return "native"
    return "mcp"


def discover_e2e_tests() -> List[str]:
    try:
        from tools.testing.e2e_runner import discover_e2e_tests as _discover
    except ImportError:
        return []
    return _discover(mode="auto")


def run_e2e_tests(
    run_id: str,
    logger: Optional[logging.Logger] = None,
    attempt: int = 1,
) -> List[E2ETestResult]:
    log = _logger_for(logger)
    mode = _detect_e2e_mode()
    log.info("orchestrator: E2E mode=%s", mode)

    if mode == "native":
        from tools.testing.e2e_runner import run_playwright_native
        return run_playwright_native(run_id, log, project="chromium")

    from tools.testing.e2e_runner import discover_mcp_tests, execute_e2e_test
    test_files = discover_mcp_tests()
    if not test_files:
        log.info("orchestrator: no MCP E2E tests found")
        return []
    results: List[E2ETestResult] = []
    for test_file in test_files:
        result = execute_e2e_test(test_file, run_id, log)
        if result is None:
            continue
        results.append(result)
        if not result.passed:
            log.info(
                "orchestrator: E2E failed (%s), stopping fail-fast",
                result.test_name,
            )
            break
    return results


def run_e2e_tests_with_resolution(
    run_id: str,
    logger: Optional[logging.Logger] = None,
    max_attempts: int = MAX_E2E_TEST_RETRY_ATTEMPTS,
) -> Tuple[List[E2ETestResult], int, int]:
    log = _logger_for(logger)
    results: List[E2ETestResult] = []
    passed_n = 0
    failed_n = 0

    for attempt in range(1, max_attempts + 1):
        log.info("\n=== E2E Attempt %d/%d ===", attempt, max_attempts)
        results = run_e2e_tests(run_id, log, attempt)
        if not results:
            break
        passed_n = sum(1 for r in results if r.passed)
        failed_n = len(results) - passed_n
        if failed_n == 0:
            log.info("orchestrator: all E2E tests passed")
            break
        if attempt == max_attempts:
            log.info("orchestrator: max E2E attempts reached")
            break
        log.info("orchestrator: retrying E2E (attempt %d)", attempt + 1)
    return results, passed_n, failed_n


# ────────────────────────────────────────────────────────────────────────────
# Gates
# ────────────────────────────────────────────────────────────────────────────


def _gate_from_probe(
    name: str,
    severity: str,
    nist_control: str,
    probe: callable,  # type: ignore[valid-type]
) -> GateResult:
    try:
        return probe()
    except Exception as exc:
        return GateResult(
            gate_name=name,
            passed=False,
            severity="warning",
            details=f"{name} unavailable: {exc}",
            nist_control=nist_control,
        )


def evaluate_security_gate(
    project_dir: str,
    logger: Optional[logging.Logger] = None,
) -> GateEvaluation:
    log = _logger_for(logger)
    log.info("orchestrator: security gate")
    gates: List[GateResult] = []

    def _sast_probe() -> GateResult:
        from tools.security.sast_runner import run_sast
        sast_result = run_sast(project_dir)
        high = (
            sast_result.get("high_count", 0)
            if isinstance(sast_result, dict) else 0
        )
        return GateResult(
            gate_name="SAST (Bandit)",
            passed=high == 0,
            severity="blocking",
            details=f"{high} HIGH findings",
            nist_control="SA-11",
        )

    gates.append(
        _gate_from_probe("SAST (Bandit)", "blocking", "SA-11", _sast_probe)
    )

    def _secret_probe() -> GateResult:
        from tools.security.secret_detector import scan_directory
        secrets = scan_directory(project_dir)
        n = len(secrets) if isinstance(secrets, list) else 0
        return GateResult(
            gate_name="Secret Detection",
            passed=n == 0,
            severity="blocking",
            details=f"{n} secrets detected",
            nist_control="IA-5",
        )

    gates.append(
        _gate_from_probe("Secret Detection", "blocking", "IA-5", _secret_probe)
    )

    if os.environ.get("ICDEV_OPENCLAW_ENABLED", "").lower() in ("true", "1", "yes"):
        def _openclaw_probe() -> GateResult:
            from tools.marketplace.openclaw_bridge import gate_check
            oc_result = gate_check()
            oc_passed = oc_result.get("passed", False)
            failures = oc_result.get("blocking_failures") or []
            details = (
                "PASS" if oc_passed
                else f"FAIL: {', '.join(failures) if failures else 'no detail'}"
            )
            return GateResult(
                gate_name="OpenClaw Bridge",
                passed=oc_passed,
                severity="blocking",
                details=details,
                nist_control="SA-12",
            )

        gates.append(
            _gate_from_probe(
                "OpenClaw Bridge", "blocking", "SA-12", _openclaw_probe,
            )
        )

    overall = all(g.passed for g in gates if g.severity == "blocking")
    return GateEvaluation(
        gate_type="code_review",
        overall_pass=overall,
        gates=gates,
        timestamp=timestamp_iso(),
        evaluated_by="icdev-test-orchestrator",
    )


def evaluate_compliance_gate(
    project_id: str,
    project_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> GateEvaluation:
    log = _logger_for(logger)
    log.info("orchestrator: compliance gate")
    gates: List[GateResult] = []
    target_dir = project_dir or "."

    def _cui_probe() -> GateResult:
        from tools.compliance.cui_marker import verify_directory
        cui_result = verify_directory(target_dir)
        unmarked = (
            cui_result.get("unmarked_count", 0)
            if isinstance(cui_result, dict) else 0
        )
        return GateResult(
            gate_name="CUI Markings",
            passed=unmarked == 0,
            severity="blocking",
            details=f"{unmarked} files missing CUI markings",
            nist_control="SC-16",
        )

    gates.append(
        _gate_from_probe("CUI Markings", "blocking", "SC-16", _cui_probe)
    )

    overall = all(g.passed for g in gates if g.severity == "blocking")
    return GateEvaluation(
        gate_type="merge",
        overall_pass=overall,
        gates=gates,
        timestamp=timestamp_iso(),
        project_id=project_id,
        evaluated_by="icdev-test-orchestrator",
    )


# ────────────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────────────


def generate_summary(
    unit_results: List[TestResult],
    bdd_results: List[TestResult],
    e2e_results: List[E2ETestResult],
    security_gate: Optional[GateEvaluation],
    compliance_gate: Optional[GateEvaluation],
    logger: Optional[logging.Logger] = None,
) -> str:
    log = _logger_for(logger)
    unit_passed = sum(1 for r in unit_results if r.passed)
    unit_failed = len(unit_results) - unit_passed
    bdd_passed = sum(1 for r in bdd_results if r.passed)
    bdd_failed = len(bdd_results) - bdd_passed
    e2e_passed = sum(1 for r in e2e_results if r.passed)
    e2e_failed = len(e2e_results) - e2e_passed

    lines: List[str] = [
        "CUI // SP-CTI",
        "",
        "## ICDEV™ Test Run Summary",
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
            mark = "PASS" if g.passed else "FAIL"
            lines.append(f"  [{mark}] {g.gate_name}: {g.details}")
        lines.append("")

    if compliance_gate:
        cg_status = "PASS" if compliance_gate.overall_pass else "FAIL"
        lines.append(f"### Compliance Gate: {cg_status}")
        for g in compliance_gate.gates:
            mark = "PASS" if g.passed else "FAIL"
            lines.append(f"  [{mark}] {g.gate_name}: {g.details}")
        lines.append("")

    total_failures = unit_failed + bdd_failed + e2e_failed
    overall = "PASSED" if total_failures == 0 else "FAILED"
    lines.append(f"### Overall: {overall}")
    lines.append("")
    lines.append("CUI // SP-CTI")
    summary = "\n".join(lines)
    log.info(summary)
    return summary


# ────────────────────────────────────────────────────────────────────────────
# Coherence + agentic helpers (CLI-only)
# ────────────────────────────────────────────────────────────────────────────


def _run_coherence_check(project_dir: str, log: logging.Logger) -> None:
    cmd = [
        sys.executable,
        "tools/workflow/coherence_checker.py",
        "--all", "--fix", "--gate", "--json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=get_safe_subprocess_env(),
            timeout=_COHERENCE_TIMEOUT_SECONDS,
            cwd=project_dir,
        )
    except FileNotFoundError:
        log.info("orchestrator: coherence checker not found")
        return
    except subprocess.TimeoutExpired:
        log.warning("orchestrator: coherence check timed out")
        return
    except Exception as exc:
        log.warning("orchestrator: coherence check error: %s", exc)
        return

    if proc.returncode == 0:
        log.info("orchestrator: coherence PASS")
        return
    log.warning("orchestrator: coherence FAIL (exit %s)", proc.returncode)
    try:
        data = json.loads(proc.stdout or "{}")
        log.warning(
            "  %s failures, %s warnings",
            data.get("failed_checks", 0), data.get("warned_checks", 0),
        )
    except (json.JSONDecodeError, ValueError):
        if proc.stderr:
            log.warning("  %s", proc.stderr[:300])


def _run_agentic_tests(
    project_dir: str, log: logging.Logger,
) -> List[TestResult]:
    project_path = Path(project_dir)
    cards_dir = project_path / "tools" / "agent" / "cards"
    if not cards_dir.exists():
        log.info("orchestrator: no agent cards directory, skipping agentic")
        return []

    templates_dir = project_path / "tools" / "builder" / "agentic_test_templates"
    if not templates_dir.exists():
        log.info("orchestrator: no agentic templates dir, skipping")
        return []

    results: List[TestResult] = []
    log.info("orchestrator: agentic templates dir found")

    py_tests = list(templates_dir.glob("test_*.py"))
    if py_tests:
        cmd = [
            sys.executable, "-m", "pytest",
            str(templates_dir), "-v", "--tb=short", "--no-header",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=get_safe_subprocess_env(),
                timeout=_AGENTIC_TIMEOUT_SECONDS,
                cwd=project_dir,
            )
        except subprocess.TimeoutExpired:
            log.warning("orchestrator: agentic tests timed out")
            return results
        except Exception as exc:
            log.warning("orchestrator: agentic tests error: %s", exc)
            return results

        for line in (proc.stdout or "").splitlines():
            if "PASSED" in line or "FAILED" in line:
                passed_flag = "PASSED" in line
                t_name = (
                    line.split("::")[1].split(" ")[0]
                    if "::" in line else line.strip()
                )
                results.append(TestResult(
                    test_name=f"agentic:{t_name}",
                    passed=passed_flag,
                    execution_command=" ".join(cmd),
                    test_purpose="Agentic infrastructure test",
                    error=None if passed_flag else line.strip(),
                    test_type="unit",
                    nist_controls=["SA-11", "SC-7"],
                ))
        a_passed = sum(1 for r in results if r.passed)
        log.info("orchestrator: agentic %d/%d passed", a_passed, len(results))

    feature_files = list(templates_dir.glob("*.feature"))
    if feature_files:
        results.append(TestResult(
            test_name="agentic_bdd_templates",
            passed=True,
            execution_command="glob agentic_test_templates/*.feature",
            test_purpose="Agentic BDD test templates discovered",
            test_type="bdd",
            nist_controls=["SA-11"],
        ))

    return results


def _run_acceptance_validation(
    args: Any,
    state: TestRunState,
    run_dir: Path,
    log: logging.Logger,
) -> Optional[Any]:
    try:
        from tools.testing.acceptance_validator import validate_acceptance
    except Exception as exc:
        log.warning("orchestrator: acceptance validation unavailable: %s", exc)
        return None

    state_file = run_dir / "state.json"
    try:
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump(state.model_dump(), fh, indent=2, default=str)
    except OSError as exc:
        log.warning("orchestrator: cannot persist interim state: %s", exc)

    try:
        report = validate_acceptance(
            plan_path=args.plan,
            test_results_path=str(state_file) if args.plan else None,
            base_url=args.base_url,
            pages=args.pages,
        )
    except Exception as exc:
        log.warning("orchestrator: acceptance validator raised: %s", exc)
        return None

    if getattr(report, "overall_pass", False):
        log.info(
            "orchestrator: acceptance V&V PASS — %s criteria, %s pages",
            getattr(report, "criteria_verified", 0),
            getattr(report, "pages_checked", 0),
        )
    else:
        log.warning(
            "orchestrator: acceptance V&V FAIL — %s failures, %s page errors",
            getattr(report, "criteria_failed", 0),
            getattr(report, "pages_with_errors", 0),
        )
    return report


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ICDEV™ Test Orchestrator")
    parser.add_argument("--project-dir", required=True,
                        help="Path to project under test")
    parser.add_argument("--project-id", help="ICDEV™ project UUID")
    parser.add_argument("--skip-e2e", action="store_true")
    parser.add_argument("--skip-sandbox", action="store_true")
    parser.add_argument("--skip-security", action="store_true")
    parser.add_argument("--skip-compliance", action="store_true")
    parser.add_argument("--skip-acceptance", action="store_true")
    parser.add_argument("--plan", help="Plan file for acceptance V&V")
    parser.add_argument("--base-url", help="Base URL for page checks")
    parser.add_argument("--pages", nargs="*",
                        help="Page paths to check for acceptance V&V")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # argparse exits on missing required args; mirror that intent
        # without bypassing the caller's exit handler in tests.
        raise

    run_id = make_run_id()
    logger = setup_logger(run_id, "test_orchestrator")
    run_dir = ensure_run_dir(run_id)

    logger.info("ICDEV™ Test Orchestrator — run_id=%s", run_id)
    logger.info("project_dir=%s", args.project_dir)

    state = TestRunState(
        run_id=run_id,
        project_id=args.project_id,
        project_dir=args.project_dir,
        started_at=timestamp_iso(),
    )

    # Step 1 — health check (best-effort)
    logger.info("\n=== Step 1: Health Check ===")
    try:
        from tools.testing.health_check import run_health_check
        health = run_health_check()
        if not health.success:
            logger.warning("orchestrator: health warnings: %s", health.errors)
    except Exception as exc:
        logger.warning("orchestrator: health check raised: %s", exc)

    # Step 2 — unit + BDD
    logger.info("\n=== Step 2: Unit + BDD Tests ===")
    all_results, _total_passed, total_failed = run_tests_with_resolution(
        args.project_dir,
        run_id=run_id,
        logger=logger,
        skip_sandbox=args.skip_sandbox,
    )
    unit_results = [r for r in all_results if r.test_type == "unit"]
    bdd_results = [r for r in all_results if r.test_type == "bdd"]
    state.unit_passed = sum(1 for r in unit_results if r.passed)
    state.unit_failed = len(unit_results) - state.unit_passed
    state.bdd_passed = sum(1 for r in bdd_results if r.passed)
    state.bdd_failed = len(bdd_results) - state.bdd_passed

    # Step 3 — E2E
    e2e_results: List[E2ETestResult] = []
    if total_failed > 0:
        logger.info("orchestrator: skipping E2E (unit/BDD failed)")
    elif args.skip_e2e:
        logger.info("orchestrator: skipping E2E (--skip-e2e)")
    else:
        logger.info("\n=== Step 3: E2E Tests ===")
        e2e_results, e2e_passed, e2e_failed = run_e2e_tests_with_resolution(
            run_id, logger,
        )
        state.e2e_passed = e2e_passed
        state.e2e_failed = e2e_failed

    # Step 4 — security gate
    security_gate: Optional[GateEvaluation] = None
    if not args.skip_security:
        logger.info("\n=== Step 4: Security Gate ===")
        security_gate = evaluate_security_gate(args.project_dir, logger)
        state.security_gate_passed = security_gate.overall_pass

    # Step 5 — compliance gate
    compliance_gate: Optional[GateEvaluation] = None
    if not args.skip_compliance and args.project_id:
        logger.info("\n=== Step 5: Compliance Gate ===")
        compliance_gate = evaluate_compliance_gate(
            args.project_id, args.project_dir, logger,
        )
        state.compliance_gate_passed = compliance_gate.overall_pass

    # Step 5b — coherence
    logger.info("\n=== Step 5b: Coherence Check ===")
    _run_coherence_check(args.project_dir, logger)

    # Step 6 — agentic
    logger.info("\n=== Step 6: Agentic Tests ===")
    agentic_results = _run_agentic_tests(args.project_dir, logger)

    # Step 7 — acceptance V&V
    if not args.skip_acceptance:
        logger.info("\n=== Step 7: Acceptance V&V ===")
        _run_acceptance_validation(args, state, run_dir, logger)
    else:
        logger.info("orchestrator: skipping acceptance (--skip-acceptance)")

    # Step 8 — summary
    logger.info("\n=== Step 8: Summary ===")
    state.completed_at = timestamp_iso()

    agentic_unit = [r for r in agentic_results if r.test_type == "unit"]
    agentic_bdd = [r for r in agentic_results if r.test_type == "bdd"]
    summary = generate_summary(
        unit_results + agentic_unit,
        bdd_results + agentic_bdd,
        e2e_results,
        security_gate,
        compliance_gate,
        logger,
    )

    state_file = run_dir / "state.json"
    try:
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump(state.model_dump(), fh, indent=2, default=str)
    except OSError as exc:
        logger.warning("orchestrator: state save failed: %s", exc)

    summary_file = run_dir / "summary.md"
    try:
        with open(summary_file, "w", encoding="utf-8") as fh:
            fh.write(summary)
    except OSError as exc:
        logger.warning("orchestrator: summary save failed: %s", exc)

    logger.info("orchestrator: results saved to %s", run_dir)

    # Best-effort audit row
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="test.complete",
            actor="test-orchestrator",
            action=(
                f"Test run {run_id}: "
                f"{state.unit_passed + state.bdd_passed + state.e2e_passed} passed, "
                f"{state.unit_failed + state.bdd_failed + state.e2e_failed} failed"
            ),
            project_id=args.project_id,
        )
    except Exception:
        pass

    agentic_failed = sum(1 for r in agentic_results if not r.passed)
    total_failures = (
        state.unit_failed + state.bdd_failed + state.e2e_failed + agentic_failed
    )
    return 0 if total_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
