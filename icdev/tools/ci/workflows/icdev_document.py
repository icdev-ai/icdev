# CUI // SP-CTI
"""ICDEV™ Document — feature documentation workflow.

Drives the ``icdev_documenter`` agent to generate documentation for the
changes on a kanban run's feature branch, then commit and push.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_document.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import logging
import subprocess
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
from tools.ci.modules.workflow_ops import format_issue_message  # noqa: E402
from tools.testing.data_types import AgentTemplateRequest  # noqa: E402
from tools.testing.utils import setup_logger  # noqa: E402


AGENT_DOCUMENTER: str = "icdev_documenter"


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def check_for_changes(logger: logging.Logger) -> bool:
    """Return True if the working tree has any diff against origin/main.

    On any subprocess error the helper assumes there *are* changes —
    we'd rather run the documenter unnecessarily than silently skip a
    legitimate documentation pass.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "origin/main", "--stat"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        logger.warning("icdev_document: git diff probe raised: %s", exc)
        return True
    has_changes = bool((proc.stdout or "").strip())
    if not has_changes:
        logger.info("icdev_document: no changes detected against origin/main")
    return has_changes


def _parse_args(argv: List[str]) -> Optional[Dict[str, str]]:
    if len(argv) < 3:
        return None
    return {"issue_number": argv[1], "run_id": argv[2]}


def _bot_envelope(run_id: str, agent: str, message: str) -> str:
    return format_issue_message(run_id, agent, message)


def _coerce_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_comment(
    vcs: Any, logger: Any, issue_int: Optional[int], body: str,
) -> None:
    if issue_int is None:
        return
    try:
        vcs.comment_on_issue(issue_int, body)
    except Exception as exc:
        logger.warning("icdev_document: comment_on_issue raised: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv)
    if args is None:
        sys.stdout.write(
            "Usage: python tools/ci/workflows/icdev_document.py "
            "<issue-number> <run-id>\n"
            "Requires run-id from a previous workflow run.\n"
        )
        return 1

    run_id = args["run_id"]
    logger = setup_logger(run_id, "icdev_document")
    logger.info(
        "ICDEV™ Document starting — run_id=%s issue=#%s",
        run_id, args["issue_number"],
    )

    state = ICDevState.load(run_id, logger=logger)
    issue_int = _coerce_int(args["issue_number"])

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_document: VCS initialization failed: %s", exc)
        return 1

    branch_name = state.get("branch_name")
    if not branch_name:
        logger.error("icdev_document: no branch_name in state")
        return 1

    branched, _ = create_branch(branch_name)
    if not branched:
        logger.error("icdev_document: branch checkout failed: %s", branch_name)
        return 1

    if not check_for_changes(logger):
        _safe_comment(
            vcs, logger, issue_int,
            _bot_envelope(run_id, "ops", "No changes to document — skipping"),
        )
        state.save("icdev_document")
        return 0

    _safe_comment(
        vcs, logger, issue_int,
        _bot_envelope(run_id, AGENT_DOCUMENTER, "Generating documentation"),
    )

    spec_path = state.get("plan_file", "")
    request = AgentTemplateRequest(
        agent_name=AGENT_DOCUMENTER,
        slash_command="/document",
        args=[run_id, spec_path],
        run_id=run_id,
    )

    response = execute_template(request)
    if not getattr(response, "success", False):
        output = getattr(response, "output", "") or ""
        logger.error("icdev_document: agent failed: %s", output[:500])
        _safe_comment(
            vcs, logger, issue_int,
            _bot_envelope(
                run_id, AGENT_DOCUMENTER,
                f"Documentation generation failed: {output[:500]}",
            ),
        )
        return 1

    doc_path = (getattr(response, "output", "") or "").strip()
    logger.info("icdev_document: documentation generated at %s", doc_path)

    commit_msg = (
        f"{AGENT_DOCUMENTER}: document feature for issue "
        f"#{args['issue_number']}"
    )
    committed, commit_err = commit_changes(commit_msg)
    if committed:
        try:
            finalize_git_operations(state, logger, vcs=vcs)
        except Exception as exc:
            logger.warning(
                "icdev_document: finalize_git_operations raised: %s", exc
            )
    else:
        logger.warning("icdev_document: commit failed: %s", commit_err)

    _safe_comment(
        vcs, logger, issue_int,
        _bot_envelope(
            run_id, AGENT_DOCUMENTER,
            f"Documentation created at `{doc_path}` and committed",
        ),
    )

    state.save("icdev_document")
    logger.info("icdev_document: phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
