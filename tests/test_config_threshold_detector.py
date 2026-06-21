# CUI // SP-CTI
"""Tests for tools.ai_augmentation.config_threshold_detector.

Validates the configuration threshold anomaly-detection tool (AAC opp aac-opp-500):
  - _parse_response: JSON extraction from LLM output
  - assess_task_runner_completeness: full flow with mocked LLM and LLM-unavailable fallback
  - configuration pillar integration: _check_makefile_or_taskfile uses the detector
  - CLI: argument parsing and output format
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_augmentation.config_threshold_detector import (
    _DEFAULT_MIN_COUNT,
    _parse_response,
    assess_task_runner_completeness,
)


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def test_parse_response_clean_json_sufficient():
    raw = json.dumps({
        "sufficient": True,
        "confidence": 0.9,
        "reason": "Makefile has 5 targets covering build, test, lint, deploy, and clean.",
        "recommendation": "keep — task coverage is adequate.",
    })
    result = _parse_response(raw)
    assert result["sufficient"] is True
    assert result["confidence"] == pytest.approx(0.9)
    assert "Makefile" in result["reason"]
    assert result["raw"] == raw


def test_parse_response_clean_json_insufficient():
    raw = json.dumps({
        "sufficient": False,
        "confidence": 0.85,
        "reason": "Only 1 target detected; missing test, lint, and deploy.",
        "recommendation": "add at least 2 more tasks.",
    })
    result = _parse_response(raw)
    assert result["sufficient"] is False
    assert result["confidence"] == pytest.approx(0.85)


def test_parse_response_markdown_fences():
    raw = "```json\n{\"sufficient\": false, \"confidence\": 0.7, \"reason\": \"too few\", \"recommendation\": \"add tasks\"}\n```"
    result = _parse_response(raw)
    assert result["sufficient"] is False
    assert result["confidence"] == pytest.approx(0.7)


def test_parse_response_embedded_json():
    raw = "Sure! {\"sufficient\": true, \"confidence\": 0.8, \"reason\": \"ok\", \"recommendation\": \"keep\"}"
    result = _parse_response(raw)
    assert result["sufficient"] is True


def test_parse_response_invalid_json_defaults():
    result = _parse_response("this is not json at all")
    assert isinstance(result["sufficient"], bool)
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["reason"], str)
    assert isinstance(result["recommendation"], str)
    assert isinstance(result["raw"], str)


@pytest.mark.parametrize("sufficient,conf", [
    (True, 1.0),
    (False, 0.0),
    (True, 0.5),
])
def test_parse_response_parametrized(sufficient, conf):
    raw = json.dumps({
        "sufficient": sufficient,
        "confidence": conf,
        "reason": "test",
        "recommendation": "test",
    })
    result = _parse_response(raw)
    assert result["sufficient"] is sufficient
    assert result["confidence"] == pytest.approx(conf)


# ---------------------------------------------------------------------------
# assess_task_runner_completeness — input validation
# ---------------------------------------------------------------------------


def test_raises_on_empty_tool_type():
    with pytest.raises(ValueError, match="tool_type"):
        assess_task_runner_completeness("", 5)


def test_raises_on_negative_count():
    with pytest.raises(ValueError, match="non-negative"):
        assess_task_runner_completeness("makefile", -1)


# ---------------------------------------------------------------------------
# assess_task_runner_completeness — mocked LLM (happy path)
# ---------------------------------------------------------------------------

_MOCK_SUFFICIENT_JSON = json.dumps({
    "sufficient": True,
    "confidence": 0.9,
    "reason": "Makefile has adequate task coverage.",
    "recommendation": "keep — no action needed.",
})

_MOCK_INSUFFICIENT_JSON = json.dumps({
    "sufficient": False,
    "confidence": 0.88,
    "reason": "Only 1 Makefile target detected; key tasks are missing.",
    "recommendation": "add at least 2 more targets.",
})


def test_assess_returns_expected_keys_sufficient():
    import tools.ai_augmentation.config_threshold_detector as ctd
    with patch.object(ctd, "_llm_assess", return_value=_parse_response(_MOCK_SUFFICIENT_JSON)):
        result = assess_task_runner_completeness("makefile", 5, context="Python web service")

    assert "sufficient" in result
    assert "confidence" in result
    assert "reason" in result
    assert "recommendation" in result
    assert "raw" in result
    assert result["sufficient"] is True


def test_assess_returns_insufficient_when_llm_says_so():
    import tools.ai_augmentation.config_threshold_detector as ctd
    with patch.object(ctd, "_llm_assess", return_value=_parse_response(_MOCK_INSUFFICIENT_JSON)):
        result = assess_task_runner_completeness("makefile", 1)

    assert result["sufficient"] is False
    assert result["confidence"] >= 0.8


def test_assess_npm_scripts_sufficient():
    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = _parse_response(json.dumps({
        "sufficient": True, "confidence": 0.95,
        "reason": "npm scripts cover all standard tasks.",
        "recommendation": "keep",
    }))
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        result = assess_task_runner_completeness("npm_scripts", 4, context="React frontend")

    assert result["sufficient"] is True


# ---------------------------------------------------------------------------
# assess_task_runner_completeness — LLM-unavailable fallback
# ---------------------------------------------------------------------------


def test_fallback_sufficient_when_count_meets_default():
    import tools.ai_augmentation.config_threshold_detector as ctd
    with patch.object(ctd, "_llm_assess", side_effect=RuntimeError("LLM unavailable")):
        result = assess_task_runner_completeness("makefile", _DEFAULT_MIN_COUNT)

    assert result["sufficient"] is True
    assert result["confidence"] == pytest.approx(0.5)
    assert result["raw"] == ""


def test_fallback_insufficient_when_count_below_default():
    import tools.ai_augmentation.config_threshold_detector as ctd
    with patch.object(ctd, "_llm_assess", side_effect=ImportError("no LLM module")):
        result = assess_task_runner_completeness("npm_scripts", _DEFAULT_MIN_COUNT - 1)

    assert result["sufficient"] is False
    assert result["raw"] == ""
    assert "add" in result["recommendation"].lower()


def test_fallback_zero_count_is_insufficient():
    import tools.ai_augmentation.config_threshold_detector as ctd
    with patch.object(ctd, "_llm_assess", side_effect=Exception("network error")):
        result = assess_task_runner_completeness("makefile", 0)

    assert result["sufficient"] is False


# ---------------------------------------------------------------------------
# configuration pillar integration
# ---------------------------------------------------------------------------


def test_pillar_uses_detector_for_makefile(tmp_path):
    """_check_makefile_or_taskfile should pass when detector says 'sufficient'."""
    makefile_content = "build:\n\tpython setup.py build\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n"
    (tmp_path / "Makefile").write_text(makefile_content, encoding="utf-8")

    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = {"sufficient": True, "confidence": 0.9, "reason": "ok", "recommendation": "keep", "raw": ""}
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import PILLAR
        results = PILLAR.run(tmp_path)

    task_result = next(r for r in results if r.criterion_id == "task-runner")
    assert task_result.passed is True
    assert "Makefile" in task_result.message


def test_pillar_uses_detector_insufficient_makefile(tmp_path):
    """When detector says 'insufficient', the Makefile check should not pass on its own."""
    makefile_content = "build:\n\tpython setup.py build\n"
    (tmp_path / "Makefile").write_text(makefile_content, encoding="utf-8")

    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = {"sufficient": False, "confidence": 0.8, "reason": "too few", "recommendation": "add more", "raw": ""}
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import PILLAR
        results = PILLAR.run(tmp_path)

    task_result = next(r for r in results if r.criterion_id == "task-runner")
    assert task_result.passed is False


def test_pillar_uses_detector_for_npm_scripts(tmp_path):
    """npm scripts path should use the detector instead of the hardcoded threshold."""
    pkg = {
        "name": "my-app",
        "scripts": {
            "start": "node index.js",
            "test": "jest",
            "build": "webpack",
            "lint": "eslint .",
        },
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = {"sufficient": True, "confidence": 0.9, "reason": "ok", "recommendation": "keep", "raw": ""}
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        from tools.ai_augmentation.agent_readiness.pillars.configuration import PILLAR
        results = PILLAR.run(tmp_path)

    task_result = next(r for r in results if r.criterion_id == "task-runner")
    assert task_result.passed is True
    assert "npm scripts" in task_result.message


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_json_output(capsys):
    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = _parse_response(_MOCK_SUFFICIENT_JSON)
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        from tools.ai_augmentation.config_threshold_detector import main
        with patch("sys.argv", ["config_threshold_detector", "--tool-type", "makefile",
                                "--count", "5", "--json"]):
            main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "sufficient" in data
    assert "confidence" in data


def test_cli_text_output(capsys):
    import tools.ai_augmentation.config_threshold_detector as ctd
    mock_result = _parse_response(_MOCK_INSUFFICIENT_JSON)
    with patch.object(ctd, "_llm_assess", return_value=mock_result):
        from tools.ai_augmentation.config_threshold_detector import main
        with patch("sys.argv", ["config_threshold_detector", "--tool-type", "makefile",
                                "--count", "1"]):
            main()

    captured = capsys.readouterr()
    assert "INSUFFICIENT" in captured.out
    assert "makefile" in captured.out
# CUI // SP-CTI
