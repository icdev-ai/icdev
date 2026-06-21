# CUI // SP-CTI
"""Unit tests for the DIC style engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.document_intelligence.style_engine import check_style, check_sections


def test_clean_text_passes():
    text = (
        "The Contractor shall deliver technical exploitation reports within 30 days of receipt. "
        "All reports comply with DIA MSIC standards. "
        "The team ensures quality through a structured peer review process."
    )
    result = check_style(text)
    assert result.passed
    assert result.score >= 70


def test_hedge_words_flagged():
    result = check_style("Perhaps the Contractor will maybe try to possibly deliver the report.")
    violation_ids = [v.rule_id for v in result.violations]
    assert "hedge_words" in violation_ids
    assert result.score < 100


def test_utilize_flagged():
    result = check_style("The team will utilize AI tools to facilitate the process.")
    violation_ids = [v.rule_id for v in result.violations]
    assert "weak_verbs" in violation_ids


def test_filler_phrase_flagged():
    result = check_style("In order to complete the task, the Contractor will proceed.")
    violation_ids = [v.rule_id for v in result.violations]
    assert "filler_phrases" in violation_ids


def test_errors_reduce_score_more_than_warnings():
    warn_result = check_style("The Contractor will utilize the system.")
    error_result = check_style("The password must be stored in the hardcoded configuration.")
    assert error_result.score < warn_result.score


def test_undefined_acronym_flagged():
    result = check_style("The AEFC provides fused intelligence products to MSIC on a daily basis.")
    violation_ids = [v.rule_id for v in result.violations]
    assert "acronym_hygiene" in violation_ids


def test_defined_acronym_not_flagged():
    text = (
        "The All-Source Exploitation Fusion Cell (AEFC) provides fused products. "
        "The AEFC operates 24/7."
    )
    result = check_style(text)
    acr_violations = [v for v in result.violations if v.rule_id == "acronym_hygiene" and v.match == "AEFC"]
    assert len(acr_violations) == 0


def test_long_sentence_flagged():
    long_sent = " ".join(["word"] * 65) + "."
    result = check_style(long_sent)
    violation_ids = [v.rule_id for v in result.violations]
    assert "sentence_length" in violation_ids


def test_check_sections_aggregates():
    sections = [
        {"heading": "Overview", "content": "The Contractor shall deliver results."},
        {"heading": "Approach", "content": "Perhaps we will maybe utilize some tools."},
    ]
    result = check_sections(sections)
    assert "overall_score" in result
    assert "sections" in result
    assert len(result["sections"]) == 2
    # Second section should score lower
    assert result["sections"][1]["result"]["score"] < result["sections"][0]["result"]["score"]


def test_empty_content_passes():
    result = check_style("")
    assert result.passed
    assert result.score == 100.0


def test_to_dict_shape():
    result = check_style("The Contractor will utilize the system.")
    d = result.to_dict()
    assert "score" in d
    assert "passed" in d
    assert "violations" in d
    assert "stats" in d
    assert isinstance(d["violations"], list)
