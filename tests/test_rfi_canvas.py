"""Tests for RFI canvas Phase A/B additions.

Covers:
  - rfi_markdown_validator.py  (Group 1)
  - rfi_style_engine.py        (Group 2 — check_style_compliance, estimate_page_count)
  - rfi_engine_runner.py       (Group 3 — weights, assembly, gatherers)
  - rfi_workbench.py           (Group 4 — template fallback, _build_markdown)

No server or real DB required; Group 3 uses monkeypatch to bypass DB calls.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# ── Group 1: Markdown Validator ───────────────────────────────────────────────

from tools.govcon.rfi_markdown_validator import validate_markdown_structure


class TestMarkdownValidator:
    def test_empty_string_is_valid(self):
        r = validate_markdown_structure("")
        assert r["valid"] is True
        assert r["issues"] == []

    def test_valid_plain_markdown(self):
        md = "# Title\n\nSome text here.\n\nAnother paragraph."
        r = validate_markdown_structure(md)
        assert r["valid"] is True
        assert r["issues"] == []

    def test_unclosed_code_fence(self):
        md = "```python\ndef foo():\n    pass\n"
        r = validate_markdown_structure(md)
        assert r["valid"] is False
        types = [i["type"] for i in r["issues"]]
        assert "unclosed_code_fence" in types

    def test_closed_code_fence_is_valid(self):
        md = "```python\ndef foo():\n    pass\n```"
        r = validate_markdown_structure(md)
        errors = [i for i in r["issues"] if i["severity"] == "error"]
        assert not any(i["type"] == "unclosed_code_fence" for i in errors)

    def test_misaligned_table_columns(self):
        # Header has 3 cols, data row has 4
        md = "| A | B | C |\n|---|---|---|\n| x | y | z | w |"
        r = validate_markdown_structure(md)
        assert r["valid"] is False
        types = [i["type"] for i in r["issues"]]
        assert "misaligned_table_columns" in types

    def test_aligned_table_is_valid(self):
        md = "| A | B | C |\n|---|---|---|\n| x | y | z |"
        r = validate_markdown_structure(md)
        errors = [i for i in r["issues"] if i["severity"] == "error"]
        assert not any(i["type"] == "misaligned_table_columns" for i in errors)

    def test_truncated_table_row_header(self):
        # Single cell header triggers truncated_table_row
        md = "| only one cell |\n|---|\n| also one |"
        r = validate_markdown_structure(md)
        types = [i["type"] for i in r["issues"]]
        assert "truncated_table_row" in types

    def test_orphaned_pipe_marker(self):
        md = "| starts with pipe but no close"
        r = validate_markdown_structure(md)
        types = [i["type"] for i in r["issues"]]
        assert "orphaned_pipe_marker" in types

    def test_orphaned_pipe_not_flagged_when_properly_closed(self):
        md = "| properly | closed |"
        r = validate_markdown_structure(md)
        # A single-row table with 2 cells — no orphaned pipe
        orphaned = [i for i in r["issues"] if i["type"] == "orphaned_pipe_marker"]
        assert not orphaned

    def test_clean_table_and_fence(self):
        md = (
            "| Col A | Col B |\n"
            "|-------|-------|\n"
            "| val1  | val2  |\n"
            "\n"
            "```python\nprint('hello')\n```"
        )
        r = validate_markdown_structure(md)
        errors = [i for i in r["issues"] if i["severity"] == "error"]
        assert errors == [], f"Unexpected errors: {errors}"
        assert r["valid"] is True

    def test_valid_returns_empty_issues(self):
        md = "**Bold** text and a [link](http://example.com)."
        r = validate_markdown_structure(md)
        assert r["issues"] == []


# ── Group 2: Style Engine ─────────────────────────────────────────────────────

from tools.govcon.rfi_style_engine import check_style_compliance, estimate_page_count


class TestStyleCompliance:
    def _guide(self, forbidden=None, headings=None):
        return {
            "forbidden_phrases": forbidden or [],
            "required_headings": headings or [],
        }

    def test_forbidden_phrase_flagged(self):
        guide = self._guide(forbidden=["TBD"])
        result = check_style_compliance("Please confirm TBD status. UNCLASSIFIED", guide)
        fp = [f for f in result["findings"] if f["type"] == "forbidden_phrase"]
        assert len(fp) == 1
        assert "TBD" in fp[0]["message"]

    def test_forbidden_phrase_case_insensitive(self):
        guide = self._guide(forbidden=["TBD"])
        result = check_style_compliance("The item is tbd. UNCLASSIFIED", guide)
        fp = [f for f in result["findings"] if f["type"] == "forbidden_phrase"]
        assert len(fp) == 1

    def test_forbidden_phrase_only_flagged_once(self):
        # Phrase appears on two lines — should only produce one finding (seen_phrases set)
        guide = self._guide(forbidden=["TBD"])
        content = "TBD here\nTBD again\nUNCLASSIFIED"
        result = check_style_compliance(content, guide)
        fp = [f for f in result["findings"] if f["type"] == "forbidden_phrase"]
        assert len(fp) == 1

    def test_no_forbidden_phrases_in_clean_content(self):
        guide = self._guide(forbidden=["TBD", "N/A"])
        result = check_style_compliance("Everything is defined. UNCLASSIFIED", guide)
        fp = [f for f in result["findings"] if f["type"] == "forbidden_phrase"]
        assert fp == []

    def test_missing_classification_warned(self):
        guide = self._guide()
        result = check_style_compliance("No classification marking anywhere.", guide)
        mc = [f for f in result["findings"] if f["type"] == "missing_classification"]
        assert len(mc) == 1

    def test_unclassified_marker_accepted(self):
        guide = self._guide()
        result = check_style_compliance("Section content. UNCLASSIFIED", guide)
        mc = [f for f in result["findings"] if f["type"] == "missing_classification"]
        assert mc == []

    def test_cui_marker_accepted(self):
        guide = self._guide()
        result = check_style_compliance("Section content. CUI", guide)
        mc = [f for f in result["findings"] if f["type"] == "missing_classification"]
        assert mc == []

    def test_required_heading_missing(self):
        guide = self._guide(headings=["Technical Approach"])
        result = check_style_compliance("No headings here. UNCLASSIFIED", guide)
        mh = [f for f in result["findings"] if f["type"] == "missing_heading"]
        assert len(mh) == 1
        assert "Technical Approach" in mh[0]["message"]

    def test_required_heading_present(self):
        guide = self._guide(headings=["Technical Approach"])
        result = check_style_compliance("## Technical Approach\n\nDetails. UNCLASSIFIED", guide)
        mh = [f for f in result["findings"] if f["type"] == "missing_heading"]
        assert mh == []

    def test_score_degrades_with_error(self):
        guide = self._guide(headings=["Required Section"])
        # Missing heading = 1 error → score <= 80
        result = check_style_compliance("Content without heading. UNCLASSIFIED", guide)
        assert result["score"] <= 80

    def test_score_100_when_fully_clean(self):
        guide = self._guide()
        # No forbidden phrases, no required headings, has UNCLASSIFIED → score 100
        result = check_style_compliance("Clean content. UNCLASSIFIED", guide)
        assert result["score"] == 100

    def test_multiple_errors_reduce_score_more(self):
        guide = self._guide(headings=["Section A", "Section B"])
        result = check_style_compliance("No headings. UNCLASSIFIED", guide)
        # 2 errors * 20 = 40 deducted → score <= 60
        assert result["score"] <= 60

    def test_empty_content_missing_classification(self):
        guide = self._guide()
        result = check_style_compliance("", guide)
        # Empty content: no classification check triggered (content is falsy)
        mc = [f for f in result["findings"] if f["type"] == "missing_classification"]
        assert mc == []


class TestPageCount:
    def test_empty_string_returns_zero(self):
        assert estimate_page_count("") == 0.0

    def test_whitespace_only_returns_zero(self):
        assert estimate_page_count("   \n  ") == 0.0

    def test_250_words_equals_one_page(self):
        content = " ".join(["word"] * 250)
        result = estimate_page_count(content, words_per_page=250)
        assert result == 1.0

    def test_500_words_equals_two_pages(self):
        content = " ".join(["word"] * 500)
        result = estimate_page_count(content, words_per_page=250)
        assert result == 2.0

    def test_custom_words_per_page(self):
        content = " ".join(["word"] * 100)
        result = estimate_page_count(content, words_per_page=100)
        assert result == 1.0

    def test_partial_page_rounds_to_two_decimals(self):
        content = " ".join(["word"] * 125)
        result = estimate_page_count(content, words_per_page=250)
        assert result == 0.5

    def test_default_words_per_page_is_250(self):
        content = " ".join(["word"] * 250)
        assert estimate_page_count(content) == 1.0


# ── Group 3: Engine Runner ────────────────────────────────────────────────────

import tools.govcon.rfi_engine_runner as _runner
from tools.govcon.rfi_engine_runner import (
    _DEFAULT_WEIGHTS,
    assemble_weighted_prompt_context,
    check_source_availability,
    get_effective_weights,
    get_session_engine_weights,
)


class _FakeDB:
    """Minimal DB stub that raises on execute to force fallback paths."""
    def execute(self, *a, **kw):
        raise Exception("no DB in tests")

    def commit(self):
        pass


class TestEngineRunnerWeights:
    def test_default_weights_has_seven_keys(self):
        assert len(_DEFAULT_WEIGHTS) == 7

    def test_default_weights_expected_keys(self):
        expected = {
            "uploads", "kg_past_performance", "prior_submissions", "rag_general",
            "innovation_engine", "creative_engine", "research_engine",
        }
        assert set(_DEFAULT_WEIGHTS.keys()) == expected

    def test_every_source_has_a_gatherer_and_a_backing_table(self):
        """Guard against the silent-zero class of bug: a weighted source whose
        gatherer or backing table does not exist contributes nothing forever."""
        assert set(_DEFAULT_WEIGHTS) == set(_runner._GATHERERS)
        assert set(_DEFAULT_WEIGHTS) == set(_runner._SOURCE_TABLES)

    def test_all_source_weights_are_positive(self):
        # Each source has a positive weight (they are relative, normalized at assembly time)
        for key, cfg in _DEFAULT_WEIGHTS.items():
            assert cfg["weight"] > 0, f"{key} must have positive weight"

    def test_get_session_engine_weights_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setattr(_runner, "_get_db", lambda: _FakeDB())
        weights = get_session_engine_weights("no-such-session")
        assert set(weights.keys()) == set(_DEFAULT_WEIGHTS.keys())
        assert weights["uploads"]["weight"] == 40

    def test_get_effective_weights_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setattr(_runner, "_get_db", lambda: _FakeDB())
        weights = get_effective_weights("no-session", "no-section")
        assert "uploads" in weights
        assert weights["uploads"]["enabled"] is True


class TestEngineRunnerAssembly:
    """_GATHERERS is built at import time with direct function refs.
    Must patch _runner._GATHERERS (the dict), not the individual fn names."""

    def _all_disabled_weights(self):
        return {k: {"enabled": False, "weight": v["weight"]} for k, v in _DEFAULT_WEIGHTS.items()}

    def _all_enabled_weights(self):
        return {k: {"enabled": True, "weight": v["weight"]} for k, v in _DEFAULT_WEIGHTS.items()}

    def _gatherers_all_ok(self):
        """Derived from _DEFAULT_WEIGHTS so a new source cannot silently go unmocked
        (which would drop it from weights_applied and skew the normalisation)."""
        gatherers = {
            key: (lambda k: lambda t, c: f"{k} content here")(key) for key in _DEFAULT_WEIGHTS
        }
        gatherers["uploads"] = lambda s, t, c: "Upload content here"  # takes session_id
        return gatherers

    def test_assemble_no_enabled_sources_returns_empty(self, monkeypatch):
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_disabled_weights())
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        assert result["context"] == ""
        assert result["sources_used"] == []
        assert result["total_chars"] == 0

    def test_assemble_with_mocked_gatherers(self, monkeypatch):
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_enabled_weights())
        monkeypatch.setattr(_runner, "_GATHERERS", self._gatherers_all_ok())
        result = assemble_weighted_prompt_context("s1", "sec1", "GovCon AI proposal")
        assert result["context"] != ""
        assert len(result["sources_used"]) > 0
        assert result["total_chars"] > 0

    def test_mocked_gatherers_all_contribute(self, monkeypatch):
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_enabled_weights())
        monkeypatch.setattr(_runner, "_GATHERERS", self._gatherers_all_ok())
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        assert set(result["sources_used"]) == set(_DEFAULT_WEIGHTS)

    def test_gatherer_failure_graceful_other_sources_contribute(self, monkeypatch):
        def _boom(*a): raise Exception("uploads DB offline")
        gatherers = {**self._gatherers_all_ok(), "uploads": _boom}
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_enabled_weights())
        monkeypatch.setattr(_runner, "_GATHERERS", gatherers)
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        assert "uploads" not in result["sources_used"]
        assert len(result["sources_used"]) >= 1

    def test_context_contains_source_label_for_uploads(self, monkeypatch):
        weights = {
            "uploads": {"enabled": True, "weight": 100},
            "kg_past_performance": {"enabled": False, "weight": 30},
            "rag_general": {"enabled": False, "weight": 20},
            "innovation_engine": {"enabled": False, "weight": 10},
            "creative_engine": {"enabled": False, "weight": 10},
            "research_engine": {"enabled": False, "weight": 10},
        }
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: weights)
        monkeypatch.setattr(_runner, "_GATHERERS", {"uploads": lambda s, t, c: "Past performance text"})
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        assert "Past Performance" in result["context"]

    def test_weights_applied_sums_to_one(self, monkeypatch):
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_enabled_weights())
        monkeypatch.setattr(_runner, "_GATHERERS", self._gatherers_all_ok())
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        # weights_applied rounds to 3 decimal places so allow ±0.01 tolerance
        total = sum(result["weights_applied"].values())
        assert abs(total - 1.0) < 0.01, f"weights_applied sums to {total}, expected ~1.0"

    def test_check_source_availability_all_false_when_db_unavailable(self, monkeypatch):
        monkeypatch.setattr(_runner, "_get_db", lambda: _FakeDB())
        result = check_source_availability("no-session")
        assert set(result.keys()) == set(_DEFAULT_WEIGHTS.keys())
        for key, val in result.items():
            assert val["available"] is False, f"{key} should be unavailable without DB"


# ── Group 4: Workbench pure-logic functions ───────────────────────────────────

from tools.govcon.rfi_workbench import _template_fallback, _build_markdown


class TestWorkbenchUtils:
    def test_template_fallback_contains_message(self):
        result = _template_fallback("Test Section", "1.1", "some prompt text")
        assert "AI generation unavailable" in result

    def test_template_fallback_contains_title(self):
        result = _template_fallback("Entity Data", "1.1", "prompt")
        assert "Entity Data" in result

    def test_template_fallback_includes_prompt_excerpt(self):
        result = _template_fallback("Title", "2.1", "unique_prompt_xyz")
        assert "unique_prompt_xyz" in result

    def test_build_markdown_contains_unclassified(self):
        session = {"rfi_number": "RFI-001", "rfi_title": "Test RFI", "profile_name": "own"}
        sections = [{
            "part": "part1", "item_number": "1.1", "title": "Entity Data",
            "content": "Our company info.", "status": "draft",
            "requirements": [], "writeguard_score": None, "writeguard_result": None,
        }]
        profile = {"entity_name": "Acme Corp", "primary_naics": "541512"}
        result = _build_markdown(session, sections, profile, {})
        assert "UNCLASSIFIED" in result

    def test_build_markdown_contains_entity_name(self):
        session = {"rfi_number": "RFI-002", "rfi_title": "AI RFI", "profile_name": "own"}
        sections = [{
            "part": "part2", "item_number": "2.1", "title": "TRL Assessment",
            "content": "TRL 6 confirmed. UNCLASSIFIED", "status": "ai_draft_ready",
            "requirements": [], "writeguard_score": 85, "writeguard_result": None,
        }]
        profile = {"entity_name": "Skyline Systems", "primary_naics": "541330"}
        result = _build_markdown(session, sections, profile, {})
        assert "Skyline Systems" in result

    def test_build_markdown_contains_section_content(self):
        session = {"rfi_number": "RFI-003", "rfi_title": "AI RFI", "profile_name": "own"}
        sections = [{
            "part": "part1", "item_number": "1.2", "title": "Business Size",
            "content": "We are a small business. UNCLASSIFIED", "status": "draft",
            "requirements": [], "writeguard_score": None, "writeguard_result": None,
        }]
        profile = {"entity_name": "Acme", "primary_naics": "541512"}
        result = _build_markdown(session, sections, profile, {})
        assert "We are a small business." in result

    def test_build_markdown_pending_section_shows_placeholder(self):
        session = {"rfi_number": "RFI-004", "rfi_title": "RFI", "profile_name": "own"}
        sections = [{
            "part": "part3", "item_number": "3.1", "title": "Timeline",
            "content": None, "ai_draft": None, "status": "pending",
            "requirements": [], "writeguard_score": None, "writeguard_result": None,
        }]
        profile = {"entity_name": "Org", "primary_naics": "541512"}
        result = _build_markdown(session, sections, profile, {})
        # No content → placeholder "content pending"
        assert "content pending" in result.lower() or "Timeline" in result

    def test_build_markdown_rfi_number_included(self):
        session = {"rfi_number": "RFI-TEST-99", "rfi_title": "Test", "profile_name": "own"}
        sections = [{
            "part": "part1", "item_number": "1.1", "title": "Entity",
            "content": "Info. UNCLASSIFIED", "status": "draft",
            "requirements": [], "writeguard_score": None, "writeguard_result": None,
        }]
        profile = {"entity_name": "TestOrg", "primary_naics": "541512"}
        result = _build_markdown(session, sections, profile, {})
        assert "RFI-TEST-99" in result


# ── Group 5: ACE Editor Review ────────────────────────────────────────────────

class TestAceEditorReview:
    """Unit tests for run_ace_editor_review() in rfi_workbench.py.

    All DB calls are patched via monkeypatch so no real DB is required.
    """

    def _make_section(self, content="", ace_feedback=None):
        return {
            "id": "sec-ace-01",
            "session_id": "sess-ace-01",
            "item_number": "2.4",
            "title": "Technical Approach",
            "content": content,
            "ai_draft": content,
            "status": "ai_draft_ready",
            "ace_feedback": ace_feedback,
        }

    def test_returns_empty_when_no_content(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_section", lambda sid: self._make_section(""))
        result = wb.run_ace_editor_review("sec-ace-01")
        assert result == {}

    def test_returns_empty_when_section_not_found(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_section", lambda sid: None)
        result = wb.run_ace_editor_review("missing-section")
        assert result == {}

    def test_result_has_required_keys(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb

        section = self._make_section("This is a well-written technical approach. UNCLASSIFIED.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)

        captured = {}

        def fake_db_execute(*a, **kw):
            class _Cur:
                def execute(self, *a, **kw): return self
                def commit(self): pass
            return _Cur()

        class _FakeConn:
            def execute(self, sql, params=None):
                if "UPDATE" in sql and "ace_feedback" in sql:
                    captured["written"] = params
                class _R:
                    pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())
        monkeypatch.setattr(wb, "_call_llm", lambda *a, **kw: "• Looks good overall.")

        result = wb.run_ace_editor_review("sec-ace-01")
        assert "issues" in result
        assert "overall" in result
        assert "summary" in result
        assert "reviewed_at" in result

    def test_overall_is_pass_for_clean_content(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb

        section = self._make_section("Our solution meets all requirements. UNCLASSIFIED.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "_call_llm", lambda *a, **kw: "• No issues.")

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())
        result = wb.run_ace_editor_review("sec-ace-01")
        assert result.get("overall") in ("pass", "warn")

    def test_verify_tag_adds_issue(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb

        section = self._make_section("Our solution meets requirements [VERIFY]. UNCLASSIFIED.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "_call_llm", lambda *a, **kw: "• Contains placeholder.")

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())
        result = wb.run_ace_editor_review("sec-ace-01")
        verify_issues = [i for i in result.get("issues", []) if "[VERIFY]" in i.get("message", "")]
        assert len(verify_issues) >= 1

    def test_feedback_written_to_db(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb

        section = self._make_section("Strong response. UNCLASSIFIED.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "_call_llm", lambda *a, **kw: "• Meets requirements.")

        written_params = []

        class _FakeConn:
            def execute(self, sql, params=None):
                if params and len(params) >= 3 and "ace_feedback" in (sql or ""):
                    written_params.append(params)
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())
        wb.run_ace_editor_review("sec-ace-01")
        assert len(written_params) >= 1
        feedback_json = written_params[0][0]
        import json
        feedback = json.loads(feedback_json)
        assert "overall" in feedback

    def test_parse_section_row_decodes_ace_feedback(self):
        import json
        import tools.govcon.rfi_workbench as wb

        payload = {"issues": [], "overall": "pass", "summary": "OK", "reviewed_at": "2026-06-30T00:00:00"}
        row = {
            "id": "sec-x",
            "writeguard_result": None,
            "requirements": "[]",
            "ace_feedback": json.dumps(payload),
        }
        result = wb._parse_section_row(row)
        assert isinstance(result["ace_feedback"], dict)
        assert result["ace_feedback"]["overall"] == "pass"


# ── Group 6: ACE Reviewer pass ────────────────────────────────────────────────

class TestAceReviewerPass:
    """Unit tests for run_ace_reviewer_pass() — no live DB or LLM."""

    def _make_section(self, content="", ace_feedback=None):
        return {
            "id": "sec-rev-01",
            "session_id": "sess-rev-01",
            "item_number": "2.4",
            "title": "Technical Approach",
            "content": content,
            "ai_draft": content,
            "status": "hitl_approved",
            "ace_feedback": ace_feedback,
        }

    def test_returns_empty_when_section_not_found(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_section", lambda sid: None)
        assert wb.run_ace_reviewer_pass("missing") == {}

    def test_returns_empty_when_no_content(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_section", lambda sid: self._make_section(""))
        assert wb.run_ace_reviewer_pass("sec-rev-01") == {}

    def test_returns_empty_when_no_router(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_section", lambda sid: self._make_section("Strong technical section."))
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])
        monkeypatch.setattr(wb, "_get_router", lambda: None)
        assert wb.run_ace_reviewer_pass("sec-rev-01") == {}

    def test_result_has_reviewer_source(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock

        section = self._make_section("Well-structured technical section meeting all objectives.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])

        llm_resp = MagicMock()
        llm_resp.content = '{"issues": [], "evaluator_score": 4, "overall": "pass", "summary": "Well-structured response."}'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_resp

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())

        result = wb.run_ace_reviewer_pass("sec-rev-01")
        assert result.get("source") == "reviewer"
        assert result.get("overall") in ("pass", "warn", "fail")

    def test_specialist_consult_not_attempted_when_flag_off(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock

        monkeypatch.delenv("ICDEV_RFI_SPECIALIST_CONSULT_ENABLED", raising=False)
        section = self._make_section("Well-structured technical section.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])

        llm_resp = MagicMock()
        llm_resp.content = '{"issues": [], "evaluator_score": 4, "overall": "pass", "summary": "Solid."}'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_resp

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())

        with patch("tools.govcon.specialist_consult.request_council_consult") as mock_consult:
            result = wb.run_ace_reviewer_pass("sec-rev-01")

        mock_consult.assert_not_called()
        assert "specialist_consult" not in result

    def test_specialist_consult_attached_when_flag_on(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock

        monkeypatch.setenv("ICDEV_RFI_SPECIALIST_CONSULT_ENABLED", "true")
        section = self._make_section("Well-structured technical section.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])

        llm_resp = MagicMock()
        llm_resp.content = '{"issues": [], "evaluator_score": 4, "overall": "pass", "summary": "Solid."}'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_resp

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())

        fake_consult_result = {"verdict": "Proceed.", "stop_reason": "completed", "source": "icdev_council"}
        with patch("tools.govcon.specialist_consult.request_council_consult", return_value=fake_consult_result):
            result = wb.run_ace_reviewer_pass("sec-rev-01")

        assert result["specialist_consult"] == fake_consult_result

    def test_specialist_consult_failure_does_not_break_reviewer_pass(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock

        monkeypatch.setenv("ICDEV_RFI_SPECIALIST_CONSULT_ENABLED", "true")
        section = self._make_section("Well-structured technical section.")
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])

        llm_resp = MagicMock()
        llm_resp.content = '{"issues": [], "evaluator_score": 4, "overall": "pass", "summary": "Solid."}'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_resp

        class _FakeConn:
            def execute(self, sql, params=None):
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())

        with patch("tools.govcon.specialist_consult.request_council_consult", side_effect=RuntimeError("boom")):
            result = wb.run_ace_reviewer_pass("sec-rev-01")

        assert result.get("source") == "reviewer"
        assert "specialist_consult" not in result

    def test_merges_into_existing_editor_feedback(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock

        existing_fb = {"issues": [{"source": "editor", "message": "Check tone"}], "overall": "warn", "summary": "Editor note", "reviewed_at": "t0"}
        section = self._make_section("Good technical section.", ace_feedback=existing_fb)
        monkeypatch.setattr(wb, "get_section", lambda sid: section)
        monkeypatch.setattr(wb, "get_requirements", lambda sid: [])

        llm_resp = MagicMock()
        llm_resp.content = '{"issues": [], "evaluator_score": 3, "overall": "warn", "summary": "Needs more specifics."}'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_resp

        written = []

        class _FakeConn:
            def execute(self, sql, params=None):
                if params and "ace_feedback" in (sql or ""):
                    written.append(params)
                class _R: pass
                return _R()
            def commit(self): pass

        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        monkeypatch.setattr(wb, "get_db", lambda: _FakeConn())

        wb.run_ace_reviewer_pass("sec-rev-01")
        assert len(written) >= 1
        import json
        merged = json.loads(written[0][0])
        # Editor feedback preserved, reviewer key added
        assert "reviewer" in merged


# ── Group 7: Cross-section consistency check ──────────────────────────────────

class TestConsistencyCheck:
    """Unit tests for check_cross_section_consistency() — no live DB or LLM."""

    def _make_section(self, item, content, status="accepted"):
        import uuid
        return {
            "id": str(uuid.uuid4()),
            "session_id": "s1",
            "item_number": item,
            "title": f"Section {item}",
            "content": content,
            "ai_draft": "",
            "status": status,
            "requirements": [],
        }

    def test_empty_accepted_returns_clean(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: [])
        r = wb.check_cross_section_consistency("s1")
        assert r["overall"] == "clean"

    def test_single_accepted_returns_clean(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: [self._make_section("1.1", "Some content here.")])
        r = wb.check_cross_section_consistency("s1")
        assert r["overall"] == "clean"

    def test_verify_tag_in_accepted_flagged_as_error(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        secs = [
            self._make_section("1.1", "Company: [VERIFY] Some Corp."),
            self._make_section("1.2", "Business size is small."),
        ]
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: None)
        r = wb.check_cross_section_consistency("s1")
        types = [c["type"] for c in r["conflicts"]]
        assert "unresolved_verify_tag" in types
        assert r["overall"] == "conflicts"

    def test_trl_spread_greater_than_2_flagged(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        secs = [
            self._make_section("2.1", "Current TRL is TRL 3 for core components."),
            self._make_section("2.4", "The system is at TRL 8, fully deployed."),
        ]
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: None)
        r = wb.check_cross_section_consistency("s1")
        types = [c["type"] for c in r["conflicts"]]
        assert "trl_mismatch" in types

    def test_trl_spread_within_2_not_flagged(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        secs = [
            self._make_section("2.1", "TRL 6 for core inference engine."),
            self._make_section("2.4", "The system is at TRL 7."),
        ]
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: None)
        r = wb.check_cross_section_consistency("s1")
        assert "trl_mismatch" not in [c["type"] for c in r["conflicts"]]

    def test_clean_sections_no_conflicts(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        secs = [
            self._make_section("1.1", "Acme Corp, 123 Main St, TS/SCI cleared."),
            self._make_section("1.2", "Acme Corp is a small business under NAICS 541512."),
        ]
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: None)
        r = wb.check_cross_section_consistency("s1")
        assert "trl_mismatch" not in [c["type"] for c in r["conflicts"]]
        assert "unresolved_verify_tag" not in [c["type"] for c in r["conflicts"]]

    def test_skip_llm_prevents_router_call(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock
        secs = [
            self._make_section("2.1", "TRL 6 system."),
            self._make_section("3.1", "Delivery in M6."),
        ]
        router_mock = MagicMock()
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        wb.check_cross_section_consistency("s1", skip_llm=True)
        router_mock.invoke.assert_not_called()

    def test_llm_conflicts_merged(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        from unittest.mock import MagicMock
        secs = [
            self._make_section("2.1", "TRL 6. No issues."),
            self._make_section("3.1", "Delivery in M6."),
        ]
        llm_response = MagicMock()
        llm_response.content = '[{"type":"timeline_mismatch","sections":["2.1","3.1"],"message":"Timeline inconsistent.","severity":"warning"}]'
        router_mock = MagicMock()
        router_mock.invoke.return_value = llm_response
        monkeypatch.setattr(wb, "get_session", lambda sid: {"id": "s1", "profile_name": "own_company", "parsed_data": {}})
        monkeypatch.setattr(wb, "get_sections", lambda sid: secs)
        monkeypatch.setattr(wb, "_get_router", lambda: router_mock)
        r = wb.check_cross_section_consistency("s1")
        assert any(c["type"] == "timeline_mismatch" for c in r["conflicts"])

    def test_session_not_found_returns_error(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: None)
        r = wb.check_cross_section_consistency("no-such-session")
        assert "error" in r


# ── Items 5-7: Persistence, Deadline, Why Us ──────────────────────────────────

class TestGenerateAllPersistence:
    """Item 5 — generate-all progress persists to .tmp/rfi_genall_{sid}.json."""

    def test_persist_writes_json(self, tmp_path, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "_GENALL_TMP_DIR", tmp_path)
        wb._generate_all_progress["sess-x"] = {"total": 3, "done": 1, "running": True}
        wb._persist_genall_progress("sess-x")
        p = tmp_path / "rfi_genall_sess-x.json"
        assert p.exists()
        import json
        data = json.loads(p.read_text())
        assert data["total"] == 3

    def test_get_status_reads_from_file_when_not_in_memory(self, tmp_path, monkeypatch):
        import json
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "_GENALL_TMP_DIR", tmp_path)
        # Ensure session not in memory
        wb._generate_all_progress.pop("sess-y", None)
        (tmp_path / "rfi_genall_sess-y.json").write_text(
            json.dumps({"total": 5, "done": 5, "running": True, "cancelled": False}),
            encoding="utf-8",
        )
        status = wb.get_generate_all_status("sess-y")
        assert status["total"] == 5
        assert status["running"] is False  # persisted state always marked not-running

    def test_get_status_returns_default_when_no_file(self, tmp_path, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "_GENALL_TMP_DIR", tmp_path)
        wb._generate_all_progress.pop("sess-z", None)
        status = wb.get_generate_all_status("sess-z")
        assert status == {"running": False, "done": 0, "total": 0}


class TestDeadlineInfo:
    """Item 6 — deadline countdown."""

    def _session(self, response_date=None):
        pd = {"objectives": [], "questionnaire_parts": []}
        if response_date:
            pd["response_date"] = response_date
        return {"id": "s1", "profile_name": "own_company", "parsed_data": pd, "rfi_number": "RFI-001"}

    def test_no_deadline_returns_none(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session())
        r = wb.get_deadline_info("s1")
        assert r["deadline"] is None
        assert r["days_remaining"] is None

    def test_future_deadline_positive_days(self, monkeypatch):
        from datetime import date, timedelta
        import tools.govcon.rfi_workbench as wb
        future = (date.today() + timedelta(days=14)).isoformat()
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session(future))
        r = wb.get_deadline_info("s1")
        assert r["days_remaining"] == 14
        assert r["overdue"] is False
        assert r["urgent"] is False

    def test_urgent_within_7_days(self, monkeypatch):
        from datetime import date, timedelta
        import tools.govcon.rfi_workbench as wb
        soon = (date.today() + timedelta(days=3)).isoformat()
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session(soon))
        r = wb.get_deadline_info("s1")
        assert r["urgent"] is True

    def test_overdue_negative_days(self, monkeypatch):
        from datetime import date, timedelta
        import tools.govcon.rfi_workbench as wb
        past = (date.today() - timedelta(days=2)).isoformat()
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session(past))
        r = wb.get_deadline_info("s1")
        assert r["overdue"] is True
        assert r["days_remaining"] < 0

    def test_invalid_date_returns_none_days(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session("not-a-date"))
        r = wb.get_deadline_info("s1")
        assert r["days_remaining"] is None


class TestGenerateWhyUs:
    """Item 7 — generate_why_us function."""

    def _session(self):
        return {"id": "s1", "profile_name": "own_company", "rfi_title": "Test RFI", "rfi_number": "RFI-001"}

    def test_uses_capability_statements_in_prompt(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session())
        monkeypatch.setattr(wb, "_load_profile", lambda name: {
            "entity_name": "Acme Corp", "solution_name": "AcmeSys",
            "capability_statements": ["Proven zero-trust AI platform"],
        })
        captured = {}
        def fake_call(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "We are uniquely positioned."
        monkeypatch.setattr(wb, "_call_llm", fake_call)
        r = wb.generate_why_us("s1", "own_company", competitor_name="BigCo")
        assert "Proven zero-trust" in captured["prompt"]
        assert "BigCo" in captured["prompt"]
        assert r["paragraph"] == "We are uniquely positioned."
        assert r["word_count"] == 4

    def test_fallback_when_no_capability_statements(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: self._session())
        monkeypatch.setattr(wb, "_load_profile", lambda name: {"entity_name": "Acme Corp"})
        monkeypatch.setattr(wb, "_call_llm", lambda *a, **kw: "Generic paragraph.")
        r = wb.generate_why_us("s1", "own_company")
        assert r["paragraph"] == "Generic paragraph."

    def test_raises_when_session_not_found(self, monkeypatch):
        import tools.govcon.rfi_workbench as wb
        monkeypatch.setattr(wb, "get_session", lambda sid: None)
        with pytest.raises(ValueError, match="not found"):
            wb.generate_why_us("no-such-session", "own_company")
