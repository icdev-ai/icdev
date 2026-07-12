"""
Tests for tools/govcon/rfi_response_generator.py

Tests the orchestration logic, profile loading, section drafting,
and end-to-end flow using text fixture files (no real PDF required).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.govcon.rfi_response_generator import (
    _draft_cover,
    _draft_part1,
    _draft_part3,
    _draft_part4,
    _load_capability_scores,
    _load_profile,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_MOCK_PROFILE = {
    "entity_name": "Acme Federal, Inc.",
    "address": "123 Main St, Reston, VA 20190",
    "cage_code": "AAAAAA",
    "sam_uei": "BBBBBBBBBBB1",
    "contact_name": "Jane Doe",
    "contact_title": "VP, Capture",
    "contact_email": "jdoe@acmefederal.com",
    "contact_phone": "703-555-0100",
    "business_size": "Small Business",
    "primary_naics": "541512",
    "socioeconomic_status": [],
    "ndc_status": "Non-Traditional Defense Contractor",
    "ndc_note": "NDC-eligible under 10 U.S.C. 3014.",
    "foci_status": "No FOCI",
    "foci_note": "Privately held by US-based owner.",
    "clearances": [{"level": "TS/SCI", "note": "SCIF-capable"}],
    "boilerplate_cover": "Acme Federal is pleased to respond to {rfi_number}.",
    "ip_posture": {
        "prior_ip": "Government Purpose Rights (GPR)",
        "developed_under_contract": "Unlimited Rights",
        "government_data": "Government owns all data and derivative models",
    },
}

_MOCK_RFI = {
    "rfi_number": "TEST-26-00001",
    "title": "AI/ML Test Orchestration",
    "naics": "541512",
    "poc_name": "Sarah Edwards",
    "poc_email": "sarah@test.mil",
    "due_date": "10 August 2026",
    "questions_due_date": "13 July 2026",
    "objectives": [
        {"id": "obj-a", "letter": "A", "title": "Line-Speed Routing", "description": "Route objects fast."},
        {"id": "obj-b", "letter": "B", "title": "Dynamic Priority", "description": "Inject priorities."},
        {"id": "obj-c", "letter": "C", "title": "Multi-Constraint", "description": "Optimize constraints."},
        {"id": "obj-d", "letter": "D", "title": "Explainability", "description": "Explain decisions."},
        {"id": "obj-e", "letter": "E", "title": "Cloud-Native", "description": "Cloud deployment."},
        {"id": "obj-f", "letter": "F", "title": "Adaptive Learning", "description": "Learn from outcomes."},
    ],
    "questionnaire_parts": [
        {"part": "Part 1", "item_number": "1.1", "topic": "Company Name", "question": "Provide legal entity name."},
    ],
    "submission_requirements": {
        "max_pages": 7,
        "max_appendix_pages": 2,
        "font_size_pt": 11,
        "due_date": "10 August 2026",
    },
}


# ── _load_profile ─────────────────────────────────────────────────────────────

def test_every_shipped_profile_carries_the_required_keys():
    """Company-agnostic by design.

    ICDEV is open source and ships NO employer's identity, so the contract worth
    testing is the SHAPE of a profile, not the name of any one company. Pinning a
    test to a specific entity_name is what made this suite fail the moment that
    company was (correctly) removed from the repo.
    """
    import yaml

    path = Path(__file__).resolve().parents[1] / "args" / "govcon_company_profiles.yaml"
    profiles = (yaml.safe_load(path.read_text(encoding="utf-8")) or {})["profiles"]
    assert profiles, "the registry must ship at least the own_company template"

    for name, profile in profiles.items():
        assert profile.get("entity_name"), f"{name}: entity_name is required"
        assert profile.get("contact_name") or profile.get("contact_email"), \
            f"{name}: a contact is required"
        assert profile.get("clearances"), f"{name}: clearances are required"


def test_load_profile_own_company_exists():
    profile = _load_profile("own_company")
    assert profile is not None
    assert "entity_name" in profile


def test_load_profile_nonexistent_raises():
    with pytest.raises(ValueError, match="Profile"):
        _load_profile("__nonexistent_profile__")


# ── _draft_cover ──────────────────────────────────────────────────────────────

def test_draft_cover_contains_rfi_number():
    cover = _draft_cover(_MOCK_PROFILE, _MOCK_RFI, "2026-08-10")
    assert "TEST-26-00001" in cover


def test_draft_cover_contains_entity_name():
    cover = _draft_cover(_MOCK_PROFILE, _MOCK_RFI, "2026-08-10")
    assert "Acme Federal" in cover


def test_draft_cover_contains_classification():
    cover = _draft_cover(_MOCK_PROFILE, _MOCK_RFI, "2026-08-10")
    assert "UNCLASSIFIED" in cover


def test_draft_cover_contains_fouo():
    cover = _draft_cover(_MOCK_PROFILE, _MOCK_RFI, "2026-08-10")
    assert "FOUO" in cover


def test_draft_cover_contains_title():
    cover = _draft_cover(_MOCK_PROFILE, _MOCK_RFI, "2026-08-10")
    assert "AI/ML Test Orchestration" in cover


# ── _draft_part1 ──────────────────────────────────────────────────────────────

def test_draft_part1_contains_entity():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "Acme Federal" in part1


def test_draft_part1_contains_naics():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "541512" in part1


def test_draft_part1_contains_cage():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "AAAAAA" in part1


def test_draft_part1_contains_clearances():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "TS/SCI" in part1


def test_draft_part1_contains_business_size():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "Small Business" in part1


def test_draft_part1_contains_foci():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    assert "FOCI" in part1


def test_draft_part1_contains_ndc_status():
    part1 = _draft_part1(_MOCK_PROFILE, _MOCK_RFI)
    # NDC or Traditional should appear
    assert "Non-Traditional" in part1 or "Traditional" in part1 or "NDC" in part1


# ── _draft_part3 ──────────────────────────────────────────────────────────────

def test_draft_part3_contains_months():
    part3 = _draft_part3(_MOCK_RFI)
    assert "Month" in part3 or "M1" in part3 or "month" in part3


def test_draft_part3_contains_risk():
    part3 = _draft_part3(_MOCK_RFI)
    assert "risk" in part3.lower() or "Risk" in part3


def test_draft_part3_is_string():
    assert isinstance(_draft_part3(_MOCK_RFI), str)
    assert len(_draft_part3(_MOCK_RFI)) > 100


# ── _draft_part4 ──────────────────────────────────────────────────────────────

def test_draft_part4_contains_rom():
    part4 = _draft_part4(_MOCK_PROFILE, _MOCK_RFI)
    assert "ROM" in part4 or "rom" in part4.lower() or "$" in part4


def test_draft_part4_contains_ip():
    part4 = _draft_part4(_MOCK_PROFILE, _MOCK_RFI)
    assert "Rights" in part4 or "IP" in part4 or "data" in part4.lower()


def test_draft_part4_ndc_traditional():
    mock_trad = dict(_MOCK_PROFILE)
    mock_trad["ndc_status"] = "Traditional Defense Contractor"
    part4 = _draft_part4(mock_trad, _MOCK_RFI)
    assert "Traditional" in part4 or "NDC" in part4 or "teaming" in part4.lower()


def test_draft_part4_ndc_non_traditional():
    mock_ndc = dict(_MOCK_PROFILE)
    mock_ndc["ndc_status"] = "Non-Traditional Defense Contractor"
    part4 = _draft_part4(mock_ndc, _MOCK_RFI)
    assert len(part4) > 50


# ── _load_capability_scores ───────────────────────────────────────────────────

def test_load_capability_scores_returns_list():
    scores = _load_capability_scores(_MOCK_RFI["objectives"])
    assert isinstance(scores, list)


def test_load_capability_scores_has_entries():
    scores = _load_capability_scores(_MOCK_RFI["objectives"])
    assert len(scores) > 0


def test_load_capability_scores_have_grade():
    scores = _load_capability_scores(_MOCK_RFI["objectives"])
    for s in scores:
        assert s.get("coverage_grade") in ("L", "M", "N")


def test_load_capability_scores_have_objective_id():
    scores = _load_capability_scores(_MOCK_RFI["objectives"])
    for s in scores:
        assert "objective_id" in s


# ── End-to-end generate (mocked exports) ─────────────────────────────────────

def test_generate_creates_markdown(tmp_path):
    """generate_response returns a markdown string with all 5 Parts."""
    from tools.govcon.rfi_response_generator import generate_response
    with patch("tools.govcon.rfi_docx_exporter.markdown_to_docx") as mock_export:
        mock_export.return_value = {"status": "ok"}
        result = generate_response(
            rfi=_MOCK_RFI,
            profile_name="own_company",
            output_dir=str(tmp_path),
            submission_date="2026-08-10",
        )
    assert "Part 1" in result["markdown_content"]
    assert "Part 3" in result["markdown_content"]
    assert "Part 4" in result["markdown_content"]


def test_generate_action_required_markers(tmp_path):
    """Parts 2, 5, Appendix scaffold with ACTION REQUIRED markers."""
    from tools.govcon.rfi_response_generator import generate_response
    with patch("tools.govcon.rfi_docx_exporter.markdown_to_docx") as mock_export:
        mock_export.return_value = {"status": "ok"}
        result = generate_response(
            rfi=_MOCK_RFI,
            profile_name="own_company",
            output_dir=str(tmp_path),
            submission_date="2026-08-10",
        )
    assert "ACTION REQUIRED" in result["markdown_content"]
    assert len(result["action_required"]) >= 3


def test_generate_writes_markdown_file(tmp_path):
    from tools.govcon.rfi_response_generator import generate_response
    result = generate_response(
        rfi=_MOCK_RFI,
        profile_name="own_company",
        output_dir=str(tmp_path),
        submission_date="2026-08-10",
        export_docx=False,
    )
    md_path = Path(result["markdown"])
    assert md_path.exists()
    assert md_path.stat().st_size > 500


def test_generate_writes_capability_scores(tmp_path):
    from tools.govcon.rfi_response_generator import generate_response
    result = generate_response(
        rfi=_MOCK_RFI,
        profile_name="own_company",
        output_dir=str(tmp_path),
        submission_date="2026-08-10",
        export_docx=False,
    )
    scores_path = Path(result["capability_scores"])
    assert scores_path.exists()
    data = json.loads(scores_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "capability_scores" in data
