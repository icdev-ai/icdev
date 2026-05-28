# CUI // SP-CTI
"""Tests for the NLP extractor enrichment of regex_user_input patterns.

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

def _regex_pattern(call: str = "re.match", func: str = "validate_email") -> dict:
    return {
        "pattern_type": "regex_user_input",
        "module_path": "foo.py",
        "function_name": func,
        "line_start": 5,
        "line_end": 5,
        "language": "python",
        "pattern_detail": {"call": call},
    }


def _make_llm_response(intent: str, nlp_candidate: str, score: float, rationale: str) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"intent": "{intent}", "nlp_candidate": "{nlp_candidate}", '
        f'"score": {score}, "rationale": "{rationale}"}}'
    )
    return resp


SOURCE_LINES = [""] * 15  # dummy lines so slice arithmetic works


# ── _classify_regex_nlp ───────────────────────────────────────────────────────

class TestClassifyRegexNlp:
    def test_returns_parsed_intent_and_score(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "validation", "email classifier", 0.8, "handles email format"
        )

        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_regex_nlp(
                "validate_email", "re.match", r'if re.match(r"^\S+@\S+$", email):'
            )

        assert result["intent"] == "validation"
        assert result["nlp_candidate"] == "email classifier"
        assert result["score"] == 0.8
        assert "rationale" in result

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content=(
                '```json\n{"intent": "extraction", "nlp_candidate": "NER", '
                '"score": 0.6, "rationale": "entity extraction"}\n```'
            )
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_regex_nlp("extract_name", "re.search", "re.search(r'\\w+', text)")

        assert result["intent"] == "extraction"
        assert result["score"] == 0.6

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        result = pc._classify_regex_nlp("f", "re.match", "if re.match(r'.*', x):")
        assert result == {}

    def test_returns_empty_dict_on_network_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("timeout")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_regex_nlp("f", "re.match", "if re.match(r'.*', x):")
        assert result == {}

    def test_unknown_intent_normalised_to_unknown(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"intent": "COMPLEX_PARSE", "nlp_candidate": "none", "score": 0.1, "rationale": "ok"}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = pc._classify_regex_nlp("f", "re.compile", "re.compile(r'x')")
        assert result["intent"] == "unknown"

    def test_valid_intents_pass_through(self):
        for intent in ("validation", "extraction", "parsing", "sanitization", "unknown"):
            mock_router = MagicMock()
            mock_router.invoke.return_value = MagicMock(
                content=f'{{"intent": "{intent}", "nlp_candidate": "none", "score": 0.5, "rationale": "ok"}}'
            )
            with patch.dict("sys.modules", {
                "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
                "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
            }):
                result = pc._classify_regex_nlp("f", "re.match", "x")
            assert result["intent"] == intent


# ── _enrich_regex_user_input_with_nlp ────────────────────────────────────────

class TestEnrichRegexUserInputWithNlp:
    def test_disabled_returns_patterns_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", False)
        pattern = _regex_pattern()
        result = pc._enrich_regex_user_input_with_nlp([pattern], SOURCE_LINES)
        assert result == [pattern]

    def test_non_regex_pattern_passes_through(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        other = {
            "pattern_type": "nested_conditionals",
            "module_path": "x.py",
            "function_name": "g",
            "line_start": 1,
            "line_end": 5,
            "pattern_detail": {"max_depth": 4},
        }
        with patch.object(pc, "_classify_regex_nlp", return_value={}):
            result = pc._enrich_regex_user_input_with_nlp([other], SOURCE_LINES)
        assert result == [other]

    def test_enriches_regex_pattern_with_nlp_keys(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        pattern = _regex_pattern()
        nlp_result = {
            "intent": "validation",
            "nlp_candidate": "email classifier",
            "score": 0.85,
            "rationale": "standard email validation",
        }

        with patch.object(pc, "_classify_regex_nlp", return_value=nlp_result):
            result = pc._enrich_regex_user_input_with_nlp([pattern], SOURCE_LINES)

        assert len(result) == 1
        detail = result[0]["pattern_detail"]
        assert detail["nlp_intent"] == "validation"
        assert detail["nlp_candidate"] == "email classifier"
        assert detail["nlp_score"] == 0.85
        assert detail["nlp_rationale"] == "standard email validation"
        assert detail["call"] == "re.match"  # original key preserved

    def test_failed_nlp_call_leaves_pattern_unchanged(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        pattern = _regex_pattern()
        with patch.object(pc, "_classify_regex_nlp", return_value={}):
            result = pc._enrich_regex_user_input_with_nlp([pattern], SOURCE_LINES)
        assert result[0]["pattern_detail"] == {"call": "re.match"}

    def test_original_pattern_dict_not_mutated(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        pattern = _regex_pattern()
        original_detail = dict(pattern["pattern_detail"])
        nlp_result = {
            "intent": "extraction",
            "nlp_candidate": "NER",
            "score": 0.7,
            "rationale": "entity names",
        }
        with patch.object(pc, "_classify_regex_nlp", return_value=nlp_result):
            pc._enrich_regex_user_input_with_nlp([pattern], SOURCE_LINES)
        assert pattern["pattern_detail"] == original_detail

    def test_mixed_patterns_only_enriches_regex_user_input(self, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        regex_p = _regex_pattern()
        other = {
            "pattern_type": "hardcoded_threshold",
            "module_path": "y.py",
            "function_name": "h",
            "line_start": 1,
            "line_end": 1,
            "pattern_detail": {"kind": "compare"},
        }
        nlp_result = {"intent": "parsing", "nlp_candidate": "none", "score": 0.3, "rationale": "ok"}
        with patch.object(pc, "_classify_regex_nlp", return_value=nlp_result):
            result = pc._enrich_regex_user_input_with_nlp([regex_p, other], SOURCE_LINES)
        assert "nlp_intent" in result[0]["pattern_detail"]
        assert "nlp_intent" not in result[1]["pattern_detail"]


# ── Integration: _ast_detect_file calls NLP enrichment when enabled ───────────

class TestAstDetectFileNlpIntegration:
    _REGEX_PY = textwrap.dedent("""\
        import re

        def validate_input(user_text):
            if re.match(r'^[a-zA-Z0-9]+$', user_text):
                return True
            return False
    """)

    def test_nlp_enrichment_not_called_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", False)
        f = tmp_path / "sample.py"
        f.write_text(self._REGEX_PY, encoding="utf-8")

        with patch.object(pc, "_enrich_regex_user_input_with_nlp") as mock_enrich:
            results = pc._ast_detect_file(str(f))

        mock_enrich.assert_not_called()
        regex_hits = [r for r in results if r["pattern_type"] == "regex_user_input"]
        assert regex_hits  # detection still works

    def test_nlp_enrichment_called_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_ML_RUI_ENABLED", True)
        f = tmp_path / "sample.py"
        f.write_text(self._REGEX_PY, encoding="utf-8")

        def fake_enrich(patterns, lines):
            return patterns

        with patch.object(pc, "_enrich_regex_user_input_with_nlp", side_effect=fake_enrich) as mock_enrich:
            pc._ast_detect_file(str(f))

        mock_enrich.assert_called_once()
        call_patterns = mock_enrich.call_args[0][0]
        assert any(p["pattern_type"] == "regex_user_input" for p in call_patterns)
