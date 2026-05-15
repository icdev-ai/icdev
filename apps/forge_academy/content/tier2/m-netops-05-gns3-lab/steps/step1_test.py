# Auto-grader for NetOps M05 Step 1: GNS3 Topology Pusher

import sys
from io import StringIO

# ── map_node_type tests ───────────────────────────────────────────────────────

assert map_node_type("router")   == "router",           f"router → 'router', got {map_node_type('router')}"
assert map_node_type("switch")   == "ethernet_switch",  f"switch → 'ethernet_switch', got {map_node_type('switch')}"
assert map_node_type("firewall") == "router",           f"firewall → 'router', got {map_node_type('firewall')}"
assert map_node_type("server")   == "docker",           f"server → 'docker', got {map_node_type('server')}"
assert map_node_type("cloud")    == "cloud",            f"cloud → 'cloud', got {map_node_type('cloud')}"
assert map_node_type("host")     == "vpcs",             f"host → 'vpcs', got {map_node_type('host')}"
assert map_node_type("unknown-type") == "vpcs",         "Unknown type must fall back to 'vpcs'"

# ── build_node_payload tests ──────────────────────────────────────────────────

node = {"id": "n1", "type": "router", "label": "Core-RTR", "x": 100, "y": 200}
payload = build_node_payload(node, "proj-uuid-123")

assert payload is not None, "build_node_payload() returned None"
assert payload.get("name")      == "Core-RTR",   f"name should be node label, got {payload.get('name')}"
assert payload.get("node_type") == "router",     f"node_type should be 'router', got {payload.get('node_type')}"
assert payload.get("x")        == 100,           f"x should be 100, got {payload.get('x')}"
assert payload.get("y")        == 200,           f"y should be 200, got {payload.get('y')}"
assert "properties"  in payload,                 "payload must include 'properties' key"
assert payload.get("canvas_id") == "n1",         f"canvas_id should be 'n1', got {payload.get('canvas_id')}"

switch_node = {"id": "n2", "type": "switch", "label": "SW-01", "x": 0, "y": 0}
sw_payload = build_node_payload(switch_node, "proj-uuid-123")
assert sw_payload.get("node_type") == "ethernet_switch", \
    f"switch node_type must be 'ethernet_switch', got {sw_payload.get('node_type')}"

# ── build_link_payload tests ──────────────────────────────────────────────────

id_map = {"n1": "gns3-aaaa", "n2": "gns3-bbbb", "n3": "gns3-cccc"}

edge = {"source": "n1", "target": "n2"}
link = build_link_payload(edge, id_map)
assert link is not None, "build_link_payload() returned None for valid edge"
assert "nodes" in link, "link payload must have 'nodes' key"
assert len(link["nodes"]) == 2, f"link must have 2 endpoints, got {len(link['nodes'])}"

endpoints = link["nodes"]
gns3_ids = {ep["node_id"] for ep in endpoints}
assert "gns3-aaaa" in gns3_ids, "source endpoint gns3-aaaa missing from link"
assert "gns3-bbbb" in gns3_ids, "target endpoint gns3-bbbb missing from link"
for ep in endpoints:
    assert "adapter_number" in ep, "Each endpoint must have 'adapter_number'"
    assert "port_number"    in ep, "Each endpoint must have 'port_number'"

# Missing endpoint → return None
edge_missing = {"source": "n1", "target": "n99"}
assert build_link_payload(edge_missing, id_map) is None, \
    "build_link_payload() must return None when a canvas_id is not in id_map"

# ── GNS3TopologyPusher with stub session ─────────────────────────────────────

_StubSession._node_counter = 0   # reset counter
pusher = GNS3TopologyPusher(
    server_url="http://localhost:3080",
    project_id="test-project-id",
    session=_StubSession(),
)

node_result = pusher.push_nodes(SAMPLE_GRAPH)
assert node_result is not None, "push_nodes() returned None"
assert "id_map"  in node_result, "push_nodes() must return 'id_map'"
assert "pushed"  in node_result, "push_nodes() must return 'pushed'"
assert "errors"  in node_result, "push_nodes() must return 'errors'"
assert node_result["pushed"] == 5, \
    f"SAMPLE_GRAPH has 5 nodes, push_nodes pushed {node_result['pushed']}"
assert len(node_result["id_map"]) == 5, \
    f"id_map must have 5 entries (one per node), got {len(node_result['id_map'])}"
assert node_result["errors"] == [], f"Stub session should produce no errors, got {node_result['errors']}"

link_result = pusher.push_links(SAMPLE_GRAPH, node_result["id_map"])
assert link_result is not None, "push_links() returned None"
assert "linked"  in link_result, "push_links() must return 'linked'"
assert "skipped" in link_result, "push_links() must return 'skipped'"
assert link_result["linked"] == 4, \
    f"SAMPLE_GRAPH has 4 edges, push_links linked {link_result['linked']}"
assert link_result["skipped"] == 0, f"All edges should link with full id_map, skipped={link_result['skipped']}"

# ── push_topology end-to-end test ─────────────────────────────────────────────

_StubSession._node_counter = 0
pusher2 = GNS3TopologyPusher("http://localhost:3080", "proj-e2e", _StubSession())

captured = StringIO()
sys.stdout = captured
try:
    result = pusher2.push_topology(SAMPLE_GRAPH)
finally:
    sys.stdout = sys.__stdout__

assert result is not None, "push_topology() returned None"
assert "status"       in result, "push_topology() must return 'status'"
assert "nodes_pushed" in result, "push_topology() must return 'nodes_pushed'"
assert "links_linked" in result, "push_topology() must return 'links_linked'"
assert "errors"       in result, "push_topology() must return 'errors'"
assert "id_map"       in result, "push_topology() must return 'id_map'"

assert result["status"]       == "ok", f"Expected 'ok' with stub session, got {result['status']}"
assert result["nodes_pushed"] == 5,    f"Expected 5 nodes pushed, got {result['nodes_pushed']}"
assert result["links_linked"] == 4,    f"Expected 4 links linked, got {result['links_linked']}"
assert result["errors"]       == [],   f"Expected no errors, got {result['errors']}"

# ── main block print test ─────────────────────────────────────────────────────

_StubSession._node_counter = 0
captured2 = StringIO()
sys.stdout = captured2
try:
    pusher3 = GNS3TopologyPusher("http://localhost:3080", "demo-project-id", _StubSession())
    main_result = pusher3.push_topology(SAMPLE_GRAPH)
    print(f"Nodes pushed: {main_result['nodes_pushed']}")
    print(f"Links linked: {main_result['links_linked']}")
    print(f"Status: {main_result['status']}")
    print(f"ID map entries: {len(main_result['id_map'])}")
finally:
    sys.stdout = sys.__stdout__

main_out = captured2.getvalue()
assert "Nodes pushed: 5" in main_out, "Print 'Nodes pushed: 5' in your main block"
assert "Links linked: 4" in main_out, "Print 'Links linked: 4' in your main block"
assert "Status: ok"      in main_out, "Print 'Status: ok' in your main block"

print("PASS: GNS3 Topology Pusher complete. map_node_type + build_node_payload + build_link_payload + GNS3TopologyPusher all verified.")
