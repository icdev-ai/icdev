#!/usr/bin/env python3
"""docgen -> DIC bridge must scrub leaked reasoning — CUI // SP-CTI.

`dic_sections` has two write paths:

  * `doc_generator.generate_document` — scrubs CoT/CoD scaffolding at
    doc_generator.py:933 before INSERT, and writes citations_json.
  * the docgen bridge in `blueprint.py` (`created_by="docgen_bridge"`) — split
    the generated document into sections and inserted them with an AI origin
    and `status='pending_review'`, scrubbing nothing.

The second path is how model reasoning ended up stored as published document
content. A live audit found leaked scaffolding in 20 of 49 AI-authored
sections — text beginning "Step 1: **Analyze the Source Material and
Context**" persisted as the body of a document section.

The scrubber was never the problem: `_has_reasoning_residue` detected all 20
and `_strip_reasoning_artifacts` would have altered 19 of them. It simply was
not called on this path. Same shape as the notebook `session_id` miss — a
control implemented on one surface and absent on its sibling.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence.doc_generator import (
    _has_reasoning_residue,
    _strip_reasoning_artifacts,
)

# Representative of what was actually found stored in dic_sections.
LEAKED = (
    "Step 1: **Analyze the Source Material and Context**\n"
    "The provided text fragments come from a draft document titled "
    '"Internet Service Providers and Peering".\n\n'
    "Step 2: Draft the section.\n"
    "ISPs exchange traffic at neutral interconnection points."
)


def test_the_leaked_pattern_is_detected():
    """Guard the premise: if the scrubber cannot see it, the fix is elsewhere."""
    assert _has_reasoning_residue(LEAKED) is True


def test_the_scrubber_removes_the_scaffolding():
    cleaned = _strip_reasoning_artifacts(LEAKED)
    assert cleaned != LEAKED
    assert "Step 1:" not in cleaned
    assert "Analyze the Source Material" not in cleaned


def test_the_scrubber_keeps_the_substance():
    """Scrubbing must not eat the document."""
    cleaned = _strip_reasoning_artifacts(LEAKED)
    assert "ISPs exchange traffic" in cleaned


@pytest.mark.parametrize("text", ["", "   ", None])
def test_scrubber_tolerates_empty(text):
    """The bridge passes section content straight through, including blanks."""
    assert _strip_reasoning_artifacts(text or "") in ("", "   ".strip(), "   ")


def test_clean_prose_is_left_alone():
    """A section with no reasoning residue must survive byte-identical."""
    clean = (
        "# Overview\n\nThe contractor shall retain all records for seven years "
        "following contract closeout [source: chunk c1]."
    )
    assert _strip_reasoning_artifacts(clean) == clean


def test_bridge_calls_the_scrubber():
    """Pin the wiring, not just the primitive.

    The primitive already worked; the defect was that this path never invoked
    it. Asserting the call site is what stops that regressing.
    """
    import inspect

    from tools.document_intelligence import blueprint

    src = inspect.getsource(blueprint)
    bridge = src[src.index("docgen_bridge"):]
    insert_at = bridge.index("INSERT INTO dic_sections")
    assert "_strip_reasoning_artifacts" in bridge[:insert_at], (
        "the docgen bridge must scrub reasoning artifacts BEFORE inserting "
        "sections, as doc_generator does on its own write path"
    )
