# CUI // SP-CTI
"""BDC topology builder — ATO boundary enclave simulation."""
from __future__ import annotations

from pathlib import Path

from tools.studio.sim.base_topology import (
    CANVAS_EMULATOR_HOST_PORTS, EMULATOR_CONTAINER_PORT, EMULATOR_IMAGE,
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec,
    TopologySpec, emulator_health_url,
)


class BDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "bdc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        nodes = [
            NodeSpec(name="internet-gw",   node_type="cloud",           meta={"role": "internet-gateway", "boundary": "external"}),
            NodeSpec(name="boundary-fw",   node_type="vpcs",            meta={"role": "boundary-firewall", "boundary": "perimeter"}),
            NodeSpec(name="enclave-a",     node_type="ethernet_switch", meta={"role": "enclave", "boundary": "il4", "classification": "CUI"}),
            NodeSpec(name="enclave-b",     node_type="ethernet_switch", meta={"role": "enclave", "boundary": "il5", "classification": "CUI//SP-CTI"}),
            NodeSpec(name="pps-proxy",     node_type="vpcs",            meta={"role": "pps", "description": "Protected Processing Server"}),
            NodeSpec(name="isa-connector", node_type="vpcs",            meta={"role": "isa", "description": "ISA/ISCP enforcement point"}),
            NodeSpec(name="audit-node",    node_type="vpcs",            meta={"role": "audit", "description": "NIST AU-9 audit node"}),
        ]
        links = [
            LinkSpec("internet-gw",   "boundary-fw",   0, 0),
            LinkSpec("boundary-fw",   "isa-connector", 1, 0),
            LinkSpec("isa-connector", "enclave-a",     1, 0),
            LinkSpec("isa-connector", "enclave-b",     2, 0),
            LinkSpec("enclave-a",     "pps-proxy",     1, 0),
            LinkSpec("enclave-b",     "pps-proxy",     1, 1),
            LinkSpec("boundary-fw",   "audit-node",    2, 0),
        ]
        return TopologySpec(
            project_name="icdev-bdc-sim",
            canvas="bdc",
            nodes=nodes, links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="isa_enforcement", type="tcp_connect",
                          target_node="isa-connector", port=443),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-floci-bdc",
                    image=EMULATOR_IMAGE,
                    ports={CANVAS_EMULATOR_HOST_PORTS["bdc"]: EMULATOR_CONTAINER_PORT},
                    env={"DEFAULT_REGION": "us-gov-west-1"},
                    healthcheck_url=emulator_health_url(CANVAS_EMULATOR_HOST_PORTS["bdc"]),
                ),
            ],
            metadata={"enclaves": 2, "classification_levels": ["CUI", "CUI//SP-CTI"]},
        )
