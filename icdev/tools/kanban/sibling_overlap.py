#!/usr/bin/env python3
# CUI // SP-CTI
"""Serialize a card against an in-flight sibling that edits the same file (mfx-sib-01).

THE DEFECT
==========
2026-09-03/04: ten ``rmf-ui-*`` cards -- one route per card, by design -- each
appended to the SAME lines. The Compliance dropdown's active-path list and link
in both ``base.html`` copies, the ``.claude/commands/start.md`` Pages line, one
canvas ``blueprint.py``, one feature doc and the nav/coverage e2e tables.
Whichever card landed first made every open sibling CONFLICTING;
``pr_watcher.classify_conflict`` read ``real`` (git conflicts too), its
``--force-with-lease`` rebase aborted (four ``pr_watcher.rebase_failed`` rows
per card), five LLM resumes burned, then ``pr_watcher.escalate`` and a human
unioned the hunks by hand. Ten times, roughly six hours.

The MERGE door already serializes siblings: ``hold_on_sibling_conflict`` plus
``pr_watcher._sibling_conflicts``, over the non-additive files two OPEN PRs
share. DISPATCH did not, so four siblings were built concurrently and three of
the four were guaranteed to conflict. Holding at the merge door is the expensive
place to find out -- the work is already written.

WHAT THIS ADDS, AND WHERE IT SITS
=================================
An admission check beside the respawn guard in
``tools/genesis/reflexes/kanban.py::_get_due_tasks``. A task is HELD when a
sibling in its own epic is already ``in_progress`` or ``pr_opened`` AND the two
cards' DECLARED artifact paths overlap on a NON-ADDITIVE file.

A HOLD IS A WAIT, NOT A PARK. The card stays ``scheduled``, yields its selection
slot to something that can actually run, and is re-evaluated next cycle -- when
the sibling reaches ``done`` the hold evaporates on its own. Nothing on the task
row changes; the reason is recorded on a ``scheduled -> scheduled`` transition
row (actor ``sibling-serializer``) so the wait is legible on the board, and the
per-cycle hold count is reported by the reflex.

NEVER A SECOND COPY OF EITHER INPUT
===================================
* Declared paths come from :func:`tools.kanban.artifact_evidence.declared_artifacts`
  -- the same parser ``artifact_evidence --survey`` grades a done card with.
* "Safe to co-edit" comes from :func:`tools.git.coordination_paths.is_coordination_path`
  -- which IS ``pr_watcher._is_additive_path``, re-exported. The dispatch door
  and the merge door therefore cannot disagree about what counts as a collision.

The predicate the admission calls and the predicate the survey replays are the
same function (:func:`overlap`), for the same reason: a survey that measured a
second copy of the rule would prove nothing about the rule that ships.

WHAT IT DOES NOT CLAIM
======================
``declared_artifacts`` reads PROSE and requires a creation marker within 40
characters, so it is a heuristic and an under-approximation: a card that names
no path declares nothing and is never held. That is the honest failure
direction -- an unheld pair costs a rebase, and holding on a guess costs
throughput on work that never collides. ``lane_conflicts`` is the wider, fuzzier
prose parser and is deliberately NOT used here: it reports every MENTION, which
on this corpus includes the test files and template names a card merely cites.

SURVEYED BEFORE ARMING (30 days to 2026-09-04, 1,977 scheduler dispatches)
==========================================================================
::

    scheduler dispatches                        1977
    ... with an in-flight same-epic sibling       451   23.07%
    ... whose declared paths overlap (HELD)        21    1.06%
    ... of those, went on to conflict anyway       18   85.71% of the holds

1.06% is below the 1.63% CLAUDE.md already calls refusing routine work, and
comfortably under the 2% this card set. It touches THREE epics -- ``rmf-ui``,
``cef-bck``, ``exa-bench`` -- and holds exactly the ten ``rmf-ui`` cards the
incident was written about (03, 06, 07, 08, 10, 11, 12, 14, 15, 16).

THE COST IS THREE HOLDS, NAMED: ``cef-bck-01``, ``rmf-ui-14`` and ``rmf-ui-15``
were the only held dispatches with no recorded conflict afterwards -- 0.15% of
all dispatches delayed by a cycle for a collision that would not have happened.
The other 18 each cost four ``rebase_failed`` rows, five burned LLM resumes and
a human.

TWO THINGS THE NUMBERS SAY THAT A GUESS WOULD NOT
=================================================
* Excluding the additive paths changes nothing AT THE DISPATCH LEVEL here: raw
  overlap and post-filter overlap are both 21, because every pair that shares
  ``.claude/commands/start.md`` also shares a canvas ``blueprint.py``. The
  filter is still load-bearing -- it is what stops the rule generalising to
  every card that adds a page -- but on this corpus it removed no hold, and
  claiming it took the rate from 2.38% to 1.06% would be a fabrication.
* ONE hold rests on ``includes/iqe_query_widget.html`` alone (``rmf-ui-16``
  behind ``rmf-ui-11``). That path is a Jinja include NAME, not a file either
  card edits -- it is a proxy for "both of these add a canvas page", and it is
  honest to call it that. The pair did conflict.

Re-derive the figures quoted in ``args/genesis_config.yaml`` with::

    python -m tools.kanban.sibling_overlap --survey
    python -m tools.kanban.sibling_overlap --survey --window-days 30 --json
    python -m tools.kanban.sibling_overlap --holds          # what would be held NOW
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]  # sys.path bootstrap only
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.git.coordination_paths import is_coordination_path  # noqa: E402
from tools.kanban.artifact_evidence import declared_artifacts  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.sibling_overlap")

#: A sibling in one of these owns the shared file right now. ``scheduled`` is
#: NOT here: two scheduled cards are both still waiting, and holding one behind
#: the other would deadlock the pair.
IN_FLIGHT_STATUSES = frozenset({"in_progress", "pr_opened"})

#: The actor recorded on the wait row, so a reader can tell a serialization hold
#: from a failure.
HOLD_ACTOR = "sibling-serializer"


def epic_of(task_id: str) -> Optional[str]:
    """``rmf-ui-16`` -> ``rmf-ui``. ``None`` when the id is not epic-shaped.

    The board's contract is ``<task_prefix><epic_key>-<N>`` (CLAUDE.md), so the
    epic is everything left of a trailing numeric segment. An opaque machine id
    (``task-<hex>``) has no epic and can never be held -- it has no siblings by
    construction, which is the correct answer rather than a gap.
    """
    if not task_id or "-" not in task_id:
        return None
    head, _, tail = task_id.rpartition("-")
    if not head or not tail or not tail.isdigit():
        return None
    return head


def declared_non_additive(title: str, description: str) -> Set[str]:
    """Paths this card says it will create, minus the ones many branches co-edit.

    The additive filter is what keeps the rule below the routine-work line: the
    ten rmf-ui cards all declare ``.claude/commands/start.md``, and so does every
    other card that adds a page. Serializing on THAT would serialize the board.
    """
    return {
        p for p in declared_artifacts(title or "", description or "")
        if not is_coordination_path(p)
    }


def overlap(a_title: str, a_desc: str, b_title: str, b_desc: str) -> Set[str]:
    """Non-additive declared paths BOTH cards claim. Empty set = no collision.

    THE one predicate. The dispatch admission and the survey both call it, so a
    surveyed rate is a rate for the rule that ships.
    """
    return declared_non_additive(a_title, a_desc) & declared_non_additive(b_title, b_desc)


@dataclass(frozen=True)
class Hold:
    """One admission refusal, carrying the evidence that produced it."""

    task_id: str
    sibling_id: str
    sibling_status: str
    epic: str
    shared_paths: Tuple[str, ...]

    @property
    def reason(self) -> str:
        paths = ", ".join(self.shared_paths[:3])
        more = "" if len(self.shared_paths) <= 3 else f" (+{len(self.shared_paths) - 3} more)"
        return (
            f"waiting on sibling {self.sibling_id} ({self.sibling_status}): both cards "
            f"declare {paths}{more}"
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "sibling_id": self.sibling_id,
            "sibling_status": self.sibling_status,
            "epic": self.epic,
            "shared_paths": list(self.shared_paths),
            "reason": self.reason,
        }


def _in_flight_by_epic(conn) -> Dict[str, List[dict]]:
    """Every ``in_progress`` / ``pr_opened`` card, grouped by epic."""
    statuses = sorted(IN_FLIGHT_STATUSES)
    placeholders = ",".join(["%s"] * len(statuses))
    rows = conn.execute(
        "SELECT id, title, description, status FROM kanban_tasks "
        f"WHERE status IN ({placeholders})",  # nosec B608 — placeholders only
        tuple(statuses),
    ).fetchall()
    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        rec = dict(row)
        epic = epic_of(rec.get("id") or "")
        if epic:
            grouped.setdefault(epic, []).append(rec)
    return grouped


def find_holds(tasks: Sequence[dict], conn=None) -> Dict[str, Hold]:
    """``{task_id: Hold}`` for candidates an in-flight sibling owns a file of.

    FAIL-OPEN on every unknown. An unreadable board must never wedge dispatch --
    a missed hold costs one rebase, a wedged scheduler costs the whole queue.
    """
    if not tasks:
        return {}
    owns_conn = conn is None
    holds: Dict[str, Hold] = {}
    try:
        if owns_conn:
            from tools.db.storage import get_connection
            conn = get_connection()
        by_epic = _in_flight_by_epic(conn)
        if not by_epic:
            return {}
        for task in tasks:
            task_id = task.get("id")
            epic = epic_of(task_id or "")
            if not epic or epic not in by_epic:
                continue
            mine = declared_non_additive(
                task.get("title") or "", task.get("description") or "")
            if not mine:
                continue
            for sibling in by_epic[epic]:
                if sibling.get("id") == task_id:
                    continue
                shared = mine & declared_non_additive(
                    sibling.get("title") or "", sibling.get("description") or "")
                if not shared:
                    continue
                holds[task_id] = Hold(
                    task_id=task_id,
                    sibling_id=sibling["id"],
                    sibling_status=sibling.get("status") or "",
                    epic=epic,
                    shared_paths=tuple(sorted(shared)),
                )
                break
    except Exception as exc:  # noqa: BLE001 — never wedge dispatch
        logger.warning("sibling-overlap admission skipped: %s", exc)
        return {}
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return holds


# ── survey ────────────────────────────────────────────────────────────────────

#: A dispatch is a scheduler-written ``-> in_progress`` transition.
DISPATCH_ACTOR = "scheduler"

#: pr_watcher's own record that a branch hit a real merge conflict. These are
#: the rows the incident left behind; nothing new is written to measure this.
CONFLICT_ACTIONS = (
    "pr_watcher.rebase_failed",
    "pr_watcher.escalate",
    "pr_watcher.resume",
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _status_at(timeline: List[Tuple[str, str]], when: str) -> Optional[str]:
    """Status of a task at ``when``, from its ordered transitions. ``None`` = unknown.

    ``None`` is never folded into "not in flight": a card whose history starts
    after the moment asked about was not observed, and counting an unobserved
    card as idle would understate the collision rate.
    """
    seen: Optional[str] = None
    for recorded_at, to_status in timeline:
        if recorded_at > when:
            break
        seen = to_status
    return seen


def _conflicted_task_ids(conn, since: str) -> Set[str]:
    """Task ids pr_watcher recorded a merge conflict against, in the window."""
    placeholders = ",".join(["%s"] * len(CONFLICT_ACTIONS))
    rows = conn.execute(
        "SELECT details FROM audit_trail "
        f"WHERE actor = 'pr_watcher' AND action IN ({placeholders}) "  # nosec B608
        "  AND created_at > %s",
        (*CONFLICT_ACTIONS, since),
    ).fetchall()
    ids: Set[str] = set()
    for row in rows:
        raw = dict(row).get("details")
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — a malformed row is no evidence
            continue
        task_id = (payload or {}).get("task_id")
        if task_id:
            ids.add(task_id)
    return ids


def survey(window_days: int = 30, conn=None) -> dict:
    """Replay every scheduler dispatch in the window through :func:`overlap`.

    Reports the fire rate the rule WOULD have had, and how many of the dispatches
    it would have held went on to cost a real merge conflict.

    UNMEASURABLE, never a clean zero, on a board with no dispatches in the
    window: an empty denominator is not a low fire rate.
    """
    owns_conn = conn is None
    try:
        if owns_conn:
            from tools.db.storage import get_connection
            conn = get_connection()
        since = _iso(datetime.now(timezone.utc) - timedelta(days=window_days))

        cards = {
            dict(r)["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, title, description FROM kanban_tasks").fetchall()
        }

        timelines: Dict[str, List[Tuple[str, str]]] = {}
        for row in conn.execute(
            "SELECT task_id, to_status, recorded_at FROM kanban_status_transitions "
            "ORDER BY recorded_at ASC"
        ).fetchall():
            rec = dict(row)
            timelines.setdefault(rec["task_id"], []).append(
                (str(rec["recorded_at"]), rec["to_status"]))

        dispatches = [
            dict(r) for r in conn.execute(
                "SELECT task_id, recorded_at FROM kanban_status_transitions "
                "WHERE to_status = 'in_progress' AND actor = %s AND recorded_at > %s "
                "ORDER BY recorded_at ASC",
                (DISPATCH_ACTOR, since),
            ).fetchall()
        ]
        conflicted = _conflicted_task_ids(conn, since)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    total = len(dispatches)
    if not total:
        return {
            "status": "unmeasurable",
            "reason": "no scheduler dispatches recorded in the window",
            "window_days": window_days,
            "dispatches": 0,
            "with_in_flight_sibling": None,
            "overlapping_raw": None,
            "overlapping_raw_pct": None,
            "held": None,
            "fire_rate_pct": None,
            "held_that_conflicted": None,
            "conflict_precision_pct": None,
            "holds": [],
        }

    epics: Dict[str, List[str]] = {}
    for task_id in cards:
        epic = epic_of(task_id)
        if epic:
            epics.setdefault(epic, []).append(task_id)

    with_sibling = 0
    overlapping_raw = 0
    holds: List[dict] = []
    for dispatch in dispatches:
        task_id = dispatch["task_id"]
        when = str(dispatch["recorded_at"])
        card = cards.get(task_id)
        epic = epic_of(task_id)
        if not card or not epic:
            continue
        in_flight = [
            sib for sib in epics.get(epic, [])
            if sib != task_id
            and _status_at(timelines.get(sib, []), when) in IN_FLIGHT_STATUSES
        ]
        if not in_flight:
            continue
        with_sibling += 1
        raw_mine = set(declared_artifacts(
            card.get("title") or "", card.get("description") or ""))
        counted_raw = False
        held_for: Optional[dict] = None
        for sib in in_flight:
            sib_card = cards.get(sib)
            if not sib_card:
                continue
            sib_title = sib_card.get("title") or ""
            sib_desc = sib_card.get("description") or ""
            if not counted_raw and raw_mine & set(declared_artifacts(sib_title, sib_desc)):
                overlapping_raw += 1
                counted_raw = True
            shared = overlap(
                card.get("title") or "", card.get("description") or "",
                sib_title, sib_desc,
            )
            if shared and held_for is None:
                held_for = {
                    "task_id": task_id,
                    "dispatched_at": when,
                    "sibling_id": sib,
                    "epic": epic,
                    "shared_paths": sorted(shared),
                    "conflicted_after": task_id in conflicted,
                }
            if counted_raw and held_for is not None:
                break
        if held_for:
            holds.append(held_for)

    held = len(holds)
    held_conflicted = sum(1 for h in holds if h["conflicted_after"])
    return {
        "status": "measured",
        "window_days": window_days,
        "dispatches": total,
        "with_in_flight_sibling": with_sibling,
        "overlapping_raw": overlapping_raw,
        "overlapping_raw_pct": round(overlapping_raw / total * 100, 2),
        "held": held,
        "fire_rate_pct": round(held / total * 100, 2),
        "held_that_conflicted": held_conflicted,
        "conflict_precision_pct": (
            round(held_conflicted / held * 100, 2) if held else None),
        "holds": holds,
    }


def _render_survey(result: dict) -> str:
    if result.get("status") != "measured":
        return (
            f"sibling-overlap survey: UNMEASURABLE — {result.get('reason')}\n"
            f"  window: {result.get('window_days')}d"
        )
    precision = result["conflict_precision_pct"]
    lines = [
        f"Sibling-overlap dispatch survey — {result['window_days']} day window",
        "",
        f"  scheduler dispatches                       {result['dispatches']:>6}",
        f"  ... with an in-flight same-epic sibling    {result['with_in_flight_sibling']:>6}",
        f"  ... declared paths overlap (raw)           {result['overlapping_raw']:>6}"
        f"   {result['overlapping_raw_pct']:>6.2f}%",
        f"  ... after excluding additive paths (HELD)  {result['held']:>6}"
        f"   {result['fire_rate_pct']:>6.2f}%",
        f"  held dispatches that DID conflict          {result['held_that_conflicted']:>6}"
        + (f"   {precision:>6.2f}%" if precision is not None else "        —"),
        "",
    ]
    for hold in result["holds"][:40]:
        flag = "CONFLICTED" if hold["conflicted_after"] else "clean"
        lines.append(
            f"  {hold['task_id']:<18} waits on {hold['sibling_id']:<18} "
            f"[{flag}]  {', '.join(hold['shared_paths'][:2])}"
        )
    if len(result["holds"]) > 40:
        lines.append(f"  … {len(result['holds']) - 40} more")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Would this dispatch collide with an in-flight sibling? (mfx-sib-01)")
    parser.add_argument("--survey", action="store_true",
                        help="replay recorded dispatches and report the fire rate")
    parser.add_argument("--holds", action="store_true",
                        help="what a dispatch right now would hold")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.holds:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            scheduled = [
                dict(r) for r in conn.execute(
                    "SELECT id, title, description FROM kanban_tasks "
                    "WHERE status = 'scheduled'").fetchall()
            ]
            holds = find_holds(scheduled, conn=conn)
        finally:
            conn.close()
        payload = {"scheduled": len(scheduled),
                   "held": len(holds),
                   "holds": [h.to_dict() for h in holds.values()]}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"{payload['held']} of {payload['scheduled']} scheduled task(s) held")
            for hold in holds.values():
                print(f"  {hold.task_id}: {hold.reason}")
        return 0

    result = survey(window_days=args.window_days)
    print(json.dumps(result, indent=2) if args.json else _render_survey(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
