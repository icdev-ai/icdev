"""AlpacaProvider — MarketDataProvider backed by the existing AlpacaAdapter.

Wraps tools/trading/brokers/alpaca_adapter.py.
Real-time stock data requires an Alpaca Unlimited subscription; free tier is
delayed 15 minutes, so is_delayed reflects adapter.is_paper().
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.trading.data.provider import Bar, Chain, MarketDataProvider, Quote

from tools.logging.icdev_logger import get_logger
log = get_logger("trading.alpaca")

_SOURCE_ID = "alpaca"


class AlpacaProvider(MarketDataProvider):
    """Live/paper market data via Alpaca REST API."""

    def __init__(self) -> None:
        from tools.trading.brokers.alpaca_adapter import get_default
        self._adapter = get_default()

    @property
    def provider_id(self) -> str:
        return _SOURCE_ID

    @property
    def is_delayed(self) -> bool:
        # Paper endpoint indicates no real-time feed; free tier is also delayed.
        return self._adapter.is_paper()

    def is_available(self) -> bool:
        return self._adapter.is_available()

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    def get_quote(self, symbol: str) -> Quote:
        as_of = datetime.now(timezone.utc)
        raw = self._adapter.get_latest_quote(symbol)
        # Alpaca quote keys: bp/ap/bp/ap for bid/ask, lp for last trade price
        bid = float(raw.get("bp") or raw.get("bid_price") or 0.0)
        ask = float(raw.get("ap") or raw.get("ask_price") or 0.0)
        # last trade price comes from a separate endpoint; use mid if absent
        last_raw = self._adapter.get_latest_trade(symbol)
        last = float(last_raw.get("p") or last_raw.get("price") or ((bid + ask) / 2))
        volume = int(raw.get("bs") or raw.get("bid_size") or 0)
        vendor_ts = str(raw.get("t") or raw.get("timestamp") or "")
        return Quote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            source=_SOURCE_ID,
            as_of=as_of,
            is_delayed=self.is_delayed,
            vendor_ts=vendor_ts,
        )

    def get_bars(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        as_of = datetime.now(timezone.utc)
        raw_bars = self._adapter.get_bars(
            symbol,
            timeframe=interval,
            limit=500,
        )
        bars: list[Bar] = []
        for b in raw_bars:
            try:
                ts_str = b.get("t") or ""
                # Parse ISO timestamp from Alpaca (may include Z suffix)
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else as_of
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                bars.append(Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=round(float(b.get("o", 0.0)), 4),
                    high=round(float(b.get("h", 0.0)), 4),
                    low=round(float(b.get("l", 0.0)), 4),
                    close=round(float(b.get("c", 0.0)), 4),
                    volume=int(b.get("v", 0) or 0),
                    source=_SOURCE_ID,
                    as_of=as_of,
                    is_delayed=self.is_delayed,
                    vendor_ts=ts_str,
                ))
            except Exception as exc:
                log.warning("Skipping malformed Alpaca bar: %s — %s", b, exc)
        return bars

    def get_options_chain(self, symbol: str, expiry: str) -> Chain:
        # Alpaca does not currently provide options data on the free/paper tier.
        # Return an empty chain with honest provenance.
        log.info("Options chain unavailable via Alpaca provider for %s/%s", symbol, expiry)
        return Chain(
            symbol=symbol,
            expiry=expiry,
            contracts=[],
            source=_SOURCE_ID,
            as_of=datetime.now(timezone.utc),
            is_delayed=True,
        )
