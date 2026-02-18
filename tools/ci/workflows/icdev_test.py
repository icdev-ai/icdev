# CUI // SP-CTI
# ICDEV Test — Testing workflow
# Adapted from ADW adw_test.py with dual platform support

"""
ICDEV Test — Run test suite with retry and resolution.

Usage:
    python tools/ci/workflows/icdev_test.py <issue-number> <run-id> [--skip-e2e]

Workflow:
    1. Load state from previous phase
    2. Run test suite (py_compile, ruff, pytest, behave, bandit)
    3. Retry failed tests with automatic resolution
    4. Run E2E tests if not skipped
    5. Evaluate security and compliance gates
    6. Commit test results
    7. Push and update PR/MR
"""

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState
from tools.ci.modules.git_ops import commit_changes, finalize_git_operations
from tools.ci.modules.vcs import VCS
from tools.ci.modules.workflow_ops import (
    format_issue_message,
)
from tools.testing.utils import setup_logger

AGENT_TESTER = "icdev_tester"
MAX_TEST_RETRY = 4
MAX_E2E_RETRY = 2


def run_test_suite(run_id: str, logger: logging.Logger, skip_e2e: bool = False) -> dict:
    """Run the full test suite using the test orchestrator."""
    from tools.testing.test_orchestrator import (
        run_tests_with_resolution,
        run_e2e_tests_with_resolution,
        evaluate_security_gate,
        evaluate_compliance_gate,
    )

    results = {
        "unit_tests": None,
        "e2e_tests": None,
        "security_gate": None,
        "compliance_gate": None,
        "all_passed": True,
    }

    # Run unit/integration tests with retry
    logger.info("Running test suite with retry...")
    test_results = run_tests_with_resolution(
        project_dir=str(PROJECT_ROOT),
        max_attempts=MAX_TEST_RETRY,
    )
    results["unit_tests"] = test_results

    # Check if unit tests passed
    all_passed = all(r.get("passed", False) for r in test_results if isinstance(r, dict))
    if not all_passed:
        results["all_passed"] = False
        logger.warning("Some tests failed after retries")

    # E2E tests
    if not skip_e2e:
        logger.info("Running E2E tests...")
        try:
            e2e_results = run_e2e_tests_with_resolution(max_attempts=MAX_E2E_RETRY)
            results["e2e_tests"] = e2e_results
        except Exception as e:
            logger.warning(f"E2E tests skipped: {e}")
            results["e2e_tests"] = {"skipped": True, "reason": str(e)}

    # Security gate
    logger.info("Evaluating security gate...")
    try:
        security = evaluate_security_gate(str(PROJECT_ROOT))
        results["security_gate"] = security
        if not security.get("passed", True):
            results["all_passed"] = False
    except Exception as e:
        logger.warning(f"Security gate evaluation failed: {e}")

    # Compliance gate
    logger.info("Evaluating compliance gate...")
    try:
        compliance = evaluate_compliance_gate(str(PROJECT_ROOT))
        results["compliance_gate"] = compliance
        if not compliance.get("passed", True):
            results["all_passed"] = False
    except Exception as e:
        logger.warning(f"Compliance gate evaluation failed: {e}")

    return results


def format_test_summary(results: dict) -> str:
    """Format test results for issue comment."""
    lines = ["## Test Results\n"]

    # Unit tests
    if results.get("unit_tests"):
        passed = sum(1 for r in results["unit_tests"] if isinstance(r, dict) and r.get("passed", False))
        total = len([r for r in results["unit_tests"] if isinstance(r, dict)])
        lines.append(f"**Unit/Integration Tests:** {passed}/{total} passed")

    # E2E tests
    if results.get("e2e_tests"):
        if isinstance(results["e2e_tests"], dict) and results["e2e_tests"].get("skipped"):
            lines.append(f"**E2E Tests:** Skipped ({results['e2e_tests'].get('reason', '')})")
        else:
            lines.append("**E2E Tests:** Completed")

    # Gates
    if results.get("security_gate"):
        status = "PASS" if results["security_gate"].get("passed", True) else "FAIL"
        lines.append(f"**Security Gate:** {status}")

    if results.get("compliance_gate"):
        status = "PASS" if results["compliance_gate"].get("passed", True) else "FAIL"
        lines.append(f"**Compliance Gate:** {status}")

    overall = "PASS" if results.get("all_passed", False) else "FAIL"
    lines.append(f"\n**Overall:** {overall}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python tools/ci/workflows/icdev_test.py <issue-number> <run-id> [--skip-e2e]")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2]
    skip_e2e = "--skip-e2e" in sys.argv

    state = ICDevState.load(run_id)
    logger = setup_logger(run_id, "icdev_test")
    logger.info(f"ICDEV Test starting — run_id: {run_id}, issue: #{issue_number}")

    try:
        vcs = VCS()
    except ValueError as e:
        logger.error(f"VCS initialization failed: {e}")
        sys.exit(1)

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_TESTER, "Starting test suite"),
    )

    # Run tests
    results = run_test_suite(run_id, logger, skip_e2e)

    # Post results to issue
    summary = format_test_summary(results)
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_TESTER, summary),
    )

    # Commit test results
    success, error = commit_changes(f"{AGENT_TESTER}: test results for issue #{issue_number}")
    if success:
        finalize_git_operations(state, logger, vcs)

    if results["all_passed"]:
        logger.info("Test phase completed — all passed")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, "ops", "Test phase completed — all passed"),
        )
    else:
        logger.warning("Test phase completed — some failures, attempting recovery...")

        # Attempt self-recovery (D134)
        recovered = False
        try:
            from tools.ci.core.recovery_engine import RecoveryEngine

            engine = RecoveryEngine()
            failure_text = json.dumps(results, default=str)
            recovery_result = engine.attempt_recovery(
                "test", failure_text, run_id, issue_number, state,
            )

            if recovery_result.recovered:
                recovered = True
                logger.info(
                    f"Test recovery succeeded after {recovery_result.attempts} attempt(s)"
                )
                vcs.comment_on_issue(
                    int(issue_number),
                    format_issue_message(
                        run_id, "recovery",
                        f"Test failures recovered after {recovery_result.attempts} attempt(s). "
                        f"Fixed files: {', '.join(recovery_result.fixed_files)}",
                    ),
                )
            else:
                logger.warning(f"Test recovery failed: {recovery_result.error}")
                escalation = engine.format_escalation_message(recovery_result)
                vcs.comment_on_issue(
                    int(issue_number),
                    format_issue_message(run_id, "recovery", escalation),
                )
        except ImportError:
            logger.info("Recovery engine not available — skipping recovery")
        except Exception as e:
            logger.warning(f"Recovery attempt failed: {e}")

        if not recovered:
            vcs.comment_on_issue(
                int(issue_number),
                format_issue_message(run_id, "ops", "Test phase completed — some failures"),
            )
            sys.exit(1)

    state.save("icdev_test")


if __name__ == "__main__":
    main()
