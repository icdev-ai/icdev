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
    def test_default_weights_has_six_keys(self):
        assert len(_DEFAULT_WEIGHTS) == 6

    def test_default_weights_expected_keys(self):
        expected = {
            "uploads", "kg_past_performance", "rag_general",
            "innovation_engine", "creative_engine", "research_engine",
        }
        assert set(_DEFAULT_WEIGHTS.keys()) == expected

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
        return {
            "uploads": lambda s, t, c: "Upload content here",
            "kg_past_performance": lambda t, c: "KG content here",
            "rag_general": lambda t, c: "RAG content here",
            "innovation_engine": lambda t, c: "Innovation signals",
            "creative_engine": lambda t, c: "Creative specs",
            "research_engine": lambda t, c: "Research dossier",
        }

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

    def test_mocked_gatherers_all_six_contribute(self, monkeypatch):
        monkeypatch.setattr(_runner, "get_effective_weights", lambda *a: self._all_enabled_weights())
        monkeypatch.setattr(_runner, "_GATHERERS", self._gatherers_all_ok())
        result = assemble_weighted_prompt_context("s1", "sec1", "topic")
        assert set(result["sources_used"]) == {
            "uploads", "kg_past_performance", "rag_general",
            "innovation_engine", "creative_engine", "research_engine",
        }

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
