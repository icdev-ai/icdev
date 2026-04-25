#!/usr/bin/env python3
"""FathomDesk Broker Adapter — Alpaca limit/stop order submission.

Credentials from .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
Defaults to paper trading (https://paper-api.alpaca.markets).

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

    @property
    def is_paper(self) -> bool:
        return "paper" in self._base_url

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
    ) -> dict:
        """Submit a limit order. Returns the Alpaca order response dict."""
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
    ) -> dict:
        """Submit a stop order. Returns the Alpaca order response dict."""
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
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    adapter = BrokerAdapter()

    try:
        if args.test_paper:
            result = adapter.submit_limit_order("SPY", 1, 10.00, "buy", "day")
        elif args.limit_order:
            ticker, qty, price, side = args.limit_order
            result = adapter.submit_limit_order(ticker, float(qty), float(price), side, args.tif)
        elif args.stop_order:
            ticker, qty, price, side = args.stop_order
            result = adapter.submit_stop_order(ticker, float(qty), float(price), side)
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
