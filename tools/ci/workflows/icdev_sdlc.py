# CUI // SP-CTI
# ICDEV SDLC — Complete Software Development Life Cycle orchestrator
# Adapted from ADW adw_sdlc.py with dual platform support

"""
ICDEV SDLC — Chains together all workflow phases.

Usage:
    python tools/ci/workflows/icdev_sdlc.py <issue-number> [run-id] [--orchestrated]

Pipeline:
    1. icdev_plan   — Planning phase
    2. icdev_build  — Implementation phase
    3. icdev_test   — Testing phase (--skip-e2e)
    4. icdev_review — Code review phase
    5. icdev_comply — Compliance artifacts (if applicable)

Flags:
    --orchestrated  Use multi-agent DAG orchestration (parallel execution)
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.workflow_ops import ensure_run_id


def run_phase(phase_name: str, script_name: str, issue_number: str,
              run_id: str, extra_args: list = None) -> bool:
    """Run a workflow phase as a subprocess."""
    script_path = Path(__file__).parent / f"{script_name}.py"

    cmd = [sys.executable, str(script_path), issue_number, run_id]
    if extra_args:
        cmd.extend(extra_args)

    print(f"\n{'='*60}")
    print(f"  {phase_name.upper()} PHASE")
    print(f"{'='*60}")
    print(f"Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        print(f"\n{phase_name} phase FAILED (exit code: {result.returncode})")
        return False

    print(f"\n{phase_name} phase completed")
    return True


def run_orchestrated(issue_number: str, run_id: str) -> bool:
    """Run SDLC pipeline using multi-agent orchestration (DAG-based parallelism).

    Uses TeamOrchestrator to decompose the SDLC into a subtask DAG
    and execute independent phases in parallel where possible.
    Falls back to sequential execution if orchestrator is unavailable.
    """
    try:
        from tools.agent.team_orchestrator import TeamOrchestrator

        print(f"\n{'='*60}")
        print(f"  ORCHESTRATED SDLC (Multi-Agent DAG)")
        print(f"{'='*60}")

        orchestrator = TeamOrchestrator(max_workers=4)

        # Decompose the SDLC task
        task_desc = (
            f"Execute full SDLC pipeline for issue #{issue_number} (run_id: {run_id}). "
            f"Phases: Plan (classify issue, create branch, generate plan), "
            f"Build (implement from plan with TDD), "
            f"Test (unit + BDD + security scan), "
            f"Review (code review with compliance check). "
            f"Plan must complete before Build. Build must complete before Test. "
            f"Test and Review can run in parallel after Build."
        )

        workflow = orchestrator.decompose_task(task_desc, project_id=f"issue-{issue_number}")
        print(f"Workflow: {workflow.name} ({len(workflow.subtasks)} subtasks)")

        # Execute the workflow
        workflow = orchestrator.execute_workflow(workflow, timeout=1200)

        if workflow.status == "completed":
            print(f"\nOrchestrated SDLC completed successfully")
            return True
        elif workflow.status == "partially_completed":
            print(f"\nOrchestrated SDLC partially completed — check failed subtasks")
            return False
        else:
            print(f"\nOrchestrated SDLC failed: {workflow.status}")
            return False

    except ImportError:
        print("TeamOrchestrator not available — falling back to sequential execution")
        return False
    except Exception as e:
        print(f"Orchestrated execution failed ({e}) — falling back to sequential")
        return False


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("CUI // SP-CTI")
        print("Usage: python tools/ci/workflows/icdev_sdlc.py <issue-number> [run-id] [--orchestrated]")
        print("\nRuns the complete SDLC pipeline:")
        print("  1. Plan   — Issue classification, branch, plan generation")
        print("  2. Build  — Implementation from plan")
        print("  3. Test   — pytest, behave, ruff, bandit, security gates")
        print("  4. Review — Code review against spec")
        print("  5. Comply — Compliance artifacts (SSP, POAM, STIG, SBOM)")
        print("\nFlags:")
        print("  --orchestrated  Use multi-agent DAG orchestration (parallel execution)")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    orchestrated = "--orchestrated" in sys.argv

    # Ensure run_id
    run_id = ensure_run_id(issue_number, run_id)
    print(f"CUI // SP-CTI")
    print(f"ICDEV SDLC — run_id: {run_id}, issue: #{issue_number}")

    # Orchestrated mode: use TeamOrchestrator for DAG-based parallel execution
    if orchestrated:
        if run_orchestrated(issue_number, run_id):
            print(f"\n{'='*60}")
            print(f"  ICDEV SDLC COMPLETE (Orchestrated)")
            print(f"{'='*60}")
            print(f"Run ID: {run_id}")
            print(f"Issue:  #{issue_number}")
            return
        # Fall through to sequential if orchestrated failed
        print("Falling back to sequential SDLC pipeline...")

    # Phase 1: Plan
    if not run_phase("Plan", "icdev_plan", issue_number, run_id):
        print("Pipeline aborted at Plan phase")
        sys.exit(1)

    # Phase 2: Build
    if not run_phase("Build", "icdev_build", issue_number, run_id):
        print("Pipeline aborted at Build phase")
        sys.exit(1)

    # Phase 3: Test (skip E2E in SDLC for speed)
    if not run_phase("Test", "icdev_test", issue_number, run_id, ["--skip-e2e"]):
        print("Pipeline aborted at Test phase")
        sys.exit(1)

    # Phase 4: Review
    if not run_phase("Review", "icdev_review", issue_number, run_id):
        print("Pipeline aborted at Review phase")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  ICDEV SDLC COMPLETE")
    print(f"{'='*60}")
    print(f"Run ID: {run_id}")
    print(f"Issue:  #{issue_number}")
    print(f"All phases completed successfully.")


if __name__ == "__main__":
    main()
