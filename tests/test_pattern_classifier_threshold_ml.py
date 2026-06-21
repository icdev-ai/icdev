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

def _threshold_pattern(constant: float = 100.0, func: str = "process") -> dict:
    return {
        "pattern_type": "hardcoded_threshold",
        "module_path": "foo.py",
        "function_name": func,
        "line_start": 10,
        "line_end": 10,
        "language": "python",
        "pattern_detail": {"kind": "compare", "constants": [constant], "anomaly_detected": True},
    }


def _make_llm_response(category: str, score: float, rationale: str) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"category": "{category}", "score": {score}, "rationale": "{rationale}"}}'
    )
    return resp


SOURCE_LINES = [""] * 20  # dummy lines so slice arithmetic works


# ── _classify_hardcoded_threshold_ml ─────────────────────────────────────────

class TestClassifyHardcodedThresholdMl:
    def test_returns_parsed_category_and_score(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "dynamic_threshold", 0.87, "learned from traffic patterns"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml(
                "process", "100", ">=", "if count >= 100:\n    alert()"
            )

        assert result["category"] == "dynamic_threshold"
        assert result["score"] == 0.87
        assert result["rationale"] == "learned from traffic patterns"

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content=(
                '```json\n{"category": "config_parameter", "score": 0.5, '
                '"rationale": "should be config"}\n```'
            )
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", "50", ">", "if x > 50:")

        assert result["category"] == "config_parameter"
        assert result["score"] == 0.5

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        result = pc._classify_hardcoded_threshold_ml("f", "42", ">=", "if n >= 42: pass")
        assert result == {}

    def test_returns_empty_dict_on_network_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("connection refused")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", "99", ">", "if x > 99:")
        assert result == {}

    def test_unknown_category_normalised_to_empty_string(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"category": "MAGIC_VALUE", "score": 0.99, "rationale": "bad"}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", "7", ">", "if x > 7:")
        assert result["category"] == ""

    def test_fixed_boundary_category_accepted(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "fixed_boundary", 0.1, "HTTP status codes are structural"
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("check_status", "200", "==", "if code == 200:")
        assert result["category"] == "fixed_boundary"


# ── _enrich_hardcoded_threshold_with_ml ──────────────────────────────────────

class TestEnrichHardcodedThresholdWithMl:
    def test_disabled_returns_patterns_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", False)
        pattern = _threshold_pattern()
        result = pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)
        assert result == [pattern]

    def test_non_threshold_pattern_passes_through(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        other = {
            "pattern_type": "nested_conditionals",
            "module_path": "x.py",
            "function_name": "g",
            "line_start": 1,
            "line_end": 5,
            "pattern_detail": {"max_depth": 4},
        }
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value={}):
            result = pc._enrich_hardcoded_threshold_with_ml([other], SOURCE_LINES)
        assert result == [other]

    def test_enriches_threshold_pattern_with_ml_keys(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        pattern = _threshold_pattern(constant=500.0)
        ml_result = {
            "category": "dynamic_threshold",
            "score": 0.9,
            "rationale": "alert volume varies with traffic",
        }

        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            result = pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)

        assert len(result) == 1
        detail = result[0]["pattern_detail"]
        assert detail["ml_category"] == "dynamic_threshold"
        assert detail["ml_score"] == 0.9
        assert detail["ml_rationale"] == "alert volume varies with traffic"
        assert detail["constants"] == [500.0]  # original key preserved

    def test_failed_ml_call_leaves_pattern_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        pattern = _threshold_pattern()
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value={}):
            result = pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)
        assert "ml_category" not in result[0]["pattern_detail"]

    def test_original_pattern_dict_not_mutated(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        pattern = _threshold_pattern()
        original_detail = dict(pattern["pattern_detail"])
        ml_result = {"category": "config_parameter", "score": 0.4, "rationale": "externalise"}
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)
        assert pattern["pattern_detail"] == original_detail  # original unchanged

    def test_mixed_patterns_only_enriches_threshold(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        ht = _threshold_pattern()
        other = {
            "pattern_type": "regex_user_input",
            "module_path": "y.py",
            "function_name": "h",
            "line_start": 5,
            "line_end": 5,
            "pattern_detail": {"call": "re.match"},
        }
        ml_result = {"category": "dynamic_threshold", "score": 0.8, "rationale": "ok"}
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            result = pc._enrich_hardcoded_threshold_with_ml([ht, other], SOURCE_LINES)
        assert "ml_category" in result[0]["pattern_detail"]
        assert "ml_category" not in result[1]["pattern_detail"]

    def test_binop_pattern_extracts_operator_from_detail(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        binop_pattern = {
            "pattern_type": "hardcoded_threshold",
            "module_path": "bar.py",
            "function_name": "calc",
            "line_start": 3,
            "line_end": 3,
            "pattern_detail": {
                "kind": "binop",
                "op": "Div",
                "constants": [2],
                "anomaly_detected": True,
            },
        }
        ml_result = {"category": "fixed_boundary", "score": 0.1, "rationale": "halving is structural"}
        captured: list[dict] = []

        def fake_classify(function_name, constant, operator, snippet):
            captured.append({"constant": constant, "operator": operator})
            return ml_result

        with patch.object(pc, "_classify_hardcoded_threshold_ml", side_effect=fake_classify):
            pc._enrich_hardcoded_threshold_with_ml([binop_pattern], SOURCE_LINES)

        assert captured[0]["operator"] == "Div"
        assert captured[0]["constant"] == "2"


# ── Integration: _ast_detect_file calls enrichment when enabled ───────────────

class TestAstDetectFileIntegration:
    _THRESHOLD_PY = textwrap.dedent("""\
        def rate_check(count, total, delta):
            baseline = 10.0
            # extra numeric literals to build a population for AD
            a = 20.0
            b = 30.0
            c = 40.0
            d = 50.0
            if count > 999:
                raise ValueError("too many")
            return total / baseline + delta
    """)

    def test_enrichment_not_called_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", False)
        f = tmp_path / "sample.py"
        f.write_text(self._THRESHOLD_PY, encoding="utf-8")

        with patch.object(pc, "_enrich_hardcoded_threshold_with_ml") as mock_enrich:
            results = pc._ast_detect_file(str(f))

        mock_enrich.assert_not_called()
        ht = [r for r in results if r["pattern_type"] == "hardcoded_threshold"]
        assert ht  # detection still works without enrichment

    def test_enrichment_called_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        f = tmp_path / "sample.py"
        f.write_text(self._THRESHOLD_PY, encoding="utf-8")

        def fake_enrich(patterns, lines):
            return patterns

        with patch.object(
            pc, "_enrich_hardcoded_threshold_with_ml", side_effect=fake_enrich
        ) as mock_enrich:
            pc._ast_detect_file(str(f))

        mock_enrich.assert_called_once()
        call_patterns = mock_enrich.call_args[0][0]
        assert any(p["pattern_type"] == "hardcoded_threshold" for p in call_patterns)
