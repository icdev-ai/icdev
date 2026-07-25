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
    """Show durable profile facts, remember a new one, or forget one (sag-mem-01).

    Usage: /memory | /memory forget <N|text> | /memory remember <fact text>
    """
    from tools.agent_runtime.profile_memory import (
        forget_fact,
        list_facts,
        remember_facts,
    )

    user_id = getattr(runtime, "user_id", "default")
    tenant_id = getattr(runtime, "tenant_id", "")
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "forget":
        if not rest:
            return "Usage: /memory forget <N|text>", False
        removed = forget_fact(rest, user_id=user_id, tenant_id=tenant_id)
        if removed is None:
            return f"No fact matched {rest!r}.", False
        # Re-inject the (now-updated) profile on the next turn.
        if hasattr(runtime, "_profile_preamble"):
            runtime._profile_preamble = None
        return f"Forgot: {removed}", False

    if sub == "remember":
        if not rest:
            return "Usage: /memory remember <fact text>", False
        n = remember_facts([{"text": rest, "confidence": 0.9, "source": "manual"}],
                           user_id=user_id, tenant_id=tenant_id)
        if hasattr(runtime, "_profile_preamble"):
            runtime._profile_preamble = None
        return ("Remembered." if n else "Already known."), False

    facts = list_facts(user_id=user_id, tenant_id=tenant_id)
    if not facts:
        return (
            "No remembered facts yet. Use '/memory remember <fact>' to add one, "
            "or they are captured automatically from your sessions.",
            False,
        )
    lines = [f"Remembered facts ({len(facts)}):"]
    for i, f in enumerate(facts[:20], start=1):
        conf = f.get("confidence", 0)
        lines.append(f"  {i}. {f.get('text', '')}  (confidence {conf:.2f}, {f.get('source', '?')})")
    lines.append("Use '/memory forget <N>' to remove one.")
    return "\n".join(lines), False


def _cmd_usage(runtime: Any, _arg: str) -> "tuple[str, bool]":
    u = runtime.session.usage()
    return (
        f"Usage — turns: {u['turns']}, input: {u['input_tokens']}, "
        f"output: {u['output_tokens']}, total: {u['total_tokens']} tokens, "
        f"cost: ${u['cost_usd']:.6f} (session {u['session_id'] or 'n/a'})",
        False,
    )


def _cmd_search(_runtime: Any, arg: str) -> "tuple[str, bool]":
    """Full-text search past session turns. Usage: /search <query> (sag-rt-04).

    Surfaces indexed turns with the context id so a match can be resumed via
    ``icdev chat --resume <ctx-id>``.
    """
    query = arg.strip()
    if not query:
        return "Usage: /search <query>", False
    from tools.agent_runtime.sessions import search_sessions

    results = search_sessions(query, limit=10)
    if not results:
        return f"No matches for {query!r}.", False
    lines = [f"{len(results)} match(es) for {query!r}:"]
    for i, r in enumerate(results, start=1):
        ctx = r.get("context_id") or "?"
        snippet = " ".join((r.get("content") or "").split())[:80]
        lines.append(f"  {i}. [{r.get('type', '')}] {snippet}  (resume: {ctx})")
    lines.append("Resume with: icdev chat --resume <ctx-id>")
    return "\n".join(lines), False


def _cmd_snapshot(_runtime: Any, arg: str) -> "tuple[str, bool]":
    """Manual checkpoint of one or more repo-relative paths. Usage: /snapshot <path> [path...]"""
    from tools.agent_runtime.checkpoints import create_checkpoint

    paths = [p for p in arg.split() if p.strip()]
    if not paths:
        return "Usage: /snapshot <path> [more paths...]", False
    cp = create_checkpoint(paths, label="manual /snapshot")
    return f"Checkpoint {cp.id} created ({len(cp.files)} path(s)).", False


def _cmd_rollback(_runtime: Any, arg: str) -> "tuple[str, bool]":
    """Roll back to a checkpoint. Usage: /rollback [N|<id>] (N=1 is newest)."""
    from tools.agent_runtime.checkpoints import (
        list_checkpoints,
        describe_changes,
        rollback,
    )

    tokens = arg.split()
    confirmed = "--yes" in tokens or "-y" in tokens
    positional = [t for t in tokens if not t.startswith("-")]
    sub = positional[0] if positional else ""

    cps = list_checkpoints()
    if not cps:
        return "No checkpoints available.", False

    # list mode
    if sub in ("", "list", "ls"):
        lines = ["Checkpoints (newest first):"]
        for i, cp in enumerate(cps[:10], start=1):
            lines.append(f"  {i}. {cp.id}  {cp.label}  ({len(cp.files)} path(s))")
        lines.append("Use /rollback <N> to preview, /rollback <N> --yes to apply.")
        return "\n".join(lines), False

    # resolve target (N is 1-based newest-first, or an explicit id)
    target = None
    if sub.isdigit():
        idx = int(sub) - 1
        if 0 <= idx < len(cps):
            target = cps[idx]
    else:
        target = next((c for c in cps if c.id == sub), None)
    if target is None:
        return f"No checkpoint matches {sub!r}. Try /rollback list.", False

    changes = describe_changes(target)
    if not changes:
        return f"Checkpoint {target.id}: nothing to restore.", False

    preview = "\n".join(f"  - {c}" for c in changes)
    if not confirmed:
        # Show what will change and require explicit confirmation (no mutation yet).
        return (
            f"Rollback of {target.id} ({target.label}) would make these changes:\n"
            f"{preview}\n\nRe-run '/rollback {sub} --yes' to apply.",
            False,
        )

    result = rollback(target.id, confirm=lambda _c: True)
    if result.get("ok"):
        undo = result.get("undo_checkpoint")
        return (
            f"Rolled back to {target.id}. Applied "
            f"{len(result.get('applied', []))} change(s):\n{preview}\n"
            f"(undo with /rollback {undo} --yes)",
            False,
        )
    return f"Rollback failed: {result.get('reason')}", False


def _cmd_skill(runtime: Any, arg: str) -> "tuple[str, bool]":
    """Propose / review / promote auto-generated skills (sag-skl-01, HITL).

    Usage:
      /skill propose <pattern>   Draft + queue a skill proposal (quarantined)
      /skill list [status]       List proposals (default: pending)
      /skill approve <id>        Promote an approved proposal to .agents/skills/
      /skill reject <id> [why]   Reject a quarantined proposal
    """
    from tools.agent_runtime import skills_lifecycle as sl

    parts = arg.split(maxsplit=1)
    sub = (parts[0].lower() if parts else "list")
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        if sub == "propose":
            if not rest:
                return "Usage: /skill propose <pattern>", False
            sid = getattr(runtime.session, "context_id", "")
            res = sl.propose_skill(rest, session_id=sid, model=getattr(runtime, "llm_function", ""))
            if res.get("proposed"):
                return f"Proposed '{res.get('skill_name')}' (id {res.get('skill_id')}) — quarantined pending review.", False
            return f"Not proposed: {res.get('reason') or res.get('error') or 'unknown'}.", False
        if sub == "list":
            props = sl.list_proposals(rest or "pending")
            if not props:
                return "No proposals.", False
            lines = [f"{len(props)} proposal(s):"]
            for p in props:
                lines.append(f"  {p['artifact_id']}  [{p['status']}]  {p['skill_name']}")
            return "\n".join(lines), False
        if sub == "approve":
            if not rest:
                return "Usage: /skill approve <artifact_id>", False
            res = sl.approve_proposal(rest.split()[0], approver="sag-operator")
            if res.get("approved"):
                return f"Promoted {res['name']} → {res['skill_dir']}", False
            return f"Approve failed: {res.get('error')}", False
        if sub == "reject":
            if not rest:
                return "Usage: /skill reject <artifact_id> [reason]", False
            bits = rest.split(maxsplit=1)
            ok = sl.reject_proposal(bits[0], approver="sag-operator", reason=(bits[1] if len(bits) > 1 else ""))
            return ("Rejected." if ok else "No such proposal."), False
        return "Usage: /skill propose|list|approve|reject ...", False
    except Exception as exc:  # noqa: BLE001
        logger.warning("commands: /skill failed: %s", exc)
        return f"error: {exc}", False


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
    "/skill": Command(_cmd_skill, "Propose/review/promote auto-skills (HITL). Usage: /skill propose|list|approve|reject ..."),
    "/memory": Command(_cmd_memory, "Show/remember/forget durable facts. Usage: /memory [forget <N>|remember <fact>]"),
    "/usage": Command(_cmd_usage, "Show token/cost stats for this session."),
    "/search": Command(_cmd_search, "Search past session turns. Usage: /search <query>"),
    "/snapshot": Command(_cmd_snapshot, "Checkpoint paths now. Usage: /snapshot <path> [more...]"),
    "/rollback": Command(_cmd_rollback, "Preview/apply a rollback. Usage: /rollback [N|id] [--yes]"),
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
