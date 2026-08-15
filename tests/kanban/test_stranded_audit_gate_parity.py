#!/usr/bin/env python3
"""The stranded audit must decide "merged?" the way the gate does. CUI // SP-CTI

Two components answered the same question with different code and disagreed:

  the GATE   tools/genesis/reflexes/kanban.py::_branch_has_unmerged_commits
             -- used by the dispatch timeout guard AND by
             tools/kanban/cli.py --set-status done (it imports the very same
             function), so those two were already in sync with each other.

  the AUDIT  tools/kanban/stranded_audit.py, an independent implementation that
             compared by ANCESTRY, never asked whether a branch's PR had merged,
             and matched only the exact name kanban/<id>.

Measured on the live board 2026-08-15: of 506 tasks the audit called stranded,
184 (36%) had branches whose PRs were ALL closed or merged -- 159 MERGED -- and
the gate skipped every one. Six of the eight tasks landed that day were reported
stranded with their work verifiably on main; the two that were not are exactly
the two merged with a merge commit rather than a squash.

A squash merge lands the patch under a new SHA with NO ancestry link, so
`git log origin/main..ref` still lists the commits. Only a patch-id comparison
(`git cherry`) sees them as landed. That is why the two must share a primitive
rather than merely both "check git".

Deterministic: no network, no board, no gh. Every git/PR fact is injected.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402
from tools.kanban import stranded_audit as sa  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    k._ABANDONED_BRANCH_CACHE.clear()
    sa._CHERRY_CACHE.clear()
    sa._PR_STATE_SOURCE = "unprimed"
    yield
    k._ABANDONED_BRANCH_CACHE.clear()
    sa._CHERRY_CACHE.clear()


# --------------------------------------------------------------------------- #
# THE regression: a squash-merged branch is not stranded
# --------------------------------------------------------------------------- #

def test_a_branch_whose_pr_merged_is_not_stranded(monkeypatch):
    """The 36%. Its PR is MERGED, so it is landed however git spells the SHAs."""
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-1"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-1"] = True  # PR merged

    def _boom(*a, **kw):
        raise AssertionError("must not run git for an already-merged PR")

    monkeypatch.setattr(sa.subprocess, "run", _boom)
    assert sa._stranded_git_check("t-1", "main", refs=[], merged=set()) == (True, 0)


def test_the_audit_and_the_gate_agree_on_the_squash_case(monkeypatch):
    """Parity, stated as one assertion: neither may call it unmerged."""
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-2"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-2"] = True

    _, audit_unmerged = sa._stranded_git_check("t-2", "main", refs=[], merged=set())
    gate_unmerged = k._branch_has_unmerged_commits("t-2")
    assert audit_unmerged == 0
    assert gate_unmerged is False
    assert bool(audit_unmerged) == gate_unmerged


# --------------------------------------------------------------------------- #
# It must still find genuinely stranded work
# --------------------------------------------------------------------------- #

def test_an_unmerged_branch_with_no_pr_is_still_stranded(monkeypatch):
    """The guard must not become "nothing is ever stranded"."""
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-3"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-3"] = False  # no PR

    class _R:
        returncode = 0
        stdout = "+ aaaaaaa\n+ bbbbbbb\n- ccccccc\n"

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **kw: _R())
    exists, n = sa._stranded_git_check("t-3", "main", refs=[], merged=set())
    assert (exists, n) == (True, 2), "only '+' lines count; '-' is patch-equivalent upstream"


def test_a_task_with_no_branch_is_not_stranded(monkeypatch):
    monkeypatch.setattr(k, "_branches_for_task", lambda tid, root, refs=None: [])
    assert sa._stranded_git_check("t-4", "main", refs=[], merged=set()) == (False, 0)


# --------------------------------------------------------------------------- #
# exists is computed BEFORE the merged filter
# --------------------------------------------------------------------------- #

def test_a_merged_branch_still_counts_as_existing(monkeypatch):
    """Otherwise a `validating` row moves from one false finding to another.

    audit_stranded_tasks classifies (exists=False, validating) as
    orphan_validating. If the merged filter erased existence, every validating
    row whose work landed would be reported stuck-with-no-branch instead.
    """
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-5"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-5"] = True
    exists, n = sa._stranded_git_check("t-5", "main", refs=[], merged=set())
    assert exists is True and n == 0


# --------------------------------------------------------------------------- #
# The accelerators may not change an answer
# --------------------------------------------------------------------------- #

def test_the_merged_set_short_circuits_without_running_git(monkeypatch):
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-6"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-6"] = False

    def _boom(*a, **kw):
        raise AssertionError("a ref in the merged set must not be compared")

    monkeypatch.setattr(sa.subprocess, "run", _boom)
    assert sa._stranded_git_check(
        "t-6", "main", refs=[], merged={"kanban/t-6"}) == (True, 0)


def test_the_cherry_result_is_memoised_per_ref(monkeypatch):
    """Same ref, two tasks (a parent matches its child's branch) -> one compare."""
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/shared"])
    k._ABANDONED_BRANCH_CACHE["kanban/shared"] = False
    calls = []

    class _R:
        returncode = 0
        stdout = "+ aaaaaaa\n"

    def _run(argv, **kw):
        calls.append(argv)
        return _R()

    monkeypatch.setattr(sa.subprocess, "run", _run)
    sa._stranded_git_check("p-1", "main", refs=[], merged=set())
    sa._stranded_git_check("p-2", "main", refs=[], merged=set())
    assert len(calls) == 1, "the second lookup must come from _CHERRY_CACHE"


def test_a_failed_compare_is_not_cached(monkeypatch):
    """A transient git error must not pin 'clean' for the rest of the run."""
    monkeypatch.setattr(k, "_branches_for_task",
                        lambda tid, root, refs=None: ["kanban/t-7"])
    k._ABANDONED_BRANCH_CACHE["kanban/t-7"] = False

    class _Fail:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **kw: _Fail())
    sa._stranded_git_check("t-7", "main", refs=[], merged=set())
    assert "kanban/t-7" not in sa._CHERRY_CACHE


# --------------------------------------------------------------------------- #
# Unmeasured must never read as clean
# --------------------------------------------------------------------------- #

def test_a_truncated_pr_listing_does_not_prime_negatives(monkeypatch):
    """Absent-from-a-truncated-list means UNKNOWN, not "has no PR".

    Priming those as not-abandoned would manufacture the very false strandings
    this change removes.
    """
    class _R:
        returncode = 0
        stdout = '[{"headRefName":"a","state":"MERGED"},{"headRefName":"b","state":"OPEN"}]'

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **kw: _R())
    sa.prime_pr_state_cache(refs=["kanban/never-seen"], limit=2)  # 2 entries >= limit
    assert sa._PR_STATE_SOURCE == "truncated"
    assert "kanban/never-seen" not in k._ABANDONED_BRANCH_CACHE


def test_a_complete_listing_primes_negatives(monkeypatch):
    class _R:
        returncode = 0
        stdout = '[{"headRefName":"a","state":"MERGED"}]'

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **kw: _R())
    sa.prime_pr_state_cache(refs=["kanban/never-seen", "origin/kanban/other"], limit=500)
    assert sa._PR_STATE_SOURCE == "bulk"
    assert k._ABANDONED_BRANCH_CACHE["kanban/never-seen"] is False
    assert k._ABANDONED_BRANCH_CACHE["a"] is True
    assert "kanban/other" in k._ABANDONED_BRANCH_CACHE, "origin/ prefix must be stripped"


def test_gh_unavailable_reports_the_posture(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("gh not found")

    monkeypatch.setattr(sa.subprocess, "run", _boom)
    assert sa.prime_pr_state_cache(refs=["x"]) == 0
    assert sa._PR_STATE_SOURCE == "unavailable"


def test_the_report_states_the_pr_state_source():
    """A reader must be able to tell a measured run from an unmeasurable one."""
    class _Conn:
        def execute(self, *a, **kw):
            class _C:
                def fetchall(self_inner):
                    return []
            return _C()

    rep = sa.audit_stranded_tasks(conn=_Conn(), git_check=lambda t: (False, 0), fetch=False)
    assert rep["pr_state_source"] == "injected"


# --------------------------------------------------------------------------- #
# Card volume is bounded, and says so
# --------------------------------------------------------------------------- #

def test_card_filing_is_capped_and_reports_what_it_deferred(monkeypatch):
    """A cap that reads as "that was all of them" is a silent truncation."""
    findings = {
        "default_branch": "main",
        "stranded": [{"id": f"t-{i}", "status": "done", "title": "x",
                      "unmerged_commits": 1, "branch": f"kanban/t-{i}"}
                     for i in range(sa._MAX_CARDS_PER_RUN + 12)],
        "orphan_validating": [],
    }
    seen = {}
    import tools.kanban.task_factory as tf
    monkeypatch.setattr(tf, "create_tasks",
                        lambda specs: seen.setdefault("n", len(specs)) or [])
    sa._file_suggested_cards(findings)
    assert seen["n"] == sa._MAX_CARDS_PER_RUN
    assert findings["cards_deferred"] == 12, "the remainder must be reported, not dropped"


def test_under_the_cap_nothing_is_deferred(monkeypatch):
    findings = {"default_branch": "main",
                "stranded": [{"id": "t-1", "status": "done", "title": "x",
                              "unmerged_commits": 1, "branch": "kanban/t-1"}],
                "orphan_validating": []}
    import tools.kanban.task_factory as tf
    monkeypatch.setattr(tf, "create_tasks", lambda specs: [])
    sa._file_suggested_cards(findings)
    assert "cards_deferred" not in findings


# --------------------------------------------------------------------------- #
# The seam the gate exposes for this
# --------------------------------------------------------------------------- #

def test_branches_for_task_accepts_a_supplied_ref_list(monkeypatch):
    """The audit pays for one ref listing, not one per task."""
    def _boom(*a, **kw):
        raise AssertionError("must not shell out when refs are supplied")

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", _boom)
    out = k._branches_for_task(
        "abc-01", "/nonexistent",
        refs=["kanban/abc-01", "origin/kanban/abc-01", "kanban/unrelated"])
    assert out == ["kanban/abc-01", "origin/kanban/abc-01"]


def test_supplying_refs_does_not_change_matching_semantics():
    """Same boundary rule as the fresh-listing path: abc-01 must not match abc-01x."""
    refs = ["kanban/abc-01", "kanban/abc-01x", "kanban/abc-01-d1", "feature/abc-01"]
    out = k._branches_for_task("abc-01", "/nonexistent", refs=refs)
    assert "kanban/abc-01x" not in out
    assert "kanban/abc-01" in out
    assert "kanban/abc-01-d1" in out, "a child's branch still blocks its parent"
    assert "feature/abc-01" in out, "non-kanban prefixes count too"
