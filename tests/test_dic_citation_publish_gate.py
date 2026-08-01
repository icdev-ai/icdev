#!/usr/bin/env python3
"""DIC citation publish gate — CUI // SP-CTI.

`api_review_approve` gated only on unresolved [PLACEHOLDER] tokens. An
AI-authored section could be published making entirely uncited claims, or
citing a chunk that was never retrieved for it, and the approve route would
happily move the version to `approved`.

`citation_guard` was enforced on the docmod regeneration entry
(`doc_modernization/regen_quality_gate.py`) and inside Cortex's
GovernancePipeline, but NOT on DIC's own HITL publish route — the one a human
reviewer actually clicks.

These tests cover the gate's decision logic: which sections it judges, what it
treats as a defect, and that human-authored prose is left alone.
"""
from __future__ import annotations

import json

import pytest

from tools.document_intelligence.consistency_checker import (
    AI_ORIGINS,
    _allowed_sources,
)
from tools.quality.citation_grounding import citation_gate


# --------------------------------------------------------------------------- #
# allowed_sources extraction
# --------------------------------------------------------------------------- #


def test_allowed_sources_reads_chunk_ids_from_stored_citations():
    payload = json.dumps([
        {"doc_id": "d1", "chunk_id": "chunk-1d3bcebf9045", "page": 0},
        {"doc_id": "d2", "chunk_id": "chunk-2a93004ca200", "page": 3},
    ])
    assert _allowed_sources(payload) == {"chunk-1d3bcebf9045", "chunk-2a93004ca200"}


@pytest.mark.parametrize("payload", [None, "", "[]", "not json", "{}", json.dumps([])])
def test_allowed_sources_degrades_to_empty(payload):
    """Never raise on a malformed citations_json — an approve must not 500."""
    assert _allowed_sources(payload) == set()


def test_allowed_sources_accepts_already_parsed_lists():
    assert _allowed_sources([{"chunk_id": "c1"}, {"id": "c2"}, "c3"]) == {"c1", "c2", "c3"}


# --------------------------------------------------------------------------- #
# Which sections the gate judges
# --------------------------------------------------------------------------- #


def test_ai_origins_covers_generated_and_regenerated():
    """The live DB carries ai_regenerated too, which ORIGIN_TYPES omits."""
    assert {"ai_generated", "ai_regenerated", "ai_assisted"} <= set(AI_ORIGINS)
    assert "human_authored" not in AI_ORIGINS
    assert "template" not in AI_ORIGINS


# --------------------------------------------------------------------------- #
# The defects it must catch — via the shared TRUST module, not a reimplementation
# --------------------------------------------------------------------------- #


def test_uncited_ai_section_is_a_defect():
    sections = [{
        "item_number": "Introduction",
        "content": "The retention period is seven years.",   # no [source: ...]
        "allowed_sources": {"chunk-abc"},
    }]
    findings = citation_gate(sections, require_citations=True)
    assert [f["issue"] for f in findings] == ["missing_citations"]


def test_citation_to_unretrieved_evidence_is_a_defect():
    """The failure this gate exists for: a well-formed citation to nothing.

    `[source: chunk zzz]` is structurally perfect — it parses, it looks right in
    the UI — but that chunk was never retrieved for this section.
    """
    sections = [{
        "item_number": "Scope",
        "content": "Records are retained for seven years [source: chunk zzz].",
        "allowed_sources": {"chunk-abc", "chunk-def"},
    }]
    findings = citation_gate(sections, require_citations=True)
    issues = {f["issue"] for f in findings}
    assert "hallucinated_citation" in issues
    assert findings[0]["detail"] == ["zzz"]


def test_properly_cited_section_passes():
    sections = [{
        "item_number": "Scope",
        "content": "Records are retained for seven years [source: chunk chunk-abc].",
        "allowed_sources": {"chunk-abc"},
    }]
    assert citation_gate(sections, require_citations=True) == []


def test_abstained_section_is_exempt():
    """An abstained section makes no claim, so it owes no citation."""
    sections = [{
        "item_number": "Unknown",
        "content": "(Abstained — insufficient evidence to support this section.)",
        "abstained": True,
        "allowed_sources": set(),
    }]
    assert citation_gate(sections, require_citations=True) == []


def test_multiple_defects_are_all_reported():
    """A reviewer needs the full list, not just the first failure."""
    sections = [
        {"item_number": "A", "content": "Claim with no citation.", "allowed_sources": {"c1"}},
        {"item_number": "B", "content": "Claim [source: chunk nope].", "allowed_sources": {"c1"}},
        {"item_number": "C", "content": "Good [source: chunk c1].", "allowed_sources": {"c1"}},
    ]
    findings = citation_gate(sections, require_citations=True)
    by_item = {f["item_number"]: f["issue"] for f in findings}
    assert by_item.get("A") == "missing_citations"
    assert by_item.get("B") == "hallucinated_citation"
    assert "C" not in by_item


def test_gate_shape_matches_placeholder_guard():
    """Both gates must be consumable by the same route code.

    `citation_gate` and `content_grounding.placeholder_findings` are treated
    symmetrically by the approve route; if their finding shapes diverge, one of
    the two branches silently stops reporting.
    """
    findings = citation_gate(
        [{"item_number": "A", "content": "Uncited.", "allowed_sources": set()}],
        require_citations=True,
    )
    assert findings, "expected a finding to inspect"
    for f in findings:
        assert set(f) >= {"item_number", "issue", "detail"}
