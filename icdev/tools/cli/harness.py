#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev harness` -- wire ICDEV's guard into a non-Claude coding harness.

Usage:
    icdev harness install-guard opencode [--project DIR | --global] [--json]

Copies the packaged ``tool.execute.before`` plugin into opencode's plugin
directory, with this interpreter and this ICDEV root filled in. The plugin
calls ``python -m tools.hooks.harness_guard``, which runs the same checks as
the Claude Code PreToolUse hook (omx-guard-01).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="icdev harness",
                                description="Wire ICDEV's guard into a coding harness.")
    sub = p.add_subparsers(dest="cmd", required=True)
    ig = sub.add_parser("install-guard", help="Install the ICDEV guard plugin.")
    ig.add_argument("harness", choices=["opencode"])
    where = ig.add_mutually_exclusive_group()
    where.add_argument("--project", type=Path, default=None,
                       help="Project directory (default: cwd) -> <DIR>/.opencode/plugin/")
    where.add_argument("--global", dest="global_", action="store_true",
                       help="User-wide: ~/.config/opencode/plugin/")
    ig.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    from tools.hooks.harness_guard import install_guard

    result = install_guard(args.harness, project=args.project, global_=args.global_)
    if args.as_json:
        print(json.dumps(result))
    else:
        state = "installed" if result["changed"] else "already current"
        print(f"ICDEV guard for {args.harness}: {state} at {result['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
