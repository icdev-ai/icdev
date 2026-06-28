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
    get_speculative_decoder,
)


# ---------------------------------------------------------------------------
# SpeculativeConfig defaults
# ---------------------------------------------------------------------------

class TestSpeculativeConfig:
    def test_defaults(self):
        cfg = SpeculativeConfig()
        assert cfg.enabled is False
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

    def test_returns_none_when_config_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")
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
