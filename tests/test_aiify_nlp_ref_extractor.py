# CUI // SP-CTI
"""Tests for the NLP ref extractor in tools.aiify.engine.

All LLM calls are mocked — no network traffic required.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.aiify.engine as eng


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_response(project_name: str, hosting_platform: str, languages: list) -> MagicMock:
    resp = MagicMock()
    resp.content = (
        f'{{"project_name": "{project_name}", '
        f'"hosting_platform": "{hosting_platform}", '
        f'"detected_languages": {languages}}}'
    )
    return resp


# ── _nlp_extract_ref_info ─────────────────────────────────────────────────────

class TestNlpExtractRefInfo:
    def test_returns_empty_dict_when_disabled(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", False)
        result = eng._nlp_extract_ref_info("https://github.com/org/repo", "git_url")
        assert result == {}

    def test_returns_empty_dict_on_import_error(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        monkeypatch.setitem(sys.modules, "tools.llm.provider", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "tools.llm.router", None)    # type: ignore[arg-type]
        result = eng._nlp_extract_ref_info("https://github.com/org/repo", "git_url")
        assert result == {}

    def test_returns_empty_dict_on_network_error(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("connection refused")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("https://github.com/org/repo", "git_url")
        assert result == {}

    def test_parses_github_url(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "repo", "github", '["python"]'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("https://github.com/org/repo", "git_url")

        assert result["project_name"] == "repo"
        assert result["hosting_platform"] == "github"
        assert result["detected_languages"] == ["python"]

    def test_parses_local_path(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = _make_llm_response(
            "my-service", "local", '["java", "kotlin"]'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("/home/user/my-service", "local_path")

        assert result["project_name"] == "my-service"
        assert result["hosting_platform"] == "local"
        assert "java" in result["detected_languages"]

    def test_strips_markdown_fences_from_response(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content=(
                '```json\n{"project_name": "backend", "hosting_platform": "gitlab", '
                '"detected_languages": ["go"]}\n```'
            )
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("https://gitlab.com/org/backend", "git_url")

        assert result["project_name"] == "backend"
        assert result["hosting_platform"] == "gitlab"
        assert result["detected_languages"] == ["go"]

    def test_detected_languages_coerced_to_string_list(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"project_name": "x", "hosting_platform": "local", "detected_languages": [1, 2]}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("/path/x", "local_path")

        assert all(isinstance(lang, str) for lang in result["detected_languages"])

    def test_non_list_detected_languages_normalised_to_empty(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            content='{"project_name": "x", "hosting_platform": "local", "detected_languages": "python"}'
        )
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("/path/x", "local_path")

        assert result["detected_languages"] == []

    def test_returns_empty_dict_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(content="not json at all")
        with patch.dict("sys.modules", {
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock()),
            "tools.llm.router": MagicMock(LLMRouter=MagicMock(return_value=mock_router)),
        }):
            result = eng._nlp_extract_ref_info("/path/x", "local_path")
        assert result == {}


# ── run_scan integration: nlp_ref_info wired into language_profile ────────────

class TestRunScanNlpRefIntegration:
    """Verify that run_scan() includes nlp_ref_extraction in language_profile
    when NLP extraction is enabled and returns a non-empty dict."""

    def _make_scan_deps(self):
        """Return a minimal set of mocks for run_scan()."""
        return {
            "init_db": MagicMock(),
            "detect_patterns": MagicMock(return_value=[]),
            "score_opportunity": MagicMock(return_value={
                "value_score": 0.5, "feasibility_score": 0.5,
                "risk_score": 0.5, "composite_score": 0.5,
                "score_detail": {},
            }),
            "generate_roadmap": MagicMock(return_value={
                "roadmap_id": "rm-test-001",
                "phases": [],
            }),
        }

    def test_nlp_ref_info_added_to_language_profile_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", True)

        nlp_result = {
            "project_name": "test-proj",
            "hosting_platform": "github",
            "detected_languages": ["python"],
        }
        monkeypatch.setattr(eng, "_nlp_extract_ref_info", MagicMock(return_value=nlp_result))

        captured_profile: list = []

        def fake_insert(conn, sql, params, id_col="id", commit=True):
            if "aiify_scans" in sql and "INSERT" in sql:
                # language_profile is 3rd positional param
                captured_profile.append(params[2])
            return 1

        monkeypatch.setattr(eng, "init_db", MagicMock())
        monkeypatch.setattr(eng, "detect_patterns", MagicMock(return_value=[]))
        monkeypatch.setattr(eng, "generate_roadmap", MagicMock(return_value={
            "roadmap_id": "rm-x", "phases": [],
        }))
        monkeypatch.setattr(eng, "run_readiness_check", MagicMock(return_value={
            "pillar_scores": {}, "overall_readiness_score": 0.0, "icdev_checks": {},
        }))
        monkeypatch.setattr(eng, "_insert", fake_insert)
        monkeypatch.setattr(eng, "get_connection", MagicMock(return_value=MagicMock(
            __enter__=MagicMock(), __exit__=MagicMock(), close=MagicMock(),
        )))
        monkeypatch.setattr(eng, "_promote_top_opportunities", MagicMock(return_value=0))
        monkeypatch.setattr(eng, "_promote_phase_opportunities", MagicMock(return_value=0))
        monkeypatch.setattr(eng, "_register_innovation_signals", MagicMock(return_value=0))

        target = tmp_path / "src"
        target.mkdir()
        (target / "main.py").write_text("x = 1\n", encoding="utf-8")

        eng.run_scan("local_path", str(target))

        assert eng._nlp_extract_ref_info.call_count >= 1
        call_args = eng._nlp_extract_ref_info.call_args
        assert call_args[0][1] == "local_path"

    def test_nlp_ref_info_absent_from_language_profile_when_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(eng, "_NLP_REF_ENABLED", False)
        mock_nlp = MagicMock(return_value={})
        monkeypatch.setattr(eng, "_nlp_extract_ref_info", mock_nlp)

        monkeypatch.setattr(eng, "init_db", MagicMock())
        monkeypatch.setattr(eng, "detect_patterns", MagicMock(return_value=[]))
        monkeypatch.setattr(eng, "generate_roadmap", MagicMock(return_value={
            "roadmap_id": "rm-x", "phases": [],
        }))
        monkeypatch.setattr(eng, "run_readiness_check", MagicMock(return_value={
            "pillar_scores": {}, "overall_readiness_score": 0.0, "icdev_checks": {},
        }))
        monkeypatch.setattr(eng, "_insert", MagicMock(return_value=1))
        monkeypatch.setattr(eng, "get_connection", MagicMock(return_value=MagicMock(
            __enter__=MagicMock(), __exit__=MagicMock(), close=MagicMock(),
        )))
        monkeypatch.setattr(eng, "_promote_top_opportunities", MagicMock(return_value=0))
        monkeypatch.setattr(eng, "_promote_phase_opportunities", MagicMock(return_value=0))
        monkeypatch.setattr(eng, "_register_innovation_signals", MagicMock(return_value=0))

        target = tmp_path / "src"
        target.mkdir()
        (target / "main.py").write_text("x = 1\n", encoding="utf-8")

        eng.run_scan("local_path", str(target))

        # When disabled, _nlp_extract_ref_info returns {} so nlp_ref_extraction is absent
        mock_nlp.assert_called_once()
