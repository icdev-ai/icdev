# CUI // SP-CTI
"""Tests for the ML classifier enrichment of nested_conditionals patterns.

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

def _nested_pattern(depth: int = 4, func: str = "classify") -> dict:
    return {
        "pattern_type": "nested_conditionals",
        "module_path": "foo.py",
        "function_name": func,
        "line_start": 10,
        "line_end": 30,
        "language": "python",
        "pattern_detail": {"max_depth": depth},
    }


def _make_llm_response(label: str, score: float, rationale: str) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"label": "{label}", "score": {score}, "rationale": "{rationale}"}}'
    )
    return resp


SOURCE_LINES = [""] * 35  # dummy lines so slice arithmetic works


# ── _classify_nested_conditional_ml ──────────────────────────────────────────

class TestClassifyNestedConditionalMl:
    def test_returns_parsed_label_and_score(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response("high", 0.9, "complex rules")

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_nested_conditional_ml("my_func", 5, "if a:\n  if b:")

        assert result["label"] == "high"
        assert result["score"] == 0.9
        assert result["rationale"] == "complex rules"

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='```json\n{"label": "medium", "score": 0.5, "rationale": "ok"}\n```'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_nested_conditional_ml("f", 3, "if x: if y:")

        assert result["label"] == "medium"
        assert result["score"] == 0.5

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        # When import fails, function must not raise
        result = pc._classify_nested_conditional_ml("f", 3, "if x: pass")
        assert result == {}

    def test_returns_empty_dict_on_network_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("connection refused")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_nested_conditional_ml("f", 4, "if x: if y:")
        assert result == {}

    def test_unknown_label_is_normalised_to_empty_string(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"label": "CRITICAL", "score": 0.99, "rationale": "bad"}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_nested_conditional_ml("f", 4, "if x: if y:")
        assert result["label"] == ""


# ── _enrich_nested_conditionals_with_ml ──────────────────────────────────────

class TestEnrichNestedConditionalsWithMl:
    def test_disabled_returns_patterns_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", False)
        pattern = _nested_pattern()
        result = pc._enrich_nested_conditionals_with_ml([pattern], SOURCE_LINES)
        assert result == [pattern]

    def test_non_nested_pattern_passes_through(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        other = {
            "pattern_type": "hardcoded_threshold",
            "module_path": "x.py",
            "function_name": "g",
            "line_start": 1,
            "line_end": 2,
            "pattern_detail": {"kind": "compare"},
        }
        with patch.object(pc, "_classify_nested_conditional_ml", return_value={}):
            result = pc._enrich_nested_conditionals_with_ml([other], SOURCE_LINES)
        assert result == [other]

    def test_enriches_nested_conditional_with_ml_keys(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        pattern = _nested_pattern()
        ml_result = {"label": "high", "score": 0.88, "rationale": "complex"}

        with patch.object(pc, "_classify_nested_conditional_ml", return_value=ml_result):
            result = pc._enrich_nested_conditionals_with_ml([pattern], SOURCE_LINES)

        assert len(result) == 1
        detail = result[0]["pattern_detail"]
        assert detail["ml_label"] == "high"
        assert detail["ml_score"] == 0.88
        assert detail["ml_rationale"] == "complex"
        assert detail["max_depth"] == 4  # original key preserved

    def test_failed_ml_call_leaves_pattern_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        pattern = _nested_pattern()
        with patch.object(pc, "_classify_nested_conditional_ml", return_value={}):
            result = pc._enrich_nested_conditionals_with_ml([pattern], SOURCE_LINES)
        assert result[0]["pattern_detail"] == {"max_depth": 4}

    def test_original_pattern_dict_not_mutated(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        pattern = _nested_pattern()
        original_detail = dict(pattern["pattern_detail"])
        ml_result = {"label": "low", "score": 0.2, "rationale": "guard clause"}
        with patch.object(pc, "_classify_nested_conditional_ml", return_value=ml_result):
            pc._enrich_nested_conditionals_with_ml([pattern], SOURCE_LINES)
        assert pattern["pattern_detail"] == original_detail  # original unchanged

    def test_mixed_patterns_only_enriches_nested_conditionals(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        nc = _nested_pattern()
        other = {"pattern_type": "regex_user_input", "module_path": "y.py",
                 "function_name": "h", "line_start": 5, "line_end": 5,
                 "pattern_detail": {"call": "re.match"}}
        ml_result = {"label": "medium", "score": 0.6, "rationale": "some rules"}
        with patch.object(pc, "_classify_nested_conditional_ml", return_value=ml_result):
            result = pc._enrich_nested_conditionals_with_ml([nc, other], SOURCE_LINES)
        assert "ml_label" in result[0]["pattern_detail"]
        assert "ml_label" not in result[1]["pattern_detail"]


# ── Integration: _ast_detect_file calls enrichment when enabled ───────────────

class TestAstDetectFileIntegration:
    _DEEP_NESTED_PY = textwrap.dedent("""\
        def classify(x, y, z, w):
            if x > 0:
                if y > 0:
                    if z > 0:
                        if w > 0:
                            return "all positive"
                        return "w non-positive"
                    return "z non-positive"
                return "y non-positive"
            return "x non-positive"
    """)

    def test_enrichment_not_called_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", False)
        f = tmp_path / "sample.py"
        f.write_text(self._DEEP_NESTED_PY, encoding="utf-8")

        with patch.object(pc, "_enrich_nested_conditionals_with_ml") as mock_enrich:
            results = pc._ast_detect_file(str(f))

        mock_enrich.assert_not_called()
        nc = [r for r in results if r["pattern_type"] == "nested_conditionals"]
        assert nc  # detection still works

    def test_enrichment_called_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_NC_ENABLED", True)
        f = tmp_path / "sample.py"
        f.write_text(self._DEEP_NESTED_PY, encoding="utf-8")

        object()

        def fake_enrich(patterns, lines):
            return patterns  # pass-through but trackable

        with patch.object(pc, "_enrich_nested_conditionals_with_ml", side_effect=fake_enrich) as mock_enrich:
            pc._ast_detect_file(str(f))

        mock_enrich.assert_called_once()
        call_patterns = mock_enrich.call_args[0][0]
        assert any(p["pattern_type"] == "nested_conditionals" for p in call_patterns)
