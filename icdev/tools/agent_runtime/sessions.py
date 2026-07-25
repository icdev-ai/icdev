# CUI // SP-CTI
"""Session management for the standalone agent runtime (SAG).

A :class:`RuntimeSession` couples the two production persistence layers ICDEV
already ships — it introduces **no** new storage:

- :class:`tools.chat.chat_manager.ChatManager` owns the user-visible conversation
  (a ``chat_contexts`` row plus ``chat_messages`` turns): title, status, and the
  human-readable transcript.
- :mod:`icdev.tools.llm.agent_loop_session` owns the LLM-facing transcript in
  ``agent_loop_sessions`` (``save_session`` / ``load_session``), keyed by the
  :class:`AgentLoopResult.session_id`. Passing that id back as
  ``resume_session_id`` restores the full tool-use history across turns and
  process restarts.

The runtime keeps one "current" :class:`RuntimeSession`; ``/new`` swaps it for a
fresh one. All DB access flows through ``get_connection()`` (PG-primary) under
the same RLS as the rest of the platform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tools.chat.chat_manager import ChatManager
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.sessions")

_LLM_FUNCTION = "code_generation"


@dataclass
class RuntimeSession:
    """A single standalone-agent conversation.

    Attributes:
        context_id:  ``chat_contexts.id`` for the human-readable transcript.
        title:       Display title (mutable via ``/title``).
        resume_session_id: The most recent :class:`AgentLoopResult.session_id`,
            replayed into the next turn so tool-use history persists. Empty until
            the first turn completes.
        total_input_tokens / total_output_tokens / total_cost_usd: Cumulative
            usage across every turn in this session (for ``/usage``).
        turn_count:  Number of completed agent turns.
    """

    context_id: str
    title: str = "Untitled session"
    resume_session_id: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    turn_count: int = 0
    _manager: ChatManager = field(repr=False, default=None)  # type: ignore[assignment]

    # -- construction ------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        manager: ChatManager | None = None,
        title: str = "Untitled session",
        system_prompt: str | None = None,
        classification: str | None = None,
    ) -> "RuntimeSession":
        """Create a fresh session (new ``chat_contexts`` row) and return it."""
        mgr = manager or ChatManager()
        ctx_id = mgr.create_context(
            title=title,
            system_prompt=system_prompt,
            classification=classification,
            config={"origin": "standalone_agent_runtime"},
        )
        logger.info("agent_runtime: created session %s (title=%r)", ctx_id, title)
        return cls(context_id=ctx_id, title=title, _manager=mgr)

    @property
    def manager(self) -> ChatManager:
        if self._manager is None:
            self._manager = ChatManager()
        return self._manager

    # -- transcript --------------------------------------------------------

    def record_user(self, text: str) -> None:
        """Append the user's turn to the human-readable transcript."""
        try:
            self.manager.add_message(self.context_id, role="user", content=text)
        except Exception as exc:  # noqa: BLE001 — transcript is best-effort
            logger.warning("agent_runtime: failed to record user message: %s", exc)

    def record_assistant(self, text: str) -> None:
        """Append the assistant's turn to the human-readable transcript."""
        try:
            self.manager.add_message(
                self.context_id, role="assistant", content=text or ""
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_runtime: failed to record assistant message: %s", exc)

    def messages(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent human-readable transcript messages (for ``/memory``)."""
        try:
            return self.manager.get_messages(self.context_id, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_runtime: failed to load messages: %s", exc)
            return []

    def set_title(self, title: str) -> None:
        self.title = title
        try:
            self.manager.update_title(self.context_id, title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_runtime: failed to update title: %s", exc)

    # -- LLM-facing session persistence -----------------------------------

    def persist(self, result: Any, *, system_prompt: str = "") -> None:
        """Persist an :class:`AgentLoopResult`, roll usage forward, and update the
        resume id so the next turn continues this conversation."""
        # Import lazily to avoid a hard import cost when persistence is unused.
        from icdev.tools.llm.agent_loop_session import save_session

        session_id = getattr(result, "session_id", "") or ""
        if session_id:
            self.resume_session_id = session_id
            try:
                save_session(
                    result,
                    llm_function=_LLM_FUNCTION,
                    system_prompt=system_prompt,
                )
            except Exception as exc:  # noqa: BLE001 — save is best-effort
                logger.warning("agent_runtime: save_session failed: %s", exc)

        self.turn_count += 1
        self.total_input_tokens += int(getattr(result, "total_input_tokens", 0) or 0)
        self.total_output_tokens += int(getattr(result, "total_output_tokens", 0) or 0)
        self.total_cost_usd += float(getattr(result, "total_cost_usd", 0.0) or 0.0)

    def usage(self) -> dict[str, Any]:
        """Return a usage summary dict for ``/usage``."""
        return {
            "turns": self.turn_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
            "session_id": self.resume_session_id,
            "context_id": self.context_id,
        }
