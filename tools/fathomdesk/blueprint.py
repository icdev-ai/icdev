# CUI // SP-CTI
"""FathomDesk API — OHLCV bars, trap events, backtest, and reflex endpoints.

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

POST /fathomdesk/backtest
  Body: {"ticker": "AAPL", "strategy_id": "covered_call", "start": "2023-01-01", "end": "2023-12-31"}

Returns: BacktestResult JSON + persists row to ad_backtest_runs (append-only).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

fathomdesk_api = Blueprint("fathomdesk_api", __name__)

from tools.logging.icdev_logger import get_logger
_logger = get_logger("icdev.fathomdesk.blueprint")

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


@fathomdesk_api.route("/api/macro/intelligence", methods=["GET"])
def api_macro_intelligence():
    """Return macro regime badges for the /analysis page panel.

    Returns:
        qeqt_phase: EXPANDING | NEUTRAL | CONTRACTING
        credit_stress: ACCELERATING | NEUTRAL | CONTRACTING
        rotation_signal: regime label (e.g. EXPANSION, STAGFLATION, GREEN, etc.)
        macro_score: 0-100 composite score
        summary: human-readable summary string
        fetched_at: ISO timestamp
    """
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        ctx = fetch_macro_context()
        return jsonify({
            "qeqt_phase": ctx.get("qeqt_phase", "NEUTRAL"),
            "credit_stress": ctx.get("credit_impulse", {}).get("label", "NEUTRAL"),
            "rotation_signal": ctx.get("regime", "UNKNOWN"),
            "macro_score": ctx.get("macro_score"),
            "summary": ctx.get("summary", ""),
            "fetched_at": ctx.get("fetched_at"),
        })
    except Exception as exc:
        # nav-plat-04: a data outage must NOT masquerade as a benign NEUTRAL
        # regime. Log it and return an explicit error state (HTTP 503) with
        # null badges so no consumer renders NEUTRAL for missing data.
        _logger.exception("macro/intelligence fetch failed: %s", exc)
        return jsonify({
            "status": "error",
            "detail": str(exc),
            "qeqt_phase": None,
            "credit_stress": None,
            "rotation_signal": None,
            "macro_score": None,
            "summary": "",
            "fetched_at": None,
            "error": str(exc),
        }), 503


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


@fathomdesk_api.route("/fathomdesk/backtest", methods=["POST"])
def run_backtest():
    """POST {ticker, strategy_id, start, end} → BacktestSession run + persist to ad_backtest_runs.

    Body fields (all required):
        ticker      — equity symbol, e.g. "AAPL"
        strategy_id — strategy tag from STRATEGY_TYPES, e.g. "covered_call"
        start       — window start date "YYYY-MM-DD"
        end         — window end date "YYYY-MM-DD"

    Returns BacktestResult JSON with run_id referencing the persisted ad_backtest_runs row.
    """
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    strategy_id = (body.get("strategy_id") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()

    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    if not strategy_id:
        return jsonify({"error": "strategy_id is required"}), 400
    if not start:
        return jsonify({"error": "start is required"}), 400
    if not end:
        return jsonify({"error": "end is required"}), 400

    try:
        from tools.fathomdesk.backtester import BacktestSession
        result = BacktestSession().run(ticker, strategy_id, start, end)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    trades = result.get("trades", [])
    profitable = sum(1 for t in trades if t.get("realized_pnl", 0) > 0)
    win_rate = round(profitable / len(trades), 4) if trades else 0.0

    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        placeholders = ", ".join([ph] * 12)
        try:
            conn.execute(
                f"INSERT INTO ad_backtest_runs "  # nosec B608
                f"(id, ticker, strategy_id, backtest_start, backtest_end, "
                f"sharpe_ratio, calmar_ratio, max_drawdown_pct, win_rate, "
                f"trade_count, triggered_by, created_at) "
                f"VALUES ({placeholders})",
                [
                    run_id,
                    ticker,
                    strategy_id,
                    start,
                    end,
                    result.get("sharpe_ratio"),
                    result.get("calmar_ratio"),
                    result.get("max_drawdown_pct"),
                    win_rate,
                    result.get("trade_count"),
                    "api",
                    created_at,
                ],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        return jsonify({"error": f"db write failed: {exc}"}), 500

    return jsonify({
        "run_id": run_id,
        "ticker": ticker,
        "strategy_id": strategy_id,
        "start": start,
        "end": end,
        "sharpe_ratio": result.get("sharpe_ratio"),
        "calmar_ratio": result.get("calmar_ratio"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "win_rate": win_rate,
        "realized_pnl": result.get("realized_pnl"),
        "trade_count": result.get("trade_count"),
        "open_lots": result.get("open_lots"),
        "trades": result.get("trades"),
        "created_at": created_at,
    })
