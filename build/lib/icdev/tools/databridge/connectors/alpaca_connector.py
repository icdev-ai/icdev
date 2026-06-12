#!/usr/bin/env python3
"""Alpaca Markets DataBridge Connector — equities + crypto trading API.

Connects to Alpaca Markets API for:
  - Account info and buying power
  - Position management (current holdings)
  - Order placement and tracking (paper + live)
  - Market data: equity and crypto OHLCV bars, latest quotes
  - Asset discovery

Credentials from .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("alpaca")
    conn.connect({"api_key": "...", "secret_key": "...", "base_url": "..."})
    result = conn.read(ConnectorRequest(table_name="positions"))

Usage (standalone CLI):
    python tools/databridge/connectors/alpaca_connector.py --health --json
    python tools/databridge/connectors/alpaca_connector.py --table account --json
    python tools/databridge/connectors/alpaca_connector.py --table positions --json
    python tools/databridge/connectors/alpaca_connector.py --bars AAPL --json
    python tools/databridge/connectors/alpaca_connector.py --crypto-bars BTC/USD --json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector  # noqa: E402
from tools.databridge.registry import register_connector  # noqa: E402

# Alpaca API endpoints
ALPACA_TRADING_ENDPOINTS = {
    "account": "/v2/account",
    "positions": "/v2/positions",
    "orders": "/v2/orders",
    "assets": "/v2/assets",
    "clock": "/v2/clock",
    "calendar": "/v2/calendar",
}

# Market data uses a different base URL
ALPACA_DATA_BASE = "https://data.alpaca.markets"

DEFAULT_PAPER_URL = "https://paper-api.alpaca.markets"


@register_connector
class AlpacaConnector(SaaSBaseConnector):
    """Alpaca Markets connector for trading + market data.

    Supports paper and live trading via ALPACA_BASE_URL.
    Market data always goes through data.alpaca.markets.
    """

    _connector_name = "alpaca"
    _default_base_url = DEFAULT_PAPER_URL
    _endpoints = {
        **ALPACA_TRADING_ENDPOINTS,
        # Market data endpoints (handled specially via _data_base_url)
        "bars_stock": "/v2/stocks/{symbol}/bars",
        "bars_crypto": "/v1beta3/crypto/us/bars",
        "quote_latest": "/v2/stocks/{symbol}/quotes/latest",
        "trades_latest": "/v2/stocks/{symbol}/trades/latest",
    }

    # Data endpoints that use the market data base URL
    _DATA_ENDPOINTS = {"bars_stock", "bars_crypto", "quote_latest", "trades_latest"}

    def __init__(self) -> None:
        super().__init__()
        self._data_base_url = ALPACA_DATA_BASE

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,  # Order placement
            supports_schema_inference=True,
            max_batch_size=1000,
            supported_formats=["json"],
        )

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Alpaca uses APCA-API-KEY-ID + APCA-API-SECRET-KEY headers."""
        api_key = config.get("api_key", os.environ.get("ALPACA_API_KEY", ""))
        secret_key = config.get("secret_key", os.environ.get("ALPACA_SECRET_KEY", ""))
        return {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect with .env fallbacks for credentials."""
        # Apply .env defaults
        if "base_url" not in config:
            config["base_url"] = os.environ.get("ALPACA_BASE_URL", DEFAULT_PAPER_URL)
        if "api_key" not in config:
            config["api_key"] = os.environ.get("ALPACA_API_KEY", "")
        if "secret_key" not in config:
            config["secret_key"] = os.environ.get("ALPACA_SECRET_KEY", "")

        return super().connect(config)

    def health_check(self) -> Dict[str, Any]:
        """Check Alpaca connectivity via /v2/account."""
        try:
            url = f"{self._base_url}/v2/account"
            acct = self._http_get(url)
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "base_url": self._base_url,
                "account_id": acct.get("id", ""),
                "account_status": acct.get("status", ""),
                "buying_power": acct.get("buying_power", "0"),
                "paper_trading": "paper" in self._base_url,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "connector": self._connector_name,
            }

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read from trading or market data endpoints."""
        table = request.table_name or request.query

        # Handle market data endpoints with different base URL
        if table in self._DATA_ENDPOINTS:
            return self._read_market_data(table, request)

        return super().read(request)

    def _read_market_data(
        self,
        table: str,
        request: ConnectorRequest,
    ) -> ConnectorResponse:
        """Fetch market data from data.alpaca.markets."""
        t0 = time.time()
        filters = dict(request.filters) if request.filters else {}
        symbol = filters.pop("symbol", "AAPL")

        path = self._endpoints[table]

        if table == "bars_stock":
            path = path.replace("{symbol}", symbol)
            url = f"{self._data_base_url}{path}"
            params = {"timeframe": filters.get("timeframe", "1Day")}
            if "start" in filters:
                params["start"] = filters["start"]
            if "end" in filters:
                params["end"] = filters["end"]
            params["limit"] = str(request.limit or 100)

        elif table == "bars_crypto":
            url = f"{self._data_base_url}{path}"
            params = {
                "symbols": symbol,
                "timeframe": filters.get("timeframe", "1Hour"),
            }
            if "start" in filters:
                params["start"] = filters["start"]
            params["limit"] = str(request.limit or 100)

        elif table in ("quote_latest", "trades_latest"):
            path = path.replace("{symbol}", symbol)
            url = f"{self._data_base_url}{path}"
            params = {}

        else:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown data table: {table}"],
            )

        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

        try:
            data = self._http_get(url)
            rows = self._extract_market_rows(data, table, symbol)
            duration = int((time.time() - t0) * 1000)
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows) if isinstance(rows, list) else 1,
                duration_ms=duration,
                metadata={"endpoint": table, "symbol": symbol},
            )
        except HTTPError as exc:
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}"],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def _extract_market_rows(self, data: Any, table: str, symbol: str) -> list:
        """Extract bar/quote data from Alpaca market data response."""
        if table == "bars_stock":
            return data.get("bars", []) if isinstance(data, dict) else data
        elif table == "bars_crypto":
            # Crypto bars are nested: {"bars": {"BTC/USD": [...]}}
            if isinstance(data, dict) and "bars" in data:
                bars = data["bars"]
                return bars.get(symbol, []) if isinstance(bars, dict) else bars
            return data
        elif table in ("quote_latest", "trades_latest"):
            return [data] if isinstance(data, dict) else data
        return data if isinstance(data, list) else [data]

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """Place an order via POST /v2/orders."""
        table = request.table_name or request.query
        if table != "orders":
            return ConnectorResponse(
                status="error",
                errors=[f"Write only supported for 'orders', got '{table}'"],
            )

        t0 = time.time()
        url = f"{self._base_url}/v2/orders"
        try:
            resp = self._http_post(url, data)
            return ConnectorResponse(
                status="ok",
                data=resp,
                row_count=1,
                duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}", body],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def cancel_order(self, order_id: str) -> ConnectorResponse:
        """Cancel an order via DELETE /v2/orders/{id}."""
        t0 = time.time()
        url = f"{self._base_url}/v2/orders/{order_id}"
        try:
            self._http_delete(url)
            return ConnectorResponse(
                status="ok",
                data={"cancelled": order_id},
                duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {exc.code}: {exc.reason}"],
                duration_ms=int((time.time() - t0) * 1000),
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_env():
    """Load .env if python-dotenv available."""
    try:
        from dotenv import load_dotenv

        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


def main():
    _load_env()

    parser = argparse.ArgumentParser(description="Alpaca DataBridge Connector CLI")
    parser.add_argument("--health", action="store_true", help="Check connectivity")
    parser.add_argument("--table", help="Read endpoint (account, positions, orders)")
    parser.add_argument("--bars", metavar="SYMBOL", help="Fetch equity OHLCV bars")
    parser.add_argument("--crypto-bars", metavar="SYMBOL", help="Fetch crypto OHLCV bars")
    parser.add_argument("--timeframe", default="1Day", help="Bar timeframe (default: 1Day)")
    parser.add_argument("--limit", type=int, default=10, help="Max rows (default: 10)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    conn = AlpacaConnector()
    if not conn.connect({}):
        result = {"status": "error", "error": "Failed to connect to Alpaca"}
        print(json.dumps(result, indent=2) if args.json else f"ERROR: {result['error']}")
        sys.exit(1)

    if args.health:
        result = conn.health_check()
    elif args.table:
        result = conn.read(ConnectorRequest(table_name=args.table, limit=args.limit))
        result = {
            "status": result.status,
            "data": result.data,
            "row_count": result.row_count,
            "errors": result.errors,
            "duration_ms": result.duration_ms,
        }
    elif args.bars:
        result = conn.read(
            ConnectorRequest(
                table_name="bars_stock",
                limit=args.limit,
                filters={"symbol": args.bars, "timeframe": args.timeframe},
            )
        )
        result = {
            "status": result.status,
            "data": result.data,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
        }
    elif args.crypto_bars:
        result = conn.read(
            ConnectorRequest(
                table_name="bars_crypto",
                limit=args.limit,
                filters={"symbol": args.crypto_bars, "timeframe": args.timeframe},
            )
        )
        result = {
            "status": result.status,
            "data": result.data,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
        }
    else:
        result = {"tables": conn.list_tables()}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
