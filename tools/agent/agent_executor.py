# [TEMPLATE: CUI // SP-CTI]
"""Agent Executor — subprocess-based Claude Code CLI invocation with retry/audit."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.agent.agent_models import AgentPromptRequest, AgentPromptResponse, RetryCode

DB_PATH = BASE_DIR / "data" / "icdev.db"
OUTPUT_DIR = BASE_DIR / "agents"

# Safe environment variable allowlist
SAFE_ENV_ALLOWLIST = [
    "PATH", "HOME", "USER", "LANG", "TERM",
    "ANTHROPIC_API_KEY", "AWS_DEFAULT_REGION", "AWS_REGION",
    "ICDEV_DB_PATH", "ICDEV_PROJECT_ROOT",
    "CLAUDE_SESSION_ID",
]


def get_safe_agent_env(extra_vars: dict = None) -> dict:
    """Build a minimal, safe environment for agent subprocesses."""
    env = {}
    for key in SAFE_ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val:
            env[key] = val
    env["ICDEV_DB_PATH"] = str(DB_PATH)
    env["ICDEV_PROJECT_ROOT"] = str(BASE_DIR)
    if extra_vars:
        env.update(extra_vars)
    return env


def log_execution(execution_id: str, request: AgentPromptRequest,
                  response: AgentPromptResponse, db_path: Path = None):
    """Log agent execution to database (append-only)."""
    path = db_path or DB_PATH
    try:
        conn = sqlite3.connect(str(path))
        conn.execute(
            """INSERT INTO agent_executions
               (execution_id, project_id, agent_type, model, prompt_hash,
                status, retry_count, duration_ms, input_tokens, output_tokens,
                output_path, error_message, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                execution_id,
                request.project_dir,
                "claude_code",
                response.model or request.model,
                response.prompt_hash,
                response.status,
                response.retry_count,
                response.duration_ms,
                response.input_tokens,
                response.output_tokens,
                response.output_path,
                response.error_message,
                request.classification,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: Failed to log execution: {e}", file=sys.stderr)


def parse_jsonl_output(output_path: Path) -> AgentPromptResponse:
    """Parse Claude Code JSONL output file into AgentPromptResponse."""
    response = AgentPromptResponse()
    response.output_path = str(output_path)
    response.raw_jsonl = []

    if not output_path.exists():
        response.status = "failed"
        response.error_message = f"Output file not found: {output_path}"
        response.retry_code = RetryCode.RETRYABLE_ERROR
        return response

    text_parts = []
    try:
        with open(output_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    response.raw_jsonl.append(entry)

                    entry_type = entry.get("type", "")
                    if entry_type == "result":
                        response.status = "completed"
                        response.retry_code = RetryCode.SUCCESS
                        result_text = entry.get("result", "")
                        if result_text:
                            text_parts.append(result_text)
                    elif entry_type == "error":
                        response.status = "failed"
                        response.error_message = entry.get("error", "Unknown error")
                        if "rate" in response.error_message.lower():
                            response.retry_code = RetryCode.RATE_LIMITED
                        else:
                            response.retry_code = RetryCode.RETRYABLE_ERROR
                    elif entry_type == "assistant":
                        content = entry.get("message", {}).get("content", "")
                        if isinstance(content, str) and content:
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text_parts.append(block.get("text", ""))

                    # Token tracking
                    usage = entry.get("usage", {})
                    if usage:
                        response.input_tokens += usage.get("input_tokens", 0)
                        response.output_tokens += usage.get("output_tokens", 0)

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        response.status = "failed"
        response.error_message = f"Failed to parse output: {e}"
        response.retry_code = RetryCode.FATAL_ERROR

    response.output_text = "\n".join(text_parts)

    if not response.status or response.status == "pending":
        response.status = "completed" if text_parts else "failed"
        if response.status == "failed":
            response.retry_code = RetryCode.RETRYABLE_ERROR

    return response


def execute_agent(request: AgentPromptRequest, max_retries: int = 3,
                  retry_delays: list = None) -> AgentPromptResponse:
    """Execute a Claude Code CLI agent with retry logic and audit trail."""
    if retry_delays is None:
        retry_delays = [1, 3, 5]

    execution_id = str(uuid.uuid4())
    prompt_hash = hashlib.sha256(request.prompt.encode()).hexdigest()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{execution_id}.jsonl"

    final_response = AgentPromptResponse(
        execution_id=execution_id,
        model=request.model,
        prompt_hash=prompt_hash,
        classification=request.classification,
    )

    for attempt in range(max_retries + 1):
        start_time = time.time()

        # Build CLI command
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", request.model,
            "--max-turns", str(request.max_turns),
        ]

        if request.allowed_tools:
            for tool in request.allowed_tools:
                cmd.extend(["--allowedTools", tool])

        if request.system_prompt:
            cmd.extend(["--system-prompt", request.system_prompt])

        cmd.extend(["--prompt", request.prompt])

        # Execute
        env = get_safe_agent_env(request.env_vars)
        cwd = request.project_dir or str(BASE_DIR)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=cwd,
                timeout=request.timeout_seconds,
                stdin=subprocess.DEVNULL,
            )

            # Write output to JSONL file
            with open(output_path, "w") as f:
                if result.stdout:
                    f.write(result.stdout)

            # Parse response
            final_response = parse_jsonl_output(output_path)
            final_response.execution_id = execution_id
            final_response.model = request.model
            final_response.prompt_hash = prompt_hash
            final_response.classification = request.classification
            final_response.duration_ms = int((time.time() - start_time) * 1000)
            final_response.retry_count = attempt

            if final_response.retry_code == RetryCode.SUCCESS:
                final_response.status = "completed"
                log_execution(execution_id, request, final_response)
                return final_response

            # Check if retryable
            if attempt < max_retries and final_response.retry_code in (
                RetryCode.RETRYABLE_ERROR, RetryCode.RATE_LIMITED
            ):
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                print(f"Retry {attempt + 1}/{max_retries} after {delay}s: {final_response.error_message}",
                      file=sys.stderr)
                final_response.status = "retried"
                log_execution(execution_id, request, final_response)
                time.sleep(delay)
                continue

        except subprocess.TimeoutExpired:
            final_response.duration_ms = int((time.time() - start_time) * 1000)
            final_response.status = "timeout"
            final_response.retry_code = RetryCode.TIMEOUT
            final_response.error_message = f"Timeout after {request.timeout_seconds}s"
            final_response.retry_count = attempt

            if attempt < max_retries:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                log_execution(execution_id, request, final_response)
                time.sleep(delay)
                continue

        except FileNotFoundError:
            final_response.status = "failed"
            final_response.retry_code = RetryCode.FATAL_ERROR
            final_response.error_message = "claude CLI not found in PATH"
            log_execution(execution_id, request, final_response)
            return final_response

        except Exception as e:
            final_response.status = "failed"
            final_response.retry_code = RetryCode.FATAL_ERROR
            final_response.error_message = str(e)
            final_response.duration_ms = int((time.time() - start_time) * 1000)
            final_response.retry_count = attempt

    # All retries exhausted
    if final_response.status != "completed":
        final_response.status = "failed"
    log_execution(execution_id, request, final_response)
    return final_response


def execute_agent_bedrock(
    request: AgentPromptRequest,
    max_retries: int = 3,
    tool_handlers: dict = None,
) -> AgentPromptResponse:
    """Execute an agent via Amazon Bedrock API instead of Claude Code CLI.

    Uses BedrockClient for model invocation with fallback chain,
    adaptive thinking, effort parameter, and structured outputs.
    Falls back to execute_agent() if Bedrock is unavailable.

    Args:
        request: AgentPromptRequest with prompt, model, etc.
        max_retries: Max retry attempts (handled by BedrockClient internally).
        tool_handlers: Optional dict of tool_name -> callable for tool_use loops.

    Returns:
        AgentPromptResponse with Bedrock results.
    """
    execution_id = str(uuid.uuid4())
    prompt_hash = hashlib.sha256(request.prompt.encode()).hexdigest()
    start_time = time.time()

    final_response = AgentPromptResponse(
        execution_id=execution_id,
        model=request.model,
        prompt_hash=prompt_hash,
        classification=request.classification,
    )

    # Enhancement #4: Use LLMRouter for vendor-agnostic invocation
    agent_id = request.env_vars.get("AGENT_ID", "") if request.env_vars else ""

    # Inject agent memory context into system prompt
    system_prompt = request.system_prompt or ""
    if agent_id and request.project_dir:
        try:
            from tools.agent.agent_memory import inject_context
            memory_context = inject_context(
                agent_id=agent_id,
                project_id=request.project_dir,
                max_memories=5,
            )
            if memory_context:
                system_prompt = f"{system_prompt}\n\n{memory_context}" if system_prompt else memory_context
        except ImportError:
            pass
        except Exception:
            pass

    messages = [
        {"role": "user", "content": [{"type": "text", "text": request.prompt}]},
    ]

    # Map model names to routing functions
    function_map = {
        "opus": "agent_orchestrator",
        "sonnet": "agent_builder",
        "sonnet-4-5": "agent_builder",
        "sonnet-3-5": "agent_builder",
        "haiku": "agent_builder",
    }
    routing_function = function_map.get(request.model, "agent_builder")

    # For tool_use loops, fall back to BedrockClient (it has invoke_with_tools)
    if tool_handlers:
        try:
            from tools.agent.bedrock_client import BedrockClient, BedrockRequest
            client = BedrockClient()
            model_map = {
                "opus": "opus", "sonnet": "sonnet-4-5",
                "sonnet-4-5": "sonnet-4-5", "sonnet-3-5": "sonnet-3-5",
                "haiku": "sonnet-3-5",
            }
            model_preference = model_map.get(request.model, "sonnet-4-5")
            effort = client._agent_effort_overrides.get(agent_id, "medium")
            bedrock_req = BedrockRequest(
                messages=messages, system_prompt=system_prompt,
                agent_id=agent_id, project_id=request.project_dir or "",
                model_preference=model_preference, effort=effort,
                max_tokens=request.max_turns * 4096,
                classification=request.classification,
            )
            resp = client.invoke_with_tools(
                bedrock_req, tool_handlers=tool_handlers,
                max_iterations=request.max_turns,
            )
        except ImportError:
            print("Warning: bedrock_client not available for tool_use, falling back to CLI", file=sys.stderr)
            return execute_agent(request, max_retries=max_retries)
    else:
        # Use vendor-agnostic LLMRouter
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest
            router = LLMRouter()
            llm_req = LLMRequest(
                messages=messages,
                system_prompt=system_prompt,
                agent_id=agent_id,
                project_id=request.project_dir or "",
                effort="medium",
                max_tokens=request.max_turns * 4096,
                classification=request.classification,
            )
            resp = router.invoke(routing_function, llm_req)
        except ImportError:
            # Fall back to BedrockClient
            try:
                from tools.agent.bedrock_client import BedrockClient, BedrockRequest
                client = BedrockClient()
                model_map = {
                    "opus": "opus", "sonnet": "sonnet-4-5",
                    "sonnet-4-5": "sonnet-4-5", "sonnet-3-5": "sonnet-3-5",
                    "haiku": "sonnet-3-5",
                }
                model_preference = model_map.get(request.model, "sonnet-4-5")
                effort = client._agent_effort_overrides.get(agent_id, "medium")
                bedrock_req = BedrockRequest(
                    messages=messages, system_prompt=system_prompt,
                    agent_id=agent_id, project_id=request.project_dir or "",
                    model_preference=model_preference, effort=effort,
                    max_tokens=request.max_turns * 4096,
                    classification=request.classification,
                )
                resp = client.invoke(bedrock_req)
            except ImportError:
                print("Warning: No LLM backend available, falling back to CLI", file=sys.stderr)
                return execute_agent(request, max_retries=max_retries)

    try:

        # Map BedrockResponse -> AgentPromptResponse
        final_response.status = "completed"
        final_response.retry_code = RetryCode.SUCCESS
        final_response.output_text = resp.content
        final_response.model = resp.model_id
        final_response.input_tokens = resp.input_tokens
        final_response.output_tokens = resp.output_tokens
        final_response.duration_ms = resp.duration_ms or int((time.time() - start_time) * 1000)

        # Log execution
        log_execution(execution_id, request, final_response)

        # Audit trail
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="bedrock_invoked",
                actor=agent_id or "agent-executor",
                action=f"Bedrock invocation via {resp.model_id}",
                project_id=request.project_dir,
                details={
                    "execution_id": execution_id,
                    "model_id": resp.model_id,
                    "model_preference": model_preference,
                    "effort": effort,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "thinking_tokens": resp.thinking_tokens,
                    "duration_ms": resp.duration_ms,
                },
                classification=request.classification,
            )
        except Exception:
            pass  # Best-effort audit

        return final_response

    except ImportError:
        # boto3 not installed — fall back to CLI
        print("Warning: boto3 not available, falling back to CLI", file=sys.stderr)
        return execute_agent(request, max_retries=max_retries)

    except Exception as e:
        # Bedrock call failed — log and fall back to CLI
        final_response.duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)

        # Check if it's a rate limit or model availability issue
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "TooManyRequestsException"):
            try:
                from tools.audit.audit_logger import log_event
                log_event(
                    event_type="bedrock_rate_limited",
                    actor=agent_id or "agent-executor",
                    action=f"Rate limited on {model_preference}: {error_msg}",
                    project_id=request.project_dir,
                    classification=request.classification,
                )
            except Exception:
                pass

        # Log fallback
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="bedrock_fallback",
                actor=agent_id or "agent-executor",
                action=f"Falling back to CLI after Bedrock error: {error_msg}",
                project_id=request.project_dir,
                classification=request.classification,
            )
        except Exception:
            pass

        print(f"Warning: Bedrock failed ({error_msg}), falling back to CLI", file=sys.stderr)
        return execute_agent(request, max_retries=max_retries)


def main():
    """CLI entry point for agent execution."""
    parser = argparse.ArgumentParser(description="Execute Claude Code agent")
    parser.add_argument("--prompt", required=True, help="Prompt to send to agent")
    parser.add_argument("--model", default="sonnet", choices=["sonnet", "opus", "haiku"])
    parser.add_argument("--project-dir", help="Working directory for agent")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Output JSON response")
    parser.add_argument("--bedrock", action="store_true",
                        help="Use Bedrock API instead of Claude Code CLI")
    parser.add_argument("--system-prompt", default=None, help="System prompt for agent")
    args = parser.parse_args()

    request = AgentPromptRequest(
        prompt=args.prompt,
        model=args.model,
        project_dir=args.project_dir,
        timeout_seconds=args.timeout,
        max_turns=args.max_turns,
        system_prompt=args.system_prompt,
    )

    if args.bedrock:
        response = execute_agent_bedrock(request, max_retries=args.max_retries)
    else:
        response = execute_agent(request, max_retries=args.max_retries)

    if args.json:
        import dataclasses
        out = dataclasses.asdict(response)
        out["retry_code"] = response.retry_code.value
        print(json.dumps(out, indent=2))
    else:
        print(f"Status: {response.status}")
        print(f"Model: {response.model}")
        print(f"Duration: {response.duration_ms}ms")
        print(f"Tokens: {response.input_tokens} in / {response.output_tokens} out")
        if response.error_message:
            print(f"Error: {response.error_message}")
        if response.output_text:
            print(f"\n--- Output ---\n{response.output_text}")


if __name__ == "__main__":
    main()
