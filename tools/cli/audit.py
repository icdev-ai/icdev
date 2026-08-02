#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev audit` subcommands — compliance evidence export, and reading the feed.

Usage:
    icdev audit export --framework soc2 --tenant-id <tid> --output report.html
    icdev audit export --framework soc2 --tenant-id <tid> --format json
    icdev audit tail [--follow] [--json] [--project ID] [--event-type T]
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    # `tail` owns a rich flag set of its own (--follow, --source, repeatable
    # --event-type). Dispatching to it before argparse sees the arguments keeps
    # the two subcommands' flags from having to coexist in one parser, and lets
    # `icdev audit tail --help` show tail's own help rather than this one.
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "tail":
        from tools.cli.audit_tail import main as tail_main

        return tail_main(argv[1:])

    p = argparse.ArgumentParser(
        prog="icdev audit",
        description="ICDEV™ audit commands: compliance export and feed tailing.",
        epilog="tail: read the live audit feed — `icdev audit tail --help`",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # Registered so it appears in `icdev audit --help`; actually handled by the
    # early dispatch above.
    sub.add_parser("tail", help="Tail the audit feed (see `icdev audit tail --help`).",
                   add_help=False)

    exp = sub.add_parser("export", help="Export a compliance evidence report.")
    exp.add_argument("--framework", default="soc2", choices=["soc2"],
                     help="Compliance framework (default: soc2)")
    exp.add_argument("--tenant-id", default="default", dest="tenant_id")
    exp.add_argument("--format", dest="fmt", choices=["html", "json"], default="html")
    exp.add_argument("--output", default=None,
                     help="Output file path. Defaults to soc2_report.<fmt>")
    exp.add_argument("--collect", action="store_true",
                     help="Run evidence collector before exporting")
    exp.add_argument("--since", default=None,
                     help="Only collect events since this date (ISO format)")
    exp.add_argument("--json", dest="as_json", action="store_true",
                     help="Print coverage summary as JSON to stdout")

    args = p.parse_args(argv)

    if args.cmd == "export":
        if args.collect:
            from tools.compliance.soc2_collector import collect_evidence
            result = collect_evidence(args.tenant_id, framework=args.framework, since=args.since)
            if not args.as_json:
                print(f"Collected {result['collected']} evidence items "
                      f"(skipped {result['skipped']}), "
                      f"covering {len(result['controls_covered'])} controls.")

        from tools.compliance.soc2_exporter import export_report
        output = args.output or f"soc2_report.{args.fmt}"
        import json
        data = export_report(args.tenant_id, output, fmt=args.fmt, framework=args.framework)

        if args.as_json:
            print(json.dumps(data["summary"], indent=2))
        else:
            s = data["summary"]
            print(f"Report: {output}")
            print(f"Coverage: {s['coverage_pct']}% "
                  f"({s['evidenced']}/{s['total_controls']} controls evidenced)")
        return 0

    print(f"icdev audit: unknown command '{args.cmd}'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
