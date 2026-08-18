# CUI // SP-CTI
"""The survey that decides whether the pre-dispatch landed check may refuse.

Two things are pinned here. The first is a correctness bug the survey exposed:
a merge commit naming the task id was read as evidence the task LANDED, even
when the id named the merge TARGET — ``Merge ... 'origin/main' into
kanban/<id>`` is main going into the branch, and sits on main at all only
because the branch merged later. Reading it as a landing dates the delivery to
the branch's sync instead.

The second is the survey's own arithmetic, because it is the artifact the
posture decision rests on. It reports a WRONG bucket — fires that would have
withheld work the repo went on to take — and if that bucket silently read zero,
the gate would look safe to arm on the strength of a bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.kanban import landed_check as lc
from tools.kanban import landed_dispatch_survey as lds

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _c(offset_h, sha, subject):
    return (T0 + timedelta(hours=offset_h), sha, subject)


# ── the merge-direction bug ────────────────────────────────────────────────
def test_a_merge_into_the_task_branch_is_not_a_landing():
    """main -> branch. Nothing landed; the commit is reachable from main only
    because the branch merged afterwards."""
    assert lc._classify(
        "ground-sol-01", "Merge remote-tracking branch 'origin/main' into "
        "kanban/ground-sol-01", "") is None


def test_a_branch_to_branch_merge_naming_the_id_twice_is_not_a_landing():
    """The id on BOTH sides still means something was merged INTO the task's
    branch — the direction is what matters, not how often the id appears."""
    assert lc._classify(
        "prem-ricoas-02", "Merge remote-tracking branch "
        "'origin/kanban/prem-ricoas-02' into kanban/prem-ricoas-02", "") is None


def test_a_pull_request_merge_is_still_a_landing():
    """The tier must keep working for the shape it was written for: the id names
    the SOURCE, so the branch went into the default branch."""
    assert lc._classify(
        "dvg-core-02", "Merge pull request #747 from icdev-ai/feat/dvg-core-02",
        "") == lc.CONFIDENCE_MERGE_REF


def test_an_ordinary_subject_is_unaffected():
    assert lc._classify("kpr-fix-02", "fix(kanban): one rule (kpr-fix-02) (#1804)",
                        "") == lc.CONFIDENCE_SUBJECT


def test_a_body_only_mention_is_still_not_a_landing():
    """A body mention is a citation as often as a delivery; the tier exists to
    report, never to block."""
    assert lc._classify("kpr-fix-02", "chore: unrelated", "see kpr-fix-02") == \
        lc.CONFIDENCE_BODY


def test_merge_target_reads_only_the_destination():
    assert lds is not None  # module imports
    assert lc._merge_target("Merge branch 'a' into b") == "b"
    assert lc._merge_target("Merge pull request #1 from org/feat/x") == ""
    assert lc._merge_target("feat: not a merge at all into anything") == ""


# ── the survey's arithmetic ────────────────────────────────────────────────
class _Conn:
    """Minimal stand-in: the survey issues exactly one SELECT."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        return self

    def fetchall(self):
        return self._rows


def _run(monkeypatch, commits, dispatches):
    monkeypatch.setattr(lds, "_git_log", lambda *a, **k: commits)
    rows = [{"task_id": t, "recorded_at": w.isoformat()} for t, w in dispatches]
    return lds.survey(_Conn(rows), ref="origin/main")


def test_a_rebuild_of_delivered_work_is_a_CORRECT_refusal(monkeypatch):
    r = _run(monkeypatch,
             [_c(0, "aaa", "fix: done (t-01) (#1)")],
             [("t-01", T0 + timedelta(hours=5))])
    assert r["measurable"] and r["fires"] == 1
    assert r["correct"] == 1 and r["wrong"] == 0


def test_a_dispatch_that_lands_more_work_is_a_WRONG_refusal(monkeypatch):
    """The bucket the arming decision turns on. A task legitimately spans
    several commits, and refusing its second one withholds real work."""
    r = _run(monkeypatch,
             [_c(0, "aaa", "feat: part one (t-01) (#1)"),
              _c(9, "bbb", "feat: part two (t-01) (#2)")],
             [("t-01", T0 + timedelta(hours=5))])
    assert r["fires"] == 1
    assert r["wrong"] == 1 and r["correct"] == 0
    assert r["wrong_examples"][0]["later_sha"] == "bbb"


def test_a_dispatch_before_anything_landed_does_not_fire(monkeypatch):
    r = _run(monkeypatch,
             [_c(9, "aaa", "feat: t-01 lands (t-01) (#1)")],
             [("t-01", T0 + timedelta(hours=5))])
    assert r["fires"] == 0


def test_the_wrong_share_is_reported_against_both_denominators(monkeypatch):
    """Fires and dispatches are different denominators and the survey must not
    quietly pick the flattering one — 29% of fires and 2.67% of dispatches are
    the same finding, and only one of them sounds acceptable."""
    r = _run(monkeypatch,
             [_c(0, "a", "feat: (t-01) (#1)"), _c(9, "b", "feat: (t-01) (#2)"),
              _c(0, "c", "feat: (t-02) (#3)")],
             [("t-01", T0 + timedelta(hours=5)), ("t-02", T0 + timedelta(hours=5)),
              ("t-03", T0 + timedelta(hours=5))])
    assert r["dispatches"] == 3 and r["fires"] == 2
    assert r["wrong"] == 1
    assert r["wrong_pct"] == 33.33            # of all dispatches
    assert r["wrong_share_of_fires_pct"] == 50.0


def test_a_body_mention_never_counts_as_a_landing(monkeypatch):
    """The survey must measure the tiers that BLOCK. Counting body mentions
    would inflate the fire rate with commits that merely cite the task."""
    r = _run(monkeypatch,
             [_c(0, "aaa", "chore: unrelated subject")],
             [("t-01", T0 + timedelta(hours=5))])
    assert r["fires"] == 0


def test_a_merge_into_the_branch_does_not_register_as_a_landing(monkeypatch):
    """End to end: the classifier fix must reach the survey, or the survey keeps
    reporting fires the gate would no longer produce."""
    r = _run(monkeypatch,
             [_c(0, "aaa", "Merge branch 'origin/main' into kanban/t-01")],
             [("t-01", T0 + timedelta(hours=5))])
    assert r["fires"] == 0


# ── a database with no history must not read as "safe to arm" ──────────────
def test_no_dispatch_history_is_UNMEASURABLE_not_a_clean_zero(monkeypatch):
    monkeypatch.setattr(lds, "_git_log", lambda *a, **k: [_c(0, "a", "x (t-01)")])
    r = lds.survey(_Conn([]), ref="origin/main")
    assert r["measurable"] is False
    assert "fire_rate_pct" not in r, (
        "a fresh worktree reporting 0% would read as proof the gate never fires")


def test_an_unreachable_ref_is_UNMEASURABLE(monkeypatch):
    monkeypatch.setattr(lds, "_git_log", lambda *a, **k: [])
    rows = [{"task_id": "t-01", "recorded_at": T0.isoformat()}]
    assert lds.survey(_Conn(rows), ref="origin/nope")["measurable"] is False


# ── the fast path must be the same predicate as the gate ───────────────────
def test_tokenised_matching_agrees_with_the_gates_regex():
    """`landings_for` tokenises subjects instead of running one regex per id.
    That is only sound because the split class is the boundary class — assert
    it, including the near-miss the boundary exists to reject."""
    import re

    subject = "fix(ctx): thing (ctx-perf-02) (#9)"
    for tid, expected in (("ctx-perf-02", True), ("ctx-perf-021", False),
                          ("ctx-perf", False)):
        by_regex = bool(re.search(lc._grep_pattern(tid), subject))
        by_tokens = tid in set(lds._WORD_SPLIT.split(subject))
        assert by_regex is expected, tid
        assert by_tokens is expected, tid


def test_an_id_the_fast_path_cannot_tokenise_still_matches():
    """A dotted id would be split in half by the tokeniser, so it must fall back
    to the real pattern rather than silently never matching."""
    commits = [_c(0, "aaa", "fix: thing (v1.2-task) (#1)")]
    found = lds.landings_for({"v1.2-task"}, commits)
    assert list(found) == ["v1.2-task"]
