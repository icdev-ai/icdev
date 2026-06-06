# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Network Canvas — Bandwidth Capacity Planning Simulation.

Assigns expected traffic volumes to topology links, identifies bottlenecks
and over-subscribed uplinks, recommends link aggregation or upgrade paths,
and produces an exportable capacity planning report.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# ── Bandwidth lookup table (label fragment → Mbps) ─────────────────────────
_BW_MAP: list[tuple[str, int]] = [
    # Cloud interconnect circuit types (match before generic speeds)
    ("DX-400G", 400_000),
    ("DX-100G", 100_000),
    ("DX-10G", 10_000),
    ("DX-1G", 1_000),
    ("ER-100G", 100_000),
    ("ER-10G", 10_000),
    ("IC-100G", 100_000),
    ("IC-10G", 10_000),
    ("FC-100G", 100_000),
    ("FC-10G", 10_000),
    ("FC-1G", 1_000),
    ("VPN ECMP", 9_800),
    ("VPN-TUNNEL", 4_900),
    ("VPN TUNNEL", 4_900),
    ("IPSEC TUNNEL", 4_900),
    # Generic speeds
    ("400G", 400_000),
    ("100G", 100_000),
    ("40G", 40_000),
    ("25G", 25_000),
    ("10G", 10_000),
    ("1G", 1_000),
    ("100M", 100),
    ("10M", 10),
]

# Thresholds (%)
_WARN_PCT = 60
_CRIT_PCT = 80
_MAX_PCT = 100

# Upgrade ladder: current capacity → recommended next tier (Mbps)
_UPGRADE_LADDER: dict[int, int] = {
    10: 100,
    100: 1_000,
    1_000: 10_000,
    10_000: 25_000,
    25_000: 40_000,
    40_000: 100_000,
    100_000: 400_000,
}

# LAG break-even: suggest LAG when ≥2 links needed to stay under CRIT
_LAG_MIN_LINKS = 2


def _parse_link_bw(label: str) -> int:
    """Return link capacity in Mbps from a label string."""
    upper = (label or "").upper()
    for fragment, mbps in _BW_MAP:
        if fragment in upper:
            return mbps
    return 1_000  # default 1 GbE


def _next_upgrade(current_mbps: int) -> int:
    """Return the next tier capacity for a given link capacity."""
    for tier, nxt in sorted(_UPGRADE_LADDER.items()):
        if current_mbps <= tier:
            return nxt
    return current_mbps * 4  # beyond ladder: 4x


def _mbps_label(mbps: int) -> str:
    if mbps >= 1_000_000:
        return f"{mbps // 1_000_000} Tbps"
    if mbps >= 1_000:
        return f"{mbps // 1_000} Gbps"
    return f"{mbps} Mbps"


def run_bandwidth_sim(graph: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Run bandwidth capacity planning simulation.

    Parameters
    ----------
    graph : dict
        Topology graph with ``nodes`` and ``edges`` lists.
    params : dict
        Simulation parameters:
        - ``traffic_profiles`` : list of {edge_id, traffic_mbps} overrides
        - ``base_utilization_pct`` : global baseline util % (default 30)
        - ``growth_rate_pct`` : annual growth % (default 20)
        - ``years`` : projection horizon (default 3)
        - ``peak_multiplier`` : peak / average ratio (default 1.5)

    Returns
    -------
    dict
        Full simulation result including per-link analysis, bottlenecks,
        recommendations, and a narrative summary.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build node-id → label map for readable output
    node_labels: dict[str, str] = {n.get("id", ""): (n.get("label") or n.get("type") or n.get("id", "")) for n in nodes}

    # Params
    base_util = float(params.get("base_utilization_pct", 30))
    growth_rate = float(params.get("growth_rate_pct", 20))
    years = int(params.get("years", 3))
    peak_mult = float(params.get("peak_multiplier", 1.5))

    # Index traffic profile overrides by edge_id
    traffic_overrides: dict[str, float] = {}
    for tp in params.get("traffic_profiles", []):
        eid = tp.get("edge_id", "")
        val = tp.get("traffic_mbps")
        if eid and val is not None:
            traffic_overrides[eid] = float(val)

    link_results: list[dict[str, Any]] = []

    for e in edges:
        eid = e.get("id", str(uuid.uuid4())[:8])
        label = e.get("label") or ""
        src_id = e.get("source", "")
        dst_id = e.get("target", "")
        src_lbl = node_labels.get(src_id, src_id)
        dst_lbl = node_labels.get(dst_id, dst_id)
        link_bw = _parse_link_bw(label)

        # Current traffic: use override or derive from base_util + deterministic jitter
        if eid in traffic_overrides:
            current_mbps = traffic_overrides[eid]
        else:
            # Deterministic per-link jitter based on edge hash so results are stable
            jitter = abs(hash(eid)) % 30 - 15  # -15..+14
            effective_util = max(5.0, min(95.0, base_util + jitter))
            current_mbps = link_bw * effective_util / 100.0

        current_util_pct = round(current_mbps / link_bw * 100, 1)
        peak_mbps = round(current_mbps * peak_mult, 1)
        peak_util_pct = round(peak_mbps / link_bw * 100, 1)

        # Year-by-year growth projection
        projections: list[dict[str, Any]] = []
        proj_mbps = current_mbps
        for yr in range(1, years + 1):
            proj_mbps = proj_mbps * (1 + growth_rate / 100)
            proj_util = round(proj_mbps / link_bw * 100, 1)
            peak_proj = round(proj_mbps * peak_mult / link_bw * 100, 1)
            projections.append(
                {
                    "year": yr,
                    "avg_util_pct": proj_util,
                    "peak_util_pct": min(peak_proj, 100.0),
                    "avg_traffic_mbps": round(proj_mbps, 1),
                }
            )

        # Status at end of projection
        final_util = projections[-1]["avg_util_pct"] if projections else current_util_pct
        final_peak = projections[-1]["peak_util_pct"] if projections else peak_util_pct

        if peak_util_pct >= _MAX_PCT or final_peak >= _MAX_PCT:
            status = "saturated"
        elif peak_util_pct >= _CRIT_PCT or final_util >= _CRIT_PCT:
            status = "critical"
        elif peak_util_pct >= _WARN_PCT or final_util >= _WARN_PCT:
            status = "warning"
        else:
            status = "ok"

        # VPN / cloud interconnect detection
        upper_label = (label or "").upper()
        is_vpn = any(kw in upper_label for kw in ("VPN", "IPSEC", "TUNNEL"))
        is_cloud_dx = any(kw in upper_label for kw in ("DX-", "DX "))

        # Recommendation logic
        recommendation = "none"
        upgrade_to_mbps: int | None = None
        lag_links: int | None = None
        notes: list[str] = []

        if status in ("critical", "saturated"):
            upgrade_to_mbps = _next_upgrade(link_bw)
            # Check if LAG is viable (2-4 links of same type instead of single upgrade)
            lag_needed = 2
            while (link_bw * lag_needed * (1 - final_util / 100)) < (link_bw * 0.4):
                lag_needed += 1
                if lag_needed > 4:
                    break

            if is_vpn:
                # VPN tunnels cannot be upgraded in-place
                recommendation = "add_tunnels_or_migrate"
                notes.append(
                    "Cannot upgrade VPN tunnel in-place — add parallel tunnels for ECMP (2x = 9.8G) or migrate to DX/ER"
                )
                lag_links = None
            elif lag_needed <= 4:
                lag_links = lag_needed
                # Cloud DX LAG limits
                if is_cloud_dx:
                    max_lag = 2 if link_bw >= 100_000 else 4
                    if lag_links > max_lag:
                        lag_links = max_lag
                    notes.append(f"AWS Direct Connect LAG max {max_lag} links for {_mbps_label(link_bw)} circuits")
                    notes.append("LAG provides bandwidth only — NOT location/device redundancy (single AWS device)")
                recommendation = "lag_or_upgrade"
            else:
                recommendation = "upgrade"
        elif status == "warning":
            upgrade_to_mbps = _next_upgrade(link_bw)
            if is_vpn:
                recommendation = "plan_tunnel_expansion"
                notes.append(
                    "VPN tunnel approaching 4.9 Gbps hard limit — add ECMP tunnels or migrate to dedicated circuit"
                )
            else:
                recommendation = "plan_upgrade"

        link_results.append(
            {
                "edge_id": eid,
                "label": label,
                "source_id": src_id,
                "target_id": dst_id,
                "source_label": src_lbl,
                "target_label": dst_lbl,
                "link_bw_mbps": link_bw,
                "link_bw_label": _mbps_label(link_bw),
                "current_traffic_mbps": round(current_mbps, 1),
                "current_util_pct": current_util_pct,
                "peak_util_pct": peak_util_pct,
                "status": status,
                "projections": projections,
                "recommendation": recommendation,
                "upgrade_to_mbps": upgrade_to_mbps,
                "upgrade_to_label": _mbps_label(upgrade_to_mbps) if upgrade_to_mbps else None,
                "lag_links": lag_links,
                "notes": notes if notes else None,
            }
        )

    # Aggregate stats
    total_links = len(link_results)
    bottlenecks = [r for r in link_results if r["status"] in ("critical", "saturated")]
    warnings = [r for r in link_results if r["status"] == "warning"]
    ok_links = [r for r in link_results if r["status"] == "ok"]

    avg_util = round(sum(r["current_util_pct"] for r in link_results) / total_links, 1) if total_links else 0

    # Highest-utilization links
    top_loaded = sorted(link_results, key=lambda r: r["current_util_pct"], reverse=True)[:5]

    # Capacity upgrade summary
    upgrades_needed = [
        {
            "link": r["label"] or f"{r['source_label']} → {r['target_label']}",
            "current": r["link_bw_label"],
            "upgrade_to": r["upgrade_to_label"],
            "lag_alternative": (f"{r['lag_links']}x {r['link_bw_label']} LAG" if r["lag_links"] else None),
            "reason": r["recommendation"],
        }
        for r in link_results
        if r["recommendation"] in ("upgrade", "lag_or_upgrade", "plan_upgrade")
    ]

    # Narrative
    narrative = _build_narrative(
        total_links,
        bottlenecks,
        warnings,
        ok_links,
        avg_util,
        growth_rate,
        years,
        upgrades_needed,
    )

    return {
        "sim_id": str(uuid.uuid4()),
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "base_utilization_pct": base_util,
            "growth_rate_pct": growth_rate,
            "years": years,
            "peak_multiplier": peak_mult,
        },
        "summary": {
            "total_links": total_links,
            "node_count": len(nodes),
            "bottleneck_count": len(bottlenecks),
            "warning_count": len(warnings),
            "ok_count": len(ok_links),
            "avg_utilization_pct": avg_util,
            "overall_health": ("critical" if bottlenecks else "warning" if warnings else "ok"),
        },
        "links": link_results,
        "bottlenecks": bottlenecks,
        "top_loaded_links": top_loaded,
        "upgrades_needed": upgrades_needed,
        "narrative": narrative,
    }


def _build_narrative(
    total_links: int,
    bottlenecks: list[dict],
    warnings: list[dict],
    ok_links: list[dict],
    avg_util: float,
    growth_rate: float,
    years: int,
    upgrades: list[dict],
) -> str:
    lines: list[str] = []
    lines.append(f"Capacity planning simulation analysed {total_links} link(s) across the topology.")
    lines.append(
        f"Current average link utilisation is {avg_util}%, "
        f"with a {growth_rate}% annual growth projection over {years} year(s)."
    )
    if bottlenecks:
        names = ", ".join((r["label"] or f"{r['source_label']} → {r['target_label']}") for r in bottlenecks[:3])
        tail = f" (and {len(bottlenecks) - 3} more)" if len(bottlenecks) > 3 else ""
        lines.append(
            f"CRITICAL: {len(bottlenecks)} link(s) are at or approaching saturation: "
            f"{names}{tail}. Immediate action required."
        )
    if warnings:
        lines.append(
            f"WARNING: {len(warnings)} link(s) are in the warning band (60–80% util) "
            f"and will reach critical utilisation within the projection window."
        )
    if ok_links:
        lines.append(f"{len(ok_links)} link(s) are operating within normal parameters.")
    if upgrades:
        lag_count = sum(1 for u in upgrades if u.get("lag_alternative"))
        upg_count = len(upgrades) - lag_count
        parts = []
        if upg_count:
            parts.append(f"{upg_count} direct capacity upgrade(s)")
        if lag_count:
            parts.append(f"{lag_count} link aggregation (LAG) candidate(s)")
        lines.append("Recommended remediation: " + " and ".join(parts) + ".")
    return " ".join(lines)


def _upgrade_section(upgrades: list[dict], upgrade_rows: str) -> str:
    if not upgrades:
        return "<p>No upgrades required.</p>"
    return (
        "<table>"
        "<thead><tr><th>Link</th><th>Current</th><th>Upgrade To</th><th>LAG Alternative</th></tr></thead>"
        "<tbody>" + upgrade_rows + "</tbody>"
        "</table>"
    )


def generate_report_html(sim_result: dict[str, Any], topology_name: str = "Topology") -> str:
    """Generate a self-contained HTML capacity planning report."""
    now = sim_result.get("ran_at", datetime.now(timezone.utc).isoformat())[:19].replace("T", " ")
    summary = sim_result.get("summary", {})
    params = sim_result.get("params", {})
    links = sim_result.get("links", [])
    upgrades = sim_result.get("upgrades_needed", [])
    narrative = sim_result.get("narrative", "")

    health = summary.get("overall_health", "ok")
    health_color = {"ok": "#27ae60", "warning": "#f39c12", "critical": "#e74c3c"}.get(health, "#888")

    status_colors = {
        "ok": "#27ae60",
        "warning": "#f39c12",
        "critical": "#e74c3c",
        "saturated": "#8e44ad",
    }

    link_rows = ""
    for r in links:
        sc = status_colors.get(r["status"], "#888")
        link_rows += (
            f"<tr>"
            f"<td>{r.get('label') or r.get('edge_id', '')}</td>"
            f"<td>{r['source_label']} → {r['target_label']}</td>"
            f"<td>{r['link_bw_label']}</td>"
            f"<td>{r['current_util_pct']}%</td>"
            f"<td>{r['peak_util_pct']}%</td>"
            f"<td style='color:{sc};font-weight:600;'>{r['status'].upper()}</td>"
            f"<td>{r.get('upgrade_to_label') or '—'}</td>"
            f"</tr>"
        )

    upgrade_rows = ""
    for u in upgrades:
        lag = u.get("lag_alternative") or "—"
        upgrade_rows += (
            f"<tr><td>{u['link']}</td><td>{u['current']}</td><td>{u.get('upgrade_to') or '—'}</td><td>{lag}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Bandwidth Capacity Planning Report — {topology_name}</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 32px; color: #222; }}
  h1 {{ color: #1a3a5c; }} h2 {{ color: #1a3a5c; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
  .banner {{ background:#1a3a5c; color:#fff; padding:8px 16px; border-radius:4px; font-size:12px; margin-bottom:16px; }}
  .health {{ display:inline-block; padding:4px 12px; border-radius:12px; color:#fff;
             background:{health_color}; font-weight:600; font-size:14px; }}
  .summary-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:16px 0; }}
  .kpi {{ background:#f5f7fa; border-radius:6px; padding:12px; text-align:center; }}
  .kpi-val {{ font-size:28px; font-weight:700; color:#1a3a5c; }}
  .kpi-lbl {{ font-size:11px; color:#666; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }}
  th {{ background:#1a3a5c; color:#fff; padding:6px 10px; text-align:left; }}
  td {{ padding:5px 10px; border-bottom:1px solid #eee; }}
  tr:nth-child(even) td {{ background:#f9f9f9; }}
  .narrative {{ background:#eaf4ff; border-left:4px solid #3498db; padding:12px 16px;
                border-radius:4px; margin:16px 0; font-size:13px; line-height:1.6; }}
  .footer {{ margin-top:32px; font-size:11px; color:#888; border-top:1px solid #ddd; padding-top:8px; }}
</style>
</head>
<body>
<div class="banner">CUI // SP-CTI — ICDEV™ Network Design Canvas — Capacity Planning Report</div>
<h1>Bandwidth Capacity Planning Report</h1>
<p><strong>Topology:</strong> {topology_name} &nbsp;|&nbsp;
   <strong>Generated:</strong> {now} UTC &nbsp;|&nbsp;
   <strong>Health:</strong> <span class="health">{health.upper()}</span></p>

<div class="narrative">{narrative}</div>

<h2>Summary</h2>
<div class="summary-grid">
  <div class="kpi"><div class="kpi-val">{summary.get("total_links", 0)}</div><div class="kpi-lbl">Total Links</div></div>
  <div class="kpi"><div class="kpi-val" style="color:#e74c3c;">{summary.get("bottleneck_count", 0)}</div><div class="kpi-lbl">Bottlenecks</div></div>
  <div class="kpi"><div class="kpi-val" style="color:#f39c12;">{summary.get("warning_count", 0)}</div><div class="kpi-lbl">Warnings</div></div>
  <div class="kpi"><div class="kpi-val">{summary.get("avg_utilization_pct", 0)}%</div><div class="kpi-lbl">Avg Utilisation</div></div>
</div>
<p><strong>Projection:</strong> {params.get("growth_rate_pct", 20)}% annual growth over {params.get("years", 3)} year(s) &nbsp;|&nbsp;
   <strong>Peak multiplier:</strong> {params.get("peak_multiplier", 1.5)}x</p>

<h2>Per-Link Analysis</h2>
<table>
  <thead><tr><th>Link</th><th>Path</th><th>Capacity</th><th>Avg Util</th><th>Peak Util</th><th>Status</th><th>Upgrade To</th></tr></thead>
  <tbody>{link_rows}</tbody>
</table>

<h2>Upgrade Recommendations</h2>
{_upgrade_section(upgrades, upgrade_rows)}

<div class="footer">
  ICDEV™ Network Design Canvas &copy; 2026 &nbsp;|&nbsp; Classification: CUI // SP-CTI &nbsp;|&nbsp;
  Report ID: {sim_result.get("sim_id", "—")}
</div>
</body>
</html>"""
