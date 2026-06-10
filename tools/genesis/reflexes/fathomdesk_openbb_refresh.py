# CUI // SP-CTI
"""Genesis Reflex — FathomDesk OpenBB Performance Refresh.

Periodically refreshes 1-year return data for FathomDesk tracked tickers.
Reads the active ticker set from ad_signals (up to MAX_TICKERS distinct tickers),
fetches 1-year price history for each via the OpenBB gateway, computes the
1y return, upserts results into ad_ticker_performance, and flags anomalous
returns using Z-score detection with optional LLM narrative analysis.

Gracefully skips when openbb is not installed (air-gap safe).

GREEN tier (read + upsert).  Air-gap safe.  COOLDOWN_HOURS=4.
"""
IMPLEMENTATION_STATUS = "full"

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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

# Replaces hardcoded LIMIT 50 — configurable via config["max_tickers"]
DEFAULT_MAX_TICKERS = 50
# Replaces hardcoded len(data) < 2 / len(closes) < 2
MIN_CLOSES_FOR_RETURN = 2
# Fallback Z-score threshold when LLM recommendation is unavailable
ANOMALY_ZSCORE_THRESHOLD_DEFAULT = 2.0
# Clamp bounds for LLM-recommended thresholds (prevents degenerate values)
ANOMALY_ZSCORE_MIN = 1.0
ANOMALY_ZSCORE_MAX = 4.0
# Default LLM model for anomaly detection and narrative (overridable via config["anomaly_model"])
DEFAULT_ANOMALY_MODEL = "claude-haiku-4-5-20251001"


def _get_ticker_limit(config: Dict[str, Any]) -> int:
    """Return max tickers from config, defaulting to DEFAULT_MAX_TICKERS."""
    return int(config.get("max_tickers", DEFAULT_MAX_TICKERS))


def _get_tickers(conn: Any, limit: int) -> List[str]:
    """Return up to ``limit`` distinct tickers from ad_signals."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM ad_signals LIMIT ?", (limit,)
        ).fetchall()
        return [
            r["ticker"] if hasattr(r, "keys") else r[0]
            for r in rows
            if (r["ticker"] if hasattr(r, "keys") else r[0])
        ]
    except Exception:
        return []


def _llm_recommend_threshold(n_tickers: int, mean: float, std: float, model: str) -> float:
    """Ask Claude Haiku to recommend a Z-score anomaly threshold for this distribution.

    Small samples (<10) need lower thresholds (1.5–2.0); large samples (>30) tolerate
    higher thresholds (2.0–3.0). Falls back to ANOMALY_ZSCORE_THRESHOLD_DEFAULT on error.
    Clamps output to [ANOMALY_ZSCORE_MIN, ANOMALY_ZSCORE_MAX].
    """
    try:
        from tools.llm.anthropic_provider import anthropic_sdk
        client = anthropic_sdk.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Return distribution for {n_tickers} stock tickers: "
                        f"mean={mean:.2f}%, std={std:.2f}%. "
                        "Recommend a Z-score threshold (float) for anomaly detection. "
                        "Small samples (<10) → 1.5–2.0; large (>30) → 2.0–3.0. "
                        "Reply with ONLY a single float, e.g.: 2.0"
                    ),
                }
            ],
        )
        raw = msg.content[0].text.strip().split()[0]
        return max(ANOMALY_ZSCORE_MIN, min(ANOMALY_ZSCORE_MAX, float(raw)))
    except Exception:
        return ANOMALY_ZSCORE_THRESHOLD_DEFAULT


def _compute_1y_return(data: List[Dict]) -> Optional[float]:
    """Return percentage 1y return from a list of OHLCV dicts, or None."""
    if not data or len(data) < MIN_CLOSES_FOR_RETURN:
        return None
    try:
        closes = []
        for row in data:
            val = row.get("close") or row.get("Close")
            if val is not None:
                closes.append(float(val))
        if len(closes) < MIN_CLOSES_FOR_RETURN:
            return None
        first, last = closes[0], closes[-1]
        if first == 0:
            return None
        return (last - first) / first * 100.0
    except Exception:
        return None


def _compute_return_anomalies(
    returns: Dict[str, float],
    model: str = DEFAULT_ANOMALY_MODEL,
) -> Tuple[Dict[str, bool], float, float, float]:
    """Detect anomalous 1y returns using an LLM-recommended Z-score threshold.

    Returns (is_anomaly_map, mean, std, threshold_used).
    Needs ≥3 tickers; below that all False with default threshold.
    """
    if len(returns) < 3:
        return {t: False for t in returns}, 0.0, 0.0, ANOMALY_ZSCORE_THRESHOLD_DEFAULT

    values = list(returns.values())
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5

    if std == 0:
        return {t: False for t in returns}, mean, std, ANOMALY_ZSCORE_THRESHOLD_DEFAULT

    threshold = _llm_recommend_threshold(len(returns), mean, std, model)
    anomaly_map = {
        ticker: abs((ret - mean) / std) > threshold
        for ticker, ret in returns.items()
    }
    return anomaly_map, mean, std, threshold


def _llm_analyze_anomalies(
    anomalous: Dict[str, float],
    model: str,
    threshold: float = ANOMALY_ZSCORE_THRESHOLD_DEFAULT,
) -> Optional[str]:
    """Generate a one-sentence LLM narrative for anomalous tickers.

    Returns None if LLM unavailable or no anomalies provided.
    """
    if not anomalous:
        return None
    try:
        from tools.llm.anthropic_provider import anthropic_sdk  # optional dependency

        client = anthropic_sdk.Anthropic()
        ticker_list = ", ".join(
            f"{t}={v:+.1f}%" for t, v in sorted(anomalous.items())
        )
        msg = client.messages.create(
            model=model,
            max_tokens=128,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"These tickers have statistically anomalous 1-year returns "
                        f"(|Z| > {threshold:.1f}): {ticker_list}. "
                        "In one concise sentence, characterize the pattern."
                    ),
                }
            ],
        )
        return msg.content[0].text.strip()
    except Exception:
        return None


def run(config: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Execute one performance-refresh cycle with anomaly detection.

    Returns a dict with keys ``refreshed`` (count upserted), ``anomalies``
    (count of flagged tickers), and ``source='openbb_gateway'``.
    """
    run_id = f"obb-perf-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    model = config.get("anomaly_model", DEFAULT_ANOMALY_MODEL)
    print(f"[openbb_refresh] run start  run_id={run_id}")

    if _gateway is None or not _gateway.available:
        print("[openbb_refresh] openbb gateway not available — skipping")
        return {
            "success": True,
            "refreshed": 0,
            "anomalies": 0,
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
            "anomalies": 0,
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
            "anomalies": 0,
            "source": "openbb_gateway",
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    ticker_limit = _get_ticker_limit(config)
    tickers = _get_tickers(conn, ticker_limit)
    print(f"[openbb_refresh] tickers found: {len(tickers)} (limit={ticker_limit})")

    # Phase 1 — fetch returns
    returns_map: Dict[str, float] = {}
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
        returns_map[ticker] = p1y

    # Phase 2 — anomaly detection with LLM-recommended Z-score threshold
    anomaly_map, ret_mean, ret_std, zscore_threshold = _compute_return_anomalies(
        returns_map, model
    )
    anomalous_returns = {t: v for t, v in returns_map.items() if anomaly_map.get(t)}
    print(
        f"[openbb_refresh] anomaly detection  mean={ret_mean:.2f}% "
        f"std={ret_std:.2f}%  threshold={zscore_threshold:.2f}  "
        f"flagged={len(anomalous_returns)}"
    )
    narrative = _llm_analyze_anomalies(anomalous_returns, model, zscore_threshold)
    if narrative:
        print(f"[openbb_refresh] anomaly narrative: {narrative}")

    # Phase 3 — upsert with anomaly flag
    ok_count = 0
    for ticker, p1y in returns_map.items():
        is_anomaly = 1 if anomaly_map.get(ticker) else 0
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ad_ticker_performance "
                "(ticker, p1y, is_anomaly, refreshed_at) VALUES (?, ?, ?, ?)",
                (ticker, p1y, is_anomaly, now.isoformat()),
            )
            conn.commit()
            ok_count += 1
            flag = " [ANOMALY]" if is_anomaly else ""
            print(f"  [openbb_refresh] {ticker} p1y={p1y:.2f}%{flag}")
        except Exception as exc:
            err_count += 1
            print(f"  [openbb_refresh] {ticker} db write error: {exc}")

    conn.close()
    print(
        f"[openbb_refresh] run complete  refreshed={ok_count} "
        f"anomalies={len(anomalous_returns)} err={err_count}"
    )
    return {
        "success": True,
        "refreshed": ok_count,
        "anomalies": len(anomalous_returns),
        "source": "openbb_gateway",
        "metric_value": float(ok_count),
        "details": {
            "run_id": run_id,
            "tickers_attempted": len(tickers),
            "ok": ok_count,
            "errors": err_count,
            "anomalous_tickers": list(anomalous_returns.keys()),
            "anomaly_narrative": narrative,
            "return_mean_pct": round(ret_mean, 2),
            "return_std_pct": round(ret_std, 2),
            "zscore_threshold": round(zscore_threshold, 2),
            "ran_at": now.isoformat(),
        },
    }
