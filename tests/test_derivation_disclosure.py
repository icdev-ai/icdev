#!/usr/bin/env python3
"""Derived content must not look like quoted content. CUI // SP-CTI.

A cited answer presents three different things identically today: a quotation,
a paraphrase, and a number the model computed that appears in no source. The
third passes citation validation — the cited chunk exists — while asserting a
figure that is not on the page.

These tests pin the three-way classification and the formula recovery that
makes a computed figure auditable.
"""
from __future__ import annotations

import pytest

from tools.quality.derivation import (
    DERIV_NUMERIC,
    DERIV_TEXT,
    DERIV_VERBATIM,
    classify_claim,
    derive_formula,
    disclose_derivations,
    extract_numbers,
)


# --------------------------------------------------------------------------- #
# Numeric extraction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text,expected", [
    ("The total is 45.", 45.0),
    ("Obligated $1,250,000 to date.", 1250000.0),
    ("Roughly 4.2 million dollars.", 4200000.0),
    ("A 12.5% share.", 12.5),
    ("Reduced by -30 units.", -30.0),
])
def test_extract_numbers_normalizes_form(text, expected):
    """Scale words and separators must normalize, or every unit-differing
    restatement misreads as an unexplained computed figure."""
    vals = [v for v, _lit in extract_numbers(text)]
    assert expected in vals


def test_scale_word_and_digits_compare_equal():
    a = [v for v, _ in extract_numbers("$4.2 million")]
    b = [v for v, _ in extract_numbers("4,200,000")]
    assert a == b


def test_version_like_tokens_do_not_become_operands():
    """`v1.2.3` and identifiers must not seed the formula search with noise."""
    assert extract_numbers("See section abc123 of v1.2.3") == [] or all(
        lit.strip() not in {"abc123", "v1.2.3"} for _v, lit in
        extract_numbers("See section abc123 of v1.2.3")
    )


# --------------------------------------------------------------------------- #
# Verbatim
# --------------------------------------------------------------------------- #


def test_exact_quotation_is_verbatim():
    src = {"1": "The contractor shall retain all records for seven years."}
    d = classify_claim("The contractor shall retain all records for seven years.", src)
    assert d.kind == DERIV_VERBATIM
    assert not d.is_derived


def test_verbatim_ignores_whitespace_and_case():
    src = {"1": "The   Contractor  shall\nretain all records."}
    d = classify_claim("the contractor shall retain all records.", src)
    assert d.kind == DERIV_VERBATIM


def test_verbatim_wins_even_when_it_contains_numbers():
    """A figure lifted word-for-word off the page needs no derivation notice."""
    src = {"1": "Total obligated value is $4,150,000 as of March."}
    d = classify_claim("Total obligated value is $4,150,000 as of March.", src)
    assert d.kind == DERIV_VERBATIM


def test_verbatim_survives_a_trailing_citation_marker():
    src = {"1": "Payment is due within 30 days."}
    d = classify_claim("Payment is due within 30 days. [source: 1]", src)
    assert d.kind == DERIV_VERBATIM


# --------------------------------------------------------------------------- #
# Derived text
# --------------------------------------------------------------------------- #


def test_paraphrase_is_derived_text():
    src = {"1": "The contractor shall retain all records for seven years."}
    d = classify_claim("Records must be kept by the contractor for seven years.", src)
    assert d.kind == DERIV_TEXT
    assert d.is_derived


def test_derived_text_carries_the_span_it_came_from():
    """The disclosure is only useful if it shows what was restated."""
    src = {"1": "The contractor shall retain all records for seven years."}
    d = classify_claim("Records must be kept for seven years by the contractor.", src)
    assert d.quote, "derived-text must expose its supporting span"
    assert d.source_ids == ["1"]


def test_describe_distinguishes_the_classes():
    src = {"1": "The contractor shall retain all records for seven years."}
    quoted = classify_claim("The contractor shall retain all records for seven years.", src)
    para = classify_claim("Records are kept for seven years.", src)
    assert quoted.describe() != para.describe()
    assert "uoted" in quoted.describe()


# --------------------------------------------------------------------------- #
# Derived numeric — the case this module exists for
# --------------------------------------------------------------------------- #


def test_computed_sum_is_disclosed_with_its_formula():
    src = {"1": "Phase A obligated 20. Phase B obligated 25."}
    d = classify_claim("Total obligation across phases is 45.", src)
    assert d.kind == DERIV_NUMERIC
    assert d.formula, "a recoverable sum must be reported"
    assert d.value == 45.0
    assert len(d.operands) == 2


def test_operands_name_their_source():
    """'Show each operand's source' is the explicit requirement."""
    src = {"a": "Phase A obligated 20.", "b": "Phase B obligated 25."}
    d = classify_claim("Total is 45.", src)
    assert d.kind == DERIV_NUMERIC
    assert {o.source_id for o in d.operands} == {"a", "b"}


def test_percentage_derivation_is_recovered():
    src = {"1": "25 of the 200 controls were inherited."}
    d = classify_claim("That is 12.5% of controls.", src)
    assert d.kind == DERIV_NUMERIC
    assert d.formula


def test_difference_derivation_is_recovered():
    src = {"1": "Ceiling is 900. Obligated to date is 350."}
    d = classify_claim("Remaining capacity is 550.", src)
    assert d.kind == DERIV_NUMERIC
    assert d.formula


def test_rounded_result_still_matches():
    """A correctly-rounded figure must not read as unexplained."""
    src = {"1": "Values are 10 and 3."}
    d = classify_claim("The ratio is 3.33.", src)
    assert d.kind == DERIV_NUMERIC
    assert d.formula, "rounded quotient should still recover its formula"


def test_fabricated_number_is_flagged_with_no_formula():
    """The adversarial case: a well-cited number grounded in nothing.

    This is what an invented figure looks like, and it is precisely what
    citation validation cannot catch — the cited chunk exists, so the citation
    is 'valid'.
    """
    src = {"1": "The contractor shall retain records for seven years."}
    d = classify_claim("The contract is valued at $8,412,900. [source: 1]", src)
    assert d.kind == DERIV_NUMERIC
    assert not d.formula, "no derivation should be recoverable"
    assert d.unexplained
    assert "no derivation" in d.describe()


def test_absence_of_a_formula_is_not_rendered_as_fine():
    src = {"1": "Records retained seven years."}
    d = classify_claim("Value is 8412900.", src)
    assert d.to_dict()["formula"] == ""
    assert d.to_dict()["description"] != "Quoted verbatim from the cited source."


# --------------------------------------------------------------------------- #
# Formula search bounds
# --------------------------------------------------------------------------- #


def test_derive_formula_needs_at_least_two_operands():
    assert derive_formula(45.0, {"1": "only 20 here"}) == (None, [])


def test_derive_formula_returns_nothing_when_unreachable():
    formula, operands = derive_formula(999999.0, {"1": "2 and 3 and 4"})
    assert formula is None and operands == []


def test_shortest_derivation_is_preferred():
    """A 2-operand explanation beats a 3-operand coincidence on the same value."""
    formula, operands = derive_formula(30.0, {"1": "10 and 20 and 5 and 25"})
    assert formula is not None
    assert len(operands) == 2


def test_formula_search_terminates_on_a_large_pool():
    """Bounded sweep — a big source must not blow up the request."""
    big = " ".join(str(i) for i in range(1, 200))
    formula, _ = derive_formula(10_000_000.0, {"1": big})
    assert formula is None or isinstance(formula, str)


# --------------------------------------------------------------------------- #
# Whole-answer report
# --------------------------------------------------------------------------- #


def test_report_counts_each_class():
    src = {"1": "Phase A obligated 20. Phase B obligated 25."}
    text = ("Phase A obligated 20. [source: 1] "
            "Total obligation across phases is 45. [source: 1]")
    rep = disclose_derivations(text, src)
    assert rep["counts"][DERIV_NUMERIC] >= 1
    assert rep["has_derived"] is True


def test_report_flags_unexplained_numerics():
    src = {"1": "Records retained seven years."}
    rep = disclose_derivations("The value is 8412900. [source: 1]", src)
    assert rep["has_unexplained_numeric"] is True
    assert rep["unexplained_numeric_count"] == 1


def test_fully_quoted_answer_reports_no_derivation():
    src = {"1": "The contractor shall retain all records for seven years."}
    rep = disclose_derivations(
        "The contractor shall retain all records for seven years. [source: 1]", src)
    assert rep["has_derived"] is False
    assert rep["has_unexplained_numeric"] is False


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input_is_safe(text):
    rep = disclose_derivations(text, {"1": "anything"})
    assert rep["claims"] == []
    assert rep["has_derived"] is False


def test_no_sources_does_not_crash():
    d = classify_claim("Some claim with 42 in it.", {})
    assert d.kind in (DERIV_TEXT, DERIV_NUMERIC)
