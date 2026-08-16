# CUI // SP-CTI
"""`icdev chat` / `icdev sessions` — CLI surface for the standalone agent runtime.

Wires the sag-rt-01 :class:`AgentRuntime` and the sag-rt-02 slash-command
registry into the ``icdev`` dispatcher (and ``python -m tools.agent_runtime``)
so an operator can hold a persistent, resumable conversation from a plain shell —
no Claude Code, no web session.

Subcommands::

    icdev chat                     Interactive REPL (full slash-command set).
    icdev chat -q "query"          Single-shot: run one turn, print, exit.
    icdev chat --resume <ctx-id>   Resume an existing conversation.
    icdev chat --fork <ctx-id> --at <seq>
                                   Branch a NEW session from that one's state at
                                   ``seq`` in ``agent_session_events`` (hcx-evt-05).
                                   Without --at, the legal boundaries are listed.
    icdev sessions list            List this user's conversations (newest first).
    icdev sessions export <ctx-id> Export a transcript as JSONL.

No new storage or LLM path is introduced: conversations live in the canonical
``chat_contexts`` / ``chat_messages`` tables (auto-provisioned on first use via
:func:`tools.agent_runtime.sessions.ensure_chat_tables`) and every turn flows
through :func:`icdev.tools.llm.agent_loop.run_agent_loop` behind the provider
abstraction.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


# ---------------------------------------------------------------------------
# icdev chat
# ---------------------------------------------------------------------------


def chat_main(argv: list[str] | None = None) -> int:
    """Handle ``icdev chat [...]``."""
    parser = argparse.ArgumentParser(
        prog="icdev chat",
        description="Interactive or single-shot ICDEV standalone agent session.",
    )
    parser.add_argument(
        "-q", "--query", help="Run a single turn with this query, print it, and exit."
    )
    parser.add_argument(
        "--resume",
        metavar="CTX_ID",
        help="Resume an existing conversation by its chat context id.",
    )
    parser.add_argument(
        "--fork",
        metavar="CTX_ID",
        help=(
            "Branch a NEW session from an existing one at --at, seeded from that "
            "session's agent_session_events prefix. Without --at, the legal fork "
            "boundaries are printed and nothing is created."
        ),
    )
    parser.add_argument(
        "--at",
        type=int,
        metavar="SEQ",
        help=(
            "With --fork: the inclusive boundary seq. Resolved against the event "
            "log, never rounded — a boundary inside an open turn is refused."
        ),
    )
    parser.add_argument("--user", default=None, help="Operator user id (RLS).")
    parser.add_argument("--tenant", default=None, help="Tenant slug (RLS).")
    parser.add_argument(
        "--json",
        action="store_true",
        help="For single-shot (-q): emit the result as JSON.",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Suppress the interactive banner.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream tokens as they arrive (tool-free conversational turns).",
    )
    parser.add_argument(
        "--unattended",
        action="store_true",
        help=(
            "Deliver this session's approval asks to the approval inbox instead "
            "of this console. ROUTING ONLY — it does not widen what the agent may "
            "do, downgrade any tier, or auto-approve anything; an irreversible "
            "call still halts, on a durable pending item instead of on EOF. The "
            "flag is persisted against the session, so --resume keeps it."
        ),
    )
    parser.add_argument(
        "--attended",
        action="store_true",
        help=(
            "Explicitly route asks back to this console, overriding a flag stored "
            "against a resumed session."
        ),
    )
    parser.add_argument(
        "--unattended-reason",
        default="",
        help="With --unattended: the reason recorded against the session.",
    )
    args = parser.parse_args(argv)

    if args.unattended and args.attended:
        print(
            "icdev chat: --unattended and --attended are mutually exclusive.",
            file=sys.stderr,
        )
        return 2

    if args.fork and args.resume:
        print(
            "icdev chat: --fork and --resume are mutually exclusive — one "
            "continues a session, the other branches a new one from it.",
            file=sys.stderr,
        )
        return 2
    if args.at is not None and not args.fork:
        print("icdev chat: --at is only meaningful with --fork.", file=sys.stderr)
        return 2

    # Master switch (hgx-cfg-01): `icdev disable sag` / ICDEV_SAG_ENABLED=false.
    # Refuse up front rather than starting a runtime the operator turned off —
    # and name both the flag and the file, so the message is actionable.
    try:
        from tools.agent_runtime.config import load_config

        if not load_config().enabled:
            print(
                "icdev chat: the standalone agent runtime is disabled "
                "(ICDEV_SAG_ENABLED / `enabled` in args/agent_runtime.yaml). "
                "Enable it with `icdev enable sag`.",
                file=sys.stderr,
            )
            return 2
    except Exception:  # noqa: BLE001 — an unreadable config never blocks the CLI
        pass

    from tools.agent_runtime.sessions import ensure_chat_tables

    ensure_chat_tables()

    from tools.agent_runtime.commands import dispatch as _command_dispatch
    from tools.agent_runtime.runtime import AgentRuntime

    # icdev chat is a short-lived process, so it is safe to apply the active
    # profile's dotenv overlay into this process (sag-prof-01).
    kwargs: dict[str, Any] = {
        "command_handler": _command_dispatch,
        "apply_profile_env": True,
    }
    if args.user:
        kwargs["user_id"] = args.user
    if args.tenant is not None:
        kwargs["tenant_id"] = args.tenant

    runtime = AgentRuntime(**kwargs)

    if args.resume:
        try:
            runtime.resume_session(args.resume)
        except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
            print(f"icdev chat: cannot resume {args.resume!r}: {exc}", file=sys.stderr)
            return 2

    if args.fork:
        rc = _apply_fork(runtime, args)
        if rc != 0:
            return rc

    # agov-inbox-04. Resolved AFTER --resume, because the flag is keyed on the
    # session and a resumed session carries the one it was started with.
    if _apply_unattended(runtime, args) != 0:
        return 2

    if args.query:
        return _single_shot(runtime, args)

    runtime.loop(banner=not args.no_banner, stream=args.stream)
    return 0


def _apply_fork(runtime: Any, args: argparse.Namespace) -> int:
    """Branch a new session from ``--fork`` at ``--at`` and bind the runtime to it.

    Three properties, in this order:

    1. **No ``--at`` is a survey, not a default.** The legal boundaries are
       printed and NOTHING is created. Picking one — the last turn, say — would
       be this CLI choosing a fork point on the operator's behalf, and a fork at
       the wrong seq is indistinguishable from a fork at the right one until the
       new session answers the wrong question.
    2. **A refusal is actionable.** ``ForkRefused`` names the reason and the
       legal boundaries either side, and both are printed verbatim.
    3. **Everything goes to stderr.** ``-q --json`` writes a machine-readable
       result to stdout; a fork banner on the same stream would break it.

    Returns 0 to continue into the session, non-zero to abort.
    """
    from tools.agent_runtime.event_log import EventLogUnavailable
    from tools.agent_runtime.fork import ForkRefused, describe, fork_session

    if args.at is None:
        try:
            report = describe(args.fork)
        except (EventLogUnavailable, ValueError) as exc:
            print(f"icdev chat: cannot survey {args.fork!r}: {exc}", file=sys.stderr)
            return 2
        legal = report["legal_boundaries"]
        print(
            f"icdev chat: --fork needs --at. Session {args.fork} holds "
            f"{report['events']} event(s) across {report['turns']} turn(s).",
            file=sys.stderr,
        )
        print(
            "  legal boundaries: "
            + (", ".join(str(s) for s in legal) if legal else "(none yet)"),
            file=sys.stderr,
        )
        if report["open_turn"]:
            print(
                f"  a turn is open from seq {report['open_turn']} and cannot be "
                "forked until it ends.",
                file=sys.stderr,
            )
        return 2

    try:
        result = fork_session(
            args.fork,
            args.at,
            user_id=args.user,
            tenant_id=args.tenant,
            actor=args.user or "",
        )
    except ForkRefused as exc:
        print(f"icdev chat: fork refused ({exc.reason}): {exc}", file=sys.stderr)
        return 2
    except (EventLogUnavailable, ValueError) as exc:
        print(f"icdev chat: cannot fork {args.fork!r}: {exc}", file=sys.stderr)
        return 2

    try:
        runtime.resume_session(result.context_id)
    except Exception as exc:  # noqa: BLE001 — the fork exists; binding to it failed
        print(
            f"icdev chat: the fork was created as {result.context_id} but could "
            f"not be opened ({exc}). Resume it with "
            f"`icdev chat --resume {result.context_id}`.",
            file=sys.stderr,
        )
        return 2

    if not args.no_banner:
        print(result.summary(), file=sys.stderr)
    else:
        # --no-banner suppresses the greeting, never a warning: every entry here
        # is something the fork could not do (an unwritten seed, a missing
        # provenance event), and a silent one leaves the operator with a session
        # that is quietly less than it looks.
        for warning in result.warnings:
            print(f"icdev chat: fork warning: {warning}", file=sys.stderr)
    return 0


def _apply_unattended(runtime: Any, args: argparse.Namespace) -> int:
    """Resolve, persist and apply the session's unattended flag (agov-inbox-04).

    Three properties, in this order:

    1. **Explicit only.** ``--unattended`` / ``--attended`` are the only things
       that *set* the flag here. Neither the absence of a TTY nor a redirected
       stdin is consulted — see :mod:`tools.agent_runtime.unattended`.
    2. **Persisted.** An explicit flag is written against the session id, so
       ``icdev chat --resume <ctx>`` after a crash or a reboot resumes with the
       same routing instead of silently reverting to a console approver that
       denies on EOF.
    3. **Fails closed and loudly.** A flag the operator asked for that could not
       be stored would produce a session that refuses everything for no visible
       reason, so the CLI refuses to start instead.

    Returns 0 to continue, non-zero to abort.
    """
    from tools.agent_runtime.unattended import (
        SOURCE_CLI,
        UnattendedStoreUnavailable,
        resolve,
        set_unattended,
    )

    session_id = getattr(getattr(runtime, "session", None), "context_id", "") or ""
    explicit: bool | None = True if args.unattended else (False if args.attended else None)

    if explicit is not None:
        try:
            set_unattended(
                session_id,
                explicit,
                reason=args.unattended_reason,
                source=SOURCE_CLI,
            )
        except (UnattendedStoreUnavailable, ValueError) as exc:
            print(
                f"icdev chat: --{'unattended' if explicit else 'attended'} could "
                f"not be recorded ({exc}). Refusing to start: a session whose "
                "routing was not persisted would deny every irreversible action "
                "with no visible cause.",
                file=sys.stderr,
            )
            return 2

    state = resolve(session_id, unattended=explicit)
    runtime.unattended = state.unattended
    if state.unattended and not args.no_banner:
        print(
            "Unattended: approval asks for this session go to the approval inbox "
            "(`python tools/agent_runtime/approval_inbox.py --list --state pending`). "
            "Routing only — irreversible actions still require a human.",
            file=sys.stderr,
        )
    return 0


#: POSIX convention for "terminated by SIGINT" (128 + 2), used by the
#: single-shot path so a script can tell a stop from a normal completion.
_EXIT_INTERRUPTED = 130


def _single_shot(runtime: Any, args: argparse.Namespace) -> int:
    """Run one turn for ``icdev chat -q``, honouring Ctrl-C.

    There is no prompt to return to here, so an interrupt ends the process —
    but it ends it *cleanly*: the turn stops at its next boundary, whatever the
    agent produced so far is printed and persisted, and the exit code is
    :data:`_EXIT_INTERRUPTED` instead of a traceback.
    """
    from tools.agent_runtime.runtime import install_interrupt_handler

    interrupted = False
    with install_interrupt_handler(runtime, lambda msg: print(msg, file=sys.stderr)):
        try:
            if args.stream and not args.json:
                def _emit(delta: str) -> None:
                    sys.stdout.write(delta)
                    sys.stdout.flush()

                text = runtime.stream_turn(args.query, on_delta=_emit)
                if not text.endswith("\n"):
                    print()
                return _EXIT_INTERRUPTED if runtime.stopping else 0
            result = runtime.run_turn(args.query)
        except KeyboardInterrupt:  # second Ctrl-C — escalated past the token
            print("\nInterrupted.", file=sys.stderr)
            return _EXIT_INTERRUPTED
        interrupted = getattr(result, "truncation_reason", "") == "stop_event"

    text = getattr(result, "final_content", "") or ""
    if args.json:
        print(
            json.dumps(
                {
                    "context_id": runtime.session.context_id,
                    "response": text,
                    "usage": runtime.session.usage(),
                    "stopped": interrupted,
                },
                indent=2,
            )
        )
    else:
        print(text or ("(stopped)" if interrupted else "(no response)"))
    return _EXIT_INTERRUPTED if interrupted else 0


# ---------------------------------------------------------------------------
# icdev sessions
# ---------------------------------------------------------------------------


def _chat_manager(user: str | None, tenant: str | None):
    from tools.agent_runtime.sessions import _default_tenant_id, _default_user_id
    from tools.chat.chat_manager import ChatManager

    return ChatManager(_default_user_id(user), _default_tenant_id(tenant))


def _sessions_list(args: argparse.Namespace) -> int:
    from tools.agent_runtime.sessions import ensure_chat_tables

    ensure_chat_tables()
    mgr = _chat_manager(args.user, args.tenant)
    status = None if args.status in ("all", "*") else args.status
    rows = mgr.list_contexts(status=status, limit=args.limit)
    if args.json:
        print(json.dumps({"count": len(rows), "sessions": rows}, indent=2, default=str))
        return 0
    if not rows:
        print("No sessions.")
        return 0
    print(f"{len(rows)} session(s):")
    for r in rows:
        cid = r.get("id", "")
        title = r.get("title") or "(untitled)"
        st = r.get("status", "")
        msgs = r.get("message_count", 0)
        last = r.get("last_activity_at") or r.get("updated_at") or ""
        print(f"  {cid:<20} [{st:<9}] {msgs:>3} msg  {last:<20}  {title}")
    return 0


def _sessions_export(args: argparse.Namespace) -> int:
    from tools.agent_runtime.sessions import ensure_chat_tables

    ensure_chat_tables()
    mgr = _chat_manager(args.user, args.tenant)
    ctx = mgr.get_context(args.context_id)
    if not ctx:
        print(f"icdev sessions export: no such session {args.context_id!r}", file=sys.stderr)
        return 2
    messages = mgr.get_messages(args.context_id, limit=100000)

    lines: list[str] = [
        json.dumps({"_type": "context", **ctx}, default=str),
    ]
    for m in messages:
        lines.append(json.dumps({"_type": "message", **m}, default=str))
    payload = "\n".join(lines) + "\n"

    if args.output:
        from pathlib import Path

        Path(args.output).write_text(payload, encoding="utf-8", newline="")
        print(f"Exported {len(messages)} message(s) to {args.output}")
    else:
        sys.stdout.write(payload)
    return 0


def _sessions_search(args: argparse.Namespace) -> int:
    from tools.agent_runtime.sessions import search_sessions

    query = " ".join(args.query).strip()
    if not query:
        print("icdev sessions search: a query is required", file=sys.stderr)
        return 2
    results = search_sessions(query, limit=args.limit)
    if args.json:
        print(json.dumps({"count": len(results), "results": results}, indent=2, default=str))
        return 0
    if not results:
        print(f"No matches for {query!r}.")
        return 0
    print(f"{len(results)} match(es) for {query!r}:")
    for i, r in enumerate(results, start=1):
        ctx = r.get("context_id") or "?"
        snippet = " ".join((r.get("content") or "").split())[:90]
        print(f"  {i}. [{r.get('type', '')}] {snippet}")
        print(f"       resume: icdev chat --resume {ctx}")
    return 0


def sessions_main(argv: list[str] | None = None) -> int:
    """Handle ``icdev sessions <cmd> [...]``."""
    parser = argparse.ArgumentParser(prog="icdev sessions")
    subs = parser.add_subparsers(dest="cmd")

    list_p = subs.add_parser("list", help="List conversations (newest first).")
    list_p.add_argument("--user", default=None)
    list_p.add_argument("--tenant", default=None)
    list_p.add_argument(
        "--status",
        default="active",
        help="Filter by status, or 'all' for every status (default: active).",
    )
    list_p.add_argument("--limit", type=int, default=50)
    list_p.add_argument("--json", action="store_true")

    exp_p = subs.add_parser("export", help="Export a transcript as JSONL.")
    exp_p.add_argument("context_id")
    exp_p.add_argument("--user", default=None)
    exp_p.add_argument("--tenant", default=None)
    exp_p.add_argument(
        "-o", "--output", default=None, help="Write JSONL here (default: stdout)."
    )

    search_p = subs.add_parser(
        "search", help="Full-text search past session turns (with resume ids)."
    )
    search_p.add_argument("query", nargs="+", help="Search terms.")
    search_p.add_argument("--limit", type=int, default=20)
    search_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        return _sessions_list(args)
    if args.cmd == "export":
        return _sessions_export(args)
    if args.cmd == "search":
        return _sessions_search(args)

    parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# python -m tools.agent_runtime
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Top-level dispatcher for ``python -m tools.agent_runtime``.

    ``sessions`` routes to :func:`sessions_main`; everything else (including a
    bare invocation) routes to :func:`chat_main`.
    """
    _argv = list(sys.argv[1:] if argv is None else argv)
    if _argv and _argv[0] == "sessions":
        return sessions_main(_argv[1:])
    if _argv and _argv[0] == "chat":
        return chat_main(_argv[1:])
    return chat_main(_argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
