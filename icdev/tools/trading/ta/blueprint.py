# CUI // SP-CTI
"""TA Patterns Blueprint — REST endpoint for chart pattern detection.

GET /api/ta/patterns
  ?ticker=SPY       — equity symbol (required)
  ?period=3mo       — lookback window (default "3mo")
  ?interval=1d      — bar interval (default "1d")
  Returns: {"ticker", "patterns": [...], "sr_levels": [...], "count": N}
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _load_ta_config() -> dict:
    try:
        import yaml
        cfg_path = BASE_DIR / "args" / "ta_config.yaml"
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    except Exception:
        pass
    return {}


def _fetch_bars(ticker: str, period: str = "3mo", interval: str = "1d") -> list[dict]:
    """Fetch OHLCV bars, normalising keys to h/l/o/c/v.

    Tries FathomDeskDataGateway first; falls back to an empty list if the
    dependency is unavailable (e.g. in CI without market-data credentials).
    """
    try:
        from tools.fathomdesk.data_gateway import FathomDeskDataGateway
        gw = FathomDeskDataGateway()
        raw = gw.historical_bars(ticker, period=period, interval=interval)
        bars: list[dict] = []
        for bar in raw:
            bars.append({
                "h": float(bar.get("h", bar.get("high", 0))),
                "l": float(bar.get("l", bar.get("low", 0))),
                "o": float(bar.get("o", bar.get("open", 0))),
                "c": float(bar.get("c", bar.get("close", 0))),
                "v": float(bar.get("v", bar.get("volume", 0))),
                "t": bar.get("t", bar.get("timestamp", bar.get("date", ""))),
            })
        return bars
    except Exception:
        return []


def create_ta_blueprint() -> Blueprint:
    """Create and return the TA Patterns Flask blueprint."""
    bp = Blueprint("ta_patterns", __name__)

    @bp.route("/api/ta/patterns", methods=["GET"])
    def ta_patterns():
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400

        period = request.args.get("period", "3mo").strip() or "3mo"
        interval = request.args.get("interval", "1d").strip() or "1d"

        cfg = _load_ta_config()
        cluster_pct = float(cfg.get("sr_proximity_pct", 0.5))

        bars = _fetch_bars(ticker, period=period, interval=interval)

        if not bars:
            return jsonify({
                "ticker": ticker,
                "bar_count": 0,
                "patterns": [],
                "sr_levels": [],
                "count": 0,
            })

        try:
            from tools.trading.ta.patterns import detect_patterns
            patterns = detect_patterns(bars)
        except Exception:
            patterns = []

        try:
            from tools.trading.ta.support_resistance import compute_sr
            sr_levels = compute_sr(bars, cluster_pct=cluster_pct)
        except Exception:
            sr_levels = []

        return jsonify({
            "ticker": ticker,
            "bar_count": len(bars),
            "patterns": patterns,
            "sr_levels": sr_levels,
            "count": len(patterns),
        })

    return bp
