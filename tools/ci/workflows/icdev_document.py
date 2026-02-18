# CUI // SP-CTI
# ICDEV Document — Documentation generation workflow
# Adapted from ADW adw_document.py with dual platform support

"""
ICDEV Document — Generate documentation for implemented features.

Usage:
    python tools/ci/workflows/icdev_document.py <issue-number> <run-id>

Requires:
    - run-id from a previous workflow run
    - Branch name and plan file in state

Workflow:
    1. Load state from previous phase
    2. Check for changes against main branch
    3. Generate feature documentation via Claude Code
    4. Commit documentation
    5. Push and update PR/MR
"""

import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState
from tools.ci.modules.git_ops import create_branch, commit_changes, finalize_git_operations
from tools.ci.modules.vcs import VCS
from tools.ci.modules.agent import execute_template
from tools.ci.modules.workflow_ops import (
    format_issue_message,
)
from tools.testing.data_types import AgentTemplateRequest
from tools.testing.utils import setup_logger

AGENT_DOCUMENTER = "icdev_documenter"


def check_for_changes(logger: logging.Logger) -> bool:
    """Check if there are changes between current branch and main."""
    try:
        result = subprocess.run(
            ["git", "diff", "origin/main", "--stat"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
        )
        has_changes = bool(result.stdout.strip())
        if not has_changes:
            logger.info("No changes detected against origin/main")
        return has_changes
    except Exception:
        return True  # Assume changes if check fails


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python tools/ci/workflows/icdev_document.py <issue-number> <run-id>")
        print("\nRequires run-id from a previous workflow run.")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2]

    state = ICDevState.load(run_id)
    logger = setup_logger(run_id, "icdev_document")
    logger.info(f"ICDEV Document starting — run_id: {run_id}, issue: #{issue_number}")

    try:
        vcs = VCS()
    except ValueError as e:
        logger.error(f"VCS initialization failed: {e}")
        sys.exit(1)

    # Get branch from state
    branch_name = state.get("branch_name")
    if not branch_name:
        logger.error("No branch_name in state. Run icdev_plan first.")
        sys.exit(1)

    # Checkout branch
    success, _ = create_branch(branch_name)
    if not success:
        logger.error(f"Failed to checkout branch: {branch_name}")
        sys.exit(1)

    # Check for changes
    if not check_for_changes(logger):
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, "ops", "No changes to document — skipping"),
        )
        logger.info("No changes to document")
        state.save("icdev_document")
        return

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_DOCUMENTER, "Generating documentation"),
    )

    # Generate documentation
    spec_path = state.get("plan_file", "")

    request = AgentTemplateRequest(
        agent_name=AGENT_DOCUMENTER,
        slash_command="/document",
        args=[run_id, spec_path],
        run_id=run_id,
    )

    response = execute_template(request)

    if response.success:
        doc_path = response.output.strip()
        logger.info(f"Documentation generated: {doc_path}")

        # Commit
        commit_msg = f"{AGENT_DOCUMENTER}: document feature for issue #{issue_number}"
        success, error = commit_changes(commit_msg)
        if success:
            finalize_git_operations(state, logger, vcs)

        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(
                run_id, AGENT_DOCUMENTER,
                f"Documentation created at `{doc_path}` and committed"
            ),
        )
    else:
        logger.error(f"Documentation generation failed: {response.output}")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(
                run_id, AGENT_DOCUMENTER,
                f"Documentation generation failed: {response.output[:500]}"
            ),
        )
        sys.exit(1)

    state.save("icdev_document")
    logger.info("Documentation phase completed")


if __name__ == "__main__":
    main()
