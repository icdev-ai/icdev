# CUI // SP-CTI
"""Slash-command registry for the standalone agent runtime (SAG sag-rt-02).

Slash commands are handled deterministically **without re-prompting the LLM**.
The registry is data-driven — a dict of ``name -> Command(handler, help)`` — so
the gateway agent-mode (sag-gw-01) can reuse the exact same dispatcher behind the
Remote Command Gateway's security chain.

Each handler has signature ``handler(runtime, arg) -> tuple[str, bool]`` returning
``(response_text, should_exit)``. :func:`dispatch` adapts this to the
:data:`AgentRuntime.command_handler` contract ``(runtime, raw) ->
(handled, response, should_exit)`` and is injected via
``AgentRuntime(command_handler=dispatch)`` (see :func:`build_runtime`).

Command set:

- ``/new [title]``  — start a fresh session (new chat context).
- ``/clear``        — alias for ``/new`` that keeps the current title.
- ``/title [name]`` — show or set the session title.
- ``/tools``        — list the currently discovered tools.
- ``/skills``       — list ``icdev-*`` skills from ``tools/skills/registry.py``.
- ``/memory``       — show remembered facts (stub until sag-mem-01 lands).
- ``/usage``        — token / cost stats for the current session.
- ``/help`` (``/?``) — list commands.
- ``/rollback``     — checkpoint rollback (stub until sag-safe-02 lands).
- ``/exit`` (``/quit``) — graceful shutdown; the session is already persisted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.commands")

# handler(runtime, arg) -> (response_text, should_exit)
Handler = Callable[[Any, str], "tuple[str, bool]"]


@dataclass(frozen=True)
class Command:
    handler: Handler
    help: str


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _cmd_new(runtime: Any, arg: str) -> "tuple[str, bool]":
    title = arg.strip() or "Untitled session"
    runtime.new_session(title=title)
    return f"Started new session {runtime.session.context_id} (title: {title}).", False


def _cmd_clear(runtime: Any, _arg: str) -> "tuple[str, bool]":
    title = runtime.session.title
    runtime.new_session(title=title)
    return f"Cleared. New session {runtime.session.context_id}.", False


def _cmd_title(runtime: Any, arg: str) -> "tuple[str, bool]":
    name = arg.strip()
    if not name:
        return f"Current title: {runtime.session.title}", False
    runtime.session.set_title(name)
    return f"Title set to: {name}", False


def _cmd_tools(runtime: Any, _arg: str) -> "tuple[str, bool]":
    names = runtime.tool_names()
    if not names:
        return "No tools registered.", False
    return f"{len(names)} tools: " + ", ".join(names), False


def _cmd_skills(_runtime: Any, _arg: str) -> "tuple[str, bool]":
    try:
        from tools.skills.registry import load_registry

        reg = load_registry()
        skills = reg.get("skills", {}) or {}
        if not skills:
            return "No skills found.", False
        names = sorted(skills)
        return f"{len(names)} skills: " + ", ".join(names), False
    except Exception as exc:  # noqa: BLE001
        logger.warning("commands: /skills failed: %s", exc)
        return f"error listing skills: {exc}", False


def _cmd_memory(runtime: Any, arg: str) -> "tuple[str, bool]":
    # Full profile-memory integration lands in sag-mem-01. Until then, degrade
    # to a useful view of the current session's recent transcript.
    sub = arg.strip().lower()
    if sub.startswith("forget"):
        return "Memory not enabled yet (arrives with sag-mem-01).", False
    msgs = runtime.session.messages(limit=10)
    if not msgs:
        return (
            "No remembered facts yet. Persistent user-profile memory arrives "
            "with sag-mem-01; showing session transcript only.",
            False,
        )
    lines = [f"  [{m.get('role')}] {str(m.get('content', ''))[:120]}" for m in msgs]
    return "Recent session transcript (profile memory arrives in sag-mem-01):\n" + "\n".join(lines), False


def _cmd_usage(runtime: Any, _arg: str) -> "tuple[str, bool]":
    u = runtime.session.usage()
    return (
        f"Usage — turns: {u['turns']}, input: {u['input_tokens']}, "
        f"output: {u['output_tokens']}, total: {u['total_tokens']} tokens, "
        f"cost: ${u['cost_usd']:.6f} (session {u['session_id'] or 'n/a'})",
        False,
    )


def _cmd_rollback(_runtime: Any, _arg: str) -> "tuple[str, bool]":
    return "Checkpoints not enabled (arrives with sag-safe-02).", False


def _cmd_exit(_runtime: Any, _arg: str) -> "tuple[str, bool]":
    return "Session saved. Goodbye.", True


def _cmd_help(_runtime: Any, _arg: str) -> "tuple[str, bool]":
    lines = ["Available commands:"]
    seen: set[int] = set()
    for name, cmd in REGISTRY.items():
        # Skip aliases (same Command object already listed).
        if id(cmd) in seen:
            continue
        seen.add(id(cmd))
        lines.append(f"  {name:<12} {cmd.help}")
    return "\n".join(lines), False


# ---------------------------------------------------------------------------
# Registry (data-driven; aliases share a Command object)
# ---------------------------------------------------------------------------

_new_cmd = Command(_cmd_new, "Start a fresh session. Usage: /new [title]")
_exit_cmd = Command(_cmd_exit, "Save the session and exit.")
_help_cmd = Command(_cmd_help, "List available commands.")

REGISTRY: dict[str, Command] = {
    "/new": _new_cmd,
    "/clear": Command(_cmd_clear, "Clear the conversation, keeping the title."),
    "/title": Command(_cmd_title, "Show or set the session title. Usage: /title [name]"),
    "/tools": Command(_cmd_tools, "List the currently discovered tools."),
    "/skills": Command(_cmd_skills, "List available icdev-* skills."),
    "/memory": Command(_cmd_memory, "Show remembered facts (full support in sag-mem-01)."),
    "/usage": Command(_cmd_usage, "Show token/cost stats for this session."),
    "/rollback": Command(_cmd_rollback, "Roll back to a checkpoint (sag-safe-02)."),
    "/help": _help_cmd,
    "/?": _help_cmd,
    "/exit": _exit_cmd,
    "/quit": _exit_cmd,
}


def dispatch(runtime: Any, raw: str) -> "tuple[bool, str, bool]":
    """Dispatch a raw ``/command`` line.

    Matches the :data:`AgentRuntime.command_handler` contract. Returns
    ``(handled, response_text, should_exit)``. Unknown commands are still
    "handled" (they never fall through to the LLM) and return a hint.
    """
    parts = raw.strip().split(maxsplit=1)
    if not parts:
        return True, "", False
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    cmd = REGISTRY.get(name)
    if cmd is None:
        return True, f"Unknown command {name!r}. Try /help.", False
    try:
        response, should_exit = cmd.handler(runtime, arg)
    except Exception as exc:  # noqa: BLE001 — never let a command crash the REPL
        logger.exception("commands: handler for %s failed", name)
        return True, f"error running {name}: {exc}", False
    return True, response, should_exit


def command_names() -> list[str]:
    """Return the registered command names (primary + aliases), sorted."""
    return sorted(REGISTRY)


def build_runtime(**kwargs: Any) -> Any:
    """Construct an :class:`AgentRuntime` wired to this full command registry."""
    from tools.agent_runtime.runtime import AgentRuntime

    kwargs.setdefault("command_handler", dispatch)
    return AgentRuntime(**kwargs)
