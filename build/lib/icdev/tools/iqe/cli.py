"""IQE CLI — parse and execute an intent query from the command line."""

from __future__ import annotations

import argparse
import json
import sys

from tools.iqe.parser import IQEParser
from tools.iqe.executor import IQEExecutor


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iqe", description="IQE Intent Query Engine")
    ap.add_argument("query", nargs="?", help="Intent query string")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    args = ap.parse_args(argv)

    if not args.query:
        ap.print_help()
        return 1

    tree = IQEParser().parse(args.query)
    result = IQEExecutor().execute(tree)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
