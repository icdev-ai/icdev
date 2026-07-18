# CUI // SP-CTI
"""DIC doc_generator confidence bands must stay coupled to the shared TRUST bands.

citation_grounding is the single source of truth for the include / flag / abstain
confidence bands (>=0.7 include, 0.4-0.69 flag + HITL, <0.4 abstain). Its own
docstring records that it "Mirrors the DIC doc_generator thresholds. Kept here so
every surface uses the same bands." Before this coupling, doc_generator hardcoded
its own ``0.7`` / ``0.4`` literals, so retuning the shared bands would silently
leave DIC drafting on the old numbers. These tests pin that the two agree.
"""

import importlib

# tools.* is a shim over icdev.tools.*; importlib resolves the submodules cleanly.
_doc_generator = importlib.import_module("tools.document_intelligence.doc_generator")
_citation_grounding = importlib.import_module("tools.quality.citation_grounding")


def test_include_band_is_the_shared_constant():
    assert _doc_generator._CONF_INCLUDE == _citation_grounding.CONF_INCLUDE


def test_abstain_band_is_the_shared_constant():
    assert _doc_generator._CONF_ABSTAIN == _citation_grounding.CONF_ABSTAIN


def test_bands_agree_with_classify_confidence():
    """The three-way doc_generator gate must partition the same way the shared
    classify_confidence helper does at the band edges."""
    inc = _citation_grounding.CONF_INCLUDE
    abst = _citation_grounding.CONF_ABSTAIN
    assert _citation_grounding.classify_confidence(inc) == "include"
    assert _citation_grounding.classify_confidence((inc + abst) / 2) == "flag"
    assert _citation_grounding.classify_confidence(abst - 0.01) == "abstain"
