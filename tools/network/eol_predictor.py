"""CUI // SP-CTI -- EOL/EOS Predictive Risk Scorer (PNA module)"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

def get_connection():
    from tools.network.db.init_db import get_connection as _gc
    return _gc()

log = logging.getLogger(__name__)

# Hardware model → EOS date (vendor hardware EOL)
_HW_EOS_REGISTRY: dict[str, str] = {
    # Cisco ISR routers
    "ISR4321": "2028-01-31",
    "ISR4331": "2028-01-31",
    "ISR4351": "2028-01-31",
    "ISR4431": "2028-01-31",
    "ISR4451": "2028-01-31",
    "ISR4221": "2027-01-31",
    # Cisco ASR routers
    "ASR1001": "2025-07-31",
    "ASR1002": "2025-07-31",
    "ASR1006": "2026-07-31",
    "ASR9001": "2027-09-30",
    # Cisco Catalyst switches
    "WS-C3750": "2023-10-31",
    "WS-C3850": "2025-10-31",
    "WS-C9300": "2031-01-31",
    "WS-C9400": "2031-01-31",
    # Juniper MX
    "MX80": "2026-06-30",
    "MX480": "2027-06-30",
    "MX960": "2028-06-30",
    # Palo Alto PA
    "PA-3020": "2025-06-30",
    "PA-5220": "2029-01-31",
    "PA-5280": "2029-01-31",
}

# OS version prefix → EOS date (DISA/vendor EOL calendars)
_OS_EOS_REGISTRY = {
    # Cisco IOS-XE train → Cisco LTS/EOS
    "17.6": "2026-09-29",
    "17.3": "2025-11-30",
    "17.9": "2027-09-30",
    "16.12": "2025-04-30",
    "16.9": "2024-09-30",
    # Cisco IOS (classic)
    "15.7": "2024-06-30",
    "15.9": "2025-12-31",
    # Cisco NX-OS
    "9.3": "2026-01-31",
    "10.2": "2027-06-30",
    # Junos
    "22.4": "2025-12-31",
    "22.2": "2025-06-30",
    "21.4": "2024-12-31",
    "20.4": "2024-09-30",
    "23.2": "2026-06-30",
    # Palo Alto PAN-OS
    "10.1": "2025-11-30",
    "10.2": "2026-11-30",
    "11.0": "2025-11-30",
    # Fortinet FortiOS
    "7.0": "2025-09-30",
    "7.2": "2026-09-30",
    "7.4": "2027-09-30",
    # Aruba ArubaOS
    "10.4": "2026-12-31",
}

_NQE_QUERY = (
    "foreach device in network.devices select { "
    "name: device.name, managementIp: device.managementIp, "
    "platform: { ostype: device.platform.ostype, "
    "osversion: device.platform.osversion } }"
)


def _lookup_eos(vendor: Optional[str], model_or_version: Optional[str]) -> tuple:
    """Look up EOS date for a device.

    First tries hardware model registry, then OS version prefix registry.
    Returns (eos_date_str | None, source_str).
    """
    if not model_or_version:
        return None, "local_heuristic"

    # 1. Try hardware model lookup (exact match, case-insensitive)
    key = str(model_or_version).upper()
    for hw_key, eos in _HW_EOS_REGISTRY.items():
        if hw_key.upper() in key or key in hw_key.upper():
            return eos, "static_registry"

    # 2. Try OS version prefix match (major.minor)
    v = str(model_or_version)
    parts = v.replace("-", ".").split(".")
    for n in (2, 1):
        prefix = ".".join(parts[:n])
        if prefix in _OS_EOS_REGISTRY:
            return _OS_EOS_REGISTRY[prefix], "static_registry"

    return None, "local_heuristic"


def _compute_risk_score(days_remaining: Optional[int], has_cve: bool) -> float:
    if days_remaining is None:
        base = 0.20
    elif days_remaining < 0:
        return 1.0
    elif days_remaining <= 90:
        base = 0.85
    elif days_remaining <= 180:
        base = 0.65
    elif days_remaining <= 365:
        base = 0.45
    else:
        base = 0.15

    cve_boost = 0.15 if has_cve else 0.0
    return round(min(1.0, base + cve_boost), 4)


def _risk_tier(score: float) -> str:
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _fetch_devices_nqe(network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query(_NQE_QUERY, network_id=network_id)
        rows = result.get("rows") or result if isinstance(result, list) else []
        return rows
    except Exception as exc:
        log.warning("NQE device fetch failed: %s", exc)
    return []


def _compute_active_cves(conn, device_name: str) -> tuple:
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM nc_vuln_findings "
            "WHERE host LIKE ? AND severity IN ('critical','high')",
            (f"%{device_name}%",),
        )
        row = cur.fetchone()
        count = row[0] if row else 0
        return count > 0, int(count)
    except Exception:
        return False, 0


def _insert_prediction(conn, pred: dict) -> None:
    conn.execute(
        """
        INSERT INTO nc_eol_predictions
            (device_name, vendor, model, os_version,
             eos_date, eol_date, days_remaining,
             has_active_cves, active_cve_count,
             risk_score, risk_tier, nqe_source, model_version,
             predicted_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            pred["device_name"], pred.get("vendor"), pred.get("model"),
            pred.get("os_version"), pred.get("eos_date"), pred.get("eol_date"),
            pred.get("days_remaining"),
            1 if pred.get("has_active_cves") else 0,
            pred.get("active_cve_count", 0),
            pred["risk_score"], pred["risk_tier"],
            pred.get("nqe_source", "local_heuristic"),
            pred.get("model_version", "1.0"),
            pred.get("predicted_at", datetime.now(timezone.utc).isoformat()),
        ),
    )


def predict_eol_risk(device_name=None, network_id=None):
    devices = _fetch_devices_nqe(network_id)
    if device_name:
        devices = [d for d in devices if d.get("name") == device_name]

    predictions = []
    today = datetime.now(timezone.utc).date()

    with get_connection() as conn:
        for dev in devices:
            name = dev.get("name", "")
            platform = dev.get("platform") or {}
            ostype = platform.get("ostype") or dev.get("ostype")
            osversion = platform.get("osversion") or dev.get("osversion")

            eos_date_str = _lookup_eos(ostype, osversion)
            nqe_source = "static_registry" if eos_date_str else "local_heuristic"

            days_remaining = None
            if eos_date_str:
                try:
                    eos = date.fromisoformat(eos_date_str)
                    days_remaining = (eos - today).days
                except ValueError:
                    pass

            has_cve, cve_count = _compute_active_cves(conn, name)
            score = _compute_risk_score(days_remaining, has_cve)
            tier = _risk_tier(score)

            pred = {
                "device_name": name,
                "vendor": ostype,
                "model": None,
                "os_version": osversion,
                "eos_date": eos_date_str,
                "eol_date": None,
                "days_remaining": days_remaining,
                "has_active_cves": has_cve,
                "active_cve_count": cve_count,
                "risk_score": score,
                "risk_tier": tier,
                "nqe_source": nqe_source,
                "model_version": "1.0",
                "predicted_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _insert_prediction(conn, pred)
                conn.commit()
            except Exception as exc:
                log.warning("Failed to insert EOL prediction for %s: %s", name, exc)
            predictions.append(pred)

    return {"success": True, "scored": len(predictions), "predictions": predictions}


def get_eol_predictions(device_name=None, risk_tier=None, limit=50):
    with get_connection() as conn:
        where, params = [], []
        if device_name:
            where.append("device_name = ?")
            params.append(device_name)
        if risk_tier:
            where.append("risk_tier = ?")
            params.append(risk_tier)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM nc_eol_predictions {clause} ORDER BY predicted_at DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        try:
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            rows = cur.fetchall()
            return [dict(r) if hasattr(r, "keys") else r for r in rows]


def get_eol_summary():
    with get_connection() as conn:
        try:
            cur = conn.execute(
                "SELECT risk_tier, COUNT(DISTINCT device_name) FROM nc_eol_predictions GROUP BY risk_tier"
            )
            by_tier = {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            by_tier = {}

        critical_count = by_tier.get("critical", 0)
        total = sum(by_tier.values())

        return {
            "total": total,
            "critical_count": critical_count,
            "by_tier": by_tier,
        }