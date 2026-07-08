"""Tests for tools/quality/content_grounding.py — shared, surface-agnostic
anti-hallucination utilities (extracted from rfi_grounding)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.quality.content_grounding import (
    check_numeric_claims,
    find_placeholders,
    placeholder_findings,
    substitute_facts,
)


class TestSubstituteFacts:
    def test_substitutes_case_insensitively(self):
        out, subs = substitute_facts("[Uei_Number] here", [(r"\[UEI_NUMBER\]", "ABC123")])
        assert out == "ABC123 here"
        assert subs[0]["count"] == 1

    def test_skips_empty_values(self):
        out, subs = substitute_facts("[UEI]", [(r"\[UEI\]", ""), (r"\[UEI\]", None)])
        assert out == "[UEI]"
        assert subs == []

    def test_empty_text(self):
        assert substitute_facts("", [(r"x", "y")]) == ("", [])


class TestPlaceholderFindings:
    def test_reports_by_item_number(self):
        sections = [
            {"item_number": "1.1", "content": "UEI [UEI_NUMBER]"},
            {"item_number": "2.1", "content": "clean"},
            {"item_number": "3.1", "content": "", "ai_draft": "CAGE [CAGE_CODE]"},
        ]
        findings = placeholder_findings(sections)
        assert len(findings) == 2
        assert findings[0] == {"item_number": "1.1", "placeholders": ["[UEI_NUMBER]"]}
        assert findings[1]["item_number"] == "3.1"

    def test_falls_back_to_title_then_id(self):
        findings = placeholder_findings([{"title": "Overview", "content": "[TBD X]"}])
        assert findings[0]["item_number"] == "Overview"

    def test_content_prefers_content_over_draft(self):
        sections = [{"item_number": "1.1", "content": "clean", "ai_draft": "[LEFTOVER]"}]
        assert placeholder_findings(sections) == []

    def test_custom_content_keys(self):
        sections = [{"id": "abc", "draft_content": "[TODO ITEM]"}]
        findings = placeholder_findings(sections, content_keys=("draft_content",))
        assert findings[0]["placeholders"] == ["[TODO ITEM]"]


class TestReExports:
    def test_rfi_grounding_reexports_shared_functions(self):
        from tools.govcon import rfi_grounding
        assert rfi_grounding.find_placeholders is find_placeholders
        assert rfi_grounding.check_numeric_claims is check_numeric_claims
