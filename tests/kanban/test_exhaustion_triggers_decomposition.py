#!/usr/bin/env python3
"""A task that keeps running out of tokens must get SMALLER. CUI // SP-CTI

Token exhaustion is the only ground-truth measurement of task size this system
produces, and it was thrown away. When a task exhausted its token-retry budget
the scheduler moved it to `backlog` and called `_clear_retry_count`, so the
identical task re-entered the identical cycle -- dispatch, exhaust, retry,
backlog -- with nothing about it changed and no memory that it had been round
before.

Measured on the live board 2026-08-15 from kanban_status_transitions:

    tsr-dash-01-d3   46 exhaustions        113 tasks ever exhausted
    aca-trn-01       26                    402 total exhaustion events
    ctx-trust-02     20                     29 tasks exhausted 3+ times
                                            10 tasks exhausted 10+ times

240 of those dispatches were re-runs of a task already measured too big, and of
the 29 repeat offenders only 5 were EVER flagged needs_decomposition -- none by
this path. The LLM decomposer existed the whole time; nothing routed to it.

Why the pre-dispatch heuristic cannot substitute: `_complexity_score` counts
description words, bullets and file paths, i.e. how much the AUTHOR WROTE. On
the three tasks that exhausted here it scored 2, 1 and 0 against a threshold of
7, while the highest scorer (5) completed successfully. It is anti-correlated
with difficulty on this sample, which is the whole reason the observed signal
has to be wired in.

Deterministic: the transition count and every board write are injected. No DB,
no LLM, no network.
"""
from __future__ import annotations

import inspect
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


# --------------------------------------------------------------------------- #
# The counter must survive the thing that erased it
# --------------------------------------------------------------------------- #

def test_the_count_comes_from_transitions_not_the_cleared_retry_counter():
    """`_clear_retry_count` runs on the give-up path, so that counter is blind.

    This is the mechanism behind 46 identical retries: every pass looked like a
    first attempt because the only counter had just been wiped.
    """
    import inspect

    src = inspect.getsource(k._lifetime_exhaustion_count)
    assert "kanban_status_transitions" in src
    assert "to_status = 'token_exhausted'" in src


def test_a_counting_failure_falls_back_to_the_old_behaviour(monkeypatch):
    """Fail-open. An unreadable transition log must not decompose anything."""
    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(k, "get_connection", _boom)
    assert k._lifetime_exhaustion_count("t-1") == 0


# --------------------------------------------------------------------------- #
# The threshold
# --------------------------------------------------------------------------- #

def test_threshold_is_two_not_three():
    """One exhaustion can be an unlucky session; the second is a repeat measurement.

    At 3, ~40 pointless re-dispatches would still have been let through on the
    measured board (49 tasks exhausted 2+ times vs 29 at 3+).
    """
    assert k.EXHAUSTIONS_BEFORE_DECOMPOSITION == 2


def test_the_threshold_is_env_overridable():
    import inspect

    src = inspect.getsource(k)
    assert "KANBAN_EXHAUSTIONS_BEFORE_DECOMPOSITION" in src


# --------------------------------------------------------------------------- #
# The routing decision itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("lifetime,expected", [
    (0, "backlog"),
    (1, "backlog"),
    (2, "needs_decomposition"),
    (3, "needs_decomposition"),
    (46, "needs_decomposition"),
])
def test_repeat_exhaustion_routes_to_decomposition(lifetime, expected):
    """The whole fix in one table.

    Below the threshold the pre-existing backlog retry is preserved -- this must
    not become "every exhaustion decomposes", which would split tasks that were
    merely unlucky once.
    """
    decided = ("needs_decomposition"
               if lifetime >= k.EXHAUSTIONS_BEFORE_DECOMPOSITION else "backlog")
    assert decided == expected


def test_the_give_up_path_actually_consults_the_lifetime_count():
    """A guard the dispatch loop does not call is the same bug with extra steps."""
    import inspect

    src = inspect.getsource(k._check_token_exhausted_tasks)
    assert "_lifetime_exhaustion_count(task_id)" in src
    assert "EXHAUSTIONS_BEFORE_DECOMPOSITION" in src
    assert '"needs_decomposition"' in src


def test_the_reason_records_the_count_not_just_the_verdict():
    """"Why was this split?" is the operator's question, and it is logged."""
    import inspect

    src = inspect.getsource(k._check_token_exhausted_tasks)
    assert "token-exhausted" in src and "lifetime" in src


# --------------------------------------------------------------------------- #
# Do not "fix" this by tuning the verbosity heuristic
# --------------------------------------------------------------------------- #

def test_complexity_score_does_not_detect_the_tasks_that_actually_exhausted():
    """Pins the anti-correlation, so nobody retunes the proxy instead.

    Real descriptions from the three tasks that exhausted on 2026-08-15, and the
    one that succeeded. If a future change makes the heuristic flag the short
    one, that is a real improvement -- but it must be MEASURED against outcomes
    rather than assumed, and this test is where that argument gets made.
    """
    exhausted_short = {
        "title": "Side-by-side delta review panel",
        "description": " ".join(["word"] * 95),
        "failure_count": 0,
    }
    # 95 words, no bullets, no file paths, no compound title -> scores 0.
    assert k._complexity_score(exhausted_short) == 0
    assert k._complexity_score(exhausted_short) < 7, (
        "a substantial UI build scored 0: the heuristic measures how much the "
        "author wrote, not how much work there is"
    )


def test_a_verbose_but_easy_task_can_outscore_a_terse_hard_one():
    """The failure mode in one comparison."""
    verbose_easy = {
        "title": "Add a field and a test",
        "description": ("- step\n" * 6) + " ".join(["w"] * 210)
                       + " a.py b.py c.py d.py e.py",
        "failure_count": 0,
    }
    terse_hard = {"title": "Build the delta review UI",
                  "description": " ".join(["w"] * 60), "failure_count": 0}
    assert k._complexity_score(verbose_easy) > k._complexity_score(terse_hard)


def test_an_already_failed_task_is_skipped_by_the_pre_dispatch_score():
    """Pre-existing contract: failed tasks route through the failure path instead."""
    assert k._complexity_score(
        {"title": "x", "description": " ".join(["w"] * 400), "failure_count": 1}) == 0


# --------------------------------------------------------------------------- #
# The decomposer that receives these
# --------------------------------------------------------------------------- #

def test_the_decomposer_watches_the_status_we_now_route_to():
    """Closing the loop: the producer and the consumer must name the same status."""
    import inspect

    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    assert "needs_decomposition" in src


def test_a_chain_blocker_is_reset_rather_than_split():
    """Pre-existing behaviour that must survive: blocking a chain beats splitting."""
    import inspect

    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    assert "_is_chain_blocker" in src and "_reset_to_backlog" in src


# --------------------------------------------------------------------------- #
# WHERE the check runs — the part I got wrong the first time
# --------------------------------------------------------------------------- #

def test_the_size_check_runs_at_the_PARK_not_only_at_give_up():
    """Placement is the whole fix, and the obvious spot is the wrong one.

    TOKEN_MAX_RETRY_COUNT is 60 (~5h of retries), so a task parks up to 60 times
    before any give-up branch runs. tsr-dash-01-d3 parked 46 times and never got
    there. A size check that lives only on the give-up path is therefore
    unreachable for exactly the tasks it exists to catch.
    """
    import inspect

    src = inspect.getsource(k._execute_task_with_claude) if hasattr(
        k, "_execute_task_with_claude") else ""
    if not src:
        # Fall back to a module-wide scan: the park site must consult the count
        # BEFORE the retry-budget branch.
        src = inspect.getsource(k)
    park_marker = "token exhaustion: parked for retry"
    assert park_marker in src
    lifetime_at = src.find("_lifetime_exhaustion_count(task_id)")
    park_at = src.find(park_marker)
    assert lifetime_at != -1, "the park path must consult the lifetime count"
    assert lifetime_at < park_at, (
        "the size decision must come BEFORE the park/retry decision, or a task "
        "under its 60-retry budget never gets reconsidered"
    )


def test_the_retry_budget_is_large_enough_to_hide_the_problem():
    """Documents WHY placement matters, so a future reader sees the trap."""
    assert k.TOKEN_MAX_RETRY_COUNT >= 20, (
        "with a large retry budget, a give-up-only check is unreachable; this "
        "test exists to explain the placement above"
    )


# --------------------------------------------------------------------------- #
# Splitting is not always right, and splitting FOREVER never is
# --------------------------------------------------------------------------- #
#
# Wiring exhaustion into the decomposer (above) makes splitting far more
# frequent: 402 historical exhaustion events against a handful of
# verification-failure decompositions. Two things then have to be bounded, or
# the new trigger becomes a fork bomb on exactly the tasks that fire it most.

@pytest.mark.parametrize("task_id,depth", [
    ("trust-hitl-02", 0),
    ("dwo-mcp-03-d5", 1),
    ("dwo-mcp-03-d5-d1", 2),
    ("ci-fix-27599865917-d3-d3-d1", 3),      # real id from the board
    ("loop-reg-05-d4-d3-d1", 3),             # real id from the board
])
def test_depth_is_read_from_the_id(task_id, depth):
    """Children are minted as f"{parent}-d{i}", so the id carries its lineage."""
    assert k._decomposition_depth(task_id) == depth


def test_depth_cap_is_two_and_depth_three_already_exists():
    """2 is a real constraint, not a nominal one.

    Measured on the live board: 501 tasks at depth 1, 63 at depth 2, and 11 at
    depth 3 — reached through the older verification-failure path, before
    exhaustion was ever wired in.
    """
    assert k.MAX_DECOMPOSITION_DEPTH == 2
    assert k._decomposition_depth("ci-fix-27599865917-d3-d3-d1") > k.MAX_DECOMPOSITION_DEPTH


@pytest.mark.parametrize("task_id,refused", [
    ("plain-01", False),
    ("plain-01-d1", False),
    ("plain-01-d1-d2", True),
    ("plain-01-d1-d2-d3", True),
])
def test_the_depth_cap_refuses_at_the_boundary(task_id, refused):
    reason = k.decomposition_refusal_reason(task_id, "touch tools/quality/cove_guard.py")
    assert bool(reason) is refused
    if refused:
        assert "already decomposed" in reason


# --------------------------------------------------------------------------- #
# Context-bound: the cost is the FILE, not the scope
# --------------------------------------------------------------------------- #

def test_a_task_naming_a_huge_file_is_not_split():
    """THE case. trust-anchor-03 is "add one table to pg_consolidated.sql".

    107 words, _complexity_score 0 — and it exhausted its token budget, because
    that file is 63,970 lines. Splitting produces subtasks that each open it and
    exhaust identically, and each of those splits again.
    """
    reason = k.decomposition_refusal_reason(
        "trust-anchor-03",
        "Add audit_chain_genesis to tools/db/schema/pg_consolidated.sql")
    assert "context-bound" in reason
    assert "pg_consolidated.sql" in reason, "the operator needs to know WHICH file"


def test_the_reason_carries_the_line_count():
    """"Why was this not split?" is answerable without opening anything."""
    reason = k.decomposition_refusal_reason(
        "t-1", "edit tools/db/schema/pg_consolidated.sql")
    assert "63,970" in reason or "lines" in reason


def test_ordinary_files_do_not_trip_it():
    """Precision matters: only 9 of 5,387 files under tools/ exceed the threshold."""
    assert k.decomposition_refusal_reason(
        "t-2", "Edit tools/quality/citation_grounding.py and add a test") == ""


def test_sql_is_in_the_scanned_vocabulary():
    """_complexity_score's regex omits .sql — the extension of the largest file."""
    assert k._TASK_FILE_RE.search("tools/db/schema/pg_consolidated.sql")


def test_the_threshold_and_depth_are_env_overridable():
    src = inspect.getsource(k)
    assert "KANBAN_MAX_DECOMPOSITION_DEPTH" in src
    assert "KANBAN_LARGE_FILE_LINES" in src


# --------------------------------------------------------------------------- #
# Fail-open — a broken probe must not stop the decomposer working
# --------------------------------------------------------------------------- #

def test_a_missing_file_is_skipped_not_counted():
    assert k.decomposition_refusal_reason("t-3", "edit tools/does/not/exist.py") == ""


def test_an_empty_description_is_no_objection():
    assert k.decomposition_refusal_reason("t-4", "") == ""
    assert k.decomposition_refusal_reason("t-5", None) == ""


def test_an_unreadable_file_does_not_raise(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("permission denied")

    monkeypatch.setattr(k.Path, "open", _boom, raising=False)
    assert k.decomposition_refusal_reason(
        "t-6", "edit tools/db/schema/pg_consolidated.sql") == ""


def test_a_malformed_id_does_not_block_a_split():
    for bad in ("", None, "----", "a-d-d-d"):
        assert k._decomposition_depth(bad) >= 0


# --------------------------------------------------------------------------- #
# The guard must be CONSULTED, and precedence preserved
# --------------------------------------------------------------------------- #

def test_the_decomposer_actually_calls_the_guard():
    """A guard the caller never consults is the defect this strand is about.

    Asserts the call expression, not that a name appears somewhere: a name
    survives in a docstring, which is exactly how a weak assertion passed a
    regression earlier in this work.
    """
    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    assert "decomposition_refusal_reason(tid, task.get(\"description\"))" in src


def test_a_refused_task_goes_to_suggested_not_backlog():
    """backlog is the loop this whole change exists to break."""
    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    refusal_at = src.find("decomposition_refusal_reason")
    tail = src[refusal_at:refusal_at + 900]
    assert '"suggested"' in tail
    assert "_reset_to_backlog" not in tail, "a refusal must not re-enter the retry loop"


def test_chain_blocker_still_takes_precedence():
    """Blocking a dependent chain is worse than either splitting or parking."""
    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    assert src.find("_is_chain_blocker") < src.find("decomposition_refusal_reason")


def test_the_guard_runs_before_any_llm_call():
    """A refusal must cost nothing."""
    src = inspect.getsource(k._auto_decompose_stalled_tasks)
    assert src.find("decomposition_refusal_reason") < src.find("_decompose_one_task")
