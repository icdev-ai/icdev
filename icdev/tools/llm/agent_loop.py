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

Improvements over v1:
  - **Parallel read-only tool execution**: tools with ``"is_read_only": true`` in
    their schema run concurrently via a ``ThreadPoolExecutor``; state-modifying
    tools still run sequentially.
  - **ResultSubtype**: typed string constants mirror the Claude Agent SDK result
    subtypes so callers can switch on well-defined values.
  - **Per-tool timeout**: ``tool_timeout_seconds`` prevents a hung handler from
    blocking the entire loop.
  - **Structured output retries**: ``output_schema`` + ``max_structured_output_retries``
    validate ``final_content`` as JSON and auto-retry if the LLM produces invalid output.
  - **Lifecycle hooks**: ``on_pre_tool_use`` (can block), ``on_post_tool_use`` (audit),
    ``on_stop`` (fires on all exit paths). ``on_turn`` kept for backward compat.

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
        tools=[{"type": "function", "function": {"name": "read_file", "is_read_only": True, ...}}],
        tool_handlers={"read_file": lambda inp, stop: read(inp["path"])},
        llm_function="code_generation",
        max_iterations=12,
        tool_timeout_seconds=30,
    )
    print(result.final_content, result.result_subtype)
"""
from __future__ import annotations

import concurrent.futures as _futures
import json
import math
import threading
import uuid as _uuid
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
# Result subtypes — mirrors Claude Agent SDK result subtypes
# ---------------------------------------------------------------------------


class ResultSubtype:
    """String constants for :attr:`AgentLoopResult.result_subtype`.

    Mirrors the Claude Agent SDK ``ResultMessage.subtype`` values so callers can
    switch on well-defined outcomes rather than an untyped string.
    """

    success = "success"
    """Loop ended cleanly (end_turn or DONE sentinel)."""

    error_max_turns = "error_max_turns"
    """Hit ``max_iterations`` without the LLM ending the turn."""

    error_max_budget_tokens = "error_max_budget_tokens"
    """Cumulative token count exceeded ``max_total_tokens``."""

    error_max_budget_cost = "error_max_budget_cost"
    """Cumulative cost exceeded ``max_cost_usd``."""

    error_stop_event = "error_stop_event"
    """External ``stop_event`` was set before the loop finished."""

    error_during_execution = "error_during_execution"
    """Unhandled error during the LLM invocation phase."""

    error_max_structured_output_retries = "error_max_structured_output_retries"
    """``output_schema`` provided but valid JSON not produced within retry limit."""

    error_consecutive_tool_failures = "error_consecutive_tool_failures"
    """Every tool call in each of N consecutive turns returned an error."""

    error_stalled = "error_stalled"
    """No novel successful tool call for ``stall_threshold`` consecutive turns."""


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
        result_subtype: One of the :class:`ResultSubtype` string constants.
        tool_call_log: List of ``{turn, name, input, result, error}`` dicts.
        messages:      The full message history at termination (for inspection/persist).
        total_input_tokens:  Cumulative input tokens across all turns.
        total_output_tokens: Cumulative output tokens across all turns.
        total_cost_usd:      Cumulative cost across all turns (when provider reports it).
        compression_events:  List of context-compression events applied to messages.
        truncation_reason:   Why the loop stopped: ``completed``, ``max_iterations``,
            ``max_total_tokens``, ``max_cost_usd``, or ``stop_event``.
            Kept for backward compat — prefer ``result_subtype``.
        session_id:  UUID generated at loop start. Pass as ``resume_session_id`` to
            a subsequent :func:`run_agent_loop` call to restore this conversation.
    """

    done: bool = False
    truncated: bool = False
    turns: int = 0
    final_content: str = ""
    stop_reason: str = ""
    result_subtype: str = ""
    session_id: str = ""
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    compression_events: list[dict[str, Any]] = field(default_factory=list)
    truncation_reason: str = ""
    model_id: str = ""
    """model_id from the first successful LLM response — used by cross-grader enforcement."""
    provider: str = ""
    """provider name from the first successful LLM response."""


# Type aliases for callback hooks.
ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]
TurnCallback = Callable[[int, Any, list[dict[str, Any]]], None]
PreToolUseHook = Callable[[str, dict[str, Any]], "str | None"]
PostToolUseHook = Callable[[str, dict[str, Any], str, bool], None]
StopHook = Callable[["AgentLoopResult"], None]


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
        if "tool_timeout_seconds" in budgets:
            defaults["tool_timeout_seconds"] = float(budgets["tool_timeout_seconds"])
        if "tool_result_max_chars" in budgets:
            defaults["tool_result_max_chars"] = int(budgets["tool_result_max_chars"])
        if "llm_call_timeout_seconds" in budgets:
            defaults["llm_call_timeout_seconds"] = float(budgets["llm_call_timeout_seconds"])
        if "stall_threshold" in budgets:
            defaults["stall_threshold"] = int(budgets["stall_threshold"])
        mem_cfg = raw.get("agent_loop", {}).get("memory", {})
        if "enabled" in mem_cfg:
            defaults["memory_enabled"] = bool(mem_cfg["enabled"])
        if "top_k" in mem_cfg:
            defaults["memory_top_k"] = int(mem_cfg["top_k"])
        if "tier" in mem_cfg:
            defaults["memory_tier"] = str(mem_cfg["tier"])
    except Exception as exc:
        logger.debug("agent_loop: failed to load budget defaults: %s", exc)
    return defaults


def _retrieve_memory_context(user_prompt: str, top_k: int, tier: str) -> str:
    """Retrieve relevant memory and format as a context block for the system prompt.

    Called once before the first LLM turn. Returns empty string on any error
    so the loop degrades gracefully when the memory system is unavailable.
    """
    if not user_prompt or top_k <= 0:
        return ""
    try:
        from tools.memory.hybrid_search import search as _mem_search
        results = _mem_search(user_prompt, limit=top_k, tier=tier or "episodic|semantic")
        if not results:
            return ""
        lines = ["## Retrieved Memory Context"]
        for r in results:
            content = (r.get("content") or "").strip()
            if content:
                entry_type = r.get("type", "event")
                lines.append(f"- [{entry_type}] {content[:400]}")
        if len(lines) <= 1:
            return ""
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("agent_loop: memory retrieval failed: %s", exc)
        return ""


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
            f"No available provider for function {llm_function!r}."
        )
    provider_name = getattr(provider, "provider_name", None) or ""
    if provider_name == "cli":
        raise AgentLoopUnsupported(
            "CLI bridge provider cannot serve native tool-use requests — "
            "it flattens tools to text. Use a direct API provider."
        )
    if model_config and model_config.get("supports_tools") is False:
        raise AgentLoopUnsupported(
            f"Model resolved for {llm_function!r} reports supports_tools=false."
        )


# ---------------------------------------------------------------------------
# Structured output validation (stdlib only, no jsonschema dep)
# ---------------------------------------------------------------------------


def _validate_output_schema(obj: Any, schema: dict[str, Any]) -> None:
    """Lightweight schema check: type check + required-key presence.

    Only validates if the schema declares ``required`` or ``properties``. Raises
    ``ValueError`` with a descriptive message on failure.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object (dict), got {type(obj).__name__}")
    required = schema.get("required", [])
    for key in required:
        if key not in obj:
            raise ValueError(f"Missing required field: {key!r}")
    for key, prop_schema in schema.get("properties", {}).items():
        if key not in obj:
            continue
        expected_type = prop_schema.get("type")
        if expected_type == "array" and not isinstance(obj[key], list):
            raise ValueError(f"Field {key!r} must be an array, got {type(obj[key]).__name__}")
        elif expected_type == "object" and not isinstance(obj[key], dict):
            raise ValueError(f"Field {key!r} must be an object, got {type(obj[key]).__name__}")
        elif expected_type == "string" and not isinstance(obj[key], str):
            raise ValueError(f"Field {key!r} must be a string, got {type(obj[key]).__name__}")
        elif expected_type in ("integer", "number") and not isinstance(obj[key], (int, float)):
            raise ValueError(f"Field {key!r} must be a number, got {type(obj[key]).__name__}")


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
# Tool execution helpers
# ---------------------------------------------------------------------------


def _truncate_tool_result(text: str, max_chars: int | None) -> str:
    """Truncate a tool result to *max_chars* with a notice appended."""
    if max_chars is None or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 80)
    notice = (
        f"\n[tool_result truncated: {len(text)} chars total, "
        f"showing first {keep} — pass max_results or a narrower scope to reduce output]"
    )
    return text[:keep] + notice


def _build_read_only_set(tools: list[dict[str, Any]]) -> set[str]:
    """Return the set of tool names marked ``is_read_only`` in their schema."""
    read_only: set[str] = set()
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function", {})
        name = fn.get("name", "") or t.get("name", "")
        if fn.get("is_read_only") or t.get("is_read_only"):
            read_only.add(name)
    return read_only


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
    on_pre_tool_use: PreToolUseHook | None = None,
    on_post_tool_use: PostToolUseHook | None = None,
    on_stop: StopHook | None = None,
    max_total_tokens: int | None = None,
    max_cost_usd: float | None = None,
    context_window_tokens: int | None = None,
    compression_budget_tokens: int | None = None,
    tool_timeout_seconds: float | None = None,
    output_schema: dict[str, Any] | None = None,
    max_structured_output_retries: int = 3,
    resume_session_id: str | None = None,
    tool_result_max_chars: int | None = None,
    max_consecutive_errors: int | None = 3,
    llm_call_timeout_seconds: float | None = None,
    stall_threshold: int | None = None,
    memory_enabled: bool | None = None,
    memory_top_k: int | None = None,
    memory_tier: str | None = None,
) -> AgentLoopResult:
    """Run an agentic LLM loop with native tool use until the task is done.

    Each turn:
      1. Optionally compress message history if it exceeds the context window.
      2. Call ``router.invoke(llm_function, LLMRequest(... tools=tools ...))``.
      3. If the response has no ``tool_calls`` → the LLM ended the turn; return
         its ``content`` as the final answer.
      4. Otherwise append the assistant ``tool_use`` message, dispatch each tool
         call (read-only tools in parallel, state-modifying ones sequentially),
         append ``tool_result`` messages, and re-prompt. A handler returning
         :data:`DONE` terminates the loop.

    Termination: end_turn (no tool_calls), a ``DONE`` sentinel from a handler,
    ``stop_event`` set, ``max_iterations`` reached (→ ``truncated=True``), or a
    hard budget cap exceeded (``max_total_tokens`` / ``max_cost_usd``).

    Args:
        router:        An ``LLMRouter`` instance.
        system_prompt: System instruction for the agent.
        user_prompt:   Initial user task message.
        tools:         OpenAI function-calling tool schema list. Add
                       ``"is_read_only": true`` inside the ``function`` dict to
                       mark tools that can run concurrently.
        tool_handlers: ``{tool_name: handler}`` — ``handler(input, stop_event) -> str``.
        llm_function:  Router routing function key (default ``code_generation``).
        max_iterations: Hard cap on LLM turns (default 12).
        max_tokens:    Per-turn max output tokens.
        temperature:   Sampling temperature.
        effort:        Router effort tier (low/medium/high/xhigh/max).
        stop_event:    When set, the loop exits at the next turn boundary.
        on_turn:       Optional ``callback(turn, response, messages)`` after each
                       turn (for audit/persist/observability). Kept for backward compat.
        on_pre_tool_use: Optional ``hook(name, input) -> str | None`` called before
                       each tool executes. Return a non-empty string to block
                       execution; that string becomes the error tool_result text.
        on_post_tool_use: Optional ``hook(name, input, result_text, is_error)``
                       called after each tool execution for audit/sidecar use.
        on_stop:       Optional ``hook(result)`` called once when the loop ends,
                       regardless of exit reason, before returning to the caller.
        max_total_tokens: Hard cap on cumulative input+output tokens across turns.
        max_cost_usd:     Hard cap on cumulative USD cost (when providers report it).
        context_window_tokens: Soft threshold; if message history exceeds this,
            it is compressed before the next LLM turn.
        compression_budget_tokens: Target token budget used when compression is
            triggered (defaults to 75% of ``context_window_tokens``).
        tool_timeout_seconds: Per-tool execution timeout. If a handler doesn't
            return within this many seconds, it is cancelled and an error
            tool_result is appended. ``None`` means no timeout (default). Loaded
            from ``args/llm_config.yaml`` ``agent_loop.budgets.tool_timeout_seconds``
            when not explicitly set.
        output_schema: Optional JSON Schema dict. After the loop ends with
            ``done=True``, ``final_content`` is parsed as JSON and validated
            against this schema. If invalid, the LLM is re-prompted up to
            ``max_structured_output_retries`` times. On exhaustion, result_subtype
            is set to ``error_max_structured_output_retries``.
        max_structured_output_retries: Max re-prompt attempts when
            ``output_schema`` validation fails (default 3).
        resume_session_id: UUID of a prior :class:`AgentLoopResult`. When set,
            the saved message history is loaded from the ``agent_loop_sessions``
            table and used as the starting conversation instead of a fresh
            ``user_prompt`` message. This lets a loop resume after a budget hit,
            ``max_iterations`` truncation, or process restart.
        tool_result_max_chars: Maximum characters for any single tool result returned
            to the LLM. Results exceeding this limit are truncated with a notice.
            ``None`` means no truncation (default). Loaded from
            ``args/llm_config.yaml`` ``agent_loop.budgets.tool_result_max_chars``
            when not explicitly set.
        max_consecutive_errors: If every tool call in each of N consecutive turns
            returns an error (``is_error=True``), abort with
            ``ResultSubtype.error_consecutive_tool_failures``. ``None`` disables
            this guard. Default 3. Loaded from
            ``args/llm_config.yaml`` ``agent_loop.budgets.max_consecutive_errors``.

    Returns:
        :class:`AgentLoopResult`.

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
    if tool_timeout_seconds is None and "tool_timeout_seconds" in budget_defaults:
        tool_timeout_seconds = budget_defaults["tool_timeout_seconds"]
    if tool_result_max_chars is None and "tool_result_max_chars" in budget_defaults:
        tool_result_max_chars = budget_defaults["tool_result_max_chars"]
    if llm_call_timeout_seconds is None and "llm_call_timeout_seconds" in budget_defaults:
        llm_call_timeout_seconds = budget_defaults["llm_call_timeout_seconds"]
    if stall_threshold is None and "stall_threshold" in budget_defaults:
        stall_threshold = int(budget_defaults["stall_threshold"])
    if stall_threshold is None:
        stall_threshold = 3
    # max_consecutive_errors: Python default=3, None=explicitly disabled.
    # Do NOT load from budget_defaults — None must mean "disable", not "use config".

    # Resolve memory injection config from args/llm_config.yaml.
    if memory_enabled is None:
        memory_enabled = budget_defaults.get("memory_enabled", True)
    if memory_top_k is None:
        memory_top_k = budget_defaults.get("memory_top_k", 5)
    if memory_tier is None:
        memory_tier = budget_defaults.get("memory_tier", "episodic|semantic")

    # Inject retrieved memory into system_prompt before the first turn.
    if memory_enabled and not resume_session_id:
        _mem_ctx = _retrieve_memory_context(user_prompt, memory_top_k, memory_tier)
        if _mem_ctx:
            system_prompt = system_prompt + "\n\n" + _mem_ctx

    # Build set of read-only tool names for parallel execution.
    _read_only_tools = _build_read_only_set(tools)

    # Assign a unique session ID so callers can persist and resume this loop.
    session_id = str(_uuid.uuid4())

    # Resume: if a prior session ID is given, load its message history instead
    # of starting fresh. Falls back to a new conversation if the session is not
    # found or the DB is unavailable.
    messages: list[dict[str, Any]]
    if resume_session_id:
        try:
            from icdev.tools.llm.agent_loop_session import load_session as _load_session
            prior = _load_session(resume_session_id)
        except Exception as _exc:
            logger.warning("agent_loop: could not load session %s: %s", resume_session_id, _exc)
            prior = []
        if prior:
            messages = list(prior)
            logger.info(
                "agent_loop: resuming session %s → %s (%d prior messages)",
                resume_session_id,
                session_id,
                len(messages),
            )
        else:
            messages = [{"role": "user", "content": user_prompt}]
    else:
        messages = [{"role": "user", "content": user_prompt}]

    tool_call_log: list[dict[str, Any]] = []
    result = AgentLoopResult(messages=messages, session_id=session_id)

    response: Any = None

    # Single executor shared across all turns for parallel read-only tool execution
    # and per-tool timeouts on sequential tools.
    _consecutive_all_error_turns = 0
    # Loop control state — controls 2, 3, 5.
    _budget_pressure_injected = False
    _call_counts: dict[str, int] = {}
    _DUPLICATE_WARN_THRESHOLD = 3
    _DUPLICATE_ERROR_THRESHOLD = 5
    _last_progress_turn: int = -1
    _seen_call_keys: set[str] = set()
    with _futures.ThreadPoolExecutor(max_workers=16) as executor:
        for turn in range(max_iterations):
            if stop_event is not None and stop_event.is_set():
                break

            # Control 5: Stall detector — abort if no novel successful call for N turns.
            if _last_progress_turn >= 0 and (turn - _last_progress_turn) >= stall_threshold:
                logger.warning(
                    "agent_loop: stall detected — no novel successful tool call for %d turns"
                    " (last_progress_turn=%d, current_turn=%d)",
                    turn - _last_progress_turn, _last_progress_turn, turn,
                )
                result.result_subtype = ResultSubtype.error_stalled
                result.truncated = True
                break

            # Control 2: Budget pressure — inject 'N turns remaining' in last 20% of budget.
            _turns_remaining = max_iterations - turn
            _pressure_threshold = max(1, math.ceil(max_iterations * 0.2))
            if not _budget_pressure_injected and 0 < _turns_remaining <= _pressure_threshold:
                messages.append({
                    "role": "user",
                    "content": (
                        f"[System] You have {_turns_remaining} turn(s) remaining. "
                        "If the task is not complete, call done() now with partial results "
                        "and a clear description of what remains to be done."
                    ),
                })
                _budget_pressure_injected = True

            # Control 4: Compress message history; notify model when compression occurred.
            _pre_compress_len = len(messages)
            messages = _maybe_compress_messages(
                messages,
                context_window_tokens=context_window_tokens,
                compression_budget_tokens=compression_budget_tokens,
                compression_events=result.compression_events,
            )
            if len(messages) < _pre_compress_len:
                messages.append({
                    "role": "user",
                    "content": (
                        "[System] Your conversation history was compressed to fit the context window. "
                        "Prior work has been summarized above. The current task and any completed "
                        "steps are preserved in the summary. Continue from the current state."
                    ),
                })
            result.messages = messages

            request = LLMRequest(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                effort=effort,
            )
            try:
                # Control 1: LLM call timeout — wraps the blocking invoke call.
                if llm_call_timeout_seconds:
                    _llm_fut = executor.submit(router.invoke, llm_function, request)
                    try:
                        response = _llm_fut.result(timeout=llm_call_timeout_seconds)
                    except _futures.TimeoutError:
                        logger.error(
                            "agent_loop: LLM call timed out after %.0fs (turn %d/%d)",
                            llm_call_timeout_seconds, turn + 1, max_iterations,
                        )
                        result.result_subtype = ResultSubtype.error_during_execution
                        result.truncated = True
                        result.truncation_reason = "error_during_execution"
                        break
                else:
                    response = router.invoke(llm_function, request)
            except Exception as exc:
                logger.error("agent_loop: LLM invocation failed on turn %d: %s", turn, exc)
                result.truncated = True
                result.result_subtype = ResultSubtype.error_during_execution
                result.truncation_reason = "error_during_execution"
                break

            result.turns = turn + 1
            result.stop_reason = getattr(response, "stop_reason", "") or ""
            result.final_content = getattr(response, "content", "") or ""
            result.total_input_tokens += getattr(response, "input_tokens", 0) or 0
            result.total_output_tokens += getattr(response, "output_tokens", 0) or 0
            result.total_cost_usd += getattr(response, "cost_usd", 0.0) or 0.0
            # Cross-grader: capture model/provider from first successful LLM response.
            if not result.model_id:
                result.model_id = getattr(response, "model_id", "") or ""
            if not result.provider:
                result.provider = getattr(response, "provider", "") or ""

            # No tool calls → LLM ended the turn with a final answer.
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                messages.append(_assistant_message(response))  # final assistant text
                result.done = True
                result.result_subtype = ResultSubtype.success
                if on_turn is not None:
                    on_turn(turn, response, messages)
                break

            # Append the assistant's tool_use message.
            messages.append(_assistant_message(response))

            # ----------------------------------------------------------------
            # Execute tool calls.
            # Read-only tools run in parallel; state-modifying run sequentially.
            # Results are collected and appended in original call order.
            # ----------------------------------------------------------------
            ro_indices = [
                i for i, tc in enumerate(tool_calls)
                if tc.get("name", "") in _read_only_tools
            ]
            seq_indices = [
                i for i in range(len(tool_calls))
                if i not in set(ro_indices)
            ]

            # tc_results[i] = (out_text, is_error, error_msg_or_None)
            tc_results: dict[int, tuple[str, bool, str | None]] = {}

            # -- Read-only tools: submit all, resolve all --
            ro_futures: dict[int, _futures.Future[Any]] = {}
            for i in ro_indices:
                tc = tool_calls[i]
                tc_name = tc.get("name") or ""
                tc_input = tc.get("input") or {}
                handler = tool_handlers.get(tc_name)
                if handler is None:
                    err = f"Tool {tc_name!r} is not registered."
                    tc_results[i] = (err, True, err)
                    continue
                block_msg = on_pre_tool_use(tc_name, tc_input) if on_pre_tool_use else None
                if block_msg:
                    tc_results[i] = (block_msg, True, f"blocked: {block_msg}")
                    continue
                ro_futures[i] = executor.submit(handler, tc_input, stop_event)

            for i, fut in ro_futures.items():
                tc = tool_calls[i]
                tc_name = tc.get("name") or ""
                tc_input = tc.get("input") or {}
                try:
                    out = fut.result(timeout=tool_timeout_seconds)
                    if out is DONE or out == DONE:
                        tc_results[i] = (DONE, False, None)
                    else:
                        tc_results[i] = (str(out), False, None)
                except _futures.TimeoutError:
                    msg = f"Tool {tc_name!r} timed out after {tool_timeout_seconds}s."
                    tc_results[i] = (msg, True, msg)
                    logger.warning("agent_loop: handler %s timed out after %ss", tc_name, tool_timeout_seconds)
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                    tc_results[i] = (err, True, err)
                    logger.warning("agent_loop: handler %s raised: %s", tc_name, exc)

            # -- Sequential tools: one at a time --
            for i in seq_indices:
                tc = tool_calls[i]
                tc_name = tc.get("name") or ""
                tc_input = tc.get("input") or {}
                handler = tool_handlers.get(tc_name)
                if handler is None:
                    err = f"Tool {tc_name!r} is not registered."
                    tc_results[i] = (err, True, err)
                    continue
                block_msg = on_pre_tool_use(tc_name, tc_input) if on_pre_tool_use else None
                if block_msg:
                    tc_results[i] = (block_msg, True, f"blocked: {block_msg}")
                    continue
                try:
                    if tool_timeout_seconds is not None:
                        fut = executor.submit(handler, tc_input, stop_event)
                        out = fut.result(timeout=tool_timeout_seconds)
                    else:
                        out = handler(tc_input, stop_event)
                    if out is DONE or out == DONE:
                        tc_results[i] = (DONE, False, None)
                    else:
                        tc_results[i] = (str(out), False, None)
                except _futures.TimeoutError:
                    msg = f"Tool {tc_name!r} timed out after {tool_timeout_seconds}s."
                    tc_results[i] = (msg, True, msg)
                    logger.warning("agent_loop: handler %s timed out after %ss", tc_name, tool_timeout_seconds)
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                    tc_results[i] = (err, True, err)
                    logger.warning("agent_loop: handler %s raised: %s", tc_name, exc)

            # Circuit breaker: track turns where EVERY tool call errored.
            if tc_results:
                all_errors_this_turn = all(is_err for _, is_err, _ in tc_results.values())
                if all_errors_this_turn:
                    _consecutive_all_error_turns += 1
                else:
                    _consecutive_all_error_turns = 0

            # -- Append results in original call order --
            done_signalled = False
            for i, tc in enumerate(tool_calls):
                tc_id = tc.get("id") or ""
                tc_name = tc.get("name") or ""
                tc_input = tc.get("input") or {}
                out_text, is_error, err_msg = tc_results.get(
                    i, ("Tool execution failed (no result captured).", True, "no result captured")
                )

                # Controls 3 & 5: duplicate-call detection and stall tracking.
                _call_key = f"{tc_name}:{json.dumps(tc_input, sort_keys=True, default=str)}"
                _call_counts[_call_key] = _call_counts.get(_call_key, 0) + 1
                _call_n = _call_counts[_call_key]
                if out_text is not DONE and out_text != DONE:
                    if _call_n >= _DUPLICATE_ERROR_THRESHOLD:
                        # Control 3: block the call outright — force a new approach.
                        out_text = (
                            f"[Loop guard] Tool '{tc_name}' has been called {_call_n} times with "
                            f"identical inputs. Blocking to prevent an infinite loop. "
                            f"You must try a fundamentally different approach."
                        )
                        is_error = True
                        err_msg = f"duplicate-blocked:{_call_key}"
                    elif _call_n >= _DUPLICATE_WARN_THRESHOLD and not is_error:
                        # Control 3: prepend a warning but still return the result.
                        out_text = (
                            f"[Loop guard] Warning: '{tc_name}' called with identical inputs "
                            f"{_call_n} time(s). If this is not advancing the task, try a "
                            f"different approach.\n\n"
                        ) + out_text

                # Control 5: update stall tracker on novel successful calls.
                if not is_error and out_text is not DONE and out_text != DONE:
                    if _call_key not in _seen_call_keys:
                        _seen_call_keys.add(_call_key)
                        _last_progress_turn = turn

                entry: dict[str, Any] = {
                    "turn": turn,
                    "name": tc_name,
                    "input": tc_input,
                    "result": "",
                    "error": None,
                }
                if out_text is DONE or out_text == DONE:
                    done_signalled = True
                    entry["result"] = "DONE"
                    tool_call_log.append(entry)
                    messages.append(_tool_result_message(tc_id, "Task complete.", tool_name=tc_name))
                elif is_error:
                    entry["error"] = err_msg
                    tool_call_log.append(entry)
                    messages.append(
                        _tool_result_message(tc_id, out_text, tool_name=tc_name, is_error=True)
                    )
                else:
                    entry["result"] = out_text  # untruncated for session replay
                    tool_call_log.append(entry)
                    messages.append(_tool_result_message(
                        tc_id,
                        _truncate_tool_result(out_text, tool_result_max_chars),
                        tool_name=tc_name,
                    ))

                # Fire post-tool-use hook (observe only, cannot block at this stage).
                if on_post_tool_use is not None:
                    effective_result = out_text if not is_error else (err_msg or out_text)
                    try:
                        on_post_tool_use(tc_name, tc_input, effective_result, is_error)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("agent_loop: on_post_tool_use raised: %s", exc)

            if on_turn is not None:
                on_turn(turn, response, messages)

            # Hard budget check after executing all tools this turn.
            if max_total_tokens is not None and (
                result.total_input_tokens + result.total_output_tokens
            ) > max_total_tokens:
                result.truncated = True
                result.result_subtype = ResultSubtype.error_max_budget_tokens
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
                result.result_subtype = ResultSubtype.error_max_budget_cost
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
                result.result_subtype = ResultSubtype.success
                break

            if (
                max_consecutive_errors is not None
                and _consecutive_all_error_turns >= max_consecutive_errors
            ):
                result.truncated = True
                result.result_subtype = ResultSubtype.error_consecutive_tool_failures
                result.truncation_reason = "error_consecutive_tool_failures"
                logger.warning(
                    "agent_loop: aborting after %d consecutive all-error turns",
                    _consecutive_all_error_turns,
                )
                break

            if stop_event is not None and stop_event.is_set():
                break
        else:
            # for-loop exhausted without break → hit max_iterations.
            result.truncated = True
            result.result_subtype = ResultSubtype.error_max_turns
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
    elif not result.done and not result.truncated and not result.truncation_reason:
        result.truncation_reason = "stop_event"
        if not result.result_subtype:
            result.result_subtype = ResultSubtype.error_stop_event

    # -------------------------------------------------------------------------
    # Structured output validation (post-loop, only when output_schema provided)
    # -------------------------------------------------------------------------
    if output_schema is not None and result.done and not result.truncated:
        for retry in range(max_structured_output_retries + 1):
            try:
                parsed = json.loads(result.final_content)
                _validate_output_schema(parsed, output_schema)
                break  # valid — keep result.done=True, result_subtype=success
            except (json.JSONDecodeError, ValueError) as exc:
                if retry == max_structured_output_retries:
                    logger.warning(
                        "agent_loop: structured output validation exhausted after %d retries: %s",
                        max_structured_output_retries,
                        exc,
                    )
                    result.done = False
                    result.truncated = True
                    result.result_subtype = ResultSubtype.error_max_structured_output_retries
                    result.truncation_reason = "max_structured_output_retries"
                    break
                # Re-prompt the LLM to fix its output (no tools).
                schema_hint = json.dumps(output_schema)
                correction = (
                    f"Your previous response was not valid JSON or did not match "
                    f"the required schema ({exc}). "
                    f"Return ONLY a valid JSON object matching: {schema_hint}"
                )
                messages.append({"role": "user", "content": correction})
                from tools.llm.provider import LLMRequest  # already imported above
                req = LLMRequest(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=[],  # no tools during structured output retry
                    max_tokens=max_tokens,
                    temperature=temperature,
                    effort=effort,
                )
                try:
                    resp = router.invoke(llm_function, req)
                    result.turns += 1
                    result.final_content = getattr(resp, "content", "") or ""
                    result.total_input_tokens += getattr(resp, "input_tokens", 0) or 0
                    result.total_output_tokens += getattr(resp, "output_tokens", 0) or 0
                    result.total_cost_usd += getattr(resp, "cost_usd", 0.0) or 0.0
                    messages.append({"role": "assistant", "content": result.final_content})
                except Exception as invoke_exc:
                    logger.warning("agent_loop: structured output retry invoke failed: %s", invoke_exc)
                    result.done = False
                    result.truncated = True
                    result.result_subtype = ResultSubtype.error_during_execution
                    result.truncation_reason = "error_during_execution"
                    break

    result.messages = messages

    # Fire on_stop hook regardless of exit reason.
    if on_stop is not None:
        try:
            on_stop(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_loop: on_stop raised: %s", exc)

    return result
