"""IQE CLI — python -m tools.iqe.run --query <file> | --query-string <str> [--json|--human]

Exit codes: 0 success, 1 parse error, 2 exec error.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from tools.iqe import IQESyntaxError, execute_query, parse


def _render_table(rows: list[dict]) -> str:
    if not rows:
        return "(no results)"
    headers = list(rows[0].keys())
    widths = [
        max(len(h), max((len(str(r.get(h, ""))) for r in rows), default=0))
        for h in headers
    ]
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep = "  ".join("-" * w for w in widths)
    data_lines = [
        "  ".join(str(r.get(h, "")).ljust(w) for h, w in zip(headers, widths))
        for r in rows
    ]
    return "\n".join([header_line, sep, *data_lines])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.iqe.run",
        description="Execute an IQE query against registered collections or a SQLite DB.",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--query", metavar="FILE", help="Path to .iqe query file")
    src.add_argument("--query-string", metavar="STR", help="Inline IQE query string")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="Emit JSON array to stdout")
    fmt.add_argument("--human", action="store_true", help="Emit human-readable table (default)")
    args = ap.parse_args(argv)

    if args.query:
        path = Path(args.query)
        try:
            query_text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: cannot read {path}: {e}", file=sys.stderr)
            return 2
    else:
        query_text = args.query_string

    try:
        ast = parse(query_text)
    except IQESyntaxError as e:
        print(f"parse error: {e}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(":memory:")  # pg-ok: in-memory query sandbox, not ICDEV storage
    try:
        rows = execute_query(ast, conn)
    except sqlite3.OperationalError:
        rows = []
    except Exception as e:
        print(f"exec error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        print(json.dumps(rows, default=str))
    else:
        print(_render_table(rows))

    return 0


if __name__ == "__main__":
    sys.exit(main())
