"""Tests for _NLPExtractor and _parse_json_array in tools/genesis/stagnation_detector.py."""
import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.stagnation_detector import (
    _NLPExtractor,
    _STOPWORDS,
    _parse_json_array,
    _tokenize,
)


# ── _NLPExtractor.extract ──────────────────────────────────────────────────


def test_extract_empty_string():
    assert _NLPExtractor.extract("") == set()


def test_extract_falls_back_to_tokenize_when_no_llm():
    """When LLM is unavailable, extract() returns the same set as _tokenize()."""
    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = True
        instance.has_any_llm.return_value = False
        _NLPExtractor._cache.clear()
        text = "detect phantom improvements and reflex plateau"
        result = _NLPExtractor.extract(text)
        expected = _tokenize(text)
        assert result == expected


def test_extract_uses_llm_keywords_when_available():
    """When LLM returns a valid JSON array, extract() uses those keywords."""
    mock_resp = MagicMock()
    mock_resp.content = '["stagnation", "oscillation", "reflex", "plateau"]'

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()
        result = _NLPExtractor.extract("some text about stagnation reflex")
        assert "stagnation" in result
        assert "oscillation" in result
        assert "reflex" in result


def test_extract_caches_result():
    """Second call with same text hits the cache; LLM not called again."""
    _NLPExtractor._cache.clear()
    text = "oscillation stagnation detection unique text abc123"
    key = hashlib.sha256(text.encode()).hexdigest()[:16]

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = True
        instance.has_any_llm.return_value = False

        result1 = _NLPExtractor.extract(text)
        assert key in _NLPExtractor._cache

        result2 = _NLPExtractor.extract(text)
        assert result1 == result2
        # LLM path entered only once (second call hits cache before LLM check)
        assert instance.is_no_llm_mode.call_count == 1


def test_extract_stopwords_excluded():
    """Stopwords are excluded from NLP extraction results."""
    _NLPExtractor._cache.clear()
    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = True
        result = _NLPExtractor.extract("the reflex was detected in the system")
    assert "the" not in result
    assert "was" not in result
    assert "in" not in result


def test_extract_llm_invalid_json_falls_back():
    """If LLM returns invalid JSON, extract() falls back to _tokenize()."""
    mock_resp = MagicMock()
    mock_resp.content = "not valid json at all"

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()
        text = "stagnation reflex plateau detected"
        result = _NLPExtractor.extract(text)
        expected = _tokenize(text)
        assert result == expected


def test_extract_llm_exception_falls_back():
    """If LLM raises an exception, extract() falls back to _tokenize()."""
    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.side_effect = RuntimeError("connection refused")
        _NLPExtractor._cache.clear()
        text = "diminishing returns slope threshold"
        result = _NLPExtractor.extract(text)
        expected = _tokenize(text)
        assert result == expected


# ── _parse_json_array ──────────────────────────────────────────────────────


def test_parse_json_array_clean():
    text = '[{"description": "approach A"}, {"description": "approach B"}]'
    result = _parse_json_array(text)
    assert result is not None
    assert len(result) == 2
    assert result[0]["description"] == "approach A"


def test_parse_json_array_embedded_in_text():
    text = 'Here are alternatives: [{"description": "rewrite"}, {"description": "simplify"}] done.'
    result = _parse_json_array(text)
    assert result is not None
    assert len(result) == 2
    assert result[0]["description"] == "rewrite"


def test_parse_json_array_no_array():
    result = _parse_json_array("No JSON here at all")
    assert result is None


def test_parse_json_array_malformed():
    result = _parse_json_array("[{bad json{")
    assert result is None


def test_parse_json_array_nested_brackets():
    text = '[{"description": "use nested [brackets] here", "tags": ["a", "b"]}]'
    result = _parse_json_array(text)
    assert result is not None
    assert "description" in result[0]
    assert "tags" in result[0]


def test_parse_json_array_empty_array():
    result = _parse_json_array("[]")
    assert result == []


def test_parse_json_array_text_before_and_after():
    text = "prefix text\n[{\"description\": \"try lateral approach\"}]\nsuffix text"
    result = _parse_json_array(text)
    assert result is not None
    assert result[0]["description"] == "try lateral approach"
