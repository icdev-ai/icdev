# CUI // SP-CTI
"""Base dataclasses and builder interface for GNS3 canvas simulation topologies."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NodeSpec:
    name: str
    node_type: str  # vpcs | docker | cloud | nat | ethernet_switch | ethernet_hub
    compute_id: str = "local"
    properties: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)  # canvas label, resource type, etc.


@dataclass
class LinkSpec:
    a_node: str  # node *name* — resolved to node_id after creation
    b_node: str
    a_port: int = 0
    b_port: int = 0


@dataclass
class ProbeSpec:
    name: str
    type: str           # topology_deployed | link_count | tcp_connect | http_get | localstack_apply
    target_node: str = ""
    port: int = 0
    path: str = "/"
    expected_value: Any = None


@dataclass
class DockerServiceSpec:
    """Optional Docker container to spin up alongside the GNS3 topology."""
    name: str           # unique container name
    image: str          # e.g. "postgres:15-alpine"
    ports: dict[int, int] = field(default_factory=dict)   # {host_port: container_port}
    env: dict[str, str] = field(default_factory=dict)
    healthcheck_url: str = ""   # HTTP URL to poll for readiness


@dataclass
class TopologySpec:
    project_name: str
    canvas: str
    nodes: list[NodeSpec] = field(default_factory=list)
    links: list[LinkSpec] = field(default_factory=list)
    probes: list[ProbeSpec] = field(default_factory=list)
    docker_services: list[DockerServiceSpec] = field(default_factory=list)
    teardown_after: bool = True
    snapshot_before_teardown: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTopologyBuilder:
    canvas = "generic"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        """Build a TopologySpec from canvas design artifacts.

        Subclasses override this; the default returns a two-node stub so the
        executor always has something to deploy even for unknown canvases.
        """
        return TopologySpec(
            project_name=f"icdev-{self.canvas}-sim",
            canvas=self.canvas,
            nodes=[
                NodeSpec(name="node-a", node_type="vpcs", meta={"role": "primary"}),
                NodeSpec(name="node-b", node_type="vpcs", meta={"role": "secondary"}),
            ],
            links=[LinkSpec(a_node="node-a", b_node="node-b")],
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=1),
            ],
        )
