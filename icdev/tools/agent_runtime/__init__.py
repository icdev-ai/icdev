# CUI // SP-CTI
"""ICDEV Standalone Agent Runtime (SAG).

A persistent, interactive agent process that reuses ICDEV's production LLM
agent loop (:mod:`icdev.tools.llm.agent_loop`), chat context persistence
(:mod:`tools.chat.chat_manager`), and provider abstraction
(:class:`tools.llm.router.LLMRouter`). No new LLM execution paths or persistence
layers are introduced — the runtime is a thin orchestration shell.

Public surface:

- :class:`AgentRuntime`  — the runtime engine (``run_turn`` + REPL ``loop``).
- :class:`RuntimeSession` — a single conversation (chat context + agent-loop
  session id) with save/resume.
- :func:`build_builtin_toolset` — the small hardcoded starter toolset
  (file read/search, health_check). Auto-discovery lands in sag-reg-01.
"""
from __future__ import annotations

from tools.agent_runtime.builtin_tools import build_builtin_toolset
from tools.agent_runtime.runtime import AgentRuntime
from tools.agent_runtime.sessions import RuntimeSession

__all__ = ["AgentRuntime", "RuntimeSession", "build_builtin_toolset"]
