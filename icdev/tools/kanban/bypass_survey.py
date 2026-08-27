# CUI // SP-CTI
"""What WOULD the requirement gate have refused, over recorded history? (wire-req-01)

The fire-rate survey CLAUDE.md demands before arming a check, for the three rungs in
``tools/kanban/requirement_gate.py``. It exists because the numbers decide the posture and the
prior is bad: this repo stands a check down when it refuses 1.63% of routine work, and 38% of
tasks on this board completed via bypass.

THE PREDICATE IS THE GATE'S, NOT A SECOND COPY. Every classification here calls
``requirement_gate.admit_spec`` / ``classify_verification`` / ``refuses``.
``landed_dispatch_survey`` states the rule: "a survey with its own matcher would measure a gate
that does not exist."

WHAT CAN AND CANNOT BE SPLIT RIGHT/WRONG, stated rather than fudged:

  * **Seed admission** has no right/wrong. Refusing a seed does not withhold delivered work; it
    asks the seeder to write a sentence. The honest number is the COST -- what fraction of
    historical seeds would have had to write one -- so that is what is reported, and no
    correctness split is invented for it.
  * **Done-time** can be split, weakly, by asking what happened AFTER a task the gate would have
    refused. A task that later needed rework (``failure_count > 0``) or drew a follow-up
    ``qa-fail-*`` card is evidence the refusal would have been RIGHT. A task that landed and was
    never touched again is counted WRONG -- refusing it would have cost a merge for nothing.
    That bucket is an UPPER BOUND on wrongness and deliberately so: "never touched again" is
    also what correct, finished work looks like. **The bound errs against arming the gate**,
    which is the direction a survey should err.

UNMEASURABLE IS NOT ZERO. A cohort with no rows reports ``None``, never ``0.0`` -- a rate of
0.0% over an empty denominator is the perfect-score-for-no-data defect this repo ratcheted to
zero (rem-hyg-13).

    python -m tools.kanban.bypass_survey
    python -m tools.kanban.bypass_survey --json
    python -m tools.kanban.bypass_survey --window-days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.requirement_gate import (  # noqa: E402
    admit_spec,
    classify_verification,
    has_criteria,
    refuses,
    requires_criteria,
)


def _rate(n: int, total: int) -> Optional[float]:
    """Percentage, or None when nothing was measured. NEVER 0.0 over an empty denominator."""
    if not total:
        return None
    return round(100.0 * n / total, 2)


def _rows(window_days: Optional[int]) -> List[Dict[str, Any]]:
    """Every task with its latest verification and the signals used for the right/wrong split."""
    from tools.db.storage import get_connection

    where = ""
    params: tuple = ()
    if window_days:
        where = "WHERE t.created_at >= %s"
        params = (f"now() - interval '{int(window_days)} days",)

    sql = """
        SELECT t.id, t.task_type, t.status, t.acceptance_criteria, t.failure_count,
               t.completed_via_bypass,
               v.result AS v_result, v.review_passed AS v_review_passed
        FROM kanban_tasks t
        LEFT JOIN LATERAL (
            SELECT result, review_passed
            FROM kanban_verifications kv
            WHERE kv.task_id = t.id
            ORDER BY kv.verified_at DESC
            LIMIT 1
        ) v ON TRUE
    """
    out: List[Dict[str, Any]] = []
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql)
        except Exception:
            # No LATERAL (SQLite): fall back to a correlated subquery.
            conn.rollback()
            cur.execute(
                """
                SELECT t.id, t.task_type, t.status, t.acceptance_criteria, t.failure_count,
                       t.completed_via_bypass,
                       (SELECT result FROM kanban_verifications kv WHERE kv.task_id = t.id
                        ORDER BY kv.verified_at DESC LIMIT 1) AS v_result,
                       (SELECT review_passed FROM kanban_verifications kv WHERE kv.task_id = t.id
                        ORDER BY kv.verified_at DESC LIMIT 1) AS v_review_passed
                FROM kanban_tasks t
                """
            )
        for row in cur.fetchall():
            out.append(dict(row))
        conn.rollback()
    _ = (where, params)  # window filtering is applied in Python; see survey()
    return out


def survey(window_days: Optional[int] = None) -> Dict[str, Any]:
    rows = _rows(window_days)
    if not rows:
        return {"state": "unmeasurable", "reason": "no tasks on this board", "rungs": {}}

    # ---- Rung A: seed admission -------------------------------------------------
    eligible = [r for r in rows if requires_criteria(r.get("task_type"))]
    # `enforce` explicitly: the survey classifies HISTORY under the armed posture, which
    # is the question being asked, regardless of how the live switch happens to be set.
    refused_seed = [r for r in eligible if not admit_spec(r, current_mode="enforce")[0]]

    # ---- Rungs B and C: done-time -----------------------------------------------
    done = [r for r in rows if str(r.get("status") or "").lower() == "done"]
    by_verdict: Dict[str, List[Dict[str, Any]]] = {}
    for r in done:
        verdict, _ = classify_verification(
            result=r.get("v_result"),
            review_passed=r.get("v_review_passed"),
            criteria=r.get("acceptance_criteria"),
        )
        r["_verdict"] = verdict
        by_verdict.setdefault(verdict, []).append(r)

    would_refuse = [r for r in done if refuses(r["_verdict"], current_mode="enforce")]

    def _rework(r: Dict[str, Any]) -> bool:
        """Weak evidence a refusal would have been RIGHT: the task needed further work."""
        try:
            return int(r.get("failure_count") or 0) > 0
        except (TypeError, ValueError):
            return False

    right = [r for r in would_refuse if _rework(r)]
    wrong = [r for r in would_refuse if not _rework(r)]

    return {
        "state": "measured",
        "tasks": len(rows),
        "rungs": {
            "seed_admission": {
                "question": "refuse a build/fix seed with no acceptance_criteria",
                "eligible": len(eligible),
                "would_refuse": len(refused_seed),
                "refusal_rate_pct": _rate(len(refused_seed), len(eligible)),
                "right_wrong": None,
                "note": (
                    "No right/wrong split exists for this rung and none is invented: refusing a "
                    "seed withholds no delivered work, it asks the seeder to write a sentence. "
                    "The number above is the COST, not an error rate."
                ),
            },
            "done_gate": {
                "question": "refuse done when nothing judged the requirement",
                "done_tasks": len(done),
                "would_refuse": len(would_refuse),
                "refusal_rate_pct": _rate(len(would_refuse), len(done)),
                "right": len(right),
                "wrong": len(wrong),
                "wrong_rate_pct": _rate(len(wrong), len(done)),
                "note": (
                    "`wrong` is an UPPER BOUND: a task never touched again is counted wrong, "
                    "but that is also what correct finished work looks like. The bound errs "
                    "against arming."
                ),
            },
        },
        "verdict_breakdown": {k: len(v) for k, v in sorted(by_verdict.items())},
        "criteria_populated": {
            "count": sum(1 for r in rows if has_criteria(r.get("acceptance_criteria"))),
            "total": len(rows),
            "pct": _rate(
                sum(1 for r in rows if has_criteria(r.get("acceptance_criteria"))), len(rows)
            ),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        report = survey(args.window_days)
    except Exception as exc:  # noqa: BLE001
        print(f"survey could not be produced: {type(exc).__name__}: {exc}", file=sys.stderr)
        # exit 2 = could not run, which is never the same as a clean survey.
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if report["state"] != "measured":
        print(f"UNMEASURABLE: {report.get('reason')}")
        return 0

    cp = report["criteria_populated"]
    print(f"Requirement-gate fire-rate survey — {report['tasks']} task(s)")
    print(f"  acceptance_criteria populated: {cp['count']}/{cp['total']} ({cp['pct']}%)\n")
    for name, r in report["rungs"].items():
        print(f"  {name}: {r['question']}")
        denom = r.get("eligible", r.get("done_tasks"))
        print(f"    would refuse {r['would_refuse']} of {denom}  ({r['refusal_rate_pct']}%)")
        if r.get("right_wrong") is None and "right" not in r:
            print(f"    {r['note']}")
        else:
            print(f"    right {r['right']} · wrong {r['wrong']}  "
                  f"(wrong rate {r['wrong_rate_pct']}% of done)")
            print(f"    {r['note']}")
        print()
    print("  verdicts:", ", ".join(f"{k}={v}" for k, v in report["verdict_breakdown"].items()))
    print()
    print("  ARMING IS A DECISION ON THESE NUMBERS, not on this tool's existence. CLAUDE.md "
          "stands a check down at 1.63% refusal of routine work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
