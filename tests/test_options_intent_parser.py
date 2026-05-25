# CUI // SP-CTI
"""Phase 7.6 — smoke tests for tools/trading/options/intent_parser.py.

Scope: verify the rule-fallback path produces schema-valid dicts across a
representative corpus of trader intents. The LLM path is exercised only
indirectly via ICDEV_NO_LLM=true (forces the rule path), since the LLM
contract is interchangeable — any provider that returns the same JSON
surface should work.
"""
from __future__ import annotations


import pytest

from tools.trading.options.intent_parser import parse_intent

_ALLOWED_DIR = {"bullish", "bearish", "neutral", "volatile"}
_ALLOWED_HORIZON = {"intraday", "short", "earnings", "medium", "long"}
_ALLOWED_IV = {"high", "low", "neutral"}
_ALLOWED_RISK = {"defined", "undefined"}


@pytest.fixture(autouse=True)
def _force_rule_path(monkeypatch):
    """Run every test with the LLM path disabled so behavior is deterministic."""
    monkeypatch.setenv("ICDEV_NO_LLM", "true")


# ---------------------------------------------------------------------------
# Schema-validity smoke — every output field must be in the allowed enum.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Bullish AAPL through earnings, limited risk",
    "Short AMZN, high conviction, undefined risk ok",
    "Sell volatility on SPY next week",
    "Range-bound chop on QQQ next month",
    "LEAPS on NVDA, 6 months out",
    "Intraday scalp on TSLA",
    "Expect big earnings surprise on GOOGL",
    "Bearish oil, 30 days, spread",
    "",                                    # empty input must not crash
    "This sentence has no trading content",  # must fall back to defaults
])
def test_intent_always_returns_valid_schema(text):
    r = parse_intent(text)
    assert r["direction"] in _ALLOWED_DIR
    assert r["horizon"] in _ALLOWED_HORIZON
    assert r["iv_view"] in _ALLOWED_IV
    assert r["risk_cap"] in _ALLOWED_RISK
    assert r["source"] == "rule"
    assert r["raw_text"] == text


# ---------------------------------------------------------------------------
# Targeted extraction — specific phrases should land in specific enums.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Bullish AAPL through earnings, limited risk",
     {"direction": "bullish", "horizon": "earnings", "risk_cap": "defined",
      "underlying": "AAPL"}),
    ("Short AMZN, high conviction, undefined risk ok",
     {"direction": "bearish", "risk_cap": "undefined", "underlying": "AMZN"}),
    ("Sell volatility on SPY next week",
     {"iv_view": "high", "horizon": "short", "underlying": "SPY"}),
    ("Range-bound chop on QQQ next month",
     {"direction": "neutral", "horizon": "medium", "underlying": "QQQ"}),
    ("LEAPS on NVDA, 6 months out",
     {"horizon": "long", "underlying": "NVDA"}),
    ("Intraday scalp on TSLA",
     {"horizon": "intraday", "underlying": "TSLA"}),
    ("Expect big earnings surprise on GOOGL",
     {"direction": "volatile", "horizon": "earnings", "underlying": "GOOGL"}),
    ("Bearish oil, 30 days, spread",
     {"direction": "bearish", "horizon": "medium", "risk_cap": "defined"}),
])
def test_intent_extracts_targeted_fields(text, expected):
    r = parse_intent(text)
    for k, v in expected.items():
        assert r[k] == v, f"{text!r} field {k}: expected {v!r}, got {r[k]!r}"


# ---------------------------------------------------------------------------
# Explicit underlying kwarg overrides text extraction.
# ---------------------------------------------------------------------------
def test_explicit_underlying_wins():
    r = parse_intent("Bullish on the stock", underlying="MSFT")
    assert r["underlying"] == "MSFT"


def test_explicit_underlying_is_uppercased_and_stripped():
    r = parse_intent("thesis", underlying="  msft  ")
    assert r["underlying"] == "MSFT"


# ---------------------------------------------------------------------------
# 'undefined' must not substring-match 'defined' (regression).
# ---------------------------------------------------------------------------
def test_undefined_risk_is_not_defined():
    r = parse_intent("Naked short, undefined risk acceptable")
    assert r["risk_cap"] == "undefined"


# ---------------------------------------------------------------------------
# Defaults kick in when nothing matches.
# ---------------------------------------------------------------------------
def test_defaults_when_no_hints():
    r = parse_intent("plain text, no trading terms here")
    # Defaults per args/options_intent_schema.yaml.
    assert r["direction"] == "neutral"
    assert r["horizon"] == "short"
    assert r["iv_view"] == "neutral"
    assert r["risk_cap"] == "defined"


# ---------------------------------------------------------------------------
# parse_intent never raises — even on bizarre input.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [None, "", "   ", "\n\n", "12345", "🚀🚀🚀"])
def test_parser_never_raises(text):
    r = parse_intent(text or "")
    assert isinstance(r, dict)
    assert r["direction"] in _ALLOWED_DIR
