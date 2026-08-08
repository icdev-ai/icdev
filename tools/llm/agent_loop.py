# CUI // SP-CTI
"""Backward-compatibility shim for the agent-loop primitive.

The canonical implementation lives in :mod:`icdev.tools.llm.agent_loop`. This
module used to be a full, physically-separate copy of that code, which drifted
out of sync (it lacked ``run_agent_loop_with_rubric``, ``RubricGrade``,
``RubricVerdict``, ``RubricLoopResult`` and post-June TRUST/redaction changes).
Runtime call sites that imported ``from tools.llm.agent_loop import ...`` — e.g.
``tools/cortex/api.py`` and ``tools/mcp/cortex_server.py`` (and their ``icdev/``
mirrors) — silently bound the stale copy.

This file is now a pure re-export shim: every public (and internally-shared)
name is imported directly from the canonical module, so
``from tools.llm.agent_loop import X`` yields the *same object* as
``from icdev.tools.llm.agent_loop import X``. Do not add logic here — edit the
canonical module instead.
"""
from __future__ import annotations

# Re-export the entire public surface. ``import *`` picks up every non-underscore
# name (DONE, ResultSubtype, AgentLoopResult, run_agent_loop, LoopStage,
# StagedLoopResult, run_staged_agent_loop, RubricVerdict, RubricGrade,
# RubricLoopResult, run_agent_loop_with_rubric, the AgentLoop* exceptions, the
# ToolHandler/*Hook type aliases, RUBRIC_GRADER_SYSTEM_PROMPT, logger, ...).
from icdev.tools.llm.agent_loop import *  # noqa: F401,F403

# Explicitly re-export the underscore-prefixed helpers that other modules and the
# test suite import by name (``import *`` skips these because they start with
# ``_``). Keeping them here preserves object identity for callers that do
# ``from tools.llm.agent_loop import _load_budget_defaults`` etc.
from icdev.tools.llm.agent_loop import (  # noqa: F401
    DONE,
    AgentLoopCompactionError,
    AgentLoopResult,
    AgentLoopStopped,
    AgentLoopTimeout,
    AgentLoopUnsupported,
    LoopStage,
    PostToolUseHook,
    PreToolUseHook,
    RUBRIC_GRADER_SYSTEM_PROMPT,
    ResultSubtype,
    RubricGrade,
    RubricLoopResult,
    RubricVerdict,
    StagedLoopResult,
    StopHook,
    ToolHandler,
    TurnCallback,
    _assistant_message,
    _build_read_only_set,
    _check_tool_support,
    _estimate_block_tokens,
    _estimate_message_tokens,
    _estimate_text_tokens,
    _grade_against_rubric,
    _invoke_pluggable_grader,
    _is_budget_block,
    _load_budget_defaults,
    _maybe_compress_messages,
    _resolve_context_window_tokens,
    _retrieve_memory_context,
    _serialize_payload,
    _stop_for_hard_budget,
    _tool_result_message,
    _truncate_tool_result,
    _validate_output_schema,
    logger,
    run_agent_loop,
    run_agent_loop_with_rubric,
    run_staged_agent_loop,
)

# Explicit public surface for ``from tools.llm.agent_loop import *`` downstream.
__all__ = [
    "DONE",
    "AgentLoopCompactionError",
    "AgentLoopResult",
    "AgentLoopStopped",
    "AgentLoopTimeout",
    "AgentLoopUnsupported",
    "LoopStage",
    "PostToolUseHook",
    "PreToolUseHook",
    "RUBRIC_GRADER_SYSTEM_PROMPT",
    "ResultSubtype",
    "RubricGrade",
    "RubricLoopResult",
    "RubricVerdict",
    "StagedLoopResult",
    "StopHook",
    "ToolHandler",
    "TurnCallback",
    "logger",
    "run_agent_loop",
    "run_agent_loop_with_rubric",
    "run_staged_agent_loop",
]
