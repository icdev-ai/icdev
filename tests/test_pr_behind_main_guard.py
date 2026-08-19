# CUI // SP-CTI
"""kpr-stale-02: a green, MERGEABLE PR far behind main must NOT be `ready`.

THE HOLE THESE TESTS PIN. `mergeable` answers one question — does this branch
collide TEXTUALLY with its base — and GitHub answers MERGEABLE for a branch
arbitrarily far behind main so long as nothing does. So the CONFLICTING
interlock only ever caught the colliding subset, and the rest merged CLEANLY
while re-applying their whole diff over a tree that had moved on. #1651 was
-38/+26 on rest_v1.py and 36 commits behind main; every gate said green.

REVERSE DIRECTION IS THE POINT, and it is what `red_first_gate` records: each
test below fails against the pre-change tree, because before it there was no
`behind_main` state and a stale green PR classified `ready`.
"""
from __future__ import annotations

import json

import pytest

from tools.ci import merge_readiness as mr


def _pr(**over):
    """A PR that every OTHER rung of the ladder waves through."""
    base = {
        "number": 1651,
        "url": "https://github.com/o/r/pull/1651",
        "title": "fix(cortex): REST v1 ran the TRUST chain twice",
        "headRefName": "kanban/ctx-trust-02",
        "headRefOid": "3198e978" + "0" * 32,
        "baseRefName": "main",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "labels": [],
        "statusCheckRollup": [{"name": "Test", "conclusion": "SUCCESS"}],
        "reviews": [{"state": "APPROVED"}],
        "state": "OPEN",
    }
    base.update(over)
    return base


# ────────────────────────────────────────────────────────────────────────────
# The reverse-direction test
# ────────────────────────────────────────────────────────────────────────────


def test_green_mergeable_but_behind_main_is_not_ready():
    """THE regression. Green + MERGEABLE + stale must never be `ready`."""
    verdict = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=36, max_behind_commits=10)
    assert verdict.state == mr.BEHIND_MAIN
    assert verdict.ready is False
    # The reason has to carry the number, or a human cannot judge it.
    assert "36" in verdict.reason
    assert "10" in verdict.reason


def test_the_pr_is_otherwise_perfectly_ready():
    """Proves the test above isolates staleness and nothing else.

    Without this, `behind_main` could be passing for the wrong reason — a typo
    in the fixture that trips an earlier rung would look identical.
    """
    verdict = mr.classify_merge_readiness(_pr(), default_branch="main",
                                          behind_by=0)
    assert verdict.state == mr.READY, verdict.reason


@pytest.mark.parametrize("behind", [0, 1, 5, 9, 10])
def test_a_branch_within_the_limit_still_merges(behind):
    """The measured routine population (max 8 behind at merge) is untouched."""
    verdict = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=behind, max_behind_commits=10)
    assert verdict.state == mr.READY
    assert str(behind) in verdict.reason


@pytest.mark.parametrize("behind", [11, 36, 217])
def test_a_branch_past_the_limit_is_refused(behind):
    verdict = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=behind, max_behind_commits=10)
    assert verdict.state == mr.BEHIND_MAIN


# ────────────────────────────────────────────────────────────────────────────
# UNMEASURED is not zero
# ────────────────────────────────────────────────────────────────────────────


def test_unmeasured_staleness_is_fail_open_but_says_so():
    """`None` must not be read as "fresh" silently.

    Fail-open is deliberate — a forge that cannot answer must not freeze the
    pipeline — but a reason that claimed freshness nobody measured is how the
    hole stayed open in the first place.
    """
    verdict = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=None)
    assert verdict.state == mr.READY
    assert "UNMEASURED" in verdict.reason


def test_a_measured_zero_and_an_unmeasured_branch_read_differently():
    measured = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=0)
    unmeasured = mr.classify_merge_readiness(
        _pr(), default_branch="main", behind_by=None)
    assert measured.reason != unmeasured.reason


# ────────────────────────────────────────────────────────────────────────────
# The forge's own verdict — the belt, not the check
# ────────────────────────────────────────────────────────────────────────────


def test_merge_state_status_behind_is_refused_without_any_count():
    """When the base branch has `strict` protection the forge says so itself."""
    verdict = mr.classify_merge_readiness(
        _pr(mergeStateStatus="BEHIND"), default_branch="main", behind_by=None)
    assert verdict.state == mr.BEHIND_MAIN
    assert "BEHIND" in verdict.reason


def test_merge_state_status_clean_does_not_clear_a_stale_branch():
    """The trap: this repo has `strict: false`, so CLEAN means nothing here.

    Measured 2026-08-18 — `required_status_checks.strict` is false on this
    repository, so GitHub reports mergeStateStatus=CLEAN for a branch 217
    commits behind main. A check keyed on the forge verdict alone could never
    fire once, which is the "threshold that makes the check never fire" this
    task was explicitly told not to ship.
    """
    verdict = mr.classify_merge_readiness(
        _pr(mergeStateStatus="CLEAN"), default_branch="main",
        behind_by=217, max_behind_commits=10)
    assert verdict.state == mr.BEHIND_MAIN


# ────────────────────────────────────────────────────────────────────────────
# Ladder placement + purity
# ────────────────────────────────────────────────────────────────────────────


def test_behind_main_is_declared_in_the_vocabulary():
    assert mr.BEHIND_MAIN in mr.MERGE_STATES
    # Immediately before `ready`: it is the last rung, because it is the only
    # one that costs a forge round-trip.
    states = list(mr.MERGE_STATES)
    assert states.index(mr.BEHIND_MAIN) == states.index(mr.READY) - 1


def test_a_cheaper_refusal_still_wins():
    """A red, stale PR reports `ci_failed` — what the merger saw FIRST.

    The module docstring's rule is that the report describes the merger's own
    order. The merger never measures staleness for a PR it already refused.
    """
    verdict = mr.classify_merge_readiness(
        _pr(statusCheckRollup=[{"name": "Test", "conclusion": "FAILURE"}]),
        default_branch="main", behind_by=217)
    assert verdict.state == mr.CI_FAILED


def test_classify_stays_pure_with_the_new_rung():
    pr = _pr()
    before = json.dumps(pr, sort_keys=True)
    mr.classify_merge_readiness(pr, default_branch="main", behind_by=99)
    assert json.dumps(pr, sort_keys=True) == before


def test_the_default_limit_is_not_a_number_that_never_fires():
    """Guards the survey. 120 merged PRs topped out at 8 behind; #1651 was 36.

    A later edit that raises this to "never fires" has to break this test on
    the way past.
    """
    assert 8 < mr.DEFAULT_MAX_BEHIND_COMMITS <= 20
    verdict = mr.classify_merge_readiness(_pr(), default_branch="main",
                                          behind_by=36)
    assert verdict.state == mr.BEHIND_MAIN


# ────────────────────────────────────────────────────────────────────────────
# measure_behind_by — the impure half
# ────────────────────────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_measure_behind_by_parses_the_compare_endpoint():
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc(0, "36\n")

    assert mr.measure_behind_by("main", "abc123", runner=runner) == 36
    assert "compare/main...abc123" in " ".join(seen["cmd"])


@pytest.mark.parametrize("proc", [
    _Proc(1, "", "not found"),        # gh failed
    _Proc(0, ""),                     # empty output
    _Proc(0, "not-a-number"),         # unparseable
])
def test_measure_behind_by_reports_unmeasured_never_zero(proc):
    assert mr.measure_behind_by("main", "abc", runner=lambda *a, **k: proc) is None


def test_measure_behind_by_never_raises():
    def boom(*a, **k):
        raise OSError("gh is not installed")

    assert mr.measure_behind_by("main", "abc", runner=boom) is None


def test_measure_behind_by_needs_both_ends():
    assert mr.measure_behind_by("", "abc") is None
    assert mr.measure_behind_by("main", "") is None


def test_measure_behind_map_compares_against_each_prs_own_base():
    calls = []

    def runner(cmd, **kw):
        calls.append(" ".join(cmd))
        return _Proc(0, "3")

    prs = [_pr(url="u1", baseRefName="main", headRefOid="h1"),
           _pr(url="u2", baseRefName="release/1.0", headRefOid="h2")]
    out = mr.measure_behind_map(prs, default_branch="main", runner=runner)
    assert out == {"u1": 3, "u2": 3}
    assert any("compare/main...h1" in c for c in calls)
    assert any("compare/release/1.0...h2" in c for c in calls)


def test_measure_behind_map_deduplicates_by_base_and_head():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _Proc(0, "2")

    prs = [_pr(url="u1", headRefOid="same"), _pr(url="u2", headRefOid="same")]
    mr.measure_behind_map(prs, default_branch="main", runner=runner)
    assert len(calls) == 1


# ────────────────────────────────────────────────────────────────────────────
# The report
# ────────────────────────────────────────────────────────────────────────────


def test_report_keeps_unmeasured_distinct_from_zero():
    report = mr.build_report(
        [_pr(url="u1"), _pr(url="u2", number=2)],
        default_branch="main", behind_by_url={"u1": 0})
    rows = {r["url"]: r for r in report["prs"]}
    assert rows["u1"]["behind_by"] == 0 and rows["u1"]["behind_measured"] is True
    assert rows["u2"]["behind_by"] is None and rows["u2"]["behind_measured"] is False
    assert report["behind_measured_count"] == 1


def test_report_renders_a_question_mark_not_a_zero_for_unmeasured():
    report = mr.build_report([_pr(url="u1")], default_branch="main")
    table = mr.render_table(report)
    assert "BEHIND" in table
    row = [ln for ln in table.splitlines() if "#1651" in ln][0]
    assert " ? " in row
    assert table.isascii()


def test_report_states_the_threshold_it_used():
    report = mr.build_report([_pr()], default_branch="main",
                             max_behind_commits=10)
    assert report["max_behind_commits"] == 10
    assert "refused above 10" in mr.render_table(report)


def test_report_classifies_a_stale_pr_as_behind_main():
    report = mr.build_report([_pr(url="u1")], default_branch="main",
                             behind_by_url={"u1": 36}, max_behind_commits=10)
    assert report["prs"][0]["state"] == mr.BEHIND_MAIN
    assert report["ready"] == 0


def test_gh_fields_request_what_the_check_reads():
    """A rung that reads a field nobody asked gh for is a rung that never fires."""
    assert "mergeStateStatus" in mr._GH_FIELDS
    assert "headRefOid" in mr._GH_FIELDS
    from tools.ci import pr_watcher as pw
    assert "mergeStateStatus" in pw._GH_JSON_FIELDS
    assert "headRefOid" in pw._GH_JSON_FIELDS


# ────────────────────────────────────────────────────────────────────────────
# The watcher — both doors, and they differ ONLY in the repair
# ────────────────────────────────────────────────────────────────────────────


def _watcher(prs, merged, *, behind, config=None):
    from tools.ci import pr_watcher as pw

    def _list_runner(cmd, **kw):
        return _Proc(0, json.dumps(prs))

    cfg = {"merge_unlinked_prs": True, "auto_merge_enabled": True,
           "refuse_merge_when_behind": True, "max_behind_commits": 10}
    cfg.update(config or {})
    w = pw.PRWatcher(
        config=cfg,
        get_connection=lambda: None,
        pr_list_runner=_list_runner,
        default_branch_resolver=lambda: "main",
        behind_probe=lambda base, head: behind,
    )
    # `**_kw`: kpr-watch-04 hands `_auto_merge` the PR record too, so the
    # shared chokepoint can answer the hold-label question for both callers.
    w._auto_merge = lambda url, **_kw: (merged.append(url) or True)  # type: ignore
    w._audit = lambda action: None  # type: ignore
    return w, pw


def test_unlinked_sweep_refuses_a_stale_pr(monkeypatch):
    merged = []
    w, pw = _watcher([_pr(url="https://github.com/o/r/pull/1651")],
                     merged, behind=36)
    monkeypatch.setattr(pw, "list_pr_tasks", lambda _c: [])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert merged == []


def test_unlinked_sweep_still_merges_a_fresh_pr(monkeypatch):
    merged = []
    w, pw = _watcher([_pr(url="https://github.com/o/r/pull/1651")],
                     merged, behind=3)
    monkeypatch.setattr(pw, "list_pr_tasks", lambda _c: [])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert merged == ["https://github.com/o/r/pull/1651"]


def test_unlinked_sweep_never_rebases(monkeypatch):
    """The sweep has no task and no claim on the branch, so it must not push.

    A stale UNLINKED PR is reported and left for a human — the whole sweep is
    documented as never pushing, un-drafting, rebasing or closing.
    """
    merged, rebased = [], []
    w, pw = _watcher([_pr(url="https://github.com/o/r/pull/1651")],
                     merged, behind=217)
    w._maybe_rebase = lambda task, state: rebased.append(state) or {}  # type: ignore
    monkeypatch.setattr(pw, "list_pr_tasks", lambda _c: [])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert merged == [] and rebased == []


def test_the_sweep_pays_for_the_measurement_only_when_it_would_merge(monkeypatch):
    """Staleness is the one rung that reaches the forge — bound the cost."""
    probed = []
    merged = []
    prs = [
        _pr(url="https://github.com/o/r/pull/1", isDraft=True),
        _pr(url="https://github.com/o/r/pull/2",
            statusCheckRollup=[{"name": "T", "conclusion": "FAILURE"}]),
        _pr(url="https://github.com/o/r/pull/3", mergeable="CONFLICTING"),
        _pr(url="https://github.com/o/r/pull/4"),          # the only ready one
    ]
    w, pw = _watcher(prs, merged, behind=0)
    w._behind_probe = lambda base, head: probed.append(head) or 0  # type: ignore
    monkeypatch.setattr(pw, "list_pr_tasks", lambda _c: [])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert len(probed) == 1, "measured a PR the merger had already refused"


def test_watcher_caches_the_measurement_per_head_sha():
    w, _ = _watcher([], [], behind=4)
    calls = []
    w._behind_probe = lambda base, head: calls.append(head) or 4  # type: ignore
    state = {"baseRefName": "main", "headRefOid": "abc"}
    assert w._behind_by(state) == 4
    assert w._behind_by(state) == 4
    assert len(calls) == 1
    # A rebase changes the head sha, so it is re-measured rather than cached.
    assert w._behind_by({"baseRefName": "main", "headRefOid": "def"}) == 4
    assert len(calls) == 2


def test_watcher_treats_a_headless_state_as_unmeasured():
    w, _ = _watcher([], [], behind=99)
    assert w._behind_by({"baseRefName": "main"}) is None
    assert w._stale_verdict({"baseRefName": "main", "mergeable": "MERGEABLE"}) is None


def test_the_config_switch_actually_disables_the_hold():
    w, _ = _watcher([], [], behind=999,
                    config={"refuse_merge_when_behind": False})
    assert w._stale_verdict(
        {"baseRefName": "main", "headRefOid": "abc"}) is None


def test_stale_verdict_fires_on_the_forge_verdict_without_a_count():
    w, _ = _watcher([], [], behind=None)
    verdict = w._stale_verdict(
        {"baseRefName": "main", "headRefOid": "abc",
         "mergeStateStatus": "BEHIND"})
    assert verdict is not None and "BEHIND" in verdict[1]


def test_hitl_alert_for_a_stale_branch_cannot_flap():
    """A stale PR is green AND MERGEABLE, so every other condition says
    "recovered" — the exact shape that cycled ~180 alert rows a day before.

    `_hitl_recovered` documents that a fourth raise site must be negated there.
    """
    w, _ = _watcher([], [], behind=36)
    state = {"mergeable": "MERGEABLE", "headRefOid": "abc",
             "baseRefName": "main", "createdAt": "2026-01-01T00:00:00Z",
             "statusCheckRollup": [{"name": "T", "conclusion": "SUCCESS"}]}
    assert w._hitl_recovered(state, cycle=0, max_cycles=5) is False
    # ...and a branch that has since been rebased DOES recover.
    w._behind_cache.clear()
    w._behind_probe = lambda base, head: 1  # type: ignore
    assert w._hitl_recovered(state, cycle=0, max_cycles=5) is True


def test_the_watcher_and_the_table_cannot_hold_two_thresholds():
    """One number, read from one place — the same rule as the label list."""
    import yaml

    raw = yaml.safe_load(
        (mr.REPO_ROOT / "args" / "pr_watcher_config.yaml").read_text(
            encoding="utf-8"))
    w, _ = _watcher([], [], behind=0, config={
        "max_behind_commits": raw["max_behind_commits"]})
    assert w._max_behind() == raw["max_behind_commits"]
    assert mr._configured_max_behind() == raw["max_behind_commits"]
