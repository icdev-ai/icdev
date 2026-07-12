"""Tests for FathomDesk Phase 1 — market data integrity.

Covers:
  - MarketDataProvider ABC contract (OfflineFixtureProvider as concrete impl)
  - DataQualityError on structural violations
  - Staleness detection
  - Gap detection (warning-only)
  - Split-adjustment heuristic (warning-only)
  - Provenance fields on every returned object
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

# tools/trading/ is gitignored (FathomDesk trading modules ship outside the
# committed tree — see .gitignore), so provider/fixture_provider/quality are
# absent on fresh checkouts and in CI. Skip this module cleanly rather than
# hard-erroring collection of the whole `pytest tests/` run.
pytest.importorskip("tools.trading.data.provider")

# ---------------------------------------------------------------------------
# Provider + quality modules under test
# ---------------------------------------------------------------------------
from tools.trading.data.provider import Bar, Chain, MarketDataProvider, Quote, OptionContract
from tools.trading.data.fixture_provider import OfflineFixtureProvider
from tools.trading.data.quality import (
    DataQualityError,
    check_bar_series,
    check_quote_freshness,
    check_split_adjusted,
    is_stale_quote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(offset_seconds: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)


def _make_bar(symbol: str = "AAPL", ts: datetime = None, close: float = 100.0, open_: float = 99.0) -> Bar:
    ts = ts or _utc()
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=open_,
        high=close + 1,
        low=open_ - 1,
        close=close,
        volume=100_000,
        source="fixture",
        as_of=_utc(),
        is_delayed=True,
    )


def _make_quote(symbol: str = "AAPL", age_seconds: float = 0.0, source: str = "fixture") -> Quote:
    return Quote(
        symbol=symbol,
        bid=99.5,
        ask=100.5,
        last=100.0,
        volume=50_000,
        source=source,
        as_of=_utc(age_seconds),
        is_delayed=True,
    )


# ---------------------------------------------------------------------------
# ABC contract via OfflineFixtureProvider
# ---------------------------------------------------------------------------

class TestFixtureProvider:
    def setup_method(self):
        self.provider = OfflineFixtureProvider()

    def test_implements_abc(self):
        assert isinstance(self.provider, MarketDataProvider)

    def test_provider_id(self):
        assert self.provider.provider_id == "fixture"

    def test_is_delayed_always_true(self):
        assert self.provider.is_delayed is True

    def test_get_quote_returns_quote(self):
        q = self.provider.get_quote("AAPL")
        assert isinstance(q, Quote)
        assert q.symbol == "AAPL"
        assert q.source == "fixture"
        assert q.as_of is not None
        assert q.is_delayed is True

    def test_get_quote_has_non_zero_prices(self):
        q = self.provider.get_quote("MSFT")
        assert q.last > 0
        assert q.bid > 0
        assert q.ask > 0
        assert q.ask >= q.bid

    def test_get_bars_returns_bars(self):
        start = _utc(86400 * 10)
        end = _utc()
        bars = self.provider.get_bars("AAPL", "1Day", start, end)
        assert len(bars) > 0
        for b in bars:
            assert isinstance(b, Bar)
            assert b.symbol == "AAPL"
            assert b.source == "fixture"
            assert b.as_of is not None
            assert b.is_delayed is True

    def test_get_bars_provenance_on_every_bar(self):
        bars = self.provider.get_bars("TSLA", "1Day", _utc(200 * 86400), _utc())
        for b in bars:
            assert b.source != "", "source must not be empty"
            assert b.as_of is not None, "as_of must not be None"

    def test_get_options_chain_returns_chain(self):
        chain = self.provider.get_options_chain("AAPL", "2026-12-19")
        assert isinstance(chain, Chain)
        assert chain.symbol == "AAPL"
        assert chain.expiry == "2026-12-19"
        assert chain.source == "fixture"
        assert chain.is_delayed is True
        assert len(chain.contracts) > 0

    def test_get_options_chain_contracts_have_provenance(self):
        chain = self.provider.get_options_chain("SPY", "2026-06-20")
        for c in chain.contracts:
            assert isinstance(c, OptionContract)
            assert c.source == "fixture"

    def test_synthetic_bars_deterministic(self):
        start = _utc(100 * 86400)
        end = _utc()
        bars_a = self.provider.get_bars("AAPL", "1Day", start, end)
        bars_b = self.provider.get_bars("AAPL", "1Day", start, end)
        assert len(bars_a) == len(bars_b)
        for a, b in zip(bars_a, bars_b):
            assert a.close == b.close

    def test_to_dict_has_provenance(self):
        bars = self.provider.get_bars("GOOG", "1Day", _utc(10 * 86400), _utc())
        d = bars[0].to_dict()
        assert "source" in d
        assert "as_of" in d
        assert "is_delayed" in d
        assert d["is_delayed"] is True


# ---------------------------------------------------------------------------
# Quote freshness
# ---------------------------------------------------------------------------

class TestQuoteFreshness:
    def test_fresh_quote_returns_true(self):
        q = _make_quote(age_seconds=10)
        assert check_quote_freshness(q) is True

    def test_stale_quote_returns_false(self):
        q = _make_quote(age_seconds=400)  # > 300s default threshold
        assert check_quote_freshness(q) is False

    def test_custom_threshold(self):
        q = _make_quote(age_seconds=50)
        # 50s > 30s threshold → stale
        assert check_quote_freshness(q, max_age_seconds=30) is False
        # 50s < 60s threshold → fresh
        assert check_quote_freshness(q, max_age_seconds=60) is True

    def test_is_stale_quote_convenience(self):
        fresh = _make_quote(age_seconds=10)
        stale = _make_quote(age_seconds=500)
        assert is_stale_quote(fresh) is False
        assert is_stale_quote(stale) is True

    def test_naive_datetime_treated_as_utc(self):
        q = _make_quote(age_seconds=10)
        q.as_of = q.as_of.replace(tzinfo=None)  # strip tz
        # Should still work without raising
        result = check_quote_freshness(q)
        assert isinstance(result, bool)

    def test_stale_warning_logged(self, caplog):
        q = _make_quote(age_seconds=600)
        with caplog.at_level(logging.WARNING, logger="tools.trading.data.quality"):
            check_quote_freshness(q)
        assert any("Stale quote" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Bar series structural guards
# ---------------------------------------------------------------------------

class TestBarSeriesGuards:
    def test_empty_series_passes(self):
        check_bar_series([])  # no raise

    def test_single_bar_passes(self):
        check_bar_series([_make_bar()])

    def test_monotonic_series_passes(self):
        bars = [_make_bar(ts=_utc(86400 * (10 - i))) for i in range(10)]
        check_bar_series(bars)  # no raise

    def test_duplicate_timestamp_raises(self):
        t = _utc(86400)
        bars = [_make_bar(ts=t), _make_bar(ts=t)]
        with pytest.raises(DataQualityError, match="Duplicate timestamp"):
            check_bar_series(bars)

    def test_non_monotonic_timestamp_raises(self):
        t0 = _utc(86400 * 2)
        t1 = _utc(86400 * 3)  # t1 is OLDER than t0 (more seconds ago)
        bars = [_make_bar(ts=t0), _make_bar(ts=t1)]
        with pytest.raises(DataQualityError, match="Non-monotonic"):
            check_bar_series(bars)

    def test_gap_detection_logs_warning(self, caplog):
        # Create 10 daily bars then skip 10 days → gap
        base = _utc(86400 * 20)
        bars = [_make_bar(ts=base + timedelta(days=i)) for i in range(10)]
        # Insert a large gap: jump 25 days instead of 1
        bars.append(_make_bar(ts=base + timedelta(days=35)))
        with caplog.at_level(logging.WARNING, logger="tools.trading.data.quality"):
            check_bar_series(bars)
        assert any("gap detected" in r.message.lower() for r in caplog.records)

    def test_no_gap_warning_for_uniform_series(self, caplog):
        base = _utc(86400 * 100)
        bars = [_make_bar(ts=base + timedelta(days=i)) for i in range(30)]
        with caplog.at_level(logging.WARNING, logger="tools.trading.data.quality"):
            check_bar_series(bars)
        assert not any("gap" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Split-adjustment heuristic
# ---------------------------------------------------------------------------

class TestSplitAdjustedGuard:
    def test_normal_bars_no_warning(self, caplog):
        base = _utc(86400 * 5)
        bars = [_make_bar(ts=base + timedelta(days=i), close=100.0 + i * 0.5, open_=100.0 + i * 0.5 - 0.1)
                for i in range(5)]
        with caplog.at_level(logging.WARNING, logger="tools.trading.data.quality"):
            check_split_adjusted(bars)
        assert not any("split" in r.message.lower() for r in caplog.records)

    def test_2_for_1_split_logs_warning(self, caplog):
        base = _utc(86400 * 2)
        bars = [
            _make_bar(ts=base, close=200.0, open_=199.0),
            _make_bar(ts=base + timedelta(days=1), close=101.0, open_=100.0),  # ~50% drop
        ]
        with caplog.at_level(logging.WARNING, logger="tools.trading.data.quality"):
            check_split_adjusted(bars)
        assert any("split" in r.message.lower() for r in caplog.records)

    def test_single_bar_no_error(self, caplog):
        with caplog.at_level(logging.WARNING):
            check_split_adjusted([_make_bar()])
        assert not caplog.records


# ---------------------------------------------------------------------------
# Bar.to_dict provenance contract
# ---------------------------------------------------------------------------

class TestBarToDict:
    def test_to_dict_contains_all_provenance_keys(self):
        b = _make_bar()
        b.source = "alpaca"
        d = b.to_dict()
        assert d["source"] == "alpaca"
        assert "as_of" in d
        assert "is_delayed" in d
        assert "t" in d
        assert "o" in d
        assert "h" in d
        assert "l" in d
        assert "c" in d
        assert "v" in d

    def test_quote_to_dict_contains_provenance(self):
        q = _make_quote(source="alpaca")
        d = q.to_dict()
        assert d["source"] == "alpaca"
        assert "as_of" in d
        assert "is_delayed" in d


# ---------------------------------------------------------------------------
# market_data.py dict provenance (integration-style, no network)
# ---------------------------------------------------------------------------

class TestMarketDataProvenance:
    def test_generate_sample_bars_has_provenance(self):
        from tools.trading.data.market_data import _generate_sample_bars
        bars = _generate_sample_bars("AAPL", 5)
        for b in bars:
            assert "source" in b, "source key missing from sample bar"
            assert "as_of" in b, "as_of key missing from sample bar"
            assert "is_delayed" in b, "is_delayed key missing from sample bar"
            assert b["source"] == "sample"
            assert b["is_delayed"] is True

    def test_fetch_bars_sample_fallback_has_provenance(self, monkeypatch):
        """When Alpaca is unavailable, fetch_bars falls back to sample and carries provenance."""
        from tools.trading.brokers import alpaca_adapter
        monkeypatch.setattr(alpaca_adapter, "get_default", lambda: _FakeUnavailableAdapter())
        from tools.trading.data import market_data
        bars = market_data.fetch_bars("AAPL", limit=3)
        assert len(bars) > 0
        for b in bars:
            assert b.get("source") == "sample"
            assert "as_of" in b
            assert b.get("is_delayed") is True

    def test_fetch_latest_quote_sample_fallback_has_provenance(self, monkeypatch):
        from tools.trading.brokers import alpaca_adapter
        monkeypatch.setattr(alpaca_adapter, "get_default", lambda: _FakeUnavailableAdapter())
        from tools.trading.data import market_data
        q = market_data.fetch_latest_quote("AAPL")
        assert q.get("source") == "sample"
        assert "as_of" in q
        assert q.get("is_delayed") is True


class _FakeUnavailableAdapter:
    def is_available(self):
        return False
    def is_paper(self):
        return False
    def get_bars(self, *a, **kw):
        return []
    def get_latest_quote(self, *a, **kw):
        return {}
    def get_latest_trade(self, *a, **kw):
        return {}
    def get_crypto_bars(self, *a, **kw):
        return []
