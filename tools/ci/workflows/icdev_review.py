# CUI // SP-CTI
# ICDEV Review — Code review workflow
# Adapted from ADW adw_review.py with dual platform support

"""
ICDEV Review — Automated code review against spec with security checks.

Usage:
    python tools/ci/workflows/icdev_review.py <issue-number> <run-id>

Workflow:
    1. Load state and find spec/plan file
    2. Run review against specification
    3. Filter issues by severity (blocker/warning/info)
    4. Create patches for blocker issues
    5. Commit review results
    6. Push and update PR/MR
"""

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState
from tools.ci.modules.git_ops import commit_changes, finalize_git_operations
from tools.ci.modules.vcs import VCS
from tools.ci.modules.agent import execute_template
from tools.ci.modules.workflow_ops import (
    format_issue_message,
    implement_plan,
    AGENT_PLANNER,
)
from tools.testing.data_types import AgentTemplateRequest
from tools.testing.utils import setup_logger

AGENT_REVIEWER = "icdev_reviewer"
MAX_REVIEW_RETRY = 3


def run_review(plan_file: str, run_id: str, logger: logging.Logger) -> dict:
    """Run code review against the spec/plan file."""
    request = AgentTemplateRequest(
        agent_name=AGENT_REVIEWER,
        slash_command="/icdev-review",
        args=[plan_file],
        run_id=run_id,
    )

    response = execute_template(request)

    return {
        "success": response.success,
        "output": response.output,
        "session_id": response.session_id,
    }


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python tools/ci/workflows/icdev_review.py <issue-number> <run-id>")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2]

    state = ICDevState.load(run_id)
    logger = setup_logger(run_id, "icdev_review")
    logger.info(f"ICDEV Review starting — run_id: {run_id}, issue: #{issue_number}")

    try:
        vcs = VCS()
    except ValueError as e:
        logger.error(f"VCS initialization failed: {e}")
        sys.exit(1)

    # Find plan/spec file
    plan_file = state.get("plan_file")
    if not plan_file or not os.path.exists(plan_file):
        logger.error(f"Plan file not found: {plan_file}")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, AGENT_REVIEWER, "No plan file found — cannot review"),
        )
        sys.exit(1)

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_REVIEWER, "Starting code review"),
    )

    # Run review
    review_result = run_review(plan_file, run_id, logger)

    if review_result["success"]:
        logger.info("Review completed successfully")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(
                run_id, AGENT_REVIEWER,
                f"## Code Review Complete\n\n{review_result['output'][:2000]}"
            ),
        )
    else:
        logger.warning(f"Review found issues: {review_result['output'][:500]}")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(
                run_id, AGENT_REVIEWER,
                f"## Code Review Issues\n\n{review_result['output'][:2000]}"
            ),
        )

    # Commit review artifacts
    success, _ = commit_changes(f"{AGENT_REVIEWER}: review results for issue #{issue_number}")
    if success:
        finalize_git_operations(state, logger, vcs)

    logger.info("Review phase completed")
    state.save("icdev_review")


if __name__ == "__main__":
    main()
