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
    "aws-dx",
    "aws-dx-gw",
    "az-er",
    "az-er-global",
    "gcp-ic",
    "gcp-ic-partner",
    "oci-fc",
    "oci-fc-vc",
    "ibm-dl",
    "ibm-dl-ded",
    "ibm-dl-con",
    "megaport-mcr",
    "equinix-fabric",
}

_VPN_TYPES = {
    "aws-vpn",
    "aws-vpn-ha",
    "aws-vgw",
    "az-vpn-gw",
    "az-lng",
    "gcp-vpn",
    "gcp-ha-vpn",
    "gcp-classic-vpn",
    "oci-ipsec",
    "ibm-vpn",
}

_TRANSIT_HUB_TYPES = {
    "aws-tgw",
    "aws-tgw-rt",
    "aws-cloudwan",
    "az-vwan",
    "az-route-server",
    "gcp-ncc",
    "gcp-ncc-spoke",
    "oci-drg",
    "ibm-tg",
}

_SCCA_BCAP_TYPES = {
    "dod-bcap",
    "dod-niprnet-onramp",
}

_FIREWALL_TYPES = {
    "firewall",
    "aws-nfw",
    "az-fw",
    "gcp-armor",
    "oci-waf",
    "aws-waf",
}

_LB_TYPES = {
    "aws-alb",
    "aws-nlb",
    "aws-gwlb",
    "aws-ga",
    "az-appgw",
    "az-front",
    "az-crosslb",
    "gcp-lb",
    "gcp-gfe",
    "oci-lb",
    "ibm-lb",
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
        recommendations.append(
            "Add dedicated circuit for production workloads — VPN alone has lower reliability and throughput"
        )
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
        recommendations.append(
            "Enable BFD (Bidirectional Forwarding Detection) on all DX VIFs for sub-second (<1s) failover"
        )

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
        detected.append(
            {
                "pattern": "dx_primary_vpn_backup",
                **HYBRID_CONNECTIVITY_PATTERNS["dx_primary_vpn_backup"],
            }
        )
    elif has_dx and not has_vpn:
        missing.append(
            {
                "pattern": "dx_primary_vpn_backup",
                "reason": "Dedicated circuit found without VPN backup — add VPN for failover",
                "severity": "high",
            }
        )

    # Check for dual DX
    dx_nodes = [nid for nid, ntype in node_types.items() if ntype in _DX_TYPES]
    if len(dx_nodes) >= 2:
        detected.append(
            {
                "pattern": "dual_dx_diverse_locations",
                **HYBRID_CONNECTIVITY_PATTERNS["dual_dx_diverse_locations"],
            }
        )

    # Check for transit hub
    vpc_count = sum(1 for t in type_set if t in {"aws-vpc", "az-vnet", "gcp-vpc", "oci-vcn", "ibm-vpc"})
    if has_hub and vpc_count >= 2:
        detected.append(
            {
                "pattern": "transit_hub_multi_vpc",
                **HYBRID_CONNECTIVITY_PATTERNS["transit_hub_multi_vpc"],
            }
        )
    elif vpc_count >= 2 and not has_hub:
        missing.append(
            {
                "pattern": "transit_hub_multi_vpc",
                "reason": f"{vpc_count} VPCs detected without a transit hub — use TGW/vWAN/NCC/DRG for centralized routing",
                "severity": "medium",
            }
        )

    # SD-WAN
    if has_sdwan:
        detected.append(
            {
                "pattern": "sdwan_overlay",
                **HYBRID_CONNECTIVITY_PATTERNS["sdwan_overlay"],
            }
        )

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
            detected.append(
                {
                    **ap,
                    "affected_node": dn.get("label", dn["id"]),
                    "affected_node_id": dn["id"],
                }
            )
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
                detected.append(
                    {
                        **ap,
                        "affected_node": n.get("label", n["id"]),
                        "affected_node_id": n["id"],
                    }
                )
                break

    # AP-008: Single DX without VPN backup
    vpn_nodes = [n for n in nodes if node_types.get(n["id"]) in _VPN_TYPES]
    if len(dx_nodes) == 1 and not vpn_nodes:
        ap = next(a for a in CLOUD_NETWORKING_ANTIPATTERNS if a["id"] == "AP-008")
        detected.append(
            {
                **ap,
                "affected_node": dx_nodes[0].get("label", dx_nodes[0]["id"]),
                "affected_node_id": dx_nodes[0]["id"],
            }
        )

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


# ── SCCA Compliance Analysis ───────────────────────────────────────────────────


def analyze_scca_compliance(nodes: list[dict], edges: list[dict]) -> dict:
    """Analyze a topology for SCCA (Secure Cloud Computing Architecture) compliance.

    Detects which SCCA components (BCAP, VDSS, VDMS, TCCM) are present,
    scores overall SCCA readiness, and returns findings with recommendations.

    Returns:
        Dict with component_status, score, findings, and recommendations.
    """
    from tools.network.constants import (
        SCCA_FIREWALL_TYPES,
        SCCA_IDENTITY_TYPES,
        SCCA_LOGGING_TYPES,
        SCCA_KMS_TYPES,
        SCCA_SCANNING_TYPES,
        SCCA_DDOS_TYPES,
    )

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    has_dx = bool(type_set & _DX_TYPES)
    has_firewall = bool(type_set & SCCA_FIREWALL_TYPES)
    has_waf = bool(type_set & {"aws-waf", "az-appgw", "az-front", "oci-waf", "gcp-armor", "ibm-cis"})
    has_ddos = bool(type_set & SCCA_DDOS_TYPES)
    has_hub = bool(type_set & _TRANSIT_HUB_TYPES)
    has_flow_logs = bool(type_set & {"aws-flowlogs", "az-flowlogs", "gcp-flowlogs", "oci-flowlogs", "ibm-flowlogs"})
    has_identity = bool(type_set & SCCA_IDENTITY_TYPES)
    has_logging = bool(type_set & SCCA_LOGGING_TYPES)
    has_kms = bool(type_set & SCCA_KMS_TYPES)
    has_scanning = bool(type_set & SCCA_SCANNING_TYPES)

    # Component scoring
    components = {
        "bcap": {
            "present": has_dx,
            "score": 100 if has_dx else 0,
            "findings": [] if has_dx else ["No dedicated interconnect (DX/ER/IC/FC/DL) to DISN/BCAP"],
            "recommendations": [] if has_dx else ["Add dedicated circuit for DISN connectivity (FRD §2.1.1)"],
        },
        "vdss": {
            "present": has_firewall,
            "score": 0,
            "findings": [],
            "recommendations": [],
        },
        "vdms": {
            "present": has_logging or has_kms or has_scanning,
            "score": 0,
            "findings": [],
            "recommendations": [],
        },
        "tccm": {
            "present": has_identity,
            "score": 0,
            "findings": [],
            "recommendations": [],
        },
    }

    # VDSS scoring
    vdss_score = 0
    vdss_max = 6
    if has_firewall:
        vdss_score += 1
    else:
        components["vdss"]["findings"].append("No network firewall for IDS/IPS inspection")
        components["vdss"]["recommendations"].append(
            "Add Network Firewall/Azure FW for stateful inspection (FRD §2.1.2.7)"
        )
    if has_waf:
        vdss_score += 1
    else:
        components["vdss"]["findings"].append("No WAF for application-layer filtering")
        components["vdss"]["recommendations"].append("Add WAF for HTTP inspection (FRD §2.1.2.4)")
    if has_ddos:
        vdss_score += 1
    else:
        components["vdss"]["findings"].append("No DDoS protection at perimeter")
        components["vdss"]["recommendations"].append("Add Shield/DDoS Protection/Cloud Armor (FRD §2.1.2.1)")
    if has_hub:
        vdss_score += 1
    else:
        components["vdss"]["findings"].append("No transit hub for centralized traffic routing")
        components["vdss"]["recommendations"].append("Add TGW/vWAN/DRG for east-west inspection (FRD §2.1.2.8)")
    if has_flow_logs:
        vdss_score += 1
    else:
        components["vdss"]["findings"].append("No VPC/VNet flow logs for traffic monitoring")
        components["vdss"]["recommendations"].append("Enable Flow Logs on all VPCs/VNets (FRD §2.1.2.16)")
    # Check east-west inspection: firewall connected to transit hub
    if has_firewall and has_hub:
        adj = _build_adj(edges)
        fw_ids = [nid for nid, ntype in node_types.items() if ntype in SCCA_FIREWALL_TYPES]
        hub_ids = [nid for nid, ntype in node_types.items() if ntype in _TRANSIT_HUB_TYPES]
        ew_ok = any(h in adj.get(f, set()) for f in fw_ids for h in hub_ids)
        if ew_ok:
            vdss_score += 1
        else:
            components["vdss"]["findings"].append(
                "Firewall not connected to transit hub — east-west traffic may bypass inspection"
            )
            components["vdss"]["recommendations"].append(
                "Connect firewall to TGW/vWAN/DRG for all inter-VPC traffic inspection"
            )
    components["vdss"]["score"] = round(vdss_score / vdss_max * 100)

    # VDMS scoring
    vdms_score = 0
    vdms_max = 4
    if has_logging:
        vdms_score += 1
    else:
        components["vdms"]["findings"].append("No centralized logging/SIEM service")
        components["vdms"]["recommendations"].append("Add SecurityHub/Sentinel/SCC for SIEM (FRD §2.1.3.7)")
    if has_kms:
        vdms_score += 1
    else:
        components["vdms"]["findings"].append("No FIPS 140-2 key management service")
        components["vdms"]["recommendations"].append(
            "Add KMS/Key Vault/OCI Vault for encryption key management (FRD §2.1.2.13)"
        )
    if has_scanning:
        vdms_score += 1
    else:
        components["vdms"]["findings"].append("No vulnerability scanning (ACAS equivalent)")
        components["vdms"]["recommendations"].append(
            "Add Inspector/Defender/Cloud Guard for continuous monitoring (FRD §2.1.3.1)"
        )
    if has_identity:
        vdms_score += 1
    else:
        components["vdms"]["findings"].append("No identity/directory service for CAC/MFA")
        components["vdms"]["recommendations"].append(
            "Add Managed AD/Entra ID/Identity Domains for CAC auth (FRD §2.1.3.3)"
        )
    components["vdms"]["score"] = round(vdms_score / vdms_max * 100)

    # TCCM scoring
    tccm_score = 0
    tccm_max = 2
    if has_identity:
        tccm_score += 1
    else:
        components["tccm"]["findings"].append("No centralized IAM/SSO service")
        components["tccm"]["recommendations"].append("Add IAM Identity Center/Entra ID for RBAC (FRD §2.1.4.6)")
    has_audit = bool(type_set & {"aws-ct", "oci-audit", "az-monitor", "gcp-scc", "ibm-scc"})
    if has_audit:
        tccm_score += 1
    else:
        components["tccm"]["findings"].append("No API audit trail service")
        components["tccm"]["recommendations"].append("Add CloudTrail/OCI Audit for API activity logging (FRD §2.1.4.2)")
    components["tccm"]["score"] = round(tccm_score / tccm_max * 100)

    # Overall score
    weights = {"bcap": 0.15, "vdss": 0.40, "vdms": 0.30, "tccm": 0.15}
    overall = sum(components[c]["score"] * weights[c] for c in weights)
    overall = round(max(0, min(100, overall)))

    if overall >= 90:
        rating = "SCCA COMPLIANT"
    elif overall >= 70:
        rating = "PARTIALLY COMPLIANT"
    elif overall >= 40:
        rating = "SIGNIFICANT GAPS"
    else:
        rating = "NON-COMPLIANT"

    # Aggregate
    all_findings = []
    all_recommendations = []
    for c in ("bcap", "vdss", "vdms", "tccm"):
        for f in components[c]["findings"]:
            all_findings.append(f"[{c.upper()}] {f}")
        for r in components[c]["recommendations"]:
            all_recommendations.append(f"[{c.upper()}] {r}")

    return {
        "scca_score": overall,
        "scca_rating": rating,
        "components": components,
        "findings": all_findings,
        "recommendations": all_recommendations,
        "components_present": sum(1 for c in components.values() if c["present"]),
        "components_total": 4,
    }


def get_scca_mapping(component: str, target_csp: str) -> dict:
    """Look up the CSP-native services for a SCCA component.

    Args:
        component: SCCA component key ('bcap', 'vdss', 'vdms', 'tccm').
        target_csp: Target CSP ('aws', 'azure', 'gcp', 'oci', 'ibm').

    Returns:
        Dict with component info, services, node_types, and note.
    """
    from tools.network.constants import SCCA_CSP_MAPPING, SCCA_COMPONENTS

    comp_info = SCCA_COMPONENTS.get(component)
    if not comp_info:
        return {"error": f"Unknown SCCA component: {component}"}
    mapping = SCCA_CSP_MAPPING.get(component, {})
    csp_info = mapping.get(target_csp, {})
    return {
        "component": component,
        "name": comp_info["name"],
        "acronym": comp_info["acronym"],
        "disa_ref": comp_info["disa_ref"],
        "target_csp": target_csp,
        "services": csp_info.get("services", []),
        "node_types": csp_info.get("node_types", []),
        "note": csp_info.get("note", ""),
    }


def get_scca_matrix() -> list[dict]:
    """Return the full SCCA-to-CSP service mapping matrix.

    Returns list of dicts, one per SCCA component, with services for all CSPs.
    """
    from tools.network.constants import SCCA_CSP_MAPPING, SCCA_COMPONENTS

    matrix = []
    for comp_key, mapping in SCCA_CSP_MAPPING.items():
        comp_info = SCCA_COMPONENTS.get(comp_key, {})
        row = {
            "component": comp_key,
            "name": comp_info.get("name", ""),
            "acronym": comp_info.get("acronym", ""),
            "description": mapping.get("description", ""),
        }
        for csp in ("aws", "azure", "gcp", "oci", "ibm"):
            csp_info = mapping.get(csp, {})
            row[f"{csp}_services"] = csp_info.get("services", [])
            row[f"{csp}_note"] = csp_info.get("note", "")
        matrix.append(row)
    return matrix


def detect_landing_zone_pattern(nodes: list[dict], edges: list[dict]) -> dict:
    """Detect which Landing Zone pattern a topology follows.

    Analyzes multi-account/VPC structure, transit hub usage, and centralized
    security services to classify the topology as a recognized LZ pattern.

    Returns:
        Dict with detected_pattern, csp, confidence, and details.
    """
    from tools.network.constants import LANDING_ZONE_PATTERNS

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())
    labels = {n["id"]: (n.get("label") or "").lower() for n in nodes}
    all_labels = " ".join(labels.values())

    # Count VPCs/VNets/VCNs per CSP
    vpc_counts = {
        "aws": sum(1 for t in node_types.values() if t == "aws-vpc"),
        "azure": sum(1 for t in node_types.values() if t == "az-vnet"),
        "gcp": sum(1 for t in node_types.values() if t == "gcp-vpc"),
        "oci": sum(1 for t in node_types.values() if t in ("oci-vcn",)),
        "ibm": sum(1 for t in node_types.values() if t == "ibm-vpc"),
    }

    # Determine primary CSP
    primary_csp = max(vpc_counts, key=vpc_counts.get) if any(vpc_counts.values()) else None
    if not primary_csp or vpc_counts[primary_csp] < 2:
        return {
            "detected_pattern": None,
            "csp": primary_csp,
            "confidence": 0,
            "details": "Fewer than 2 VPCs/VNets — not a landing zone topology",
        }

    # Check for transit hub
    has_hub = bool(type_set & _TRANSIT_HUB_TYPES)

    # Check for SCCA indicators
    scca = analyze_scca_compliance(nodes, edges)
    has_scca = scca["components_present"] >= 3

    # Label-based hints
    lz_hints = any(
        kw in all_labels for kw in ("landing zone", "shared services", "log archive", "audit", "network account", "hub")
    )

    # Confidence scoring
    confidence = 0
    if vpc_counts[primary_csp] >= 3:
        confidence += 30
    if has_hub:
        confidence += 25
    if has_scca:
        confidence += 25
    if lz_hints:
        confidence += 20
    confidence = min(100, confidence)

    pattern_info = LANDING_ZONE_PATTERNS.get(primary_csp, {})

    return {
        "detected_pattern": pattern_info.get("name") if confidence >= 50 else None,
        "csp": primary_csp,
        "network_pattern": pattern_info.get("network_pattern", "unknown"),
        "confidence": confidence,
        "vpc_count": vpc_counts[primary_csp],
        "has_transit_hub": has_hub,
        "has_scca_components": has_scca,
        "scca_score": scca["scca_score"],
        "reference": pattern_info.get("reference", ""),
        "details": pattern_info.get("description", ""),
    }


# ── WA Security Pillar Analysis ────────────────────────────────────────────────


def analyze_wa_security(nodes: list[dict], edges: list[dict]) -> dict:
    """Analyze a topology against the AWS Well-Architected Security Pillar.

    Evaluates 11 areas (SEC01-SEC11) based on the presence of security
    services, network segmentation, encryption, and identity controls.

    Returns:
        Dict with area_scores, overall_score, rating, findings, recommendations.
    """
    from tools.network.constants import (
        SCCA_FIREWALL_TYPES,
        SCCA_IDENTITY_TYPES,
        SCCA_LOGGING_TYPES,
        SCCA_KMS_TYPES,
        SCCA_SCANNING_TYPES,
        SCCA_DDOS_TYPES,
        WA_THREAT_DETECTION_TYPES,
        WA_CONFIG_MONITORING_TYPES,
    )

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    # Detect capabilities
    vpc_count = sum(1 for t in type_set if t in {"aws-vpc", "az-vnet", "gcp-vpc", "oci-vcn", "ibm-vpc"})
    has_firewall = bool(type_set & SCCA_FIREWALL_TYPES)
    has_waf = bool(type_set & {"aws-waf", "az-appgw", "az-front", "oci-waf", "gcp-armor", "ibm-cis"})
    has_ddos = bool(type_set & SCCA_DDOS_TYPES)
    has_identity = bool(type_set & SCCA_IDENTITY_TYPES)
    has_logging = bool(type_set & SCCA_LOGGING_TYPES)
    has_kms = bool(type_set & SCCA_KMS_TYPES)
    has_scanning = bool(type_set & SCCA_SCANNING_TYPES)
    has_threat_detection = bool(type_set & WA_THREAT_DETECTION_TYPES)
    has_config_monitoring = bool(type_set & WA_CONFIG_MONITORING_TYPES)
    has_flow_logs = bool(type_set & {"aws-flowlogs", "az-flowlogs", "gcp-flowlogs", "oci-flowlogs", "ibm-flowlogs"})
    has_hub = bool(type_set & _TRANSIT_HUB_TYPES)
    has_dx = bool(type_set & _DX_TYPES)
    has_vpn = bool(type_set & _VPN_TYPES)
    has_subnets = bool(type_set & {"aws-subnet", "az-subnet", "gcp-subnet", "oci-subnet", "ibm-subnet"})

    findings = []
    recommendations = []

    # Score each area (0-100)
    area_scores = {}

    # SEC01: Foundations
    s = 0
    if vpc_count >= 2:
        s += 50
    else:
        findings.append("[SEC01] Single VPC — no workload isolation")
        recommendations.append("[SEC01] Separate workloads into multiple VPCs/accounts (SEC01-BP01)")
    if has_hub:
        s += 50
    else:
        findings.append("[SEC01] No transit hub for centralized governance")
        recommendations.append("[SEC01] Add Transit Gateway/vWAN for centralized network control")
    area_scores["foundations"] = s

    # SEC02/03: Identity & Permissions
    s = 100 if has_identity else 0
    if not has_identity:
        findings.append("[SEC02] No centralized identity service (SSO/AD/Entra)")
        recommendations.append("[SEC02] Add IAM Identity Center or Entra ID for federated access (SEC02-BP04)")
    area_scores["identity"] = s

    # SEC04: Detection
    s = 0
    max_d = 4
    if has_logging:
        s += 1
    else:
        findings.append("[SEC04] No centralized logging service")
        recommendations.append("[SEC04] Add CloudTrail/Sentinel/SCC for centralized logging (SEC04-BP01)")
    if has_threat_detection:
        s += 1
    else:
        findings.append("[SEC04] No threat detection service (GuardDuty/Defender)")
        recommendations.append("[SEC04] Add GuardDuty/Defender/Cloud Guard for threat detection (SEC04-BP03)")
    if has_config_monitoring:
        s += 1
    else:
        findings.append("[SEC04] No compliance monitoring (Config/Policy)")
        recommendations.append("[SEC04] Add AWS Config or equivalent for compliance tracking (SEC04-BP04)")
    if has_flow_logs:
        s += 1
    else:
        findings.append("[SEC04] No VPC flow logs for traffic analysis")
        recommendations.append("[SEC04] Enable flow logs on all VPCs/VNets (SEC04-BP01)")
    area_scores["detection"] = round(s / max_d * 100)

    # SEC05/06: Infrastructure Protection
    s = 0
    max_i = 4
    if has_subnets:
        s += 1
    else:
        findings.append("[SEC05] No subnet segmentation (network layers)")
        recommendations.append("[SEC05] Create public/private subnet layers (SEC05-BP01)")
    if has_firewall or has_waf:
        s += 1
    else:
        findings.append("[SEC05] No network inspection (firewall/WAF)")
        recommendations.append("[SEC05] Add Network Firewall or WAF for L3-L7 inspection (SEC05-BP03)")
    if has_ddos:
        s += 1
    else:
        findings.append("[SEC05] No DDoS protection")
        recommendations.append("[SEC05] Add Shield/DDoS Protection/Cloud Armor (SEC05-BP03)")
    if has_scanning:
        s += 1
    else:
        findings.append("[SEC06] No vulnerability scanning service")
        recommendations.append("[SEC06] Add Inspector/Defender/VSS for vulnerability management (SEC06-BP01)")
    area_scores["infrastructure"] = round(s / max_i * 100)

    # SEC07/08/09: Data Protection
    s = 0
    max_dp = 2
    if has_kms:
        s += 1
    else:
        findings.append("[SEC08] No key management service (KMS)")
        recommendations.append("[SEC08] Add KMS/Key Vault for FIPS 140-2 encryption (SEC08-BP01)")
    encrypted_links = sum(
        1 for e in edges if (e.get("protocol") or "").lower() in ("ipsec", "tls", "macsec", "bgp", "https")
    )
    total_links = len(edges) or 1
    if encrypted_links / total_links >= 0.3 or has_dx or has_vpn:
        s += 1
    else:
        findings.append("[SEC09] Few encrypted links detected in topology")
        recommendations.append("[SEC09] Use TLS/IPsec/MACsec on all network connections (SEC09-BP02)")
    area_scores["data_protection"] = round(s / max_dp * 100)

    # SEC10: Incident Response (logging + threat detection covers pre-deployment)
    s = 0
    if has_logging and has_threat_detection:
        s = 100
    elif has_logging or has_threat_detection:
        s = 50
        findings.append("[SEC10] Partial IR tooling — need both logging AND threat detection")
        recommendations.append("[SEC10] Deploy Detective/Sentinel + GuardDuty/Defender before incidents (SEC10-BP06)")
    else:
        findings.append("[SEC10] No IR tooling pre-deployed")
        recommendations.append("[SEC10] Pre-deploy threat detection and logging services (SEC10-BP06)")
    area_scores["incident_response"] = s

    # Overall weighted score
    weights = {
        "foundations": 0.10,
        "identity": 0.15,
        "detection": 0.25,
        "infrastructure": 0.25,
        "data_protection": 0.15,
        "incident_response": 0.10,
    }
    overall = sum(area_scores.get(a, 0) * w for a, w in weights.items())
    overall = round(max(0, min(100, overall)))

    if overall >= 85:
        rating = "WELL-ARCHITECTED"
    elif overall >= 65:
        rating = "PARTIALLY ALIGNED"
    elif overall >= 40:
        rating = "SIGNIFICANT GAPS"
    else:
        rating = "NOT ALIGNED"

    return {
        "wa_security_score": overall,
        "wa_security_rating": rating,
        "area_scores": area_scores,
        "total_best_practices": 57,
        "findings": findings,
        "recommendations": recommendations,
        "design_principles_applicable": 7,
    }


def get_wa_security_summary() -> dict:
    """Return a summary of all WA Security Pillar best practices with risk distribution."""
    from tools.network.constants import WA_SECURITY_BEST_PRACTICES, WA_SECURITY_AREAS

    risk_counts = {"high": 0, "medium": 0, "low": 0}
    for bp in WA_SECURITY_BEST_PRACTICES:
        risk_counts[bp["risk"]] = risk_counts.get(bp["risk"], 0) + 1

    return {
        "total_areas": len(WA_SECURITY_AREAS),
        "total_best_practices": len(WA_SECURITY_BEST_PRACTICES),
        "risk_distribution": risk_counts,
        "areas": {k: v["name"] for k, v in WA_SECURITY_AREAS.items()},
    }


def analyze_csp_security(nodes: list[dict], edges: list[dict], csp: str = "auto") -> dict:
    """Analyze a topology against the appropriate CSP-specific security framework.

    Automatically detects the primary CSP if csp='auto', then runs:
    - AWS → analyze_wa_security() (SEC01-SEC11)
    - Azure → MCSB analysis (NS/IM/PA/DP/LT/ES families)
    - GCP → GCP Security Foundations analysis
    - OCI → OCI Security / CIS Benchmark analysis
    - IBM → IBM SCC Best Practices analysis

    Returns:
        Dict with framework_name, csp, score, rating, findings, recommendations.
    """
    # Detect CSP
    if csp == "auto":
        node_types = {n["id"]: n.get("type", "") for n in nodes}
        csp_counts = {}
        for ntype in node_types.values():
            for prefix, csp_key in [
                ("aws-", "aws"),
                ("az-", "azure"),
                ("gcp-", "gcp"),
                ("oci-", "oci"),
                ("ibm-", "ibm"),
            ]:
                if ntype.startswith(prefix):
                    csp_counts[csp_key] = csp_counts.get(csp_key, 0) + 1
        csp = max(csp_counts, key=csp_counts.get) if csp_counts else "aws"

    # For AWS, delegate to existing analyzer
    if csp == "aws":
        result = analyze_wa_security(nodes, edges)
        result["framework"] = "AWS Well-Architected Security Pillar"
        result["csp"] = "aws"
        return result

    # For all other CSPs, run the same detection logic but with CSP-specific labels
    from tools.network.constants import (
        SCCA_FIREWALL_TYPES,
        SCCA_IDENTITY_TYPES,
        SCCA_LOGGING_TYPES,
        SCCA_KMS_TYPES,
        SCCA_SCANNING_TYPES,
        SCCA_DDOS_TYPES,
        WA_THREAT_DETECTION_TYPES,
    )

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    frameworks = {
        "azure": "Microsoft Cloud Security Benchmark (MCSB)",
        "gcp": "GCP Security Foundations Framework",
        "oci": "OCI Security Best Practices + CIS Benchmark",
        "ibm": "IBM Cloud Security & Compliance Center",
    }

    has_firewall = bool(type_set & SCCA_FIREWALL_TYPES)
    has_waf = bool(type_set & {"aws-waf", "az-appgw", "az-front", "oci-waf", "gcp-armor", "ibm-cis"})
    has_ddos = bool(type_set & SCCA_DDOS_TYPES)
    has_identity = bool(type_set & SCCA_IDENTITY_TYPES)
    has_logging = bool(type_set & SCCA_LOGGING_TYPES)
    has_kms = bool(type_set & SCCA_KMS_TYPES)
    has_scanning = bool(type_set & SCCA_SCANNING_TYPES)
    has_threat = bool(type_set & WA_THREAT_DETECTION_TYPES)
    has_subnets = bool(type_set & {"aws-subnet", "az-subnet", "gcp-subnet", "oci-subnet", "ibm-subnet"})

    findings = []
    recommendations = []
    score_parts = []
    max_parts = 8

    checks = [
        (has_firewall, "Network firewall/inspection", "Add centralized firewall"),
        (has_identity, "Centralized identity provider", "Add identity service (Entra/Identity Domains/IAM)"),
        (has_logging, "Centralized logging/SIEM", "Add logging service (Sentinel/SCC/Audit)"),
        (has_threat, "Threat detection service", "Add threat detection (Defender/Cloud Guard/SCC)"),
        (has_kms, "Key management (FIPS 140-2)", "Add KMS/Key Vault/Vault for encryption"),
        (has_scanning, "Vulnerability scanning", "Add vulnerability scanning service"),
        (has_subnets, "Network layer segmentation", "Create subnet layers for isolation"),
        (has_ddos or has_waf, "Perimeter protection (WAF/DDoS)", "Add WAF or DDoS protection"),
    ]

    for present, label, rec in checks:
        if present:
            score_parts.append(1)
        else:
            score_parts.append(0)
            findings.append(f"Missing: {label}")
            recommendations.append(rec)

    overall = round(sum(score_parts) / max_parts * 100)

    if overall >= 85:
        rating = "WELL-ARCHITECTED"
    elif overall >= 65:
        rating = "PARTIALLY ALIGNED"
    elif overall >= 40:
        rating = "SIGNIFICANT GAPS"
    else:
        rating = "NOT ALIGNED"

    return {
        "framework": frameworks.get(csp, "Unknown"),
        "csp": csp,
        "csp_security_score": overall,
        "csp_security_rating": rating,
        "checks_passed": sum(score_parts),
        "checks_total": max_parts,
        "findings": findings,
        "recommendations": recommendations,
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
    elif "--scca-matrix" in sys.argv:
        print(json.dumps(get_scca_matrix(), indent=2))
    elif "--scca-components" in sys.argv:
        from tools.network.constants import SCCA_COMPONENTS

        print(json.dumps(SCCA_COMPONENTS, indent=2))
    elif "--landing-zones" in sys.argv:
        from tools.network.constants import LANDING_ZONE_PATTERNS

        print(json.dumps(LANDING_ZONE_PATTERNS, indent=2, default=str))
    elif "--wa-security" in sys.argv:
        print(json.dumps(get_wa_security_summary(), indent=2))
    elif "--csp-security" in sys.argv:
        print(
            json.dumps(
                {
                    "csp_security_frameworks": {
                        "aws": "AWS Well-Architected Security Pillar (SEC01-SEC11)",
                        "azure": "Microsoft Cloud Security Benchmark (MCSB)",
                        "gcp": "GCP Security Foundations Framework",
                        "oci": "OCI Security Best Practices + CIS Benchmark",
                        "ibm": "IBM Cloud Security & Compliance Center",
                    },
                    "auto_detection": "CSP detected from node type prefixes (aws-/az-/gcp-/oci-/ibm-)",
                    "checks_per_csp": 8,
                    "ratings": ["WELL-ARCHITECTED", "PARTIALLY ALIGNED", "SIGNIFICANT GAPS", "NOT ALIGNED"],
                },
                indent=2,
            )
        )
    elif "--egress-compare" in sys.argv:
        gb = 1000.0
        for i, arg in enumerate(sys.argv):
            if arg == "--gb" and i + 1 < len(sys.argv):
                gb = float(sys.argv[i + 1])
        print(json.dumps(compare_egress_costs(gb), indent=2))
    elif "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "module": "cloud_architecture",
                    "capabilities": [
                        "resiliency_scoring",
                        "csp_equivalence",
                        "connectivity_patterns",
                        "antipattern_detection",
                        "cost_estimation",
                        "scca_compliance",
                        "scca_mapping",
                        "landing_zone_detection",
                        "wa_security_analysis",
                        "csp_security_analysis",
                    ],
                    "csps": ["aws", "azure", "gcp", "oci", "ibm"],
                    "equivalence_services": len(CSP_EQUIVALENCE),
                    "resiliency_tiers": len(RESILIENCY_TIERS),
                    "connectivity_patterns": len(HYBRID_CONNECTIVITY_PATTERNS),
                    "antipatterns": len(CLOUD_NETWORKING_ANTIPATTERNS),
                    "scca_components": 4,
                    "landing_zone_patterns": 5,
                    "wa_security_best_practices": 57,
                },
                indent=2,
            )
        )
    else:
        print("ICDEV Network Canvas — Cloud Architecture Engine")
        print(f"  CSP equivalence mappings: {len(CSP_EQUIVALENCE)}")
        print(f"  Resiliency tiers: {len(RESILIENCY_TIERS)}")
        print(f"  Connectivity patterns: {len(HYBRID_CONNECTIVITY_PATTERNS)}")
        print(f"  Anti-patterns: {len(CLOUD_NETWORKING_ANTIPATTERNS)}")
        print(f"  Egress pricing models: {len(CLOUD_EGRESS_PRICING)} CSPs")
        print("  SCCA components: 4")
        print("  Landing zone patterns: 5")
        print("  WA Security best practices: 57")
        print("\nFlags: --json, --equivalence-matrix, --resiliency-tiers,")
        print("       --antipatterns, --patterns, --egress-compare [--gb N],")
        print("       --scca-matrix, --scca-components, --landing-zones,")
        print("       --wa-security, --csp-security")


if __name__ == "__main__":
    main()
