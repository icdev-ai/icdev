# CUI // SP-CTI
"""ICDEV™ Review — automated code-review workflow.

A short CI script that runs the ``icdev_reviewer`` agent against the
spec/plan file recorded in the kanban run state, posts a comment on the
linked issue, and finalises git operations.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_review.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.agent import execute_template  # noqa: E402
from tools.ci.modules.git_ops import (  # noqa: E402
    commit_changes,
    finalize_git_operations,
)
from tools.ci.modules.state import ICDevState  # noqa: E402
from tools.ci.modules.vcs import VCS  # noqa: E402
from tools.ci.modules.workflow_ops import format_issue_message  # noqa: E402
from tools.testing.data_types import AgentTemplateRequest  # noqa: E402
from tools.testing.utils import setup_logger  # noqa: E402


# ── Public constants (preserved for callers that import them) ─────────────
AGENT_REVIEWER: str = "icdev_reviewer"
MAX_REVIEW_RETRY: int = 3

# Cap on how much agent output we embed in an issue comment so we don't
# blow past GitHub/GitLab body limits or paste a wall of text.
OUTPUT_PREVIEW_CHARS: int = 2000


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def run_review(
    plan_file: str,
    run_id: str,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """Invoke the reviewer agent against ``plan_file``.

    Returns the canonical dict shape documented in the spec, regardless
    of whether the underlying ``execute_template`` returns an object or
    a dict.
    """
    request = AgentTemplateRequest(
        agent_name=AGENT_REVIEWER,
        slash_command="/icdev-review",
        args=[plan_file],
        run_id=run_id,
    )
    response = execute_template(request)
    return {
        "success": bool(getattr(response, "success", False)),
        "output": getattr(response, "output", "") or "",
        "session_id": getattr(response, "session_id", None),
    }


# ────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ────────────────────────────────────────────────────────────────────────────


def _parse_args(argv: List[str]) -> Optional[Dict[str, str]]:
    if len(argv) < 3:
        return None
    return {"issue_number": argv[1], "run_id": argv[2]}


def _bot_envelope(run_id: str, message: str) -> str:
    return format_issue_message(run_id, AGENT_REVIEWER, message)


def _coerce_issue_number(value: str, logger: logging.Logger) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.error("icdev_review: issue number is not numeric: %r", value)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        sys.stdout.write(
            "Usage: python tools/ci/workflows/icdev_review.py "
            "<issue-number> <run-id>\n"
        )
        return 1

    run_id = args["run_id"]
    logger = setup_logger(run_id, "icdev_review")
    logger.info(
        "ICDEV™ Review starting — run_id=%s issue=#%s",
        run_id, args["issue_number"],
    )

    state = ICDevState.load(run_id, logger=logger)

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_review: VCS initialization failed: %s", exc)
        return 1

    issue_int = _coerce_issue_number(args["issue_number"], logger)

    plan_file = state.get("plan_file")
    if not plan_file or not os.path.exists(plan_file):
        logger.error("icdev_review: plan file not found: %s", plan_file)
        if issue_int is not None:
            try:
                vcs.comment_on_issue(
                    issue_int,
                    _bot_envelope(run_id, "No plan file found — cannot review"),
                )
            except Exception as exc:
                logger.warning(
                    "icdev_review: comment_on_issue raised: %s", exc
                )
        return 1

    if issue_int is not None:
        try:
            vcs.comment_on_issue(
                issue_int,
                _bot_envelope(run_id, "Starting code review"),
            )
        except Exception as exc:
            logger.warning(
                "icdev_review: starting comment failed: %s", exc
            )

    review = run_review(plan_file, run_id, logger)
    preview = (review.get("output") or "")[:OUTPUT_PREVIEW_CHARS]

    if review["success"]:
        logger.info("icdev_review: agent reported success")
        body = f"## Code Review Complete\n\n{preview}"
    else:
        logger.warning("icdev_review: agent reported issues: %s", preview[:500])
        body = f"## Code Review Issues\n\n{preview}"

    if issue_int is not None:
        try:
            vcs.comment_on_issue(issue_int, _bot_envelope(run_id, body))
        except Exception as exc:
            logger.warning(
                "icdev_review: result comment failed: %s", exc
            )

    committed, commit_err = commit_changes(
        f"{AGENT_REVIEWER}: review results for issue #{args['issue_number']}",
    )
    if committed:
        try:
            finalize_git_operations(state, logger, vcs=vcs)
        except Exception as exc:
            logger.warning(
                "icdev_review: finalize_git_operations raised: %s", exc
            )
    else:
        logger.warning("icdev_review: commit failed: %s", commit_err)

    state.save("icdev_review")
    logger.info("icdev_review: phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
