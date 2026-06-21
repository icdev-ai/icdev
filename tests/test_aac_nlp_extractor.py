# CUI // SP-CTI
"""Tests for tools.ai_augmentation.nlp_extractor.

Validates that:
  - classify_input_ref() returns correct types for deterministic inputs
  - The regex fallback (_regex_classify) produces the correct labels
  - LLM errors are handled gracefully with regex fallback
  - The LLM response parser handles malformed JSON without crashing
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_augmentation.nlp_extractor import (
    _parse_llm_response,
    _regex_classify,
    classify_input_ref,
)


# ---------------------------------------------------------------------------
# _regex_classify — deterministic fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref,expected", [
    ("https://github.com/org/repo", "git_url"),
    ("http://gitlab.internal/team/project.git", "git_url"),
    ("git@github.com:org/repo.git", "git_url"),
    ("ssh://git@bitbucket.org/proj/repo", "git_url"),
    ("git://github.com/user/repo.git", "git_url"),
    ("/home/user/projects/myapp", "local_path"),
    ("C:\\AI\\ICDev", "local_path"),
    ("./relative/path", "local_path"),
    ("org/repo", "local_path"),  # ambiguous — regex picks local_path
])
def test_regex_classify(ref, expected):
    assert _regex_classify(ref) == expected


# ---------------------------------------------------------------------------
# _parse_llm_response — JSON parsing
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    raw = '{"input_type": "git_url", "confidence": 0.97, "reason": "starts with https://"}'
    assert _parse_llm_response(raw) == "git_url"


def test_parse_json_in_markdown_fence():
    raw = '```json\n{"input_type": "local_path", "confidence": 0.9, "reason": "absolute path"}\n```'
    assert _parse_llm_response(raw) == "local_path"


def test_parse_unknown_type_returns_unknown():
    raw = '{"input_type": "ftp_server", "confidence": 0.5, "reason": "ftp prefix"}'
    assert _parse_llm_response(raw) == "unknown"


def test_parse_missing_field_returns_unknown():
    raw = '{"confidence": 0.8, "reason": "no type field"}'
    assert _parse_llm_response(raw) == "unknown"


def test_parse_totally_malformed_returns_unknown():
    assert _parse_llm_response("not json at all") == "unknown"


def test_parse_empty_string_returns_unknown():
    assert _parse_llm_response("") == "unknown"


# ---------------------------------------------------------------------------
# classify_input_ref — LLM path (mocked)
# ---------------------------------------------------------------------------


def _make_llm_response(input_type: str):
    resp = MagicMock()
    resp.content = f'{{"input_type": "{input_type}", "confidence": 0.95, "reason": "test"}}'
    return resp


def test_classify_uses_llm_result():
    with patch("tools.ai_augmentation.nlp_extractor._llm_classify", return_value="git_url"):
        assert classify_input_ref("https://github.com/org/repo") == "git_url"


def test_classify_llm_local_path():
    with patch("tools.ai_augmentation.nlp_extractor._llm_classify", return_value="local_path"):
        assert classify_input_ref("/home/user/project") == "local_path"


def test_classify_llm_ambiguous_shorthand():
    # GitHub shorthand "org/repo" — LLM recognizes as git_url; regex would say local_path
    with patch("tools.ai_augmentation.nlp_extractor._llm_classify", return_value="git_url"):
        assert classify_input_ref("org/repo") == "git_url"


# ---------------------------------------------------------------------------
# classify_input_ref — fallback when LLM unavailable
# ---------------------------------------------------------------------------


def test_classify_falls_back_to_regex_on_llm_error():
    from tools.llm.router import LLMUnavailableError

    def _raise(*_a, **_kw):
        raise LLMUnavailableError("no llm")

    with patch("tools.ai_augmentation.nlp_extractor._llm_classify", side_effect=_raise):
        assert classify_input_ref("https://github.com/org/repo") == "git_url"
        assert classify_input_ref("/local/path") == "local_path"


def test_classify_falls_back_on_generic_exception():
    with patch("tools.ai_augmentation.nlp_extractor._llm_classify", side_effect=RuntimeError("timeout")):
        assert classify_input_ref("git@github.com:org/repo.git") == "git_url"


# ---------------------------------------------------------------------------
# classify_input_ref — edge cases
# ---------------------------------------------------------------------------


def test_classify_empty_string_returns_unknown():
    assert classify_input_ref("") == "unknown"


def test_classify_whitespace_only_returns_unknown():
    assert classify_input_ref("   ") == "unknown"
