# CUI // SP-CTI
"""Should this task be dispatched at all? Asked once, before a token is spent.

THE DEFECT. Measured 2026-08-21 over 523 kanban PRs: 27 of 232 task branches
(11.6%) drew more than one PR and 12 landed TWO merged PRs. #1862 duplicated
rem-hyg-14 — already merged as #1858 — and, being ten commits behind, would have
deleted 5,545 lines across 76 files including a fix that merged two hours
earlier. GitHub reported it MERGEABLE throughout, correctly: `mergeable` answers
only whether the diff COLLIDES.

The guards that existed were scattered and each individually fail-open — a
coordination lease keyed on a pid that exits after handoff, `landed_check`
(advisory, because `landed_dispatch_survey` measured it 29% WRONG when it
fires), and the respawn guard. No single place asked every question and RECORDED
its answer, so the refusal rate could be argued about but not measured.

THE DISCRIMINATOR IS PR IDENTITY, not "is the id on main". A task legitimately
spans several commits across several PRs, which is exactly why `landed_check` is
wrong a third of the time it fires. What #1862 actually was: a MERGED PR already
carried this task's work, and the task was dispatched again anyway.

NARROWED BY MEASUREMENT, NOT BY INTUITION. Replaying all 6,528 recorded
scheduler dispatches against real PR history:

    rule                                     fires   wrong    wrong % of
                                                             all dispatches
    landed_check (id is on main)             9.20%   29.0%      2.67%
    a merged PR exists for this branch       3.80%   25.0%      0.95%
    ... AND no PR was OPEN at dispatch       3.09%   12.4%      0.38%
    ... AND exactly ONE prior merge          2.99%   11.8%      0.35%

Both narrowings are the same finding in two forms: a branch with work ALREADY IN
FLIGHT is a legitimately multi-PR task. Refusing when another PR was open would
have been wrong 80.4% of the time, and refusing a branch with two prior merges
wrong 85.7% of the time. Those are not edge cases to be excused — they are the
population the rule must not touch.

DEFAULT IS `report`, EVEN THOUGH THE SURVEY SUPPORTS ARMING. 0.35% is well under
the 1.63% this repo already calls refusing routine work, and 88.2% of fires are
correct. It still ships advisory: shipping a gate enforcing on day one, against a
survey written by the same change, is the pattern this repo has been bitten by
twice (kpr-fix-03). `KANBAN_DISPATCH_ADMISSION=enforce` arms it; `=off` disables
it entirely. Re-survey with `--survey` before changing that default, and never
raise a threshold to quieten it.

ONE RULE, TWO RENDERINGS. `classify` is the whole policy, and `--survey` replays
history through THAT SAME FUNCTION. A survey with its own copy of the predicate
measures a gate that does not exist — the defect `deps.py` names explicitly
after six enforcement sites each grew their own copy.

FAIL-OPEN, ALWAYS. A forge that cannot be reached, a task with no branch, an
unparseable date: all `unmeasurable`, which never refuses. A gate that blocks
dispatch when it cannot see is how a board stops moving at 3am.

Usage:
    python -m tools.kanban.dispatch_admission --task rem-hyg-14
    python -m tools.kanban.dispatch_admission --survey --json
    python -m tools.kanban.dispatch_admission --survey --window-days 30
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — gh only, fixed argv, shell=False
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# ── Verdicts ────────────────────────────────────────────────────────────────
#: Dispatch. The only verdict that spends a token.
ALLOW = "allow"
#: A merged PR already carries this task and nothing is in flight.
REFUSE = "refuse"
#: The question could not be answered. NEVER refuses — see the module docstring.
UNMEASURABLE = "unmeasurable"

#: report (default) | enforce | off
MODE_ENV = "KANBAN_DISPATCH_ADMISSION"
DEFAULT_MODE = "report"
_MODES = ("report", "enforce", "off")

GH_TIMEOUT_SECONDS = 30


@dataclass
class Verdict:
    task_id: str
    verdict: str
    reason: str
    #: Merged PRs for this branch that predate the moment being judged.
    prior_merged: List[int] = field(default_factory=list)
    #: PRs for this branch open at that moment — the strongest allow signal.
    open_prs: List[int] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        """Whether this verdict stops a dispatch, given the current mode."""
        return self.verdict == REFUSE and mode() == "enforce"

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "verdict": self.verdict,
                "reason": self.reason, "prior_merged": self.prior_merged,
                "open_prs": self.open_prs, "mode": mode(), "blocks": self.blocks}


def mode() -> str:
    raw = (os.environ.get(MODE_ENV) or DEFAULT_MODE).strip().lower()
    return raw if raw in _MODES else DEFAULT_MODE


def branch_for(task_id: str) -> str:
    """The canonical branch a dispatched task builds on."""
    return f"kanban/{task_id}"


# ────────────────────────────────────────────────────────────────────────────
# The rule. Everything else renders it.
# ────────────────────────────────────────────────────────────────────────────
def classify(task_id: str, prior_merged: Optional[Sequence[int]],
             open_prs: Optional[Sequence[int]]) -> Verdict:
    """The whole policy, as a pure function.

    `prior_merged` is None when PR history could not be read — distinct from an
    empty list, which means it was read and there were none. Conflating them
    would let an unreachable forge read as "nothing has merged", which is the
    ALLOW direction here and therefore safe, but it would also make the survey
    report coverage it never had.
    """
    if not task_id:
        return Verdict(task_id, UNMEASURABLE, "no task id")
    if prior_merged is None or open_prs is None:
        return Verdict(task_id, UNMEASURABLE,
                       "PR history for this branch could not be read")

    prior = list(prior_merged)
    opened = list(open_prs)

    if not prior:
        return Verdict(task_id, ALLOW, "no merged PR carries this task yet",
                       prior_merged=prior, open_prs=opened)

    # Work already in flight. Measured: refusing here is WRONG 80.4% of the
    # time — an open PR is what a legitimately multi-PR task looks like.
    if opened:
        return Verdict(task_id, ALLOW,
                       f"a PR for this branch is open ({', '.join('#%d' % p for p in opened)}) "
                       f"— work in flight, not a duplicate",
                       prior_merged=prior, open_prs=opened)

    # Two or more prior merges is the same signal one step on. Measured: 85.7%
    # wrong. A branch that has landed twice is a task that lands in pieces.
    if len(prior) > 1:
        return Verdict(task_id, ALLOW,
                       f"{len(prior)} merged PRs already — this task lands in pieces",
                       prior_merged=prior, open_prs=opened)

    return Verdict(
        task_id, REFUSE,
        f"PR #{prior[0]} already merged for this task and nothing is in flight",
        prior_merged=prior, open_prs=opened)


# ────────────────────────────────────────────────────────────────────────────
# Reading PR history
# ────────────────────────────────────────────────────────────────────────────
def _gh_prs(branch: str, runner=None) -> Optional[List[Dict[str, Any]]]:
    """PRs for one branch, or None when the forge could not answer."""
    run = runner or _run_gh
    try:
        result = run(["pr", "list", "--head", branch, "--state", "all",
                      "--json", "number,state,mergedAt,createdAt", "--limit", "50"])
    except Exception:  # noqa: BLE001
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    try:
        return json.loads(getattr(result, "stdout", "") or "[]")
    except (ValueError, TypeError):
        return None


def _run_gh(args: List[str]):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, gh only
        ["gh", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=GH_TIMEOUT_SECONDS, check=False, shell=False,
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def split_history(prs: Optional[Sequence[Dict[str, Any]]],
                  at: Optional[datetime] = None):
    """(prior_merged, open_prs) as of *at*, or (None, None) if unreadable."""
    if prs is None:
        return None, None
    moment = at or datetime.now(timezone.utc)
    prior, opened = [], []
    for pr in prs:
        number = pr.get("number")
        if number is None:
            continue
        merged_at = _parse_dt(pr.get("mergedAt"))
        created_at = _parse_dt(pr.get("createdAt"))
        if merged_at and merged_at < moment:
            prior.append(int(number))
        elif created_at and created_at < moment and (
                merged_at is None or merged_at > moment):
            # Open at that instant: created before, not yet merged. A CLOSED
            # unmerged PR reaches here too, which is deliberate — it is still
            # evidence somebody was working this branch.
            opened.append(int(number))
    return sorted(prior), sorted(opened)


def assess(task_id: str, *, runner=None, at: Optional[datetime] = None) -> Verdict:
    """The gate's entry point. Never raises."""
    prs = _gh_prs(branch_for(task_id), runner=runner)
    prior, opened = split_history(prs, at)
    return classify(task_id, prior, opened)


# ────────────────────────────────────────────────────────────────────────────
# The survey — the SAME `classify`, replayed over recorded history
# ────────────────────────────────────────────────────────────────────────────
def survey(window_days: Optional[int] = None, conn=None,
           prs_by_branch: Optional[Dict[str, List[Dict[str, Any]]]] = None
           ) -> Dict[str, Any]:
    """What would this gate have done, and would it have been RIGHT?

    A dispatch is the `-> in_progress` transition written by the scheduler —
    the exact moment this gate runs, so it is the only population whose fire
    rate describes it.

    "Right" is decided by what happened AFTERWARDS: if another PR for the same
    branch merged after the dispatch, the re-dispatch produced work that landed
    and refusing it would have been WRONG. That is a fact about the branch, not
    a judgement, which is what makes the split checkable.
    """
    close = False
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        close = True
    try:
        sql = ("SELECT task_id, recorded_at FROM kanban_status_transitions "
               "WHERE to_status = 'in_progress' AND actor = 'scheduler'")
        params: List[Any] = []
        if window_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            sql += " AND recorded_at >= %s"
            params.append(cutoff.isoformat())
        sql += " ORDER BY recorded_at"
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"state": "unmeasurable", "reason": f"transitions unreadable: {exc}",
                "dispatches": None, "fires": None}
    finally:
        if close:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    if prs_by_branch is None:
        prs_by_branch = _all_kanban_prs()
    if prs_by_branch is None:
        return {"state": "unmeasurable", "reason": "PR history unavailable",
                "dispatches": None, "fires": None}

    dispatches = fires = right = wrong = skipped = 0
    for row in rows:
        record = dict(row)
        task_id = str(record.get("task_id") or "").strip()
        when = _parse_dt(record.get("recorded_at"))
        if not task_id or not when:
            skipped += 1
            continue
        dispatches += 1
        branch = branch_for(task_id)
        prs = prs_by_branch.get(branch, [])
        prior, opened = split_history(prs, when)
        if classify(task_id, prior, opened).verdict != REFUSE:
            continue
        fires += 1
        later = [p for p in prs
                 if (_parse_dt(p.get("mergedAt")) or datetime.min.replace(
                     tzinfo=timezone.utc)) > when]
        if later:
            wrong += 1
        else:
            right += 1

    if not dispatches:
        # No operating history. Never a clean zero.
        return {"state": "unmeasurable",
                "reason": "no recorded scheduler dispatches in this window",
                "dispatches": 0, "fires": None}
    return {
        "state": "measured",
        "dispatches": dispatches, "unparseable_rows": skipped,
        "fires": fires, "right": right, "wrong": wrong,
        "fire_rate_pct": round(fires / dispatches * 100, 2),
        "wrong_of_fires_pct": round(wrong / fires * 100, 1) if fires else None,
        "wrong_of_dispatches_pct": round(wrong / dispatches * 100, 2),
        "mode": mode(),
    }


def _all_kanban_prs() -> Optional[Dict[str, List[Dict[str, Any]]]]:
    result = _run_gh(["pr", "list", "--state", "all", "--limit", "800",
                      "--json", "number,headRefName,state,mergedAt,createdAt"])
    if getattr(result, "returncode", 1) != 0:
        return None
    try:
        data = json.loads(result.stdout or "[]")
    except (ValueError, TypeError):
        return None
    out: Dict[str, List[Dict[str, Any]]] = {}
    for pr in data:
        head = pr.get("headRefName") or ""
        if head.startswith("kanban/"):
            out.setdefault(head, []).append(pr)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", help="assess one task id")
    parser.add_argument("--survey", action="store_true",
                        help="replay recorded dispatches through the same rule")
    parser.add_argument("--window-days", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.task:
        verdict = assess(args.task)
        if args.json:
            print(json.dumps(verdict.to_dict(), indent=2, default=str))
        else:
            print(f"{verdict.verdict.upper()}  {verdict.task_id}")
            print(f"  {verdict.reason}")
            print(f"  mode={mode()} blocks={verdict.blocks}")
        return 0

    if args.survey:
        report = survey(window_days=args.window_days)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        elif report["state"] != "measured":
            print(f"Survey UNMEASURABLE: {report.get('reason')}")
        else:
            print(f"Dispatch admission survey — {report['dispatches']} dispatch(es)")
            print(f"  would fire      : {report['fires']} ({report['fire_rate_pct']}%)")
            print(f"  refusing RIGHT  : {report['right']}")
            print(f"  refusing WRONG  : {report['wrong']} "
                  f"({report['wrong_of_fires_pct']}% of fires, "
                  f"{report['wrong_of_dispatches_pct']}% of dispatches)")
            print(f"  mode            : {report['mode']}")
            print("  (1.63% of calls is the rate this repo already calls "
                  "refusing routine work)")
        return 0 if report["state"] == "measured" else 2

    parser.error("one of --task or --survey is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
