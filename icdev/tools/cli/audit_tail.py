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
    icdev audit tail --source runtime_invocations   # runtime telemetry, same format
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

# Only the source NAME is imported eagerly — it is a bare string the formatter
# needs and duplicating it here would let the two spellings drift. `AuditStore`
# itself stays a lazy import inside main(), so the tests that patch it on the
# store module still take effect.
from tools.audit.store import SOURCE_RUNTIME

# Poll interval for --follow. Not configurable by design: the audit feed is not
# a latency-sensitive surface, and a tight loop against the primary database is
# a cost every other consumer pays for.
FOLLOW_INTERVAL_SEC = 2.0

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Fixed-width source column. Padded to the same 5 characters so the columns to
# the right of it stay aligned no matter which sources a run mixes.
_SOURCE_LABEL = {
    "hook_events": "hook ",
    "runtime_invocations": "run  ",
}


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
    src = _SOURCE_LABEL.get(row.get("source"), "audit")
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


def _runtime_summary(row: Dict[str, Any]) -> str:
    """The one-line "what happened" for an invocation.

    Ordered by what an operator scans for: the outcome, how long it took, then
    the reason it failed — or, when it did not fail, the argument KEY NAMES the
    recorder kept (never values; see ``invocation_recorder``).
    """
    parts = [row.get("status") or "?"]

    duration = row.get("duration_ms")
    if duration is not None:
        parts.append(f"{duration}ms")

    error_class = (row.get("error_class") or "").strip()
    error_message = (row.get("error_message") or "").strip()
    if error_class and error_message:
        parts.append(f"{error_class}: {error_message}")
    elif error_class or error_message:
        parts.append(error_class or error_message)
    elif row.get("arg_keys"):
        parts.append("(" + ", ".join(row["arg_keys"]) + ")")

    # Single-spaced on purpose: `_format_row` collapses runs of whitespace, so
    # wider separators would survive only in --json and the two views would
    # disagree about a string that is meant to be the same string.
    return " ".join(parts)


def _as_feed_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Render a ``runtime_invocations`` row in the shape ``tail()`` returns.

    The mapping is chosen so the existing formatter needs no branch: the
    invocation NAME lands in the event-type column (it is the thing that ran)
    and the SURFACE lands in the actor column (it is what ran it).

    Two deliberate choices:

      * ``created_at`` carries ``started_at``, not the row's own ``created_at``.
        The feed contract is "when the event happened", and for an invocation
        that is when it started — which is also the column the store ORDERs and
        filters ``since`` on, so the screen order, the ``--follow`` cursor and
        the SQL cannot disagree. The row's insert-time ``created_at`` is dropped
        rather than kept under a second name: two near-identical timestamps is
        an invitation to read the wrong one.
      * The runtime-only columns (``duration_ms``, ``arg_keys``, ``parent_id``
        …) are preserved alongside, so ``--json`` stays a superset rather than
        the lossy view the terminal necessarily is.
    """
    status = (row.get("status") or "").lower()
    merged = dict(row)
    merged.pop("created_at", None)
    merged.update({
        "id": row.get("id") or "",
        "source": SOURCE_RUNTIME,
        "created_at": row.get("started_at") or "",
        "event_type": row.get("name") or "",
        "actor": row.get("surface") or "",
        "project_id": row.get("project_id") or "",
        "summary": _runtime_summary(row),
        # Only a failure earns colour. `running` is not a warning — most rows
        # in a live table are mid-flight and painting them all would make the
        # highlight meaningless.
        "severity": "high" if status == "error" else "info",
        "session_id": row.get("session_id") or "",
        "classification": row.get("classification") or "",
    })
    return merged


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


def _runtime_rows(store, args: argparse.Namespace, since: Optional[str],
                  limit: int) -> List[Dict[str, Any]]:
    """Fetch runtime invocations through the shared feed flags.

    The generic flags are mapped onto the columns the terminal actually shows,
    so what you filter is what you read: ``--event-type`` matches the
    invocation name (the event-type column) and ``--actor`` matches the surface
    (the actor column). ``read_runtime_invocations`` drops ``None`` filters, so
    unset flags are passed straight through.
    """
    return [_as_feed_row(r) for r in store.read_runtime_invocations(
        limit=limit,
        project_id=args.project_id,
        since=since,
        surface=args.actor,
        name=args.event_types[0] if args.event_types else None,
    )]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="icdev audit tail",
        description="Tail the ICDEV audit feed (audit_trail + hook_events), or "
                    "runtime telemetry with --source runtime_invocations.",
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
                   help="Filter by event type (repeatable; a single invocation "
                        "name for --source runtime_invocations)")
    p.add_argument("--actor", default=None,
                   help="Filter by actor (audit_trail only; the surface for "
                        "--source runtime_invocations)")
    p.add_argument("--since", default=None,
                   help="Only events strictly newer than this ISO-8601 timestamp")
    p.add_argument("--source", dest="sources", action="append", default=None,
                   choices=["audit_trail", "hook_events", "runtime_invocations"],
                   help="Restrict to a source (repeatable; default: audit_trail + "
                        "hook_events). runtime_invocations is telemetry rather than "
                        "audit evidence and must be requested on its own.")
    p.add_argument("--no-color", dest="no_colour", action="store_true",
                   help="Disable coloured severity output")
    p.add_argument("--list-types", action="store_true",
                   help="List event types this deployment actually emits, with counts")
    args = p.parse_args(argv)

    from tools.audit.store import ALL_SOURCES, AuditFilter, AuditStore

    sources = args.sources or list(ALL_SOURCES)
    runtime_mode = SOURCE_RUNTIME in sources
    if runtime_mode and len(set(sources)) > 1:
        # The store keeps runtime_invocations out of ALL_SOURCES for a reason:
        # one agent session makes hundreds of MCP calls, so merging it into the
        # audit feed would bury the audit rows the caller asked for. Refusing is
        # the honest answer — quietly showing a feed that is 95% telemetry is not.
        print("icdev audit tail: --source runtime_invocations cannot be combined "
              "with other sources — it is higher-volume telemetry, not audit "
              "evidence. Request it on its own.", file=sys.stderr)
        return 1
    if runtime_mode and args.event_types and len(args.event_types) > 1:
        # audit_trail takes an IN-list; the runtime reader filters on a single
        # `name =`. Taking the first and dropping the rest would silently answer
        # a different question than the one that was asked.
        print("icdev audit tail: --source runtime_invocations accepts a single "
              "--event-type (matched against the invocation name).", file=sys.stderr)
        return 1

    store = AuditStore()
    colour = (not args.no_colour) and (not args.as_json) and _supports_colour(sys.stdout)

    if runtime_mode and args.list_types:
        # --list-types reads audit_trail. Answering with audit event types under
        # a runtime_invocations flag would look like the runtime emitted them.
        print("icdev audit tail: --list-types describes audit_trail event types. "
              "For the runtime name ranking use `icdev runtime top`.",
              file=sys.stderr)
        return 1

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
        sources=sources,
        limit=args.limit,
    )

    def fetch(since: Optional[str], limit: int) -> List[Dict[str, Any]]:
        """One entry point for both the first read and every --follow poll.

        Whichever source is selected, the caller gets rows in the feed shape —
        so the printing, the cursor and the retry loop below stay source-blind.
        """
        if runtime_mode:
            return _runtime_rows(store, args, since, limit)
        return store.tail(AuditFilter(
            project_id=filters.project_id,
            event_types=filters.event_types,
            actor=filters.actor,
            since=since,
            sources=filters.sources,
            limit=limit,
        ))

    try:
        rows = fetch(args.since, args.limit)
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
                new_rows = fetch(cursor, max(filters.limit, 100))
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
