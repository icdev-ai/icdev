# CUI // SP-CTI
"""The Awaiting Merge gatherer falls back to REST when GraphQL refuses (kpr-watch-16).

MEASURED 2026-09-09 23:01Z, and again on 2026-09-05: `gh pr list` (GRAPHQL) is
refused with ``API rate limit already exceeded for user ID 263484343`` while REST
answers the same question with rc=0 in the same second -- and ``gh api
rate_limit`` reports ``{"limit":5000,"remaining":5000,"used":0}`` immediately
BEFORE and immediately AFTER the refusal, so the documented triage ("sleep until
reset") waits on a number that never moves.

The Home panel then renders "Could not read merge readiness ... showing the last
good report below". That panel is behaving correctly (kpr-watch-03 keeps a failed
report visible rather than letting it read as a clean board) -- but the report is
STALE, and the transport that could have answered was never asked.

WHAT THESE DEFEND
    * the fallback happens, and ONLY on the rate-limit shape
    * an auth failure still RAISES -- "an empty report and a report that could
      not be produced are different answers, and only the first is data"
    * a REST record is FAITHFUL, not plausible: an empty statusCheckRollup
      classifies as `no_checks`, a REAL finding state, so handing the ladder an
      empty list because we could not ask would FABRICATE that finding
    * the ladder classifies a REST record and its GraphQL twin IDENTICALLY
"""
from __future__ import annotations

import pytest

from tools.ci import merge_readiness as mr

RATE_LIMIT_STDERR = (
    "GraphQL: API rate limit already exceeded for user ID 263484343."
)


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _runner(rc=0, stdout="[]", stderr=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _Proc(rc, stdout, stderr)

    run.calls = calls  # type: ignore[attr-defined]
    return run


# ─────────────────────────────────────────────────── a tiny fake REST surface
def _rest_stub(*, pulls=None, detail=None, checks=None, statuses=None,
               reviews=None, files=None, fail=()):
    """Answers the five endpoints the REST gatherer needs."""
    seen = []

    def rest(path, **kw):
        seen.append(path)
        for frag in fail:
            if frag in path:
                raise RuntimeError("boom: %s" % path)
        if path.endswith("/pulls?state=open&per_page=100") or "pulls?state=open" in path:
            return list(pulls or [])
        if "/check-runs" in path:
            return {"check_runs": list(checks or [])}
        if "/status" in path:
            return {"statuses": list(statuses or [])}
        if path.endswith("/reviews"):
            return list(reviews or [])
        if path.endswith("/files"):
            return list(files or [])
        return dict(detail or {})

    rest.seen = seen  # type: ignore[attr-defined]
    return rest


PULL = {
    "number": 42,
    "html_url": "https://github.com/o/r/pull/42",
    "title": "a change",
    "draft": False,
    "state": "open",
    "updated_at": "2026-09-09T22:00:00Z",
    "head": {"ref": "kanban/x-1", "sha": "deadbeef"},
    "base": {"ref": "main"},
    "labels": [{"name": "ready"}],
}


# ───────────────────────────────────────────────────────── the fallback fires
def test_a_graphql_rate_limit_falls_back_to_rest():
    rest = _rest_stub(pulls=[PULL], detail={"mergeable": True,
                                            "mergeable_state": "clean"})
    got = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest)
    assert got.transport == "rest"
    assert [p["number"] for p in got.prs] == [42]


def test_graphql_success_never_touches_rest():
    """A working GraphQL call must not spend REST budget."""
    rest = _rest_stub(pulls=[PULL])
    got = mr.gather_open_prs(runner=_runner(rc=0, stdout="[]"), rest=rest)
    assert got.transport == "graphql"
    assert rest.seen == [], "REST was called while GraphQL was healthy"


@pytest.mark.parametrize(
    "stderr",
    [
        "gh: Not Found (HTTP 404)",
        "error connecting to api.github.com",
        "You are not logged into any GitHub hosts. Run gh auth login",
        "",
    ],
    ids=["not-found", "network", "unauthenticated", "empty"],
)
def test_a_non_rate_limit_failure_still_raises(stderr):
    """Falling back on EVERY error turns an auth failure into a quiet report."""
    rest = _rest_stub(pulls=[PULL])
    with pytest.raises(RuntimeError):
        mr.gather_open_prs(runner=_runner(rc=1, stderr=stderr), rest=rest)
    assert rest.seen == [], "REST was called for a non-rate-limit failure"


# ──────────────────────────────────────────────── the record must be FAITHFUL
def test_rest_rollup_carries_real_checks_not_an_empty_list():
    """An empty rollup means `no_checks`, which is a FINDING, not an absence."""
    rest = _rest_stub(
        pulls=[PULL],
        detail={"mergeable": True, "mergeable_state": "clean"},
        checks=[{"name": "Test", "status": "completed", "conclusion": "success",
                 "started_at": "2026-09-09T21:00:00Z",
                 "completed_at": "2026-09-09T21:10:00Z"}],
        statuses=[{"context": "Helm Lint", "state": "success",
                   "updated_at": "2026-09-09T21:05:00Z"}],
    )
    pr = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest).prs[0]
    names = {mr._check_name(c) for c in pr["statusCheckRollup"]}
    assert names == {"Test", "Helm Lint"}


def test_check_run_and_status_context_keep_their_distinct_shapes():
    """error_classifier documents both: CheckRun has `conclusion` and no `state`;
    StatusContext has `state` and no `conclusion`. Flattening them to one shape
    would silently change how every check is judged."""
    rest = _rest_stub(
        pulls=[PULL],
        detail={"mergeable": True, "mergeable_state": "clean"},
        checks=[{"name": "Test", "status": "completed", "conclusion": "failure"}],
        statuses=[{"context": "Helm Lint", "state": "success"}],
    )
    rollup = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest).prs[0]["statusCheckRollup"]
    by_name = {mr._check_name(c): c for c in rollup}
    assert by_name["Test"].get("conclusion", "").upper() == "FAILURE"
    assert "state" not in by_name["Test"]
    assert by_name["Helm Lint"].get("state", "").upper() == "SUCCESS"
    assert "conclusion" not in by_name["Helm Lint"]


def test_a_failed_detail_fetch_is_reported_never_an_empty_rollup():
    """If the ask FAILED, the record must not read as 'this PR has no checks'."""
    rest = _rest_stub(pulls=[PULL], detail={"mergeable": True},
                      fail=("/check-runs",))
    got = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest)
    assert got.degraded, "a failed detail fetch was not reported"
    pr = got.prs[0]
    assert "statusCheckRollup" not in pr, (
        "an unaskable rollup was emitted as [], which classifies as no_checks"
    )


def test_mergeable_maps_to_the_ladder_vocabulary():
    for flag, state, expected in (
        (True, "clean", "MERGEABLE"),
        (False, "dirty", "CONFLICTING"),
        (None, "unknown", "UNKNOWN"),
    ):
        rest = _rest_stub(pulls=[PULL],
                          detail={"mergeable": flag, "mergeable_state": state})
        pr = mr.gather_open_prs(
            runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest).prs[0]
        assert pr["mergeable"] == expected, flag


def test_files_are_carried_for_the_protected_path_rung():
    rest = _rest_stub(pulls=[PULL], detail={"mergeable": True},
                      files=[{"filename": "tools/ci/pr_watcher.py"}])
    pr = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest).prs[0]
    assert mr._changed_files(pr) == ["tools/ci/pr_watcher.py"]


# ───────────────────────────────────────────── the ladder is NOT re-implemented
def test_a_rest_record_classifies_exactly_as_its_graphql_twin():
    """The transport changed; the verdict must not."""
    graphql_pr = {
        "url": "https://github.com/o/r/pull/42", "number": 42,
        "title": "a change", "isDraft": False, "state": "OPEN",
        "headRefName": "kanban/x-1", "baseRefName": "main",
        "mergeable": "MERGEABLE", "labels": [{"name": "ready"}],
        "statusCheckRollup": [
            {"name": "Test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "reviews": [], "updatedAt": "2026-09-09T22:00:00Z",
    }
    rest = _rest_stub(
        pulls=[PULL],
        detail={"mergeable": True, "mergeable_state": "clean"},
        checks=[{"name": "Test", "status": "completed", "conclusion": "success"}],
    )
    rest_pr = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest).prs[0]

    a = mr.classify_merge_readiness(graphql_pr, default_branch="main",
                                    linked_urls=(), behind_by=0)
    b = mr.classify_merge_readiness(rest_pr, default_branch="main",
                                    linked_urls=(), behind_by=0)
    assert a.state == b.state, (a, b)


# ──────────────────────────────────────────────────────────────── the bound
def test_the_detail_bound_is_reported_and_never_silent():
    pulls = [dict(PULL, number=n, head={"ref": "b%d" % n, "sha": "s%d" % n})
             for n in range(1, 6)]
    got = mr.gather_open_prs(runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR),
                             rest=_rest_stub(pulls=pulls,
                                             detail={"mergeable": True}),
                             max_detail=2)
    assert len(got.prs) == 5, "a bound on DETAIL must not drop PRs"
    assert len(got.detail_deferred) == 3
    deferred = set(got.detail_deferred)
    for pr in got.prs:
        if pr["number"] in deferred:
            assert "statusCheckRollup" not in pr


# ─────────────────────────── a PARTIAL rollup is worse than an absent one
@pytest.mark.parametrize(
    "broken", ["/check-runs", "/status"], ids=["check-runs-down", "status-down"]
)
def test_a_partial_rollup_is_never_emitted(broken):
    """The half that answered can be GREEN while the failure sat in the other.

    Emitting what we could read would let the ladder report `ready` for a red
    PR -- a strictly worse fabrication than the `no_checks` case, because it
    points the wrong way. So the rollup is asserted only when BOTH the
    check-runs and the combined-status endpoints answered.
    """
    rest = _rest_stub(
        pulls=[PULL],
        detail={"mergeable": True, "mergeable_state": "clean"},
        checks=[{"name": "Test", "status": "completed", "conclusion": "success"}],
        statuses=[{"context": "Helm Lint", "state": "success"}],
        fail=(broken,),
    )
    got = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest)
    assert "statusCheckRollup" not in got.prs[0]
    assert any(broken.strip("/") in note for note in got.degraded), got.degraded


def test_both_sources_answering_emits_the_union():
    """The control: with both endpoints healthy the rollup IS emitted."""
    rest = _rest_stub(
        pulls=[PULL],
        detail={"mergeable": True, "mergeable_state": "clean"},
        checks=[{"name": "Test", "status": "completed", "conclusion": "success"}],
        statuses=[{"context": "Helm Lint", "state": "success"}],
    )
    got = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR), rest=rest)
    assert {mr._check_name(c) for c in got.prs[0]["statusCheckRollup"]} == {
        "Test", "Helm Lint"}
    assert got.degraded == []


def test_no_sha_means_the_rollup_was_never_askable():
    pull = dict(PULL, head={"ref": "kanban/x-1"})   # no sha
    got = mr.gather_open_prs(
        runner=_runner(rc=1, stderr=RATE_LIMIT_STDERR),
        rest=_rest_stub(pulls=[pull], detail={"mergeable": True}))
    assert "statusCheckRollup" not in got.prs[0]
