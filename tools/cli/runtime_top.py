#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev runtime top` — what the runtime actually ran, from the terminal.

``runtime_invocations`` records every MCP tool call, agent execution, persona
run and role step (migration 341 + ``invocation_recorder``). Until now nothing
read it, so "which MCP tool is slow" and "which one is failing" were questions
that needed a SQL client.

    icdev runtime top                          # per-surface rollup + top 20 names
    icdev runtime top --surface mcp            # just the MCP tools
    icdev runtime top --sort errors            # the ones that are failing
    icdev runtime top --sort duration          # the ones that are slow
    icdev runtime top --since 2026-08-07T00:00:00+00:00
    icdev runtime top --hours 24               # same thing, relative
    icdev runtime top --json | jq .

The default sort is by call count, which answers "what is this deployment
doing". ``--sort errors`` and ``--sort duration`` answer the two questions you
open this for, and they sort in SQL — a client-side sort of the top-20-by-calls
cannot surface a tool that is slow but rarely called.

Exit codes: 0 on success (including an empty table), 1 if the query failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tools.observability.invocation_recorder import SURFACES

SORT_KEYS = ("calls", "errors", "duration")


def _describe_backend() -> str:
    """Which database we actually read, for the empty-result message.

    An empty rollup and a rollup read from the WRONG database look identical on
    screen. ``tools/db/storage.py`` resolves ``.env`` from its own repo root, so
    running from a git worktree (which has no ``.env`` — it is gitignored)
    silently falls back to an empty SQLite file instead of the PostgreSQL board.
    Printing the source turns a confusing "(no invocations)" into an obvious
    "you are pointed at the wrong DB". Same reasoning as ``icdev audit tail``.
    """
    try:
        from tools.db import storage

        backend = getattr(storage, "_BACKEND", "unknown")
        if backend == "sqlite":
            return f"sqlite: {getattr(storage, 'DB_PATH', '?')}"
        return f"{backend}: {os.environ.get('ICDEV_DATABASE_URL', '(ICDEV_DATABASE_URL unset)')}"
    except Exception:  # noqa: BLE001
        return "unknown"


def resolve_since(since: Optional[str], hours: Optional[float],
                  now: Optional[datetime] = None) -> Optional[str]:
    """Turn ``--since`` / ``--hours`` into one ISO-8601 UTC string, or None.

    An explicit ``--since`` wins over ``--hours``: the absolute timestamp is the
    more specific instruction, and silently preferring the relative one would
    make a scripted ``--since`` quietly wrong.
    """
    if since:
        return since
    if hours is None:
        return None
    base = now or datetime.now(timezone.utc)
    return (base - timedelta(hours=float(hours))).isoformat()


def _ms(value: Optional[float]) -> str:
    """Render a duration. ``-`` when nothing has completed yet, never ``0``.

    A surface whose invocations are all still ``running`` has no measured
    duration at all; printing ``0.0`` there would read as "instantaneous".
    """
    return "-" if value is None else f"{value:,.0f}"


def _table(rows: List[Dict[str, Any]], first_col: str, header: str) -> List[str]:
    """Render a rollup as fixed-width text. Empty input yields no lines."""
    if not rows:
        return []
    labels = [str(r.get(first_col) or "") for r in rows]
    width = max([len(header)] + [len(s) for s in labels])
    width = min(width, 60)

    out = [
        f"{header:<{width}}  {'CALLS':>7}  {'ERRORS':>7}  {'ERR%':>6}  "
        f"{'RUN':>4}  {'AVG ms':>8}  {'MAX ms':>8}",
        # 52 = the six fixed-width numeric columns plus their two-space gutters.
        "-" * (width + 52),
    ]
    for row, label in zip(rows, labels):
        out.append(
            f"{label[:width]:<{width}}  {row['calls']:>7,}  {row['errors']:>7,}  "
            f"{row['error_rate_pct']:>5.1f}%  {row['running']:>4,}  "
            f"{_ms(row['avg_ms']):>8}  {_ms(row['max_ms']):>8}"
        )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="icdev runtime top",
        description="Per-surface and per-name rollup of runtime_invocations "
                    "(MCP tools, agents, personas, roles).",
    )
    p.add_argument("--surface", choices=list(SURFACES), default=None,
                   help="Restrict the per-name table to one surface")
    p.add_argument("--limit", type=int, default=20,
                   help="Rows in the per-name table (default: 20)")
    p.add_argument("--sort", dest="order_by", choices=list(SORT_KEYS), default="calls",
                   help="Sort the per-name table (default: calls)")
    p.add_argument("--since", default=None,
                   help="Only invocations started at or after this ISO-8601 UTC timestamp")
    p.add_argument("--hours", type=float, default=None,
                   help="Only the last N hours (ignored when --since is given)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit the whole rollup as one JSON object")
    args = p.parse_args(argv)

    from tools.observability.invocation_store import InvocationStore

    since = resolve_since(args.since, args.hours)
    store = InvocationStore()

    surfaces = store.by_surface(since=since)
    # Both reads go through the same store, so a failure in either leaves a
    # message here. Captured before the second call overwrites it.
    error = store.last_error
    names = store.by_name(surface=args.surface, since=since,
                          limit=args.limit, order_by=args.order_by)
    error = error or store.last_error

    if args.as_json:
        print(json.dumps({
            "since": since,
            "surface": args.surface,
            "sort": args.order_by,
            "by_surface": surfaces,
            "by_name": names,
            "error": error,
        }, indent=2, default=str))
        return 1 if error else 0

    if error:
        print(f"icdev runtime top: query failed: {error}", file=sys.stderr)
        print(f"  (read from {_describe_backend()})", file=sys.stderr)
        return 1

    window = f" since {since}" if since else ""
    print(f"Runtime invocations by surface{window}")
    surface_lines = _table(surfaces, "surface", "SURFACE")
    if surface_lines:
        print("\n".join(surface_lines))
    else:
        print(f"  (none — read from {_describe_backend()})")

    if surfaces:
        scope = args.surface or "all surfaces"
        print(f"\nTop {len(names)} by {args.order_by} — {scope}")
        print("\n".join(_table(names, "name", "NAME")) or "  (none)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
