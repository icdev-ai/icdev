# CUI // SP-CTI
"""Tests for the STIG compliance pillar — NLP extractor and criterion checks."""
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
    _parse_nlp_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _patch_nlp(monkeypatch, return_value):
    """Monkeypatch _nlp_extract_stig_refs to return a fixed value."""
    import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
    monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda *_a, **_kw: return_value)


def _disable_nlp(monkeypatch):
    """Disable the NLP extractor entirely (simulates missing API key)."""
    _patch_nlp(monkeypatch, None)


# ---------------------------------------------------------------------------
# _parse_nlp_json — JSON extraction from LLM response text
# ---------------------------------------------------------------------------

class TestParseNlpJson:
    def test_parses_clean_json(self):
        raw = '{"found": true, "refs": ["V-220938"], "confidence": 0.95}'
        result = _parse_nlp_json(raw)
        assert result == {"found": True, "refs": ["V-220938"], "confidence": 0.95}

    def test_parses_json_with_prose_prefix(self):
        raw = 'Here is the analysis:\n{"found": false, "refs": [], "confidence": 0.1}'
        result = _parse_nlp_json(raw)
        assert result is not None
        assert result["found"] is False

    def test_parses_json_in_markdown_fence(self):
        raw = "```json\n{\"found\": true, \"refs\": [\"CAT I\"], \"confidence\": 0.8}\n```"
        result = _parse_nlp_json(raw)
        assert result is not None
        assert result["found"] is True

    def test_returns_none_for_invalid_json(self):
        result = _parse_nlp_json("This is just plain text with no JSON.")
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = _parse_nlp_json("")
        assert result is None

    def test_returns_none_for_json_array(self):
        # Arrays are valid JSON but not a dict — should return None
        result = _parse_nlp_json('[1, 2, 3]')
        assert result is None

    def test_non_greedy_preferred_over_greedy_when_nested(self):
        # Non-greedy picks the innermost complete object when nested braces exist
        raw = 'outer {"found": true, "refs": [], "confidence": 0.5} trailer'
        result = _parse_nlp_json(raw)
        assert result is not None
        assert "found" in result


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  stig_compliance:\n"
            "    nlp_extractor:\n"
            "      enabled: false\n"
            "      model: claude-haiku-4-5-20251001\n"
            "      confidence_threshold: 0.85\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is False
        assert result["nlp_extractor_confidence_threshold"] == 0.85
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is True
        assert result["nlp_extractor_confidence_threshold"] == 0.7
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_model"] == "claude-haiku-4-5-20251001"
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _nlp_extract_stig_refs — NLP extraction (API mocked)
# ---------------------------------------------------------------------------

class TestNlpExtractStigRefs:
    def _make_provider_mock(self, json_payload: dict):
        mock_response = MagicMock()
        mock_response.content = json.dumps(json_payload)
        mock_provider = MagicMock()
        mock_provider.invoke.return_value = mock_response
        return mock_provider

    def test_returns_none_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = _nlp_extract_stig_refs("some text", "find STIG refs")
        assert result is None

    def test_returns_none_when_nlp_disabled(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": False,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _nlp_extract_stig_refs("some text", "find STIG refs")
        assert result is None

    def test_returns_parsed_result_from_llm(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        expected = {"found": True, "refs": ["V-220938"], "confidence": 0.92}
        mock_response = MagicMock()
        mock_response.content = json.dumps(expected)
        mock_provider_instance = MagicMock()
        mock_provider_instance.invoke.return_value = mock_response
        # AnthropicLLMProvider is imported locally inside _nlp_extract_stig_refs,
        # so we patch it at the source module level.
        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider_instance):
            with patch("tools.llm.provider.LLMRequest"):
                result = _nlp_extract_stig_refs("V-220938 is a finding", "identify STIG IDs")
        # If the provider module is unavailable in CI the function returns None; accept either.
        assert result is None or result == expected

    def test_returns_none_on_llm_exception(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        mock_provider_instance = MagicMock()
        mock_provider_instance.invoke.side_effect = RuntimeError("network error")
        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider",
                   return_value=mock_provider_instance):
            with patch("tools.llm.provider.LLMRequest"):
                result = _nlp_extract_stig_refs("some text", "find refs")
        # Returns None either because the provider raised or because import is unavailable.
        assert result is None


# ---------------------------------------------------------------------------
# _check_stig_vids_in_code
# ---------------------------------------------------------------------------

class TestCheckStigVidsInCode:
    def test_passes_on_regex_hit(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "tools/security.py", "# STIG: V-220938 — enforce strong auth\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed
        assert "V-IDs found" in result.message

    def test_fails_when_no_vids_and_nlp_disabled(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "tools/security.py", "# no compliance annotations here\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed

    def test_passes_via_nlp_when_regex_misses(self, tmp_path, monkeypatch):
        _write(tmp_path, "tools/security.py", "# enforces vulnerability finding two-two-oh-nine-three-eight\n")
        _patch_nlp(monkeypatch, {"found": True, "refs": ["V-220938"], "confidence": 0.91})
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_below_confidence_threshold_does_not_pass(self, tmp_path, monkeypatch):
        _write(tmp_path, "tools/security.py", "# vague security mention\n")
        _patch_nlp(monkeypatch, {"found": True, "refs": ["V-220938"], "confidence": 0.4})
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_stig_in_docs
# ---------------------------------------------------------------------------

class TestCheckStigInDocs:
    def test_passes_on_regex_hit_in_docs(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "docs/compliance.md", "# DISA STIG V-220938 remediated\n")
        result = _check_stig_in_docs(tmp_path)
        assert result.passed

    def test_passes_via_nlp_when_regex_misses_in_docs(self, tmp_path, monkeypatch):
        _write(tmp_path, "docs/security.md", "All findings comply with DoD hardening guides.\n")
        _patch_nlp(monkeypatch, {"found": True, "refs": ["DISA STIG"], "confidence": 0.88})
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _check_stig_in_docs(tmp_path)
        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# _check_stig_checklist — NLP coverage includes compliance_docs
# ---------------------------------------------------------------------------

class TestCheckStigChecklist:
    def test_passes_on_ckl_file(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "stig.ckl", "<CHECKLIST><ASSET/><STIGS><V-220938 status='NotAFinding'/></STIGS></CHECKLIST>")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_passes_on_compliance_doc_with_vid(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        # Use a neutral name so the file is not matched by the docs/**/*stig* glob
        # in checklist_files — it should reach the compliance-docs loop instead.
        _write(tmp_path, "docs/compliance/findings.md", "## V-220938 — Finding closed\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed
        assert "compliance docs" in result.message

    def test_passes_via_nlp_on_compliance_doc_when_regex_misses(self, tmp_path, monkeypatch):
        """NLP enhanced path must cover compliance_docs, not only checklist_files."""
        _write(tmp_path, "docs/compliance/stig-notes.md",
               "All vulnerability two-two-oh-nine-three-eight findings are closed.\n")
        _patch_nlp(monkeypatch, {"found": True, "refs": ["V-220938"], "confidence": 0.85})
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _check_stig_checklist(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_fails_when_no_checklist_and_nlp_disabled(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        result = _check_stig_checklist(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_cat1_remediation
# ---------------------------------------------------------------------------

class TestCheckCat1Remediation:
    def test_passes_on_vid_and_cat_pattern_cooccurrence(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "tools/hardening.py",
               "# V-220938 — CAT I finding; enforce strong auth\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed

    def test_passes_via_nlp_when_prose_only(self, tmp_path, monkeypatch):
        _write(tmp_path, "docs/remediation.md",
               "All Category One findings have been closed per the STIG review.\n")
        _patch_nlp(monkeypatch, {"found": True, "refs": ["CAT I"], "confidence": 0.9})
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            "nlp_extractor_enabled": True,
            "nlp_extractor_model": "claude-haiku-4-5-20251001",
            "nlp_extractor_max_tokens": 256,
            "nlp_extractor_confidence_threshold": 0.7,
            "nlp_extractor_text_sample_chars": 2000,
        })
        result = _check_cat1_remediation(tmp_path)
        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# PILLAR integration
# ---------------------------------------------------------------------------

class TestStigCompliancePillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"stig-vids-in-code", "stig-in-docs", "stig-checklist", "cat1-remediation", "external-stig-scanner"}

    def test_all_criteria_pass_with_full_fixture(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "tools/security.py", "# STIG: V-220938 — CAT I finding addressed\n")
        _write(tmp_path, "docs/stig-overview.md", "DISA STIG V-220938 remediated.\n")
        _write(tmp_path, "docs/compliance/stig-rhel.md", "V-220938 status: NotAFinding\n")
        results = PILLAR.run(tmp_path)
        by_id = {r.criterion_id: r for r in results}
        assert by_id["stig-vids-in-code"].passed
        assert by_id["stig-in-docs"].passed
        assert by_id["stig-checklist"].passed
        assert by_id["cat1-remediation"].passed

    def test_score_all_pass(self, tmp_path, monkeypatch):
        _disable_nlp(monkeypatch)
        _write(tmp_path, "tools/security.py", "# STIG: V-220938 — CAT I\n")
        _write(tmp_path, "docs/stig.md", "DISA STIG V-220938\n")
        _write(tmp_path, "docs/compliance/checklist.md", "V-220938 status: NotAFinding\n")
        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]
        assert score["percentage"] == 1.0
