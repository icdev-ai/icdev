"""CUI // SP-CTI -- Supply Chain Risk Scorer (PNA module)"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from tools.network.db.init_db import get_connection

log = logging.getLogger(__name__)

_NQE_DEVICES_QUERY = (
    "foreach device in network.devices select { "
    "name: device.name, vendor: device.vendor, "
    "model: device.model, osVersion: device.osVersion }"
)

_KNOWN_RISKY_VENDORS = {
    "huawei": 0.25,
    "zte": 0.30,
    "dahua": 0.35,
    "hikvision": 0.35,
    "kaspersky": 0.35,
}


def _fetch_devices_nqe(network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.query(_NQE_DEVICES_QUERY, network_id=network_id)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "items" in result:
            return result["items"]
    except Exception as exc:
        log.warning("NQE device fetch failed: %s", exc)
    return []


def _get_vendor_cves(conn, vendor):
    try:
        cur = conn.execute(
            """
            SELECT a.cve_id, a.cvss_score, a.in_kev
            FROM nc_advisories a
            WHERE LOWER(a.vendor) LIKE ?
            ORDER BY a.cvss_score DESC
            LIMIT 20
            """,
            (f"%{vendor.lower()}%",),
        )
        rows = cur.fetchall()
        return [{"cve_id": r[0], "cvss": r[1], "kev": bool(r[2])} for r in rows]
    except Exception:
        return []


def _get_kev_count(conn, vendor):
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM nc_advisories WHERE LOWER(vendor) LIKE ? AND in_kev = 1",
            (f"%{vendor.lower()}%",),
        )
        return cur.fetchone()[0] or 0
    except Exception:
        return 0


def _compute_supply_chain_score(
    device_count, cve_count, kev_count, critical_count, high_count, vendor_lc
):
    # CISA KEV hits are highest weight: each counts 0.08
    kev_factor = min(0.40, kev_count * 0.08)

    # Critical CVE density: each critical adds 0.05
    critical_factor = min(0.25, critical_count * 0.05)

    # High CVE factor
    high_factor = min(0.10, high_count * 0.01)

    # Known risky vendor baseline
    vendor_base = _KNOWN_RISKY_VENDORS.get(vendor_lc, 0.05)

    # Scale by device count footprint: large footprint amplifies risk
    footprint_mult = min(2.0, 1.0 + device_count * 0.05)

    score = min(1.0, round((vendor_base + kev_factor + critical_factor + high_factor) * footprint_mult, 4))

    if score >= 0.70:
        rating = "critical"
    elif score >= 0.50:
        rating = "high"
    elif score >= 0.25:
        rating = "medium"
    else:
        rating = "low"

    return score, rating


def _insert_supply_chain(conn, rec):
    conn.execute(
        """
        INSERT INTO nc_supply_chain_risk
            (vendor, device_count, model_count,
             cve_count, kev_count,
             critical_cve_count, high_cve_count,
             risk_score, vendor_risk_rating,
             top_cves_json, nqe_device_sample_json,
             model_version, assessed_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            rec["vendor"],
            rec["device_count"],
            rec["model_count"],
            rec["cve_count"],
            rec["kev_count"],
            rec["critical_cve_count"],
            rec["high_cve_count"],
            rec["risk_score"],
            rec["vendor_risk_rating"],
            json.dumps(rec.get("top_cves_json", [])),
            json.dumps(rec.get("nqe_device_sample_json", [])),
            rec.get("model_version", "1.0"),
            rec.get("assessed_at", datetime.now(timezone.utc).isoformat()),
        ),
    )
    conn.commit()


def assess_supply_chain_risk(network_id=None):
    devices = _fetch_devices_nqe(network_id)

    # Aggregate by vendor
    vendor_map = {}
    for dev in devices:
        vendor = (dev.get("vendor") or "Unknown").strip()
        if vendor not in vendor_map:
            vendor_map[vendor] = {"devices": [], "models": set()}
        vendor_map[vendor]["devices"].append(dev)
        model = dev.get("model")
        if model:
            vendor_map[vendor]["models"].add(model)

    results = []
    with get_connection() as conn:
        for vendor, data in vendor_map.items():
            device_list = data["devices"]
            model_set = data["models"]
            vendor_lc = vendor.lower()

            cves = _get_vendor_cves(conn, vendor)
            kev_count = _get_kev_count(conn, vendor)
            cve_count = len(cves)
            critical_count = sum(1 for c in cves if c.get("cvss", 0) >= 9.0)
            high_count = sum(1 for c in cves if 7.0 <= c.get("cvss", 0) < 9.0)

            score, rating = _compute_supply_chain_score(
                len(device_list), cve_count, kev_count, critical_count, high_count, vendor_lc
            )

            sample_devices = [
                {"name": d.get("name"), "model": d.get("model"), "os_version": d.get("osVersion")}
                for d in device_list[:5]
            ]

            rec = {
                "vendor": vendor,
                "device_count": len(device_list),
                "model_count": len(model_set),
                "cve_count": cve_count,
                "kev_count": kev_count,
                "critical_cve_count": critical_count,
                "high_cve_count": high_count,
                "risk_score": score,
                "vendor_risk_rating": rating,
                "top_cves_json": cves[:10],
                "nqe_device_sample_json": sample_devices,
                "model_version": "1.0",
                "assessed_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _insert_supply_chain(conn, rec)
            except Exception as exc:
                log.warning("Failed to insert supply chain risk for %s: %s", vendor, exc)
            results.append(rec)

    return sorted(results, key=lambda r: r["risk_score"], reverse=True)


def get_supply_chain_risks(vendor=None, risk_rating=None, limit=50):
    with get_connection() as conn:
        where, params = [], []
        if vendor:
            where.append("vendor = ?")
            params.append(vendor)
        if risk_rating:
            where.append("vendor_risk_rating = ?")
            params.append(risk_rating)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM nc_supply_chain_risk {clause} ORDER BY risk_score DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_supply_chain_summary():
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT vendor_risk_rating, COUNT(*) FROM nc_supply_chain_risk GROUP BY vendor_risk_rating"
        )
        by_tier = {row[0]: row[1] for row in cur.fetchall()}
        cur = conn.execute("SELECT SUM(kev_count) FROM nc_supply_chain_risk")
        total_kev = cur.fetchone()[0] or 0
        return {
            "total_vendors": sum(by_tier.values()),
            "total_kev_exposures": total_kev,
            "by_rating": by_tier,
        }
