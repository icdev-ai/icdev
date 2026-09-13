#!/usr/bin/env python3
# CUI // SP-CTI
"""The artifact-pin pair is DECLARED, and declaring it cannot resurrect a deletion (kpr-watch-20).

WHAT IS AND IS NOT PINNED HERE. The historical replay -- "would the union have
reproduced the six hand resolutions?" -- lives in
`tools/kanban/artifact_pin_union_survey.py` and is NOT asserted in this file.
The gated pytest run checks out at depth 1 (`test-shard` in
.github/workflows/icdev-ci.yml), so there is no merge history in it: a test
replaying `origin/main` would report UNMEASURABLE on every CI run and pass,
which is a perfect score over an empty denominator (rem-hyg-13). What IS pinned
here is everything that needs no history -- the declaration itself, and the
behaviour of the SHIPPED resolver on the REAL bytes of these two files.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import artifact_pin_union_survey as SURVEY  # noqa: E402
from tools.kanban.union_resolver import (  # noqa: E402
    UnionRefused,
    load_declared_rules,
    match_declaration,
    merge_three_way,
)

PAIR = ("args/pinned_artifacts.yaml", "tests/airgap/test_artifact_freshness.py")
RULES = ["keep_both_blocks", "adjacent_edits"]


def _declarations():
    return load_declared_rules().get("files") or []


def _real(rel: str):
    return (ROOT / rel).read_text(encoding="utf-8").splitlines(keepends=True)


def _find_block(lines, opener: str, length: int):
    """(lo, hi) of a real, semantically whole block starting at `opener`.

    Deleting an arbitrary slice would prove the same mechanism, but the point
    of the deletion test is that a REMOVED PIN and a REMOVED TEST cannot come
    back, so the slice is one.
    """
    for idx, line in enumerate(lines):
        if line.startswith(opener):
            return idx, min(idx + length, len(lines))
    raise AssertionError(f"no block starting {opener!r} in the file under test")


# ── 1. the declaration itself ───────────────────────────────────────────────
@pytest.mark.parametrize("rel", PAIR)
def test_the_artifact_pin_pair_is_declared_with_the_append_shaped_rules(rel):
    """Both files carry the rules CLAUDE.md carries, for the same reason."""
    decl = match_declaration(rel, _declarations())
    assert decl is not None, f"{rel} matches no union_resolver.files entry"
    assert list(decl.get("rules") or []) == RULES


@pytest.mark.parametrize("rel", PAIR)
def test_the_declared_pair_is_the_pair_the_survey_measures(rel):
    """The declaration and the survey's default population cannot drift apart:
    a file declared on an unmeasured shape is the defect this card exists to
    avoid, and a file measured but undeclared buys nothing."""
    assert rel in SURVEY.DEFAULT_FILES
    assert list(SURVEY.DEFAULT_RULES) == RULES


# ── 2. the deletion case, ASSERTED rather than inherited ────────────────────
@pytest.mark.parametrize("rel,opener,length", [
    ("args/pinned_artifacts.yaml", "  - name: opensearch", 6),
    ("tests/airgap/test_artifact_freshness.py",
     "def test_the_shipped_postgres_pin", 6),
])
def test_an_empty_side_over_a_non_empty_base_still_refuses_on_these_files(
        rel, opener, length):
    """mfx-mrg-08 made the empty-side rung consult `base_seg`; this asserts it
    ON THESE FILES rather than inheriting it.

    One side DELETES a real pin entry / a real pinning test, the other edits
    the same lines. If the union yielded to the non-empty side the deletion
    would be silently discarded and the removed pin -- or the removed test --
    would be resurrected into a commit neither side wrote. It must refuse and
    let a human see the conflict.
    """
    base = _real(rel)
    lo, hi = _find_block(base, opener, length)
    assert base[lo:hi], "the block under test must be non-empty"

    deleting_side = base[:lo] + base[hi:]
    editing_side = base[:lo] + ["# edited by the other branch\n"] + base[lo + 1:]

    for main, card in ((deleting_side, editing_side), (editing_side, deleting_side)):
        with pytest.raises(UnionRefused):
            merge_three_way(base, main, card, RULES)


@pytest.mark.parametrize("rel", PAIR)
def test_the_refusal_is_about_the_deletion_and_not_about_the_file(rel):
    """The control for the test above. Two sides APPENDING at the same point in
    the same file union cleanly, so the refusal is the deletion, not a file the
    rules cannot handle at all."""
    base = _real(rel)
    at = len(base) // 2
    main = base[:at] + ["# main appended this\n"] + base[at:]
    card = base[:at] + ["# the card appended this\n"] + base[at:]
    merged, notes = merge_three_way(base, main, card, RULES)
    text = "".join(merged)
    assert "# main appended this\n" in text
    assert "# the card appended this\n" in text
    assert text.index("# main appended this") < text.index("# the card appended this")
    assert any(n.startswith("keep_both_blocks") for n in notes)


# ── 3. why the wheel mirror is NOT declared ─────────────────────────────────
MIRROR = "icdev/data/args/floci_runtime_images.yaml"


def test_the_floci_wheel_mirror_and_its_source_are_not_declared():
    """Considered and refused (kpr-watch-20 criterion 4). It conflicted on
    #2269 only because the mirror was STALE; `sync_package_tree.py` regenerates
    it from args/floci_runtime_images.yaml, which merged clean in 4 of 4."""
    for rel in (MIRROR, "args/floci_runtime_images.yaml"):
        assert match_declaration(rel, _declarations()) is None, (
            f"{rel} is declared; kpr-watch-20 measured that it must not be")


def test_unioning_two_inserts_of_the_same_yaml_key_ships_a_duplicate_key():
    """The MEASURED reason the mirror is not declared.

    Its one conflict is an add/add of the SAME `note:` key under the SAME
    entry. `keep_both_blocks` keeps both, and `yaml.safe_load` accepts a
    duplicate mapping key silently (last wins) -- so the rung's YAML verifier
    would PASS and ship a declaration of record carrying two contradictory
    notes. That is exactly how the wrong rule shipped a broken file twice on
    2026-09-03, in a form no verifier catches.
    """
    base = ["images:\n", "  - ref: mysql\n", "    tag: \"8.0.36\"\n"]
    main = base[:3] + ["    note: \"main's measurement\"\n"]
    card = base[:3] + ["    note: \"the card's measurement\"\n"]
    merged, _ = merge_three_way(base, main, card, RULES)
    text = "".join(merged)
    assert text.count("    note:") == 2

    seen = []

    class _Dup(yaml.SafeLoader):
        pass

    def _mapping(loader, node, deep=False):
        keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
        seen.extend(k for k in keys if keys.count(k) > 1)
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    _Dup.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
    yaml.load(text, Loader=_Dup)          # the verifier's check: it PASSES
    assert "note" in seen, "the duplicate key this test exists to name is gone"


# ── 4. the survey's own honesty rails ───────────────────────────────────────
def test_lost_content_is_none_and_never_zero_when_nothing_was_measured():
    """"No hunk lost content" and "no hunk was measured" justify opposite
    decisions, so they must never share a spelling."""
    report = SURVEY.survey(files=(), rules=RULES)
    assert report["measurable"] is False
    assert report["lost_content"] is None
    assert report["hunks"] is None
    assert report["agreeing"] is None


def test_a_hunk_the_human_rewrote_is_not_filed_as_lost_union_content():
    """Provenance, which a whole-file verdict cannot ask. A line that landed,
    is absent from the union and appears on NO side was written by the human
    DURING the resolution -- the union could not have produced it."""
    landed = ["kept\n", "the human wrote this while resolving\n"]
    union = ["kept\n"]
    sides = ["kept\n"]
    assert SURVEY._classify(landed, union, sides)["verdict"] == "human_also_edited"

    # ... and a line that DID come from a side is the finding, not prose.
    sides_with_it = ["kept\n", "the human wrote this while resolving\n"]
    out = SURVEY._classify(landed, union, sides_with_it)
    assert out["verdict"] == "union_lost_content"
    assert out["lost"] == 1


def test_a_region_that_cannot_be_anchored_uniquely_is_unmeasured_not_clean():
    """An anchor that matched twice would silently compare the wrong region and
    call the result agreement."""
    doc = ["a\n", "x\n", "a\n", "x\n"]
    assert SURVEY._anchored_region(doc, ["a\n"], ["x\n"]) is None
    assert SURVEY._anchored_region(["p\n", "mid\n", "q\n"], ["p\n"], ["q\n"]) == ["mid\n"]


def test_the_recorded_refusal_reason_parses_back_to_its_file_set():
    """Verbatim from `audit_trail.details` for #2270 -- the replay reads this
    string, so a change to the watcher's wording must break a test and not a
    measurement nobody re-ran."""
    reason = ("refused: files=['args/pinned_artifacts.yaml', "
              "'tests/airgap/test_artifact_freshness.py'] rules=[] verifiers=[] "
              "-- undeclared: args/pinned_artifacts.yaml matches no "
              "union_resolver.files entry")
    assert SURVEY.refusal_files(reason) == list(PAIR)
    assert all(match_declaration(f, _declarations()) is not None
               for f in SURVEY.refusal_files(reason))


def test_a_partial_set_still_counts_as_still_refused():
    """kpr-watch-14 measured that a PARTIAL resolution buys zero, so a refusal
    only becomes a candidate when EVERY file in its set is declared."""
    rows = [
        {"task_id": "t1", "reason": "refused: files=['args/pinned_artifacts.yaml']"},
        {"task_id": "t2", "reason": ("refused: files=['args/pinned_artifacts.yaml', "
                                     "'tools/security/row_security.py']")},
    ]
    rep = SURVEY.replay_refusals(_declarations(), rows)
    assert rep["now_declared"] == 1
    assert rep["still_refused"] == 1
    assert rep["undeclared_paths"] == {"tools/security/row_security.py": 1}


def test_an_unreachable_board_is_unmeasurable_and_never_an_empty_corpus(monkeypatch):
    """A fresh worktree and an ephemeral CI database have no refusal history.
    Reporting that as "0 refusals, nothing left to buy" is the fabrication the
    liveness gates already refuse to make."""
    monkeypatch.setattr(SURVEY, "_load_refusal_rows", lambda: None)
    rep = SURVEY.replay_refusals(_declarations())
    assert rep["measurable"] is False
    assert rep["rows"] is None
    assert rep["now_declared"] is None
    assert rep["share_now_declared"] is None
