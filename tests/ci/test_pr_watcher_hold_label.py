# CUI // SP-CTI
"""kpr-watch-04: a hold label must mean the same thing on BOTH merge paths.

THE ASYMMETRY. ``_NO_AUTOMERGE_LABELS`` (hold / do-not-merge / do not merge /
wip / no-automerge / blocked) was referenced at exactly ONE site — inside
``_sweep_unlinked_prs``, whose own comment said so. The task-linked path never
saw a label and could not have: ``_GH_JSON_FIELDS`` did not request ``labels``,
so for a ``kanban/<task-id>`` PR the label was not even fetched.

So the documented escape hatch did not cover the door that does most of the
merging, and it failed in the dangerous direction: a human labelling a kanban PR
``do-not-merge`` got no warning and no effect, and would reasonably believe the
PR was held. The remaining brakes on that path are the draft (which
``_mark_ready`` clears itself once a dependency is satisfied), an unsatisfied
dependency, a manual gate row, and a reviewer requesting changes — so releasing
a MANUAL-ONLY card's gate removes every one of them at once.

THE REVERSE-DIRECTION TEST IS STATED FIRST, and it is behavioural on purpose.
``not merge_calls`` is the assertion that matters: a check that runs after
``_auto_merge`` is not a check, it is a commit on main. Against the pre-change
tree ``merge_readiness.hold_labels`` does not exist and the linked path merges a
``do-not-merge`` PR happily, so this whole file is the recorded RED.

THE AUTO-READY DECISION (asked for explicitly by the card): a hold label
suppresses un-drafting too. The reasoning is written next to the code in
``PRWatcher._mark_ready``; ``test_a_hold_label_also_suppresses_auto_ready``
pins it, and ``test_a_human_can_still_un_draft_by_hand`` pins the limit of it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.ci.merge_readiness as mr
import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow

_PR = "https://github.com/o/r/pull/904"
_TASK = "task-held"


def _state(*, labels=(), draft=False):
    """A green, approved, MERGEABLE, on-main PR — every other rung satisfied,
    so the label is the only thing left that can decide the outcome."""
    return {
        "state": "OPEN",
        "url": _PR,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "baseRefName": "main",
        "isDraft": draft,
        "labels": [{"name": n} for n in labels],
        "reviews": [{"state": "APPROVED", "author": "b"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }


def _build_watcher(state, *, merge_calls, ready_calls=None, config=None):
    tasks = [_FakeRow(id=_TASK, title="T", description="",
                      status="in_progress", executor_url=_PR)]

    def fake_runner(cmd, **kw):
        (ready_calls if "ready" in cmd else merge_calls).append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_list(cmd, **kw):
        payload = json.dumps([{"url": _PR, "files": [{"path": "tools/a/one.py"}]}])
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    cfg = {
        "auto_merge_enabled": True,
        "auto_merge_require_approval": False,
        "max_resume_cycles_per_task": 5,
        # Out of the way: this file is about the label, and a lone PR has no
        # siblings and nothing to be behind.
        "sibling_conflict_check": False,
        "landed_check_on_poll": False,
        "refuse_merge_when_behind": False,
    }
    cfg.update(config or {})
    return pw.PRWatcher(
        config=cfg,
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": _TASK, "result": "pass", "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: state,
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=fake_runner,
        pr_list_runner=fake_list,
        default_branch_resolver=lambda: "main",
    )


def _audits(w):
    seen = []
    w._audit = lambda action: seen.append(action)
    return seen


# ── the reverse direction ───────────────────────────────────────────────────
@pytest.mark.parametrize("label", sorted(mr.NO_AUTOMERGE_LABELS))
def test_a_task_linked_pr_carrying_a_hold_label_is_not_merged(label):
    """THE ONE THE CARD ASKED FOR. Every label in the list, on the path that
    never honoured any of them."""
    merge_calls = []
    w = _build_watcher(_state(labels=[label]), merge_calls=merge_calls)
    _audits(w)
    report = w.poll_once()

    assert not merge_calls, (
        "a task-linked PR labelled %r must not be merged — the label is the "
        "documented escape hatch and this is the path that does most of the "
        "merging" % label
    )
    action = report.actions[-1]
    assert action.action == "wait"
    assert label in action.reason


def test_the_refusal_is_reported_as_held_label_not_a_silent_skip():
    """'Silent skip' was half the complaint. The state comes from the shared
    classifier's vocabulary so the report and the merger cannot drift."""
    w = _build_watcher(_state(labels=["do-not-merge"]), merge_calls=[])
    seen = _audits(w)
    report = w.poll_once()

    assert report.actions[-1].classification == mr.HELD_LABEL == "held_label"
    holds = [a for a in seen if a.action == "held_label_hold"]
    assert holds, "the refusal must leave an audit row"
    assert holds[0].task_id == _TASK
    assert "do-not-merge" in holds[0].reason


def test_an_unlabelled_pr_still_merges():
    """Strictly ADDITIVE blocking: nothing that merged before may stop."""
    merge_calls = []
    w = _build_watcher(_state(), merge_calls=merge_calls)
    _audits(w)
    report = w.poll_once()

    assert merge_calls, "an unlabelled green PR must still merge"
    assert report.actions[-1].action == "merge"


def test_an_unrelated_label_does_not_block():
    merge_calls = []
    w = _build_watcher(_state(labels=["enhancement", "needs-docs"]),
                       merge_calls=merge_calls)
    _audits(w)
    w.poll_once()
    assert merge_calls, "only the declared hold labels may block"


# ── the auto-READY decision ─────────────────────────────────────────────────
def test_a_hold_label_also_suppresses_auto_ready():
    """DECIDED: yes. Un-drafting here is not a human clicking a button, it is a
    step of the watcher's own merge sequence — and the call site's own rule is
    that it must never happen for a PR that was not about to merge anyway. A PR
    labelled `wip` is by definition not about to merge, so un-drafting it
    destroys state and enables nothing."""
    merge_calls, ready_calls = [], []
    w = _build_watcher(_state(labels=["wip"], draft=True),
                       merge_calls=merge_calls, ready_calls=ready_calls)
    _audits(w)
    w.poll_once()

    assert not ready_calls, (
        "un-drafting a PR a human labelled `wip` overrides an explicit human "
        "signal with an automated one"
    )
    assert not merge_calls


def test_a_draft_without_a_hold_label_is_still_un_drafted():
    """The other direction of the same decision. Auto-ready is the fix for the
    jam where finished work waited on a human to click a button; a label the
    human did not apply must not reintroduce it."""
    ready_calls = []
    w = _build_watcher(_state(draft=True), merge_calls=[], ready_calls=ready_calls)
    _audits(w)
    w.poll_once()
    assert ready_calls, "auto-ready must still run for an unlabelled draft"


def test_a_human_can_still_un_draft_by_hand():
    """THE LIMIT OF THE DECISION, and why the two controls stay independent
    where it matters: this suppresses `gh pr ready` issued by the WATCHER. A PR
    a person has already taken out of draft is still refused at the merge, so
    the label never depends on the draft to do its job."""
    merge_calls = []
    w = _build_watcher(_state(labels=["hold"], draft=False),
                       merge_calls=merge_calls)
    _audits(w)
    w.poll_once()
    assert not merge_calls


# ── the chokepoints ─────────────────────────────────────────────────────────
def test_auto_merge_refuses_when_handed_a_held_record():
    """Both merge paths call `_auto_merge`, so a future caller cannot route
    around the label the way the task-linked path routed around it for its
    whole existence."""
    w = _build_watcher(_state(), merge_calls=[])
    assert w._auto_merge(_PR, state=_state(labels=["blocked"])) is False


def test_auto_merge_without_a_record_is_unchanged():
    """`state` is optional: the unlinked sweep asked the classifier first and
    every existing caller must keep working."""
    merge_calls = []
    w = _build_watcher(_state(), merge_calls=merge_calls)
    assert w._auto_merge(_PR) is True
    assert merge_calls


def test_the_refusal_logs_as_well_as_audits(caplog):
    """'A refusal must leave a trace' — the principle
    tests/ci/test_pr_watcher_stale_conflict_recovery.py exists to defend, after
    eleven PRs went unmerged for a day with no evidence a merge was attempted.
    Name the label, or 'refused' without the reason is the same silence."""
    w = _build_watcher(_state(), merge_calls=[])
    _audits(w)
    pw.logger.propagate = True
    with caplog.at_level("WARNING", logger=pw.logger.name):
        assert w._auto_merge(_PR, state=_state(labels=["no-automerge"])) is False
    assert any("no-automerge" in r.getMessage() for r in caplog.records)


def test_mark_ready_refuses_ahead_of_dry_run():
    """Placement mirrors the protected-path guard: `dry_run` returns True at the
    top of `_mark_ready`, so a guard behind it would let a dry run report a
    transition the real run refuses."""
    w = _build_watcher(_state(), merge_calls=[])
    w.dry_run = True
    assert w._mark_ready(_PR, _TASK, w._connection(),
                         state=_state(labels=["hold"])) is False


# ── the fetch, and the shared extraction ────────────────────────────────────
def test_labels_are_actually_fetched_for_a_task_linked_pr():
    """THE ROOT CAUSE. Honouring a label the request never asks for is
    impossible, and nothing would have said so — `state.get("labels")` on a
    record without the field is simply empty."""
    assert "labels" in pw._GH_JSON_FIELDS.split(",")


def test_both_paths_read_one_list_through_one_function():
    """A shared LIST was not enough — it was already shared and still meant two
    different things. The membership test itself has to be the shared thing."""
    assert pw._NO_AUTOMERGE_LABELS is mr.NO_AUTOMERGE_LABELS
    assert pw.hold_labels is mr.hold_labels


@pytest.mark.parametrize("labels,expected", [
    ([{"name": "Do-Not-Merge"}], ["do-not-merge"]),
    ([{"name": "  hold  "}, {"name": "wip"}], ["hold", "wip"]),
    ([{"name": "enhancement"}], []),
    ([], []),
    (None, []),
    (["hold"], ["hold"]),                      # bare-string shape
    ([{"name": None}, {"name": ""}], []),
])
def test_hold_labels_normalises(labels, expected):
    assert mr.hold_labels({"labels": labels}) == expected


def test_the_classifier_still_reports_held_label_for_an_unlinked_pr():
    """The unlinked sweep's behaviour must be byte-for-byte what it was."""
    v = mr.classify_merge_readiness(
        {"url": _PR, "isDraft": False, "baseRefName": "main",
         "mergeable": "MERGEABLE", "labels": [{"name": "hold"}],
         "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "t"}]},
        default_branch="main")
    assert v.state == mr.HELD_LABEL
    assert v.reason == "carries hold label(s): hold"


def test_the_guard_runs_before_the_un_draft():
    """ORDERING IS THE SAFETY PROPERTY, the same one kpr-watch-05 pinned for the
    protected-path guard: the EARLY un-draft at the top of the auto-merge branch
    fires long before the merge does, and un-drafting is visible and hard to
    walk back."""
    import inspect

    src = inspect.getsource(pw)
    guard = src.index("held = self._refuse_held_label(pr_url, state, task[")
    undraft = src.index('if state.get("isDraft"):\n                        '
                        'self._mark_ready(')
    assert guard < undraft, (
        "the hold-label refusal must precede the un-draft on the linked path")
