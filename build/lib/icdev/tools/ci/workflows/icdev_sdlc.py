# CUI // SP-CTI
"""ICDEV™ SDLC — end-to-end workflow orchestrator.

Chains the per-phase workflow scripts (plan → build → test → e2e →
coherence → review → comply) together as subprocesses, with optional
``--orchestrated`` mode that runs them through a multi-agent DAG when
the team orchestrator is available.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_sdlc.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.workflow_ops import ensure_run_id  # noqa: E402


# Phases that earn a self-recovery attempt on failure.
RECOVERABLE_PHASES = frozenset({"Test", "Build"})

_PHASE_TO_PARSER = {
    "Test": "test",
    "Build": "compile",
}


# ────────────────────────────────────────────────────────────────────────────
# Banner helpers
# ────────────────────────────────────────────────────────────────────────────


def _banner(title: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  {title.upper()}")
    print(bar)


# ────────────────────────────────────────────────────────────────────────────
# Phase runner
# ────────────────────────────────────────────────────────────────────────────


def run_phase(
    phase_name: str,
    script_name: str,
    issue_number: str,
    run_id: str,
    extra_args: Optional[List[str]] = None,
) -> bool:
    """Run one workflow phase as a subprocess. Returns True on success
    (or successful recovery)."""
    script_path = Path(__file__).parent / f"{script_name}.py"
    cmd: List[str] = [
        sys.executable, str(script_path), issue_number, run_id,
    ]
    if extra_args:
        cmd.extend(extra_args)

    _banner(f"{phase_name} PHASE")
    print("Command: " + " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    if proc.returncode == 0:
        print(f"\n{phase_name} phase completed")
        return True

    print(
        f"\n{phase_name} phase FAILED (exit code: {proc.returncode})"
    )
    if phase_name in RECOVERABLE_PHASES:
        recovered = _attempt_phase_recovery(
            phase_name,
            (proc.stdout or "") + (proc.stderr or ""),
            run_id,
            issue_number,
        )
        if recovered:
            print(f"\n{phase_name} phase RECOVERED — continuing pipeline")
            return True
    return False


def _attempt_phase_recovery(
    phase_name: str,
    failure_output: str,
    run_id: str,
    issue_number: str,
) -> bool:
    try:
        from tools.ci.core.recovery_engine import RecoveryEngine
        from tools.ci.modules.state import ICDevState
    except ImportError:
        print("[Recovery] recovery engine not available")
        return False

    parser_phase = _PHASE_TO_PARSER.get(phase_name, phase_name.lower())
    print(f"\n[Recovery] attempting self-recovery for {phase_name}")

    try:
        engine = RecoveryEngine()
        state = ICDevState.load(run_id)
        result = engine.attempt_recovery(
            parser_phase,
            failure_output,
            run_id,
            issue_number,
            state,
        )
    except Exception as exc:
        print(f"[Recovery] recovery raised: {exc}")
        return False

    if getattr(result, "recovered", False):
        attempts = getattr(result, "attempts", "?")
        files = getattr(result, "fixed_files", []) or []
        print(f"[Recovery] {phase_name} recovered after {attempts} attempt(s)")
        if files:
            print(f"[Recovery] fixed files: {files}")
        return True

    error = getattr(result, "error", "unknown")
    print(f"[Recovery] {phase_name} recovery failed: {error}")
    try:
        from tools.ci.modules.vcs import VCS
        from tools.ci.modules.workflow_ops import format_issue_message
        vcs = VCS()
        escalation = engine.format_escalation_message(result)
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, "recovery", escalation),
        )
    except Exception:
        pass  # best-effort
    return False


# ────────────────────────────────────────────────────────────────────────────
# Orchestrated mode
# ────────────────────────────────────────────────────────────────────────────


def run_orchestrated(issue_number: str, run_id: str) -> bool:
    try:
        from tools.agent.team_orchestrator import TeamOrchestrator
    except ImportError:
        print("TeamOrchestrator not available — falling back to sequential")
        return False

    _banner("ORCHESTRATED SDLC (Multi-Agent DAG)")
    try:
        orchestrator = TeamOrchestrator(max_workers=4)
        task_desc = (
            f"Execute full SDLC pipeline for issue #{issue_number} "
            f"(run_id: {run_id}). Phases: Plan (classify issue, create "
            f"branch, generate plan), Build (implement from plan with "
            f"TDD), Test (unit + BDD + security scan), Review (code "
            f"review with compliance check). Plan must complete before "
            f"Build. Build must complete before Test. Test and Review "
            f"can run in parallel after Build."
        )
        workflow = orchestrator.decompose_task(
            task_desc, project_id=f"issue-{issue_number}",
        )
        print(
            f"Workflow: {getattr(workflow, 'name', 'unnamed')} "
            f"({len(getattr(workflow, 'subtasks', []) or [])} subtasks)"
        )
        workflow = orchestrator.execute_workflow(workflow, timeout=1200)
    except Exception as exc:
        print(
            f"Orchestrated execution failed ({exc}) — falling back to sequential"
        )
        return False

    status = getattr(workflow, "status", "")
    if status == "completed":
        print("\nOrchestrated SDLC completed successfully")
        return True
    if status == "partially_completed":
        print("\nOrchestrated SDLC partially completed — check failed subtasks")
        return False
    print(f"\nOrchestrated SDLC failed: {status}")
    return False


# ────────────────────────────────────────────────────────────────────────────
# Coherence integration
# ────────────────────────────────────────────────────────────────────────────


def _run_coherence_phase() -> None:
    try:
        from tools.workflow.coherence_checker import run_checks as coherence_check
    except Exception as exc:
        print(f"WARNING: coherence check unavailable: {exc}")
        return

    _banner("COHERENCE PHASE")
    try:
        coherence = coherence_check(autofix=True)
    except Exception as exc:
        print(f"WARNING: coherence check raised: {exc}")
        return

    if getattr(coherence, "overall_pass", False):
        passed = getattr(coherence, "passed_checks", "?")
        total = getattr(coherence, "total_checks", "?")
        print(f"Coherence passed ({passed}/{total} checks)")
        if getattr(coherence, "total_fixes", 0):
            print(f"  Auto-fixed {coherence.total_fixes} issue(s)")
    else:
        failed = getattr(coherence, "failed_checks", "?")
        warned = getattr(coherence, "warned_checks", "?")
        print(
            f"WARNING: coherence check found {failed} failure(s), "
            f"{warned} warning(s)"
        )


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _parse_args(argv: List[str]):
    if len(argv) < 2:
        return None
    issue_number = argv[1]
    run_id_arg: Optional[str] = None
    if len(argv) > 2 and not argv[2].startswith("--"):
        run_id_arg = argv[2]
    orchestrated = "--orchestrated" in argv
    return {
        "issue_number": issue_number,
        "run_id_arg": run_id_arg,
        "orchestrated": orchestrated,
    }


def _print_usage() -> None:
    print("CUI // SP-CTI")
    print(
        "Usage: python tools/ci/workflows/icdev_sdlc.py "
        "<issue-number> [run-id] [--orchestrated]"
    )
    print("\nRuns the complete SDLC pipeline:")
    print("  1. Plan   — Issue classification, branch, plan generation")
    print("  2. Build  — Implementation from plan")
    print("  3. Test   — pytest, behave, ruff, bandit, security gates")
    print("  4. E2E    — Playwright browser verification")
    print("  5. Review — Code review against spec")
    print("  6. Comply — Compliance artifacts (SSP, POAM, STIG, SBOM)")
    print("\nFlags:")
    print(
        "  --orchestrated  Use multi-agent DAG orchestration "
        "(parallel execution)"
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        _print_usage()
        return 1

    issue_number = args["issue_number"]
    run_id = ensure_run_id(issue_number, args["run_id_arg"])
    orchestrated = bool(args["orchestrated"])

    print("CUI // SP-CTI")
    print(f"ICDEV™ SDLC — run_id: {run_id}, issue: #{issue_number}")

    if orchestrated:
        if run_orchestrated(issue_number, run_id):
            _banner("ICDEV™ SDLC COMPLETE (Orchestrated)")
            print(f"Run ID: {run_id}")
            print(f"Issue:  #{issue_number}")
            return 0
        print("Falling back to sequential SDLC pipeline...")

    if not run_phase("Plan", "icdev_plan", issue_number, run_id):
        print("Pipeline aborted at Plan phase")
        return 1

    if not run_phase("Build", "icdev_build", issue_number, run_id):
        print("Pipeline aborted at Build phase")
        return 1

    if not run_phase("Test", "icdev_test", issue_number, run_id):
        print("Pipeline aborted at Test phase")
        return 1

    if not run_phase("E2E", "icdev_e2e", issue_number, run_id):
        # E2E failure is non-blocking — log but continue.
        print("WARNING: E2E phase had failures — review screenshots before merge")

    _run_coherence_phase()

    if not run_phase("Review", "icdev_review", issue_number, run_id):
        print("Pipeline aborted at Review phase")
        return 1

    if not run_phase("Comply", "icdev_comply", issue_number, run_id):
        print("Pipeline aborted at Comply phase")
        return 1

    _banner("ICDEV™ SDLC COMPLETE")
    print(f"Run ID: {run_id}")
    print(f"Issue:  #{issue_number}")
    print("All phases completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
