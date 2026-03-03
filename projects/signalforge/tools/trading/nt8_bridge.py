#!/usr/bin/env python3
"""SignalForge NT8 Bridge — gRPC client for NinjaTrader 8.

CUI // SP-CTI

Connects to NinjaTrader 8 via gRPC for:
- Market data streaming (1-min bars)
- Order execution (market orders with bracket TP/SL)
- Position tracking
- Health check / heartbeat

Usage:
    python tools/trading/nt8_bridge.py --status --json
    python tools/trading/nt8_bridge.py --connect --port 50051
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


def _load_config() -> dict:
    config_path = Path(__file__).parent.parent.parent / "args" / "trading_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "bridge": {
            "host": "localhost",
            "port": 50051,
            "heartbeat_interval_sec": 5,
            "reconnect_delay_sec": 5,
            "max_reconnect_attempts": 10,
        },
    }


@dataclass
class BridgeStatus:
    """NT8 bridge connection status."""
    connected: bool
    host: str
    port: int
    last_heartbeat: str
    reconnect_attempts: int
    uptime_sec: float
    last_error: str


@dataclass
class MarketBar:
    """Single OHLCV bar from NT8."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class OrderRequest:
    """Order submission to NT8."""
    instrument: str
    direction: str  # BUY or SELL
    quantity: int
    order_type: str  # MARKET, LIMIT, STOP
    price: float
    take_profit: float
    stop_loss: float


@dataclass
class OrderResponse:
    """Order response from NT8."""
    order_id: str
    status: str  # FILLED, PENDING, REJECTED
    fill_price: float
    fill_time: str
    message: str


class NT8Bridge:
    """gRPC bridge to NinjaTrader 8.

    This is a stub implementation that defines the interface.
    Full gRPC implementation requires the proto definitions
    and a running NT8 instance with the gRPC strategy loaded.
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            config = _load_config()
        bridge = config.get("bridge", {})
        self.host = bridge.get("host", "localhost")
        self.port = bridge.get("port", 50051)
        self.heartbeat_interval = bridge.get("heartbeat_interval_sec", 5)
        self.reconnect_delay = bridge.get("reconnect_delay_sec", 5)
        self.max_reconnects = bridge.get("max_reconnect_attempts", 10)

        self._connected = False
        self._channel = None
        self._reconnect_count = 0
        self._start_time = None
        self._last_heartbeat = ""
        self._last_error = ""

    def connect(self) -> bool:
        """Establish connection to NT8 gRPC server."""
        try:
            # Check if port is reachable
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((self.host, self.port))
            sock.close()

            if result == 0:
                self._connected = True
                self._start_time = time.time()
                self._last_heartbeat = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._reconnect_count = 0
                return True
            else:
                self._last_error = f"Port {self.port} not reachable"
                return False
        except Exception as e:
            self._last_error = str(e)
            return False

    def disconnect(self):
        """Disconnect from NT8."""
        self._connected = False
        self._channel = None

    def heartbeat(self) -> bool:
        """Send heartbeat to NT8."""
        if not self._connected:
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((self.host, self.port))
            sock.close()

            if result == 0:
                self._last_heartbeat = time.strftime("%Y-%m-%dT%H:%M:%S")
                return True
            else:
                self._connected = False
                self._last_error = "Heartbeat failed"
                return False
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            return False

    def reconnect(self) -> bool:
        """Attempt reconnection with exponential backoff."""
        for attempt in range(self.max_reconnects):
            self._reconnect_count = attempt + 1
            delay = min(self.reconnect_delay * (2 ** attempt), 60)

            if self.connect():
                return True

            time.sleep(delay)

        return False

    def get_status(self) -> BridgeStatus:
        """Get current bridge status."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return BridgeStatus(
            connected=self._connected,
            host=self.host,
            port=self.port,
            last_heartbeat=self._last_heartbeat,
            reconnect_attempts=self._reconnect_count,
            uptime_sec=round(uptime, 1),
            last_error=self._last_error,
        )

    def submit_order(self, order: OrderRequest) -> OrderResponse:
        """Submit order to NT8 via gRPC.

        Stub — full implementation requires gRPC proto service.
        """
        if not self._connected:
            return OrderResponse(
                order_id="",
                status="REJECTED",
                fill_price=0,
                fill_time="",
                message="Not connected to NT8",
            )

        # Placeholder — actual gRPC call would go here
        import uuid
        order_id = f"NT8-{uuid.uuid4().hex[:8]}"

        return OrderResponse(
            order_id=order_id,
            status="PENDING",
            fill_price=0,
            fill_time="",
            message="Order submitted (stub — gRPC not implemented)",
        )

    def get_latest_bar(self, instrument: str = "ES") -> Optional[MarketBar]:
        """Get the latest completed bar from NT8.

        Stub — full implementation requires gRPC streaming.
        """
        if not self._connected:
            return None

        # Placeholder
        return None

    def get_position(self, instrument: str = "ES") -> dict:
        """Get current position from NT8.

        Stub — full implementation requires gRPC call.
        """
        if not self._connected:
            return {"instrument": instrument, "quantity": 0, "avg_price": 0, "unrealized_pnl": 0}

        return {
            "instrument": instrument,
            "quantity": 0,
            "avg_price": 0,
            "unrealized_pnl": 0,
        }


def main():
    parser = argparse.ArgumentParser(description="SignalForge NT8 Bridge")
    parser.add_argument("--connect", action="store_true", help="Test connection to NT8")
    parser.add_argument("--status", action="store_true", help="Show bridge status")
    parser.add_argument("--host", default="localhost", help="NT8 host")
    parser.add_argument("--port", type=int, default=50051, help="NT8 gRPC port")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    config = _load_config()
    if args.host != "localhost":
        config.setdefault("bridge", {})["host"] = args.host
    if args.port != 50051:
        config.setdefault("bridge", {})["port"] = args.port

    bridge = NT8Bridge(config)

    if args.connect:
        connected = bridge.connect()
        status = bridge.get_status()

        if args.json:
            print(json.dumps({
                "status": "connected" if connected else "failed",
                "bridge": asdict(status),
            }, indent=2))
        else:
            if connected:
                print(f"Connected to NT8 at {status.host}:{status.port}")
            else:
                print(f"Failed to connect: {status.last_error}")

    elif args.status:
        status = bridge.get_status()
        if args.json:
            print(json.dumps({
                "status": "success",
                "bridge": asdict(status),
            }, indent=2))
        else:
            print("NT8 Bridge Status:")
            for k, v in asdict(status).items():
                print(f"  {k}: {v}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
