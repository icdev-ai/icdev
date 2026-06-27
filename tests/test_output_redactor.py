# CUI // SP-CTI
"""Tests for output redactor — PII and credential redaction."""
from __future__ import annotations
import pytest
from icdev.tools.llm.output_redactor import redact, RedactResult, _PLACEHOLDER


class TestCleanContent:
    def test_clean_text_passes_through(self):
        r = redact("The analysis shows 3 findings in the security module.")
        assert not r.flagged
        assert r.redacted_text == "The analysis shows 3 findings in the security module."
        assert not r.changed

    def test_empty_string(self):
        r = redact("")
        assert not r.flagged

    def test_mode_off_skips_scanning(self):
        # Even with a matching key pattern, mode=off returns original unchanged
        r = redact("sk-abc" + "A" * 42, mode="off")
        assert not r.flagged
        assert "sk-abc" in r.redacted_text

    def test_json_output_no_false_positive(self):
        text = '{"status": "ok", "count": 42, "items": ["a", "b"]}'
        r = redact(text)
        assert not r.flagged


class TestPIIPatterns:
    def test_email_redacted(self):
        r = redact("Contact john.doe@example.com for access", mode="redact")
        assert r.flagged
        assert "john.doe@example.com" not in r.redacted_text
        assert _PLACEHOLDER in r.redacted_text

    def test_email_in_pattern_hits(self):
        r = redact("user@domain.com", mode="warn")
        assert "email" in r.pattern_hits

    def test_ssn_redacted(self):
        r = redact("SSN: 123-45-6789", mode="redact")
        assert r.flagged
        assert "123-45-6789" not in r.redacted_text

    def test_credit_card_redacted(self):
        r = redact("Card: 4111111111111111", mode="redact")
        assert r.flagged
        assert "4111111111111111" not in r.redacted_text

    def test_us_phone_redacted(self):
        r = redact("Call 555-867-5309 now", mode="redact")
        assert r.flagged

    def test_multiple_pii_in_one_string(self):
        r = redact("user@domain.com SSN: 123-45-6789", mode="warn")
        assert "email" in r.pattern_hits
        assert "ssn" in r.pattern_hits


class TestCredentialPatterns:
    def test_openai_api_key_redacted(self):
        key = "sk-" + "A" * 48
        r = redact(f"key = {key}", mode="redact")
        assert r.flagged
        assert key not in r.redacted_text

    def test_github_token_redacted(self):
        r = redact("token: ghp_" + "A" * 36, mode="redact")
        assert r.flagged

    def test_aws_access_key_redacted(self):
        r = redact("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", mode="redact")
        assert r.flagged

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        r = redact(f"Authorization: Bearer {jwt}", mode="redact")
        assert r.flagged

    def test_private_key_block_flagged(self):
        r = redact("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...", mode="redact")
        assert r.flagged

    def test_password_in_url_redacted(self):
        r = redact("postgres://admin:s3cr3t@localhost:5432/db", mode="redact")
        assert r.flagged


class TestModes:
    def test_warn_mode_text_unchanged(self):
        text = "email: user@domain.com"
        r = redact(text, mode="warn")
        assert r.flagged
        assert r.redacted_text == text
        assert not r.changed

    def test_redact_mode_replaces(self):
        r = redact("email: user@domain.com", mode="redact")
        assert r.flagged
        assert r.changed
        assert "user@domain.com" not in r.redacted_text

    def test_off_mode_no_scan(self):
        r = redact("user@domain.com", mode="off")
        assert not r.flagged
        assert r.redacted_text == "user@domain.com"

    def test_custom_placeholder(self):
        r = redact("user@domain.com", mode="redact", placeholder="***")
        assert "***" in r.redacted_text
        assert "[REDACTED]" not in r.redacted_text


class TestSkipAndExtra:
    def test_skip_email_pattern(self):
        r = redact("user@domain.com", mode="redact", skip_patterns=["email"])
        assert not r.flagged

    def test_skip_multiple_patterns(self):
        r = redact("user@domain.com and 123-45-6789", mode="warn", skip_patterns=["email", "ssn"])
        assert not r.flagged

    def test_extra_patterns_detected(self):
        import re
        extra = [("custom_id", re.compile(r"CUST-\d{6}"))]
        r = redact("order CUST-123456 processed", mode="redact", extra_patterns=extra)
        assert r.flagged
        assert "CUST-123456" not in r.redacted_text

    def test_extra_pattern_name_in_hits(self):
        import re
        extra = [("my_secret", re.compile(r"MYSECRET-\d+"))]
        r = redact("MYSECRET-9999", mode="warn", extra_patterns=extra)
        assert "my_secret" in r.pattern_hits


class TestChangedProperty:
    def test_changed_true_when_redacted(self):
        r = redact("user@domain.com", mode="redact")
        assert r.changed

    def test_changed_false_when_clean(self):
        r = redact("clean text", mode="redact")
        assert not r.changed

    def test_changed_false_in_warn_mode(self):
        r = redact("user@domain.com", mode="warn")
        assert not r.changed  # warn doesn't modify text


class TestResilience:
    def test_returns_result_type(self):
        r = redact("any text")
        assert isinstance(r, RedactResult)

    def test_none_like_empty_does_not_crash(self):
        r = redact("")
        assert isinstance(r, RedactResult)
        assert not r.flagged


class TestAgentLoopResultField:
    def test_output_redacted_field_exists(self):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        r = AgentLoopResult(done=True, truncated=False, turns=1, final_content="")
        assert hasattr(r, "output_redacted")
        assert r.output_redacted is False

    def test_output_redacted_default_false(self):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        r = AgentLoopResult(done=False, truncated=True, turns=3, final_content="some text")
        assert r.output_redacted is False
