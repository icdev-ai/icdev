# CUI // SP-CTI
"""Tests for tools.ai_augmentation.nlp_extractor — NLP-based input_ref classifier."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ai_augmentation.nlp_extractor import (
    _regex_fallback,
    classify_input_ref,
)


# ---------------------------------------------------------------------------
# Regex fallback (deterministic, no LLM)
# ---------------------------------------------------------------------------


class TestRegexFallback:
    def test_https_url_is_git(self):
        assert _regex_fallback("https://github.com/org/repo") == "git_url"

    def test_http_url_is_git(self):
        assert _regex_fallback("http://gitlab.example.com/org/repo.git") == "git_url"

    def test_ssh_git_url_is_git(self):
        assert _regex_fallback("git@github.com:org/repo.git") == "git_url"

    def test_absolute_unix_path_is_local(self):
        assert _regex_fallback("/home/user/my-project") == "local_path"

    def test_absolute_windows_path_is_local(self):
        assert _regex_fallback("C:\\Users\\user\\ICDev") == "local_path"

    def test_relative_path_is_local(self):
        assert _regex_fallback("./my-project") == "local_path"

    def test_bare_name_is_local(self):
        assert _regex_fallback("my-project") == "local_path"

    def test_empty_string_is_local(self):
        assert _regex_fallback("") == "local_path"


# ---------------------------------------------------------------------------
# classify_input_ref — LLM path (mocked)
# ---------------------------------------------------------------------------


class TestClassifyInputRefLLM:
    def _make_mock_response(self, label: str) -> MagicMock:
        """Build a fake LLMRouter response for the given label."""
        resp = MagicMock()
        resp.content = f'{{"input_type": "{label}", "confidence": 0.95, "reason": "test"}}'
        return resp

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_classifies_https_as_git(self, mock_llm):
        mock_llm.return_value = "git_url"
        result = classify_input_ref("https://github.com/org/repo")
        assert result == "git_url"
        mock_llm.assert_called_once()

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_classifies_local_path(self, mock_llm):
        mock_llm.return_value = "local_path"
        result = classify_input_ref("/home/user/project")
        assert result == "local_path"

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_failure_falls_back_to_regex(self, mock_llm):
        mock_llm.side_effect = RuntimeError("LLM unavailable")
        result = classify_input_ref("https://github.com/org/repo")
        assert result == "git_url"

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_returns_unknown_falls_back_to_regex(self, mock_llm):
        mock_llm.return_value = "unknown"
        result = classify_input_ref("https://github.com/org/repo")
        assert result == "git_url"

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_classifies_ssh_url(self, mock_llm):
        mock_llm.return_value = "git_url"
        result = classify_input_ref("git@github.com:org/repo.git")
        assert result == "git_url"

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_classifies_windows_path(self, mock_llm):
        mock_llm.return_value = "local_path"
        result = classify_input_ref("C:\\AI\\ICDev")
        assert result == "local_path"

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_llm_receives_input_ref(self, mock_llm):
        mock_llm.return_value = "local_path"
        classify_input_ref("some-path")
        args, _ = mock_llm.call_args
        assert "some-path" in args[0]

    def test_empty_input_returns_local_path(self):
        result = classify_input_ref("")
        assert result == "local_path"


# ---------------------------------------------------------------------------
# Return value contract
# ---------------------------------------------------------------------------


class TestReturnValueContract:
    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_always_returns_valid_type(self, mock_llm):
        valid_types = {"git_url", "local_path"}
        for label in ("git_url", "local_path", "unknown", "garbage"):
            mock_llm.return_value = label
            result = classify_input_ref("some-input")
            assert result in valid_types

    @patch("tools.ai_augmentation.nlp_extractor._llm_classify")
    def test_never_returns_none(self, mock_llm):
        mock_llm.return_value = None  # type: ignore[assignment]
        result = classify_input_ref("some-input")
        assert result is not None
        assert isinstance(result, str)
