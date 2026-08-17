# CUI // SP-CTI
"""pr_watcher must not merge a PR whose work is ALREADY on the default branch.

The board tracks task -> PR. Nothing checked task -> MAIN, and those are
different questions: a PR whose work merged under a different number stays
green and MERGEABLE, and merging it re-applies a diff against a branch that has
moved on. #1651 was -38/+26 on rest_v1.py — merging it would have DELETED 38
lines main currently has, with every gate on the board reporting green.

`tools.kanban.landed_check` already answered this at SEED time (task_factory)
and DISPATCH time (reflexes/kanban.py). These tests pin the third moment.

The assertions here are BEHAVIOURAL on purpose. Twice in this file's history a
test asserted only that a helper EXISTED and went on passing after the call
site was deleted — the declared-but-unconsumed defect written into its own
test. `not merge_calls` under enforce is what proves the check runs BEFORE
_auto_merge; after it, the damage is a commit on main.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow, _green_pr_state

_PR = "https://github.com/o/r/pull/900"
_TASK = "task-landed"


def _report(*, checked=True, landed=True, referenced=False, confidence="subject"):
    return {
        "task_id": _TASK,
        "ref": "origin/main",
        "checked": checked,
        "reason": "" if checked else "git unavailable",
        "landed": landed,
        "referenced": referenced or landed,
        "confidence": confidence,
        "commits": [{"sha": "abc1234", "subject": "feat: the work (#task-landed)",
                     "evidence": "subject"}] if confidence else [],
    }


def _build_watcher(*, merge_calls, hitl_calls=None, enabled=True):
    tasks = [_FakeRow(id=_TASK, title="T", description="",
                      status="in_progress", executor_url=_PR)]

    def fake_merge(cmd, **kw):
        merge_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_list(cmd, **kw):
        payload = json.dumps([{"url": _PR, "files": [{"path": "tools/a/one.py"}]}])
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    w = pw.PRWatcher(
        config={
            "auto_merge_enabled": True,
            "auto_merge_require_approval": False,
            "max_resume_cycles_per_task": 5,
            # Keep the sibling guard out of the way — this file is about the
            # landed guard, and a lone PR has no siblings anyway.
            "sibling_conflict_check": False,
            "landed_check_on_poll": enabled,
        },
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": _TASK, "result": "pass", "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: _green_pr_state("main"),
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=fake_merge,
        pr_list_runner=fake_list,
        default_branch_resolver=lambda: "main",
    )
    if hitl_calls is not None:
        w._hitl_alert = lambda tid, url, reason: hitl_calls.append((tid, reason))
    return w


def _audits(w):
    """Capture WatcherActions handed to _audit (the warn path never reaches
    report.actions, so asserting on the report alone would miss it)."""
    seen = []
    w._audit = lambda action: seen.append(action)
    return seen


@pytest.fixture
def landed_stub(monkeypatch):
    """Patch the real check. pr_watcher does `from tools.kanban import
    landed_check` INSIDE the method, so the module object is what must be
    patched — patching a differently-spelled alias would silently miss."""
    import tools.kanban.landed_check as lc

    def _install(report, mode="warn"):
        monkeypatch.setattr(lc, "check_landed_bulk",
                            lambda ids, **kw: {_TASK: report})
        monkeypatch.setattr(lc, "mode", lambda: mode)
        return lc

    return _install


# ---------------------------------------------------------------------------
# _landed_map — the batch lookup
# ---------------------------------------------------------------------------
def test_landed_map_batches_every_task_id(landed_stub, monkeypatch):
    """One git call answers for the whole batch, not one call per PR."""
    import tools.kanban.landed_check as lc
    seen = {}

    def fake_bulk(ids, **kw):
        seen["ids"] = list(ids)
        return {i: _report(landed=False, referenced=False, confidence=None) for i in ids}

    monkeypatch.setattr(lc, "check_landed_bulk", fake_bulk)
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    w._landed_map([{"id": "a-1"}, {"id": "b-2"}, {"id": "c-3"}])
    assert seen["ids"] == ["a-1", "b-2", "c-3"]


def test_landed_map_is_fail_open(monkeypatch):
    """An unreachable git returns {} — it must never wedge merging."""
    import tools.kanban.landed_check as lc

    def boom(ids, **kw):
        raise RuntimeError("no git")

    monkeypatch.setattr(lc, "check_landed_bulk", boom)
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    assert w._landed_map([{"id": "a-1"}]) == {}


def test_landed_map_skips_empty_task_list():
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    assert w._landed_map([]) == {}


# ---------------------------------------------------------------------------
# poll_once — the decision
# ---------------------------------------------------------------------------
def test_already_landed_warns_but_merges_by_default(landed_stub):
    """Advisory posture: audited, but the merge still proceeds."""
    landed_stub(_report(), mode="warn")
    merge_calls = []
    w = _build_watcher(merge_calls=merge_calls)
    seen = _audits(w)
    report = w.poll_once()

    assert report.actions[-1].action == "merge"
    assert merge_calls, "warn mode must not block the merge"
    warns = [a for a in seen if a.action == "already_landed_warn"]
    assert warns, "the finding must be audited even when it does not block"
    assert _TASK in warns[0].reason


def test_already_landed_holds_under_enforce(landed_stub):
    """`not merge_calls` is the real assertion: the check ran BEFORE the merge."""
    landed_stub(_report(), mode="enforce")
    merge_calls, hitl = [], []
    w = _build_watcher(merge_calls=merge_calls, hitl_calls=hitl)
    _audits(w)
    report = w.poll_once()

    action = report.actions[-1]
    assert action.action == "wait"
    assert "already on the default branch" in action.reason
    assert not merge_calls, (
        "enforce must hold the merge — a check that runs after _auto_merge is "
        "not a check, it is a commit on main"
    )
    assert hitl and _TASK == hitl[0][0], "a held PR must reach a human"


def test_unchecked_report_is_never_a_finding(landed_stub):
    """checked:False means the check could not run — not that it found nothing.

    Fail-open is the whole safety story: an unreachable git must merge as
    normal rather than hold every PR on the board.

    `landed=True` here is deliberate and is what makes this test DISCRIMINATE.
    `_empty_report` always pairs checked:False with landed:False, so a report
    with both false would pass just as happily against a call site that never
    consulted `checked` at all — the guard would be untested. Pinning the
    contradictory combination is the only way to prove `checked` is read.
    """
    landed_stub(_report(checked=False, landed=True, confidence="subject"), mode="enforce")
    merge_calls = []
    w = _build_watcher(merge_calls=merge_calls)
    seen = _audits(w)
    report = w.poll_once()

    assert report.actions[-1].action == "merge"
    assert merge_calls
    assert not [a for a in seen if a.action.startswith("already_landed")]


def test_body_only_reference_never_blocks(landed_stub):
    """A body mention is a citation at least as often as a landing.

    landed_check reports that as referenced=True / landed=False, and this call
    site must respect the distinction rather than re-deriving it.
    """
    landed_stub(_report(landed=False, referenced=True, confidence="body"),
                mode="enforce")
    merge_calls = []
    w = _build_watcher(merge_calls=merge_calls)
    seen = _audits(w)
    report = w.poll_once()

    assert report.actions[-1].action == "merge"
    assert merge_calls
    assert not [a for a in seen if a.action.startswith("already_landed")]


def test_check_can_be_disabled_by_config(landed_stub, monkeypatch):
    """landed_check_on_poll: false skips the batch lookup entirely."""
    import tools.kanban.landed_check as lc
    called = []
    monkeypatch.setattr(lc, "check_landed_bulk",
                        lambda ids, **kw: called.append(ids) or {})
    monkeypatch.setattr(lc, "mode", lambda: "enforce")

    merge_calls = []
    w = _build_watcher(merge_calls=merge_calls, enabled=False)
    _audits(w)
    report = w.poll_once()

    assert not called, "the batch lookup must not run when disabled"
    assert report.actions[-1].action == "merge"
