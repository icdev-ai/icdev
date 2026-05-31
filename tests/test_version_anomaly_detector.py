# CUI // SP-CTI
"""Tests for tools.aiify.version_anomaly_detector.

Validates the version compatibility anomaly-detection tool (AI-ify opp 467):
  - _parse_response: JSON extraction from LLM output
  - check_version_compat: full flow with mocked LLM
  - CLI: argument parsing and output format
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.aiify.version_anomaly_detector import (
    _parse_response,
    check_version_compat,
)


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def test_parse_response_clean_json():
    raw = json.dumps({
        "compatible": True,
        "confidence": 0.9,
        "reason": "chardet 4.0.0 is widely used and tested.",
        "recommendation": "keep — version is stable and supported.",
    })
    result = _parse_response(raw)
    assert result["compatible"] is True
    assert result["confidence"] == pytest.approx(0.9)
    assert "chardet" in result["reason"]
    assert result["raw"] == raw


def test_parse_response_markdown_fences():
    raw = "```json\n{\"compatible\": false, \"confidence\": 0.7, \"reason\": \"old\", \"recommendation\": \"upgrade\"}\n```"
    result = _parse_response(raw)
    assert result["compatible"] is False
    assert result["confidence"] == pytest.approx(0.7)


def test_parse_response_embedded_json():
    raw = "Sure! Here is the answer: {\"compatible\": true, \"confidence\": 0.8, \"reason\": \"ok\", \"recommendation\": \"keep\"}"
    result = _parse_response(raw)
    assert result["compatible"] is True


def test_parse_response_invalid_json_defaults():
    result = _parse_response("this is not json at all")
    assert isinstance(result["compatible"], bool)
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["reason"], str)
    assert isinstance(result["recommendation"], str)


# ---------------------------------------------------------------------------
# check_version_compat (mocked LLM)
# ---------------------------------------------------------------------------

_MOCK_LLM_JSON = json.dumps({
    "compatible": True,
    "confidence": 0.85,
    "reason": "chardet 4.0.0 is compatible with requests.",
    "recommendation": "keep — no action needed.",
})


def _make_mock_router(content: str = _MOCK_LLM_JSON):
    mock_response = MagicMock()
    mock_response.content = content
    mock_router = MagicMock()
    mock_router.invoke.return_value = mock_response
    return mock_router


def test_check_version_compat_returns_expected_keys():
    from tools.aiify import version_anomaly_detector as vad
    with patch.object(vad, "_llm_assess", return_value=_parse_response(_MOCK_LLM_JSON)):
        result = vad.check_version_compat("chardet", "4.0.0", context="requests HTTP library")

    assert "compatible" in result
    assert "confidence" in result
    assert "reason" in result
    assert "recommendation" in result
    assert "raw" in result


def test_check_version_compat_incompatible_version():
    bad_json = json.dumps({
        "compatible": False,
        "confidence": 0.95,
        "reason": "chardet 2.0.0 is too old.",
        "recommendation": "upgrade to >= 3.0.2.",
    })
    from tools.aiify import version_anomaly_detector as vad
    with patch.object(vad, "_llm_assess", return_value=_parse_response(bad_json)):
        result = vad.check_version_compat("chardet", "2.0.0")

    assert result["compatible"] is False
    assert result["confidence"] >= 0.9


def test_check_version_compat_raises_on_empty_package():
    with pytest.raises(ValueError, match="package and version"):
        check_version_compat("", "1.0.0")


def test_check_version_compat_raises_on_empty_version():
    with pytest.raises(ValueError, match="package and version"):
        check_version_compat("chardet", "")


# ---------------------------------------------------------------------------
# Integration: _parse_response is pure and round-trips correctly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("compat,conf", [
    (True, 1.0),
    (False, 0.0),
    (True, 0.5),
])
def test_parse_response_parametrized(compat, conf):
    raw = json.dumps({
        "compatible": compat,
        "confidence": conf,
        "reason": "test",
        "recommendation": "test",
    })
    result = _parse_response(raw)
    assert result["compatible"] is compat
    assert result["confidence"] == pytest.approx(conf)
# CUI // SP-CTI
