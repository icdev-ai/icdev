# CUI // SP-CTI
# ICDEV Poll Trigger — Cron-based issue polling for GitHub + GitLab
# Refactored to use EventEnvelope + EventRouter (D132, D133)

"""
Cron-based ICDEV trigger that polls GitHub/GitLab issues.

Polls every 20 seconds to detect:
1. New issues without comments
2. Issues where the latest comment contains 'icdev'
3. Issues with icdev_ workflow commands

When a qualifying issue is found, normalizes into an EventEnvelope and
routes through EventRouter (D132) to the correct workflow. This fixes the
previous bug where all poll triggers routed to icdev_plan.py regardless of
the command in the comment.

Usage:
    python tools/ci/triggers/poll_trigger.py

Environment:
    POLL_INTERVAL: Seconds between polls (default: 20)
"""

import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Set, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER
from tools.ci.core.event_router import EventRouter
from tools.ci.modules.vcs import VCS

# Configuration
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "20"))

# Track processed issues
processed_issues: Set[int] = set()
issue_last_comment: Dict[int, Optional[str]] = {}

# Graceful shutdown flag
shutdown_requested = False

# Central router instance
router = EventRouter()


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    print(f"\nINFO: Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


def should_process_issue(vcs: VCS, issue_number: int) -> tuple:
    """Determine if an issue should be processed based on comments.

    Returns:
        (should_process: bool, latest_comment_body: str)
    """
    comments = vcs.fetch_issue_comments(issue_number)

    # No comments — new issue, process it
    if not comments:
        print(f"INFO: Issue #{issue_number} has no comments — marking for processing")
        return True, ""

    # Get the latest comment
    latest_comment = comments[-1]
    comment_body = latest_comment.get("body", "") or latest_comment.get("note", "")
    comment_id = str(latest_comment.get("id", ""))

    # Check if we've already processed this comment
    last_processed = issue_last_comment.get(issue_number)
    if last_processed == comment_id:
        return False, ""

    comment_lower = comment_body.lower().strip()

    # Check if latest comment is exactly 'icdev'
    if comment_lower == "icdev":
        print(f"INFO: Issue #{issue_number} — latest comment is 'icdev'")
        issue_last_comment[issue_number] = comment_id
        return True, comment_body

    # Check if latest comment contains an icdev_ workflow command
    if "icdev_" in comment_lower:
        print(f"INFO: Issue #{issue_number} — contains icdev_ command")
        issue_last_comment[issue_number] = comment_id
        return True, comment_body

    return False, ""


def trigger_workflow(issue_data: dict, platform: str, latest_comment: str = "") -> bool:
    """Route issue through EventEnvelope + EventRouter.

    This replaces the old hardcoded icdev_plan.py routing. The EventRouter
    determines the correct workflow from the command in the envelope.
    """
    try:
        # Normalize into EventEnvelope
        envelope = EventEnvelope.from_poll_issue(
            issue_data, platform, latest_comment=latest_comment
        )

        issue_number = issue_data.get("number") or issue_data.get("iid")
        print(
            f"INFO: Routing issue #{issue_number} via EventRouter "
            f"(workflow={envelope.workflow_command or 'auto-detect'})"
        )

        # Route through central router
        result = router.route(envelope)

        action = result.get("action", "")
        if action == "launched":
            print(
                f"INFO: Launched {result.get('workflow')} for issue "
                f"#{issue_number} (run_id: {result.get('run_id')})"
            )
            return True
        elif action == "queued":
            print(f"INFO: Queued event for issue #{issue_number} ({result.get('reason')})")
            return True
        else:
            print(f"INFO: Issue #{issue_number} ignored ({result.get('reason')})")
            return False

    except Exception as e:
        issue_number = issue_data.get("number") or issue_data.get("iid")
        print(f"ERROR: Failed to route issue #{issue_number}: {e}")
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

            should_process, latest_comment = should_process_issue(vcs, issue_number)
            if should_process:
                new_qualifying.append((issue, latest_comment))

        if new_qualifying:
            print(f"INFO: Found {len(new_qualifying)} qualifying issues")

            for issue, latest_comment in new_qualifying:
                if shutdown_requested:
                    print(f"INFO: Shutdown requested, stopping")
                    break

                issue_number = issue.get("number") or issue.get("iid")
                if trigger_workflow(issue, platform, latest_comment):
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
    print(f"INFO: Starting ICDEV Poll Trigger (EventRouter-based)")
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
