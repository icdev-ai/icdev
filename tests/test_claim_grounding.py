#!/usr/bin/env python3
"""Claim-level grounding — CUI // SP-CTI.

Everything in `citation_grounding` before this layer answers "does this citation
point at a source that exists?". `validate_citations` compares cited ids to
offered ids; `compute_attribution_score` measures how much of a chunk resurfaced
in the output. Neither compares a CLAIM to the TEXT of the source it cites, so a
well-formed `[source: chunk 3]` on a wholly invented sentence passed every gate.

These tests pin the layer that closes that: span binding (token F1) plus the
anchor guard, both deterministic and both required. The whole file runs with no
LLM — that is the point, per D310: the blocking path must not need a model.
"""
from __future__ import annotations

import pytest

from tools.quality.citation_grounding import (
    CONF_ABSTAIN,
    CONF_INCLUDE,
    _anchor_present,
    bind_claim_span,
    claim_gate,
    decompose_claims,
    extract_anchors,
    ground_claims,
    verify_claim,
)

RETENTION = (
    "The contractor shall retain all records for a period of seven years "
    "following contract closeout, as required by the retention clause."
)
SOURCES = {"c1": RETENTION}


def _one(text, sources=None, **kw):
    return ground_claims(text, sources or SOURCES, **kw)["claims"][0]


# --------------------------------------------------------------------------- #
# The headline case
# --------------------------------------------------------------------------- #


def test_fabricated_claim_with_valid_citation_is_unsupported():
    """A perfect citation on an invented sentence. The reason this layer exists."""
    v = _one("Personnel must evacuate within thirty minutes of alarm activation [source: chunk c1].")
    assert v["verdict"] == "unsupported"


def test_correct_paraphrase_survives():
    """Control — the gate must not simply reject everything."""
    v = _one("The contractor shall retain all records for seven years following contract closeout [source: chunk c1].")
    assert v["verdict"] == "supported"
    assert v["bound_spans"], "a supported claim must carry the span that supports it"
    assert "seven years" in v["bound_spans"][0]["quote"]


@pytest.mark.parametrize("swapped,anchor", [
    ("forty-seven years", "forty-seven"),   # spelled-out number
    ("12 years", "12"),                     # digit
    ("seventeen years", "seventeen"),       # substring of nothing, but absent
])
def test_quantity_swaps_are_caught_by_the_anchor_guard(swapped, anchor):
    """The most likely prose fabrication: one number quietly changed.

    Lexical overlap stays near-identical, so span F1 alone scores these as well
    supported — the spelled-out case measured 0.75 before the anchor guard
    recognised number words. The anchor is what catches it.
    """
    text = f"The contractor shall retain all records for {swapped} following contract closeout [source: chunk c1]."
    v = _one(text)
    assert v["verdict"] == "unsupported"
    assert v["method"] == "anchor"
    assert anchor in v["missing_anchors"]


def test_gate_names_the_fabricated_value():
    """A reviewer needs to know WHICH value is wrong, not just that one is."""
    text = "Records are retained for forty-seven years [source: chunk c1]."
    findings = claim_gate(ground_claims(text, SOURCES))
    assert findings
    assert findings[0]["issue"] == "unsupported_claim"
    assert "forty-seven" in findings[0]["detail"]


# --------------------------------------------------------------------------- #
# Anchor matching precision
# --------------------------------------------------------------------------- #


def test_anchor_matching_is_word_boundary_aware():
    """'seven' must not count as present in 'seventeen'.

    A naive substring test would let a quantity swap through the guard — the
    exact defect the guard exists to catch.
    """
    assert _anchor_present("seven", "seventeen years") is False
    assert _anchor_present("seven", "seven years") is True


@pytest.mark.parametrize("anchor,haystack,expected", [
    ("forty-seven", "forty seven years", True),      # hyphen vs space
    ("$7,500", "costs 7500 dollars", True),          # separators
    ("7500", "costs $7,500 total", True),
    ("AC-3", "control AC-3 applies", True),
    ("AC-3", "control AC-4 applies", False),
])
def test_anchor_normalisation(anchor, haystack, expected):
    assert _anchor_present(anchor, haystack) is expected


def test_extract_anchors_drops_submatches():
    """'7' inside '$7,500' must not be counted separately."""
    anchors = extract_anchors("The fee is $7,500 per year.")
    assert "$7,500" in anchors
    assert "7" not in anchors


def test_extract_anchors_ignores_bare_determiners():
    assert extract_anchors("The contractor shall comply.") == []


# --------------------------------------------------------------------------- #
# Span binding
# --------------------------------------------------------------------------- #


def test_bind_returns_offsets_into_the_source():
    span = bind_claim_span("retain records for seven years", RETENTION, "c1")
    assert span is not None
    assert RETENTION[span["start"]:span["end"]] == span["quote"]
    assert span["source_id"] == "c1"


def test_bind_scores_by_f1_not_recall():
    """A claim asserting far more than the span says must not score full marks.

    Recall alone would reward any claim that reuses the source's vocabulary.
    """
    tight = bind_claim_span("retain records for seven years", RETENTION)
    padded = bind_claim_span(
        "retain records for seven years and also indemnify the agency against "
        "all third-party liability arising from any cause whatsoever",
        RETENTION,
    )
    assert tight["score"] > padded["score"]


@pytest.mark.parametrize("claim,source", [("", "text"), ("claim", ""), ("", "")])
def test_bind_handles_empty_inputs(claim, source):
    assert bind_claim_span(claim, source) is None


def test_bind_returns_none_when_nothing_overlaps():
    assert bind_claim_span("zebra quantum flute", RETENTION) is None


# --------------------------------------------------------------------------- #
# Verdict semantics
# --------------------------------------------------------------------------- #


def test_uncited_claim_is_uncited_not_unsupported():
    """An uncited sentence makes no attributed assertion.

    Conflating it with 'unsupported' would make every non-factual connecting
    sentence a gate failure.
    """
    v = _one("Records are retained for some period.")
    assert v["verdict"] == "uncited"
    assert claim_gate(ground_claims("Records are retained for some period.", SOURCES)) == []


def test_citation_to_a_source_that_was_not_supplied_is_unsupported():
    v = _one("Records are retained for seven years [source: chunk nope].")
    assert v["verdict"] == "unsupported"


def test_supported_ratio_counts_only_cited_claims():
    """Uncited sentences must not dilute or inflate the ratio."""
    text = (
        "This section describes retention. "
        "The contractor shall retain all records for seven years following contract closeout [source: chunk c1]."
    )
    r = ground_claims(text, SOURCES)
    assert r["uncited"] == 1
    assert r["supported"] == 1
    assert r["supported_ratio"] == 1.0


def test_bands_come_from_the_shared_constants():
    """No new thresholds — reuse CONF_INCLUDE / CONF_ABSTAIN."""
    assert 0.0 < CONF_ABSTAIN < CONF_INCLUDE <= 1.0


# --------------------------------------------------------------------------- #
# The injected judge
# --------------------------------------------------------------------------- #


def test_judge_can_veto_a_lexically_plausible_claim():
    v = verify_claim(
        "Records are retained for seven years [source: chunk c1].",
        SOURCES,
        judge=lambda claim, span: False,
    )
    assert v["verdict"] == "unsupported"
    assert v["method"] == "judge"


def test_judge_failure_never_blocks():
    """A judge that raises must leave the deterministic verdict standing."""
    def boom(claim, span):
        raise RuntimeError("provider unreachable")

    v = verify_claim(
        "The contractor shall retain all records for seven years following contract closeout [source: chunk c1].",
        SOURCES,
        judge=boom,
    )
    assert v["verdict"] == "supported"


def test_judge_cannot_rescue_a_claim_absent_from_its_own_span():
    """Ported from the DIC verifier cross-check.

    A model saying 'yes' must not override zero lexical footprint — that is how
    an LLM launders a fabrication past a deterministic gate.
    """
    v = verify_claim(
        "Personnel must evacuate within thirty minutes [source: chunk c1].",
        SOURCES,
        judge=lambda claim, span: True,
    )
    assert v["verdict"] == "unsupported"


def test_no_judge_is_the_air_gap_path():
    """Everything above runs with judge=None. This asserts it explicitly."""
    r = ground_claims(
        "Personnel must evacuate within thirty minutes [source: chunk c1].", SOURCES
    )
    assert r["unsupported"] == 1


# --------------------------------------------------------------------------- #
# Decomposition + gate shape
# --------------------------------------------------------------------------- #


def test_decompose_offsets_index_the_original_text():
    text = "First claim here. Second claim there. Third one."
    claims = decompose_claims(text)
    assert len(claims) == 3
    for claim, start, end in claims:
        assert text[start:end].strip() == claim


@pytest.mark.parametrize("text", ["", "   ", None])
def test_decompose_handles_empty(text):
    assert decompose_claims(text) == []


def test_claim_gate_shape_matches_the_sibling_gates():
    """Must be consumable by the same promote/export code as citation_gate."""
    findings = claim_gate(ground_claims(
        "Personnel must evacuate within thirty minutes [source: chunk c1].", SOURCES))
    assert findings
    for f in findings:
        assert set(f) >= {"item_number", "issue", "detail"}


def test_claim_gate_can_be_disabled():
    report = ground_claims(
        "Personnel must evacuate within thirty minutes [source: chunk c1].", SOURCES)
    assert claim_gate(report, require_supported=False) == []
