# CUI // SP-CTI
"""Agent loop primitive — automatic LLM re-prompting with native tool use.

This is ICDEV's first reusable, router-level agent loop. An "agent loop" is the
canonical pattern where an agent automatically re-prompts the LLM after each tool
call::

    LLM --tool_calls--> execute tools --tool_result--> re-prompt --> repeat until done

It is routed through :class:`tools.llm.router.LLMRouter` and uses the **native
tool-use** protocol (``LLMRequest.tools`` / ``LLMResponse.tool_calls`` /
``stop_reason``), so it works with any provider whose models report
``supports_tools`` (Anthropic, OpenAI, Bedrock, Ollama tool-capable models). The
CLI bridge provider flattens tools to text and cannot serve tool-use requests —
:func:`run_agent_loop` raises :class:`AgentLoopUnsupported` in that case so the
caller can fall back to a non-agentic mode.

The primitive is **tool-handler-agnostic**: callers declare an OpenAI
function-calling tool schema plus a ``{tool_name: handler}`` map. Handlers receive
``(input_dict, stop_event)`` and return a string result. A handler may return the
:data:`DONE` sentinel to terminate the loop (the ``done`` tool pattern).

Modelled on ``icdev/tools/agent/bedrock_client.py::invoke_with_tools`` but
provider-agnostic via the router, and on
``icdev/tools/anvil/agentic_runner.py::run_agentic_loop`` for the message-history
shape — except here tool calls are native structured blocks, not text JSON.

Usage::

    from tools.llm.router import LLMRouter
    from tools.llm.agent_loop import run_agent_loop, DONE

    router = LLMRouter()
    result = run_agent_loop(
        router,
        system_prompt="You are a coding agent.",
        user_prompt="Fix the bug in foo.py",
        tools=[{"type": "function", "function": {"name": "read_file", ...}}],
        tool_handlers={"read_file": lambda inp, stop: read(inp["path"])},
        llm_function="code_generation",
        max_iterations=12,
    )
    print(result.final_content, result.tool_call_log)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.agent_loop")


# ---------------------------------------------------------------------------
# Sentinels & exceptions
# ---------------------------------------------------------------------------

#: Sentinel a tool handler may return to terminate the loop (the ``done`` tool).
#: Treated as "task complete"; not appended to the message history as a
#: ``tool_result`` — a friendly confirmation string is appended instead.
DONE = "@agent_loop_done@"


class AgentLoopUnsupported(RuntimeError):
    """Raised when the resolved LLM provider cannot serve native tool-use requests.

    The CLI bridge provider flattens ``LLMRequest.tools`` to text and ignores
    them, and some models report ``supports_tools: false``. In either case an
    agent loop cannot function — callers should fall back to a non-agentic mode.
    """


class AgentLoopTimeout(RuntimeError):
    """Raised when ``max_iterations`` is reached without the LLM ending the turn."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopResult:
    """Outcome of :func:`run_agent_loop`.

    Attributes:
        done:      True if the loop terminated via end_turn / ``DONE`` sentinel.
        truncated: True if the loop hit ``max_iterations`` without ending.
        turns:     Number of LLM turns executed.
        final_content: The LLM's final text response (empty if truncated mid-tools).
        stop_reason:   ``stop_reason`` of the final LLM response.
        tool_call_log: List of ``{turn, name, input, result, error}`` dicts.
        messages:      The full message history at termination (for inspection/persist).
        total_input_tokens:  Cumulative input tokens across all turns.
        total_output_tokens: Cumulative output tokens across all turns.
        total_cost_usd:      Cumulative cost across all turns (when provider reports it).
        compression_events:  List of context-compression events applied to messages.
        truncation_reason:   Why the loop stopped: ``completed``, ``max_iterations``,
            ``max_total_tokens``, ``max_cost_usd``, or ``stop_event``.
    """

    done: bool = False
    truncated: bool = False
    turns: int = 0
    final_content: str = ""
    stop_reason: str = ""
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    compression_events: list[dict[str, Any]] = field(default_factory=list)
    truncation_reason: str = ""


# Type alias: handler(input_dict, stop_event) -> str (or DONE sentinel).
ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]
TurnCallback = Callable[[int, Any, list[dict[str, Any]]], None]


# ---------------------------------------------------------------------------
# Budget / context-window helpers
# ---------------------------------------------------------------------------


def _estimate_text_tokens(text: str) -> int:
    """Cheap token estimate: ~4 characters per token, matching ContextCompressor."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Estimate tokens in a single message (text-only; tool blocks counted by text)."""
    content = msg.get("content", "")
    if isinstance(content, list):
        return sum(
            _estimate_text_tokens(str(block.get("text", "")))
            for block in content
            if isinstance(block, dict)
        )
    return _estimate_text_tokens(str(content))




def _load_budget_defaults() -> dict[str, Any]:
    """Read ``agent_loop.budgets`` defaults from repo-root ``args/llm_config.yaml``."""
    defaults: dict[str, Any] = {}
    try:
        import yaml
        from pathlib import Path

        here = Path(__file__).resolve()
        root: Path | None = None
        for parent in [here, *here.parents]:
            if (parent / ".git").exists():
                root = parent
                break
        if root is None:
            return defaults
        cfg_path = root / "args" / "llm_config.yaml"
        if not cfg_path.exists():
            return defaults
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        budgets = raw.get("agent_loop", {}).get("budgets", {})
        for key in (
            "max_total_tokens",
            "context_window_tokens",
            "compression_budget_tokens",
        ):
            if key in budgets:
                defaults[key] = int(budgets[key])
        if "max_cost_usd" in budgets:
            defaults["max_cost_usd"] = float(budgets["max_cost_usd"])
    except Exception as exc:
        logger.debug("agent_loop: failed to load budget defaults: %s", exc)
    return defaults


def _maybe_compress_messages(
    messages: list[dict[str, Any]],
    *,
    context_window_tokens: int | None,
    compression_budget_tokens: int | None,
    compression_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compress message history when it exceeds the context-window threshold.

    Uses :func:`icdev.tools.llm.context_compressor.compress_messages` so tool-use
    blocks are preserved. A failed compression is logged and leaves messages
    unchanged.
    """
    if not context_window_tokens:
        return messages
    current = sum(_estimate_message_tokens(m) for m in messages)
    if current <= context_window_tokens:
        return messages
    try:
        from icdev.tools.llm.context_compressor import compress_messages

        budget = compression_budget_tokens or int(context_window_tokens * 0.75)
        result = compress_messages(messages, budget_tokens=budget, content_type="auto")
        compression_events.append(
            {
                "method": result.method,
                "original_tokens": result.original_tokens,
                "compressed_tokens": result.compressed_tokens,
                "compression_ratio": result.compression_ratio,
            }
        )
        return result.messages
    except Exception as exc:
        logger.warning("agent_loop: context compression failed: %s", exc)
        return messages


# ---------------------------------------------------------------------------
# Capability guard
# ---------------------------------------------------------------------------


def _check_tool_support(router: Any, llm_function: str) -> None:
    """Raise :class:`AgentLoopUnsupported` if the provider can't do native tool use.

    Probes the same provider the router will use for ``llm_function``. The CLI
    bridge (``provider_name == "cli"``) and any model with ``supports_tools: false``
    are rejected.
    """
    try:
        provider, _model_id, model_config = router.get_provider_for_function(llm_function)
    except Exception as exc:  # noqa: BLE001 — probe must degrade to "unsupported"
        raise AgentLoopUnsupported(
            f"Could not resolve a provider for function {llm_function!r}: {exc}"
        ) from exc

    if provider is None:
        raise AgentLoopUnsupported(
            f"No available LLM provider for function {llm_function!r}."
        )

    name = getattr(provider, "provider_name", "") or ""
    if name == "cli":
        raise AgentLoopUnsupported(
            "Agent loop requires native tool use; the CLI bridge provider flattens "
            "tools to text and cannot serve tool-use requests. Fall back to step mode."
        )

    # supports_tools absent → assume True (Anthropic/OpenAI/Bedrock default capable).
    if model_config.get("supports_tools") is False:
        raise AgentLoopUnsupported(
            f"Resolved model for {llm_function!r} reports supports_tools=false; "
            "agent loop requires a tool-capable model."
        )


# ---------------------------------------------------------------------------
# Message-block helpers (universal content-block format)
# ---------------------------------------------------------------------------


def _assistant_message(response: Any) -> dict[str, Any]:
    """Build an assistant message with text + tool_use blocks from an LLMResponse."""
    content: list[dict[str, Any]] = []
    text = (response.content or "").strip()
    if text:
        content.append({"type": "text", "text": text})
    for tc in response.tool_calls or []:
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or "",
                "name": tc.get("name") or "",
                "input": tc.get("input") or {},
            }
        )
    return {"role": "assistant", "content": content}


def _tool_result_message(
    tool_use_id: str, text: str, tool_name: str = "", is_error: bool = False
) -> dict[str, Any]:
    """Build a user message carrying a single tool_result content block.

    ``tool_name`` is included so providers that use a dedicated tool-result
    message role (e.g. Ollama's ``{"role":"tool","name":...}``) can emit it
    without having to reverse-map ``tool_use_id`` back to a name.
    """
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "name": tool_name,
        "content": [{"type": "text", "text": text}],
    }
    if is_error:
        block["is_error"] = True
    return {"role": "user", "content": [block]}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_agent_loop(
    router: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, ToolHandler],
    llm_function: str = "code_generation",
    max_iterations: int = 12,
    max_tokens: int = 4096,
    temperature: float = 1.0,
    effort: str = "medium",
    stop_event: threading.Event | None = None,
    on_turn: TurnCallback | None = None,
    max_total_tokens: int | None = None,
    max_cost_usd: float | None = None,
    context_window_tokens: int | None = None,
    compression_budget_tokens: int | None = None,
) -> AgentLoopResult:
    """Run an agentic LLM loop with native tool use until the task is done.

    Each turn:
      1. Optionally compress message history if it exceeds the context window.
      2. Call ``router.invoke(llm_function, LLMRequest(... tools=tools ...))``.
      3. If the response has no ``tool_calls`` → the LLM ended the turn; return
         its ``content`` as the final answer.
      4. Otherwise append the assistant ``tool_use`` message, dispatch each tool
         call through ``tool_handlers``, append ``tool_result`` messages, and
         re-prompt. A handler returning :data:`DONE` terminates the loop.

    Termination: end_turn (no tool_calls), a ``DONE`` sentinel from a handler,
    ``stop_event`` set, ``max_iterations`` reached (→ ``truncated=True``), or a
    hard budget cap exceeded (``max_total_tokens`` / ``max_cost_usd``).

    Args:
        router:        An ``LLMRouter`` instance.
        system_prompt: System instruction for the agent.
        user_prompt:   Initial user task message.
        tools:         OpenAI function-calling tool schema list.
        tool_handlers: ``{tool_name: handler}`` — ``handler(input, stop_event) -> str``.
        llm_function:  Router routing function key (default ``code_generation``).
        max_iterations: Hard cap on LLM turns (default 12).
        max_tokens:    Per-turn max output tokens.
        temperature:   Sampling temperature.
        effort:        Router effort tier (low/medium/high/max).
        stop_event:    When set, the loop exits at the next turn boundary.
        on_turn:       Optional ``callback(turn, response, messages)`` after each
                       turn (for audit/persist/observability).
        max_total_tokens: Hard cap on cumulative input+output tokens across turns.
        max_cost_usd:     Hard cap on cumulative USD cost (when providers report it).
        context_window_tokens: Soft threshold; if message history exceeds this,
            it is compressed before the next LLM turn.
        compression_budget_tokens: Target token budget used when compression is
            triggered (defaults to 75% of ``context_window_tokens``).

    Returns:
        AgentLoopResult.

    Raises:
        AgentLoopUnsupported: resolved provider cannot do native tool use.
    """
    # Lazy import to avoid import cycles at module load.
    from tools.llm.provider import LLMRequest

    _check_tool_support(router, llm_function)

    # Resolve optional budget defaults from args/llm_config.yaml.
    budget_defaults = _load_budget_defaults()
    if max_total_tokens is None and "max_total_tokens" in budget_defaults:
        max_total_tokens = budget_defaults["max_total_tokens"]
    if max_cost_usd is None and "max_cost_usd" in budget_defaults:
        max_cost_usd = budget_defaults["max_cost_usd"]
    if context_window_tokens is None and "context_window_tokens" in budget_defaults:
        context_window_tokens = budget_defaults["context_window_tokens"]
    if compression_budget_tokens is None and "compression_budget_tokens" in budget_defaults:
        compression_budget_tokens = budget_defaults["compression_budget_tokens"]
    if compression_budget_tokens is None and context_window_tokens is not None:
        compression_budget_tokens = int(context_window_tokens * 0.75)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt}
    ]
    tool_call_log: list[dict[str, Any]] = []
    result = AgentLoopResult(messages=messages)

    response: Any = None
    for turn in range(max_iterations):
        if stop_event is not None and stop_event.is_set():
            break

        # Compress message history when it is approaching the model context window.
        messages = _maybe_compress_messages(
            messages,
            context_window_tokens=context_window_tokens,
            compression_budget_tokens=compression_budget_tokens,
            compression_events=result.compression_events,
        )
        result.messages = messages

        request = LLMRequest(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
        )
        response = router.invoke(llm_function, request)
        result.turns = turn + 1
        result.stop_reason = getattr(response, "stop_reason", "") or ""
        result.final_content = getattr(response, "content", "") or ""
        result.total_input_tokens += getattr(response, "input_tokens", 0) or 0
        result.total_output_tokens += getattr(response, "output_tokens", 0) or 0
        result.total_cost_usd += getattr(response, "cost_usd", 0.0) or 0.0

        # No tool calls → LLM ended the turn with a final answer.
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            messages.append(_assistant_message(response))  # final assistant text
            result.done = True
            if on_turn is not None:
                on_turn(turn, response, messages)
            break

        # Append the assistant's tool_use message.
        messages.append(_assistant_message(response))

        # Execute each tool call and append tool_result blocks.
        done_signalled = False
        for tc in tool_calls:
            tc_id = tc.get("id") or ""
            tc_name = tc.get("name") or ""
            tc_input = tc.get("input") or {}
            entry: dict[str, Any] = {
                "turn": turn,
                "name": tc_name,
                "input": tc_input,
                "result": "",
                "error": None,
            }
            handler = tool_handlers.get(tc_name)
            if handler is None:
                err_text = f"Tool {tc_name!r} is not registered."
                entry["error"] = err_text
                tool_call_log.append(entry)
                messages.append(_tool_result_message(tc_id, err_text, tool_name=tc_name, is_error=True))
                continue

            try:
                handler_out = handler(tc_input, stop_event)
            except Exception as exc:  # noqa: BLE001 — surface errors to the LLM
                err_text = f"{type(exc).__name__}: {exc}"
                entry["error"] = err_text
                tool_call_log.append(entry)
                messages.append(_tool_result_message(tc_id, err_text, tool_name=tc_name, is_error=True))
                logger.warning("agent_loop: handler %s raised: %s", tc_name, exc)
                continue

            if handler_out is DONE or handler_out == DONE:
                done_signalled = True
                entry["result"] = "DONE"
                tool_call_log.append(entry)
                messages.append(_tool_result_message(tc_id, "Task complete.", tool_name=tc_name))
                # Continue draining remaining tool_calls in this turn, then stop.
                continue

            out_text = str(handler_out)
            entry["result"] = out_text
            tool_call_log.append(entry)
            messages.append(_tool_result_message(tc_id, out_text, tool_name=tc_name))

        if on_turn is not None:
            on_turn(turn, response, messages)

        # Hard budget check after executing all tools this turn.
        if max_total_tokens is not None and (
            result.total_input_tokens + result.total_output_tokens
        ) > max_total_tokens:
            result.truncated = True
            result.truncation_reason = "max_total_tokens"
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Agent loop stopped: exceeded max_total_tokens="
                        f"{max_total_tokens} (input={result.total_input_tokens}, "
                        f"output={result.total_output_tokens})."
                    ),
                }
            )
            break

        if max_cost_usd is not None and result.total_cost_usd > max_cost_usd:
            result.truncated = True
            result.truncation_reason = "max_cost_usd"
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Agent loop stopped: exceeded max_cost_usd={max_cost_usd:.4f} "
                        f"(current={result.total_cost_usd:.4f})."
                    ),
                }
            )
            break

        if done_signalled:
            result.done = True
            break

        if stop_event is not None and stop_event.is_set():
            break
    else:
        # for-loop exhausted without break → hit max_iterations.
        result.truncated = True
        logger.warning(
            "agent_loop: hit max_iterations=%d without termination for llm_function=%s",
            max_iterations,
            llm_function,
        )

    result.tool_call_log = tool_call_log
    result.messages = messages
    if response is not None:
        result.final_content = getattr(response, "content", "") or result.final_content
        result.stop_reason = getattr(response, "stop_reason", "") or result.stop_reason

    # Set the human-readable truncation/completion reason for the caller.
    if result.truncated and not result.truncation_reason:
        result.truncation_reason = "max_iterations"
    elif result.done and not result.truncated:
        result.truncation_reason = "completed"
    elif not result.done and not result.truncated:
        result.truncation_reason = "stop_event"
    return result
