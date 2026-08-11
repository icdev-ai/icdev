# CUI // SP-CTI
"""The opt-in credential layer in tools/redaction/detector.py (agov-case-01).

This detector runs on every LLM egress. The layer added here exists so the AGOV
CASE timeline can mask a token in a rendered command line, and the property
worth a test is the one that keeps that from changing anything else: it is OFF
unless a caller asks. A default flip would silently alter what every existing
sanitizer sends, which is the kind of change that is only noticed downstream.

The second property is that the patterns are not a second copy. They are read
from ``tools.security.secret_detector.BUILTIN_PATTERNS`` — the list the
security scanner already uses — so a pattern fixed in one place is fixed in
both. A test that hardcoded its own regex here would defeat that.
"""

from __future__ import annotations

import importlib

detector_module = importlib.import_module("tools.redaction.detector")
secret_detector = importlib.import_module("tools.security.secret_detector")

# Ollama NER is a network call to a generative model; every detector built here
# switches it off so the assertions are about regexes, not about a model.
NER_OFF = {"use_ollama_ner": False}

BEARER = "Bearer redactionLayerFixtureTokenNotReal01"  # nosec B105 -- test fixture
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # nosec B105 -- documented AWS example value


def _entity_types(detector, text):
    return {d.entity_type for d in detector.detect(text)}


def test_credentials_are_not_detected_by_default():
    """The load-bearing assertion: existing callers are unaffected."""
    detector = detector_module.RedactionDetector(**NER_OFF)

    assert not any(e.startswith("SECRET_") for e in _entity_types(detector, BEARER))


def test_credentials_are_detected_when_a_caller_opts_in():
    detector = detector_module.RedactionDetector(detect_secrets=True, **NER_OFF)

    assert "SECRET_BEARER_TOKEN" in _entity_types(detector, f"curl -H '{BEARER}'")
    assert "SECRET_AWS_ACCESS_KEY" in _entity_types(
        detector, f"aws_access_key_id={AWS_KEY}")


def test_the_config_toggle_turns_the_layer_on_without_a_code_change():
    """``detection.secret_patterns.enabled`` is the platform-wide switch."""
    config = detector_module.load_config()
    enabled = dict(config)
    enabled["detection"] = {**config.get("detection", {}),
                            "secret_patterns": {"enabled": True}}

    detector = detector_module.RedactionDetector(config=enabled, **NER_OFF)
    assert "SECRET_BEARER_TOKEN" in _entity_types(detector, BEARER)


def test_the_shipped_default_is_off():
    """Read from the file, not from a fixture: this is a deployment property."""
    config = detector_module.load_config()

    assert config.get("detection", {}).get("secret_patterns", {}).get("enabled") is False


def test_every_builtin_pattern_is_loaded_under_a_derived_entity_type():
    """Sourced from the scanner's list, so the two cannot drift."""
    detector = detector_module.RedactionDetector(detect_secrets=True, **NER_OFF)
    loaded = {p["entity_type"] for p in detector._custom_patterns
              if p["entity_type"].startswith("SECRET_")}

    assert len(loaded) == len(secret_detector.BUILTIN_PATTERNS)
    assert "SECRET_PRIVATE_KEY" in loaded
    assert "SECRET_JWT_TOKEN" in loaded


def test_anonymizer_uses_an_injected_detector_instead_of_building_its_own():
    """Without this seam a caller's detection profile is silently discarded."""
    anonymizer_module = importlib.import_module("tools.redaction.anonymizer")
    detector = detector_module.RedactionDetector(detect_secrets=True, **NER_OFF)
    anonymizer = anonymizer_module.RedactionAnonymizer(detector=detector)

    result = anonymizer.anonymize(f"curl -H '{BEARER}' https://example.test")

    assert BEARER not in result.anonymized_text
    assert "[SECRET_BEARER_TOKEN]" in result.anonymized_text
