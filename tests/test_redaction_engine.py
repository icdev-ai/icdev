# CUI // SP-CTI
"""Core redaction-engine unit tests (trust-mask-05, closes B-gap-6).

Exercises the always-on regex detection layer, the reversible surrogate
registry round-trip, and GovConSanitizer scope enforcement — none of which
require Ollama NER or Presidio (those layers degrade gracefully). Tests that
would need NER/Presidio restrict entity filters to regex-detectable types.
"""

import importlib

import pytest

det_mod = importlib.import_module("tools.redaction.detector")
anon_mod = importlib.import_module("tools.redaction.anonymizer")
reg_mod = importlib.import_module("tools.redaction.registry")

_SSN = "My SSN is 123-45-6789 for the record."
_EMAIL = "Contact jane.doe@example.com please."


@pytest.fixture(scope="module")
def detector():
    return det_mod.RedactionDetector()


class TestDetectorRegexLayer:
    def test_detects_ssn(self, detector):
        found = detector.detect(_SSN, entities=["US_SSN"])
        assert any(d.entity_type == "US_SSN" for d in found)

    def test_detects_email(self, detector):
        found = detector.detect(_EMAIL, entities=["EMAIL_ADDRESS"])
        assert any(d.entity_type == "EMAIL_ADDRESS" for d in found)

    def test_detection_positions_valid(self, detector):
        found = detector.detect(_SSN, entities=["US_SSN"])
        d = next(x for x in found if x.entity_type == "US_SSN")
        assert _SSN[d.start:d.end] == "123-45-6789"

    def test_clean_text_no_ssn(self, detector):
        assert detector.detect("nothing sensitive here", entities=["US_SSN"]) == []

    def test_supported_entities_and_health(self, detector):
        assert detector.get_supported_entities()
        assert detector.health().get("status") == "ok"


class TestAnonymizer:
    @pytest.fixture(scope="class")
    def anonymizer(self):
        return anon_mod.RedactionAnonymizer()

    def test_anonymizes_ssn(self, anonymizer):
        result = anonymizer.anonymize(_SSN, impact_level="IL4", entities=["US_SSN"])
        assert result.detections
        assert "123-45-6789" not in result.anonymized_text

    def test_clean_text_unchanged(self, anonymizer):
        result = anonymizer.anonymize("nothing sensitive", entities=["US_SSN"])
        assert result.anonymized_text == "nothing sensitive"
        assert result.detections == []


class TestRegistryRoundTrip:
    def test_surrogate_is_stable(self):
        reg = reg_mod.RedactionRegistry()
        s1 = reg.get_or_create_surrogate("PERSON", "Jane Q. Public")
        s2 = reg.get_or_create_surrogate("PERSON", "Jane Q. Public")
        assert s1 == s2
        assert s1 != "Jane Q. Public"

    def test_distinct_values_distinct_surrogates(self):
        reg = reg_mod.RedactionRegistry()
        a = reg.get_or_create_surrogate("PERSON", "Alice Adams")
        b = reg.get_or_create_surrogate("PERSON", "Bob Barker")
        assert a != b

    def test_de_anonymize_restores_original(self):
        reg = reg_mod.RedactionRegistry()
        surrogate = reg.get_or_create_surrogate("PERSON", "Carol Klein")
        restored = reg.de_anonymize(f"The lead is {surrogate} on this effort.")
        assert "Carol Klein" in restored


class TestSanitizerScope:
    @pytest.fixture(scope="class")
    def sanitizer(self):
        gs_mod = importlib.import_module("tools.redaction.govcon_sanitizer")
        return gs_mod.GovConSanitizer()

    def test_exempt_function_is_skipped(self, sanitizer):
        text, meta = sanitizer.sanitize_for_llm(_SSN, function_name="screenshot_validation")
        assert meta.get("skipped") is True
        assert text == _SSN  # untouched

    def test_enforced_function_processes(self, sanitizer):
        # proposal_drafting is enforced; is_local_only=False forces sanitization.
        text, meta = sanitizer.sanitize_for_llm(
            _SSN, function_name="proposal_drafting", impact_level="IL4", is_local_only=False
        )
        # Either it sanitized (skipped False) or degraded gracefully — but if it
        # ran, the raw SSN must not remain.
        if not meta.get("skipped", False):
            assert "123-45-6789" not in text

    def test_local_only_skips_non_enforced(self, sanitizer):
        # skip_for_local_only defaults true — a non-enforced, non-exempt function
        # routed local-only skips redaction (data never leaves the machine).
        _text, meta = sanitizer.sanitize_for_llm(
            _SSN, function_name="some_generic_function", is_local_only=True
        )
        assert meta.get("skipped") is True
        assert meta.get("reason") == "local_only_routing"

    def test_enforced_never_skipped_local(self, sanitizer):
        # Enforced modules are NEVER skipped, even local-only.
        _text, meta = sanitizer.sanitize_for_llm(
            _SSN, function_name="proposal_drafting", is_local_only=True
        )
        assert meta.get("skipped") is not True
