#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC EOS/EOL Lifecycle Scanner.

Scans ni_devices for hardware approaching or past end-of-life,
computes a composite migration-opportunity score, and surfaces
replacement candidates from nc_hardware_profiles.

Usage:
    python tools/ndc/eol_scanner.py --horizon 365 --json
    python tools/ndc/eol_scanner.py --device-id <id> --json
    python tools/ndc/eol_scanner.py --simulate-eol --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _days_until(date_str: str | None) -> int:
    d = _parse_date(date_str)
    if not d:
        return 9999
    return max(0, (d - datetime.now(timezone.utc)).days)


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _get_stig_counts(conn: sqlite3.Connection, device_label: str) -> Dict[str, int]:
    """Count open STIG findings by severity for a device label."""
    rows = conn.execute(
        """SELECT severity, COUNT(*) AS c
           FROM ndc_stig_findings
           WHERE device = %s AND status = 'open'
           GROUP BY severity""",
        (device_label,),
    ).fetchall()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["severity"].lower()] = r["c"]
    return counts


def _compute_composite_score(
    days_until_eol: int,
    criticality_score: float,
    downstream_count: int,
    cat1_count: int,
    cat2_count: int,
    replacement_cost: float,
    annual_maintenance_cost: float,
) -> float:
    """Compute composite migration opportunity score (0.0–1.0)."""
    # Time urgency: higher score as EOL approaches
    if days_until_eol <= 0:
        time_score = 1.0
    elif days_until_eol <= 90:
        time_score = 0.9
    elif days_until_eol <= 180:
        time_score = 0.75
    elif days_until_eol <= 365:
        time_score = 0.55
    else:
        time_score = 0.3

    # Criticality: normalize to 0–1 (assume max 10)
    crit_score = min(criticality_score, 10.0) / 10.0

    # Downstream impact: sigmoid-ish scaling
    dep_score = min(downstream_count / 20.0, 1.0)

    # Compliance risk: CAT1 is severe, CAT2 moderate
    compliance_score = min((cat1_count * 0.25 + cat2_count * 0.1), 1.0)

    # Cost of inaction: annual maintenance as fraction of replacement cost
    cost_penalty = min((annual_maintenance_cost * 3) / max(replacement_cost, 1.0), 1.0)

    # Weighted composite
    score = (
        time_score * 0.30 +
        crit_score * 0.25 +
        dep_score * 0.15 +
        compliance_score * 0.15 +
        cost_penalty * 0.15
    )
    return round(min(max(score, 0.0), 1.0), 4)


def _priority_from_score(score: float) -> str:
    if score >= 0.8:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _status_from_eol(days_until_eol: int) -> str:
    if days_until_eol <= 0:
        return "eol_reached"
    if days_until_eol <= 90:
        return "eol_imminent"
    if days_until_eol <= 365:
        return "eol_approaching"
    return "healthy"


def scan_devices(
    horizon_days: int = 730,
    device_id: Optional[str] = None,
    include_healthy: bool = False,
) -> Dict[str, Any]:
    """Scan ni_devices for EOS/EOL opportunities."""
    conn = _nc_conn()
    results: List[Dict[str, Any]] = []

    cutoff = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).strftime("%Y-%m-%d")

    try:
        if device_id:
            rows = conn.execute(
                """SELECT id, topology_id, node_id, label, device_type, vendor, model,
                          firmware_version, eol_date, eos_date, purchase_cost,
                          replacement_cost, site, rack_location, criticality_score,
                          downstream_count, annual_maintenance_cost, notes
                   FROM ni_devices WHERE id = %s""",
                (device_id,),
            ).fetchall()
        else:
            where = ""
            params: list = [cutoff, cutoff]
            if not include_healthy:
                where = "WHERE (eol_date IS NOT NULL AND eol_date <= ?) OR (eos_date IS NOT NULL AND eos_date <= ?)"
            else:
                params = []
                where = ""
            rows = conn.execute(
                f"""SELECT id, topology_id, node_id, label, device_type, vendor, model,
                           firmware_version, eol_date, eos_date, purchase_cost,
                           replacement_cost, site, rack_location, criticality_score,
                           downstream_count, annual_maintenance_cost, notes
                    FROM ni_devices {where}
                    ORDER BY eol_date ASC""",
                params,
            ).fetchall()

        for r in rows:
            eol_days = _days_until(r["eol_date"])
            eos_days = _days_until(r["eos_date"])
            effective_days = min(eol_days, eos_days)

            stig_counts = _get_stig_counts(conn, r["label"] or "")
            cat1 = stig_counts.get("cat1", 0)
            cat2 = stig_counts.get("cat2", 0)

            composite = _compute_composite_score(
                days_until_eol=effective_days,
                criticality_score=r["criticality_score"] or 0.0,
                downstream_count=r["downstream_count"] or 0,
                cat1_count=cat1,
                cat2_count=cat2,
                replacement_cost=r["replacement_cost"] or 0.0,
                annual_maintenance_cost=r["annual_maintenance_cost"] or 0.0,
            )

            results.append({
                "device_id": r["id"],
                "topology_id": r["topology_id"],
                "node_id": r["node_id"],
                "label": r["label"],
                "device_type": r["device_type"],
                "vendor": r["vendor"],
                "model": r["model"],
                "firmware_version": r["firmware_version"],
                "site": r["site"],
                "rack_location": r["rack_location"],
                "eol_date": r["eol_date"],
                "eos_date": r["eos_date"],
                "days_until_eol": eol_days,
                "days_until_eos": eos_days,
                "criticality_score": r["criticality_score"],
                "downstream_count": r["downstream_count"],
                "open_cat1_findings": cat1,
                "open_cat2_findings": cat2,
                "replacement_cost": r["replacement_cost"],
                "annual_maintenance_cost": r["annual_maintenance_cost"],
                "composite_score": composite,
                "priority": _priority_from_score(composite),
                "status": _status_from_eol(effective_days),
                "notes": r["notes"],
            })
    finally:
        conn.close()

    return {
        "classification": "CUI // SP-CTI",
        "scan_timestamp": _now(),
        "horizon_days": horizon_days,
        "device_id_filter": device_id,
        "devices_scanned": len(results),
        "opportunities": results,
    }


def simulate_eol_dates(
    past_pct: float = 0.33,
    near_pct: float = 0.33,
    horizon_days: int = 365,
) -> Dict[str, Any]:
    """ deterministically shuffle ni_devices EOL dates for demo realism."""
    conn = _nc_conn()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=180)).strftime("%Y-%m-%d")
    near = (now + timedelta(days=90)).strftime("%Y-%m-%d")
    mid = (now + timedelta(days=horizon_days + 30)).strftime("%Y-%m-%d")
    far = (now + timedelta(days=900)).strftime("%Y-%m-%d")

    updated = 0
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM ni_devices ORDER BY id").fetchall()]
        for i, did in enumerate(ids):
            if i % 3 == 0:
                vals = (past, past, did)
            elif i % 3 == 1:
                vals = (near, near, did)
            else:
                vals = (mid, far, did)
            conn.execute("UPDATE ni_devices SET eol_date=%s, eos_date=%s WHERE id=%s", vals)
            updated += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "classification": "CUI // SP-CTI",
        "devices_updated": updated,
        "buckets": {"past": past, "near": near, "mid": mid, "far": far},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC EOS/EOL Scanner")
    parser.add_argument("--horizon", type=int, default=730, help="EOL horizon in days")
    parser.add_argument("--device-id", type=str, default=None, help="Scan single device")
    parser.add_argument("--include-healthy", action="store_true", help="Include devices outside horizon")
    parser.add_argument("--simulate-eol", action="store_true", help="Shuffle demo EOL dates")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.simulate_eol:
        result = simulate_eol_dates()
    else:
        result = scan_devices(
            horizon_days=args.horizon,
            device_id=args.device_id,
            include_healthy=args.include_healthy,
        )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Scanned {result['devices_scanned']} devices")
        for opp in result.get("opportunities", [])[:10]:
            print(
                f"  {opp['label']} ({opp['vendor']} {opp['model']}) "
                f"EOL={opp['eol_date']} score={opp['composite_score']} prio={opp['priority']}"
            )


if __name__ == "__main__":
    main()
