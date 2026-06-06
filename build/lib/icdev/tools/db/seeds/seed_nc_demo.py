#!/usr/bin/env python3
# CUI // SP-CTI
"""NC Demo Seed -- populates network_canvas.db with realistic network design demo data.

Tables seeded (core subset):
  topologies (3), nc_sites (10), nc_circuits (15), nc_ipam_blocks (8),
  nc_peering_agreements (3), nc_compliance_profiles (3), nc_compliance_findings (10),
  nc_change_requests (5), nc_discovery_scans (4), nc_audit (10), nc_versions (6),
  nc_customers (5)

Usage:
    python tools/db/seeds/seed_nc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_nc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.network.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "network_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _safe_execute(conn, sql, params):
    expected = sql.count("?")
    actual = len(params) if isinstance(params, (list, tuple)) else 1
    if expected != actual:
        raise ValueError(f"Placeholder mismatch: {expected} placeholders but {actual} params")
    conn.execute(sql, params)


def _reset_demo_data(conn) -> None:
    for tbl in (
        "nc_customers", "nc_audit", "nc_discovery_scans", "nc_change_requests",
        "nc_compliance_findings", "nc_compliance_profiles", "nc_peering_agreements",
        "nc_ipam_blocks", "nc_circuits", "nc_sites", "nc_versions",
        "topologies",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_TOPOLOGY_IDS = {f"nc-topo-{i:03d}": _uid() for i in range(3)}

_TOPOLOGIES = [
    {
        "id": _TOPOLOGY_IDS["nc-topo-000"],
        "name": "Federal Backbone (MPLS)",
        "description": "Multi-site MPLS backbone connecting 5 federal agencies across 3 regions with redundant paths and SCCA compliance.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "dc-east", "type": "datacenter", "label": "DC East (Ashburn)", "x": 200, "y": 100},
                {"id": "dc-west", "type": "datacenter", "label": "DC West (Denver)", "x": 500, "y": 100},
                {"id": "dc-central", "type": "datacenter", "label": "DC Central (Dallas)", "x": 350, "y": 300},
                {"id": "pe-1", "type": "pe_router", "label": "PE Router 1", "x": 200, "y": 200},
                {"id": "pe-2", "type": "pe_router", "label": "PE Router 2", "x": 500, "y": 200},
                {"id": "ce-1", "type": "ce_router", "label": "CE Router (Agency A)", "x": 100, "y": 300},
                {"id": "ce-2", "type": "ce_router", "label": "CE Router (Agency B)", "x": 600, "y": 300},
                {"id": "fw-1", "type": "firewall", "label": "Palo Alto NGFW", "x": 350, "y": 400},
            ],
            "edges": [
                {"id": "e1", "source": "dc-east", "target": "pe-1"},
                {"id": "e2", "source": "dc-west", "target": "pe-2"},
                {"id": "e3", "source": "dc-central", "target": "pe-1"},
                {"id": "e4", "source": "dc-central", "target": "pe-2"},
                {"id": "e5", "source": "pe-1", "target": "ce-1"},
                {"id": "e6", "source": "pe-2", "target": "ce-2"},
                {"id": "e7", "source": "ce-1", "target": "fw-1"},
                {"id": "e8", "source": "ce-2", "target": "fw-1"},
            ],
        }),
        "template_id": "tpl-mpls-backbone",
    },
    {
        "id": _TOPOLOGY_IDS["nc-topo-001"],
        "name": "Zero Trust Campus Network",
        "description": "Zero Trust campus network with micro-segmentation, SDP gateways, and NIST 800-207 architecture.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "sdp-gw", "type": "gateway", "label": "SDP Gateway", "x": 350, "y": 50},
                {"id": "idp", "type": "idp", "label": "Identity Provider", "x": 200, "y": 50},
                {"id": "segment-hr", "type": "segment", "label": "HR Segment", "x": 100, "y": 200},
                {"id": "segment-it", "type": "segment", "label": "IT Segment", "x": 350, "y": 200},
                {"id": "segment-fin", "type": "segment", "label": "Finance Segment", "x": 600, "y": 200},
                {"id": "pa-1", "type": "policy_engine", "label": "Policy Engine", "x": 350, "y": 350},
                {"id": "siem", "type": "siem", "label": "SIEM / SOAR", "x": 500, "y": 350},
            ],
            "edges": [
                {"id": "e1", "source": "idp", "target": "sdp-gw"},
                {"id": "e2", "source": "sdp-gw", "target": "segment-hr"},
                {"id": "e3", "source": "sdp-gw", "target": "segment-it"},
                {"id": "e4", "source": "sdp-gw", "target": "segment-fin"},
                {"id": "e5", "source": "segment-hr", "target": "pa-1"},
                {"id": "e6", "source": "segment-it", "target": "pa-1"},
                {"id": "e7", "source": "segment-fin", "target": "pa-1"},
                {"id": "e8", "source": "pa-1", "target": "siem"},
            ],
        }),
        "template_id": "tpl-zero-trust-campus",
    },
    {
        "id": _TOPOLOGY_IDS["nc-topo-002"],
        "name": "Cloud Transit Architecture (AWS GovCloud)",
        "description": "AWS GovCloud transit architecture with Direct Connect, Transit Gateway, VPC peering, and TIC 3.0 compliance.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "onprem", "type": "datacenter", "label": "On-Prem DC", "x": 100, "y": 200},
                {"id": "dx", "type": "link", "label": "Direct Connect", "x": 250, "y": 200},
                {"id": "tgw", "type": "gateway", "label": "Transit Gateway", "x": 400, "y": 200},
                {"id": "vpc-prod", "type": "vpc", "label": "Prod VPC", "x": 550, "y": 100},
                {"id": "vpc-staging", "type": "vpc", "label": "Staging VPC", "x": 550, "y": 200},
                {"id": "vpc-mgmt", "type": "vpc", "label": "Mgmt VPC", "x": 550, "y": 300},
                {"id": "tic", "type": "gateway", "label": "TIC 3.0 Gateway", "x": 700, "y": 200},
                {"id": "internet", "type": "cloud", "label": "Internet", "x": 850, "y": 200},
            ],
            "edges": [
                {"id": "e1", "source": "onprem", "target": "dx"},
                {"id": "e2", "source": "dx", "target": "tgw"},
                {"id": "e3", "source": "tgw", "target": "vpc-prod"},
                {"id": "e4", "source": "tgw", "target": "vpc-staging"},
                {"id": "e5", "source": "tgw", "target": "vpc-mgmt"},
                {"id": "e6", "source": "tgw", "target": "tic"},
                {"id": "e7", "source": "tic", "target": "internet"},
            ],
        }),
        "template_id": "tpl-aws-transit",
    },
]

_CUSTOMERS = [
    ("cust-001", "Dept of Veterans Affairs", "federal", "John Smith", "john.smith@va.gov"),
    ("cust-002", "US Army Cyber Command", "federal", "Sarah Johnson", "sarah.j@army.mil"),
    ("cust-003", "DHS CISA", "federal", "Mike Brown", "mike.brown@cisa.dhs.gov"),
    ("cust-004", "NASA JPL", "federal", "Lisa Davis", "lisa.davis@jpl.nasa.gov"),
    ("cust-005", "NOAA", "federal", "Tom Wilson", "tom.wilson@noaa.gov"),
]

_SITES = []
_site_names = [
    ("Ashburn DC", "Ashburn", "VA", "US", "datacenter"),
    ("Denver DC", "Denver", "CO", "US", "datacenter"),
    ("Dallas POP", "Dallas", "TX", "US", "pop"),
    ("Seattle Office", "Seattle", "WA", "US", "office"),
    ("Atlanta Office", "Atlanta", "GA", "US", "office"),
    ("Chicago DC", "Chicago", "IL", "US", "datacenter"),
    ("Phoenix POP", "Phoenix", "AZ", "US", "pop"),
    ("NYC Office", "New York", "NY", "US", "office"),
    ("LA Office", "Los Angeles", "CA", "US", "office"),
    ("Honolulu Office", "Honolulu", "HI", "US", "office"),
]
for i, (name, city, state, country, stype) in enumerate(_site_names):
    _SITES.append({
        "id": _uid(),
        "customer_id": _CUSTOMERS[i % len(_CUSTOMERS)][0],
        "name": name,
        "address": f"{random.randint(100, 9999)} Main St",
        "city": city,
        "state": state,
        "country": country,
        "site_type": stype,
        "classification": "CUI",
    })

_CIRCUITS = []
_carriers = ["Lumen", "Verizon", "AT&T", "CenturyLink", "Zayo", "Cogent", "Equinix"]
_bandwidths = ["1G", "10G", "40G", "100G", "10M", "100M"]
for i in range(15):
    _CIRCUITS.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i % len(_TOPOLOGIES)]["id"],
        "circuit_id": f"CIR-{i+1:04d}",
        "carrier": random.choice(_carriers),
        "circuit_type": random.choice(["mpls", "dedicated", "wave", "ip-transit", "cross-connect"]),
        "bandwidth": random.choice(_bandwidths),
        "handoff_a": random.choice(["SMF", "MMF", "RJ45", "CWDM"]),
        "handoff_z": random.choice(["SMF", "MMF", "RJ45", "CWDM"]),
        "customer": _CUSTOMERS[i % len(_CUSTOMERS)][1],
        "site": _SITES[i % len(_SITES)]["name"],
        "monthly_cost_usd": round(random.uniform(500, 15000), 2),
        "contract_start": _ts(i * 24),
        "contract_end": _ts(i * 24 + 8760),
        "sla_uptime_pct": round(random.uniform(99.5, 99.999), 3),
        "install_status": random.choice(["active", "active", "active", "planned", "pending"]),
        "notes": f"Circuit {i+1} notes",
    })

_IPAM_BLOCKS = []
_networks = [
    ("10.0.0.0/16", "ipv4", 100, "global", "Production"),
    ("10.1.0.0/16", "ipv4", 200, "global", "Staging"),
    ("10.2.0.0/16", "ipv4", 300, "global", "Management"),
    ("192.168.0.0/24", "ipv4", 10, "site-a", "Office LAN"),
    ("172.16.0.0/20", "ipv4", 50, "site-b", "Data Center"),
    ("fd00:1::/48", "ipv6", None, "global", "IPv6 Production"),
    ("fd00:2::/48", "ipv6", None, "global", "IPv6 Staging"),
    ("10.255.0.0/24", "ipv4", 1, "global", "Loopback"),
]
for i, (net, af, vlan, vrf, desc) in enumerate(_networks):
    _IPAM_BLOCKS.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i % len(_TOPOLOGIES)]["id"],
        "network": net,
        "address_family": af,
        "vlan_id": vlan,
        "vrf": vrf,
        "description": desc,
        "site_id": _SITES[i % len(_SITES)]["id"],
        "gateway": f"{net.split('/')[0][:-1]}1" if af == "ipv4" else "",
        "gateway_v6": f"{net.split('/')[0][:-1]}1" if af == "ipv6" else "",
        "utilization_pct": round(random.uniform(10, 85), 1),
    })

_PEERING_AGREEMENTS = []
for i in range(3):
    _PEERING_AGREEMENTS.append({
        "id": _uid(),
        "peer_name": random.choice(["Cloudflare", "Akamai", "NTT", "Hurricane Electric"]),
        "peer_asn": str(random.choice([13335, 32787, 2914, 6939])),
        "our_asn": "64512",
        "peering_type": random.choice(["settlement_free", "paid", "partial"]),
        "routing_method": random.choice(["bgp", "static", "ospf"]),
        "status": random.choice(["evaluation", "active", "inactive"]),
        "purpose": random.choice(["internet_exchange", "transit", "content_delivery"]),
        "purpose_category": "connectivity",
        "business_justification": f"Peering agreement {i+1} for improved latency and cost reduction.",
        "locations": json.dumps(["Ashburn, VA", "Dallas, TX"]),
        "port_speed": random.choice(["1G", "10G", "40G", "100G"]),
        "contract_start": _ts(i * 48),
        "contract_end": _ts(i * 48 + 8760),
        "monthly_cost": round(random.uniform(0, 5000), 2),
        "traffic_commit": "10G",
        "ratio_limit": "2:1",
        "sla_latency_ms": round(random.uniform(1, 20), 1),
        "sla_packet_loss": round(random.uniform(0.001, 0.1), 3),
        "sla_uptime_pct": round(random.uniform(99.5, 99.999), 3),
        "noc_contact": "John Smith",
        "noc_email": "noc@peer.net",
        "noc_phone": "+1-555-0100",
        "legal_entity": random.choice(["PeerNet LLC", "Global Transit Inc.", "IX Partners"]),
        "notes": f"Peering agreement {i+1} notes",
        "project_id": _uid(),
        "partner_id": _uid(),
        "approver_name": "",
        "approver_role": "",
        "approved_at": "",
    })

_COMPLIANCE_PROFILES = []
for i in range(3):
    _COMPLIANCE_PROFILES.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i]["id"],
        "regimes": json.dumps(["fisma_high", "stig", "nist_800_53"]),
        "classification": random.choice(["CUI", "SECRET", "PUBLIC"]),
        "environment": random.choice(["IL4", "IL5", "IL6"]),
        "auto_audit": 1,
    })

_COMPLIANCE_FINDINGS = []
for i in range(10):
    _COMPLIANCE_FINDINGS.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i % len(_TOPOLOGIES)]["id"],
        "audit_id": _uid(),
        "rule_id": f"NET-ENC-{i+1:03d}",
        "regime": random.choice(["fisma_high", "stig", "fips", "zta"]),
        "severity": random.choice(["CAT1", "CAT2", "CAT3"]),
        "title": f"Finding {i+1}: Network encryption compliance",
        "description": f"Description for finding {i+1}",
        "affected_entity": random.choice(["router-1", "switch-2", "fw-1"]),
        "affected_type": random.choice(["node", "edge", "topology"]),
        "status": random.choice(["open", "remediated", "accepted_risk"]),
        "fix_action": json.dumps({"action": "update_config", "params": {}}),
    })

_CHANGE_REQUESTS = []
for i in range(5):
    _CHANGE_REQUESTS.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i % len(_TOPOLOGIES)]["id"],
        "title": f"Change Request {i+1}",
        "description": f"Description for change request {i+1}",
        "status": random.choice(["draft", "submitted", "approved", "rejected"]),
        "submitter_name": random.choice(["engineer-a", "engineer-b", "admin"]),
        "submitted_at": _ts(i * 4),
        "document_json": json.dumps({"risk": random.choice(["low", "medium", "high"]), "impact": "medium"}),
    })

_DISCOVERY_SCANS = []
for i in range(4):
    _DISCOVERY_SCANS.append({
        "id": _uid(),
        "topology_id": _TOPOLOGIES[i % len(_TOPOLOGIES)]["id"],
        "name": f"Discovery Scan {i+1}",
        "method": random.choice(["snmp", "ssh", "netflow", "api"]),
        "targets": random.choice(["10.0.0.0/16", "192.168.0.0/24", "172.16.0.0/20"]),
        "config_json": json.dumps({"timeout": 30, "threads": 10}),
        "status": random.choice(["completed", "running", "failed"]),
        "devices_json": json.dumps([{"ip": f"10.0.0.{j}", "type": "router"} for j in range(1, 6)]),
        "graph_json": json.dumps({"nodes": [], "edges": []}),
        "stats_json": json.dumps({"devices_found": random.randint(10, 200), "devices_new": random.randint(0, 10)}),
        "error": "",
        "started_at": _ts(i * 6),
        "completed_at": _ts(i * 6 + 2) if random.random() > 0.2 else None,
    })


def seed_topologies(conn) -> int:
    sql = """INSERT OR IGNORE INTO topologies (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _TOPOLOGIES:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_customers(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_customers (
        id, name, customer_type, contact_name, contact_email, contract_ref, notes, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CUSTOMERS:
        _safe_execute(conn, sql, (
            row[0], row[1], row[2], row[3], row[4], "", "", _ts(count * 2),
        ))
        count += 1
    return count


def seed_sites(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_sites (
        id, customer_id, name, address, city, state, country, site_type, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SITES:
        _safe_execute(conn, sql, (
            row["id"], row["customer_id"], row["name"], row["address"],
            row["city"], row["state"], row["country"], row["site_type"],
            row["classification"], _ts(count * 2),
        ))
        count += 1
    return count


def seed_circuits(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_circuits (
        id, topology_id, circuit_id, carrier, circuit_type, bandwidth, handoff_a, handoff_z,
        customer, site, monthly_cost_usd, contract_start, contract_end, sla_uptime_pct,
        install_status, notes, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CIRCUITS:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["circuit_id"], row["carrier"],
            row["circuit_type"], row["bandwidth"], row["handoff_a"], row["handoff_z"],
            row["customer"], row["site"], row["monthly_cost_usd"], row["contract_start"],
            row["contract_end"], row["sla_uptime_pct"], row["install_status"],
            row["notes"], row["contract_start"], row["contract_start"],
        ))
        count += 1
    return count


def seed_ipam_blocks(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_ipam_blocks (
        id, topology_id, network, address_family, vlan_id, vrf, description, site_id,
        gateway, gateway_v6, utilization_pct, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _IPAM_BLOCKS:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["network"], row["address_family"],
            row["vlan_id"], row["vrf"], row["description"], row["site_id"],
            row["gateway"], row["gateway_v6"], row["utilization_pct"], _ts(count * 2),
        ))
        count += 1
    return count


def seed_peering_agreements(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_peering_agreements (
        id, peer_name, peer_asn, our_asn, peering_type, routing_method, status, purpose,
        purpose_category, business_justification, locations, port_speed, contract_start,
        contract_end, monthly_cost, traffic_commit, ratio_limit, sla_latency_ms,
        sla_packet_loss, sla_uptime_pct, noc_contact, noc_email, noc_phone, legal_entity,
        notes, project_id, created_at, updated_at, partner_id, approver_name, approver_role, approved_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _PEERING_AGREEMENTS:
        _safe_execute(conn, sql, (
            row["id"], row["peer_name"], row["peer_asn"], row["our_asn"],
            row["peering_type"], row["routing_method"], row["status"], row["purpose"],
            row["purpose_category"], row["business_justification"], row["locations"],
            row["port_speed"], row["contract_start"], row["contract_end"],
            row["monthly_cost"], row["traffic_commit"], row["ratio_limit"],
            row["sla_latency_ms"], row["sla_packet_loss"], row["sla_uptime_pct"],
            row["noc_contact"], row["noc_email"], row["noc_phone"], row["legal_entity"],
            row["notes"], row["project_id"], _ts(count * 2), _ts(count * 2),
            row["partner_id"], row["approver_name"], row["approver_role"], row["approved_at"],
        ))
        count += 1
    return count


def seed_compliance_profiles(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_compliance_profiles (
        id, topology_id, regimes, classification, environment, auto_audit, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _COMPLIANCE_PROFILES:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["regimes"], row["classification"],
            row["environment"], row["auto_audit"], _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_compliance_findings(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_compliance_findings (
        id, topology_id, audit_id, rule_id, regime, severity, title, description,
        affected_entity, affected_type, status, fix_action, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _COMPLIANCE_FINDINGS:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["audit_id"], row["rule_id"],
            row["regime"], row["severity"], row["title"], row["description"],
            row["affected_entity"], row["affected_type"], row["status"],
            row["fix_action"], _ts(count * 2),
        ))
        count += 1
    return count


def seed_change_requests(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_change_requests (
        id, topology_id, title, description, status, submitter_name, submitted_at, document_json, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CHANGE_REQUESTS:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["title"], row["description"], row["status"],
            row["submitter_name"], row["submitted_at"], row["document_json"],
            _ts(count * 4), _ts(count * 4),
        ))
        count += 1
    return count


def seed_discovery_scans(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_discovery_scans (
        id, topology_id, name, method, targets, config_json, status, devices_json, graph_json,
        stats_json, error, started_at, completed_at, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _DISCOVERY_SCANS:
        _safe_execute(conn, sql, (
            row["id"], row["topology_id"], row["name"], row["method"], row["targets"],
            row["config_json"], row["status"], row["devices_json"], row["graph_json"],
            row["stats_json"], row["error"], row["started_at"], row["completed_at"],
            _ts(count * 2),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_audit (
        action, entity_type, entity_id, details, user_id, classification, ts
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("topology_created", "topology", "Topology created"),
        ("circuit_added", "circuit", "Circuit added"),
        ("compliance_check", "compliance", "Compliance check run"),
        ("discovery_scan", "scan", "Discovery scan completed"),
        ("change_request", "change", "Change request submitted"),
    ]
    for i in range(10):
        action, etype, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            action, etype, _uid(), detail,
            random.choice(["admin", "engineer-a", "engineer-b"]),
            "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO nc_versions (
        id, topology_id, version_num, label, phase, graph_json, created_by, notes, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(6):
        topo = _TOPOLOGIES[i % len(_TOPOLOGIES)]
        _safe_execute(conn, sql, (
            _uid(), topo["id"], i + 1, f"v{i+1}",
            random.choice(["design", "review", "deploy"]),
            topo["graph_json"], "system", f"Version {i+1}", _ts(i * 5),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "topologies", "nc_customers", "nc_sites", "nc_circuits", "nc_ipam_blocks",
        "nc_peering_agreements", "nc_compliance_profiles", "nc_compliance_findings",
        "nc_change_requests", "nc_discovery_scans", "nc_audit", "nc_versions",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="NC Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "topologies": seed_topologies(conn),
            "nc_customers": seed_customers(conn),
            "nc_sites": seed_sites(conn),
            "nc_circuits": seed_circuits(conn),
            "nc_ipam_blocks": seed_ipam_blocks(conn),
            "nc_peering_agreements": seed_peering_agreements(conn),
            "nc_compliance_profiles": seed_compliance_profiles(conn),
            "nc_compliance_findings": seed_compliance_findings(conn),
            "nc_change_requests": seed_change_requests(conn),
            "nc_discovery_scans": seed_discovery_scans(conn),
            "nc_audit": seed_audit(conn),
            "nc_versions": seed_versions(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_nc] Seeded {counts}")
            print(f"[seed_nc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
