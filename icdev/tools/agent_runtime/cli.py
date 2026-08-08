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
    args = parser.parse_args(argv)

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

    if args.query:
        return _single_shot(runtime, args)

    runtime.loop(banner=not args.no_banner, stream=args.stream)
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

        Path(args.output).write_text(payload, encoding="utf-8")
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
