# CUI // SP-CTI
"""NDC topology builder — reuses live GNS3 projects from NDC discovery.

Priority order:
  1. Reuse existing GNS3 project named 'icdev-ndc-sim' (from a prior sim run)
  2. Parse NDC topology discovery artifact  (topology_discovery.json)
  3. Parse diagram export artifact           (diagram_export.json)
  4. Fall back to canonical 6-node topology  (router/switch/firewall/server/internet/mgmt)
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)

_DEVICE_TYPE_MAP = {
    "router":       "vpcs",
    "switch":       "ethernet_switch",
    "firewall":     "vpcs",
    "hub":          "ethernet_hub",
    "cloud":        "cloud",
    "nat":          "nat",
    "server":       "vpcs",
    "workstation":  "vpcs",
    "access_point": "vpcs",
    "l3-switch":    "ethernet_switch",
    "core-switch":  "ethernet_switch",
    "distribution": "ethernet_switch",
}

# GNS3 node_type → NodeSpec node_type  (for project-reuse path)
_GNS3_TO_SPEC = {
    "qemu":             "vpcs",
    "docker":           "vpcs",
    "vpcs":             "vpcs",
    "cloud":            "cloud",
    "nat":              "nat",
    "dynamips":         "vpcs",
    "iou":              "vpcs",
    "ethernet_hub":     "ethernet_hub",
    "ethernet_switch":  "ethernet_switch",
    "frame_relay_switch":"ethernet_switch",
}


def _try_reuse_gns3_project(gns3_url: str, username: str, password: str, token: str):
    """Return (nodes, links) from an existing 'icdev-ndc-sim' GNS3 project, or None."""
    try:
        from tools.network.adapters.gns3_adapter import GNS3Adapter
        adapter = GNS3Adapter(gns3_url, username=username, password=password, token=token)
        if adapter.health().get("status") != "ok":
            return None
        projects = adapter.list_projects()
        project = next((p for p in projects if p.get("name") == "icdev-ndc-sim"), None)
        if not project:
            return None
        pid = project.get("project_id", "")
        if not pid:
            return None

        # Read nodes + links from existing project
        from tools.databridge.connector import ConnectorRequest
        node_resp = adapter._connector.read(
            ConnectorRequest(table_name="nodes", filters={"project_id": pid}))
        link_resp = adapter._connector.read(
            ConnectorRequest(table_name="links", filters={"project_id": pid}))

        raw_nodes = node_resp.data if node_resp.status == "ok" else []
        raw_links = link_resp.data if link_resp.status == "ok" else []

        nodes = [
            NodeSpec(
                name=n.get("name", f"node-{i}"),
                node_type=_GNS3_TO_SPEC.get(n.get("node_type", "vpcs"), "vpcs"),
                meta={"gns3_node_id": n.get("node_id", ""),
                      "gns3_node_type": n.get("node_type", ""),
                      "role": "reused"},
            )
            for i, n in enumerate(raw_nodes or [])
        ]
        links = []
        for lnk in (raw_links or []):
            endpoints = lnk.get("nodes", [])
            if len(endpoints) >= 2:
                # GNS3 link stores node_id; try to match back to name
                a_id = endpoints[0].get("node_id", "")
                b_id = endpoints[1].get("node_id", "")
                a_name = next((n.name for n in nodes
                                if n.meta.get("gns3_node_id") == a_id), "")
                b_name = next((n.name for n in nodes
                                if n.meta.get("gns3_node_id") == b_id), "")
                if a_name and b_name:
                    links.append(LinkSpec(
                        a_node=a_name, b_node=b_name,
                        a_port=endpoints[0].get("port_number", 0),
                        b_port=endpoints[1].get("port_number", 0),
                    ))

        if nodes:
            return nodes, links
    except Exception:
        pass
    return None


def _parse_discovery_artifact(artifacts_dir: Path):
    """Try to parse NDC topology_discovery.json or diagram_export.json."""
    for fname in ("topology_discovery.json", "diagram_export.json", "topology.json"):
        p = artifacts_dir / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            nodes_raw = data.get("devices") or data.get("nodes") or []
            links_raw = data.get("links") or data.get("edges") or []

            nodes: list[NodeSpec] = []
            for dev in nodes_raw:
                name = dev.get("name") or dev.get("id") or f"node-{len(nodes)}"
                dtype = (dev.get("type") or dev.get("device_type") or "vpcs").lower()
                gns3_type = _DEVICE_TYPE_MAP.get(dtype, "vpcs")
                nodes.append(NodeSpec(
                    name=name, node_type=gns3_type,
                    meta={"role": dtype, "ip": dev.get("ip", ""),
                          "vendor": dev.get("vendor", "")},
                ))
            links: list[LinkSpec] = []
            for lnk in links_raw:
                a = lnk.get("source") or lnk.get("from") or ""
                b = lnk.get("target") or lnk.get("to") or ""
                if a and b:
                    links.append(LinkSpec(
                        a_node=a, b_node=b,
                        a_port=lnk.get("source_port", 0),
                        b_port=lnk.get("target_port", 0),
                    ))
            if nodes:
                return nodes, links
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def _default_topology():
    nodes = [
        NodeSpec(name="core-router",    node_type="vpcs",            meta={"role": "router",   "tier": "core"}),
        NodeSpec(name="dist-switch-1",  node_type="ethernet_switch", meta={"role": "switch",   "tier": "distribution"}),
        NodeSpec(name="dist-switch-2",  node_type="ethernet_switch", meta={"role": "switch",   "tier": "distribution"}),
        NodeSpec(name="fw-perimeter",   node_type="vpcs",            meta={"role": "firewall", "tier": "perimeter"}),
        NodeSpec(name="inet-cloud",     node_type="cloud",           meta={"role": "internet"}),
        NodeSpec(name="srv-segment",    node_type="ethernet_switch", meta={"role": "server-vlan"}),
        NodeSpec(name="mgmt-node",      node_type="vpcs",            meta={"role": "management"}),
    ]
    links = [
        LinkSpec("inet-cloud",    "fw-perimeter",  0, 0),
        LinkSpec("fw-perimeter",  "core-router",   1, 0),
        LinkSpec("core-router",   "dist-switch-1", 1, 0),
        LinkSpec("core-router",   "dist-switch-2", 2, 0),
        LinkSpec("dist-switch-1", "srv-segment",   1, 0),
        LinkSpec("dist-switch-2", "mgmt-node",     1, 0),
    ]
    return nodes, links


class NDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "ndc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        import os
        gns3_url  = os.environ.get("GNS3_URL", "http://localhost:3080")
        username  = os.environ.get("GNS3_USERNAME", "")
        password  = os.environ.get("GNS3_PASSWORD", "")
        token     = os.environ.get("GNS3_TOKEN", "")

        # 1. Reuse live GNS3 project
        result = _try_reuse_gns3_project(gns3_url, username, password, token)
        source = "gns3_reuse"
        if result is None:
            # 2. Parse discovery artifact
            result = _parse_discovery_artifact(artifacts_dir)
            source = "discovery_artifact"
        if result is None:
            # 3. Default topology
            result = _default_topology()
            source = "default"

        nodes, links = result

        probes = [
            ProbeSpec(name="topology_deployed", type="topology_deployed"),
            ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
        ]

        docker_services = [
            DockerServiceSpec(
                name="icdev-netbox-ndc",
                image="lscr.io/linuxserver/netbox:latest",
                ports={8080: 8080},
                env={"SUPERUSER_EMAIL": "admin@icdev.local",
                     "SUPERUSER_PASSWORD": "icdev-netbox"},
                healthcheck_url="http://localhost:8080/",
            ),
        ]
        # Also start LocalStack for any cloud-backed NDC resources
        docker_services.append(DockerServiceSpec(
            name="icdev-floci-ndc",
            image=EMULATOR_IMAGE,
            ports={CANVAS_EMULATOR_HOST_PORTS["ndc"]: EMULATOR_CONTAINER_PORT},
            env={"DEFAULT_REGION": "us-east-1"},
            healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["ndc"]),
        ))

        return TopologySpec(
            project_name="icdev-ndc-sim",
            canvas="ndc",
            nodes=nodes,
            links=links,
            probes=probes,
            docker_services=docker_services,
            metadata={
                "source": source,
                "device_count": len(nodes),
                "link_count": len(links),
                "gns3_url": gns3_url,
            },
        )
