# CUI // SP-CTI
"""Tests: fail-closed redaction egress gate (trust-mask-01).

Covers LLMRouter._pre_invoke_redaction:
    - default (fail-open): sanitizer unavailable/errors -> skip (returns None)
    - fail_closed: sanitizer unavailable/errors -> RedactionUnavailableError
    - legitimate skips (disabled, excluded) never raise even when fail_closed
And response_drafter._redaction_fail_closed config read.
"""

import importlib
from unittest.mock import patch

import pytest

router_mod = importlib.import_module("tools.llm.router")
provider_mod = importlib.import_module("tools.llm.provider")
LLMRouter = router_mod.LLMRouter
RedactionUnavailableError = router_mod.RedactionUnavailableError
LLMRequest = provider_mod.LLMRequest


@pytest.fixture()
def router():
    r = LLMRouter()
    # Deterministic: local-only chain so sanitize path is predictable; the
    # tests below drive behavior via _config + _get_sanitizer overrides.
    r._get_chain_for_function = lambda fn: []          # is_local = all([]) == True
    return r


def _req():
    return LLMRequest(messages=[{"role": "user", "content": "SSN 123-45-6789"}], classification="IL4")


class TestSanitizerUnavailable:
    def test_fail_open_skips_when_sanitizer_none(self, router):
        router._config = {"redaction": {"enabled": True, "fail_closed": False}}
        router._get_sanitizer = lambda: None
        assert router._pre_invoke_redaction("code_generation", _req()) is None

    def test_fail_closed_blocks_when_sanitizer_none(self, router):
        router._config = {"redaction": {"enabled": True, "fail_closed": True}}
        router._get_sanitizer = lambda: None
        with pytest.raises(RedactionUnavailableError) as ei:
            router._pre_invoke_redaction("code_generation", _req())
        assert ei.value.function == "code_generation"


class TestSanitizerErrors:
    def test_fail_closed_blocks_when_sanitize_raises(self, router):
        router._config = {"redaction": {"enabled": True, "fail_closed": True}}

        class _Boom:
            session_id = "s1"

            def sanitize_for_llm(self, *a, **k):
                raise RuntimeError("presidio exploded")

        router._get_sanitizer = lambda: _Boom()
        with pytest.raises(RedactionUnavailableError):
            router._pre_invoke_redaction("code_generation", _req())

    def test_fail_open_swallows_sanitize_error(self, router):
        router._config = {"redaction": {"enabled": True, "fail_closed": False}}

        class _Boom:
            session_id = "s1"

            def sanitize_for_llm(self, *a, **k):
                raise RuntimeError("presidio exploded")

        router._get_sanitizer = lambda: _Boom()
        assert router._pre_invoke_redaction("code_generation", _req()) is None


class TestLegitimateSkipsNeverBlock:
    def test_disabled_never_blocks(self, router):
        router._config = {"redaction": {"enabled": False, "fail_closed": True}}
        router._get_sanitizer = lambda: None
        assert router._pre_invoke_redaction("code_generation", _req()) is None

    def test_excluded_function_never_blocks(self, router):
        router._config = {
            "redaction": {"enabled": True, "fail_closed": True, "excluded_functions": ["pulse_draft"]}
        }
        router._get_sanitizer = lambda: None
        assert router._pre_invoke_redaction("pulse_draft", _req()) is None


class TestResponseDrafterConfigRead:
    def test_reads_the_shipped_config(self):
        """The reader reflects args/redaction_config.yaml, which ships
        fail_closed: true as of 4ea77afd5 (Pillar 0, policy-routed LLM).

        This asserted `is False` against the pre-Pillar-0 config and had been
        failing since. The code's fail-OPEN default (unreadable config) is
        covered by test_unreadable_config_falls_open below.
        """
        rd = importlib.import_module("tools.govcon.response_drafter")
        assert rd._redaction_fail_closed() is True

    def test_unreadable_config_falls_open(self, tmp_path):
        """An operator must opt IN to fail-closed; a missing/corrupt config
        never silently blocks drafting."""
        rd = importlib.import_module("tools.govcon.response_drafter")
        with patch.object(rd, "_ROOT", tmp_path):  # no args/redaction_config.yaml here
            assert rd._redaction_fail_closed() is False
