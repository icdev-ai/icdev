# CUI // SP-CTI
"""ICDEV™ workflow operation helpers.

Shared library used by every ``tools/ci/workflows/icdev_*.py`` script.
Wraps the agent template invocations for issue classification, branch
naming, plan building, plan implementation, commit message generation,
and PR creation, plus a few small utilities (``ensure_run_id``,
``find_existing_branch_for_issue``, ``format_issue_message``,
``extract_icdev_info``).

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/modules/workflow_ops.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from tools.ci.modules.agent import BOT_IDENTIFIER, execute_template
from tools.ci.modules.state import ICDevState
from tools.testing.data_types import (
    AgentPromptResponse,
    AgentTemplateRequest,
)
from tools.testing.utils import parse_json


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

_module_logger = get_logger(__name__)


# ── Agent name constants ──────────────────────────────────────────────────
AGENT_PLANNER: str = "icdev_planner"
AGENT_IMPLEMENTOR: str = "icdev_implementor"
AGENT_CLASSIFIER: str = "issue_classifier"
AGENT_BRANCH_GENERATOR: str = "branch_generator"
AGENT_PR_CREATOR: str = "pr_creator"


# Whitelist of slash commands the classify_workflow agent is permitted
# to return.
AVAILABLE_ICDEV_WORKFLOWS: List[str] = [
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

_VALID_ISSUE_COMMANDS = {"/chore", "/bug", "/feature", "/patch"}
_CLASSIFY_RE = re.compile(r"(/chore|/bug|/feature|/patch|0)")


# ────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ────────────────────────────────────────────────────────────────────────────


def format_issue_message(
    run_id: str,
    agent_name: str,
    message: str,
    session_id: Optional[str] = None,
) -> str:
    """Format a comment body with the bot identifier and tracking key."""
    if session_id:
        return f"{BOT_IDENTIFIER} {run_id}_{agent_name}_{session_id}: {message}"
    return f"{BOT_IDENTIFIER} {run_id}_{agent_name}: {message}"


# ────────────────────────────────────────────────────────────────────────────
# Agent invocations
# ────────────────────────────────────────────────────────────────────────────


def extract_icdev_info(
    text: str,
    temp_run_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Use the classifier agent to pull an `icdev_*` slash command and
    a run id out of free-form text. Returns ``(command, run_id)`` or
    ``(None, None)`` on any failure."""
    request = AgentTemplateRequest(
        agent_name="icdev_classifier",
        slash_command="/classify_workflow",
        args=[text],
        run_id=temp_run_id,
    )
    try:
        response = execute_template(request)
    except Exception as exc:
        _module_logger.warning(
            "extract_icdev_info: agent invocation raised: %s", exc
        )
        return None, None

    if not getattr(response, "success", False):
        _module_logger.info(
            "extract_icdev_info: classifier returned non-success: %s",
            getattr(response, "output", "")[:200],
        )
        return None, None

    try:
        data = parse_json(getattr(response, "output", "") or "", dict)
    except (ValueError, TypeError):
        return None, None

    icdev_command = (data.get("icdev_slash_command") or "").lstrip("/")
    run_id = data.get("run_id")
    if icdev_command and icdev_command in AVAILABLE_ICDEV_WORKFLOWS:
        return icdev_command, run_id
    return None, None


def classify_issue(
    issue_json: str,
    run_id: str,
    logger: logging.Logger,
) -> Tuple[Optional[str], Optional[str]]:
    """Ask the classifier agent for one of /chore, /bug, /feature,
    /patch (or "0" for "no command")."""
    request = AgentTemplateRequest(
        agent_name=AGENT_CLASSIFIER,
        slash_command="/classify_issue",
        args=[issue_json],
        run_id=run_id,
    )
    logger.debug("workflow_ops.classify_issue: invoking classifier")
    response = execute_template(request)

    if not getattr(response, "success", False):
        return None, getattr(response, "output", "agent failure")

    output = (getattr(response, "output", "") or "").strip()
    match = _CLASSIFY_RE.search(output)
    issue_command = match.group(1) if match else output

    if issue_command == "0":
        return None, f"No command selected: {output}"
    if issue_command not in _VALID_ISSUE_COMMANDS:
        return None, f"Invalid command selected: {output}"
    return issue_command, None


def generate_branch_name(
    issue_json: str,
    issue_class: str,
    run_id: str,
    logger: logging.Logger,
) -> Tuple[Optional[str], Optional[str]]:
    """Ask the branch_generator agent for a branch name string."""
    issue_type = issue_class.lstrip("/")
    request = AgentTemplateRequest(
        agent_name=AGENT_BRANCH_GENERATOR,
        slash_command="/generate_branch_name",
        args=[issue_type, run_id, issue_json],
        run_id=run_id,
    )
    response = execute_template(request)
    if not getattr(response, "success", False):
        return None, getattr(response, "output", "agent failure")
    branch_name = (getattr(response, "output", "") or "").strip()
    logger.info("workflow_ops.generate_branch_name: %s", branch_name)
    return branch_name, None


def build_plan(
    issue_json: str,
    command: str,
    run_id: str,
    logger: logging.Logger,
) -> AgentPromptResponse:
    """Ask the planner agent to build an implementation plan."""
    request = AgentTemplateRequest(
        agent_name=AGENT_PLANNER,
        slash_command=command,
        args=[issue_json],
        run_id=run_id,
    )
    return execute_template(request)


def implement_plan(
    plan_file: str,
    run_id: str,
    logger: logging.Logger,
    agent_name: Optional[str] = None,
) -> AgentPromptResponse:
    """Ask the implementor agent (or a caller-supplied alias) to apply
    a plan file via the ``/implement`` slash command."""
    implementor = agent_name or AGENT_IMPLEMENTOR
    request = AgentTemplateRequest(
        agent_name=implementor,
        slash_command="/implement",
        args=[plan_file],
        run_id=run_id,
    )
    return execute_template(request)


def create_commit(
    agent_name: str,
    issue_json: str,
    issue_class: str,
    run_id: str,
    logger: logging.Logger,
) -> Tuple[Optional[str], Optional[str]]:
    """Ask the agent for a commit message via ``/commit``. Uses a
    transient agent name ``<agent>_committer`` so the agent's session
    can be isolated from the main implementor session."""
    issue_type = issue_class.lstrip("/")
    committer_name = f"{agent_name}_committer"
    request = AgentTemplateRequest(
        agent_name=committer_name,
        slash_command="/commit",
        args=[agent_name, issue_type, issue_json],
        run_id=run_id,
    )
    response = execute_template(request)
    if not getattr(response, "success", False):
        return None, getattr(response, "output", "agent failure")
    commit_msg = (getattr(response, "output", "") or "").strip()
    logger.info("workflow_ops.create_commit: %s", commit_msg)
    return commit_msg, None


def create_pull_request(
    branch_name: str,
    issue_json: str,
    state: ICDevState,
    logger: logging.Logger,
) -> Tuple[Optional[str], Optional[str]]:
    """Ask the PR-creator agent to open a PR via ``/pull_request``."""
    plan_file = state.get("plan_file") or "No plan file"
    run_id = state.get("run_id")
    request = AgentTemplateRequest(
        agent_name=AGENT_PR_CREATOR,
        slash_command="/pull_request",
        args=[branch_name, issue_json, plan_file, run_id],
        run_id=run_id,
    )
    response = execute_template(request)
    if not getattr(response, "success", False):
        return None, getattr(response, "output", "agent failure")
    pr_url = (getattr(response, "output", "") or "").strip()
    logger.info("workflow_ops.create_pull_request: %s", pr_url)
    return pr_url, None


# ────────────────────────────────────────────────────────────────────────────
# Run id management
# ────────────────────────────────────────────────────────────────────────────


def ensure_run_id(
    issue_number: str,
    run_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Resolve or create a run id for the given issue.

    * If ``run_id`` is provided AND the on-disk state for that id
      already records the same id, reuse it.
    * Otherwise create a new ``ICDevState`` (with the supplied id, or a
      freshly-generated one) and save it.
    """
    from tools.testing.utils import make_run_id

    log = logger if logger is not None else _module_logger

    if run_id:
        existing = ICDevState.load(run_id, logger=log)
        if existing.get("run_id") == run_id:
            log.info("workflow_ops.ensure_run_id: reusing %s", run_id)
            return run_id
        fresh = ICDevState(run_id, logger=log)
        fresh.update(run_id=run_id, issue_number=issue_number)
        fresh.save("ensure_run_id")
        return run_id

    new_run_id = make_run_id()
    state = ICDevState(new_run_id, logger=log)
    state.update(run_id=new_run_id, issue_number=issue_number)
    state.save("ensure_run_id")
    log.info("workflow_ops.ensure_run_id: created %s", new_run_id)
    return new_run_id


# ────────────────────────────────────────────────────────────────────────────
# Branch lookup
# ────────────────────────────────────────────────────────────────────────────


def find_existing_branch_for_issue(
    issue_number: str,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """Walk ``git branch -a`` looking for a branch tagged with the
    given issue number (and optional run id)."""
    try:
        proc = subprocess.run(
            ["git", "branch", "-a"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None

    needle_issue = f"-issue-{issue_number}-"
    needle_run = f"-icdev-{run_id}-" if run_id else None

    for raw in (proc.stdout or "").splitlines():
        branch = raw.strip().replace("* ", "").replace("remotes/origin/", "")
        if needle_issue not in branch:
            continue
        if needle_run is not None:
            if needle_run not in branch:
                continue
            return branch
        return branch
    return None
