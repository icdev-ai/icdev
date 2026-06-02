# CUI // SP-CTI
"""Tests for the NLP subject extractor in oracle_triage.

Covers _validate_extracted_subject, _extract_subject (regex path, NLP path,
and fallback), plus the gate behaviour of _nlp_extract_subject.
"""
from __future__ import annotations

import os
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root on path for import without installing the package.
_REPO = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools.genesis.reflexes.oracle_triage import (
    _LENS_VALIDATORS,
    _extract_subject,
    _validate_extracted_subject,
)


# ---------------------------------------------------------------------------
# _validate_extracted_subject
# ---------------------------------------------------------------------------

class TestValidateExtractedSubject:
    def test_empty_string_invalid(self):
        assert _validate_extracted_subject("", "tool_not_in_manifest") is False

    def test_whitespace_only_invalid(self):
        assert _validate_extracted_subject("   ", "orphan_db_table") is False

    def test_valid_file_path(self):
        assert _validate_extracted_subject("tools/ttx/engine.py", "tool_not_in_manifest") is True

    def test_valid_nested_file_path(self):
        assert _validate_extracted_subject("icdev/tools/genesis/harness/eval.py", "tool_not_in_manifest") is True

    def test_invalid_file_path_no_extension(self):
        assert _validate_extracted_subject("tools/ttx/engine", "tool_not_in_manifest") is False

    def test_valid_route(self):
        assert _validate_extracted_subject("/api/agents/status", "route_not_listed") is True

    def test_valid_route_root(self):
        assert _validate_extracted_subject("/", "route_not_listed") is True

    def test_invalid_route_no_leading_slash(self):
        assert _validate_extracted_subject("api/agents/status", "route_not_listed") is False

    def test_valid_table_name(self):
        assert _validate_extracted_subject("kanban_tasks", "orphan_db_table") is True

    def test_invalid_table_name_too_short(self):
        assert _validate_extracted_subject("x", "orphan_db_table") is False

    def test_unknown_lens_accepts_any_non_empty(self):
        assert _validate_extracted_subject("anything", "unknown_lens") is True


# ---------------------------------------------------------------------------
# _extract_subject — regex (canonical prefix) path
# ---------------------------------------------------------------------------

class TestExtractSubjectRegex:
    """Standard "lens gap: <subject>" titles are resolved deterministically."""

    def test_tool_not_in_manifest_standard(self):
        result = _extract_subject(
            "tool_not_in_manifest gap: tools/dic/engine.py",
            "tool_not_in_manifest",
        )
        assert result == "tools/dic/engine.py"

    def test_route_not_listed_standard(self):
        result = _extract_subject(
            "route_not_listed gap: /studio/narrate",
            "route_not_listed",
        )
        assert result == "/studio/narrate"

    def test_orphan_db_table_standard(self):
        result = _extract_subject(
            "orphan_db_table gap: harness_eval",
            "orphan_db_table",
        )
        assert result == "harness_eval"

    def test_standard_title_no_nlp_call(self):
        """NLP extractor should NOT be called when regex succeeds."""
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_subject"
        ) as mock_nlp:
            _extract_subject(
                "tool_not_in_manifest gap: tools/foo/bar.py",
                "tool_not_in_manifest",
            )
            mock_nlp.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_subject — NLP fallback path
# ---------------------------------------------------------------------------

class TestExtractSubjectNLPFallback:
    """Non-standard titles fall back to NLP extraction when available."""

    def test_nonstandard_title_uses_nlp(self):
        """NLP is called when prefix regex doesn't match."""
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_subject",
            return_value="tools/audit/scanner.py",
        ) as mock_nlp:
            result = _extract_subject(
                "Missing manifest entry for tools/audit/scanner.py",
                "tool_not_in_manifest",
            )
            assert result == "tools/audit/scanner.py"
            mock_nlp.assert_called_once()

    def test_nonstandard_title_falls_back_to_raw_when_nlp_none(self):
        """When NLP returns None, the raw title is used as last resort."""
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_subject",
            return_value=None,
        ):
            raw = "Route /api/iqe-query not listed in start.md"
            result = _extract_subject(raw, "route_not_listed")
            assert result == raw

    def test_nlp_result_returned_for_nonstandard(self):
        """NLP-extracted route is returned correctly."""
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_subject",
            return_value="/api/iqe-query",
        ):
            result = _extract_subject(
                "Route /api/iqe-query not listed in start.md",
                "route_not_listed",
            )
            assert result == "/api/iqe-query"


# ---------------------------------------------------------------------------
# _nlp_extract_subject — gate behaviour (no LLM call needed)
# ---------------------------------------------------------------------------

class TestNLPExtractSubjectGate:
    """_nlp_extract_subject returns None when the gate env var is off."""

    def test_gate_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        from tools.genesis.reflexes.oracle_triage import _nlp_extract_subject
        result = _nlp_extract_subject("tool_not_in_manifest gap: tools/foo.py", "tool_not_in_manifest")
        assert result is None

    def test_gate_off_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "false")
        from tools.genesis.reflexes.oracle_triage import _nlp_extract_subject
        result = _nlp_extract_subject("tool_not_in_manifest gap: tools/foo.py", "tool_not_in_manifest")
        assert result is None

    def test_gate_on_attempts_llm(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        mock_response = MagicMock()
        mock_response.content = "tools/foo/bar.py"
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_cls = MagicMock(return_value=mock_router_instance)
        mock_request_cls = MagicMock()

        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", mock_request_cls):
            import tools.genesis.reflexes.oracle_triage as mod
            with patch.object(mod, "_validate_extracted_subject", return_value=True):
                result = mod._nlp_extract_subject(
                    "Missing manifest entry for tools/foo/bar.py",
                    "tool_not_in_manifest",
                )
        assert result == "tools/foo/bar.py"

    def test_gate_on_llm_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        import tools.genesis.reflexes.oracle_triage as mod

        with patch("tools.llm.router.LLMRouter", side_effect=RuntimeError("no connection")):
            result = mod._nlp_extract_subject("some title", "tool_not_in_manifest")
            assert result is None


# ---------------------------------------------------------------------------
# _nlp_extract_batch_subjects — gate and behaviour
# ---------------------------------------------------------------------------

class TestNLPExtractBatchSubjects:
    """_nlp_extract_batch_subjects gate and output filtering."""

    def test_gate_off_by_default_returns_empty(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        import tools.genesis.reflexes.oracle_triage as mod
        result = mod._nlp_extract_batch_subjects("tool_not_in_manifest", "some description")
        assert result == []

    def test_gate_off_explicit_false_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "false")
        import tools.genesis.reflexes.oracle_triage as mod
        result = mod._nlp_extract_batch_subjects("orphan_db_table", "some description")
        assert result == []

    def test_gate_on_returns_validated_subjects(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        mock_response = MagicMock()
        mock_response.content = "tools/foo/bar.py\ntools/baz/qux.py\nnot_valid_no_ext"
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_cls = MagicMock(return_value=mock_router_instance)

        import tools.genesis.reflexes.oracle_triage as mod
        with patch("tools.llm.router.LLMRouter", mock_router_cls), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = mod._nlp_extract_batch_subjects("tool_not_in_manifest", "description")

        assert "tools/foo/bar.py" in result
        assert "tools/baz/qux.py" in result
        # no-extension string fails validator and must be filtered
        assert "not_valid_no_ext" not in result

    def test_gate_on_filters_invalid_routes(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        mock_response = MagicMock()
        mock_response.content = "/api/canvas/report\nnot-a-route"
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response

        import tools.genesis.reflexes.oracle_triage as mod
        with patch("tools.llm.router.LLMRouter", MagicMock(return_value=mock_router_instance)), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = mod._nlp_extract_batch_subjects("route_not_listed", "description")

        assert "/api/canvas/report" in result
        assert "not-a-route" not in result

    def test_gate_on_llm_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        import tools.genesis.reflexes.oracle_triage as mod
        with patch("tools.llm.router.LLMRouter", side_effect=RuntimeError("no connection")):
            result = mod._nlp_extract_batch_subjects("orphan_db_table", "some description")
        assert result == []

    def test_empty_llm_response_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        mock_response = MagicMock()
        mock_response.content = ""
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response

        import tools.genesis.reflexes.oracle_triage as mod
        with patch("tools.llm.router.LLMRouter", MagicMock(return_value=mock_router_instance)), \
             patch("tools.llm.provider.LLMRequest", MagicMock()):
            result = mod._nlp_extract_batch_subjects("orphan_db_table", "description")
        assert result == []


# ---------------------------------------------------------------------------
# _verify_batch — NLP fallback integration
# ---------------------------------------------------------------------------

class TestVerifyBatchNLPFallback:
    """_verify_batch falls back to NLP when standard description parsing yields nothing."""

    def test_nlp_fallback_called_on_nonstandard_description(self):
        task = {
            "title": "[Batch] tool_not_in_manifest: 2 gap findings",
            "description": "Non-standard description without a Subjects: header",
        }
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_batch_subjects",
            return_value=["tools/foo/bar.py"],
        ) as mock_nlp, patch(
            "tools.genesis.reflexes.oracle_triage._verify_tool_not_in_manifest",
            return_value=("promote", "file exists"),
        ):
            from tools.genesis.reflexes.oracle_triage import _verify_batch
            action, reason = _verify_batch(task)
        mock_nlp.assert_called_once()
        assert action == "promote"

    def test_skip_when_both_parsers_return_empty(self):
        task = {
            "title": "[Batch] tool_not_in_manifest: 2 gap findings",
            "description": "No subjects extractable here",
        }
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_batch_subjects",
            return_value=[],
        ):
            from tools.genesis.reflexes.oracle_triage import _verify_batch
            action, reason = _verify_batch(task)
        assert action == "skip"
        assert "could not parse" in reason

    def test_nlp_not_called_when_standard_parsing_succeeds(self):
        """NLP extractor should NOT be invoked when Subjects: block is present."""
        desc = "Subjects:\n- tools/foo/bar.py\n- tools/baz/qux.py\n"
        task = {
            "title": "[Batch] tool_not_in_manifest: 2 gap findings",
            "description": desc,
        }
        with patch(
            "tools.genesis.reflexes.oracle_triage._nlp_extract_batch_subjects",
        ) as mock_nlp, patch(
            "tools.genesis.reflexes.oracle_triage._verify_tool_not_in_manifest",
            return_value=("promote", "file exists"),
        ):
            from tools.genesis.reflexes.oracle_triage import _verify_batch
            _verify_batch(task)
        mock_nlp.assert_not_called()
