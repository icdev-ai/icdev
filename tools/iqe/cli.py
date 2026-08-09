"""IQE CLI — parse and execute an intent query from the command line."""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ...and DROP this file's own directory again. `python tools/iqe/cli.py` leaves
# tools/iqe on sys.path, where tools/iqe/ast.py SHADOWS the stdlib `ast` module:
# the first `dataclasses` import reaches it via inspect -> annotationlib -> ast
# and the process dies with a circular-import error that never names iqe. This
# package is only ever imported as `tools.iqe.*`, so the bare directory on the
# path buys nothing and costs that.
_OWN_DIR = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _OWN_DIR]

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
