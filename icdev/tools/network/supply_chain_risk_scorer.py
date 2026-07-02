# CUI // SP-CTI
"""PNA — Predictive Network Analytics: Supply Chain Risk Scorer (pna-scs-01).

Scores supply chain risk by vendor, cross-referencing NQE device inventory
with CVE advisory data (nc_advisories) and CISA KEV exploitation flags.

Algorithm weights:
    kev_norm        = min(kev_count / 5.0, 1.0)            × 0.45
    cve_density     = min(cve_count / device_count / 10.0)  × 0.30
    critical_ratio  = critical_cve_count / max(cve_count, 1) × 0.25

Risk ratings: critical(≥0.75), high(≥0.50), medium(≥0.25), low

Public API:
    score_supply_chain_risk(network_id)  → dict
    get_supply_chain_risks(vendor, rating, limit)  → list[dict]
    get_supply_chain_summary()  → dict

CLI:
    python tools/network/supply_chain_risk_scorer.py --score --json
    python tools/network/supply_chain_risk_scorer.py --risks --rating critical --json
    python tools/network/supply_chain_risk_scorer.py --summary --json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

logger = get_logger(__name__)

MODEL_VERSION = "1.0"

_W_KEV = 0.45
_W_DENSITY = 0.30
_W_CRITICAL = 0.25

# Known vendor name normalizations (NQE may return platform.ostype variants)
_VENDOR_ALIASES: dict[str, str] = {
    "ios": "cisco",
    "ios-xe": "cisco",
    "ios-xr": "cisco",
    "nx-os": "cisco",
    "nxos": "cisco",
    "junos": "juniper",
    "eos": "arista",
    "pan-os": "palo_alto",
    "panos": "palo_alto",
    "fortios": "fortinet",
    "bigip": "f5",
    "acos": "a10",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vendor_risk_rating(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.50:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _normalize_vendor(raw: str | None) -> str:
    """Normalize NQE platform.ostype to a canonical vendor name."""
    if not raw:
        return "unknown"
    lower = raw.lower().strip()
    return _VENDOR_ALIASES.get(lower, lower)


def _get_nqe_device_inventory(network_id: str | None) -> list[dict]:
    """Query NQE for device inventory. Returns list of {name, vendor, model, ip}."""
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query("network.devices[platform.ostype]", network_id=network_id)
        rows = result.get("rows") or []
        devices = []
        for r in rows:
            raw_vendor = (
                r.get("platform", {}).get("ostype")
                or r.get("ostype")
                or r.get("vendor")
                or ""
            )
            model = (
                r.get("platform", {}).get("platform")
                or r.get("hardware", {}).get("platform")
                or r.get("model")
                or ""
            )
            devices.append({
                "name": r.get("name") or r.get("device_name") or "",
                "vendor": _normalize_vendor(raw_vendor),
                "model": model,
                "ip": r.get("management", {}).get("ip") or r.get("ip") or "",
            })
        return devices
    except Exception as exc:
        logger.warning("NQE device inventory query failed: %s", exc)
        return []


def _build_vendor_map(devices: list[dict]) -> dict[str, dict]:
    """Aggregate device list into {vendor → {device_count, models, device_names}}."""
    vendor_map: dict[str, dict] = {}
    for d in devices:
        v = d["vendor"] or "unknown"
        if v not in vendor_map:
            vendor_map[v] = {"device_count": 0, "models": set(), "device_names": []}
        vendor_map[v]["device_count"] += 1
        if d.get("model"):
            vendor_map[v]["models"].add(d["model"])
        if d.get("name"):
            vendor_map[v]["device_names"].append(d["name"])
    return vendor_map


def _query_vendor_advisories(conn, vendor: str) -> dict:
    """Query nc_advisories for CVE data matching vendor."""
    try:
        pattern = f"%{vendor}%"
        row = conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN severity IN ('critical','high') THEN 1 ELSE 0 END) AS critical_high,
                SUM(CASE WHEN exploited_in_wild = '1' THEN 1 ELSE 0 END) AS kev_count,
                MAX(cvss_score) AS max_cvss
               FROM nc_advisories
               WHERE vendor LIKE %s""",
            (pattern,),
        ).fetchone()
        if row:
            return {
                "cve_count": int(row[0] or 0),
                "critical_high": int(row[1] or 0),
                "kev_count": int(row[2] or 0),
                "max_cvss": float(row[3] or 0.0),
            }
    except Exception as exc:
        logger.debug("Advisory query failed for vendor %s: %s", vendor, exc)
    return {"cve_count": 0, "critical_high": 0, "kev_count": 0, "max_cvss": 0.0}


def _get_top_cves(conn, vendor: str, limit: int = 3) -> list[str]:
    """Return top N CVE IDs by cvss_score for this vendor."""
    try:
        rows = conn.execute(
            """SELECT cve_id FROM nc_advisories
               WHERE vendor LIKE %s AND cve_id != ''
               ORDER BY CAST(cvss_score AS REAL) DESC
               LIMIT %s""",
            (f"%{vendor}%", limit),
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


_COUNTRY_RISK_SCORES: dict[str, float] = {
    "cisco": 0.15,
    "juniper": 0.15,
    "arista": 0.15,
    "palo alto": 0.20,
    "paloalto": 0.20,
    "fortinet": 0.25,
    "f5": 0.20,
    "checkpoint": 0.20,
    "check point": 0.20,
    "hp": 0.15,
    "nokia": 0.20,
    "ericsson": 0.20,
    "huawei": 0.75,
    "zte": 0.75,
}


def _country_risk(vendor_name: str) -> float:
    """Return country-of-origin risk score for a vendor (0=low, 1=high)."""
    key = (vendor_name or "").lower().strip()
    return _COUNTRY_RISK_SCORES.get(key, 0.80)


def _score_vendor(vendor_name: str, device_count: int, cve_count: int, eol_count: int) -> float:
    """Compute vendor supply chain risk score using country+concentration+CVE+EOL formula."""
    total_devices = max(device_count, 1)
    country_score = _country_risk(vendor_name)
    concentration_norm = min(device_count / max(total_devices, 1), 1.0) * 0.25
    cve_density_norm = min(cve_count / total_devices / 10.0, 1.0)
    eol_exposure_norm = min(eol_count / total_devices, 1.0)
    risk = (country_score * 0.30) + concentration_norm + (cve_density_norm * 0.25) + (eol_exposure_norm * 0.20)
    return round(min(1.0, max(0.0, risk)), 4)


def _risk_tier_vendor(score: float) -> str:
    """Map supply chain risk score to tier."""
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


# Alias used by tests
_risk_tier = _risk_tier_vendor


def _compute_vendor_risk(vendor_info: dict, advisory_data: dict) -> dict:
    """Compute risk_score and components for one vendor."""
    device_count = vendor_info["device_count"]
    cve_count = advisory_data["cve_count"]
    kev_count = advisory_data["kev_count"]
    critical_count = advisory_data["critical_high"]

    kev_norm = min(kev_count / 5.0, 1.0)
    cve_density = min(cve_count / max(device_count, 1) / 10.0, 1.0)
    critical_ratio = critical_count / max(cve_count, 1)

    risk_score = (kev_norm * _W_KEV) + (cve_density * _W_DENSITY) + (critical_ratio * _W_CRITICAL)
    risk_score = max(0.0, min(1.0, risk_score))

    return {
        "kev_norm": round(kev_norm, 4),
        "cve_density": round(cve_density, 4),
        "critical_ratio": round(critical_ratio, 4),
        "risk_score": round(risk_score, 4),
    }


def _write_supply_chain_risk(conn, row_data: dict) -> int:
    """Insert into nc_supply_chain_risk (APPEND-ONLY)."""
    now = _now()
    cur = conn.execute(
        """INSERT INTO nc_supply_chain_risk
           (vendor, device_count, model_count, cve_count, kev_count,
            critical_cve_count, risk_score, vendor_risk_rating,
            top_cves_json, nqe_device_sample_json,
            kev_norm, cve_density, critical_ratio,
            model_version, assessed_at, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row_data["vendor"],
            row_data["device_count"],
            row_data["model_count"],
            row_data["cve_count"],
            row_data["kev_count"],
            row_data["critical_cve_count"],
            row_data["risk_score"],
            row_data["vendor_risk_rating"],
            row_data["top_cves_json"],
            row_data["nqe_device_sample_json"],
            row_data["kev_norm"],
            row_data["cve_density"],
            row_data["critical_ratio"],
            MODEL_VERSION,
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_supply_chain_risk(network_id: str | None = None) -> dict:
    """Score supply chain risk for all vendors in NQE inventory.

    Falls back to advisory-only vendor list if NQE returns no devices.
    Returns a summary dict with ``vendors_scored``, ``by_rating``, and ``scores`` list.
    """
    conn = get_connection()
    try:
        devices = _get_nqe_device_inventory(network_id)

        if not devices:
            # Fallback: derive vendors from nc_advisories
            logger.info("NQE returned no devices — falling back to advisory vendor list")
            try:
                rows = conn.execute(
                    "SELECT DISTINCT vendor FROM nc_advisories WHERE vendor != '' ORDER BY vendor"
                ).fetchall()
                vendors_from_db = [r[0].lower() for r in rows if r[0]]
            except Exception:
                vendors_from_db = []
            vendor_map = {v: {"device_count": 1, "models": set(), "device_names": []} for v in vendors_from_db}
        else:
            vendor_map = _build_vendor_map(devices)

        scores = []
        for vendor, info in vendor_map.items():
            if vendor == "unknown":
                continue

            advisory_data = _query_vendor_advisories(conn, vendor)
            risk_components = _compute_vendor_risk(info, advisory_data)
            top_cves = _get_top_cves(conn, vendor)
            device_sample = info["device_names"][:5]

            row_data = {
                "vendor": vendor,
                "device_count": info["device_count"],
                "model_count": len(info["models"]),
                "cve_count": advisory_data["cve_count"],
                "kev_count": advisory_data["kev_count"],
                "critical_cve_count": advisory_data["critical_high"],
                "risk_score": risk_components["risk_score"],
                "vendor_risk_rating": _vendor_risk_rating(risk_components["risk_score"]),
                "top_cves_json": json.dumps(top_cves),
                "nqe_device_sample_json": json.dumps(device_sample),
                "kev_norm": risk_components["kev_norm"],
                "cve_density": risk_components["cve_density"],
                "critical_ratio": risk_components["critical_ratio"],
            }

            try:
                rid = _write_supply_chain_risk(conn, row_data)
                row_data["id"] = rid
            except Exception as exc:
                if "no such table" in str(exc).lower():
                    row_data["warning"] = "nc_supply_chain_risk not found — run migration 222"
                else:
                    row_data["warning"] = str(exc)

            scores.append(row_data)

        scores.sort(key=lambda x: x["risk_score"], reverse=True)

        by_rating: dict[str, int] = {}
        for s in scores:
            r = s.get("vendor_risk_rating", "low")
            by_rating[r] = by_rating.get(r, 0) + 1

        return {
            "vendors_scored": len(scores),
            "by_rating": by_rating,
            "nqe_devices_found": len(devices),
            "scores": scores,
        }
    finally:
        conn.close()


def get_supply_chain_risks(
    vendor: str | None = None,
    rating: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return latest nc_supply_chain_risk row per vendor, ordered by risk_score DESC."""
    conn = get_connection()
    try:
        sql = """SELECT * FROM nc_supply_chain_risk
                 WHERE id IN (
                     SELECT MAX(id) FROM nc_supply_chain_risk GROUP BY vendor
                 )"""
        params: list = []
        if vendor:
            sql += " AND vendor LIKE ?"
            params.append(f"%{vendor}%")
        if rating:
            sql += " AND vendor_risk_rating = ?"
            params.append(rating)
        sql += " ORDER BY risk_score DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    finally:
        conn.close()


def get_supply_chain_summary() -> dict:
    """Return aggregate supply chain risk statistics."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT vendor, risk_score, vendor_risk_rating, kev_count, device_count
               FROM nc_supply_chain_risk
               WHERE id IN (
                   SELECT MAX(id) FROM nc_supply_chain_risk GROUP BY vendor
               )
               ORDER BY risk_score DESC"""
        ).fetchall()
        if not rows:
            return {
                "total_vendors": 0,
                "critical_vendors": 0,
                "high_risk_vendors": 0,
                "total_kev_exposure": 0,
                "top_risk_vendors": [],
            }

        total_kev = sum(r[3] or 0 for r in rows)
        by_rating: dict[str, int] = {}
        for r in rows:
            rt = r[2] or "low"
            by_rating[rt] = by_rating.get(rt, 0) + 1

        top_5 = [
            {"vendor": r[0], "risk_score": r[1], "rating": r[2], "kev_count": r[3]}
            for r in rows[:5]
        ]

        return {
            "total_vendors": len(rows),
            "critical_vendors": by_rating.get("critical", 0),
            "high_risk_vendors": by_rating.get("high", 0),
            "total_kev_exposure": total_kev,
            "by_rating": by_rating,
            "top_risk_vendors": top_5,
        }
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return {
                "total_vendors": 0,
                "critical_vendors": 0,
                "high_risk_vendors": 0,
                "total_kev_exposure": 0,
                "top_risk_vendors": [],
            }
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PNA Supply Chain Risk Scorer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--score", action="store_true", help="Score all vendors")
    group.add_argument("--risks", action="store_true", help="Query nc_supply_chain_risk")
    group.add_argument("--summary", action="store_true", help="Aggregate summary")
    parser.add_argument("--network-id", help="Forward Networks network ID")
    parser.add_argument("--vendor", help="Filter by vendor name")
    parser.add_argument("--rating", help="Filter by risk rating (critical/high/medium/low)")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.score:
        result = score_supply_chain_risk(network_id=args.network_id)
    elif args.risks:
        result = get_supply_chain_risks(vendor=args.vendor, rating=args.rating, limit=args.limit)
    else:
        result = get_supply_chain_summary()

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for r in result:
                print(r)
        else:
            print(result)


if __name__ == "__main__":
    _main()
