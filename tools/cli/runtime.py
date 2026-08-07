#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev runtime top` — what the runtime actually ran, from the terminal.

``runtime_invocations`` records every MCP tool call, agent run, persona run and
role step (migration 341 + ``tools/observability/invocation_recorder.py``).
Until now the only way to see it was a SQL client: PR #1196 wrote the rows and
PR #1194 gave ``audit_trail`` a reader, but this table had neither.

    icdev runtime top                         # every surface, top 20 by calls
    icdev runtime top --surface mcp           # just the MCP tools
    icdev runtime top --limit 50
    icdev runtime top --errors-only           # only what has actually failed
    icdev runtime top --sort avg_ms           # slowest rather than busiest
    icdev runtime top --since 2026-08-01      # rows started after a timestamp
    icdev runtime top --surfaces-only         # headline totals, no per-name table
    icdev runtime top --json | jq .

Exit codes: 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

#: Columns a caller may sort the per-name table by. ``calls`` is the default
#: because "what is busiest" is the question an operator asks first; ``avg_ms``
#: and ``max_ms`` answer "what is slow" and ``errors`` answers "what is broken".
SORT_KEYS = ("calls", "errors", "error_rate", "avg_ms", "max_ms")


def _ms(value: Optional[float]) -> str:
    """Render a duration, distinguishing "not measured" from "zero".

    ``avg_ms`` is None when nothing on that row has completed yet. Printing
    that as ``0.0`` would read as an instantaneous tool rather than an
    unfinished one, so it prints as ``-``.
    """
    if value is None:
        return "-"
    if value >= 10_000:
        return f"{value / 1000:.1f}s"
    return f"{value:.1f}"


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _print_surfaces(surfaces: List[Dict[str, Any]]) -> None:
    print(f"{'surface':<10}{'names':>7}{'calls':>9}{'errors':>8}{'err%':>8}"
          f"{'avg ms':>10}{'max ms':>10}")
    print("-" * 62)
    for s in surfaces:
        print(f"{s['surface'][:10]:<10}{s['names']:>7}{s['calls']:>9}"
              f"{s['errors']:>8}{_pct(s['error_rate']):>8}"
              f"{_ms(s['avg_ms']):>10}{_ms(s['max_ms']):>10}")


def _print_names(rows: List[Dict[str, Any]], total: int) -> None:
    width = max((len(r["name"]) for r in rows), default=4)
    width = min(max(width, 4), 46)
    print(f"{'surface':<9}{'name':<{width}}  {'calls':>7}{'errors':>8}"
          f"{'err%':>8}{'avg ms':>10}{'max ms':>10}  last seen")
    print("-" * (9 + width + 47))
    for r in rows:
        last = (r.get("last_started_at") or "")[:16].replace("T", " ")
        print(f"{r['surface'][:8]:<9}{r['name'][:width]:<{width}}  {r['calls']:>7}"
              f"{r['errors']:>8}{_pct(r['error_rate']):>8}"
              f"{_ms(r['avg_ms']):>10}{_ms(r['max_ms']):>10}  {last}")
    if total > len(rows):
        print(f"... {total - len(rows)} more (raise --limit to see them)")


def _sorted(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Re-sort the per-name rows client-side.

    The store always orders by calls so the top-N truncation is stable and
    meaningful; re-sorting the already-selected rows here is what ``--sort``
    means. Sorting in SQL instead would change WHICH rows survive --limit,
    which is a different (and more surprising) command.
    """
    if key == "calls":
        return rows
    return sorted(rows, key=lambda r: (r.get(key) is None, -(r.get(key) or 0),
                                       r["surface"], r["name"]))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="icdev runtime top",
        description="Per-surface and per-name rollup of runtime_invocations "
                    "(MCP tools, agents, personas, roles).",
    )
    p.add_argument("--surface", default=None,
                   help="Restrict to one surface: mcp, agent, persona or role")
    p.add_argument("--name", default=None,
                   help="Restrict to one tool / agent / persona / role name")
    p.add_argument("--status", default=None,
                   help="Restrict to one status: running, ok or error")
    p.add_argument("--since", default=None,
                   help="Only invocations started after this ISO-8601 timestamp")
    p.add_argument("--limit", type=int, default=20,
                   help="Rows in the per-name table (default: 20)")
    p.add_argument("--sort", default="calls", choices=SORT_KEYS,
                   help="Sort the per-name table by this column (default: calls)")
    p.add_argument("--errors-only", action="store_true",
                   help="Only show names that have recorded at least one error")
    p.add_argument("--surfaces-only", action="store_true",
                   help="Print the per-surface totals and nothing else")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit the whole report as one JSON object")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    from tools.observability.invocation_recorder import SURFACES
    from tools.observability.invocation_store import InvocationFilter, InvocationStore

    if args.surface and args.surface not in SURFACES:
        print(f"icdev runtime top: unknown surface '{args.surface}' "
              f"(expected one of: {', '.join(SURFACES)})", file=sys.stderr)
        return 1

    filters = InvocationFilter(
        surface=args.surface,
        name=args.name,
        status=args.status,
        since=args.since,
        # Fetch every group; --errors-only filters AFTER the read, so a small
        # --limit must not decide which errors are visible.
        limit=0,
    )

    try:
        report = InvocationStore().report(filters)
    except Exception as exc:  # noqa: BLE001
        print(f"icdev runtime top: query failed: {exc}", file=sys.stderr)
        return 1

    names = report["names"]
    if args.errors_only:
        names = [r for r in names if r["errors"] > 0]
    total_names = len(names)
    limit = max(0, int(args.limit))
    names = _sorted(names, args.sort)[:limit] if limit else _sorted(names, args.sort)

    if args.as_json:
        print(json.dumps({
            "surfaces": report["surfaces"],
            "names": names,
            "total_names": total_names,
            "filters": {**report["filters"],
                        "limit": limit,
                        "sort": args.sort,
                        "errors_only": args.errors_only},
        }, indent=2, default=str))
        return 0

    if not report["surfaces"]:
        # An empty table and a read against the WRONG database look identical on
        # screen. audit_tail hit this exact trap (a worktree has no .env, so
        # storage falls back to an empty SQLite file instead of the PostgreSQL
        # board); print the source so "(no invocations)" is actionable.
        from tools.cli.audit_tail import _describe_backend

        print(f"(no invocations recorded — read from {_describe_backend()})",
              file=sys.stderr)
        print("Is ICDEV_OBS_INVOCATIONS=0, or has migration 341 not run?",
              file=sys.stderr)
        return 0

    _print_surfaces(report["surfaces"])
    if args.surfaces_only:
        return 0

    print()
    if not names:
        print("(no names matched)")
        return 0
    _print_names(names, total_names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
