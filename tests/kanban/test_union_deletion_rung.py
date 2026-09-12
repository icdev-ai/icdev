#!/usr/bin/env python3
# CUI // SP-CTI
"""The empty-side rung must not silently DISCARD A DELETION (mfx-mrg-08).

`union_resolver._resolve_cluster` opened with two lines that ran before any
declared rule and never looked at `base_seg`:

    if not main_seg and card_seg:  return card_seg, other_side_when_empty
    if not card_seg and main_seg:  return main_seg, other_side_when_empty

An empty side was read as "this branch had nothing to say here", which is also
exactly what "this branch DELETED these lines" looks like. `keep_both_blocks`
and `table_rows` both open `if base_seg: return None` -- "both REWROTE existing
lines, not two appends" -- and the universal rung, running FIRST and refusing
nothing, did not. `resolve_index_conflicts`' invariant is "a human sees the
conflict, not a half-resolution", and a discarded deletion is worse than a half
resolution because it looks complete.

THE RUNG IS NARROWED, NOT REMOVED, so the intended case is asserted in BOTH
directions beside the defect: over an EMPTY base a side with nothing to say
still yields to the other, and `other_side_when_empty` is still the rule that
says so.

The byte-identity pins below are the structural half of the same claim: this
change touched ONE cluster-deciding function, and the four declared rules plus
the three-way engine are unchanged. They are pinned by SOURCE HASH rather than
by a git diff because the gated pytest shards check out at depth 1 (icdev-ci.yml
-- "no step in this job reads git history"), so a merge-base comparison is
UNMEASURABLE exactly where this test runs. Updating a hash is then a deliberate
one-line act in the commit that changes a rule, which is the property wanted:
twice a wrong union rule shipped a broken file, and neither time was the change
to the rule the thing anybody was looking at.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import pathlib
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban import union_resolver as ur  # noqa: E402

RULES = ["keep_both_blocks", "adjacent_edits"]


# -- the defect ---------------------------------------------------------------
# base holds two lines; MAIN rewrites the first of them; the CARD deletes both.
# Every equality short-circuit in `merge_three_way` misses this, so the hunk
# reaches `_resolve_cluster` with `card_seg` empty over a NON-EMPTY base.
_BASE = ["a\n", "OLD ONE\n", "OLD TWO\n", "z\n"]
_MAIN_REWROTE = ["a\n", "NEW ONE\n", "OLD TWO\n", "z\n"]
_CARD_DELETED = ["a\n", "z\n"]


def test_card_side_deletion_is_not_silently_restored():
    """The card removed the lines; restoring them from main discards that."""
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(_BASE, _MAIN_REWROTE, _CARD_DELETED, rules=RULES)


def test_main_side_deletion_is_not_silently_restored():
    """The mirror image: MAIN removed the lines and the card rewrote them."""
    with pytest.raises(ur.UnionRefused):
        ur.merge_three_way(_BASE, _CARD_DELETED, _MAIN_REWROTE, rules=RULES)


def test_the_refusal_names_the_deletion():
    """A human reading the escalation must see WHY, not only that it refused."""
    with pytest.raises(ur.UnionRefused) as exc:
        ur.merge_three_way(_BASE, _MAIN_REWROTE, _CARD_DELETED, rules=RULES)
    assert "deleted" in str(exc.value).lower()


def test_the_hunk_really_reaches_the_rung():
    """A control: without this the two tests above could pass for a hunk the
    early equality branches disposed of, which would prove nothing about the
    rung. Asserted by asking `_resolve_cluster` DIRECTLY."""
    with pytest.raises(ur.UnionRefused):
        ur._resolve_cluster(["OLD ONE\n", "OLD TWO\n"], ["NEW ONE\n", "OLD TWO\n"],
                            [], RULES, 1)
    with pytest.raises(ur.UnionRefused):
        ur._resolve_cluster(["OLD ONE\n", "OLD TWO\n"], [],
                            ["NEW ONE\n", "OLD TWO\n"], RULES, 1)


# -- the intended case, narrowed and not removed ------------------------------
def test_empty_base_empty_main_still_yields_to_the_card():
    got, rule = ur._resolve_cluster([], [], ["INSERTED\n"], RULES, 0)
    assert got == ["INSERTED\n"]
    assert rule == ur.RULE_OTHER_SIDE_WHEN_EMPTY


def test_empty_base_empty_card_still_yields_to_main():
    got, rule = ur._resolve_cluster([], ["INSERTED\n"], [], RULES, 0)
    assert got == ["INSERTED\n"]
    assert rule == ur.RULE_OTHER_SIDE_WHEN_EMPTY


@pytest.mark.parametrize("swap", [False, True])
def test_a_pure_one_sided_insertion_still_merges(swap):
    """End to end, both directions: the side that inserted keeps its block."""
    base = ["a\n", "b\n"]
    inserted = ["a\n", "X\n", "b\n"]
    main, card = (base, inserted) if not swap else (inserted, base)
    merged, _notes = ur.merge_three_way(base, main, card, rules=RULES)
    assert merged == inserted


def test_two_sided_append_is_untouched():
    """`keep_both_blocks` over an empty base still keeps both, in order."""
    base = ["a\n"]
    merged, _notes = ur.merge_three_way(base, ["a\n", "M\n"], ["a\n", "C\n"], rules=RULES)
    assert merged == ["a\n", "M\n", "C\n"]


# -- the structural half: one function changed --------------------------------
#: sha256 of the SOURCE of each function this card must not have touched.
#: Regenerate deliberately, in the commit that changes the function:
#:   python -c "import inspect,hashlib;from tools.kanban import union_resolver as u;\
#:     print(hashlib.sha256(inspect.getsource(u._rule_table_rows).encode()).hexdigest())"
_PINNED = {
    "_rule_keep_both_blocks": "c901fc94e0a9396520a019bedcc0744b0ce592cfce38cbced8f0b9c3859e2648",
    "_rule_table_rows": "9d56e879ceeab6ae1e2138c1f6b8420f2bfed4637d0e934217a89e2f7324a012",
    "_rule_quoted_list_line": "68bcebd15058956db4beae59f53e27914eb78294818339085c5e1479e1567f74",
    "_union_quoted": "d73e1997320001582fa377587eb21bce290b8a0be801b8dcefcc57c63ca3bb03",
    "merge_three_way": "98fcafe05cd3dc2c059e4db0c07a971152f0c456a830807eede48830d07ed525",
    "_clusters": "8acf8644385fadcb37161c7a26db54e12ee22fb1caa4d81d1f027722c8fb11bc",
    "_map_index": "f0e2a505f6ffbba0f5478e2b16d8734c994d5a003b43f6d0636aec11e8c402c9",
    "_touches_at_seam": "96d0f62bb3edf92139f0ab81beb16ce89c9459098fbea588c0e3d1a2bc65fe6e",
}


def _source_sha(name: str) -> str:
    return hashlib.sha256(
        inspect.getsource(getattr(ur, name)).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", sorted(_PINNED))
def test_only_resolve_cluster_changed(name):
    assert _source_sha(name) == _PINNED[name], (
        f"{name} changed. Every declared rule and the three-way engine are pinned; "
        f"mfx-mrg-08 narrowed `_resolve_cluster` and nothing else. If the change is "
        f"deliberate, update the hash in this test in the same commit.")


def test_the_pin_actually_discriminates():
    """A positive control. A hash test that hashes nothing also reports clean."""
    src = inspect.getsource(ur._rule_keep_both_blocks)
    mutated = src.replace("return None", "return []", 1)
    assert mutated != src
    assert hashlib.sha256(mutated.encode("utf-8")).hexdigest() != _source_sha(
        "_rule_keep_both_blocks")


def test_the_rule_registry_is_unchanged():
    """The five rule NAMES and the declarable set are what `--list-rules` prints."""
    assert set(ur.RULE_DESCRIPTIONS) == {
        ur.RULE_OTHER_SIDE_WHEN_EMPTY, ur.RULE_KEEP_BOTH_BLOCKS, ur.RULE_TABLE_ROWS,
        ur.RULE_QUOTED_LIST_LINE, ur.RULE_ADJACENT_EDITS,
    }
    assert ur.DECLARABLE_RULES == frozenset({
        ur.RULE_KEEP_BOTH_BLOCKS, ur.RULE_TABLE_ROWS, ur.RULE_QUOTED_LIST_LINE,
        ur.RULE_ADJACENT_EDITS,
    })
    assert set(ur._CLUSTER_RULES) == {
        ur.RULE_KEEP_BOTH_BLOCKS, ur.RULE_TABLE_ROWS, ur.RULE_QUOTED_LIST_LINE,
    }


def test_resolve_cluster_consults_the_base():
    """AST: the rung's early return is GUARDED by `base_seg`. A behavioural test
    alone would pass for an edit that special-cased this fixture."""
    tree = ast.parse(pathlib.Path(ur.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_resolve_cluster")
    returns_rung = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Return) and "RULE_OTHER_SIDE_WHEN_EMPTY" in ast.dump(n)
    ]
    assert returns_rung, "the rung was removed, not narrowed"
    for node in returns_rung:
        guards = [a for a in ast.walk(fn)
                  if isinstance(a, ast.If) and "base_seg" in ast.dump(a.test)
                  and node in list(ast.walk(a))]
        assert guards, "an other_side_when_empty return that no base_seg test guards"
