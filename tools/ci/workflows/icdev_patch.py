# CUI // SP-CTI
# ICDEV Patch — Quick fix workflow for single-issue patches
# Adapted from ADW adw_patch.py with dual platform support

"""
ICDEV Patch — Create and implement a focused patch from issue content.

Usage:
    python tools/ci/workflows/icdev_patch.py <issue-number> [run-id]

Looks for 'icdev_patch' keyword in issue body or comments, then:
    1. Creates/finds branch for the issue
    2. Creates focused patch plan from content containing 'icdev_patch'
    3. Implements the patch
    4. Commits and pushes
"""

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState
from tools.ci.modules.git_ops import create_branch, commit_changes, finalize_git_operations
from tools.ci.modules.vcs import VCS
from tools.ci.modules.agent import execute_template
from tools.ci.modules.workflow_ops import (
    classify_issue,
    generate_branch_name,
    create_commit,
    implement_plan,
    format_issue_message,
    ensure_run_id,
)
from tools.testing.data_types import AgentTemplateRequest
from tools.testing.utils import setup_logger

AGENT_PATCH_PLANNER = "patch_planner"
AGENT_PATCH_IMPLEMENTOR = "patch_implementor"


def get_patch_content(issue_data: dict, vcs: VCS, issue_number: int, logger: logging.Logger) -> str:
    """Get patch content from issue or comments containing 'icdev_patch'."""
    # Check comments for 'icdev_patch'
    comments = vcs.fetch_issue_comments(issue_number)
    for comment in reversed(comments):
        body = comment.get("body", "") or comment.get("note", "")
        if "icdev_patch" in body.lower():
            logger.info("Found 'icdev_patch' in comment")
            return body

    # Check issue body
    body = issue_data.get("body", "") or issue_data.get("description", "")
    if "icdev_patch" in body.lower():
        title = issue_data.get("title", "")
        logger.info("Found 'icdev_patch' in issue body")
        return f"Issue #{issue_number}: {title}\n\n{body}"

    # Fallback: use full issue as patch request
    title = issue_data.get("title", "")
    logger.info("No 'icdev_patch' keyword found, using full issue as patch request")
    return f"Issue #{issue_number}: {title}\n\n{body}"


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python tools/ci/workflows/icdev_patch.py <issue-number> [run-id]")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else None

    run_id = ensure_run_id(issue_number, run_id)
    state = ICDevState.load(run_id)
    logger = setup_logger(run_id, "icdev_patch")
    logger.info(f"ICDEV Patch starting — run_id: {run_id}, issue: #{issue_number}")

    try:
        vcs = VCS()
        platform = "gitlab" if vcs.is_gitlab else "github"
        state.update(platform=platform)
    except ValueError as e:
        logger.error(f"VCS initialization failed: {e}")
        sys.exit(1)

    # Fetch issue
    try:
        issue_data = vcs.fetch_issue(int(issue_number))
        issue_json = json.dumps(issue_data)
    except Exception as e:
        logger.error(f"Failed to fetch issue: {e}")
        sys.exit(1)

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", "Starting patch workflow"),
    )

    # Create or find branch
    branch_name = state.get("branch_name")
    if not branch_name:
        # Classify and generate branch
        issue_command, _ = classify_issue(issue_json, run_id, logger)
        issue_command = issue_command or "/patch"

        branch_name, error = generate_branch_name(issue_json, issue_command, run_id, logger)
        if error:
            logger.error(f"Branch name generation failed: {error}")
            sys.exit(1)

    success, error = create_branch(branch_name)
    if not success:
        logger.error(f"Branch operation failed: {error}")
        sys.exit(1)

    state.update(branch_name=branch_name, issue_class="/patch")
    state.save("icdev_patch")
    logger.info(f"Working on branch: {branch_name}")

    # Get patch content
    patch_content = get_patch_content(issue_data, vcs, int(issue_number), logger)

    # Create patch plan
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_PATCH_PLANNER, "Creating patch plan"),
    )

    request = AgentTemplateRequest(
        agent_name=AGENT_PATCH_PLANNER,
        slash_command="/patch",
        args=[run_id, patch_content],
        run_id=run_id,
    )

    plan_response = execute_template(request)
    if not plan_response.success:
        logger.error(f"Patch plan failed: {plan_response.output}")
        sys.exit(1)

    patch_file = plan_response.output.strip()
    state.update(plan_file=patch_file)
    state.save("icdev_patch")
    logger.info(f"Patch plan created: {patch_file}")

    # Implement patch
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_PATCH_IMPLEMENTOR, "Implementing patch"),
    )

    impl_response = implement_plan(patch_file, run_id, logger, AGENT_PATCH_IMPLEMENTOR)
    if not impl_response.success:
        logger.error(f"Patch implementation failed: {impl_response.output}")
        sys.exit(1)

    # Commit
    commit_msg, error = create_commit(
        AGENT_PATCH_IMPLEMENTOR, issue_json, "/patch", run_id, logger
    )
    if error:
        commit_msg = f"{AGENT_PATCH_IMPLEMENTOR}: patch for issue #{issue_number}"

    success, error = commit_changes(commit_msg)
    if not success:
        logger.error(f"Commit failed: {error}")
        sys.exit(1)

    # Push and create PR/MR
    finalize_git_operations(state, logger, vcs)

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", "Patch workflow completed"),
    )

    state.save("icdev_patch")
    logger.info("Patch workflow completed")


if __name__ == "__main__":
    main()
