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

import pytest  # noqa: E402

from tools.dashboard.recovery_summary import (  # noqa: E402
    CLOSED_STATUSES,
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


# --------------------------------------------------------------------------- #
# 4. The vocabulary is the WRITER's, and the board is an outcome (2026-09-02)
# --------------------------------------------------------------------------- #
from tools.dashboard.recovery_summary import AUDIT_ACTIONS, CLOSED_STATUSES  # noqa: E402


def test_every_attempt_kind_the_watcher_writes_is_counted():
    """`rebase_failed` and `ci_retrigger` ARE attempts. The first version counted
    only `resume` and a bare `rebase` the watcher never writes, so rmf-disc-01
    (rebased twice, resumed once) read as one attempt."""
    got = _by_id(summarize_recovery([
        _row("rebase_failed", "t-8", at="2026-09-02T22:02:34"),
        _row("resume", "t-8", at="2026-09-02T22:02:35"),
        _row("rebase_failed", "t-8", at="2026-09-02T22:03:29"),
        _row("ci_retrigger", "t-9", at="2026-09-02T23:01:10"),
    ]))
    assert got["t-8"]["attempts"] == 3
    assert got["t-9"]["attempts"] == 1 and got["t-9"]["kind"] == "ci_retrigger"


def test_a_refunded_attempt_is_not_an_attempt():
    """The watcher's own accounting withdrew it; the summary must agree, or a
    refunded resume reads as a retry loop."""
    assert summarize_recovery([_row("resume", "t-10"), _row("resume_refund", "t-10")]) == []


def test_a_task_the_board_closed_after_an_attempt_recovered():
    """THE 2026-09-02 defect: rmf-disc-01 was marked done by the watcher's own
    reconcile ("PR is MERGED") -- a status transition, never a `merge` audit row
    -- and read "still trying" for an hour."""
    got = summarize_recovery([_row("resume", "rmf-disc-01")],
                             task_status={"rmf-disc-01": "done"})
    assert got[0]["outcome"] == RECOVERED
    assert got[0]["board_status"] == "done"


def test_escalation_still_wins_over_a_closed_board_row():
    """A task closed AFTER the watcher gave up was closed by the human it asked for."""
    got = summarize_recovery([_row("resume", "t-11"), _row("escalate", "t-11")],
                             task_status={"t-11": "done"})
    assert got[0]["outcome"] == NEEDED_A_HUMAN


def test_an_open_board_row_is_still_unresolved():
    got = summarize_recovery([_row("resume", "t-12")], task_status={"t-12": "pr_opened"})
    assert got[0]["outcome"] == UNRESOLVED
    assert got[0]["board_status"] == "pr_opened"


def test_no_task_status_keeps_the_old_answer():
    """Callers that pass nothing get exactly the pre-change behaviour."""
    assert summarize_recovery([_row("resume", "t-13")])[0]["outcome"] == UNRESOLVED


def test_the_audit_query_and_the_classifier_share_one_vocabulary():
    """The SQL in app.py must fetch every kind the classifier can count, BY
    REFERENCE -- the two drifted, and two attempt kinds silently vanished."""
    src = (ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "AUDIT_ACTIONS" in src, "app.py no longer builds the recovery query from AUDIT_ACTIONS"
    assert "'pr_watcher.rebase', 'pr_watcher.resume'" not in src, "a hand-written action list is back"
    for kind in ("resume", "rebase", "rebase_failed", "ci_retrigger",
                 "resume_refund", "rebase_refund", "escalate", "merge"):
        assert f"pr_watcher.{kind}" in AUDIT_ACTIONS


def test_closed_statuses_match_the_project_card():
    """Two hand-maintained copies of 'what counts as closed' is the defect the
    project cards had until 2026-08-28; this one is pinned to that one."""
    src = (ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert '_closed_statuses = ("done", "decomposed", "cancelled", "merged")' in src
    assert CLOSED_STATUSES == ("done", "decomposed", "cancelled", "merged")


# ══════════════════════════════════════════════════════════════════════════
# "needed a human" is a fact about the past; "needs a human" is a call to
# action. The panel conflated them (2026-09-09)
#
# MEASURED on the live board that morning: the Autonomous Recovery headline
# read `6 needed a human` and ALL SIX subjects were `done` -- six calls to
# action with nothing behind any of them. That is the same overstatement this
# module exists to fix ("14 auto-recovered" where the honest answer was 3),
# pointing the other way: the panel now understated how much was FINISHED.
# ══════════════════════════════════════════════════════════════════════════

def _escalated_rows(task_id="t1"):
    """One attempt and the watcher's own escalation, the shape that yields
    NEEDED_A_HUMAN whatever follows it."""
    return [
        {"action": "pr_watcher.resume", "created_at": "2026-09-09T01:00:00+00:00",
         "d": json.dumps({"task_id": task_id, "reason": "first injection"})},
        {"action": "pr_watcher.escalate", "created_at": "2026-09-09T02:00:00+00:00",
         "d": json.dumps({"task_id": task_id, "reason": "resume undelivered"})},
    ]


class TestNeedsAttentionSeparatesOpenFromHistorical:

    def test_an_escalation_on_an_OPEN_task_needs_attention(self):
        out = summarize_recovery(_escalated_rows(), task_status={"t1": "pr_opened"})
        assert len(out) == 1
        assert out[0]["outcome"] == NEEDED_A_HUMAN
        assert out[0]["needs_attention"] is True

    def test_an_escalation_on_a_DONE_task_does_not(self):
        """The human already came. The escalation stays in the record; it stops
        being a call to action."""
        out = summarize_recovery(_escalated_rows(), task_status={"t1": "done"})
        assert out[0]["outcome"] == NEEDED_A_HUMAN, "the historical fact is unchanged"
        assert out[0]["needs_attention"] is False

    @pytest.mark.parametrize("status", list(CLOSED_STATUSES))
    def test_every_closed_status_clears_the_call_to_action(self, status):
        """One definition of closed. A second copy in the template is the defect
        this field exists to prevent."""
        out = summarize_recovery(_escalated_rows(), task_status={"t1": status})
        assert out[0]["needs_attention"] is False, status

    def test_an_unknown_board_status_still_needs_attention(self):
        """Fail toward asking. A task the board cannot speak for is not
        evidence that a human has been."""
        out = summarize_recovery(_escalated_rows(), task_status={})
        assert out[0]["needs_attention"] is True

    def test_a_recovered_task_never_needs_attention(self):
        rows = [
            {"action": "pr_watcher.resume", "created_at": "2026-09-09T01:00:00+00:00",
             "d": json.dumps({"task_id": "t2", "reason": "r"})},
            {"action": "pr_watcher.merge", "created_at": "2026-09-09T02:00:00+00:00",
             "d": json.dumps({"task_id": "t2", "reason": "auto-merge ok"})},
        ]
        out = summarize_recovery(rows, task_status={"t2": "done"})
        assert out[0]["outcome"] == RECOVERED
        assert out[0]["needs_attention"] is False

    def test_an_unresolved_task_is_not_reported_as_needing_a_human(self):
        """`still trying` is the watcher's own state and has its own colour; it
        must not be folded into the escalation count."""
        rows = [{"action": "pr_watcher.resume",
                 "created_at": "2026-09-09T01:00:00+00:00",
                 "d": json.dumps({"task_id": "t3", "reason": "r"})}]
        out = summarize_recovery(rows, task_status={"t3": "in_progress"})
        assert out[0]["outcome"] == UNRESOLVED
        assert out[0]["needs_attention"] is False

    def test_the_field_is_present_on_every_entry(self):
        """The template branches on it, so a missing key would silently read as
        falsy and paint an open escalation as resolved."""
        rows = _escalated_rows("a") + _escalated_rows("b")
        out = summarize_recovery(rows, task_status={"a": "done"})
        assert len(out) == 2
        assert all("needs_attention" in e for e in out)


class TestThePanelReadsTheDerivedFieldRatherThanReimplementingIt:
    """Source assertions. The headline and the row colour are the two places a
    second definition of "closed" would grow back."""

    @staticmethod
    def _panel() -> str:
        from pathlib import Path
        import tools.dashboard as d
        return (Path(d.__file__).parent / "templates"
                / "_autonomy_status.html").read_text(encoding="utf-8")

    def test_the_headline_counts_open_escalations_separately(self):
        src = self._panel()
        assert "_humanOpen" in src
        assert "r.needs_attention" in src
        assert "NEED a human" in src

    def test_a_fully_resolved_batch_says_so(self):
        assert "needed a human, all resolved" in self._panel()

    def test_the_template_does_not_carry_its_own_closed_list(self):
        """It must branch on needs_attention, never on board_status strings."""
        src = self._panel()
        for literal in ("'decomposed'", '"decomposed"', "'cancelled'", '"cancelled"'):
            assert literal not in src, f"{literal} is a second closed-status copy"

    def test_red_is_reserved_for_what_is_still_open(self):
        src = self._panel()
        i = src.index("needs_attention")
        window = src[i:i + 400]
        assert "#dc3545" in window, "red must sit on the needs_attention branch"
