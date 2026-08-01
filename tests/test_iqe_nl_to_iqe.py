# CUI // SP-CTI
"""Unit tests for NL-to-IQE: tools/network/nl_query.py

Tests cover:
  - classify_query()           — intent routing
  - TopologyGraphAdapter       — graph construction and look-ups
  - _inventory_query()         — type-filtered and summary inventory
  - _path_query()              — BFS path finding
  - _failure_query()           — blast-radius / articulation detection
  - _neighbor_query()          — adjacency look-up
  - answer_query()             — end-to-end with in-memory SQLite
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools.network.nl_query import (
    TopologyGraphAdapter,
    _failure_query,
    _inventory_query,
    _neighbor_query,
    _path_query,
    answer_query,
    classify_query,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Minimal JointJS graph: SW-Core <-> RTR-Edge <-> SW-Access
_GRAPH_JSON = {
    "cells": [
        {
            "id": "sw-1",
            "type": "standard.Rectangle",
            "deviceType": "switch",
            "attrs": {"label": {"text": "SW-Core"}},
        },
        {
            "id": "sw-2",
            "type": "standard.Rectangle",
            "deviceType": "switch",
            "attrs": {"label": {"text": "SW-Access"}},
        },
        {
            "id": "rtr-1",
            "type": "standard.Rectangle",
            "deviceType": "router",
            "attrs": {"label": {"text": "RTR-Edge"}},
        },
        {
            "id": "fw-1",
            "type": "standard.Rectangle",
            "deviceType": "firewall",
            "attrs": {"label": {"text": "FW-Perimeter"}},
        },
        # Links
        {
            "id": "link-1",
            "type": "standard.Link",
            "source": {"id": "sw-1"},
            "target": {"id": "rtr-1"},
        },
        {
            "id": "link-2",
            "type": "standard.Link",
            "source": {"id": "rtr-1"},
            "target": {"id": "sw-2"},
        },
        {
            "id": "link-3",
            "type": "standard.Link",
            "source": {"id": "rtr-1"},
            "target": {"id": "fw-1"},
        },
    ]
}


@pytest.fixture()
def graph() -> TopologyGraphAdapter:
    return TopologyGraphAdapter(_GRAPH_JSON)


@pytest.fixture()
def mem_db():
    """In-memory SQLite DB with minimal network canvas schema.

    The raw sqlite3 connection is set up directly (with ``?`` placeholders), then
    wrapped in ``StorageConnection(raw, "sqlite")`` so the PG-native ``%s``
    placeholders issued by ``nl_query.answer_query`` are translated to SQLite
    ``?`` exactly as they are in production (where the route passes a
    StorageConnection from ``get_connection()``). Passing a raw sqlite3 connection
    here previously raised ``sqlite3.OperationalError: near "%": syntax error``.
    """
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY,
            name TEXT,
            graph_json TEXT,
            created_at TEXT
        );
        CREATE TABLE nc_query_log (
            id TEXT PRIMARY KEY,
            topology_id TEXT,
            question TEXT,
            intent TEXT,
            answer TEXT,
            engine TEXT,
            ts TEXT
        );
        """
    )
    raw.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (?,?,?)",
        ("topo-1", "Test Topology", json.dumps(_GRAPH_JSON)),
    )
    raw.commit()
    return StorageConnection(raw, "sqlite")


# ---------------------------------------------------------------------------
# classify_query
# ---------------------------------------------------------------------------


def test_classify_inventory_show_all_switches() -> None:
    assert classify_query("show all switches") == "inventory"


def test_classify_inventory_count() -> None:
    assert classify_query("how many routers are in this topology?") == "inventory"


def test_classify_path() -> None:
    assert classify_query("what is the path from RTR-Edge to SW-Access?") == "path"


def test_classify_failure() -> None:
    assert classify_query("what happens if RTR-Edge goes down?") == "failure"


def test_classify_neighbor() -> None:
    assert classify_query("what devices are directly connected to SW-Core?") == "neighbor"


def test_classify_compliance() -> None:
    # avoid triggering inventory first (classify_query checks inventory before compliance)
    assert classify_query("what CAT1 STIG violations are open?") == "compliance"


def test_classify_general_falls_through() -> None:
    result = classify_query("explain the design philosophy of this network")
    assert result == "general"


# ---------------------------------------------------------------------------
# TopologyGraphAdapter
# ---------------------------------------------------------------------------


def test_graph_node_count(graph: TopologyGraphAdapter) -> None:
    assert graph.node_count == 4


def test_graph_edge_count(graph: TopologyGraphAdapter) -> None:
    assert graph.edge_count == 3


def test_nodes_by_type_switch(graph: TopologyGraphAdapter) -> None:
    switches = graph.nodes_by_type("switch")
    assert len(switches) == 2


def test_nodes_by_type_router(graph: TopologyGraphAdapter) -> None:
    routers = graph.nodes_by_type("router")
    assert len(routers) == 1


def test_find_node_by_name_exact(graph: TopologyGraphAdapter) -> None:
    hits = graph.find_node_by_name("SW-Core")
    assert len(hits) == 1
    assert graph.label_of(hits[0]) == "SW-Core"


def test_find_node_by_name_partial(graph: TopologyGraphAdapter) -> None:
    hits = graph.find_node_by_name("sw-")
    assert len(hits) >= 2


def test_neighbors_of_router_has_three(graph: TopologyGraphAdapter) -> None:
    # RTR-Edge is linked to SW-Core, SW-Access, FW-Perimeter
    rtr_ids = graph.find_node_by_name("RTR-Edge")
    assert rtr_ids
    neighbors = graph.neighbors_of(rtr_ids[0])
    assert len(neighbors) == 3


def test_label_of_returns_correct_label(graph: TopologyGraphAdapter) -> None:
    sw_ids = graph.find_node_by_name("SW-Core")
    assert graph.label_of(sw_ids[0]) == "SW-Core"


def test_type_of_returns_device_type(graph: TopologyGraphAdapter) -> None:
    rtr_ids = graph.find_node_by_name("RTR-Edge")
    assert graph.type_of(rtr_ids[0]) == "router"


def test_compact_text_contains_nodes(graph: TopologyGraphAdapter) -> None:
    text = graph.compact_text()
    assert "RTR-Edge" in text
    assert "SW-Core" in text


def test_empty_graph_has_zero_nodes() -> None:
    g = TopologyGraphAdapter({"cells": []})
    assert g.node_count == 0
    assert g.edge_count == 0


# ---------------------------------------------------------------------------
# _inventory_query
# ---------------------------------------------------------------------------


def test_inventory_query_by_type_switch(graph: TopologyGraphAdapter) -> None:
    result = _inventory_query("show all switches", graph)
    assert result["engine"] == "inventory"
    assert result["data"]["count"] == 2
    assert result["data"]["type"] == "switch"
    assert "SW-Core" in result["data"]["devices"] or "SW-Access" in result["data"]["devices"]


def test_inventory_query_by_type_router(graph: TopologyGraphAdapter) -> None:
    result = _inventory_query("list all routers", graph)
    assert result["data"]["count"] == 1
    assert "RTR-Edge" in result["data"]["devices"]


def test_inventory_query_summary_no_type(graph: TopologyGraphAdapter) -> None:
    result = _inventory_query("show inventory summary", graph)
    assert "total_nodes" in result["data"]
    assert result["data"]["total_nodes"] == 4


def test_inventory_query_answer_contains_count(graph: TopologyGraphAdapter) -> None:
    result = _inventory_query("show all switches", graph)
    assert "2" in result["answer"]


# ---------------------------------------------------------------------------
# _path_query
# ---------------------------------------------------------------------------


def test_path_query_finds_path(graph: TopologyGraphAdapter) -> None:
    result = _path_query("path from SW-Core to SW-Access", graph)
    assert result["engine"] == "path"
    assert result["data"]["path_count"] >= 1
    assert "SW-Core" in result["data"]["source"]
    assert "SW-Access" in result["data"]["target"]


def test_path_query_shortest_path_length(graph: TopologyGraphAdapter) -> None:
    result = _path_query("route from SW-Core to SW-Access", graph)
    # SW-Core → RTR-Edge → SW-Access = 2 hops
    assert result["data"]["shortest_hops"] == 2


def test_path_query_no_devices_returns_error(graph: TopologyGraphAdapter) -> None:
    result = _path_query("what is the path?", graph)
    assert "Could not identify" in result["answer"]
    assert result["data"] == []


# ---------------------------------------------------------------------------
# _failure_query
# ---------------------------------------------------------------------------


def test_failure_query_router_is_articulation(graph: TopologyGraphAdapter) -> None:
    # RTR-Edge connects all devices — it's an articulation point
    result = _failure_query("what if RTR-Edge goes down?", graph)
    assert result["engine"] == "failure"
    assert result["data"]["is_critical"] is True
    assert "RTR-Edge" in result["data"]["failed_device"]


def test_failure_query_leaf_not_critical(graph: TopologyGraphAdapter) -> None:
    # SW-Core is a leaf — removing it does NOT partition the graph
    result = _failure_query("what if SW-Core fails?", graph)
    assert result["data"]["is_critical"] is False


def test_failure_query_no_device_returns_error(graph: TopologyGraphAdapter) -> None:
    result = _failure_query("what happens if something fails?", graph)
    assert "Could not identify" in result["answer"]


# ---------------------------------------------------------------------------
# _neighbor_query
# ---------------------------------------------------------------------------


def test_neighbor_query_router(graph: TopologyGraphAdapter) -> None:
    result = _neighbor_query("what is connected to RTR-Edge?", graph)
    assert result["engine"] == "neighbor"
    assert result["data"]["count"] == 3


def test_neighbor_query_switch(graph: TopologyGraphAdapter) -> None:
    result = _neighbor_query("what is linked to SW-Core?", graph)
    assert result["data"]["count"] == 1
    assert "RTR-Edge" in result["data"]["neighbors"]


def test_neighbor_query_unknown_device(graph: TopologyGraphAdapter) -> None:
    result = _neighbor_query("what is adjacent to?", graph)
    assert "Could not identify" in result["answer"]


# ---------------------------------------------------------------------------
# answer_query (end-to-end)
# ---------------------------------------------------------------------------


def test_answer_query_inventory_intent(mem_db: sqlite3.Connection) -> None:
    result = answer_query("topo-1", "show all switches", mem_db)
    assert result["intent"] == "inventory"
    assert result["engine"] == "inventory"
    assert "query_id" in result
    assert result["query_id"] != ""


def test_answer_query_path_intent(mem_db: sqlite3.Connection) -> None:
    result = answer_query("topo-1", "path from SW-Core to SW-Access", mem_db)
    assert result["intent"] == "path"


def test_answer_query_missing_topology(mem_db: sqlite3.Connection) -> None:
    result = answer_query("nonexistent-id", "show all switches", mem_db)
    assert "not found" in result["answer"].lower()


def test_answer_query_empty_question(mem_db: sqlite3.Connection) -> None:
    result = answer_query("topo-1", "", mem_db)
    assert "Please enter" in result["answer"]


def test_answer_query_persists_query_log(mem_db: sqlite3.Connection) -> None:
    answer_query("topo-1", "show all switches", mem_db)
    row = mem_db.execute("SELECT * FROM nc_query_log LIMIT 1").fetchone()
    assert row is not None
    assert row["intent"] == "inventory"


def test_answer_query_returns_topology_metadata(mem_db: sqlite3.Connection) -> None:
    result = answer_query("topo-1", "show all switches", mem_db)
    assert result["topology_name"] == "Test Topology"
    assert result["node_count"] == 4
    assert result["edge_count"] == 3
