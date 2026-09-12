# CUI // SP-CTI
"""Seed a multi-epic card as PARALLEL LANES, so the board's capacity is usable.

THE DEFECT THIS EXISTS FOR, and it was mine. On 2026-09-11 a planning session
seeded a 22-task card as ONE linear `depends_on_task_id` chain, because that is
what the seeding convention says ("a linear depends_on chain so the runner takes
them in order"). With nothing else on the board, exactly ONE task could ever be
dispatched: every other row was waiting on its predecessor. The operator asked
why only one job was running against `MAX_IN_PROGRESS = 3`. The repair was a
hand-written `UPDATE kanban_tasks SET depends_on_task_id` over 21 rows -- a raw
board write, which is the shape `args/kanban_raw_insert_census.txt` exists to
keep out of this repo, done by the very session that should know better.

A CHAIN IS NOT WRONG, IT IS ONE LANE. Ordering exists for a real reason: two
sibling tasks that append to the same file collide, and this repo has measured
what that costs -- `merge=union` is not applied by GitHub, so siblings appending
to `args/ci_test_files/core.txt` turned 82.8% of merged kanban PRs CONFLICTING,
costing a rebase on 30.9% and a human on 27.4%. So the unit of ordering is not
"the card" but "the group of tasks that touch the same files". Tasks in
DIFFERENT groups have no reason to wait for each other.

WHAT THIS DOES. `create_lanes` takes named lanes, chains each lane internally
with `depends_on_task_id`, and leaves the lanes independent of one another --
then REPORTS the parallelism it produced, because the failure mode is silent. A
planner who accidentally passes one lane gets told `startable: 1`, which is the
number that would have caught the original mistake in the second it was made.

IT SEEDS THROUGH `create_tasks` AND NOWHERE ELSE, so every guarantee that
function provides still applies -- the task-type validation PostgreSQL enforces
and SQLite does not, `_assert_real_board`'s refusal to write a throwaway
worktree database, the gate-shaped-id refusal, the acceptance-criteria
admission, the landed check, and idempotent dedupe. There is no INSERT in this
module and a test reads its AST to keep it that way.

    from tools.kanban.lanes import create_lanes

    result = create_lanes({
        "cost": [spec_a, spec_b],      # both touch tools/cost/ -> ordered
        "mem":  [spec_c, spec_d],      # both touch .claude/hooks/ -> ordered
        "bin":  [spec_e],              # independent -> starts immediately
    })
    result["startable"]   # 3 -- the number the board can dispatch at once

NAMING THE LANES BY WHAT THEY SHARE is the whole discipline: a lane key like
`kanban_py` or `hooks` states WHY those tasks are ordered, where `lane1` hides
it. Nothing enforces that -- it is a convention a reviewer can check.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# sys.path BOOTSTRAP first, so `python tools/kanban/lanes.py --help` reaches
# main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)


class LaneError(ValueError):
    """A lane layout that would not do what the caller means."""


def plan_lanes(lanes: Mapping[str, Sequence[Mapping[str, Any]]],
               *, gate_task_id: Optional[str] = None) -> Dict[str, Any]:
    """Work out the chaining WITHOUT touching the board. Seeds nothing.

    Returns the specs with `depends_on_task_id` filled in, plus the parallelism
    the layout would produce. `create_lanes` is this plus one `create_tasks`
    call, so a caller can inspect the plan first and a test can assert on the
    shape without a database.
    """
    if not lanes:
        raise LaneError("no lanes given; create_lanes needs at least one")

    planned: List[Dict[str, Any]] = []
    per_lane: Dict[str, List[str]] = {}
    seen: Dict[str, str] = {}

    for lane_name, specs in lanes.items():
        if not specs:
            raise LaneError(
                f"lane {lane_name!r} is empty. An empty lane is almost always a "
                "typo in the caller, and silently ignoring it would seed fewer "
                "tasks than the planner thinks they asked for."
            )
        previous: Optional[str] = gate_task_id
        ids: List[str] = []
        for spec in specs:
            item = dict(spec)
            task_id = str(item.get("id") or "").strip()
            if not task_id:
                raise LaneError(f"a spec in lane {lane_name!r} has no id")
            if task_id in seen:
                raise LaneError(
                    f"task {task_id!r} appears in lane {seen[task_id]!r} and lane "
                    f"{lane_name!r}. A task belongs to exactly one lane -- it has "
                    "one predecessor."
                )
            seen[task_id] = lane_name

            # A caller-supplied dependency is HONOURED, never overwritten: a task
            # held behind a manual gate (`depends_on_task_id=<prefix>-gate-00`)
            # is the documented way to keep the runner off work a human owns, and
            # silently replacing it with a lane predecessor would release work
            # that was deliberately held.
            declared = item.get("depends_on_task_id")
            if not declared:
                item["depends_on_task_id"] = previous
            previous = task_id
            ids.append(task_id)
            planned.append(item)
        per_lane[lane_name] = ids

    # A task is STARTABLE when nothing it must wait for is still to be done:
    # no dependency at all, and not held behind a gate. A dependency pointing
    # OUTSIDE this batch is only startable if that task is already finished,
    # which this module cannot know -- so it counts as not startable, the
    # direction that under-promises.
    startable = [
        str(s["id"]) for s in planned if not s.get("depends_on_task_id")
    ]

    longest = max((len(v) for v in per_lane.values()), default=0)
    return {
        "specs": planned,
        "lanes": per_lane,
        "lane_count": len(per_lane),
        "task_count": len(planned),
        "startable": len(startable),
        "startable_ids": startable,
        # The shortest path to finishing the card if every lane ran flat out:
        # the longest lane. A single chain makes this equal to task_count, which
        # is the tell.
        "critical_path": longest,
        "gate_task_id": gate_task_id,
    }


def describe(plan: Mapping[str, Any]) -> str:
    """One human line per lane, plus the number that matters."""
    out = [
        f"{plan['task_count']} task(s) in {plan['lane_count']} lane(s); "
        f"{plan['startable']} can start immediately; "
        f"critical path {plan['critical_path']}"
    ]
    for lane, ids in plan["lanes"].items():
        out.append(f"  {lane:<12} {' -> '.join(ids)}")
    if plan["lane_count"] == 1 and plan["task_count"] > 1:
        out.append(
            "  NOTE: one lane means one task at a time. If these tasks do not all "
            "touch the same files, split them into lanes -- the board's capacity "
            "is otherwise unusable."
        )
    return "\n".join(out)


def create_lanes(lanes: Mapping[str, Sequence[Mapping[str, Any]]], *,
                 claim: bool = False,
                 gate_task_id: Optional[str] = None) -> Dict[str, Any]:
    """Seed lanes through the canonical seeder. Returns the plan plus `created`.

    `claim` and `gate_task_id` are passed straight through to the mechanisms
    that already exist -- the coordination lease `create_tasks(claim=True)`
    takes, and the manual-gate dependency. Neither is reimplemented here.
    """
    plan = plan_lanes(lanes, gate_task_id=gate_task_id)
    from tools.kanban.task_factory import create_tasks

    plan["created"] = create_tasks(plan["specs"], claim=claim)
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="Plan a lane-shaped seed. Reads specs as JSON; seeds nothing.")
    ap.add_argument("--plan-file", required=True,
                    help='JSON: {"lane": [ {spec}, ... ], ...}')
    ap.add_argument("--gate", default=None, help="hold every lane behind this task id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    plan = plan_lanes(data, gate_task_id=args.gate)
    plan.pop("specs", None)  # the plan, not the payload
    print(json.dumps(plan, indent=2) if args.json else describe(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
