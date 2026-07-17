# CUI // SP-CTI
"""Analyze backlog tasks to determine which batch can be promoted to SCHEDULED.

This script queries kanban_tasks for all BACKLOG tasks and evaluates:
1. Dependency satisfaction (scalar + junction deps)
2. Priority order (critical > high > medium > low)
3. Phase-exit gates (for phased task IDs like efa-E3-*)
4. Recent update cooldown (2 min)
5. Quarantine status

It then produces a prioritized list of promotable tasks, grouped by project.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _phase_info(task_id: str) -> tuple[str, str] | None:
    """Extract (prefix, phase) from phased IDs like efa-E3-01 -> ('efa', 'E3')."""
    import re
    m = re.match(r"^([a-z0-9]+)-([A-Z]\d+)-\d+$", task_id)
    if m:
        return m.group(1), m.group(2)
    return None


def _phase_complete(prefix: str, phase: str, conn) -> tuple[bool, list[str]]:
    """Check if ALL tasks in prior phase are done/decomposed."""
    pattern = f"{prefix}-{phase}-%"
    rows = conn.execute(
        "SELECT id, status FROM kanban_tasks WHERE id LIKE %s",
        (pattern,),
    ).fetchall()
    if not rows:
        return True, []
    undone = []
    for r in rows:
        d = dict(r)
        if d["status"] not in ("done", "decomposed"):
            undone.append(d["id"])
    return len(undone) == 0, undone


def _deps_satisfied(task_id: str, conn) -> tuple[bool, list[str]]:
    """Check if ALL dependencies (scalar + junction) are done/decomposed.
    Returns (ok, list of blocking dep ids).
    """
    blocking: list[str] = []

    # Scalar dep
    row = conn.execute(
        "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s",
        (task_id,),
    ).fetchone()
    if row:
        scalar_dep = dict(row).get("depends_on_task_id")
        if scalar_dep:
            dep_row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (scalar_dep,)
            ).fetchone()
            if not dep_row:
                blocking.append(f"{scalar_dep} (missing)")
            elif dict(dep_row)["status"] not in ("done", "decomposed"):
                blocking.append(scalar_dep)

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
        if not dep_row:
            blocking.append(f"{dep_id} (missing)")
        elif dict(dep_row)["status"] not in ("done", "decomposed"):
            blocking.append(dep_id)

    return len(blocking) == 0, blocking


def analyze_backlog() -> None:
    conn = get_connection()
    try:
        # Count totals
        total_backlog = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'backlog'"
        ).fetchone()[0]
        total_scheduled = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'scheduled'"
        ).fetchone()[0]
        total_in_progress = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'in_progress'"
        ).fetchone()[0]

        print("=" * 80)
        print("KANBAN BACKLOG ANALYSIS")
        print("=" * 80)
        print(f"Total backlog:     {total_backlog}")
        print(f"Total scheduled:   {total_scheduled}")
        print(f"Total in_progress: {total_in_progress}")
        print()

        # Fetch all backlog tasks, ordered by priority then age
        rows = conn.execute(
            "SELECT id, title, description, priority, created_at, updated_at, "
            "       depends_on_task_id, project_id, last_failure_reason "
            "FROM kanban_tasks "
            "WHERE status = 'backlog' "
            "ORDER BY "
            "  CASE WHEN depends_on_task_id IS NOT NULL THEN 0 ELSE 1 END, "
            "  CASE priority "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'high' THEN 1 "
            "    WHEN 'medium' THEN 2 "
            "    ELSE 3 END, "
            "  created_at ASC"
        ).fetchall()

        tasks = [dict(r) for r in rows]

        # Filter: cooldown (updated within last 2 min)
        now = _utcnow()
        cooldown_tasks = [
            t for t in tasks
            if t.get("updated_at")
            and (isinstance(t["updated_at"], datetime) or isinstance(t["updated_at"], str))
            and datetime.fromisoformat(str(t["updated_at"]).replace("Z", "+00:00")) > (now - timedelta(minutes=2))
        ]
        tasks = [t for t in tasks if t not in cooldown_tasks]

        # Filter: quarantined
        quarantine_tasks = [
            t for t in tasks
            if t.get("last_failure_reason") and "QUARANTINED by self_debug" in t["last_failure_reason"]
        ]
        tasks = [t for t in tasks if t not in quarantine_tasks]

        # Evaluate deps and phase gates
        promotable: list[dict] = []
        blocked: list[dict] = []
        phase_blocked: list[dict] = []

        for t in tasks:
            tid = t["id"]

            # Check deps
            deps_ok, blocking = _deps_satisfied(tid, conn)
            if not deps_ok:
                t["_blocking"] = blocking
                blocked.append(t)
                continue

            # Check phase-exit gate
            pinfo = _phase_info(tid)
            if pinfo:
                prefix, phase = pinfo
                if phase != "A":
                    prior_phase = chr(ord(phase[0]) - 1) + phase[1:]
                    complete, undone = _phase_complete(prefix, prior_phase, conn)
                    if not complete:
                        t["_phase"] = f"{prefix}-{prior_phase}"
                        t["_unfinished"] = undone[:5]
                        phase_blocked.append(t)
                        continue

            promotable.append(t)

        # ---- Report: Promotable tasks ----
        print(f"PROMOTABLE BACKLOG TASKS: {len(promotable)}")
        print("-" * 80)

        # Group by project
        by_project: dict[str, list[dict]] = {}
        for t in promotable:
            pid = t.get("project_id") or "(none)"
            by_project.setdefault(pid, []).append(t)

        # Sort projects: acf first, then by count descending
        sorted_projects = sorted(
            by_project.keys(),
            key=lambda p: (0 if p == "acf" else 1, -len(by_project[p]))
        )

        for proj in sorted_projects:
            tasks_in_proj = by_project[proj]
            print(f"\n[{proj}] -- {len(tasks_in_proj)} task(s)")
            for t in tasks_in_proj:
                dep_str = ""
                if t.get("depends_on_task_id"):
                    dep_str = f" -> after {t['depends_on_task_id']}"
                jdeps = conn.execute(
                    "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s",
                    (t["id"],),
                ).fetchall()
                if jdeps:
                    dep_str += f" +{len(jdeps)} junction dep(s)"
                print(
                    f"  {t['id']} | {t['priority']:<7} | {t['title'][:60]}{dep_str}"
                )

        # ---- Report: Blocked tasks ----
        print(f"\n\nBLOCKED BY DEPENDENCIES: {len(blocked)}")
        print("-" * 80)
        by_project_blocked: dict[str, list[dict]] = {}
        for t in blocked:
            pid = t.get("project_id") or "(none)"
            by_project_blocked.setdefault(pid, []).append(t)
        for proj in sorted(by_project_blocked.keys(), key=lambda p: 0 if p == "acf" else 1):
            print(f"\n[{proj}] -- {len(by_project_blocked[proj])} blocked task(s)")
            for t in by_project_blocked[proj]:
                print(f"  {t['id']} | {t['priority']:<7} | waiting on: {', '.join(t['_blocking'][:3])}")

        # ---- Report: Phase-blocked tasks ----
        print(f"\n\nBLOCKED BY PHASE-EXIT GATE: {len(phase_blocked)}")
        print("-" * 80)
        for t in phase_blocked:
            print(
                f"  {t['id']} | {t['priority']:<7} | "
                f"waiting for phase {t['_phase']} ({len(t['_unfinished'])} unfinished)"
            )

        # ---- Report: Quarantined tasks ----
        print(f"\n\nQUARANTINED (skipped): {len(quarantine_tasks)}")
        print("-" * 80)
        for t in quarantine_tasks:
            print(f"  {t['id']} | {t['priority']:<7} | {t['title'][:50]}")

        # ---- Report: Cooldown tasks ----
        print(f"\n\nCOOLDOWN (< 2 min since update): {len(cooldown_tasks)}")
        print("-" * 80)
        for t in cooldown_tasks:
            print(f"  {t['id']} | {t['priority']:<7} | updated_at={t['updated_at']}")

        # ---- Recommendation ----
        print("\n" + "=" * 80)
        print("RECOMMENDATION")
        print("=" * 80)

        MAX_AUTO_PROMOTE = 3  # matches kanban.py default
        batch = promotable[:MAX_AUTO_PROMOTE]
        print(f"Promote the top {len(batch)} backlog tasks to SCHEDULED:")
        for t in batch:
            print(f"  -> {t['id']} ({t['project_id'] or 'no project'})")

        if len(promotable) > MAX_AUTO_PROMOTE:
            print(f"\n  ({len(promotable) - MAX_AUTO_PROMOTE} more promotable tasks will be picked up in subsequent cycles)")

    finally:
        conn.close()


if __name__ == "__main__":
    analyze_backlog()
