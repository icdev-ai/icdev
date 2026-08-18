# CUI // SP-CTI
"""Serialising a merge queue means choosing who goes first.

hold_on_sibling_conflict exists to SERIALISE merges that touch the same source
file — merge one, let the rest rebase. It held every one of them instead: if A
shares a file with B then B shares one with A, so both were held and nothing
broke the tie.

With 14 AGOV PRs over the same new modules on 2026-08-09, every PR was a sibling
of several others. The board sat at "11 awaiting merge" with ZERO active tasks
while two of those PRs were green, mergeable and blocked only by each other.
Refusing to choose is not serialisation, it is a stall.
"""
from __future__ import annotations

import tools.ci.pr_watcher as pw


def _sibs(*numbers):
    return {f"https://x/pull/{n}": {"shared.py"} for n in numbers}


def test_the_lowest_number_goes_first():
    assert pw._wins_sibling_tiebreak("https://x/pull/1494", _sibs(1495, 1499)) is True


def test_everyone_else_waits():
    assert pw._wins_sibling_tiebreak("https://x/pull/1496", _sibs(1495, 1499)) is False


def test_exactly_one_winner_in_any_group():
    """The property that matters: the group always makes progress, and never
    merges two PRs that touch the same file at once."""
    group = [1477, 1480, 1483, 1494, 1495]
    winners = [
        n for n in group
        if pw._wins_sibling_tiebreak(
            f"https://x/pull/{n}",
            {f"https://x/pull/{o}": {"shared.py"} for o in group if o != n})
    ]
    assert winners == [min(group)], winners


def test_a_pr_with_no_siblings_is_never_held():
    assert pw._wins_sibling_tiebreak("https://x/pull/9", {}) is True
    assert pw._wins_sibling_tiebreak("https://x/pull/9", None) is True


def test_an_unreadable_url_never_wins_by_accident():
    """It sorts last, so a parse failure cannot let a PR jump the queue."""
    assert pw._wins_sibling_tiebreak("not-a-url", _sibs(1)) is False


def test_the_verdict_is_stable_across_processes():
    """Two watchers must not both decide they are first — the rule reads only the
    numbers, so it needs no coordination."""
    sibs = _sibs(1495, 1499)
    first = pw._wins_sibling_tiebreak("https://x/pull/1494", sibs)
    for _ in range(5):
        assert pw._wins_sibling_tiebreak("https://x/pull/1494", sibs) is first


# ── a blocker that cannot merge is not a queue position (kpr-watch-08) ──────
#
# The sentence at the top of this file applies one level up. A PR held behind a
# sibling that CANNOT merge is not being serialised behind it — it is waiting for
# a slot that never comes free, and the hold is recomputed every poll so the wait
# never expires. MEASURED 2026-08-17 by tools/ci/sibling_hold_survey.py: of six
# open PRs, #1769 was held behind #1744 (a draft carrying a real CLAUDE.md
# conflict, open more than a day) and #1781 behind #1773 — under the posture that
# ships today, not a hypothetical one.
def test_a_sibling_that_cannot_merge_is_dropped_from_the_tiebreak():
    sibs = _sibs(1744, 1799)
    blocked = {"https://x/pull/1744"}
    assert pw._wins_sibling_tiebreak("https://x/pull/1769", sibs) is False
    assert pw._wins_sibling_tiebreak(
        "https://x/pull/1769", sibs, blocked=blocked) is True


def test_a_blocker_that_can_merge_still_holds():
    """Narrowed, not disarmed — the queue must still form."""
    assert pw._wins_sibling_tiebreak(
        "https://x/pull/1769", _sibs(1744), blocked=set()) is False


def test_dropping_blockers_cannot_merge_two_prs_on_one_file():
    """The invariant the guard exists for. A sibling excluded here is one the
    forge would refuse anyway, so no pair of file-sharing PRs both become free."""
    group = [1744, 1769, 1781]
    blocked = {"https://x/pull/1744"}  # draft / CONFLICTING
    winners = [
        n for n in group
        if pw._wins_sibling_tiebreak(
            f"https://x/pull/{n}",
            {f"https://x/pull/{o}": {"shared.py"} for o in group if o != n},
            blocked=blocked)
    ]
    assert winners == [1744, 1769], (
        "the blocked PR still 'wins' vacuously — it cannot merge, so that is "
        "inert — and exactly one MERGEABLE PR is freed")


def test_the_exclusion_is_recomputed_not_permanent():
    """A blocker rebased back to MERGEABLE rejoins the queue and wins it."""
    sibs = _sibs(1744)
    assert pw._wins_sibling_tiebreak(
        "https://x/pull/1769", sibs, blocked={"https://x/pull/1744"}) is True
    assert pw._wins_sibling_tiebreak(
        "https://x/pull/1769", sibs, blocked=set()) is False


# ── which PRs count as unable to merge ──────────────────────────────────────
def test_a_draft_cannot_merge():
    assert pw._pr_can_merge({"draft": True, "mergeable": "MERGEABLE"}) is False


def test_a_conflicting_pr_cannot_merge():
    assert pw._pr_can_merge({"draft": False, "mergeable": "CONFLICTING"}) is False


def test_a_mergeable_pr_can():
    assert pw._pr_can_merge({"draft": False, "mergeable": "MERGEABLE"}) is True


def test_unknown_mergeability_counts_as_MERGEABLE():
    """Erring the other way drops a sibling from the tie-break on no evidence,
    and letting a PR past a hold is the direction with consequences."""
    assert pw._pr_can_merge({"draft": False, "mergeable": None}) is True
    assert pw._pr_can_merge({}) is True
