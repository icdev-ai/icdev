#!/usr/bin/env python3
# CUI // SP-CTI
"""Internal Awareness Engine — Phase promoter.

⚠️  DEPRECATED as of 2026-04-11 (migration 015 / task-61f0bed63b).

Native task dependency support landed in ``kanban_tasks.depends_on_task_id``
plus a listener-side gate in ``tools/genesis/reflexes/kanban.py::_get_due_tasks``.
New sequential workflows should set ``depends_on_task_id`` at create time
and drop phases straight into ``backlog`` — the listener itself now refuses
to promote blocked tasks.

This script is preserved for backward compatibility with any phase tasks
whose description still references it. It continues to work on
status='scheduled' phases exactly as before.

Usage:
    python tools/awareness/promote_next_phase.py --after <completed_task_id>
    python tools/awareness/promote_next_phase.py --phase 2
    python tools/awareness/promote_next_phase.py --list

The script:
  1. Identifies the caller's phase (from --after <task_id> or --phase <N>)
  2. Finds Phase N+1 in kanban_tasks (title LIKE 'Phase N+1/6%')
  3. If Phase N+1 exists and is in 'scheduled' status: sets status=backlog,
     clears scheduled_at, sets updated_at to 11 min ago (bypasses cooldown)
  4. Prints the promoted task_id + title as JSON
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _phase_sort_key(title: str) -> tuple:
    """Canonical ordering for phase titles.

    Matches both plain phases (`Phase N/6`) and split sub-phases
    (`Phase Na/6`, `Phase Nb/6`, ...). Returns a tuple that sorts
    correctly: `(main_num, sub_letter)`. Non-matching titles sort last.

    Examples:
        "Phase 1/6: Foo"    -> (1, "")
        "Phase 1a/6: Foo"   -> (1, "a")
        "Phase 1b/6: Foo"   -> (1, "b")
        "Phase 2/6: Foo"    -> (2, "")
    """
    m = re.match(r"Phase (\d)([a-z])?/6", title)
    if not m:
        return (999, "z")
    return (int(m.group(1)), m.group(2) or "")


def _all_phase_tasks_sorted() -> list[dict]:
    """Return every phase task (including sub-phases) in canonical
    execution order.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, status, scheduled_at, title "
            "FROM kanban_tasks WHERE title LIKE 'Phase %%/6%%'"
        ).fetchall()
        phases = [dict(r) for r in rows]
        phases.sort(key=lambda r: _phase_sort_key(r["title"]))
        return phases
    finally:
        conn.close()


def _list_phases() -> list[dict]:
    """Return every phase task in order, JSON-serializable."""
    result: list[dict] = []
    for d in _all_phase_tasks_sorted():
        if d.get("scheduled_at") and hasattr(d["scheduled_at"], "isoformat"):
            d["scheduled_at"] = d["scheduled_at"].isoformat()
        result.append(
            {
                "id": d["id"],
                "status": d["status"],
                "scheduled_at": d.get("scheduled_at"),
                "title": d["title"][:80],
            }
        )
    return result


def _find_next_phase_task(current_task_id: str | None, explicit_phase: int | None) -> dict | None:
    """Find the next phase after the given completed task.

    Two resolution modes:
      1. `--after <task_id>`: locate the task in the canonical-ordered
         list and return the one immediately after it.
      2. `--phase <N>`: legacy numeric form — return Phase (N+1)/6 or
         Phase (N+1)a/6 if the next main number was split.

    Handles both plain phases and sub-phases uniformly because the
    canonical sort is title-based, not computed from the phase number.
    """
    phases = _all_phase_tasks_sorted()
    if not phases:
        return None

    target_index = -1
    if current_task_id:
        for i, p in enumerate(phases):
            if p["id"] == current_task_id:
                target_index = i
                break
    elif explicit_phase is not None:
        # Find the LAST task whose main phase number equals explicit_phase
        # (so --phase 1 on a sub-phase chain jumps past all of 1a..1g).
        for i in range(len(phases) - 1, -1, -1):
            key = _phase_sort_key(phases[i]["title"])
            if key[0] == explicit_phase:
                target_index = i
                break

    if target_index < 0 or target_index + 1 >= len(phases):
        return None
    return phases[target_index + 1]


def _promote(next_task: dict) -> dict:
    """Keep the promoted phase task in the SCHEDULED lane but bump its
    scheduled_at to 5 minutes ago so the kanban reflex's scheduled
    promotion query (scheduled_at <= NOW()) immediately marks it as
    due. This preserves the visual convention of all six phases sitting
    in the SCHEDULED column on the kanban board, with the currently
    active phase distinguished only by its past scheduled_at.

    Setting scheduled_at to the past instead of the future avoids
    polluting the board with a task that jumps out of the scheduled
    column mid-flight and then back in.
    """
    now = datetime.now(timezone.utc)
    five_min_ago = (now - timedelta(minutes=5)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE kanban_tasks SET status = 'scheduled', scheduled_at = %s, "
            "updated_at = %s WHERE id = %s",
            (five_min_ago, now.isoformat(), next_task["id"]),
        )
        conn.commit()
        return {
            "promoted_task_id": next_task["id"],
            "promoted_title": next_task["title"],
            "previous_status": next_task["status"],
            "new_status": "scheduled",
            "scheduled_at": five_min_ago,
            "note": "Task is in SCHEDULED lane with scheduled_at in the past — the listener will promote to in_progress on its next cycle.",
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote the next Internal Awareness Engine phase")
    parser.add_argument("--after", type=str, default="", help="Task ID that just completed (infers phase)")
    parser.add_argument("--phase", type=int, default=0, help="Explicit current phase number (1-6)")
    parser.add_argument("--list", action="store_true", help="List all phases and their current status")
    parser.add_argument("--json", action="store_true", help="JSON output (default)")
    args = parser.parse_args()

    if args.list:
        phases = _list_phases()
        print(json.dumps({"phases": phases}, indent=2))
        return 0

    if not args.after and not args.phase:
        print(json.dumps({"error": "pass --after <task_id> or --phase <N>"}))
        return 2

    next_task = _find_next_phase_task(args.after or None, args.phase or None)
    if not next_task:
        print(json.dumps({"info": "no next phase to promote (end of chain, or current task not found)"}))
        return 0
    if next_task["status"] not in ("scheduled", "token_exhausted"):
        print(json.dumps({
            "info": f"next phase is already in status={next_task['status']}, nothing to do",
            "task_id": next_task["id"],
            "title": next_task["title"],
        }))
        return 0

    result = _promote(next_task)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
