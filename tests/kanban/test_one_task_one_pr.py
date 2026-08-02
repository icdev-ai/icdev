"""One task, one PR (kpr-dup-02).

A retry runs on a fresh branch, and `gh pr create` only fails when a PR exists
for that EXACT head — so the dispatcher opened a second PR for a task that
already had one. gdx-aud-01 reached three (#1135, #1220, #1221) and pr_linker
could only guess which mattered.

The load-bearing test here is `test_refuses_to_close_a_pr_with_unique_commits`:
closing a PR whose branch holds work that exists nowhere else would destroy
committed work to tidy the board, which is far worse than the duplicate.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

kanban = pytest.importorskip("tools.genesis.reflexes.kanban")


class GitHubFake:
    """Stands in for gh + git. Records what was closed."""

    def __init__(self, *, branches, prs, cherry=None, cherry_rc=0):
        self.branches = branches
        self.prs = prs                      # branch -> [pr dicts]
        self.cherry = cherry or {}          # (upstream, head) -> ["+sha", ...]
        self.cherry_rc = cherry_rc
        self.closed = []
        self.created = []

    def __call__(self, args, **kwargs):
        if args[0] == "git" and args[1] == "for-each-ref":
            return SimpleNamespace(returncode=0, stdout="\n".join(self.branches), stderr="")
        if args[0] == "git" and args[1] == "cherry":
            key = (args[2], args[3])
            return SimpleNamespace(
                returncode=self.cherry_rc,
                stdout="\n".join(self.cherry.get(key, [])), stderr="",
            )
        if args[0] == "gh" and args[1] == "pr" and args[2] == "list":
            head = args[args.index("--head") + 1]
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(self.prs.get(head, [])), stderr="")
        if args[0] == "gh" and args[1] == "pr" and args[2] == "close":
            self.closed.append(args[3])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def patched(monkeypatch):
    def _install(fake):
        import subprocess
        monkeypatch.setattr(
            kanban, "_branches_for_task",
            lambda tid, root: [b for b in fake.branches if tid in b])
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return _install


def _pr(num, branch, created="2026-08-01T00:00:00Z"):
    return {"url": f"https://gh/o/r/pull/{num}", "number": num,
            "headRefName": branch, "createdAt": created}


# ── discovery ───────────────────────────────────────────────────────────────

def test_finds_prs_on_suffixed_branches_not_just_the_canonical_one(patched):
    """The whole bug: a worker's PR lives on kanban/<id>-r2, not kanban/<id>."""
    patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-r2", "origin/kanban/t1-land"],
        prs={"kanban/t1-r2": [_pr(2, "kanban/t1-r2")],
             "kanban/t1-land": [_pr(3, "kanban/t1-land")]},
    ))
    found = kanban._open_prs_for_task("t1", "/repo")
    assert {p["number"] for p in found} == {2, 3}


def test_excludes_the_branch_we_are_working_on(patched):
    patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-r2"],
        prs={"kanban/t1": [_pr(1, "kanban/t1")], "kanban/t1-r2": [_pr(2, "kanban/t1-r2")]},
    ))
    found = kanban._open_prs_for_task("t1", "/repo", exclude_branch="kanban/t1")
    assert [p["number"] for p in found] == [2]


def test_deduplicates_a_pr_reachable_via_two_refs(patched):
    patched(GitHubFake(
        branches=["kanban/t1", "origin/kanban/t1"],
        prs={"kanban/t1": [_pr(1, "kanban/t1")]},
    ))
    assert len(kanban._open_prs_for_task("t1", "/repo")) == 1


def test_newest_first(patched):
    patched(GitHubFake(
        branches=["kanban/t1-a", "kanban/t1-b"],
        prs={"kanban/t1-a": [_pr(1, "kanban/t1-a", "2026-08-01T00:00:00Z")],
             "kanban/t1-b": [_pr(2, "kanban/t1-b", "2026-08-02T00:00:00Z")]},
    ))
    assert [p["number"] for p in kanban._open_prs_for_task("t1", "/repo")] == [2, 1]


def test_no_branches_means_no_prs(patched):
    patched(GitHubFake(branches=[], prs={}))
    assert kanban._open_prs_for_task("t1", "/repo") == []


# ── supersede ───────────────────────────────────────────────────────────────

def test_closes_a_fully_contained_stale_pr(patched):
    fake = patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-old"],
        prs={"kanban/t1-old": [_pr(7, "kanban/t1-old")]},
        cherry={("kanban/t1", "origin/kanban/t1-old"): ["- abc123"]},  # nothing unique
    ))
    closed = kanban._supersede_stale_prs("t1", "https://gh/o/r/pull/9", "kanban/t1", "/repo")
    assert closed == ["https://gh/o/r/pull/7"]
    assert fake.closed == ["7"]


def test_refuses_to_close_a_pr_with_unique_commits(patched):
    """Losing committed work to tidy the board is worse than a duplicate PR."""
    fake = patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-old"],
        prs={"kanban/t1-old": [_pr(7, "kanban/t1-old")]},
        cherry={("kanban/t1", "origin/kanban/t1-old"): ["+ deadbee"]},  # unique work
    ))
    closed = kanban._supersede_stale_prs("t1", "https://gh/o/r/pull/9", "kanban/t1", "/repo")
    assert closed == []
    assert fake.closed == []


def test_refuses_to_close_when_the_comparison_errors(patched):
    """An unreadable compare is not evidence the work is safe."""
    fake = patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-old"],
        prs={"kanban/t1-old": [_pr(7, "kanban/t1-old")]},
        cherry_rc=128,
    ))
    assert kanban._supersede_stale_prs("t1", "https://gh/o/r/pull/9", "kanban/t1", "/repo") == []
    assert fake.closed == []


def test_never_closes_the_pr_we_are_keeping(patched):
    fake = patched(GitHubFake(
        branches=["kanban/t1", "kanban/t1-r2"],
        prs={"kanban/t1-r2": [_pr(9, "kanban/t1-r2")]},
        cherry={("kanban/t1", "origin/kanban/t1-r2"): []},
    ))
    kanban._supersede_stale_prs("t1", "https://gh/o/r/pull/9", "kanban/t1", "/repo")
    assert fake.closed == []


def test_close_failure_does_not_raise(patched):
    class Boom(GitHubFake):
        def __call__(self, args, **kwargs):
            if args[0] == "gh" and args[2] == "close":
                raise OSError("gh exploded")
            return super().__call__(args, **kwargs)

    patched(Boom(
        branches=["kanban/t1", "kanban/t1-old"],
        prs={"kanban/t1-old": [_pr(7, "kanban/t1-old")]},
        cherry={("kanban/t1", "origin/kanban/t1-old"): []},
    ))
    assert kanban._supersede_stale_prs("t1", "https://gh/o/r/pull/9", "kanban/t1", "/repo") == []
