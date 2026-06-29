# CUI // SP-CTI
"""FathomDesk Phase 7.9 — Options coach: portfolio-level Greek exposure alerts.

check_greek_exposure(user_id) is called by the position coach daemon every
scan cycle. Alerts are written to ad_coach_alerts with a 4-hour per-
(user, alert_type) cooldown so high-exposure portfolios don't spam the user.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

_THRESHOLDS_PATH = Path(__file__).parents[3] / "args" / "options_coach_thresholds.yaml"
_COOLDOWN_HOURS_DEFAULT = 4

# Keep ALERT_TYPES in sync with ALERT_TYPES in
# tools/db/migrations/027_ad_coach_alerts/up.py.
ALERT_TYPES = (
    "delta_high",
    "theta_burn",
    "vega_exposure",
    "gamma_elevated",
)

# (alert_type, threshold predicate, human message factory)
_CHECKS = (
    (
        "delta_high",
        lambda g, _: abs(g["net_delta"]) > g["_thresh"]["delta_shares"],
        lambda g, _: (
            f"Delta exposure high: consider hedging "
            f"(net Δ {g['net_delta']:+.1f} share-equivalents)"
        ),
    ),
    (
        "theta_burn",
        lambda g, _: g["net_theta"] < -g["_thresh"]["theta_day_usd"],
        lambda g, _: (
            f"High theta burn rate: "
            f"${abs(g['net_theta']):.2f}/day time decay"
        ),
    ),
    (
        "vega_exposure",
        lambda g, _: abs(g["net_vega"]) > g["_thresh"]["vega_per_iv_usd"],
        lambda g, _: (
            f"Vega exposure: "
            f"${abs(g['net_vega']):.2f} risk per 1% IV change"
        ),
    ),
    (
        "gamma_elevated",
        lambda g, _: abs(g["net_gamma"]) > g["_thresh"]["gamma_abs"],
        lambda g, _: (
            f"Elevated gamma: large moves amplified "
            f"(net Γ {g['net_gamma']:+.2f})"
        ),
    ),
)


def _load_thresholds() -> dict:
    try:
        with open(_THRESHOLDS_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _is_on_cooldown(conn, user_id: str, alert_type: str, cooldown_hours: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM ad_coach_alerts"
        " WHERE user_id = %s AND alert_type = %s AND fired_at > %s"
        " LIMIT 1",
        (user_id, alert_type, cutoff),
    ).fetchone()
    return row is not None


def _insert_alert(
    conn,
    user_id: str,
    alert_type: str,
    message: str,
    metrics: dict,
) -> str:
    alert_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ad_coach_alerts (id, user_id, alert_type, message, fired_at, metrics_json)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (alert_id, user_id, alert_type, message, now, json.dumps(metrics)),
    )
    conn.commit()
    return alert_id


def check_greek_exposure(user_id: str, conn=None) -> list[dict]:
    """Check portfolio Greeks for *user_id* and fire alerts when thresholds exceeded.

    Thresholds are read from args/options_coach_thresholds.yaml (hot-reloadable).
    Each alert_type is rate-limited to once per greek_alert_cooldown_hours (default 4h).

    Returns:
        List of fired alert dicts: {alert_type, message, alert_id}. Empty when
        no threshold is crossed or all triggered types are within cooldown.
    """
    if conn is None:
        from tools.trading.db import get_conn
        conn = get_conn()

    from tools.trading.options.portfolio_greeks import compute_portfolio_greeks

    greeks = compute_portfolio_greeks(user_id=user_id, conn=conn)

    cfg = _load_thresholds()
    cooldown_hours = int(cfg.get("greek_alert_cooldown_hours", _COOLDOWN_HOURS_DEFAULT))

    greeks["_thresh"] = {
        "delta_shares": float(cfg.get("greek_delta_shares", 500)),
        "theta_day_usd": float(cfg.get("greek_theta_day_usd", 50)),
        "vega_per_iv_usd": float(cfg.get("greek_vega_per_iv_usd", 200)),
        "gamma_abs": float(cfg.get("greek_gamma_abs", 50)),
    }

    fired: list[dict] = []
    for alert_type, predicate, message_fn in _CHECKS:
        if not predicate(greeks, cfg):
            continue
        if _is_on_cooldown(conn, user_id, alert_type, cooldown_hours):
            continue
        message = message_fn(greeks, cfg)
        metrics_snapshot = {
            "net_delta": greeks["net_delta"],
            "net_gamma": greeks["net_gamma"],
            "net_theta": greeks["net_theta"],
            "net_vega": greeks["net_vega"],
            "position_count": greeks["position_count"],
            "stale_count": greeks["stale_count"],
        }
        alert_id = _insert_alert(conn, user_id, alert_type, message, metrics_snapshot)
        fired.append({"alert_type": alert_type, "message": message, "alert_id": alert_id})

    return fired
