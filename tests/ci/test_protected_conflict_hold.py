# CUI // SP-CTI
"""mfx-mrg-03: the protected-path refusal was on an arm a conflicting PR never
entered.

`_refuse_protected` was called in ONE place on the task-linked path — inside the
MERGEABLE arm, immediately before the un-draft and `_auto_merge` — while
`_maybe_rebase` and the resume ladder live in the `MERGE_CONFLICT` arm. Not
"later in one ladder": a DIFFERENT BRANCH of it. A PR that is conflicting from
the moment it opens can therefore never reach the rung that would refuse it, and
the rung only fires once the PR is mergeable — precisely when it is no longer
needed to prevent wasted work.

Measured on #2064 (mfx-mrg-01), which changed `tools/ci/pr_watcher.py`, the
FIRST entry in `protected_paths`: 63 `rebase_failed`, 5 `resume` and an
`escalate`, and **0 of its 165 `pr_watcher.*` audit rows mention `protected`**.

WHAT IS SUPPRESSED AND WHAT IS NOT, because the survey decided it rather than
taste. The refusal is asked and audited before any `_maybe_rebase` call, and it
suppresses the RESUME ladder and the escalation. It does NOT suppress the
bounded rebase: replaying all 210 recorded conflict-ladder episodes
(`tools/ci/protected_conflict_survey.py`), holding ahead of `_maybe_rebase`
would have taken a SUCCESSFUL rebase away from 11 of the 32 episodes it catches
— 8 of them a single pushed rebase and nothing else before the PR merged, i.e.
3.81% of the population, above the 1.63% CLAUDE.md already calls refusing
routine work. `test_the_bounded_rebase_is_still_attempted` is that finding, in
the form that fails if somebody later "tidies" the hold up one rung.

Against the pre-change tree there is no `_protected_hits_seen` and no hold, so a
conflicting protected PR spends a resume — which is what every behavioural
assertion here reads, and what makes this file the recorded RED.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest
import yaml

import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow

_PR = "https://github.com/o/r/pull/2064"
_TASK = "mfx-mrg-01"
_GUARDED = ["tools/ci/pr_watcher.py", "args/pr_watcher_config.yaml"]
_PROTECTED_FILE = "tools/ci/pr_watcher.py"
_INNOCENT_FILE = "docs/notes.md"


def _state(mergeable="CONFLICTING", *, draft=False):
    return {
        "state": "OPEN",
        "url": _PR,
        "mergeable": mergeable,
        "mergeStateStatus": "DIRTY" if mergeable == "CONFLICTING" else "CLEAN",
        "baseRefName": "main",
        "headRefName": "kanban/%s" % _TASK,
        "headRefOid": "a" * 40,
        "isDraft": draft,
        "labels": [],
        "reviews": [{"state": "APPROVED", "author": "b"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }


def _build(state, *, files=(_PROTECTED_FILE,), listing=True, config=None):
    """A watcher with the forge stubbed and every rung but the one under test
    taken out of the way."""
    tasks = [_FakeRow(id=_TASK, title="T", description="",
                      status="merge_conflict", executor_url=_PR)]

    def fake_list(cmd, **kw):
        payload = ("[]" if not listing else json.dumps(
            [{"url": _PR, "files": [{"path": p} for p in files],
              "mergeable": state.get("mergeable"), "isDraft": False}]))
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    cfg = {
        "auto_merge_enabled": True,
        "auto_merge_require_approval": False,
        "max_resume_cycles_per_task": 5,
        "protected_paths": list(_GUARDED),
        # The index the refusal reads is the sibling listing the poll already
        # fetches, so this must stay ON — see `_protected_hits_seen`.
        "sibling_conflict_check": True,
        "landed_check_on_poll": False,
        "superseded_check": False,
        "refuse_merge_when_behind": False,
    }
    cfg.update(config or {})
    w = pw.PRWatcher(
        config=cfg,
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": _TASK, "result": "pass",
                            "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: state,
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="", stderr=""),
        pr_list_runner=fake_list,
        default_branch_resolver=lambda: "main",
    )
    # A REAL conflict: git reproduces it, so no rebase can resolve it. Stubbed
    # rather than run, because `classify_conflict` shells out to git against a
    # branch that does not exist here.
    w.classify_conflict = lambda st, **kw: pw.CONFLICT_REAL
    return w


class _Trace:
    """Records what the poll did, in order."""

    def __init__(self, w, *, rebase=None):
        self.order = []
        self.audits = []
        self.resumes = []
        self.ready = []
        real_refuse = w._refuse_protected

        def refuse(pr_url, task_id=""):
            hits = real_refuse(pr_url, task_id)
            if hits:
                self.order.append("refuse")
            return hits

        def maybe_rebase(task, state):
            self.order.append("rebase")
            return rebase or {"attempted": True, "pushed": False,
                              "reason": "conflict is real; rebase aborted",
                              "base_sha": "b" * 40}

        w._refuse_protected = refuse
        w._maybe_rebase = maybe_rebase
        w._audit = self.audits.append
        w._send_resume = lambda task_id, ctx: (
            self.resumes.append(task_id) or True)
        w._mark_ready = lambda *a, **kw: (self.ready.append(a) or True)
        w._hitl_alert = lambda *a, **kw: None

    def actions(self, name):
        return [a for a in self.audits if a.action == name]


# ── the reverse direction: the resume that must no longer be spent ──────────
def test_a_conflicting_protected_pr_never_spends_a_resume():
    """THE ONE THE CARD ASKED FOR. #2064 spent five of them and then escalated
    on 'resume cap reached', which was never the reason."""
    w = _build(_state())
    t = _Trace(w)
    w.poll_once()

    assert not t.resumes, (
        "a PR touching %s can never be merged by this watcher, so an agent "
        "resuming on it cannot produce a merge — the resume budget must not be "
        "spent" % _PROTECTED_FILE)
    assert not t.actions("resume")
    assert not t.actions("escalate")


def test_the_refusal_is_audited_on_the_first_poll():
    w = _build(_state())
    t = _Trace(w)
    report = w.poll_once()

    held = t.actions("protected_path_hold")
    assert len(held) == 1, "the real reason must be recorded, once, immediately"
    assert _PROTECTED_FILE in held[0].reason
    assert held[0].task_id == _TASK

    wait = [a for a in report.actions if a.action == "wait"][-1]
    assert "protected path" in wait.reason
    assert _PROTECTED_FILE in wait.reason


def test_the_wait_row_is_written_every_poll_so_merge_stall_can_attribute():
    """`merge_stall` attributes a stall from a 24h window of `reason` text
    (args/merge_stall.yaml, pattern 'protected path'). A hold recorded only
    once ages out of that window and the PR reads as an unexplained `alarm`."""
    w = _build(_state())
    t = _Trace(w)
    w.poll_once()
    w.poll_once()

    waits = [a for a in t.actions("wait") if "protected path" in a.reason]
    assert len(waits) == 2
    # The `protected_path_hold` row keeps its own once-per-PR dedupe, and that
    # is deliberately NOT asserted here: `_protected_already_held` reads the
    # audit trail back, which this fixture does not persist. It is pinned
    # against a real database by tests/ci/test_protected_path_report_and_audit.py.


def test_the_refusal_precedes_the_rebase():
    """The acceptance criterion, behaviourally: asked and audited BEFORE any
    `_maybe_rebase` call, not after 63 of them."""
    w = _build(_state())
    t = _Trace(w)
    w.poll_once()
    assert t.order[:2] == ["refuse", "rebase"], t.order


def test_the_bounded_rebase_is_still_attempted():
    """THE SURVEY'S FINDING, pinned. Holding one rung earlier would have taken a
    successful rebase from 11 of the 32 episodes the refusal catches — 8 of them
    a single pushed rebase and nothing else before the PR merged. A control that
    stops work it was never meant to stop gets switched off."""
    w = _build(_state())
    t = _Trace(w)
    w.poll_once()
    assert "rebase" in t.order, (
        "the bounded rebase is the one rung measured to REPAIR these PRs")


def test_a_pushed_rebase_still_reports_a_rebase_and_not_a_hold():
    """The repaired case must look exactly as it did before this change."""
    w = _build(_state())
    _Trace(w, rebase={"attempted": True, "pushed": True,
                      "reason": "rebased onto origin/main",
                      "base_sha": "c" * 40})
    report = w.poll_once()
    assert [a.action for a in report.actions] == ["rebase"]


# ── the direction that must NOT change ─────────────────────────────────────
def test_an_unprotected_conflicting_pr_still_gets_its_resume():
    w = _build(_state(), files=(_INNOCENT_FILE,))
    t = _Trace(w)
    w.poll_once()
    assert t.resumes == [_TASK], (
        "the refusal must not touch a PR that hits no protected path")


def test_an_unreadable_listing_fails_OPEN_here():
    """The OPPOSITE default from the merge refusal, and the asymmetry is
    deliberate. `_protected_hits` fails CLOSED because a merge gate that opens
    when it cannot see is not a gate. This one only stops the watcher spending
    resumes, and stopping on an unreadable listing would hold work the ladder
    would legitimately have repaired."""
    w = _build(_state(), listing=False)
    t = _Trace(w)
    w.poll_once()
    assert t.resumes == [_TASK]
    assert not t.actions("protected_path_hold")


def test_the_merge_refusal_still_fails_closed():
    """Pinned beside the fail-open one, because shipping both defaults in one
    file is the only way the asymmetry stays visible."""
    w = _build(_state(mergeable="MERGEABLE"), listing=False)
    assert w._protected_hits(_PR) == sorted(_GUARDED)
    assert w._protected_hits_seen(_PR, {}) is None


# ── `_protected_hits_seen` in isolation ────────────────────────────────────
def test_protected_hits_seen_has_three_answers():
    w = _build(_state())
    index = {_PR: {"files": {_PROTECTED_FILE}}}
    assert w._protected_hits_seen(_PR, index) == [_PROTECTED_FILE]
    assert w._protected_hits_seen(_PR, {_PR: {"files": {_INNOCENT_FILE}}}) == []
    assert w._protected_hits_seen(_PR, None) is None, "unmeasured, not clean"


def test_protected_hits_seen_is_empty_when_protection_is_off():
    w = _build(_state(), config={"protected_paths": []})
    assert w._protected_hits_seen(_PR, None) == []


# ── the mergeable arm: ahead of the un-draft again ─────────────────────────
def test_a_protected_mergeable_pr_is_held_before_the_un_draft():
    """kpr-watch-05 put the refusal 'AHEAD OF THE UN-DRAFT' for a stated reason:
    un-drafting is visible, hard to walk back, and burns the one brake a human
    still has. The un-draft was later moved UP to fix a different bug, silently
    overtaking the guard — every protected PR reaching this arm was un-drafted
    before anything asked whether it could ever be merged."""
    w = _build(_state(mergeable="MERGEABLE", draft=True))
    t = _Trace(w)
    report = w.poll_once()
    assert not t.ready, "a protected PR must not be un-drafted"
    assert t.actions("protected_path_hold")
    assert "protected path" in report.actions[-1].reason


def test_a_clean_mergeable_pr_costs_no_extra_forge_listing():
    """Moving the rung up past the sibling / landed / behind-main holds means
    PRs that used to `continue` before reaching it now reach it, and
    `_refuse_protected` costs a `gh pr list` per poll — the GraphQL door the
    outage behind this card was refusing. A PR present in the listing the poll
    already fetched is measured, so a clean answer must not pay for a second
    one."""
    listings = []
    w = _build(_state(mergeable="MERGEABLE"), files=(_INNOCENT_FILE,))
    inner = w._pr_list_runner

    def counting(cmd, **kw):
        listings.append(list(cmd))
        return inner(cmd, **kw)

    w._pr_list_runner = counting
    _Trace(w)
    w.poll_once()
    index_calls = [c for c in listings if "url,files,mergeable,isDraft" in c]
    assert len(index_calls) == 2, (
        "expected the sibling map plus `_auto_merge`'s own chokepoint and "
        "NOTHING from the moved rung — got %d: %r" % (len(index_calls),
                                                      index_calls))


def test_a_protected_mergeable_pr_is_held_before_the_stale_rebase():
    """The behind-main rung calls `_maybe_rebase` and pushes. Costs nothing
    measured — all 13 successful rebases the survey found on protected PRs
    carry `classification=merge_conflict` and none came from this rung."""
    w = _build(_state(mergeable="MERGEABLE"),
               config={"refuse_merge_when_behind": True})
    t = _Trace(w)
    w.poll_once()
    assert "rebase" not in t.order


# ── structural: the ordering, and the budget the card says not to touch ────
def test_the_refusal_is_asked_before_every_maybe_rebase_call():
    """A behavioural test covers today's two call sites; this one covers the
    third somebody adds. The refusal has to be ASKED before `_maybe_rebase`
    appears anywhere in `poll_once`."""
    src = inspect.getsource(pw.PRWatcher.poll_once)
    asked = src.index("protected_conflict = (")
    assert asked < src.index("_maybe_rebase("), (
        "the protected-path question must be asked before the first "
        "`_maybe_rebase` call in poll_once")


def test_the_rebase_and_resume_budgets_are_unchanged():
    """'Do NOT raise the rebase budget to quieten this — the budget is not the
    defect.' Nor lower it: a smaller budget would be this change taking credit
    for a different one."""
    cfg = yaml.safe_load(
        (pw.ROOT / "args/pr_watcher_config.yaml").read_text(
            encoding="utf-8")) or {}
    assert cfg.get("max_rebase_attempts_per_task") == 2
    assert cfg.get("max_resume_cycles_per_task") == 5
    assert cfg.get("auto_rebase_on_conflict") is True


@pytest.mark.parametrize("path", _GUARDED)
def test_the_guarded_paths_this_file_asserts_against_are_the_shipped_ones(path):
    cfg = yaml.safe_load(
        (pw.ROOT / "args/pr_watcher_config.yaml").read_text(
            encoding="utf-8")) or {}
    assert path in (cfg.get("protected_paths") or [])
