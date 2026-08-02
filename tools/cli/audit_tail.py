#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev audit tail` — read the audit feed from the terminal.

``audit_trail`` is the richest observability surface ICDEV has (15k+ rows, 246
event types, 165 write sites) and until now only the dashboard could read it.
This is the ``tail -f`` for it.

    icdev audit tail                          # last 50 events
    icdev audit tail --limit 200
    icdev audit tail --follow                 # poll for new events
    icdev audit tail --json | jq .            # one JSON object per line
    icdev audit tail --project my-project
    icdev audit tail --event-type security_scan_completed
    icdev audit tail --source hook_events
    icdev audit tail --list-types             # what this deployment actually emits

Exit codes: 0 on success (including a clean Ctrl-C), 1 on error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# Poll interval for --follow. Not configurable by design: the audit feed is not
# a latency-sensitive surface, and a tight loop against the primary database is
# a cost every other consumer pays for.
FOLLOW_INTERVAL_SEC = 2.0

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _supports_colour(stream) -> bool:
    """True only for a real terminal. A redirected stream gets plain text."""
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def _colour(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def _severity_code(severity: str) -> str:
    return {
        "critical": "1;31",  # bold red
        "high": "31",        # red
        "medium": "33",      # yellow
        "low": "36",         # cyan
    }.get((severity or "").lower(), "0")


def _format_row(row: Dict[str, Any], colour: bool) -> str:
    ts = (row.get("created_at") or "")[:19].replace("T", " ")
    src = "hook " if row.get("source") == "hook_events" else "audit"
    sev = (row.get("severity") or "info").lower()
    etype = (row.get("event_type") or "")[:34]
    actor = (row.get("actor") or "")[:18]
    summary = " ".join((row.get("summary") or "").split())[:90]

    etype_s = _colour(f"{etype:<34}", _severity_code(sev), colour and sev != "info")
    return f"{ts:19}  {src}  {etype_s}  {actor:<18}  {summary}"


def _print_rows(rows: List[Dict[str, Any]], as_json: bool, colour: bool) -> None:
    for row in rows:
        if as_json:
            # One object per line so the stream stays jq-able while following.
            print(json.dumps(row, default=str), flush=True)
        else:
            print(_format_row(row, colour), flush=True)


def _cursor_of(rows: List[Dict[str, Any]]) -> Optional[str]:
    """Newest created_at in a batch — the resume point for --follow."""
    return max((r.get("created_at") or "" for r in rows), default="") or None


def _describe_backend() -> str:
    """Which database we actually read, for the empty-result message.

    An empty feed and a feed read from the WRONG database look identical on
    screen, and the wrong one is easy to hit: ``tools/db/storage.py`` resolves
    ``.env`` from its own repo root, so running from a git worktree (which has
    no ``.env`` — it is gitignored) silently falls back to an empty SQLite file
    instead of the PostgreSQL board. Printing the source turns a confusing
    "(no events)" into an obvious "you are pointed at the wrong DB".
    """
    try:
        from tools.db import storage

        backend = getattr(storage, "_BACKEND", "unknown")
        if backend == "sqlite":
            return f"sqlite: {getattr(storage, 'DB_PATH', '?')}"
        return f"{backend}: {os.environ.get('ICDEV_DATABASE_URL', '(ICDEV_DATABASE_URL unset)')}"
    except Exception:  # noqa: BLE001
        return "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="icdev audit tail",
        description="Tail the ICDEV audit feed (audit_trail + hook_events).",
    )
    p.add_argument("--limit", type=int, default=50,
                   help="Number of events to show (default: 50)")
    p.add_argument("--follow", "-f", action="store_true",
                   help=f"Poll for new events every {FOLLOW_INTERVAL_SEC:g}s until interrupted")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit one JSON object per line")
    p.add_argument("--project", dest="project_id", default=None,
                   help="Filter by project_id")
    p.add_argument("--event-type", dest="event_types", action="append", default=None,
                   help="Filter by event type (repeatable)")
    p.add_argument("--actor", default=None,
                   help="Filter by actor (audit_trail only)")
    p.add_argument("--since", default=None,
                   help="Only events strictly newer than this ISO-8601 timestamp")
    p.add_argument("--source", dest="sources", action="append", default=None,
                   choices=["audit_trail", "hook_events"],
                   help="Restrict to a source (repeatable; default: both)")
    p.add_argument("--no-color", dest="no_colour", action="store_true",
                   help="Disable coloured severity output")
    p.add_argument("--list-types", action="store_true",
                   help="List event types this deployment actually emits, with counts")
    args = p.parse_args(argv)

    from tools.audit.store import ALL_SOURCES, AuditFilter, AuditStore

    store = AuditStore()
    colour = (not args.no_colour) and (not args.as_json) and _supports_colour(sys.stdout)

    if args.list_types:
        types = store.event_types()
        if args.as_json:
            print(json.dumps(types, indent=2))
        elif not types:
            print("No event types found (is audit_trail populated?)")
        else:
            width = max(len(t["event_type"]) for t in types)
            for t in types:
                print(f"{t['event_type']:<{width}}  {t['count']:>7}")
        return 0

    filters = AuditFilter(
        project_id=args.project_id,
        event_types=args.event_types,
        actor=args.actor,
        since=args.since,
        sources=args.sources or list(ALL_SOURCES),
        limit=args.limit,
    )

    try:
        rows = store.tail(filters)
    except Exception as exc:  # noqa: BLE001
        print(f"icdev audit tail: query failed: {exc}", file=sys.stderr)
        return 1

    # Oldest-first on screen so the newest ends up at the bottom, next to the
    # prompt — the way tail(1) behaves and the way --follow has to append.
    _print_rows(list(reversed(rows)), args.as_json, colour)

    if not args.follow:
        if not rows and not args.as_json:
            print(f"(no events matched — read from {_describe_backend()})",
                  file=sys.stderr)
        return 0

    cursor = _cursor_of(rows) or args.since
    try:
        while True:
            time.sleep(FOLLOW_INTERVAL_SEC)
            try:
                new_rows = store.tail(AuditFilter(
                    project_id=filters.project_id,
                    event_types=filters.event_types,
                    actor=filters.actor,
                    since=cursor,
                    sources=filters.sources,
                    limit=max(filters.limit, 100),
                ))
            except Exception as exc:  # noqa: BLE001 — a transient DB blip must
                # not end a long-running follow; report once and keep going.
                print(f"icdev audit tail: poll failed ({exc}) — retrying",
                      file=sys.stderr, flush=True)
                continue
            if not new_rows:
                continue
            _print_rows(list(reversed(new_rows)), args.as_json, colour)
            cursor = _cursor_of(new_rows) or cursor
    except KeyboardInterrupt:
        # Ctrl-C is how you stop a tail. That is a success, not a traceback.
        if not args.as_json:
            print("", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
