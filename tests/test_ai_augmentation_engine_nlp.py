# CUI // SP-CTI
"""Tests for NLP extractor in tools/ai_augmentation/engine.py."""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tools.ai_augmentation.engine import (
    _build_summary,
    _load_engine_nlp_config,
    _nlp_extract_ref_name,
)


class TestLoadEngineNlpConfig(unittest.TestCase):
    def setUp(self):
        _load_engine_nlp_config.cache_clear()

    def tearDown(self):
        _load_engine_nlp_config.cache_clear()

    def test_returns_defaults_when_config_missing(self):
        """Falls back to hard-coded defaults when config file is absent."""
        with patch(
            "tools.ai_augmentation.engine._ENGINE_ARGS_PATH",
            pathlib.Path("/nonexistent/path.yaml"),
        ):
            _load_engine_nlp_config.cache_clear()
            cfg = _load_engine_nlp_config()
        self.assertIn("nlp_extractor_enabled", cfg)
        self.assertTrue(cfg["nlp_extractor_enabled"])
        self.assertEqual(cfg["nlp_extractor_model"], "claude-haiku-4-5-20251001")
        self.assertEqual(cfg["nlp_extractor_max_tokens"], 128)
        self.assertAlmostEqual(cfg["nlp_extractor_confidence_threshold"], 0.7)

    def test_returns_all_required_keys(self):
        """Config dict always contains all required keys."""
        cfg = _load_engine_nlp_config()
        required = {
            "nlp_extractor_enabled",
            "nlp_extractor_model",
            "nlp_extractor_max_tokens",
            "nlp_extractor_confidence_threshold",
        }
        self.assertTrue(required.issubset(cfg.keys()))


class TestNlpExtractRefName(unittest.TestCase):
    def setUp(self):
        _load_engine_nlp_config.cache_clear()

    def tearDown(self):
        _load_engine_nlp_config.cache_clear()

    def test_returns_none_when_disabled(self):
        """Returns None when nlp_extractor_enabled is False."""
        with patch(
            "tools.ai_augmentation.engine._load_engine_nlp_config",
            return_value={
                "nlp_extractor_enabled": False,
                "nlp_extractor_model": "claude-haiku-4-5-20251001",
                "nlp_extractor_max_tokens": 128,
                "nlp_extractor_confidence_threshold": 0.7,
            },
        ):
            result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertIsNone(result)

    def test_returns_none_when_no_api_key(self):
        """Returns None when ANTHROPIC_API_KEY is absent."""
        env = {k: v for k, v in __import__("os").environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertIsNone(result)

    def test_returns_none_when_anthropic_unavailable(self):
        """Returns None when anthropic package is not installed."""
        with patch.dict("sys.modules", {"anthropic": None}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertIsNone(result)

    def test_returns_name_on_successful_extraction(self):
        """Returns NLP-extracted project name when API call succeeds with high confidence."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"name": "my-project", "confidence": 0.95}')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                _load_engine_nlp_config.cache_clear()
                result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertEqual(result, "my-project")

    def test_returns_none_when_confidence_below_threshold(self):
        """Returns None when NLP confidence is below the configured threshold."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"name": "my-project", "confidence": 0.3}')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                _load_engine_nlp_config.cache_clear()
                result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertIsNone(result)

    def test_returns_none_when_api_raises_exception(self):
        """Returns None when the Anthropic API call raises an exception."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("network error")
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                _load_engine_nlp_config.cache_clear()
                result = _nlp_extract_ref_name("https://github.com/org/my-project.git")
        self.assertIsNone(result)

    def test_returns_none_on_malformed_json_response(self):
        """Returns None when the LLM returns non-JSON or JSON with missing keys."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="here is the project name")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                _load_engine_nlp_config.cache_clear()
                result = _nlp_extract_ref_name("/path/to/repo")
        self.assertIsNone(result)


class TestBuildSummaryWithNlpFallback(unittest.TestCase):
    def test_uses_nlp_name_when_extraction_succeeds(self):
        """Uses NLP-extracted name in the summary when NLP returns a name."""
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value="nlp-extracted-name",
        ):
            result = _build_summary("https://github.com/org/my-project.git", [])
        self.assertIn("nlp-extracted-name", result)

    def test_regex_fallback_for_git_url_when_nlp_unavailable(self):
        """Falls back to regex extraction for git URL when NLP returns None."""
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value=None,
        ):
            result = _build_summary("https://github.com/org/my-project.git", [])
        self.assertIn("my-project", result)

    def test_regex_fallback_for_local_path_when_nlp_unavailable(self):
        """Falls back to pathlib.Path.name extraction when NLP returns None."""
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value=None,
        ):
            result = _build_summary("/home/user/my-repo", [])
        self.assertIn("my-repo", result)

    def test_no_opportunities_message_without_nlp(self):
        """Returns 'no AI-augmentable patterns' when opp_rows is empty."""
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value=None,
        ):
            result = _build_summary("https://github.com/org/repo.git", [])
        self.assertIn("no AI-augmentable patterns", result)

    def test_opportunity_count_in_summary(self):
        """Summary includes correct opportunity count when opps are present."""
        opps = [
            {"pattern_type": "regex_user_input", "ai_paradigm": "nlp_extractor"},
            {"pattern_type": "regex_user_input", "ai_paradigm": "nlp_extractor"},
            {"pattern_type": "nested_conditionals", "ai_paradigm": "ml_classifier"},
        ]
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value=None,
        ):
            result = _build_summary("https://github.com/org/repo.git", opps)
        self.assertIn("3 augmentation opportunities", result)

    def test_nlp_name_takes_precedence_over_regex(self):
        """NLP name is used even when regex would produce a different name."""
        with patch(
            "tools.ai_augmentation.engine._nlp_extract_ref_name",
            return_value="intelligent-name",
        ):
            result = _build_summary("https://github.com/org/different-url-name.git", [])
        self.assertIn("intelligent-name", result)
        self.assertNotIn("different-url-name", result)


if __name__ == "__main__":
    unittest.main()
