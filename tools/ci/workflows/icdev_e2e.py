# [TEMPLATE: CUI // SP-CTI]
# ICDEV™ E2E — Playwright browser verification workflow

from __future__ import annotations

"""
ICDEV™ E2E — Run Playwright E2E tests with screenshot validation.

Usage:
    python tools/ci/workflows/icdev_e2e.py <issue-number> <run-id>

Workflow:
    1. Start dashboard if not running
    2. Discover E2E test specs in .claude/commands/e2e/
    3. Run all E2E tests via e2e_runner.py
    4. Validate screenshots with vision model
    5. Report results
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check_dashboard_running(base_url: str = "http://localhost:5050") -> bool:
    """Check if the dashboard is reachable."""
    try:
        import urllib.request

        req = urllib.request.Request(f"{base_url}/", method="HEAD")
        urllib.request.urlopen(req, timeout=5)  # nosec B310 -- URL scheme validated; internal/configured endpoints only
        return True
    except Exception:
        return False


def stop_dashboard(proc: subprocess.Popen | None) -> None:
    """Stop a dashboard we started, and make sure it is actually gone.

    ``terminate()`` alone is a REQUEST. On Windows it maps to TerminateProcess
    for the child but the call returns without waiting, and a dashboard mid
    DB-init can outlive it — so the escalation to ``kill()`` is not belt and
    braces, it is the part that works.

    Observed 2026-08-15: three dashboards from an E2E worktree
    (.tmp/worktrees/task-e2e-27e596dc) were still running 6+ hours later, and
    one of them held port 5050 — the MAIN dashboard's port — serving code from a
    commit eight hours stale. Restarting the real dashboard by hand did nothing
    visible, because the orphan owned the socket and the new process could not
    bind it. That looked exactly like "Flask does not auto-reload".
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        proc.wait(timeout=5)
    except Exception as exc:  # noqa: BLE001 — teardown must not mask the result
        print(f"WARNING: could not stop dashboard pid={proc.pid}: {exc}")


def start_dashboard() -> subprocess.Popen | None:
    """Start the dashboard in the background."""
    app_path = PROJECT_ROOT / "tools" / "dashboard" / "app.py"
    if not app_path.exists():
        return None

    proc = subprocess.Popen(
        [sys.executable, str(app_path)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    # Wait for dashboard to be ready (max 15 seconds)
    import time

    for _ in range(30):
        time.sleep(0.5)
        if check_dashboard_running():
            return proc

    # Slow start is the DANGEROUS branch, not the harmless one. The caller gets
    # None and therefore no handle, so anything left alive here can never be
    # stopped by this program again — it just keeps holding the port. The old
    # code sent one terminate() and returned; stop_dashboard waits and escalates.
    print("WARNING: Dashboard did not start within 15 seconds")
    stop_dashboard(proc)
    return None


def run_e2e_tests(validate_screenshots: bool = True) -> dict:
    """Run E2E tests via the e2e_runner."""
    runner_path = PROJECT_ROOT / "tools" / "testing" / "e2e_runner.py"
    if not runner_path.exists():
        return {"status": "skipped", "reason": "e2e_runner.py not found"}

    cmd = [sys.executable, str(runner_path), "--run-all"]
    if validate_screenshots:
        cmd.append("--validate-screenshots")

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )

    # Try to parse JSON output
    try:
        output = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        output = {
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }

    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "output": output,
    }


def discover_e2e_specs() -> list:
    """List available E2E test spec files."""
    e2e_dir = PROJECT_ROOT / ".claude" / "commands" / "e2e"
    if not e2e_dir.exists():
        return []
    return sorted(e2e_dir.glob("*.md"))


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("CUI // SP-CTI")
        print("Usage: python tools/ci/workflows/icdev_e2e.py <issue-number> [run-id]")
        print("\nRuns Playwright E2E browser verification:")
        print("  1. Start dashboard (if not running)")
        print("  2. Discover E2E specs in .claude/commands/e2e/")
        print("  3. Run all E2E tests with screenshot validation")
        print("  4. Report results")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "unknown"

    print("CUI // SP-CTI")
    print(f"ICDEV™ E2E — run_id: {run_id}, issue: #{issue_number}")
    print()

    # Step 1: Discover E2E specs
    specs = discover_e2e_specs()
    print(f"E2E test specs found: {len(specs)}")
    for spec in specs:
        print(f"  - {spec.name}")

    if not specs:
        print("\nNo E2E test specs found in .claude/commands/e2e/")
        print("E2E phase: SKIPPED (no specs)")
        sys.exit(0)

    # Step 2: Check/start dashboard
    dashboard_proc = None
    if not check_dashboard_running():
        print("\nDashboard not running — starting...")
        dashboard_proc = start_dashboard()
        if dashboard_proc:
            print("Dashboard started (PID: {})".format(dashboard_proc.pid))
            # Registered the moment we own the process. The finally below covers
            # the normal and exception paths; atexit additionally covers a
            # sys.exit() raised from deeper code. Neither covers SIGKILL, which
            # is why stop_dashboard escalates rather than trusting terminate().
            import atexit

            atexit.register(stop_dashboard, dashboard_proc)
        else:
            print("WARNING: Could not start dashboard — E2E tests may fail")
    else:
        print("\nDashboard already running")

    # Everything from here runs under a finally, because the teardown used to be
    # straight-line code after the try/except: ANY exception raised between
    # starting the dashboard and reaching it skipped teardown and left the
    # process running forever. A kanban session that exhausts its token budget
    # mid-E2E takes exactly that path. Observed 2026-08-15: three dashboards from
    # .tmp/worktrees/task-e2e-27e596dc were still alive 6+ hours later and one
    # held port 5050 — the MAIN dashboard's port — serving a commit eight hours
    # stale, so restarting the real dashboard by hand changed nothing visible.
    try:
        # Step 3: Run E2E tests
        print(f"\n{'=' * 40}")
        print("  Running E2E Tests")
        print(f"{'=' * 40}")

        try:
            results = run_e2e_tests(validate_screenshots=True)
        except subprocess.TimeoutExpired:
            results = {"status": "failed", "reason": "E2E tests timed out (600s)"}

        # Step 4: Report results
        print(f"\nE2E Result: {results['status'].upper()}")
        if results.get("output") and isinstance(results["output"], dict):
            if "total" in results["output"]:
                print(f"  Total: {results['output'].get('total', 'N/A')}")
                print(f"  Passed: {results['output'].get('passed', 'N/A')}")
                print(f"  Failed: {results['output'].get('failed', 'N/A')}")
    finally:
        if dashboard_proc:
            print("\nStopping dashboard...")
            stop_dashboard(dashboard_proc)

    # Exit code
    if results["status"] == "passed":
        print("\nE2E phase: PASSED")
        sys.exit(0)
    elif results["status"] == "skipped":
        print(f"\nE2E phase: SKIPPED ({results.get('reason', 'unknown')})")
        sys.exit(0)
    else:
        print("\nE2E phase: FAILED — review screenshots before merge")
        # Non-blocking: exit 0 with warning (SDLC logs warning but continues)
        # Change to sys.exit(1) to make E2E failures blocking
        sys.exit(1)


if __name__ == "__main__":
    main()
