"""GNS3 Topology Probe — NDC Workflow Step 1 (GNS3 variant).

Queries the GNS3 REST API, maps live nodes/links to ICDEV NDC topology format,
seeds the ndc_topologies DB table, and generates an Ansible inventory file.

Reads from .env:
  GNS3_SERVER      http://localhost:3080
  GNS3_PROJECT     lab-01          (blank = all projects)
  GNS3_USERNAME    (optional)
  GNS3_PASSWORD    (optional)

Outputs JSON contract:
  {"gate": "PASS", "canvas": "ndc", "artifacts": [...], "topology_id": "..."}
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_CANVAS = "ndc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# GNS3 node_type → NDC topology type
_TYPE_MAP = {
    "qemu":             "router",
    "vpcs":             "host",
    "ethernet_switch":  "switch",
    "ethernet_hub":     "switch",
    "nat":              "gateway",
    "cloud":            "cloud",
    "frame_relay_switch": "switch",
    "atm_switch":       "switch",
}

# Heuristic: node name keywords → role
_ROLE_KEYWORDS = {
    "edge":   "edge_router",
    "wan":    "edge_router",
    "core":   "core_router",
    "dist":   "distribution",
    "access": "access_switch",
    "sw":     "access_switch",
    "switch": "access_switch",
    "srv":    "server",
    "server": "server",
    "pc":     "host",
    "host":   "host",
    "nat":    "nat",
    "cloud":  "cloud",
}

# Known management IPs from gns3_topology_builder.py
_DEVICE_MAP_DEFAULT = {
    "Mikrotik-1": {"ip": "192.168.1.1", "role": "edge_router",  "os": "routeros", "ssh_port": 22},
    "R2-CORE":    {"ip": "10.0.0.2",    "role": "core_router",  "os": "routeros", "ssh_port": 22},
    "PC1":        {"ip": "10.1.0.10",   "role": "host",         "os": "vpcs"},
    "PC2":        {"ip": "10.1.0.11",   "role": "host",         "os": "vpcs"},
    "SRV1":       {"ip": "10.2.0.10",   "role": "server",       "os": "vpcs"},
    "SRV2":       {"ip": "10.2.0.11",   "role": "server",       "os": "vpcs"},
    "SW-ACCESS1": {"ip": "",            "role": "switch",        "os": "switch"},
    "SW-ACCESS2": {"ip": "",            "role": "switch",        "os": "switch"},
    "NAT-GW":     {"ip": "",            "role": "nat",           "os": "nat"},
}


def _load_env() -> dict:
    try:
        from dotenv import dotenv_values
        return dict(dotenv_values(_ROOT / ".env"))
    except Exception:
        return {}


def _infer_role(name: str) -> str:
    nl = name.lower()
    for kw, role in _ROLE_KEYWORDS.items():
        if kw in nl:
            return role
    return "device"


def _probe_gns3(env: dict) -> dict:
    """Return topology dict {project_id, name, nodes, edges}."""
    from tools.network.adapters.gns3_adapter import GNS3Adapter
    server   = env.get("GNS3_SERVER",   "http://localhost:3080")
    project  = env.get("GNS3_PROJECT",  "lab-01")
    username = env.get("GNS3_USERNAME", "")
    password = env.get("GNS3_PASSWORD", "")

    g = GNS3Adapter(server, username=username, password=password)
    h = g.health()
    if h["status"] != "ok":
        raise RuntimeError(f"GNS3 unreachable at {server}: {h.get('error')}")

    projects = g.list_projects()
    if project:
        projects = [p for p in projects if p["name"] == project]
    if not projects:
        raise RuntimeError(f"No project matching '{project}'")

    # Load device map from env JSON or use defaults
    try:
        device_map: dict = json.loads(env.get("GNS3_DEVICE_MAP", "{}"))
    except (json.JSONDecodeError, TypeError):
        device_map = {}
    device_map = {**_DEVICE_MAP_DEFAULT, **device_map}

    all_topologies = []
    for proj in projects:
        pid   = proj["project_id"]
        pname = proj["name"]

        raw_nodes = g._get_list(f"/v2/projects/{pid}/nodes")
        raw_links = g._get_list(f"/v2/projects/{pid}/links")

        nodes = []
        for n in raw_nodes:
            nid    = n.get("node_id", "")
            nname  = n.get("name", nid[:8])
            ntype  = _TYPE_MAP.get(n.get("node_type", ""), "device")
            dev    = device_map.get(nname, {})
            role   = dev.get("role") or _infer_role(nname)
            os_    = dev.get("os", "")
            mgmt_ip = dev.get("ip", "")
            ssh_port = dev.get("ssh_port", 22)
            nodes.append({
                "id":            nid,
                "type":          ntype,
                "label":         nname,
                "role":          role,
                "os":            os_,
                "mgmt_ip":       mgmt_ip,
                "ssh_port":      ssh_port,
                "console":       n.get("console"),
                "console_type":  n.get("console_type", "telnet"),
                "status":        n.get("status", "unknown"),
                "classification": "",
            })

        edges = []
        for lnk in raw_links:
            lnk_nodes = lnk.get("nodes", [])
            if len(lnk_nodes) == 2:
                edges.append({
                    "id":     lnk.get("link_id", ""),
                    "source": lnk_nodes[0]["node_id"],
                    "target": lnk_nodes[1]["node_id"],
                    "type":   "ethernet",
                    "a_adapter": lnk_nodes[0].get("adapter_number", 0),
                    "a_port":    lnk_nodes[0].get("port_number", 0),
                    "b_adapter": lnk_nodes[1].get("adapter_number", 0),
                    "b_port":    lnk_nodes[1].get("port_number", 0),
                })

        all_topologies.append({
            "project_id":  pid,
            "name":        pname,
            "nodes":       nodes,
            "edges":       edges,
            "device_map":  device_map,
        })

    return all_topologies


def _seed_db(topologies: list) -> list[str]:
    """Upsert topology rows into ndc_topologies; return list of IDs."""
    ids = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            ts = datetime.now(timezone.utc).isoformat()
            for topo in topologies:
                tid = str(uuid.uuid5(uuid.NAMESPACE_DNS,
                                     f"gns3:{topo['project_id']}"))
                design = {"nodes": topo["nodes"], "edges": topo["edges"]}
                conn.execute(
                    """INSERT INTO ndc_topologies
                       (id, name, source, project_id, design_json, device_map,
                        ztp_status, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(id) DO UPDATE SET
                         design_json=excluded.design_json,
                         device_map=excluded.device_map,
                         updated_at=excluded.updated_at
                    """,
                    (
                        tid, topo["name"], "gns3", topo["project_id"],
                        json.dumps(design), json.dumps(topo["device_map"]),
                        "pending", ts, ts,
                    ),
                )
                ids.append(tid)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Non-fatal — continue even if DB is unavailable
        print(f"[probe] DB seed warning: {exc}", file=sys.stderr)
    return ids


def _write_ansible_inventory(topologies: list, uid: str) -> Path:
    """Generate Ansible inventory ini for all routers/hosts."""
    lines = ["# Ansible Inventory — Generated by ICDEV GNS3 Topology Probe",
             f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             ""]

    routers = []
    hosts   = []
    for topo in topologies:
        for n in topo["nodes"]:
            ip = n.get("mgmt_ip", "")
            if not ip:
                continue
            os_ = n.get("os", "")
            if os_ == "routeros":
                routers.append((n["label"], ip, n.get("ssh_port", 22)))
            elif os_ == "vpcs":
                hosts.append((n["label"], ip))

    lines.append("[mikrotik_routers]")
    for name, ip, port in routers:
        lines.append(f"{name} ansible_host={ip} ansible_port={port} "
                     f"ansible_user=${{GNS3_SSH_USER:-admin}} "
                     f"ansible_password=${{GNS3_SSH_PASS:-}} "
                     f"ansible_network_os=routeros ansible_connection=network_cli")
    lines.append("")

    lines.append("[vpcs_hosts]")
    for name, ip in hosts:
        lines.append(f"{name} ansible_host={ip}")
    lines.append("")

    lines += [
        "[all:vars]",
        "ansible_python_interpreter=/usr/bin/python3",
        "",
    ]

    inv_path = _ARTIFACTS_DIR / f"ansible_inventory_{uid}.ini"
    inv_path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return inv_path


def _write_topology_report(topologies: list, uid: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# GNS3 Topology Probe Report",
        f"**Generated:** {ts}  ",
        f"**Projects scanned:** {len(topologies)}",
        "",
    ]
    for topo in topologies:
        nodes = topo["nodes"]
        edges = topo["edges"]
        lines += [
            f"## Project: `{topo['name']}`  ({topo['project_id'][:8]})",
            f"**Nodes:** {len(nodes)}  **Links:** {len(edges)}",
            "",
            "### Device Inventory",
            "| Name | Type | Role | OS | Mgmt IP | Status |",
            "|------|------|------|----|---------|--------|",
        ]
        for n in nodes:
            lines.append(
                f"| {n['label']} | {n['type']} | {n['role']} "
                f"| {n['os'] or '—'} | {n['mgmt_ip'] or '—'} | {n['status']} |"
            )
        lines += ["", "### Wiring", "| From | To | Link Type |", "|------|----|-----------|"]
        name_map = {n["id"]: n["label"] for n in nodes}
        for e in edges:
            a = name_map.get(e["source"], e["source"][:8])
            b = name_map.get(e["target"], e["target"][:8])
            lines.append(
                f"| {a} (a{e['a_adapter']}/p{e['a_port']}) "
                f"| {b} (a{e['b_adapter']}/p{e['b_port']}) | ethernet |"
            )
        lines.append("")

    rpt_path = _ARTIFACTS_DIR / f"topology_probe_{uid}.md"
    rpt_path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return rpt_path


def run(run_id: str = "", project_id: str = "default", canvas: str = _CANVAS) -> dict:
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    uid  = uuid.uuid4().hex[:8]
    env  = _load_env()

    topologies = _probe_gns3(env)
    topo_ids   = _seed_db(topologies)

    inv_path = _write_ansible_inventory(topologies, uid)
    rpt_path = _write_topology_report(topologies, uid)

    total_nodes = sum(len(t["nodes"]) for t in topologies)
    total_links = sum(len(t["edges"]) for t in topologies)
    routers = [n for t in topologies for n in t["nodes"] if n["os"] == "routeros"]

    return {
        "gate":         "PASS",
        "canvas":       canvas,
        "topology_ids": topo_ids,
        "projects":     len(topologies),
        "total_nodes":  total_nodes,
        "total_links":  total_links,
        "routers":      [r["label"] for r in routers],
        "artifacts": [
            {"name": "Topology Probe Report",  "path": rpt_path.relative_to(_ROOT).as_posix(), "type": "md"},
            {"name": "Ansible Inventory",       "path": inv_path.relative_to(_ROOT).as_posix(), "type": "ini"},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GNS3 Topology Probe — NDC Step 1")
    parser.add_argument("--run-id",     default="")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--canvas",     default=_CANVAS)
    parser.add_argument("--json",       action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.run_id, args.project_id, args.canvas)
        print(json.dumps(result))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
