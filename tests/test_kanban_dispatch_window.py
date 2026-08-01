# CUI // SP-CTI
"""Tests for dispatch-window starvation.

_get_due_tasks truncates candidates to available_slots (3 by default), and the
respawn guards (open PR / just completed) used to run only AFTER that, inside
_dispatch_to_claude. So a task that could never be dispatched still occupied a
slot in the selection window every cycle.

Observed live: the three highest-priority due tasks all had open PRs. The
scheduler picked exactly those three, skipped all three, and dispatched
nothing — while thirteen ready tasks behind them were never considered. Every
gate read green and the board sat idle for hours.
"""
from __future__ import annotations

import pytest

from tools.genesis.reflexes import kanban as k


def _tasks(*ids):
    return [{"id": i, "title": f"task {i}"} for i in ids]


@pytest.fixture(autouse=True)
def _clear_pr_cache():
    k._open_pr_branch_cache.clear()
    yield
    k._open_pr_branch_cache.clear()


# --------------------------------------------------------------------------
# The starvation itself
# --------------------------------------------------------------------------

def test_blocked_tasks_yield_their_slot(monkeypatch):
    """The exact production failure: top 3 all blocked, 3 slots, nothing runs."""
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(
        k, "_open_pr_head_branches",
        lambda root: {"kanban/blocked-1", "kanban/blocked-2", "kanban/blocked-3"},
    )
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    candidates = _tasks("blocked-1", "blocked-2", "blocked-3",
                        "ready-1", "ready-2", "ready-3", "ready-4")
    kept = k._drop_respawn_guarded(candidates)

    assert [t["id"] for t in kept] == ["ready-1", "ready-2", "ready-3", "ready-4"]
    # Truncating to the 3 available slots now yields runnable work.
    assert [t["id"] for t in kept[:3]] == ["ready-1", "ready-2", "ready-3"]


def test_recently_completed_tasks_yield_their_slot(monkeypatch):
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: {"just-done"})
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    kept = k._drop_respawn_guarded(_tasks("just-done", "ready-1"))
    assert [t["id"] for t in kept] == ["ready-1"]


def test_unblocked_tasks_are_untouched(monkeypatch):
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    candidates = _tasks("a", "b", "c")
    assert k._drop_respawn_guarded(candidates) == candidates


def test_empty_input_is_safe():
    assert k._drop_respawn_guarded([]) == []


def test_tasks_without_an_id_are_dropped_not_crashed(monkeypatch):
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    kept = k._drop_respawn_guarded([{"title": "no id"}, {"id": "ok"}])
    assert [t["id"] for t in kept] == ["ok"]


# --------------------------------------------------------------------------
# Cost: one gh call for the board, not one per task
# --------------------------------------------------------------------------

def test_open_pr_lookup_is_batched_per_repo(monkeypatch):
    """Per-task _has_open_pr is a subprocess with a 10s timeout — far too
    expensive to run across every candidate during selection."""
    calls = []

    def _fake(root):
        calls.append(root)
        return set()

    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", _fake)
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    k._drop_respawn_guarded(_tasks(*[f"t{i}" for i in range(20)]))
    assert calls == ["/repo"], f"expected one lookup for one repo, got {calls}"


def test_external_repo_tasks_are_looked_up_in_their_own_repo(monkeypatch):
    """An external task's PRs live in ITS repo — asking ICDev always says no."""
    seen = []

    def _fake(root):
        seen.append(root)
        return set()

    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", _fake)
    monkeypatch.setattr(
        k, "_task_repo_root",
        lambda tid: "/compass" if tid.startswith("prem-") else "/icdev",
    )

    k._drop_respawn_guarded(_tasks("prem-a", "icdev-a", "prem-b"))
    assert sorted(set(seen)) == ["/compass", "/icdev"]


# --------------------------------------------------------------------------
# Failure modes must not block dispatch
# --------------------------------------------------------------------------

def test_gh_unavailable_does_not_filter_anything(monkeypatch):
    """Air-gap: no gh means no filtering, and the per-task guard is the backstop."""
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())
    monkeypatch.setattr(k, "_task_repo_root", lambda tid: "/repo")

    candidates = _tasks("a", "b")
    assert k._drop_respawn_guarded(candidates) == candidates


def test_open_pr_branches_returns_empty_set_on_gh_failure(monkeypatch):
    class _R:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: _R())
    assert k._open_pr_head_branches("/repo") == set()


def test_open_pr_branches_returns_empty_set_when_gh_raises(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(k.subprocess, "run", _boom)
    assert k._open_pr_head_branches("/repo") == set()


def test_repo_root_failure_falls_back_to_base_dir(monkeypatch):
    def _boom(tid):
        raise RuntimeError("no repo registry")

    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids, **kw: set())
    monkeypatch.setattr(k, "_task_repo_root", _boom)
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())

    kept = k._drop_respawn_guarded(_tasks("a"))
    assert [t["id"] for t in kept] == ["a"]


def test_recent_success_batch_is_empty_for_no_ids():
    assert k._tasks_with_recent_success([]) == set()


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------

def test_pr_listing_is_cached_within_the_ttl(monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stdout = '[{"headRefName": "kanban/x"}]'

    def _run(*a, **kw):
        calls.append(1)
        return _R()

    monkeypatch.setattr(k.subprocess, "run", _run)
    first = k._open_pr_head_branches("/repo")
    second = k._open_pr_head_branches("/repo")
    assert first == second == {"kanban/x"}
    assert len(calls) == 1, "second lookup inside the TTL must hit the cache"


def test_pr_cache_expires(monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stdout = "[]"

    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: (calls.append(1), _R())[1])
    k._open_pr_head_branches("/repo")
    # Age the cache entry past its TTL.
    stamp, value = k._open_pr_branch_cache["/repo"]
    k._open_pr_branch_cache["/repo"] = (stamp - k._OPEN_PR_CACHE_TTL_SECONDS - 1, value)
    k._open_pr_head_branches("/repo")
    assert len(calls) == 2


def test_cache_ttl_is_shorter_than_a_scheduler_cycle():
    """Stale PR state must not survive into the next cycle's decision."""
    assert k._OPEN_PR_CACHE_TTL_SECONDS < 60


# --------------------------------------------------------------------------
# The filter must run before the cap
# --------------------------------------------------------------------------

def test_filter_is_applied_before_truncation_in_source():
    from pathlib import Path

    src = Path(k.__file__).read_text(encoding="utf-8")
    filter_at = src.index("result = _drop_respawn_guarded(result)")
    cap_at = src.index("result = result[:available_slots]")
    assert filter_at < cap_at, (
        "filtering after the cap is the bug: blocked tasks consume slots they "
        "can never use"
    )
