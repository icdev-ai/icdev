# CUI // SP-CTI
"""Tests for tools/genesis/goal_template_generator.py — metadata extraction via LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.goal_template_generator import (
    _compute_confidence_anomaly_detection,
    _tool_name_to_title,
    extract_metadata_with_llm,
    generate_goal_from_pattern,
)


def _mock_llm_response(content: str):
    r = MagicMock()
    r.content = content
    return r


def _sample_pattern():
    return {
        "pattern": ["Read", "LLMRouter", "Write"],
        "frequency": 5,
        "caller_diversity": 3,
        "composite_score": 0.85,
    }


# ---------------------------------------------------------------------------
# _tool_name_to_title
# ---------------------------------------------------------------------------


class TestToolNameToTitle:
    def test_generic_only_chain_falls_back_to_first_three(self):
        title = _tool_name_to_title(["Read", "Write", "Grep"])
        assert "Workflow" in title

    def test_distinctive_tools_included(self):
        title = _tool_name_to_title(["Read", "LLMRouter", "DatabaseQuery"])
        assert "LLMRouter" in title or "DatabaseQuery" in title

    def test_long_chain_shows_pipeline_label(self):
        chain = ["ToolA", "ToolB", "ToolC", "ToolD", "ToolE"]
        title = _tool_name_to_title(chain)
        assert "Pipeline" in title

    def test_short_chain_arrow_format(self):
        title = _tool_name_to_title(["Alpha", "Beta"])
        assert "→" in title


# ---------------------------------------------------------------------------
# generate_goal_from_pattern — deterministic behaviour
# ---------------------------------------------------------------------------


class TestGenerateGoalFromPatternDeterministic:
    def test_returns_required_keys(self):
        result = generate_goal_from_pattern(_sample_pattern())
        for key in ("goal_markdown", "title", "pattern_id", "confidence"):
            assert key in result

    def test_pattern_id_starts_with_tpat(self):
        result = generate_goal_from_pattern(_sample_pattern())
        assert result["pattern_id"].startswith("tpat-")

    def test_pattern_id_deterministic(self):
        p = _sample_pattern()
        r1 = generate_goal_from_pattern(p)
        r2 = generate_goal_from_pattern(p)
        assert r1["pattern_id"] == r2["pattern_id"]

    def test_title_in_markdown(self):
        result = generate_goal_from_pattern(_sample_pattern())
        assert result["title"] in result["goal_markdown"]

    def test_pattern_id_in_markdown(self):
        result = generate_goal_from_pattern(_sample_pattern())
        assert result["pattern_id"] in result["goal_markdown"]

    def test_llm_metadata_not_called_by_default(self):
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm"
        ) as mock_fn:
            generate_goal_from_pattern(_sample_pattern())
            mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# generate_goal_from_pattern — LLM metadata integration
# ---------------------------------------------------------------------------


class TestGenerateGoalFromPatternLlmMetadata:
    def test_extract_called_when_flag_set(self):
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value={},
        ) as mock_fn:
            generate_goal_from_pattern(_sample_pattern(), use_llm_metadata=True)
            mock_fn.assert_called_once()

    def test_llm_title_overrides_deterministic(self):
        fake = {"title": "AI-Generated Custom Title"}
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value=fake,
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert result["title"] == "AI-Generated Custom Title"

    def test_llm_inputs_outputs_in_markdown(self):
        fake = {
            "inputs": "- Custom input A\n- Custom input B",
            "outputs": "- Custom output X",
        }
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value=fake,
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert "Custom input A" in result["goal_markdown"]
        assert "Custom output X" in result["goal_markdown"]

    def test_llm_description_in_markdown(self):
        fake = {"description": "This workflow does something special."}
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value=fake,
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert "This workflow does something special." in result["goal_markdown"]

    def test_extracted_flag_true_on_success(self):
        fake = {"title": "LLM Title", "description": "LLM Desc"}
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value=fake,
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert result.get("llm_metadata_extracted") is True

    def test_extracted_flag_false_on_empty_metadata(self):
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value={},
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert result.get("llm_metadata_extracted") is False

    def test_empty_llm_metadata_falls_back_deterministic_title(self):
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value={},
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert result["title"]

    def test_category_in_markdown_when_provided(self):
        fake = {"category": "llm-generation"}
        with patch(
            "tools.genesis.goal_template_generator.extract_metadata_with_llm",
            return_value=fake,
        ):
            result = generate_goal_from_pattern(
                _sample_pattern(), use_llm_metadata=True
            )
        assert "llm-generation" in result["goal_markdown"]


# ---------------------------------------------------------------------------
# extract_metadata_with_llm
# ---------------------------------------------------------------------------


class TestExtractMetadataWithLlm:
    def test_returns_empty_when_no_hardprompt(self, tmp_path):
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        try:
            result = extract_metadata_with_llm(["Read", "Write"], 3, 2)
            assert result == {}
        finally:
            mod.BASE_DIR = original

    def test_returns_empty_on_llm_exception(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.side_effect = Exception("LLM unavailable")
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["Read", "Write"], 3, 2)
            assert result == {}
        finally:
            mod.BASE_DIR = original

    def test_returns_empty_on_invalid_json(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.return_value = _mock_llm_response("not json at all")
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["ToolA"], 1, 1)
            assert result == {}
        finally:
            mod.BASE_DIR = original

    def test_parses_valid_json_response(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        payload = {
            "title": "Read-Write Pipeline",
            "description": "Reads files and writes outputs.",
            "inputs": ["File path", "Write target"],
            "outputs": ["Written file"],
            "category": "file-ops",
            "prerequisites": ["File must exist"],
        }
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.return_value = _mock_llm_response(json.dumps(payload))
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["Read", "Write"], 3, 2)
            assert result["title"] == "Read-Write Pipeline"
            assert result["category"] == "file-ops"
            assert "File path" in result["inputs"]
            assert "Written file" in result["outputs"]
        finally:
            mod.BASE_DIR = original

    def test_parses_json_in_code_fence(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        payload = {
            "title": "Fenced Title",
            "description": "Desc.",
            "inputs": [],
            "outputs": [],
            "category": "other",
            "prerequisites": [],
        }
        fenced = f"```json\n{json.dumps(payload)}\n```"
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.return_value = _mock_llm_response(fenced)
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["ToolA"], 1, 1)
            assert result["title"] == "Fenced Title"
        finally:
            mod.BASE_DIR = original

    def test_inputs_formatted_as_bullet_list(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        payload = {
            "inputs": ["Item A", "Item B"],
            "outputs": [],
        }
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.return_value = _mock_llm_response(json.dumps(payload))
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["ToolA"], 1, 1)
            assert result["inputs"] == "- Item A\n- Item B"
        finally:
            mod.BASE_DIR = original

    def test_none_llm_response_returns_empty(self, tmp_path):
        _make_hardprompt(tmp_path)
        import tools.genesis.goal_template_generator as mod

        original = mod.BASE_DIR
        mod.BASE_DIR = tmp_path
        try:
            with patch("tools.llm.router.LLMRouter") as mock_cls:
                mock_router = MagicMock()
                mock_router.invoke.return_value = None
                mock_cls.return_value = mock_router
                result = extract_metadata_with_llm(["ToolA"], 1, 1)
            assert result == {}
        finally:
            mod.BASE_DIR = original


# ---------------------------------------------------------------------------
# _compute_confidence_anomaly_detection
# ---------------------------------------------------------------------------


class TestComputeConfidenceAnomalyDetection:
    def test_baseline_inputs_near_midpoint(self):
        # freq=3, div=2, score=0.5 → all ratios=1.0 → raw=1.0 → confidence=0.65
        result = _compute_confidence_anomaly_detection(3, 2, 0.5)
        assert 0.60 <= result <= 0.70, f"Expected ~0.65, got {result}"

    def test_zero_inputs_yield_floor(self):
        result = _compute_confidence_anomaly_detection(0, 0, 0.0)
        assert result == 0.40

    def test_very_high_inputs_yield_ceiling(self):
        result = _compute_confidence_anomaly_detection(100, 50, 1.0)
        assert result == 0.90

    def test_output_always_in_yellow_tier(self):
        for freq, div, score in [(1, 1, 0.1), (5, 3, 0.7), (0, 0, 0.0), (50, 20, 1.0)]:
            r = _compute_confidence_anomaly_detection(freq, div, score)
            assert 0.40 <= r <= 0.90

    def test_monotone_in_signal_quality(self):
        low = _compute_confidence_anomaly_detection(1, 1, 0.2)
        high = _compute_confidence_anomaly_detection(10, 5, 0.9)
        assert high > low

    def test_result_rounded_to_4dp(self):
        result = _compute_confidence_anomaly_detection(5, 3, 0.6)
        assert result == round(result, 4)

    def test_confidence_varies_with_pattern(self):
        p1 = _sample_pattern()
        p2 = {**_sample_pattern(), "frequency": 1, "caller_diversity": 1, "composite_score": 0.1}
        r1 = generate_goal_from_pattern(p1)
        r2 = generate_goal_from_pattern(p2)
        assert r1["confidence"] != r2["confidence"], "Confidence must not be hardcoded"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hardprompt(tmp_path: Path) -> None:
    hp_dir = tmp_path / "hardprompts" / "genesis"
    hp_dir.mkdir(parents=True)
    (hp_dir / "metadata_extraction.md").write_text(
        "Chain: {tool_chain} Freq: {frequency} Div: {diversity}",
        encoding="utf-8",
    )
