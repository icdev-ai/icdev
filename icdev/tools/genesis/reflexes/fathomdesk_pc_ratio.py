# CUI // SP-CTI
"""Genesis Reflex — FathomDesk Put/Call Ratio History (fdmm-pcr-04).

Runs on a 24h cadence. Fetches the latest CBOE equity put/call ratio series
and appends a snapshot row to ad_pc_ratio_history, providing the backing store
that put_call_sentiment pillar modules read for z-score regime detection.

GREEN tier (read + append-only writes, no LLM in hot path). Air-gap safe.
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"


import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

# ── Constants ──────────────────────────────────────────────────────────────────

COOLDOWN_HOURS = 23  # guard against rapid re-fire within the 24h window
_REFLEX_KEY = "fathomdesk_pc_ratio"

_DDL = """CREATE TABLE IF NOT EXISTS ad_pc_ratio_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT    NOT NULL,
    equity_pc_ratio REAL,
    raw_series_json TEXT,
    source      TEXT    NOT NULL DEFAULT 'cboe',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
)"""


# ── Fetcher ────────────────────────────────────────────────────────────────────

def fetch_cboe_pc_ratio() -> Optional[List[float]]:
    """Return the equity put/call ratio series from the market_sentiment module.

    Returns None if data is unavailable (network down, air-gap, etc.).
    """
    try:
        from tools.trading.data.market_sentiment import get_equity_pc_series  # noqa: PLC0415
        return get_equity_pc_series()
    except Exception:  # nosec B110 — defensive: data source may be unavailable in air-gap
        pass
    return None


# ── Cooldown helpers ───────────────────────────────────────────────────────────

def _check_cooldown(conn: Any, key: str, hours: int) -> bool:
    """Return True if cooldown has expired (safe to emit)."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT value FROM ad_reflex_cooldowns WHERE key = %s AND value > %s",
            (key, cutoff),
        ).fetchone()
        return row is None
    except Exception:
        return True


def _mark_cooldown(conn: Any, key: str, now: datetime) -> None:
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_reflex_cooldowns (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO ad_reflex_cooldowns (key, value) VALUES (%s, %s)",
            (key, now.isoformat()),
        )
        conn.commit()
    except Exception:  # nosec B110 — cooldown failure is non-fatal
        pass


# ── Core logic ─────────────────────────────────────────────────────────────────

def _persist_snapshot(conn: Any, series: List[float], now: datetime) -> bool:
    """Append one row to ad_pc_ratio_history.  Returns True on success."""
    try:
        conn.execute(_DDL)
        conn.commit()
        latest = series[-1] if series else None
        conn.execute(
            "INSERT INTO ad_pc_ratio_history "
            "(fetched_at, equity_pc_ratio, raw_series_json, source, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                now.isoformat(),
                latest,
                json.dumps(series),
                "cboe",
                now.isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"  [pc_ratio] WARNING: persist failed: {exc}")
        try:
            conn.rollback()
        except Exception:  # nosec B110 — rollback failure is non-fatal
            pass
        return False


# ── Genesis contract ───────────────────────────────────────────────────────────

def run(config: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Execute one P/C ratio snapshot cycle.

    Called by the Genesis daemon every 24 hours.
    Returns a summary dict consumed by the daemon's success_metric gate.
    """
    run_id = f"pc-ratio-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    print(f"[pc_ratio] run start  run_id={run_id}")

    if get_connection is None:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": "get_connection not available", "run_id": run_id},
        }

    conn = None
    try:
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    if not _check_cooldown(conn, _REFLEX_KEY, COOLDOWN_HOURS):
        conn.close()
        print(f"[pc_ratio] skipped — within {COOLDOWN_HOURS}h cooldown")
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"skipped": True, "reason": "cooldown", "run_id": run_id},
        }

    series = fetch_cboe_pc_ratio()
    if not series:
        _mark_cooldown(conn, _REFLEX_KEY, now)
        conn.close()
        print("[pc_ratio] no data returned from fetcher")
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"run_id": run_id, "rows_written": 0, "reason": "no_data"},
        }

    ok = _persist_snapshot(conn, series, now)
    _mark_cooldown(conn, _REFLEX_KEY, now)
    conn.close()

    rows_written = 1 if ok else 0
    print(f"[pc_ratio] run complete  rows_written={rows_written}  latest_pc={series[-1]:.4f}")
    return {
        "success": ok,
        "metric_value": float(rows_written),
        "details": {
            "run_id": run_id,
            "rows_written": rows_written,
            "latest_equity_pc": series[-1] if series else None,
            "series_length": len(series),
            "ran_at": now.isoformat(),
        },
    }
