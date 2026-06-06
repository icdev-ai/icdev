# CUI // SP-CTI
"""Unit tests for IQE NL-to-IQE temporal/date-range extraction: tools/iqe/nl_to_iqe.py

Covers the deterministic ``_extract_temporal`` nlp-extractor path (the analog of
date-partitioned log parsing), its precedence inside ``_pattern_translate``, the
field-resolution fallback, and the guarantee that every emitted IQE string parses
under the IQE grammar.
"""
from __future__ import annotations

import pytest

from tools.iqe.nl_to_iqe import (
    _extract_temporal,
    _temporal_field,
    _pattern_translate,
    nl_to_iqe,
)
from tools.iqe.parser import parse

COLLECTIONS = ["logs", "events"]


# ---------------------------------------------------------------------------
# _extract_temporal — single-comparison operator mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, op",
    [
        ("logs created after 2026-01-01", ">"),
        ("events newer than 2026-01-01", ">"),
        ("logs more recent than 2026-01-01", ">"),
        ("logs created before 2026-01-01", "<"),
        ("events older than 2026-01-01", "<"),
        ("logs prior to 2026-01-01", "<"),
        ("logs earlier than 2026-01-01", "<"),
        ("logs since 2026-01-01", ">="),
        ("events as of 2026-01-01", ">="),
        ("logs starting from 2026-01-01", ">="),
        ("logs from 2026-01-01", ">="),
        ("logs until 2026-01-01", "<="),
        ("events up to 2026-01-01", "<="),
        ("logs through 2026-01-01", "<="),
        ("logs by 2026-01-01", "<="),
        ("logs on 2026-01-01", "=="),
        ("events dated 2026-01-01", "=="),
    ],
)
def test_extract_temporal_operators(question: str, op: str) -> None:
    result = _extract_temporal(question, COLLECTIONS)
    assert result is not None
    assert f'{op} "2026-01-01"' in result["iqe"]


def test_extract_temporal_on_or_after_beats_after() -> None:
    # "on or after" must resolve to >=, not be truncated to >.
    result = _extract_temporal("logs on or after 2026-01-01", COLLECTIONS)
    assert '>= "2026-01-01"' in result["iqe"]


def test_extract_temporal_on_or_before_beats_before() -> None:
    result = _extract_temporal("logs on or before 2026-01-01", COLLECTIONS)
    assert '<= "2026-01-01"' in result["iqe"]


# ---------------------------------------------------------------------------
# _extract_temporal — field resolution
# ---------------------------------------------------------------------------


def test_extract_temporal_named_field() -> None:
    result = _extract_temporal("logs created after 2026-01-01", COLLECTIONS)
    assert 'n.created > "2026-01-01"' in result["iqe"]


def test_extract_temporal_default_field_when_unrecognized() -> None:
    # "logs" is a collection noun, not a date field → fall back to timestamp.
    result = _extract_temporal("logs after 2026-01-01", COLLECTIONS)
    assert 'n.timestamp > "2026-01-01"' in result["iqe"]


def test_extract_temporal_updated_field() -> None:
    result = _extract_temporal("records updated since 2026-03-01", COLLECTIONS)
    assert 'n.updated >= "2026-03-01"' in result["iqe"]


# ---------------------------------------------------------------------------
# _extract_temporal — range form
# ---------------------------------------------------------------------------


def test_extract_temporal_between_range() -> None:
    result = _extract_temporal(
        "logs created between 2026-01-01 and 2026-03-01", COLLECTIONS
    )
    assert 'n.created >= "2026-01-01"' in result["iqe"]
    assert 'n.created <= "2026-03-01"' in result["iqe"]
    assert " and " in result["iqe"]


def test_extract_temporal_between_default_field() -> None:
    result = _extract_temporal(
        "events between 2026-01-01 and 2026-02-01", COLLECTIONS
    )
    assert 'n.timestamp >= "2026-01-01"' in result["iqe"]
    assert 'n.timestamp <= "2026-02-01"' in result["iqe"]


# ---------------------------------------------------------------------------
# _extract_temporal — collection match + negatives
# ---------------------------------------------------------------------------


def test_extract_temporal_collection_match() -> None:
    result = _extract_temporal("events after 2026-01-01", COLLECTIONS)
    assert "in events" in result["iqe"]


def test_extract_temporal_collection_default() -> None:
    result = _extract_temporal("created after 2026-01-01", COLLECTIONS)
    assert "in logs" in result["iqe"]


def test_extract_temporal_no_date_returns_none() -> None:
    # relative dates are left to the LLM path
    assert _extract_temporal("logs created in the last 7 days", COLLECTIONS) is None


def test_extract_temporal_no_phrase_returns_none() -> None:
    assert _extract_temporal("show all logs", COLLECTIONS) is None


def test_extract_temporal_empty_collections_falls_back_to_nodes() -> None:
    result = _extract_temporal("after 2026-01-01", [])
    assert "in nodes" in result["iqe"]


# ---------------------------------------------------------------------------
# _temporal_field
# ---------------------------------------------------------------------------


def test_temporal_field_recognized() -> None:
    assert _temporal_field("created") == "created"


def test_temporal_field_unrecognized_defaults() -> None:
    assert _temporal_field("logs") == "timestamp"


def test_temporal_field_none_defaults() -> None:
    assert _temporal_field(None) == "timestamp"


# ---------------------------------------------------------------------------
# _pattern_translate — precedence + regression
# ---------------------------------------------------------------------------


def test_pattern_translate_temporal_takes_precedence() -> None:
    # "show all" pattern would otherwise widen this to select *
    result = _pattern_translate("show all logs after 2026-01-01", COLLECTIONS)
    assert '> "2026-01-01"' in result["iqe"]


def test_pattern_translate_numeric_comparison_unaffected() -> None:
    # temporal extraction must not steal a plain numeric comparison query
    result = _pattern_translate("events with weight over 10", COLLECTIONS)
    assert "where n.weight > 10" in result["iqe"]


def test_pattern_translate_show_all_still_works() -> None:
    result = _pattern_translate("show all logs", COLLECTIONS)
    assert result["iqe"] == "foreach n in logs select *"


# ---------------------------------------------------------------------------
# nl_to_iqe — public surface
# ---------------------------------------------------------------------------


def test_nl_to_iqe_temporal_no_llm() -> None:
    # resolves deterministically — no network/LLM call required
    result = nl_to_iqe("logs created after 2026-01-01", COLLECTIONS)
    assert 'n.created > "2026-01-01"' in result["iqe"]
    assert "explanation" in result


# ---------------------------------------------------------------------------
# Generated IQE must parse under the grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "logs created after 2026-01-01",
        "events before 2026-02-15",
        "logs since 2026-03-01",
        "logs until 2026-12-31",
        "records on 2026-01-10",
        "logs created between 2026-01-01 and 2026-03-01",
    ],
)
def test_generated_temporal_iqe_parses(question: str) -> None:
    result = _extract_temporal(question, COLLECTIONS)
    assert result is not None
    # parse() raises on malformed IQE; a clean return proves the query is valid
    node = parse(result["iqe"])
    assert node is not None
