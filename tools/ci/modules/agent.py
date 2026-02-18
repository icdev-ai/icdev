# CUI // SP-CTI
# ICDEV Agent Executor — Claude Code CLI subprocess invocation
# Adapted from ADW agent.py

"""
Execute Claude Code CLI as a subprocess with slash commands.

Adapted from ADW agent.py pattern:
- Build prompt from slash command + args
- Execute claude CLI with stream-json output
- Parse JSONL output to extract result
- Save prompts and raw output for debugging

Usage:
    from tools.ci.modules.agent import execute_template
    request = AgentTemplateRequest(
        agent_name="planner",
        slash_command="/icdev-build",
        args=["user auth with JWT"],
        run_id="abc12345",
    )
    response = execute_template(request)
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.data_types import AgentTemplateRequest, AgentPromptRequest, AgentPromptResponse
from tools.testing.utils import get_safe_subprocess_env

# Model mapping per slash command (adapted from ADW SLASH_COMMAND_MODEL_MAP)
SLASH_COMMAND_MODEL_MAP = {
    # Classification (fast, cheap)
    "/classify_issue": "haiku",
    "/classify_workflow": "haiku",
    "/generate_branch_name": "haiku",
    # Planning (needs reasoning)
    "/icdev-init": "sonnet",
    "/icdev-build": "opus",
    "/icdev-comply": "sonnet",
    "/icdev-deploy": "sonnet",
    "/bug": "opus",
    "/feature": "opus",
    "/chore": "sonnet",
    "/patch": "sonnet",
    # Testing (fast execution)
    "/icdev-test": "sonnet",
    "/test": "sonnet",
    "/test_e2e": "sonnet",
    "/resolve_failed_test": "sonnet",
    "/resolve_failed_e2e_test": "sonnet",
    # Review/security (needs depth)
    "/icdev-review": "opus",
    "/review": "opus",
    "/icdev-secure": "sonnet",
    "/icdev-status": "haiku",
    "/icdev-monitor": "sonnet",
    "/icdev-knowledge": "sonnet",
    # Documentation
    "/document": "sonnet",
    # Git operations (fast)
    "/commit": "haiku",
    "/pull_request": "haiku",
    "/implement": "opus",
}

# Bot identifier for preventing infinite loops
BOT_IDENTIFIER = "[ICDEV-BOT]"


def _ensure_agent_dir(run_id: str, agent_name: str) -> Path:
    """Create and return the agent working directory."""
    agent_dir = PROJECT_ROOT / "agents" / run_id / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "prompts").mkdir(exist_ok=True)
    return agent_dir


def prompt_claude_code(request: AgentPromptRequest) -> AgentPromptResponse:
    """Execute Claude Code CLI with a direct prompt.

    Adapted from ADW prompt_claude_code pattern:
    - Invokes claude CLI with -p flag
    - Captures stream-json output to file
    - Parses JSONL to extract result
    """
    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    env = get_safe_subprocess_env()

    # Build command
    cmd = [claude_path, "-p", request.prompt]
    cmd.extend(["--model", request.model])
    cmd.extend(["--output-format", "stream-json"])
    cmd.append("--verbose")

    # Output file
    output_file = request.output_file
    if not output_file:
        output_file = str(PROJECT_ROOT / ".tmp" / "agent_output.jsonl")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_file, "w") as f:
            result = subprocess.run(
                cmd, stdout=f, stderr=subprocess.PIPE, text=True,
                env=env, timeout=600,
                cwd=request.project_dir or str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,  # Prevent hanging (ADW sandbox lesson)
            )

        # Parse JSONL output
        output_text = ""
        session_id = None
        is_error = False

        with open(output_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "result":
                        output_text = msg.get("result", "")
                        session_id = msg.get("session_id")
                        is_error = msg.get("is_error", False)
                        break
                except json.JSONDecodeError:
                    continue

        return AgentPromptResponse(
            output=output_text,
            success=not is_error and result.returncode == 0,
            session_id=session_id,
            duration_ms=None,
        )

    except subprocess.TimeoutExpired:
        return AgentPromptResponse(
            output="Claude Code timed out after 600 seconds",
            success=False,
        )
    except FileNotFoundError:
        return AgentPromptResponse(
            output=f"Claude Code CLI not found at '{claude_path}'",
            success=False,
        )
    except Exception as e:
        return AgentPromptResponse(
            output=f"Agent execution error: {str(e)}",
            success=False,
        )


def execute_template(request: AgentTemplateRequest) -> AgentPromptResponse:
    """Execute a Claude Code slash command template.

    Adapted from ADW execute_template pattern:
    1. Look up model from SLASH_COMMAND_MODEL_MAP
    2. Build prompt: "{slash_command} {args}"
    3. Save prompt to agents/{run_id}/{agent_name}/prompts/
    4. Execute via prompt_claude_code()
    5. Return structured response
    """
    # Determine model
    model = SLASH_COMMAND_MODEL_MAP.get(request.slash_command, request.model)

    # Build prompt
    prompt = request.slash_command
    if request.args:
        prompt += " " + " ".join(request.args)

    # Set up agent directory
    agent_dir = _ensure_agent_dir(request.run_id, request.agent_name)

    # Save prompt for debugging
    cmd_name = request.slash_command.lstrip("/").replace("-", "_")
    prompt_file = agent_dir / "prompts" / f"{cmd_name}.txt"
    with open(prompt_file, "w") as f:
        f.write(prompt)

    # Output file
    output_file = str(agent_dir / "raw_output.jsonl")

    # Execute
    prompt_request = AgentPromptRequest(
        prompt=prompt,
        agent_name=request.agent_name,
        model=model,
        output_file=output_file,
        project_dir=".",
    )

    response = prompt_claude_code(prompt_request)

    # Also save as JSON array for easier reading
    try:
        jsonl_path = agent_dir / "raw_output.jsonl"
        json_path = agent_dir / "raw_output.json"
        if jsonl_path.exists():
            entries = []
            with open(jsonl_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            with open(json_path, "w") as f:
                json.dump(entries, f, indent=2)
    except Exception:
        pass

    return response
