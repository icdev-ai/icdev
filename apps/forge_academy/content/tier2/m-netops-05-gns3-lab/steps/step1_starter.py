"""NetOps M05 — GNS3 Topology Pusher.

Goal: Build an agent that translates a canvas node graph into GNS3 REST API
payloads, then pushes each node and link to a live GNS3 project.

You won't need a running GNS3 server to pass the auto-grader — the core
logic (type mapping, payload building, link construction) is tested
independently of the HTTP layer.
"""

# ── Node type mapping: canvas type → GNS3 node type string ───────────────────
# GNS3 supports: "vpcs", "router", "switch", "ethernet_switch",
#                "cloud", "nat", "docker", "qemu", "iou"

NODE_TYPE_MAP = {
    # TODO: fill in these mappings
    "router":   "???",   # hint: a VPCS works for testing; real labs use "router"
    "switch":   "???",   # hint: "ethernet_switch"
    "firewall": "???",   # hint: treat as a "router" node type
    "server":   "???",   # hint: Docker container → "docker"
    "cloud":    "???",   # hint: GNS3 "cloud" node type
    "host":     "???",   # hint: VPCS is the lightweight host emulator
}

# ── Sample graph (used by grader — do not modify) ─────────────────────────────
SAMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "router",   "label": "Core-RTR",    "x": 100, "y": 200},
        {"id": "n2", "type": "switch",   "label": "Access-SW",   "x": 300, "y": 200},
        {"id": "n3", "type": "firewall", "label": "Edge-FW",     "x": 500, "y": 200},
        {"id": "n4", "type": "server",   "label": "App-Server",  "x": 300, "y": 400},
        {"id": "n5", "type": "host",     "label": "Workstation", "x": 100, "y": 400},
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n2", "target": "n4"},
        {"source": "n1", "target": "n5"},
    ],
}


def map_node_type(canvas_type: str) -> str:
    """Return the GNS3 node type string for a given canvas node type.

    Unknown types should fall back to "vpcs" (safe default).
    """
    # TODO: implement using NODE_TYPE_MAP
    pass


def build_node_payload(node: dict, project_id: str) -> dict:
    """Build a GNS3 REST API node-creation payload dict.

    GNS3 POST /v2/projects/{project_id}/nodes expects:
        {
            "name":      <node label>,
            "node_type": <gns3 type string>,
            "x":         <int>,
            "y":         <int>,
            "properties": {}
        }

    Args:
        node:       Canvas node dict with keys: id, type, label, x, y.
        project_id: GNS3 project UUID (embed in the dict for caller convenience).

    Returns a dict matching the schema above.  Also include "canvas_id" (the
    node's "id" field) so the caller can track canvas_id → gns3_id mapping.
    """
    # TODO: implement
    pass


def build_link_payload(edge: dict, node_id_map: dict) -> dict | None:
    """Build a GNS3 link-creation payload from a canvas edge.

    GNS3 POST /v2/projects/{project_id}/links expects:
        {
            "nodes": [
                {"node_id": <gns3_id_A>, "adapter_number": 0, "port_number": 0},
                {"node_id": <gns3_id_B>, "adapter_number": 0, "port_number": 0},
            ]
        }

    Args:
        edge:        Canvas edge with "source" and "target" canvas node IDs.
        node_id_map: Dict mapping canvas_id → gns3_node_id (from a prior push).

    Returns the payload dict, or None if either endpoint is missing from the map.
    """
    # TODO: implement
    pass


class GNS3TopologyPusher:
    """Push a canvas graph to a GNS3 project via REST API.

    The pusher is split into two phases:
      1. push_nodes(graph)  — POST each node; return canvas_id → gns3_id map
      2. push_links(graph, id_map) — POST each link using the id map
      3. push_topology(graph) — orchestrate both phases

    The HTTP session is injected so you can pass a real requests.Session or a
    stub during testing.
    """

    def __init__(self, server_url: str, project_id: str, session=None):
        """
        Args:
            server_url:  GNS3 base URL, e.g. "http://localhost:3080"
            project_id:  GNS3 project UUID
            session:     requests.Session (or compatible stub). If None, create one.
        """
        # TODO: store server_url, project_id, session
        pass

    def push_nodes(self, graph: dict) -> dict:
        """POST each graph node to GNS3; return mapping of canvas_id → gns3_node_id.

        For each node in graph["nodes"]:
          - Build the payload with build_node_payload()
          - POST to {server_url}/v2/projects/{project_id}/nodes
          - Extract "node_id" from the JSON response
          - Add canvas_id → gns3_node_id to result dict

        On HTTP error, skip the node and record the label in an "errors" list.

        Returns:
            {
                "id_map":  {"canvas_id": "gns3_node_id", ...},
                "pushed":  <int>,
                "errors":  [str, ...],
            }
        """
        # TODO: implement
        pass

    def push_links(self, graph: dict, id_map: dict) -> dict:
        """POST each edge as a GNS3 link using the id_map.

        Returns:
            {
                "linked":  <int>,
                "skipped": <int>,
                "errors":  [str, ...],
            }
        """
        # TODO: implement
        pass

    def push_topology(self, graph: dict) -> dict:
        """Orchestrate push_nodes + push_links; return combined summary.

        Returns:
            {
                "status":       "ok" | "partial" | "error",
                "nodes_pushed": <int>,
                "links_linked": <int>,
                "errors":       [str, ...],
                "id_map":       {canvas_id: gns3_node_id, ...},
            }
        """
        # TODO: implement
        pass


# ── Stub session for local testing (do not modify) ───────────────────────────

class _StubSession:
    """Minimal requests.Session stub that returns canned GNS3 responses."""
    _node_counter = 0

    def post(self, url, json=None, **kwargs):
        self.__class__._node_counter += 1
        # Simulate GNS3 returning a node_id on POST /nodes
        if "/nodes" in url and "/links" not in url:
            return _StubResp({"node_id": f"gns3-{self.__class__._node_counter:04d}",
                              "name": (json or {}).get("name", "unknown")})
        # Simulate GNS3 returning a link_id on POST /links
        return _StubResp({"link_id": f"lnk-{self.__class__._node_counter:04d}"})


class _StubResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 201

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


# ── Entry point (used in print tests) ────────────────────────────────────────

if __name__ == "__main__":
    pusher = GNS3TopologyPusher(
        server_url="http://localhost:3080",
        project_id="demo-project-id",
        session=_StubSession(),
    )
    result = pusher.push_topology(SAMPLE_GRAPH)
    print(f"Nodes pushed: {result['nodes_pushed']}")
    print(f"Links linked: {result['links_linked']}")
    print(f"Status: {result['status']}")
    print(f"ID map entries: {len(result['id_map'])}")
