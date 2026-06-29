"""CUI // SP-CTI -- EOL/EOS Predictive Risk Scorer (PNA module)"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

log = get_logger(__name__)

# (vendor_lower_fragment, model_prefix) -> EOS date
_EOS_REGISTRY = {
    ("cisco", "ISR4321"): "2025-10-31",
    ("cisco", "ISR4331"): "2025-10-31",
    ("cisco", "ISR4351"): "2026-01-31",
    ("cisco", "ISR4431"): "2026-01-31",
    ("cisco", "ISR4451"): "2026-01-31",
    ("cisco", "ASR1001-X"): "2025-04-30",
    ("cisco", "ASR1002-X"): "2025-04-30",
    ("cisco", "C9300"): "2030-01-31",
    ("cisco", "C9500"): "2030-01-31",
    ("juniper", "SRX300"): "2027-06-30",
    ("juniper", "SRX320"): "2027-06-30",
    ("juniper", "MX80"): "2025-12-31",
    ("palo alto", "PA-220"): "2025-03-31",
    ("palo alto", "PA-820"): "2026-09-30",
    ("palo alto", "PA-850"): "2026-09-30",
    ("fortinet", "FortiGate-60E"): "2026-02-28",
    ("fortinet", "FortiGate-100E"): "2026-06-30",
}

_NQE_DEVICES_QUERY = (
    "foreach device in network.devices select { "
    "name: device.name, vendor: device.vendor, "
    "model: device.model, osVersion: device.osVersion }"
)


def _lookup_eos(vendor: Optional[str], model: Optional[str]):
    """Return (eos_date_str|None, source_str) for a given vendor+model."""
    if not vendor or not model:
        return None, "local_heuristic"
    vendor_lc = vendor.lower()
    for (reg_vendor, reg_model), eos in _EOS_REGISTRY.items():
        if reg_vendor in vendor_lc and model.startswith(reg_model):
            return eos, "static_registry"
    return None, "local_heuristic"


def _score_device(eos_date_str: Optional[str], active_cve_count: int):
    """Return (risk_score, risk_tier, days_remaining) for a device."""
    today = datetime.now(timezone.utc).date()
    days_remaining: Optional[int] = None

    if eos_date_str:
        try:
            eos = date.fromisoformat(eos_date_str)
            days_remaining = (eos - today).days
        except ValueError:
            pass

    if days_remaining is None:
        base = 0.15
    elif days_remaining < 0:
        base = 0.70
    elif days_remaining <= 90:
        base = 0.55
    elif days_remaining <= 180:
        base = 0.40
    elif days_remaining <= 365:
        base = 0.25
    else:
        base = 0.10

    cve_boost = min(0.30, active_cve_count * 0.05)
    score = min(1.0, round(base + cve_boost, 4))

    if score >= 0.80:
        tier = "critical"
    elif score >= 0.60:
        tier = "high"
    elif score >= 0.35:
        tier = "medium"
    else:
        tier = "low"

    return score, tier, days_remaining


def _fetch_devices_nqe(network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.query(_NQE_DEVICES_QUERY, network_id=network_id)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("rows") or result.get("items") or []
    except Exception as exc:
        log.warning("NQE device fetch failed: %s", exc)
    return []


def _compute_active_cves(conn, device_name: str):
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM nc_vuln_findings "
            "WHERE host LIKE %s AND severity IN ('critical','high')",
            (f"%{device_name}%",),
        )
        row = cur.fetchone()
        count = row[0] if row else 0
        return int(count)
    except Exception:
        return 0


def _insert_prediction(conn, pred: dict) -> None:
    conn.execute(
        """
        INSERT INTO nc_eol_predictions
            (device_name, vendor, model, os_version,
             eos_date, eol_date, days_remaining,
             has_active_cves, active_cve_count,
             risk_score, risk_tier, nqe_source, model_version,
             predicted_at, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        """,
        (
            pred["device_name"], pred.get("vendor"), pred.get("model"),
            pred.get("os_version"), pred.get("eos_date"), pred.get("eol_date"),
            pred.get("days_remaining"),
            1 if pred.get("active_cve_count", 0) > 0 else 0,
            pred.get("active_cve_count", 0),
            pred["risk_score"], pred["risk_tier"],
            pred.get("nqe_source", "local_heuristic"),
            pred.get("model_version", "1.0"),
            pred.get("predicted_at", datetime.now(timezone.utc).isoformat()),
        ),
    )


def predict_eol_risk(device_name: Optional[str] = None, network_id: Optional[str] = None):
    from tools.network.db.init_db import get_connection

    devices = _fetch_devices_nqe(network_id)
    if device_name:
        devices = [d for d in devices if d.get("name") == device_name]
    if not devices:
        return []

    predictions = []
    with get_connection() as conn:
        for dev in devices:
            name = dev.get("name", "")
            vendor = dev.get("vendor")
            model = dev.get("model")
            os_ver = dev.get("osVersion")

            eos_date, nqe_source = _lookup_eos(vendor, model)
            cve_count = _compute_active_cves(conn, name)
            score, tier, days_rem = _score_device(eos_date, cve_count)

            pred = {
                "device_name": name,
                "vendor": vendor,
                "model": model,
                "os_version": os_ver,
                "eos_date": eos_date,
                "eol_date": None,
                "days_remaining": days_rem,
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

    return predictions


def get_eol_predictions(device_name: Optional[str] = None, risk_tier: Optional[str] = None, limit: int = 50):
    from tools.network.db.init_db import get_connection

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
            return []


def get_eol_summary():
    from tools.network.db.init_db import get_connection

    with get_connection() as conn:
        try:
            cur = conn.execute(
                "SELECT risk_tier, COUNT(DISTINCT device_name) FROM nc_eol_predictions GROUP BY risk_tier"
            )
            by_tier = {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            by_tier = {}

        try:
            cur = conn.execute(
                "SELECT COUNT(DISTINCT device_name) FROM nc_eol_predictions WHERE days_remaining < 0"
            )
            row = cur.fetchone()
            already_eos = row[0] if row else 0
        except Exception:
            already_eos = 0

        return {
            "total_devices": sum(by_tier.values()),
            "already_eos": already_eos,
            "by_tier": by_tier,
        }