# CUI // SP-CTI
# ICDEV Poll Trigger — Cron-based issue polling for GitHub + GitLab
# Adapted from ADW trigger_cron.py with dual platform support

"""
Cron-based ICDEV trigger that polls GitHub/GitLab issues.

Polls every 20 seconds to detect:
1. New issues without comments
2. Issues where the latest comment contains 'icdev'
3. Issues with icdev_ workflow commands

When a qualifying issue is found, triggers the appropriate ICDEV workflow.

Usage:
    python tools/ci/triggers/poll_trigger.py

Environment:
    POLL_INTERVAL: Seconds between polls (default: 20)
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Set, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.vcs import VCS
from tools.ci.modules.workflow_ops import (
    extract_icdev_info,
    BOT_IDENTIFIER,
)
from tools.ci.modules.state import ICDevState
from tools.testing.utils import make_run_id, get_safe_subprocess_env

# Configuration
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "20"))

# Track processed issues
processed_issues: Set[int] = set()
issue_last_comment: Dict[int, Optional[str]] = {}

# Graceful shutdown flag
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    print(f"\nINFO: Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


def should_process_issue(vcs: VCS, issue_number: int) -> bool:
    """Determine if an issue should be processed based on comments."""
    comments = vcs.fetch_issue_comments(issue_number)

    # No comments — new issue, process it
    if not comments:
        print(f"INFO: Issue #{issue_number} has no comments — marking for processing")
        return True

    # Get the latest comment
    latest_comment = comments[-1]
    comment_body = latest_comment.get("body", "") or latest_comment.get("note", "")
    comment_id = str(latest_comment.get("id", ""))

    # Check if we've already processed this comment
    last_processed = issue_last_comment.get(issue_number)
    if last_processed == comment_id:
        return False

    comment_lower = comment_body.lower().strip()

    # Check if latest comment is exactly 'icdev'
    if comment_lower == "icdev":
        print(f"INFO: Issue #{issue_number} — latest comment is 'icdev'")
        issue_last_comment[issue_number] = comment_id
        return True

    # Check if latest comment contains an icdev_ workflow command
    if "icdev_" in comment_lower:
        print(f"INFO: Issue #{issue_number} — contains icdev_ command")
        issue_last_comment[issue_number] = comment_id
        return True

    return False


def trigger_workflow(issue_number: int, platform: str) -> bool:
    """Trigger the ICDEV plan workflow for a specific issue."""
    try:
        script_path = PROJECT_ROOT / "tools" / "ci" / "workflows" / "icdev_plan.py"

        run_id = make_run_id()
        print(f"INFO: Triggering ICDEV workflow for issue #{issue_number} (run_id: {run_id})")

        # Create initial state
        state = ICDevState(run_id)
        state.update(
            run_id=run_id,
            issue_number=str(issue_number),
            platform=platform,
        )
        state.save("poll_trigger")

        cmd = [sys.executable, str(script_path), str(issue_number), run_id]

        # Launch in background
        process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=get_safe_subprocess_env(),
            stdin=subprocess.DEVNULL,
        )

        print(f"INFO: Background process started (PID: {process.pid})")
        return True

    except Exception as e:
        print(f"ERROR: Failed to trigger workflow for issue #{issue_number}: {e}")
        return False


def check_and_process_issues(vcs: VCS):
    """Main function that checks for issues and processes qualifying ones."""
    if shutdown_requested:
        return

    start_time = time.time()
    platform = "gitlab" if vcs.is_gitlab else "github"
    print(f"INFO: Starting issue check cycle (platform: {platform})")

    try:
        issues = vcs.list_open_issues(limit=50)

        if not issues:
            print(f"INFO: No open issues found")
            return

        new_qualifying = []

        for issue in issues:
            # GitHub returns 'number', GitLab returns 'iid'
            issue_number = issue.get("number") or issue.get("iid")
            if not issue_number:
                continue

            if issue_number in processed_issues:
                continue

            if should_process_issue(vcs, issue_number):
                new_qualifying.append(issue_number)

        if new_qualifying:
            print(f"INFO: Found {len(new_qualifying)} qualifying issues: {new_qualifying}")

            for issue_number in new_qualifying:
                if shutdown_requested:
                    print(f"INFO: Shutdown requested, stopping")
                    break

                if trigger_workflow(issue_number, platform):
                    processed_issues.add(issue_number)
                else:
                    print(f"WARNING: Failed to process issue #{issue_number}, will retry")
        else:
            print(f"INFO: No new qualifying issues")

        cycle_time = time.time() - start_time
        print(f"INFO: Cycle completed in {cycle_time:.2f}s (processed: {len(processed_issues)} total)")

    except Exception as e:
        print(f"ERROR: Error during check cycle: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main entry point for the poll trigger."""
    print(f"CUI // SP-CTI")
    print(f"INFO: Starting ICDEV Poll Trigger")
    print(f"INFO: Poll interval: {POLL_INTERVAL} seconds")

    # Auto-detect platform
    try:
        vcs = VCS()
        platform = "GitLab" if vcs.is_gitlab else "GitHub"
        print(f"INFO: Detected platform: {platform}")
        print(f"INFO: Repository: {vcs.repo_path}")
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run initial check immediately
    check_and_process_issues(vcs)

    # Main loop
    print(f"INFO: Entering main polling loop")
    while not shutdown_requested:
        time.sleep(POLL_INTERVAL)
        if not shutdown_requested:
            check_and_process_issues(vcs)

    print(f"INFO: Shutdown complete")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h"]:
        print(__doc__)
        sys.exit(0)
    main()
