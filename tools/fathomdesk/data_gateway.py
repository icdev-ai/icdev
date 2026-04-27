#!/usr/bin/env python3
"""FathomDesk Data Gateway — unified market-data facade.

Aggregates OpenBB (fundamentals/options), Alpaca (broker), and yfinance
(price fallback) behind a single class so callers don't need to wire
adapters individually.  All external dependencies are guarded — the class
instantiates cleanly even when none of them are installed.

Usage:
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway
    gw = FathomDeskDataGateway()
"""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_yfinance = None
try:
    import yfinance as _yfinance  # type: ignore[import-untyped]
except ImportError:
    _yfinance = None

from tools.fathomdesk.openbb_gateway import gateway as _obb_singleton, OpenBBGateway  # noqa: E402
from tools.fathomdesk.broker_adapter import BrokerAdapter  # noqa: E402

_ALPACA_DATA_BASE = "https://data.alpaca.markets"
_REQUEST_TIMEOUT = 10


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


class FathomDeskDataGateway:
    """Unified data facade over OpenBB, Alpaca, and yfinance.

    Attributes:
        _obb: The module-level :class:`OpenBBGateway` singleton.
        _alpaca: Lazily-initialised :class:`BrokerAdapter`; ``None`` until
            first call to :meth:`_get_alpaca`.
    """

    def __init__(self) -> None:
        self._obb: OpenBBGateway = _obb_singleton
        self._alpaca: BrokerAdapter | None = None

    def _get_alpaca(self) -> BrokerAdapter:
        """Return the :class:`BrokerAdapter`, creating it on first access."""
        if self._alpaca is None:
            self._alpaca = BrokerAdapter()
        return self._alpaca

    def macro_indicator(self, indicator: str) -> list:
        """Fetch macro indicator time-series from OpenBB.

        Tries ``economy.fred_series`` (FRED series ID, e.g. ``"GDP"``,
        ``"UNRATE"``) first, then falls back to ``economy.indicators``
        (named indicator lookup).

        Args:
            indicator: FRED series ID or indicator name.

        Returns:
            List of record dicts from OpenBB; empty list on any failure or
            when OpenBB is unavailable.
        """
        if not self._obb.available:
            return []
        obb = self._obb._obb  # the underlying openbb module
        try:
            result = obb.economy.fred_series(symbol=indicator)
            return result.to_df().to_dict(orient="records")
        except Exception:
            pass
        try:
            result = obb.economy.indicators(name=indicator)
            return result.to_df().to_dict(orient="records")
        except Exception:
            return []

    def _alpaca_latest_price(self, ticker: str) -> float:
        """Fetch the latest trade price from the Alpaca Data API.

        Raises:
            ValueError: When ALPACA_API_KEY is not configured.
            RuntimeError: On HTTP errors from the Alpaca API.
        """
        _load_env()
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise ValueError("ALPACA_API_KEY not set")
        url = f"{_ALPACA_DATA_BASE}/v2/stocks/{ticker.upper()}/trades/latest"
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
            "User-Agent": "ICDEV-FathomDesk/1.0",
        }
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # nosec B310
                body = json.loads(resp.read().decode("utf-8"))
            return float(body["trade"]["p"])
        except HTTPError as exc:
            raise RuntimeError(f"Alpaca HTTP {exc.code}: {exc.reason}") from exc

    def current_quote(self, ticker: str) -> dict:
        """Return the current price for *ticker* via Alpaca → yfinance fallback.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.

        Returns:
            Dict with keys:
            - ``ticker`` (str): the requested symbol.
            - ``price`` (float | None): latest trade price; ``None`` when all
              sources are unavailable.
            - ``source`` (str): ``"alpaca"``, ``"yfinance"``, or
              ``"unavailable"``.
        """
        try:
            price = self._alpaca_latest_price(ticker)
            return {"ticker": ticker, "price": price, "source": "alpaca"}
        except Exception:
            pass

        if _yfinance is not None:
            try:
                t = _yfinance.Ticker(ticker)
                price = t.fast_info.last_price
                if price is not None:
                    return {"ticker": ticker, "price": float(price), "source": "yfinance"}
            except Exception:
                pass

        return {"ticker": ticker, "price": None, "source": "unavailable"}

    def options_chain(self, ticker: str) -> dict:
        """Return the options chain for *ticker* via OpenBB → fetch_chain() fallback.

        Tries OpenBB derivatives.options.chains first; falls back to
        tools.trading.options.chain.fetch_chain(). Returns the same shape as
        fetch_chain() with an extra ``source`` field.

        Args:
            ticker: Equity symbol, e.g. ``"SPY"``.

        Returns:
            Dict with keys: ticker, contracts, iv_rank, iv_percentile, source,
            plus all other keys from fetch_chain(). source is ``'openbb'`` or
            ``'chain_module'``.
        """
        sym = ticker.upper()

        if self._obb.available:
            try:
                obb_result = self._obb.get_options_chain(sym)
                if not obb_result.get("error") and obb_result.get("data"):
                    contracts = obb_result["data"]
                    iv_rank: object = None
                    iv_percentile: object = None
                    # Try to derive ATM IV and call compute_ivr for proper 52w stats
                    try:
                        from tools.trading.options.chain import compute_ivr
                        iv_vals = [
                            float(c.get("implied_volatility") or c.get("impliedVolatility") or 0)
                            for c in contracts
                            if (c.get("implied_volatility") or c.get("impliedVolatility"))
                        ]
                        if iv_vals:
                            atm_iv = sum(iv_vals) / len(iv_vals)
                            ivr = compute_ivr(sym, atm_iv)
                            iv_rank = ivr.get("iv_rank")
                            iv_percentile = ivr.get("iv_percentile")
                    except Exception:
                        pass
                    return {
                        "ticker": sym,
                        "contracts": contracts,
                        "iv_rank": iv_rank,
                        "iv_percentile": iv_percentile,
                        "source": "openbb",
                    }
            except Exception:
                pass

        # Fallback: chain_module (yfinance-backed, works in air-gap when offline)
        from tools.trading.options.chain import fetch_chain
        result = fetch_chain(sym)
        result["ticker"] = sym
        result.setdefault("contracts", [])
        result.setdefault("iv_rank", None)
        result.setdefault("iv_percentile", None)
        result["source"] = "chain_module"
        return result
