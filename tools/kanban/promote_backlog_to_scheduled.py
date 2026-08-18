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

#: A dependency in one of these states no longer holds anything back. A parent
#: split into children is finished for gating purposes, which is what
#: `decomposed` means here.
_DEPS_SATISFIED_STATUSES = ("done", "decomposed")


def _dep_done(dep_id: str, conn) -> bool:
    """True when `dep_id` exists AND has reached a satisfying status.

    A dangling reference returns False on purpose: a prerequisite that is not on
    the board is not evidence that anything finished.
    """
    row = conn.execute(
        "SELECT status FROM kanban_tasks WHERE id = %s", (dep_id,)
    ).fetchone()
    return bool(row) and dict(row)["status"] in _DEPS_SATISFIED_STATUSES


def _deps_satisfied(task_id: str, conn) -> bool:
    """Whether every declared dependency of `task_id` is satisfied.

    THE BOARD CARRIES TWO DEPENDENCY SYSTEMS AND THEY MEAN DIFFERENT THINGS.

      `kanban_task_deps` (junction)  the REAL graph — fan-in prerequisites,
                                     written deliberately. cef-di-03 depends on
                                     cef-rsv-01 and cef-rsv-02.
      `depends_on_task_id` (scalar)  SEEDING ORDER, written when a batch is
                                     created. cef-di-03 "depends on" cef-di-02
                                     only because it was seeded after it.

    This used to require BOTH, so the false linear chain overrode the true
    graph. Measured 2026-08-18: cef-di-03/04/05/06 had every real prerequisite
    satisfied (cef-rsv-01/02/03 all done) and were held solely by seeding order
    — five independent migrations of five different modules onto one
    already-built API, forced to run one at a time. Across a full day exactly
    ONE task was ever in flight: 16 backlog, 15 dependency-blocked, 1 manual
    gate, zero dispatchable. It is also why building a loop and a graph into the
    harness changed nothing: the executor could loop, and a correct graph
    existed, but dispatch was gated by a second, wrong one.

    So: junction rows ARE the declaration when a task has them, and the scalar
    is not consulted at all — reading it "just as a warning" is how it becomes a
    gate again. With no junction rows the scalar IS the only declaration and is
    still honoured, which is exactly what holds a task behind a manual gate
    (kpr-watch-01 -> kpr-gate-02 has zero junction rows and must keep holding).
    """
    jdeps = conn.execute(
        "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s",
        (task_id,),
    ).fetchall()
    if jdeps:
        return all(_dep_done(dict(r)["depends_on_id"], conn) for r in jdeps)

    row = conn.execute(
        "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s", (task_id,)
    ).fetchone()
    scalar_dep = dict(row).get("depends_on_task_id") if row else None
    if not scalar_dep:
        return True
    return _dep_done(scalar_dep, conn)


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
