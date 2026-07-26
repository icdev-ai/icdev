#!/usr/bin/env python3
"""Derivation disclosure must actually reach the DIC chat response. CUI // SP-CTI.

The primitive being correct is not the same as the user being told. DIC's
history here is specifically that grounding machinery shipped, was called, and
was a no-op — so the wiring gets its own tests.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence import blueprint as bp


class _R:
    def __init__(self, content, chunk_id="c1"):
        self.content = content
        self.chunk_id = chunk_id


def test_sources_are_keyed_by_both_ordinal_and_chunk_id():
    """Chat answers cite `[1]`; citations_json records chunk ids.

    Keying one way only would classify every claim against an empty pool and
    report the whole answer as synthesized.
    """
    sources = bp._chat_claim_sources([_R("some text", chunk_id="chunk-9")])
    assert "1" in sources and "chunk-9" in sources


def test_empty_results_yield_no_disclosure():
    assert bp._derivation_disclosure("Anything.", []) is None


def test_computed_figure_is_disclosed_through_the_blueprint():
    """End-to-end through the wiring, not just the primitive."""
    results = [_R("Phase A obligated 20. Phase B obligated 25.", chunk_id="c1")]
    rep = bp._derivation_disclosure("Total obligation is 45. [1]", results)
    assert rep is not None
    assert rep["counts"]["derived-numeric"] >= 1
    assert rep["has_derived"] is True


def test_fabricated_figure_raises_the_unexplained_flag():
    """The flag the UI turns amber on — a number grounded in nothing."""
    results = [_R("Records are retained for seven years.", chunk_id="c1")]
    rep = bp._derivation_disclosure("The contract is worth $8,412,900. [1]", results)
    assert rep["has_unexplained_numeric"] is True


def test_quoted_answer_reports_nothing_derived():
    """No badge on a fully-quoted answer — a badge that always shows is noise."""
    results = [_R("The contractor shall retain all records for seven years.", chunk_id="c1")]
    rep = bp._derivation_disclosure(
        "The contractor shall retain all records for seven years. [1]", results)
    assert rep["has_derived"] is False


def test_disclosure_failure_does_not_raise(monkeypatch):
    """Best-effort: a disclosure failure must never cost the user their answer."""
    import tools.quality.derivation as dv

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(dv, "disclose_derivations", boom)
    assert bp._derivation_disclosure("Some claim.", [_R("text")]) is None


# --------------------------------------------------------------------------- #
# Provenance carries the class
# --------------------------------------------------------------------------- #


def test_provenance_exposes_derivation_and_defaults_to_unclassified():
    """Defaulting to "verbatim" would assert a quotation nobody checked."""
    from tools.quality.citation_grounding import Provenance

    assert Provenance().to_dict()["derivation"] == ""
    assert Provenance(derivation="derived-numeric").to_dict()["derivation"] == "derived-numeric"


@pytest.mark.parametrize("mod", ["tools.quality.derivation", "icdev.tools.quality.derivation"])
def test_derivation_module_exists_in_both_trees(mod):
    """TRUST invariant: grounding modules ship in both the legacy and packaged
    trees, so a generated child app inherits them."""
    import importlib

    importlib.import_module(mod)
