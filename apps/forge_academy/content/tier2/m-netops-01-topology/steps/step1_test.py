# Auto-grader for NetOps M01 Step 1: Network Topology Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = TopologyAgent()
    report = agent.run(SAMPLE_CONFIGS)
    print(f"Nodes: {report['node_count']}")
    print(f"Edges: {report['edge_count']}")
    print(f"Unknown: {report['unknown_nodes']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# parse_device_config tests
dev = parse_device_config(SAMPLE_CONFIGS[0])
assert dev is not None, "parse_device_config() returned None"
assert dev.get("hostname") == "core-sw-01", f"Expected 'core-sw-01', got {dev.get('hostname')}"
assert len(dev.get("interfaces", [])) == 2, f"Expected 2 interfaces, got {len(dev.get('interfaces', []))}"
iface = dev["interfaces"][0]
assert iface.get("ip") == "10.0.1.1", f"Expected ip '10.0.1.1', got {iface.get('ip')}"
assert iface.get("subnet") == "255.255.255.0", f"Expected subnet, got {iface.get('subnet')}"
assert "edge-rtr-01" in dev.get("neighbors", []), "core-sw-01 must list edge-rtr-01 as neighbor"
assert "access-sw-01" in dev.get("neighbors", []), "core-sw-01 must list access-sw-01 as neighbor"

dev2 = parse_device_config(SAMPLE_CONFIGS[1])
assert dev2.get("hostname") == "access-sw-01", f"Expected 'access-sw-01', got {dev2.get('hostname')}"
assert len(dev2.get("neighbors", [])) == 1, "access-sw-01 has 1 neighbor"

# build_topology tests
devices = [parse_device_config(c) for c in SAMPLE_CONFIGS]
topo = build_topology(devices)
assert topo is not None, "build_topology() returned None"
assert "nodes" in topo, "topology must have 'nodes'"
assert "edges" in topo, "topology must have 'edges'"
assert len(topo["nodes"]) == 3, f"Expected 3 nodes (3 device hostnames), got {len(topo['nodes'])}"

# edges: core<->access + core<->edge-rtr (dmz-fw also references edge-rtr -> same edge)
edge_pairs = {frozenset([e["source"], e["target"]]) for e in topo["edges"]}
assert frozenset(["core-sw-01", "access-sw-01"]) in edge_pairs, "Missing core-sw-01 <-> access-sw-01 edge"
assert frozenset(["core-sw-01", "edge-rtr-01"]) in edge_pairs, "Missing core-sw-01 <-> edge-rtr-01 edge"

# TopologyAgent.run tests
assert report is not None, "TopologyAgent.run() returned None"
assert "node_count" in report, "Report must include node_count"
assert "edge_count" in report, "Report must include edge_count"
assert "topology" in report, "Report must include topology"
assert "unknown_nodes" in report, "Report must include unknown_nodes"

assert report["node_count"] == 3, f"Expected node_count=3, got {report['node_count']}"
assert "edge-rtr-01" in report["unknown_nodes"], \
    "edge-rtr-01 appears as neighbor but has no config — must be in unknown_nodes"

assert len(output) > 10, "Print report fields in __main__ block"

print("PASS: Network Topology Agent complete. parse_device_config + build_topology + TopologyAgent.run all verified.")
