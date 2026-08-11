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

import contextlib
import signal
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
_DEFAULT_MAX_ITERATIONS = 12

# A command handler takes (runtime, raw_input) and returns a
# ``(handled, response_text, should_exit)`` tuple.
CommandHandler = Callable[["AgentRuntime", str], "tuple[bool, str, bool]"]


class AgentRuntime:
    """Persistent interactive agent runtime.

    Args:
        router: An ``LLMRouter`` instance. When ``None``, one is constructed
            lazily on first use (provider abstraction — no model IDs here).
        system_prompt: System instruction for the agent.
        llm_function: Router routing-function key. ``None`` (the default) takes
            it from ``args/agent_runtime.yaml`` / ``ICDEV_SAG_LLM_FUNCTION``,
            falling back to ``code_generation``.
        max_iterations / max_total_tokens / max_cost_usd: Per-turn budget caps
            forwarded to :func:`run_agent_loop`. ``None`` defers to the config
            layer the same way.
        config: An :class:`~tools.agent_runtime.config.AgentRuntimeConfig`.
            ``None`` loads ``args/agent_runtime.yaml`` (cached). Passing one
            explicitly is how a test — or an embedder with its own config file —
            pins the runtime's settings without touching ``os.environ``.
        command_handler: Optional slash-command dispatcher. When ``None``, the
            built-in minimal dispatcher (``/help``, ``/new``, ``/exit``) is used;
            sag-rt-02 injects the full registry here.
        unattended: Deliver approval asks to the approval inbox instead of a
            console (agov-inbox-04). **Routing only** — it does not widen what
            the agent may do, downgrade any tier, or approve anything; an
            irreversible call still halts, on a durable ``approval_items`` row
            instead of on EOF. Set by an explicit human act (``icdev chat
            --unattended``, a cron job's own column), never inferred from a
            missing TTY.

    Precedence for every configurable value is ``explicit argument > environment
    variable > args/agent_runtime.yaml > built-in default`` (hgx-cfg-01). The
    config file is a layer beneath the environment, never above it.
    """

    def __init__(
        self,
        *,
        router: Any = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        llm_function: str | None = None,
        max_iterations: int | None = None,
        max_total_tokens: int | None = None,
        max_cost_usd: float | None = None,
        config: Any = None,
        command_handler: CommandHandler | None = None,
        user_id: str = "default",
        tenant_id: str = "",
        profile: str | None = None,
        apply_profile_env: bool = False,
        unattended: bool = False,
    ) -> None:
        self._router = router
        # agov-inbox-04. A plain attribute, not a resolved policy: it selects
        # which approver `use_toolset` injects and nothing else.
        self.unattended = bool(unattended)
        self.system_prompt = system_prompt
        # -- declarative configuration (hgx-cfg-01) -------------------------
        # Loaded once, here, so every knob has one visible resolution point.
        # A missing/broken config file degrades to the built-in defaults rather
        # than refusing to construct — configuration is not a hard dependency of
        # starting an agent.
        if config is None:
            try:
                from tools.agent_runtime.config import load_config

                config = load_config()
            except Exception as exc:  # noqa: BLE001 — config layer is optional
                logger.debug("agent_runtime: config layer unavailable: %s", exc)
        self.config = config
        self.llm_function = (
            llm_function
            if llm_function is not None
            else self._configured("llm_function", _DEFAULT_LLM_FUNCTION)
        )
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else self._configured("max_iterations", _DEFAULT_MAX_ITERATIONS)
        )
        self.max_total_tokens = (
            max_total_tokens
            if max_total_tokens is not None
            else self._configured("max_total_tokens", None)
        )
        self.max_cost_usd = (
            max_cost_usd
            if max_cost_usd is not None
            else self._configured("max_cost_usd", None)
        )
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
        # Set for the duration of a turn (hgx-ctxw-03). The SIGINT handler reads
        # it to decide whether Ctrl-C cancels the turn or leaves the REPL.
        self._turn_active = threading.Event()
        self._profile_preamble: str | None = None
        self._project_preamble: str | None = None
        self._goals_preamble: str | None = None

    # -- configuration (hgx-cfg-01) ----------------------------------------

    def _configured(self, name: str, fallback: Any) -> Any:
        """Read one runtime knob off :attr:`config`, or ``fallback``.

        Tolerant on purpose: ``config`` may be ``None`` (the loader failed) or a
        duck-typed stand-in supplied by an embedder, and a runtime that will not
        construct because a YAML file is missing is worse than one running on
        defaults.
        """
        if self.config is None:
            return fallback
        try:
            value = getattr(self.config, name)
        except Exception as exc:  # noqa: BLE001 — a bad config never blocks start
            logger.debug("agent_runtime: config.%s unreadable: %s", name, exc)
            return fallback
        return fallback if value is None else value

    # -- cancellation (hgx-ctxw-03) ----------------------------------------

    @property
    def stop_event(self) -> threading.Event:
        """The cancellation token handed to every turn (and every tool handler)."""
        return self._stop

    @property
    def stopping(self) -> bool:
        """True once :meth:`stop` has been called and not yet cleared."""
        return self._stop.is_set()

    @property
    def turn_active(self) -> bool:
        """True while a turn is executing (used by the REPL's SIGINT handler)."""
        return self._turn_active.is_set()

    def stop(self) -> None:
        """Request that the in-flight turn stop at its next safe boundary.

        Thread- and signal-safe: it only sets a ``threading.Event``, which is
        what the loop, every waiting future and every cooperating tool handler
        poll. The turn ends with ``truncation_reason="stop_event"``; it is not
        an error and the process is untouched. Call :meth:`clear_stop` before
        running another turn.
        """
        self._stop.set()

    def clear_stop(self) -> None:
        """Clear a previous :meth:`stop` so the next turn may run.

        Deliberately NOT called at the start of :meth:`run_turn`: a caller that
        does ``runtime.stop()`` then ``runtime.run_turn(...)`` must get a turn
        that exits at its first boundary, not one that silently ignores the
        stop. The REPL clears the token after each turn instead.
        """
        self._stop.clear()

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
        self._goals_preamble = None
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
        self._goals_preamble = None
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

        When no ``approver`` is supplied and this runtime is ``unattended``
        (agov-inbox-04), the approval ask is routed to the durable approval
        inbox instead of a console nobody is watching. That is a change of
        DESTINATION only: the same gate, the same policy, the same tiers, and a
        call that needed a human still needs a human — it now suspends on a
        pending ``approval_items`` row rather than being denied on EOF. An
        explicitly passed ``approver`` always wins.

        Returns the sorted names of the now-active tools.
        """
        from tools.agent_runtime.toolsets import build_toolset

        if approver is None and self.unattended:
            try:
                from tools.agent_runtime.unattended import safety_approver_for

                approver = safety_approver_for(
                    self.session.context_id, unattended=True
                )
            except Exception as exc:  # noqa: BLE001 — degrade to the STRICTER path
                logger.warning(
                    "agent_runtime: unattended routing unavailable (%s); asks go "
                    "to the console approver, which denies on EOF", exc,
                )

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

    # -- standing goals (hgx-goal-02) ---------------------------------------

    def _goals_context(self) -> str:
        """The operator's active standing goals, capped and budgeted.

        Cached like the other preambles, but with a shorter life: unlike the
        project's instructions, goals change *during* a session — that is the
        point of ``/goal`` — so every mutation calls :meth:`invalidate_goals`
        and the next turn rebuilds this from the store. See
        ``goal_context.render_block`` for the two caps (count, then tokens).
        """
        if getattr(self, "_goals_preamble", None) is None:
            block = ""
            try:
                from tools.agent_runtime.goal_context import build_for_runtime

                block = build_for_runtime(
                    self.llm_function,
                    self.system_prompt,
                    user_id=self.user_id,
                    tenant_id=self.tenant_id,
                    context_id=getattr(self.session, "context_id", "") or "",
                )
            except Exception as exc:  # noqa: BLE001 — goals are best-effort
                logger.debug("agent_runtime: goal injection skipped: %s", exc)
            self._goals_preamble = block
        return self._goals_preamble

    def invalidate_goals(self) -> None:
        """Drop the cached goal block so the next turn re-reads the store.

        Public because the ``/goal`` handlers live in ``commands.py`` and a
        command that mutates goals must not have to reach into a private
        attribute to make its own change visible.
        """
        self._goals_preamble = None

    # -- profile memory (sag-mem-01) ---------------------------------------

    def _profile_memory_enabled(self) -> bool:
        """``ICDEV_SAG_PROFILE_MEMORY`` → ``args/agent_runtime.yaml`` → on.

        The env var is read first and still wins (hgx-cfg-01). Defaults to on so
        an install with no config file behaves exactly as it did before.
        """
        if self.config is None:
            return True
        try:
            from tools.agent_runtime.config import ENV_PROFILE_MEMORY

            return self.config.subsystem_enabled(
                "profile_memory", env=ENV_PROFILE_MEMORY, default=True
            )
        except Exception as exc:  # noqa: BLE001 — a bad config never disables memory
            logger.debug("agent_runtime: profile-memory toggle unreadable: %s", exc)
            return True

    def _effective_system_prompt(self, user_input: str) -> str:
        """System prompt with project context, goals and the operator profile.

        Built at session start (first turn): the project's own instructions
        (:meth:`_project_context`), the active standing goals
        (:meth:`_goals_context`), then ``profile_memory`` — durable
        facts/preferences plus the top hybrid-memory hits keyed to the first
        prompt — and cached so subsequent turns reuse the same preamble. The
        goal block is the one part that is rebuilt mid-session, whenever
        :meth:`invalidate_goals` has been called.
        """
        if getattr(self, "_profile_preamble", None) is None:
            preamble = ""
            if self._profile_memory_enabled():
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
            for p in (
                self._project_context(),
                self._goals_context(),
                self._profile_preamble,
                self.system_prompt,
            )
            if p
        ]
        return "\n\n".join(parts)

    # -- turn execution ----------------------------------------------------

    def run_turn(self, user_input: str) -> Any:
        """Execute one agent turn over ``user_input`` and return the
        :class:`AgentLoopResult`.

        The conversation is persisted and the resume id rolled forward so the
        next turn continues the same session (tool-use history included).

        The turn is interruptible: :meth:`stop` (or Ctrl-C in the REPL) ends it
        at the next boundary with ``truncation_reason == "stop_event"``. The
        partial transcript is still recorded and persisted, so ``/resume``
        continues from where the operator stopped.
        """
        from icdev.tools.llm.agent_loop import run_agent_loop

        self.session.record_user(user_input)
        self._turn_active.set()
        try:
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
        finally:
            self._turn_active.clear()
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
        streaming error it returns an error string (the REPL stays alive). Like
        :meth:`run_turn` it is interruptible — :meth:`stop` ends it at the next
        chunk boundary and whatever streamed so far is recorded.
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
        self._turn_active.set()
        try:
            for chunk in self.router.invoke_streaming(self.llm_function, request):
                # Cancellation boundary: a stream yields many small chunks, so
                # this is as responsive as the loop's per-turn boundary.
                if self._stop.is_set():
                    logger.info("agent_runtime: stream stopped by operator")
                    text = "".join(chunks)
                    self.session.record_assistant(text)
                    return text
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
        finally:
            self._turn_active.clear()

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

        For the duration of the REPL, Ctrl-C stops the *turn* rather than the
        *process* — see :func:`install_interrupt_handler`. The previous SIGINT
        disposition is restored on the way out, so embedding this REPL does not
        leave a handler behind.
        """
        if banner:
            mode = " (streaming)" if stream else ""
            output_fn(
                f"ICDEV standalone agent runtime{mode}. Type /help for commands, "
                "/exit to quit. Ctrl-C stops the running turn."
            )
        with install_interrupt_handler(self, output_fn):
            self._repl(input_fn, output_fn, stream)

    def _repl(
        self,
        input_fn: Callable[[str], str],
        output_fn: Callable[[str], None],
        stream: bool,
    ) -> None:
        """The read-eval-print body. See :meth:`loop` for the public contract."""
        while True:
            try:
                raw = input_fn("icdev> ")
            except (EOFError, KeyboardInterrupt):
                # At the prompt (no turn running) the SIGINT handler re-raises,
                # so Ctrl-C here still means "leave", exactly as before.
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
                    def _emit(delta: str) -> None:
                        sys.stdout.write(delta)
                        sys.stdout.flush()

                    reply = self.stream_turn(text, on_delta=_emit)
                    if not reply.endswith("\n"):
                        output_fn("")  # terminate the streamed line
                    if self.stopping:
                        output_fn("Turn stopped.")
                else:
                    result = self.run_turn(text)
                    if getattr(result, "truncation_reason", "") == "stop_event":
                        partial = getattr(result, "final_content", "") or ""
                        if partial:
                            output_fn(partial)
                        output_fn(
                            "Turn stopped. Partial work is saved — "
                            "type a follow-up to continue."
                        )
                    else:
                        output_fn(
                            getattr(result, "final_content", "") or "(no response)"
                        )
            except KeyboardInterrupt:
                # A second Ctrl-C escalates past the cooperative stop. It is a
                # BaseException, so `except Exception` below never sees it —
                # catching it here is what keeps the process alive.
                output_fn("\nTurn interrupted.")
            except Exception as exc:  # noqa: BLE001 — keep the REPL alive
                logger.exception("agent_runtime: turn failed")
                output_fn(f"error: {exc}")
            finally:
                # Re-arm for the next turn. Held until here so the branches
                # above can still read `self.stopping`.
                self.clear_stop()


# ---------------------------------------------------------------------------
# Ctrl-C handling (hgx-ctxw-03)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def install_interrupt_handler(
    runtime: AgentRuntime,
    output_fn: Callable[[str], None] = print,
) -> Any:
    """Make Ctrl-C stop the running turn instead of killing the process.

    ``signal.signal(SIGINT, ...)`` is the one signal API that behaves the same
    on Windows and POSIX, which is why it is used here rather than
    ``SIGBREAK``, process groups or ``loop.add_signal_handler`` (Unix-only).
    Windows delivers the interrupt to the **main thread only**, so the handler
    must not assume a worker will see a ``KeyboardInterrupt``; it sets
    :attr:`AgentRuntime.stop_event` — which the loop, its pending futures and
    every cooperating tool handler poll — instead.

    Three cases, by design:

    * **No turn running** — re-raise ``KeyboardInterrupt`` so Ctrl-C at the
      prompt still leaves the REPL (unchanged behaviour).
    * **First Ctrl-C during a turn** — set the token. The turn unwinds at its
      next boundary and the REPL prompts again.
    * **Second Ctrl-C during the same turn** — re-raise, escalating past a
      handler that is ignoring the token. ``_repl`` catches it, so even this
      returns to the prompt rather than killing the process.

    Degrades to a no-op when the signal cannot be installed —
    ``signal.signal`` raises ``ValueError`` off the main thread, which is the
    normal case for an embedded or test-driven REPL.
    """
    previous: Any = None
    installed = False

    def _handler(_signum: int, _frame: Any) -> None:
        if not runtime.turn_active:
            raise KeyboardInterrupt
        if runtime.stopping:  # second Ctrl-C — escalate
            raise KeyboardInterrupt
        runtime.stop()
        try:
            output_fn("\n^C stopping the current turn... (Ctrl-C again to force)")
        except Exception:  # noqa: BLE001 — a signal handler must not raise
            pass

    try:
        previous = signal.signal(signal.SIGINT, _handler)
        installed = True
    except (ValueError, OSError, RuntimeError) as exc:
        # Not the main thread, or a platform without an installable SIGINT.
        logger.debug("agent_runtime: SIGINT handler not installed: %s", exc)

    try:
        yield installed
    finally:
        if installed:
            try:
                signal.signal(signal.SIGINT, previous)
            except (ValueError, OSError, RuntimeError) as exc:  # pragma: no cover
                logger.debug("agent_runtime: SIGINT handler not restored: %s", exc)


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
