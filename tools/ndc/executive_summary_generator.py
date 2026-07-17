#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Executive Summary Generator.

Generates portfolio-level executive dashboards for network migration
planning: devices at risk, aggregate replacement cost, TCO delta,
strategic timeline, and compliance health.

Usage:
    python tools/ndc/executive_summary_generator.py --json
    python tools/ndc/executive_summary_generator.py --format markdown --output exec_summary.md
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _days_until_eol(eol_date: str | None) -> int:
    if not eol_date:
        return 9999
    try:
        d = datetime.strptime(eol_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (d - datetime.now(timezone.utc)).days)
    except Exception:
        return 9999


def _compute_tco(
    replacement_cost: float,
    annual_maintenance: float,
    power_w: float = 200.0,
    rack_units: int = 2,
    years: int = 3,
) -> Dict[str, float]:
    """Compute 3-year TCO components."""
    power_cost_per_year = (power_w / 1000) * 8760 * 0.12  # $0.12/kWh
    rack_cost_per_year = rack_units * 1200  # $1,200/RU/year
    total_maintenance = annual_maintenance * years
    total_power = power_cost_per_year * years
    total_rack = rack_cost_per_year * years
    return {
        "replacement_cost": replacement_cost,
        "maintenance_3yr": round(total_maintenance, 2),
        "power_3yr": round(total_power, 2),
        "rack_3yr": round(total_rack, 2),
        "total_3yr": round(replacement_cost + total_maintenance + total_power + total_rack, 2),
    }


def generate_executive_summary(horizon_days: int = 365) -> Dict[str, Any]:
    """Generate executive dashboard data from NDC database."""
    conn = _nc_conn()
    cutoff = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).strftime("%Y-%m-%d")

    # ── Devices at risk ──────────────────────────────────────────────────
    at_risk_rows = conn.execute(
        """SELECT id, vendor, model, device_type, site, eol_date, eos_date,
                  replacement_cost, annual_maintenance_cost,
                  criticality_score, downstream_count, label
           FROM ni_devices
           WHERE (eol_date IS NOT NULL AND eol_date <= %s)
              OR (eos_date IS NOT NULL AND eos_date <= %s)
           ORDER BY eol_date ASC""",
        (cutoff, cutoff),
    ).fetchall()

    devices_at_risk: List[Dict[str, Any]] = []
    total_replacement_cost = 0.0
    total_annual_maintenance = 0.0
    sites_at_risk: set[str] = set()
    vendor_counts: Dict[str, int] = {}

    for r in at_risk_rows:
        eol_days = _days_until_eol(r["eol_date"])
        eos_days = _days_until_eol(r["eos_date"])
        effective_days = min(eol_days, eos_days)

        devices_at_risk.append({
            "id": r["id"],
            "label": r["label"] or "",
            "vendor": r["vendor"],
            "model": r["model"],
            "device_type": r["device_type"],
            "site": r["site"],
            "eol_date": r["eol_date"],
            "eos_date": r["eos_date"],
            "days_until_eol": effective_days,
            "replacement_cost": r["replacement_cost"] or 0.0,
            "criticality_score": r["criticality_score"] or 0.0,
            "downstream_count": r["downstream_count"] or 0,
        })

        total_replacement_cost += r["replacement_cost"] or 0.0
        total_annual_maintenance += r["annual_maintenance_cost"] or 0.0
        if r["site"]:
            sites_at_risk.add(r["site"])
        vendor_counts[r["vendor"] or "Unknown"] = vendor_counts.get(r["vendor"] or "Unknown", 0) + 1

    # ── Compliance health ────────────────────────────────────────────────
    stig_rows = conn.execute(
        """SELECT severity, COUNT(*) AS c
           FROM ndc_stig_findings
           WHERE status = 'open'
           GROUP BY severity"""
    ).fetchall()
    stig_counts: Dict[str, int] = {}
    for r in stig_rows:
        stig_counts[r["severity"].lower()] = r["c"]

    cat1_count = stig_counts.get("cat1", 0)
    cat2_count = stig_counts.get("cat2", 0)
    cat3_count = stig_counts.get("cat3", 0)

    # ── Circuits / cloud connectivity ──────────────────────────────────
    circuit_rows = conn.execute(
        """SELECT carrier, circuit_type, bandwidth, monthly_cost_usd, site
           FROM nc_circuits"""
    ).fetchall()
    total_monthly_circuit_cost = sum(r["monthly_cost_usd"] or 0 for r in circuit_rows)
    cloud_circuits = [r for r in circuit_rows if r["circuit_type"] and "cloud" in r["circuit_type"].lower()]
    cloud_monthly_cost = sum(r["monthly_cost_usd"] or 0 for r in cloud_circuits)

    # ── COA strategy summary (sample) ──────────────────────────────────
    # For executive view, estimate downtime and cost per COA
    coa_summary = {
        "coa_1_rip_replace": {
            "name": "Rip & Replace",
            "risk_level": "high",
            "estimated_downtime_hours": len(devices_at_risk) * 4,
            "total_cost": round(total_replacement_cost * 1.0, 2),
            "description": "Swap hardware in single maintenance window per device.",
        },
        "coa_2_phased": {
            "name": "Phased Cutover",
            "risk_level": "medium",
            "estimated_downtime_hours": len(devices_at_risk) * 1.5,
            "total_cost": round(total_replacement_cost * 1.15, 2),  # +15% labor
            "description": "Migrate circuits/services in phases over weeks.",
        },
        "coa_3_side_by_side": {
            "name": "Side-by-Side VLAN",
            "risk_level": "low",
            "estimated_downtime_hours": len(devices_at_risk) * 0.1,
            "total_cost": round(total_replacement_cost * 1.25, 2),  # +25% labor/hardware
            "description": "Run old+new in parallel on same VLAN domain. Near-zero downtime.",
        },
    }

    # ── Timeline (quarterly buckets) ────────────────────────────────────
    now = datetime.now(timezone.utc)
    timeline: List[Dict[str, Any]] = []
    for q_offset in range(4):
        q_start = now + timedelta(days=q_offset * 90)
        q_end = q_start + timedelta(days=90)
        q_devices = [
            d for d in devices_at_risk
            if d["eol_date"]
            and q_start.strftime("%Y-%m-%d") <= d["eol_date"] <= q_end.strftime("%Y-%m-%d")
        ]
        if q_devices:
            timeline.append({
                "quarter": f"Q{((q_start.month - 1) // 3) + 1} {q_start.year}",
                "start_date": q_start.strftime("%Y-%m-%d"),
                "end_date": q_end.strftime("%Y-%m-%d"),
                "device_count": len(q_devices),
                "replacement_cost": round(sum(d["replacement_cost"] for d in q_devices), 2),
                "sites": sorted(set(d["site"] for d in q_devices if d["site"])),
            })

    conn.close()

    return {
        "classification": "CUI // SP-CTI",
        "generated_at": _now(),
        "horizon_days": horizon_days,
        "portfolio_summary": {
            "total_devices": len(devices_at_risk),
            "sites_at_risk": len(sites_at_risk),
            "vendors_affected": vendor_counts,
            "total_replacement_cost": round(total_replacement_cost, 2),
            "total_annual_maintenance": round(total_annual_maintenance, 2),
            "total_monthly_circuit_cost": round(total_monthly_circuit_cost, 2),
            "cloud_monthly_cost": round(cloud_monthly_cost, 2),
            "cat1_findings": cat1_count,
            "cat2_findings": cat2_count,
            "cat3_findings": cat3_count,
        },
        "coa_comparison": coa_summary,
        "timeline": timeline,
        "devices_at_risk": devices_at_risk[:20],  # limit detail list
    }


def _render_markdown(data: Dict[str, Any]) -> str:
    ps = data["portfolio_summary"]
    coas = data["coa_comparison"]
    lines = [
        "# NDC Executive Summary",
        f"**Generated:** {data['generated_at']}  ",
        f"**Classification:** {data['classification']}  ",
        f"**Horizon:** {data['horizon_days']} days",
        "",
        "## Portfolio at a Glance",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Devices at Risk | {ps['total_devices']} |",
        f"| Sites Affected | {ps['sites_at_risk']} |",
        f"| Total Replacement Cost | ${ps['total_replacement_cost']:,.0f} |",
        f"| Annual Maintenance (current) | ${ps['total_annual_maintenance']:,.0f} |",
        f"| Monthly Circuit Cost | ${ps['total_monthly_circuit_cost']:,.0f} |",
        f"| Cloud Monthly Cost | ${ps['cloud_monthly_cost']:,.0f} |",
        f"| Open CAT1 Findings | {ps['cat1_findings']} |",
        f"| Open CAT2 Findings | {ps['cat2_findings']} |",
        "",
        "## COA Strategy Comparison",
        "",
        "| Strategy | Risk | Downtime (hrs) | Est. Cost |",
        "|----------|------|----------------|-----------|",
    ]
    for key, coa in coas.items():
        lines.append(
            f"| {coa['name']} | {coa['risk_level'].upper()} | {coa['estimated_downtime_hours']} | ${coa['total_cost']:,.0f} |"
        )
    lines += ["", "## Migration Timeline", ""]
    if data["timeline"]:
        lines.append("| Quarter | Devices | Replacement Cost | Sites |")
        lines.append("|---------|---------|------------------|-------|")
        for t in data["timeline"]:
            sites_str = ", ".join(t["sites"][:3])
            lines.append(f"| {t['quarter']} | {t['device_count']} | ${t['replacement_cost']:,.0f} | {sites_str} |")
    else:
        lines.append("No migrations scheduled within current horizon.")
    lines += ["", "---", "*Generated by ICDEV™ Network Design Canvas — Executive Summary Generator*"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Executive Summary Generator")
    parser.add_argument("--horizon", type=int, default=365, help="EOL horizon in days")
    parser.add_argument("--format", type=str, default="json", choices=["json", "markdown"], help="Output format")
    parser.add_argument("--output", type=str, default="", help="Output file path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = generate_executive_summary(horizon_days=args.horizon)

    if args.format == "markdown":
        body = _render_markdown(result)
    else:
        body = json.dumps(result, indent=2)

    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
        print(f"Written to {args.output}")
    else:
        print(body)


if __name__ == "__main__":
    main()
