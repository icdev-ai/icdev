#!/usr/bin/env python3
from __future__ import annotations
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
import time
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
from tools.fathomdesk.constants import RATE_LIMIT_RETRY_MAX  # noqa: E402

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


def _is_intl_ticker(symbol: str) -> bool:
    """Return True if *symbol* is exchange-qualified (e.g. CNC.TO, 7203.T, 0700.HK).

    Alpaca covers US equities only; exchange-qualified tickers must be routed
    to yfinance, which natively understands the TICKER.EXCHANGE suffix format.
    """
    return "." in symbol


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

    def historical_bars(
        self,
        ticker: str,
        period: str = "3mo",
        interval: str = "1d",
        as_of_date: str | None = None,
    ) -> list[dict]:
        """Return OHLCV bars for *ticker* via OpenBB → yfinance fallback.

        Args:
            ticker: Equity symbol, e.g. ``"SPY"``.
            period: Lookback window accepted by yfinance (``"1mo"``, ``"3mo"``,
                ``"6mo"``, ``"1y"``, etc.).  OpenBB uses a derived start date.
            interval: Bar interval accepted by yfinance (``"1d"``, ``"1h"``,
                etc.).  OpenBB uses its own default granularity.
            as_of_date: Optional end-date anchor (``"YYYY-MM-DD"``).  When set,
                yfinance fetches bars ending on this date rather than today.

        Returns:
            List of bar dicts with keys ``date``, ``open``, ``high``, ``low``,
            ``close``, ``volume``, and ``source``.  Empty list on total failure.
        """
        sym = ticker.upper()

        # --- OpenBB path (US equities only; skip for exchange-qualified intl tickers) ---
        if not _is_intl_ticker(sym) and self._obb.available:
            try:
                result = self._obb.get_price(sym, period)
                raw = result.get("data", [])
                if raw:
                    bars: list[dict] = []
                    for row in raw:
                        # Normalise key names — OpenBB may use Title-case or full names
                        date_val = (
                            row.get("date")
                            or row.get("Date")
                            or row.get("timestamp")
                            or ""
                        )
                        bars.append(
                            {
                                "date": str(date_val),
                                "open": float(row.get("open") or row.get("Open") or 0),
                                "high": float(row.get("high") or row.get("High") or 0),
                                "low": float(row.get("low") or row.get("Low") or 0),
                                "close": float(row.get("close") or row.get("Close") or row.get("adj_close") or 0),
                                "volume": int(row.get("volume") or row.get("Volume") or 0),
                                "source": "openbb",
                            }
                        )
                    if bars:
                        return bars
            except Exception:
                pass

        # --- yfinance fallback ---
        if _yfinance is not None:
            try:
                t = _yfinance.Ticker(sym)
                yf_kwargs: dict = {"period": period, "interval": interval}
                if as_of_date is not None:
                    yf_kwargs["end"] = as_of_date
                hist = t.history(**yf_kwargs)
                if not hist.empty:
                    bars = []
                    for ts, row in hist.iterrows():
                        bars.append(
                            {
                                "date": str(ts.date() if hasattr(ts, "date") else ts),
                                "open": float(row.get("Open") or 0),
                                "high": float(row.get("High") or 0),
                                "low": float(row.get("Low") or 0),
                                "close": float(row.get("Close") or 0),
                                "volume": int(row.get("Volume") or 0),
                                "source": "yfinance",
                            }
                        )
                    return bars
            except Exception:
                pass

        return []

    def fundamentals(self, ticker: str, as_of_date: str | None = None) -> dict:
        """Return fundamental overview for *ticker* via OpenBB.

        Calls :meth:`OpenBBGateway.get_fundamentals` and returns the first
        data record merged with ``ticker`` and ``source``.  Missing fields,
        including ``sector``, default to ``None``.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.
            as_of_date: Optional date anchor (``"YYYY-MM-DD"``).  Accepted for
                API consistency with :meth:`historical_bars`; passed through to
                OpenBB when the underlying provider supports it.

        Returns:
            Dict of fundamental fields with ``ticker`` and ``source`` set.
            ``sector`` is ``None`` when absent in source data.
        """
        sym = ticker.upper()
        raw = self._obb.get_fundamentals(sym)
        records = raw.get("data") or []
        record = dict(records[0]) if records else {}
        result: dict = {"ticker": sym, "source": raw.get("source", "openbb")}
        result.update(record)
        result.setdefault("sector", None)
        return result

    def fundamentals_with_nav(self, ticker: str) -> dict:
        """Return fundamentals augmented with explicit NAV data for Tier 1 sectors.

        For REITs, banks, and insurers (Tier 1), attempts to fetch NAV per share
        via yfinance ``info`` fields (``bookValue``, ``navPerShare``).  For all
        other sectors the result is identical to :meth:`fundamentals` with
        ``nav_per_share=None`` and ``sector_tier`` computed from the sector name.

        Args:
            ticker: Equity symbol, e.g. ``"O"`` (Realty Income REIT).

        Returns:
            Fundamentals dict augmented with:
            - ``nav_per_share`` (float | None)
            - ``sector_tier`` (int: 1, 2, or 3)
        """
        from tools.trading.analysts.quality import nav_tier_for_sector

        result = self.fundamentals(ticker)
        sector = result.get("sector") or ""
        tier   = nav_tier_for_sector(sector)

        result["sector_tier"] = tier
        result["nav_per_share"] = None

        if tier == 1 and _yfinance is not None:
            try:
                t    = _yfinance.Ticker(ticker.upper())
                info = t.info or {}
                # yfinance exposes navPerShare for ETFs/CEFs; bookValue for stocks
                nav = info.get("navPerShare") or info.get("bookValue")
                if nav is not None:
                    result["nav_per_share"] = float(nav)
            except Exception:
                pass

        return result

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

    def fetch_news(self, ticker: str, as_of_date: str | None = None) -> list[dict]:
        """Return recent news articles for *ticker* via yfinance with retry/backoff.

        Retries up to ``RATE_LIMIT_RETRY_MAX`` times on transient errors (e.g.
        HTTP 429 rate-limit) using exponential backoff (2^attempt seconds).

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.
            as_of_date: Optional date anchor (``"YYYY-MM-DD"``).  Accepted for
                API consistency with :meth:`historical_bars`; yfinance does not
                expose a server-side news date filter so callers must filter the
                returned list by ``providerPublishTime`` if needed.

        Returns:
            List of news article dicts from yfinance; empty list when yfinance
            is unavailable or all retry attempts are exhausted.
        """
        if _yfinance is None:
            return []

        # Exchange-qualified tickers (CNC.TO, 7203.T, 0700.HK) route directly to
        # yfinance — Alpaca covers US equities only. yfinance accepts the full
        # TICKER.EXCHANGE suffix as-is, so no symbol transformation is needed.
        sym = ticker.upper()
        for attempt in range(RATE_LIMIT_RETRY_MAX):
            try:
                t = _yfinance.Ticker(sym)
                articles = t.news or []
                return list(articles)
            except Exception:
                if attempt < RATE_LIMIT_RETRY_MAX - 1:
                    time.sleep(2 ** attempt)

        return []

    def fetch_defi_yield(self, protocol: str) -> dict:
        """Return DeFi yield and TVL data for *protocol* via public APIs.

        Sources:
        - DefiLlama APY API (https://yields.llama.fi/pools) for pool TVL and APY
        - Coingecko free tier for governance/native token price

        Target protocols: Aave, Compound, Uniswap, Jupiter.  Unknown protocols
        are attempted with a lowercase slug match against DefiLlama project names.

        Args:
            protocol: Protocol name, e.g. ``"aave"``, ``"compound"``,
                ``"uniswap"``, or ``"jupiter"``.

        Returns:
            Dict with keys: ``protocol``, ``tvl_usd``, ``apy_mean``,
            ``pool_count``, ``token_price_usd``, ``source``.
            Empty dict on any network or parse failure (graceful degradation).
        """
        _PROTOCOL_META: dict[str, dict] = {
            "aave":     {"llama_slug": "aave-v3",   "coingecko_id": "aave"},
            "compound": {"llama_slug": "compound-v3", "coingecko_id": "compound-governance-token"},
            "uniswap":  {"llama_slug": "uniswap-v3",  "coingecko_id": "uniswap"},
            "jupiter":  {"llama_slug": "jupiter",      "coingecko_id": "jupiter-exchange-solana"},
        }
        key = protocol.lower().strip()
        meta = _PROTOCOL_META.get(key, {"llama_slug": key, "coingecko_id": key})
        llama_slug = meta["llama_slug"]
        coingecko_id = meta["coingecko_id"]

        # --- DefiLlama pools ---
        tvl_usd = 0.0
        apy_mean = 0.0
        pool_count = 0
        try:
            req = Request(
                "https://yields.llama.fi/pools",
                headers={"User-Agent": "ICDEV-FathomDesk/1.0", "Accept": "application/json"},
            )
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))
            pools = [
                p for p in (payload.get("data") or [])
                if (p.get("project") or "").lower() == llama_slug
            ]
            if pools:
                pool_count = len(pools)
                tvl_usd = sum(float(p.get("tvlUsd") or 0) for p in pools)
                weighted = sum(
                    float(p.get("apy") or 0) * float(p.get("tvlUsd") or 0)
                    for p in pools
                )
                apy_mean = weighted / tvl_usd if tvl_usd else 0.0
        except Exception:
            return {}

        # --- Coingecko token price (optional; failure doesn't abort result) ---
        token_price: float | None = None
        try:
            cg_url = (
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={coingecko_id}&vs_currencies=usd"
            )
            req = Request(
                cg_url,
                headers={"User-Agent": "ICDEV-FathomDesk/1.0", "Accept": "application/json"},
            )
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # nosec B310
                cg_data = json.loads(resp.read().decode("utf-8"))
            raw_price = (cg_data.get(coingecko_id) or {}).get("usd")
            token_price = float(raw_price) if raw_price else None
        except Exception:
            pass

        return {
            "protocol": key,
            "tvl_usd": tvl_usd,
            "apy_mean": round(apy_mean, 4),
            "pool_count": pool_count,
            "token_price_usd": token_price,
            "source": "defillama+coingecko",
        }
