#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev runtime top` — what the runtime actually ran, ranked by call count.

``runtime_invocations`` (migration 341) is the only place that records MCP tool
calls, agent runs, persona sessions and role steps in one shape. Until now the
only reader was ``invocation_recorder.summary()`` called from Python. This is
the terminal view of it, and the sibling of ``icdev audit tail``: that command
answers "what happened", this one answers "what is slow and what is failing".

    icdev runtime top                        # top 20 names across all surfaces
    icdev runtime top --limit 50
    icdev runtime top --surface mcp          # just the MCP tools
    icdev runtime top --surface agent        # SAG tool calls (hgx-obs-01)
    icdev runtime top --json | jq .

``top`` answers "what is slow and what is failing" across every run. ``trace``
answers it for ONE run:

    icdev runtime trace <correlation-id>     # every span of one agent run
    icdev runtime trace <correlation-id> --json

The correlation id is ``AgentLoopResult.trace_id``. Both the ``agent.turn``
spans the loop emits and the ``gen_ai.invoke`` spans the router emits beneath
them carry it, so one id returns the whole run — which is the join that
``tools/agent_runtime`` had no way to express before.

The rollup itself lives in ``tools/observability/invocation_recorder.py`` so
that this command and the coverage check cannot disagree about what "calls" or
"errors" mean — no SQL is authored here. Likewise ``trace`` reads through
``tools/observability/agent_trace.spans_for_correlation``.

``--limit`` bounds the number of NAMES shown, not the number of invocations
scanned: the underlying ``summary()`` aggregates the whole table and returns the
busiest N groups. That is the useful ranking for a `top`-style view, and saying
so here is cheaper than someone discovering it from a surprising average.

Exit codes: 0 on success (including an empty table), 1 on error.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from tools.observability import invocation_recorder

# Long MCP names (`mcp__icdev-unified__...`) would otherwise set the table width
# for every row. Truncation is display-only; --json carries the full name.
_MAX_NAME = 60

_HEADERS = ("SURFACE", "NAME", "CALLS", "ERRORS", "AVG MS", "MAX MS")
_ALIGN = ("<", "<", ">", ">", ">", ">")
_ERRORS_COL = 3


def _supports_colour(stream) -> bool:
    """True only for a real terminal. A redirected stream gets plain text."""
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def _num(value: Any) -> Optional[float]:
    """Coerce one aggregate cell to a float, or None.

    PostgreSQL returns ``Decimal`` for ``avg()``/``sum()`` and SQLite returns
    float/int, so the two backends hand back different Python types for the same
    query. Both also return NULL when every row in a group is still ``running``
    and has no ``duration_ms`` yet. One helper here means the formatter and the
    JSON encoder never have to care which backend answered.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Backend-independent, JSON-safe view of a ``summary()`` result."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        avg_ms = _num(row.get("avg_ms"))
        max_ms = _num(row.get("max_ms"))
        out.append({
            "surface": str(row.get("surface") or ""),
            "name": str(row.get("name") or ""),
            "calls": int(_num(row.get("calls")) or 0),
            "errors": int(_num(row.get("errors")) or 0),
            # Sub-millisecond precision on an average is noise; one decimal is
            # enough to tell 0.4ms from 4ms without implying more than we know.
            "avg_ms": None if avg_ms is None else round(avg_ms, 1),
            "max_ms": None if max_ms is None else int(max_ms),
        })
    return out


def _cells(rows: Sequence[Dict[str, Any]]) -> List[List[str]]:
    body = []
    for row in rows:
        name = row["name"]
        if len(name) > _MAX_NAME:
            name = name[: _MAX_NAME - 1] + "…"
        body.append([
            row["surface"],
            name,
            str(row["calls"]),
            str(row["errors"]),
            "-" if row["avg_ms"] is None else f"{row['avg_ms']:.0f}",
            "-" if row["max_ms"] is None else str(row["max_ms"]),
        ])
    return body


def render(rows: Sequence[Dict[str, Any]], colour: bool = False) -> List[str]:
    """Aligned table for normalised rows. Returns lines, does not print.

    Colour is applied by MEANING (a row with errors is red) rather than by
    matching status words inside the cell text, which is what
    ``output_formatter.format_table`` does — that would paint
    ``workflow_loop_create`` green because "flow" contains "low".
    """
    body = _cells(rows)
    widths = [len(h) for h in _HEADERS]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: Sequence[str]) -> str:
        return "  ".join(
            f"{cell:{_ALIGN[i]}{widths[i]}}" for i, cell in enumerate(cells)
        ).rstrip()

    lines = [_line(_HEADERS), "  ".join("-" * w for w in widths)]
    for row in body:
        text = _line(row)
        if colour and row[_ERRORS_COL] not in ("0", "-"):
            text = f"\033[31m{text}\033[0m"
        lines.append(text)
    return lines


def _totals(rows: Sequence[Dict[str, Any]]) -> str:
    calls = sum(r["calls"] for r in rows)
    errors = sum(r["errors"] for r in rows)
    return f"{len(rows)} name(s)  {calls} call(s)  {errors} error(s)"


def _top(args: argparse.Namespace) -> int:
    # summary() swallows its own exceptions and returns [] on failure, so an
    # empty result and an unreachable/un-migrated database look identical here.
    # Naming the backend on an empty table is what tells those two apart — the
    # same reason `icdev audit tail` prints it, and the same trap: a git
    # worktree has no .env, so storage silently falls back to an empty SQLite
    # file instead of the PostgreSQL board.
    try:
        rows = normalise(invocation_recorder.summary(
            surface=args.surface, limit=max(1, args.limit),
        ))
    except Exception as exc:  # noqa: BLE001
        print(f"icdev runtime top: query failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        from tools.cli.audit_tail import _describe_backend

        surface = f" for surface '{args.surface}'" if args.surface else ""
        print(f"(no invocations recorded{surface} — read from {_describe_backend()})",
              file=sys.stderr)
        return 0

    colour = (not args.no_colour) and _supports_colour(sys.stdout)
    for line in render(rows, colour):
        print(line)
    print(f"\n{_totals(rows)}")
    return 0


# ---------------------------------------------------------------------------
# `icdev runtime trace <correlation-id>` — one run, every span
# ---------------------------------------------------------------------------
_TRACE_HEADERS = ("START", "DURATION", "STATUS", "SPAN", "DETAIL")
_TRACE_ALIGN = ("<", ">", "<", "<", "<")
_MAX_DETAIL = 58


def _detail(span: Dict[str, Any]) -> str:
    """The one attribute worth showing per span kind, or a compact fallback.

    A turn span is identified by its turn number and a router span by the model
    it called; those two answer "what happened here" for the two span kinds an
    agent run actually produces. Anything else falls back to whatever non-ICDEV
    attributes it carries, so a future span type still renders something useful.
    """
    attrs = span.get("attributes") or {}
    if not isinstance(attrs, dict):
        return ""
    if "agent.turn" in attrs:
        bits = [f"turn={attrs['agent.turn']}"]
        if attrs.get("agent.tool_call_count") is not None:
            bits.append(f"tools={attrs['agent.tool_call_count']}")
        if attrs.get("gen_ai.response.model"):
            bits.append(str(attrs["gen_ai.response.model"]))
        return " ".join(bits)
    if attrs.get("gen_ai.request.model"):
        model = str(attrs["gen_ai.request.model"])
        fn = attrs.get("icdev.llm_function")
        return f"{model} ({fn})" if fn else model
    other = [f"{k}={v}" for k, v in sorted(attrs.items()) if not k.startswith("icdev.")]
    return " ".join(other)


def render_trace(spans: Sequence[Dict[str, Any]]) -> List[str]:
    """Aligned one-line-per-span view. Returns lines, does not print."""
    body = []
    for span in spans:
        detail = _detail(span)
        if len(detail) > _MAX_DETAIL:
            detail = detail[: _MAX_DETAIL - 1] + "…"
        duration = span.get("duration_ms")
        body.append([
            str(span.get("start_time") or "")[:19],
            "-" if duration is None else f"{int(duration)}ms",
            str(span.get("status_code") or "UNSET"),
            str(span.get("name") or ""),
            detail,
        ])

    widths = [len(h) for h in _TRACE_HEADERS]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: Sequence[str]) -> str:
        return "  ".join(
            f"{cell:{_TRACE_ALIGN[i]}{widths[i]}}" for i, cell in enumerate(cells)
        ).rstrip()

    lines = [_line(_TRACE_HEADERS), "  ".join("-" * w for w in widths)]
    lines.extend(_line(row) for row in body)
    return lines


def _trace(args: argparse.Namespace) -> int:
    from tools.observability.agent_trace import spans_for_correlation

    try:
        spans = spans_for_correlation(args.correlation_id, limit=max(1, args.limit))
    except Exception as exc:  # noqa: BLE001
        print(f"icdev runtime trace: query failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(spans, indent=2, default=str))
        return 0

    if not spans:
        from tools.cli.audit_tail import _describe_backend

        # Same trap as `top`: a worktree without a .env silently reads an empty
        # SQLite file rather than the PostgreSQL board, and an empty result and
        # a wrong database look identical without this.
        print(
            f"(no spans recorded for correlation id {args.correlation_id!r} — "
            f"read from {_describe_backend()})",
            file=sys.stderr,
        )
        return 0

    for line in render_trace(spans):
        print(line)
    turns = sum(1 for s in spans if str(s.get("name") or "") == "agent.turn")
    print(f"\n{len(spans)} span(s)  {turns} turn(s)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="icdev runtime",
        description="Read runtime invocation telemetry (runtime_invocations).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    top = sub.add_parser(
        "top",
        help="Rank tools/agents/personas/roles by call count, with error and latency stats.",
        description="Per-name rollup of runtime invocations: calls, errors, avg/max duration.",
    )
    top.add_argument("--limit", type=int, default=20,
                     help="Number of names to show, busiest first (default: 20)")
    top.add_argument("--surface", default=None, choices=list(invocation_recorder.SURFACES),
                     help="Restrict to one surface (default: all)")
    top.add_argument("--json", dest="as_json", action="store_true",
                     help="Emit the rollup as JSON")
    top.add_argument("--no-color", dest="no_colour", action="store_true",
                     help="Disable coloured output for rows with errors")

    trace = sub.add_parser(
        "trace",
        help="Show every span of one agent run, joined by its correlation id.",
        description=(
            "List the spans carrying a correlation id, oldest first: the "
            "agent.turn spans the loop emits and the gen_ai.invoke spans the "
            "router emits beneath them."
        ),
    )
    trace.add_argument("correlation_id",
                       help="Run correlation id (AgentLoopResult.trace_id)")
    trace.add_argument("--limit", type=int, default=500,
                       help="Maximum spans to return (default: 500)")
    trace.add_argument("--json", dest="as_json", action="store_true",
                       help="Emit the spans as JSON")

    args = p.parse_args(argv)
    if args.cmd == "top":
        return _top(args)
    if args.cmd == "trace":
        return _trace(args)

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
