# CUI // SP-CTI
"""MDC topology builder — cloud migration source/target side-by-side simulation.

Artifact parsing order:
  1. migration_waves.yaml / migration_plan.json  — extract waves, source/target systems
  2. source_inventory.json / inventory.json       — enumerate source hosts/services
  3. Default — generic 3-tier src→transit→tgt with dual LocalStack
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)


def _parse_migration_waves(path: Path) -> Optional[dict]:
    """Parse migration_waves.yaml or migration_plan.json."""
    try:
        if path.suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        waves = data.get("waves") or data.get("migration_waves") or []
        source_region = data.get("source_region", "on-prem")
        target_region = data.get("target_region", "us-east-1")
        source_systems = data.get("source_systems") or data.get("source", {})
        return {
            "waves": waves,
            "source_region": source_region,
            "target_region": target_region,
            "source_systems": source_systems,
        }
    except Exception:
        return None


def _parse_inventory(path: Path) -> Optional[list[dict]]:
    """Parse source_inventory.json or inventory.json for source host list."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[:8]  # cap topology size
        if isinstance(data, dict):
            hosts = data.get("hosts") or data.get("servers") or data.get("instances") or []
            return hosts[:8]
    except Exception:
        pass
    return None


def _nodes_from_inventory(hosts: list[dict], env: str) -> list[NodeSpec]:
    nodes = []
    for h in hosts:
        name = h.get("name") or h.get("hostname") or h.get("id") or f"{env}-host"
        role = h.get("role") or h.get("type") or "server"
        node_type = "ethernet_switch" if role in ("switch", "network") else "vpcs"
        nodes.append(NodeSpec(
            name=f"{env}-{name}"[:20],
            node_type=node_type,
            meta={"role": f"{env}-{role}", "env": env, "ip": h.get("ip", "")},
        ))
    return nodes


class MDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "mdc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        plan: Optional[dict] = None
        inventory: Optional[list] = None

        for fname in ("migration_waves.yaml", "migration_waves.yml",
                       "migration_plan.json", "migration_plan.yaml"):
            p = artifacts_dir / fname
            if p.exists():
                plan = _parse_migration_waves(p)
                if plan:
                    break

        for fname in ("source_inventory.json", "inventory.json", "hosts.json"):
            p = artifacts_dir / fname
            if p.exists():
                inventory = _parse_inventory(p)
                if inventory:
                    break

        source_region = (plan or {}).get("source_region", "on-prem")
        target_region = (plan or {}).get("target_region", "us-east-1")
        waves = (plan or {}).get("waves", ["wave-1-db", "wave-2-app", "wave-3-storage"])

        # Build source-side nodes
        if inventory:
            src_nodes = _nodes_from_inventory(inventory, "src")
        else:
            src_nodes = [
                NodeSpec(name="src-db",      node_type="vpcs",  meta={"role": "source-db",      "env": "source", "region": source_region}),
                NodeSpec(name="src-app",     node_type="vpcs",  meta={"role": "source-app",     "env": "source"}),
                NodeSpec(name="src-storage", node_type="cloud", meta={"role": "source-storage", "env": "source"}),
            ]

        nodes = src_nodes + [
            NodeSpec(name="src-fabric",  node_type="ethernet_switch", meta={"role": "source-network", "env": "source"}),
            NodeSpec(name="dms-engine",  node_type="vpcs",            meta={"role": "dms", "env": "transit"}),
            NodeSpec(name="vpn-tunnel",  node_type="vpcs",            meta={"role": "vpn", "env": "transit"}),
            NodeSpec(name="mig-fabric",  node_type="ethernet_switch", meta={"role": "migration-fabric"}),
            NodeSpec(name="tgt-db",      node_type="vpcs",            meta={"role": "target-db",  "env": "target", "region": target_region}),
            NodeSpec(name="tgt-app",     node_type="vpcs",            meta={"role": "target-app", "env": "target"}),
            NodeSpec(name="tgt-storage", node_type="cloud",           meta={"role": "target-storage", "env": "target"}),
            NodeSpec(name="tgt-fabric",  node_type="ethernet_switch", meta={"role": "target-network", "env": "target"}),
        ]

        # Source nodes → src-fabric
        links: list[LinkSpec] = []
        for n in src_nodes:
            links.append(LinkSpec(n.name, "src-fabric"))

        links += [
            LinkSpec("src-fabric", "vpn-tunnel"),
            LinkSpec("vpn-tunnel", "mig-fabric"),
            LinkSpec("dms-engine", "mig-fabric"),
            LinkSpec("mig-fabric", "tgt-fabric"),
            LinkSpec("tgt-db",     "tgt-fabric"),
            LinkSpec("tgt-app",    "tgt-fabric"),
            LinkSpec("tgt-storage","tgt-fabric"),
        ]

        source_meta = "artifact" if (plan or inventory) else "default"

        return TopologySpec(
            project_name="icdev-mdc-sim",
            canvas="mdc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count",        type="link_count", expected_value=len(links)),
                ProbeSpec(name="emulator_apply",  type="emulator_apply"),
                ProbeSpec(name="source_emulator", type="tcp_connect",
                          target_node="dms-engine",
                          port=CANVAS_EMULATOR_HOST_PORTS["mdc-src"]),
                ProbeSpec(name="target_emulator", type="tcp_connect",
                          target_node="dms-engine",
                          port=CANVAS_EMULATOR_HOST_PORTS["mdc-tgt"]),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-floci-mdc-src",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["mdc-src"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": source_region if "aws" in source_region else "us-east-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["mdc-src"]),
                ),
                DockerServiceSpec(
                    name="icdev-floci-mdc-tgt",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["mdc-tgt"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": target_region if target_region != "on-prem" else "us-east-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["mdc-tgt"]),
                ),
            ],
            metadata={
                "waves": waves,
                "source": source_meta,
                "source_region": source_region,
                "target_region": target_region,
                "source_host_count": len(src_nodes),
            },
        )
