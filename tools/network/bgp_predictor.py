"""CUI // SP-CTI -- BGP Session Instability Predictor (PNA module)"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

log = get_logger(__name__)

_NQE_BGP_QUERY = (
    "foreach session in network.bgp.sessions select { "
    "device: session.router.name, "
    "peerIp: session.peerAddress, "
    "peerAsn: session.peerAsn, "
    "state: session.state, "
    "routeCount: session.prefixCount }"
)

_FLAP_WINDOW_HOURS = 24
_FLAP_WINDOW_7D_HOURS = 168


def _fetch_bgp_sessions_nqe(network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.query(_NQE_BGP_QUERY, network_id=network_id)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "items" in result:
            return result["items"]
    except Exception as exc:
        log.warning("NQE BGP session fetch failed: %s", exc)
    return []


def _count_flaps(conn, session_key, hours):
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM nc_bgp_events
            WHERE session_key = ?
              AND event_type = 'flap'
              AND event_at >= datetime('now', ? || ' hours')
            """,
            (session_key, f"-{hours}"),
        )
        return cur.fetchone()[0] or 0
    except Exception:
        return 0


def _compute_bgp_score(flap_24h, flap_7d, session_state):
    # Flap contribution: each flap in 24h adds 0.08, capped at 0.64
    flap_factor_24h = min(0.64, flap_24h * 0.08)
    # Weekly trend adds at most 0.20 more
    flap_factor_7d = min(0.20, max(0.0, (flap_7d - flap_24h) / max(1, flap_7d) * 0.20))

    state_penalty = 0.0
    if session_state and session_state.lower() not in ("established",):
        state_penalty = 0.20

    raw = flap_factor_24h + flap_factor_7d + state_penalty
    # stability_score: higher = more stable; invert raw risk
    stability = round(max(0.0, 1.0 - min(1.0, raw)), 4)

    if stability <= 0.30:
        tier = "critical"
    elif stability <= 0.50:
        tier = "high"
    elif stability <= 0.70:
        tier = "medium"
    else:
        tier = "low"

    # Estimated hours to outage: extrapolate from flap rate
    predicted_outage_hrs = None
    if flap_24h >= 3:
        predicted_outage_hrs = round(24.0 / flap_24h, 1)
    elif flap_7d >= 5:
        predicted_outage_hrs = round(168.0 / max(1, flap_7d) * 2, 1)

    confidence = min(0.95, 0.40 + flap_7d * 0.03 + flap_24h * 0.05)

    return stability, tier, predicted_outage_hrs, round(confidence, 3)


def _session_state_score(state: str) -> float:
    """Risk score from BGP session state (1.0 = most risky, 0.0 = healthy)."""
    s = (state or "").lower()
    if s == "established":
        return 0.0
    if s == "active":
        return 0.60
    if s == "idle":
        return 0.80
    return 1.0  # down / unknown / anything else


def _score_bgp_session(session: dict) -> float:
    """Risk-based BGP session score: 1.0 = most risky, 0.0 = stable."""
    flap_24h = session.get("flaps_24h", 0) or 0
    route_changes = session.get("route_changes", 0) or 0
    state = session.get("state", "established")
    flap_weight = min(flap_24h / 10.0, 1.0)
    route_change_weight = min(route_changes / 100.0, 1.0)
    state_score = _session_state_score(state)
    return round(min(1.0, flap_weight * 0.40 + state_score * 0.35 + route_change_weight * 0.25), 4)


def _risk_tier(risk_score: float) -> str:
    """Map a risk score (0–1, higher = worse) to a tier label."""
    if risk_score >= 0.80:
        return "critical"
    if risk_score >= 0.60:
        return "high"
    if risk_score >= 0.35:
        return "medium"
    return "low"


def _insert_bgp_prediction(conn, pred):
    conn.execute(
        """
        INSERT INTO nc_bgp_predictions
            (session_key, device_name, peer_ip, peer_asn,
             stability_score, flap_count_24h, flap_count_7d, flap_risk,
             route_count, session_state, predicted_outage_hrs,
             confidence, model_version, predicted_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            pred["session_key"],
            pred["device_name"],
            pred["peer_ip"],
            pred.get("peer_asn"),
            pred["stability_score"],
            pred["flap_count_24h"],
            pred["flap_count_7d"],
            pred["flap_risk"],
            pred.get("route_count"),
            pred.get("session_state"),
            pred.get("predicted_outage_hrs"),
            pred["confidence"],
            pred.get("model_version", "1.0"),
            pred.get("predicted_at", datetime.now(timezone.utc).isoformat()),
        ),
    )
    conn.commit()


def predict_bgp_stability(session_key=None, network_id=None):
    sessions = _fetch_bgp_sessions_nqe(network_id)
    results = []

    with get_connection() as conn:
        for sess in sessions:
            device_name = sess.get("device", "")
            peer_ip = sess.get("peerIp", "")
            s_key = f"{peer_ip}|{device_name}"

            if session_key and s_key != session_key:
                continue

            flap_24h = _count_flaps(conn, s_key, _FLAP_WINDOW_HOURS)
            flap_7d = _count_flaps(conn, s_key, _FLAP_WINDOW_7D_HOURS)
            state = sess.get("state", "")
            stability, tier, outage_hrs, conf = _compute_bgp_score(flap_24h, flap_7d, state)

            pred = {
                "session_key": s_key,
                "device_name": device_name,
                "peer_ip": peer_ip,
                "peer_asn": sess.get("peerAsn"),
                "stability_score": stability,
                "flap_count_24h": flap_24h,
                "flap_count_7d": flap_7d,
                "flap_risk": tier,
                "route_count": sess.get("routeCount"),
                "session_state": state,
                "predicted_outage_hrs": outage_hrs,
                "confidence": conf,
                "model_version": "1.0",
                "predicted_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _insert_bgp_prediction(conn, pred)
            except Exception as exc:
                log.warning("Failed to insert BGP prediction for %s: %s", s_key, exc)
            results.append(pred)

    return results


def record_bgp_event(device_name, peer_ip, event_type, peer_asn=None):
    from tools.network.db.init_db import get_connection as _gc
    s_key = f"{peer_ip}|{device_name}"
    with _gc() as conn:
        conn.execute(
            """
            INSERT INTO nc_bgp_events
                (session_key, device_name, peer_ip, peer_asn, event_type, event_at, created_at)
            VALUES (?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """,
            (s_key, device_name, peer_ip, peer_asn, event_type),
        )
        conn.commit()
    return s_key


def get_bgp_predictions(device_name=None, flap_risk=None, limit=50):
    from tools.network.db.init_db import get_connection as _gc
    with _gc() as conn:
        where, params = [], []
        if device_name:
            where.append("device_name = ?")
            params.append(device_name)
        if flap_risk:
            where.append("flap_risk = ?")
            params.append(flap_risk)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM nc_bgp_predictions {clause} ORDER BY stability_score ASC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_bgp_summary():
    from tools.network.db.init_db import get_connection as _gc
    with _gc() as conn:
        cur = conn.execute(
            "SELECT flap_risk, COUNT(DISTINCT session_key) FROM nc_bgp_predictions GROUP BY flap_risk"
        )
        by_tier = {row[0]: row[1] for row in cur.fetchall()}
        cur = conn.execute(
            "SELECT COUNT(*) FROM nc_bgp_predictions WHERE predicted_outage_hrs IS NOT NULL"
        )
        at_risk = cur.fetchone()[0] or 0
        return {"total_sessions": sum(by_tier.values()), "at_risk_sessions": at_risk, "by_tier": by_tier}
