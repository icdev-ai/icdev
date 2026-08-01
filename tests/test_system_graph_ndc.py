# CUI // SP-CTI
"""Tests for NDC (Network Design Canvas) registration in the federated system graph.

Covers ndc-brg-02: the NDC contributes a canvas node plus edges (to its DB table
group, the /network route surface, and the Security Design Canvas coupling) via
the same provider path the ``system_graph_get`` MCP tool uses
(``tools.system_graph.graph_builder.build_graph``).
"""

from __future__ import annotations

from tools.system_graph.graph_builder import build_graph


NDC_LABEL = "Network Design Canvas"


def _find_ndc(graph: dict) -> dict | None:
    for n in graph["nodes"]:
        if n.get("label") == NDC_LABEL:
            return n
    return None


def _edges_touching(graph: dict, node_id: str) -> list[dict]:
    return [
        e for e in graph["edges"]
        if e["source"] == node_id or e["target"] == node_id
    ]


def test_ndc_node_present_with_edges():
    """build_graph() surfaces the NDC canvas node with >= 2 edges."""
    # Restrict to the ndc source for a fast, deterministic assertion — the same
    # loader the full ``system_graph_get`` path invokes.
    graph = build_graph(sources=["ndc"])

    ndc = _find_ndc(graph)
    assert ndc is not None, "NDC canvas node missing from federated system graph"
    assert ndc["type"] == "canvas_module"
    assert ndc["source"] == "ndc"

    edges = _edges_touching(graph, ndc["id"])
    assert len(edges) >= 2, f"expected >=2 NDC edges, got {len(edges)}"

    # The three owned couplings (table group, /network route, SDC) must resolve.
    targets = {e["target"] for e in edges if e["source"] == ndc["id"]}
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    target_labels = {labels.get(t, "") for t in targets}
    assert any("tables" in lbl for lbl in target_labels), "no DB-table-group edge"
    assert "/network" in target_labels, "no /network route edge"
    assert "Security Design Canvas" in target_labels, "no SDC coupling edge"


def test_ndc_present_when_canvas_db_unavailable(monkeypatch):
    """Graph build still succeeds (node present, no crash) when the canvas DB raises."""
    def _boom(*_a, **_k):
        raise RuntimeError("network_canvas DB unavailable")

    # The topology count import is lazy inside the loader, so patching the module
    # attribute intercepts the live call.
    monkeypatch.setattr("tools.network.db.init_db.get_connection", _boom)

    graph = build_graph(sources=["ndc"])

    ndc = _find_ndc(graph)
    assert ndc is not None, "NDC node must survive canvas DB outage"
    # Live count is optional; degraded build omits it but keeps the node/edges.
    assert "topology_count" not in ndc["properties"]
    assert len(_edges_touching(graph, ndc["id"])) >= 2


def test_ndc_included_in_full_graph_build():
    """The default full-federation build (no sources arg) includes the NDC node."""
    graph = build_graph()
    assert _find_ndc(graph) is not None
    assert graph["stats"]["source_counts"].get("ndc", 0) >= 1
