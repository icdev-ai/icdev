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
import contextlib as _contextlib
import json
import math
import os
import threading
import time
import uuid as _uuid
from collections import deque as _deque
from dataclasses import dataclass, field
from typing import Any, Callable

from icdev.tools.logging.icdev_logger import get_logger

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


class AgentLoopCompactionError(RuntimeError):
    """Raised when history exceeded the context window and compaction failed.

    Only raised when compaction was LOAD-BEARING — the transcript is already
    over ``context_window_tokens`` and the compressor could not shrink it. The
    loop converts this into :attr:`ResultSubtype.error_context_compaction`
    rather than dispatching the oversized transcript to the provider and
    reporting the provider's rejection as a generic execution fault.
    """


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

    error_max_wall_clock = "error_max_wall_clock"
    """Total elapsed session time exceeded ``max_wall_clock_seconds``.

    Distinct from every other budget subtype on purpose. ``tool_timeout_seconds``
    and ``llm_call_timeout_seconds`` are PER-CALL ceilings: a session can stay
    under both of them, under the token budget and under the cost budget, and
    still run for hours — which is what a slow external dependency or a patient
    loop produces. Reporting that as ``error_max_budget_tokens`` (or, worse, as
    an external kill with no result at all) reads as "task too big" rather than
    "ran too long".
    """

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

    error_semantic_loop = "error_semantic_loop"
    """Recent tool calls were semantically equivalent — the agent was looping.

    Distinct from :attr:`error_max_budget_tokens` on purpose: an undetected loop
    burns the token budget to its ceiling and then reports budget exhaustion,
    which reads as "task too big" rather than "agent was stuck". See
    :mod:`icdev.tools.llm.loop_detector`.
    """

    error_context_compaction = "error_context_compaction"
    """History outgrew the context window and the compressor failed to shrink it.

    Distinct from :attr:`error_during_execution` on purpose. Compaction used to
    be swallowed with a WARNING and the UNCHANGED (oversized) transcript was
    handed to the provider, which rejected it — so a compressor bug, a missing
    ``context_compressor`` dependency and a network fault all surfaced as the
    same generic execution error and none of them was diagnosable from a
    transcript. See :class:`AgentLoopCompactionError`.
    """

    error_budget_blocked = "error_budget_blocked"
    """A pre-invoke budget gate REFUSED the call — the loop never reached a model.

    Raised by the router's per-agent (``tools.agent.token_tracker``) and
    per-module (``tools.budget.module_budget_tracker``) hard-stops. Distinct
    from :attr:`error_max_budget_tokens` / :attr:`error_max_budget_cost`, which
    are this loop's OWN per-run ceilings: those mean "this run spent its
    allowance", while this one means "the account/module allowance was already
    gone before this run started". Also distinct from
    :attr:`error_during_execution` — a governed refusal is not a crash.
    """


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
            ``max_total_tokens``, ``max_cost_usd``, ``max_wall_clock_seconds``,
            ``context_compaction_failed``, ``budget_blocked``, or ``stop_event``.
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
    elapsed_seconds: float = 0.0
    """Wall-clock seconds from loop start to termination (all exit paths)."""
    compression_events: list[dict[str, Any]] = field(default_factory=list)
    truncation_reason: str = ""
    model_id: str = ""
    """model_id from the first successful LLM response — used by cross-grader enforcement."""
    provider: str = ""
    """provider name from the first successful LLM response."""
    parent_session_id: str = ""
    """Parent session UUID when this loop was spawned by another agent loop (distributed tracing)."""
    tenant_id: str = ""
    """Tenant ID for cost attribution (set via run_agent_loop parameter)."""
    user_id: str = ""
    """User ID for cost attribution (set via run_agent_loop parameter)."""
    trace_id: str = ""
    """Correlation ID threaded through memory writes, evals, and OTel spans (migration 229)."""
    output_redacted: bool = False
    """True when output_redactor replaced PII/credentials in final_content (IL4/IL5)."""
    loop_detection: dict[str, Any] = field(default_factory=dict)
    """Details of the semantic-loop detection that ended the run (empty if none).

    Shape: :meth:`icdev.tools.llm.loop_detector.LoopDetection.as_dict` — tool
    name, cluster size, window size, distinct turns, mean similarity, reason.
    """


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


def _serialize_payload(payload: Any) -> str:
    """Render a tool payload the way it goes on the wire, for token accounting."""
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — accounting must never raise
        return str(payload)


def _estimate_block_tokens(block: dict[str, Any], _depth: int = 0) -> int:
    """Estimate tokens in one content block, INCLUDING its tool traffic.

    Counting only ``block["text"]`` reports ZERO for the two block types that
    carry the bulk of an agentic conversation:

      * ``tool_use`` — the arguments the model produced live under ``input``.
      * ``tool_result`` — the tool's output is nested under ``content``, either
        as a list of blocks or as a plain string.

    In a tool-heavy run (i.e. every real build) that made the dominant content
    invisible to the compaction trigger, so history grew past the real window
    while the estimate stayed near zero.
    """
    total = _estimate_text_tokens(str(block.get("text") or ""))
    tool_input = block.get("input")
    if tool_input:
        total += _estimate_text_tokens(_serialize_payload(tool_input))
    inner = block.get("content")
    if isinstance(inner, list):
        if _depth < 4:  # nesting is one level in practice; bound it anyway
            total += sum(
                _estimate_block_tokens(b, _depth + 1) for b in inner if isinstance(b, dict)
            )
    elif inner:
        total += _estimate_text_tokens(_serialize_payload(inner))
    return total


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Estimate tokens in a single message, counting text AND tool blocks."""
    content = msg.get("content", "")
    if isinstance(content, list):
        return sum(
            _estimate_block_tokens(block) for block in content if isinstance(block, dict)
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
        if "max_wall_clock_seconds" in budgets:
            defaults["max_wall_clock_seconds"] = float(budgets["max_wall_clock_seconds"])
        if "stall_threshold" in budgets:
            defaults["stall_threshold"] = int(budgets["stall_threshold"])
        mem_cfg = raw.get("agent_loop", {}).get("memory", {})
        if "enabled" in mem_cfg:
            defaults["memory_enabled"] = bool(mem_cfg["enabled"])
        if "top_k" in mem_cfg:
            defaults["memory_top_k"] = int(mem_cfg["top_k"])
        if "tier" in mem_cfg:
            defaults["memory_tier"] = str(mem_cfg["tier"])
        rubric_cfg = raw.get("agent_loop", {}).get("rubric", {})
        if "max_grading_iterations" in rubric_cfg:
            defaults["max_grading_iterations"] = int(rubric_cfg["max_grading_iterations"])
        if "grader_max_tokens" in rubric_cfg:
            defaults["grader_max_tokens"] = int(rubric_cfg["grader_max_tokens"])
    except Exception as exc:
        logger.debug("agent_loop: failed to load budget defaults: %s", exc)
    return defaults


def _resolve_context_window_tokens(llm_function: str, configured: int | None) -> int | None:
    """Compaction threshold for *llm_function* — the routed chain's real window.

    ``args/llm_config.yaml`` declares a single static
    ``agent_loop.budgets.context_window_tokens`` for every function and every
    model. :mod:`icdev.tools.llm.context_budget` already knows the real,
    per-model numbers, and ``floor_window_for_function`` takes the MINIMUM
    across the chain that could serve the function — the only bound that stays
    correct when a run falls back mid-flight from a large-window model to a
    small one. That minimum is used verbatim; there is deliberately no branch on
    model family, because the chain minimum is the whole point.

    *configured* is kept as the fallback for when the chain declares no window
    at all (an unrouted function, an unreadable config, or a chain whose models
    carry no ``context_window``), so this can only sharpen the threshold, never
    remove it.
    """
    try:
        from icdev.tools.llm import context_budget

        chain = context_budget.chain_for_function(llm_function)
        windows = context_budget.model_windows()
        if not any(windows.get(model) for model in chain):
            return configured
        return int(context_budget.floor_window_for_function(llm_function))
    except Exception as exc:  # noqa: BLE001 — degrade to the configured value
        logger.debug("agent_loop: context-window resolution failed: %s", exc)
        return configured


def _wall_clock_deadline(max_wall_clock_seconds: float | None) -> float | None:
    """Absolute ``time.monotonic()`` deadline for a session, or ``None``.

    A budget of ``None`` or ``<= 0`` disables the ceiling. Callers that carve a
    shared budget into per-round slices (:func:`run_agent_loop_with_rubric`,
    :func:`run_staged_agent_loop`) must check for an exhausted budget
    themselves rather than passing ``<= 0`` down — otherwise "no time left"
    would silently mean "run forever".
    """
    if not max_wall_clock_seconds or max_wall_clock_seconds <= 0:
        return None
    return time.monotonic() + float(max_wall_clock_seconds)


def _stop_for_wall_clock(
    result: "AgentLoopResult",
    messages: list[dict[str, Any]],
    *,
    budget: float,
    elapsed: float,
) -> None:
    """Mark *result* as ended by the session wall-clock ceiling."""
    result.truncated = True
    result.result_subtype = ResultSubtype.error_max_wall_clock
    result.truncation_reason = "max_wall_clock_seconds"
    logger.warning(
        "agent_loop: wall-clock budget exhausted — elapsed=%.0fs > max_wall_clock_seconds=%.0fs",
        elapsed,
        budget,
    )
    messages.append(
        {
            "role": "system",
            "content": (
                f"Agent loop stopped: exceeded max_wall_clock_seconds={budget:.0f} "
                f"(elapsed={elapsed:.0f}s)."
            ),
        }
    )


# Exception class names that mean "a budget gate refused this call", not "the
# call crashed". Matched by NAME across the whole MRO rather than by isinstance
# because ``tools.budget.module_budget_tracker`` and
# ``icdev.tools.budget.module_budget_tracker`` are physically separate modules in
# a checkout, so their error classes are distinct objects — an isinstance check
# against one of them silently misses the other. Name-matching also keeps this
# module from importing the trackers (and their DB layer) just to classify.
_BUDGET_BLOCK_EXCEPTIONS = frozenset(
    {
        "BudgetExceededError",        # tools.agent.token_tracker (per-agent)
        "ModuleBudgetExceededError",  # tools.budget.module_budget_tracker (per-module)
    }
)


def _is_budget_block(exc: BaseException) -> bool:
    """True when *exc* is a pre-invoke budget refusal rather than an execution fault."""
    return any(klass.__name__ in _BUDGET_BLOCK_EXCEPTIONS for klass in type(exc).__mro__)


def _stop_for_hard_budget(
    result: "AgentLoopResult",
    messages: list[dict[str, Any]],
    *,
    max_total_tokens: int | None,
    max_cost_usd: float | None,
) -> bool:
    """Mark *result* stopped if a cumulative token/cost ceiling is already blown.

    Returns True when a ceiling fired (caller must ``break``), False otherwise.

    Evaluated at TWO points per turn — before dispatching the turn's tools and
    again after the tool sweep. Only the post-sweep check existed, which meant a
    turn whose LLM response already blew the ceiling still ran every tool it
    asked for, so the overshoot was one whole turn plus all of its tool output
    rather than the one LLM call that crossed the line.
    """
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
        return True

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
        return True

    return False


def _retrieve_memory_context(user_prompt: str, top_k: int, tier: str) -> str:
    """Retrieve relevant memory and format as a context block for the system prompt.

    Called once before the first LLM turn. Returns empty string on any error
    so the loop degrades gracefully when the memory system is unavailable.
    """
    if not user_prompt or top_k <= 0:
        return ""
    try:
        from icdev.tools.memory.hybrid_search import search as _mem_search
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
    blocks are preserved.

    Raises:
        AgentLoopCompactionError: If compaction was needed (history is already
            over ``context_window_tokens``) and the compressor raised. This used
            to be a WARNING that returned the messages UNCHANGED, so the loop
            went on to hand an oversized transcript to the provider and reported
            the provider's rejection as ``error_during_execution`` — the actual
            cause was invisible in the transcript. The failure is recorded in
            ``compression_events`` before raising so the event list still shows
            the attempt.
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
        logger.error(
            "agent_loop: context compaction failed at %d tokens (window=%d): %s",
            current,
            context_window_tokens,
            exc,
        )
        compression_events.append(
            {
                "method": "failed",
                "original_tokens": current,
                "compressed_tokens": current,
                "compression_ratio": 1.0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        raise AgentLoopCompactionError(
            f"Context compaction failed at {current} estimated tokens "
            f"(context_window_tokens={context_window_tokens}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


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


def _resolve_approval_gate(
    approval_gate: "PreToolUseHook | bool | None",
) -> "PreToolUseHook | None":
    """Turn the ``approval_gate`` argument into a hook, or ``None`` (ars-appr-01).

    ``True`` / env-enabled builds the default reversibility gate; a callable is
    taken as-is; ``False`` disables it. See :func:`run_agent_loop` for why the
    default is env-resolved rather than always-on.
    """
    if approval_gate is False:
        return None
    if callable(approval_gate):
        return approval_gate
    if approval_gate is None:
        mode = (os.environ.get("ICDEV_AGENT_APPROVAL_MODE") or "").strip().lower()
        if not mode or mode == "off":
            return None
    try:
        from tools.agent_runtime.approval_gate import build_approval_hook

        return build_approval_hook()
    except Exception as exc:  # noqa: BLE001
        # The gate was asked for and could not be built. Refusing every tool call
        # is the fail-closed answer: an operator who set ICDEV_AGENT_APPROVAL_MODE
        # did not ask for "run unsupervised if the gate is broken".
        logger.error("agent_loop: approval gate unavailable, denying all tools: %s", exc)
        # Bind the message now: Python unbinds `exc` at the end of the except
        # block, so a closure over it raises NameError when the hook fires.
        why = f"{type(exc).__name__}: {exc}"

        def _deny(name: str, _input: dict[str, Any]) -> str:
            return (
                f"BLOCKED: the approval gate was requested but could not be loaded "
                f"({why}), so {name} cannot be authorised. Fix the gate or unset "
                "ICDEV_AGENT_APPROVAL_MODE."
            )

        return _deny


def _compose_pre_tool_hooks(
    *hooks: "PreToolUseHook | None",
) -> "PreToolUseHook | None":
    """Chain pre-tool hooks in order; the first non-empty block message wins."""
    active = [h for h in hooks if h is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _chained(name: str, tool_input: dict[str, Any]) -> "str | None":
        for hook in active:
            msg = hook(name, tool_input)
            if msg:
                return msg
        return None

    return _chained


# ---------------------------------------------------------------------------
# Continuous Harness feed — record a codegen decision per loop run
# ---------------------------------------------------------------------------


def _codegen_confidence(
    *, result: "AgentLoopResult", rubric_verdict: str | None = None
) -> float:
    """Confidence that this codegen run's task ultimately *resolves* (0.0–1.0).

    Used as the calibratable probability fed to ``harness_eval`` so ECE can be
    computed against the eventual ``actual_outcome``. When a rubric verdict is
    available it dominates (``satisfied`` → high); otherwise a cleanly completed
    loop gets a moderate-high prior and a truncated/errored one a low prior.
    """
    if rubric_verdict is not None:
        return {
            RubricVerdict.satisfied: 0.9,            # satisfied -> high
            RubricVerdict.needs_revision: 0.5,
            RubricVerdict.max_iterations_reached: 0.4,
            RubricVerdict.failed: 0.2,
            RubricVerdict.grader_error: 0.3,
        }.get(rubric_verdict, 0.5)
    return 0.7 if result.done else 0.3


def _record_codegen_decision(
    *,
    harness_task_id: str | None,
    session_id: str,
    llm_function: str,
    result: "AgentLoopResult",
    rubric_verdict: str | None = None,
    grading_attempts: int | None = None,
) -> None:
    """Record ONE ``harness_eval`` decision row for a code-generation run.

    Fires at loop completion so the kanban reflex's ``record_outcome()`` (called
    on task status transitions) finally attaches a real outcome to a real
    decision row — previously codegen runs recorded nothing, so outcome updates
    silently no-oped and the precision/ECE gates computed over an empty set.

    ``task_id`` is the caller-supplied ``harness_task_id`` (a kanban task id when
    dispatched by the runner) or, failing that, the loop's ``session_id`` so a
    row always lands. ``reflex`` is always ``"codegen"``. Fully non-fatal and
    lazy-imported: a missing table / DB never breaks the agent loop.
    """
    task_id = harness_task_id or session_id
    if not task_id:
        return
    try:
        from tools.genesis.harness.eval_harness import record_decision
    except Exception as exc:  # noqa: BLE001 — recording must never break the loop
        logger.warning("agent_loop: could not import record_decision: %s", exc)
        return

    # Predicted outcome: the final rubric verdict when graded, else "done" for a
    # cleanly completed loop or the failure subtype (error_max_turns, …).
    if rubric_verdict is not None:
        decision = rubric_verdict
    else:
        decision = "done" if result.done else (result.result_subtype or "incomplete")

    metadata: dict[str, Any] = {
        "llm_function": llm_function,
        "session_id": session_id,
        "result_subtype": result.result_subtype,
        # Carried into harness_eval so "agent was stuck" stays separable from
        # "task exhausted its budget" once the run is only a telemetry row.
        "truncation_reason": result.truncation_reason,
        "done": result.done,
        "truncated": result.truncated,
        "turns": result.turns,
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_cost_usd": result.total_cost_usd,
        # Wall-clock duration, so a run that stayed under every token/cost cap
        # but burned hours is separable in the harness record (ars-wall-01).
        "elapsed_seconds": round(result.elapsed_seconds, 3),
    }
    if result.loop_detection:
        metadata["loop_detection"] = result.loop_detection
    if rubric_verdict is not None:
        metadata["rubric_verdict"] = rubric_verdict
    if grading_attempts is not None:
        metadata["grading_attempts"] = grading_attempts

    try:
        record_decision(
            task_id=task_id,
            reflex="codegen",
            decision=decision,
            confidence=_codegen_confidence(result=result, rubric_verdict=rubric_verdict),
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 — recording must never break the loop
        logger.warning("agent_loop: record_decision(codegen) failed: %s", exc)


# ---------------------------------------------------------------------------
# Run observability (hgx-obs-01)
# ---------------------------------------------------------------------------
class _NoTurnTrace:
    """Stand-in used when the observability package cannot be imported.

    Same surface as ``agent_trace.TurnTracer``, every method a no-op. The loop
    therefore never branches on whether tracing is available — a checkout
    without ``tools/observability`` runs the identical code path.
    """

    trace_id = ""

    def begin(self, turn: int) -> None:
        pass

    def annotate(self, **attributes: Any) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def finish(self) -> None:
        pass


def _open_run_observability(trace_id: str, session_id: str) -> "tuple[Any, Any]":
    """``(turn_tracer, correlation_scope)`` for one loop run.

    Degrades to a no-op tracer and a null context manager rather than failing:
    an agent run must not depend on its own telemetry being importable.
    """
    try:
        from tools.observability.agent_trace import TurnTracer, correlation_scope

        return TurnTracer(trace_id, session_id=session_id), correlation_scope(trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_loop: run observability unavailable: %s", exc)
        return _NoTurnTrace(), _contextlib.nullcontext()


def _submit_traced(executor: Any, fn: Any, *args: Any) -> Any:
    """``executor.submit`` that carries the caller's context into the worker.

    A pool worker starts with an EMPTY context, so the run correlation id and
    the active span are both invisible inside it — which would silently detach
    every parallel tool call and every timed LLM call from the run that issued
    them. Falls back to a plain submit if propagation is unavailable.
    """
    try:
        from tools.observability.agent_trace import submit_with_context

        return submit_with_context(executor, fn, *args)
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_loop: context propagation unavailable: %s", exc)
        return executor.submit(fn, *args)


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
    approval_gate: PreToolUseHook | bool | None = None,
    max_total_tokens: int | None = None,
    max_cost_usd: float | None = None,
    max_wall_clock_seconds: float | None = None,
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
    loop_detection: dict[str, Any] | None = None,
    memory_enabled: bool | None = None,
    memory_top_k: int | None = None,
    memory_tier: str | None = None,
    parent_session_id: str = "",
    tenant_id: str = "",
    user_id: str = "",
    sanitize_tool_results: bool = True,
    initial_messages: list[dict[str, Any]] | None = None,
    harness_task_id: str | None = None,
    correlation_id: str = "",
    agent_id: str = "",
    _record_harness_decision: bool = True,
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
    hard budget cap exceeded (``max_total_tokens`` / ``max_cost_usd`` /
    ``max_wall_clock_seconds``).

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
        approval_gate: Reversibility gate for irreversible tool calls
                       (ars-appr-01). ``True`` builds the default gate from
                       :func:`tools.agent_runtime.approval_gate.build_approval_hook`;
                       a callable is used as-is (same contract as
                       ``on_pre_tool_use``); ``False`` disables it. The default
                       ``None`` resolves from ``ICDEV_AGENT_APPROVAL_MODE``: the
                       gate is built when that env var is set to anything other
                       than ``off``, and is otherwise absent.

                       Absent-by-default is a deliberate, stated choice, not an
                       oversight. Every existing caller passes its own tool
                       vocabulary, and the gate's policy is fail-closed
                       (unenumerated tool -> halt), so switching it on globally
                       would deny every call in every caller that has not yet
                       classified its tools. Turning it on is one env var; a
                       runtime that executes real irreversible operations —
                       ``tools/agent_runtime`` already gates its dispatch via
                       :func:`tools.agent_runtime.safety.build_safety_gate` —
                       must set it. When it runs, it composes *before*
                       ``on_pre_tool_use``: the gate's block wins.
        max_total_tokens: Hard cap on cumulative input+output tokens across turns.
        max_cost_usd:     Hard cap on cumulative USD cost (when providers report it).
        max_wall_clock_seconds: Hard cap on TOTAL elapsed session time, checked at
            each turn boundary. ``tool_timeout_seconds`` and
            ``llm_call_timeout_seconds`` are per-CALL ceilings — a session can
            stay under both, under the token budget and under the cost budget,
            and still run for hours. Exceeding this ends the run with
            ``ResultSubtype.error_max_wall_clock`` and
            ``truncation_reason="max_wall_clock_seconds"``, deliberately distinct
            from the token/cost reasons. ``None`` loads
            ``args/llm_config.yaml`` ``agent_loop.budgets.max_wall_clock_seconds``;
            ``0`` (or negative) disables the ceiling entirely. Elapsed time is
            always reported in :attr:`AgentLoopResult.elapsed_seconds`.
        context_window_tokens: Soft threshold; if message history exceeds this,
            it is compressed before the next LLM turn. ``None`` (the default)
            resolves the REAL window of the chain routed for ``llm_function``
            via
            :func:`icdev.tools.llm.context_budget.floor_window_for_function` —
            the minimum across that chain, so a mid-flight fallback to a
            smaller-window model cannot overflow. ``args/llm_config.yaml``
            ``agent_loop.budgets.context_window_tokens`` is the fallback for a
            chain that declares no window.
        compression_budget_tokens: Target token budget used when compression is
            triggered (defaults to 75% of ``context_window_tokens``). A
            config-supplied value is clamped to that 75% when the resolved
            window is smaller than the configured one.
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
        loop_detection: Overrides for the semantic-loop detector (see
            :mod:`icdev.tools.llm.loop_detector`). ``None`` reads
            ``args/llm_config.yaml`` ``agent_loop.loop_detection``. Pass
            ``{"enabled": False}`` to disable, or tune ``window`` /
            ``similarity_threshold`` / ``min_cluster_size`` /
            ``min_distinct_turns`` / ``coverage_ratio`` per call. When a loop is
            detected the run ends with ``ResultSubtype.error_semantic_loop`` and
            ``truncation_reason="semantic_loop"`` — deliberately distinct from
            the budget-exhaustion reasons, since an undetected loop otherwise
            just spends the token budget and reports ``max_total_tokens``.
        initial_messages: Pre-built message history to start from, taking
            precedence over both ``resume_session_id`` and ``user_prompt``-based
            seeding. Used by :func:`run_agent_loop_with_rubric` to resume a
            transcript with grader feedback appended; also usable directly by
            callers that maintain their own message history.
        harness_task_id: Optional Continuous-Harness task id to key the recorded
            ``harness_eval`` decision on. When the runner dispatches a kanban
            task, pass its task id so the kanban reflex's later ``record_outcome``
            attaches to this decision. When ``None`` the loop's own
            ``session_id`` is used so a decision row still lands (see
            ``_record_codegen_decision``).
        correlation_id: Run-level id threading this loop through its telemetry —
            it becomes :attr:`AgentLoopResult.trace_id`, the ``agent.turn`` span
            per turn, the ``icdev.correlation_id`` attribute on every
            ``gen_ai.invoke`` span the router emits beneath it, and the
            ``correlation_id`` column of every tool call recorded to
            ``runtime_invocations``. Pass a caller-owned id (a task id, a request
            id) to join an agent run to whatever dispatched it; the default mints
            a fresh uuid4 per run, which is what the loop always did.
        agent_id: Budget/attribution identity carried on every ``LLMRequest``
            this loop issues. The router's per-agent hard-stop
            (``tools.agent.token_tracker.check_budget``, keyed off
            ``args/llm_config.yaml`` ``token_budgets.per_agent``) is guarded by
            ``if request.agent_id:`` — the loop never set the field, so that
            gate was unreachable for every agent-loop call regardless of how the
            budget was configured. Pass the caller's real agent identity (e.g.
            ``"builder-agent"``) to place a run under that agent's cap. The
            default derives ``agent-loop:<llm_function>`` so requests always
            carry an id and the gate is live; that synthetic id has no
            ``per_agent`` entry, so it inherits ``default_monthly_usd`` and
            evaluates to ``allow`` until spend is recorded against it.
        _record_harness_decision: Private flag. When ``True`` (default), a single
            ``reflex="codegen"`` decision is recorded at loop completion.
            :func:`run_agent_loop_with_rubric` sets it ``False`` for its inner
            loops and records ONE decision itself, reflecting the final grade.

    Returns:
        :class:`AgentLoopResult`.

    Raises:
        AgentLoopUnsupported: resolved provider cannot do native tool use.
    """
    # Lazy import to avoid import cycles at module load.
    from icdev.tools.llm.provider import LLMRequest

    _check_tool_support(router, llm_function)

    # Budget/attribution identity for every request this loop issues. The
    # router's per-agent gate is behind ``if request.agent_id:``, so an empty
    # field means the gate never runs — resolve a stable fallback rather than
    # leaving it blank.
    effective_agent_id = agent_id or f"agent-loop:{llm_function}"

    # Resolve optional budget defaults from args/llm_config.yaml.
    budget_defaults = _load_budget_defaults()
    if max_total_tokens is None and "max_total_tokens" in budget_defaults:
        max_total_tokens = budget_defaults["max_total_tokens"]
    if max_cost_usd is None and "max_cost_usd" in budget_defaults:
        max_cost_usd = budget_defaults["max_cost_usd"]
    if max_wall_clock_seconds is None and "max_wall_clock_seconds" in budget_defaults:
        max_wall_clock_seconds = budget_defaults["max_wall_clock_seconds"]
    # Context window: an explicit caller value always wins. Otherwise resolve the
    # REAL window of the chain routed for llm_function, falling back to the
    # static config value when that chain declares none.
    _caller_context_window = context_window_tokens
    if _caller_context_window is None:
        context_window_tokens = _resolve_context_window_tokens(
            llm_function, budget_defaults.get("context_window_tokens")
        )
    _caller_compression_budget = compression_budget_tokens
    if compression_budget_tokens is None and "compression_budget_tokens" in budget_defaults:
        compression_budget_tokens = budget_defaults["compression_budget_tokens"]
    if compression_budget_tokens is None and context_window_tokens is not None:
        compression_budget_tokens = int(context_window_tokens * 0.75)
    # A config-supplied compression budget can exceed a resolved window that is
    # smaller than the configured one; compressing to a target above the window
    # would leave history over the threshold and re-fire every turn.
    if (
        _caller_compression_budget is None
        and compression_budget_tokens is not None
        and context_window_tokens is not None
        and compression_budget_tokens > context_window_tokens * 0.75
    ):
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
    # Semantic loop detector — config file supplies the defaults, the caller may
    # override any key (including turning it off entirely).
    from icdev.tools.llm.loop_detector import (
        ToolCallRecord as _ToolCallRecord,
        detect_semantic_loop as _detect_semantic_loop,
        load_detector_config as _load_detector_config,
    )

    _loop_cfg = _load_detector_config()
    _loop_cfg.update(loop_detection or {})
    _loop_detection_enabled = bool(_loop_cfg.get("enabled", True))
    _loop_window = max(1, int(_loop_cfg.get("window", 8)))
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
    if memory_enabled and not resume_session_id and initial_messages is None:
        _mem_ctx = _retrieve_memory_context(user_prompt, memory_top_k, memory_tier)
        if _mem_ctx:
            system_prompt = system_prompt + "\n\n" + _mem_ctx

    # Build set of read-only tool names for parallel execution.
    _read_only_tools = _build_read_only_set(tools)

    # Compose the approval gate (ars-appr-01) in front of the caller's own hook.
    # The gate runs first so an irreversible call halts even when the caller's
    # hook would have waved it through.
    on_pre_tool_use = _compose_pre_tool_hooks(
        _resolve_approval_gate(approval_gate), on_pre_tool_use
    )

    # Assign a unique session ID so callers can persist and resume this loop.
    session_id = str(_uuid.uuid4())

    # Correlation ID: threads this loop run through memory writes, evals, and OTel spans.
    # A caller-supplied id wins so an agent run can be joined to whatever asked
    # for it; otherwise one is minted per run, as it always was.
    trace_id = correlation_id or str(_uuid.uuid4())

    # Resume: if a prior session ID is given, load its message history instead
    # of starting fresh. Falls back to a new conversation if the session is not
    # found or the DB is unavailable.
    messages: list[dict[str, Any]]
    _checkpoint_start_turn = 0
    if initial_messages is not None:
        messages = list(initial_messages)
    elif resume_session_id:
        # Try new session_store checkpoint first (richer: turn_number + cost tracking).
        _ckpt_data = None
        try:
            from icdev.tools.llm.session_store import load_checkpoint as _load_ckpt
            _ckpt_data = _load_ckpt(resume_session_id)
        except Exception as _ckpt_exc:
            logger.debug("agent_loop: session_store checkpoint load failed: %s", _ckpt_exc)
        if _ckpt_data and _ckpt_data.get("messages"):
            messages = list(_ckpt_data["messages"])
            _checkpoint_start_turn = _ckpt_data.get("turn_number", 0)
            logger.info(
                "agent_loop: resumed session %s from turn %d (%d messages) via session_store",
                resume_session_id, _checkpoint_start_turn, len(messages),
            )
        else:
            # Fallback to legacy agent_loop_session table.
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

    # Session wall-clock ceiling (ars-wall-01). Monotonic so a clock adjustment
    # mid-run cannot extend or collapse the budget.
    _loop_started = time.monotonic()
    _wall_deadline = _wall_clock_deadline(max_wall_clock_seconds)

    tool_call_log: list[dict[str, Any]] = []
    result = AgentLoopResult(messages=messages, session_id=session_id)
    result.parent_session_id = parent_session_id
    result.tenant_id = tenant_id
    result.user_id = user_id
    result.trace_id = trace_id

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
    # Control 6: rolling window of executed calls for semantic-loop detection.
    _recent_calls: _deque[Any] = _deque(maxlen=_loop_window)

    # Observability (hgx-obs-01). `_turn_trace` opens one span per turn and
    # `_correlation` publishes the run id to everything the turn calls — most
    # importantly `tools/agent_runtime/dispatch.py`, which records each tool call
    # to `runtime_invocations` and reads the id from the ambient context rather
    # than being handed it through the fixed `handler(input, stop)` contract.
    _turn_trace, _correlation = _open_run_observability(trace_id, session_id)
    with _correlation, _futures.ThreadPoolExecutor(max_workers=16) as executor:
        for turn in range(max_iterations):
            # Opening turn N closes turn N-1's span. The turn body has a dozen
            # `break` paths; `finish()` after the loop catches whichever one ran.
            _turn_trace.begin(turn)
            if stop_event is not None and stop_event.is_set():
                break

            # Control 7: Session wall-clock ceiling — checked BEFORE the LLM
            # call so a run that is already over budget does not start another
            # turn it cannot afford. Checked again after the turn's tools so a
            # single long turn is caught on the same turn it blew the budget.
            if _wall_deadline is not None and time.monotonic() >= _wall_deadline:
                _stop_for_wall_clock(
                    result,
                    messages,
                    budget=max_wall_clock_seconds,
                    elapsed=time.monotonic() - _loop_started,
                )
                break

            # Control 5: Stall detector — abort if no novel successful call for N turns.
            if _last_progress_turn >= 0 and (turn - _last_progress_turn) >= stall_threshold:
                logger.warning(
                    "agent_loop: stall detected — no novel successful tool call for %d turns"
                    " (last_progress_turn=%d, current_turn=%d)",
                    turn - _last_progress_turn, _last_progress_turn, turn,
                )
                result.result_subtype = ResultSubtype.error_stalled
                result.truncation_reason = "stalled"
                result.truncated = True
                break

            # Control 6: Semantic loop detector — abort when recent tool calls are
            # equivalent-but-not-identical (control 3 only sees exact duplicates,
            # and the stall detector only sees *no* progress). Runs before the LLM
            # call so a loop stops well short of the token ceiling.
            if _loop_detection_enabled and len(_recent_calls) >= 2:
                _detection = _detect_semantic_loop(_recent_calls, config=_loop_cfg)
                if _detection.detected:
                    logger.warning("agent_loop: semantic loop detected — %s", _detection.reason)
                    result.result_subtype = ResultSubtype.error_semantic_loop
                    result.truncation_reason = "semantic_loop"
                    result.loop_detection = _detection.as_dict()
                    result.truncated = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Agent loop stopped: semantic loop detected — "
                                f"{_detection.reason}."
                            ),
                        }
                    )
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
            try:
                messages = _maybe_compress_messages(
                    messages,
                    context_window_tokens=context_window_tokens,
                    compression_budget_tokens=compression_budget_tokens,
                    compression_events=result.compression_events,
                )
            except AgentLoopCompactionError as exc:
                # The transcript is over the window and could not be shrunk.
                # Calling the provider anyway guarantees a rejection that reads
                # as a generic execution error, so stop here with the real cause.
                result.truncated = True
                result.result_subtype = ResultSubtype.error_context_compaction
                result.truncation_reason = "context_compaction_failed"
                logger.error("agent_loop: turn %d aborted — %s", turn, exc)
                messages.append(
                    {
                        "role": "system",
                        "content": f"Agent loop stopped: context compaction failed — {exc}",
                    }
                )
                result.messages = messages
                _turn_trace.error(f"context compaction failed: {exc}")
                break
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
                correlation_id=trace_id,
                agent_id=effective_agent_id,
            )
            try:
                # Control 1: LLM call timeout — wraps the blocking invoke call.
                if llm_call_timeout_seconds:
                    _llm_fut = _submit_traced(executor, router.invoke, llm_function, request)
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
                        _turn_trace.error(
                            f"llm call timed out after {llm_call_timeout_seconds}s"
                        )
                        break
                else:
                    response = router.invoke(llm_function, request)
            except Exception as exc:
                if _is_budget_block(exc):
                    # A budget gate refused the call before any model saw it.
                    # Reporting that as error_during_execution made a governed
                    # refusal indistinguishable from a crash or a network fault.
                    logger.warning(
                        "agent_loop: budget gate blocked the LLM call on turn %d: %s", turn, exc
                    )
                    result.truncated = True
                    result.result_subtype = ResultSubtype.error_budget_blocked
                    result.truncation_reason = "budget_blocked"
                    messages.append(
                        {
                            "role": "system",
                            "content": f"Agent loop stopped: budget gate blocked the LLM call — {exc}",
                        }
                    )
                    _turn_trace.error(f"budget blocked: {type(exc).__name__}: {exc}")
                    break
                logger.error("agent_loop: LLM invocation failed on turn %d: %s", turn, exc)
                result.truncated = True
                result.result_subtype = ResultSubtype.error_during_execution
                result.truncation_reason = "error_during_execution"
                _turn_trace.error(f"{type(exc).__name__}: {exc}")
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
            _turn_trace.annotate(**{
                "gen_ai.response.model": getattr(response, "model_id", "") or "",
                "gen_ai.system": getattr(response, "provider", "") or "",
                "gen_ai.usage.input_tokens": getattr(response, "input_tokens", 0) or 0,
                "gen_ai.usage.output_tokens": getattr(response, "output_tokens", 0) or 0,
                "gen_ai.response.finish_reason": result.stop_reason,
                "agent.tool_call_count": len(tool_calls),
            })
            if not tool_calls:
                messages.append(_assistant_message(response))  # final assistant text
                result.done = True
                result.result_subtype = ResultSubtype.success
                if on_turn is not None:
                    on_turn(turn, response, messages)
                break

            # Hard budget check BEFORE dispatching this turn's tools. The
            # response that just landed is already accounted for, so if it blew
            # a ceiling there is nothing to gain from running the tools it asked
            # for — and running them costs a full tool sweep plus its output on
            # the next turn's input. Checked before the assistant's tool_use
            # message is appended so the transcript does not end on a tool_use
            # with no matching tool_result, which providers reject on resume.
            if _stop_for_hard_budget(
                result,
                messages,
                max_total_tokens=max_total_tokens,
                max_cost_usd=max_cost_usd,
            ):
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
                ro_futures[i] = _submit_traced(executor, handler, tc_input, stop_event)

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
                        fut = _submit_traced(executor, handler, tc_input, stop_event)
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

                # Sanitize tool result for prompt injection before appending to history.
                if sanitize_tool_results and not is_error and out_text is not DONE and out_text != DONE:
                    try:
                        from icdev.tools.llm.tool_result_sanitizer import sanitize as _san, load_config as _san_cfg
                        _san_config = _san_cfg()
                        _san_result = _san(
                            tc_name,
                            out_text,
                            mode=_san_config.get("mode", "warn"),
                            max_chars=_san_config.get("max_result_chars", 32768),
                        )
                        if _san_result.flagged:
                            out_text = _san_result.sanitized_text
                    except Exception:
                        pass  # non-fatal

                # Control 6: feed the semantic-loop window. Errors are included —
                # re-running a failing command with cosmetic variation is the
                # canonical loop — but the DONE sentinel is not a real result.
                if _loop_detection_enabled and out_text is not DONE and out_text != DONE:
                    _recent_calls.append(
                        _ToolCallRecord(
                            turn=turn,
                            name=tc_name,
                            arguments=tc_input,
                            result=out_text,
                            is_error=is_error,
                        )
                    )

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

            # Checkpoint at end of each turn for session resumption.
            try:
                from icdev.tools.llm.session_store import save_checkpoint as _save_ckpt
                _save_ckpt(
                    session_id,
                    turn,
                    messages,
                    parent_session_id=parent_session_id,
                    model_id=result.model_id,
                    provider=result.provider,
                    input_tokens=result.total_input_tokens,
                    output_tokens=result.total_output_tokens,
                    cost_usd=result.total_cost_usd,
                )
            except Exception:
                pass  # non-fatal

            # Hard budget check after executing all tools this turn. Kept
            # alongside the pre-dispatch check: a tool handler may itself run an
            # agent loop or otherwise move the counters, and the checkpoint
            # above must be written before the run stops.
            if _stop_for_hard_budget(
                result,
                messages,
                max_total_tokens=max_total_tokens,
                max_cost_usd=max_cost_usd,
            ):
                break

            if _wall_deadline is not None and time.monotonic() >= _wall_deadline:
                _stop_for_wall_clock(
                    result,
                    messages,
                    budget=max_wall_clock_seconds,
                    elapsed=time.monotonic() - _loop_started,
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

    # Closes whichever turn span was open when the loop left — including every
    # `break` path and the `for/else` max-iterations exhaustion.
    _turn_trace.finish()

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
                from icdev.tools.llm.provider import LLMRequest  # already imported above
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

    # Output redaction — IL4/IL5 compliance: scrub PII/credentials from final content
    # before it is returned to the caller or persisted to ace_sessions / agent_evals.
    if result.final_content:
        try:
            from icdev.tools.llm.output_redactor import redact as _rd, load_config as _rd_cfg
            _rd_conf = _rd_cfg()
            if _rd_conf.get("enabled", True):
                _rd_result = _rd(
                    result.final_content,
                    mode=_rd_conf.get("mode", "redact"),
                    placeholder=_rd_conf.get("placeholder", "[REDACTED]"),
                )
                if _rd_result.flagged:
                    result.final_content = _rd_result.redacted_text
                    result.output_redacted = True
        except Exception:
            pass  # non-fatal

    # Delete checkpoint on clean completion — avoids stale checkpoints accumulating.
    if result.done and not result.truncated:
        try:
            from icdev.tools.llm.session_store import delete_checkpoint as _del_ckpt
            _del_ckpt(session_id)
        except Exception:
            pass  # non-fatal

    # Total session duration, measured on ALL exit paths (including the
    # post-loop structured-output retries, which are part of the session).
    result.elapsed_seconds = time.monotonic() - _loop_started

    # Continuous Harness feed — record a codegen decision on ALL exit paths
    # (success, truncated, budget, stall, error) so kanban outcomes can attach.
    # Suppressed for run_agent_loop_with_rubric's inner loops (it records once
    # itself, reflecting the final grade).
    if _record_harness_decision:
        _record_codegen_decision(
            harness_task_id=harness_task_id,
            session_id=session_id,
            llm_function=llm_function,
            result=result,
        )

    # Fire on_stop hook regardless of exit reason.
    if on_stop is not None:
        try:
            on_stop(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_loop: on_stop raised: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Loopy adaptation — staged feedback-driven agent loops
# ---------------------------------------------------------------------------


@dataclass
class LoopStage:
    """One named stage in a :func:`run_staged_agent_loop` pipeline.

    Inspired by the Loopy workflow platform: each stage has an explicit name,
    optional prompt suffix, acceptance criteria, and before/after examples so
    the LLM knows what "done" looks like before it starts.
    """

    name: str
    """Human-readable stage name, e.g. ``"PLAN"``, ``"EXECUTE"``, ``"REFLECT"``."""

    prompt_suffix: str = ""
    """Extra instructions appended to the base system prompt for this stage only."""

    acceptance_criteria: str = ""
    """What a successful stage output looks like. Injected at the end of the user prompt."""

    before_example: str = ""
    """Optional example of the input for this stage (shown in system prompt)."""

    after_example: str = ""
    """Optional example of the expected output for this stage (shown in system prompt)."""

    max_iterations: int | None = None
    """Per-stage turn cap; falls back to the ``run_staged_agent_loop`` default."""


@dataclass
class StagedLoopResult:
    """Aggregated result of a :func:`run_staged_agent_loop` run."""

    done: bool = False
    """True only when every stage completed successfully."""

    stages_completed: list[str] = field(default_factory=list)
    """Stage names that finished with ``AgentLoopResult.done=True``."""

    stages_failed: list[str] = field(default_factory=list)
    """Stage names that did not complete (first failure stops the pipeline)."""

    stage_results: dict[str, AgentLoopResult] = field(default_factory=dict)
    """Per-stage :class:`AgentLoopResult`, keyed by :attr:`LoopStage.name`."""

    final_content: str = ""
    """``final_content`` of the last stage that ran."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    session_id: str = ""
    """UUID generated at pipeline start (does not equal any single stage session)."""


def run_staged_agent_loop(
    router: Any,
    *,
    stages: list[LoopStage],
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
    on_stage_complete: Callable[[str, AgentLoopResult], None] | None = None,
    **kwargs: Any,
) -> StagedLoopResult:
    """Run a series of named, feedback-driven stages through the agent loop.

    Each :class:`LoopStage` gets its own system-prompt context (name, suffix,
    before/after examples, acceptance criteria) and its own :func:`run_agent_loop`
    invocation.  Output from each stage is forwarded as context to the next.

    The pipeline stops at the first stage that does not complete (``done=False``).

    Args:
        router:        An ``LLMRouter`` instance.
        stages:        Ordered list of :class:`LoopStage` objects.
        system_prompt: Base system instruction shared by all stages.
        user_prompt:   The initial task description (used verbatim for stage 1).
        tools:         Tool schema list (shared across all stages).
        tool_handlers: ``{tool_name: handler}`` map (shared across all stages).
        llm_function:  Router routing key.
        max_iterations: Default per-stage turn cap (overridable per stage).
        max_tokens:    Per-turn output tokens.
        temperature:   Sampling temperature.
        effort:        Router effort tier.
        stop_event:    When set, propagated to the current stage to abort it.
        on_stage_complete: Optional ``callback(stage_name, AgentLoopResult)``
            fired after each stage completes (pass or fail).
        **kwargs:      Forwarded to each :func:`run_agent_loop` call (e.g.
            ``max_total_tokens``, ``on_pre_tool_use``, ``output_schema``).
            ``max_wall_clock_seconds`` is the exception: it is a budget for the
            WHOLE pipeline, so each stage receives only the time remaining.

    Returns:
        :class:`StagedLoopResult`.
    """
    staged = StagedLoopResult(session_id=str(_uuid.uuid4()))
    prev_output: str = ""

    # Session wall-clock ceiling (ars-wall-01) is a PIPELINE budget, not a
    # per-stage one — forwarding it verbatim through **kwargs would multiply it
    # by len(stages). Resolve one deadline here and hand each stage only the
    # time actually left.
    _wall_budget = kwargs.pop("max_wall_clock_seconds", None)
    if _wall_budget is None:
        _wall_budget = _load_budget_defaults().get("max_wall_clock_seconds")
    _wall_deadline = _wall_clock_deadline(_wall_budget)

    for idx, stage in enumerate(stages):
        if stop_event is not None and stop_event.is_set():
            break

        if _wall_deadline is not None:
            _remaining = _wall_deadline - time.monotonic()
            if _remaining <= 0:
                logger.warning(
                    "run_staged_agent_loop: wall-clock budget of %.0fs exhausted before stage %s",
                    _wall_budget, stage.name,
                )
                staged.stages_failed.append(stage.name)
                break
            kwargs["max_wall_clock_seconds"] = _remaining

        # Build stage-specific system prompt.
        stage_system = system_prompt
        if stage.name:
            stage_system += f"\n\n## Stage: {stage.name}"
        if stage.prompt_suffix:
            stage_system += f"\n{stage.prompt_suffix}"
        if stage.before_example:
            stage_system += f"\n\n### Example input\n{stage.before_example}"
        if stage.after_example:
            stage_system += f"\n\n### Example output\n{stage.after_example}"

        # Build stage-specific user prompt.
        if prev_output and idx > 0:
            stage_user = (
                f"Previous stage ({stages[idx - 1].name}) output:\n"
                f"{prev_output}\n\n---\n\n{user_prompt}"
            )
        else:
            stage_user = user_prompt
        if stage.acceptance_criteria:
            stage_user += f"\n\nAcceptance criteria for this stage:\n{stage.acceptance_criteria}"

        stage_result = run_agent_loop(
            router,
            system_prompt=stage_system,
            user_prompt=stage_user,
            tools=tools,
            tool_handlers=tool_handlers,
            llm_function=llm_function,
            max_iterations=stage.max_iterations if stage.max_iterations is not None else max_iterations,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
            stop_event=stop_event,
            **kwargs,
        )

        staged.stage_results[stage.name] = stage_result
        staged.total_input_tokens += stage_result.total_input_tokens
        staged.total_output_tokens += stage_result.total_output_tokens
        staged.total_cost_usd += stage_result.total_cost_usd
        staged.final_content = stage_result.final_content

        if on_stage_complete is not None:
            try:
                on_stage_complete(stage.name, stage_result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("run_staged_agent_loop: on_stage_complete raised for %s: %s", stage.name, exc)

        if not stage_result.done:
            staged.stages_failed.append(stage.name)
            break

        staged.stages_completed.append(stage.name)
        prev_output = stage_result.final_content

    staged.done = len(staged.stages_failed) == 0 and len(staged.stages_completed) == len(stages)
    return staged


# ---------------------------------------------------------------------------
# Rubric-gated self-iteration — declare "what done looks like", loop until a
# grader agrees. Adapted from deepagents' RubricMiddleware pattern.
# ---------------------------------------------------------------------------


class RubricVerdict:
    """String constants for :attr:`RubricGrade.verdict`.

    ``satisfied`` / ``needs_revision`` / ``failed`` are emitted by the grader
    LLM call. ``max_iterations_reached`` and ``grader_error`` are synthesized
    by :func:`run_agent_loop_with_rubric` itself — the grader cannot emit them.
    """

    satisfied = "satisfied"
    """Every rubric criterion is met — the loop stops here."""

    needs_revision = "needs_revision"
    """At least one criterion fails; ``feedback`` explains what to fix."""

    failed = "failed"
    """The rubric is malformed or impossible to evaluate against the response."""

    max_iterations_reached = "max_iterations_reached"
    """``needs_revision`` fired on the final allowed attempt; the loop stops
    with the agent's last response intact rather than looping forever."""

    grader_error = "grader_error"
    """The grader call itself failed (bad JSON, provider error, timeout)."""


RUBRIC_GRADER_SYSTEM_PROMPT = """You are a strict grader evaluating whether an agent's response satisfies a rubric.

You will be shown the rubric and the agent's final response. Judge ONLY against the rubric criteria — do not
reward style, verbosity, or effort that isn't called for by the rubric.

Return ONLY a JSON object with exactly these two fields, no other text:

{"verdict": "satisfied" | "needs_revision" | "failed", "feedback": "<concise, actionable feedback>"}

- "satisfied": every rubric criterion is met. "feedback" may be a short confirmation.
- "needs_revision": at least one criterion is not met. "feedback" MUST say exactly what to fix so the
  agent can act on it directly.
- "failed": the rubric itself is unmeetable or malformed given the response, or you genuinely cannot
  evaluate it. Use this sparingly — prefer "needs_revision" whenever a fix is describable."""

_RUBRIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "required": ["verdict", "feedback"],
    "properties": {
        "verdict": {"type": "string"},
        "feedback": {"type": "string"},
    },
}


@dataclass
class RubricGrade:
    """One grading round's verdict from :func:`run_agent_loop_with_rubric`."""

    verdict: str = ""
    feedback: str = ""
    raw_response: str = ""


@dataclass
class RubricLoopResult:
    """Outcome of :func:`run_agent_loop_with_rubric`."""

    result: AgentLoopResult = field(default_factory=AgentLoopResult)
    """The underlying agent loop's result from the final (or only) attempt."""

    satisfied: bool = False
    """True only if a grading round returned :attr:`RubricVerdict.satisfied`."""

    grades: list[RubricGrade] = field(default_factory=list)
    """One entry per grading round attempted, in order."""

    grading_attempts: int = 0
    """Number of grading rounds actually run (0 if the agent loop never completed)."""


def _grade_against_rubric(
    router: Any,
    *,
    rubric: str,
    final_content: str,
    llm_function: str,
    max_tokens: int,
    effort: str,
) -> RubricGrade:
    """Single grader LLM call: judge *final_content* against *rubric*.

    Not itself an agent loop — one plain, tool-free ``router.invoke`` call with
    a JSON-only system prompt, validated against :data:`_RUBRIC_OUTPUT_SCHEMA`.
    """
    from icdev.tools.llm.provider import LLMRequest

    grader_user_prompt = (
        f"## Rubric\n{rubric}\n\n## Agent's final response\n{final_content or '(empty response)'}"
    )
    request = LLMRequest(
        system_prompt=RUBRIC_GRADER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": grader_user_prompt}],
        tools=[],
        max_tokens=max_tokens,
        temperature=0.0,
        effort=effort,
    )
    try:
        response = router.invoke(llm_function, request)
        raw = getattr(response, "content", "") or ""
        parsed = json.loads(raw)
        _validate_output_schema(parsed, _RUBRIC_OUTPUT_SCHEMA)
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in (RubricVerdict.satisfied, RubricVerdict.needs_revision, RubricVerdict.failed):
            raise ValueError(f"Ungraded/unrecognized verdict: {verdict!r}")
        return RubricGrade(verdict=verdict, feedback=str(parsed.get("feedback", "")), raw_response=raw)
    except Exception as exc:  # noqa: BLE001 — any grader failure degrades to grader_error
        logger.warning("agent_loop: rubric grading failed: %s", exc)
        return RubricGrade(verdict=RubricVerdict.grader_error, feedback=str(exc))


def _invoke_pluggable_grader(
    grader: Callable[[AgentLoopResult], RubricGrade],
    result: AgentLoopResult,
) -> RubricGrade:
    """Call a pluggable, code-based grader, degrading failures to grader_error.

    Mirrors the failure contract of :func:`_grade_against_rubric`: the rubric
    loop must never crash because a caller's grader raised or returned garbage.
    The grader inspects real state (build/test/gate results) instead of judging
    text with a model, so it is fully LLM-agnostic — it never touches the router.
    """
    try:
        grade = grader(result)
        if not isinstance(grade, RubricGrade):
            raise TypeError(
                f"grader returned {type(grade).__name__}, expected RubricGrade"
            )
        verdict = str(grade.verdict).strip().lower()
        if verdict not in (
            RubricVerdict.satisfied,
            RubricVerdict.needs_revision,
            RubricVerdict.failed,
        ):
            raise ValueError(f"grader returned unrecognized verdict: {grade.verdict!r}")
        grade.verdict = verdict
        return grade
    except Exception as exc:  # noqa: BLE001 — any grader failure degrades to grader_error
        logger.warning("agent_loop: pluggable grader failed: %s", exc)
        return RubricGrade(verdict=RubricVerdict.grader_error, feedback=str(exc))


def run_agent_loop_with_rubric(
    router: Any,
    *,
    rubric: str | None = None,
    grader: Callable[[AgentLoopResult], RubricGrade] | None = None,
    max_grading_iterations: int | None = None,
    grader_llm_function: str | None = None,
    grader_max_tokens: int | None = None,
    on_grade: Callable[[int, RubricGrade], None] | None = None,
    harness_task_id: str | None = None,
    **kwargs: Any,
) -> RubricLoopResult:
    """Run :func:`run_agent_loop` and grade its output against *rubric*.

    LLM-agnostic: this primitive never constructs an LLM client and never
    assumes a provider. It routes every model call through the injected
    ``router`` by FUNCTION NAME (``llm_function`` / ``grader_llm_function``),
    which ``args/llm_config.yaml`` maps to whatever provider is configured
    (Claude / Kimi / Ollama / …, with air-gap fallback chains). Pass a custom
    ``grader`` for a code-based rubric (e.g. the delivery-pipeline gates); it
    is plain Python and touches no model at all.

    Declares "what done looks like" up front instead of trusting the agent's
    own judgment that it is finished. Each round:

      1. Run (or resume) the agent loop.
      2. If it did not complete cleanly (``result.done is False`` — truncated,
         budget-capped, errored), stop immediately. Grading a partial or
         failed response is not meaningful.
      3. A separate, tool-free grader LLM call judges ``result.final_content``
         against *rubric* and returns ``satisfied`` / ``needs_revision`` / ``failed``.
      4. On ``needs_revision``, the grader's feedback is appended to the
         transcript as a user message and the agent loop resumes from that
         point (via ``initial_messages``) with a fresh turn budget — it does
         not restart from ``user_prompt``.

    Grading stops on ``satisfied``, ``failed``, ``grader_error``, or after
    ``max_grading_iterations`` rounds — whichever comes first. The agent's
    last response is always preserved in the returned result, even when the
    grader never reaches ``satisfied``.

    Args:
        router: An ``LLMRouter`` instance.
        rubric: Free-text description of what a satisfactory response looks
            like. Passed verbatim to the LLM grader. Provide EITHER ``rubric``
            OR ``grader`` — not both, and not neither.
        grader: Optional pluggable, code-based grader
            ``callback(AgentLoopResult) -> RubricGrade``. When supplied it
            REPLACES the LLM rubric grader: no ``router`` call is made for
            grading, so verdicts come from real state (build/test/gate results)
            rather than a model judging text. Must return a :class:`RubricGrade`
            whose verdict is ``satisfied`` / ``needs_revision`` / ``failed``;
            any exception or bad return degrades to ``grader_error`` (loop
            stops). ``needs_revision`` feedback is injected verbatim and the
            agent loop resumes to fix it, exactly as with an LLM rubric.
        max_grading_iterations: Max grade-then-revise rounds before giving up.
            Loaded from ``args/llm_config.yaml``
            ``agent_loop.rubric.max_grading_iterations`` when not set
            explicitly; falls back to 3.
        grader_llm_function: Router function key for the grader call. Defaults
            to the same ``llm_function`` used for the agent loop.
        grader_max_tokens: Max output tokens for the grader's JSON response.
            Loaded from ``args/llm_config.yaml``
            ``agent_loop.rubric.grader_max_tokens`` when not set explicitly;
            falls back to 1024.
        on_grade: Optional ``callback(attempt, RubricGrade)`` fired after each
            grading round (for audit/observability).
        harness_task_id: Optional Continuous-Harness task id. Exactly ONE
            ``reflex="codegen"`` ``harness_eval`` decision is recorded for the
            whole rubric run (reflecting the FINAL grade), not one per grading
            round — inner :func:`run_agent_loop` calls have their own recording
            suppressed via ``_record_harness_decision=False``.
        **kwargs: Forwarded to :func:`run_agent_loop` (``system_prompt``,
            ``user_prompt``, ``tools``, ``tool_handlers``, etc.). Must not
            include ``initial_messages`` — it is managed internally.
            ``max_wall_clock_seconds`` is treated as a budget for the ENTIRE
            rubric run (all rounds plus the grading between them), not per
            round: each round receives only the time remaining, and a run whose
            budget is exhausted between rounds stops with
            ``result.truncation_reason == "max_wall_clock_seconds"``.

    Returns:
        :class:`RubricLoopResult`.

    Raises:
        ValueError: ``initial_messages`` was passed in ``kwargs``.
    """
    if "initial_messages" in kwargs:
        raise ValueError(
            "run_agent_loop_with_rubric manages initial_messages internally; "
            "do not pass it explicitly."
        )
    if grader is not None and rubric is not None:
        raise ValueError(
            "run_agent_loop_with_rubric: pass either 'rubric' (LLM grader) or "
            "'grader' (code grader), not both."
        )
    if grader is None and rubric is None:
        raise ValueError(
            "run_agent_loop_with_rubric: exactly one of 'rubric' or 'grader' "
            "is required."
        )

    budget_defaults = _load_budget_defaults()
    if max_grading_iterations is None:
        max_grading_iterations = budget_defaults.get("max_grading_iterations", 3)
    if grader_max_tokens is None:
        grader_max_tokens = budget_defaults.get("grader_max_tokens", 1024)

    agent_llm_function = kwargs.get("llm_function", "code_generation")
    effective_grader_function = grader_llm_function or agent_llm_function
    effort = kwargs.get("effort", "medium")

    # Session wall-clock ceiling (ars-wall-01) spans the WHOLE rubric run —
    # every agent-loop round AND the grading between them. Passing the caller's
    # budget to each round verbatim would let a 3-round rubric run for 3× the
    # ceiling, which is precisely the "loop-level and task-level budgets race
    # each other" failure this bound exists to remove.
    _wall_budget = kwargs.pop("max_wall_clock_seconds", None)
    if _wall_budget is None:
        _wall_budget = budget_defaults.get("max_wall_clock_seconds")
    _wall_deadline = _wall_clock_deadline(_wall_budget)

    loop_result = RubricLoopResult()
    prior_messages: list[dict[str, Any]] | None = None

    for attempt in range(1, max_grading_iterations + 1):
        call_kwargs = dict(kwargs)
        if _wall_deadline is not None:
            _remaining = _wall_deadline - time.monotonic()
            if _remaining <= 0:
                # Budget spent by earlier rounds (or their grading). Report it
                # on the result with the loop's own reason rather than letting
                # the caller's external kill timer decide.
                _elapsed = float(_wall_budget) - _remaining
                _stop_for_wall_clock(
                    loop_result.result,
                    loop_result.result.messages,
                    budget=float(_wall_budget),
                    elapsed=_elapsed,
                )
                loop_result.result.done = False
                loop_result.result.elapsed_seconds = _elapsed
                break
            call_kwargs["max_wall_clock_seconds"] = _remaining
        if prior_messages is not None:
            call_kwargs["initial_messages"] = prior_messages
        # Suppress per-round recording — one decision is recorded post-loop
        # reflecting the FINAL grade (not one row per grading round).
        call_kwargs["_record_harness_decision"] = False

        result = run_agent_loop(router, **call_kwargs)
        loop_result.result = result

        if not result.done:
            break  # agent loop itself didn't complete cleanly — nothing to grade

        if grader is not None:
            grade = _invoke_pluggable_grader(grader, result)
        else:
            grade = _grade_against_rubric(
                router,
                rubric=rubric,
                final_content=result.final_content,
                llm_function=effective_grader_function,
                max_tokens=grader_max_tokens,
                effort=effort,
            )
        loop_result.grades.append(grade)
        loop_result.grading_attempts = attempt
        if on_grade is not None:
            try:
                on_grade(attempt, grade)
            except Exception as exc:  # noqa: BLE001
                logger.warning("run_agent_loop_with_rubric: on_grade raised: %s", exc)

        if grade.verdict == RubricVerdict.satisfied:
            loop_result.satisfied = True
            break
        if grade.verdict in (RubricVerdict.failed, RubricVerdict.grader_error):
            break
        if attempt >= max_grading_iterations:
            grade.verdict = RubricVerdict.max_iterations_reached
            break

        # needs_revision — inject feedback and resume from the current transcript.
        prior_messages = list(result.messages) + [{
            "role": "user",
            "content": (
                f"[Rubric grader] Your response needs revision:\n{grade.feedback}\n\n"
                "Please address this feedback and provide an updated final response."
            ),
        }]

    # Continuous Harness feed — record exactly ONE codegen decision for the whole
    # rubric run, reflecting the FINAL grade (or the underlying result when the
    # agent loop never completed and no grade was produced).
    final_verdict = loop_result.grades[-1].verdict if loop_result.grades else None
    _record_codegen_decision(
        harness_task_id=harness_task_id,
        session_id=loop_result.result.session_id,
        llm_function=agent_llm_function,
        result=loop_result.result,
        rubric_verdict=final_verdict,
        grading_attempts=loop_result.grading_attempts,
    )

    return loop_result
