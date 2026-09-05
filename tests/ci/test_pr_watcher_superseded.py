# CUI // SP-CTI
"""pr_watcher CLOSES a superseded PR instead of holding the queue behind it.

The unit tests in ``test_pr_superseded.py`` pin the CLASSIFIER. These pin the
CONSUMER, and they are behavioural on purpose: a test that only asserts the
helper exists goes on passing after the call site is deleted -- the
declared-but-unconsumed defect written into its own test, which this repository
has shipped more than once.

Four properties, and every one of them is a way the feature could be wrong
while looking right:

  * the PR is CLOSED, with a comment carrying the evidence;
  * it is NEVER merged and NEVER un-drafted -- a superseded branch that gets
    un-drafted is one keystroke from a revert landing on main;
  * the audit row is ``pr_watcher.superseded``, so the act is reconstructible
    from the trail rather than from a log line nobody kept;
  * an unreadable forge answer changes NOTHING. ``checked: False`` is not a
    finding, and the PR takes exactly the path it took before.
"""
from __future__ import annotations

import json
from types import SimpleNamespace


import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow

_PR = "https://github.com/o/r/pull/2015"
_TASK = "rmf-oscal-01"
_BRANCH = "kanban/rmf-oscal-01"
_HEAD = "7083ea7464081f2b1c2ba1a1d4a9d0f2e7f0a111"

_MERGED_SIBLING = {
    "number": 2014,
    "url": "https://github.com/o/r/pull/2014",
    "title": "feat(compliance): OSCAL assessment-plan + a CKL/CKLB emitter",
    "body": "",
    "headRefName": _BRANCH,
    "headRefOid": _HEAD,
    "mergedAt": "2026-09-02T22:29:38Z",
    "commits": [{"oid": _HEAD}],
}


def _state(**over):
    state = {
        "state": "OPEN",
        "number": 2015,
        "url": _PR,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "baseRefName": "main",
        "headRefName": _BRANCH,
        "headRefOid": _HEAD,
        # `commits` is in _GH_JSON_FIELDS for exactly this check: the commit
        # list IS the signal, and a sha comparison cannot answer a squash.
        "commits": [{"oid": _HEAD}],
        "reviews": [{"state": "APPROVED", "author": "b"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }
    state.update(over)
    return state


def _build(*, merged_prs, calls, status="pr_opened", state=None,
           config_over=None, landed=None):
    tasks = [_FakeRow(id=_TASK, title="T", description="",
                      status=status, executor_url=_PR)]

    def runner(cmd, **kw):
        calls.append(list(cmd))
        if "--state" in cmd and "merged" in cmd:
            if merged_prs is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="nope")
            return SimpleNamespace(returncode=0, stdout=json.dumps(merged_prs),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    config = {
        "auto_merge_enabled": True,
        "auto_merge_require_approval": False,
        "max_resume_cycles_per_task": 5,
        "sibling_conflict_check": False,
        "landed_check_on_poll": False,
        "link_prs_on_poll": False,
        "superseded_check": True,
        "superseded_close": True,
        "superseded_revert_leg": False,
    }
    config.update(config_over or {})
    w = pw.PRWatcher(
        config=config,
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": _TASK, "result": "pass",
                            "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: state or _state(),
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=runner,
        pr_list_runner=runner,
        gh_runner=runner,
        gh_close_runner=runner,
        default_branch_resolver=lambda: "main",
    )
    w._mark_ready = lambda *a, **kw: calls.append(["MARK_READY"]) or True
    w._hitl_alert = lambda *a, **kw: None
    if landed is not None:
        w._landed_map = lambda tasks_: {_TASK: landed}
    return w


def _audits(w):
    seen = []
    real = w._audit
    w._audit = lambda action: (seen.append(action), real(action))[0]
    return seen


def _cmds(calls):
    return [" ".join(str(x) for x in c) for c in calls]


# ---------------------------------------------------------------------------
def test_a_superseded_pr_is_closed_with_its_evidence():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls)
    report = w.poll_once()

    closes = [c for c in _cmds(calls) if "pr close" in c]
    assert closes, "the superseded PR was never closed: %s" % _cmds(calls)
    body = " ".join(closes)
    assert "#2014" in body
    assert _HEAD[:12] in body
    assert [a for a in report.actions if a.action == "close_superseded"]


def test_a_superseded_pr_is_never_merged_and_never_undrafted():
    """The safety property. Un-drafting is one keystroke from a revert."""
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               state=_state(isDraft=True))
    w.poll_once()
    joined = _cmds(calls)
    assert not [c for c in joined if "pr merge" in c], joined
    assert "MARK_READY" not in [c for c in joined], joined


def test_the_audit_row_is_pr_watcher_superseded():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls)
    seen = _audits(w)
    w.poll_once()
    actions = {a.action for a in seen}
    assert "close_superseded" in actions
    supersede = [a for a in seen if a.action == "close_superseded"][0]
    assert supersede.classification == pw.SUPERSEDED
    assert "2014" in supersede.reason


def test_an_unreadable_merged_listing_changes_nothing():
    """FAIL-OPEN. `checked: False` is not a finding."""
    calls = []
    w = _build(merged_prs=None, calls=calls)
    w.poll_once()
    assert not [c for c in _cmds(calls) if "pr close" in c]


def test_a_pr_with_no_merged_sibling_is_untouched():
    calls = []
    w = _build(merged_prs=[], calls=calls)
    w.poll_once()
    assert not [c for c in _cmds(calls) if "pr close" in c]


def test_report_only_mode_audits_without_closing():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               config_over={"superseded_close": False})
    seen = _audits(w)
    w.poll_once()
    assert not [c for c in _cmds(calls) if "pr close" in c]
    assert [a for a in seen if a.action == "superseded_warn"]


def test_the_check_can_be_switched_off_entirely():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               config_over={"superseded_check": False})
    w.poll_once()
    joined = _cmds(calls)
    assert not [c for c in joined if "pr close" in c]
    assert not [c for c in joined if "--state merged" in c]


def test_dry_run_closes_nothing():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls)
    w.dry_run = True
    w.poll_once()
    assert not [c for c in _cmds(calls) if "pr close" in c]


# ---------------------------------------------------------------------------
# The task, not only the PR: work that is on main must be COMPLETED, never
# re-dispatched.
# ---------------------------------------------------------------------------
def _landed(checked=True, landed=True):
    return {"task_id": _TASK, "checked": checked, "landed": landed,
            "referenced": landed, "confidence": "subject", "commits": [],
            "ref": "origin/main", "reason": ""}


def test_a_landed_task_is_completed_rather_than_redispatched():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               landed=_landed(), config_over={"landed_check_on_poll": True})
    moves = []
    import tools.ci.pr_watcher as _pw
    real = _pw._set_task_status
    _pw._set_task_status = lambda gc, tid, st, reason="": moves.append((tid, st))
    try:
        w.poll_once()
    finally:
        _pw._set_task_status = real
    assert (_TASK, "done") in moves


def test_a_task_whose_work_is_not_on_main_is_left_alone():
    """No landing, no completion. Never fabricate a `done`."""
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               landed=_landed(landed=False),
               config_over={"landed_check_on_poll": True})
    moves = []
    import tools.ci.pr_watcher as _pw
    real = _pw._set_task_status
    _pw._set_task_status = lambda gc, tid, st, reason="": moves.append((tid, st))
    try:
        w.poll_once()
    finally:
        _pw._set_task_status = real
    assert not moves


def test_an_unchecked_landed_report_never_completes_a_task():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls,
               landed=_landed(checked=False, landed=True),
               config_over={"landed_check_on_poll": True})
    moves = []
    import tools.ci.pr_watcher as _pw
    real = _pw._set_task_status
    _pw._set_task_status = lambda gc, tid, st, reason="": moves.append((tid, st))
    try:
        w.poll_once()
    finally:
        _pw._set_task_status = real
    assert not moves


def test_the_merged_listing_is_fetched_once_per_poll_not_once_per_pr():
    calls = []
    w = _build(merged_prs=[_MERGED_SIBLING], calls=calls)
    w.poll_once()
    listings = [c for c in _cmds(calls) if "--state merged" in c]
    assert len(listings) == 1, listings
