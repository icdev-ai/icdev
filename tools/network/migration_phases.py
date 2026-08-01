# [CUI // SP-CTI]
"""ICDEV™ Network Migration Phases Engine.

Computes info boxes, phase-state graphs, and consolidation analysis for the
three-panel migration phases view (/network/migration-phases/<topo_id>).

No Flask dependency — pure data functions that take/return dicts.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

logger = get_logger(__name__)


# ── Device type classification ─────────────────────────────────────────────────

_ROUTER_TYPES = {"router", "cisco-router", "juniper-router", "arista-router", "core-router", "pe-router", "ce-router"}
_SWITCH_L3_TYPES = {"switch-l3", "cisco-switch-l3", "arista-switch-l3", "distribution-switch", "core-switch"}
_SWITCH_L2_TYPES = {"switch-l2", "cisco-switch-l2", "access-switch", "aggregation-switch"}
_FIREWALL_TYPES = {"firewall", "cisco-firewall", "juniper-srx", "palo-alto", "checkpoint", "fortinet", "asa"}
_LB_TYPES = {"load-balancer", "f5-ltm", "nginx", "haproxy", "citrix-adc"}
_WAN_TYPES = {"wan-link", "circuit", "mpls", "internet", "vpn", "leased-line", "sd-wan"}
_CLOUD_TYPES = {"aws-vpc", "aws-tgw", "az-vnet", "gcp-vpc", "cloud-region"}


def _classify(node_type: str) -> str:
    t = (node_type or "").lower()
    if t in _ROUTER_TYPES:
        return "router"
    if t in _SWITCH_L3_TYPES:
        return "switch-l3"
    if t in _SWITCH_L2_TYPES:
        return "switch-l2"
    if t in _FIREWALL_TYPES:
        return "firewall"
    if t in _LB_TYPES:
        return "load-balancer"
    if t in _WAN_TYPES:
        return "wan"
    if t in _CLOUD_TYPES:
        return "cloud"
    return "other"


def _vendor(node: dict) -> str:
    v = (node.get("vendor") or node.get("meta", {}).get("vendor") or "").strip()
    if v:
        return v
    t = (node.get("type") or "").lower()
    if "cisco" in t:
        return "Cisco"
    if "juniper" in t or "srx" in t or "mx" in t:
        return "Juniper"
    if "arista" in t:
        return "Arista"
    if "palo" in t:
        return "Palo Alto"
    if "fortinet" in t or "fortigate" in t:
        return "Fortinet"
    if "checkpoint" in t:
        return "Check Point"
    return "Unknown"


def _is_eol(node: dict) -> bool:
    eol = node.get("eol") or node.get("meta", {}).get("eol_date") or ""
    if not eol:
        return False
    try:
        eol_dt = datetime.fromisoformat(str(eol).split("T")[0])
        return eol_dt < datetime.now()
    except Exception:
        return False


def _rack_units(node: dict) -> int:
    return int(node.get("rack_units") or node.get("meta", {}).get("rack_units") or 2)


def _power_watts(node: dict) -> int:
    return int(node.get("power_watts") or node.get("meta", {}).get("power_watts") or 200)


# ── Info Box Computation ───────────────────────────────────────────────────────

def compute_infoboxes(graph: dict, phase_key: str = "current", phase_meta: dict | None = None) -> list[dict]:
    """Compute all critical info boxes for a diagram panel.

    Args:
        graph: Topology graph dict with "nodes" and "edges".
        phase_key: "current", "phase-N", or "final".
        phase_meta: Phase metadata dict (title, duration, risk, rollback, etc.).

    Returns:
        List of info box dicts, each with: id, title, icon, color, rows.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    boxes: list[dict] = []

    # Box 1: Device Inventory
    boxes.append(_box_device_inventory(nodes))

    # Box 2: Link Utilization
    boxes.append(_box_link_utilization(edges, nodes))

    # Box 3: IP & VLAN Addressing
    boxes.append(_box_ip_vlan(nodes, edges))

    # Box 4: Redundancy Health
    boxes.append(_box_redundancy(nodes, edges))

    # Box 5: Routing Protocols
    boxes.append(_box_routing_protocols(nodes, edges))

    # Box 6: Security Posture
    boxes.append(_box_security(nodes, edges))

    # Box 7: WAN / Circuits
    boxes.append(_box_wan_circuits(nodes, edges))

    # Box 8: Hardware Health
    boxes.append(_box_hardware_health(nodes))

    # Phase-specific boxes
    if phase_key.startswith("phase") and phase_meta:
        boxes.append(_box_phase_scope(phase_meta, nodes))
        boxes.append(_box_change_risk(phase_meta))
        boxes.append(_box_port_mapping(phase_meta))
        boxes.append(_box_validation_checklist(phase_meta))

    return boxes


def _box_device_inventory(nodes: list[dict]) -> dict:
    counts: dict[str, int] = {}
    vendors: dict[str, int] = {}
    eol_count = 0
    for n in nodes:
        cls = _classify(n.get("type", ""))
        counts[cls] = counts.get(cls, 0) + 1
        v = _vendor(n)
        vendors[v] = vendors.get(v, 0) + 1
        if _is_eol(n):
            eol_count += 1

    rows = [
        {"label": "Total Devices", "value": str(len(nodes)), "status": ""},
        {"label": "Routers", "value": str(counts.get("router", 0)), "status": ""},
        {"label": "Switches (L3)", "value": str(counts.get("switch-l3", 0)), "status": ""},
        {"label": "Switches (L2)", "value": str(counts.get("switch-l2", 0)), "status": ""},
        {"label": "Firewalls", "value": str(counts.get("firewall", 0)), "status": ""},
        {"label": "Load Balancers", "value": str(counts.get("load-balancer", 0)), "status": ""},
    ]
    vendor_str = ", ".join(f"{v} ({c})" for v, c in sorted(vendors.items(), key=lambda x: -x[1]) if v != "Unknown")
    rows.append({"label": "Vendors", "value": vendor_str or "—", "status": ""})
    rows.append({
        "label": "EOL Devices",
        "value": str(eol_count),
        "status": "warn" if eol_count > 0 else "ok",
    })

    return {
        "id": "device-inventory",
        "title": "Device Inventory",
        "icon": "🖥",
        "color": "#1e6eb5",
        "rows": rows,
    }


def _box_link_utilization(edges: list[dict], nodes: list[dict]) -> dict:
    total = len(edges)
    # Derive utilization from edge metadata (stored or synthetic)
    utils = []
    congested = 0
    dark = 0
    for e in edges:
        util = float(e.get("utilization_pct") or e.get("meta", {}).get("utilization_pct") or 0)
        utils.append((e.get("label") or f"{e.get('source','?')}→{e.get('target','?')}", util))
        if util > 80:
            congested += 1
        if util == 0:
            dark += 1

    avg_util = round(sum(u for _, u in utils) / len(utils), 1) if utils else 0
    top5 = sorted(utils, key=lambda x: -x[1])[:5]

    rows = [
        {"label": "Total Links", "value": str(total), "status": ""},
        {"label": "Avg Utilization", "value": f"{avg_util}%", "status": "warn" if avg_util > 70 else "ok"},
        {"label": "Congested (>80%)", "value": str(congested), "status": "warn" if congested > 0 else "ok"},
        {"label": "Dark / Unused", "value": str(dark), "status": "info"},
    ]
    for label, util in top5:
        if util > 0:
            rows.append({"label": f"  {label[:20]}", "value": f"{util:.0f}%", "status": "warn" if util > 80 else ""})

    return {
        "id": "link-utilization",
        "title": "Link Utilization",
        "icon": "📊",
        "color": "#2e7d32",
        "rows": rows,
    }


def _box_ip_vlan(nodes: list[dict], edges: list[dict]) -> dict:
    mgmt_ips = [n.get("ip") or n.get("mgmt_ip") or n.get("meta", {}).get("mgmt_ip") for n in nodes]
    mgmt_ips = [ip for ip in mgmt_ips if ip]
    vlans: set[int] = set()
    subnets: set[str] = set()
    ipv6_count = 0
    for n in nodes:
        meta = n.get("meta") or {}
        for v in (meta.get("vlans") or []):
            try:
                vlans.add(int(v))
            except Exception:
                pass
        if meta.get("ipv6_enabled"):
            ipv6_count += 1
        for sn in (meta.get("subnets") or []):
            subnets.add(sn)

    rows = [
        {"label": "Devices with Mgmt IP", "value": str(len(mgmt_ips)), "status": ""},
        {"label": "VLANs Configured", "value": str(len(vlans)), "status": ""},
        {"label": "Subnets", "value": str(len(subnets)) if subnets else "—", "status": ""},
        {"label": "IPv6 Enabled", "value": str(ipv6_count), "status": "info" if ipv6_count > 0 else ""},
        {"label": "VLAN Range", "value": f"{min(vlans)}–{max(vlans)}" if vlans else "—", "status": ""},
    ]
    if mgmt_ips:
        rows.append({"label": "Sample Mgmt IP", "value": mgmt_ips[0], "status": ""})

    return {
        "id": "ip-vlan",
        "title": "IP & VLAN Addressing",
        "icon": "🌐",
        "color": "#6a1b9a",
        "rows": rows,
    }


def _box_redundancy(nodes: list[dict], edges: list[dict]) -> dict:
    ha_pairs = 0
    lacp_bonds = 0
    spofs = 0
    stp_mode = "Unknown"
    for n in nodes:
        meta = n.get("meta") or {}
        if meta.get("ha_pair") or meta.get("ha_partner"):
            ha_pairs += 1
        if meta.get("lacp_enabled") or meta.get("port_channel_count", 0) > 0:
            lacp_bonds += int(meta.get("port_channel_count") or 1)
        if meta.get("is_spof"):
            spofs += 1
        if meta.get("stp_mode"):
            stp_mode = meta["stp_mode"]

    ha_pairs = ha_pairs // 2 if ha_pairs > 1 else ha_pairs  # pairs not individual nodes

    rows = [
        {"label": "HA Pairs", "value": str(ha_pairs), "status": "warn" if ha_pairs == 0 else "ok"},
        {"label": "LACP Bonds", "value": str(lacp_bonds), "status": ""},
        {"label": "Spanning Tree", "value": stp_mode, "status": ""},
        {
            "label": "Single Points of Failure",
            "value": str(spofs),
            "status": "critical" if spofs > 2 else "warn" if spofs > 0 else "ok",
        },
    ]

    return {
        "id": "redundancy",
        "title": "Redundancy Health",
        "icon": "🔁",
        "color": "#00838f",
        "rows": rows,
    }


def _box_routing_protocols(nodes: list[dict], edges: list[dict]) -> dict:
    bgp_sessions = 0
    bgp_asn = "—"
    ospf_areas: set[str] = set()
    isis_nets: set[str] = set()
    static_routes = 0
    for n in nodes:
        meta = n.get("meta") or {}
        bgp_sessions += int(meta.get("bgp_session_count") or 0)
        if meta.get("bgp_asn") and bgp_asn == "—":
            bgp_asn = str(meta["bgp_asn"])
        for a in (meta.get("ospf_areas") or []):
            ospf_areas.add(str(a))
        for net in (meta.get("isis_nets") or []):
            isis_nets.add(net)
        static_routes += int(meta.get("static_route_count") or 0)

    rows = [
        {"label": "BGP ASN", "value": bgp_asn, "status": ""},
        {"label": "BGP Sessions", "value": str(bgp_sessions), "status": ""},
        {"label": "OSPF Areas", "value": str(len(ospf_areas)) if ospf_areas else "0", "status": ""},
        {"label": "ISIS Nets", "value": str(len(isis_nets)) if isis_nets else "0", "status": ""},
        {"label": "Static Routes", "value": str(static_routes), "status": ""},
    ]

    return {
        "id": "routing-protocols",
        "title": "Routing Protocols",
        "icon": "🛣",
        "color": "#e65100",
        "rows": rows,
    }


def _box_security(nodes: list[dict], edges: list[dict]) -> dict:
    fw_count = sum(1 for n in nodes if _classify(n.get("type", "")) == "firewall")
    zones: set[str] = set()
    acl_count = 0
    telnet_risk = 0
    ssh_count = 0
    snmp_v3_count = 0
    for n in nodes:
        meta = n.get("meta") or {}
        if meta.get("zone"):
            zones.add(meta["zone"])
        acl_count += int(meta.get("acl_count") or 0)
        if meta.get("telnet_enabled"):
            telnet_risk += 1
        if meta.get("ssh_enabled"):
            ssh_count += 1
        if meta.get("snmp_version") == "v3":
            snmp_v3_count += 1

    rows = [
        {"label": "Firewalls", "value": str(fw_count), "status": "warn" if fw_count == 0 else "ok"},
        {"label": "Security Zones", "value": str(len(zones)), "status": ""},
        {"label": "ACL Rules", "value": str(acl_count), "status": ""},
        {"label": "SSH Enabled", "value": str(ssh_count), "status": "ok" if ssh_count > 0 else "warn"},
        {
            "label": "Telnet Enabled",
            "value": str(telnet_risk),
            "status": "critical" if telnet_risk > 0 else "ok",
        },
        {"label": "SNMPv3", "value": str(snmp_v3_count), "status": ""},
    ]

    return {
        "id": "security-posture",
        "title": "Security Posture",
        "icon": "🔒",
        "color": "#b71c1c",
        "rows": rows,
    }


def _box_wan_circuits(nodes: list[dict], edges: list[dict]) -> dict:
    wan_edges = [e for e in edges if _classify(e.get("type", "")) == "wan"
                 or e.get("circuit_type") or e.get("meta", {}).get("provider")]
    providers: set[str] = set()
    total_bw_gbps = 0.0
    next_expiry = "—"
    for e in edges:
        meta = e.get("meta") or {}
        if meta.get("provider"):
            providers.add(meta["provider"])
        bw = float(e.get("bandwidth_gbps") or e.get("bandwidth") or meta.get("bandwidth_gbps") or 0)
        total_bw_gbps += bw
        if meta.get("contract_expiry") and next_expiry == "—":
            next_expiry = str(meta["contract_expiry"])

    rows = [
        {"label": "WAN Links", "value": str(len(wan_edges)), "status": ""},
        {"label": "Providers", "value": ", ".join(sorted(providers)) if providers else "—", "status": ""},
        {"label": "Total WAN BW", "value": f"{total_bw_gbps:.1f} Gbps" if total_bw_gbps else "—", "status": ""},
        {"label": "Next Contract Expiry", "value": next_expiry, "status": "warn" if next_expiry != "—" else ""},
    ]

    return {
        "id": "wan-circuits",
        "title": "WAN / Circuits",
        "icon": "🌍",
        "color": "#37474f",
        "rows": rows,
    }


def _box_hardware_health(nodes: list[dict]) -> dict:
    high_cpu = 0
    high_mem = 0
    err_ifaces = 0
    oldest_years = 0
    oldest_name = "—"
    for n in nodes:
        meta = n.get("meta") or {}
        if float(meta.get("cpu_pct") or 0) > 80:
            high_cpu += 1
        if float(meta.get("memory_pct") or 0) > 85:
            high_mem += 1
        err_ifaces += int(meta.get("interface_error_count") or 0)
        age_years = int(meta.get("age_years") or 0)
        if age_years > oldest_years:
            oldest_years = age_years
            oldest_name = n.get("label") or n.get("id") or "—"

    rows = [
        {"label": "High CPU (>80%)", "value": str(high_cpu), "status": "warn" if high_cpu > 0 else "ok"},
        {"label": "High Memory (>85%)", "value": str(high_mem), "status": "warn" if high_mem > 0 else "ok"},
        {"label": "Interface Errors", "value": str(err_ifaces), "status": "warn" if err_ifaces > 0 else "ok"},
        {
            "label": "Oldest Device",
            "value": f"{oldest_name} ({oldest_years}yr)" if oldest_years else "—",
            "status": "warn" if oldest_years > 7 else "",
        },
    ]

    return {
        "id": "hardware-health",
        "title": "Hardware Health",
        "icon": "⚙",
        "color": "#4a148c",
        "rows": rows,
    }


def _box_phase_scope(phase_meta: dict, nodes: list[dict]) -> dict:
    changing = phase_meta.get("changing_devices") or []
    new_devs = phase_meta.get("new_devices") or []
    retiring = phase_meta.get("retiring_devices") or []
    port_maps = phase_meta.get("port_mappings") or []

    rows = [
        {"label": "Phase", "value": f"{phase_meta.get('phase_num','?')} of {phase_meta.get('total_phases','?')}", "status": ""},
        {"label": "Title", "value": str(phase_meta.get("title", "—"))[:30], "status": ""},
        {"label": "Duration", "value": f"{phase_meta.get('duration_days', 0)} days", "status": ""},
        {"label": "Maint. Window", "value": str(phase_meta.get("maintenance_window") or "TBD"), "status": ""},
        {"label": "Devices Changing", "value": str(len(changing)), "status": ""},
        {"label": "New Devices", "value": str(len(new_devs)), "status": "info"},
        {"label": "Retiring Devices", "value": str(len(retiring)), "status": "warn"},
        {"label": "Port Remappings", "value": str(len(port_maps)), "status": ""},
    ]

    return {
        "id": "phase-scope",
        "title": "Phase Scope",
        "icon": "📋",
        "color": "#f57f17",
        "rows": rows,
    }


def _box_change_risk(phase_meta: dict) -> dict:
    risk = str(phase_meta.get("risk_level") or "MEDIUM").upper()
    risk_status = {"LOW": "ok", "MEDIUM": "warn", "HIGH": "warn", "CRITICAL": "critical"}.get(risk, "warn")

    rows = [
        {"label": "Risk Level", "value": risk, "status": risk_status},
        {"label": "Customer Impact", "value": str(phase_meta.get("customer_impact") or "Minimal"), "status": ""},
        {"label": "Rollback Time", "value": str(phase_meta.get("rollback_time_min") or "30") + " min", "status": ""},
        {"label": "Rollback Trigger", "value": str(phase_meta.get("rollback_criteria") or "BGP down > 5min")[:35], "status": ""},
        {"label": "Dependencies", "value": str(phase_meta.get("dependencies") or "None"), "status": ""},
        {"label": "Parallel Run", "value": "Yes" if phase_meta.get("parallel_run") else "No", "status": ""},
    ]

    return {
        "id": "change-risk",
        "title": "Change Risk",
        "icon": "⚠",
        "color": "#c62828",
        "rows": rows,
    }


def _box_port_mapping(phase_meta: dict) -> dict:
    pm = phase_meta.get("port_mappings") or []
    conflicts = [m for m in pm if m.get("conflict")]
    unmapped = [m for m in pm if not m.get("target")]

    rows = [
        {"label": "Ports Remapped", "value": str(len(pm)), "status": ""},
        {"label": "Unmapped (Manual)", "value": str(len(unmapped)), "status": "warn" if unmapped else "ok"},
        {"label": "Conflicts", "value": str(len(conflicts)), "status": "critical" if conflicts else "ok"},
        {"label": "LAG Reconfigured", "value": str(phase_meta.get("lag_reconfig_count") or 0), "status": ""},
    ]

    return {
        "id": "port-mapping",
        "title": "Port Mapping",
        "icon": "🔌",
        "color": "#00695c",
        "rows": rows,
    }


def _box_validation_checklist(phase_meta: dict) -> dict:
    pre_checks = phase_meta.get("pre_checks") or ["Backup config", "Verify BGP sessions", "Test rollback path"]
    post_checks = phase_meta.get("post_checks") or ["Verify BGP re-established", "Ping mesh", "Check log for errors"]

    rows = [
        {"label": "Pre-Checks", "value": str(len(pre_checks)), "status": ""},
        {"label": "Post-Checks", "value": str(len(post_checks)), "status": ""},
        {"label": "Go/No-Go Criteria", "value": str(phase_meta.get("go_nogo_count") or 3), "status": ""},
    ]
    for check in pre_checks[:3]:
        rows.append({"label": f"  ✓ {check[:28]}", "value": "", "status": ""})

    return {
        "id": "validation-checklist",
        "title": "Validation Checklist",
        "icon": "✅",
        "color": "#1b5e20",
        "rows": rows,
    }


def compute_final_infoboxes(current_graph: dict, final_graph: dict, consolidation: dict) -> list[dict]:
    """Compute info boxes for the Final/To-Be panel (current 8 + 4 final-specific)."""
    boxes = compute_infoboxes(final_graph, phase_key="final")
    boxes.append(_box_consolidation_results(consolidation))
    boxes.append(_box_tco_delta(consolidation))
    boxes.append(_box_performance_gains(current_graph, final_graph, consolidation))
    boxes.append(_box_compliance_improvement(consolidation))
    return boxes


def _box_consolidation_results(c: dict) -> dict:
    rows = [
        {"label": "Devices Removed", "value": str(c.get("devices_removed", 0)), "status": "ok"},
        {"label": "Rack Units Freed", "value": f"{c.get('rack_units_freed', 0)} U", "status": "ok"},
        {"label": "Power Reduction", "value": f"{c.get('power_saved_watts', 0)} W ({c.get('power_saved_kwh_yr', 0):,.0f} kWh/yr)", "status": "ok"},
        {"label": "Ports Consolidated", "value": str(c.get("ports_consolidated_summary", "—")), "status": ""},
        {"label": "Vendor Reduction", "value": str(c.get("vendor_reduction", "—")), "status": ""},
    ]
    return {"id": "consolidation-results", "title": "Consolidation Results", "icon": "📦", "color": "#1565c0", "rows": rows}


def _box_tco_delta(c: dict) -> dict:
    capex = c.get("capex_delta", 0)
    opex = c.get("opex_annual_delta", 0)
    tco = c.get("tco_3yr_delta", 0)
    rows = [
        {"label": "CapEx Delta", "value": f"${capex:+,.0f}", "status": "ok" if capex < 0 else "warn"},
        {"label": "OpEx Delta (annual)", "value": f"${opex:+,.0f}/yr", "status": "ok" if opex < 0 else "warn"},
        {"label": "Power Savings", "value": f"${c.get('power_cost_savings_yr', 0):,.0f}/yr", "status": "ok"},
        {"label": "Maint. Savings", "value": f"${c.get('maintenance_savings_yr', 0):,.0f}/yr", "status": "ok"},
        {"label": "3-Year TCO Delta", "value": f"${tco:+,.0f}", "status": "ok" if tco < 0 else "warn"},
    ]
    return {"id": "tco-delta", "title": "3-Year TCO Delta", "icon": "💰", "color": "#1b5e20", "rows": rows}


def _box_performance_gains(current: dict, final: dict, c: dict) -> dict:
    rows = [
        {"label": "Bandwidth Increase", "value": f"+{c.get('bw_increase_pct', 0):.0f}%", "status": "ok"},
        {"label": "Redundancy Gain", "value": f"SPOFs: {c.get('spof_before', 0)} → {c.get('spof_after', 0)}", "status": "ok"},
        {"label": "Avg Link Util", "value": f"{c.get('avg_util_before', 0):.0f}% → {c.get('avg_util_after', 0):.0f}%", "status": "ok"},
        {"label": "Latency Impact", "value": str(c.get("latency_change", "Neutral")), "status": ""},
        {"label": "New HA Pairs", "value": f"+{c.get('new_ha_pairs', 0)}", "status": "ok"},
    ]
    return {"id": "performance-gains", "title": "Performance Gains", "icon": "🚀", "color": "#00695c", "rows": rows}


def _box_compliance_improvement(c: dict) -> dict:
    rows = [
        {"label": "STIG Compliance", "value": f"{c.get('stig_before', 0):.0f}% → {c.get('stig_after', 0):.0f}%", "status": "ok"},
        {"label": "EOL Devices", "value": f"{c.get('eol_before', 0)} → {c.get('eol_after', 0)}", "status": "ok"},
        {"label": "Controls Added", "value": str(c.get("controls_added", 0)), "status": "ok"},
        {"label": "Audit Findings Resolved", "value": str(c.get("findings_resolved", 0)), "status": "ok"},
    ]
    return {"id": "compliance-improvement", "title": "Compliance Gains", "icon": "🏛", "color": "#4a148c", "rows": rows}


# ── Phase Graph Generation ─────────────────────────────────────────────────────

def generate_phase_graph(current_graph: dict, phase: dict) -> dict:
    """Overlay phase changes onto current topology.

    Colors nodes: existing=blue, changing=orange, new=green, retiring=red.
    """
    nodes = [dict(n) for n in current_graph.get("nodes", [])]
    edges = [dict(e) for e in current_graph.get("edges", [])]

    changing = set(phase.get("changing_devices") or [])
    new_devs = phase.get("new_devices") or []
    retiring = set(phase.get("retiring_devices") or [])

    for n in nodes:
        nid = n.get("id", "")
        if nid in retiring:
            n["phase_state"] = "retiring"
            n["label"] = n.get("label", nid) + " (retiring)"
        elif nid in changing:
            n["phase_state"] = "changing"
        else:
            n["phase_state"] = "existing"

    for nd in new_devs:
        nd = dict(nd)
        nd["phase_state"] = "new"
        nd["label"] = nd.get("label", nd.get("id", "new")) + " (new)"
        nodes.append(nd)

    return {"nodes": nodes, "edges": edges}


def generate_final_graph(current_graph: dict, phases: list[dict]) -> dict:
    """Generate the final/to-be graph by applying all phase changes."""
    nodes = {n["id"]: dict(n) for n in current_graph.get("nodes", [])}
    edges = list(current_graph.get("edges", []))

    for phase in phases:
        for nid in (phase.get("retiring_devices") or []):
            nodes.pop(nid, None)
        for nd in (phase.get("new_devices") or []):
            nodes[nd["id"]] = dict(nd)
        for nid in (phase.get("changing_devices") or []):
            if nid in nodes and phase.get("device_updates", {}).get(nid):
                nodes[nid].update(phase["device_updates"][nid])

    return {"nodes": list(nodes.values()), "edges": edges}


# ── Consolidation Analysis ─────────────────────────────────────────────────────

def run_consolidation_analysis(current_graph: dict, final_graph: dict) -> dict:
    """Compute consolidation metrics: devices, racks, power, cost, performance."""
    cur_nodes = current_graph.get("nodes", [])
    fin_nodes = final_graph.get("nodes", [])
    cur_edges = current_graph.get("edges", [])
    fin_edges = final_graph.get("edges", [])

    cur_count = len(cur_nodes)
    fin_count = len(fin_nodes)
    removed = max(0, cur_count - fin_count)

    # Rack units
    cur_ru = sum(_rack_units(n) for n in cur_nodes)
    fin_ru = sum(_rack_units(n) for n in fin_nodes)
    ru_freed = max(0, cur_ru - fin_ru)

    # Power
    cur_watts = sum(_power_watts(n) for n in cur_nodes)
    fin_watts = sum(_power_watts(n) for n in fin_nodes)
    watts_saved = max(0, cur_watts - fin_watts)
    kwh_per_yr = round(watts_saved * 8760 / 1000, 0)

    # Cost estimates (rough)
    cost_per_kwh = 0.12
    power_cost_savings = round(kwh_per_yr * cost_per_kwh, 0)
    maint_per_device_yr = 1800  # avg annual maintenance cost per device
    maint_savings = removed * maint_per_device_yr
    opex_delta = -(power_cost_savings + maint_savings)
    capex_delta = removed * -5000  # rough avg HW cost saved by not replacing
    tco_3yr = opex_delta * 3 + capex_delta

    # Vendor reduction
    cur_vendors = {_vendor(n) for n in cur_nodes}
    fin_vendors = {_vendor(n) for n in fin_nodes}
    vendor_reduction = f"{len(cur_vendors)} → {len(fin_vendors)}" if cur_vendors != fin_vendors else f"{len(cur_vendors)} (unchanged)"

    # EOL
    cur_eol = sum(1 for n in cur_nodes if _is_eol(n))
    fin_eol = sum(1 for n in fin_nodes if _is_eol(n))

    # Bandwidth
    cur_bw = sum(float(e.get("bandwidth_gbps") or 0) for e in cur_edges)
    fin_bw = sum(float(e.get("bandwidth_gbps") or 0) for e in fin_edges)
    bw_pct = round((fin_bw - cur_bw) / max(cur_bw, 1) * 100, 1) if cur_bw else 0

    # SPOF (simplified: nodes with no redundant partner)
    cur_spof = sum(1 for n in cur_nodes if not (n.get("meta") or {}).get("ha_pair"))
    fin_spof = sum(1 for n in fin_nodes if not (n.get("meta") or {}).get("ha_pair"))
    # Estimate new HA pairs added
    new_ha = max(0, (fin_count - cur_count + removed))  # rough

    return {
        "devices_removed": removed,
        "rack_units_freed": ru_freed,
        "power_saved_watts": watts_saved,
        "power_saved_kwh_yr": kwh_per_yr,
        "capex_delta": capex_delta,
        "opex_annual_delta": opex_delta,
        "power_cost_savings_yr": power_cost_savings,
        "maintenance_savings_yr": maint_savings,
        "tco_3yr_delta": tco_3yr,
        "vendor_reduction": vendor_reduction,
        "eol_before": cur_eol,
        "eol_after": fin_eol,
        "bw_increase_pct": max(0, bw_pct),
        "spof_before": cur_spof,
        "spof_after": fin_spof,
        "new_ha_pairs": new_ha,
        "avg_util_before": 0,  # populated from edge metadata if available
        "avg_util_after": 0,
        "stig_before": 0,
        "stig_after": 0,
        "controls_added": 0,
        "findings_resolved": removed,  # reasonable proxy
        "latency_change": "Neutral" if removed == 0 else "Reduced (fewer hops)",
        "ports_consolidated_summary": f"{sum(int(n.get('meta',{}).get('port_count',24) or 24) for n in cur_nodes)} → {sum(int(n.get('meta',{}).get('port_count',24) or 24) for n in fin_nodes)}",
    }


# ── DB Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_missing_relation(exc: Exception) -> bool:
    """True when *exc* means the target table simply doesn't exist yet.

    Distinguishes the benign early-boot "table not created yet" case (which the
    best-effort consolidation cache tolerates) from a genuine failure such as a
    missing UNIQUE constraint behind the ON CONFLICT clause, which MUST surface
    (ndc-fix-03). Matches SQLite ("no such table") and PostgreSQL UndefinedTable
    ("relation \"…\" does not exist", SQLSTATE 42P01).
    """
    msg = str(exc).lower()
    if "no such table" in msg:  # sqlite
        return True
    if "relation" in msg and "does not exist" in msg:  # pg UndefinedTable
        return True
    return exc.__class__.__name__ == "UndefinedTable"


def save_consolidation(topo_id: str, analysis: dict) -> str | None:
    """Persist consolidation analysis to DB (upsert keyed on topo_id).

    Returns the new row id on success, or None when the target table is not yet
    created (benign — the cache is populated best-effort during early boot).

    Raises on any genuine failure. In particular, the upsert relies on a UNIQUE
    constraint on ``nc_consolidation_analysis.topo_id`` to back the
    ``ON CONFLICT(topo_id)`` clause; if that constraint is missing the database
    rejects the statement, and this surfaces the error instead of silently
    no-op'ing (which previously masked a broken save behind a warning).
    """
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        aid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO nc_consolidation_analysis
              (id, topo_id, current_device_count, final_device_count,
               devices_removed, rack_units_freed, power_saved_watts,
               capex_delta, opex_annual_delta, tco_3yr_delta,
               bw_increase_pct, spof_count_before, spof_count_after,
               analysis_json, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(topo_id) DO UPDATE SET
              devices_removed=excluded.devices_removed,
              rack_units_freed=excluded.rack_units_freed,
              power_saved_watts=excluded.power_saved_watts,
              capex_delta=excluded.capex_delta,
              opex_annual_delta=excluded.opex_annual_delta,
              tco_3yr_delta=excluded.tco_3yr_delta,
              analysis_json=excluded.analysis_json,
              updated_at=excluded.updated_at
            """,
            (
                aid, topo_id,
                analysis.get("current_device_count", 0),
                analysis.get("final_device_count", 0),
                analysis.get("devices_removed", 0),
                analysis.get("rack_units_freed", 0),
                analysis.get("power_saved_watts", 0),
                analysis.get("capex_delta", 0),
                analysis.get("opex_annual_delta", 0),
                analysis.get("tco_3yr_delta", 0),
                analysis.get("bw_increase_pct", 0),
                analysis.get("spof_before", 0),
                analysis.get("spof_after", 0),
                json.dumps(analysis),
                _now(), _now(),
            ),
        )
        conn.commit()
        return aid
    except Exception as exc:
        if _is_missing_relation(exc):
            logger.warning(
                "consolidation save skipped — nc_consolidation_analysis not ready",
                exc_info=True,
            )
            return None
        # Genuine failure (e.g. missing UNIQUE constraint behind ON CONFLICT,
        # integrity violation). Surface it rather than silently discarding.
        logger.error("consolidation save failed for topo_id=%s", topo_id, exc_info=True)
        raise
    finally:
        conn.close()


def load_consolidation(topo_id: str) -> dict:
    """Load previously saved consolidation analysis from DB."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT analysis_json FROM nc_consolidation_analysis WHERE topo_id=%s", (topo_id,)
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


# ── Physical / Logical Topology Diagram Builders ──────────────────────────────

import re as _re


def _two_hop_subgraph(graph: dict, center_ids: list[str]) -> dict:
    """Return a subgraph containing all nodes within 2 hops of center_ids."""
    if not center_ids:
        return dict(graph)

    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    # Build adjacency
    adj: dict[str, list[str]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, []).append(t)
            adj.setdefault(t, []).append(s)

    # BFS up to 2 hops
    included = set()
    for cid in center_ids:
        if cid not in nodes:
            continue
        included.add(cid)
        for n1 in adj.get(cid, []):
            included.add(n1)
            for n2 in adj.get(n1, []):
                included.add(n2)

    filtered_nodes = [dict(nodes[nid]) for nid in included if nid in nodes]
    filtered_edges = [
        dict(e) for e in edges
        if e.get("source") in included and e.get("target") in included
    ]
    return {"nodes": filtered_nodes, "edges": filtered_edges}


def _get_device_interfaces(conn, device_id: str) -> list[dict]:
    """Fetch running config and extract interface names + descriptions."""
    try:
        row = conn.execute(
            """SELECT config_text FROM ni_device_configs
               WHERE device_id=%s ORDER BY created_at DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        config = row["config_text"] if row and row["config_text"] else ""
    except Exception:
        config = ""

    if not config:
        # Try resolving node_id -> device_id
        try:
            dev_row = conn.execute(
                "SELECT id FROM ni_devices WHERE node_id=%s", (device_id,)
            ).fetchone()
            if dev_row:
                did = dev_row["id"]
                row = conn.execute(
                    """SELECT config_text FROM ni_device_configs
                       WHERE device_id=%s ORDER BY created_at DESC LIMIT 1""",
                    (did,),
                ).fetchone()
                config = row["config_text"] if row and row["config_text"] else ""
        except Exception:
            pass

    if not config:
        return []

    interfaces = []
    # Juniper style: ge-0/0/0 { description "..."; }
    for m in _re.finditer(r'^\s*([gx]e-\d+/\d+/\d+|et-\d+/\d+/\d+|ae\d+|lo\d+|irb\.\d+)\s*\{', config, _re.MULTILINE):
        name = m.group(1)
        desc_match = _re.search(
            rf'{_re.escape(name)}\s*\{{[^}}]*description\s+"([^"]+)"',
            config, _re.DOTALL,
        )
        desc = desc_match.group(1) if desc_match else ""
        interfaces.append({"name": name, "description": desc})
    return interfaces


def _infer_interface(edge: dict, device_interfaces: list[dict], is_source: bool) -> str | None:
    """Heuristic: pick the best interface from device_interfaces for this edge."""
    etype = (edge.get("type") or "").lower()
    label = (edge.get("label") or "").lower()
    desc_keyword = ""

    if "wan" in etype or "isp" in label or "bgp" in label:
        desc_keyword = "isp"
    elif "circuit" in etype or "core" in label or "inside" in etype:
        desc_keyword = "south|lag|owned|core"
    elif "backbone" in etype:
        desc_keyword = "backbone|trunk"
    elif "e" in etype and "bgp" in label:
        desc_keyword = "bgp|peer"

    # Prefer interface whose description matches the keyword
    if desc_keyword:
        for iface in device_interfaces:
            if _re.search(desc_keyword, iface.get("description", "").lower()):
                return iface["name"]

    # Fallback: first physical interface (skip loopback)
    for iface in device_interfaces:
        if not iface["name"].startswith("lo") and not iface["name"].startswith("irb"):
            return iface["name"]

    # Last resort: any interface
    return device_interfaces[0]["name"] if device_interfaces else None


def _annotate_physical_edges(graph: dict, conn) -> dict:
    """Annotate each edge with source_interface and target_interface."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Pre-fetch interfaces for all nodes
    node_id_to_interfaces: dict[str, list[dict]] = {}
    for n in nodes:
        nid = n.get("id", "")
        node_id_to_interfaces[nid] = _get_device_interfaces(conn, nid)

    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        src_if = _infer_interface(e, node_id_to_interfaces.get(src, []), True)
        tgt_if = _infer_interface(e, node_id_to_interfaces.get(tgt, []), False)
        if src_if:
            e["source_interface"] = src_if
        if tgt_if:
            e["target_interface"] = tgt_if
        e["diagram_type"] = "physical"

    return graph


def _build_logical_graph(base_graph: dict) -> dict:
    """Build a logical view: edges colored by protocol, nodes annotated with ASN/subnet."""
    graph = {"nodes": [dict(n) for n in base_graph.get("nodes", [])], "edges": []}

    # Enhance nodes with logical metadata
    for n in graph["nodes"]:
        meta = n.get("meta") or {}
        n["logical"] = {
            "asn": meta.get("bgp_asn") or "",
            "role": meta.get("role") or n.get("type", ""),
            "subnet": meta.get("mgmt_ip") or meta.get("ip") or "",
            "vrfs": meta.get("vrfs") or [],
            "vlans": meta.get("vlans") or [],
        }

    for e in base_graph.get("edges", []):
        edge = dict(e)
        etype = (edge.get("type") or "").lower()
        label = (edge.get("label") or "").lower()

        # Determine logical protocol
        if "bgp" in label or "bgp" in etype:
            edge["logical_type"] = "bgp"
            edge["protocol"] = "BGP"
            edge["protocol_color"] = "#8e44ad"  # purple
        elif "ospf" in label or "ospf" in etype:
            edge["logical_type"] = "ospf"
            edge["protocol"] = "OSPF"
            edge["protocol_color"] = "#27ae60"  # green
        elif "isis" in label or "isis" in etype:
            edge["logical_type"] = "isis"
            edge["protocol"] = "ISIS"
            edge["protocol_color"] = "#e67e22"  # orange
        elif "static" in label or "static" in etype:
            edge["logical_type"] = "static"
            edge["protocol"] = "Static"
            edge["protocol_color"] = "#7f8c8d"  # gray
        elif "wan" in etype or "circuit" in etype or "backbone" in etype:
            edge["logical_type"] = "transport"
            edge["protocol"] = "Transport"
            edge["protocol_color"] = "#2980b9"  # blue
        elif "inside" in etype or "lag" in label:
            edge["logical_type"] = "internal"
            edge["protocol"] = "Internal"
            edge["protocol_color"] = "#1abc9c"  # teal
        else:
            edge["logical_type"] = "unknown"
            edge["protocol"] = "Unknown"
            edge["protocol_color"] = "#95a5a6"

        edge["diagram_type"] = "logical"
        graph["edges"].append(edge)

    return graph


def _phase_center_ids(phase: dict) -> list[str]:
    """Extract center node IDs from phase metadata."""
    props = phase.get("properties_json") or phase.get("properties") or {}
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            props = {}
    centers = []
    for key in ("changing_devices", "new_devices", "retiring_devices", "context_devices"):
        centers.extend(props.get(key) or [])
    # Also include any explicit phase lists
    centers.extend(phase.get("changing_devices") or [])
    centers.extend(phase.get("new_devices") or [])
    centers.extend(phase.get("retiring_devices") or [])
    return list(dict.fromkeys(centers))  # dedupe preserve order


def generate_phase_physical_graph(current_graph: dict, phase: dict, conn) -> dict:
    """Generate a 2-hop physical topology with interface annotations."""
    base = generate_phase_graph(current_graph, phase)
    centers = _phase_center_ids(phase)
    subgraph = _two_hop_subgraph(base, centers)
    return _annotate_physical_edges(subgraph, conn)


def generate_phase_logical_graph(current_graph: dict, phase: dict) -> dict:
    """Generate a 2-hop logical topology with protocol annotations."""
    base = generate_phase_graph(current_graph, phase)
    centers = _phase_center_ids(phase)
    subgraph = _two_hop_subgraph(base, centers)
    return _build_logical_graph(subgraph)


def compute_physical_infoboxes(graph: dict, phase_meta: dict | None = None) -> list[dict]:
    """Info boxes specific to the physical connectivity view."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    iface_edges = [e for e in edges if e.get("source_interface") or e.get("target_interface")]
    lag_count = sum(1 for e in edges if "ae" in (e.get("source_interface") or "") or "ae" in (e.get("target_interface") or ""))
    fiber_count = sum(1 for e in edges if "xe" in (e.get("source_interface") or "") or "xe" in (e.get("target_interface") or ""))
    copper_count = sum(1 for e in edges if "ge" in (e.get("source_interface") or "") or "ge" in (e.get("target_interface") or ""))

    # Identify the migration focal device
    focal = "—"
    if phase_meta:
        for key in ("changing_devices", "new_devices", "retiring_devices", "context_devices"):
            devs = phase_meta.get(key) or phase_meta.get("properties", {}).get(key) or []
            if devs:
                focal = devs[0]
                break

    rows = [
        {"label": "View", "value": "Physical (L1/L2)", "status": "info"},
        {"label": "Devices Shown", "value": str(len(nodes)), "status": ""},
        {"label": "Physical Links", "value": str(len(edges)), "status": ""},
        {"label": "Interface-Mapped Links", "value": str(len(iface_edges)), "status": "ok" if iface_edges else "warn"},
        {"label": "LAG / Port-Channels", "value": str(lag_count), "status": ""},
        {"label": "Fiber (10G+)", "value": str(fiber_count), "status": ""},
        {"label": "Copper (1G)", "value": str(copper_count), "status": ""},
        {"label": "Focal Device", "value": focal, "status": "info"},
    ]

    return [
        {"id": "phy-view-summary", "title": "Physical View Summary", "icon": "🔌", "color": "#1e6eb5", "rows": rows},
        _box_device_inventory(nodes),
        _box_link_utilization(edges, nodes),
        _box_hardware_health(nodes),
    ]


def compute_logical_infoboxes(graph: dict, phase_meta: dict | None = None) -> list[dict]:
    """Info boxes specific to the logical/routing view."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    protocols: dict[str, int] = {}
    bgp_peers = 0
    ospf_areas: set[str] = set()
    for e in edges:
        p = e.get("protocol") or e.get("logical_type", "Unknown")
        protocols[p] = protocols.get(p, 0) + 1
        if e.get("logical_type") == "bgp":
            bgp_peers += 1

    for n in nodes:
        meta = n.get("meta") or {}
        for a in (meta.get("ospf_areas") or []):
            ospf_areas.add(str(a))

    proto_rows = [{"label": p, "value": str(c), "status": "info"} for p, c in sorted(protocols.items(), key=lambda x: -x[1])]

    rows = [
        {"label": "View", "value": "Logical (L3)", "status": "info"},
        {"label": "Devices Shown", "value": str(len(nodes)), "status": ""},
        {"label": "Logical Links", "value": str(len(edges)), "status": ""},
        {"label": "BGP Peerings", "value": str(bgp_peers), "status": "ok" if bgp_peers else "warn"},
        {"label": "OSPF Areas", "value": ", ".join(sorted(ospf_areas)) if ospf_areas else "—", "status": ""},
    ]
    rows.extend(proto_rows)

    return [
        {"id": "log-view-summary", "title": "Logical View Summary", "icon": "🌐", "color": "#8e44ad", "rows": rows},
        _box_routing_protocols(nodes, edges),
        _box_ip_vlan(nodes, edges),
        _box_security(nodes, edges),
    ]
