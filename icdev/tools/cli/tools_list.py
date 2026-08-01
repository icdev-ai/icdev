# CUI // SP-CTI
"""`icdev tools` — inspect the agent runtime's discovered tools and bundles.

Subcommands:
  icdev tools list [--json]        List every discovered tool (name, source, schema).
  icdev tools bundles [--json]     List the toolset bundles from args/agent_toolsets.yaml.

Backs the sag-reg-02 requirement ``icdev tools list --json``: it emits all
discovered tools with their OpenAI schemas so an external agent (or an operator)
can inspect the full surface before selecting a bounded bundle.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icdev tools")
    subs = parser.add_subparsers(dest="cmd")

    list_p = subs.add_parser("list", help="List all discovered agent tools.")
    list_p.add_argument("--json", action="store_true", help="Emit JSON.")

    bundles_p = subs.add_parser("bundles", help="List toolset bundles.")
    bundles_p.add_argument("--json", action="store_true", help="Emit JSON.")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        from tools.agent_runtime.toolsets import all_discovered_tools

        tools = all_discovered_tools()
        if args.json:
            print(json.dumps({"count": len(tools), "tools": tools}, indent=2))
        else:
            print(f"{len(tools)} discovered tools:")
            for t in tools:
                ro = "ro" if t["read_only"] else "rw"
                desc = (t["schema"].get("function") or {}).get("description", "")
                print(f"  {t['name']:<34} [{t['source']:<9} {ro}] {desc[:70]}")
        return 0

    if args.cmd == "bundles":
        from tools.agent_runtime.toolsets import list_bundles

        bundles = list_bundles()
        if args.json:
            print(json.dumps({"count": len(bundles), "bundles": bundles}, indent=2))
        else:
            print(f"{len(bundles)} toolset bundles:")
            for b in bundles:
                flag = " (mutating)" if b["mutating"] else ""
                print(f"  {b['name']:<12} {b['tool_count']} tools{flag} — {b['description'][:60]}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
