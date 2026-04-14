"""Tests for _per_cycle_cache + earnings_batch."""

from unittest import mock

import pytest

from tools.trading.analysis import _per_cycle_cache as cache
from tools.trading.llm import earnings_batch as eb


@pytest.fixture(autouse=True)
def _reset_cache():
    cache.invalidate()


# ---------------- per-cycle cache ----------------


def test_loader_called_once_on_hit():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return "hello"

    v1 = cache.get_or_compute("quote", ("AAPL",), loader, ttl=60)
    v2 = cache.get_or_compute("quote", ("AAPL",), loader, ttl=60)
    assert v1 == v2 == "hello"
    assert calls["n"] == 1


def test_ttl_expires():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return calls["n"]

    # ttl=0 forces recompute on every call
    v1 = cache.get_or_compute("quote", ("AAPL",), loader, ttl=0)
    v2 = cache.get_or_compute("quote", ("AAPL",), loader, ttl=0)
    assert v1 == 1 and v2 == 2


def test_invalidate_clears_namespace():
    cache.get_or_compute("quote", ("A",), lambda: 1, ttl=60)
    cache.get_or_compute("regime", ("R",), lambda: 2, ttl=60)
    removed = cache.invalidate("quote")
    assert removed == 1
    # regime still cached
    assert cache.get_or_compute("regime", ("R",), lambda: 99, ttl=60) == 2


def test_invalidate_all_clears_everything():
    cache.get_or_compute("quote", ("A",), lambda: 1, ttl=60)
    cache.get_or_compute("regime", ("R",), lambda: 2, ttl=60)
    cache.invalidate()
    assert cache.size() == 0


# ---------------- earnings_batch ----------------


def test_batch_falls_back_to_heuristic_when_llm_missing():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        from tools.llm.router import LLMUnavailableError

        r = mock.Mock()
        r.invoke.side_effect = LLMUnavailableError("no provider")
        Rcls.return_value = r
        items = [("AAPL", "Management raised guidance with strong confidence."),
                 ("MSFT", "Lowered guidance. Margin pressure and weakness.")]
        out = eb.analyze_batch(items)
    assert len(out) == 2
    # Heuristic directions should match
    assert out[0]["guidance_direction"] in ("raised", "mixed", "unknown")


def test_batch_uses_llm_response_array():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        r = mock.Mock()
        r.invoke.return_value = mock.Mock(
            content=(
                '[{"ticker":"A","guidance_direction":"raised","tone":"bullish","risk_flags":[],"supply_chain_mentions":[],"ai_capex_direction":"up","summary":"s1"},'
                ' {"ticker":"B","guidance_direction":"lowered","tone":"bearish","risk_flags":["fx"],"supply_chain_mentions":[],"ai_capex_direction":"flat","summary":"s2"}]'
            ),
            provider="ollama",
            model_id="qwen3.5",
        )
        Rcls.return_value = r
        out = eb.analyze_batch([("A", "raised guidance"), ("B", "cut guidance")])
    assert len(out) == 2
    assert out[0]["guidance_direction"] == "raised"
    assert out[1]["guidance_direction"] == "lowered"


def test_empty_input_returns_empty():
    assert eb.analyze_batch([]) == []


def test_malformed_llm_falls_back_to_heuristic():
    with mock.patch("tools.llm.router.LLMRouter") as Rcls:
        r = mock.Mock()
        r.invoke.return_value = mock.Mock(content="totally not JSON", provider="ollama", model_id="x")
        Rcls.return_value = r
        out = eb.analyze_batch([("A", "raised guidance confidently"), ("B", "lowered below consensus")])
    assert len(out) == 2
