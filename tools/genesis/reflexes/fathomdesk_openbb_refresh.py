# CUI // SP-CTI
"""Genesis Reflex — FathomDesk OpenBB Refresh.

Periodically refreshes FathomDesk market data via the OpenBB gateway.
Reads the active ticker universe from ad_universe, fetches fresh price
snapshots for each ticker, and writes summary rows to ad_openbb_refresh_log.

Gracefully skips when openbb is not installed (air-gap safe).

GREEN tier (read + append-only writes).  Air-gap safe.
"""

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

_PERIOD = "7d"


def _get_universe(conn: Any) -> List[str]:
    """Return active tickers from ad_universe, or [] if table absent."""
    try:
        rows = conn.execute(
            "SELECT ticker FROM ad_universe WHERE active = 1 ORDER BY ticker"
        ).fetchall()
        return [
            r["ticker"] if hasattr(r, "keys") else r[0]
            for r in rows
            if (r["ticker"] if hasattr(r, "keys") else r[0])
        ]
    except Exception:
        return []


def _ensure_log_table(conn: Any) -> None:
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_openbb_refresh_log (
                id         TEXT PRIMARY KEY,
                run_id     TEXT NOT NULL,
                ticker     TEXT NOT NULL,
                status     TEXT NOT NULL,
                error      TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    except Exception:
        pass


def _log_row(conn: Any, run_id: str, ticker: str, status: str, error: str | None, now: datetime) -> None:
    try:
        conn.execute(
            "INSERT INTO ad_openbb_refresh_log (id, run_id, ticker, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, run_id, ticker, status, error, now.isoformat()),
        )
        conn.commit()
    except Exception:
        pass


def run(config: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Execute one OpenBB refresh cycle.

    Returns a dict with key ``refreshed`` (count of tickers attempted).
    """
    run_id = f"obb-refresh-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    print(f"[openbb_refresh] run start  run_id={run_id}")

    # Gateway unavailable — return refreshed=0, not an error
    if _gateway is None or not _gateway.available:
        print("[openbb_refresh] openbb gateway not available — skipping data fetch")
        return {
            "success": True,
            "refreshed": 0,
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
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    _ensure_log_table(conn)
    tickers = _get_universe(conn)

    ok_count = 0
    err_count = 0
    for ticker in tickers:
        result = _gateway.get_price(ticker, _PERIOD)
        if result.get("error"):
            _log_row(conn, run_id, ticker, "error", result["error"], now)
            err_count += 1
            print(f"  [openbb_refresh] {ticker} error: {result['error']}")
        else:
            _log_row(conn, run_id, ticker, "ok", None, now)
            ok_count += 1
            print(f"  [openbb_refresh] {ticker} ok ({len(result.get('data', []))} rows)")

    conn.close()
    refreshed = ok_count + err_count
    print(f"[openbb_refresh] run complete  refreshed={refreshed} ok={ok_count} err={err_count}")
    return {
        "success": True,
        "refreshed": refreshed,
        "metric_value": float(ok_count),
        "details": {
            "run_id": run_id,
            "tickers_attempted": refreshed,
            "ok": ok_count,
            "errors": err_count,
            "ran_at": now.isoformat(),
        },
    }
