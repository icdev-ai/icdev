#!/usr/bin/env python3
"""FathomDesk OpenBB Gateway — optional market-data bridge via openbb-platform.

Wraps the openbb SDK with a graceful fallback when the package is not installed.
All callers should check `gateway.available` before invoking data methods.

Usage (CLI):
    python tools/fathomdesk/openbb_gateway.py --health
"""

import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_openbb = None
try:
    import openbb as _openbb  # type: ignore[import-untyped]
except ImportError:
    _openbb = None


def _period_to_date(period: str) -> date:
    """Convert a human period string to an absolute date.

    Supported tokens: ``Nd`` (days), ``Nw`` (weeks), ``Nm`` (months),
    ``Ny`` (years), and the special alias ``ytd`` (year-to-date).

    Args:
        period: A string like ``"7d"``, ``"4w"``, ``"6m"``, ``"2y"``, ``"ytd"``.

    Returns:
        A :class:`datetime.date` representing *today minus the period*.

    Raises:
        ValueError: If the period string is not recognised.
    """
    today = date.today()
    if not period:
        raise ValueError("period must not be empty")

    lower = period.strip().lower()

    if lower == "ytd":
        return today.replace(month=1, day=1)

    unit = lower[-1]
    try:
        n = int(lower[:-1])
    except ValueError:
        raise ValueError(f"Unrecognised period format: {period!r}")

    if unit == "d":
        return today - timedelta(days=n)
    if unit == "w":
        return today - timedelta(weeks=n)
    if unit == "m":
        # Approximate: 30 days per month
        return today - timedelta(days=n * 30)
    if unit == "y":
        return today.replace(year=today.year - n)

    raise ValueError(f"Unknown period unit {unit!r} in {period!r}")


class OpenBBGateway:
    """Thin adapter around the openbb SDK with availability guard.

    Attributes:
        _available: ``True`` when the openbb package is importable and the
            session was initialised successfully; ``False`` otherwise.
    """

    def __init__(self) -> None:
        if _openbb is not None:
            try:
                # openbb ≥4 exposes a top-level ``obb`` singleton; earlier
                # versions expose the package directly.  Either way, a
                # successful import is sufficient to declare availability.
                self._obb = _openbb
                self._available = True
            except Exception:
                self._available = False
                self._obb = None
        else:
            self._available = False
            self._obb = None

    @property
    def available(self) -> bool:
        """Return ``True`` if the openbb SDK is ready to serve requests."""
        return self._available

    def get_price(self, ticker: str, period: str) -> dict:
        """Fetch historical OHLCV price data for *ticker* over *period*.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.
            period: Period string accepted by :func:`_period_to_date`,
                e.g. ``"7d"``, ``"6m"``, ``"2y"``, ``"ytd"``.

        Returns:
            Dict with keys ``ticker``, ``indicator``, ``data``, ``source``,
            and optionally ``error``.
        """
        if not self._available:
            return {"ticker": ticker, "indicator": "price", "data": [], "source": "openbb", "error": "openbb not available"}
        try:
            start_date = _period_to_date(period)
            result = self._obb.equity.price.historical(symbol=ticker, start_date=str(start_date))
            data = result.to_df().to_dict(orient="records")
            return {"ticker": ticker, "indicator": "price", "data": data, "source": "openbb"}
        except Exception as exc:
            return {"ticker": ticker, "indicator": "price", "data": [], "source": "openbb", "error": str(exc)}

    def get_fundamentals(self, ticker: str) -> dict:
        """Fetch fundamental overview data for *ticker*.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.

        Returns:
            Dict with keys ``ticker``, ``indicator``, ``data``, ``source``,
            and optionally ``error``.
        """
        if not self._available:
            return {"ticker": ticker, "indicator": "fundamentals", "data": [], "source": "openbb", "error": "openbb not available"}
        try:
            result = self._obb.equity.fundamental.overview(symbol=ticker)
            data = result.to_df().to_dict(orient="records")
            return {"ticker": ticker, "indicator": "fundamentals", "data": data, "source": "openbb"}
        except Exception as exc:
            return {"ticker": ticker, "indicator": "fundamentals", "data": [], "source": "openbb", "error": str(exc)}

    def get_options_chain(self, ticker: str) -> dict:
        """Fetch the full options chain for *ticker*.

        Args:
            ticker: Equity symbol, e.g. ``"AAPL"``.

        Returns:
            Dict with keys ``ticker``, ``indicator``, ``data``, ``source``,
            and optionally ``error``.
        """
        if not self._available:
            return {"ticker": ticker, "indicator": "options_chain", "data": [], "source": "openbb", "error": "openbb not available"}
        try:
            result = self._obb.derivatives.options.chains(symbol=ticker)
            data = result.to_df().to_dict(orient="records")
            return {"ticker": ticker, "indicator": "options_chain", "data": data, "source": "openbb"}
        except Exception as exc:
            return {"ticker": ticker, "indicator": "options_chain", "data": [], "source": "openbb", "error": str(exc)}


# Module-level singleton — import and use directly:
#   from tools.fathomdesk.openbb_gateway import gateway
gateway = OpenBBGateway()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenBB Gateway health check")
    parser.add_argument("--health", action="store_true", help="Print availability status")
    args = parser.parse_args()

    if args.health:
        status = "available" if gateway.available else "unavailable (openbb not installed)"
        print(f"OpenBBGateway: {status}")
