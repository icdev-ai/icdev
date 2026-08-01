# CUI // SP-CTI
"""Tests: confabulation assessment wired into DIC generation (halluc-03).

The full generate_document flow needs DB + LLM + search, so these verify the
observable contract: GeneratedSection carries a confabulation assessment
through to_dict, and the wired detector flags risky section text.
"""

import importlib

dg = importlib.import_module("tools.document_intelligence.doc_generator")
cd = importlib.import_module("tools.security.confabulation_detector")


class TestGeneratedSectionCarriesConfabulation:
    def test_field_defaults_empty(self):
        s = dg.GeneratedSection(heading="Overview", content="text")
        assert s.confabulation == {}

    def test_to_dict_includes_confabulation(self):
        s = dg.GeneratedSection(
            heading="Overview",
            content="text",
            confabulation={"risk_level": "low", "findings_count": 0},
        )
        result = dg.GenerateResult(sections=[s])
        d = result.to_dict()
        assert "confabulation" in d["sections"][0]
        assert d["sections"][0]["confabulation"]["risk_level"] == "low"


class TestWiredDetectorBehavior:
    def test_clean_section_low_risk(self):
        a = cd.assess("The system implements zero-trust networking per NIST SP 800-207.")
        assert a["risk_level"] == "low"
        assert a["findings_count"] == 0

    def test_hedging_section_flagged(self):
        a = cd.assess("As an AI, I think this design might probably work, but I cannot verify it.")
        assert a["findings_count"] >= 1
        assert any(f.get("type") == "hedging_language" for f in a["findings"])
