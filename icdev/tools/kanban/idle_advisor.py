# CUI // SP-CTI
"""Why is the pipeline idle, and what should a human do about it? (kpr-idle-01)

``kanban_scheduler`` logs ``Cycle N: idle (no due tasks)`` and nothing else. That
line is true in six materially different situations, five of which need
different actions and one of which needs none:

===================  ====================================================
``paused``           a pause sentinel is in force
``wedged``           real work holds every slot but stopped heartbeating
``review_bound``     candidates exist, all have an open PR — merge, don't dispatch
``decision_bound``   all remaining work sits behind a HELD MANUAL GATE
``chain_bound``      blocked by ordinary unmet dependencies
``drained``          genuinely nothing left — healthy
===================  ====================================================

Told apart only by hand, they cost hours. On 2026-08-03 the board sat idle twice
with three dispatch slots free: once ``review_bound`` behind ten unmerged PRs,
once ``decision_bound`` behind four held gates. Both times the log said exactly
what it says when everything is finished.

WHAT THIS DOES NOT DO
---------------------
**It never releases a gate, and it never moves a task.** A held gate is a human
saying "not yet"; an advisor that opens gates to keep the pipeline busy has
quietly substituted throughput for intent — and the gate would stop being a
control the moment anyone learned it could be overridden by waiting. Every
function here is a read. The recommendation is an argument, not an action.

A GATE IS ONLY JUSTIFIED BY A STATED RISK (kpr-idle-02)
-------------------------------------------------------
A gate stops work, so it owes a reason: what goes wrong if the runner builds
this card unattended. Procedure is not a reason — "do not move without a
decision" and "re-hold after /start" say what to DO, not what breaks, so they
cannot be reviewed and cannot be weighed against the cost of holding.

A gate with no stated risk therefore outranks every justified gate for release,
regardless of how much work each holds: it is not a control, it is work nobody
has looked at. Volume and priority decide among equals, never across that line.
Measured on the live board the day this shipped: of four held gates, two stated
a reason and two did not, and one of the silent pair had been holding 19 tasks —
three of them critical — for 39 hours.

WHAT THE RANKING CAN AND CANNOT KNOW
------------------------------------
It ranks on signals it can actually measure: how much work would become
dispatchable, the priority mix, how much of that work is self-declaring itself
blocked, and how long the gate has been held. It CANNOT know which card matters
to the business this week, so it does not pretend to — ``blind_spots`` says so
in the output rather than burying it under a confident-looking score.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.gates import declared_risk, is_manual_gate  # noqa: E402

#: A task whose TITLE announces it cannot proceed. These are the expensive ones:
#: dispatched, an agent spends a full session rediscovering why, and usually
#: "resolves" it by weakening something. Detected from the title because that is
#: where the board records it today.
_INERT_TITLE = re.compile(r"^\s*(BLOCKED|DECLINED|WONTFIX|SUPERSEDED)\b", re.I)

#: Priority -> weight. Deliberately coarse: this orders candidates, it does not
#: measure them, and a finer scale would imply precision the inputs do not have.
_PRIORITY_WEIGHT = {"critical": 3, "high": 2, "medium": 1, "low": 0}

#: Added to any gate holding work with no stated risk, so it outranks every
#: justified gate no matter how much work they hold. Large on purpose: this
#: is a precedence rule expressed as arithmetic, not a heavy nudge.
_UNJUSTIFIED_PRECEDENCE = 10_000.0

PAUSED = "paused"
WEDGED = "wedged"
REVIEW_BOUND = "review_bound"
DECISION_BOUND = "decision_bound"
CHAIN_BOUND = "chain_bound"
DRAINED = "drained"

#: Reasons a human should act on. `drained` is healthy; `paused` is already a
#: deliberate human act, so neither warrants a recommendation.
ACTIONABLE = frozenset({WEDGED, REVIEW_BOUND, DECISION_BOUND, CHAIN_BOUND})


@dataclasses.dataclass
class GateCandidate:
    """One held gate, with the evidence for and against releasing it."""

    gate_id: str
    title: str
    held_hours: Optional[float]
    open_tasks: int
    ready_on_release: int
    priority_mix: Dict[str, int]
    inert: List[str]
    risk: Optional[str]
    risk_confidence: str
    justified: bool
    score: float
    caveats: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(value: Any) -> Optional[float]:
    if not isinstance(value, datetime):
        return None
    stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return (_utcnow() - stamp).total_seconds() / 3600.0


def _held_gates(conn) -> List[Dict[str, Any]]:
    # `description` is not optional here: it is where a gate states its risk,
    # and omitting it would make every gate look unjustified — the failure would
    # read as a policy finding rather than a missing column.
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, description, updated_at FROM kanban_tasks "
        "WHERE status = 'in_progress'"
    ).fetchall()]
    return [r for r in rows if is_manual_gate(r.get("id"), r.get("title"))]


def _live_work(conn) -> List[Dict[str, Any]]:
    """in_progress rows that are real work, not gates."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, last_heartbeat_at, updated_at FROM kanban_tasks "
        "WHERE status = 'in_progress'"
    ).fetchall()]
    return [r for r in rows if not is_manual_gate(r.get("id"), r.get("title"))]


def _score_gate(conn, gate: Dict[str, Any]) -> GateCandidate:
    """Evidence for one gate. Every number here is counted, none is assumed."""
    gate_id = gate["id"]
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, priority, status FROM kanban_tasks "
        "WHERE depends_on_task_id = %s", (gate_id,)
    ).fetchall()]
    open_rows = [r for r in rows if r.get("status") != "done"]

    sibling_ids = {r["id"] for r in rows}
    ready = 0
    for row in open_rows:
        jdeps = [dict(x)["depends_on_id"] for x in conn.execute(
            "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s", (row["id"],)
        ).fetchall()]
        held_by_sibling = False
        for dep in jdeps:
            if dep not in sibling_ids:
                continue
            st = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (dep,)).fetchone()
            if st and dict(st)["status"] != "done":
                held_by_sibling = True
                break
        if not held_by_sibling:
            ready += 1

    mix: Dict[str, int] = {}
    for row in open_rows:
        mix[row.get("priority") or "unset"] = mix.get(row.get("priority") or "unset", 0) + 1

    inert = [f"{r['id']}: {r['title']}" for r in open_rows
             if _INERT_TITLE.match(r.get("title") or "")]

    # A gate stops work, so it owes a reason (kpr-idle-02). One that states no
    # risk is not a control — it is work nobody has looked at — and it should be
    # released or justified BEFORE a justified gate is opened, regardless of
    # which holds more tasks. So justification dominates the ranking rather than
    # adjusting it: volume decides among equals, never across this line.
    risk, confidence = declared_risk(gate.get("description"))
    justified = confidence != "none"

    urgency = sum(_PRIORITY_WEIGHT.get(r.get("priority") or "", 0) for r in open_rows)
    held = _hours_since(gate.get("updated_at"))
    score = float(ready + urgency - 2 * len(inert)) + (held or 0.0) / 1000.0
    if not justified:
        score += _UNJUSTIFIED_PRECEDENCE

    caveats: List[str] = []
    if not justified:
        caveats.append(
            "held with NO stated risk — a gate that cannot say what goes wrong "
            "if the runner builds this card is not a control, it is unreviewed "
            "work. Add a `RISK:` line to the gate's description or release it"
        )
    elif confidence == "implicit":
        caveats.append(
            f"risk is stated in prose, not as a `RISK:` line: \"{(risk or '')[:110]}\" "
            "— restate it so it can be reviewed rather than inferred"
        )
    if inert:
        caveats.append(
            f"{len(inert)} of {len(open_rows)} open task(s) self-declare "
            "BLOCKED/DECLINED in their title — triage or close these BEFORE "
            "releasing, or an agent will be dispatched onto them and will "
            "'resolve' work the board already said cannot proceed"
        )
    if ready and ready < len(open_rows):
        caveats.append(
            f"only {ready} of {len(open_rows)} start immediately; the rest are "
            "chained behind siblings, so this releases less parallel work than "
            "the task count suggests"
        )
    if not open_rows:
        caveats.append("no open tasks — this gate can simply be closed")

    return GateCandidate(
        gate_id=gate_id,
        title=(gate.get("title") or "")[:120],
        held_hours=round(held, 1) if held is not None else None,
        open_tasks=len(open_rows),
        ready_on_release=ready,
        priority_mix=mix,
        inert=inert,
        risk=risk,
        risk_confidence=confidence,
        justified=justified,
        score=round(score, 3),
        caveats=caveats,
    )


def _gate_backlog_clause(conn) -> str:
    """", and N backlog task(s) sit behind M held gate(s)" — or "" when none do.

    Deliberately cheap: a gate listing plus one count, no per-gate scoring. This
    runs inside a higher-precedence branch, so it must not make the common case
    slower to report a secondary fact.
    """
    try:
        gates = _held_gates(conn)
        if not gates:
            return ""
        ids = [g["id"] for g in gates]
        placeholders = ", ".join(["%s"] * len(ids))
        held = int(dict(conn.execute(
            "SELECT count(*) AS n FROM kanban_tasks "  # nosec B608 — ids are bound
            f"WHERE status = 'backlog' AND depends_on_task_id IN ({placeholders})",
            tuple(ids),
        ).fetchone())["n"])
        if not held:
            return ""
        return (f", and {held} backlog task(s) sit behind {len(gates)} held "
                f"gate(s) ({', '.join(sorted(ids))}) — clearing review alone will "
                "not refill the queue")
    except Exception:  # noqa: BLE001 — a secondary clause must never break dispatch
        return ""


def diagnose(conn=None, *, stale_heartbeat_hours: float = 2.0) -> Dict[str, Any]:
    """Classify why dispatch produced nothing. Pure read — mutates nothing."""
    own = conn is None
    conn = conn or get_connection()
    try:
        # Ordered deliberately: the first true statement is the one a human can
        # act on. A paused scheduler behind a held gate is a paused scheduler.
        try:
            from tools.kanban.scheduler_control import manual_paused
            if manual_paused():
                return _result(PAUSED, "a pause sentinel is in force; dispatch is "
                                       "suppressed until it is lifted or expires")
        except Exception:  # noqa: BLE001 — never let the advisor break the scheduler
            pass

        live = _live_work(conn)
        stale = [r for r in live
                 if (_hours_since(r.get("last_heartbeat_at") or r.get("updated_at")) or 0)
                 > stale_heartbeat_hours]
        if live and len(stale) == len(live):
            return _result(
                WEDGED,
                f"{len(stale)} task(s) hold every slot but have not heartbeat in "
                f"over {stale_heartbeat_hours}h — they are occupying capacity "
                "without progressing",
                stale=[r["id"] for r in stale],
            )

        scheduled = int(dict(conn.execute(
            "SELECT count(*) AS n FROM kanban_tasks WHERE status = 'scheduled'"
        ).fetchone())["n"])
        if scheduled:
            # Something is ready but dispatch yielded nothing: the respawn guard
            # is the only thing between the two, and it drops tasks with an open
            # PR. Naming it beats "idle".
            #
            # The gate clause matters as much as the reason does. review_bound
            # wins the precedence race whenever ANY task is scheduled, so on
            # 2026-08-09 the scheduler reported three withheld tasks for hours and
            # never mentioned that all 37 backlog tasks behind them were held by
            # manual gates. Both were true, and acting on only the first — merge
            # the three PRs — bought three dispatches and idled again. Saying it
            # here costs one count query and collapses two diagnoses into one.
            return _result(
                REVIEW_BOUND,
                f"{scheduled} task(s) are scheduled and due but every one was "
                "withheld at dispatch — the usual cause is an open PR per task. "
                "The unblock is review: merge or close them"
                + _gate_backlog_clause(conn),
            )

        gates = _held_gates(conn)
        backlog = int(dict(conn.execute(
            "SELECT count(*) AS n FROM kanban_tasks WHERE status = 'backlog'"
        ).fetchone())["n"])

        if gates and backlog:
            candidates = sorted(
                (_score_gate(conn, g) for g in gates),
                key=lambda c: c.score, reverse=True,
            )
            gated = sum(c.open_tasks for c in candidates)
            if gated:
                return _result(
                    DECISION_BOUND,
                    f"{gated} task(s) are held behind {len(candidates)} manual "
                    "gate(s). Nothing is broken — the pipeline is waiting on a "
                    "human decision about which card to open next",
                    candidates=[c.to_dict() for c in candidates],
                    recommendation=_recommend(candidates),
                )

        if backlog:
            return _result(
                CHAIN_BOUND,
                f"{backlog} backlog task(s) exist but none has its dependencies "
                "satisfied; they are waiting on ordinary predecessors, not on a "
                "decision",
            )

        return _result(DRAINED, "no scheduled or backlog work remains — the "
                               "pipeline is idle because it is finished")
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _recommend(candidates: List[GateCandidate]) -> Dict[str, Any]:
    """The argument for the top candidate, and what the ranking cannot see."""
    top = candidates[0]
    runners = candidates[1:]
    if not top.justified:
        why = (
            f"it states no risk. It has held {top.open_tasks} task(s) for "
            f"{top.held_hours}h without recording what would go wrong if the "
            "runner built them, so there is nothing to review and nothing to "
            "weigh against the cost of holding. Release it, or add a `RISK:` "
            "line saying what the hold is protecting against"
        )
    else:
        why = (
            f"{top.ready_on_release} task(s) would start immediately "
            f"(priority mix {top.priority_mix or '{}'}), the largest amount of "
            "genuinely dispatchable work among the gates that justify holding"
        )
    return {
        "release": top.gate_id,
        "why": why,
        "justified": top.justified,
        "risk": top.risk,
        "before_releasing": top.caveats,
        "then": [c.gate_id for c in runners],
        "command": f"python tools/kanban/cli.py --set-status {top.gate_id} done",
        "blind_spots": [
            "This ranking counts work; it cannot know which card matters to the "
            "business this week. If a lower-ranked gate is more strategically "
            "urgent, that outranks everything here.",
            "It cannot see effort. Nineteen small tasks and nineteen large ones "
            "score identically.",
            "A gate held deliberately for an external reason (an unfinished "
            "dependency outside this repo, a pending approval) looks the same to "
            "it as one simply forgotten.",
        ],
    }


def _result(reason: str, detail: str, **extra: Any) -> Dict[str, Any]:
    out = {
        "reason": reason,
        "actionable": reason in ACTIONABLE,
        "detail": detail,
        "checked_at": _utcnow().isoformat(),
    }
    out.update(extra)
    return out


def summary_line(diagnosis: Dict[str, Any]) -> str:
    """One line for the scheduler log — the reason, never a bare 'idle'."""
    line = f"idle [{diagnosis['reason']}]: {diagnosis['detail']}"
    rec = diagnosis.get("recommendation")
    if rec:
        line += f" | recommended: release {rec['release']}"
        if rec.get("before_releasing"):
            line += f" (caveat: {rec['before_releasing'][0]})"
    return line


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Explain why the kanban pipeline is idle, and what to do."
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    diagnosis = diagnose()
    if args.json:
        print(json.dumps(diagnosis, indent=2, default=str))
        return 0

    print(summary_line(diagnosis))
    for cand in diagnosis.get("candidates", []):
        print(f"\n  {cand['gate_id']}  score={cand['score']}  "
              f"open={cand['open_tasks']} ready={cand['ready_on_release']} "
              f"held={cand['held_hours']}h  {cand['priority_mix']}")
        for caveat in cand["caveats"]:
            print(f"      caveat: {caveat}")
    rec = diagnosis.get("recommendation")
    if rec:
        print(f"\n  RECOMMEND: release {rec['release']}")
        print(f"    why : {rec['why']}")
        print(f"    then: {', '.join(rec['then']) or '(none)'}")
        print(f"    cmd : {rec['command']}")
        print("    this ranking cannot see:")
        for gap in rec["blind_spots"]:
            print(f"      - {gap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
