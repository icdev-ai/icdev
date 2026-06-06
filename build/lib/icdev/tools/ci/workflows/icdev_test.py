# CUI // SP-CTI
"""ICDEV™ Test — testing workflow with retry and self-recovery.

Drives the test orchestrator under :mod:`tools.testing.test_orchestrator`
to run unit/integration tests with retry, optionally run E2E tests,
evaluate the security and compliance gates, and post a summary on the
linked issue. On any failure the workflow asks
:mod:`tools.ci.core.recovery_engine` for a self-recovery attempt before
giving up.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_test.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.git_ops import (  # noqa: E402
    commit_changes,
    finalize_git_operations,
)
from tools.ci.modules.state import ICDevState  # noqa: E402
from tools.ci.modules.vcs import VCS  # noqa: E402
from tools.ci.modules.workflow_ops import format_issue_message  # noqa: E402
from tools.testing.utils import setup_logger  # noqa: E402


AGENT_TESTER: str = "icdev_tester"
MAX_TEST_RETRY: int = 4
MAX_E2E_RETRY: int = 2


# ────────────────────────────────────────────────────────────────────────────
# Test driver
# ────────────────────────────────────────────────────────────────────────────


def _import_orchestrator():
    """Lazy import the orchestrator module so unit tests can stub it."""
    from tools.testing import test_orchestrator
    return test_orchestrator


def run_test_suite(
    run_id: str,
    logger: logging.Logger,
    skip_e2e: bool = False,
) -> Dict[str, Any]:
    """Run unit, E2E, and gate evaluations for a kanban run.

    Returns the canonical dict shape documented in the spec.
    """
    orch = _import_orchestrator()

    results: Dict[str, Any] = {
        "unit_tests": None,
        "e2e_tests": None,
        "security_gate": None,
        "compliance_gate": None,
        "all_passed": True,
    }

    # ── Unit + integration ─────────────────────────────────────────
    logger.info("icdev_test: running unit/integration tests")
    try:
        unit_results = orch.run_tests_with_resolution(
            project_dir=str(PROJECT_ROOT),
            max_attempts=MAX_TEST_RETRY,
        )
    except Exception as exc:
        logger.error("icdev_test: unit driver crashed: %s", exc)
        unit_results = [{"passed": False, "error": str(exc)}]
    results["unit_tests"] = unit_results

    if not _all_passed(unit_results):
        results["all_passed"] = False
        logger.warning("icdev_test: unit tests had failures")

    # ── E2E ────────────────────────────────────────────────────────
    if skip_e2e:
        results["e2e_tests"] = {"skipped": True, "reason": "--skip-e2e flag"}
    else:
        logger.info("icdev_test: running E2E tests")
        try:
            e2e_payload = orch.run_e2e_tests_with_resolution(
                run_id, logger, max_attempts=MAX_E2E_RETRY,
            )
            # Expect (results, passed_count, failed_count) or a dict.
            if isinstance(e2e_payload, tuple) and len(e2e_payload) >= 3:
                e2e_results, _e2e_passed, e2e_failed = e2e_payload[:3]
                results["e2e_tests"] = e2e_results
                if e2e_failed:
                    results["all_passed"] = False
                    logger.warning("icdev_test: E2E failures: %s", e2e_failed)
            else:
                results["e2e_tests"] = e2e_payload
        except Exception as exc:
            logger.error("icdev_test: E2E driver crashed: %s", exc)
            results["e2e_tests"] = {"error": True, "reason": str(exc)}
            results["all_passed"] = False

    # ── Security gate ──────────────────────────────────────────────
    logger.info("icdev_test: evaluating security gate")
    try:
        sec = orch.evaluate_security_gate(str(PROJECT_ROOT))
        results["security_gate"] = sec
        if isinstance(sec, dict) and not sec.get("passed", True):
            results["all_passed"] = False
    except Exception as exc:
        logger.warning("icdev_test: security gate raised: %s", exc)

    # ── Compliance gate ────────────────────────────────────────────
    logger.info("icdev_test: evaluating compliance gate")
    try:
        comp = orch.evaluate_compliance_gate(str(PROJECT_ROOT))
        results["compliance_gate"] = comp
        if isinstance(comp, dict) and not comp.get("passed", True):
            results["all_passed"] = False
    except Exception as exc:
        logger.warning("icdev_test: compliance gate raised: %s", exc)

    return results


def _all_passed(results: Any) -> bool:
    if not results:
        return False
    if isinstance(results, list):
        return all(
            isinstance(r, dict) and r.get("passed", False)
            for r in results
        )
    if isinstance(results, dict):
        return bool(results.get("passed", False))
    return False


# ────────────────────────────────────────────────────────────────────────────
# Result formatter
# ────────────────────────────────────────────────────────────────────────────


def format_test_summary(results: Dict[str, Any]) -> str:
    lines: List[str] = ["## Test Results", ""]

    unit = results.get("unit_tests")
    if unit:
        passed = sum(
            1 for r in unit
            if isinstance(r, dict) and r.get("passed", False)
        )
        total = sum(1 for r in unit if isinstance(r, dict))
        lines.append(f"**Unit/Integration Tests:** {passed}/{total} passed")

    e2e = results.get("e2e_tests")
    if e2e is not None:
        if isinstance(e2e, dict) and e2e.get("skipped"):
            reason = e2e.get("reason", "")
            lines.append(f"**E2E Tests:** Skipped ({reason})")
        else:
            lines.append("**E2E Tests:** Completed")

    sec = results.get("security_gate")
    if sec is not None:
        status = "PASS" if (
            isinstance(sec, dict) and sec.get("passed", True)
        ) else "FAIL"
        lines.append(f"**Security Gate:** {status}")

    comp = results.get("compliance_gate")
    if comp is not None:
        status = "PASS" if (
            isinstance(comp, dict) and comp.get("passed", True)
        ) else "FAIL"
        lines.append(f"**Compliance Gate:** {status}")

    overall = "PASS" if results.get("all_passed", False) else "FAIL"
    lines.append("")
    lines.append(f"**Overall:** {overall}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ────────────────────────────────────────────────────────────────────────────


def _bot(run_id: str, agent: str, message: str) -> str:
    return format_issue_message(run_id, agent, message)


def _safe_comment(
    vcs: Any, logger: Any, issue_int: Optional[int], body: str,
) -> None:
    if issue_int is None:
        return
    try:
        vcs.comment_on_issue(issue_int, body)
    except Exception as exc:
        logger.warning("icdev_test: comment_on_issue raised: %s", exc)


def _coerce_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _try_recover(
    results: Dict[str, Any],
    run_id: str,
    issue_number: str,
    state: Any,
    vcs: Any,
    issue_int: Optional[int],
    logger: logging.Logger,
) -> bool:
    """Best-effort self-recovery via the recovery engine. Returns True
    when recovery succeeded."""
    try:
        from tools.ci.core.recovery_engine import RecoveryEngine
    except ImportError:
        logger.info("icdev_test: recovery engine not available")
        return False
    try:
        engine = RecoveryEngine()
        recovery = engine.attempt_recovery(
            "test",
            json.dumps(results, default=str),
            run_id,
            issue_number,
            state,
        )
    except Exception as exc:
        logger.warning("icdev_test: recovery raised: %s", exc)
        return False

    if getattr(recovery, "recovered", False):
        attempts = getattr(recovery, "attempts", "?")
        files = ", ".join(getattr(recovery, "fixed_files", []) or [])
        logger.info(
            "icdev_test: recovery succeeded after %s attempt(s)", attempts,
        )
        _safe_comment(
            vcs, logger, issue_int,
            _bot(
                run_id, "recovery",
                f"Test failures recovered after {attempts} attempt(s). "
                f"Fixed files: {files}",
            ),
        )
        return True

    logger.warning(
        "icdev_test: recovery failed: %s",
        getattr(recovery, "error", "unknown"),
    )
    try:
        message = engine.format_escalation_message(recovery)
    except Exception as exc:
        message = f"Recovery engine could not format escalation: {exc}"
    _safe_comment(vcs, logger, issue_int, _bot(run_id, "recovery", message))
    return False


def _parse_args(
    argv: List[str],
) -> Optional[Dict[str, Any]]:
    if len(argv) < 3:
        return None
    return {
        "issue_number": argv[1],
        "run_id": argv[2],
        "skip_e2e": "--skip-e2e" in argv,
    }


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        sys.stdout.write(
            "Usage: python tools/ci/workflows/icdev_test.py "
            "<issue-number> <run-id> [--skip-e2e]\n"
        )
        return 1

    run_id = args["run_id"]
    issue_number = args["issue_number"]
    skip_e2e = bool(args["skip_e2e"])

    logger = setup_logger(run_id, "icdev_test")
    logger.info(
        "ICDEV™ Test starting — run_id=%s issue=#%s skip_e2e=%s",
        run_id, issue_number, skip_e2e,
    )

    state = ICDevState.load(run_id, logger=logger)
    issue_int = _coerce_int(issue_number)

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_test: VCS initialization failed: %s", exc)
        return 1

    _safe_comment(vcs, logger, issue_int,
                  _bot(run_id, AGENT_TESTER, "Starting test suite"))

    results = run_test_suite(run_id, logger, skip_e2e=skip_e2e)
    summary = format_test_summary(results)
    _safe_comment(vcs, logger, issue_int,
                  _bot(run_id, AGENT_TESTER, summary))

    committed, commit_err = commit_changes(
        f"{AGENT_TESTER}: test results for issue #{issue_number}",
    )
    if committed:
        try:
            finalize_git_operations(state, logger, vcs=vcs)
        except Exception as exc:
            logger.warning(
                "icdev_test: finalize_git_operations raised: %s", exc
            )
    else:
        logger.warning("icdev_test: commit failed: %s", commit_err)

    if results["all_passed"]:
        logger.info("icdev_test: phase complete — all passed")
        _safe_comment(
            vcs, logger, issue_int,
            _bot(run_id, "ops", "Test phase completed — all passed"),
        )
        state.save("icdev_test")
        return 0

    logger.warning("icdev_test: failures present, attempting recovery")
    recovered = _try_recover(
        results, run_id, issue_number, state, vcs, issue_int, logger,
    )
    if not recovered:
        _safe_comment(
            vcs, logger, issue_int,
            _bot(run_id, "ops", "Test phase completed — some failures"),
        )
        state.save("icdev_test")
        return 1

    state.save("icdev_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
