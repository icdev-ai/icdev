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
        user_id: str = "default",
        tenant_id: str = "",
        profile: str | None = None,
        apply_profile_env: bool = False,
    ) -> None:
        self._router = router
        self.system_prompt = system_prompt
        self.llm_function = llm_function
        self.max_iterations = max_iterations
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.command_handler = command_handler
        self.user_id = user_id
        # -- profile isolation (sag-prof-01) -------------------------------
        # Resolve the sticky active profile at startup (unless one is passed).
        # A named profile namespaces the tenant so sessions + profile memory are
        # partitioned without per-profile .db files; the default profile is a
        # strict no-op (tenant unchanged), preserving existing behaviour exactly.
        self.profile = ""
        try:
            from tools.agent_runtime import profiles as _profiles

            self.profile = (
                _profiles.active_profile() if profile is None
                else ("" if _profiles.is_default(profile) else profile)
            )
            if self.profile and apply_profile_env:
                _profiles.apply_overlay(self.profile)
            self.tenant_id = _profiles.scoped_tenant(tenant_id, self.profile)
        except Exception as exc:  # noqa: BLE001 — isolation is best-effort
            logger.debug("agent_runtime: profile resolution skipped: %s", exc)
            self.tenant_id = tenant_id
        self.tools, self.tool_handlers = build_builtin_toolset()
        self.session: RuntimeSession = RuntimeSession.create(
            title="Untitled session",
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )
        self._stop = threading.Event()
        self._profile_preamble: str | None = None
        self._project_preamble: str | None = None

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
        """Start a fresh session, replacing the current one.

        Before swapping, run the best-effort post-session skill-proposal hook on
        the closing session (sag-skl-01) — env-gated (``ICDEV_SAG_SKILL_PROPOSALS``)
        so it is silent by default and never blocks the swap.
        """
        self._post_session_hook()
        self.session = RuntimeSession.create(
            title=title,
            manager=self.session.manager,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )
        # Re-inject the operator profile + project context at the next turn of
        # the new session (a /new after an edit to CLAUDE.md picks it up).
        self._profile_preamble = None
        self._project_preamble = None
        return self.session

    def _post_session_hook(self) -> None:
        """Best-effort skill proposal on session close (sag-skl-01). Never raises."""
        try:
            from tools.agent_runtime.skills_lifecycle import maybe_propose_from_session

            maybe_propose_from_session(self)
        except Exception as exc:  # noqa: BLE001 — hook is best-effort
            logger.debug("agent_runtime: post-session hook skipped: %s", exc)

    def resume_session(self, context_id: str) -> RuntimeSession:
        """Rehydrate an existing conversation (``icdev chat --resume <ctx-id>``).

        Replaces the current session with one bound to ``context_id`` so new
        turns append to that transcript and tool-use history continues from where
        it left off.
        """
        self.session = RuntimeSession.load(
            context_id,
            manager=self.session.manager,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
        )
        self._profile_preamble = None
        self._project_preamble = None
        return self.session

    def use_toolset(
        self,
        bundle_names: list[str],
        *,
        approval_mode: str | None = None,
        approver: Any = None,
    ) -> list[str]:
        """Swap the runtime's tools to a bundle-based, safety-gated toolset.

        Replaces the small read-only starter toolset with the tools named by the
        given bundles (``args/agent_toolsets.yaml``), assembled through the
        sag-reg-02 dispatch layer with the sag-safe-01 command-approval gate. Any
        mutating tool (file write, terminal execution) therefore passes through
        ``run_pre_tool_check`` and the approval flow before executing.

        Returns the sorted names of the now-active tools.
        """
        from tools.agent_runtime.toolsets import build_toolset

        tools, handlers = build_toolset(
            bundle_names,
            approval_mode=approval_mode,
            router=self._router,  # may be None; smart mode degrades to heuristic
            approver=approver,
        )
        self.tools = tools
        self.tool_handlers = handlers
        return self.tool_names()

    def tool_names(self) -> list[str]:
        """Names of the currently registered tools (for ``/tools``)."""
        names = []
        for t in self.tools:
            fn = t.get("function", {}) if isinstance(t, dict) else {}
            name = fn.get("name") or t.get("name")
            if name:
                names.append(name)
        return sorted(names)

    # -- project context (hgx-sess-01) --------------------------------------

    def _project_context(self) -> str:
        """The project's own instructions, budgeted to the model's window.

        ``CLAUDE.md`` / ``AGENTS.md`` / ``memory/MEMORY.md`` plus the existing
        ``session_context_builder`` project-state summary, sized against
        ``floor_window_for_function`` so a 32k local model gets a truncated block
        rather than a swallowed window (see ``project_context``). Built once per
        session and cached; ``/new`` clears it.
        """
        if getattr(self, "_project_preamble", None) is None:
            preamble = ""
            try:
                from tools.agent_runtime.project_context import build_for_runtime

                preamble = build_for_runtime(self.llm_function, self.system_prompt)
            except Exception as exc:  # noqa: BLE001 — context is best-effort
                logger.debug("agent_runtime: project context skipped: %s", exc)
            self._project_preamble = preamble
        return self._project_preamble

    # -- profile memory (sag-mem-01) ---------------------------------------

    def _effective_system_prompt(self, user_input: str) -> str:
        """System prompt with project context + the operator profile injected once.

        Built at session start (first turn): the project's own instructions
        (:meth:`_project_context`) followed by ``profile_memory`` — durable
        facts/preferences plus the top hybrid-memory hits keyed to the first
        prompt — and cached so subsequent turns reuse the same preamble.
        """
        if getattr(self, "_profile_preamble", None) is None:
            preamble = ""
            try:
                from tools.agent_runtime.profile_memory import build_profile_context

                preamble = build_profile_context(
                    self.user_id, self.tenant_id, query=user_input
                )
            except Exception as exc:  # noqa: BLE001 — memory is best-effort
                logger.debug("agent_runtime: profile injection skipped: %s", exc)
            self._profile_preamble = preamble
        parts = [
            p
            for p in (self._project_context(), self._profile_preamble, self.system_prompt)
            if p
        ]
        return "\n\n".join(parts)

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
            system_prompt=self._effective_system_prompt(user_input),
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

    def stream_turn(
        self,
        user_input: str,
        *,
        on_delta: "Callable[[str], None] | None" = None,
    ) -> str:
        """Stream a conversational turn's tokens via ``LLMRouter.invoke_streaming``.

        A lightweight, **tool-free** turn for the interactive REPL: tokens are
        surfaced through ``on_delta`` as they arrive (sag-rt-04) instead of
        blocking until the full answer is ready. The turn is still recorded to the
        transcript and indexed, and usage is rolled forward, but it does not run
        the tool loop — use :meth:`run_turn` when the model needs tools.

        Returns the full accumulated assistant text. Falls back gracefully: on any
        streaming error it returns an error string (the REPL stays alive).
        """
        from tools.llm.provider import LLMRequest

        self.session.record_user(user_input)
        request = LLMRequest(
            messages=[{"role": "user", "content": user_input}],
            system_prompt=self._effective_system_prompt(user_input),
            agent_id="agent_runtime",
            project_id="agent_runtime",
        )
        chunks: list[str] = []
        in_tok = out_tok = 0
        try:
            for chunk in self.router.invoke_streaming(self.llm_function, request):
                ctype = chunk.get("type") if isinstance(chunk, dict) else None
                if ctype == "text":
                    delta = chunk.get("text", "") or ""
                    if delta:
                        chunks.append(delta)
                        if on_delta is not None:
                            on_delta(delta)
                elif ctype == "message_stop":
                    usage = chunk.get("usage") or {}
                    in_tok = int(usage.get("input_tokens", 0) or 0)
                    out_tok = int(usage.get("output_tokens", 0) or 0)
                elif ctype == "error":
                    err = chunk.get("error", "unknown streaming error")
                    logger.warning("agent_runtime: streaming error: %s", err)
                    text = "".join(chunks)
                    self.session.record_assistant(text)
                    return text or f"error: {err}"
        except Exception as exc:  # noqa: BLE001 — keep the REPL alive
            logger.warning("agent_runtime: stream_turn failed: %s", exc)
            text = "".join(chunks)
            self.session.record_assistant(text)
            return text or f"error: {exc}"

        text = "".join(chunks)
        self.session.record_assistant(text)
        # Roll usage forward (no agent-loop session id in the streaming path).
        self.session.turn_count += 1
        self.session.total_input_tokens += in_tok
        self.session.total_output_tokens += out_tok
        return text

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
        stream: bool = False,
    ) -> None:
        """Run the interactive read-eval-print loop until ``/exit`` or EOF.

        ``input_fn`` / ``output_fn`` are injectable for testing. A leading ``/``
        routes to :meth:`dispatch_command`; anything else is an agent turn. When
        ``stream`` is set, conversational turns render token-by-token via
        :meth:`stream_turn` (tool-free); otherwise the full tool-capable
        :meth:`run_turn` is used.
        """
        if banner:
            mode = " (streaming)" if stream else ""
            output_fn(
                f"ICDEV standalone agent runtime{mode}. Type /help for commands, "
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
                if stream:
                    import sys as _sys

                    def _emit(delta: str) -> None:
                        _sys.stdout.write(delta)
                        _sys.stdout.flush()

                    reply = self.stream_turn(text, on_delta=_emit)
                    if not reply.endswith("\n"):
                        output_fn("")  # terminate the streamed line
                else:
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
    # Wire the full slash-command registry (sag-rt-02) as the dispatcher.
    from tools.agent_runtime.commands import dispatch as _command_dispatch

    runtime = AgentRuntime(command_handler=_command_dispatch)
    runtime.loop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
