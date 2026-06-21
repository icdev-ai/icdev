# CUI // SP-CTI
"""Tests for the NLP input-reference classifier added to engine.py.

All LLM calls are mocked — no network traffic is required.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.ai_augmentation.engine as engine


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_llm_response(is_remote: bool, project_name: str, confidence: float = 0.9) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"is_remote": {str(is_remote).lower()}, '
        f'"project_name": "{project_name}", '
        f'"confidence": {confidence}}}'
    )
    return resp


def _mock_llm_modules(is_remote: bool, project_name: str, confidence: float = 0.9):
    mock_router = MagicMock()
    mock_router.invoke.return_value = _make_llm_response(is_remote, project_name, confidence)
    return {
        "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
        "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
    }


# ── _classify_input_ref_nlp ───────────────────────────────────────────────────

class TestClassifyInputRefNlp:
    def test_returns_dict_for_remote_url(self):
        with patch.dict("sys.modules", _mock_llm_modules(True, "my-repo")):
            result = engine._classify_input_ref_nlp("https://github.com/org/my-repo.git")
        assert result is not None
        assert result["is_remote"] is True
        assert result["project_name"] == "my-repo"
        assert 0.0 <= result["confidence"] <= 1.0

    def test_returns_dict_for_local_path(self):
        with patch.dict("sys.modules", _mock_llm_modules(False, "myproject")):
            result = engine._classify_input_ref_nlp("/home/user/myproject")
        assert result is not None
        assert result["is_remote"] is False
        assert result["project_name"] == "myproject"

    def test_returns_none_on_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # type: ignore[arg-type]
        result = engine._classify_input_ref_nlp("https://github.com/org/repo")
        assert result is None

    def test_returns_none_on_network_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("timeout")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = engine._classify_input_ref_nlp("https://github.com/org/repo")
        assert result is None

    def test_returns_none_when_json_unparseable(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(content="not json at all")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = engine._classify_input_ref_nlp("some/path")
        assert result is None

    def test_strips_markdown_fences(self):
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='```json\n{"is_remote": false, "project_name": "proj", "confidence": 0.8}\n```'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = engine._classify_input_ref_nlp("/some/proj")
        assert result is not None
        assert result["project_name"] == "proj"


# ── _is_git_url (NLP-enabled path) ───────────────────────────────────────────

class TestIsGitUrlNlp:
    def test_nlp_disabled_uses_regex_for_https(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        assert engine._is_git_url("https://github.com/org/repo") is True

    def test_nlp_disabled_uses_regex_for_git_at(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        assert engine._is_git_url("git@github.com:org/repo.git") is True

    def test_nlp_disabled_uses_regex_for_local_path(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        assert engine._is_git_url("/home/user/project") is False

    def test_nlp_enabled_returns_llm_result_true(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        nlp_result = {"is_remote": True, "project_name": "repo", "confidence": 0.95}
        with patch.object(engine, "_classify_input_ref_nlp", return_value=nlp_result):
            assert engine._is_git_url("https://github.com/org/repo") is True

    def test_nlp_enabled_returns_llm_result_false(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        nlp_result = {"is_remote": False, "project_name": "project", "confidence": 0.9}
        with patch.object(engine, "_classify_input_ref_nlp", return_value=nlp_result):
            assert engine._is_git_url("/local/project") is False

    def test_nlp_enabled_falls_back_to_regex_on_none(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        with patch.object(engine, "_classify_input_ref_nlp", return_value=None):
            assert engine._is_git_url("https://github.com/org/repo") is True

    def test_nlp_enabled_falls_back_to_regex_on_none_local(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        with patch.object(engine, "_classify_input_ref_nlp", return_value=None):
            assert engine._is_git_url("/local/path") is False


# ── _build_summary (NLP-enabled path) ────────────────────────────────────────

class TestBuildSummaryNlp:
    def test_nlp_disabled_extracts_name_from_url(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        summary = engine._build_summary("https://github.com/org/my-repo.git", [])
        assert "my-repo" in summary

    def test_nlp_disabled_extracts_name_from_local_path(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        summary = engine._build_summary("/home/user/myproject", [])
        assert "myproject" in summary

    def test_nlp_enabled_uses_llm_project_name(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        nlp_result = {"is_remote": True, "project_name": "nlp-extracted-name", "confidence": 0.9}
        with patch.object(engine, "_classify_input_ref_nlp", return_value=nlp_result):
            summary = engine._build_summary("https://github.com/org/something.git", [])
        assert "nlp-extracted-name" in summary

    def test_nlp_enabled_falls_back_to_regex_on_none(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        with patch.object(engine, "_classify_input_ref_nlp", return_value=None):
            summary = engine._build_summary("https://github.com/org/fallback-repo.git", [])
        assert "fallback-repo" in summary

    def test_nlp_enabled_falls_back_to_regex_when_project_name_empty(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", True)
        nlp_result = {"is_remote": True, "project_name": "", "confidence": 0.5}
        with patch.object(engine, "_classify_input_ref_nlp", return_value=nlp_result):
            summary = engine._build_summary("https://github.com/org/target-repo.git", [])
        assert "target-repo" in summary

    def test_summary_includes_opportunity_count(self, monkeypatch):
        monkeypatch.setattr(engine, "_NLP_INPUT_ENABLED", False)
        opps = [
            {"pattern_type": "regex_user_input", "ai_paradigm": "nlp_extractor"},
            {"pattern_type": "regex_user_input", "ai_paradigm": "nlp_extractor"},
        ]
        summary = engine._build_summary("/some/project", opps)
        assert "2" in summary
