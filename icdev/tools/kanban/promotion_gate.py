# CUI // SP-CTI
"""Is a detector card still WORK at the moment it is about to be promoted?
(autonomy-act-07)

THE DEFECT. ``detector_findings.card_disposition`` (autonomy-act-04, widened to
a second landing door by -06) decides CARD-or-RECORD ONCE, AT SEEDING, and
nothing asks again. That is sound for a finding whose subject is static and
wrong for one whose subject is a PR that can land ten minutes later. The window
between seeding and dispatch is exactly where a human — or another card — fixes
the thing, and nothing looked before spending a worker session on it.

MEASURED 2026-09-12, end to end. The card was RIGHT when it was seeded:

    ~17:00  three artifact-freshness PRs are genuinely stuck (CONFLICTING).
            pr_watcher escalates each; detector_findings seeds three
            [NEEDED-A-HUMAN] cards. CORRECT.
    20:58   artifact-fresh-da63da118f merges. pr_watcher.merge row written.
    21:22   artifact-fresh-a7486018c7 merges. pr_watcher.merge row written.
    21:34   ALL THREE cards are promoted and dispatched — a worker session each.
    21:35   all three are force-closed by hand, "nothing left to build".

By 21:34 two of the three satisfied the record-not-card conjunction IN FULL, and
the third satisfied it through the ledger door. Three sessions, zero possible
RED: there was nothing left to change.

THE RULE IS IMPORTED, NEVER RESPELLED. ``merge_after_escalation``,
``ledger_landing`` and ``card_disposition`` are called here exactly as
``consume`` and ``dispositions`` call them, over the same primary rows, and
``card_disposition`` is the only place ``recovery_summary.CLOSED_STATUSES`` is
read. Nothing is stored: a verdict about the ORDER of two audit rows goes stale
the moment either row's successor is written, so it is re-derived at the
promotion, not looked up.

WHAT IT DOES NOT TOUCH. The detector, its thresholds and its window; the
projection row; ``seen_count``; ``_clear_missing``. A withheld card's finding
stays exactly as it was and is still surfaced by
``python -m tools.kanban.detector_findings --records``. This is a PROMOTION
check, not a change to what counts as a finding.

WHAT A WITHHELD CARD DOES NEXT — nothing, and that is the deliberate trade. It
stays where it is (``suggested`` or ``backlog``), visible on the board, and a
human closes it. That is exactly what happened at 21:35 on 2026-09-12 anyway;
what this removes is the three worker sessions in front of it. CLOSING the card
would need this module to write to the board, which is the one thing a read-only
promotion check must not start doing.

EVERY UNKNOWN STILL DISPATCHES. An unreadable order, a subject not on the board,
a subject still in flight, an unreadable board, an absent projection, a raised
exception anywhere in here: all keep the card. That is ``card_disposition``'s
own contract (``superseded`` stays None, never False) and this module adds a
blanket ``except`` on top of it. A wasted dispatch and a silently demoted
escalation are not the same price.

REPLAYED BEFORE ARMING (2026-09-12, live board). Point-in-time over every
recovery finding that drew a card and reached ``scheduled`` — 40 of them, each
evaluated with only the watcher rows, ledger rows and subject board status that
existed at its own ``scheduled_at``:

    seeding verdict -> promotion verdict      n     what this gate changes
    card    -> record                        18     WITHHELD (new)
    record  -> record                        13     never carded today anyway
    card    -> card                           9     untouched

    control: of the 18, subjects INDEPENDENTLY confirmed on
             origin/main by `landed_check.check_landed_bulk`     18 / 18
             withheld WRONGLY (subject not delivered)             0

Two of the 31 total withholds have a subject git cannot confirm
(``qa-fail-84f92cebcf4fe498``, ``qa-fail-b2537204d4a9b6dd`` — a qa-fail id is
not named in the commit that fixes it); both are ``done`` on the board and both
are in the ``record -> record`` bucket, i.e. already records at seeding and not
this gate's doing. The incremental population is 18 with a clean control, which
is why this ships ENFORCING rather than advisory. 18 withholds against the
6,528 recorded scheduler dispatches ``dispatch_admission`` surveys is 0.28% of
all dispatches — under the 1.63% this repo calls refusing routine work — while
being 45% of the detector-card population it is scoped to. Both denominators
are reported because they answer different questions.

``ICDEV_PROMOTION_GATE=report`` records the verdict and withholds nothing;
``=off`` skips the read entirely. Re-run ``--survey`` before changing the
default, and never widen the rule to quieten a card.

THE THREE DOORS. A detector card is seeded ``suggested`` and can reach a worker
through more than one path, so the check is asked at each, exactly as
``_is_manual_gate``/``_is_test_fixture`` already are:

  1. ``dashboard/api/kanban.py::promote_all_suggested`` — the bulk move OUT of
     ``suggested`` (the door that fired on 2026-09-12).
  2. ``kanban/promote_backlog_to_scheduled.py::promote`` — backlog -> scheduled.
  3. ``genesis/reflexes/kanban.py::_get_due_tasks`` — the last stop before a
     token is spent, for a card that reached ``scheduled`` by a dashboard move
     or the decay sweep without passing (1) or (2).

Usage:
    python -m tools.kanban.promotion_gate --check task-det-04931fe8de
    python -m tools.kanban.promotion_gate --records --json
    python -m tools.kanban.promotion_gate --survey --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from icdev.core.paths import repo_root
from tools.common.helpers import parse_utc_timestamp
from tools.logging.icdev_logger import get_logger

# NO `sys.path` BOOTSTRAP, deliberately. This module is documented and invoked as
# `python -m tools.kanban.promotion_gate` only — never `python <path>.py` — so
# the import root is already resolved and a bootstrap would have to compute the
# repo root from `__file__` ABOVE the `icdev.core.paths` import that exists to
# stop exactly that (xit-decl-03 vs kax-conflict-04, which is how CI shard 4 went
# red). `detector_findings`, this module's only caller-side sibling, is the same
# shape. `repo_root` is used once, in `__main__`, to find the `.env`.

logger = get_logger("icdev.kanban.promotion_gate")

#: Kill switch, in the auditable form CLAUDE.md's PreToolUse rule asks for — an
#: environment variable a run records, never a shell operator inside a config
#: string.
MODE_ENV = "ICDEV_PROMOTION_GATE"
MODE_ENFORCE = "enforce"   # a record is withheld from promotion
MODE_REPORT = "report"     # the verdict is derived and logged; nothing withheld
MODE_OFF = "off"           # not even read
_MODES = (MODE_ENFORCE, MODE_REPORT, MODE_OFF)


def mode() -> str:
    """``enforce`` (default), ``report`` or ``off``.

    Default is ENFORCE, and the replay in this module's docstring is the whole
    argument for that: 18 incremental withholds, 0 of them wrong against an
    independent git check. ``dispatch_admission`` ships advisory because its
    survey put it at 88.2% correct; this one is at 100% over its population and
    the failure mode it prevents — a session that cannot go RED — has no upside.
    """
    raw = str(os.environ.get(MODE_ENV, "") or "").strip().lower()
    if raw in _MODES:
        return raw
    if raw in ("0", "false", "no"):
        return MODE_OFF
    return MODE_ENFORCE


def _ids(tasks: Sequence[Any], key: Optional[Callable[[Any], Any]] = None) -> List[str]:
    """Task ids out of a sequence of ids, dicts or rows. Order preserved."""
    out: List[str] = []
    for t in tasks or ():
        if key is not None:
            raw = key(t)
        elif isinstance(t, Mapping):
            raw = t.get("id")
        else:
            raw = t
        if raw:
            out.append(str(raw))
    return out


def verdicts(task_ids: Sequence[Any], *, conn=None,
             key: Optional[Callable[[Any], Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Re-derive the record-not-card disposition for each task id that carries a
    ``recovery`` finding. Ids with no finding are ABSENT from the result.

    Absent means "no opinion", which is the same as promote. Only a finding
    whose disposition is RECORD *right now* produces ``withheld: True``, and
    only in ``enforce`` mode; ``would_withhold`` records the verdict either way
    so ``report`` mode measures the same thing the armed gate would do.
    """
    wanted = set(_ids(task_ids, key))
    if not wanted or mode() == MODE_OFF:
        return {}

    own = conn is None
    try:
        from tools.kanban.detector_findings import (
            DETECTOR_RECOVERY, DISPOSITION_RECORD, _ledger_rows_safe, _task_status,
            card_disposition, ledger_landing, list_findings, merge_after_escalation,
            tables_present, watcher_outcome_rows,
        )
        if own:
            from tools.db.storage import get_connection
            conn = get_connection()
        if not tables_present(conn):
            return {}
        findings = [f for f in list_findings(conn, detector=DETECTOR_RECOVERY,
                                             limit=10_000)
                    if str(f.get("task_id") or "") in wanted]
        if not findings:
            # The common case, and the cheap one: no candidate is a detector
            # card, so the two big audit reads below never happen.
            return {}

        # LIFETIME, like ``dispositions``: a card can sit in `suggested` for
        # days, which puts its escalation outside any recent window — and an
        # escalation the reader cannot see reports UNMEASURABLE, i.e. promote.
        rows = watcher_outcome_rows(conn, window_hours=None)
        ledger = _ledger_rows_safe(conn)

        armed = mode() == MODE_ENFORCE
        out: Dict[str, Dict[str, Any]] = {}
        for rec in findings:
            subject = str(rec.get("subject") or "")
            probe = dict(rec)
            order = merge_after_escalation(rows, subject)
            probe["merge_after_escalation"] = order
            disp = card_disposition(
                probe, subject_status=_task_status(conn, subject),
                landed_on_main=ledger_landing(ledger, subject,
                                              escalated_at=order.get("escalated_at")))
            is_record = disp.get("disposition") == DISPOSITION_RECORD
            out[str(rec["task_id"])] = {
                "task_id": str(rec["task_id"]),
                "finding_id": rec.get("finding_id"),
                "subject": subject,
                "mode": mode(),
                "would_withhold": is_record,
                "withheld": bool(is_record and armed),
                **disp,
            }
        return out
    except Exception as exc:  # noqa: BLE001 — a gate that cannot see must not refuse
        logger.warning("promotion_gate: verdicts unreadable, promoting everything: %s", exc)
        return {}
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


#: ``(door, task_id, finding_id)`` already announced by THIS process.
#:
#: A withheld card stays where it is, so ``_get_due_tasks`` re-selects it on
#: every 60s cycle and would log the same line 1,440 times a day — the shape
#: that gets a log filtered and then ignored. First sight is a WARNING, every
#: repeat is DEBUG. Keyed on the finding id as well as the task, so a card whose
#: finding is superseded announces again rather than inheriting the old silence.
_ANNOUNCED: set = set()


def filter_promotable(tasks: Sequence[Any], *, conn=None,
                      key: Optional[Callable[[Any], Any]] = None,
                      door: str = "") -> Tuple[List[Any], Dict[str, Dict[str, Any]]]:
    """``(promotable, withheld_by_task_id)``, order preserved.

    ``door`` names the caller in the log line, because three of them ask and a
    withhold nobody can attribute is a withhold nobody can audit. Each held
    entry carries ``announced_before`` so a caller that prints to a console can
    print once rather than once a cycle.
    """
    if not tasks:
        return list(tasks or []), {}
    verds = verdicts(tasks, conn=conn, key=key)
    if not verds:
        return list(tasks), {}
    held = {k: v for k, v in verds.items() if v.get("withheld")}
    for tid, v in verds.items():
        if not v.get("would_withhold"):
            continue
        stamp = (door, tid, v.get("finding_id"))
        seen = stamp in _ANNOUNCED
        _ANNOUNCED.add(stamp)
        v["announced_before"] = seen
        (logger.debug if seen else logger.warning)(
            "promotion_gate[%s]: %s %s — %s (finding %s stays ACTIVE; see "
            "`detector_findings --records`)",
            door or "?", "WITHHELD" if v.get("withheld") else "would withhold",
            tid, v.get("reason"), v.get("finding_id"))
    if not held:
        return list(tasks), {}

    def _id_of(t: Any) -> str:
        return (_ids([t], key) or [""])[0]

    return [t for t in tasks if _id_of(t) not in held], held


# ---------------------------------------------------------------------------
# Replay — the SAME predicate, run against history (dispatch_admission's rule)
# ---------------------------------------------------------------------------
def _rows_at(rows: Sequence[Mapping[str, Any]], at) -> List[Mapping[str, Any]]:
    out = []
    for r in rows or ():
        t = parse_utc_timestamp(dict(r).get("created_at"))
        if t is not None and t <= at:
            out.append(r)
    return out


def survey(conn=None, *, control: bool = True) -> Dict[str, Any]:
    """Replay every detector card's promotion and report what this gate would
    have done — and, as the CONTROL, whether the subject was really delivered.

    Point-in-time throughout: the watcher rows, the ledger rows and the
    subject's board status are each truncated to the card's own
    ``scheduled_at``. A card with no ``scheduled_at`` was never promoted and is
    excluded from the denominator rather than counted as a pass.

    The control is INDEPENDENT of the rule under test:
    ``landed_check.check_landed_bulk`` asks git whether the subject id is on
    ``origin/<default>``, which shares no input with the two audit rows
    ``merge_after_escalation`` orders. ``landed_check`` is itself fail-open, so
    a subject it cannot confirm is reported, never assumed wrong — see
    ``control_unconfirmed``.
    """
    own = conn is None
    try:
        from tools.kanban.detector_findings import (
            DETECTOR_RECOVERY, DISPOSITION_RECORD,
            _ledger_rows_safe, card_disposition, ledger_landing, list_findings,
            merge_after_escalation, tables_present, watcher_outcome_rows,
        )
        if own:
            from tools.db.storage import get_connection
            conn = get_connection()
        if not tables_present(conn):
            return {"state": "unmigrated", "measured": False}
        findings = [f for f in list_findings(conn, detector=DETECTOR_RECOVERY,
                                             limit=10_000) if f.get("task_id")]
        if not findings:
            return {"state": "no_findings", "measured": False, "promoted": 0}

        card_ids = [str(f["task_id"]) for f in findings]
        subjects = sorted({str(f["subject"]) for f in findings})
        cards = _by_id(conn, "SELECT id, status, scheduled_at FROM kanban_tasks",
                       card_ids)
        subject_now = _by_id(conn, "SELECT id, status, completed_at FROM kanban_tasks",
                             subjects)
        subject_tr = _transitions(conn, subjects)
        rows = watcher_outcome_rows(conn, window_hours=None)
        ledger = _ledger_rows_safe(conn)

        promoted: List[Dict[str, Any]] = []
        never_promoted = 0
        for f in findings:
            at = parse_utc_timestamp((cards.get(str(f["task_id"])) or {}).get("scheduled_at"))
            if at is None:
                never_promoted += 1
                continue
            subject = str(f["subject"])
            order = merge_after_escalation(_rows_at(rows, at), subject)
            probe = dict(f)
            probe["merge_after_escalation"] = order
            disp = card_disposition(
                probe,
                subject_status=_status_at(subject, at, subject_tr, subject_now),
                landed_on_main=ledger_landing(_rows_at(ledger, at), subject,
                                              escalated_at=order.get("escalated_at")))
            promoted.append({
                "task_id": str(f["task_id"]), "subject": subject,
                "promoted_at": at.isoformat(),
                "promotion_verdict": disp.get("disposition"),
                "reason": disp.get("reason"), "landed_via": disp.get("landed_via"),
            })

        withheld = [p for p in promoted if p["promotion_verdict"] == DISPOSITION_RECORD]
        report: Dict[str, Any] = {
            "state": "ok", "measured": True,
            "findings_with_a_card": len(findings),
            "never_promoted": never_promoted,
            "promoted": len(promoted),
            "would_withhold": len(withheld),
            # `x if n else 100.0` here would breach args/perfect_score_gate.yaml.
            "would_withhold_pct": (round(100.0 * len(withheld) / len(promoted), 1)
                                   if promoted else None),
            "dispatched_anyway": [p["task_id"] for p in withheld],
            "findings": promoted,
        }
        if control and withheld:
            report.update(_control(sorted({p["subject"] for p in withheld})))
        return report
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _by_id(conn, select: str, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    rows = conn.execute(f"{select} WHERE id IN ({ph})", tuple(ids)).fetchall()  # nosec B608
    return {str(dict(r)["id"]): dict(r) for r in rows}


def _transitions(conn, ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in conn.execute(
        "SELECT task_id, to_status, recorded_at FROM kanban_status_transitions "  # nosec B608
        f"WHERE task_id IN ({ph}) ORDER BY recorded_at", tuple(ids)
    ).fetchall():
        d = dict(r)
        out.setdefault(str(d["task_id"]), []).append(d)
    return out


def _status_at(subject: str, at, transitions, now_rows) -> Optional[str]:
    """The subject's board status as of ``at``, or None when it cannot be known.

    None is the honest answer and the safe one: ``card_disposition`` reads it as
    "not on the board" and returns CARD, so an unknowable history replays as a
    dispatch rather than as a withhold this gate never earned.
    """
    last = None
    for d in transitions.get(subject) or ():
        t = parse_utc_timestamp(d.get("recorded_at"))
        if t is not None and t <= at:
            last = d.get("to_status")
    if last is not None:
        return str(last)
    cur = now_rows.get(subject)
    if not cur:
        return None
    done_at = parse_utc_timestamp(cur.get("completed_at"))
    return str(cur.get("status")) if (done_at is not None and done_at <= at) else None


def _control(subjects: Sequence[str]) -> Dict[str, Any]:
    """Independent delivery check for the would-be-withheld subjects."""
    try:
        from tools.kanban.landed_check import check_landed_bulk

        rep = check_landed_bulk(list(subjects))
    except Exception as exc:  # noqa: BLE001 — an unavailable control is not a clean one
        return {"control_state": f"unavailable: {type(exc).__name__}: {exc}",
                "control_confirmed": None, "control_unconfirmed": None}
    confirmed = [s for s in subjects if (rep.get(s) or {}).get("landed")]
    unconfirmed = [s for s in subjects if not (rep.get(s) or {}).get("landed")]
    return {
        "control_state": "ok",
        "control_subjects": len(subjects),
        "control_confirmed": len(confirmed),
        "control_unconfirmed": unconfirmed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", nargs="+", metavar="TASK_ID",
                    help="re-derive the promotion verdict for these card ids")
    ap.add_argument("--records", action="store_true",
                    help="verdict for every detector card still OPEN on the board")
    ap.add_argument("--survey", action="store_true",
                    help="replay every promoted detector card through this gate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.survey:
        rep = survey()
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        else:
            if not rep.get("measured"):
                print(f"UNMEASURED — {rep.get('state')}")
                return 0
            print(f"promoted detector cards: {rep['promoted']} "
                  f"(never promoted: {rep['never_promoted']})")
            print(f"would have been WITHHELD: {rep['would_withhold']} "
                  f"({rep['would_withhold_pct']}%)")
            print(f"control — subjects independently confirmed on main: "
                  f"{rep.get('control_confirmed')} of {rep.get('control_subjects')}; "
                  f"unconfirmed: {rep.get('control_unconfirmed')}")
        return 0

    ids: List[str] = list(args.check or [])
    if args.records and not ids:
        ids = _open_detector_cards()
    if not ids:
        print("nothing to check — pass --check <task-id> or --records")
        return 0
    verds = verdicts(ids)
    if args.json:
        print(json.dumps({"mode": mode(), "verdicts": verds}, indent=2, default=str))
    else:
        for tid in ids:
            v = verds.get(tid)
            if not v:
                print(f"promote  {tid:<28} no recovery finding — no opinion")
                continue
            print(f"{'WITHHOLD' if v['would_withhold'] else 'promote '} {tid:<28} "
                  f"{v['subject']:<28} {v['reason']}")
    return 0


def _open_detector_cards() -> List[str]:
    """Detector cards that have not reached a terminal status yet."""
    try:
        from tools.db.storage import get_connection
        from tools.kanban.detector_findings import DETECTOR_RECOVERY, list_findings

        conn = get_connection()
        try:
            ids = [str(f["task_id"]) for f in
                   list_findings(conn, detector=DETECTOR_RECOVERY, limit=10_000)
                   if f.get("task_id")]
            if not ids:
                return []
            ph = ",".join(["%s"] * len(ids))
            rows = conn.execute(
                f"SELECT id FROM kanban_tasks WHERE id IN ({ph}) "  # nosec B608
                "AND status NOT IN ('done', 'failed')", tuple(ids)).fetchall()
            return [str(dict(r)["id"]) for r in rows]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("promotion_gate: open-card listing unavailable: %s", exc)
        return []


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run reads the same board as the
    # daemon. Repo root via __file__, never cwd — this runs from worktrees.
    try:
        from dotenv import load_dotenv as _load

        _load(repo_root(__file__) / ".env", override=True)
    except ImportError:
        pass
    sys.exit(main())
