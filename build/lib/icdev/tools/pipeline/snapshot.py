#!/usr/bin/env python3
# CUI // SP-CTI
"""CLI for manual PDC pipeline snapshots.

Usage::

    python -m tools.pipeline.snapshot --pipeline-id PIPE_ID
    python -m tools.pipeline.snapshot --pipeline-id PIPE_ID --label "pre-deploy"
    python -m tools.pipeline.snapshot --pipeline-id PIPE_ID --json
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.pipeline.snapshot",
        description="Take a manual PDC pipeline snapshot",
    )
    parser.add_argument("--pipeline-id", required=True, help="Pipeline ID to snapshot")
    parser.add_argument("--label", default=None, help="Optional snapshot label")
    parser.add_argument("--user", default="cli", help="User identity for audit trail")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    from tools.pipeline.twin import take_snapshot

    try:
        snap = take_snapshot(args.pipeline_id, label=args.label, user_id=args.user)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(snap, indent=2))
    else:
        print(f"Snapshot {snap['id']} created for pipeline {snap['pipeline_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
