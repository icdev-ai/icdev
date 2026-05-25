# CUI // SP-CTI
"""Agent composer for the agent toolkit (OPT-67).

Provides a one-line `create_agent()` factory that returns an Agent
object with an `.invoke(messages)` method. The agent wires LLMRouter
(for the LLM call) with a tool catalog (for side effects) and runs a
bounded loop: LLM → parse tool calls → execute → feed results back.

Works in three modes:

1. **No LLM / deterministic tools only** — call the tool functions
   directly without going through an LLM. Agent is just a namespace
   for the toolkit. Useful for testing and scripts that want the
   primitive API without an LLM roundtrip.

2. **Single-shot LLM** — call LLMRouter once, return the response.
   No tool execution. Useful when the caller just wants an LLM answer
   with the toolkit in-scope for documentation.

3. **Tool-calling loop** (default) — run up to `max_iterations` cycles
   of (LLM → parse tool_calls → execute → inject results). Halts when
   the LLM returns no tool_calls OR the iteration cap is hit.

LLMRouter is invoked via function name (default 'code_generation').
Individual providers may or may not support OpenAI-format tool_calls —
the composer degrades to single-shot mode if the provider doesn't
return parseable tool_calls in LLMResponse.

Inspired by langchain-ai/deepagents (MIT) `create_deep_agent()`
factory. ICDEV implementation is independent: no LangGraph, no
langchain-core. Pure Python + LLMRouter.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tools.agent_toolkit._fs import (
    edit_file,
    glob,
    grep,
    ls,
    read_file,
    write_file,
)
from tools.agent_toolkit._planning import update_todo, write_todos
from tools.agent_toolkit._shell import execute_shell
from tools.agent_toolkit._subagent import spawn_subagent

logger = get_logger("icdev.agent_toolkit.composer")


# Default tools exposed to every Agent unless the caller overrides.
# Each entry: name → callable. Callable signatures are passed kwargs
# from the LLM's tool_call arguments dict.
_DEFAULT_TOOLS: Dict[str, Callable] = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "ls": ls,
    "glob": glob,
    "grep": grep,
    "execute_shell": execute_shell,
    "write_todos": write_todos,
    "update_todo": update_todo,
    "spawn_subagent": spawn_subagent,
}


def list_default_tools() -> List[str]:
    """Return the names of the default tool catalog."""
    return list(_DEFAULT_TOOLS.keys())


@dataclass
class AgentResult:
    """Result of a full Agent.invoke() call."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls_made: int = 0
    iterations: int = 0
    duration_ms: int = 0
    stop_reason: str = ""
    final_content: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "messages": self.messages,
            "tool_calls_made": self.tool_calls_made,
            "iterations": self.iterations,
            "duration_ms": self.duration_ms,
            "stop_reason": self.stop_reason,
            "final_content": self.final_content,
            "error": self.error,
        }


@dataclass
class Agent:
    """A composed agent with a name, system prompt, tool catalog, and
    LLMRouter function. Use tools.agent_toolkit.create_agent() instead
    of instantiating directly so the defaults are wired correctly.
    """

    name: str
    system_prompt: str
    tools: Dict[str, Callable]
    function: str = "code_generation"  # LLMRouter function key
    max_iterations: int = 10
    max_tokens: int = 4096
    temperature: float = 0.3
    model: str = ""  # optional explicit model override

    def invoke(self, messages: List[Dict[str, Any]]) -> AgentResult:
        """Run the agent loop against an input message list.

        Args:
            messages: OpenAI-format list of
                [{"role": "user"|"assistant"|"tool", "content": str, ...}]

        Returns:
            AgentResult with the final message history, tool call count,
            iteration count, duration, and stop reason.
        """
        result = AgentResult()
        t0 = time.time()
        result.messages = list(messages)

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter
        except ImportError as exc:
            result.error = f"LLMRouter import failed: {exc}"
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        router = LLMRouter()

        for iteration in range(self.max_iterations):
            result.iterations = iteration + 1

            request = LLMRequest(
                messages=result.messages,
                system_prompt=self.system_prompt,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=_tool_schemas(self.tools),
                agent_id=f"agent_toolkit:{self.name}",
                project_id="agent_toolkit",
            )

            try:
                response = router.invoke(self.function, request)
            except Exception as exc:
                result.error = f"LLMRouter invoke failed: {type(exc).__name__}: {exc}"
                result.stop_reason = "llm_error"
                break

            # Append the assistant turn
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = response.tool_calls
            result.messages.append(assistant_msg)
            result.final_content = response.content or ""
            result.stop_reason = response.stop_reason or ""

            # If no tool calls, we're done
            if not response.tool_calls:
                break

            # Execute each tool call and append tool results
            for tc in response.tool_calls:
                tool_name = tc.get("name") or tc.get("function", {}).get("name", "")
                tool_args_raw = tc.get("arguments") or tc.get("function", {}).get("arguments", {})
                if isinstance(tool_args_raw, str):
                    try:
                        tool_args = json.loads(tool_args_raw)
                    except json.JSONDecodeError:
                        tool_args = {}
                else:
                    tool_args = tool_args_raw or {}

                tool_fn = self.tools.get(tool_name)
                if not tool_fn:
                    tool_output: Any = {
                        "error": f"unknown tool '{tool_name}'; "
                                 f"available: {sorted(self.tools.keys())}"
                    }
                else:
                    try:
                        tool_output = tool_fn(**tool_args)
                    except Exception as exc:
                        tool_output = {
                            "error": f"{type(exc).__name__}: {exc}",
                        }

                result.tool_calls_made += 1
                result.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                    "content": _safe_json(tool_output),
                })

        # If we exited the loop because the LLM kept emitting tool_calls
        # right up to the iteration cap (i.e., the last response STILL
        # had tool_calls), record that as the terminal reason — overriding
        # any per-turn 'tool_use' that the LLM set on its last response.
        if result.iterations >= self.max_iterations and response.tool_calls:
            result.stop_reason = "max_iterations"

        result.duration_ms = int((time.time() - t0) * 1000)
        return result


def _safe_json(obj: Any, max_len: int = 8000) -> str:
    """Serialize a tool result to a short JSON string for the LLM context.

    Non-serializable objects fall back to str(). Length capped so a big
    grep or ls result doesn't blow out the context window.
    """
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        s = s[:max_len] + f"... (truncated, {len(s) - max_len} more chars)"
    return s


def _tool_schemas(tools: Dict[str, Callable]) -> List[Dict]:
    """Build OpenAI function-calling schemas from the tool catalog.

    This is a minimal schema — every tool is declared with its name and
    an empty parameters object. Providers that enforce strict JSON
    schemas may need richer definitions, but the common OpenAI/Anthropic
    tool_use format accepts name-only declarations for many use cases.
    """
    schemas: List[Dict] = []
    for name, fn in tools.items():
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": doc[:200] or f"{name} primitive from agent_toolkit",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        })
    return schemas


def create_agent(
    name: str,
    system_prompt: str,
    tools: Optional[Dict[str, Callable]] = None,
    extra_tools: Optional[Dict[str, Callable]] = None,
    function: str = "code_generation",
    max_iterations: int = 10,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    include_defaults: bool = True,
    model: str = "",
) -> Agent:
    """One-line agent factory.

    Args:
        name: Human-readable agent name (used in agent_id for audit
            trails and token budget tracking).
        system_prompt: System prompt injected on every LLM call.
        tools: If provided, REPLACES the default tool catalog. Pass a
            dict of {name: callable}.
        extra_tools: Tools to ADD on top of the defaults. Most common
            use case — the caller wants defaults plus their own.
        function: LLMRouter function name. See args/llm_config.yaml.
        max_iterations: Cap on agent loop iterations per invoke().
        max_tokens: Per-LLM-call response token budget.
        temperature: LLM sampling temperature.
        include_defaults: If False AND tools is None, the agent has
            NO tools. Useful for pure-chat agents.
        model: Optional explicit model override (e.g., 'claude-sonnet').

    Returns:
        Agent instance.

    Example:
        >>> from tools.agent_toolkit import create_agent
        >>> agent = create_agent(
        ...     name="demo",
        ...     system_prompt="You are a helpful ICDEV assistant.",
        ... )
        >>> result = agent.invoke([
        ...     {"role": "user", "content": "List files in tools/canvas/"}
        ... ])
        >>> print(result.final_content)
    """
    if tools is not None:
        catalog = dict(tools)
    elif include_defaults:
        catalog = dict(_DEFAULT_TOOLS)
    else:
        catalog = {}

    if extra_tools:
        catalog.update(extra_tools)

    return Agent(
        name=name,
        system_prompt=system_prompt,
        tools=catalog,
        function=function,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
    )
