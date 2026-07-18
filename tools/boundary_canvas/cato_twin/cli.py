#!/usr/bin/env python3
# CUI // SP-CTI
"""cATO Twin CLI — unified entry point for BDC Phase 1 operations.

Sub-commands:
  snapshot   — write a compliance twin snapshot
  query      — run an IQE compliance query
  poam       — generate POA&M from latest snapshot violations
  status     — show run summary for a project+framework
  migrate    — apply migration 027 to the live DB

Examples:
  python tools/boundary_canvas/cato_twin/cli.py snapshot \
      --project-id proj-001 --framework "FedRAMP Moderate" \
      --controls-json /path/to/controls.json

  python tools/boundary_canvas/cato_twin/cli.py query \
      'foreach ctrl in compliance.twin_snapshots("FedRAMP Moderate") \
       where ctrl.status != "satisfied" select ctrl.control_id'

  python tools/boundary_canvas/cato_twin/cli.py poam \
      --snapshot-id snap-<uuid> --project-id proj-001

  python tools/boundary_canvas/cato_twin/cli.py status \
      --project-id proj-001 --framework "FedRAMP Moderate"

  python tools/boundary_canvas/cato_twin/cli.py migrate
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _cmd_snapshot(args):
    from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
    controls = json.loads(Path(args.controls_json).read_text(encoding="utf-8"))
    snap_id = write_snapshot(
        project_id=args.project_id,
        framework=args.framework,
        controls=controls,
        triggered_by=args.triggered_by,
    )
    out = {"snapshot_id": snap_id, "status": "ok"}
    if args.json:
        print(json.dumps(out))
    else:
        print(f"Snapshot written: {snap_id}")


def _cmd_query(args):
    from tools.iqe.adapters.compliance import run_query
    results = run_query(args.query_string)
    if args.json:
        print(json.dumps(results, default=str))
    else:
        for row in results:
            print(row)
        print(f"\n{len(results)} row(s) returned.")


def _cmd_poam(args):
    from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
    result = generate_from_violations(args.snapshot_id, args.project_id)
    if args.json:
        print(json.dumps(result))
    else:
        print(f"New POAM items: {result['new_items']}")
        print(f"Skipped: {result['skipped_items']}")


def _cmd_status(args):
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        runs = conn.execute(
            """SELECT snapshot_id, started_at, completed_at, total_controls,
                      satisfied, partially_satisfied, not_satisfied
               FROM compliance_twin_runs
               WHERE project_id = %s AND framework = %s
               ORDER BY started_at DESC LIMIT 5""",
            (args.project_id, args.framework),
        ).fetchall()
        rows = [dict(r) for r in runs]
        if args.json:
            print(json.dumps(rows, default=str))
        else:
            for r in rows:
                pct = round(r["satisfied"] / r["total_controls"] * 100, 1) if r["total_controls"] else 0
                print(
                    f"[{r['started_at'][:19]}] snap={r['snapshot_id'][:20]}... "
                    f"total={r['total_controls']} sat={r['satisfied']} "
                    f"partial={r['partially_satisfied']} fail={r['not_satisfied']} "
                    f"score={pct}%"
                )
    finally:
        conn.close()


def _cmd_migrate(args):
    from tools.db.storage import get_connection
    from tools.db.migrations.migration_027_compliance_twin_schema.up import up as apply_up
    conn = get_connection()
    try:
        result = apply_up(conn)
        if args.json:
            print(json.dumps(result))
        else:
            print(f"Migration 027: {result['status']}")
            for a in result.get("actions", []):
                print(f"  {a}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="BDC cATO Twin CLI (CUI // SP-CTI)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    # snapshot
    p_snap = sub.add_parser("snapshot", help="Write a compliance twin snapshot")
    p_snap.add_argument("--project-id", required=True, dest="project_id")
    p_snap.add_argument("--framework", required=True)
    p_snap.add_argument("--controls-json", required=True, dest="controls_json")
    p_snap.add_argument("--triggered-by", default="manual", dest="triggered_by")

    # query
    p_query = sub.add_parser("query", help="Run an IQE compliance query")
    p_query.add_argument("query_string", metavar="QUERY", help="IQE query string")

    # poam
    p_poam = sub.add_parser("poam", help="Generate POA&M from snapshot violations")
    p_poam.add_argument("--snapshot-id", required=True, dest="snapshot_id")
    p_poam.add_argument("--project-id", required=True, dest="project_id")

    # status
    p_status = sub.add_parser("status", help="Show run summary")
    p_status.add_argument("--project-id", required=True, dest="project_id")
    p_status.add_argument("--framework", required=True)

    # migrate
    sub.add_parser("migrate", help="Apply migration 027 to live DB")

    args = parser.parse_args()
    dispatch = {
        "snapshot": _cmd_snapshot,
        "query": _cmd_query,
        "poam": _cmd_poam,
        "status": _cmd_status,
        "migrate": _cmd_migrate,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
