"""Tests for _NLPExtractor in tools/genesis/convergence.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.convergence import _NLPExtractor, _tokenize


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
    mock_resp.content = '["convergence", "drift", "metric", "plateau"]'

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()
        result = _NLPExtractor.extract("some text about convergence drift")
        assert "convergence" in result
        assert "drift" in result
        assert "metric" in result


def test_extract_excludes_stopwords_from_llm_output():
    """LLM-returned stopwords are stripped from the result set."""
    mock_resp = MagicMock()
    mock_resp.content = '["the", "is", "convergence", "a", "drift"]'

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()
        result = _NLPExtractor.extract("text")
        assert "the" not in result
        assert "is" not in result
        assert "convergence" in result


def test_extract_caches_result():
    """Second call with same text skips LLM entirely."""
    mock_resp = MagicMock()
    mock_resp.content = '["cached_keyword"]'

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()

        text = "unique text for cache test xyzzy"
        _NLPExtractor.extract(text)
        _NLPExtractor.extract(text)  # second call should hit cache

        assert instance.invoke.call_count == 1


def test_extract_falls_back_on_malformed_llm_json():
    """If LLM returns non-JSON, extract() falls back to _tokenize()."""
    mock_resp = MagicMock()
    mock_resp.content = "not valid json at all"

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()
        text = "convergence drift detection"
        result = _NLPExtractor.extract(text)
        assert result == _tokenize(text)


def test_extract_falls_back_on_llm_exception():
    """If LLM call raises, extract() falls back to _tokenize()."""
    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.side_effect = RuntimeError("LLM unavailable")
        _NLPExtractor._cache.clear()
        text = "reflex plateau detection"
        result = _NLPExtractor.extract(text)
        assert result == _tokenize(text)


def test_extract_truncates_long_text_to_llm():
    """Text longer than 2000 chars is truncated before sending to the LLM."""
    mock_resp = MagicMock()
    mock_resp.content = '["keyword"]'

    with patch("icdev.tools.llm.router.LLMRouter") as MockRouter:
        instance = MockRouter.return_value
        instance.is_no_llm_mode.return_value = False
        instance.has_any_llm.return_value = True
        instance.invoke.return_value = mock_resp
        _NLPExtractor._cache.clear()

        long_text = "word " * 1000  # 5000 chars
        _NLPExtractor.extract(long_text)

        call_args = instance.invoke.call_args
        sent_content = call_args[0][1].messages[0]["content"]
        assert len(sent_content) <= 2000
