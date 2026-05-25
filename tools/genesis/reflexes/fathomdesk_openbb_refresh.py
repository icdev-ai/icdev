# CUI // SP-CTI
"""Genesis Reflex — FathomDesk OpenBB Performance Refresh.

Periodically refreshes 1-year return data for FathomDesk tracked tickers.
Reads the active ticker set from ad_signals (up to 50 distinct tickers),
fetches 1-year price history for each via the OpenBB gateway, computes the
1y return, and upserts results into ad_ticker_performance.

Gracefully skips when openbb is not installed (air-gap safe).

GREEN tier (read + upsert).  Air-gap safe.  COOLDOWN_HOURS=4.
"""
IMPLEMENTATION_STATUS = "full"

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import sys

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

try:
    from tools.fathomdesk.openbb_gateway import gateway as _gateway
except Exception:
    _gateway = None  # type: ignore[assignment]

COOLDOWN_HOURS = 4


def _get_tickers(conn: Any) -> List[str]:
    """Return up to 50 distinct tickers from ad_signals."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM ad_signals LIMIT 50"
        ).fetchall()
        return [
            r["ticker"] if hasattr(r, "keys") else r[0]
            for r in rows
            if (r["ticker"] if hasattr(r, "keys") else r[0])
        ]
    except Exception:
        return []


def _compute_1y_return(data: List[Dict]) -> float | None:
    """Return percentage 1y return from a list of OHLCV dicts, or None."""
    if not data or len(data) < 2:
        return None
    try:
        closes = []
        for row in data:
            val = row.get("close") or row.get("Close")
            if val is not None:
                closes.append(float(val))
        if len(closes) < 2:
            return None
        first, last = closes[0], closes[-1]
        if first == 0:
            return None
        return (last - first) / first * 100.0
    except Exception:
        return None


def run(config: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Execute one performance-refresh cycle.

    Returns a dict with keys ``refreshed`` (count upserted) and
    ``source='openbb_gateway'``.
    """
    run_id = f"obb-perf-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    print(f"[openbb_refresh] run start  run_id={run_id}")

    if _gateway is None or not _gateway.available:
        print("[openbb_refresh] openbb gateway not available — skipping")
        return {
            "success": True,
            "refreshed": 0,
            "source": "openbb_gateway",
            "metric_value": 0.0,
            "details": {
                "run_id": run_id,
                "skipped": True,
                "reason": "openbb_not_available",
                "ran_at": now.isoformat(),
            },
        }

    if get_connection is None:
        return {
            "success": False,
            "refreshed": 0,
            "source": "openbb_gateway",
            "metric_value": 0.0,
            "details": {"error": "get_connection not available", "run_id": run_id},
        }

    conn = None
    try:
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "refreshed": 0,
            "source": "openbb_gateway",
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    tickers = _get_tickers(conn)
    print(f"[openbb_refresh] tickers found: {len(tickers)}")

    ok_count = 0
    err_count = 0
    for ticker in tickers:
        result = _gateway.get_price(ticker, "1y")
        if result.get("error"):
            err_count += 1
            print(f"  [openbb_refresh] {ticker} error: {result['error']}")
            continue

        p1y = _compute_1y_return(result.get("data", []))
        if p1y is None:
            err_count += 1
            print(f"  [openbb_refresh] {ticker} insufficient price data")
            continue

        try:
            conn.execute(
                "INSERT OR REPLACE INTO ad_ticker_performance "
                "(ticker, p1y, refreshed_at) VALUES (?, ?, ?)",
                (ticker, p1y, now.isoformat()),
            )
            conn.commit()
            ok_count += 1
            print(f"  [openbb_refresh] {ticker} p1y={p1y:.2f}%")
        except Exception as exc:
            err_count += 1
            print(f"  [openbb_refresh] {ticker} db write error: {exc}")

    conn.close()
    refreshed = ok_count
    print(f"[openbb_refresh] run complete  refreshed={refreshed} ok={ok_count} err={err_count}")
    return {
        "success": True,
        "refreshed": refreshed,
        "source": "openbb_gateway",
        "metric_value": float(ok_count),
        "details": {
            "run_id": run_id,
            "tickers_attempted": ok_count + err_count,
            "ok": ok_count,
            "errors": err_count,
            "ran_at": now.isoformat(),
        },
    }