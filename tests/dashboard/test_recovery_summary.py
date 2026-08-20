# CUI // SP-CTI
""""Auto-recovered" must count outcomes, not attempts (rem-hyg-16).

The Autonomous Recovery panel rendered one line per ``pr_watcher.resume`` /
``pr_watcher.rebase`` audit row and headlined the count as "N auto-recovered
(24h)", under a section titled "Recovered without a human".

Those rows are ATTEMPTS, and the resume budget is five — so the overstatement is
structural, not incidental: **a task retried to the cap and then fixed by hand
contributes five rows to a list of recoveries, while a task genuinely fixed on
the first attempt contributes one.** The worse the outcome, the bigger the
number.

Measured on the live board 2026-08-20, where the panel read "14 auto-recovered":
six distinct tasks, of which three recovered. The two contributing ten of the
fourteen rows had both escalated — and ``task-c49fb2727d`` was then fixed BY
HAND (a 16-commit-stale branch plus a host-dependent ``as_posix()`` comparison;
no LLM resume can fix either, because the branch it is asked to repair looks
fine locally).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dashboard.recovery_summary import (  # noqa: E402
    NEEDED_A_HUMAN,
    RECOVERED,
    UNRESOLVED,
    summarize_recovery,
)


def _row(action, task_id, at="2026-08-20T10:00:00", reason=""):
    return {
        "action": f"pr_watcher.{action}",
        "d": json.dumps({"task_id": task_id, "reason": reason}),
        "created_at": at,
    }


def _by_id(rows):
    return {r["task_id"]: r for r in rows}


# --------------------------------------------------------------------------- #
# 1. The defect: five attempts on one task is one task, not five recoveries
# --------------------------------------------------------------------------- #
def test_five_attempts_on_one_task_collapse_to_one_row():
    rows = summarize_recovery([_row("resume", "t-1", at=f"2026-08-20T10:0{i}:00")
                               for i in range(5)])
    assert len(rows) == 1
    assert rows[0]["attempts"] == 5, "the retry count must survive — a loop must read as a loop"


def test_an_escalated_task_is_not_a_recovery_however_many_attempts():
    """`escalate` is the watcher's OWN verdict: "manual intervention required"."""
    rows = summarize_recovery(
        [_row("resume", "t-2") for _ in range(5)] + [_row("escalate", "t-2")]
    )
    assert rows[0]["outcome"] == NEEDED_A_HUMAN
    assert rows[0]["attempts"] == 5


def test_a_merge_after_an_escalation_is_a_humans_merge():
    """THE ordering rule. Both live 5-attempt tasks show merged AND escalated:
    they merged only because a person stepped in. Letting `merge` win would
    reclassify the two worst outcomes as successes."""
    rows = summarize_recovery([
        _row("resume", "t-3"), _row("escalate", "t-3"), _row("merge", "t-3"),
    ])
    assert rows[0]["outcome"] == NEEDED_A_HUMAN


def test_a_first_attempt_that_merged_is_a_real_recovery():
    rows = summarize_recovery([_row("resume", "t-4"), _row("merge", "t-4")])
    assert rows[0]["outcome"] == RECOVERED
    assert rows[0]["attempts"] == 1


def test_an_attempt_with_no_outcome_yet_is_unresolved():
    """Neither merged nor escalated: still in flight. Counting it as recovered
    is the optimism this whole change removes."""
    assert summarize_recovery([_row("resume", "t-5")])[0]["outcome"] == UNRESOLVED


def test_a_rebase_counts_as_an_attempt_too():
    rows = summarize_recovery([_row("rebase", "t-6"), _row("merge", "t-6")])
    assert rows[0]["outcome"] == RECOVERED
    assert rows[0]["kind"] == "rebase"


# --------------------------------------------------------------------------- #
# 2. What must NOT be counted
# --------------------------------------------------------------------------- #
def test_a_merge_the_watcher_never_attempted_is_not_a_recovery():
    """Inflating in the other direction: a PR that merged on its own was not
    recovered by anything."""
    assert summarize_recovery([_row("merge", "t-7")]) == []


def test_a_row_without_a_task_id_is_ignored():
    assert summarize_recovery([{"action": "pr_watcher.resume", "d": "{}",
                                "created_at": "2026-08-20T10:00:00"}]) == []


def test_unparseable_details_never_raise():
    assert summarize_recovery([{"action": "pr_watcher.resume", "d": "not json",
                                "created_at": "x"}]) == []


def test_no_rows_is_an_empty_list_not_an_error():
    assert summarize_recovery([]) == []
    assert summarize_recovery(None) == []


# --------------------------------------------------------------------------- #
# 3. The measured board, end to end
# --------------------------------------------------------------------------- #
def test_the_live_2026_08_20_shape_reports_three_of_six():
    """The exact rows behind the "14 auto-recovered" headline."""
    rows = []
    for i in range(5):
        rows.append(_row("resume", "qa-fail-e2e-baseurl-01", at=f"2026-08-20T09:0{i}:00"))
        rows.append(_row("resume", "task-c49fb2727d", at=f"2026-08-20T09:1{i}:00"))
    rows += [
        _row("escalate", "qa-fail-e2e-baseurl-01"), _row("merge", "qa-fail-e2e-baseurl-01"),
        _row("escalate", "task-c49fb2727d"), _row("merge", "task-c49fb2727d"),
        _row("resume", "cef-ui-01", at="2026-08-20T09:20:00"),
        _row("resume", "cef-ci-01", at="2026-08-20T09:21:00"), _row("merge", "cef-ci-01"),
        _row("rebase", "cef-ci-02", at="2026-08-20T09:22:00"), _row("merge", "cef-ci-02"),
        _row("resume", "rem-hyg-10", at="2026-08-20T09:23:00"), _row("merge", "rem-hyg-10"),
    ]
    got = summarize_recovery(rows)

    assert len(got) == 6, "14 audit rows, six tasks"
    outcomes = {r["outcome"] for r in got}
    assert outcomes == {RECOVERED, NEEDED_A_HUMAN, UNRESOLVED}

    counts = {o: sum(1 for r in got if r["outcome"] == o) for o in outcomes}
    assert counts[RECOVERED] == 3, f"the honest headline is 3, not 14: {counts}"
    assert counts[NEEDED_A_HUMAN] == 2

    idx = _by_id(got)
    assert idx["task-c49fb2727d"]["attempts"] == 5
    assert idx["task-c49fb2727d"]["outcome"] == NEEDED_A_HUMAN, (
        "the task a human fixed by hand must never appear as auto-recovered"
    )


def test_the_cap_keeps_the_newest_entries():
    rows = [_row("resume", f"t-{i}", at=f"2026-08-20T{i:02d}:00:00") for i in range(30)]
    got = summarize_recovery(rows, limit=5)
    assert len(got) == 5
    assert got[0]["task_id"] == "t-29", "newest first"
