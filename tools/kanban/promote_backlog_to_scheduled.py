# CUI // SP-CTI
"""Promote eligible backlog tasks to SCHEDULED.

SQL is authored for PostgreSQL (%s placeholders). This module previously used
bare `?` and leaned on translate_sql, which is an init-only fallback and must
never be load-bearing at runtime (see CLAUDE.md).

This script identifies backlog tasks whose dependencies are satisfied
(scalar + junction deps all done/decomposed), sets their status to 'scheduled'
with a current timestamp, and logs the promotion.

Usage:
    python tools/kanban/promote_backlog_to_scheduled.py [--dry-run]
    python tools/kanban/promote_backlog_to_scheduled.py --project acf --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



from tools.kanban.gates import is_manual_gate as _is_manual_gate  # noqa: F401

def _deps_satisfied(task_id: str, conn) -> bool:
    """Check if ALL dependencies (scalar + junction) are done/decomposed."""
    # Scalar dep
    row = conn.execute(
        "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s", (task_id,)
    ).fetchone()
    if row:
        scalar_dep = dict(row).get("depends_on_task_id")
        if scalar_dep:
            dep_row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (scalar_dep,)
            ).fetchone()
            if not dep_row or dict(dep_row)["status"] not in ("done", "decomposed"):
                return False

    # Junction deps
    jdeps = conn.execute(
        "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s",
        (task_id,),
    ).fetchall()
    for r in jdeps:
        dep_id = dict(r)["depends_on_id"]
        dep_row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = %s", (dep_id,)
        ).fetchone()
        if not dep_row or dict(dep_row)["status"] not in ("done", "decomposed"):
            return False

    return True


def promote(
    *,
    dry_run: bool = False,
    project_filter: str | None = None,
    max_tasks: int | None = None,
) -> list[str]:
    """Promote eligible backlog tasks to scheduled.

    Args:
        dry_run: validate and report but don't write.
        project_filter: only promote tasks from this project_id (e.g. 'acf').
        max_tasks: cap the number of promotions (None = all eligible).

    Returns:
        List of promoted task IDs.
    """
    conn = get_connection()
    try:
        # Build query
        where = "WHERE status = 'backlog'"
        params: list = []
        if project_filter:
            where += " AND project_id = %s"
            params.append(project_filter)

        rows = conn.execute(
            f"SELECT id, title, priority, project_id, depends_on_task_id "  # nosec B608
            f"FROM kanban_tasks {where} "
            "ORDER BY "
            "  CASE WHEN depends_on_task_id IS NOT NULL THEN 0 ELSE 1 END, "
            "  CASE priority "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'high' THEN 1 "
            "    WHEN 'medium' THEN 2 "
            "    ELSE 3 END, "
            "  created_at ASC",
            tuple(params),
        ).fetchall()

        tasks = [dict(r) for r in rows]
        eligible = []
        for t in tasks:
            # A manual gate has NO dependencies, so _deps_satisfied() happily calls it
            # "ready" — promoting it out of the very state that makes it a gate. Skip.
            if _is_manual_gate(t["id"], t.get("title")):
                continue
            if _deps_satisfied(t["id"], conn):
                eligible.append(t)
            if max_tasks and len(eligible) >= max_tasks:
                break

        if max_tasks:
            eligible = eligible[:max_tasks]

        if not eligible:
            print("No eligible backlog tasks to promote.")
            return []

        now_iso = _utcnow_iso()
        promoted: list[str] = []

        print(f"{'[DRY-RUN] ' if dry_run else ''}Promoting {len(eligible)} backlog task(s) to SCHEDULED:")
        for t in eligible:
            tid = t["id"]
            proj = t.get("project_id") or "(none)"
            print(f"  -> {tid} | {proj} | {t['priority']} | {t['title'][:60]}")
            if not dry_run:
                conn.execute(
                    "UPDATE kanban_tasks SET status = 'scheduled', scheduled_at = %s, updated_at = %s "
                    "WHERE id = %s AND status = 'backlog'",
                    (now_iso, now_iso, tid),
                )
                promoted.append(tid)

        if not dry_run:
            conn.commit()
            print(f"Committed {len(promoted)} promotion(s).")

        return promoted

    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Promote eligible backlog tasks to SCHEDULED")
    ap.add_argument("--dry-run", action="store_true", help="Validate and report but don't write")
    ap.add_argument("--project", help="Only promote tasks from this project (e.g. 'acf')")
    ap.add_argument("--max", type=int, dest="max_tasks", help="Max number of tasks to promote")
    args = ap.parse_args(argv)

    promote(
        dry_run=args.dry_run,
        project_filter=args.project,
        max_tasks=args.max_tasks,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
