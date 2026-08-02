"""Tests for tools/kanban/pr_linker.py — task <-> PR reconciliation.

Guards the failure this module exists to fix: an open kanban PR whose task row
has an empty `executor_url` is invisible to pr_watcher forever, because the only
writer of that column requires a local `kanban/<id>` ref AND an un-suffixed
branch name. Ten green PRs were stranded that way on 2026-08-02.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.kanban import pr_linker


# ── branch -> task id ───────────────────────────────────────────────────────

TASK_IDS = {"gdx-aud-01", "gdx-aud-01-d2", "tsr-core-01", "idp-score-01-d1"}


@pytest.mark.parametrize("branch,expected", [
    ("kanban/gdx-aud-01", "gdx-aud-01"),
    ("kanban/idp-score-01-d1", "idp-score-01-d1"),
    # retry suffixes must still resolve to the base task
    ("kanban/gdx-aud-01-r2", "gdx-aud-01"),
    ("kanban/gdx-aud-01-land", "gdx-aud-01"),
    ("kanban/tsr-core-01-d5-r2", "tsr-core-01"),
    # longest match wins: -d2 is its own task, not a suffix of gdx-aud-01
    ("kanban/gdx-aud-01-d2", "gdx-aud-01-d2"),
    ("kanban/gdx-aud-01-d2-r3", "gdx-aud-01-d2"),
    # non-kanban branches are not ours
    ("fix/swp-swallow-01-d1-missing-path", None),
    ("main", None),
    ("kanban/", None),
    ("", None),
    # unknown task
    ("kanban/nope-99", None),
])
def test_branch_to_task_id(branch, expected):
    assert pr_linker.branch_to_task_id(branch, TASK_IDS) == expected


def test_boundary_stops_numeric_run_on():
    """`tsr-core-011` must not bind to task `tsr-core-01`."""
    assert pr_linker.branch_to_task_id("kanban/tsr-core-011", TASK_IDS) is None


# ── link_open_prs ───────────────────────────────────────────────────────────

class FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("SELECT"):
            return SimpleNamespace(fetchall=lambda: self._rows)
        assert "UPDATE kanban_tasks" in sql
        self.updates.append(params)
        return SimpleNamespace(fetchall=lambda: [])

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _rows(*pairs):
    return [{"id": tid, "executor_url": url} for tid, url in pairs]


def _runner(prs, returncode=0, stdout=None):
    def run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(prs) if stdout is None else stdout,
            stderr="",
        )
    return run


def test_links_task_with_empty_executor_url():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([{
            "number": 1135, "url": "https://github.com/o/r/pull/1135",
            "headRefName": "kanban/gdx-aud-01", "createdAt": "2026-08-01T00:00:00Z",
        }]),
    )
    assert out["linked"] == [{
        "task_id": "gdx-aud-01",
        "url": "https://github.com/o/r/pull/1135",
        "branch": "kanban/gdx-aud-01",
    }]
    assert conn.updates == [("https://github.com/o/r/pull/1135", "gdx-aud-01")]
    assert conn.committed


def test_never_overwrites_an_existing_pr_link():
    """A wrong link is worse than a missing one — the watcher would merge it."""
    conn = FakeConn(_rows(("gdx-aud-01", "https://github.com/o/r/pull/999")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([{
            "number": 1135, "url": "https://github.com/o/r/pull/1135",
            "headRefName": "kanban/gdx-aud-01", "createdAt": "2026-08-01T00:00:00Z",
        }]),
    )
    assert out["linked"] == []
    assert out["already_linked"] == [
        {"task_id": "gdx-aud-01", "url": "https://github.com/o/r/pull/999"}
    ]
    assert conn.updates == []


def test_multiple_open_prs_links_newest_and_reports_ambiguity():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([
            {"number": 1135, "url": "https://github.com/o/r/pull/1135",
             "headRefName": "kanban/gdx-aud-01", "createdAt": "2026-08-01T00:00:00Z"},
            {"number": 1221, "url": "https://github.com/o/r/pull/1221",
             "headRefName": "kanban/gdx-aud-01-r2", "createdAt": "2026-08-02T00:00:00Z"},
            {"number": 1220, "url": "https://github.com/o/r/pull/1220",
             "headRefName": "kanban/gdx-aud-01-land", "createdAt": "2026-08-01T12:00:00Z"},
        ]),
    )
    assert [e["url"] for e in out["linked"]] == ["https://github.com/o/r/pull/1221"]
    assert len(out["ambiguous"]) == 1
    assert out["ambiguous"][0]["task_id"] == "gdx-aud-01"
    assert sorted(out["ambiguous"][0]["others"]) == [
        "https://github.com/o/r/pull/1135", "https://github.com/o/r/pull/1220",
    ]


def test_dry_run_writes_nothing():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([{
            "number": 1135, "url": "https://github.com/o/r/pull/1135",
            "headRefName": "kanban/gdx-aud-01", "createdAt": "2026-08-01T00:00:00Z",
        }]),
        dry_run=True,
    )
    assert len(out["linked"]) == 1
    assert conn.updates == []
    assert not conn.committed


def test_non_kanban_branches_are_ignored_not_reported():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([{
            "number": 1174, "url": "https://github.com/o/r/pull/1174",
            "headRefName": "fix/some-manual-branch", "createdAt": "2026-08-01T00:00:00Z",
        }]),
    )
    assert out["linked"] == []
    assert out["unmatched"] == []   # not a kanban branch — not our business
    assert conn.updates == []


def test_unmatched_kanban_branch_is_reported():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(
        lambda: conn,
        runner=_runner([{
            "number": 1, "url": "https://github.com/o/r/pull/1",
            "headRefName": "kanban/deleted-task-42", "createdAt": "2026-08-01T00:00:00Z",
        }]),
    )
    assert out["unmatched"] == [
        {"branch": "kanban/deleted-task-42", "url": "https://github.com/o/r/pull/1"}
    ]


def test_no_open_prs_is_a_noop():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    out = pr_linker.link_open_prs(lambda: conn, runner=_runner([]))
    assert out == {"linked": [], "ambiguous": [], "already_linked": [], "unmatched": []}
    assert conn.updates == []


def test_gh_failure_raises_so_the_caller_can_degrade():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    with pytest.raises(RuntimeError, match="gh pr list failed"):
        pr_linker.link_open_prs(
            lambda: conn, runner=_runner([], returncode=1))


def test_gh_non_json_raises():
    conn = FakeConn(_rows(("gdx-aud-01", "")))
    with pytest.raises(RuntimeError, match="non-JSON"):
        pr_linker.link_open_prs(
            lambda: conn, runner=_runner([], stdout="not json"))
