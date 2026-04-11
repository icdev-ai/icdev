# [TEMPLATE: CUI // SP-CTI]
# ICDEV™ Plan — Agentic planning workflow
# Adapted from ADW adw_plan.py with dual platform support

"""
ICDEV™ Plan — Issue classification, branch creation, and plan generation.

Usage:
    python tools/ci/workflows/icdev_plan.py <issue-number> [run-id]

Workflow:
    1. Fetch issue details (GitHub or GitLab)
    2. Classify issue type (/chore, /bug, /feature)
    3. Create feature branch
    4. Generate implementation plan via Claude Code
    5. Commit plan
    6. Push and create PR/MR
"""

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState  # noqa: E402
from tools.ci.modules.git_ops import create_branch, commit_changes, finalize_git_operations  # noqa: E402
from tools.ci.modules.vcs import VCS  # noqa: E402
from tools.ci.modules.workflow_ops import (  # noqa: E402
    classify_issue,
    build_plan,
    generate_branch_name,
    create_commit,
    format_issue_message,
    ensure_run_id,
    AGENT_PLANNER,
)
from tools.testing.utils import setup_logger  # noqa: E402


def check_env_vars(logger: logging.Logger) -> None:
    """Check that at least one LLM provider is reachable for code_generation.

    OPT-32: ICDEV is LLM-agnostic. The plan workflow needs a code_generation
    provider — Claude Code CLI is the most ergonomic option, but Bedrock,
    Vertex, OCI GenAI, watsonx, and Ollama all qualify. We probe the LLMRouter
    chain rather than hard-checking for `claude` on PATH or ANTHROPIC_API_KEY.
    """
    import shutil

    # 1. Claude Code CLI on PATH = session auth works (VSCode extension or CLI login)
    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    if shutil.which(claude_path):
        logger.info(f"Claude Code CLI found at: {shutil.which(claude_path)} — using session auth")
        return

    # 2. Probe LLMRouter for ANY reachable provider on the code_generation chain.
    #    This covers Bedrock, Vertex, OCI, watsonx, Ollama, OpenAI-compatible
    #    endpoints, and direct Anthropic API — anything configured in
    #    args/llm_config.yaml.
    try:
        from tools.llm.router import LLMRouter
    except ImportError as exc:
        logger.error(f"LLMRouter import failed: {exc}")
        sys.exit(1)

    try:
        router = LLMRouter()
        provider, model_id, model_cfg = router.get_provider_for_function("code_generation")
        if provider is not None:
            logger.info(
                f"LLM provider available: {provider.provider_name} / {model_id} "
                f"(via LLMRouter for function=code_generation)"
            )
            return
    except Exception as exc:
        logger.warning(f"LLMRouter probe failed: {exc}")

    # 3. Last-resort hint — check for direct API keys so the error message can
    #    suggest the simplest fix for the operator's environment.
    hints = []
    if os.getenv("ANTHROPIC_API_KEY"):
        hints.append("ANTHROPIC_API_KEY is set but no provider matched it — check args/llm_config.yaml")
    if os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_PROFILE"):
        hints.append("AWS credentials present — Bedrock provider may need to be enabled in args/llm_config.yaml")
    if os.getenv("OLLAMA_HOST") or os.getenv("OLLAMA_BASE_URL"):
        hints.append("Ollama env vars set — confirm ollama serve is running and the model is pulled")

    logger.error(
        "No LLM provider reachable for function=code_generation. Either:\n"
        "  1. Install Claude Code CLI (VSCode extension or CLI login), or\n"
        "  2. Configure a provider in args/llm_config.yaml (Bedrock, Vertex, OCI, watsonx, Ollama, Anthropic API), or\n"
        "  3. Set ANTHROPIC_API_KEY for direct API access (legacy path)"
    )
    for hint in hints:
        logger.error(f"  hint: {hint}")
    sys.exit(1)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python tools/ci/workflows/icdev_plan.py <issue-number> [run-id]")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else None

    # Ensure run_id exists
    run_id = ensure_run_id(issue_number, run_id)
    state = ICDevState.load(run_id)
    logger = setup_logger(run_id, "icdev_plan")
    logger.info(f"ICDEV™ Plan starting — run_id: {run_id}, issue: #{issue_number}")

    check_env_vars(logger)

    # Initialize VCS
    try:
        vcs = VCS()
        platform = "gitlab" if vcs.is_gitlab else "github"
        state.update(platform=platform)
        state.save("icdev_plan")
    except ValueError as e:
        logger.error(f"VCS initialization failed: {e}")
        sys.exit(1)

    # Fetch issue
    logger.info("Fetching issue details...")
    try:
        issue_data = vcs.fetch_issue(int(issue_number))
        issue_json = json.dumps(issue_data)
    except Exception as e:
        logger.error(f"Failed to fetch issue: {e}")
        sys.exit(1)

    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", "Starting planning phase"),
    )

    # Classify issue
    issue_command, error = classify_issue(issue_json, run_id, logger)
    if error:
        logger.error(f"Classification failed: {error}")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, "ops", f"Classification failed: {error}"),
        )
        sys.exit(1)

    state.update(issue_class=issue_command)
    state.save("icdev_plan")
    logger.info(f"Issue classified as: {issue_command}")
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", f"Issue classified as: {issue_command}"),
    )

    # Generate branch name
    branch_name, error = generate_branch_name(issue_json, issue_command, run_id, logger)
    if error:
        logger.error(f"Branch name generation failed: {error}")
        sys.exit(1)

    # Create branch
    success, error = create_branch(branch_name)
    if not success:
        logger.error(f"Branch creation failed: {error}")
        sys.exit(1)

    state.update(branch_name=branch_name)
    state.save("icdev_plan")
    logger.info(f"Working on branch: {branch_name}")
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", f"Working on branch: {branch_name}"),
    )

    # Build plan
    logger.info("Building implementation plan...")
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, AGENT_PLANNER, "Building implementation plan"),
    )

    plan_response = build_plan(issue_json, issue_command, run_id, logger)
    if not plan_response.success:
        logger.error(f"Plan generation failed: {plan_response.output}")
        vcs.comment_on_issue(
            int(issue_number),
            format_issue_message(run_id, AGENT_PLANNER, f"Plan failed: {plan_response.output}"),
        )
        sys.exit(1)

    plan_file_path = plan_response.output.strip()

    if not plan_file_path or not os.path.exists(plan_file_path):
        logger.error(f"Plan file not found: {plan_file_path}")
        sys.exit(1)

    state.update(plan_file=plan_file_path)
    state.save("icdev_plan")
    logger.info(f"Plan file: {plan_file_path}")

    # Commit plan
    commit_msg, error = create_commit(AGENT_PLANNER, issue_json, issue_command, run_id, logger)
    if error:
        logger.error(f"Commit message generation failed: {error}")
        sys.exit(1)

    success, error = commit_changes(commit_msg)
    if not success:
        logger.error(f"Commit failed: {error}")
        sys.exit(1)

    logger.info(f"Committed: {commit_msg}")

    # Push and create PR/MR
    finalize_git_operations(state, logger, vcs)

    logger.info("Planning phase completed")
    vcs.comment_on_issue(
        int(issue_number),
        format_issue_message(run_id, "ops", "Planning phase completed"),
    )
    state.save("icdev_plan")


if __name__ == "__main__":
    main()
