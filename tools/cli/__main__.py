#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev` dispatcher — routes subcommands to the right module.

Subcommands:
  icdev init [target]              Scaffold a new project from bootstrap
  icdev enable <name> [<name>...]  Turn on canvas / subsystem toggles in .env
  icdev disable <name> [...]       Turn off canvas / subsystem toggles
  icdev status                     Show which canvases / subsystems are on
  icdev list                       List all supported toggles

Each subcommand has its own `--help`; for backward compat a bare `icdev`
shows the subcommand index.
"""

from __future__ import annotations

import sys


USAGE = """\
Usage: icdev <subcommand> [args]

Subcommands:
  init [target]            Scaffold a new ICDEV(TM) project (CLAUDE.md + FORGE
                           data + .claude/ + .env). Default target: cwd.
  scaffold canvas <key>    Generate a new canvas from a Jinja2 template.
  scaffold child-app <key> Generate a new child app from a Jinja2 template.
  profile list             List enterprise core profiles.
  profile show [<name>]     Show active/core profile details.
  profile apply <name>     Apply a profile's env overrides to .env.
  enable <name> [...]      Enable canvas(es) / subsystem(s) by flipping the
                           right .env flags (e.g. boundary, security, rag).
  disable <name> [...]     Disable — flip flags to false.
  status [--json]          Report which toggles are currently on/off.
  list [--json]            List supported toggle names + descriptions.
  tools list [--json]      List all tools discovered by the agent runtime.
  tools bundles [--json]   List agent toolset bundles (args/agent_toolsets.yaml).
  chat [-q "query"]        Start an interactive standalone agent session
                           (--resume <ctx-id> to continue a conversation).
  sessions list|export     List conversations / export a transcript as JSONL.
  audit export             Export SOC 2 (and future framework) evidence reports.
  demo seed --tenant <slug> [--canvases <c1,c2,...>]
                           Provision a demo tenant with synthetic data and
                           ICDEV_DEMO_MODE enabled (read-only banner).

Run `icdev <subcommand> --help` for subcommand-specific options.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    sub = args[0]
    rest = args[1:]

    if sub == "init":
        from icdev.tools.cli.init import main as init_main
        # init_main reads sys.argv directly; inject rest into argv so its
        # argparse sees only the subcommand args.
        old_argv = sys.argv
        sys.argv = ["icdev init"] + rest
        try:
            return init_main()
        finally:
            sys.argv = old_argv

    if sub in ("enable", "disable", "status", "list"):
        from icdev.tools.cli.enable import main as enable_main
        return enable_main([sub] + rest)

    if sub == "scaffold":
        from icdev.tools.cli.scaffold import main as scaffold_main
        return scaffold_main(rest)

    if sub == "profile":
        from icdev.tools.cli.profile import main as profile_main
        return profile_main(rest)

    if sub == "tools":
        from tools.cli.tools_list import main as tools_main
        return tools_main(rest)

    if sub == "chat":
        from tools.agent_runtime.cli import chat_main
        return chat_main(rest)

    if sub == "sessions":
        from tools.agent_runtime.cli import sessions_main
        return sessions_main(rest)

    if sub == "audit":
        from tools.cli.audit import main as audit_main
        return audit_main(rest)

    if sub == "demo":
        return _demo_main(rest)

    print(f"icdev: unknown subcommand '{sub}'\n\n{USAGE}", file=sys.stderr)
    return 2


def _demo_main(args: list[str]) -> int:
    """Handle 'icdev demo <cmd> [opts]'."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="icdev demo")
    subs = parser.add_subparsers(dest="cmd")

    seed_p = subs.add_parser("seed", help="Provision a demo tenant with synthetic data")
    seed_p.add_argument("--tenant", required=True, help="Demo tenant slug (e.g. demo-acme)")
    seed_p.add_argument(
        "--canvases",
        default=None,
        help="Comma-separated canvas domains to seed (default: all 6 standard domains)",
    )

    parsed = parser.parse_args(args)
    if parsed.cmd == "seed":
        canvases = (
            [c.strip() for c in parsed.canvases.split(",") if c.strip()]
            if parsed.canvases
            else None
        )
        from icdev.tools.demo.seeder import seed_demo_tenant
        result = seed_demo_tenant(parsed.tenant, canvases=canvases)
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
