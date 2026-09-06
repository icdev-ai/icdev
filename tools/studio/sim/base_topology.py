# CUI // SP-CTI
"""Base dataclasses and builder interface for GNS3 canvas simulation topologies."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.cloud import emulator

# ── The canvas emulator containers ─────────────────────────────────────────
#
# The image and the in-container port come from the ONE seam (flx-seam-01), so
# a canvas topology never respells the pinned tag. See tools/cloud/emulator.py.
EMULATOR_IMAGE = emulator.IMAGE
EMULATOR_CONTAINER_PORT = emulator.CONTAINER_PORT
EMULATOR_HEALTH_PATH = emulator.HEALTH_PATH

# HOST port per canvas emulator container. ONE TABLE, because the thing that
# goes wrong here is a collision and a collision is only visible in one place.
#
# THE RECONCILIATION (flx-sim-01). Every one of these containers already mapped
# its host port to 4566 INSIDE the container, which is floci's port, so the
# in-container side needed nothing. The HOST side did: `bdc`, `ddc` and the
# `idc` AWS profile all bound host 4566 -- each other's port, and the port the
# compose-managed floci binds (emulator.DEFAULT_ENDPOINT). Two of those three
# canvases cannot simulate on one host, and none of them can while the shared
# emulator is up: `docker run -p 4566:4566` fails on an address already in use,
# `_start_docker_service` records a warn finding, and the sim carries on and
# reports a mode it did not reach. They move to 4574/4575/4576.
#
# 4566 IS RESERVED and appears in no entry below -- it belongs to the
# deployment's own emulator, never to a canvas simulation.
# No entry may fall inside emulator.PROXY_PORT_RANGES.
# 4577 / 4588 / 4599 are floci-az / floci-gcp / floci-oci and are left free for
# the sibling cards (flx-az-01, flx-gcp-01, flx-oci-01) -- allocate upward from
# 4576 only after checking that list.
CANVAS_EMULATOR_HOST_PORTS: dict[str, int] = {
    "pdc":     4567,
    "mdc-src": 4568,
    "mdc-tgt": 4569,
    "aimc":    4570,
    "ohc":     4571,
    "idc-oci": 4572,
    "ndc":     4573,
    "bdc":     4574,
    "ddc":     4575,
    "idc-aws": 4576,
}


def emulator_health_url(host_port: int) -> str:
    """Health URL for a canvas emulator container published on ``host_port``."""
    return f"http://localhost:{host_port}{EMULATOR_HEALTH_PATH}"


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
    type: str           # topology_deployed | link_count | tcp_connect | http_get | emulator_apply
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
