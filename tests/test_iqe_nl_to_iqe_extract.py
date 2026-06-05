# CUI // SP-CTI
"""Unit tests for IQE NL-to-IQE comparison extraction: tools/iqe/nl_to_iqe.py

Covers the deterministic ``_extract_comparison`` nlp-extractor path added to the
regex translator, the regression-safe ``_pattern_translate`` ordering, and the
guarantee that every emitted IQE string parses under the IQE grammar.
"""
from __future__ import annotations

import pytest

from tools.iqe.nl_to_iqe import (
    _extract_comparison,
    _match_collection,
    _pattern_translate,
    nl_to_iqe,
)
from tools.iqe.parser import parse

COLLECTIONS = ["nodes", "edges"]


# ---------------------------------------------------------------------------
# _extract_comparison — operator mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, op, val",
    [
        ("show edges with weight greater than 10", ">", "10"),
        ("nodes with degree more than 4", ">", "4"),
        ("edges with weight over 7", ">", "7"),
        ("nodes with degree at least 3", ">=", "3"),
        ("nodes with degree no less than 3", ">=", "3"),
        ("edges with weight less than 5", "<", "5"),
        ("nodes with degree under 2", "<", "2"),
        ("edges with weight at most 9", "<=", "9"),
        ("nodes with score equal to 7", "==", "7"),
        ("nodes with score not equal to 7", "!=", "7"),
    ],
)
def test_extract_comparison_operators(question: str, op: str, val: str) -> None:
    result = _extract_comparison(question, COLLECTIONS)
    assert result is not None
    assert f"{op} {val}" in result["iqe"]


def test_extract_comparison_picks_field() -> None:
    result = _extract_comparison("edges with weight greater than 10", COLLECTIONS)
    assert "n.weight >" in result["iqe"]


def test_extract_comparison_decimal_preserved() -> None:
    result = _extract_comparison("nodes with weight greater than 2.5", COLLECTIONS)
    assert "> 2.5" in result["iqe"]


def test_extract_comparison_integer_normalized() -> None:
    # leading zero / float-free integers are emitted as plain ints
    result = _extract_comparison("nodes with degree at least 03", COLLECTIONS)
    assert ">= 3" in result["iqe"]


def test_extract_comparison_ge_beats_gt() -> None:
    # "greater than or equal to" must not be truncated to ">"
    result = _extract_comparison(
        "nodes with degree greater than or equal to 5", COLLECTIONS
    )
    assert ">= 5" in result["iqe"]
    assert "> 5" not in result["iqe"].replace(">= 5", "")


def test_extract_comparison_collection_match() -> None:
    result = _extract_comparison("edges with weight over 10", COLLECTIONS)
    assert "in edges" in result["iqe"]


def test_extract_comparison_collection_default() -> None:
    # no collection noun present → first collection is the default
    result = _extract_comparison("weight over 10", COLLECTIONS)
    assert "in nodes" in result["iqe"]


def test_extract_comparison_no_number_returns_none() -> None:
    assert _extract_comparison("show all switches", COLLECTIONS) is None


def test_extract_comparison_inverted_form_left_to_llm() -> None:
    # "<phrase> <number> <field>" is ambiguous: the token before the phrase is
    # the filler "with" (a stopword), so no predicate is emitted and the query
    # falls through to the LLM translation path.
    assert _extract_comparison("show nodes with more than 5 connections", COLLECTIONS) is None


def test_extract_comparison_empty_collections_falls_back_to_nodes() -> None:
    result = _extract_comparison("weight over 3", [])
    assert "in nodes" in result["iqe"]


# ---------------------------------------------------------------------------
# _match_collection
# ---------------------------------------------------------------------------


def test_match_collection_singularized() -> None:
    assert _match_collection("show edges", COLLECTIONS, "nodes") == "edges"


def test_match_collection_default_when_absent() -> None:
    assert _match_collection("show widgets", COLLECTIONS, "nodes") == "nodes"


# ---------------------------------------------------------------------------
# _pattern_translate — precedence + regression
# ---------------------------------------------------------------------------


def test_pattern_translate_comparison_takes_precedence() -> None:
    # "show all" pattern would otherwise widen this to select *
    result = _pattern_translate("show all edges with weight over 10", COLLECTIONS)
    assert "where n.weight > 10" in result["iqe"]


def test_pattern_translate_show_all_still_works() -> None:
    result = _pattern_translate("show all nodes", COLLECTIONS)
    assert result["iqe"] == "foreach n in nodes select *"


def test_pattern_translate_type_filter_still_works() -> None:
    result = _pattern_translate("nodes of type router", COLLECTIONS)
    assert 'where n.type == "router"' in result["iqe"]


def test_pattern_translate_count_still_works() -> None:
    result = _pattern_translate("how many edges", COLLECTIONS)
    assert result["iqe"].startswith("foreach n in edges")


# ---------------------------------------------------------------------------
# nl_to_iqe — public surface
# ---------------------------------------------------------------------------


def test_nl_to_iqe_comparison_no_llm() -> None:
    # comparison resolves deterministically — no network/LLM call required
    result = nl_to_iqe("edges with weight greater than 10", COLLECTIONS)
    assert "where n.weight > 10" in result["iqe"]
    assert "explanation" in result


def test_nl_to_iqe_empty_question_fallback() -> None:
    result = nl_to_iqe("", COLLECTIONS)
    assert result["iqe"] == "foreach n in nodes select *"


# ---------------------------------------------------------------------------
# Generated IQE must parse under the grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "edges with weight greater than 10",
        "nodes with degree at least 3",
        "edges with weight less than 5",
        "nodes with score equal to 7",
        "nodes with weight greater than 2.5",
    ],
)
def test_generated_comparison_iqe_parses(question: str) -> None:
    result = _extract_comparison(question, COLLECTIONS)
    assert result is not None
    # parse() raises on malformed IQE; a clean return proves the query is valid
    node = parse(result["iqe"])
    assert node is not None
