# CUI // SP-CTI
"""Tests for the ML classifier enrichment of hardcoded_threshold patterns.

All LLM calls are mocked — no network traffic is required.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.ai_augmentation.pattern_classifier as pc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ht_pattern(constants=None, func: str = "check", line: int = 10) -> dict:
    return {
        "pattern_type": "hardcoded_threshold",
        "module_path": "foo.py",
        "function_name": func,
        "line_start": line,
        "line_end": line,
        "language": "python",
        "pattern_detail": {
            "kind": "compare",
            "constants": constants or [500],
            "anomaly_detected": True,
        },
    }


def _make_llm_response(label: str, score: float, rationale: str) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"label": "{label}", "score": {score}, "rationale": "{rationale}"}}'
    )
    return resp


SOURCE_LINES = [""] * 20  # dummy lines so slice arithmetic works


# ── _classify_hardcoded_threshold_ml ─────────────────────────────────────────

class TestClassifyHardcodedThresholdMl:
    def test_returns_parsed_label_and_score(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "business_threshold", 0.9, "configurable rate limit"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("check", [500], "if x > 500:")

        assert result["label"] == "business_threshold"
        assert result["score"] == 0.9
        assert result["rationale"] == "configurable rate limit"

    def test_algorithm_constant_label_accepted(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "algorithm_constant", 0.8, "bit mask constant"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("mask", [256], "if x & 256:")

        assert result["label"] == "algorithm_constant"

    def test_boundary_check_label_accepted(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "boundary_check", 0.7, "sentinel zero check"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("guard", [0], "if n > 0:")

        assert result["label"] == "boundary_check"

    def test_invalid_label_cleared_to_empty(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "nonsense_label", 0.5, "unknown"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [10], "if x > 10:")

        assert result["label"] == ""

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content=(
                '```json\n{"label": "business_threshold", "score": 0.75,'
                ' "rationale": "ok"}\n```'
            )
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [100], "if x > 100:")

        assert result["label"] == "business_threshold"
        assert result["score"] == 0.75

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        result = pc._classify_hardcoded_threshold_ml("f", [42], "if x > 42:")
        assert result == {}

    def test_empty_label_when_no_json_in_response(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(content="not json at all")

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [7], "if n > 7:")

        # No JSON object found → data={} → label cleared to "" (not a valid label)
        assert result.get("label") == ""

    def test_returns_empty_dict_on_router_exception(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("network error")

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [99], "if x > 99:")

        assert result == {}


# ── _enrich_hardcoded_thresholds_with_ml ─────────────────────────────────────

class TestEnrichHardcodedThresholdsWithMl:
    def test_enriches_hardcoded_threshold_patterns(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        mock_ml = MagicMock(return_value={
            "label": "business_threshold",
            "score": 0.88,
            "rationale": "page size limit",
        })
        monkeypatch.setattr(pc, "_classify_hardcoded_threshold_ml", mock_ml)

        patterns = [_ht_pattern(constants=[100], line=5)]
        enriched = pc._enrich_hardcoded_thresholds_with_ml(patterns, SOURCE_LINES)

        assert len(enriched) == 1
        detail = enriched[0]["pattern_detail"]
        assert detail["ml_label"] == "business_threshold"
        assert detail["ml_score"] == 0.88
        assert detail["ml_rationale"] == "page size limit"

    def test_non_threshold_patterns_pass_through_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        monkeypatch.setattr(pc, "_classify_hardcoded_threshold_ml", MagicMock(return_value={}))

        other = {
            "pattern_type": "nested_conditionals",
            "module_path": "foo.py",
            "function_name": "f",
            "line_start": 1,
            "line_end": 5,
            "pattern_detail": {"max_depth": 3},
        }
        patterns = [other, _ht_pattern()]
        enriched = pc._enrich_hardcoded_thresholds_with_ml(patterns, SOURCE_LINES)

        assert enriched[0] is other  # unchanged reference
        assert enriched[1]["pattern_type"] == "hardcoded_threshold"

    def test_no_enrichment_when_ml_returns_empty(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        monkeypatch.setattr(pc, "_classify_hardcoded_threshold_ml", MagicMock(return_value={}))

        patterns = [_ht_pattern()]
        enriched = pc._enrich_hardcoded_thresholds_with_ml(patterns, SOURCE_LINES)

        assert "ml_label" not in enriched[0]["pattern_detail"]

    def test_disabled_by_default_returns_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", False)
        mock_ml = MagicMock()
        monkeypatch.setattr(pc, "_classify_hardcoded_threshold_ml", mock_ml)

        patterns = [_ht_pattern()]
        enriched = pc._enrich_hardcoded_thresholds_with_ml(patterns, SOURCE_LINES)

        mock_ml.assert_not_called()
        assert enriched is patterns  # exact same list returned

    def test_original_pattern_detail_preserved(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        monkeypatch.setattr(pc, "_classify_hardcoded_threshold_ml", MagicMock(return_value={
            "label": "algorithm_constant",
            "score": 0.6,
            "rationale": "bit mask",
        }))

        p = _ht_pattern(constants=[256])
        enriched = pc._enrich_hardcoded_thresholds_with_ml([p], SOURCE_LINES)

        detail = enriched[0]["pattern_detail"]
        assert detail["kind"] == "compare"
        assert detail["constants"] == [256]
        assert detail["anomaly_detected"] is True
        assert detail["ml_label"] == "algorithm_constant"


# ── _ML_HT_ENABLED default ────────────────────────────────────────────────────

class TestMlHtEnabledDefault:
    def test_disabled_by_default(self):
        # ml_hardcoded_threshold.enabled defaults to false in config
        assert pc._ML_HT_ENABLED is False

    def test_valid_labels_set(self):
        assert "business_threshold" in pc._ML_HT_VALID_LABELS
        assert "algorithm_constant" in pc._ML_HT_VALID_LABELS
        assert "boundary_check" in pc._ML_HT_VALID_LABELS

    def test_default_model_is_haiku(self):
        assert pc._ML_HT_MODEL == "claude-haiku-4-5-20251001"

    def test_context_lines_configured(self):
        # Config default is 3 lines of context for threshold patterns
        assert pc._ML_HT_CONTEXT_LINES == 3


# ── Integration: _ast_detect_file wires enrichment ───────────────────────────

class TestAstDetectFileIntegration:
    _SOURCE = textwrap.dedent("""\
        def process(items):
            if len(items) > 500:
                raise ValueError("too many")
    """)

    def test_ml_enrichment_called_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", False)
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", False)
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)

        called_with: list[dict] = []

        def fake_enrich(patterns, lines):
            called_with.extend(patterns)
            return patterns

        monkeypatch.setattr(pc, "_enrich_hardcoded_thresholds_with_ml", fake_enrich)

        f = tmp_path / "t.py"
        f.write_text(self._SOURCE, encoding="utf-8")
        pc._ast_detect_file(str(f))

        assert any(
            p["pattern_type"] == "hardcoded_threshold" for p in called_with
        ), "enricher should have received hardcoded_threshold patterns"

    def test_ml_enrichment_not_called_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", False)
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", False)
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", False)

        mock_enrich = MagicMock(side_effect=lambda p, l: p)
        monkeypatch.setattr(pc, "_enrich_hardcoded_thresholds_with_ml", mock_enrich)

        f = tmp_path / "t.py"
        f.write_text(self._SOURCE, encoding="utf-8")
        pc._ast_detect_file(str(f))

        mock_enrich.assert_not_called()
