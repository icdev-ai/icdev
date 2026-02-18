# CUI // SP-CTI
# ICDEV Workflow Operations — classify, branch, commit, PR helpers
# Adapted from ADW workflow_ops.py with dual platform support

"""
Shared workflow operations for ICDEV CI/CD pipelines.

Provides issue classification, branch name generation, commit message creation,
PR/MR creation, and workflow extraction using Claude Code slash commands.

Usage:
    from tools.ci.modules.workflow_ops import classify_issue, generate_branch_name
    issue_command, error = classify_issue(issue_json, run_id, logger)
    branch_name, error = generate_branch_name(issue_json, issue_command, run_id, logger)
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from tools.ci.modules.agent import execute_template, BOT_IDENTIFIER
from tools.ci.modules.state import ICDevState
from tools.testing.data_types import AgentTemplateRequest, AgentPromptResponse
from tools.testing.utils import parse_json

# Agent name constants (adapted from ADW)
AGENT_PLANNER = "icdev_planner"
AGENT_IMPLEMENTOR = "icdev_implementor"
AGENT_CLASSIFIER = "issue_classifier"
AGENT_BRANCH_GENERATOR = "branch_generator"
AGENT_PR_CREATOR = "pr_creator"

# Available ICDEV workflows for runtime validation
AVAILABLE_ICDEV_WORKFLOWS = [
    "icdev_plan",
    "icdev_build",
    "icdev_test",
    "icdev_review",
    "icdev_comply",
    "icdev_secure",
    "icdev_deploy",
    "icdev_document",
    "icdev_patch",
    "icdev_plan_build",
    "icdev_plan_build_test",
    "icdev_plan_build_test_review",
    "icdev_sdlc",
]


def format_issue_message(
    run_id: str, agent_name: str, message: str, session_id: Optional[str] = None
) -> str:
    """Format a message for issue comments with ICDEV tracking and bot identifier."""
    if session_id:
        return f"{BOT_IDENTIFIER} {run_id}_{agent_name}_{session_id}: {message}"
    return f"{BOT_IDENTIFIER} {run_id}_{agent_name}: {message}"


def extract_icdev_info(
    text: str, temp_run_id: str
) -> Tuple[Optional[str], Optional[str]]:
    """Extract ICDEV workflow and run_id from text using classify_workflow agent.
    Returns (workflow_command, run_id) tuple."""

    request = AgentTemplateRequest(
        agent_name="icdev_classifier",
        slash_command="/classify_workflow",
        args=[text],
        run_id=temp_run_id,
    )

    try:
        response = execute_template(request)

        if not response.success:
            print(f"Failed to classify ICDEV workflow: {response.output}")
            return None, None

        try:
            data = parse_json(response.output, dict)
            icdev_command = data.get("icdev_slash_command", "").replace("/", "")
            run_id = data.get("run_id")

            if icdev_command and icdev_command in AVAILABLE_ICDEV_WORKFLOWS:
                return icdev_command, run_id

            return None, None

        except (ValueError, TypeError):
            return None, None

    except Exception as e:
        print(f"Error calling classify_workflow: {e}")
        return None, None


def classify_issue(
    issue_json: str, run_id: str, logger: logging.Logger
) -> Tuple[Optional[str], Optional[str]]:
    """Classify issue and return appropriate slash command.
    Returns (command, error_message) tuple."""

    request = AgentTemplateRequest(
        agent_name=AGENT_CLASSIFIER,
        slash_command="/classify_issue",
        args=[issue_json],
        run_id=run_id,
    )

    logger.debug(f"Classifying issue...")
    response = execute_template(request)

    if not response.success:
        return None, response.output

    output = response.output.strip()
    classification_match = re.search(r"(/chore|/bug|/feature|/patch|0)", output)

    if classification_match:
        issue_command = classification_match.group(1)
    else:
        issue_command = output

    if issue_command == "0":
        return None, f"No command selected: {response.output}"

    if issue_command not in ["/chore", "/bug", "/feature", "/patch"]:
        return None, f"Invalid command selected: {response.output}"

    return issue_command, None


def generate_branch_name(
    issue_json: str, issue_class: str, run_id: str, logger: logging.Logger
) -> Tuple[Optional[str], Optional[str]]:
    """Generate and create a git branch for the issue.
    Returns (branch_name, error_message) tuple."""
    issue_type = issue_class.replace("/", "")

    request = AgentTemplateRequest(
        agent_name=AGENT_BRANCH_GENERATOR,
        slash_command="/generate_branch_name",
        args=[issue_type, run_id, issue_json],
        run_id=run_id,
    )

    response = execute_template(request)

    if not response.success:
        return None, response.output

    branch_name = response.output.strip()
    logger.info(f"Generated branch name: {branch_name}")
    return branch_name, None


def build_plan(
    issue_json: str, command: str, run_id: str, logger: logging.Logger
) -> AgentPromptResponse:
    """Build implementation plan for the issue."""
    request = AgentTemplateRequest(
        agent_name=AGENT_PLANNER,
        slash_command=command,
        args=[issue_json],
        run_id=run_id,
    )

    return execute_template(request)


def implement_plan(
    plan_file: str, run_id: str, logger: logging.Logger,
    agent_name: Optional[str] = None
) -> AgentPromptResponse:
    """Implement the plan using the /implement command."""
    implementor_name = agent_name or AGENT_IMPLEMENTOR

    request = AgentTemplateRequest(
        agent_name=implementor_name,
        slash_command="/implement",
        args=[plan_file],
        run_id=run_id,
    )

    return execute_template(request)


def create_commit(
    agent_name: str, issue_json: str, issue_class: str,
    run_id: str, logger: logging.Logger
) -> Tuple[Optional[str], Optional[str]]:
    """Create a git commit with a properly formatted message.
    Returns (commit_message, error_message) tuple."""
    issue_type = issue_class.replace("/", "")
    unique_agent_name = f"{agent_name}_committer"

    request = AgentTemplateRequest(
        agent_name=unique_agent_name,
        slash_command="/commit",
        args=[agent_name, issue_type, issue_json],
        run_id=run_id,
    )

    response = execute_template(request)

    if not response.success:
        return None, response.output

    commit_message = response.output.strip()
    logger.info(f"Created commit message: {commit_message}")
    return commit_message, None


def create_pull_request(
    branch_name: str, issue_json: str, state: ICDevState,
    logger: logging.Logger
) -> Tuple[Optional[str], Optional[str]]:
    """Create a pull request/merge request for the implemented changes.
    Returns (pr_url, error_message) tuple."""
    plan_file = state.get("plan_file") or "No plan file"
    run_id = state.get("run_id")

    request = AgentTemplateRequest(
        agent_name=AGENT_PR_CREATOR,
        slash_command="/pull_request",
        args=[branch_name, issue_json, plan_file, run_id],
        run_id=run_id,
    )

    response = execute_template(request)

    if not response.success:
        return None, response.output

    pr_url = response.output.strip()
    logger.info(f"Created PR/MR: {pr_url}")
    return pr_url, None


def ensure_run_id(
    issue_number: str,
    run_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Get run_id or create a new one and initialize state."""
    from tools.testing.utils import make_run_id

    if run_id:
        state = ICDevState.load(run_id, logger)
        if state.get("run_id") == run_id:
            if logger:
                logger.info(f"Found existing state for run_id: {run_id}")
            return run_id
        state = ICDevState(run_id, logger)
        state.update(run_id=run_id, issue_number=issue_number)
        state.save("ensure_run_id")
        return run_id

    new_run_id = make_run_id()
    state = ICDevState(new_run_id, logger)
    state.update(run_id=new_run_id, issue_number=issue_number)
    state.save("ensure_run_id")
    if logger:
        logger.info(f"Created new run_id and state: {new_run_id}")
    return new_run_id


def find_existing_branch_for_issue(
    issue_number: str, run_id: Optional[str] = None
) -> Optional[str]:
    """Find an existing branch for the given issue number."""
    result = subprocess.run(
        ["git", "branch", "-a"], capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        return None

    branches = result.stdout.strip().split("\n")

    for branch in branches:
        branch = branch.strip().replace("* ", "").replace("remotes/origin/", "")
        if f"-issue-{issue_number}-" in branch:
            if run_id and f"-icdev-{run_id}-" in branch:
                return branch
            elif not run_id:
                return branch

    return None
