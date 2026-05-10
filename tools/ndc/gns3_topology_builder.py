"""GNS3 Topology Builder — NDC Lab Setup Tool.

Builds a 3-tier reference network topology in GNS3 via REST API:

  [NAT-GW] ─── [R1-EDGE] ─── [R2-CORE] ─── [SW-ACCESS1] ─── [PC1]
                                    │                       └── [PC2]
                                    └──── [SW-ACCESS2] ─── [SRV1]
                                                        └── [SRV2]

IP plan:
  NAT/WAN       → R1-EDGE ether0  : DHCP
  R1-EDGE ether1 ↔ R2-CORE ether0 : 10.0.0.0/30  (R1=.1, R2=.2)
  R2-CORE ether1 → SW-ACCESS1     : 10.1.0.0/24  (gw=10.1.0.1)
  R2-CORE ether2 → SW-ACCESS2     : 10.2.0.0/24  (gw=10.2.0.1)
  PC1  : 10.1.0.10/24  PC2  : 10.1.0.11/24
  SRV1 : 10.2.0.10/24  SRV2 : 10.2.0.11/24

Usage:
  python tools/ndc/gns3_topology_builder.py [--server http://localhost:3080] [--json]

Run once to stand up the lab. Idempotent — skips nodes that already exist by name.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.network.adapters.gns3_adapter import GNS3Adapter  # noqa: E402

# ── Template IDs (GNS3 built-ins — stable across installs) ────────────────
_T_MIKROTIK = "28bcd7fe-3c87-4bd0-9c49-3b9aa1b63da6"
_T_VPCS     = "19021f99-e36f-394d-b4a1-8aaa902ab9cc"
_T_SWITCH   = "1966b864-93e7-32d5-965f-001384eec461"
_T_CLOUD    = "39e257dc-8412-3174-b6b3-0ee3ed6a43e9"  # Cloud — no libvirt required

# ── Canvas layout positions ────────────────────────────────────────────────
_NODES = [
    # name          template_id    x      y    role
    ("CLOUD-WAN",   _T_CLOUD,    -1100,   80,  "cloud"),
    ("R2-CORE",     _T_MIKROTIK,  -450,   80,  "router"),
    ("SW-ACCESS1",  _T_SWITCH,    -150,  -80,  "switch"),
    ("SW-ACCESS2",  _T_SWITCH,    -150,  240,  "switch"),
    ("PC1",         _T_VPCS,       150,  -160, "host"),
    ("PC2",         _T_VPCS,       150,   -0,  "host"),
    ("SRV1",        _T_VPCS,       150,   160, "host"),
    ("SRV2",        _T_VPCS,       150,   320, "host"),
]

# ── Wiring plan: (from_name, from_adapter, from_port, to_name, to_adapter, to_port)
_LINKS = [
    # Cloud WAN → R1-EDGE Ethernet0
    ("CLOUD-WAN",  0, 0, "Mikrotik-1", 0, 0),
    # R1-EDGE Ethernet1 → R2-CORE Ethernet0
    ("Mikrotik-1", 1, 0, "R2-CORE",    0, 0),
    # R2-CORE Ethernet1 → SW-ACCESS1 port0
    ("R2-CORE",    1, 0, "SW-ACCESS1", 0, 0),
    # R2-CORE Ethernet2 → SW-ACCESS2 port0
    ("R2-CORE",    2, 0, "SW-ACCESS2", 0, 0),
    # SW-ACCESS1 → PC1
    ("SW-ACCESS1", 0, 1, "PC1",        0, 0),
    # SW-ACCESS1 → PC2
    ("SW-ACCESS1", 0, 2, "PC2",        0, 0),
    # SW-ACCESS2 → SRV1
    ("SW-ACCESS2", 0, 1, "SRV1",       0, 0),
    # SW-ACCESS2 → SRV2
    ("SW-ACCESS2", 0, 2, "SRV2",       0, 0),
]

# Management IPs for each node (informational — used by probe + ZTP generator)
DEVICE_MAP = {
    "Mikrotik-1": {"ip": "192.168.1.1", "role": "edge_router",  "os": "routeros", "console_port": 5000},
    "R2-CORE":    {"ip": "10.0.0.2",    "role": "core_router",  "os": "routeros"},
    "PC1":        {"ip": "10.1.0.10",   "role": "host",         "os": "vpcs"},
    "PC2":        {"ip": "10.1.0.11",   "role": "host",         "os": "vpcs"},
    "SRV1":       {"ip": "10.2.0.10",   "role": "server",       "os": "vpcs"},
    "SRV2":       {"ip": "10.2.0.11",   "role": "server",       "os": "vpcs"},
    "SW-ACCESS1": {"ip": "",            "role": "switch",        "os": "switch"},
    "SW-ACCESS2": {"ip": "",            "role": "switch",        "os": "switch"},
    "CLOUD-WAN":  {"ip": "",            "role": "cloud",         "os": "cloud"},
}


def _create_node_from_template(g: GNS3Adapter, pid: str, name: str,
                                template_id: str, x: int, y: int) -> dict:
    """POST /v2/projects/{pid}/templates/{tid} to instantiate a node."""
    path = f"/v2/projects/{pid}/templates/{template_id}"
    resp = g._request("POST", path, body={"x": x, "y": y, "name": name,
                                          "compute_id": "local"})
    return resp


def _link(g: GNS3Adapter, pid: str,
          a_node_id: str, a_adapter: int, a_port: int,
          b_node_id: str, b_adapter: int, b_port: int) -> dict:
    body = {
        "nodes": [
            {"node_id": a_node_id, "adapter_number": a_adapter, "port_number": a_port},
            {"node_id": b_node_id, "adapter_number": b_adapter, "port_number": b_port},
        ]
    }
    return g._request("POST", f"/v2/projects/{pid}/links", body=body)


def build(server: str = "http://localhost:3080",
          project_name: str = "lab-01") -> dict:
    g = GNS3Adapter(server)

    health = g.health()
    if health["status"] != "ok":
        return {"status": "error", "error": f"GNS3 unreachable: {health.get('error')}"}

    # Find project
    projects = g.list_projects()
    project = next((p for p in projects if p["name"] == project_name), None)
    if not project:
        return {"status": "error", "error": f"Project '{project_name}' not found"}

    pid = project["project_id"]

    # Build name→node_id map from existing nodes
    existing = {n["name"]: n for n in g._get_list(f"/v2/projects/{pid}/nodes")}
    name_to_id: dict[str, str] = {n: d["node_id"] for n, d in existing.items()}

    created = []
    skipped = []

    # Create missing nodes
    for name, template_id, x, y, _ in _NODES:
        if name in existing:
            skipped.append(name)
            continue
        resp = _create_node_from_template(g, pid, name, template_id, x, y)
        if resp.get("status") in ("error", "unreachable"):
            return {"status": "error", "error": f"Failed to create {name}: {resp}"}
        nid = resp.get("node_id", "")
        name_to_id[name] = nid
        created.append({"name": name, "node_id": nid[:8]})
        time.sleep(0.2)

    # Build existing links set to avoid duplicates
    existing_links = g._get_list(f"/v2/projects/{pid}/links")
    linked_pairs: set = set()
    for lnk in existing_links:
        nodes = lnk.get("nodes", [])
        if len(nodes) == 2:
            pair = frozenset(
                (n["node_id"], n["adapter_number"], n["port_number"])
                for n in nodes
            )
            linked_pairs.add(pair)

    links_created = []
    links_skipped = []

    for a_name, a_adp, a_prt, b_name, b_adp, b_prt in _LINKS:
        a_id = name_to_id.get(a_name)
        b_id = name_to_id.get(b_name)
        if not a_id or not b_id:
            continue
        pair = frozenset([
            (a_id, a_adp, a_prt),
            (b_id, b_adp, b_prt),
        ])
        if pair in linked_pairs:
            links_skipped.append(f"{a_name}->{b_name}")
            continue
        resp = _link(g, pid, a_id, a_adp, a_prt, b_id, b_adp, b_prt)
        if resp.get("status") in ("error", "unreachable"):
            links_skipped.append(f"{a_name}->{b_name} (error: {resp.get('error','')})")
        else:
            links_created.append(f"{a_name}(a{a_adp})->{b_name}(a{b_adp})")
        time.sleep(0.1)

    # Start new nodes
    for name in [n for n, *_ in _NODES]:
        nid = name_to_id.get(name)
        if nid and name not in [n for n, d in existing.items() if d.get("status") == "started"]:
            g._request("POST", f"/v2/projects/{pid}/nodes/{nid}/start")
            time.sleep(0.3)

    return {
        "status": "success",
        "project": project_name,
        "project_id": pid,
        "nodes_created": created,
        "nodes_skipped": skipped,
        "links_created": links_created,
        "links_skipped": links_skipped,
        "device_map": DEVICE_MAP,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GNS3 Topology Builder — NDC Lab")
    parser.add_argument("--server",  default="http://localhost:3080")
    parser.add_argument("--project", default="lab-01")
    parser.add_argument("--json",    action="store_true")
    args = parser.parse_args()

    result = build(args.server, args.project)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["status"] == "error":
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Project  : {result['project']} ({result['project_id'][:8]})")
        print(f"Created  : {len(result['nodes_created'])} nodes — {[n['name'] for n in result['nodes_created']]}")
        print(f"Skipped  : {result['nodes_skipped']}")
        print(f"Links+   : {result['links_created']}")
        print(f"Links~   : {result['links_skipped']}")


if __name__ == "__main__":
    main()
