
"""NetOps M01 — Network Topology Agent.
Goal: Parse raw device configs, build adjacency graph, flag unknown nodes.
"""


SAMPLE_CONFIGS = [
    """\
hostname core-sw-01
!
interface GigabitEthernet0/1
 ip address 10.0.1.1 255.255.255.0
!
interface GigabitEthernet0/2
 ip address 10.0.2.1 255.255.255.252
!
cdp neighbor
 neighbor: edge-rtr-01 port: Gi0/2
 neighbor: access-sw-01 port: Gi0/1""",

    """\
hostname access-sw-01
!
interface GigabitEthernet0/0
 ip address 10.0.1.2 255.255.255.0
!
cdp neighbor
 neighbor: core-sw-01 port: Gi0/0""",

    """\
hostname dmz-fw-01
!
interface Ethernet1
 ip address 192.168.0.1 255.255.255.128
!
cdp neighbor
 neighbor: edge-rtr-01 port: Eth1""",
]


def parse_device_config(config_str: str) -> dict:
    """Parse a single device config string into a structured dict.

    Read each line. When you see 'hostname <name>', record the hostname.
    When you see 'interface <name>', start a new interface block; add it
    to the list when the block ends or a new block begins.
    Within an interface block, 'ip address <ip> <mask>' gives ip and subnet.
    Under the 'cdp neighbor' section, lines with 'neighbor: <name>' give neighbors.

    Return format:
        {
            "hostname": str,
            "interfaces": [{"name": str, "ip": str, "subnet": str}, ...],
            "neighbors": [str, ...]   # list of neighbor hostnames
        }
    """
    # YOUR CODE HERE
    pass


def build_topology(devices: list) -> dict:
    """Build an undirected topology graph from a list of parsed device dicts.

    Collect all device hostnames as nodes. Walk each device's 'neighbors'
    list to produce edges. Deduplicate bidirectional links: store one edge
    with source = alphabetically-first hostname, target = the other.

    Return format:
        {
            "nodes": [str, ...],
            "edges": [{"source": str, "target": str}, ...]
        }
    """
    # YOUR CODE HERE
    pass


class TopologyAgent:
    def run(self, raw_configs: list) -> dict:
        """Parse all configs, build topology, identify unknown nodes.

        Unknown nodes: hostnames that appear as a neighbor in at least
        one device but have no config of their own in raw_configs.

        Return format:
            {
                "node_count": int,
                "edge_count": int,
                "topology": dict,           # from build_topology
                "unknown_nodes": [str, ...]
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = TopologyAgent()
    report = agent.run(SAMPLE_CONFIGS)
    print(f"Nodes: {report['node_count']}")
    print(f"Edges: {report['edge_count']}")
    print(f"Unknown: {report['unknown_nodes']}")
