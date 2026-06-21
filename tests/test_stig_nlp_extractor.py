# CUI // SP-CTI
"""TDD tests for the STIG NLP extractor — Phase 512.

BDD Scenario: Replace regex_user_input patterns with an NLP extractor
(claude-haiku-4-5-20251001) in the STIG compliance pillar.

  Given  the STIG compliance pillar evaluates a repository
  When   STIG V-IDs, documentation references, or CAT markers appear in content
  Then   the NLP extractor correctly identifies them using LLM or regex fallback
  And    the updated pillar check functions produce the same pass/fail results
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Minimal repo directory for pillar checks."""
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture()
def mock_llm_response():
    """Factory that returns a mock LLM response with a given text body."""
    def _make(text: str):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        return resp
    return _make


# ---------------------------------------------------------------------------
# Tests for StigNlpExtractor class
# ---------------------------------------------------------------------------

class TestStigNlpExtractorExists:
    """The NLP extractor module must be importable."""

    def test_module_importable(self):
        from tools.ai_augmentation.agent_readiness.pillars import stig_nlp_extractor  # noqa: F401

    def test_class_exists(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        assert callable(StigNlpExtractor)

    def test_has_extract_stig_markers_method(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor()
        assert hasattr(extractor, "extract_stig_markers")
        assert callable(extractor.extract_stig_markers)


class TestStigNlpExtractorFallback:
    """When LLM is unavailable the extractor must fall back to regex."""

    def test_fallback_detects_vid_when_llm_unavailable(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("# STIG: V-220938 — disable root login")
        assert result["has_vid"] is True

    def test_fallback_detects_sv_rule_when_llm_unavailable(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("SV-220938r853845_rule is tracked here")
        assert result["has_vid"] is True

    def test_fallback_detects_stig_doc_ref(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("See DISA STIG for RHEL 9 compliance.")
        assert result["has_doc_ref"] is True

    def test_fallback_detects_cat_marker(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("CAT I finding must be remediated.")
        assert result["has_cat_marker"] is True

    def test_fallback_returns_false_on_empty_content(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("")
        assert result["has_vid"] is False
        assert result["has_doc_ref"] is False
        assert result["has_cat_marker"] is False

    def test_fallback_returns_false_for_unrelated_content(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("This is a README with no security references.")
        assert result["has_vid"] is False
        assert result["has_doc_ref"] is False
        assert result["has_cat_marker"] is False


class TestStigNlpExtractorReturnShape:
    """extract_stig_markers must always return a consistent dict shape."""

    def test_result_has_required_keys(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("some content")
        assert "has_vid" in result
        assert "has_doc_ref" in result
        assert "has_cat_marker" in result
        assert "method" in result

    def test_fallback_method_label(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor
        extractor = StigNlpExtractor(use_llm=False)
        result = extractor.extract_stig_markers("any text")
        assert result["method"] == "regex_fallback"

    def test_llm_method_label_when_llm_mocked(self, mock_llm_response):
        """When the Anthropic client is mocked the method label is 'nlp_extractor'."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_llm_response(
            '{"has_vid": false, "has_doc_ref": false, "has_cat_marker": false}'
        )
        extractor = StigNlpExtractor(use_llm=True, client=mock_client)
        result = extractor.extract_stig_markers("some content")
        assert result["method"] == "nlp_extractor"

    def test_llm_positive_vid_detection(self, mock_llm_response):
        """LLM path returns has_vid=True when LLM says so."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_llm_response(
            '{"has_vid": true, "has_doc_ref": false, "has_cat_marker": false}'
        )
        extractor = StigNlpExtractor(use_llm=True, client=mock_client)
        result = extractor.extract_stig_markers("# STIG: V-220938")
        assert result["has_vid"] is True
        assert result["method"] == "nlp_extractor"

    def test_llm_failure_falls_back_to_regex(self, mock_llm_response):
        """When LLM raises an exception the extractor falls back to regex."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("LLM unavailable")
        extractor = StigNlpExtractor(use_llm=True, client=mock_client)
        result = extractor.extract_stig_markers("# STIG: V-220938 — root login")
        # Must fall back to regex and still detect the V-ID
        assert result["has_vid"] is True
        assert result["method"] == "regex_fallback"

    def test_llm_malformed_json_falls_back_to_regex(self, mock_llm_response):
        """When LLM returns non-JSON the extractor falls back to regex."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor import StigNlpExtractor

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_llm_response("not valid json")
        extractor = StigNlpExtractor(use_llm=True, client=mock_client)
        result = extractor.extract_stig_markers("V-220938 referenced here")
        assert result["has_vid"] is True
        assert result["method"] == "regex_fallback"


# ---------------------------------------------------------------------------
# Tests for updated stig_compliance pillar check functions
# ---------------------------------------------------------------------------

class TestStigCompliancePillarUsesExtractor:
    """The STIG compliance pillar check functions must use StigNlpExtractor."""

    def test_pillar_module_imports_extractor(self):
        """stig_compliance.py must import StigNlpExtractor."""
        import inspect
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        src = inspect.getsource(mod)
        assert "StigNlpExtractor" in src, (
            "stig_compliance.py must import and use StigNlpExtractor"
        )

    def test_check_stig_vids_in_code_passes_with_vid(self, tmp_repo):
        """_check_stig_vids_in_code passes when V-ID is present."""
        (tmp_repo / "main.py").write_text(
            "# STIG: V-220938 — disable root login\npassword = 'secret'",
            encoding="utf-8",
        )
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import _check_stig_vids_in_code
        result = _check_stig_vids_in_code(tmp_repo)
        assert result.passed is True

    def test_check_stig_vids_in_code_fails_without_vid(self, tmp_repo):
        """_check_stig_vids_in_code fails when no V-ID is present."""
        (tmp_repo / "main.py").write_text(
            "# Normal code without any STIG identifiers",
            encoding="utf-8",
        )
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import _check_stig_vids_in_code
        result = _check_stig_vids_in_code(tmp_repo)
        assert result.passed is False

    def test_check_stig_in_docs_passes_with_disa_ref(self, tmp_repo):
        """_check_stig_in_docs passes when DISA STIG is mentioned in README."""
        (tmp_repo / "README.md").write_text(
            "# Compliance\nThis project follows DISA STIG guidelines.",
            encoding="utf-8",
        )
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import _check_stig_in_docs
        result = _check_stig_in_docs(tmp_repo)
        assert result.passed is True

    def test_check_cat1_remediation_passes_with_cat_and_vid(self, tmp_repo):
        """_check_cat1_remediation passes when CAT I + V-ID co-occur."""
        (tmp_repo / "compliance.py").write_text(
            "# CAT I finding: V-220938 requires immediate remediation",
            encoding="utf-8",
        )
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import _check_cat1_remediation
        result = _check_cat1_remediation(tmp_repo)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Tests for the PILLAR aggregate
# ---------------------------------------------------------------------------

class TestStigCompliancePillarAggregate:
    """PILLAR.run() must remain functional after the NLP refactor."""

    def test_pillar_run_returns_four_results(self, tmp_repo):
        """PILLAR.run() yields exactly 4 CriterionResult objects."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import PILLAR
        results = PILLAR.run(tmp_repo)
        assert len(results) == 4

    def test_pillar_score_shape(self, tmp_repo):
        """PILLAR.score() returns a dict with required keys."""
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import PILLAR
        results = PILLAR.run(tmp_repo)
        score = PILLAR.score(results)
        assert "pillar_id" in score
        assert "passed" in score
        assert "total" in score
        assert "percentage" in score

    def test_pillar_id(self):
        from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import PILLAR
        assert PILLAR.id == "stig-compliance"
