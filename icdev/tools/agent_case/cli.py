#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — the operator surface: ``timeline``, ``build``, ``verify``.

Three subcommands over one agent session, every one of which takes ``--json``:

===========  =============================================================
``timeline`` join the per-session records ICDEV already writes and print
             them in order (``session_timeline``)
``build``    write that session out as a portable SHA-256-manifested case
             bundle (``case_bundler``)
``verify``   re-check a bundle's manifest digests, ``hook_events`` HMACs and
             the migration-149 audit hash chain, naming WHICH records failed
             (``bundle_verifier``)
===========  =============================================================

**Deliberately CLI-only, with no dashboard page.** A page would trip the
8-component completeness gate in CLAUDE.md — template, ``icdev/`` mirrored
template, blueprint route, backing module, constants, migration, nav link and
full IQE wiring (adapter, ``POST /api/iqe-query``, the widget include, a
``_CANVAS_MAP`` entry, a ``PATH_CANVAS`` entry, and three seed queries) — and
shipping a template without the other seven is named in CLAUDE.md as a repeated
past failure. A CASE UI is a separate card, and it must land all eight together.

Exit codes are the verifier's, so a script can branch on them uniformly:

= ===========================================================================
0 succeeded / every layer passed
1 a verification layer FAILED, or the command errored
2 nothing failed but something could not be verified (indeterminate)
3 the bundle is unreadable
= ===========================================================================

``timeline`` and ``build`` exit 0 on an empty session. A session with no records
is a finding the operator needs to see reported, not an error to be raised at
them — and ``--json`` callers should not have to distinguish "no rows" from
"the query broke".

Usage:
    python tools/agent_case/cli.py timeline --session <id> [--json]
    python tools/agent_case/cli.py build --session <id> --out <dir> [--json]
    python tools/agent_case/cli.py verify --bundle <dir> [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run by path, sys.path[0] is this file's own directory — never the import root.
# Bootstrap it before the first first-party import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_case.bundle_verifier import (  # noqa: E402
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNREADABLE,
    HMAC_SECRET_ENV,
    LAYERS,
    format_report,
    verify_bundle,
)
from tools.agent_case.case_bundler import build_case_bundle, format_bundle  # noqa: E402
from tools.agent_case.session_timeline import (  # noqa: E402
    build_timeline,
    format_timeline,
)


def _emit(payload: dict, text: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(text)


def _fail(message: str, as_json: bool, exit_code: int = EXIT_FAILED) -> int:
    """Report an error on the same channel the caller asked for.

    A ``--json`` caller that gets a traceback on stderr and nothing on stdout
    has to parse Python to find out what went wrong, so the error is a JSON
    document too. It always carries ``ok: false`` — a consumer should never have
    to infer failure from the absence of a key.
    """
    if as_json:
        print(json.dumps({"ok": False, "error": message,
                          "exit_code": exit_code}, indent=2))
    else:
        print(f"CUI // SP-CTI\nERROR: {message}", file=sys.stderr)
    return exit_code


def cmd_timeline(args) -> int:
    try:
        result = build_timeline(args.session, since=args.since, until=args.until,
                               limit=args.limit)
    except Exception as exc:  # surfaced, never swallowed
        return _fail(f"timeline failed for session {args.session!r}: {exc}", args.json)
    result["ok"] = True
    _emit(result, format_timeline(result), args.json)
    return EXIT_OK


def cmd_build(args) -> int:
    try:
        result = build_case_bundle(args.session, args.out, since=args.since,
                                  until=args.until, limit=args.limit,
                                  overwrite=args.force)
    except FileExistsError as exc:
        return _fail(str(exc), args.json)
    except Exception as exc:
        return _fail(f"bundle build failed for session {args.session!r}: {exc}",
                     args.json)
    result["ok"] = True
    _emit(result, format_bundle(result), args.json)
    return EXIT_OK


def cmd_verify(args) -> int:
    try:
        report = verify_bundle(args.bundle, secret=args.secret, layers=args.layer)
    except Exception as exc:
        return _fail(f"verification could not run against {args.bundle!r}: {exc}",
                     args.json, exit_code=EXIT_UNREADABLE)
    exit_code = report.get("exit_code", EXIT_FAILED)
    report["ok"] = exit_code == EXIT_OK
    _emit(report, format_report(report), args.json)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/agent_case/cli.py",
        description="AGOV CASE — agent-session timeline, case bundle, and bundle "
                    "verification. CLI-only by design; there is no dashboard page.",
        epilog="Exit codes: 0 ok / 1 a layer FAILED or the command errored / "
               "2 indeterminate / 3 bundle unreadable.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    timeline = sub.add_parser(
        "timeline", help="Ordered per-session timeline over the tables ICDEV writes")
    timeline.add_argument("--session", required=True, help="session_id to reconstruct")
    timeline.add_argument("--since", help="Inclusive ISO-8601 lower bound")
    timeline.add_argument("--until", help="Inclusive ISO-8601 upper bound")
    timeline.add_argument("--limit", type=int, help="Max rows per source")
    timeline.add_argument("--json", action="store_true", help="Emit JSON")
    timeline.set_defaults(func=cmd_timeline)

    build = sub.add_parser(
        "build", help="Write the session out as a portable SHA-256-manifested bundle")
    build.add_argument("--session", required=True, help="session_id to export")
    build.add_argument("--out", required=True, help="Bundle directory to write")
    build.add_argument("--since", help="Inclusive ISO-8601 lower bound")
    build.add_argument("--until", help="Inclusive ISO-8601 upper bound")
    build.add_argument("--limit", type=int, help="Max rows per source")
    build.add_argument("--force", action="store_true",
                       help="Replace an existing bundle in --out")
    build.add_argument("--json", action="store_true", help="Emit JSON")
    build.set_defaults(func=cmd_build)

    verify = sub.add_parser(
        "verify", help="Re-check a bundle and name which records failed which layer")
    verify.add_argument("--bundle", required=True, help="Path to the bundle directory")
    verify.add_argument("--layer", action="append", choices=LAYERS,
                        help="Verify only this layer (repeatable). Default: all three.")
    verify.add_argument("--secret", help=f"HMAC key; defaults to ${HMAC_SECRET_ENV}")
    verify.add_argument("--json", action="store_true", help="Emit JSON")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
