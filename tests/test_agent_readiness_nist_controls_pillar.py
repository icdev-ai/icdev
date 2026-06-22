# CUI // SP-CTI
"""Tests for the nist_controls pillar — threshold loading and check functions."""
from __future__ import annotations

import pathlib
import textwrap
from unittest.mock import patch


from tools.ai_augmentation.agent_readiness.pillars.nist_controls import (
    PILLAR,
    _check_control_ids_in_code,
    _check_crosswalk_config,
    _check_nist_in_docs,
    _check_ssp_present,
    _nlp_extract_nist_refs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _patch_thresholds(monkeypatch, **overrides):
    defaults = {
        "nlp_extractor_enabled": False,  # off by default in tests — no API calls
        "nlp_extractor_model": "claude-haiku-4-5-20251001",
        "nlp_extractor_max_tokens": 256,
        "nlp_extractor_confidence_threshold": 0.7,
        "nlp_extractor_text_sample_chars": 2000,
        "nlp_extractor_max_files": 15,
    }
    defaults.update(overrides)
    import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
    monkeypatch.setattr(mod, "_load_thresholds", lambda: defaults)


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  nist_controls:\n"
            "    nlp_extractor:\n"
            "      enabled: false\n"
            "      model: claude-haiku-4-5-20251001\n"
            "      max_tokens: 512\n"
            "      confidence_threshold: 0.8\n"
            "      text_sample_chars: 3000\n"
            "      max_files: 20\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is False
        assert result["nlp_extractor_max_tokens"] == 512
        assert result["nlp_extractor_confidence_threshold"] == 0.8
        assert result["nlp_extractor_text_sample_chars"] == 3000
        assert result["nlp_extractor_max_files"] == 20
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is True
        assert result["nlp_extractor_max_tokens"] == 256
        assert result["nlp_extractor_confidence_threshold"] == 0.7
        assert result["nlp_extractor_max_files"] == 15
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  nist_controls:\n    nlp_extractor:\n      max_files: 25\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_max_files"] == 25
        assert result["nlp_extractor_confidence_threshold"] == 0.7  # default
        assert result["nlp_extractor_max_tokens"] == 256             # default
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_max_files"] == 15
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _check_control_ids_in_code
# ---------------------------------------------------------------------------

class TestCheckControlIdsInCode:
    def test_passes_on_explicit_control_id_in_comment(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        _write(tmp_path, "auth.py", "# NIST: AC-2\ndef create_user(): pass\n")
        result = _check_control_ids_in_code(tmp_path)
        assert result.passed
        assert "auth.py" in result.message

    def test_passes_on_complex_control_id_with_enhancement(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        _write(tmp_path, "audit.py", "# AU-12(1) logging enabled\nlog = True\n")
        result = _check_control_ids_in_code(tmp_path)
        assert result.passed

    def test_fails_when_no_control_ids_and_nlp_disabled(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "main.py", "x = 1\n")
        result = _check_control_ids_in_code(tmp_path)
        assert not result.passed
        assert result.details  # remediation hint present

    def test_passes_on_multiple_control_ids(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        _write(tmp_path, "sec.py", "# Implements SC-28, AU-12, and AC-17\npass\n")
        result = _check_control_ids_in_code(tmp_path)
        assert result.passed
        assert "1 file" in result.message

    def test_nlp_path_uses_configured_max_files(self, tmp_path, monkeypatch):
        """NLP path should only scan up to nlp_extractor_max_files files."""
        call_count = []

        def fake_nlp(text, task):
            call_count.append(1)
            return None  # simulate no NLP result

        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_nlp_extract_nist_refs", fake_nlp)
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=True, nlp_extractor_max_files=3)

        for i in range(10):
            _write(tmp_path, f"f{i:02d}.py", "x = 1\n")

        _check_control_ids_in_code(tmp_path)
        # NLP should be called at most max_files (3) times, not for all 10 files
        assert len(call_count) <= 3

    def test_nlp_result_accepted_above_confidence_threshold(self, tmp_path, monkeypatch):
        _write(tmp_path, "service.py", "# access control logic\ndef check(): pass\n")

        def fake_nlp(text, task):
            return {"found": True, "refs": ["AC-2", "AU-12"], "confidence": 0.9}

        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_nlp_extract_nist_refs", fake_nlp)
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=True, nlp_extractor_confidence_threshold=0.7)

        result = _check_control_ids_in_code(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_result_rejected_below_confidence_threshold(self, tmp_path, monkeypatch):
        _write(tmp_path, "service.py", "x = 1\n")

        def fake_nlp(text, task):
            return {"found": True, "refs": ["AC-2"], "confidence": 0.5}

        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_nlp_extract_nist_refs", fake_nlp)
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=True, nlp_extractor_confidence_threshold=0.7)

        result = _check_control_ids_in_code(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_nist_in_docs
# ---------------------------------------------------------------------------

class TestCheckNistInDocs:
    def test_passes_on_nist_pattern_in_readme(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        _write(tmp_path, "README.md", "# My Project\n\nCompliant with NIST SP 800-53.\n")
        result = _check_nist_in_docs(tmp_path)
        assert result.passed
        assert "README.md" in result.message

    def test_passes_on_control_id_in_docs(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        _write(tmp_path, "docs/architecture.md", "## Security\n\nImplements AC-2 and AU-12.\n")
        result = _check_nist_in_docs(tmp_path)
        assert result.passed

    def test_fails_when_no_nist_references_and_nlp_disabled(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "README.md", "# My Project\n\nNo compliance refs.\n")
        result = _check_nist_in_docs(tmp_path)
        assert not result.passed
        assert result.details

    def test_nlp_path_uses_configured_max_files(self, tmp_path, monkeypatch):
        call_count = []

        def fake_nlp(text, task):
            call_count.append(1)
            return None

        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_nlp_extract_nist_refs", fake_nlp)
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=True, nlp_extractor_max_files=2)

        for i in range(6):
            _write(tmp_path, f"docs/guide{i}.md", "# Guide\nNo refs.\n")

        _check_nist_in_docs(tmp_path)
        assert len(call_count) <= 2

    def test_nlp_natural_language_reference_accepted(self, tmp_path, monkeypatch):
        _write(tmp_path, "docs/compliance.md", "# Compliance\nAccess control framework.\n")

        def fake_nlp(text, task):
            return {"found": True, "refs": ["NIST 800-53"], "confidence": 0.85}

        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_nlp_extract_nist_refs", fake_nlp)
        _patch_thresholds(monkeypatch, nlp_extractor_enabled=True, nlp_extractor_confidence_threshold=0.7)

        result = _check_nist_in_docs(tmp_path)
        assert result.passed
        assert "NLP" in result.message


# ---------------------------------------------------------------------------
# _check_ssp_present
# ---------------------------------------------------------------------------

class TestCheckSspPresent:
    def test_passes_when_ssp_md_exists(self, tmp_path):
        _write(tmp_path, "docs/ssp-v1.md", "# System Security Plan\n")
        result = _check_ssp_present(tmp_path)
        assert result.passed

    def test_passes_when_compliance_dir_exists(self, tmp_path):
        (tmp_path / "compliance").mkdir()
        result = _check_ssp_present(tmp_path)
        assert result.passed

    def test_passes_when_docs_compliance_dir_exists(self, tmp_path):
        (tmp_path / "docs" / "compliance").mkdir(parents=True)
        result = _check_ssp_present(tmp_path)
        assert result.passed

    def test_fails_when_no_ssp_artifacts(self, tmp_path):
        result = _check_ssp_present(tmp_path)
        assert not result.passed
        assert result.details


# ---------------------------------------------------------------------------
# _check_crosswalk_config
# ---------------------------------------------------------------------------

class TestCheckCrosswalkConfig:
    def test_passes_on_crosswalk_yaml(self, tmp_path):
        _write(tmp_path, "args/crosswalk.yaml", "nist_to_fedramp: {}\n")
        result = _check_crosswalk_config(tmp_path)
        assert result.passed

    def test_passes_on_crosswalk_engine_import_in_code(self, tmp_path):
        _write(tmp_path, "tools/compliance.py", "from crosswalk import CrosswalkEngine\n")
        result = _check_crosswalk_config(tmp_path)
        assert result.passed

    def test_passes_on_fedramp_reference_in_code(self, tmp_path):
        _write(tmp_path, "tools/fedramp.py", "# fedramp compliance mapping\nx = 1\n")
        result = _check_crosswalk_config(tmp_path)
        assert result.passed

    def test_fails_when_no_crosswalk_config(self, tmp_path):
        result = _check_crosswalk_config(tmp_path)
        assert not result.passed
        assert result.details


# ---------------------------------------------------------------------------
# _nlp_extract_nist_refs — unit tests for the NLP extractor helper
# ---------------------------------------------------------------------------

class TestNlpExtractNistRefs:
    def test_returns_none_when_nlp_disabled(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {
            **mod._DEFAULTS,
            "nlp_extractor_enabled": False,
        })
        result = _nlp_extract_nist_refs("some text", "find refs")
        assert result is None

    def test_returns_none_when_no_api_key(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {**mod._DEFAULTS, "nlp_extractor_enabled": True})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        result = _nlp_extract_nist_refs("some text", "find refs")
        assert result is None

    def test_returns_none_on_import_error(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.nist_controls as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: {**mod._DEFAULTS, "nlp_extractor_enabled": True})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch.dict("sys.modules", {"tools.llm.anthropic_provider": None}):
            result = _nlp_extract_nist_refs("some text", "find refs")
        assert result is None


# ---------------------------------------------------------------------------
# PILLAR integration — criteria list
# ---------------------------------------------------------------------------

class TestNistControlsPillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {
            "nist-control-ids-in-code",
            "nist-in-docs",
            "ssp-present",
            "crosswalk-config",
        }

    def test_pillar_id(self):
        assert PILLAR.id == "nist-controls"

    def test_pillar_score_all_pass(self, tmp_path, monkeypatch):
        _patch_thresholds(monkeypatch)
        # Set up artifacts satisfying all 4 criteria
        _write(tmp_path, "auth.py", "# AC-2 user management\ndef create_user(): pass\n")
        _write(tmp_path, "README.md", "Compliant with NIST SP 800-53.\n")
        (tmp_path / "docs" / "compliance").mkdir(parents=True)
        _write(tmp_path, "args/crosswalk.yaml", "nist_to_fedramp: {}\n")

        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]
