"""OfflineFixtureProvider — deterministic MarketDataProvider for tests and air-gap mode.

Uses the same LCG as the original _generate_sample_bars so fixture data is
stable across test runs. All returned objects carry source="fixture" and
is_delayed=True to make it impossible for stale-data checks to treat
fixture data as live.
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from tools.trading.data.provider import Bar, Chain, MarketDataProvider, OptionContract, Quote
from tools.logging.icdev_logger import get_logger

log = get_logger("trading.fixture")

_SOURCE_ID = "fixture"
# Default snapshot directory — optional; if absent, synthetic data is used
_DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "market_data"


class OfflineFixtureProvider(MarketDataProvider):
    """Deterministic provider for tests and air-gapped environments.

    Priority:
    1. If a JSON snapshot file exists at <snapshot_dir>/<symbol>.json, load it.
    2. Otherwise generate synthetic data deterministically via LCG seeded on symbol.
    """

    def __init__(self, snapshot_dir: Optional[Path] = None) -> None:
        self._snapshot_dir = snapshot_dir or _DEFAULT_SNAPSHOT_DIR

    @property
    def provider_id(self) -> str:
        return _SOURCE_ID

    @property
    def is_delayed(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    def get_quote(self, symbol: str) -> Quote:
        snapshot = self._load_snapshot(symbol)
        as_of = datetime.now(timezone.utc)
        if snapshot and snapshot.get("quote"):
            q = snapshot["quote"]
            return Quote(
                symbol=symbol,
                bid=float(q.get("bid", 0.0)),
                ask=float(q.get("ask", 0.0)),
                last=float(q.get("last", 0.0)),
                volume=int(q.get("volume", 0)),
                source=_SOURCE_ID,
                as_of=as_of,
                is_delayed=True,
            )
        bars = self._synthetic_bars(symbol, n=1)
        price = bars[-1].close if bars else 100.0
        return Quote(
            symbol=symbol,
            bid=round(price * 0.999, 2),
            ask=round(price * 1.001, 2),
            last=price,
            volume=100_000,
            source=_SOURCE_ID,
            as_of=as_of,
            is_delayed=True,
        )

    def get_bars(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        snapshot = self._load_snapshot(symbol)
        as_of = datetime.now(timezone.utc)
        if snapshot and snapshot.get("bars"):
            bars = []
            for b in snapshot["bars"]:
                ts_str = b.get("t") or ""
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    ts = as_of
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                bars.append(Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=float(b.get("o", 0.0)),
                    high=float(b.get("h", 0.0)),
                    low=float(b.get("l", 0.0)),
                    close=float(b.get("c", 0.0)),
                    volume=int(b.get("v", 0)),
                    source=_SOURCE_ID,
                    as_of=as_of,
                    is_delayed=True,
                    vendor_ts=ts_str,
                ))
            return bars
        # Filter synthetic bars to requested window
        n = max(1, (end - start).days + 1) if end > start else 200
        return self._synthetic_bars(symbol, n=min(n, 500))

    def get_options_chain(self, symbol: str, expiry: str) -> Chain:
        return Chain(
            symbol=symbol,
            expiry=expiry,
            contracts=self._synthetic_options(symbol, expiry),
            source=_SOURCE_ID,
            as_of=datetime.now(timezone.utc),
            is_delayed=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_snapshot(self, symbol: str) -> Optional[dict]:
        path = self._snapshot_dir / f"{symbol.upper()}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("Failed to load snapshot %s: %s", path, exc)
        return None

    def _synthetic_bars(self, symbol: str, n: int = 200) -> list[Bar]:
        seed = zlib.crc32(symbol.encode("utf-8")) & 0xFFFFFFFF
        base_price = 100.0 + (seed % 400)
        base_volume = 1_000_000 + (seed % 5_000_000)
        price = base_price
        now = datetime.now(timezone.utc)
        bars: list[Bar] = []
        for i in range(n):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            change_pct = ((seed % 1000) - 500) / 10000.0
            open_price = price
            close_price = price * (1 + change_pct)
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            intra_range = abs(change_pct) + (seed % 100) / 10000.0
            high = max(open_price, close_price) * (1 + intra_range)
            low = min(open_price, close_price) * (1 - intra_range)
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            volume = int(base_volume * (0.5 + (seed % 100) / 100.0))
            bars.append(Bar(
                symbol=symbol,
                timestamp=now - timedelta(days=n - i),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close_price, 2),
                volume=volume,
                source=_SOURCE_ID,
                as_of=now,
                is_delayed=True,
            ))
            price = close_price
        return bars

    def _synthetic_options(self, symbol: str, expiry: str) -> list[OptionContract]:
        seed = zlib.crc32(symbol.encode("utf-8")) & 0xFFFFFFFF
        base_price = 100.0 + (seed % 400)
        strikes = [base_price * m for m in (0.9, 0.95, 1.0, 1.05, 1.1)]
        now = datetime.now(timezone.utc)
        contracts: list[OptionContract] = []
        for strike in strikes:
            for option_type in ("call", "put"):
                contracts.append(OptionContract(
                    symbol=symbol,
                    expiry=expiry,
                    strike=round(strike, 2),
                    option_type=option_type,
                    bid=round(max(0.01, (base_price - strike if option_type == "put" else strike - base_price) * 0.1), 2),
                    ask=round(max(0.02, (base_price - strike if option_type == "put" else strike - base_price) * 0.12), 2),
                    last=round(max(0.01, (base_price - strike if option_type == "put" else strike - base_price) * 0.11), 2),
                    volume=1000,
                    open_interest=5000,
                    implied_volatility=0.25,
                    delta=0.5 if option_type == "call" else -0.5,
                    gamma=0.05,
                    theta=-0.02,
                    vega=0.15,
                    source=_SOURCE_ID,
                    as_of=now,
                    is_delayed=True,
                ))
        return contracts
