# CUI // SP-CTI
"""Is the status-transition log COMPLETE? (autonomy flag 1)

WHY IT MATTERS. `kanban_status_transitions` is the primary evidence three
detectors read: `status_churn` (kpr-watch-11) counts A->B->A returns,
`landed_dispatch_survey` (kpr-fix-03) replays 6,218 dispatches through it, and
`merge_stall` (kpr-watch-02) dates a PR's eligibility from it. Each of those is
only as complete as the log, and none of them can tell a move that never
happened from a move that happened and was never written down.

MEASURED 2026-08-21 on the live board: of 3,338 tasks the board calls `done`,
3,024 have at least one transition row and **273** have no recorded ARRIVAL at
done — their last logged move leaves `done` or leaves them mid-flight, and the
board says done anyway.

    2026-06  189      2026-07  74      2026-08  10

The rate is falling by roughly an order of magnitude a month, so this is mostly
historical debt rather than an active leak. That distinction is the whole reason
this reports a TREND and not a total: a bare 273 reads as an ongoing haemorrhage
and would be triaged as one.

A REFUSED MOVE IS NOT A DEPARTURE, and getting this wrong inflates the number.
`_move_task` records a blocked transition with a `REFUSED_` pseudo-status
(`REFUSED_done_unmerged`, `REFUSED_uncomplete_backlog`) — the row means the guard
FIRED and the task STAYED. Counting those as departures is exactly the mistake
that produced a first draft of this measurement, and it is why the constant below
is a prefix match rather than a list of two names.

WHAT IT DOES NOT CLAIM. It does not say WHICH writer skipped the log. 39 sites
`UPDATE kanban_tasks ... status` outside `_move_task`, and the CLI's own
recorder is best-effort (`_record_manual_transition` swallows a failed INSERT
with a warning), so a gap can come from a bypassing writer OR from a recorder
that tried and failed. Naming a culprit would be a guess; the gap is the fact.

Report only. This measures the BOARD, not a diff, so a `--gate` would fail
commits for a condition the committer did not cause.

Usage:
    python -m tools.kanban.transition_log_gap
    python -m tools.kanban.transition_log_gap --json
    python -m tools.kanban.transition_log_gap --window-days 30
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_BASE = __file__.rsplit("tools", 1)[0].rstrip("\\/")
if _BASE and _BASE not in sys.path:
    sys.path.insert(0, _BASE)

#: A transition whose `to_status` starts with this is a RECORDED REFUSAL: the
#: guard fired and the task did NOT move. Never a departure.
REFUSED_PREFIX = "REFUSED_"

MEASURED = "measured"
UNMEASURABLE = "unmeasurable"


def _parse(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_done_state(rows) -> Dict[str, str]:
    """``{task_id: 'in'|'out'}`` from the transition log alone.

    The LAST move that touched `done` decides. Counting arrivals alone would
    call a task stably done on the strength of a move it later reversed.
    """
    state: Dict[str, str] = {}
    for row in rows or []:
        record = dict(row)
        task_id = str(record.get("task_id") or "")
        if not task_id:
            continue
        to_status = str(record.get("to_status") or "")
        from_status = str(record.get("from_status") or "")
        if to_status == "done":
            state[task_id] = "in"
        elif from_status == "done" and not to_status.startswith(REFUSED_PREFIX):
            state[task_id] = "out"
    return state


def measure(conn=None, window_days: Optional[int] = None) -> Dict[str, Any]:
    """Tasks the board calls done with no recorded arrival. Never raises."""
    close = False
    if conn is None:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            close = True
        except Exception as exc:  # noqa: BLE001
            return {"state": UNMEASURABLE, "reason": f"no database: {exc}",
                    "done": None, "unlogged": None, "by_month": {}}
    try:
        done_rows = conn.execute(
            "SELECT id, updated_at FROM kanban_tasks WHERE status = 'done'"
        ).fetchall()
        known_rows = conn.execute(
            "SELECT DISTINCT task_id FROM kanban_status_transitions"
        ).fetchall()
        touch_rows = conn.execute(
            "SELECT task_id, from_status, to_status FROM kanban_status_transitions "
            "WHERE to_status = 'done' OR from_status = 'done' ORDER BY recorded_at"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"state": UNMEASURABLE, "reason": f"log unreadable: {exc}",
                "done": None, "unlogged": None, "by_month": {}}
    finally:
        if close:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    done = {str(dict(r).get("id")): _parse(dict(r).get("updated_at"))
            for r in done_rows or []}
    if not done:
        # No operating history. Never a clean zero.
        return {"state": UNMEASURABLE,
                "reason": "no task on this board is done — nothing to check",
                "done": 0, "unlogged": None, "by_month": {}}

    known = {str(dict(r).get("task_id")) for r in known_rows or []}
    arrived = {t for t, v in last_done_state(touch_rows).items() if v == "in"}

    # A task with NO transition rows at all has no history to contradict the
    # board — it is not evidence of a skipped write.
    gap = sorted((set(done) - arrived) & known)

    cutoff = None
    if window_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    by_month: Counter = Counter()
    in_window: List[str] = []
    for task_id in gap:
        when = done.get(task_id)
        if when is None:
            by_month["unknown"] += 1
            continue
        by_month[when.strftime("%Y-%m")] += 1
        if cutoff is None or when >= cutoff:
            in_window.append(task_id)

    return {
        "state": MEASURED,
        "done": len(done),
        "with_any_transition": len(set(done) & known),
        "unlogged": len(gap),
        "unlogged_in_window": len(in_window) if window_days else None,
        "window_days": window_days,
        "by_month": dict(sorted(by_month.items())),
        "sample": gap[:10],
    }


def render(report: Dict[str, Any]) -> str:
    if report["state"] != MEASURED:
        return (f"Transition-log completeness — {report['state']}\n"
                f"  {report.get('reason')}\n"
                f"  (unmeasurable is NOT complete — nobody could check)")
    out = [
        "Transition-log completeness — measured",
        f"  tasks the board calls done : {report['done']}",
        f"  with any transition row    : {report['with_any_transition']}",
        f"  with NO recorded arrival   : {report['unlogged']}",
        "",
        "  by month last updated (the TREND is the finding, not the total):",
    ]
    for month, count in report["by_month"].items():
        out.append(f"    {month}: {count}")
    out.append("")
    out.append("  Every detector that reads kanban_status_transitions — status_churn,")
    out.append("  landed_dispatch_survey, merge_stall — is reading this log.")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--window-days", type=int, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = measure(window_days=args.window_days)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    # Report only, deliberately no --gate: it measures the BOARD, not a diff.
    return 0 if report["state"] == MEASURED else 2


if __name__ == "__main__":
    raise SystemExit(main())
