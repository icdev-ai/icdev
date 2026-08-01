"""
Tests for tools/govcon/solicitation_parser.py

Exercises UCF section splitting, Section L instruction extraction, Section M
evaluation factor extraction, CLIN parsing, and submission requirement
extraction without requiring a real PDF on disk.

Mirrors tests/test_govcon_rfi_parser.py.
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.govcon.solicitation_parser import (
    _extract_basis_of_award,
    _extract_clins_text,
    _extract_document_sections,
    _extract_relative_importance,
    _extract_section_l_instructions,
    _extract_section_m_factors,
    _extract_submission_requirements,
    _extract_volume_structure,
    _split_ucf_sections,
    parse_solicitation,
)


# ── Sample solicitation text blocks ──────────────────────────────────────────

_SAMPLE_SECTIONS = """
TABLE OF CONTENTS
SECTION A - SOLICITATION/CONTRACT FORM ......... 1
SECTION B - SUPPLIES OR SERVICES AND PRICES ......... 3
SECTION L - INSTRUCTIONS TO OFFERORS ......... 45
SECTION M - EVALUATION FACTORS FOR AWARD ......... 52

SECTION A - SOLICITATION/CONTRACT FORM

Solicitation Number: W56KGY-26-R-0021
NAICS Code: 541512
Total Small Business Set-Aside
Point of Contact: Jane Smith
Email: jane.smith@army.mil
Title: AI Orchestration Platform Modernization

SECTION B - SUPPLIES OR SERVICES AND PRICES

0001    AI Platform Development Services FFP 12 MO $100,000.00 $1,200,000.00
0002    Cybersecurity Compliance Support FFP 12 MO $50,000.00 $600,000.00
0003AA  Optional Surge Support T&M 6 MO $25,000.00 $150,000.00

SECTION L - INSTRUCTIONS TO OFFERORS

L.1 General Instructions
Offerors shall submit proposals in three volumes as described below.

L.2 Volume Structure
Volume I - Technical Approach
Volume II - Past Performance
Volume III - Price Proposal

L.3 Formatting
Proposals are limited to 50 pages maximum, using 12-point Times New Roman font,
with 1-inch margins. Submit 2 copies via PIEE portal.
Questions must be submitted by 20 July 2026.
Proposals are due no later than 15 August 2026.

SECTION M - EVALUATION FACTORS FOR AWARD

Award will be made on a best-value tradeoff basis.
Factor 1 - Technical Approach (Weight: 50%)
Subfactor 1.1 - System Architecture
Subfactor 1.2 - AI/ML Methodology
Factor 2 - Past Performance (Weight: 30%)
Factor 3 - Price (Weight: 20%)
Factor 1 is significantly more important than Factor 2.
"""


# ── _extract_document_sections ────────────────────────────────────────────────

def test_extract_document_sections_letters():
    sections = _extract_document_sections(_SAMPLE_SECTIONS)
    letters = [s["letter"] for s in sections]
    assert letters == ["A", "B", "L", "M"]


def test_extract_document_sections_titles_stripped():
    sections = _extract_document_sections(_SAMPLE_SECTIONS)
    by_letter = {s["letter"]: s["title"] for s in sections}
    assert by_letter["L"] == "INSTRUCTIONS TO OFFERORS"
    assert by_letter["M"] == "EVALUATION FACTORS FOR AWARD"


def test_extract_document_sections_empty():
    assert _extract_document_sections("") == []


def test_extract_document_sections_skips_toc_duplicates():
    """Body heading position wins over the TOC entry for each letter."""
    sections = _extract_document_sections(_SAMPLE_SECTIONS)
    assert len(sections) == 4  # not 8 (TOC + body)


# ── _split_ucf_sections ───────────────────────────────────────────────────────

def test_split_ucf_sections_keys():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    assert set(bodies.keys()) == {"A", "B", "L", "M"}


def test_split_ucf_sections_l_contains_instructions():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    assert "L.1 General Instructions" in bodies["L"]
    assert "Factor 1" not in bodies["L"]


def test_split_ucf_sections_empty():
    assert _split_ucf_sections("no headings here") == {}


# ── _extract_section_l_instructions ───────────────────────────────────────────

def test_extract_section_l_instructions_count():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    items = _extract_section_l_instructions(bodies["L"])
    assert len(items) == 3


def test_extract_section_l_instruction_numbers():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    items = _extract_section_l_instructions(bodies["L"])
    numbers = [i["number"] for i in items]
    assert numbers == ["L.1", "L.2", "L.3"]


def test_extract_section_l_instructions_have_required_keys():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    for item in _extract_section_l_instructions(bodies["L"]):
        assert "number" in item
        assert "title" in item
        assert "text" in item


def test_extract_section_l_instructions_body_text():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    items = _extract_section_l_instructions(bodies["L"])
    assert "three volumes" in items[0]["text"]


def test_extract_section_l_instructions_empty():
    assert _extract_section_l_instructions("") == []


# ── _extract_volume_structure ─────────────────────────────────────────────────

def test_extract_volume_structure():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    volumes = _extract_volume_structure(bodies["L"])
    assert len(volumes) == 3
    assert volumes[0] == {"volume": "I", "title": "Technical Approach"}


def test_extract_volume_structure_empty():
    assert _extract_volume_structure("no volumes mentioned") == []


# ── _extract_section_m_factors ────────────────────────────────────────────────

def test_extract_section_m_factors_count():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    factors = _extract_section_m_factors(bodies["M"])
    assert len(factors) == 3


def test_extract_section_m_factor_names():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    factors = _extract_section_m_factors(bodies["M"])
    assert factors[0]["name"].startswith("Technical Approach")
    assert factors[1]["name"].startswith("Past Performance")


def test_extract_section_m_factor_weights():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    factors = _extract_section_m_factors(bodies["M"])
    assert [f["weight_pct"] for f in factors] == [50, 30, 20]


def test_extract_section_m_subfactors():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    factors = _extract_section_m_factors(bodies["M"])
    subs = factors[0]["subfactors"]
    assert len(subs) == 2
    assert subs[0]["number"] == "1.1"
    assert "Architecture" in subs[0]["name"]


def test_extract_section_m_factors_empty():
    assert _extract_section_m_factors("") == []


def test_extract_basis_of_award_best_value():
    assert _extract_basis_of_award("best-value tradeoff basis") == "best_value_tradeoff"


def test_extract_basis_of_award_lpta():
    assert _extract_basis_of_award("lowest price technically acceptable (LPTA)") == "lpta"


def test_extract_basis_of_award_absent():
    assert _extract_basis_of_award("no basis stated") == ""


def test_extract_relative_importance():
    bodies = _split_ucf_sections(_SAMPLE_SECTIONS)
    stmt = _extract_relative_importance(bodies["M"])
    assert "significantly more important" in stmt


# ── _extract_clins_text ───────────────────────────────────────────────────────

def test_extract_clins_count():
    clins = _extract_clins_text(_SAMPLE_SECTIONS)
    assert len(clins) == 3


def test_extract_clin_numbers():
    clins = _extract_clins_text(_SAMPLE_SECTIONS)
    assert [c["clin"] for c in clins] == ["0001", "0002", "0003AA"]


def test_extract_clin_contract_type():
    clins = _extract_clins_text(_SAMPLE_SECTIONS)
    assert clins[0]["contract_type"] == "FFP"
    assert clins[2]["contract_type"] == "T&M"


def test_extract_clin_pricing_fields():
    clins = _extract_clins_text(_SAMPLE_SECTIONS)
    assert clins[0]["quantity"] == 12
    assert clins[0]["unit"] == "MO"
    assert clins[0]["unit_price"] == "$100,000.00"
    assert clins[0]["amount"] == "$1,200,000.00"


def test_extract_clins_have_required_keys():
    for c in _extract_clins_text(_SAMPLE_SECTIONS):
        for key in ("clin", "description", "contract_type", "quantity", "unit", "unit_price", "amount"):
            assert key in c


def test_extract_clins_empty():
    assert _extract_clins_text("") == []


# ── _extract_submission_requirements ─────────────────────────────────────────

def test_extract_submission_max_pages():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["max_pages"] == 50


def test_extract_submission_font_size():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["font_size_pt"] == 12


def test_extract_submission_margins():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["margins_in"] == 1.0


def test_extract_submission_copies():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["copies"] == 2


def test_extract_submission_portal():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert "PIEE" in req["submission_portal"]


def test_extract_submission_due_date():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["due_date"] == "15 August 2026"


def test_extract_submission_questions_due_date():
    req = _extract_submission_requirements(_SAMPLE_SECTIONS)
    assert req["questions_due_date"] == "20 July 2026"


# ── parse_solicitation integration ────────────────────────────────────────────

def test_parse_solicitation_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_solicitation("/nonexistent/path/solicitation.pdf")


def test_parse_solicitation_txt_file(tmp_path):
    """parse_solicitation works on plain-text files (txt suffix = direct read)."""
    txt_file = tmp_path / "test_solicitation.txt"
    txt_file.write_text(_SAMPLE_SECTIONS, encoding="utf-8")

    result = parse_solicitation(str(txt_file))

    assert result["solicitation_number"] == "W56KGY-26-R-0021"
    assert result["naics"] == "541512"
    assert result["set_aside"] == "Total Small Business Set-Aside"
    assert result["title"] == "AI Orchestration Platform Modernization"
    assert result["poc_email"] == "jane.smith@army.mil"
    assert result["source"] == "solicitation_document"
    assert len(result["document_sections"]) == 4
    assert len(result["section_l_instructions"]) == 3
    assert len(result["section_m_factors"]) == 3
    assert len(result["clins"]) == 3
    assert result["basis_of_award"] == "best_value_tradeoff"
    assert "parsed_at" in result
    assert result["raw_text_length"] > 0


def test_parse_solicitation_returns_uuid(tmp_path):
    txt_file = tmp_path / "s.txt"
    txt_file.write_text(_SAMPLE_SECTIONS, encoding="utf-8")
    result = parse_solicitation(str(txt_file))
    import uuid
    uuid.UUID(result["id"])  # raises if not valid UUID


def test_parse_solicitation_no_ucf_headings_fallback(tmp_path):
    """Combined-synopsis docs without SECTION headings still yield L/M items
    scanned over the full text."""
    text = (
        "Solicitation: FA8750-26-R-1001\n"
        "L.1 Instructions\nSubmit one volume.\n"
        "Factor 1 - Technical (Weight: 60%)\n"
        "Factor 2 - Price (Weight: 40%)\n"
    )
    txt_file = tmp_path / "combined.txt"
    txt_file.write_text(text, encoding="utf-8")
    result = parse_solicitation(str(txt_file))
    assert result["document_sections"] == []
    assert len(result["section_l_instructions"]) == 1
    assert len(result["section_m_factors"]) == 2


def test_parse_solicitation_submission_requirements_present(tmp_path):
    txt_file = tmp_path / "s2.txt"
    txt_file.write_text(_SAMPLE_SECTIONS, encoding="utf-8")
    result = parse_solicitation(str(txt_file))
    assert "submission_requirements" in result
    assert isinstance(result["submission_requirements"], dict)
