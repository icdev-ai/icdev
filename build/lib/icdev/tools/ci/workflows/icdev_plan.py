# CUI // SP-CTI
"""ICDEV™ Plan — agentic planning workflow.

Kicks off a kanban run: fetch the linked issue, classify it, generate
the feature branch, ask the planner agent for an implementation plan,
commit, and push.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/workflows/icdev_plan.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
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
    AGENT_PLANNER,
    build_plan,
    classify_issue,
    create_commit,
    ensure_run_id,
    format_issue_message,
    generate_branch_name,
)
from tools.testing.utils import setup_logger  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Environment probe
# ────────────────────────────────────────────────────────────────────────────


def check_env_vars(logger: logging.Logger) -> None:
    """Verify at least one LLM provider is reachable for ``code_generation``.

    Probes (in order): Claude Code CLI on PATH, then LLMRouter for any
    provider on the ``code_generation`` chain. Exits 1 with a multi-line
    error message if neither is available.
    """
    cli_path_env = os.getenv("CLAUDE_CODE_PATH", "claude")
    cli = shutil.which(cli_path_env)
    if cli:
        logger.info("icdev_plan: Claude Code CLI at %s — using session auth", cli)
        return

    try:
        from tools.llm.router import LLMRouter
    except ImportError as exc:
        logger.error("icdev_plan: LLMRouter import failed: %s", exc)
        sys.exit(1)

    try:
        router = LLMRouter()
        provider, model_id, _model_cfg = router.get_provider_for_function(
            "code_generation"
        )
    except Exception as exc:
        logger.warning("icdev_plan: LLMRouter probe raised: %s", exc)
        provider = None
        model_id = ""

    if provider is not None:
        logger.info(
            "icdev_plan: LLM provider available — %s / %s",
            getattr(provider, "provider_name", "?"), model_id,
        )
        return

    hints: List[str] = []
    if os.getenv("ANTHROPIC_API_KEY"):
        hints.append(
            "ANTHROPIC_API_KEY is set but no provider matched it — "
            "check args/llm_config.yaml"
        )
    if os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_PROFILE"):
        hints.append(
            "AWS credentials present — Bedrock provider may need to be "
            "enabled in args/llm_config.yaml"
        )
    if os.getenv("OLLAMA_HOST") or os.getenv("OLLAMA_BASE_URL"):
        hints.append(
            "Ollama env vars set — confirm `ollama serve` is running "
            "and the model is pulled"
        )

    logger.error(
        "icdev_plan: no LLM provider reachable for function="
        "code_generation. Either:\n"
        "  1. Install Claude Code CLI (VSCode extension or CLI login),\n"
        "  2. Configure a provider in args/llm_config.yaml "
        "(Bedrock, Vertex, OCI, watsonx, Ollama, Anthropic API), or\n"
        "  3. Set ANTHROPIC_API_KEY for direct API access (legacy path)."
    )
    for hint in hints:
        logger.error("  hint: %s", hint)
    sys.exit(1)


# ────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ────────────────────────────────────────────────────────────────────────────


def _bot(run_id: str, agent: str, message: str) -> str:
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
        logger.warning("icdev_plan: comment_on_issue raised: %s", exc)


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
            "Usage: python tools/ci/workflows/icdev_plan.py "
            "<issue-number> [run-id]\n"
        )
        return 1

    issue_str = args["issue_number"]
    issue_int = _coerce_int(issue_str)
    run_id = ensure_run_id(issue_str, args["run_id"])

    logger = setup_logger(run_id, "icdev_plan")
    logger.info(
        "ICDEV™ Plan starting — run_id=%s issue=#%s", run_id, issue_str
    )

    state = ICDevState.load(run_id, logger=logger)
    check_env_vars(logger)

    try:
        vcs = VCS()
    except ValueError as exc:
        logger.error("icdev_plan: VCS initialization failed: %s", exc)
        return 1

    state.update(
        platform="gitlab" if getattr(vcs, "is_gitlab", False) else "github"
    )
    state.save("icdev_plan")

    if issue_int is None:
        logger.error("icdev_plan: issue_number is not numeric: %r", issue_str)
        return 1

    try:
        issue_data = vcs.fetch_issue(issue_int) or {}
    except Exception as exc:
        logger.error("icdev_plan: fetch_issue raised: %s", exc)
        return 1
    issue_json = json.dumps(issue_data)

    _safe_comment(vcs, logger, issue_int,
                  _bot(run_id, "ops", "Starting planning phase"))

    issue_command, classify_err = classify_issue(issue_json, run_id, logger)
    if classify_err:
        logger.error("icdev_plan: classification failed: %s", classify_err)
        _safe_comment(
            vcs, logger, issue_int,
            _bot(run_id, "ops", f"Classification failed: {classify_err}"),
        )
        return 1

    state.update(issue_class=issue_command)
    state.save("icdev_plan")
    logger.info("icdev_plan: classified as %s", issue_command)
    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, "ops", f"Issue classified as: {issue_command}"),
    )

    branch_name, branch_err = generate_branch_name(
        issue_json, issue_command, run_id, logger,
    )
    if branch_err or not branch_name:
        logger.error("icdev_plan: branch name generation failed: %s", branch_err)
        return 1

    branched, branch_failure = create_branch(branch_name)
    if not branched:
        logger.error("icdev_plan: branch creation failed: %s", branch_failure)
        return 1

    state.update(branch_name=branch_name)
    state.save("icdev_plan")
    logger.info("icdev_plan: working on branch %s", branch_name)
    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, "ops", f"Working on branch: {branch_name}"),
    )

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, AGENT_PLANNER, "Building implementation plan"),
    )

    plan_response = build_plan(issue_json, issue_command, run_id, logger)
    if not getattr(plan_response, "success", False):
        output = getattr(plan_response, "output", "") or ""
        logger.error("icdev_plan: plan generation failed: %s", output[:500])
        _safe_comment(
            vcs, logger, issue_int,
            _bot(run_id, AGENT_PLANNER, f"Plan failed: {output}"),
        )
        return 1

    plan_file_path = (getattr(plan_response, "output", "") or "").strip()
    if not plan_file_path or not os.path.exists(plan_file_path):
        logger.error("icdev_plan: plan file not found at %s", plan_file_path)
        return 1

    state.update(plan_file=plan_file_path)
    state.save("icdev_plan")
    logger.info("icdev_plan: plan file %s", plan_file_path)

    commit_msg, commit_err = create_commit(
        AGENT_PLANNER, issue_json, issue_command, run_id, logger,
    )
    if commit_err or not commit_msg:
        logger.error("icdev_plan: create_commit failed: %s", commit_err)
        return 1

    committed, commit_failure = commit_changes(commit_msg)
    if not committed:
        logger.error("icdev_plan: commit failed: %s", commit_failure)
        return 1
    logger.info("icdev_plan: committed %s", commit_msg)

    try:
        finalize_git_operations(state, logger, vcs=vcs)
    except Exception as exc:
        logger.warning("icdev_plan: finalize_git_operations raised: %s", exc)

    _safe_comment(
        vcs, logger, issue_int,
        _bot(run_id, "ops", "Planning phase completed"),
    )
    state.save("icdev_plan")
    logger.info("icdev_plan: phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
