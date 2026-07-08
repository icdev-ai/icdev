#!/usr/bin/env python3
"""kanban CLI — one-stop wrapper for kanban_tasks operations via get_connection().

Replaces the pattern of writing throwaway .tmp/sqlite3 scripts.
Always routes through the configured storage backend (PostgreSQL in production).

Usage examples:
  # Mark one or more tasks done
  python tools/kanban/cli.py --set-status zig-ext-08 done
  python tools/kanban/cli.py --set-status zig-ext-08 zig-ext-09 zig-ext-10 done

  # Show a single task
  python tools/kanban/cli.py --show zig-ext-08

  # List tasks by prefix and/or status
  python tools/kanban/cli.py --list --prefix zig-ext
  python tools/kanban/cli.py --list --prefix zig-ext --status backlog
  python tools/kanban/cli.py --list --status in_progress

  # JSON output (pipe-friendly)
  python tools/kanban/cli.py --list --prefix zig-ext --json
  python tools/kanban/cli.py --show zig-ext-08 --json
"""

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python tools/kanban/cli.py` from the repo root
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
load_dotenv(_repo_root / ".env")

from icdev.tools.db.storage import get_connection

VALID_STATUSES = frozenset({
    "backlog", "scheduled", "in_progress", "done",
    "failed", "suggested", "needs_decomposition",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_manual_transition(conn, task_id: str, from_status, to_status: str) -> None:
    """Append a 'manual' row to kanban_status_transitions (best-effort).

    The scheduler's stale-reaper (tools/genesis/reflexes/kanban.py::
    _reap_stale_in_progress) fast-reaps any in_progress task after just
    KANBAN_SILENT_DISPATCH_THRESHOLD_SECONDS (default 60s) if its
    .tmp/kanban/<id>.log is empty and it isn't tracked in the scheduler's
    own _running dict — a signal meant to catch a scheduler-dispatched
    subprocess that died before writing any output. This CLI never spawns
    a subprocess or writes that log file, so without this record the
    reaper cannot distinguish "scheduler dispatch died silently" from "a
    human/external session is legitimately working this task out-of-band"
    and reaps it back to backlog within a minute regardless of how long
    the real work actually takes. Recording actor='manual' here lets the
    reaper apply its normal (much longer) timeout instead. Never raises —
    audit-log failure (e.g. migration 025 not yet run) must not block the
    primary status update.
    """
    try:
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                "kst-" + secrets.token_hex(6),
                task_id, from_status, to_status, "manual",
                "tools/kanban/cli.py --set-status", _now(),
            ),
        )
    except Exception:
        pass


def cmd_set_status(task_ids: list, status: str, json_out: bool) -> int:
    if status not in VALID_STATUSES:
        print(
            f"ERROR: invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    now = _now()
    results = []
    with get_connection() as conn:
        for tid in task_ids:
            prior_row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            prior_status = dict(prior_row)["status"] if prior_row else None

            if status == "done":
                conn.execute(
                    "UPDATE kanban_tasks SET status = %s, updated_at = %s, completed_at = %s WHERE id = %s",
                    (status, now, now, tid),
                )
            else:
                conn.execute(
                    "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
                    (status, now, tid),
                )
            if prior_row:
                _record_manual_transition(conn, tid, prior_status, status)
            row = conn.execute(
                "SELECT id, title, status FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            if row:
                results.append(dict(row))
            else:
                results.append({"id": tid, "error": "not found"})

    if json_out:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if "error" in r:
                print(f"  NOT FOUND: {r['id']}")
            else:
                print(f"  {r['id']}: {r['status']}  {r.get('title', '')[:70]}")
    return 0


def cmd_show(task_id: str, json_out: bool) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, status, priority, task_type, "
            "       created_at, updated_at, completed_at, "
            "       failure_count, last_failure_reason "
            "FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()

    if not row:
        print(f"NOT FOUND: {task_id}", file=sys.stderr)
        return 1

    d = dict(row)
    if json_out:
        print(json.dumps(d, indent=2, default=str))
    else:
        print(f"  id:       {d['id']}")
        print(f"  title:    {d['title']}")
        print(f"  status:   {d['status']}")
        print(f"  priority: {d.get('priority', '-')}")
        print(f"  type:     {d.get('task_type', '-')}")
        print(f"  updated:  {d.get('updated_at', '-')}")
        if d.get("last_failure_reason"):
            print(f"  failure:  {d['last_failure_reason'][:120]}")
    return 0


def cmd_list(prefix: str | None, status: str | None, json_out: bool) -> int:
    conditions = []
    params = []

    if prefix:
        conditions.append("id LIKE %s")
        params.append(prefix + "%")
    if status:
        if status not in VALID_STATUSES:
            print(f"ERROR: invalid status '{status}'.", file=sys.stderr)
            return 1
        conditions.append("status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, title, status, priority FROM kanban_tasks {where} ORDER BY id",
            params,
        ).fetchall()

    results = [dict(r) for r in rows]

    if json_out:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("  (no tasks found)")
        for r in results:
            print(f"  [{r['status']:15s}] {r['id']:25s} {r.get('title','')[:60]}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Kanban task manager — always routes through get_connection() (PG in prod).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")

    parser.add_subparsers(dest="cmd")

    # --set-status <id ...> <status>  (positional shorthand — args after flag)
    parser.add_argument(
        "--set-status",
        nargs="+",
        metavar=("TASK_ID", "STATUS"),
        help="Mark one or more tasks with a status. Last argument is the status.",
    )

    # --show <id>
    parser.add_argument("--show", metavar="TASK_ID", help="Show details of one task")

    # --list [--prefix PREFIX] [--status STATUS]
    parser.add_argument("--list", action="store_true", help="List tasks")
    parser.add_argument("--prefix", metavar="PREFIX", help="Filter by id prefix (used with --list)")
    parser.add_argument("--status", metavar="STATUS", help="Filter by status (used with --list)")

    args = parser.parse_args()

    if args.set_status:
        tokens = args.set_status
        if len(tokens) < 2:
            parser.error("--set-status requires at least one task ID and a status.")
        status = tokens[-1]
        task_ids = tokens[:-1]
        sys.exit(cmd_set_status(task_ids, status, args.json_out))

    elif args.show:
        sys.exit(cmd_show(args.show, args.json_out))

    elif args.list:
        sys.exit(cmd_list(args.prefix, args.status, args.json_out))

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
