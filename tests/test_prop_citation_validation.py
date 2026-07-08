# CUI // SP-CTI
"""Tests for ground-prop-02 — proposal citation validation + ground-truth prompt block.

Covers:
  - tools/govcon/solicitation_grounding.py: build_ground_truth_block +
    validate_references against the solicitation_parser.py output structure
  - tools/govcon/response_drafter.py::_build_draft_prompt ground-truth injection
  - tools/govcon/run_writeguard_on_drafts.py::_citation_finding mapping to the
    invalid_citation finding type

All functions under test are pure (no LLM, no DB).
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.govcon.solicitation_grounding import (
    build_ground_truth_block,
    validate_references,
)


def _parsed_fixture() -> dict:
    """Structured dict mirroring solicitation_parser.parse_solicitation output."""
    return {
        "solicitation_number": "W9133L-26-R-0042",
        "title": "Enterprise DevSecOps Platform Modernization",
        "document_sections": [
            {"letter": "A", "title": "Solicitation/Contract Form"},
            {"letter": "B", "title": "Supplies or Services and Prices/Costs"},
            {"letter": "C", "title": "Statement of Work"},
            {"letter": "L", "title": "Instructions, Conditions, and Notices"},
            {"letter": "M", "title": "Evaluation Factors for Award"},
        ],
        "section_l_instructions": [
            {"number": "L.1", "title": "General Instructions", "text": ""},
            {"number": "L.4", "title": "Volume Organization", "text": ""},
            {"number": "L.4.2", "title": "Technical Volume Content", "text": ""},
        ],
        "volume_structure": [
            {"volume": "I", "title": "Technical Approach"},
            {"volume": "II", "title": "Past Performance"},
            {"volume": "III", "title": "Price"},
        ],
        "section_m_factors": [
            {
                "factor": "1",
                "name": "Technical Approach",
                "weight_pct": 40,
                "subfactors": [
                    {"number": "1.1", "name": "DevSecOps Pipeline"},
                    {"number": "1.2", "name": "Compliance Automation"},
                ],
            },
            {"factor": "2", "name": "Past Performance", "weight_pct": 30, "subfactors": []},
            {"factor": "3", "name": "Price", "weight_pct": 30, "subfactors": []},
        ],
        "basis_of_award": "best_value_tradeoff",
        "relative_importance": "Factor 1 is significantly more important than Factor 2",
        "clins": [
            {"clin": "0001", "description": "DevSecOps Platform Implementation"},
            {"clin": "0002", "description": "Sustainment Support"},
        ],
        "submission_requirements": {
            "max_pages": 50,
            "due_date": "15 August 2026",
            "submission_portal": "SAM.gov",
        },
        "due_date": "15 August 2026",
    }


# ── build_ground_truth_block ──────────────────────────────────────────────────


class TestBuildGroundTruthBlock:
    def test_header_and_rules(self):
        block = build_ground_truth_block(_parsed_fixture())
        assert block.startswith("[SOLICITATION GROUND TRUTH")
        assert "W9133L-26-R-0042" in block
        assert "Rules:" in block
        assert "Never invent section numbers" in block

    def test_lists_all_citable_structure(self):
        block = build_ground_truth_block(_parsed_fixture())
        assert "C Statement of Work" in block
        assert "L.4.2 Technical Volume Content" in block
        assert "Volume II Past Performance" in block
        assert "Factor 1 Technical Approach (weight 40%)" in block
        assert "1.2 Compliance Automation" in block
        assert "0001 DevSecOps Platform Implementation" in block
        assert "Best Value Tradeoff" in block
        assert "50-page limit" in block

    def test_empty_parse_omits_structure_lines(self):
        block = build_ground_truth_block({})
        assert "Document sections:" not in block
        assert "Evaluation factors:" not in block
        assert "CLINs:" not in block
        assert "Rules:" in block  # rules always present

    def test_none_parse_is_safe(self):
        assert "Rules:" in build_ground_truth_block(None)


# ── validate_references ───────────────────────────────────────────────────────


class TestValidateReferences:
    def test_valid_references_pass(self):
        text = (
            "Per Section L.4.2, Volume I addresses Factor 1 (Subfactor 1.2). "
            "Pricing for CLIN 0001 follows Section B, and the SOW in Section C."
        )
        result = validate_references(text, _parsed_fixture())
        assert result["valid"] is True
        assert result["invalid_refs"] == []
        assert result["checked"] >= 7

    def test_invalid_section_letter(self):
        result = validate_references("As required by Section K...", _parsed_fixture())
        assert result["valid"] is False
        assert any(r["ref"] == "Section K" for r in result["invalid_refs"])

    def test_invalid_l_instruction(self):
        result = validate_references("Per Section L.9, we submit...", _parsed_fixture())
        assert any(r["ref"] == "Section L.9" for r in result["invalid_refs"])

    def test_roman_numeral_section_always_invalid(self):
        result = validate_references("Per Section IV of the RFP...", _parsed_fixture())
        assert any(
            r["ref"] == "Section IV" and "roman-numeral" in r["reason"]
            for r in result["invalid_refs"]
        )
        # Even with an empty parse, multi-letter roman sections are flagged.
        result_empty = validate_references("See Section III.", {})
        assert result_empty["valid"] is False

    def test_invalid_factor_and_subfactor(self):
        result = validate_references(
            "Our response to Factor 5 and Subfactor 2.9 demonstrates...",
            _parsed_fixture(),
        )
        refs = {r["ref"] for r in result["invalid_refs"]}
        assert "Factor 5" in refs
        assert "Subfactor 2.9" in refs

    def test_invalid_volume(self):
        result = validate_references("Volume V contains our approach.", _parsed_fixture())
        assert any(r["ref"] == "Volume V" for r in result["invalid_refs"])

    def test_volume_arabic_roman_equivalence(self):
        # Parsed volumes are roman (I, II, III); "Volume 2" cites Volume II.
        result = validate_references("Volume 2 covers past performance.", _parsed_fixture())
        assert result["valid"] is True

    def test_invalid_clin(self):
        result = validate_references("Costs are captured under CLIN 0009.", _parsed_fixture())
        assert any(r["ref"] == "CLIN 0009" for r in result["invalid_refs"])

    def test_unparsed_classes_not_checked(self):
        # A parse with no factors/volumes/CLINs must not flag those refs.
        parsed = {"document_sections": [{"letter": "C", "title": "SOW"}]}
        result = validate_references(
            "Factor 9, Volume X, and CLIN 9999 per Section C.", parsed
        )
        refs = {r["ref"] for r in result["invalid_refs"]}
        assert "Factor 9" not in refs
        assert "Volume X" not in refs
        assert "CLIN 9999" not in refs
        assert result["valid"] is True

    def test_empty_text(self):
        result = validate_references("", _parsed_fixture())
        assert result == {"valid": True, "invalid_refs": [], "checked": 0}


# ── response_drafter prompt injection ─────────────────────────────────────────


class TestDraftPromptInjection:
    def test_ground_truth_block_injected(self):
        from tools.govcon.response_drafter import _build_draft_prompt

        block = build_ground_truth_block(_parsed_fixture())
        prompt = _build_draft_prompt(
            "The contractor shall provide DevSecOps.", "- cap", "- kb", "devsecops", "", block
        )
        assert prompt.startswith("[SOLICITATION GROUND TRUTH")
        assert "Cite ONLY the sections, instructions, volumes, factors, and CLINs" in prompt
        assert "REQUIREMENT: The contractor shall provide DevSecOps." in prompt

    def test_no_block_no_grounding_rule(self):
        from tools.govcon.response_drafter import _build_draft_prompt

        prompt = _build_draft_prompt(
            "The contractor shall provide DevSecOps.", "- cap", "- kb", "devsecops", "", ""
        )
        assert "SOLICITATION GROUND TRUTH" not in prompt
        assert prompt.startswith("Draft a concise proposal response")


# ── WriteGuard invalid_citation finding ───────────────────────────────────────


class TestCitationFinding:
    def test_no_invalid_refs_returns_none(self):
        from tools.govcon.run_writeguard_on_drafts import _citation_finding

        assert _citation_finding({}) is None
        assert _citation_finding({"invalid_references": []}) is None

    def test_minor_below_three(self):
        from tools.govcon.run_writeguard_on_drafts import _citation_finding

        meta = {"invalid_references": [{"ref": "Section K", "reason": "not in solicitation"}]}
        severity, desc, rec = _citation_finding(meta)
        assert severity == "minor"
        assert "invalid_citation" in desc
        assert "Section K" in desc
        assert rec

    def test_major_at_three_or_more(self):
        from tools.govcon.run_writeguard_on_drafts import _citation_finding

        meta = {
            "invalid_references": [
                {"ref": "Section K", "reason": "x"},
                {"ref": "Factor 5", "reason": "x"},
                {"ref": "CLIN 0009", "reason": "x"},
            ]
        }
        severity, desc, _ = _citation_finding(meta)
        assert severity == "major"
        assert "3 reference(s)" in desc

    def test_finding_type_in_sqlite_ddl(self):
        # Entity-type rule: the new finding type must exist in the SQL CHECK.
        ddl = (Path(_ROOT) / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
        assert "'invalid_citation'" in ddl

    def test_finding_type_in_pg_schema(self):
        sql = (Path(_ROOT) / "tools" / "db" / "schema" / "pg_consolidated.sql").read_text(
            encoding="utf-8"
        )
        assert "'invalid_citation'::text" in sql
