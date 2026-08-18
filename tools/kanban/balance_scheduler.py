# CUI // SP-CTI
"""Kanban backlog/scheduled auto-balancer.

Periodically promotes eligible backlog tasks to SCHEDULED and demotes
scheduled tasks with unsatisfied dependencies back to BACKLOG.  The goal
is to keep a healthy "ready" buffer (scheduled) while never breaking
dependency chains or letting one project dominate the dispatch queue.

Usage:
    python tools/kanban/balance_scheduler.py [--dry-run]
    python tools/kanban/balance_scheduler.py --project acf --dry-run

Intended to be run:
  * Manually before a scheduler cycle
  * As a Genesis reflex (cadence: every 15 min)
  * By the kanban scheduler itself at the top of each cycle
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Same predicate the dispatcher uses (kpr-fix-02). A balancer that answered a
#: different question would report a queue depth the runner cannot drain.
from tools.kanban.deps import deps_satisfied as _deps_satisfied  # noqa: E402


def _count_by_project(conn) -> dict[str, dict[str, int]]:
    """Return {project_id: {"backlog": n, "scheduled": n, "in_progress": n, "total": n}}."""
    rows = conn.execute(
        "SELECT project_id, status, COUNT(*) as n "
        "FROM kanban_tasks "
        "WHERE status IN ('backlog', 'scheduled', 'in_progress') "
        "GROUP BY project_id, status"
    ).fetchall()
    data: dict[str, dict[str, int]] = defaultdict(lambda: {"backlog": 0, "scheduled": 0, "in_progress": 0, "total": 0})
    for r in rows:
        d = dict(r)
        pid = d.get("project_id") or "(none)"
        status = d["status"]
        data[pid][status] = d["n"]
        data[pid]["total"] += d["n"]
    return dict(data)


def balance(
    *,
    dry_run: bool = False,
    project_filter: str | None = None,
    max_total_promotions: int = 10,
    max_per_project: int = 3,
    target_scheduled_ratio: float = 0.15,
    demote_blocked: bool = True,
) -> dict:
    """Run the balance pass.

    Args:
        dry_run: validate and report but don't write.
        project_filter: only balance this project.
        max_total_promotions: hard cap on how many backlog tasks to promote.
        max_per_project: hard cap per project.
        target_scheduled_ratio: ideal (scheduled / total) for a project.
        demote_blocked: if True, move scheduled tasks with unsatisfied deps
            back to backlog (keeps the scheduled queue clean).

    Returns:
        Result dict with lists of promoted, demoted, and stats.
    """
    conn = get_connection()
    try:
        result = {
            "promoted": [],
            "demoted": [],
            "stats_before": {},
            "stats_after": {},
            "dry_run": dry_run,
        }
        now_iso = _utcnow_iso()

        # ── Phase 1: Demote blocked scheduled tasks ──────────────────────────
        if demote_blocked:
            scheduled_rows = conn.execute(
                "SELECT id, title, project_id, depends_on_task_id "
                "FROM kanban_tasks WHERE status = 'scheduled'"
            ).fetchall()
            for r in scheduled_rows:
                t = dict(r)
                tid = t["id"]
                if project_filter and t.get("project_id") != project_filter:
                    continue
                if not _deps_satisfied(tid, conn):
                    if not dry_run:
                        conn.execute(
                            "UPDATE kanban_tasks SET status = 'backlog', "
                            "scheduled_at = NULL, updated_at = %s WHERE id = %s",
                            (now_iso, tid),
                        )
                    result["demoted"].append({
                        "id": tid,
                        "project_id": t.get("project_id"),
                        "reason": "unsatisfied dependencies",
                    })

        # ── Phase 2: Gather eligible backlog tasks ───────────────────────────
        where = "WHERE status = 'backlog'"
        params: list = []
        if project_filter:
            where += " AND project_id = ?"
            params.append(project_filter)

        rows = conn.execute(
            f"SELECT id, title, priority, project_id, depends_on_task_id, "  # nosec B608
            f"       updated_at, last_failure_reason "
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

        eligible: list[dict] = []
        for r in rows:
            t = dict(r)
            # Skip recently updated (cooldown)
            if t.get("updated_at"):
                try:
                    updated = datetime.fromisoformat(str(t["updated_at"]).replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - updated).total_seconds() < 120:
                        continue
                except Exception:
                    pass
            # Skip quarantined
            if t.get("last_failure_reason") and "QUARANTINED by self_debug" in t["last_failure_reason"]:
                continue
            # Dep check
            if _deps_satisfied(t["id"], conn):
                eligible.append(t)

        # ── Phase 3: Apply balancing caps ────────────────────────────────────
        counts = _count_by_project(conn)
        promoted: list[dict] = []
        per_project_count: dict[str, int] = defaultdict(int)

        for t in eligible:
            if len(promoted) >= max_total_promotions:
                break
            pid = t.get("project_id") or "(none)"
            if project_filter and pid != project_filter:
                continue

            # Cap per-project
            if per_project_count[pid] >= max_per_project:
                continue

            # Ratio cap: don't over-schedule a project
            proj = counts.get(pid, {"backlog": 0, "scheduled": 0, "in_progress": 0, "total": 0})
            scheduled_total = proj.get("scheduled", 0) + per_project_count[pid]
            project_total = proj.get("total", 0)
            if project_total > 0 and (scheduled_total / project_total) > target_scheduled_ratio:
                continue

            if not dry_run:
                conn.execute(
                    "UPDATE kanban_tasks SET status = 'scheduled', scheduled_at = %s, "
                    "updated_at = %s WHERE id = %s AND status = 'backlog'",
                    (now_iso, now_iso, t["id"]),
                )
            promoted.append({
                "id": t["id"],
                "project_id": pid,
                "priority": t.get("priority"),
                "title": t.get("title"),
            })
            per_project_count[pid] += 1

        if not dry_run and (promoted or result["demoted"]):
            conn.commit()

        result["promoted"] = promoted
        result["stats_before"] = counts
        result["stats_after"] = _count_by_project(conn)
        return result

    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kanban backlog/scheduled auto-balancer")
    ap.add_argument("--dry-run", action="store_true", help="Validate and report but don't write")
    ap.add_argument("--project", help="Only balance this project (e.g. 'acf')")
    ap.add_argument("--max-total", type=int, default=10, help="Max backlog tasks to promote")
    ap.add_argument("--max-per-project", type=int, default=3, help="Max promotions per project")
    ap.add_argument("--target-ratio", type=float, default=0.15, help="Target scheduled/total ratio")
    ap.add_argument("--no-demote", action="store_true", help="Skip demoting blocked scheduled tasks")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    args = ap.parse_args(argv)

    result = balance(
        dry_run=args.dry_run,
        project_filter=args.project,
        max_total_promotions=args.max_total,
        max_per_project=args.max_per_project,
        target_scheduled_ratio=args.target_ratio,
        demote_blocked=not args.no_demote,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print("=" * 70)
    print("KANBAN BALANCER RESULT")
    print("=" * 70)

    if result["demoted"]:
        print(f"\nDEMOTED to backlog (blocked deps): {len(result['demoted'])}")
        for d in result["demoted"]:
            print(f"  <- {d['id']} ({d.get('project_id') or 'no project'}) — {d['reason']}")

    if result["promoted"]:
        print(f"\nPROMOTED to scheduled: {len(result['promoted'])}")
        for p in result["promoted"]:
            print(f"  -> {p['id']} | {p.get('project_id') or 'no project'} | {p.get('priority')} | {p.get('title', '')[:60]}")
    else:
        print("\nNo backlog tasks eligible for promotion.")

    print("\nPer-project counts:")
    for pid, stats in sorted(result["stats_after"].items(), key=lambda x: -x[1]["total"]):
        s = stats["scheduled"]
        b = stats["backlog"]
        ip = stats["in_progress"]
        total = stats["total"]
        ratio = (s / total * 100) if total else 0
        print(f"  {pid:<12} scheduled={s:>3}  backlog={b:>3}  in_progress={ip:>3}  total={total:>3}  ({ratio:.0f}% scheduled)")

    if args.dry_run:
        print("\n[DRY-RUN] No changes written.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
