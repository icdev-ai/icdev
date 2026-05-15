# [TEMPLATE: CUI // SP-CTI]
"""Tests for the ICDEV™ Budget Range Validator."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.requirements.budget_validator import (
    _normalize_amount,
    parse_budgets,
    validate_budget,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def budget_3_to_5_million_text():
    return "Project budget is 3 to 5 million dollars for full development and first year operations."


@pytest.fixture
def budget_single_million_text():
    return "The total budget is $4.2 million USD for the complete engagement."


@pytest.fixture
def budget_bare_number_text():
    return "Estimated cost is $3,500,000 for development and operations."


@pytest.fixture
def budget_below_range_text():
    return "Project budget is $2 million for full development and first year operations."


@pytest.fixture
def budget_above_range_text():
    return "Project budget is $6.5 million for full development and first year operations."


@pytest.fixture
def no_budget_text():
    return "The system must support 200 concurrent users with sub-second response time."


# ── Tests: _normalize_amount ─────────────────────────────────────────────────


class TestNormalizeAmount:
    def test_bare_number(self):
        assert _normalize_amount("3500000", None) == 3_500_000.0

    def test_million_lowercase(self):
        assert _normalize_amount("3.5", "million") == 3_500_000.0

    def test_million_abbreviation(self):
        assert _normalize_amount("5", "M") == 5_000_000.0

    def test_thousand(self):
        assert _normalize_amount("250", "thousand") == 250_000.0

    def test_thousand_abbreviation(self):
        assert _normalize_amount("750", "k") == 750_000.0

    def test_billion(self):
        assert _normalize_amount("1.2", "billion") == 1_200_000_000.0

    def test_comma_in_amount(self):
        assert _normalize_amount("3,000,000", None) == 3_000_000.0


# ── Tests: parse_budgets ─────────────────────────────────────────────────────


class TestParseBudgets:
    def test_range_3_to_5_million(self, budget_3_to_5_million_text):
        results = parse_budgets(budget_3_to_5_million_text)
        assert len(results) == 2
        amounts = sorted([r.amount_usd for r in results])
        assert amounts[0] == 3_000_000.0
        assert amounts[1] == 5_000_000.0

    def test_single_million_expression(self, budget_single_million_text):
        results = parse_budgets(budget_single_million_text)
        assert len(results) == 1
        assert results[0].amount_usd == 4_200_000.0
        assert results[0].currency_symbol == "USD"

    def test_bare_number_with_commas(self, budget_bare_number_text):
        results = parse_budgets(budget_bare_number_text)
        assert len(results) == 1
        assert results[0].amount_usd == 3_500_000.0

    def test_no_budget_returns_empty(self, no_budget_text):
        results = parse_budgets(no_budget_text)
        assert len(results) == 0

    def test_dollar_sign_with_space(self):
        text = "The budget is $ 3.5 million dollars."
        results = parse_budgets(text)
        assert len(results) == 1
        assert results[0].amount_usd == 3_500_000.0

    def test_m_suffix(self):
        text = "Funding allocated is 4.5M USD."
        results = parse_budgets(text)
        assert len(results) >= 1
        assert any(r.amount_usd == 4_500_000.0 for r in results)


# ── Tests: validate_budget ───────────────────────────────────────────────────


class TestValidateBudget:
    def test_3_to_5_million_passes(self, budget_3_to_5_million_text):
        result = validate_budget(
            text=budget_3_to_5_million_text,
            requirement_id="req-budget-001",
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"
        assert result.within_range is True
        assert result.requirement_id == "req-budget-001"
        assert len(result.parsed_budgets) == 2
        assert any("within acceptable range" in m for m in result.messages)

    def test_single_value_within_range_passes(self, budget_single_million_text):
        result = validate_budget(
            text=budget_single_million_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"
        assert result.within_range is True
        assert len(result.parsed_budgets) == 1
        assert result.parsed_budgets[0].amount_usd == 4_200_000.0

    def test_below_range_fails(self, budget_below_range_text):
        result = validate_budget(
            text=budget_below_range_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "fail"
        assert result.within_range is False
        assert any("below minimum" in m for m in result.messages)

    def test_above_range_fails(self, budget_above_range_text):
        result = validate_budget(
            text=budget_above_range_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "fail"
        assert result.within_range is False
        assert any("exceeds maximum" in m for m in result.messages)

    def test_no_budget_not_found(self, no_budget_text):
        result = validate_budget(
            text=no_budget_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "not_found"
        assert result.within_range is None
        assert len(result.parsed_budgets) == 0
        assert any("No budget expression detected" in m for m in result.messages)

    def test_bare_number_within_range(self, budget_bare_number_text):
        result = validate_budget(
            text=budget_bare_number_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"
        assert result.parsed_budgets[0].amount_usd == 3_500_000.0

    def test_result_to_dict(self, budget_3_to_5_million_text):
        result = validate_budget(
            text=budget_3_to_5_million_text,
            requirement_id="req-budget-test",
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        d = result.to_dict()
        assert d["status"] == "pass"
        assert d["requirement_id"] == "req-budget-test"
        assert d["min_usd"] == 3_000_000
        assert d["max_usd"] == 5_000_000
        assert d["within_range"] is True
        assert len(d["parsed_budgets"]) == 2
        assert "messages" in d


# ── Integration: story-level scenario ──────────────────────────────────────────


class TestBudgetStoryScenario:
    """Validate the kanban story: Project budget is 3 to 5 million dollars."""

    def test_story_budget_passes_validation(self):
        story_text = (
            "Project budget is 3 to 5 million dollars for full development and first year operations."
        )
        result = validate_budget(
            text=story_text,
            requirement_id="task-d2de91dbc2",
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"
        assert result.within_range is True
        amounts = sorted([pb.amount_usd for pb in result.parsed_budgets])
        assert amounts == [3_000_000.0, 5_000_000.0]

    def test_story_budget_min_boundary(self):
        story_text = "Project budget is $3,000,000 for full development and first year operations."
        result = validate_budget(
            text=story_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"

    def test_story_budget_max_boundary(self):
        story_text = "Project budget is $5,000,000 for full development and first year operations."
        result = validate_budget(
            text=story_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "pass"

    def test_story_budget_just_below_min_fails(self):
        story_text = "Project budget is $2,999,999 for full development and first year operations."
        result = validate_budget(
            text=story_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "fail"
        assert result.within_range is False

    def test_story_budget_just_above_max_fails(self):
        story_text = "Project budget is $5,000,001 for full development and first year operations."
        result = validate_budget(
            text=story_text,
            min_usd=3_000_000,
            max_usd=5_000_000,
        )
        assert result.status == "fail"
        assert result.within_range is False
