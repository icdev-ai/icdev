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
  audit export             Export SOC 2 (and future framework) evidence reports.

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

    if sub == "audit":
        from tools.cli.audit import main as audit_main
        return audit_main(rest)

    print(f"icdev: unknown subcommand '{sub}'\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
