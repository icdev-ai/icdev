# CUI // SP-CTI
"""Auto-merge must not depend on which door the work came through.

Every repair path in the watch loop starts from list_pr_tasks, which selects
kanban_tasks — so a PR with no task row is invisible to all of it: no auto-ready,
no auto-merge, no rebase, no escalation. On 2026-08-09 roughly a dozen PRs opened
from CLI sessions were each merged BY HAND for that reason, while kanban's own
PRs merged themselves.
"""
from __future__ import annotations

import json

import tools.ci.pr_watcher as pw


def _pr(url, *, draft=False, labels=(), base="main", mergeable="MERGEABLE",
        checks=(("SUCCESS",),), reviews=()):
    return {
        "url": url, "number": int(url.rsplit("/", 1)[-1]), "headRefName": "fix/x",
        "baseRefName": base, "isDraft": draft,
        "labels": [{"name": n} for n in labels],
        "mergeable": mergeable,
        "statusCheckRollup": [{"conclusion": c[0]} for c in checks],
        "reviews": list(reviews), "state": "OPEN",
    }


class _W(pw.PRWatcher):
    """Watcher with the forge and the task list stubbed."""

    def __init__(self, prs, linked=(), **config):
        cfg = {"auto_merge_enabled": True, "merge_unlinked_prs": True}
        cfg.update(config)
        super().__init__(config=cfg, get_connection=lambda: None)
        self._prs = prs
        self._linked = list(linked)
        self.merged = []
        self._pr_list_runner = lambda *a, **k: type(
            "P", (), {"returncode": 0, "stdout": json.dumps(self._prs), "stderr": ""})()
        # `**_kw`: kpr-watch-04 hands `_auto_merge` the PR record too.
        self._auto_merge = lambda url, **_kw: (self.merged.append(url) or True)
        self._default_branch = lambda: "main"
        self._audit = lambda action: None

    def _connection(self):
        return lambda: None


def _sweep(w, monkeypatch):
    monkeypatch.setattr(pw, "list_pr_tasks",
                        lambda gc: [{"pr_url": u} for u in w._linked])
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    return w.merged


def test_a_green_unlinked_pr_is_merged(monkeypatch):
    w = _W([_pr("https://x/pull/1")])
    assert _sweep(w, monkeypatch) == ["https://x/pull/1"]


def test_a_task_linked_pr_is_left_to_the_main_loop(monkeypatch):
    """It has a task to carry resumes and status; merging it here would skip that."""
    w = _W([_pr("https://x/pull/1")], linked=["https://x/pull/1"])
    assert _sweep(w, monkeypatch) == []


# ── the refusals ────────────────────────────────────────────────────────────
def test_a_hold_label_stops_it(monkeypatch):
    """A human may open a PR to discuss rather than to land."""
    for label in ("hold", "do-not-merge", "wip", "no-automerge", "blocked"):
        w = _W([_pr("https://x/pull/1", labels=[label])])
        assert _sweep(w, monkeypatch) == [], label


def test_a_label_is_matched_case_insensitively(monkeypatch):
    w = _W([_pr("https://x/pull/1", labels=["Do-Not-Merge"])])
    assert _sweep(w, monkeypatch) == []


def test_a_draft_is_never_un_drafted_here(monkeypatch):
    """For a human PR the draft IS the not-ready signal — a kanban task has a
    gate and a dependency to say that instead."""
    w = _W([_pr("https://x/pull/1", draft=True)])
    assert _sweep(w, monkeypatch) == []


def test_requested_changes_are_never_merged_over(monkeypatch):
    w = _W([_pr("https://x/pull/1", reviews=[{"state": "CHANGES_REQUESTED"}])])
    assert _sweep(w, monkeypatch) == []


def test_a_failing_or_unfinished_pr_is_left_alone(monkeypatch):
    for checks in ((("FAILURE",),), ((None,),), ()):
        w = _W([_pr("https://x/pull/1", checks=checks)])
        assert _sweep(w, monkeypatch) == [], checks


def test_a_pr_onto_a_non_default_base_is_left_alone(monkeypatch):
    """A stacked PR merged into main would take its parent's commits with it."""
    w = _W([_pr("https://x/pull/1", base="feat/parent")])
    assert _sweep(w, monkeypatch) == []


def test_a_conflicting_pr_is_left_alone(monkeypatch):
    w = _W([_pr("https://x/pull/1", mergeable="CONFLICTING")])
    assert _sweep(w, monkeypatch) == []


def test_the_toggle_off_disables_the_whole_sweep(monkeypatch):
    w = _W([_pr("https://x/pull/1")], merge_unlinked_prs=False)
    assert _sweep(w, monkeypatch) == []


def test_an_unreadable_task_list_refuses_rather_than_merging_everything(monkeypatch):
    """Without the linked set, every task-linked PR looks unlinked — and would be
    merged without its task ever being updated."""
    w = _W([_pr("https://x/pull/1")])
    def boom(gc):
        raise RuntimeError("db down")
    monkeypatch.setattr(pw, "list_pr_tasks", boom)
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    w._sweep_unlinked_prs(report)
    assert w.merged == []


def test_the_sweep_never_runs_from_poll_once():
    """It shelled out to a REAL `gh pr list` during the unit suite.

    poll_once is what tests call. With the sweep inside it, three existing tests
    reached the live forge and one recorded a merge call against a real open PR —
    only a stubbed _auto_merge stood between that and merging someone's work from
    a test run. The sweep is periodic housekeeping, so it belongs in the daemon
    loop, which no unit test drives.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "tools" / "ci" / "pr_watcher.py"
           ).read_text(encoding="utf-8")
    poll = src.index("    def poll_once(")
    daemon = src.index("    def run_daemon(")
    call = src.index("self._sweep_unlinked_prs(")
    assert not (poll < call < daemon), (
        "the sweep must not be called from poll_once — unit tests call it, and "
        "it reaches the live forge"
    )
    assert call > daemon, "expected the sweep to run from the daemon loop"
