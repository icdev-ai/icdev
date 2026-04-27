# CUI // SP-CTI
"""FathomDesk API — OHLCV bars and trap event history endpoints.

Inline-route blueprint (routes are hardcoded with /fathomdesk/api/... or /api/... prefix).
Register via _mount_inline(fathomdesk_api) in tools/dashboard/api/__init__.py.

GET /api/bars
  ?ticker=SPY        — equity symbol (required)
  ?period=3mo        — lookback window (default "3mo")
  ?interval=1d       — bar interval (default "1d")

Returns: {"ticker": "SPY", "period": "3mo", "bars": [...], "count": N}

GET /fathomdesk/api/traps
  ?ticker=AAPL       — filter by ticker (optional)
  ?trap_type=bull_trap — filter by pattern column (optional)
  ?limit=50          — max rows to return (default 50, max 200)

Returns: {"traps": [...], "count": N}
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

fathomdesk_api = Blueprint("fathomdesk_api", __name__)

_MAX_LIMIT = 200


@fathomdesk_api.route("/api/bars", methods=["GET"])
def api_bars():
    """Return OHLCV bars for a ticker via FathomDeskDataGateway (OpenBB → yfinance fallback)."""
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    period = request.args.get("period", "3mo").strip() or "3mo"
    interval = request.args.get("interval", "1d").strip() or "1d"

    try:
        from tools.fathomdesk.data_gateway import FathomDeskDataGateway
        gw = FathomDeskDataGateway()
        bars = gw.historical_bars(ticker, period=period, interval=interval)
    except Exception as exc:
        return jsonify({"ticker": ticker, "period": period, "bars": [], "count": 0, "error": str(exc)}), 200

    return jsonify({"ticker": ticker, "period": period, "bars": bars, "count": len(bars)})


@fathomdesk_api.route("/fathomdesk/api/traps", methods=["GET"])
def list_traps():
    """Return trap events from ad_trap_events, optionally filtered."""
    ticker = request.args.get("ticker", "").strip().upper() or None
    trap_type = request.args.get("trap_type", "").strip().lower() or None
    try:
        limit = min(int(request.args.get("limit", 50)), _MAX_LIMIT)
    except (ValueError, TypeError):
        limit = 50

    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            # Build WHERE clauses dynamically
            conditions = []
            params: list = []
            if ticker:
                conditions.append(f"ticker = {ph}")
                params.append(ticker)
            if trap_type:
                conditions.append(f"pattern = {ph}")
                params.append(trap_type)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT id, ticker, pattern, broken_level, breakout_bar, "  # nosec B608
                f"reentry_bar, confidence, volume_ratio, bar_age, timeframe, "
                f"evidence_json, created_at "
                f"FROM ad_trap_events {where} "
                f"ORDER BY created_at DESC LIMIT {ph}",
                params,
            ).fetchall()
            traps = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify({"traps": traps, "count": len(traps)})
    except Exception as exc:
        return jsonify({"traps": [], "count": 0, "error": str(exc)}), 200


@fathomdesk_api.route("/fathomdesk/api/reflex-observations", methods=["GET"])
def list_reflex_observations():
    """Return recent reflex execution records from reflex_observations."""
    try:
        limit = min(int(request.args.get("limit", 50)), _MAX_LIMIT)
    except (ValueError, TypeError):
        limit = 50

    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            rows = conn.execute(
                f"SELECT id, reflex_name, started_at, duration_ms, status "  # nosec B608
                f"FROM reflex_observations "
                f"ORDER BY started_at DESC LIMIT {ph}",
                [limit],
            ).fetchall()
            observations = [
                {
                    "id": r["id"],
                    "reflex_name": r["reflex_name"],
                    "started_at": r["started_at"],
                    "duration_ms": r["duration_ms"],
                    "success": r["status"] == "done",
                }
                for r in rows
            ]
        finally:
            conn.close()
        return jsonify({"observations": observations})
    except Exception as exc:
        return jsonify({"observations": [], "error": str(exc)}), 200
