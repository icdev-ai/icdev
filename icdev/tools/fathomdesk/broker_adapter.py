#!/usr/bin/env python3
"""FathomDesk Broker Adapter — Alpaca limit/stop order submission.

Credentials from .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
Trading mode from .env: ALPACA_TRADING_MODE=paper|live (default: paper).
Defaults to paper trading (https://paper-api.alpaca.markets).

SAFETY (nav-plat-02): paper vs. live is decided by the EXPLICIT
``ALPACA_TRADING_MODE`` env var — never by a substring of the base URL. If the
declared mode and the configured base URL disagree, construction raises loudly.
Any submit while in LIVE mode additionally requires the caller to pass
``live_confirmed=True`` as an interstitial confirmation; without it the submit
raises before any HTTP request is issued. Paper submits are unaffected.

Usage (CLI):
    python tools/fathomdesk/broker_adapter.py --limit-order SPY 1 10.00 buy
    python tools/fathomdesk/broker_adapter.py --stop-order SPY 1 400.00 sell
    python tools/fathomdesk/broker_adapter.py --test-paper
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

PAPER_URL = "https://paper-api.alpaca.markets"
ORDERS_PATH = "/v2/orders"
REQUEST_TIMEOUT = 30

# Explicit trading-mode vocabulary. Paper vs. live is decided by this env var,
# NEVER by inspecting the base URL for a "paper" substring — a misconfigured
# ALPACA_BASE_URL must not be able to silently promote paper config to live.
TRADING_MODE_ENV = "ALPACA_TRADING_MODE"
MODE_PAPER = "paper"
MODE_LIVE = "live"
VALID_MODES = (MODE_PAPER, MODE_LIVE)


class LiveOrderConfirmationError(RuntimeError):
    """Raised when a live-mode order is submitted without explicit confirmation."""


class BrokerConfigError(RuntimeError):
    """Raised when trading mode and base URL are inconsistent at construction."""


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


class BrokerAdapter:
    """Alpaca order adapter for FathomDesk live/paper trading."""

    def __init__(self) -> None:
        _load_env()
        self._api_key = os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        base = os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/")
        # Normalize: ALPACA_BASE_URL may include /v2 suffix; strip it so
        # ORDERS_PATH (/v2/orders) composes correctly with either convention.
        if base.endswith("/v2"):
            base = base[:-3]
        self._base_url = base

        # Explicit mode from env; default paper. Never inferred from the URL.
        mode = os.environ.get(TRADING_MODE_ENV, MODE_PAPER).strip().lower()
        if mode not in VALID_MODES:
            raise BrokerConfigError(
                f"{TRADING_MODE_ENV}={mode!r} is invalid; expected one of {VALID_MODES}"
            )
        self._mode = mode

        # Fail loudly when the declared mode and the configured base URL disagree.
        # We use the URL only as a cross-check against the explicit mode — it is
        # never the source of truth. The paper endpoint is identified by the
        # "paper" host substring.
        url_looks_paper = "paper" in self._base_url.lower()
        if self._mode == MODE_PAPER and not url_looks_paper:
            raise BrokerConfigError(
                f"{TRADING_MODE_ENV}=paper but ALPACA_BASE_URL={self._base_url!r} "
                "is not a paper endpoint. Refusing to run: fix the mode or the URL."
            )
        if self._mode == MODE_LIVE and url_looks_paper:
            raise BrokerConfigError(
                f"{TRADING_MODE_ENV}=live but ALPACA_BASE_URL={self._base_url!r} "
                "points at the paper endpoint. Refusing to run: fix the mode or the URL."
            )

    @property
    def is_paper(self) -> bool:
        """True when trading in paper mode. Derived from the explicit mode."""
        return self._mode == MODE_PAPER

    @property
    def is_live(self) -> bool:
        return self._mode == MODE_LIVE

    def _guard_live(self, live_confirmed: bool) -> None:
        """In live mode, require an explicit confirmation before any submit."""
        if self._mode == MODE_LIVE and not live_confirmed:
            raise LiveOrderConfirmationError(
                f"{TRADING_MODE_ENV}=live: refusing to submit a LIVE order without "
                "live_confirmed=True. Pass live_confirmed=True to confirm real-money "
                "execution."
            )

    def _preflight(self) -> None:
        if not self._api_key:
            raise ValueError("ALPACA_API_KEY not set — broker orders unavailable")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ICDEV-FathomDesk/1.0",
        }

    def _post_order(self, payload: dict) -> dict:
        url = f"{self._base_url}{ORDERS_PATH}"
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # nosec B310
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"Alpaca HTTP {exc.code}: {exc.reason} — {body}") from exc

    def submit_limit_order(
        self,
        ticker: str,
        qty: float,
        limit_price: float,
        side: str,
        time_in_force: str = "day",
        *,
        live_confirmed: bool = False,
    ) -> dict:
        """Submit a limit order. Returns the Alpaca order response dict.

        In live mode (``ALPACA_TRADING_MODE=live``) the caller MUST pass
        ``live_confirmed=True`` or a :class:`LiveOrderConfirmationError` is raised
        before any HTTP request. Paper mode ignores ``live_confirmed``.
        """
        self._guard_live(live_confirmed)
        self._preflight()
        payload = {
            "symbol": ticker.upper(),
            "qty": str(qty),
            "side": side.lower(),
            "type": "limit",
            "time_in_force": time_in_force.lower(),
            "limit_price": str(limit_price),
        }
        return self._post_order(payload)

    def submit_stop_order(
        self,
        ticker: str,
        qty: float,
        stop_price: float,
        side: str,
        *,
        live_confirmed: bool = False,
    ) -> dict:
        """Submit a stop order. Returns the Alpaca order response dict.

        In live mode (``ALPACA_TRADING_MODE=live``) the caller MUST pass
        ``live_confirmed=True`` or a :class:`LiveOrderConfirmationError` is raised
        before any HTTP request. Paper mode ignores ``live_confirmed``.
        """
        self._guard_live(live_confirmed)
        self._preflight()
        payload = {
            "symbol": ticker.upper(),
            "qty": str(qty),
            "side": side.lower(),
            "type": "stop",
            "time_in_force": "day",
            "stop_price": str(stop_price),
        }
        return self._post_order(payload)


def main() -> None:
    _load_env()

    parser = argparse.ArgumentParser(description="FathomDesk Broker Adapter CLI")
    parser.add_argument("--limit-order", nargs=4, metavar=("TICKER", "QTY", "PRICE", "SIDE"),
                        help="Submit a limit order")
    parser.add_argument("--stop-order", nargs=4, metavar=("TICKER", "QTY", "PRICE", "SIDE"),
                        help="Submit a stop order")
    parser.add_argument("--tif", default="day", help="Time-in-force for limit orders (default: day)")
    parser.add_argument("--test-paper", action="store_true",
                        help="Place 1 share SPY @ $10.00 buy limit on paper account")
    parser.add_argument("--confirm-live", action="store_true",
                        help="Required to submit orders when ALPACA_TRADING_MODE=live")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    adapter = BrokerAdapter()
    live_confirmed = bool(args.confirm_live)

    try:
        if args.test_paper:
            result = adapter.submit_limit_order(
                "SPY", 1, 10.00, "buy", "day", live_confirmed=live_confirmed)
        elif args.limit_order:
            ticker, qty, price, side = args.limit_order
            result = adapter.submit_limit_order(
                ticker, float(qty), float(price), side, args.tif,
                live_confirmed=live_confirmed)
        elif args.stop_order:
            ticker, qty, price, side = args.stop_order
            result = adapter.submit_stop_order(
                ticker, float(qty), float(price), side, live_confirmed=live_confirmed)
        else:
            parser.print_help()
            sys.exit(0)

        out = {"status": "ok", "paper": adapter.is_paper, "order": result}
    except Exception as exc:
        out = {"status": "error", "error": str(exc)}

    print(json.dumps(out, indent=2, default=str))
    if out["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
