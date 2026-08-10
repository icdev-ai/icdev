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
