"""Tests for tools/govcon/rfi_grounding.py — deterministic anti-hallucination
utilities for the RFI Response Workbench."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.govcon.rfi_grounding import (
    build_ground_truth_block,
    check_numeric_claims,
    find_placeholders,
    substitute_profile_facts,
    validate_references,
)

_PARSED = {
    "rfi_number": "RFI-99-00001",
    "title": "Synthetic Test Solicitation",
    "naics": "541512",
    "due_date": "10 August 2026",
    "questions_due_date": "13 July 2026",
    "document_sections": [
        {"number": 1, "title": "Introduction & Disclaimer"},
        {"number": 2, "title": "Background (Mission Context)"},
        {"number": 3, "title": "Capability Gap & Objectives"},
        {"number": 4, "title": "Request Information"},
        {"number": 5, "title": "Submission Instructions"},
        {"number": 6, "title": "Questions regarding the RFI"},
    ],
    "objectives": [
        {"letter": c, "title": f"Objective {c}"} for c in "ABCDEF"
    ],
    "questionnaire_parts": [
        {"part": "Part 1", "item_number": "1.1", "topic": "Entity Data"},
        {"part": "Part 2", "item_number": "2.7", "topic": "Mission Specific"},
        {"part": "Part 5", "item_number": "5.1", "topic": "Industry Recommendations"},
    ],
    "submission_requirements": {"max_pages": 7, "max_appendix_pages": 2},
}

_PROFILE = {
    "entity_name": "Example Defense Corp",
    "sam_uei": "ABC123DEF456",
    "cage_code": "1XYZ9",
    "primary_naics": "541512",
    "address": "123 Mission Dr",
    "contact_phone": "703-555-0100",
    "contact_email": "bd@example.com",
    "contact_name": "Jane Smith",
}


# ── find_placeholders ─────────────────────────────────────────────────────────

class TestFindPlaceholders:
    def test_finds_uppercase_tokens(self):
        text = "Our UEI is [UEI_NUMBER] and CAGE [CAGE CODE]. Verify [VERIFY]."
        tokens = find_placeholders(text)
        assert "[UEI_NUMBER]" in tokens
        assert "[CAGE CODE]" in tokens
        assert "[VERIFY]" in tokens

    def test_ignores_markdown_links(self):
        assert find_placeholders("See [The RFI](https://sam.gov) for details.") == []

    def test_ignores_lowercase_brackets(self):
        assert find_placeholders("array[index] and [see note]") == []

    def test_empty(self):
        assert find_placeholders("") == []
        assert find_placeholders("No placeholders here.") == []


# ── substitute_profile_facts ──────────────────────────────────────────────────

class TestSubstitution:
    def test_substitutes_identity_facts(self):
        text = "Company [COMPANY_NAME], UEI [UEI_NUMBER], CAGE [CAGE_CODE], NAICS [NAICS]."
        out, subs = substitute_profile_facts(text, _PROFILE, _PARSED)
        assert "Example Defense Corp" in out
        assert "ABC123DEF456" in out
        assert "1XYZ9" in out
        assert "541512" in out
        assert "[" not in out
        assert len(subs) == 4

    def test_substitutes_sam_uei_variant(self):
        out, _ = substitute_profile_facts("[SAM UEI]", _PROFILE, _PARSED)
        assert out == "ABC123DEF456"

    def test_fixes_mangled_rfi_number(self):
        out, subs = substitute_profile_facts(
            "reviewed RFI-99-[TASK_ORDER] thoroughly", _PROFILE, _PARSED)
        assert "RFI-99-00001" in out
        assert "[TASK_ORDER]" not in out

    def test_unknown_tokens_left_alone(self):
        out, subs = substitute_profile_facts("[SOMETHING_ELSE]", _PROFILE, _PARSED)
        assert out == "[SOMETHING_ELSE]"

    def test_missing_profile_value_not_substituted(self):
        out, _ = substitute_profile_facts("[UEI]", {}, {})
        assert out == "[UEI]"


# ── validate_references ───────────────────────────────────────────────────────

class TestValidateReferences:
    def test_valid_references_pass(self):
        text = "Per Section 3 and Objective D, see Item 2.7 in Part 2."
        result = validate_references(text, _PARSED)
        assert result["valid"] is True
        assert result["checked"] >= 4

    def test_roman_numeral_section_flagged(self):
        result = validate_references("As stated in Section IV.B of the RFI", _PARSED)
        assert result["valid"] is False
        assert any("Section IV.B" in r["ref"] for r in result["invalid_refs"])

    def test_nonexistent_section_flagged(self):
        result = validate_references("Section 9 requires this", _PARSED)
        assert result["valid"] is False

    def test_section_3_0_normalizes(self):
        result = validate_references("the objectives in Section 3.0", _PARSED)
        assert result["valid"] is True

    def test_invalid_objective_flagged(self):
        result = validate_references("Objective Z is key", _PARSED)
        assert result["valid"] is False

    def test_invalid_part_flagged(self):
        result = validate_references("as required by Part 9", _PARSED)
        assert result["valid"] is False

    def test_far_part_is_not_an_rfi_part(self):
        result = validate_references(
            "a Commercial Product under FAR Part 12 and DFARS Part 215", _PARSED)
        assert result["valid"] is True

    def test_unparsed_rfi_skips_section_checks(self):
        # No document_sections parsed → section class not validated
        result = validate_references("Section 12 blah", {})
        assert result["valid"] is True

    def test_empty_text(self):
        assert validate_references("", _PARSED)["valid"] is True


# ── build_ground_truth_block ──────────────────────────────────────────────────

class TestGroundTruthBlock:
    def test_contains_structure(self):
        block = build_ground_truth_block(_PARSED)
        assert "RFI GROUND TRUTH" in block
        assert "1 Introduction & Disclaimer" in block
        assert "6 Questions regarding the RFI" in block
        assert "2.7 Mission Specific" in block
        assert "Objectives: A" in block
        assert "7-page response limit" in block
        assert "questions due 13 July 2026" in block
        assert "roman-numeral" in block

    def test_empty_parse_still_has_rules(self):
        block = build_ground_truth_block({})
        assert "Rules:" in block


# ── check_numeric_claims ──────────────────────────────────────────────────────

class TestNumericClaims:
    def test_conflicting_rom_totals_flagged(self):
        sections = [
            {"item_number": "4.2", "content": "| ROM Total | $1,475,000 |"},
            {"item_number": "4.3", "content": "cost share against the ROM total of $2.1M"},
        ]
        conflicts = check_numeric_claims(sections)
        assert any(c["type"] == "rom_total_mismatch" for c in conflicts)

    def test_consistent_rom_totals_pass(self):
        sections = [
            {"item_number": "4.2", "content": "ROM Total: $1,475,000"},
            {"item_number": "4.3", "content": "one third of the ROM total $1,475,000"},
        ]
        assert check_numeric_claims(sections) == []

    def test_conflicting_prototype_months_flagged(self):
        sections = [
            {"item_number": "3.1", "content": "6 months from award to working prototype delivery"},
            {"item_number": "2.4", "content": "prototype delivered 9 months after award"},
        ]
        conflicts = check_numeric_claims(sections)
        assert any(c["type"] == "prototype_timeline_mismatch" for c in conflicts)

    def test_unrelated_numbers_ignored(self):
        sections = [
            {"item_number": "2.6", "content": "NIST SP 800-171 with 12 months of logs and $50 fees"},
            {"item_number": "3.2", "content": "risk window of 3 months for integration"},
        ]
        assert check_numeric_claims(sections) == []

    def test_empty_sections(self):
        assert check_numeric_claims([]) == []
