# CUI // SP-CTI
"""Tests for tools.ai_augmentation.threshold_advisor.

All LLM calls are mocked — tests are deterministic and never hit a real API.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.ai_augmentation.threshold_advisor import (
    _heuristic_recommendation,
    generate_threshold_recommendation,
)


# ---------------------------------------------------------------------------
# _heuristic_recommendation
# ---------------------------------------------------------------------------

def test_heuristic_returns_required_keys():
    result = _heuristic_recommendation(100, "/app/tools/auth.py")
    assert "config_key" in result
    assert "default_value" in result
    assert "rationale" in result
    assert "migration_snippet" in result
    assert "feasibility" in result
    assert result["source"] == "heuristic"


def test_heuristic_preserves_threshold_value():
    result = _heuristic_recommendation(42, "module.py")
    assert result["default_value"] == 42


def test_heuristic_config_key_screaming_snake():
    result = _heuristic_recommendation(5, "/tools/rate_limiter.py")
    assert result["config_key"] == result["config_key"].upper()
    assert " " not in result["config_key"]


def test_heuristic_unknown_path():
    result = _heuristic_recommendation(0.5, "<unknown>")
    assert result["config_key"]  # non-empty fallback


def test_heuristic_migration_snippet_mentions_value():
    result = _heuristic_recommendation(99, "checker.py")
    assert "99" in result["migration_snippet"]


# ---------------------------------------------------------------------------
# generate_threshold_recommendation — heuristic path (LLM unavailable)
# ---------------------------------------------------------------------------

_SAMPLE_PATTERN = {
    "pattern_type": "hardcoded_threshold",
    "module_path": "src/requests/__init__.py",
    "function_name": "<unknown>",
    "pattern_detail": {"kind": "compare", "constants": [500]},
    "language": "python",
}


def test_generate_falls_back_on_llm_unavailable():
    import tools.ai_augmentation.threshold_advisor as mod
    mock_router = MagicMock()
    mock_router.return_value.invoke.side_effect = mod.LLMUnavailableError("no llm")
    with patch.object(mod, "LLMRouter", mock_router):
        result = generate_threshold_recommendation(_SAMPLE_PATTERN)
    assert result["source"] == "heuristic"
    assert result["default_value"] == 500


def test_generate_falls_back_on_bad_json():
    import tools.ai_augmentation.threshold_advisor as mod
    resp = MagicMock()
    resp.content = "not valid json }"
    mock_router = MagicMock()
    mock_router.return_value.invoke.return_value = resp
    with patch.object(mod, "LLMRouter", mock_router):
        result = generate_threshold_recommendation(_SAMPLE_PATTERN)
    assert result["source"] == "heuristic"


def test_generate_falls_back_when_llm_flag_false():
    import tools.ai_augmentation.threshold_advisor as mod
    with patch.object(mod, "_LLM_AVAILABLE", False):
        result = generate_threshold_recommendation(_SAMPLE_PATTERN)
    assert result["source"] == "heuristic"


# ---------------------------------------------------------------------------
# generate_threshold_recommendation — LLM success path
# ---------------------------------------------------------------------------

_LLM_RESPONSE_JSON = (
    '{"config_key": "REQUESTS_MAX_RETRIES", "default_value": 500, '
    '"rationale": "Externalise for per-deployment tuning.", '
    '"migration_snippet": "import os\\n_T = int(os.getenv(\'REQUESTS_MAX_RETRIES\', 500))", '
    '"feasibility": "medium"}'
)


def test_generate_uses_llm_response_when_available():
    import tools.ai_augmentation.threshold_advisor as mod
    resp = MagicMock()
    resp.content = _LLM_RESPONSE_JSON
    mock_router = MagicMock()
    mock_router.return_value.invoke.return_value = resp
    with patch.object(mod, "LLMRouter", mock_router):
        result = generate_threshold_recommendation(_SAMPLE_PATTERN)
    assert result["source"] == "llm"
    assert result["config_key"] == "REQUESTS_MAX_RETRIES"
    assert result["default_value"] == 500
    assert result["feasibility"] == "medium"


def test_generate_passes_context_to_llm():
    import tools.ai_augmentation.threshold_advisor as mod
    resp = MagicMock()
    resp.content = _LLM_RESPONSE_JSON
    mock_router = MagicMock()
    mock_router.return_value.invoke.return_value = resp
    with patch.object(mod, "LLMRouter", mock_router):
        generate_threshold_recommendation(_SAMPLE_PATTERN, context={"il_level": "il5"})
    call_args = mock_router.return_value.invoke.call_args
    request = call_args[0][1]
    assert "il5" in request.messages[0]["content"]


# ---------------------------------------------------------------------------
# generate_threshold_recommendation — edge cases
# ---------------------------------------------------------------------------

def test_generate_handles_empty_constants():
    import tools.ai_augmentation.threshold_advisor as mod
    pattern = {**_SAMPLE_PATTERN, "pattern_detail": {"kind": "compare", "constants": []}}
    with patch.object(mod, "_LLM_AVAILABLE", False):
        result = generate_threshold_recommendation(pattern)
    assert result["default_value"] == 0


def test_generate_handles_string_pattern_detail():
    import json as _json
    import tools.ai_augmentation.threshold_advisor as mod
    pattern = {**_SAMPLE_PATTERN, "pattern_detail": _json.dumps({"kind": "binop", "constants": [7.5]})}
    with patch.object(mod, "_LLM_AVAILABLE", False):
        result = generate_threshold_recommendation(pattern)
    assert result["default_value"] == 7.5


def test_generate_no_context_defaults():
    import tools.ai_augmentation.threshold_advisor as mod
    with patch.object(mod, "_LLM_AVAILABLE", False):
        result = generate_threshold_recommendation(_SAMPLE_PATTERN, context=None)
    assert result is not None
