# CUI // SP-CTI
"""ICDEV™ Patch — focused single-issue patch workflow.

Looks for the keyword ``icdev_patch`` in an issue body or its comments,
asks the patch-planner agent to draft a tiny patch plan from that
content, hands the plan to the patch-implementor agent, then commits
and pushes.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_patch.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.agent import execute_template  # noqa: E402
from tools.ci.modules.git_ops import (  # noqa: E402
    commit_changes,
    create_branch,
    finalize_git_operations,
)
from tools.ci.modules.state import ICDevState  # noqa: E402
from tools.ci.modules.vcs import VCS  # noqa: E402
from tools.ci.modules.workflow_ops import (  # noqa: E402
    classify_issue,
    create_commit,
    ensure_run_id,
    format_issue_message,
    generate_branch_name,
    implement_plan,
)
from tools.testing.data_types import AgentTemplateRequest  # noqa: E402
from tools.testing.utils import setup_logger  # noqa: E402


AGENT_PATCH_PLANNER: str = "patch_planner"
AGENT_PATCH_IMPLEMENTOR: str = "patch_implementor"

_KEYWORD: str = "icdev_patch"


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _coerce_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bot(run_id: str, agent: str, message: str) -> str:
    return format_issue_message(run_id, agent, message)


def _safe_comment(
    vcs: Any, logger: Any, issue_int: Optional[int], body: str,
) -> None:
    if issue_int is None:
        return
    try:
        vcs.comment_on_issue(issue_int, body)
    except Exception as exc:
        logger.warning("icdev_patch: comment_on_issue raised: %s", exc)


def get_patch_content(
    issue_data: Dict[str, Any],
    vcs: Any,
    issue_number: int,
    logger: logging.Logger,
) -> str:
    """Resolve the patch instructions from the issue + its comments.

    Precedence:
        1. Most recent comment whose body contains ``icdev_patch``.
        2. Issue body if it contains ``icdev_patch``.
        3. Fallback: full issue title + body verbatim.
    """
    try:
        comments = vcs.fetch_issue_comments(issue_number) or []
    except Exception as exc:
        logger.warning("icdev_patch: fetch_issue_comments raised: %s", exc)
        comments = []

    for comment in reversed(comments):
        body = comment.get("body") or comment.get("note") or ""
        if _KEYWORD in body.lower():
            logger.info("icdev_patch: found '%s' in comment", _KEYWORD)
            return body

    body = issue_data.get("body") or issue_data.get("description") or ""
    title = issue_data.get("title") or ""
    if _KEYWORD in body.lower():
        logger.info("icdev_patch: found '%s' in issue body", _KEYWORD)
        return f"Issue #{issue_number}: {title}\n\n{body}"

    logger.info(
        "icdev_patch: no '%s' keyword — using full issue as patch request",
        _KEYWORD,
    )
    return f"Issue #{issue_number}: {title}\n\n{body}"


def _parse_args(argv: List[str]) -> Optional[Dict[str, Optional[str]]]:
    if len(argv) < 2:
        return None
    return {
        "issue_number": argv[1],
        "run_id": argv[2] if len(argv) > 2 else None,
    }


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        sys.stdout.write(
            "Usage: python tools/ci/workflows/icdev_patch.py "
            "<issue-number> [run-id]\n"
        )
        return 1

    issue_str = args["issue_number"]
    issue_int = _coerce_int(issue_str)

    run_id = ensure_run_id(issue_str, args["run_id"])
    logger = setup_logger(run_id, "icdev_patch")
    logger.info(
        "ICDEV™ Patch starting — run_id=%s issue=#%s", run_id, issue_str
    )

    state = ICDevState.load(run_id, logger=logger)

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_patch: VCS initialization failed: %s", exc)
        return 1

    state.update(platform="gitlab" if getattr(vcs, "is_gitlab", False)
                 else "github")

    if issue_int is None:
        logger.error("icdev_patch: issue_number is not numeric: %r", issue_str)
        return 1

    try:
        issue_data = vcs.fetch_issue(issue_int) or {}
    except Exception as exc:
        logger.error("icdev_patch: failed to fetch issue: %s", exc)
        return 1
    issue_json = json.dumps(issue_data)

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, "ops", "Starting patch workflow"),
    )

    branch_name = state.get("branch_name")
    if not branch_name:
        issue_command, _classify_err = classify_issue(
            issue_json, run_id, logger,
        )
        issue_command = issue_command or "/patch"
        branch_name, branch_err = generate_branch_name(
            issue_json, issue_command, run_id, logger,
        )
        if branch_err or not branch_name:
            logger.error(
                "icdev_patch: branch name generation failed: %s", branch_err
            )
            return 1

    branched, branch_err = create_branch(branch_name)
    if not branched:
        logger.error("icdev_patch: branch operation failed: %s", branch_err)
        return 1

    state.update(branch_name=branch_name, issue_class="/patch")
    state.save("icdev_patch")
    logger.info("icdev_patch: working on branch %s", branch_name)

    patch_content = get_patch_content(issue_data, vcs, issue_int, logger)

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, AGENT_PATCH_PLANNER, "Creating patch plan"),
    )

    plan_request = AgentTemplateRequest(
        agent_name=AGENT_PATCH_PLANNER,
        slash_command="/patch",
        args=[run_id, patch_content],
        run_id=run_id,
    )
    plan_response = execute_template(plan_request)
    if not getattr(plan_response, "success", False):
        output = getattr(plan_response, "output", "") or ""
        logger.error("icdev_patch: planner failed: %s", output[:500])
        return 1

    patch_file = (getattr(plan_response, "output", "") or "").strip()
    state.update(plan_file=patch_file)
    state.save("icdev_patch")
    logger.info("icdev_patch: patch plan created at %s", patch_file)

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, AGENT_PATCH_IMPLEMENTOR, "Implementing patch"),
    )

    impl_response = implement_plan(
        patch_file, run_id, logger, AGENT_PATCH_IMPLEMENTOR,
    )
    if not getattr(impl_response, "success", False):
        output = getattr(impl_response, "output", "") or ""
        logger.error("icdev_patch: implementor failed: %s", output[:500])
        return 1

    commit_msg, commit_err = create_commit(
        AGENT_PATCH_IMPLEMENTOR, issue_json, "/patch", run_id, logger,
    )
    if commit_err or not commit_msg:
        commit_msg = f"{AGENT_PATCH_IMPLEMENTOR}: patch for issue #{issue_str}"

    committed, commit_failure = commit_changes(commit_msg)
    if not committed:
        logger.error("icdev_patch: commit failed: %s", commit_failure)
        return 1

    try:
        finalize_git_operations(state, logger, vcs=vcs)
    except Exception as exc:
        logger.warning(
            "icdev_patch: finalize_git_operations raised: %s", exc
        )

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, "ops", "Patch workflow completed"),
    )

    state.save("icdev_patch")
    logger.info("icdev_patch: phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
