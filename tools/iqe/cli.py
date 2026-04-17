"""IQE CLI — run ICDEV Query Engine queries from the command line.

Usage:
    python tools/iqe/cli.py --query "foreach c in network.circuits where c.monthly_cost_usd > 5000 select c.circuit_id, c.carrier, c.monthly_cost_usd"
    python tools/iqe/cli.py --file context/iqe/queries/network/03_cat1_open_findings.iqe --adapter ndc --json
    python tools/iqe/cli.py --file context/iqe/queries/network/02_high_cost_circuits.iqe --topology <topology_id>
    python tools/iqe/cli.py --list-collections --adapter ndc
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _get_adapter(name: str):
    if name == "ndc":
        from tools.iqe.adapters.ndc import NDCAdapter
        return NDCAdapter()
    raise ValueError(f"Unknown adapter {name!r}. Available: ndc")


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no results)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    sep = "  ".join("-" * widths[c] for c in cols)
    print(header)
    print(sep)
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/iqe/cli.py",
        description="IQE — ICDEV Query Engine",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--query", "-q", help="IQE query string")
    src.add_argument("--file", "-f", help="Path to a .iqe query file")
    src.add_argument("--list-collections", action="store_true", help="List available collections")

    ap.add_argument("--adapter", "-a", default="ndc", help="Adapter name (default: ndc)")
    ap.add_argument("--topology", "-t", help="Scope query to a specific topology ID (NDC only)")
    ap.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")
    ap.add_argument("--count", "-c", action="store_true", help="Print row count only")

    args = ap.parse_args(argv)

    adapter = _get_adapter(args.adapter)

    if args.list_collections:
        cols = adapter.list_collections()
        if args.as_json:
            print(json.dumps({"collections": cols}, indent=2))
        else:
            for c in cols:
                print(c)
        return 0

    # Resolve query text
    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            return 1
        query_text = path.read_text(encoding="utf-8").strip()
        # Strip comment lines (# …)
        query_text = "\n".join(
            line for line in query_text.splitlines()
            if not line.strip().startswith("#")
        )
    elif args.query:
        query_text = args.query.strip()
    else:
        ap.print_help()
        return 1

    from tools.iqe.parser import parse
    from tools.iqe.interpreter import execute

    try:
        q = parse(query_text)
    except SyntaxError as e:
        print(f"PARSE ERROR: {e}", file=sys.stderr)
        return 2

    try:
        rows = execute(q, adapter, topology_id=args.topology)
    except Exception as e:  # noqa: BLE001
        print(f"EXEC ERROR: {e}", file=sys.stderr)
        return 3

    if args.count:
        print(len(rows))
        return 0

    if args.as_json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        _print_table(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
