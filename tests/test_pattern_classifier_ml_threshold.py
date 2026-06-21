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

def _threshold_pattern(constants: list | None = None, func: str = "check_limit") -> dict:
    return {
        "pattern_type": "hardcoded_threshold",
        "module_path": "foo.py",
        "function_name": func,
        "line_start": 10,
        "line_end": 10,
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
        mock_router.invoke.return_value = _make_llm_response("high", 0.9, "retry limit")

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml(
                "process_request", [500], "if retries > 500:\n    raise"
            )

        assert result["label"] == "high"
        assert result["score"] == 0.9
        assert result["rationale"] == "retry limit"

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='```json\n{"label": "medium", "score": 0.5, "rationale": "ok"}\n```'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [100], "if x > 100:")

        assert result["label"] == "medium"
        assert result["score"] == 0.5

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        result = pc._classify_hardcoded_threshold_ml("f", [42], "if x > 42: pass")
        assert result == {}

    def test_returns_empty_dict_on_network_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("timeout")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [42], "if x > 42: pass")
        assert result == {}

    def test_invalid_label_normalised_to_empty_string(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"label": "UNKNOWN_LABEL", "score": 0.4, "rationale": "n/a"}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_hardcoded_threshold_ml("f", [42], "if x > 42: pass")
        assert result["label"] == ""

    def test_valid_labels_pass_through(self):
        for label in ("high", "medium", "low"):
            mock_router = MagicMock()
            mock_router.invoke.return_value = MagicMock(
                content=f'{{"label": "{label}", "score": 0.5, "rationale": "ok"}}'
            )
            with patch.dict("sys.modules", {
                "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
                "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
            }):
                result = pc._classify_hardcoded_threshold_ml("f", [10], "if x > 10:")
            assert result["label"] == label


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
        pattern = _threshold_pattern([500])
        ml_result = {
            "label": "high",
            "score": 0.92,
            "rationale": "retry limit should be tunable per environment",
        }

        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            result = pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)

        assert len(result) == 1
        detail = result[0]["pattern_detail"]
        assert detail["ml_label"] == "high"
        assert detail["ml_score"] == 0.92
        assert detail["ml_rationale"] == "retry limit should be tunable per environment"
        assert detail["constants"] == [500]  # original key preserved

    def test_failed_ml_call_leaves_pattern_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        pattern = _threshold_pattern()
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value={}):
            result = pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)
        assert "ml_label" not in result[0]["pattern_detail"]
        assert result[0]["pattern_detail"]["constants"] == [500]

    def test_original_pattern_dict_not_mutated(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        pattern = _threshold_pattern([99])
        original_detail = dict(pattern["pattern_detail"])
        ml_result = {"label": "medium", "score": 0.6, "rationale": "ok"}
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            pc._enrich_hardcoded_threshold_with_ml([pattern], SOURCE_LINES)
        assert pattern["pattern_detail"] == original_detail

    def test_mixed_patterns_only_enriches_hardcoded_threshold(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", True)
        threshold_p = _threshold_pattern([200])
        other = {
            "pattern_type": "regex_user_input",
            "module_path": "y.py",
            "function_name": "h",
            "line_start": 1,
            "line_end": 1,
            "pattern_detail": {"call": "re.match"},
        }
        ml_result = {"label": "low", "score": 0.2, "rationale": "boundary check"}
        with patch.object(pc, "_classify_hardcoded_threshold_ml", return_value=ml_result):
            result = pc._enrich_hardcoded_threshold_with_ml([threshold_p, other], SOURCE_LINES)
        assert "ml_label" in result[0]["pattern_detail"]
        assert "ml_label" not in result[1]["pattern_detail"]


# ── Integration: _ast_detect_file calls ML enrichment when enabled ────────────

class TestAstDetectFileMlThresholdIntegration:
    _THRESHOLD_PY = textwrap.dedent("""\
        def check_rate(count):
            if count > 500:
                raise ValueError("rate exceeded")
    """)

    def test_ml_enrichment_not_called_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_HT_ENABLED", False)
        f = tmp_path / "sample.py"
        f.write_text(self._THRESHOLD_PY, encoding="utf-8")

        with patch.object(pc, "_enrich_hardcoded_threshold_with_ml") as mock_enrich:
            results = pc._ast_detect_file(str(f))

        mock_enrich.assert_not_called()
        threshold_hits = [r for r in results if r["pattern_type"] == "hardcoded_threshold"]
        assert threshold_hits  # detection still works

    def test_ml_enrichment_called_when_enabled(self, tmp_path, monkeypatch):
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
