"""MarketDataProvider — ABC and domain types for all FathomDesk market data.

Every numeric fact produced by a provider carries:
  source     — provider id (e.g. "alpaca", "fixture", "sample")
  as_of      — datetime UTC: when this data was valid at the source
  is_delayed — True when the provider serves non-real-time data

Design: thin ABC with three methods; dataclasses not dicts so provenance
cannot be silently stripped by callers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    symbol: str
    timestamp: datetime       # bar close time (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: int
    # provenance — mandatory, no default so callers can't forget them
    source: str
    as_of: datetime = field(default_factory=_utcnow)
    is_delayed: bool = False
    vendor_ts: Optional[str] = None   # raw timestamp string from vendor

    def to_dict(self) -> dict:
        return {
            "t": self.timestamp.isoformat(),
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "is_delayed": self.is_delayed,
        }


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    # provenance
    source: str
    as_of: datetime = field(default_factory=_utcnow)
    is_delayed: bool = False
    vendor_ts: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "is_delayed": self.is_delayed,
        }


@dataclass
class OptionContract:
    symbol: str            # underlying
    expiry: str            # YYYY-MM-DD
    strike: float
    option_type: str       # "call" or "put"
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float
    # provenance
    source: str
    as_of: datetime = field(default_factory=_utcnow)
    is_delayed: bool = False


@dataclass
class Chain:
    symbol: str
    expiry: str
    contracts: list[OptionContract]
    # provenance (applies to the chain as a whole)
    source: str
    as_of: datetime = field(default_factory=_utcnow)
    is_delayed: bool = False


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class MarketDataProvider(ABC):
    """Abstract base class for all FathomDesk market data sources.

    Implementors MUST populate source/as_of/is_delayed on every returned
    object — the data layer enforces this contract; no bare dicts escape.
    """

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Return the latest quote for *symbol*. Raises on hard failure."""

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Return OHLCV bars for *symbol* in [start, end]. Empty list if none."""

    @abstractmethod
    def get_options_chain(self, symbol: str, expiry: str) -> Chain:
        """Return the options chain for *symbol* expiring on *expiry* (YYYY-MM-DD)."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Short identifier string (e.g. 'alpaca', 'fixture', 'sample')."""

    @property
    @abstractmethod
    def is_delayed(self) -> bool:
        """True when this provider serves non-real-time data."""
