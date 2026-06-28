"""Tests for icdev.tools.llm.speculative_decoder."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.llm.speculative_decoder import (
    SpeculativeConfig,
    SpeculativeDecoder,
    SpeculativeDecodingUnavailable,
    _http_get,
    _detect_provider,
    _discover_draft_model,
    _is_enabled,
    _resolve_endpoint,
    get_speculative_decoder,
)


# ---------------------------------------------------------------------------
# SpeculativeConfig defaults
# ---------------------------------------------------------------------------

class TestSpeculativeConfig:
    def test_defaults(self):
        cfg = SpeculativeConfig()
        assert cfg.enabled == "auto"   # changed: was False, now "auto"
        assert cfg.draft_model == ""
        assert cfg.draft_tokens == 5
        assert cfg.verification_ratio == pytest.approx(0.7)
        assert cfg.fallback_on_failure is True
        assert cfg.target_endpoint == "http://localhost:11434"

    def test_custom_values(self):
        cfg = SpeculativeConfig(
            enabled=True,
            draft_model="qwen3-0.6b",
            draft_tokens=8,
            verification_ratio=0.5,
            fallback_on_failure=False,
            target_endpoint="http://localhost:22434",
        )
        assert cfg.enabled is True
        assert cfg.draft_model == "qwen3-0.6b"
        assert cfg.draft_tokens == 8
        assert cfg.verification_ratio == pytest.approx(0.5)
        assert cfg.fallback_on_failure is False
        assert cfg.target_endpoint == "http://localhost:22434"


# ---------------------------------------------------------------------------
# Auto-detection helpers
# ---------------------------------------------------------------------------

class TestIsEnabled:
    def test_bool_true(self):
        assert _is_enabled({"enabled": True}) is True

    def test_bool_false(self):
        assert _is_enabled({"enabled": False}) is False

    def test_string_true(self):
        assert _is_enabled({"enabled": "true"}) is True

    def test_string_false(self):
        assert _is_enabled({"enabled": "false"}) is False

    def test_string_auto(self):
        assert _is_enabled({"enabled": "auto"}) is None

    def test_missing_key_defaults_to_auto(self):
        assert _is_enabled({}) is None


class TestResolveEndpoint:
    def test_ollama_base_url_wins(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://myhost:11434")
        assert _resolve_endpoint({}) == "http://myhost:11434"

    def test_expands_env_var_pattern(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.setenv("MY_URL", "http://other:9999")
        result = _resolve_endpoint({"target_endpoint": "${MY_URL:-http://localhost:11434}"})
        assert result == "http://other:9999"

    def test_falls_back_to_config_value(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        result = _resolve_endpoint({"target_endpoint": "http://custom:12345"})
        assert result == "http://custom:12345"

    def test_falls_back_to_localhost(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        assert _resolve_endpoint({}) == "http://localhost:11434"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://myhost:11434/")
        assert _resolve_endpoint({}) == "http://myhost:11434"


class TestDetectProvider:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "kimi")
        assert _detect_provider({}) == "kimi"

    def test_reads_ollama_from_providers(self, monkeypatch):
        monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)
        cfg = {"providers": {"ollama_local": {}}}
        assert _detect_provider(cfg) == "ollama"

    def test_returns_empty_when_no_info(self, monkeypatch):
        monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)
        assert _detect_provider({}) == ""


class TestDiscoverDraftModel:
    def test_returns_empty_when_endpoint_down(self):
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(0, None)):
            assert _discover_draft_model("http://localhost:11434") == ""

    def test_finds_dspark_model(self):
        body = {"models": [{"name": "dspark_qwen3_4b:latest"}, {"name": "llama3:latest"}]}
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, body)):
            assert _discover_draft_model("http://localhost:11434") == "dspark_qwen3_4b:latest"

    def test_finds_eagle3_model(self):
        body = {"models": [{"name": "eagle3_qwen3_8b:latest"}]}
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, body)):
            assert _discover_draft_model("http://localhost:11434") == "eagle3_qwen3_8b:latest"

    def test_returns_empty_when_no_draft_model_loaded(self):
        body = {"models": [{"name": "llama3:latest"}, {"name": "qwen3:latest"}]}
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, body)):
            assert _discover_draft_model("http://localhost:11434") == ""


class TestGetSpeculativeDecoderAutoDetect:
    def test_returns_none_for_kimi_provider(self, monkeypatch):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "kimi")
        assert get_speculative_decoder() is None

    def test_returns_none_for_anthropic_provider(self, monkeypatch):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "anthropic")
        assert get_speculative_decoder() is None

    def test_auto_discovers_draft_model_when_ollama(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        cfg_file = tmp_path / "llm.yaml"
        cfg_file.write_text("speculative_decoding:\n  enabled: auto\n  draft_model: ''\n", encoding="utf-8")
        body = {"models": [{"name": "dspark_qwen3_4b:latest"}]}
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, body)):
            result = get_speculative_decoder(config_path=cfg_file)
        assert result is not None
        assert result.config.draft_model == "dspark_qwen3_4b:latest"

    def test_returns_none_when_auto_and_no_draft_model_available(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        cfg_file = tmp_path / "llm.yaml"
        cfg_file.write_text("speculative_decoding:\n  enabled: auto\n", encoding="utf-8")
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(0, None)):
            result = get_speculative_decoder(config_path=cfg_file)
        assert result is None

    def test_uses_pinned_draft_model_without_discovery(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        cfg_file = tmp_path / "llm.yaml"
        cfg_file.write_text(
            "speculative_decoding:\n  enabled: true\n  draft_model: my-draft\n  target_endpoint: 'http://localhost:11434'\n",
            encoding="utf-8",
        )
        result = get_speculative_decoder(config_path=cfg_file)
        assert result is not None
        assert result.config.draft_model == "my-draft"


# ---------------------------------------------------------------------------
# SpeculativeDecoder.is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_false_when_no_draft_model(self):
        cfg = SpeculativeConfig(draft_model="")
        decoder = SpeculativeDecoder(cfg)
        assert decoder.is_available() is False

    def test_returns_false_when_endpoint_unreachable(self):
        cfg = SpeculativeConfig(
            draft_model="qwen3-0.6b",
            target_endpoint="http://127.0.0.1:19999",  # unlikely to be listening
        )
        decoder = SpeculativeDecoder(cfg)
        # _check_ollama_available will return False (no server running)
        with patch.object(decoder, "_check_ollama_available", return_value=False):
            assert decoder.is_available() is False

    def test_returns_false_when_model_not_in_tags(self):
        cfg = SpeculativeConfig(draft_model="missing-model", target_endpoint="http://localhost:11434")
        decoder = SpeculativeDecoder(cfg)
        tags_response = {"models": [{"name": "other-model:latest"}]}
        with patch.object(decoder, "_check_ollama_available", return_value=True):
            with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, tags_response)):
                assert decoder.is_available() is False

    def test_returns_true_when_model_in_tags(self):
        cfg = SpeculativeConfig(draft_model="qwen3-0.6b", target_endpoint="http://localhost:11434")
        decoder = SpeculativeDecoder(cfg)
        tags_response = {"models": [{"name": "qwen3-0.6b:latest"}]}
        with patch.object(decoder, "_check_ollama_available", return_value=True):
            with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, tags_response)):
                assert decoder.is_available() is True

    def test_returns_false_when_tags_endpoint_returns_non_200(self):
        cfg = SpeculativeConfig(draft_model="some-model", target_endpoint="http://localhost:11434")
        decoder = SpeculativeDecoder(cfg)
        with patch.object(decoder, "_check_ollama_available", return_value=True):
            with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(500, None)):
                assert decoder.is_available() is False


# ---------------------------------------------------------------------------
# SpeculativeDecoder._check_ollama_available
# ---------------------------------------------------------------------------

class TestCheckOllamaAvailable:
    def test_returns_true_on_200(self):
        cfg = SpeculativeConfig(draft_model="x", target_endpoint="http://localhost:11434")
        decoder = SpeculativeDecoder(cfg)
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(200, {})):
            assert decoder._check_ollama_available() is True

    def test_returns_false_on_non_200(self):
        cfg = SpeculativeConfig(draft_model="x", target_endpoint="http://localhost:11434")
        decoder = SpeculativeDecoder(cfg)
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(0, None)):
            assert decoder._check_ollama_available() is False


# ---------------------------------------------------------------------------
# SpeculativeDecoder.decode — fallback behavior
# ---------------------------------------------------------------------------

class TestDecodeFallback:
    def test_falls_back_to_standard_when_unavailable_and_fallback_enabled(self):
        cfg = SpeculativeConfig(draft_model="x", fallback_on_failure=True)
        decoder = SpeculativeDecoder(cfg)
        with patch.object(decoder, "is_available", return_value=False):
            with patch.object(decoder, "_standard_decode", return_value=("hello world", {"method": "standard", "tokens_generated": 2, "draft_tokens_proposed": 0, "draft_tokens_accepted": 0, "acceptance_rate": 0.0})) as mock_std:
                text, stats = decoder.decode("prompt")
                assert text == "hello world"
                assert stats["method"] == "standard"
                mock_std.assert_called_once()

    def test_raises_when_unavailable_and_no_fallback(self):
        cfg = SpeculativeConfig(draft_model="x", fallback_on_failure=False)
        decoder = SpeculativeDecoder(cfg)
        with patch.object(decoder, "is_available", return_value=False):
            with pytest.raises(SpeculativeDecodingUnavailable):
                decoder.decode("prompt")

    def test_falls_back_when_draft_request_fails(self):
        cfg = SpeculativeConfig(draft_model="x", fallback_on_failure=True)
        decoder = SpeculativeDecoder(cfg)
        with patch.object(decoder, "is_available", return_value=True):
            with patch("icdev.tools.llm.speculative_decoder._http_post", return_value=(500, None)):
                with patch.object(decoder, "_standard_decode", return_value=("fallback", {"method": "standard", "tokens_generated": 1, "draft_tokens_proposed": 0, "draft_tokens_accepted": 0, "acceptance_rate": 0.0})) as mock_std:
                    text, stats = decoder.decode("prompt")
                    assert text == "fallback"
                    mock_std.assert_called_once()


# ---------------------------------------------------------------------------
# get_speculative_decoder
# ---------------------------------------------------------------------------

class TestGetSpeculativeDecoder:
    def test_returns_none_when_provider_not_ollama(self, monkeypatch):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "anthropic")
        result = get_speculative_decoder()
        assert result is None

    def test_returns_none_when_enabled_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        cfg_file = tmp_path / "llm_config.yaml"
        cfg_file.write_text("speculative_decoding:\n  enabled: false\n  draft_model: qwen3\n", encoding="utf-8")
        result = get_speculative_decoder(config_path=cfg_file)
        assert result is None

    def test_returns_decoder_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        cfg_file = tmp_path / "llm_config.yaml"
        cfg_file.write_text(
            "speculative_decoding:\n  enabled: true\n  draft_model: qwen3-0.6b\n  draft_tokens: 5\n  verification_ratio: 0.7\n  fallback_on_failure: true\n  target_endpoint: 'http://localhost:11434'\n",
            encoding="utf-8",
        )
        result = get_speculative_decoder(config_path=cfg_file)
        assert result is not None
        assert isinstance(result, SpeculativeDecoder)
        assert result.config.draft_model == "qwen3-0.6b"

    def test_returns_none_when_config_missing_and_no_draft_model_in_ollama(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        # No config file + Ollama not reachable → None
        with patch("icdev.tools.llm.speculative_decoder._http_get", return_value=(0, None)):
            result = get_speculative_decoder(config_path=tmp_path / "nonexistent.yaml")
        assert result is None

    def test_expands_env_var_in_endpoint(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://myhost:11434")
        cfg_file = tmp_path / "llm_config.yaml"
        cfg_file.write_text(
            "speculative_decoding:\n  enabled: true\n  draft_model: x\n  target_endpoint: '${OLLAMA_BASE_URL:-http://localhost:11434}'\n",
            encoding="utf-8",
        )
        result = get_speculative_decoder(config_path=cfg_file)
        assert result is not None
        assert result.config.target_endpoint == "http://myhost:11434"


# ---------------------------------------------------------------------------
# SpeculativeDecodingUnavailable exception
# ---------------------------------------------------------------------------

class TestSpeculativeDecodingUnavailableException:
    def test_is_runtime_error(self):
        exc = SpeculativeDecodingUnavailable("not available")
        assert isinstance(exc, RuntimeError)

    def test_message(self):
        exc = SpeculativeDecodingUnavailable("test message")
        assert "test message" in str(exc)
