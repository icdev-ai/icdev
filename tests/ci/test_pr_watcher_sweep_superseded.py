# CUI // SP-CTI
"""The unlinked sweep ASKS the superseded question before it merges (mfx-mrg-06).

``test_pr_watcher_superseded.py`` pins the LINKED path: a task the poll can
see gets the mfx-mrg-02 verdict before any other rung. MEASURED 2026-09-06
(docs/audits/mfx-mrg-05-superseded-population-survey.md), that path never saw
any of the fifteen duplicates an operator closed by hand, because every one of
their tasks was already ``done`` when the duplicate PR was opened -- and
``list_pr_tasks`` selects six non-terminal statuses. The unlinked sweep DID
see them, computed its ``linked`` set from the same query, read each as an
ordinary unlinked PR and ran ``classify_merge_readiness``. Five of that shape
had already MERGED as empty commits on main.

These pin the SWEEP, behaviourally:

  * a PR whose task went terminal is asked the question, and a fire CLOSES it
    with its evidence under the task's id -- never merged;
  * the close is gated on the PREDICATE and never on the terminal task alone:
    the same PR with no merged sibling takes exactly the path it took before;
  * an unreadable merged listing changes nothing (``checked: False`` is never a
    finding), and costs no per-PR state fetch;
  * a firing PR with NO task row is HELD and audited once, never closed (the
    sweep never closes a human's PR) and never merged (the merge IS the defect);
  * the merged index the poll already fetched is REUSED -- no second listing.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow

_TASK = "mfx-sib-01"
_BRANCH = "kanban/mfx-sib-01"
_HEAD = "9d1c6e4f2b7a0c3d5e8f1a2b3c4d5e6f7a8b9c0d"
_SIBLING_URL = "https://github.com/o/r/pull/2080"
_DUP_URL = "https://github.com/o/r/pull/2082"


def _merged_sibling(branch=_BRANCH, head=_HEAD, number=2080):
    return {
        "number": number, "url": _SIBLING_URL,
        "title": "feat(kanban): the sibling that landed", "body": "",
        "headRefName": branch, "headRefOid": head,
        "mergedAt": "2026-09-05T13:52:03Z",
        "commits": [{"oid": head}],
    }


def _open_pr(url=_DUP_URL, branch=_BRANCH, head=_HEAD, number=2082):
    """The shape the sweep's own `gh pr list` returns: NO commit list."""
    return {
        "url": url, "number": number, "headRefName": branch,
        "headRefOid": head, "baseRefName": "main", "isDraft": False,
        "labels": [], "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
        "reviews": [], "state": "OPEN",
    }


def _state(url=_DUP_URL, branch=_BRANCH, head=_HEAD, number=2082):
    """`gh pr view` for one PR: this is where the commit list comes from."""
    st = _open_pr(url=url, branch=branch, head=head, number=number)
    st["commits"] = [{"oid": head}]
    return st


class _Sweep:
    """A watcher with the forge, the board and the merge stubbed."""

    def __init__(self, *, open_prs, merged, tasks=(), config_over=None,
                 states=None):
        self.calls = []
        self.closed = []
        self.merged_urls = []
        self.audits = []
        self.state_fetches = []
        states = states or {}

        def runner(cmd, **kw):
            self.calls.append(list(cmd))
            if "close" in cmd:
                self.closed.append(list(cmd))
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if "--state" in cmd and "merged" in cmd:
                if merged is None:
                    return SimpleNamespace(returncode=1, stdout="",
                                           stderr="listing refused")
                return SimpleNamespace(returncode=0, stdout=json.dumps(merged),
                                       stderr="")
            if "--state" in cmd and "open" in cmd:
                return SimpleNamespace(returncode=0, stdout=json.dumps(open_prs),
                                       stderr="")
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        def fetch_state(url, **kw):
            self.state_fetches.append(url)
            return states.get(url) or _state(url=url)

        config = {
            "auto_merge_enabled": True,
            "auto_merge_require_approval": False,
            "merge_unlinked_prs": True,
            "sibling_conflict_check": False,
            "landed_check_on_poll": False,
            "link_prs_on_poll": False,
            "refuse_merge_when_behind": False,
            "protected_paths": [],
            "superseded_check": True,
            "superseded_close": True,
            "superseded_revert_leg": False,
        }
        config.update(config_over or {})
        rows = [_FakeRow(id=t["id"], title="T", description="",
                         status=t["status"], executor_url=t.get("pr_url", ""))
                for t in tasks]
        self.w = pw.PRWatcher(
            config=config,
            get_connection=_fake_connection_factory(rows),
            queue_message=lambda *a, **kw: {"queued": True},
            fetch_state=fetch_state,
            fetch_logs=lambda url, **kw: "",
            auto_merge_runner=runner,
            pr_list_runner=runner,
            gh_runner=runner,
            gh_close_runner=runner,
            default_branch_resolver=lambda: "main",
        )
        self.w._auto_merge = lambda url, **_kw: (
            self.merged_urls.append(url) or True)
        self.w._audit = lambda action: self.audits.append(action)
        self.w._hitl_alert = lambda *a, **kw: None
        self.w._open_pr_index = lambda *a, **kw: {}

    def run(self, monkeypatch, *, linked=()):
        # The fake board ignores the status filter, so `list_pr_tasks` is
        # pinned to what the REAL query returns: only the pollable statuses.
        monkeypatch.setattr(pw, "list_pr_tasks",
                            lambda gc, task_id=None: [{"pr_url": u}
                                                      for u in linked])
        report = pw.WatcherReport(started_at="", finished_at="",
                                  tasks_checked=0)
        self.w._sweep_unlinked_prs(report)
        return report

    def actions(self, name):
        return [a for a in self.audits if a.action == name]


_DONE_TASK = {"id": _TASK, "status": "done", "pr_url": _SIBLING_URL}


# ---------------------------------------------------------------------------
def test_a_terminal_task_duplicate_is_closed_and_never_merged(monkeypatch):
    s = _Sweep(open_prs=[_open_pr()], merged=[_merged_sibling()],
               tasks=[_DONE_TASK])
    s.run(monkeypatch)
    assert s.merged_urls == [], "the duplicate must not merge"
    assert len(s.closed) == 1 and _DUP_URL in s.closed[0]
    assert "--comment" in s.closed[0]
    (row,) = s.actions("close_superseded")
    assert row.task_id == _TASK and row.pr_url == _DUP_URL
    assert row.classification == pw.SUPERSEDED
    assert "#2080" in row.reason and "shared_commits" in row.reason


def test_the_terminal_task_alone_is_never_a_close(monkeypatch):
    """5 of the 26 terminal-born PRs were merged BY THE PIPELINE; 'the task is
    done' is what makes the PR reachable, not what closes it."""
    s = _Sweep(open_prs=[_open_pr()],
               merged=[_merged_sibling(branch="kanban/other", head="ab" * 20,
                                       number=2079)],
               tasks=[_DONE_TASK])
    s.run(monkeypatch)
    assert s.closed == []
    assert s.actions("close_superseded") == []
    # The path it took before: green, mergeable, unlinked -> merged.
    assert s.merged_urls == [_DUP_URL]


def test_an_unreadable_merged_listing_changes_nothing(monkeypatch):
    s = _Sweep(open_prs=[_open_pr()], merged=None, tasks=[_DONE_TASK])
    s.run(monkeypatch)
    assert s.closed == []
    assert s.merged_urls == [_DUP_URL]
    assert s.state_fetches == [], "unchecked must not cost a per-PR fetch"
    assert s.actions("close_superseded") == []
    assert s.actions("superseded_hold") == []


def test_a_pr_outside_every_merged_family_costs_no_state_fetch(monkeypatch):
    """The commit list comes from one `gh pr view` per CANDIDATE, because the
    listing cannot carry `commits` at --limit 100 (GraphQL node budget). A PR
    no merged sibling names is not a candidate."""
    s = _Sweep(open_prs=[_open_pr(url="https://github.com/o/r/pull/3",
                                  branch="feat/unrelated", number=3)],
               merged=[_merged_sibling()])
    s.run(monkeypatch)
    assert s.state_fetches == []
    assert s.merged_urls == ["https://github.com/o/r/pull/3"]


def test_a_firing_pr_with_no_task_is_held_never_closed_never_merged(monkeypatch):
    url = "https://github.com/o/r/pull/7"
    head = "c" * 40
    s = _Sweep(
        open_prs=[_open_pr(url=url, branch="fix/reopened", head=head, number=7)],
        merged=[_merged_sibling(branch="fix/reopened", head=head, number=6)],
        states={url: _state(url=url, branch="fix/reopened", head=head,
                            number=7)},
    )
    s.run(monkeypatch)
    assert s.closed == [], "the sweep never closes a human's PR"
    assert s.merged_urls == [], "and never merges a superseded one"
    (row,) = s.actions("superseded_hold")
    assert row.task_id == "" and row.pr_url == url
    assert "#6" in row.reason


def test_a_retry_branch_resolves_to_its_task(monkeypatch):
    branch = _BRANCH + "-r2"
    s = _Sweep(open_prs=[_open_pr(branch=branch)],
               merged=[_merged_sibling(branch=branch)],
               tasks=[_DONE_TASK],
               states={_DUP_URL: _state(branch=branch)})
    s.run(monkeypatch)
    (row,) = s.actions("close_superseded")
    assert row.task_id == _TASK
    assert s.merged_urls == []


def test_a_task_the_poll_can_see_is_left_to_the_poll(monkeypatch):
    """The linked path already asks the question (mfx-mrg-02); asking twice
    would write two audit rows and try two closes for one PR."""
    s = _Sweep(open_prs=[_open_pr()], merged=[_merged_sibling()],
               tasks=[{"id": _TASK, "status": "pr_opened", "pr_url": _DUP_URL}])
    s.run(monkeypatch, linked=[_DUP_URL])
    assert s.closed == [] and s.merged_urls == []
    assert s.state_fetches == []


def test_report_only_holds_without_closing(monkeypatch):
    s = _Sweep(open_prs=[_open_pr()], merged=[_merged_sibling()],
               tasks=[_DONE_TASK], config_over={"superseded_close": False})
    s.run(monkeypatch)
    assert s.closed == []
    assert s.merged_urls == [], "report-only still withholds the merge"
    (row,) = s.actions("superseded_warn")
    assert row.task_id == _TASK


def test_a_closed_duplicate_never_completes_a_terminal_task(monkeypatch):
    """`_handle_superseded` completes a task only when it is NOT terminal and
    its work is proven landed. A done task stays done -- no UPDATE is issued."""
    s = _Sweep(open_prs=[_open_pr()], merged=[_merged_sibling()],
               tasks=[_DONE_TASK])
    seen = []
    monkeypatch.setattr(pw, "_set_task_status",
                        lambda *a, **kw: seen.append(a) or True)
    s.run(monkeypatch)
    assert seen == []
    (row,) = s.actions("close_superseded")
    assert "task completed" not in row.reason


def test_the_polls_merged_index_is_reused_by_the_sweep(monkeypatch):
    """`merged_index` is computed once per poll; the sweep must not list again."""
    s = _Sweep(open_prs=[_open_pr()], merged=[_merged_sibling()],
               tasks=[_DONE_TASK])
    # The real query excludes a `done` task from the poll; the fake board
    # does not, so pin it here as well or the poll services the done task.
    monkeypatch.setattr(pw, "list_pr_tasks", lambda gc, task_id=None: [])
    s.w.poll_once()
    listings = [c for c in s.calls if "--state" in c and "merged" in c]
    assert len(listings) == 1
    s.run(monkeypatch)
    listings = [c for c in s.calls if "--state" in c and "merged" in c]
    assert len(listings) == 1, "the sweep re-listed merged PRs"
    assert len(s.closed) == 1


def test_the_hold_is_audited_once_per_pr(monkeypatch):
    url = "https://github.com/o/r/pull/7"
    head = "c" * 40
    s = _Sweep(
        open_prs=[_open_pr(url=url, branch="fix/reopened", head=head, number=7)],
        merged=[_merged_sibling(branch="fix/reopened", head=head, number=6)],
        states={url: _state(url=url, branch="fix/reopened", head=head,
                            number=7)},
    )
    s.run(monkeypatch)
    assert len(s.actions("superseded_hold")) == 1
    # Second poll: the prior row is visible, so nothing is re-recorded.
    s.w._count_audit_actions = lambda *a, **kw: 1
    s.run(monkeypatch)
    assert len(s.actions("superseded_hold")) == 1
    assert s.merged_urls == []
