# CUI // SP-CTI
"""Tests for the ML classifier (claude-haiku-4-5-20251001) augmentation of
nested_conditionals detection in tools.ai_augmentation.pattern_classifier.

RED phase: all tests written before implementation.

Tests cover:
  1. Config loading with defaults and overrides
  2. _ml_classify_nested_conditionals() — LLM unavailable path (None return)
  3. _ml_classify_nested_conditionals() — mocked LLM response (valid JSON)
  4. _ml_classify_nested_conditionals() — mocked LLM response (malformed JSON)
  5. Integration with detect_patterns() — AST results preserved
  6. Integration with detect_patterns() — ML results merged when LLM available
  7. detect_patterns() public API unchanged

NIST 800-53 controls: SA-11 (Developer Testing), CM-3 (Configuration Change Control)
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ai_augmentation.pattern_classifier import (
    _load_ml_classifier_config,
    _ml_classify_nested_conditionals,
    detect_patterns,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEEP_NESTING_PY = textwrap.dedent("""\
    def check(x, y, z, w):
        if x > 0:
            if y > 0:
                if z > 0:
                    if w > 0:
                        return True
        return False
""")

_SHALLOW_PY = textwrap.dedent("""\
    def check(x):
        if x > 0:
            return True
        return False
""")


@pytest.fixture
def deep_nesting_file(tmp_path):
    """Temp .py file with deeply nested conditionals (depth ≥ 3)."""
    f = tmp_path / "deep_nesting.py"
    f.write_text(_DEEP_NESTING_PY, encoding="utf-8")
    return str(f)


@pytest.fixture
def shallow_file(tmp_path):
    """Temp .py file with only shallow conditionals (depth < 3)."""
    f = tmp_path / "shallow.py"
    f.write_text(_SHALLOW_PY, encoding="utf-8")
    return str(f)


# ---------------------------------------------------------------------------
# 1. Config loading
# ---------------------------------------------------------------------------

class TestLoadMlClassifierConfig:
    """_load_ml_classifier_config() returns a dict with expected keys and defaults."""

    def test_returns_dict(self):
        cfg = _load_ml_classifier_config()
        assert isinstance(cfg, dict)

    def test_has_required_keys(self):
        cfg = _load_ml_classifier_config()
        assert "ml_classifier_enabled" in cfg
        assert "ml_classifier_model" in cfg
        assert "ml_classifier_max_tokens" in cfg
        assert "ml_classifier_confidence_threshold" in cfg
        assert "ml_classifier_source_sample_chars" in cfg

    def test_default_model(self):
        cfg = _load_ml_classifier_config()
        assert cfg["ml_classifier_model"] == "claude-haiku-4-5-20251001"

    def test_default_confidence_threshold_is_float(self):
        cfg = _load_ml_classifier_config()
        assert isinstance(cfg["ml_classifier_confidence_threshold"], float)
        assert 0.0 <= cfg["ml_classifier_confidence_threshold"] <= 1.0

    def test_default_enabled_is_bool(self):
        cfg = _load_ml_classifier_config()
        assert isinstance(cfg["ml_classifier_enabled"], bool)

    def test_returns_same_object_on_repeated_calls(self):
        """Repeated calls return equal dicts (caching/idempotency)."""
        cfg1 = _load_ml_classifier_config()
        cfg2 = _load_ml_classifier_config()
        assert cfg1 == cfg2


# ---------------------------------------------------------------------------
# 2. _ml_classify_nested_conditionals() — LLM unavailable (None returns)
# ---------------------------------------------------------------------------

class TestMlClassifyLlmUnavailable:
    """When LLM cannot be called, function returns None (triggers AST fallback)."""

    def test_returns_none_when_api_key_missing(self, deep_nesting_file):
        with patch.dict(os.environ, {}, clear=True):
            # Remove ANTHROPIC_API_KEY if present
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None

    def test_returns_none_when_anthropic_not_installed(self, deep_nesting_file):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": None}):
                result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None

    def test_returns_none_when_ml_classifier_disabled(self, deep_nesting_file):
        disabled_cfg = {
            "ml_classifier_enabled": False,
            "ml_classifier_model": "claude-haiku-4-5-20251001",
            "ml_classifier_max_tokens": 512,
            "ml_classifier_confidence_threshold": 0.7,
            "ml_classifier_source_sample_chars": 3000,
        }
        with patch(
            "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
            return_value=disabled_cfg,
        ):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None


# ---------------------------------------------------------------------------
# 3. _ml_classify_nested_conditionals() — mocked LLM, valid JSON response
# ---------------------------------------------------------------------------

class TestMlClassifyValidResponse:
    """When LLM returns valid JSON, function returns a list of pattern dicts."""

    def _mock_anthropic(self, found: bool, confidence: float, locations: list) -> Any:
        """Build a mock anthropic.Anthropic client that returns the given payload."""
        mock_response = MagicMock()
        payload = {
            "found": found,
            "confidence": confidence,
            "locations": locations,
        }
        mock_response.content = [MagicMock(text=json.dumps(payload))]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_module = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        return mock_anthropic_module

    def test_returns_list_when_found(self, deep_nesting_file):
        mock_locations = [
            {"function_name": "check", "line_start": 1, "line_end": 6, "max_depth": 4},
        ]
        mock_mod = self._mock_anthropic(True, 0.92, mock_locations)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_result_has_canonical_schema_keys(self, deep_nesting_file):
        mock_locations = [
            {"function_name": "check", "line_start": 1, "line_end": 6, "max_depth": 4},
        ]
        mock_mod = self._mock_anthropic(True, 0.92, mock_locations)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is not None
        for hit in result:
            assert "pattern_type" in hit
            assert "module_path" in hit
            assert "function_name" in hit
            assert "line_start" in hit
            assert "line_end" in hit
            assert "pattern_detail" in hit

    def test_result_pattern_type_is_nested_conditionals(self, deep_nesting_file):
        mock_locations = [
            {"function_name": "check", "line_start": 1, "line_end": 6, "max_depth": 4},
        ]
        mock_mod = self._mock_anthropic(True, 0.92, mock_locations)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is not None
        for hit in result:
            assert hit["pattern_type"] == "nested_conditionals"

    def test_result_module_path_matches_input(self, deep_nesting_file):
        mock_locations = [
            {"function_name": "check", "line_start": 1, "line_end": 6, "max_depth": 4},
        ]
        mock_mod = self._mock_anthropic(True, 0.92, mock_locations)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is not None
        for hit in result:
            assert hit["module_path"] == deep_nesting_file

    def test_returns_empty_list_when_not_found(self, shallow_file):
        mock_mod = self._mock_anthropic(False, 0.95, [])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_SHALLOW_PY, shallow_file)
        assert result == []

    def test_low_confidence_result_filtered_out(self, deep_nesting_file):
        """Results below confidence threshold should be excluded."""
        mock_locations = [
            {"function_name": "check", "line_start": 1, "line_end": 6, "max_depth": 4},
        ]
        mock_mod = self._mock_anthropic(True, 0.4, mock_locations)  # below 0.7 threshold
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value={
                        "ml_classifier_enabled": True,
                        "ml_classifier_model": "claude-haiku-4-5-20251001",
                        "ml_classifier_max_tokens": 512,
                        "ml_classifier_confidence_threshold": 0.7,
                        "ml_classifier_source_sample_chars": 3000,
                    },
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        # When LLM returns low confidence, the function returns None or empty list
        # (signals caller to use AST fallback)
        assert result is None or result == []


# ---------------------------------------------------------------------------
# 4. _ml_classify_nested_conditionals() — malformed LLM JSON
# ---------------------------------------------------------------------------

class TestMlClassifyMalformedResponse:
    """When LLM returns non-JSON or corrupt JSON, function returns None gracefully."""

    def _mock_with_text(self, text: str) -> Any:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_mod = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        return mock_mod

    def _good_cfg(self):
        return {
            "ml_classifier_enabled": True,
            "ml_classifier_model": "claude-haiku-4-5-20251001",
            "ml_classifier_max_tokens": 512,
            "ml_classifier_confidence_threshold": 0.7,
            "ml_classifier_source_sample_chars": 3000,
        }

    def test_non_json_returns_none(self, deep_nesting_file):
        mock_mod = self._mock_with_text("I cannot determine that from the code.")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value=self._good_cfg(),
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None

    def test_empty_response_returns_none(self, deep_nesting_file):
        mock_mod = self._mock_with_text("")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value=self._good_cfg(),
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None

    def test_api_exception_returns_none(self, deep_nesting_file):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_mod = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": mock_mod}):
                with patch(
                    "tools.ai_augmentation.pattern_classifier._load_ml_classifier_config",
                    return_value=self._good_cfg(),
                ):
                    result = _ml_classify_nested_conditionals(_DEEP_NESTING_PY, deep_nesting_file)
        assert result is None


# ---------------------------------------------------------------------------
# 5. Integration: detect_patterns() — AST results preserved
# ---------------------------------------------------------------------------

class TestDetectPatternsAstPreserved:
    """detect_patterns() still returns AST-based nested_conditionals when ML unavailable."""

    def test_ast_nested_conditionals_found_without_llm(self, deep_nesting_file):
        """AST fallback detects deeply nested conditionals without LLM."""
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
                results = detect_patterns(deep_nesting_file)
        nested = [r for r in results if r["pattern_type"] == "nested_conditionals"]
        assert len(nested) >= 1

    def test_ast_no_nested_conditionals_in_shallow(self, shallow_file):
        """Shallow file produces no nested_conditionals hits via AST."""
        with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
            results = detect_patterns(shallow_file)
        nested = [r for r in results if r["pattern_type"] == "nested_conditionals"]
        assert len(nested) == 0

    def test_detect_patterns_returns_list(self, deep_nesting_file):
        with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
            results = detect_patterns(deep_nesting_file)
        assert isinstance(results, list)

    def test_canonical_schema_keys_in_results(self, deep_nesting_file):
        with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
            results = detect_patterns(deep_nesting_file)
        for r in results:
            assert "pattern_type" in r
            assert "module_path" in r
            assert "function_name" in r
            assert "line_start" in r
            assert "line_end" in r
            assert "pattern_detail" in r


# ---------------------------------------------------------------------------
# 6. Integration: detect_patterns() — ML results merged when LLM available
# ---------------------------------------------------------------------------

class TestDetectPatternsMlMerge:
    """detect_patterns() merges ML classifier results with AST results, no duplicates."""

    def test_no_duplicate_hits_when_ml_overlaps_ast(self, deep_nesting_file):
        """ML results at the same (file, line, pattern_type) as AST are deduplicated."""
        ml_hit = {
            "pattern_type": "nested_conditionals",
            "module_path": deep_nesting_file,
            "function_name": "check",
            "line_start": 1,
            "line_end": 6,
            "language": "python",
            "pattern_detail": {"max_depth": 4, "source": "ml_classifier", "confidence": 0.92},
        }
        with patch(
            "tools.ai_augmentation.pattern_classifier._ml_classify_nested_conditionals",
            return_value=[ml_hit],
        ):
            with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
                results = detect_patterns(deep_nesting_file)

        nested = [r for r in results if r["pattern_type"] == "nested_conditionals"]
        # Should have at least 1 but not double-count
        assert len(nested) >= 1
        # Verify deduplication by (file, line, type)
        seen = set()
        for hit in nested:
            key = (hit["module_path"], hit["line_start"], hit["pattern_type"])
            assert key not in seen, f"Duplicate found: {key}"
            seen.add(key)

    def test_ml_only_hit_at_new_line_is_added(self, deep_nesting_file):
        """ML result at a line not covered by AST is added to output."""
        ml_hit = {
            "pattern_type": "nested_conditionals",
            "module_path": deep_nesting_file,
            "function_name": "another_func",
            "line_start": 999,  # line not in the file, won't overlap with AST
            "line_end": 1010,
            "language": "python",
            "pattern_detail": {"max_depth": 5, "source": "ml_classifier", "confidence": 0.88},
        }
        with patch(
            "tools.ai_augmentation.pattern_classifier._ml_classify_nested_conditionals",
            return_value=[ml_hit],
        ):
            with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
                results = detect_patterns(deep_nesting_file)

        nested = [r for r in results if r["pattern_type"] == "nested_conditionals"]
        lines = {r["line_start"] for r in nested}
        assert 999 in lines


# ---------------------------------------------------------------------------
# 7. Public API unchanged
# ---------------------------------------------------------------------------

class TestPublicApiUnchanged:
    """detect_patterns() signature and return type contract are preserved."""

    def test_detect_patterns_accepts_file_path_string(self, deep_nesting_file):
        result = detect_patterns(deep_nesting_file)
        assert isinstance(result, list)

    def test_detect_patterns_accepts_directory_path(self, tmp_path):
        f = tmp_path / "module.py"
        f.write_text(_DEEP_NESTING_PY, encoding="utf-8")
        result = detect_patterns(str(tmp_path))
        assert isinstance(result, list)

    def test_detect_patterns_returns_empty_for_nonexistent_path(self):
        result = detect_patterns("/nonexistent/path/to/nowhere.py")
        assert result == []

    def test_detect_patterns_result_items_are_dicts(self, deep_nesting_file):
        with patch("tools.ai_augmentation.pattern_classifier._detect_via_semgrep", return_value=None):
            result = detect_patterns(deep_nesting_file)
        for item in result:
            assert isinstance(item, dict)
