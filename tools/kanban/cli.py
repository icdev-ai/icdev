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


_RUNNER_PAUSE_RESOURCE = "kanban:runner:global"


def _task_lease_resource(task_id: str) -> str:
    return f"kanban:task:{task_id}"


def cmd_claim(task_id: str, json_out: bool) -> int:
    """Take exclusive ownership of a task for interactive (CLI) work.

    Acquires the per-task coordination lease so the autonomous kanban runner
    skips it, moves it to in_progress, and prints the task's stored branch +
    commit so you continue that branch rather than rebuilding it.
    """
    from tools.coordination import leases
    res = _task_lease_resource(task_id)
    lease = leases.acquire(res, intent="cli-manual", ttl_seconds=3600, block=False)
    if lease is None:
        h = leases.holder(res) or {}
        out = {"claimed": False, "task_id": task_id, "held_by": h.get("holder_session")}
        if json_out:
            print(json.dumps(out, indent=2))
        else:
            print(f"  CANNOT CLAIM {task_id}: held by session {h.get('holder_session', '?')}")
        return 1
    now = _now()
    info = {"claimed": True, "task_id": task_id}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, branch_name, commit_summary FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        if not row:
            print(f"  NOT FOUND: {task_id} (lease acquired; no such task row)", file=sys.stderr)
            return 1
        d = dict(row)
        prior = d.get("status")
        conn.execute(
            "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
            ("in_progress", now, task_id),
        )
        _record_manual_transition(conn, task_id, prior, "in_progress")
        info.update({
            "prior_status": prior,
            "branch": d.get("branch_name"),
            "commit": d.get("commit_summary"),
        })
    if json_out:
        print(json.dumps(info, indent=2, default=str))
    else:
        print(f"  CLAIMED {task_id} (was {info.get('prior_status')}) -> in_progress")
        print(f"    branch: {info.get('branch') or '(none recorded — start fresh off origin/main)'}")
        if info.get("commit"):
            print(f"    last commit: {str(info['commit'])[:100]}")
        print("    runner will skip this task until you --release it.")
    return 0


def cmd_release(task_id: str, json_out: bool) -> int:
    """Release the per-task lease so the runner (or another session) can take it."""
    from tools.coordination import leases
    released = leases.release(_task_lease_resource(task_id))
    if json_out:
        print(json.dumps({"released": released, "task_id": task_id}, indent=2))
    else:
        print(f"  {'RELEASED' if released else 'NOT HELD BY THIS SESSION'}: {task_id}")
    return 0 if released else 1


def cmd_pause_runner(json_out: bool) -> int:
    """Pause the autonomous kanban runner for the current session (interactive work)."""
    from tools.coordination import leases
    lease = leases.acquire(_RUNNER_PAUSE_RESOURCE, intent="cli-session", ttl_seconds=14400, block=False)
    if lease is None:
        h = leases.holder(_RUNNER_PAUSE_RESOURCE) or {}
        if json_out:
            print(json.dumps({"paused": False, "held_by": h.get("holder_session")}, indent=2))
        else:
            print(f"  ALREADY PAUSED by session {h.get('holder_session', '?')}")
        return 1
    if json_out:
        print(json.dumps({"paused": True}, indent=2))
    else:
        print("  RUNNER PAUSED — the autonomous kanban runner will skip dispatch until --resume-runner.")
    return 0


def cmd_resume_runner(json_out: bool) -> int:
    """Resume the autonomous kanban runner (release the global pause lease)."""
    from tools.coordination import leases
    released = leases.release(_RUNNER_PAUSE_RESOURCE)
    if json_out:
        print(json.dumps({"resumed": released}, indent=2))
    else:
        print(f"  {'RUNNER RESUMED' if released else 'NOT PAUSED BY THIS SESSION'}")
    return 0 if released else 1


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

    # Cross-session coordination (CLI <-> autonomous runner handoff)
    parser.add_argument("--claim", metavar="TASK_ID",
                        help="Take exclusive ownership of a task for interactive work "
                             "(runner will skip it); prints its branch/commit to continue")
    parser.add_argument("--release", metavar="TASK_ID",
                        help="Release a task lease so the runner can take it back")
    parser.add_argument("--pause-runner", action="store_true",
                        help="Pause the autonomous kanban runner for this session")
    parser.add_argument("--resume-runner", action="store_true",
                        help="Resume the autonomous kanban runner")

    args = parser.parse_args()

    if args.claim:
        sys.exit(cmd_claim(args.claim, args.json_out))

    elif args.release:
        sys.exit(cmd_release(args.release, args.json_out))

    elif args.pause_runner:
        sys.exit(cmd_pause_runner(args.json_out))

    elif args.resume_runner:
        sys.exit(cmd_resume_runner(args.json_out))

    elif args.set_status:
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
