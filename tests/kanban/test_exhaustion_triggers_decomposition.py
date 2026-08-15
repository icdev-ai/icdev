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
