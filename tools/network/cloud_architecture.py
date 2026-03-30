# CUI // SP-CTI — ICDEV Network Canvas Cloud Architecture Engine
# Classification: CUI — Controlled Unclassified Information
"""Cloud network architecture engine for NDC.

Provides deterministic analysis of hybrid/multi-cloud network topologies:
- Resiliency tier scoring (99.99%, 99.9%, dev/test, none)
- CSP equivalence lookups for multi-cloud design
- Hybrid connectivity validation against Well-Architected patterns
- Multi-cloud data transfer cost estimation
- BFD/failover time modeling
- Anti-pattern detection

Sources:
- AWS Well-Architected Hybrid Networking Lens (2025)
- AWS Direct Connect Resiliency Toolkit
- AWS re:Invent ARC322 (Networking Lens on Resiliency Architectures)
- Azure, GCP, OCI, IBM Cloud equivalence research

Pure functions — no Flask dependency, no LLM, fully air-gapped.
"""

from __future__ import annotations

from tools.network.constants import (
    CSP_EQUIVALENCE,
    RESILIENCY_TIERS,
    HYBRID_CONNECTIVITY_PATTERNS,
    CLOUD_EGRESS_PRICING,
    INTERCONNECT_PORT_PRICING,
    CLOUD_NETWORKING_ANTIPATTERNS,
)


# ── CSP detection helpers ────────────────────────────────────────────────────

_CSP_PREFIXES = {
    "aws-": "aws",
    "az-": "azure",
    "gcp-": "gcp",
    "oci-": "oci",
    "ibm-": "ibm",
}

_DX_TYPES = {
    "aws-dx", "aws-dx-gw", "az-er", "az-er-global",
    "gcp-ic", "oci-fc", "ibm-dl",
}

_VPN_TYPES = {
    "aws-vpn", "az-vpn-gw", "gcp-vpn", "ibm-vpn",
}

_TRANSIT_HUB_TYPES = {
    "aws-tgw", "aws-cloudwan", "az-vwan", "gcp-ncc", "oci-drg", "ibm-tg",
}

_FIREWALL_TYPES = {
    "firewall", "aws-nfw", "az-fw", "gcp-armor", "oci-waf", "aws-waf",
}

_LB_TYPES = {
    "aws-alb", "aws-nlb", "aws-gwlb", "aws-ga",
    "az-appgw", "az-front", "az-crosslb",
    "gcp-lb", "gcp-gfe",
    "oci-lb", "ibm-lb",
}


def _node_csp(node_type: str) -> str | None:
    """Return CSP key for a node type, or None for non-cloud types."""
    for prefix, csp in _CSP_PREFIXES.items():
        if node_type.startswith(prefix):
            return csp
    return None


def _build_adj(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    return adj


# ── Resiliency Tier Scoring ──────────────────────────────────────────────────

def score_hybrid_resiliency(nodes: list[dict], edges: list[dict]) -> dict:
    """Score the resiliency tier of a hybrid topology.

    Analyzes dedicated interconnect connections (DX, ExpressRoute, Interconnect,
    FastConnect, Direct Link) and VPN backup presence to assign a resiliency tier.

    Returns:
        Dict with tier, sla, findings, and recommendations.
    """
    node_types = {n["id"]: n.get("type", "") for n in nodes}

    # Find all dedicated interconnect nodes
    dx_nodes = [n for n in nodes if node_types.get(n["id"]) in _DX_TYPES]
    vpn_nodes = [n for n in nodes if node_types.get(n["id"]) in _VPN_TYPES]
    hub_nodes = [n for n in nodes if node_types.get(n["id"]) in _TRANSIT_HUB_TYPES]

    # Group DX nodes by CSP
    dx_by_csp: dict[str, list[dict]] = {}
    for dn in dx_nodes:
        csp = _node_csp(node_types[dn["id"]]) or "unknown"
        dx_by_csp.setdefault(csp, []).append(dn)

    # Count unique locations (inferred from node labels/config)
    def _infer_location(node: dict) -> str:
        config = node.get("config", {})
        label = node.get("label", "")
        return config.get("location", "") or config.get("site", "") or label

    findings = []
    recommendations = []

    total_dx = len(dx_nodes)
    locations = set()
    for dn in dx_nodes:
        loc = _infer_location(dn)
        if loc:
            locations.add(loc.lower().strip())

    num_locations = max(len(locations), 1) if dx_nodes else 0
    has_vpn_backup = len(vpn_nodes) > 0
    has_transit_hub = len(hub_nodes) > 0

    # Determine tier
    tier = "single"
    if total_dx >= 4 and num_locations >= 2:
        tier = "maximum"
    elif total_dx >= 2 and num_locations >= 2:
        tier = "high"
    elif total_dx >= 2 and num_locations >= 1:
        tier = "development"
    elif total_dx == 1:
        tier = "single"
    elif total_dx == 0 and vpn_nodes:
        tier = "single"  # VPN-only is not a DX resiliency tier

    tier_info = RESILIENCY_TIERS[tier]

    # Build findings
    if total_dx == 0:
        findings.append("No dedicated interconnect (DX/ER/IC/FC) found in topology")
        recommendations.append("Add dedicated circuit for production workloads — VPN alone has lower reliability and throughput")
    elif total_dx == 1:
        findings.append("Single dedicated interconnect — single point of failure, no SLA")
        recommendations.append("Add a second connection at a diverse location for at least 99.9% SLA")

    if total_dx >= 1 and not has_vpn_backup:
        findings.append("No VPN backup for dedicated interconnect — no failover path if DX fails")
        recommendations.append("Configure Site-to-Site VPN as automatic backup for DX/ER (anti-pattern AP-008)")

    if total_dx >= 2 and num_locations < 2:
        findings.append(f"All {total_dx} DX connections appear to be at a single location — location failure risk")
        recommendations.append("Distribute connections across 2+ geographically diverse colocation facilities")

    if total_dx >= 2 and not has_transit_hub:
        findings.append("Multiple DX connections without a transit hub (TGW/vWAN/NCC/DRG)")
        recommendations.append("Use a transit hub for centralized routing and simplified multi-VPC connectivity")

    # BFD check (from node configs)
    bfd_enabled = False
    for dn in dx_nodes:
        config = dn.get("config", {})
        if config.get("bfd") or config.get("bfd_enabled"):
            bfd_enabled = True
    if dx_nodes and not bfd_enabled:
        findings.append("BFD not detected on DX connections — failover will take ~90 seconds (BGP default)")
        recommendations.append("Enable BFD (Bidirectional Forwarding Detection) on all DX VIFs for sub-second (<1s) failover")

    # Failover time estimate
    failover_sec = 90.0 if not bfd_enabled else 0.9
    if not dx_nodes and vpn_nodes:
        failover_sec = 30.0  # VPN-only, depends on BGP convergence

    return {
        "tier": tier,
        "tier_label": tier_info["label"],
        "sla": tier_info["sla"],
        "dedicated_connections": total_dx,
        "unique_locations": num_locations,
        "has_vpn_backup": has_vpn_backup,
        "has_transit_hub": has_transit_hub,
        "bfd_enabled": bfd_enabled,
        "estimated_failover_sec": failover_sec,
        "findings": findings,
        "recommendations": recommendations,
        "csps_involved": sorted(dx_by_csp.keys()),
    }


# ── CSP Equivalence Lookups ──────────────────────────────────────────────────

def get_csp_equivalent(service_key: str, target_csp: str) -> dict:
    """Look up the equivalent service on a target CSP.

    Args:
        service_key: Key from CSP_EQUIVALENCE (e.g., 'dedicated_interconnect').
        target_csp: Target CSP ('aws', 'azure', 'gcp', 'oci', 'ibm').

    Returns:
        Dict with service name, type, parity, and notes.
    """
    mapping = CSP_EQUIVALENCE.get(service_key, {})
    if not mapping:
        return {"error": f"Unknown service key: {service_key}"}
    csp_info = mapping.get(target_csp, {})
    return {
        "service_key": service_key,
        "category": mapping.get("category", ""),
        "description": mapping.get("description", ""),
        "target_csp": target_csp,
        **csp_info,
    }


def get_all_equivalents(service_key: str) -> dict:
    """Get equivalents across all CSPs for a given service."""
    mapping = CSP_EQUIVALENCE.get(service_key, {})
    if not mapping:
        return {"error": f"Unknown service key: {service_key}"}
    result = {
        "service_key": service_key,
        "category": mapping.get("category", ""),
        "description": mapping.get("description", ""),
        "csps": {},
    }
    for csp in ("aws", "azure", "gcp", "oci", "ibm"):
        result["csps"][csp] = mapping.get(csp, {"parity": "none"})
    return result


def get_equivalence_matrix() -> list[dict]:
    """Return the full CSP equivalence matrix for all services."""
    matrix = []
    for key, mapping in CSP_EQUIVALENCE.items():
        row = {
            "service_key": key,
            "category": mapping.get("category", ""),
            "description": mapping.get("description", ""),
        }
        for csp in ("aws", "azure", "gcp", "oci", "ibm"):
            csp_info = mapping.get(csp, {})
            row[f"{csp}_service"] = csp_info.get("service", "—")
            row[f"{csp}_parity"] = csp_info.get("parity", "baseline" if csp == "aws" else "none")
        matrix.append(row)
    return matrix


# ── Hybrid Connectivity Validation ───────────────────────────────────────────

def detect_connectivity_pattern(nodes: list[dict], edges: list[dict]) -> dict:
    """Detect which hybrid connectivity patterns are present in a topology.

    Returns dict with detected patterns and missing recommended patterns.
    """
    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    detected = []
    missing = []

    has_dx = bool(type_set & _DX_TYPES)
    has_vpn = bool(type_set & _VPN_TYPES)
    has_hub = bool(type_set & _TRANSIT_HUB_TYPES)
    has_fw = bool(type_set & _FIREWALL_TYPES)
    has_sdwan = "sdwan-overlay" in type_set or "sdwan-edge" in type_set

    # Check for DX + VPN backup pattern
    if has_dx and has_vpn:
        detected.append({
            "pattern": "dx_primary_vpn_backup",
            **HYBRID_CONNECTIVITY_PATTERNS["dx_primary_vpn_backup"],
        })
    elif has_dx and not has_vpn:
        missing.append({
            "pattern": "dx_primary_vpn_backup",
            "reason": "Dedicated circuit found without VPN backup — add VPN for failover",
            "severity": "high",
        })

    # Check for dual DX
    dx_nodes = [nid for nid, ntype in node_types.items() if ntype in _DX_TYPES]
    if len(dx_nodes) >= 2:
        detected.append({
            "pattern": "dual_dx_diverse_locations",
            **HYBRID_CONNECTIVITY_PATTERNS["dual_dx_diverse_locations"],
        })

    # Check for transit hub
    vpc_count = sum(1 for t in type_set if t in {"aws-vpc", "az-vnet", "gcp-vpc", "oci-vcn", "ibm-vpc"})
    if has_hub and vpc_count >= 2:
        detected.append({
            "pattern": "transit_hub_multi_vpc",
            **HYBRID_CONNECTIVITY_PATTERNS["transit_hub_multi_vpc"],
        })
    elif vpc_count >= 2 and not has_hub:
        missing.append({
            "pattern": "transit_hub_multi_vpc",
            "reason": f"{vpc_count} VPCs detected without a transit hub — use TGW/vWAN/NCC/DRG for centralized routing",
            "severity": "medium",
        })

    # SD-WAN
    if has_sdwan:
        detected.append({
            "pattern": "sdwan_overlay",
            **HYBRID_CONNECTIVITY_PATTERNS["sdwan_overlay"],
        })

    return {
        "detected_patterns": detected,
        "missing_patterns": missing,
        "summary": {
            "has_dedicated_circuit": has_dx,
            "has_vpn": has_vpn,
            "has_transit_hub": has_hub,
            "has_firewall": has_fw,
            "has_sdwan": has_sdwan,
            "vpc_count": vpc_count,
        },
    }


# ── Anti-Pattern Detection ───────────────────────────────────────────────────

def detect_antipatterns(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Detect cloud networking anti-patterns in a topology.

    Based on AWS re:Invent ARC322 and Well-Architected guidance.
    Returns list of detected anti-patterns with recommendations.
    """
    node_types = {n["id"]: n.get("type", "") for n in nodes}
    adj = _build_adj(edges)
    detected = []

    # AP-004: BGP-only failover without BFD
    dx_nodes = [n for n in nodes if node_types.get(n["id"]) in _DX_TYPES]
    for dn in dx_nodes:
        config = dn.get("config", {})
        if not config.get("bfd") and not config.get("bfd_enabled"):
            ap = next(a for a in CLOUD_NETWORKING_ANTIPATTERNS if a["id"] == "AP-004")
            detected.append({
                **ap,
                "affected_node": dn.get("label", dn["id"]),
                "affected_node_id": dn["id"],
            })
            break  # One finding per anti-pattern

    # AP-006: LAG used for HA
    for n in nodes:
        config = n.get("config", {})
        label = (n.get("label") or "").lower()
        if config.get("lag") or "lag" in label:
            neighbors = adj.get(n["id"], set())
            dx_neighbors = [nb for nb in neighbors if node_types.get(nb) in _DX_TYPES]
            if dx_neighbors:
                ap = next(a for a in CLOUD_NETWORKING_ANTIPATTERNS if a["id"] == "AP-006")
                detected.append({
                    **ap,
                    "affected_node": n.get("label", n["id"]),
                    "affected_node_id": n["id"],
                })
                break

    # AP-008: Single DX without VPN backup
    vpn_nodes = [n for n in nodes if node_types.get(n["id"]) in _VPN_TYPES]
    if len(dx_nodes) == 1 and not vpn_nodes:
        ap = next(a for a in CLOUD_NETWORKING_ANTIPATTERNS if a["id"] == "AP-008")
        detected.append({
            **ap,
            "affected_node": dx_nodes[0].get("label", dx_nodes[0]["id"]),
            "affected_node_id": dx_nodes[0]["id"],
        })

    return detected


# ── Multi-Cloud Cost Estimation ──────────────────────────────────────────────

def estimate_data_transfer_cost(
    source_csp: str,
    destination: str,
    monthly_gb: float,
    transfer_type: str = "internet",
) -> dict:
    """Estimate monthly data transfer cost for a cloud egress path.

    Args:
        source_csp: Source CSP key ('aws', 'azure', 'gcp', 'oci', 'ibm').
        destination: 'internet', 'cross_region', 'cross_az', or 'dedicated' (DX/ER/IC/FC).
        monthly_gb: Monthly data transfer in GB.
        transfer_type: Same as destination (kept for backward compat).

    Returns:
        Dict with monthly cost, per-GB rate, and notes.
    """
    pricing = CLOUD_EGRESS_PRICING.get(source_csp, {})
    if not pricing:
        return {"error": f"Unknown CSP: {source_csp}"}

    dest = destination or transfer_type
    rate_key_map = {
        "internet": "internet_per_gb",
        "cross_region": "cross_region_per_gb",
        "cross_az": "cross_az_per_gb",
        "dedicated": {
            "aws": "dx_per_gb",
            "azure": "er_per_gb",
            "gcp": "interconnect_per_gb",
            "oci": "fastconnect_per_gb",
            "ibm": "direct_link_per_gb",
        },
    }

    if dest == "dedicated":
        key = rate_key_map["dedicated"].get(source_csp, "dx_per_gb")
    else:
        key = rate_key_map.get(dest, "internet_per_gb")

    # GCP has premium/standard for internet
    if source_csp == "gcp" and dest == "internet":
        key = "internet_per_gb_premium"

    rate = pricing.get(key, 0.0)
    monthly_cost = round(rate * monthly_gb, 2)

    return {
        "source_csp": source_csp,
        "destination": dest,
        "monthly_gb": monthly_gb,
        "rate_per_gb": rate,
        "monthly_cost_usd": monthly_cost,
        "note": pricing.get("note", ""),
    }


def compare_egress_costs(monthly_gb: float, destination: str = "internet") -> list[dict]:
    """Compare egress costs across all CSPs for the same transfer volume.

    Returns sorted list (cheapest first) with cost per CSP.
    """
    results = []
    for csp in ("aws", "azure", "gcp", "oci", "ibm"):
        est = estimate_data_transfer_cost(csp, destination, monthly_gb)
        if "error" not in est:
            results.append(est)
    results.sort(key=lambda x: x["monthly_cost_usd"])
    return results


def estimate_interconnect_monthly_cost(csp: str, speed_gbps: int, monthly_egress_gb: float = 0) -> dict:
    """Estimate monthly cost for a dedicated interconnect (port + data transfer).

    Args:
        csp: CSP key.
        speed_gbps: Port speed in Gbps (10 or 100).
        monthly_egress_gb: Monthly egress data in GB.
    """
    port_pricing = INTERCONNECT_PORT_PRICING.get(csp, {})
    speed_key = f"{speed_gbps}g"
    port_cost = port_pricing.get(speed_key, 0)

    egress = estimate_data_transfer_cost(csp, "dedicated", monthly_egress_gb)
    egress_cost = egress.get("monthly_cost_usd", 0)

    return {
        "csp": csp,
        "speed_gbps": speed_gbps,
        "port_monthly_usd": port_cost,
        "egress_monthly_usd": egress_cost,
        "total_monthly_usd": round(port_cost + egress_cost, 2),
        "note": port_pricing.get("note", ""),
    }


# ── Topology Cloud Analysis ─────────────────────────────────────────────────

def analyze_cloud_topology(nodes: list[dict], edges: list[dict]) -> dict:
    """Run comprehensive cloud architecture analysis on a topology.

    Combines resiliency scoring, pattern detection, anti-pattern detection,
    and cost estimation into a single report.

    Returns:
        Dict with resiliency, patterns, antipatterns, csp_breakdown, and cost_model.
    """
    node_types = {n["id"]: n.get("type", "") for n in nodes}

    # CSP breakdown
    csp_counts: dict[str, int] = {}
    for ntype in node_types.values():
        csp = _node_csp(ntype)
        if csp:
            csp_counts[csp] = csp_counts.get(csp, 0) + 1

    is_multi_cloud = len(csp_counts) > 1

    # Run sub-analyses
    resiliency = score_hybrid_resiliency(nodes, edges)
    patterns = detect_connectivity_pattern(nodes, edges)
    antipatterns = detect_antipatterns(nodes, edges)

    # Overall health score (0-100)
    score = 100
    # Deduct for resiliency gaps
    if resiliency["tier"] == "single":
        score -= 30
    elif resiliency["tier"] == "development":
        score -= 15
    elif resiliency["tier"] == "high":
        score -= 5
    # Deduct for missing patterns
    score -= len(patterns["missing_patterns"]) * 10
    # Deduct for anti-patterns
    score -= len(antipatterns) * 8
    # Deduct for no BFD
    if not resiliency["bfd_enabled"] and resiliency["dedicated_connections"] > 0:
        score -= 10
    score = max(0, min(100, score))

    # Health rating
    if score >= 90:
        health = "EXCELLENT"
    elif score >= 75:
        health = "GOOD"
    elif score >= 50:
        health = "FAIR"
    else:
        health = "POOR"

    return {
        "cloud_health_score": score,
        "cloud_health_rating": health,
        "is_multi_cloud": is_multi_cloud,
        "csp_breakdown": csp_counts,
        "resiliency": resiliency,
        "connectivity_patterns": patterns,
        "antipatterns": antipatterns,
        "total_cloud_nodes": sum(csp_counts.values()),
        "total_csps": len(csp_counts),
    }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """CLI entry point for testing cloud architecture functions."""
    import json
    import sys

    if "--equivalence-matrix" in sys.argv:
        print(json.dumps(get_equivalence_matrix(), indent=2))
    elif "--resiliency-tiers" in sys.argv:
        print(json.dumps(RESILIENCY_TIERS, indent=2))
    elif "--antipatterns" in sys.argv:
        print(json.dumps(CLOUD_NETWORKING_ANTIPATTERNS, indent=2))
    elif "--patterns" in sys.argv:
        print(json.dumps(HYBRID_CONNECTIVITY_PATTERNS, indent=2, default=str))
    elif "--egress-compare" in sys.argv:
        gb = 1000.0
        for i, arg in enumerate(sys.argv):
            if arg == "--gb" and i + 1 < len(sys.argv):
                gb = float(sys.argv[i + 1])
        print(json.dumps(compare_egress_costs(gb), indent=2))
    elif "--json" in sys.argv:
        print(json.dumps({
            "status": "ok",
            "module": "cloud_architecture",
            "capabilities": [
                "resiliency_scoring",
                "csp_equivalence",
                "connectivity_patterns",
                "antipattern_detection",
                "cost_estimation",
            ],
            "csps": ["aws", "azure", "gcp", "oci", "ibm"],
            "equivalence_services": len(CSP_EQUIVALENCE),
            "resiliency_tiers": len(RESILIENCY_TIERS),
            "connectivity_patterns": len(HYBRID_CONNECTIVITY_PATTERNS),
            "antipatterns": len(CLOUD_NETWORKING_ANTIPATTERNS),
        }, indent=2))
    else:
        print("ICDEV Network Canvas — Cloud Architecture Engine")
        print(f"  CSP equivalence mappings: {len(CSP_EQUIVALENCE)}")
        print(f"  Resiliency tiers: {len(RESILIENCY_TIERS)}")
        print(f"  Connectivity patterns: {len(HYBRID_CONNECTIVITY_PATTERNS)}")
        print(f"  Anti-patterns: {len(CLOUD_NETWORKING_ANTIPATTERNS)}")
        print(f"  Egress pricing models: {len(CLOUD_EGRESS_PRICING)} CSPs")
        print("\nFlags: --json, --equivalence-matrix, --resiliency-tiers,")
        print("       --antipatterns, --patterns, --egress-compare [--gb N]")


if __name__ == "__main__":
    main()
