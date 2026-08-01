# CUI // SP-CTI
"""OPT-71: tools/agents/adapter_base.py — AgentAdapter protocol.

OPT-71 agent adapter pattern inspired by jonwiggins/optio (MIT).
See https://github.com/jonwiggins/optio packages/agent-adapters

An AgentAdapter wraps a full *agent session* (multi-turn, tool calls,
completion detection). This sits one layer ABOVE tools/llm/*_provider.py
which wraps a single LLM call.

Usage:
    from tools.agents import pick_default, AgentSession

    adapter = pick_default()
    session = AgentSession(
        task_id="task-xyz",
        prompt="Ship OPT-42",
        working_dir="/path/to/repo",
    )
    result = adapter.invoke(session)
    if result.completed:
        ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable


class NotInstalledError(RuntimeError):
    """Raised when an adapter's backend is not available on this host."""


@dataclass
class AgentSession:
    """Everything an adapter needs to run one task.

    Adapters treat this object as read-only. Mutation lives in
    AgentResult so multiple sessions can share the same prompt without
    interfering with each other.
    """
    task_id: str
    prompt: str
    working_dir: str
    system_prompt: str = ""
    max_turns: int = 50
    max_tokens: int = 8192
    timeout_seconds: int = 1800
    auth: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Outcome of one adapter.invoke() call."""
    task_id: str
    adapter_name: str
    completed: bool
    exit_code: int = 0
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    handle: Any = None  # subprocess.Popen or _LLMTaskHandle-compatible
    structured: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Protocol every adapter must satisfy.

    Intentionally minimal so adding a new backend only means implementing
    these 5 methods.
    """

    name: str

    def available(self) -> bool:
        """Return True if this adapter can actually run on this host.

        Cheap check only — no network calls. Examples:
          - claude_cli: shutil.which('claude') is not None
          - local_llm_router: LLMRouter().can_serve('code_generation')
          - codex_cli: shutil.which('openai-codex') is not None
        """
        ...

    def prepare_prompt(self, session: AgentSession) -> str:
        """Return the final prompt string to send to the backend.

        Default behavior is to return session.prompt unchanged. Adapters
        can override to inject tool instructions, completion markers,
        etc.
        """
        ...

    def invoke(self, session: AgentSession) -> AgentResult:
        """Run the agent session and return a result.

        Raises NotInstalledError if `available()` would return False.
        Subclasses should call `self.require_available()` at the start.
        """
        ...

    def detect_completion(self, output: str) -> bool:
        """Return True if the agent output indicates task completion.

        Pure-string heuristic — no side effects. Used by pr_watcher and
        kanban to decide whether to mark a task done without an
        explicit DB transition.
        """
        ...

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Extract structured output from an agent response.

        Return at minimum:
            {"content": str, "tool_calls": [...], "diff": "..."}
        Adapters can return additional keys.
        """
        ...
