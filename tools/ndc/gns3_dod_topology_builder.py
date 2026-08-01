"""GNS3 DoD Complex Topology Builder — BCAP/NIPR/JWICS/CSP Demo Lab.

Topology:

    ISP-ATT ──┐
    ISP-LUMEN ─┤─► NIPR-EDGE-1 ──┐
  ISP-VERIZON ─► NIPR-EDGE-2 ──►  NIPR-CORE-RTR ──► BCAP-VDSS-FW ──► BCAP-VDSS-IDS ──► BCAP-VDMS ──► AWS-GOVCLOUD-IL4
                                          │                                                    │         └──► AZURE-GOV-IL5
                                          │                                                 BCAP-TCCM
                                          ├──► ONPREM-DC1-RTR ──► ONPREM-DC1-SW ──► ONPREM-SRV1
                                          │           │                             └──► ONPREM-SRV2
                                          │           │                             └──► ONPREM-WORKSTA
                                          │           └──► JWICS-EDGE ──► JWICS-CORE-SW ──► JWICS-SRV1
                                          │                                               └──► JWICS-SRV2
                                          └──► MGMT-JUMPHOST

IP Plan:
  ISP-ATT WAN:      203.0.113.0/30   NIPR-EDGE-1 ether1=.1
  ISP-LUMEN WAN:    198.51.100.0/30  NIPR-EDGE-1 ether2=.1
  ISP-VERIZON WAN:  192.0.2.0/30     NIPR-EDGE-2 ether1=.1
  NIPR EDGE-1 link: 10.10.0.0/30    EDGE-1=.1, CORE=.2
  NIPR EDGE-2 link: 10.10.0.4/30    EDGE-2=.5, CORE=.6
  BCAP ingress:     10.20.0.0/30    CORE=.1, VDSS-FW=.2
  VDSS chain:       10.20.1.0/30    VDSS-FW=.1, VDSS-IDS=.2
  IDS→VDMS:         10.20.2.0/30    VDSS-IDS=.1, VDMS=.2
  VDMS→TCCM:        10.20.3.0/30    VDMS=.1, TCCM=.2
  AWS link:         10.100.0.0/30   VDMS=.1, AWS=.2
  Azure link:       10.200.0.0/30   VDMS=.1, AZURE=.2
  OnPrem WAN:       10.30.0.0/30    CORE=.1, ONPREM=.2
  OnPrem LAN:       10.30.1.0/24    GW=10.30.1.1
  XD-Guard:         10.40.0.0/30    ONPREM=.1, JWICS-EDGE=.2
  JWICS transit:    192.168.100.0/30 EDGE=.1, SW=.2
  JWICS LAN:        192.168.100.0/24 GW=192.168.100.1
  MGMT:             10.99.0.0/30    CORE=.1, JUMPHOST=.2

Usage:
  python tools/ndc/gns3_dod_topology_builder.py [--server http://localhost:3080] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.network.adapters.gns3_adapter import GNS3Adapter
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ndc.gns3_dod_topology_builder")

# ── Template IDs (GNS3 built-ins — stable across installs) ────────────────────
_T_MIKROTIK = "28bcd7fe-3c87-4bd0-9c49-3b9aa1b63da6"
_T_VPCS     = "19021f99-e36f-394d-b4a1-8aaa902ab9cc"
_T_SWITCH   = "1966b864-93e7-32d5-965f-001384eec461"
_T_CLOUD    = "39e257dc-8412-3174-b6b3-0ee3ed6a43e9"

# ── Node definitions ────────────────────────────────────────────────────────────
# (name, template_id, x, y, role, segment, qemu_adapters)
# qemu_adapters: number of ethernet adapters for QEMU (MikroTik) nodes
_NODES = [
    # ISPs — commercial internet providers
    ("ISP-ATT",          _T_CLOUD,    -1300, -200, "cloud",  "ISP",    1),
    ("ISP-LUMEN",        _T_CLOUD,    -1300,    0, "cloud",  "ISP",    1),
    ("ISP-VERIZON",      _T_CLOUD,    -1300,  200, "cloud",  "ISP",    1),
    # NIPR — DoD unclassified network, dual-homed edge + L3 core
    ("NIPR-EDGE-1",      _T_MIKROTIK,  -850, -100, "router", "NIPR",   3),
    ("NIPR-EDGE-2",      _T_MIKROTIK,  -850,  200, "router", "NIPR",   2),
    ("NIPR-CORE-RTR",    _T_MIKROTIK,  -450,   50, "router", "NIPR",   5),
    # BCAP — DISA Boundary Cloud Access Point chain
    ("BCAP-VDSS-FW",     _T_MIKROTIK,    50,   50, "router", "BCAP",   2),
    ("BCAP-VDSS-IDS",    _T_MIKROTIK,   350,   50, "router", "BCAP",   2),
    ("BCAP-VDMS",        _T_MIKROTIK,   650,   50, "router", "BCAP",   4),
    ("BCAP-TCCM",        _T_VPCS,       650,  250, "host",   "BCAP",   1),
    # CSPs — cloud service providers
    ("AWS-GOVCLOUD-IL4", _T_CLOUD,     1050, -100, "cloud",  "CSP",    1),
    ("AZURE-GOV-IL5",    _T_CLOUD,     1050,  200, "cloud",  "CSP",    1),
    # On-Premise DC
    ("ONPREM-DC1-RTR",   _T_MIKROTIK,  -450,  400, "router", "OnPrem", 3),
    ("ONPREM-DC1-SW",    _T_SWITCH,    -450,  600, "switch", "OnPrem", 0),
    ("ONPREM-SRV1",      _T_VPCS,      -100,  550, "host",   "OnPrem", 1),
    ("ONPREM-SRV2",      _T_VPCS,      -100,  650, "host",   "OnPrem", 1),
    ("ONPREM-WORKSTA",   _T_VPCS,      -800,  600, "host",   "OnPrem", 1),
    # JWICS — classified enclave (air-gapped from NIPR)
    ("JWICS-EDGE",       _T_MIKROTIK,   -50,  700, "router", "JWICS",  2),
    ("JWICS-CORE-SW",    _T_SWITCH,     -50,  900, "switch", "JWICS",  0),
    ("JWICS-SRV1",       _T_VPCS,       250,  900, "host",   "JWICS",  1),
    ("JWICS-SRV2",       _T_VPCS,       250, 1000, "host",   "JWICS",  1),
    # MGMT — out-of-band management jump host
    ("MGMT-JUMPHOST",    _T_VPCS,      -200, -200, "host",   "MGMT",   1),
]

# ── Wiring ─────────────────────────────────────────────────────────────────────
# (from_name, from_adapter, from_port, to_name, to_adapter, to_port)
# MikroTik: adapter N = ether(N+1), port always 0
# Switch:   adapter 0, port 0..N
# VPCS/Cloud: adapter 0, port 0
_LINKS = [
    # ISPs → NIPR edge routers
    ("ISP-ATT",          0, 0,  "NIPR-EDGE-1",     0, 0),  # EDGE-1 ether1
    ("ISP-LUMEN",        0, 0,  "NIPR-EDGE-1",     1, 0),  # EDGE-1 ether2
    ("ISP-VERIZON",      0, 0,  "NIPR-EDGE-2",     0, 0),  # EDGE-2 ether1
    # NIPR edges → NIPR core
    ("NIPR-EDGE-1",      2, 0,  "NIPR-CORE-RTR",   0, 0),  # EDGE-1 ether3 → CORE ether1
    ("NIPR-EDGE-2",      1, 0,  "NIPR-CORE-RTR",   1, 0),  # EDGE-2 ether2 → CORE ether2
    # NIPR core → BCAP ingress
    ("NIPR-CORE-RTR",    2, 0,  "BCAP-VDSS-FW",    0, 0),  # CORE ether3 → VDSS-FW ether1
    # BCAP inspection chain: VDSS-FW → VDSS-IDS → VDMS
    ("BCAP-VDSS-FW",     1, 0,  "BCAP-VDSS-IDS",   0, 0),  # VDSS ether2 → IDS ether1
    ("BCAP-VDSS-IDS",    1, 0,  "BCAP-VDMS",       0, 0),  # IDS ether2 → VDMS ether1
    # BCAP-VDMS → CSPs + TCCM
    ("BCAP-VDMS",        1, 0,  "AWS-GOVCLOUD-IL4", 0, 0),  # VDMS ether2 → AWS
    ("BCAP-VDMS",        2, 0,  "AZURE-GOV-IL5",    0, 0),  # VDMS ether3 → Azure
    ("BCAP-VDMS",        3, 0,  "BCAP-TCCM",        0, 0),  # VDMS ether4 → TCCM
    # NIPR core → On-Prem DC
    ("NIPR-CORE-RTR",    3, 0,  "ONPREM-DC1-RTR",   0, 0),  # CORE ether4 → ONPREM ether1
    # On-Prem internal
    ("ONPREM-DC1-RTR",   1, 0,  "ONPREM-DC1-SW",    0, 0),  # ONPREM ether2 → SW port0
    ("ONPREM-DC1-SW",    0, 1,  "ONPREM-SRV1",      0, 0),  # SW port1
    ("ONPREM-DC1-SW",    0, 2,  "ONPREM-SRV2",      0, 0),  # SW port2
    ("ONPREM-DC1-SW",    0, 3,  "ONPREM-WORKSTA",   0, 0),  # SW port3
    # Cross-domain guard: ONPREM ether3 → JWICS-EDGE ether1
    ("ONPREM-DC1-RTR",   2, 0,  "JWICS-EDGE",       0, 0),
    # JWICS internal
    ("JWICS-EDGE",       1, 0,  "JWICS-CORE-SW",    0, 0),  # JWICS ether2 → SW port0
    ("JWICS-CORE-SW",    0, 1,  "JWICS-SRV1",       0, 0),  # SW port1
    ("JWICS-CORE-SW",    0, 2,  "JWICS-SRV2",       0, 0),  # SW port2
    # NIPR core → MGMT
    ("NIPR-CORE-RTR",    4, 0,  "MGMT-JUMPHOST",    0, 0),  # CORE ether5 → MGMT
]

# Management IP and metadata for each node (used by ZTP generator + probe)
DEVICE_MAP: dict = {
    "ISP-ATT":           {"ip": "203.0.113.2",    "role": "isp",                "os": "cloud",    "segment": "ISP"},
    "ISP-LUMEN":         {"ip": "198.51.100.2",   "role": "isp",                "os": "cloud",    "segment": "ISP"},
    "ISP-VERIZON":       {"ip": "192.0.2.2",      "role": "isp",                "os": "cloud",    "segment": "ISP"},
    "NIPR-EDGE-1":       {"ip": "10.10.0.1",      "role": "nipr_edge",          "os": "routeros", "segment": "NIPR",
                          "wan": ["203.0.113.1/30", "198.51.100.1/30"], "core": "10.10.0.1/30", "bgp_as": 65001},
    "NIPR-EDGE-2":       {"ip": "10.10.0.5",      "role": "nipr_edge",          "os": "routeros", "segment": "NIPR",
                          "wan": ["192.0.2.1/30"], "core": "10.10.0.5/30", "bgp_as": 65001},
    "NIPR-CORE-RTR":     {"ip": "10.10.0.2",      "role": "nipr_core",          "os": "routeros", "segment": "NIPR"},
    "BCAP-VDSS-FW":      {"ip": "10.20.0.2",      "role": "bcap_vdss_firewall", "os": "routeros", "segment": "BCAP"},
    "BCAP-VDSS-IDS":     {"ip": "10.20.1.2",      "role": "bcap_vdss_ids",      "os": "routeros", "segment": "BCAP"},
    "BCAP-VDMS":         {"ip": "10.20.2.2",      "role": "bcap_vdms",          "os": "routeros", "segment": "BCAP"},
    "BCAP-TCCM":         {"ip": "10.20.3.2",      "role": "bcap_tccm",          "os": "vpcs",     "segment": "BCAP"},
    "AWS-GOVCLOUD-IL4":  {"ip": "10.100.0.2",     "role": "csp",                "os": "cloud",    "segment": "CSP"},
    "AZURE-GOV-IL5":     {"ip": "10.200.0.2",     "role": "csp",                "os": "cloud",    "segment": "CSP"},
    "ONPREM-DC1-RTR":    {"ip": "10.30.0.2",      "role": "dc_router",          "os": "routeros", "segment": "OnPrem"},
    "ONPREM-DC1-SW":     {"ip": "",               "role": "switch",             "os": "switch",   "segment": "OnPrem"},
    "ONPREM-SRV1":       {"ip": "10.30.1.10",     "role": "server",             "os": "vpcs",     "segment": "OnPrem"},
    "ONPREM-SRV2":       {"ip": "10.30.1.11",     "role": "server",             "os": "vpcs",     "segment": "OnPrem"},
    "ONPREM-WORKSTA":    {"ip": "10.30.1.20",     "role": "workstation",        "os": "vpcs",     "segment": "OnPrem"},
    "JWICS-EDGE":        {"ip": "10.40.0.2",      "role": "jwics_edge",         "os": "routeros", "segment": "JWICS"},
    "JWICS-CORE-SW":     {"ip": "",               "role": "switch",             "os": "switch",   "segment": "JWICS"},
    "JWICS-SRV1":        {"ip": "192.168.100.10", "role": "classified_server",  "os": "vpcs",     "segment": "JWICS"},
    "JWICS-SRV2":        {"ip": "192.168.100.11", "role": "classified_server",  "os": "vpcs",     "segment": "JWICS"},
    "MGMT-JUMPHOST":     {"ip": "10.99.0.2",      "role": "jumphost",           "os": "vpcs",     "segment": "MGMT"},
}

PROJECT_NAME = "dod-bcap-lab"


def _create_from_template(g: GNS3Adapter, pid: str, name: str,
                           template_id: str, x: int, y: int) -> dict:
    return g._request("POST", f"/v2/projects/{pid}/templates/{template_id}",
                      body={"x": x, "y": y, "name": name, "compute_id": "local"})


def _create_builtin(g: GNS3Adapter, pid: str, name: str,
                    node_type: str, x: int, y: int) -> dict:
    return g._request("POST", f"/v2/projects/{pid}/nodes",
                      body={"name": name, "node_type": node_type,
                            "compute_id": "local", "x": x, "y": y})


def _patch_adapters(g: GNS3Adapter, pid: str, node_id: str, count: int) -> None:
    if count <= 1:
        return
    g._request("PUT", f"/v2/projects/{pid}/nodes/{node_id}",
               body={"properties": {"adapters": count}})


def _wire(g: GNS3Adapter, pid: str,
          a_id: str, a_adp: int, a_prt: int,
          b_id: str, b_adp: int, b_prt: int) -> dict:
    return g._request("POST", f"/v2/projects/{pid}/links", body={"nodes": [
        {"node_id": a_id, "adapter_number": a_adp, "port_number": a_prt},
        {"node_id": b_id, "adapter_number": b_adp, "port_number": b_prt},
    ]})


def _save_to_db(name: str, pid: str, name_to_id: dict) -> None:
    try:
        from tools.db.storage import get_connection
        import json as _json
        from datetime import datetime, timezone
        design = {
            "nodes": [
                {"id": nid, "label": n, "role": DEVICE_MAP.get(n, {}).get("role", ""),
                 "segment": DEVICE_MAP.get(n, {}).get("segment", "")}
                for n, nid in name_to_id.items()
            ],
            "edges": [],
        }
        conn = get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO ndc_topologies
                  (name, source, project_id, design_json, device_map, ztp_status, created_at, updated_at)
                VALUES (%s, 'gns3', %s, %s, %s, 'pending', %s, %s)
            """, (
                name, pid,
                _json.dumps(design), _json.dumps(DEVICE_MAP),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # DB unavailable — non-fatal
        logger.warning("_save_to_db: best-effort INSERT into ndc_topologies failed (non-blocking): %s", exc)


def build(server: str = "http://localhost:3080",
          project_name: str = PROJECT_NAME) -> dict:
    g = GNS3Adapter(server)

    health = g.health()
    if health["status"] != "ok":
        return {"status": "error", "error": f"GNS3 unreachable: {health.get('error')}"}

    # Find or create project
    projects = g.list_projects()
    project = next((p for p in projects if p["name"] == project_name), None)
    if not project:
        project = g.create_project(project_name)
        if project.get("status") == "error":
            return {"status": "error", "error": f"Cannot create project: {project.get('error')}"}

    pid = project.get("project_id") or project.get("project_id", "")

    # Existing nodes by name
    existing = {n["name"]: n for n in g._get_list(f"/v2/projects/{pid}/nodes")}
    name_to_id: dict[str, str] = {n: d["node_id"] for n, d in existing.items()}

    created, skipped, failed = [], [], []

    _BUILTIN_TYPES = {
        _T_VPCS:   "vpcs",
        _T_SWITCH: "ethernet_switch",
        _T_CLOUD:  "cloud",
    }

    for name, template_id, x, y, _role, segment, adapters in _NODES:
        if name in existing:
            skipped.append(name)
            continue

        # Try template first (works if Mikrotik CHR is installed)
        resp = _create_from_template(g, pid, name, template_id, x, y)
        if GNS3Adapter._is_error(resp):
            builtin = _BUILTIN_TYPES.get(template_id)
            if builtin:
                resp = _create_builtin(g, pid, name, builtin, x, y)
            if GNS3Adapter._is_error(resp):
                failed.append({"name": name, "error": resp.get("error", "")})
                continue

        nid = resp.get("node_id", "")
        name_to_id[name] = nid

        # Expand QEMU adapter count for multi-interface routers
        if template_id == _T_MIKROTIK and adapters > 1:
            _patch_adapters(g, pid, nid, adapters)
            time.sleep(0.15)

        created.append({"name": name, "node_id": nid[:8], "segment": segment})
        time.sleep(0.25)

    # Existing links as frozenset pairs to skip duplicates
    existing_links = g._get_list(f"/v2/projects/{pid}/links")
    linked_pairs: set = set()
    for lnk in existing_links:
        nodes = lnk.get("nodes", [])
        if len(nodes) == 2:
            linked_pairs.add(frozenset(
                (n["node_id"], n["adapter_number"], n["port_number"]) for n in nodes
            ))

    links_created, links_skipped, links_failed = [], [], []

    for a_name, a_adp, a_prt, b_name, b_adp, b_prt in _LINKS:
        a_id = name_to_id.get(a_name)
        b_id = name_to_id.get(b_name)
        if not a_id or not b_id:
            links_failed.append(f"{a_name}→{b_name} (node missing)")
            continue
        pair = frozenset([(a_id, a_adp, a_prt), (b_id, b_adp, b_prt)])
        if pair in linked_pairs:
            links_skipped.append(f"{a_name}→{b_name}")
            continue
        resp = _wire(g, pid, a_id, a_adp, a_prt, b_id, b_adp, b_prt)
        if GNS3Adapter._is_error(resp):
            links_failed.append(f"{a_name}(a{a_adp})→{b_name}(a{b_adp}): {resp.get('error','')}")
        else:
            links_created.append(f"{a_name}→{b_name}")
        time.sleep(0.1)

    _save_to_db(project_name, pid, name_to_id)

    # Start all nodes
    g._request("POST", f"/v2/projects/{pid}/nodes/start")

    return {
        "status":        "success" if not failed else "partial",
        "project":       project_name,
        "project_id":    pid,
        "gns3_url":      f"{server}/static/web-ui/server/1/project/{pid}",
        "segments":      ["ISP", "NIPR", "BCAP", "CSP", "OnPrem", "JWICS", "MGMT"],
        "nodes_created": created,
        "nodes_skipped": skipped,
        "nodes_failed":  failed,
        "links_created": links_created,
        "links_skipped": links_skipped,
        "links_failed":  links_failed,
        "device_map":    DEVICE_MAP,
        "next_step":     "python tools/ndc/ztp_dod_config_generator.py --json",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GNS3 DoD BCAP/NIPR/JWICS/CSP Topology Builder")
    parser.add_argument("--server",  default="http://localhost:3080")
    parser.add_argument("--project", default=PROJECT_NAME)
    parser.add_argument("--json",    action="store_true")
    args = parser.parse_args()

    result = build(args.server, args.project)
    if args.json:
        print(json.dumps(result, indent=2))
        return

    status = result.get("status", "error")
    if status == "error":
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Project  : {result['project']} ({result.get('project_id','')[:8]})")
    print(f"URL      : {result.get('gns3_url','')}")
    print(f"Segments : {', '.join(result['segments'])}")
    print(f"Nodes +  : {len(result['nodes_created'])} created")
    print(f"Nodes ~  : {len(result['nodes_skipped'])} skipped")
    if result["nodes_failed"]:
        print(f"Nodes !  : {len(result['nodes_failed'])} failed — {[n['name'] for n in result['nodes_failed']]}")
    print(f"Links +  : {len(result['links_created'])}")
    if result["links_failed"]:
        print(f"Links !  : {result['links_failed']}")
    print(f"Next     : {result['next_step']}")


if __name__ == "__main__":
    main()
