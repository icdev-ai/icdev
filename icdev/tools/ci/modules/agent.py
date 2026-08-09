# CUI // SP-CTI
"""ICDEV™ agent template executor.

A thin wrapper around the Claude Code CLI used by the CI workflow
scripts. Picks the appropriate model tier per slash command, writes the
rendered prompt to the per-run agent directory, delegates to the robust
executor in :mod:`tools.agent.agent_executor` when available, and falls
back to a direct subprocess invocation when it isn't.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/modules/agent.md`` (OPT-75 Phase 3
clean-room rewrite).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

from tools.testing.data_types import (
    AgentPromptRequest,
    AgentPromptResponse,
    AgentTemplateRequest,
)


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_module_logger = get_logger(__name__)


# ── Bot identifier ────────────────────────────────────────────────────────
BOT_IDENTIFIER: str = "[ICDEV\u2122-BOT]"


# ── Model selection per slash command ─────────────────────────────────────
SLASH_COMMAND_MODEL_MAP = {
    # Classification (cheap + fast)
    "/classify_issue": "haiku",
    "/classify_workflow": "haiku",
    "/generate_branch_name": "haiku",
    # Planning / implementation
    "/icdev-init": "sonnet",
    "/icdev-build": "opus",
    "/icdev-comply": "sonnet",
    "/icdev-deploy": "sonnet",
    "/bug": "opus",
    "/feature": "opus",
    "/chore": "sonnet",
    "/patch": "sonnet",
    # Testing
    "/icdev-test": "sonnet",
    "/test": "sonnet",
    "/test_e2e": "sonnet",
    "/resolve_failed_test": "sonnet",
    "/resolve_failed_e2e_test": "sonnet",
    # Review / security
    "/icdev-review": "opus",
    "/review": "opus",
    "/icdev-secure": "sonnet",
    "/icdev-status": "haiku",
    "/icdev-monitor": "sonnet",
    "/icdev-knowledge": "sonnet",
    # Documentation
    "/document": "sonnet",
    # Git operations
    "/commit": "haiku",
    "/pull_request": "haiku",
    # Implementation
    "/implement": "opus",
}

DEFAULT_TIMEOUT_SECONDS: int = 300


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _get_timeout(slash_command: str = "") -> int:
    """Read the configured executor timeout from
    ``args/cicd_config.yaml``. Honors per-command overrides under
    ``cicd.executor.timeout_overrides``."""
    config_path = PROJECT_ROOT / "args" / "cicd_config.yaml"
    if not config_path.exists():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return DEFAULT_TIMEOUT_SECONDS

    executor_cfg = (data.get("cicd") or {}).get("executor") or {}
    overrides = executor_cfg.get("timeout_overrides") or {}
    if slash_command and slash_command in overrides:
        try:
            return int(overrides[slash_command])
        except (TypeError, ValueError):
            pass
    try:
        return int(
            executor_cfg.get("default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        )
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _ensure_agent_dir(run_id: str, agent_name: str) -> Path:
    """Create and return ``<repo>/agents/<run_id>/<agent_name>/``."""
    agent_dir = PROJECT_ROOT / "agents" / run_id / agent_name
    (agent_dir / "prompts").mkdir(parents=True, exist_ok=True)
    return agent_dir


def _safe_filename(slash_command: str) -> str:
    """Slugify a slash command for the prompts/<name>.txt file."""
    return slash_command.lstrip("/").replace("-", "_") or "command"


# ────────────────────────────────────────────────────────────────────────────
# prompt_claude_code (subprocess + robust-executor delegation)
# ────────────────────────────────────────────────────────────────────────────


def _try_robust_executor(
    request: AgentPromptRequest,
    timeout_seconds: int,
) -> Optional[AgentPromptResponse]:
    """Attempt to delegate to the robust executor. Returns ``None`` if
    the executor module isn't installed."""
    try:
        from tools.agent.agent_executor import execute_agent
        from tools.agent.agent_models import (
            AgentPromptRequest as RobustRequest,
        )
    except ImportError:
        return None

    robust_req = RobustRequest(
        prompt=request.prompt,
        model=request.model,
        project_dir=request.project_dir or str(PROJECT_ROOT),
        timeout_seconds=timeout_seconds,
    )
    robust_resp = execute_agent(robust_req, max_retries=3)
    return AgentPromptResponse(
        output=getattr(robust_resp, "output_text", "") or "",
        success=getattr(robust_resp, "status", "") == "completed",
        session_id=getattr(robust_resp, "session_id", None),
        duration_ms=getattr(robust_resp, "duration_ms", None),
    )


def _parse_jsonl_result(output_path: Path) -> tuple:
    """Walk a Claude stream-json output file looking for the result
    record. Returns ``(text, session_id, is_error)``. Missing or
    malformed input degrades to ``("", None, False)``."""
    output_text = ""
    session_id: Optional[str] = None
    is_error = False
    if not output_path.exists():
        return output_text, session_id, is_error
    try:
        with open(output_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "result":
                    output_text = msg.get("result", "") or ""
                    session_id = msg.get("session_id")
                    is_error = bool(msg.get("is_error", False))
                    break
    except OSError:
        pass
    return output_text, session_id, is_error


def _direct_subprocess(
    request: AgentPromptRequest,
    timeout_seconds: int,
) -> AgentPromptResponse:
    """Fallback: invoke the Claude Code CLI directly."""
    from tools.testing.utils import get_safe_subprocess_env

    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    env = get_safe_subprocess_env()

    cmd = [
        claude_path,
        "-p", request.prompt,
        "--model", request.model,
        "--output-format", "stream-json",
        "--verbose",
    ]

    output_file = (
        Path(request.output_file) if request.output_file
        else PROJECT_ROOT / ".tmp" / "agent_output.jsonl"
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_file, "w", encoding="utf-8") as out_fh:
            proc = subprocess.run(
                cmd,
                stdout=out_fh,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                timeout=timeout_seconds,
                cwd=request.project_dir or str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        return AgentPromptResponse(
            output=f"Claude Code timed out after {timeout_seconds} seconds",
            success=False,
        )
    except FileNotFoundError:
        return AgentPromptResponse(
            output=f"Claude Code CLI not found at '{claude_path}'",
            success=False,
        )
    except Exception as exc:
        return AgentPromptResponse(
            output=f"Agent execution error: {exc}",
            success=False,
        )

    output_text, session_id, is_error = _parse_jsonl_result(output_file)
    return AgentPromptResponse(
        output=output_text,
        success=(not is_error) and proc.returncode == 0,
        session_id=session_id,
        duration_ms=None,
    )


def prompt_claude_code(request: AgentPromptRequest) -> AgentPromptResponse:
    """Execute a Claude Code CLI prompt with retry + audit when the
    robust executor is available, otherwise via direct subprocess."""
    timeout_seconds = _get_timeout()

    via_robust = _try_robust_executor(request, timeout_seconds)
    if via_robust is not None:
        return via_robust
    return _direct_subprocess(request, timeout_seconds)


# ────────────────────────────────────────────────────────────────────────────
# execute_template
# ────────────────────────────────────────────────────────────────────────────


def _convert_jsonl_to_json(jsonl_path: Path, json_path: Path) -> None:
    """Best-effort: collect every JSONL record into a JSON array beside
    the original file. All exceptions are swallowed."""
    try:
        if not jsonl_path.exists():
            return
        entries = []
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
    except Exception:
        pass


def execute_template(request: AgentTemplateRequest) -> AgentPromptResponse:
    """Render a slash-command template and run it through the agent.

    Resolves the model from :data:`SLASH_COMMAND_MODEL_MAP`, materialises
    the prompt to ``agents/<run_id>/<agent_name>/prompts/<cmd>.txt``,
    invokes :func:`prompt_claude_code`, and writes both ``raw_output.jsonl``
    and a sibling ``raw_output.json`` for human inspection.
    """
    model = SLASH_COMMAND_MODEL_MAP.get(request.slash_command, request.model)

    parts = [request.slash_command]
    if request.args:
        parts.extend(request.args)
    prompt = " ".join(parts)

    agent_dir = _ensure_agent_dir(request.run_id, request.agent_name)
    prompt_file = agent_dir / "prompts" / f"{_safe_filename(request.slash_command)}.txt"
    try:
        prompt_file.write_text(prompt, encoding="utf-8", newline="")
    except OSError as exc:
        _module_logger.warning(
            "agent: could not persist prompt file %s: %s", prompt_file, exc
        )

    jsonl_path = agent_dir / "raw_output.jsonl"
    inner = AgentPromptRequest(
        prompt=prompt,
        agent_name=request.agent_name,
        model=model,
        output_file=str(jsonl_path),
        project_dir=".",
    )

    response = prompt_claude_code(inner)
    _convert_jsonl_to_json(jsonl_path, agent_dir / "raw_output.json")
    return response
