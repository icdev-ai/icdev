# CUI // SP-CTI
"""Standalone agent runtime engine (SAG) — sag-rt-01.

:class:`AgentRuntime` is a persistent, interactive agent process. Its turn
executor (:meth:`run_turn`) is a thin shell over ICDEV's production agent loop
(:func:`icdev.tools.llm.agent_loop.run_agent_loop`), which already provides:

- native tool-use with parallel read-only dispatch,
- lifecycle hooks + budget caps (max tokens / cost / iterations),
- context compression (``context_compressor``) when history overflows,
- optional memory injection, and session resume via ``resume_session_id``.

LLM calls flow through :class:`tools.llm.router.LLMRouter` — the provider
abstraction — so no model IDs are hardcoded (admins configure ``.env`` /
``args/llm_config.yaml``). Conversation state is owned by
:class:`RuntimeSession`, which reuses ``chat_manager`` and ``agent_loop_session``
for persistence. The runtime adds no new storage or LLM execution path.

The starter toolset is intentionally small (file read/search, health_check);
dynamic tool auto-discovery lands in sag-reg-01. The slash-command registry lands
in sag-rt-02 — this module exposes a minimal built-in dispatcher plus an
extension seam (:attr:`AgentRuntime.command_handler`) that sag-rt-02/sag-gw-01
plug into without touching the turn executor.
"""
from __future__ import annotations

import sys
import threading
from typing import Any, Callable

from tools.agent_runtime.builtin_tools import build_builtin_toolset
from tools.agent_runtime.sessions import RuntimeSession
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.runtime")

_DEFAULT_SYSTEM_PROMPT = (
    "You are the ICDEV standalone agent — a helpful, precise coding and research "
    "assistant running as a persistent interactive process. You have a small set "
    "of read-only tools (read_file, search_files, health_check) scoped to the "
    "repository. Use them to ground your answers in the actual code before "
    "responding. Be concise and cite file paths you inspected."
)

_DEFAULT_LLM_FUNCTION = "code_generation"

# A command handler takes (runtime, raw_input) and returns a
# ``(handled, response_text, should_exit)`` tuple.
CommandHandler = Callable[["AgentRuntime", str], "tuple[bool, str, bool]"]


class AgentRuntime:
    """Persistent interactive agent runtime.

    Args:
        router: An ``LLMRouter`` instance. When ``None``, one is constructed
            lazily on first use (provider abstraction — no model IDs here).
        system_prompt: System instruction for the agent.
        llm_function: Router routing-function key (default ``code_generation``).
        max_iterations / max_total_tokens / max_cost_usd: Per-turn budget caps
            forwarded to :func:`run_agent_loop`.
        command_handler: Optional slash-command dispatcher. When ``None``, the
            built-in minimal dispatcher (``/help``, ``/new``, ``/exit``) is used;
            sag-rt-02 injects the full registry here.
    """

    def __init__(
        self,
        *,
        router: Any = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        llm_function: str = _DEFAULT_LLM_FUNCTION,
        max_iterations: int = 12,
        max_total_tokens: int | None = None,
        max_cost_usd: float | None = None,
        command_handler: CommandHandler | None = None,
    ) -> None:
        self._router = router
        self.system_prompt = system_prompt
        self.llm_function = llm_function
        self.max_iterations = max_iterations
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.command_handler = command_handler
        self.tools, self.tool_handlers = build_builtin_toolset()
        self.session: RuntimeSession = RuntimeSession.create(title="Untitled session")
        self._stop = threading.Event()

    # -- router ------------------------------------------------------------

    @property
    def router(self) -> Any:
        """Lazily construct the LLMRouter so importing this module is cheap."""
        if self._router is None:
            from tools.llm.router import LLMRouter

            self._router = LLMRouter()
        return self._router

    # -- session control ---------------------------------------------------

    def new_session(self, title: str = "Untitled session") -> RuntimeSession:
        """Start a fresh session, replacing the current one."""
        self.session = RuntimeSession.create(
            title=title, manager=self.session.manager
        )
        return self.session

    def tool_names(self) -> list[str]:
        """Names of the currently registered tools (for ``/tools``)."""
        names = []
        for t in self.tools:
            fn = t.get("function", {}) if isinstance(t, dict) else {}
            name = fn.get("name") or t.get("name")
            if name:
                names.append(name)
        return sorted(names)

    # -- turn execution ----------------------------------------------------

    def run_turn(self, user_input: str) -> Any:
        """Execute one agent turn over ``user_input`` and return the
        :class:`AgentLoopResult`.

        The conversation is persisted and the resume id rolled forward so the
        next turn continues the same session (tool-use history included).
        """
        from icdev.tools.llm.agent_loop import run_agent_loop

        self.session.record_user(user_input)
        result = run_agent_loop(
            self.router,
            system_prompt=self.system_prompt,
            user_prompt=user_input,
            tools=self.tools,
            tool_handlers=self.tool_handlers,
            llm_function=self.llm_function,
            max_iterations=self.max_iterations,
            max_total_tokens=self.max_total_tokens,
            max_cost_usd=self.max_cost_usd,
            resume_session_id=self.session.resume_session_id or None,
            stop_event=self._stop,
        )
        self.session.record_assistant(getattr(result, "final_content", "") or "")
        self.session.persist(result, system_prompt=self.system_prompt)
        return result

    # -- slash commands (minimal built-in; full registry in sag-rt-02) -----

    def dispatch_command(self, raw: str) -> "tuple[bool, str, bool]":
        """Dispatch a ``/command``.

        Returns ``(handled, response_text, should_exit)``. Delegates to
        :attr:`command_handler` when one is injected (sag-rt-02); otherwise
        applies the minimal built-in set so the runtime is usable standalone.
        """
        if self.command_handler is not None:
            return self.command_handler(self, raw)
        return self._builtin_dispatch(raw)

    def _builtin_dispatch(self, raw: str) -> "tuple[bool, str, bool]":
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/exit", "/quit"):
            return True, "Session saved. Goodbye.", True
        if cmd == "/new":
            self.new_session(title=arg or "Untitled session")
            return True, f"Started new session {self.session.context_id}.", False
        if cmd == "/tools":
            return True, "Tools: " + ", ".join(self.tool_names()), False
        if cmd in ("/help", "/?"):
            return (
                True,
                "Commands: /new [title], /tools, /help, /exit. "
                "(Full command set lands in sag-rt-02.)",
                False,
            )
        return True, f"Unknown command {cmd!r}. Try /help.", False

    # -- REPL --------------------------------------------------------------

    def loop(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        banner: bool = True,
    ) -> None:
        """Run the interactive read-eval-print loop until ``/exit`` or EOF.

        ``input_fn`` / ``output_fn`` are injectable for testing. A leading ``/``
        routes to :meth:`dispatch_command`; anything else is an agent turn.
        """
        if banner:
            output_fn(
                "ICDEV standalone agent runtime. Type /help for commands, "
                "/exit to quit."
            )
        while True:
            try:
                raw = input_fn("icdev> ")
            except (EOFError, KeyboardInterrupt):
                output_fn("\nSession saved. Goodbye.")
                return
            if raw is None:
                return
            text = raw.strip()
            if not text:
                continue
            if text.startswith("/"):
                _handled, response, should_exit = self.dispatch_command(text)
                if response:
                    output_fn(response)
                if should_exit:
                    return
                continue
            try:
                result = self.run_turn(text)
                output_fn(getattr(result, "final_content", "") or "(no response)")
            except Exception as exc:  # noqa: BLE001 — keep the REPL alive
                logger.exception("agent_runtime: turn failed")
                output_fn(f"error: {exc}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: start an interactive runtime session."""
    _argv = sys.argv[1:] if argv is None else argv
    if "--help" in _argv or "-h" in _argv:
        print("Usage: python -m tools.agent_runtime.runtime")
        print("Start an interactive ICDEV standalone agent session.")
        return 0
    runtime = AgentRuntime()
    runtime.loop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
