"""Tests for llm.earnings_extractor + earnings_llm pillar."""

from unittest import mock

import pytest

from tools.trading.analysis.confluence_pillars import earnings_llm
from tools.trading.llm import earnings_extractor as ee


@pytest.fixture(autouse=True)
def _bootstrap():
    ee._conn().close()
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_earnings_analysis WHERE ticker LIKE 'ZZ%'")
    c.commit()
    c.close()


def test_heuristic_detects_raised_guidance():
    text = """Management raised guidance for fiscal year 2026, noting strong momentum in cloud billings.
    We are confident in accelerating growth. Raising full-year outlook by 300 basis points."""
    out = ee._heuristic_extract(text)
    assert out["guidance_direction"] in ("raised", "mixed")
    assert out["tone"] == "bullish"


def test_heuristic_detects_lowered_guidance():
    text = "Management lowered guidance below street consensus. Margin pressure and weakness in Europe. We're cautious."
    out = ee._heuristic_extract(text)
    assert out["guidance_direction"] == "lowered"
    assert out["tone"] in ("bearish", "cautious")


def test_heuristic_flags_supply_chain():
    text = "Supply chain disruptions continue to affect our logistics costs this quarter."
    out = ee._heuristic_extract(text)
    assert any("supply chain" in r.lower() for r in out["risk_flags"])


def test_empty_input_returns_status():
    out = ee.analyze("ZZEMP", "")
    assert out["status"] == "empty_input"


def test_analyze_caches_by_hash():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        r = mock.Mock()
        resp = mock.Mock(content='{"guidance_direction":"raised","tone":"bullish","risk_flags":["fx"],"supply_chain_mentions":[],"ai_capex_direction":"up","summary":"strong quarter"}', provider="ollama", model_id="qwen3.5")
        r.invoke.return_value = resp
        Rcls.return_value = r

        out1 = ee.analyze("ZZCACHE", "Strong quarter raised guidance confidently.", use_cache=False)
        out2 = ee.analyze("ZZCACHE", "Strong quarter raised guidance confidently.", use_cache=True)

    assert out1["source"] == "fresh"
    assert out2["source"] == "cache"
    assert out1["filing_hash"] == out2["filing_hash"]


def test_fallback_to_heuristic_when_llm_unavailable():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        from tools.llm.router import LLMUnavailableError

        r = mock.Mock()
        r.invoke.side_effect = LLMUnavailableError("no provider")
        Rcls.return_value = r
        out = ee.analyze("ZZNOFLM", "Management raised guidance with strong confidence.", use_cache=False)
    assert out["status"] == "ok"
    assert out["provider"] == "heuristic"
    assert "guidance_direction" in out


def test_malformed_llm_json_falls_back():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        r = mock.Mock()
        r.invoke.return_value = mock.Mock(content="I'm an LLM, I can't output JSON!", provider="ollama", model_id="x")
        Rcls.return_value = r
        out = ee.analyze("ZZBAD", "Strong quarter raised guidance confidently.", use_cache=False)
    assert out["provider"] == "heuristic"


def test_pillar_unknown_when_no_analysis():
    p = earnings_llm.build("DEFINITELY-NOT-A-TICKER-ZZZ")
    assert p.direction == "unknown"


def test_pillar_bull_on_raised_bullish():
    with mock.patch("tools.trading.analysis.confluence_pillars.earnings_llm.latest_for_ticker",
                    return_value={"guidance_direction": "raised", "tone": "bullish", "risk_flags": []}):
        p = earnings_llm.build("ZZBULL")
    assert p.direction == "bull"


def test_pillar_bear_on_lowered_or_bearish():
    with mock.patch("tools.trading.analysis.confluence_pillars.earnings_llm.latest_for_ticker",
                    return_value={"guidance_direction": "lowered", "tone": "cautious", "risk_flags": ["margin"]}):
        p = earnings_llm.build("ZZBEAR")
    assert p.direction == "bear"


def test_pillar_neutral_on_mixed():
    with mock.patch("tools.trading.analysis.confluence_pillars.earnings_llm.latest_for_ticker",
                    return_value={"guidance_direction": "mixed", "tone": "neutral", "risk_flags": []}):
        p = earnings_llm.build("ZZMIX")
    assert p.direction == "neutral"
