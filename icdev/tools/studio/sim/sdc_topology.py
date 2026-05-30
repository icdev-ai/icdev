# CUI // SP-CTI
"""SDC topology builder — security zones with simulated threat actors for attack-path probing."""
from __future__ import annotations

from pathlib import Path

from tools.studio.sim.base_topology import (
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec, TopologySpec,
)


class SDCTopologyBuilder(BaseTopologyBuilder):
    canvas = "sdc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        nodes = [
            NodeSpec(name="internet",        node_type="cloud",            meta={"role": "internet", "zone": "untrusted"}),
            NodeSpec(name="fw-edge",         node_type="vpcs",             meta={"role": "firewall", "zone": "perimeter"}),
            NodeSpec(name="dmz-switch",      node_type="ethernet_switch",  meta={"role": "dmz"}),
            NodeSpec(name="web-server",      node_type="vpcs",             meta={"role": "web", "zone": "dmz"}),
            NodeSpec(name="app-server",      node_type="vpcs",             meta={"role": "application", "zone": "internal"}),
            NodeSpec(name="db-server",       node_type="vpcs",             meta={"role": "database", "zone": "restricted"}),
            NodeSpec(name="internal-switch", node_type="ethernet_switch",  meta={"role": "internal-net"}),
            NodeSpec(name="siem-node",       node_type="vpcs",             meta={"role": "siem", "zone": "mgmt"}),
            NodeSpec(name="threat-actor",    node_type="vpcs",             meta={"role": "attacker", "zone": "untrusted"}),
        ]
        links = [
            LinkSpec("internet",        "fw-edge",         0, 0),
            LinkSpec("threat-actor",    "internet",        0, 1),
            LinkSpec("fw-edge",         "dmz-switch",      1, 0),
            LinkSpec("dmz-switch",      "web-server",      1, 0),
            LinkSpec("web-server",      "internal-switch", 1, 0),
            LinkSpec("internal-switch", "app-server",      1, 0),
            LinkSpec("internal-switch", "db-server",       2, 0),
            LinkSpec("internal-switch", "siem-node",       3, 0),
        ]
        return TopologySpec(
            project_name="icdev-sdc-sim",
            canvas="sdc",
            nodes=nodes, links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="siem_reachable", type="tcp_connect",
                          target_node="siem-node", port=514),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-wazuh-sdc",
                    image="wazuh/wazuh-manager:4.7.0",
                    ports={1514: 1514, 55000: 55000},
                    healthcheck_url="http://localhost:55000/",
                ),
            ],
            metadata={"zones": ["untrusted", "dmz", "internal", "restricted", "mgmt"]},
        )
