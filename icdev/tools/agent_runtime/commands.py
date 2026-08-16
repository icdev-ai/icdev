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

Command set (kept in step with :data:`REGISTRY` —
``tests/agent_runtime/test_goal_commands.py::test_docstring_matches_registry``
asserts every registered command is documented here, because a drifted docstring
is how a shipped command stays invisible). Every entry below is a live handler;
none of them is a stub:

- ``/new [title]``  — start a fresh session (new chat context).
- ``/clear``        — alias for ``/new`` that keeps the current title.
- ``/title [name]`` — show or set the session title.
- ``/tools``        — list the currently discovered tools.
- ``/skills``       — list ``icdev-*`` skills from ``tools/skills/registry.py``.
- ``/skill``        — propose / review / promote auto-generated skills (HITL).
- ``/memory``       — show, remember or forget durable profile facts.
- ``/goal``         — manage standing goals; the active ones are injected into
  the system prompt (hgx-goal-02).
- ``/usage``        — token / cost stats for the current session, and the
  permission posture in force.
- ``/posture``      — show or select the permission posture; selecting one
  records the decision in ``agent_session_events`` (hcx-post-02).
- ``/fork``         — branch a new session from this one at a ``seq`` in the
  append-only event log (hcx-evt-05); with no argument it lists the boundaries
  a fork is legal at rather than choosing one.
- ``/search``       — full-text search across past session turns.
- ``/snapshot``     — checkpoint repo paths before a risky edit.
- ``/rollback``     — preview or apply a rollback to a checkpoint.
- ``/help`` (``/?``) — list commands.
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
    line = (
        f"Usage — turns: {u['turns']}, input: {u['input_tokens']}, "
        f"output: {u['output_tokens']}, total: {u['total_tokens']} tokens, "
        f"cost: ${u['cost_usd']:.6f} (session {u['session_id'] or 'n/a'})"
    )
    return f"{line}\n{_posture_line()}", False


def _posture_line() -> str:
    """One line naming the posture in force — shown by ``/usage`` (hcx-post-02).

    A posture nobody can see at runtime is a posture nobody checks, which is how
    "the agent is confined" becomes an assumption rather than an observation. It
    rides on ``/usage`` because that is the command an operator already types to
    ask what this session is costing them, and a knob that widened is exactly
    the kind of thing they want in the same answer.

    Never raises: this is a suffix on an unrelated command, and a config layer
    that cannot load must not take ``/usage`` down with it.
    """
    try:
        from tools.agent_runtime.posture_selection import describe

        report = describe()
        knobs = ", ".join(f"{k}={v!r}" for k, v in report["knobs"].items())
        line = f"Posture — {report['posture']} (by {report['source']}): {knobs}"
        if report["pinned"]:
            held = ", ".join(f"{k} by {v}" for k, v in report["pinned"].items())
            line += f"\n  NOT set by the posture: {held}"
        return line + "\n  Change it with /posture <name>."
    except Exception as exc:  # noqa: BLE001
        logger.debug("commands: posture line unavailable: %s", exc)
        return "Posture — unavailable (the config layer did not load)."


def _cmd_posture(runtime: Any, arg: str) -> "tuple[str, bool]":
    """Show or select the permission posture (hcx-post-02).

    Usage:
      /posture                 Show the posture in force and its four knobs
      /posture list            List selectable postures
      /posture <name>          Select it, recording the decision

    Selecting appends a ``permission_posture`` event to ``agent_session_events``
    naming this operator, the posture and the resolved knobs — and only then
    moves anything. Re-selecting the posture already in force records nothing.
    """
    from tools.agent_runtime.posture_selection import (
        available_postures,
        describe,
        select_posture,
    )

    sub = arg.strip()

    if not sub:
        report = describe()
        lines = [f"Posture: {report['posture']} (selected by: {report['source']})"]
        for key, value in report["knobs"].items():
            holder = report["pinned"].get(key)
            note = f"   <- pinned by {holder}, not by the posture" if holder else ""
            lines.append(f"  {key:<22} {value!r}{note}")
        lines.append("Select with '/posture <name>', or list them with '/posture list'.")
        return "\n".join(lines), False

    if sub.lower() in ("list", "ls"):
        lines = ["Selectable postures:"]
        for name, meta in available_postures().items():
            flag = "  [explicit selection only]" if meta["requires_explicit_selection"] else ""
            lines.append(f"  {name}{flag}")
            if meta["description"]:
                lines.append(f"      {meta['description']}")
        return "\n".join(lines), False

    result = select_posture(
        sub.split()[0],
        actor=str(getattr(runtime, "user_id", "") or "unknown"),
        session_id=str(getattr(getattr(runtime, "session", None), "context_id", "") or ""),
        tenant_id=getattr(runtime, "tenant_id", "") or None,
    )
    return result.summary(), False


def _cmd_fork(runtime: Any, arg: str) -> "tuple[str, bool]":
    """Branch a new session from this one at a seq (hcx-evt-05).

    Usage:
      /fork                    List the seqs this session may be forked at
      /fork <seq>              Fork here and switch to the new session
      /fork <seq> | <title>    …with a title

    The boundary is resolved against ``agent_session_events``, never against the
    stored message blob, and one that lands inside an open turn is REFUSED
    rather than rounded to a turn boundary: a prefix ending mid-turn produces a
    ``tool_use`` with no ``tool_result``, which the next provider call rejects.

    Switching is the point of doing this in the REPL — after a fork the session
    in front of the operator IS the branch, so the next thing they type goes to
    it. The parent is untouched and still resumable by its own id.
    """
    from tools.agent_runtime.event_log import EventLogUnavailable
    from tools.agent_runtime.fork import ForkRefused, describe, fork_session

    session_id = str(getattr(getattr(runtime, "session", None), "context_id", "") or "")
    if not session_id:
        return "No current session to fork.", False

    ref, _sep, title = arg.partition("|")
    ref = ref.strip()

    try:
        if not ref:
            report = describe(session_id)
            legal = report["legal_boundaries"]
            lines = [
                f"Session {session_id}: {report['events']} event(s), "
                f"{report['turns']} turn(s), max seq {report['max_seq']}.",
                "Legal fork boundaries: "
                + (", ".join(str(s) for s in legal) if legal else "(none yet)"),
            ]
            if report["open_turn"]:
                lines.append(
                    f"A turn is open from seq {report['open_turn']} — it cannot be "
                    "forked until it ends."
                )
            if report["withheld"]:
                lines.append(
                    f"{len(report['withheld'])} event(s) have withheld payloads and "
                    "cannot be projected (args/agent_event_log.yaml)."
                )
            lines.append("Fork with '/fork <seq>'.")
            return "\n".join(lines), False

        if not ref.lstrip("-").isdigit():
            return "Usage: /fork [<seq>] — the boundary is a seq, not a turn number.", False

        result = fork_session(
            session_id,
            int(ref),
            title=title.strip(),
            user_id=getattr(runtime, "user_id", None),
            tenant_id=getattr(runtime, "tenant_id", None),
            actor=str(getattr(runtime, "user_id", "") or ""),
        )
    except ForkRefused as exc:
        return f"Refused ({exc.reason}): {exc}", False
    except (EventLogUnavailable, ValueError) as exc:
        return f"Cannot fork: {exc}", False

    try:
        runtime.resume_session(result.context_id)
    except Exception as exc:  # noqa: BLE001 — the fork exists; only the switch failed
        return (
            f"{result.summary()}\nThe fork was created but could not be opened "
            f"({exc}). Resume it with: icdev chat --resume {result.context_id}",
            False,
        )
    return f"{result.summary()}\nYou are now in the fork.", False


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


# ---------------------------------------------------------------------------
# /goal (hgx-goal-02)
# ---------------------------------------------------------------------------

#: Statuses a goal can still be acted on from. This ordering is the *canonical*
#: one: ``/goal list`` prints it and the ``N`` references every subcommand
#: accepts index into it, so "pause 2" always means the second line just shown.
_LIVE_FILTER = ("active", "blocked", "paused", "pending")

_GOAL_USAGE = (
    "Usage: /goal create <title> [| detail] [--priority=N] | list [status|all] | "
    "status [N|id] | pause|resume|complete|cancel <N|id> | block <N|id> [reason] "
    "| clear [--yes]"
)


def _goal_manager(runtime: Any) -> Any:
    """A GoalManager scoped to this runtime's operator + tenant."""
    from tools.agent_runtime.standing_goals import GoalManager

    return GoalManager(
        user_id=getattr(runtime, "user_id", "default"),
        tenant_id=getattr(runtime, "tenant_id", ""),
    )


def _invalidate_goals(runtime: Any) -> None:
    """Drop the runtime's cached goal preamble so the next turn re-reads the DB.

    Called after every mutation. Without this a ``/goal create`` would not reach
    the model until the session was restarted — the goal would exist and be
    invisible, which is the failure this command set is meant to remove.
    """
    invalidate = getattr(runtime, "invalidate_goals", None)
    if callable(invalidate):
        invalidate()
    elif hasattr(runtime, "_goals_preamble"):
        runtime._goals_preamble = None


def _goal_line(index: int, goal: Any) -> str:
    bits = [f"  {index}. [{goal.status.value}]"]
    if goal.progress:
        bits.append(f"{goal.progress}%")
    bits.append(goal.title)
    line = " ".join(bits) + f"  ({goal.goal_id})"
    if goal.blocked_reason:
        line += f"\n       blocked: {goal.blocked_reason}"
    return line


def _resolve_goal(mgr: Any, ref: str, live: "list[Any]") -> Any:
    """Resolve ``ref`` to a goal: a 1-based index into ``live``, or an id/prefix.

    Indexes are offered because the ids are opaque (``goal-9f2c1a…``); prefixes
    because pasting a full one is worse. A prefix that matches more than one goal
    is rejected rather than guessed at — silently acting on the wrong goal is the
    one outcome this cannot risk.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        idx = int(ref) - 1
        return live[idx] if 0 <= idx < len(live) else None
    exact = mgr.get(ref)
    if exact is not None:
        return exact
    matches = [g for g in mgr.list_goals() if g.goal_id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


def _parse_create(arg: str) -> "tuple[str, str, int]":
    """Split ``create`` args into ``(title, detail, priority)``.

    ``|`` separates an optional detail; ``--priority=N`` may appear anywhere and
    is removed from the title. Priority matters because the injected block is
    capped — it is how an operator says which goals win the five slots.
    """
    priority = 50
    kept: list[str] = []
    for token in arg.split():
        if token.startswith("--priority="):
            try:
                priority = max(0, min(100, int(token.split("=", 1)[1])))
            except (IndexError, ValueError):
                pass
            continue
        kept.append(token)
    title, _sep, detail = " ".join(kept).partition("|")
    return title.strip(), detail.strip(), priority


def _cmd_goal(runtime: Any, arg: str) -> "tuple[str, bool]":
    """Manage standing goals — durable objectives injected into the prompt.

    Usage:
      /goal create <title> [| detail] [--priority=N]   Create and start pursuing
      /goal list [status|all]                          List goals (default: live)
      /goal status [N|id]                              What is injected, or one goal
      /goal pause|resume|complete|cancel <N|id>        Lifecycle moves
      /goal block <N|id> [reason]                      Mark blocked, recording why
      /goal clear [--yes]                              Cancel every live goal
    """
    from tools.agent_runtime.standing_goals import (
        GoalStatus,
        InvalidGoalTransition,
    )

    parts = arg.split(maxsplit=1)
    sub = (parts[0].lower() if parts else "list")
    rest = parts[1].strip() if len(parts) > 1 else ""

    mgr = _goal_manager(runtime)
    # Resolved once so an index shown by /goal list means the same thing here.
    live = mgr.list_goals(statuses=_LIVE_FILTER)

    if sub in ("create", "add", "new"):
        title, detail, priority = _parse_create(rest)
        if not title:
            return "Usage: /goal create <title> [| detail] [--priority=N]", False
        # Created ACTIVE: an operator who types the goal out has decided to
        # pursue it. A 'pending' default would make every create a two-step.
        goal = mgr.create(
            title,
            detail=detail,
            status=GoalStatus.ACTIVE,
            priority=priority,
            session_id=str(getattr(getattr(runtime, "session", None), "context_id", "")),
        )
        if goal is None:
            return "Could not create the goal (goal store unavailable).", False
        _invalidate_goals(runtime)
        return (
            f"Goal created and active: {goal.title} ({goal.goal_id}). "
            "It will be injected into the system prompt from the next turn.",
            False,
        )

    if sub in ("list", "ls"):
        wanted = None if rest.lower() in ("all", "*") else (
            (rest.lower(),) if rest else _LIVE_FILTER
        )
        goals = live if wanted == _LIVE_FILTER else mgr.list_goals(statuses=wanted)
        if not goals:
            scope = rest or "live"
            return f"No {scope} goals. Create one with '/goal create <title>'.", False
        lines = [f"{len(goals)} goal(s):"]
        lines.extend(_goal_line(i, g) for i, g in enumerate(goals, start=1))
        lines.append("Refer to a goal by its number or id, e.g. '/goal pause 1'.")
        return "\n".join(lines), False

    if sub == "status":
        if rest:
            goal = _resolve_goal(mgr, rest, live)
            if goal is None:
                return f"No goal matches {rest!r}. Try /goal list.", False
            out = [
                f"{goal.title}  ({goal.goal_id})",
                f"  status: {goal.status.value}   progress: {goal.progress}%   "
                f"priority: {goal.priority}",
            ]
            if goal.detail:
                out.append(f"  detail: {goal.detail}")
            if goal.blocked_reason:
                out.append(f"  blocked: {goal.blocked_reason}")
            out.append(f"  created: {goal.created_at or '?'}")
            return "\n".join(out), False
        return _goal_injection_status(runtime, mgr), False

    if sub == "clear":
        if not live:
            return "No live goals to clear.", False
        if "--yes" not in rest.split() and "-y" not in rest.split():
            preview = "\n".join(f"  - {g.title}" for g in live)
            return (
                f"/goal clear would cancel {len(live)} goal(s):\n{preview}\n\n"
                "Cancelling is terminal — re-run '/goal clear --yes' to apply.",
                False,
            )
        cancelled = 0
        for goal in live:
            try:
                if mgr.cancel(goal.goal_id) is not None:
                    cancelled += 1
            except InvalidGoalTransition:  # raced to terminal by another session
                continue
        _invalidate_goals(runtime)
        return f"Cancelled {cancelled} of {len(live)} goal(s).", False

    # Local (not module-level) so importing this module never needs the goal
    # subsystem — every other goal import here is lazy for the same reason.
    moves = {
        "pause": GoalStatus.PAUSED,
        "resume": GoalStatus.ACTIVE,
        "activate": GoalStatus.ACTIVE,
        "complete": GoalStatus.COMPLETED,
        "done": GoalStatus.COMPLETED,
        "cancel": GoalStatus.CANCELLED,
        "block": GoalStatus.BLOCKED,
    }
    target = moves.get(sub)
    if target is None:
        return _GOAL_USAGE, False

    ref, _sep, reason = rest.partition(" ")
    if not ref:
        return f"Usage: /goal {sub} <N|id>" + (" [reason]" if sub == "block" else ""), False
    goal = _resolve_goal(mgr, ref, live)
    if goal is None:
        return f"No goal matches {ref!r}. Try /goal list.", False
    try:
        updated = mgr.transition(
            goal.goal_id, target, blocked_reason=reason.strip()
        )
    except InvalidGoalTransition as exc:
        # A caller error, not a missing subsystem — report it, do not swallow it.
        return f"Cannot {sub} that goal: {exc}", False
    if updated is None:
        return f"Could not update {goal.goal_id} (goal store unavailable).", False
    _invalidate_goals(runtime)
    note = f" ({updated.blocked_reason})" if updated.blocked_reason else ""
    return f"Goal '{updated.title}' is now {updated.status.value}{note}.", False


def _goal_injection_status(runtime: Any, mgr: Any) -> str:
    """Summarise the board plus what is actually reaching the model.

    Reporting the *injected* count separately from the active count is the
    point: with more active goals than the cap, "you have 8 goals" and "the
    agent can see 5 of them" are different facts and the operator needs both.
    """
    from tools.agent_runtime import goal_context

    counts: dict[str, int] = {}
    for goal in mgr.list_goals():
        counts[goal.status.value] = counts.get(goal.status.value, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no goals yet"

    report = goal_context.describe(
        llm_function=getattr(runtime, "llm_function", "code_generation"),
        system_prompt=getattr(runtime, "system_prompt", ""),
        user_id=getattr(runtime, "user_id", "default"),
        tenant_id=getattr(runtime, "tenant_id", ""),
        context_id=str(getattr(getattr(runtime, "session", None), "context_id", "")),
        manager=mgr,
    )
    lines = [f"Goals: {summary}."]
    lines.append(
        f"Injected into the system prompt: {report['shown']} of "
        f"{report['total_active']} active (cap {report['limit']}, "
        f"{report['tokens']}/{report['budget']} tokens)."
    )
    if report["withheld"]:
        lines.append(
            f"  {report['withheld']} active goal(s) withheld — raise the cap with "
            f"{goal_context.ENV_LIMIT}, or re-prioritise with --priority=N."
        )
    return "\n".join(lines)


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
    "/goal": Command(_cmd_goal, "Manage standing goals injected into the prompt. Usage: /goal create|list|status|pause|resume|complete|block|cancel|clear ..."),
    "/usage": Command(_cmd_usage, "Show token/cost stats and the posture in force."),
    "/posture": Command(_cmd_posture, "Show or select the permission posture. Usage: /posture [list|<name>]"),
    "/fork": Command(_cmd_fork, "Branch this session at a seq. Usage: /fork [<seq>] [| title]"),
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
