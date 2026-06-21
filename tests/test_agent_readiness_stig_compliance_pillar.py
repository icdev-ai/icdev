# CUI // SP-CTI
"""Tests for the STIG compliance pillar's NLP extractor and check functions."""
from __future__ import annotations

import json
import pathlib
import textwrap
from unittest.mock import MagicMock, patch


from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import (
    PILLAR,
    _check_cat1_remediation,
    _check_stig_checklist,
    _check_stig_in_docs,
    _check_stig_vids_in_code,
    _nlp_extract_stig_refs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _make_response(content: str, structured_output=None):
    resp = MagicMock()
    resp.content = content
    resp.structured_output = structured_output
    return resp


def _patch_thresholds(monkeypatch, **overrides):
    import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
    defaults = {
        "nlp_extractor_enabled": True,
        "nlp_extractor_model": "claude-haiku-4-5-20251001",
        "nlp_extractor_max_tokens": 256,
        "nlp_extractor_confidence_threshold": 0.7,
        "nlp_extractor_text_sample_chars": 2000,
    }
    defaults.update(overrides)
    monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)


# ---------------------------------------------------------------------------
# _nlp_extract_stig_refs — NLP extractor unit tests
# ---------------------------------------------------------------------------

class TestNlpExtractStigRefs:
    def test_returns_none_when_nlp_disabled(self, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        result = _nlp_extract_stig_refs("some text", "find STIG refs")
        assert result is None

    def test_returns_none_when_no_api_key(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        result = _nlp_extract_stig_refs("some text", "find STIG refs")
        assert result is None

    def test_uses_structured_output_when_provider_parses(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        structured = {"found": True, "refs": ["V-220938"], "confidence": 0.95}
        mock_response = _make_response('{"found": true}', structured_output=structured)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("text with V-220938", "find STIG refs")

        assert result == structured
        assert result["refs"] == ["V-220938"]

    def test_parses_clean_json_content(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        payload = {"found": True, "refs": ["V-123456", "CAT I"], "confidence": 0.88}
        mock_response = _make_response(json.dumps(payload), structured_output=None)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("some text", "find refs")

        assert result == payload

    def test_strips_markdown_json_fence(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        payload = {"found": False, "refs": [], "confidence": 0.1}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        mock_response = _make_response(fenced, structured_output=None)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("no stigs here", "find refs")

        assert result == payload

    def test_strips_plain_code_fence(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        payload = {"found": True, "refs": ["SV-220938r1_rule"], "confidence": 0.8}
        fenced = f"```\n{json.dumps(payload)}\n```"
        mock_response = _make_response(fenced, structured_output=None)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("some text", "find sv refs")

        assert result == payload

    def test_returns_none_on_provider_exception(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_provider = MagicMock()
        mock_provider.invoke.side_effect = RuntimeError("provider down")

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("text", "find refs")

        assert result is None

    def test_returns_none_on_invalid_json_response(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        mock_response = _make_response("Sorry, I cannot answer that.", structured_output=None)

        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            result = _nlp_extract_stig_refs("text", "find refs")

        assert result is None

    def test_truncates_text_to_sample_chars(self, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_text_sample_chars=10)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        payload = {"found": False, "refs": [], "confidence": 0.0}
        mock_response = _make_response(json.dumps(payload), structured_output=None)

        mock_provider = MagicMock()
        captured_request = []

        def capture_invoke(req, model_id, model_cfg):
            captured_request.append(req)
            return mock_response

        mock_provider.invoke.side_effect = capture_invoke

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            _nlp_extract_stig_refs("A" * 100, "find refs")

        assert len(captured_request) == 1
        user_content = captured_request[0].messages[0]["content"]
        assert "A" * 11 not in user_content  # sample capped at 10 chars

    def test_system_prompt_set_on_request(self, monkeypatch):
        _patch_thresholds(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        payload = {"found": False, "refs": [], "confidence": 0.0}
        mock_response = _make_response(json.dumps(payload), structured_output=None)

        mock_provider = MagicMock()
        captured = []

        def capture_invoke(req, model_id, model_cfg):
            captured.append(req)
            return mock_response

        mock_provider.invoke.side_effect = capture_invoke

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider):
            _nlp_extract_stig_refs("text", "find STIG refs")

        assert captured[0].system_prompt != ""
        assert "JSON" in captured[0].system_prompt


# ---------------------------------------------------------------------------
# _check_stig_vids_in_code — regex fast path + NLP fallback
# ---------------------------------------------------------------------------

class TestCheckStigVidsInCode:
    def _disable_nlp(self, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)

    def test_passes_when_vid_in_py_file(self, tmp_path, monkeypatch):
        self._disable_nlp(monkeypatch)
        _write(tmp_path, "security.py", "# STIG: V-220938 — disable root login\npass\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed
        assert "security.py" in result.message

    def test_passes_when_vid_in_yaml_config(self, tmp_path, monkeypatch):
        self._disable_nlp(monkeypatch)
        _write(tmp_path, "config.yaml", "# V-220938\nsetting: value\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed

    def test_fails_when_no_vids_present(self, tmp_path, monkeypatch):
        self._disable_nlp(monkeypatch)
        _write(tmp_path, "app.py", "print('hello')\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed

    def test_nlp_fallback_detects_natural_language_reference(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _write(tmp_path, "notes.py", "# See vulnerability finding two-two-zero-nine-three-eight\npass\n")

        nlp_result = {"found": True, "refs": ["V-220938"], "confidence": 0.85}
        with patch("tools.ai_augmentation.agent_readiness.pillars.stig_compliance._nlp_extract_stig_refs",
                   return_value=nlp_result):
            result = _check_stig_vids_in_code(tmp_path)

        assert result.passed
        assert "NLP" in result.message

    def test_nlp_fallback_skipped_below_confidence(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_confidence_threshold=0.9)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _write(tmp_path, "notes.py", "# see the vulnerability notes\npass\n")

        nlp_result = {"found": True, "refs": ["V-220938"], "confidence": 0.5}
        with patch("tools.ai_augmentation.agent_readiness.pillars.stig_compliance._nlp_extract_stig_refs",
                   return_value=nlp_result):
            result = _check_stig_vids_in_code(tmp_path)

        assert not result.passed


# ---------------------------------------------------------------------------
# _check_stig_in_docs — regex fast path + NLP fallback
# ---------------------------------------------------------------------------

class TestCheckStigInDocs:
    def test_passes_on_exact_stig_keyword_in_md(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "docs/compliance.md", "# STIG Compliance\nSee DISA STIG for details.\n")
        result = _check_stig_in_docs(tmp_path)
        assert result.passed

    def test_fails_when_no_docs(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        result = _check_stig_in_docs(tmp_path)
        assert not result.passed

    def test_nlp_detects_natural_language_stig_reference(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _write(tmp_path, "README.md", "We follow the Defense Information Systems Agency security guide.\n")

        nlp_result = {"found": True, "refs": ["DISA STIG"], "confidence": 0.82}
        with patch("tools.ai_augmentation.agent_readiness.pillars.stig_compliance._nlp_extract_stig_refs",
                   return_value=nlp_result):
            result = _check_stig_in_docs(tmp_path)

        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# _check_stig_checklist — regex fast path + NLP fallback
# ---------------------------------------------------------------------------

class TestCheckStigChecklist:
    def test_passes_on_ckl_file_with_vid(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "checklist.ckl", "<VULN_ID>V-220938</VULN_ID>\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_passes_on_compliance_doc_with_stig_pattern(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "docs/compliance/stig_review.md", "## STIG V-220938 — OPEN\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_fails_when_no_checklist(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        result = _check_stig_checklist(tmp_path)
        assert not result.passed

    def test_nlp_detects_checklist_content(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _write(tmp_path, "stig_results.yaml", "status: open\nfinding: privilege escalation risk\n")

        nlp_result = {"found": True, "refs": ["CAT I", "V-220938"], "confidence": 0.9}
        with patch("tools.ai_augmentation.agent_readiness.pillars.stig_compliance._nlp_extract_stig_refs",
                   return_value=nlp_result):
            result = _check_stig_checklist(tmp_path)

        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# _check_cat1_remediation — regex fast path + NLP fallback
# ---------------------------------------------------------------------------

class TestCheckCat1Remediation:
    def test_passes_when_vid_and_cat_marker_co_occur(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "security.py", "# V-220938 CAT I — disable root login\npass\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed

    def test_passes_when_ckl_file_present(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "checklist.ckl", "<VULN_ID>V-220938</VULN_ID>\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed
        assert "assumed" in result.message

    def test_fails_when_no_evidence(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "app.py", "print('hello')\n")
        result = _check_cat1_remediation(tmp_path)
        assert not result.passed

    def test_nlp_detects_severity_in_prose(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _write(tmp_path, "docs/security.md",
               "All high-severity findings have been remediated as part of the quarterly review.\n")

        nlp_result = {"found": True, "refs": ["CAT I"], "confidence": 0.8}
        with patch("tools.ai_augmentation.agent_readiness.pillars.stig_compliance._nlp_extract_stig_refs",
                   return_value=nlp_result):
            result = _check_cat1_remediation(tmp_path)

        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# PILLAR integration
# ---------------------------------------------------------------------------

class TestStigCompliancePillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"stig-vids-in-code", "stig-in-docs", "stig-checklist", "cat1-remediation"}

    def test_pillar_id_and_name(self):
        assert PILLAR.id == "stig-compliance"
        assert "STIG" in PILLAR.name

    def test_all_criteria_pass_with_full_stig_project(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "security.py", "# STIG: V-220938 CAT I\npass\n")
        _write(tmp_path, "docs/compliance.md", "# STIG Compliance\nSee DISA STIG V-220938.\n")
        _write(tmp_path, "checklist.ckl", "<VULN_ID>V-220938</VULN_ID>\n")

        results = PILLAR.run(tmp_path)
        by_id = {r.criterion_id: r for r in results}
        assert by_id["stig-vids-in-code"].passed
        assert by_id["stig-in-docs"].passed
        assert by_id["stig-checklist"].passed
        assert by_id["cat1-remediation"].passed
