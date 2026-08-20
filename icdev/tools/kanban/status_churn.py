# CUI // SP-CTI
"""Is a task's status OSCILLATING — two writers taking turns on one row?

THE CASE THIS EXISTS FOR. On 2026-08-19 `cef-ui-03` flipped ``done`` <->
``backlog`` 95 times in 5.5 hours. `pr_watcher` completed it because its PR had
merged; the scheduler's outcome handler demoted it because its run had run out
of budget. Both were right about their own question, the row kept whichever
answer arrived last, and each demotion cascaded a `failure_count` reset onto a
descendant.

NOTHING NOTICED. Every individual transition was legitimate, so no per-move
guard could see it; the board reported ``scheduled`` throughout and the
scheduler reported ``idle``. It took a human asking why the dispatcher looked
dead. `kpr-dup-09` fixed the mechanism behind that particular loop — this
detects the SHAPE, so the next pair of writers that disagree is visible in
minutes rather than in a transition table nobody reads.

WHAT COUNTS AS OSCILLATION. A RETURN: two consecutive transitions of the form
``A -> B`` then ``B -> A``. That is the signal, and it is deliberately not "the
task changed status a lot" — a task moving backlog -> scheduled -> in_progress
-> pr_opened -> done changes status five times and is simply progressing.

SURVEYED BEFORE THRESHOLDING, over all 15,879 recorded transitions:

    tasks with >=1 return   373  (11.9% of 3,129)   <- routine, not a finding
    p50 2 returns, p90 8, max 316
    >=  2 returns   253 tasks  (8.09%)
    >=  5 returns    77 tasks  (2.46%)
    >= 10 returns    34 tasks  (1.09%)   <- the default
    >= 20 returns    18 tasks  (0.58%)

One return is common and benign, so a detector that fires on it is noise. Ten
puts the rate at 1.09%, below the 1.63% CLAUDE.md already calls refusing
routine work, and still catches the 316-return cases — of which there are
eighteen, none of which anybody had noticed.

REPORT ONLY, no ``--gate``. This measures the BOARD, not a diff, so failing a
commit on it would block unrelated work for a condition the committer did not
cause — the same reasoning that keeps `check_project_card_coverage` at warn.

    python -m tools.kanban.status_churn --json
    python -m tools.kanban.status_churn --window-hours 6
    python -m tools.kanban.status_churn --min-returns 5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

#: Returns within the window above which a task is reported. Surveyed above;
#: re-run the survey before lowering it.
DEFAULT_MIN_RETURNS = 10

#: How far back to look. A task that returned ten times over three months is a
#: different animal from one that did it in an afternoon, and only the second is
#: a live fight between two writers.
DEFAULT_WINDOW_HOURS = 24


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_transitions(conn, window_hours: int = DEFAULT_WINDOW_HOURS) -> List[dict]:
    """Recorded transitions inside the window, oldest first, per task."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows = conn.execute(
        "SELECT task_id, from_status, to_status, actor, recorded_at "
        "FROM kanban_status_transitions WHERE recorded_at >= %s "
        "ORDER BY task_id, recorded_at",
        (cutoff.isoformat(),),
    ).fetchall()
    out: List[dict] = []
    for row in rows:
        record = dict(row)
        when = _parse_dt(record.get("recorded_at"))
        if when and str(record.get("task_id") or "").strip():
            record["_when"] = when
            out.append(record)
    return out


def find_returns(transitions: Sequence[dict]) -> Dict[str, List[dict]]:
    """``{task_id: [return, ...]}`` — every ``A -> B`` followed by ``B -> A``.

    A return is the pair, not the individual move. Progression through distinct
    statuses produces none however many steps it takes, which is what keeps a
    normal task off this report.
    """
    by_task: Dict[str, List[dict]] = defaultdict(list)
    for t in transitions:
        by_task[t["task_id"]].append(t)

    found: Dict[str, List[dict]] = {}
    for task_id, moves in by_task.items():
        returns: List[dict] = []
        for i in range(1, len(moves)):
            prev, cur = moves[i - 1], moves[i]
            a, b = prev.get("from_status"), prev.get("to_status")
            c, d = cur.get("from_status"), cur.get("to_status")
            if a and b and b == c and d == a:
                returns.append({
                    "cycle": f"{a} -> {b} -> {a}",
                    "at": cur["_when"].isoformat(),
                    "actors": sorted({str(prev.get("actor") or "?"),
                                      str(cur.get("actor") or "?")}),
                })
        if returns:
            found[task_id] = returns
    return found


def churn_report(conn, window_hours: int = DEFAULT_WINDOW_HOURS,
                 min_returns: int = DEFAULT_MIN_RETURNS) -> dict:
    """Tasks whose status is oscillating, worst first."""
    transitions = load_transitions(conn, window_hours=window_hours)

    #: A board with no recorded transitions in the window is UNMEASURABLE, never
    #: a clean zero — a fresh worktree or an idle weekend would otherwise report
    #: "nothing is oscillating" and read as proof the pipeline is healthy.
    if not transitions:
        return {
            "measurable": False,
            "reason": f"no status transitions recorded in the last {window_hours}h",
            "window_hours": window_hours, "min_returns": min_returns,
        }

    returns = find_returns(transitions)
    flagged = []
    for task_id, items in returns.items():
        if len(items) < min_returns:
            continue
        actors = Counter(a for it in items for a in it["actors"])
        flagged.append({
            "task_id": task_id,
            "returns": len(items),
            "cycle": Counter(it["cycle"] for it in items).most_common(1)[0][0],
            #: The two writers taking turns. Naming them is the point — the fix
            #: is a rule about which one owns the row, and you cannot write that
            #: rule without knowing who is arguing.
            "actors": [a for a, _ in actors.most_common()],
            #: CONTESTED is the dangerous shape and it is not the same as busy.
            #: Measured on the live board, most oscillation is ONE writer
            #: retrying — `in_progress -> token_exhausted -> in_progress` by the
            #: scheduler is a task being re-attempted, which is the system
            #: working. Two or more writers returning a row to its previous
            #: state are DISAGREEING about who owns it, and that is the shape
            #: that ran 95 times in 5.5 hours without anybody noticing.
            "contested": len([a for a in actors if a != "?"]) > 1,
            "first_seen": items[0]["at"],
            "last_seen": items[-1]["at"],
        })
    # Contested first: a two-writer fight needs a rule about ownership, while a
    # single-writer retry loop needs a budget. Different fixes, so the report
    # must not bury the first among the second.
    flagged.sort(key=lambda r: (r["contested"], r["returns"]), reverse=True)

    return {
        "measurable": True,
        "window_hours": window_hours,
        "min_returns": min_returns,
        "transitions_scanned": len(transitions),
        "tasks_with_any_return": len(returns),
        "oscillating": len(flagged),
        "contested": sum(1 for r in flagged if r["contested"]),
        "tasks": flagged,
    }


def render(report: dict) -> str:
    if not report.get("measurable"):
        return (f"UNMEASURABLE — {report['reason']}.\n"
                "An idle board cannot say whether anything is oscillating; that "
                "is not the same as nothing being wrong.")
    lines = [
        f"transitions scanned    : {report['transitions_scanned']} "
        f"(last {report['window_hours']}h)",
        f"tasks with any return  : {report['tasks_with_any_return']} "
        f"(one return is routine — not a finding)",
        f"OSCILLATING (>= {report['min_returns']}) : {report['oscillating']}"
        f"   of which CONTESTED (2+ writers): {report['contested']}",
    ]
    if not report["tasks"]:
        lines.append("")
        lines.append("No task is oscillating. Two writers are not fighting over a row.")
        return "\n".join(lines)
    lines += ["", f"{'task':<22} {'returns':>7}  cycle / writers"]
    for row in report["tasks"]:
        lines.append(f"{row['task_id']:<22} {row['returns']:>7}  {row['cycle']}")
        tag = "CONTESTED" if row["contested"] else "single-writer retry"
        lines.append(f"{'':<22} {'':>7}  {tag}: {', '.join(row['actors'])}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--min-returns", type=int, default=DEFAULT_MIN_RETURNS)
    args = parser.parse_args(argv)

    from tools.db.storage import get_connection

    report = churn_report(get_connection(), window_hours=args.window_hours,
                          min_returns=args.min_returns)
    print(json.dumps(report, indent=2) if args.json else render(report))
    #: Report only. This measures the BOARD, not a diff, so failing a commit on
    #: it would block unrelated work for a condition the committer did not cause.
    return 0


if __name__ == "__main__":
    sys.exit(main())
