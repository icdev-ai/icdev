"""CUI // SP-CTI -- Compliance Drift Predictor (PNA module -- STIG-based)"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from tools.network.db.init_db import get_connection

log = logging.getLogger(__name__)

_FAIL_THRESHOLD = 0.70
_NQE_QUERY = (
    "foreach device in network.devices select { "
    "name: device.name, config: device.config }"
)

# All checks require POSITIVE evidence -- empty config scores 0.0
_STIG_CHECKS = [
    ("V-220518", "SSH v2 required",           True,
     lambda c: "ip ssh version 2" in c),
    ("V-220519", "No telnet / SSH-only transport",  True,
     lambda c: "ip ssh version 2" in c and "service telnet" not in c),
    ("V-220521", "No unsecure HTTP server",   True,
     lambda c: "ip http secure-server" in c or "no ip http server" in c),
    ("V-220520", "Password encryption enabled", False,
     lambda c: "service password-encryption" in c),
    ("V-220522", "Logging configured",          False,
     lambda c: "logging buffered" in c or "logging host" in c),
    ("V-220523", "NTP configured",              False,
     lambda c: "ntp server" in c),
    ("V-220524", "Exec timeout configured",     False,
     lambda c: "exec-timeout" in c),
    ("V-220525", "MOTD banner configured",      False,
     lambda c: "banner motd" in c or "banner login" in c),
    ("V-220526", "AAA configured",              False,
     lambda c: "aaa new-model" in c),
    ("V-220527", "SNMPv3 only",                 False,
     lambda c: "snmp-server group" in c and "v3" in c),
]


def _run_stig_checks(config_text: str):
    config = config_text or ""
    details = []
    failing = 0
    critical_failing = 0
    passing_count = 0
    total_weight = len(_STIG_CHECKS)

    for check_id, desc, is_critical, pass_fn in _STIG_CHECKS:
        passed = pass_fn(config)
        details.append({"check_id": check_id, "description": desc, "passed": passed, "critical": is_critical})
        if passed:
            passing_count += 1
        else:
            failing += 1
            if is_critical:
                critical_failing += 1

    score = round(passing_count / total_weight, 4) if total_weight else 0.0
    return score, failing, critical_failing, details


def _get_previous_score(conn, device_name: str):
    try:
        cur = conn.execute(
            "SELECT current_score FROM nc_compliance_drift "
            "WHERE device_name = ? ORDER BY assessed_at DESC LIMIT 1",
            (device_name,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _compute_drift_risk(current_score: float, drift_delta: float, critical_failing: int):
    if current_score < 0.50:
        base = 0.80
    elif current_score < 0.70:
        base = 0.55
    elif current_score < 0.85:
        base = 0.30
    else:
        base = 0.10

    drift_penalty = min(0.20, max(0.0, -float(drift_delta) * 5))
    critical_penalty = min(0.20, critical_failing * 0.07)

    score = min(1.0, round(base + drift_penalty + critical_penalty, 4))

    if score >= 0.80:
        tier = "critical"
    elif score >= 0.60:
        tier = "high"
    elif score >= 0.35:
        tier = "medium"
    else:
        tier = "low"

    return score, tier


def _insert_drift(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT INTO nc_compliance_drift
            (device_name, framework,
             last_compliant_score, current_score,
             drift_delta, drift_rate_per_day,
             failing_controls, critical_controls_failing,
             predicted_fail_date, days_to_failure,
             risk_score, risk_tier, assessed_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            rec["device_name"], rec.get("framework", "DISA_STIG"),
            rec.get("last_compliant_score"), rec["current_score"],
            rec["drift_delta"], rec.get("drift_rate_per_day"),
            rec.get("failing_controls", 0), rec.get("critical_controls_failing", 0),
            rec.get("predicted_fail_date"), rec.get("days_to_failure"),
            rec["risk_score"], rec["risk_tier"],
            rec.get("assessed_at", datetime.now(timezone.utc).isoformat()),
        ),
    )


def predict_compliance_drift(device_name=None, network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query(_NQE_QUERY, network_id=network_id)
        devices = result.get("rows") or (result if isinstance(result, list) else [])
    except Exception as exc:
        log.warning("NQE compliance fetch failed: %s", exc)
        devices = []

    if device_name:
        devices = [d for d in devices if d.get("name") == device_name]

    predictions = []
    conn = get_connection()
    for dev in devices:
        name = dev.get("name", "")
        config = dev.get("config") or ""
        current_score, failing, critical_failing, details = _run_stig_checks(config)

        last_score = _get_previous_score(conn, name)
        drift_delta = 0.0 if last_score is None else round(current_score - float(last_score), 4)

        risk_score, risk_tier = _compute_drift_risk(current_score, drift_delta, critical_failing)

        rec = {
            "device_name": name,
            "framework": "DISA_STIG",
            "last_compliant_score": last_score,
            "current_score": current_score,
            "drift_delta": drift_delta,
            "drift_rate_per_day": None,
            "failing_controls": failing,
            "critical_controls_failing": critical_failing,
            "predicted_fail_date": None,
            "days_to_failure": None,
            "risk_score": risk_score,
            "risk_tier": risk_tier,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "stig_details": details,
        }
        try:
            _insert_drift(conn, rec)
            conn.commit()
        except Exception as exc:
            log.warning("Failed to insert compliance drift for %s: %s", name, exc)
        predictions.append(rec)

    return {"devices_assessed": len(predictions), "predictions": predictions}


def get_compliance_drift(device_name=None, framework=None, limit=50):
    conn = get_connection()
    where, params = [], []
    if device_name:
        where.append("device_name = ?")
        params.append(device_name)
    if framework:
        where.append("framework = ?")
        params.append(framework)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM nc_compliance_drift {clause} ORDER BY assessed_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(sql, params)
    try:
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        rows = cur.fetchall()
        return [dict(r) if hasattr(r, "keys") else {} for r in rows]


def get_compliance_summary():
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT device_name), AVG(current_score) FROM nc_compliance_drift"
        )
        row = cur.fetchone()
        total = row[0] or 0
        avg_score = round(float(row[1]), 4) if row[1] else 0.0
    except Exception:
        total, avg_score = 0, 0.0

    try:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT device_name) FROM nc_compliance_drift "
            "WHERE risk_tier = 'critical'"
        )
        critical_count = cur.fetchone()[0] or 0
    except Exception:
        critical_count = 0

    return {
        "total_devices": total,
        "avg_compliance_score": avg_score,
        "critical_drift_count": critical_count,
    }