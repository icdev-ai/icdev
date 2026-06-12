# CUI // SP-CTI
"""ICDEV™ Build — implementation workflow.

Drives the ``icdev_implementor`` agent to realise the plan produced by
:mod:`tools.ci.workflows.icdev_plan` for a kanban run.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_build.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.git_ops import (  # noqa: E402
    commit_changes,
    create_branch,
    finalize_git_operations,
)
from tools.ci.modules.state import ICDevState  # noqa: E402
from tools.ci.modules.vcs import VCS  # noqa: E402
from tools.ci.modules.workflow_ops import (  # noqa: E402
    AGENT_IMPLEMENTOR,
    create_commit,
    format_issue_message,
    implement_plan,
)
from tools.testing.utils import setup_logger  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ────────────────────────────────────────────────────────────────────────────


def _parse_args(argv: List[str]) -> Optional[Dict[str, str]]:
    if len(argv) < 3:
        return None
    return {"issue_number": argv[1], "run_id": argv[2]}


def _bot_envelope(run_id: str, message: str) -> str:
    return format_issue_message(run_id, AGENT_IMPLEMENTOR, message)


def _coerce_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_fetch_issue(vcs: Any, issue_int: Optional[int]) -> Dict[str, Any]:
    """Pull issue metadata, swallowing every error so the build never
    aborts because the issue API hiccupped."""
    if issue_int is None:
        return {}
    fetch = getattr(vcs, "fetch_issue", None)
    if fetch is None:
        return {}
    try:
        result = fetch(issue_int)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _safe_comment(
    vcs: Any, logger: Any, issue_int: Optional[int], body: str,
) -> None:
    if issue_int is None:
        return
    try:
        vcs.comment_on_issue(issue_int, body)
    except Exception as exc:
        logger.warning("icdev_build: comment_on_issue raised: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        sys.stdout.write(
            "Usage: python tools/ci/workflows/icdev_build.py "
            "<issue-number> <run-id>\n"
            "Requires run-id from a previous icdev_plan run.\n"
        )
        return 1

    run_id = args["run_id"]
    logger = setup_logger(run_id, "icdev_build")
    logger.info(
        "ICDEV™ Build starting — run_id=%s issue=#%s",
        run_id, args["issue_number"],
    )

    state = ICDevState.load(run_id, logger=logger)
    issue_int = _coerce_int(args["issue_number"])

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_build: VCS initialization failed: %s", exc)
        return 1

    branch_name = state.get("branch_name")
    if not branch_name:
        logger.error(
            "icdev_build: state has no branch_name — run icdev_plan first"
        )
        return 1

    branched, branch_err = create_branch(branch_name)
    if not branched:
        logger.error("icdev_build: branch checkout failed: %s", branch_err)
        return 1
    logger.info("icdev_build: working on branch %s", branch_name)

    plan_file = state.get("plan_file")
    if not plan_file or not os.path.exists(plan_file):
        logger.error("icdev_build: plan file not found: %s", plan_file)
        return 1

    _safe_comment(vcs, logger, issue_int,
                  _bot_envelope(run_id, "Starting implementation"))

    logger.info("icdev_build: implementing plan %s", plan_file)
    response = implement_plan(plan_file, run_id, logger)
    if not getattr(response, "success", False):
        output = getattr(response, "output", "") or ""
        logger.error("icdev_build: implementation failed: %s", output[:500])
        _safe_comment(
            vcs, logger, issue_int,
            _bot_envelope(run_id, f"Implementation failed: {output}"),
        )
        return 1
    logger.info("icdev_build: implementation reported success")

    issue_class = state.get("issue_class", "/feature")
    issue_data = _safe_fetch_issue(vcs, issue_int)
    issue_json = json.dumps(issue_data)

    commit_msg, commit_err = create_commit(
        AGENT_IMPLEMENTOR, issue_json, issue_class, run_id, logger,
    )
    if commit_err or not commit_msg:
        logger.warning(
            "icdev_build: create_commit fell back: %s", commit_err
        )
        commit_msg = (
            f"{AGENT_IMPLEMENTOR}: implement plan for issue "
            f"#{args['issue_number']}"
        )

    committed, commit_failure = commit_changes(commit_msg)
    if not committed:
        logger.error("icdev_build: commit failed: %s", commit_failure)
        return 1
    logger.info("icdev_build: committed %s", commit_msg)

    try:
        finalize_git_operations(state, logger, vcs=vcs)
    except Exception as exc:
        logger.warning(
            "icdev_build: finalize_git_operations raised: %s", exc
        )

    _safe_comment(
        vcs, logger, issue_int,
        _bot_envelope(run_id, "Implementation committed and pushed"),
    )

    state.save("icdev_build")
    logger.info("icdev_build: phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
