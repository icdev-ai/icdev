# CUI // SP-CTI
"""ICDEV™ Agent Toolkit — unified builtin tool catalog (OPT-67).

Provides the deterministic primitive tools every ICDEV agent needs
(filesystem, shell, planning, subagent spawning) plus a one-line
`create_agent()` composer. Inspired by the tool catalog pattern
from langchain-ai/deepagents (MIT, 20k stars) but implemented as
pure deterministic Python on top of tools.llm.router.LLMRouter —
NO runtime dependency on deepagents, langgraph, or langchain-core.

ICDEV implementation is independent. Concept adopted from
https://github.com/langchain-ai/deepagents (MIT license) —
structural audit registered in tools/workflow/coherence_checker.py
_ATTRIBUTION_REGISTRY.

Usage:

    from tools.agent_toolkit import create_agent

    agent = create_agent(
        name="my-agent",
        system_prompt="You are a helpful ICDEV assistant.",
    )
    result = agent.invoke([{"role": "user", "content": "ls tools/canvas/"}])
    print(result.messages[-1]["content"])

Or use primitives directly without an LLM:

    from tools.agent_toolkit import read_file, edit_file, grep

    content = read_file("args/llm_config.yaml")
    edit_file("args/foo.yaml", "old", "new")
    hits = grep(r"TODO", paths=["tools/canvas/"])
"""
from __future__ import annotations

from tools.agent_toolkit._fs import (
    read_file,
    write_file,
    edit_file,
    ls,
    glob,
    grep,
)
from tools.agent_toolkit._shell import execute_shell
from tools.agent_toolkit._browser import (
    browser_click,
    browser_navigate,
    browser_press,
    browser_read_state,
    browser_screenshot,
    browser_select,
    browser_session,
    browser_type,
)
from tools.agent_toolkit._planning import write_todos, update_todo
from tools.agent_toolkit._subagent import (
    delegate_batch,
    delegate_task,
    spawn_subagent,
)
from tools.agent_toolkit._composer import (
    Agent,
    AgentResult,
    create_agent,
    list_default_tools,
)

__all__ = [
    # Filesystem primitives
    "read_file",
    "write_file",
    "edit_file",
    "ls",
    "glob",
    "grep",
    # Shell
    "execute_shell",
    # Browser (oss-browse-03) — scope/budget/audit enforced by
    # tools.browser.scope.GuardedDriver, not re-implemented here
    "browser_session",
    "browser_navigate",
    "browser_read_state",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_press",
    "browser_screenshot",
    # Planning
    "write_todos",
    "update_todo",
    # Subagent
    "spawn_subagent",
    "delegate_task",
    "delegate_batch",
    # Composer
    "Agent",
    "AgentResult",
    "create_agent",
    "list_default_tools",
]
