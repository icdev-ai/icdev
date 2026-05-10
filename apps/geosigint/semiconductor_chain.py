#!/usr/bin/env python3
# CUI // SP-CTI
"""Semiconductor supply chain — TSMC fab nodes, ASML EUV routes, rare earth flow, disruption cascade.

Models the global semiconductor supply chain as a directed dependency graph:
  China rare earth mines → processing → materials export → TSMC/Samsung fab → end consumers.
  ASML EUV machine production route: component suppliers → Veldhoven assembly → customer fabs.

Disruption cascade sim: propagate supply interruption from any node through dependency edges
and compute downstream production degradation within T days.

Data: OSINT (ASML annual reports, CSIS, Rhodium Group, C4ADS, MIT Observatory of Economic Complexity,
      US BIS Export Administration Reports, USGS Minerals Yearbook).
"""

from __future__ import annotations

import math
from typing import Any

# ── TSMC Fab Nodes ───────────────────────────────────────────────────────────
# Source: TSMC annual reports, CSIS Technology Supply Chain, Semiconductor Industry Association.

TSMC_FABS: list[dict[str, Any]] = [
    {
        "node_id": "tsmc-fab18",
        "name": "TSMC Fab 18 (Tainan / P6)",
        "process_nodes": ["3nm (N3)", "5nm (N5)"],
        "wafers_per_month": 130_000,
        "capacity_share_pct": 35.0,
        "lat": 22.99,
        "lon": 120.21,
        "location": "Southern Taiwan Science Park, Tainan",
        "criticality": 0.97,
        "status": "operational",
        "notes": "N3/N5 primary volume. Apple A-series, NVIDIA H100/Blackwell sourced here.",
        "color": "#ef4444",
    },
    {
        "node_id": "tsmc-fab12",
        "name": "TSMC Fab 12 (Hsinchu)",
        "process_nodes": ["7nm (N7)", "10nm (N10)"],
        "wafers_per_month": 95_000,
        "capacity_share_pct": 22.0,
        "lat": 24.79,
        "lon": 120.99,
        "location": "Hsinchu Science Park, Hsinchu",
        "criticality": 0.88,
        "status": "operational",
        "notes": "N7 workhorse for AMD Ryzen, Apple A14, Qualcomm Snapdragon.",
        "color": "#ef4444",
    },
    {
        "node_id": "tsmc-fab14",
        "name": "TSMC Fab 14 (Central Taiwan SP)",
        "process_nodes": ["7nm", "12nm", "16nm"],
        "wafers_per_month": 80_000,
        "capacity_share_pct": 18.0,
        "lat": 24.17,
        "lon": 120.68,
        "location": "Central Taiwan Science Park, Taichung",
        "criticality": 0.82,
        "status": "operational",
        "notes": "Mixed-node fab; significant 12/16nm automotive and HPC volume.",
        "color": "#f59e0b",
    },
    {
        "node_id": "tsmc-fab21-az",
        "name": "TSMC Fab 21 (Phoenix, AZ)",
        "process_nodes": ["4nm (N4P)", "2nm (N2) — Phase 2"],
        "wafers_per_month": 20_000,
        "capacity_share_pct": 5.0,
        "lat": 33.65,
        "lon": -112.02,
        "location": "Phoenix, Arizona, USA",
        "criticality": 0.60,
        "status": "ramp_up",
        "notes": "CHIPS Act recipient. Phase 1 N4P; Phase 2 N2 targeted 2028. US-soil resilience node.",
        "color": "#22c55e",
    },
    {
        "node_id": "tsmc-jasm",
        "name": "TSMC JASM (Kumamoto, Japan)",
        "process_nodes": ["12nm", "16nm", "22nm", "28nm"],
        "wafers_per_month": 55_000,
        "capacity_share_pct": 12.0,
        "lat": 32.83,
        "lon": 130.71,
        "location": "Kumamoto, Japan",
        "criticality": 0.65,
        "status": "operational",
        "notes": "Sony/Toyota-backed JV. Mature nodes for automotive and IoT.",
        "color": "#22c55e",
    },
]

# ── ASML EUV Lithography Routes ───────────────────────────────────────────────
# ASML EUV systems require ~100,000 components; 457 critical suppliers.
# Machine assembly at Veldhoven, NL; shipped via air (Boeing 747 charter) or sea.
# Source: ASML Annual Report 2023, C4ADS "Choke Points" report, CSIS.

ASML_EUV_NODES: list[dict[str, Any]] = [
    {
        "node_id": "asml-hq",
        "name": "ASML HQ / Assembly (Veldhoven)",
        "role": "euv_assembly",
        "lat": 51.41,
        "lon": 5.47,
        "location": "Veldhoven, Netherlands",
        "criticality": 1.00,
        "euv_output_per_year": 60,
        "notes": "Sole global EUV assembler. One machine weighs ~180 tons; 40 shipping crates.",
        "color": "#ef4444",
    },
    {
        "node_id": "asml-cymer",
        "name": "Cymer (ASML) — EUV Light Source",
        "role": "euv_subsystem",
        "lat": 32.89,
        "lon": -117.20,
        "location": "San Diego, CA, USA",
        "criticality": 0.96,
        "notes": "Tin-droplet plasma EUV light source; acquired 2013. Only supplier.",
        "color": "#ef4444",
    },
    {
        "node_id": "asml-zeiss",
        "name": "Carl Zeiss SMT — EUV Optics",
        "role": "euv_subsystem",
        "lat": 48.40,
        "lon": 9.98,
        "location": "Oberkochen, Germany",
        "criticality": 0.95,
        "notes": "Multilayer mirror optics polished to sub-nm precision. ASML owns 24.9% stake.",
        "color": "#ef4444",
    },
    {
        "node_id": "asml-taiwan-port",
        "name": "Kaohsiung Port (EUV delivery)",
        "role": "logistics_port",
        "lat": 22.61,
        "lon": 120.28,
        "location": "Kaohsiung, Taiwan",
        "criticality": 0.80,
        "notes": "Primary sea-delivery port for ASML equipment destined for TSMC fabs.",
        "color": "#f59e0b",
    },
    {
        "node_id": "asml-korea-port",
        "name": "Pyeongtaek / Incheon (EUV delivery)",
        "role": "logistics_port",
        "lat": 37.01,
        "lon": 127.09,
        "location": "Pyeongtaek, South Korea",
        "criticality": 0.75,
        "notes": "Primary port for Samsung / SK Hynix ASML deliveries.",
        "color": "#f59e0b",
    },
    {
        "node_id": "asml-us-delivery",
        "name": "Phoenix Sky Harbor / Los Angeles (EUV delivery)",
        "role": "logistics_port",
        "lat": 33.44,
        "lon": -112.01,
        "location": "Phoenix, AZ / Los Angeles, CA, USA",
        "criticality": 0.55,
        "notes": "Air/sea delivery route for TSMC Arizona Fab 21.",
        "color": "#22c55e",
    },
]

# ── Rare Earth Flow — China → Taiwan → World ─────────────────────────────────
# Critical rare earth elements (REE) used in semiconductor and EUV manufacturing:
#   Neodymium (Nd), Praseodymium (Pr): magnet motors, EUV positioning
#   Terbium (Tb), Dysprosium (Dy): high-performance magnets
#   Europium (Eu), Yttrium (Y): phosphors, EUV plasma targets
#   Neon (Ne), Krypton (Kr), Xenon (Xe): laser gas for DUV/EUV excimer
# Source: USGS Minerals Yearbook, CSIS, Rhodium Group China Rare Earths analysis.

RARE_EARTH_NODES: list[dict[str, Any]] = [
    # Chinese mine/processing
    {
        "node_id": "re-baotou-mine",
        "name": "Baotou / Bayan Obo",
        "role": "mine_processing",
        "lat": 41.60,
        "lon": 109.99,
        "location": "Inner Mongolia, China",
        "elements": ["Ce", "La", "Nd", "Pr", "Sm"],
        "global_share_pct": 58.0,
        "criticality": 0.92,
        "color": "#ef4444",
    },
    {
        "node_id": "re-jiangxi-mine",
        "name": "Jiangxi REE (Ion-Adsorption)",
        "role": "mine_processing",
        "lat": 25.85,
        "lon": 114.93,
        "location": "Southern Jiangxi, China",
        "elements": ["Tb", "Dy", "Y", "Eu", "Ho", "Er"],
        "global_share_pct": 30.0,
        "criticality": 0.88,
        "color": "#ef4444",
    },
    {
        "node_id": "re-sichuan-mine",
        "name": "Sichuan Carbonatite REE",
        "role": "mine_processing",
        "lat": 29.00,
        "lon": 102.00,
        "location": "Sichuan, China",
        "elements": ["Ce", "La", "Nd"],
        "global_share_pct": 8.0,
        "criticality": 0.72,
        "color": "#f59e0b",
    },
    # Processing / separation
    {
        "node_id": "re-china-separation",
        "name": "China REE Separation Industry",
        "role": "processing",
        "lat": 27.00,
        "lon": 115.00,
        "location": "China (distributed: Jiangxi, Shandong, Inner Mongolia)",
        "elements": ["all"],
        "global_share_pct": 87.0,
        "criticality": 0.94,
        "color": "#ef4444",
        "notes": "China processes ~87% of global REE even when mined elsewhere.",
    },
    # Non-China alternatives
    {
        "node_id": "re-mp-usa",
        "name": "MP Materials — Mountain Pass",
        "role": "mine_processing",
        "lat": 35.48,
        "lon": -115.54,
        "location": "Mountain Pass, California, USA",
        "elements": ["Ce", "La", "Nd", "Pr"],
        "global_share_pct": 14.0,
        "criticality": 0.55,
        "color": "#3b82f6",
        "notes": "Only US REE mine; still sends concentrate to China for separation.",
    },
    {
        "node_id": "re-lynas-au",
        "name": "Lynas — Mount Weld",
        "role": "mine_processing",
        "lat": -28.87,
        "lon": 121.87,
        "location": "Mount Weld, Western Australia",
        "elements": ["Nd", "Pr", "Ce", "La"],
        "global_share_pct": 8.0,
        "criticality": 0.50,
        "color": "#3b82f6",
        "notes": "Largest non-China REE producer; processes in Malaysia (LAMP) and Texas (planned).",
    },
    # Neon/noble gas
    {
        "node_id": "re-neon-ua",
        "name": "Ingas / Cryoin — Neon",
        "role": "noble_gas",
        "lat": 46.48,
        "lon": 30.73,
        "location": "Odesa, Ukraine",
        "elements": ["Ne", "Kr", "Xe"],
        "global_share_pct": 35.0,
        "criticality": 0.60,
        "color": "#f59e0b",
        "notes": "Pre-2022 supplied ~45% global neon. Disrupted; air separation plants expanding globally.",
    },
    # Taiwan downstream consumer
    {
        "node_id": "re-taiwan-consumer",
        "name": "TSMC / Taiwan Fab Cluster",
        "role": "consumer",
        "lat": 24.00,
        "lon": 121.00,
        "location": "Taiwan",
        "elements": ["Nd", "Pr", "Tb", "Dy", "Ne", "Kr"],
        "global_share_pct": 0.0,
        "criticality": 0.98,
        "color": "#ef4444",
        "notes": "End consumer; ~63% of advanced chip wafer starts depend on Chinese-processed REE.",
    },
]

# ── Supply Chain Dependency Graph ─────────────────────────────────────────────
# Directed edges (upstream → downstream) with flow_fraction and lead_time_days.
# flow_fraction: fraction of downstream node's input sourced from this edge.

DEPENDENCY_EDGES: list[dict[str, Any]] = [
    # REE flow: mines → China separation
    {"from": "re-baotou-mine",     "to": "re-china-separation", "flow_fraction": 0.58, "lead_time_days": 30},
    {"from": "re-jiangxi-mine",    "to": "re-china-separation", "flow_fraction": 0.30, "lead_time_days": 14},
    {"from": "re-sichuan-mine",    "to": "re-china-separation", "flow_fraction": 0.08, "lead_time_days": 21},
    # REE flow: separation → Taiwan consumer
    {"from": "re-china-separation","to": "re-taiwan-consumer",   "flow_fraction": 0.87, "lead_time_days": 45},
    {"from": "re-mp-usa",          "to": "re-taiwan-consumer",   "flow_fraction": 0.08, "lead_time_days": 60},
    {"from": "re-lynas-au",        "to": "re-taiwan-consumer",   "flow_fraction": 0.05, "lead_time_days": 50},
    # REE/noble gas → TSMC fabs
    {"from": "re-taiwan-consumer", "to": "tsmc-fab18",           "flow_fraction": 0.35, "lead_time_days": 7},
    {"from": "re-taiwan-consumer", "to": "tsmc-fab12",           "flow_fraction": 0.22, "lead_time_days": 7},
    {"from": "re-taiwan-consumer", "to": "tsmc-fab14",           "flow_fraction": 0.18, "lead_time_days": 7},
    {"from": "re-neon-ua",         "to": "tsmc-fab18",           "flow_fraction": 0.20, "lead_time_days": 90},
    {"from": "re-neon-ua",         "to": "tsmc-fab12",           "flow_fraction": 0.20, "lead_time_days": 90},
    # ASML subsystems → ASML assembly
    {"from": "asml-cymer",         "to": "asml-hq",              "flow_fraction": 1.00, "lead_time_days": 60},
    {"from": "asml-zeiss",         "to": "asml-hq",              "flow_fraction": 1.00, "lead_time_days": 90},
    # ASML → delivery ports
    {"from": "asml-hq",            "to": "asml-taiwan-port",     "flow_fraction": 0.52, "lead_time_days": 21},
    {"from": "asml-hq",            "to": "asml-korea-port",      "flow_fraction": 0.30, "lead_time_days": 18},
    {"from": "asml-hq",            "to": "asml-us-delivery",     "flow_fraction": 0.10, "lead_time_days": 14},
    # Delivery ports → TSMC fabs
    {"from": "asml-taiwan-port",   "to": "tsmc-fab18",           "flow_fraction": 0.55, "lead_time_days": 3},
    {"from": "asml-taiwan-port",   "to": "tsmc-fab12",           "flow_fraction": 0.30, "lead_time_days": 2},
    {"from": "asml-taiwan-port",   "to": "tsmc-fab14",           "flow_fraction": 0.15, "lead_time_days": 2},
    {"from": "asml-us-delivery",   "to": "tsmc-fab21-az",        "flow_fraction": 1.00, "lead_time_days": 2},
]

# All node IDs in one lookup dict (built lazily)
_ALL_NODES: dict[str, dict] | None = None


def _build_node_index() -> dict[str, dict]:
    global _ALL_NODES
    if _ALL_NODES is not None:
        return _ALL_NODES
    index: dict[str, dict] = {}
    for n in TSMC_FABS + ASML_EUV_NODES + RARE_EARTH_NODES:
        index[n["node_id"]] = n
    _ALL_NODES = index
    return index


# ── Disruption Cascade Simulator ─────────────────────────────────────────────

def _bfs_downstream(disrupted_id: str) -> list[str]:
    """BFS: collect all node IDs reachable downstream from disrupted_id."""
    downstream: list[str] = []
    frontier = [disrupted_id]
    visited = {disrupted_id}
    while frontier:
        current = frontier.pop(0)
        for edge in DEPENDENCY_EDGES:
            if edge["from"] == current and edge["to"] not in visited:
                visited.add(edge["to"])
                downstream.append(edge["to"])
                frontier.append(edge["to"])
    return downstream


def simulate_disruption(
    disrupted_node_id: str,
    disruption_severity: float = 1.0,
    horizon_days: int = 180,
) -> dict[str, Any]:
    """Cascade a supply disruption from `disrupted_node_id` downstream.

    disruption_severity: 0.0 (no effect) to 1.0 (complete severance).
    horizon_days: time window for impact assessment.

    Returns a cascade report: affected nodes, degradation estimates, lead-time to impact.
    """
    nodes = _build_node_index()
    if disrupted_node_id not in nodes:
        return {"error": f"node_not_found: {disrupted_node_id}"}

    origin = nodes[disrupted_node_id]
    downstream_ids = _bfs_downstream(disrupted_node_id)

    cascade: list[dict[str, Any]] = []
    for target_id in downstream_ids:
        # Find edges carrying flow into target_id from the disrupted sub-graph
        relevant_edges = [
            e for e in DEPENDENCY_EDGES
            if e["to"] == target_id and (
                e["from"] == disrupted_node_id or e["from"] in downstream_ids
            )
        ]
        if not relevant_edges:
            continue

        # Total flow_fraction lost from this disruption path
        lost_fraction = sum(e["flow_fraction"] for e in relevant_edges) * disruption_severity
        lost_fraction = min(1.0, lost_fraction)

        # Earliest impact = min lead_time across edges on the disruption path
        lead_days = min(e["lead_time_days"] for e in relevant_edges)
        if lead_days > horizon_days:
            continue

        target_node = nodes.get(target_id, {})
        criticality = target_node.get("criticality", 0.5)
        production_degradation = round(lost_fraction * criticality, 4)

        cascade.append({
            "node_id": target_id,
            "node_name": target_node.get("name", target_id),
            "location": target_node.get("location", "unknown"),
            "criticality": criticality,
            "flow_lost_fraction": round(lost_fraction, 4),
            "production_degradation": production_degradation,
            "lead_time_days": lead_days,
            "within_horizon": lead_days <= horizon_days,
            "color": _degradation_color(production_degradation),
        })

    cascade.sort(key=lambda r: r["production_degradation"], reverse=True)

    total_wafer_impact = _estimate_wafer_impact(cascade)

    return {
        "disrupted_node": disrupted_node_id,
        "disrupted_name": origin.get("name"),
        "disruption_severity": disruption_severity,
        "horizon_days": horizon_days,
        "affected_nodes": len(cascade),
        "total_wafer_degradation_pct": total_wafer_impact,
        "cascade": cascade,
    }


def _degradation_color(degradation: float) -> str:
    if degradation >= 0.60:
        return "#ef4444"
    if degradation >= 0.30:
        return "#f59e0b"
    return "#fbbf24"


def _estimate_wafer_impact(cascade: list[dict]) -> float:
    """Rough percentage of global advanced wafer capacity affected."""
    fab_ids = {"tsmc-fab18", "tsmc-fab12", "tsmc-fab14", "tsmc-fab21-az", "tsmc-jasm"}
    total_degradation = 0.0
    for entry in cascade:
        if entry["node_id"] in fab_ids:
            fab = next((f for f in TSMC_FABS if f["node_id"] == entry["node_id"]), None)
            if fab:
                total_degradation += entry["production_degradation"] * fab["capacity_share_pct"]
    return round(total_degradation, 2)


# ── Chokepoint Disruption Scenarios ──────────────────────────────────────────
# Pre-computed scenarios for key chokepoints / export control events.

DISRUPTION_SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "scn-taiwan-conflict",
        "label": "Taiwan Strait interdiction / conflict onset",
        "trigger_nodes": ["tsmc-fab18", "tsmc-fab12", "tsmc-fab14", "asml-taiwan-port"],
        "severity": 0.85,
        "horizon_days": 365,
        "description": (
            "Blockade or kinetic conflict severing TSMC fab operations and EUV delivery. "
            "Assessed 85% capacity disruption on advanced nodes. "
            "Global chip shortage within 3–6 months; recovery > 3 years."
        ),
        "color": "#ef4444",
    },
    {
        "scenario_id": "scn-china-ree-embargo",
        "label": "China rare earth export embargo",
        "trigger_nodes": ["re-china-separation"],
        "severity": 0.90,
        "horizon_days": 180,
        "description": (
            "Full halt to REE separation/export. Historical precedent: 2010 Japan embargo. "
            "Impact felt at fabs within 45–90 days (inventory depletion). "
            "Partial mitigation via US/Australia/EU alternate separation buildout (12–24 month lag)."
        ),
        "color": "#ef4444",
    },
    {
        "scenario_id": "scn-asml-export-ban",
        "label": "ASML EUV export ban expansion (DUV included)",
        "trigger_nodes": ["asml-hq"],
        "severity": 0.70,
        "horizon_days": 720,
        "description": (
            "Extension of existing EUV ban to include DUV (ArF immersion) systems. "
            "Blocks China fab capacity expansion; indirect supply tightening as China demand "
            "shifts to non-China foundries. Long-horizon impact 2–4 years."
        ),
        "color": "#f59e0b",
    },
    {
        "scenario_id": "scn-ukraine-neon",
        "label": "Noble gas supply disruption (neon/krypton)",
        "trigger_nodes": ["re-neon-ua"],
        "severity": 0.50,
        "horizon_days": 90,
        "description": (
            "Disruption to Ukraine neon supply (as occurred Feb–May 2022). "
            "Spot neon prices spiked 500%. Air separation capacity in US/EU partially offset. "
            "Recovery: ~4–6 months via alternative sourcing."
        ),
        "color": "#f59e0b",
    },
    {
        "scenario_id": "scn-asml-subsystem-loss",
        "label": "Cymer or Zeiss SMT catastrophic failure",
        "trigger_nodes": ["asml-cymer"],
        "severity": 0.80,
        "horizon_days": 365,
        "description": (
            "Fire/earthquake/flood at sole EUV light-source supplier (Cymer, San Diego) or "
            "optics supplier (Zeiss, Oberkochen). No alternative supplier exists. "
            "New EUV output halted; existing machines cannot be upgraded. Impact: 12–18 months."
        ),
        "color": "#ef4444",
    },
]


def run_scenario(scenario_id: str) -> dict[str, Any]:
    """Run a pre-defined disruption scenario, aggregating cascades from all trigger nodes."""
    scn = next((s for s in DISRUPTION_SCENARIOS if s["scenario_id"] == scenario_id), None)
    if scn is None:
        return {"error": f"scenario_not_found: {scenario_id}"}

    combined: dict[str, dict] = {}
    for node_id in scn["trigger_nodes"]:
        result = simulate_disruption(node_id, scn["severity"], scn["horizon_days"])
        for entry in result.get("cascade", []):
            nid = entry["node_id"]
            if nid not in combined or entry["production_degradation"] > combined[nid]["production_degradation"]:
                combined[nid] = entry

    cascade = sorted(combined.values(), key=lambda r: r["production_degradation"], reverse=True)
    total_wafer = _estimate_wafer_impact(cascade)

    return {
        "scenario_id": scenario_id,
        "label": scn["label"],
        "description": scn["description"],
        "severity": scn["severity"],
        "horizon_days": scn["horizon_days"],
        "trigger_nodes": scn["trigger_nodes"],
        "affected_nodes": len(cascade),
        "total_wafer_degradation_pct": total_wafer,
        "cascade": cascade,
        "color": scn["color"],
    }


# ── Haversine helper ──────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Supply Chain Exposure Map ─────────────────────────────────────────────────

def get_exposure_map() -> list[dict[str, Any]]:
    """Return all nodes with geographic coordinates for map overlay, criticality-sorted."""
    nodes = _build_node_index()
    rows = []
    for node in nodes.values():
        if "lat" not in node or "lon" not in node:
            continue
        rows.append({
            "node_id": node["node_id"],
            "name": node["name"],
            "role": node.get("role", node.get("category", "unknown")),
            "lat": node["lat"],
            "lon": node["lon"],
            "location": node.get("location", ""),
            "criticality": node.get("criticality", 0.5),
            "color": node.get("color", "#6b7280"),
            "notes": node.get("notes", ""),
        })
    rows.sort(key=lambda r: r["criticality"], reverse=True)
    return rows


# ── Rare Earth Flow Calculator ────────────────────────────────────────────────

def get_ree_flow(element: str | None = None) -> list[dict[str, Any]]:
    """Return rare-earth flow edges annotated with element filter.

    element: e.g. 'Nd', 'Ne'. None returns all edges involving any REE node.
    """
    ree_node_ids = {n["node_id"] for n in RARE_EARTH_NODES}
    flow_edges = [
        e for e in DEPENDENCY_EDGES
        if e["from"] in ree_node_ids or e["to"] in ree_node_ids
    ]
    nodes = _build_node_index()

    result = []
    for edge in flow_edges:
        from_node = nodes.get(edge["from"], {})
        to_node = nodes.get(edge["to"], {})

        # Element filter
        if element:
            from_elements = from_node.get("elements", [])
            if element not in from_elements and "all" not in from_elements:
                continue

        result.append({
            "from_id": edge["from"],
            "from_name": from_node.get("name", edge["from"]),
            "from_lat": from_node.get("lat"),
            "from_lon": from_node.get("lon"),
            "to_id": edge["to"],
            "to_name": to_node.get("name", edge["to"]),
            "to_lat": to_node.get("lat"),
            "to_lon": to_node.get("lon"),
            "flow_fraction": edge["flow_fraction"],
            "lead_time_days": edge["lead_time_days"],
            "elements": from_node.get("elements", []),
        })
    return result


# ── Summary API ───────────────────────────────────────────────────────────────

def get_summary() -> dict[str, Any]:
    """Dashboard aggregation — node counts, critical nodes, scenario index."""
    all_nodes = list(_build_node_index().values())
    critical = [n for n in all_nodes if n.get("criticality", 0) >= 0.90]
    return {
        "tsmc_fabs": len(TSMC_FABS),
        "asml_nodes": len(ASML_EUV_NODES),
        "ree_nodes": len(RARE_EARTH_NODES),
        "dependency_edges": len(DEPENDENCY_EDGES),
        "critical_nodes": len(critical),
        "disruption_scenarios": len(DISRUPTION_SCENARIOS),
        "top_critical_node": max(all_nodes, key=lambda n: n.get("criticality", 0))["name"],
        "taiwan_wafer_share_pct": sum(
            f["capacity_share_pct"] for f in TSMC_FABS
            if "Taiwan" in f.get("location", "")
        ),
    }
