"""
Tests for tools/govcon/rfi_document_parser.py

Exercises text extraction, regex field extraction, objective parsing,
questionnaire parsing, and submission requirement extraction without
requiring a real PDF on disk.
"""

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.govcon.rfi_document_parser import (
    _extract_objectives,
    _extract_questionnaire_parts,
    _extract_submission_requirements,
    _find_first,
    parse_rfi,
)


# ── _find_first ───────────────────────────────────────────────────────────────

def test_find_first_with_group():
    pattern = re.compile(r"NAICS\s*:\s*(\d{6})")
    assert _find_first(pattern, "NAICS: 541512 text") == "541512"


def test_find_first_no_capture_group():
    pattern = re.compile(r"[\w.\-]+@[\w.\-]+\.\w{2,6}")
    assert _find_first(pattern, "Contact: alice@example.mil here") == "alice@example.mil"


def test_find_first_no_match():
    pattern = re.compile(r"NAICS\s*:\s*(\d{6})")
    assert _find_first(pattern, "nothing here") == ""


# ── _extract_objectives ───────────────────────────────────────────────────────

_SAMPLE_OBJECTIVES = """
NSA seeks industry input on the following capability objectives:

A. Line-Speed Routing: The system must route objects at sub-millisecond latency while
processing billions of objects per day without degradation.

B. Dynamic Priority Injection: Mission operators must be able to adjust routing
priorities in real time based on mission context.

C. Multi-Constraint Optimization: The system must balance GPU utilization, cost per
object, and SLO compliance simultaneously.

D. Explainable Routing Logic: Analysts must understand why each object was routed
to a particular processing tier.

E. Cloud-Native Architecture: The system must deploy on cloud infrastructure
supporting streaming data ingest.

F. Adaptive Learning: The system must improve routing decisions over time based on
mission outcome feedback.

NSA seeks responses by 10 August 2026.
"""


def test_extract_objectives_count():
    objs = _extract_objectives(_SAMPLE_OBJECTIVES)
    assert len(objs) == 6


def test_extract_objectives_letters():
    objs = _extract_objectives(_SAMPLE_OBJECTIVES)
    letters = [o["letter"] for o in objs]
    assert letters == ["A", "B", "C", "D", "E", "F"]


def test_extract_objectives_ids():
    objs = _extract_objectives(_SAMPLE_OBJECTIVES)
    assert objs[0]["id"] == "obj-a"
    assert objs[5]["id"] == "obj-f"


def test_extract_objectives_title_stripped():
    objs = _extract_objectives(_SAMPLE_OBJECTIVES)
    assert objs[0]["title"] == "Line-Speed Routing"


def test_extract_objectives_description_not_empty():
    objs = _extract_objectives(_SAMPLE_OBJECTIVES)
    for obj in objs:
        assert len(obj["description"]) > 10


def test_extract_objectives_empty_text():
    assert _extract_objectives("") == []


def test_extract_objectives_no_objectives():
    assert _extract_objectives("This is a paragraph without any capability objectives.") == []


# ── _extract_questionnaire_parts ──────────────────────────────────────────────

_SAMPLE_QUESTIONNAIRE = """
Part 1  Administrative

1.1   Company Name           Provide the legal entity name and address.
1.2   CAGE Code              Provide CAGE code and SAM.gov UEI.

Part 2  Technical Approach

2.1   Architecture           Describe the system architecture for processing orchestration.
2.2   Latency                What are the expected per-object processing latencies by tier?
2.3   Statefulness           Describe how the system manages state across processing nodes.
"""


def test_extract_questionnaire_parts_count():
    parts = _extract_questionnaire_parts(_SAMPLE_QUESTIONNAIRE)
    assert len(parts) >= 3


def test_extract_questionnaire_item_numbers():
    parts = _extract_questionnaire_parts(_SAMPLE_QUESTIONNAIRE)
    item_nums = [p["item_number"] for p in parts]
    assert "1.1" in item_nums or "2.1" in item_nums


def test_extract_questionnaire_parts_have_required_keys():
    parts = _extract_questionnaire_parts(_SAMPLE_QUESTIONNAIRE)
    for p in parts:
        assert "part" in p
        assert "item_number" in p
        assert "topic" in p
        assert "question" in p


def test_extract_questionnaire_parts_empty():
    assert _extract_questionnaire_parts("") == []


# ── _extract_submission_requirements ─────────────────────────────────────────

_SAMPLE_SUBMISSION = """
Responses are limited to 7 pages maximum, using 11-point Times New Roman font,
with 1-inch margins. An optional 2-page technical appendix is permitted.
Questions must be submitted by 13 July 2026.
Submit responses no later than 10 August 2026 via ARC portal.
File naming convention: CompanyName_SolicitationShortName_RFI-99-00001.
"""


def test_extract_submission_max_pages():
    req = _extract_submission_requirements(_SAMPLE_SUBMISSION)
    assert req["max_pages"] == 7


def test_extract_submission_appendix_pages():
    req = _extract_submission_requirements(_SAMPLE_SUBMISSION)
    assert req["max_appendix_pages"] == 2


def test_extract_submission_font_size():
    req = _extract_submission_requirements(_SAMPLE_SUBMISSION)
    assert req["font_size_pt"] == 11


def test_extract_submission_due_date_not_empty():
    req = _extract_submission_requirements(_SAMPLE_SUBMISSION)
    assert req["due_date"] != ""


def test_extract_submission_file_naming():
    req = _extract_submission_requirements(_SAMPLE_SUBMISSION)
    assert "AIOrchestration" in req.get("file_naming", "") or req.get("file_naming", "") != ""


# ── parse_rfi integration ─────────────────────────────────────────────────────

def test_parse_rfi_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_rfi("/nonexistent/path/rfi.pdf")


def test_parse_rfi_txt_file(tmp_path):
    """parse_rfi works on plain-text files (txt suffix = direct read)."""
    rfi_text = _SAMPLE_OBJECTIVES + "\n" + _SAMPLE_QUESTIONNAIRE + "\n" + _SAMPLE_SUBMISSION
    rfi_text = "RFI Number: TEST-26-00001\nNAICS: 541512\n" + rfi_text
    txt_file = tmp_path / "test_rfi.txt"
    txt_file.write_text(rfi_text, encoding="utf-8")

    result = parse_rfi(str(txt_file))

    assert result["rfi_number"] == "TEST-26-00001"
    assert result["naics"] == "541512"
    assert len(result["objectives"]) == 6
    assert result["source"] == "rfi_document"
    assert "parsed_at" in result
    assert result["raw_text_length"] > 0


def test_parse_rfi_returns_uuid(tmp_path):
    rfi_text = "RFI-26-00001\nNAICS: 541512\n" + _SAMPLE_OBJECTIVES
    txt_file = tmp_path / "r.txt"
    txt_file.write_text(rfi_text, encoding="utf-8")
    result = parse_rfi(str(txt_file))
    import uuid
    uuid.UUID(result["id"])  # raises if not valid UUID


def test_parse_rfi_submission_requirements_present(tmp_path):
    rfi_text = "RFI: TEST-26-99\nNAICS: 541512\n" + _SAMPLE_SUBMISSION
    txt_file = tmp_path / "r2.txt"
    txt_file.write_text(rfi_text, encoding="utf-8")
    result = parse_rfi(str(txt_file))
    assert "submission_requirements" in result
    assert isinstance(result["submission_requirements"], dict)
