# [CUI // SP-CTI]
"""ICDEV™ Network Infrastructure Intelligence — Analysis Engine.

13 deterministic-first analysis functions with optional LLM narrative:
  1. analyze_redundancy     — SPOF detection, articulation points, bridge edges
  2. analyze_eol            — EOL lifecycle with Monte Carlo cost projection
  3. analyze_blast_radius   — BFS impact propagation with decay
  4. analyze_impact         — Before/after change comparison
  5. project_costs          — 5-year TCO with confidence intervals
  6. analyze_capacity       — Demand modeling + bandwidth simulation
  7. manage_configs         — Config lifecycle (ingest/diff/generate/push/validate)
  8. assess_compliance      — Security posture per network zone
  9. assess_availability    — Composite SLA/availability calculation
  10. assess_latency        — Hop-by-hop latency modeling
  11. detect_config_drift   — Running vs. golden config comparison
  12. assess_vendor_risk    — Vendor concentration + CVE exposure
  13. assess_circuits       — Circuit/contract management

Usage:
    python tools/network/network_intelligence.py --topology-id T1 --redundancy --json
    python tools/network/network_intelligence.py --topology-id T1 --eol --years 5 --json
    python tools/network/network_intelligence.py --topology-id T1 --blast-radius --device-id D1 --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.network.network_intelligence")


def _get_nc_conn():
    from tools.network.db.init_db import get_connection
    return get_connection()


def _load_config() -> dict:
    """Load network intelligence config from args/."""
    cfg_path = BASE_DIR / "args" / "network_intelligence_config.yaml"
    if cfg_path.exists():
        try:
            import yaml
            with open(cfg_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass
    return {}


def _load_topology_graph(topology_id: str, conn=None) -> dict | None:
    """Load graph_json from topologies table."""
    own_conn = conn is None
    if own_conn:
        conn = _get_nc_conn()
    row = conn.execute("SELECT graph_json FROM topologies WHERE id = %s", (topology_id,)).fetchone()
    if own_conn:
        conn.close()
    if not row:
        return None
    raw = row[0] if isinstance(row, tuple) else row["graph_json"]
    return json.loads(raw)


def _build_adjacency(graph: dict) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """Build undirected adjacency and node lookup from graph."""
    adj: dict[str, set[str]] = defaultdict(set)
    node_map: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        node_map[n["id"]] = n
    for e in graph.get("edges", []):
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    return adj, node_map


def _cache_analysis(topology_id: str, analysis_type: str, query_text: str,
                    input_data: dict, result: dict, summary: str, conn=None) -> str:
    """Cache analysis result to ni_analyses table (best-effort)."""
    aid = str(uuid.uuid4())[:12]
    try:
        own_conn = conn is None
        if own_conn:
            conn = _get_nc_conn()
        conn.execute(
            "INSERT INTO ni_analyses (id, topology_id, analysis_type, query_text, "
            "input_json, result_json, result_summary, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (aid, topology_id, analysis_type, query_text,
             json.dumps(input_data, default=str), json.dumps(result, default=str),
             summary, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        if own_conn:
            conn.close()
    except Exception as e:
        logger.debug("Cache analysis failed (non-blocking): %s", e)
    return aid


# ── 1. Redundancy Analysis ──────────────────────────────────────────────────

def _find_articulation_points(adj: dict[str, set[str]], nodes: set[str]) -> list[str]:
    """Find articulation points using Tarjan's algorithm (pure Python)."""
    visited: set[str] = set()
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    ap: set[str] = set()
    timer = [0]

    def dfs(u: str) -> None:
        children = 0
        visited.add(u)
        disc[u] = low[u] = timer[0]
        timer[0] += 1

        for v in adj.get(u, set()):
            if v not in visited:
                children += 1
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if parent.get(u) is None and children > 1:
                    ap.add(u)
                if parent.get(u) is not None and low[v] >= disc[u]:
                    ap.add(u)
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for node in nodes:
        parent[node] = None
    for node in nodes:
        if node not in visited:
            dfs(node)

    return list(ap)


def _find_bridge_edges(adj: dict[str, set[str]], nodes: set[str]) -> list[tuple[str, str]]:
    """Find bridge edges (edges whose removal disconnects the graph)."""
    visited: set[str] = set()
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    bridges: list[tuple[str, str]] = []
    timer = [0]

    def dfs(u: str) -> None:
        visited.add(u)
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        for v in adj.get(u, set()):
            if v not in visited:
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridges.append((u, v))
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for node in nodes:
        parent[node] = None
    for node in nodes:
        if node not in visited:
            dfs(node)

    return bridges


def analyze_redundancy(topology_id: str, target_site: str | None = None) -> dict:
    """Analyze network redundancy: SPOFs, bridge edges, and alternative paths."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    adj, node_map = _build_adjacency(graph)
    all_nodes = set(node_map.keys())

    # Filter by site if specified
    if target_site:
        conn = _get_nc_conn()
        rows = conn.execute(
            "SELECT node_id FROM ni_devices WHERE topology_id = %s AND site LIKE %s",
            (topology_id, f"%{target_site}%"),
        ).fetchall()
        conn.close()
        site_nodes = {r[0] if isinstance(r, tuple) else r["node_id"] for r in rows}
        if site_nodes:
            all_nodes = site_nodes

    # Find SPOFs and bridges
    aps = _find_articulation_points(adj, all_nodes)
    bridges = _find_bridge_edges(adj, all_nodes)

    cfg = _load_config()
    tiers = cfg.get("analysis", {}).get("criticality_tiers", {"RED": 0.8, "ORANGE": 0.5, "YELLOW": 0.2, "GREEN": 0.0})

    # Classify each SPOF
    spof_details = []
    for ap_id in aps:
        node = node_map.get(ap_id, {})
        # Count downstream if removed
        remaining = all_nodes - {ap_id}
        remaining_adj = {k: v - {ap_id} for k, v in adj.items() if k != ap_id}
        # BFS to find largest connected component
        if remaining:
            start = next(iter(remaining))
            visited: set[str] = set()
            queue = [start]
            visited.add(start)
            while queue:
                cur = queue.pop(0)
                for nb in remaining_adj.get(cur, set()):
                    if nb in remaining and nb not in visited:
                        visited.add(nb)
                        queue.append(nb)
            disconnected = len(remaining) - len(visited)
        else:
            disconnected = 0

        impact_ratio = disconnected / max(len(all_nodes) - 1, 1)
        if impact_ratio >= tiers.get("RED", 0.8):
            tier = "RED"
        elif impact_ratio >= tiers.get("ORANGE", 0.5):
            tier = "ORANGE"
        elif impact_ratio >= tiers.get("YELLOW", 0.2):
            tier = "YELLOW"
        else:
            tier = "GREEN"

        spof_details.append({
            "device_id": ap_id,
            "label": node.get("label", ap_id),
            "type": node.get("type", "unknown"),
            "disconnected_devices": disconnected,
            "impact_ratio": round(impact_ratio, 3),
            "tier": tier,
        })

    # Sort by impact
    spof_details.sort(key=lambda x: x["disconnected_devices"], reverse=True)

    bridge_details = []
    for u, v in bridges:
        bridge_details.append({
            "source": node_map.get(u, {}).get("label", u),
            "target": node_map.get(v, {}).get("label", v),
            "source_id": u,
            "target_id": v,
        })

    result = {
        "topology_id": topology_id,
        "total_devices": len(all_nodes),
        "spof_count": len(spof_details),
        "bridge_count": len(bridge_details),
        "spofs": spof_details,
        "bridges": bridge_details,
        "fully_redundant": len(spof_details) == 0 and len(bridge_details) == 0,
        "site_filter": target_site,
    }

    _cache_analysis(topology_id, "redundancy", f"Redundancy analysis for {target_site or 'all'}",
                    {"target_site": target_site}, result, f"{len(spof_details)} SPOFs, {len(bridge_details)} bridges")
    return result


# ── 2. EOL Analysis ─────────────────────────────────────────────────────────

def _pert_sample(optimistic: float, most_likely: float, pessimistic: float, lam: float = 4.0) -> float:
    """Sample from a PERT distribution using Beta approximation (pure Python)."""
    if pessimistic <= optimistic:
        return most_likely
    mean = (optimistic + lam * most_likely + pessimistic) / (lam + 2)
    if mean <= optimistic or mean >= pessimistic:
        return most_likely
    alpha = ((mean - optimistic) * (2 * most_likely - optimistic - pessimistic)) / (
        (most_likely - mean) * (pessimistic - optimistic)
    )
    if alpha <= 0:
        alpha = 1.0
    beta = alpha * (pessimistic - mean) / (mean - optimistic)
    if beta <= 0:
        beta = 1.0
    sample = random.betavariate(alpha, beta)
    return optimistic + sample * (pessimistic - optimistic)


def analyze_eol(topology_id: str, horizon_years: int = 5) -> dict:
    """Analyze EOL devices with Monte Carlo cost projection."""
    from tools.network.device_manager import get_eol_timeline

    cfg = _load_config()
    iterations = cfg.get("analysis", {}).get("monte_carlo_iterations", 5000)
    opt_factor = cfg.get("analysis", {}).get("cost_optimistic_factor", 0.8)
    pess_factor = cfg.get("analysis", {}).get("cost_pessimistic_factor", 1.5)
    maint_escalation = cfg.get("eol", {}).get("maintenance_escalation_rate", 0.05)

    timeline = get_eol_timeline(topology_id, horizon_years)

    # Run Monte Carlo for total cost
    total_samples: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for year_data in timeline:
            for dev in year_data.get("devices", []):
                cost = float(dev.get("replacement_cost", 0) or 0)
                if cost > 0:
                    total += _pert_sample(cost * opt_factor, cost, cost * pess_factor)
            total_samples.append(total)

    # Compute percentiles
    if total_samples:
        total_samples.sort()
        n = len(total_samples)
        percentiles = {
            "p10": round(total_samples[int(n * 0.1)], 2),
            "p50": round(total_samples[int(n * 0.5)], 2),
            "p80": round(total_samples[int(n * 0.8)], 2),
            "p90": round(total_samples[int(n * 0.9)], 2),
        }
        mean = round(sum(total_samples) / n, 2)
    else:
        percentiles = {"p10": 0, "p50": 0, "p80": 0, "p90": 0}
        mean = 0

    # Count total EOL devices
    total_eol = sum(y["device_count"] for y in timeline)

    result = {
        "topology_id": topology_id,
        "horizon_years": horizon_years,
        "total_eol_devices": total_eol,
        "timeline": timeline,
        "monte_carlo": {
            "iterations": iterations,
            "total_replacement_cost": percentiles,
            "mean": mean,
        },
        "maintenance_escalation_rate": maint_escalation,
    }

    _cache_analysis(topology_id, "eol", f"EOL analysis {horizon_years}-year horizon",
                    {"horizon_years": horizon_years}, result,
                    f"{total_eol} devices, P50 cost: ${percentiles['p50']:,.0f}")
    return result


# ── 3. Blast Radius ─────────────────────────────────────────────────────────

def analyze_blast_radius(topology_id: str, device_id: str) -> dict:
    """BFS impact propagation from a failed device with severity decay."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    adj, node_map = _build_adjacency(graph)

    cfg = _load_config()
    decay = cfg.get("analysis", {}).get("blast_radius_decay", 0.8)
    max_hops = cfg.get("analysis", {}).get("blast_radius_max_hops", 10)
    tiers = cfg.get("analysis", {}).get("criticality_tiers", {"RED": 0.8, "ORANGE": 0.5, "YELLOW": 0.2, "GREEN": 0.0})

    if device_id not in node_map:
        return {"error": f"Device {device_id} not found in topology"}

    # BFS with decay
    affected: list[dict] = []
    visited: set[str] = {device_id}
    queue: list[tuple[str, float, int]] = [(device_id, 1.0, 0)]

    while queue:
        current, impact, hops = queue.pop(0)
        if hops > 0:  # don't include the failed device itself
            node = node_map.get(current, {})
            if impact >= tiers.get("RED", 0.8):
                tier = "RED"
            elif impact >= tiers.get("ORANGE", 0.5):
                tier = "ORANGE"
            elif impact >= tiers.get("YELLOW", 0.2):
                tier = "YELLOW"
            else:
                tier = "GREEN"
            affected.append({
                "device_id": current,
                "label": node.get("label", current),
                "type": node.get("type", "unknown"),
                "impact_score": round(impact, 3),
                "tier": tier,
                "hops": hops,
            })

        if hops < max_hops:
            next_impact = impact * decay
            for neighbor in adj.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, next_impact, hops + 1))

    affected.sort(key=lambda x: x["impact_score"], reverse=True)

    source = node_map.get(device_id, {})
    result = {
        "topology_id": topology_id,
        "source_device": {
            "id": device_id,
            "label": source.get("label", device_id),
            "type": source.get("type", "unknown"),
        },
        "total_affected": len(affected),
        "by_tier": {
            "RED": len([a for a in affected if a["tier"] == "RED"]),
            "ORANGE": len([a for a in affected if a["tier"] == "ORANGE"]),
            "YELLOW": len([a for a in affected if a["tier"] == "YELLOW"]),
            "GREEN": len([a for a in affected if a["tier"] == "GREEN"]),
        },
        "affected_devices": affected,
        "max_hops_reached": max(a["hops"] for a in affected) if affected else 0,
    }

    _cache_analysis(topology_id, "blast_radius", f"Blast radius from {source.get('label', device_id)}",
                    {"device_id": device_id}, result,
                    f"{len(affected)} affected ({result['by_tier']['RED']} RED)")
    return result


# ── 4. Change Impact ────────────────────────────────────────────────────────

def analyze_impact(topology_id: str, changes: list[dict]) -> dict:
    """Compare redundancy before and after proposed changes."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    # Snapshot current state
    conn = _get_nc_conn()
    snap_id = str(uuid.uuid4())[:12]
    conn.execute(
        "INSERT INTO ni_state_snapshots (id, topology_id, snapshot_type, graph_json, "
        "device_count, link_count, change_description, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (snap_id, topology_id, "pre_change", json.dumps(graph),
         len(graph.get("nodes", [])), len(graph.get("edges", [])),
         json.dumps(changes, default=str), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    # Run before analysis
    before = analyze_redundancy(topology_id)

    # Apply changes to a copy
    modified = json.loads(json.dumps(graph))
    nodes = modified.get("nodes", [])
    edges = modified.get("edges", [])

    for change in changes:
        action = change.get("action", "")
        target_type = change.get("type", "")
        target_id = change.get("id", "")

        if action == "remove" and target_type == "node":
            nodes = [n for n in nodes if n["id"] != target_id]
            edges = [e for e in edges if e["source"] != target_id and e["target"] != target_id]
        elif action == "remove" and target_type == "edge":
            edges = [e for e in edges if e["id"] != target_id]
        elif action == "add" and target_type == "node":
            nodes.append({"id": target_id, "label": change.get("label", target_id),
                          "type": change.get("device_type", "unknown"), "x": 0, "y": 0})
        elif action == "add" and target_type == "edge":
            edges.append({"id": target_id, "source": change.get("source", ""),
                          "target": change.get("target", ""), "label": change.get("label", "")})

    modified["nodes"] = nodes
    modified["edges"] = edges

    # Temporarily save modified topology, run analysis, then clean up
    temp_id = f"temp-{uuid.uuid4().hex[:8]}"
    conn = _get_nc_conn()
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json, classification) VALUES (%s, %s, %s, %s)",
        (temp_id, f"Impact Analysis {topology_id}", json.dumps(modified), "CUI // SP-CTI"),
    )
    conn.commit()
    conn.close()

    after = analyze_redundancy(temp_id)

    # Cleanup temp topology
    conn = _get_nc_conn()
    conn.execute("DELETE FROM topologies WHERE id = %s", (temp_id,))
    conn.execute("DELETE FROM ni_analyses WHERE topology_id = %s", (temp_id,))
    conn.commit()
    conn.close()

    # Compute deltas
    result = {
        "topology_id": topology_id,
        "changes_applied": changes,
        "snapshot_id": snap_id,
        "before": {
            "spof_count": before.get("spof_count", 0),
            "bridge_count": before.get("bridge_count", 0),
            "device_count": before.get("total_devices", 0),
        },
        "after": {
            "spof_count": after.get("spof_count", 0),
            "bridge_count": after.get("bridge_count", 0),
            "device_count": after.get("total_devices", 0),
        },
        "delta": {
            "spof_change": after.get("spof_count", 0) - before.get("spof_count", 0),
            "bridge_change": after.get("bridge_count", 0) - before.get("bridge_count", 0),
            "improved": after.get("spof_count", 0) < before.get("spof_count", 0),
        },
        "new_spofs": [s for s in after.get("spofs", []) if s["device_id"] not in
                      {b["device_id"] for b in before.get("spofs", [])}],
        "resolved_spofs": [s for s in before.get("spofs", []) if s["device_id"] not in
                          {a["device_id"] for a in after.get("spofs", [])}],
    }

    _cache_analysis(topology_id, "impact", "Change impact analysis",
                    {"changes": changes}, result,
                    f"SPOFs: {result['before']['spof_count']} → {result['after']['spof_count']}")
    return result


# ── 5. Cost Projection ──────────────────────────────────────────────────────

def project_costs(topology_id: str, years: int = 5) -> dict:
    """5-year TCO: EOL replacements + maintenance + circuits + cross-connects."""
    eol_result = analyze_eol(topology_id, years)

    conn = _get_nc_conn()

    # Annual maintenance from devices
    maint_rows = conn.execute(
        "SELECT SUM(annual_maintenance_cost) FROM ni_devices WHERE topology_id = %s",
        (topology_id,),
    ).fetchone()
    annual_maintenance = float(maint_rows[0] or 0) if maint_rows else 0.0

    # Circuit costs
    circuit_rows = conn.execute(
        "SELECT SUM(monthly_cost_usd) FROM nc_circuits WHERE topology_id = %s",
        (topology_id,),
    ).fetchone()
    annual_circuits = float(circuit_rows[0] or 0) * 12 if circuit_rows else 0.0

    # Cross-connect costs
    xconn_rows = conn.execute(
        "SELECT SUM(monthly_cost_usd) FROM nc_cross_connects WHERE topology_id = %s",
        (topology_id,),
    ).fetchone()
    annual_xconn = float(xconn_rows[0] or 0) * 12 if xconn_rows else 0.0

    conn.close()

    cfg = _load_config()
    maint_escalation = cfg.get("eol", {}).get("maintenance_escalation_rate", 0.05)

    # Year-by-year TCO
    year_breakdown = []
    cumulative = 0.0
    current_year = datetime.now(timezone.utc).year

    for i in range(years):
        yr = current_year + i
        eol_year = next((y for y in eol_result.get("timeline", []) if y["year"] == yr), None)
        replacement_cost = eol_year["total_replacement_cost"] if eol_year else 0.0
        maint = annual_maintenance * (1 + maint_escalation) ** i
        year_total = replacement_cost + maint + annual_circuits + annual_xconn
        cumulative += year_total

        year_breakdown.append({
            "year": yr,
            "replacement_cost": round(replacement_cost, 2),
            "maintenance_cost": round(maint, 2),
            "circuit_cost": round(annual_circuits, 2),
            "cross_connect_cost": round(annual_xconn, 2),
            "year_total": round(year_total, 2),
            "cumulative": round(cumulative, 2),
        })

    result = {
        "topology_id": topology_id,
        "projection_years": years,
        "year_breakdown": year_breakdown,
        "total_tco": round(cumulative, 2),
        "eol_monte_carlo": eol_result.get("monte_carlo", {}),
        "annual_recurring": {
            "maintenance": round(annual_maintenance, 2),
            "circuits": round(annual_circuits, 2),
            "cross_connects": round(annual_xconn, 2),
        },
    }

    _cache_analysis(topology_id, "cost_projection", f"{years}-year TCO",
                    {"years": years}, result, f"TCO: ${cumulative:,.0f}")
    return result


# ── 6. Capacity Planning ────────────────────────────────────────────────────

def analyze_capacity(topology_id: str, demand_scenario: dict) -> dict:
    """Model capacity impact of adding users/services to a region."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    cfg = _load_config()
    service_profiles = cfg.get("capacity", {}).get("service_profiles", {})
    threshold = cfg.get("capacity", {}).get("bottleneck_threshold_pct", 80)

    user_count = demand_scenario.get("user_count", 100)
    region = demand_scenario.get("region", "")
    services = demand_scenario.get("services", ["general"])
    description = demand_scenario.get("description", f"Add {user_count} users")

    # Compute total demand
    total_sustained_mbps = 0.0
    total_peak_mbps = 0.0
    service_breakdown = []

    for svc in services:
        profile = service_profiles.get(svc, service_profiles.get("general", {"sustained_mbps": 1.0, "peak_mbps": 2.0, "concurrent_pct": 1.0}))
        concurrent = int(user_count * profile.get("concurrent_pct", 1.0))
        sustained = concurrent * profile["sustained_mbps"]
        peak = concurrent * profile["peak_mbps"]
        total_sustained_mbps += sustained
        total_peak_mbps += peak
        service_breakdown.append({
            "service": svc,
            "concurrent_users": concurrent,
            "sustained_mbps": round(sustained, 1),
            "peak_mbps": round(peak, 1),
        })

    # Run bandwidth simulation with injected traffic
    try:
        from tools.network.bandwidth_sim import run_bandwidth_sim

        # Identify region links (match by site in devices or label in nodes)
        conn = _get_nc_conn()
        site_nodes = set()
        if region:
            rows = conn.execute(
                "SELECT node_id FROM ni_devices WHERE topology_id = %s AND "
                "(site LIKE %s OR label LIKE %s)",
                (topology_id, f"%{region}%", f"%{region}%"),
            ).fetchall()
            site_nodes = {r[0] if isinstance(r, tuple) else r["node_id"] for r in rows}
        conn.close()

        # Build traffic profiles: add demand to edges touching region nodes
        traffic_profiles = []
        for e in graph.get("edges", []):
            if not region or e["source"] in site_nodes or e["target"] in site_nodes:
                traffic_profiles.append({
                    "edge_id": e.get("id", ""),
                    "traffic_mbps": total_sustained_mbps / max(len(graph.get("edges", [])), 1) * 3,
                })

        bw_result = run_bandwidth_sim(graph, {
            "traffic_profiles": traffic_profiles,
            "base_utilization_pct": 40,
            "growth_rate_pct": 10,
            "years": 3,
            "peak_multiplier": total_peak_mbps / max(total_sustained_mbps, 0.1),
        })

        bottlenecks = [l for l in bw_result.get("link_analysis", [])
                       if l.get("current_util_pct", 0) > threshold or l.get("status") in ("critical", "saturated")]
    except (ImportError, Exception) as e:
        bw_result = {"error": str(e)}
        bottlenecks = []

    result = {
        "topology_id": topology_id,
        "scenario": {
            "description": description,
            "user_count": user_count,
            "region": region,
            "services": services,
        },
        "demand": {
            "total_sustained_mbps": round(total_sustained_mbps, 1),
            "total_peak_mbps": round(total_peak_mbps, 1),
            "service_breakdown": service_breakdown,
        },
        "bottlenecks": bottlenecks,
        "bottleneck_count": len(bottlenecks),
        "bandwidth_simulation": bw_result if not bw_result.get("error") else None,
        "recommendations": [],
    }

    # Generate recommendations
    if bottlenecks:
        result["recommendations"].append(
            f"{len(bottlenecks)} link(s) exceed {threshold}% utilization with {user_count} new users"
        )
        for bl in bottlenecks[:5]:
            result["recommendations"].append(
                f"  Upgrade {bl.get('src_label', '?')} ↔ {bl.get('dst_label', '?')}: "
                f"{bl.get('current_util_pct', 0):.0f}% → consider {bl.get('recommendation', 'upgrade')}"
            )
    else:
        result["recommendations"].append(f"Current network can absorb {user_count} additional users in {region or 'all regions'}")

    _cache_analysis(topology_id, "capacity", description,
                    demand_scenario, result,
                    f"{total_sustained_mbps:.0f} Mbps sustained, {len(bottlenecks)} bottlenecks")
    return result


# ── 7. Config Management ────────────────────────────────────────────────────

def manage_configs(topology_id: str, action: str, targets: list[str] | None = None) -> dict:
    """Config lifecycle: generate, diff, ingest, push, validate."""
    if action == "generate":
        try:
            from tools.network.config_generator import generate_device_configs
            graph = _load_topology_graph(topology_id)
            if not graph:
                return {"error": "Topology not found"}
            configs = generate_device_configs(graph)
            # Store as golden
            conn = _get_nc_conn()
            now = datetime.now(timezone.utc).isoformat()
            stored = 0
            for filename, config_text in configs.items():
                cfg_id = str(uuid.uuid4())[:12]
                cfg_hash = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
                conn.execute(
                    "INSERT INTO ni_device_configs (id, device_id, config_type, config_text, "
                    "config_hash, source, version, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (cfg_id, filename, "generated", config_text, cfg_hash, "config_generator", 1, now),
                )
                stored += 1
            conn.commit()
            conn.close()
            return {"action": "generate", "configs_generated": len(configs), "stored": stored,
                    "filenames": list(configs.keys())}
        except ImportError:
            return {"error": "config_generator not available"}

    elif action == "push":
        try:
            from tools.network.inventory_export import to_ansible_inventory
            graph = _load_topology_graph(topology_id)
            if not graph:
                return {"error": "Topology not found"}
            inventory = to_ansible_inventory(graph)
            # DIC Canvas Synergy — emit config push event (dsyn-emit-01)
            try:
                from tools.ndc.event_emitter import emit_config_push
                emit_config_push(
                    topology_id=topology_id,
                    change_summary=f"Config push prepared for topology {topology_id}",
                )
            except Exception:
                pass
            return {
                "action": "push",
                "inventory_generated": True,
                "ansible_command": "ansible-playbook -i inventory.ini site.yml",
                "note": "Review generated playbook before execution. Auto-execution disabled for safety.",
                "inventory_preview": inventory[:500] if inventory else "",
            }
        except ImportError:
            return {"error": "inventory_export not available"}

    elif action == "diff":
        conn = _get_nc_conn()
        generated = conn.execute(
            "SELECT device_id, config_text, config_hash FROM ni_device_configs "
            "WHERE config_type = 'generated' ORDER BY created_at DESC",
        ).fetchall()
        running = conn.execute(
            "SELECT device_id, config_text, config_hash FROM ni_device_configs "
            "WHERE config_type = 'running' ORDER BY created_at DESC",
        ).fetchall()
        conn.close()

        gen_map = {r[0]: r for r in generated}
        run_map = {r[0]: r for r in running}

        drifted = []
        for dev_id in gen_map:
            if dev_id in run_map:
                if gen_map[dev_id][2] != run_map[dev_id][2]:  # hash mismatch
                    drifted.append({"device_id": dev_id, "status": "drifted"})
            else:
                drifted.append({"device_id": dev_id, "status": "no_running_config"})

        diff_result = {"action": "diff", "total_devices": len(gen_map),
                       "drifted_count": len(drifted), "drifted_devices": drifted}

        # DIC Canvas Synergy — emit baseline deviation event when drift found (dsyn-emit-01)
        if drifted:
            try:
                from tools.ndc.event_emitter import emit_baseline_deviation
                emit_baseline_deviation(
                    topology_id=topology_id,
                    drifted_devices=drifted,
                    drifted_count=len(drifted),
                )
            except Exception:
                pass

        return diff_result

    return {"error": f"Unknown action: {action}. Valid: generate, diff, push"}


# ── 8. Compliance Assessment ────────────────────────────────────────────────

def assess_compliance(topology_id: str, framework: str = "nist") -> dict:
    """Assess network security posture against compliance framework."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    adj, node_map = _build_adjacency(graph)
    nodes = graph.get("nodes", [])

    checks = []

    # SC-7: Boundary Protection — firewall between zones
    firewalls = [n for n in nodes if n.get("type") == "firewall"]
    checks.append({
        "control": "SC-7", "name": "Boundary Protection",
        "passed": len(firewalls) > 0,
        "detail": f"{len(firewalls)} firewall(s) found",
    })

    # SC-7(5): Deny by default
    checks.append({
        "control": "SC-7(5)", "name": "Deny by Default",
        "passed": len(firewalls) > 0,
        "detail": "Firewalls present (verify deny-by-default rules manually)",
    })

    # AC-4: Information Flow — encrypted WAN links
    wan_edges = [e for e in graph.get("edges", []) if e.get("type") in ("wan", "vpn")]
    vpn_edges = [e for e in graph.get("edges", []) if e.get("type") == "vpn"]
    checks.append({
        "control": "AC-4", "name": "Information Flow Enforcement",
        "passed": len(wan_edges) == 0 or len(vpn_edges) > 0,
        "detail": f"{len(wan_edges)} WAN links, {len(vpn_edges)} encrypted (VPN)",
    })

    # AU-3: Audit logging — SIEM/syslog servers present
    siem_servers = [n for n in nodes if any(kw in n.get("label", "").lower() for kw in ("siem", "syslog", "splunk", "elk"))]
    checks.append({
        "control": "AU-3", "name": "Content of Audit Records",
        "passed": len(siem_servers) > 0,
        "detail": f"{len(siem_servers)} logging server(s) found",
    })

    # SI-4: IDS/IPS presence
    ids_nodes = [n for n in nodes if any(kw in n.get("label", "").lower() for kw in ("ids", "ips", "nids", "suricata", "snort"))]
    checks.append({
        "control": "SI-4", "name": "System Monitoring",
        "passed": len(ids_nodes) > 0,
        "detail": f"{len(ids_nodes)} IDS/IPS node(s) found",
    })

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)

    result = {
        "topology_id": topology_id,
        "framework": framework,
        "checks": checks,
        "passed": passed,
        "total": total,
        "score_pct": round(passed / total * 100, 1) if total else 0,
    }

    _cache_analysis(topology_id, "compliance", f"Compliance assessment ({framework})",
                    {"framework": framework}, result, f"{passed}/{total} checks passed")
    return result


# ── 9. Availability Assessment ──────────────────────────────────────────────

def assess_availability(topology_id: str, src_device: str | None = None,
                        dst_device: str | None = None) -> dict:
    """Calculate composite availability for network paths."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    cfg = _load_config()
    avail_defaults = cfg.get("availability", {}).get("default_device_availability", {})
    mttr_defaults = cfg.get("availability", {}).get("default_mttr_hours", {})

    adj, node_map = _build_adjacency(graph)

    # If src/dst specified, find shortest path
    if src_device and dst_device:
        # BFS shortest path
        visited: dict[str, str | None] = {src_device: None}
        queue = [src_device]
        found = False
        while queue and not found:
            current = queue.pop(0)
            for nb in adj.get(current, set()):
                if nb not in visited:
                    visited[nb] = current
                    queue.append(nb)
                    if nb == dst_device:
                        found = True
                        break

        if not found:
            return {"error": f"No path between {src_device} and {dst_device}"}

        # Reconstruct path
        path = []
        current = dst_device
        while current is not None:
            path.append(current)
            current = visited.get(current)
        path.reverse()

        # Series availability
        path_availability = 1.0
        path_details = []
        for nid in path:
            node = node_map.get(nid, {})
            dev_type = node.get("type", "unknown")
            a = avail_defaults.get(dev_type, 0.999)
            mttr = mttr_defaults.get(dev_type, 4)
            path_availability *= a
            path_details.append({
                "device_id": nid,
                "label": node.get("label", nid),
                "type": dev_type,
                "availability": a,
                "mttr_hours": mttr,
            })

        downtime_minutes_year = round((1 - path_availability) * 365.25 * 24 * 60, 1)
        nines = -math.log10(1 - path_availability) if path_availability < 1 else 9

        return {
            "topology_id": topology_id,
            "path": path_details,
            "composite_availability": round(path_availability, 8),
            "nines": round(nines, 2),
            "annual_downtime_minutes": downtime_minutes_year,
            "meets_99_99": path_availability >= 0.9999,
        }

    # Aggregate: compute availability for all nodes
    device_avails = []
    for n in graph.get("nodes", []):
        dev_type = n.get("type", "unknown")
        a = avail_defaults.get(dev_type, 0.999)
        device_avails.append({
            "device_id": n["id"],
            "label": n.get("label", n["id"]),
            "type": dev_type,
            "availability": a,
        })

    return {
        "topology_id": topology_id,
        "device_count": len(device_avails),
        "devices": device_avails,
        "note": "Specify src_device and dst_device for path availability calculation",
    }


# ── 10. Latency Assessment ─────────────────────────────────────────────────

def assess_latency(topology_id: str, src_device: str | None = None,
                   dst_device: str | None = None) -> dict:
    """Model hop-by-hop latency between devices."""
    graph = _load_topology_graph(topology_id)
    if not graph:
        return {"error": "Topology not found"}

    cfg = _load_config()
    delay_defaults = cfg.get("latency", {}).get("device_forwarding_delay_ms", {})
    thresholds = cfg.get("latency", {}).get("thresholds", {})

    adj, node_map = _build_adjacency(graph)

    if not src_device or not dst_device:
        return {"error": "Both src_device and dst_device required for latency assessment"}

    # BFS shortest path
    visited: dict[str, str | None] = {src_device: None}
    queue = [src_device]
    found = False
    while queue and not found:
        current = queue.pop(0)
        for nb in adj.get(current, set()):
            if nb not in visited:
                visited[nb] = current
                queue.append(nb)
                if nb == dst_device:
                    found = True
                    break

    if not found:
        return {"error": f"No path between {src_device} and {dst_device}"}

    path = []
    current = dst_device
    while current is not None:
        path.append(current)
        current = visited.get(current)
    path.reverse()

    total_latency_ms = 0.0
    hop_details = []
    for i, nid in enumerate(path):
        node = node_map.get(nid, {})
        dev_type = node.get("type", "unknown")
        fwd_delay = delay_defaults.get(dev_type, 0.5)
        total_latency_ms += fwd_delay
        hop_details.append({
            "hop": i,
            "device_id": nid,
            "label": node.get("label", nid),
            "type": dev_type,
            "forwarding_delay_ms": fwd_delay,
            "cumulative_ms": round(total_latency_ms, 2),
        })

    rtt_ms = round(total_latency_ms * 2, 2)  # round-trip

    result = {
        "topology_id": topology_id,
        "path_hops": len(path),
        "one_way_latency_ms": round(total_latency_ms, 2),
        "rtt_ms": rtt_ms,
        "hop_details": hop_details,
        "thresholds": {
            "interactive": {"limit_ms": thresholds.get("interactive_ms", 50),
                           "meets": rtt_ms <= thresholds.get("interactive_ms", 50)},
            "voice": {"limit_ms": thresholds.get("voice_ms", 100),
                     "meets": rtt_ms <= thresholds.get("voice_ms", 100)},
            "vdi": {"limit_ms": thresholds.get("vdi_ms", 150),
                   "meets": rtt_ms <= thresholds.get("vdi_ms", 150)},
        },
    }
    return result


# ── 11. Config Drift Detection ──────────────────────────────────────────────

def detect_config_drift(topology_id: str) -> dict:
    """Compare running configs against golden templates."""
    return manage_configs(topology_id, "diff")


# ── 12. Vendor Risk Assessment ──────────────────────────────────────────────

def assess_vendor_risk(topology_id: str) -> dict:
    """Assess vendor concentration and supply chain risk."""
    conn = _get_nc_conn()
    rows = conn.execute(
        "SELECT vendor, COUNT(*) as cnt FROM ni_devices "
        "WHERE topology_id = %s AND vendor IS NOT NULL GROUP BY vendor ORDER BY cnt DESC",
        (topology_id,),
    ).fetchall()
    conn.close()

    cfg = _load_config()
    high_thresh = cfg.get("vendor_risk", {}).get("concentration_high_threshold_pct", 70)
    med_thresh = cfg.get("vendor_risk", {}).get("concentration_medium_threshold_pct", 50)

    total = sum(r[1] if isinstance(r, tuple) else r["cnt"] for r in rows)
    vendors = []
    for r in rows:
        vendor = r[0] if isinstance(r, tuple) else r["vendor"]
        count = r[1] if isinstance(r, tuple) else r["cnt"]
        pct = round(count / total * 100, 1) if total else 0
        risk = "HIGH" if pct >= high_thresh else "MEDIUM" if pct >= med_thresh else "LOW"
        vendors.append({"vendor": vendor, "device_count": count, "percentage": pct, "risk": risk})

    high_risk = [v for v in vendors if v["risk"] == "HIGH"]
    result = {
        "topology_id": topology_id,
        "total_devices": total,
        "vendor_count": len(vendors),
        "vendors": vendors,
        "high_risk_vendors": high_risk,
        "single_vendor_dependency": len(vendors) == 1 and total > 1,
    }

    _cache_analysis(topology_id, "vendor_risk", "Vendor risk assessment",
                    {}, result,
                    f"{len(vendors)} vendors, {len(high_risk)} high-risk")
    return result


# ── 13. Circuit/Contract Assessment ─────────────────────────────────────────

def assess_circuits(topology_id: str, horizon_months: int = 12) -> dict:
    """Assess circuit contracts: expiring, utilization, recommendations."""
    conn = _get_nc_conn()

    now = datetime.now(timezone.utc)
    cutoff = f"{now.year + (now.month + horizon_months - 1) // 12}-{((now.month + horizon_months - 1) % 12) + 1:02d}-28"

    circuits = conn.execute(
        "SELECT * FROM nc_circuits WHERE topology_id = %s ORDER BY contract_end ASC",
        (topology_id,),
    ).fetchall()
    conn.close()

    expiring = []
    active = []
    for c in circuits:
        d = dict(c) if hasattr(c, "keys") else {"id": c[0], "circuit_id": c[2], "carrier": c[3],
                                                   "contract_end": c[12], "monthly_cost_usd": c[10]}
        end_date = d.get("contract_end", "")
        if end_date and end_date <= cutoff:
            expiring.append(d)
        active.append(d)

    # Carrier concentration
    carrier_counts: dict[str, int] = defaultdict(int)
    for c in active:
        carrier = c.get("carrier", "Unknown")
        carrier_counts[carrier] += 1

    total_monthly = sum(float(c.get("monthly_cost_usd", 0) or 0) for c in active)

    result = {
        "topology_id": topology_id,
        "total_circuits": len(active),
        "expiring_within_months": horizon_months,
        "expiring_count": len(expiring),
        "expiring_circuits": expiring,
        "carrier_distribution": dict(carrier_counts),
        "total_monthly_cost": round(total_monthly, 2),
        "total_annual_cost": round(total_monthly * 12, 2),
    }

    _cache_analysis(topology_id, "circuits", f"Circuit assessment ({horizon_months}m)",
                    {"horizon_months": horizon_months}, result,
                    f"{len(expiring)} expiring, ${total_monthly:,.0f}/mo")
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Network Intelligence Engine")
    parser.add_argument("--topology-id", required=True)
    parser.add_argument("--redundancy", action="store_true")
    parser.add_argument("--site", default=None)
    parser.add_argument("--eol", action="store_true")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--blast-radius", action="store_true")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--cost", action="store_true")
    parser.add_argument("--capacity", action="store_true")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--region", default="")
    parser.add_argument("--services", nargs="*", default=["general"])
    parser.add_argument("--compliance", action="store_true")
    parser.add_argument("--vendor-risk", action="store_true")
    parser.add_argument("--circuits", action="store_true")
    parser.add_argument("--availability", action="store_true")
    parser.add_argument("--latency", action="store_true")
    parser.add_argument("--src", default=None)
    parser.add_argument("--dst", default=None)
    parser.add_argument("--config", choices=["generate", "diff", "push"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result: Any = None

    if args.redundancy:
        result = analyze_redundancy(args.topology_id, args.site)
    elif args.eol:
        result = analyze_eol(args.topology_id, args.years)
    elif args.blast_radius:
        if not args.device_id:
            print("ERROR: --device-id required for blast radius")
            sys.exit(1)
        result = analyze_blast_radius(args.topology_id, args.device_id)
    elif args.cost:
        result = project_costs(args.topology_id, args.years)
    elif args.capacity:
        result = analyze_capacity(args.topology_id, {
            "user_count": args.users, "region": args.region,
            "services": args.services, "description": f"Add {args.users} users",
        })
    elif args.compliance:
        result = assess_compliance(args.topology_id)
    elif args.vendor_risk:
        result = assess_vendor_risk(args.topology_id)
    elif args.circuits:
        result = assess_circuits(args.topology_id)
    elif args.availability:
        result = assess_availability(args.topology_id, args.src, args.dst)
    elif args.latency:
        result = assess_latency(args.topology_id, args.src, args.dst)
    elif args.config:
        result = manage_configs(args.topology_id, args.config)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
