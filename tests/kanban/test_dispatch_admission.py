# CUI // SP-CTI
"""Should this task be dispatched at all? (autonomy-adm-01)

Measured over 523 kanban PRs: 27 of 232 task branches (11.6%) drew more than one
PR and 12 landed TWO merged PRs. #1862 duplicated rem-hyg-14 — already merged as
#1858 — and would have deleted 5,545 lines across 76 files.

THE RULE WAS NARROWED BY MEASUREMENT, replaying all 6,528 recorded scheduler
dispatches:

    a merged PR exists for this branch        3.80% fire, 0.95% wrongly refused
    ... AND no PR was OPEN at dispatch        3.09% fire, 0.38%
    ... AND exactly ONE prior merge           2.99% fire, 0.35%

Both narrowings are the same finding twice: a branch with work ALREADY IN FLIGHT
is a legitimately multi-PR task. Refusing with another PR open was wrong 80.4% of
the time; refusing a branch with two prior merges, 85.7%. The tests below pin
those two carve-outs, because without them this gate refuses real work at four
times the rate the repo already calls unacceptable.

THE OTHER INVARIANT: unmeasurable NEVER refuses. A forge that cannot answer must
not stop a board.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import dispatch_admission as da  # noqa: E402

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _pr(number, *, merged=None, created=None, state="MERGED"):
    return {"number": number, "state": state,
            "mergedAt": merged.isoformat() if merged else None,
            "createdAt": (created or NOW - timedelta(days=2)).isoformat()}


# --------------------------------------------------------------------------- #
# 1. The rule
# --------------------------------------------------------------------------- #
def test_no_merged_pr_is_allowed():
    assert da.classify("t", [], []).verdict == da.ALLOW


def test_one_merged_pr_and_nothing_in_flight_is_refused():
    """#1862's exact shape."""
    got = da.classify("rem-hyg-14", [1858], [])
    assert got.verdict == da.REFUSE
    assert "1858" in got.reason, "the refusal must name the PR that already landed"


# --------------------------------------------------------------------------- #
# 2. The two carve-outs, each measured
# --------------------------------------------------------------------------- #
def test_an_open_pr_allows_even_though_one_already_merged():
    """Measured: refusing here would be WRONG 80.4% of the time. An open PR is
    what a legitimately multi-PR task looks like from the outside."""
    got = da.classify("t", [100], [101])
    assert got.verdict == da.ALLOW
    assert "in flight" in got.reason


def test_two_prior_merges_allow():
    """Measured: 85.7% wrong. A branch that has landed twice is a task that
    lands in pieces, and this gate must not touch that population."""
    assert da.classify("t", [100, 120], []).verdict == da.ALLOW


# --------------------------------------------------------------------------- #
# 3. Unmeasurable never refuses
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("prior,opened", [(None, []), ([1], None), (None, None)])
def test_unreadable_history_never_refuses(prior, opened):
    """A gate that blocks dispatch when it cannot see is how a board stops
    moving at 3am."""
    assert da.classify("t", prior, opened).verdict == da.UNMEASURABLE


def test_a_missing_task_id_is_unmeasurable():
    assert da.classify("", [1], []).verdict == da.UNMEASURABLE


def test_an_unreachable_forge_is_unmeasurable_not_a_refusal():
    class _Fail:
        returncode = 1
        stdout = ""

    assert da.assess("t", runner=lambda _a: _Fail()).verdict == da.UNMEASURABLE


def test_forge_garbage_is_unmeasurable():
    class _Junk:
        returncode = 0
        stdout = "not json"

    assert da.assess("t", runner=lambda _a: _Junk()).verdict == da.UNMEASURABLE


# --------------------------------------------------------------------------- #
# 4. History is split AS OF the moment being judged
# --------------------------------------------------------------------------- #
def test_a_pr_merged_after_the_moment_is_not_prior():
    """The survey replays past dispatches, so "already merged" must mean
    already merged THEN — not now. Using today's history would make every
    replayed dispatch look duplicated."""
    prs = [_pr(1, merged=NOW + timedelta(hours=1))]
    prior, opened = da.split_history(prs, NOW)
    assert prior == [] and opened == [1], (
        "a PR that merged later was counted as prior — the replay would "
        "condemn dispatches that were correct at the time"
    )


def test_a_pr_open_at_that_moment_is_counted_open():
    prs = [_pr(1, merged=NOW + timedelta(days=1), created=NOW - timedelta(days=1))]
    _prior, opened = da.split_history(prs, NOW)
    assert opened == [1]


def test_unreadable_history_splits_to_none_not_empty():
    """None means "could not read"; [] means "read, and there were none".
    Conflating them would make the survey report coverage it never had."""
    assert da.split_history(None, NOW) == (None, None)


# --------------------------------------------------------------------------- #
# 5. Mode: advisory by default
# --------------------------------------------------------------------------- #
def test_the_default_mode_is_report(monkeypatch):
    monkeypatch.delenv(da.MODE_ENV, raising=False)
    assert da.mode() == "report"


def test_a_refusal_does_not_block_in_report_mode(monkeypatch):
    monkeypatch.delenv(da.MODE_ENV, raising=False)
    assert da.classify("t", [1], []).blocks is False


def test_a_refusal_blocks_only_when_armed(monkeypatch):
    monkeypatch.setenv(da.MODE_ENV, "enforce")
    assert da.classify("t", [1], []).blocks is True


def test_off_disables_blocking(monkeypatch):
    monkeypatch.setenv(da.MODE_ENV, "off")
    assert da.classify("t", [1], []).blocks is False


def test_an_unknown_mode_falls_back_to_report(monkeypatch):
    """A typo in the env var must not silently arm — or silently disarm."""
    monkeypatch.setenv(da.MODE_ENV, "ENFORCED")   # not a valid mode
    assert da.mode() == "report"


def test_allow_never_blocks_whatever_the_mode(monkeypatch):
    monkeypatch.setenv(da.MODE_ENV, "enforce")
    assert da.classify("t", [], []).blocks is False


# --------------------------------------------------------------------------- #
# 6. The survey measures THIS rule, not a copy of it
# --------------------------------------------------------------------------- #
class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        return None


def test_the_survey_calls_the_same_classify(monkeypatch):
    """A survey with its own copy of the predicate measures a gate that does not
    exist — the defect deps.py names after six sites each grew a copy."""
    seen = []
    real = da.classify

    def _spy(task_id, prior, opened):
        seen.append(task_id)
        return real(task_id, prior, opened)

    monkeypatch.setattr(da, "classify", _spy)
    rows = [{"task_id": "t-1", "recorded_at": NOW.isoformat()}]
    da.survey(conn=_Conn(rows), prs_by_branch={"kanban/t-1": [
        _pr(9, merged=NOW - timedelta(hours=2))]})
    assert seen == ["t-1"], "the survey did not route through classify()"


def test_the_survey_counts_a_later_merge_as_a_wrong_refusal():
    """"Right" is decided by what happened afterwards: another PR for the same
    branch merging means the re-dispatch produced work that landed."""
    rows = [{"task_id": "t-1", "recorded_at": NOW.isoformat()}]
    # PR 10 must be RAISED after the dispatch too. Created beforehand it would
    # have been open AT the dispatch, and the rule would correctly have allowed
    # — which is the carve-out, not a wrong refusal.
    prs = {"kanban/t-1": [
        _pr(9, merged=NOW - timedelta(hours=2), created=NOW - timedelta(days=1)),
        _pr(10, merged=NOW + timedelta(hours=2), created=NOW + timedelta(hours=1)),
    ]}
    report = da.survey(conn=_Conn(rows), prs_by_branch=prs)
    assert report["fires"] == 1 and report["wrong"] == 1 and report["right"] == 0


def test_the_survey_counts_no_later_merge_as_a_right_refusal():
    rows = [{"task_id": "t-1", "recorded_at": NOW.isoformat()}]
    prs = {"kanban/t-1": [_pr(9, merged=NOW - timedelta(hours=2))]}
    report = da.survey(conn=_Conn(rows), prs_by_branch=prs)
    assert report["fires"] == 1 and report["right"] == 1 and report["wrong"] == 0


def test_a_board_with_no_dispatches_is_unmeasurable_not_a_clean_survey():
    """A fresh worktree reads an empty database. Reporting 0 fires there would
    be a clean bill of health nobody measured."""
    report = da.survey(conn=_Conn([]), prs_by_branch={})
    assert report["state"] == "unmeasurable"
    assert report["fires"] is None, "an unmeasured survey reported a fire count"


def test_an_unreadable_transitions_table_is_unmeasurable():
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("no such table")

        def close(self):
            return None

    report = da.survey(conn=_Boom(), prs_by_branch={})
    assert report["state"] == "unmeasurable" and report["fires"] is None
