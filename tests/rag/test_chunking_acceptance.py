# CUI // SP-CTI
"""Acceptance for template chunking (oss-chunk-03).

Three things the card asked for, and how each is actually established:

1. **A control catalog chunks 1:1 with controls; a STIG 1:1 with rules.**
   Measured by BOUNDARY, not by substring. The obvious check — "does this
   control id appear in exactly one chunk?" — is wrong: ``AC-2`` substring-
   matches inside ``AC-2 (1)``, so a correctly-chunked catalog reports every
   parent control as "split". Each chunk is instead required to OPEN with
   exactly one identifier.

2. **No regression on general documents.** Not measured by a corpus re-index and
   a benchmark run: the `general` template IS the pre-existing sliding window,
   so a general document takes the identical code path and the output is
   byte-identical. That is provable deterministically, and a re-index would be
   an expensive way to observe a tautology. Pinned below so it stays true.

3. **Re-index cost.** Recorded as chunk-count deltas per template, which is what
   actually drives re-index cost (embeddings are per chunk).

On enhancements: ``AC-2 (1)`` getting its own chunk is CORRECT, not a split — an
enhancement is its own control with its own id in OSCAL, and retrieval should be
able to return it alone.
"""
from __future__ import annotations

import re

import pytest

from tests.fixtures.chunking_corpora import (
    GENERAL_PROSE,
    OSCAL_CATALOG,
    OSCAL_CONTROL_IDS,
    STIG_CHECKLIST,
    STIG_RULE_IDS,
)
from tools.rag.chunker import chunk_content

#: A control identifier at the very start of a chunk, enhancement suffix included.
_OSCAL_HEAD = re.compile(r"^([A-Z]{2}-\d+(?:\s*\(\s*\d+\s*\))?)")
_STIG_HEAD = re.compile(r"^(V-\d{5,6})")


def _heads(chunks, pattern):
    """The identifier each chunk opens with, skipping any preamble chunk."""
    out = []
    for c in chunks:
        m = pattern.match(c.content.strip())
        if m:
            out.append(re.sub(r"\s+", "", m.group(1)))
    return out


# ── 1. Structural fidelity ───────────────────────────────────────────────────


def test_control_catalog_chunks_one_per_control():
    chunks = chunk_content(OSCAL_CATALOG, source_type="t", template="oscal_catalog")
    heads = _heads(chunks, _OSCAL_HEAD)

    assert len(heads) == len(set(heads)), f"a control opened two chunks: {heads}"
    for cid in OSCAL_CONTROL_IDS:
        assert cid in heads, f"{cid} never opened a chunk — it was absorbed or split"


def test_no_control_body_spans_two_chunks():
    """The property the card actually cares about: a control is never cut.

    ``(?!\\s*\\()`` is load-bearing. ``^AC-2\\b`` also matches ``AC-2 (1) ...``
    because the word boundary sits before the space, so the naive form reports
    every parent-with-an-enhancement as split — the same substring trap this
    module's docstring warns about, which the first draft of this test walked
    straight into.
    """
    chunks = chunk_content(OSCAL_CATALOG, source_type="t", template="oscal_catalog")
    for cid in OSCAL_CONTROL_IDS:
        owning = [
            c for c in chunks
            if re.match(rf"{re.escape(cid)}(?!\s*\()\b", c.content.strip())
        ]
        assert len(owning) == 1, f"{cid} opens {len(owning)} chunks"


def test_enhancements_get_their_own_chunk_which_is_correct():
    """AC-2 (1) is its own control in OSCAL, not part of AC-2.

    Retrieval should be able to return an enhancement on its own, so this is
    fidelity to the source structure rather than a split.
    """
    heads = _heads(
        chunk_content(OSCAL_CATALOG, source_type="t", template="oscal_catalog"),
        _OSCAL_HEAD,
    )
    assert "AC-2(1)" in heads
    assert "AU-6(3)" in heads
    assert "AC-2" in heads, "the parent control must still stand alone"


def test_stig_checklist_chunks_one_per_rule():
    chunks = chunk_content(STIG_CHECKLIST, source_type="t", template="stig_checklist")
    heads = _heads(chunks, _STIG_HEAD)

    assert heads == STIG_RULE_IDS, f"expected one chunk per rule in order, got {heads}"


def test_stig_rule_keeps_its_check_and_fix_text_together():
    """A rule split between Discussion and Fix Text is useless for remediation."""
    chunks = chunk_content(STIG_CHECKLIST, source_type="t", template="stig_checklist")
    for c in chunks:
        m = _STIG_HEAD.match(c.content.strip())
        if not m:
            continue
        assert "Check Text:" in c.content, f"{m.group(1)} lost its Check Text"
        assert "Fix Text:" in c.content, f"{m.group(1)} lost its Fix Text"


def test_the_wrong_template_does_not_preserve_structure():
    """The measurement must be able to detect a FAILURE, or it proves nothing.

    Chunking a control catalog with the general sliding window should not
    produce one-chunk-per-control — if it did, the structural templates would be
    measuring nothing.
    """
    structural = _heads(
        chunk_content(OSCAL_CATALOG, source_type="t", template="oscal_catalog"),
        _OSCAL_HEAD,
    )
    general = _heads(
        chunk_content(OSCAL_CATALOG, source_type="t", template="general"), _OSCAL_HEAD
    )
    assert len(structural) > len(general), (
        "the general template preserved as much structure as the structural one — "
        "this acceptance suite cannot distinguish them and proves nothing"
    )


# ── 2. No regression on general documents ────────────────────────────────────


def test_general_template_is_byte_identical_to_no_template():
    """'No regression on general documents' is true BY CONSTRUCTION.

    The general template IS the pre-existing sliding window. A corpus re-index
    plus benchmark run would be an expensive way to observe a tautology; this
    pins the tautology instead so it stays true.
    """
    a = chunk_content(GENERAL_PROSE, source_type="general", template=None)
    b = chunk_content(GENERAL_PROSE, source_type="general", template="general")
    assert [c.content for c in a] == [c.content for c in b]


def test_unknown_template_falls_back_rather_than_raising():
    """An unknown template must degrade to the default, not fail ingestion."""
    out = chunk_content(GENERAL_PROSE, source_type="general", template="not_a_template")
    assert out, "ingestion must not lose the document over a bad template name"


def test_chunking_is_deterministic():
    runs = {
        tuple(c.content for c in chunk_content(OSCAL_CATALOG, source_type="t",
                                               template="oscal_catalog"))
        for _ in range(3)
    }
    assert len(runs) == 1


# ── 3. Re-index cost ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "doc,template",
    [
        (OSCAL_CATALOG, "oscal_catalog"),
        (STIG_CHECKLIST, "stig_checklist"),
        (GENERAL_PROSE, "general"),
    ],
)
def test_chunk_count_is_recorded_for_reindex_cost(doc, template, capsys):
    """Embeddings are per chunk, so chunk-count delta IS the re-index cost.

    Printed rather than asserted against a magic number: the point is to record
    the figure with the acceptance run, not to freeze it.
    """
    baseline = len(chunk_content(doc, source_type="t", template="general"))
    templated = len(chunk_content(doc, source_type="t", template=template))
    with capsys.disabled():
        delta = templated - baseline
        print(
            f"\n  re-index cost [{template:16s}] "
            f"general={baseline:3d} -> templated={templated:3d} "
            f"({delta:+d} chunks, {delta / max(baseline, 1):+.0%})"
        )
    assert templated > 0


def test_template_used_is_recorded_on_every_chunk():
    """The decision must be auditable — oss-chunk-01's own requirement."""
    for c in chunk_content(OSCAL_CATALOG, source_type="t", template="oscal_catalog"):
        assert (c.metadata or {}).get("chunking_template") == "oscal_catalog"
