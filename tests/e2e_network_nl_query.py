"""E2E tests for Network Canvas Natural Language Query engine.

Tests the deterministic engines (path, failure, neighbor, inventory) via the
Python API — no running server or Ollama required for CI.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.network.nl_query import (
    TopologyGraphAdapter,
    classify_query,
    answer_query,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_GRAPH = {
    "cells": [
        {
            "id": "r1",
            "type": "network.Router",
            "deviceType": "Router",
            "label": "Core-Router",
            "position": {"x": 100, "y": 100},
        },
        {
            "id": "sw1",
            "type": "network.Switch",
            "deviceType": "Switch",
            "label": "Core-Switch",
            "position": {"x": 300, "y": 100},
        },
        {
            "id": "fw1",
            "type": "network.Firewall",
            "deviceType": "Firewall",
            "label": "Edge-Firewall",
            "position": {"x": 500, "y": 100},
        },
        {
            "id": "srv1",
            "type": "network.Server",
            "deviceType": "Server",
            "label": "Web-Server",
            "position": {"x": 700, "y": 100},
        },
        {
            "id": "srv2",
            "type": "network.Server",
            "deviceType": "Server",
            "label": "DB-Server",
            "position": {"x": 700, "y": 300},
        },
        # Isolated node — not connected to rest
        {
            "id": "iso1",
            "type": "network.Workstation",
            "deviceType": "Workstation",
            "label": "Isolated-WS",
            "position": {"x": 900, "y": 500},
        },
        # Links
        {
            "id": "e1",
            "type": "network.Link",
            "source": {"id": "r1"},
            "target": {"id": "sw1"},
            "protocol": "OSPF",
            "bandwidth": "10G",
        },
        {
            "id": "e2",
            "type": "network.Link",
            "source": {"id": "sw1"},
            "target": {"id": "fw1"},
            "protocol": "VLAN",
            "bandwidth": "1G",
        },
        {
            "id": "e3",
            "type": "network.Link",
            "source": {"id": "fw1"},
            "target": {"id": "srv1"},
            "protocol": "HTTPS",
            "bandwidth": "1G",
        },
        {
            "id": "e4",
            "type": "network.Link",
            "source": {"id": "sw1"},
            "target": {"id": "srv2"},
            "protocol": "SQL",
            "bandwidth": "10G",
        },
    ]
}

_TOPO_ID = "topo-test-" + str(uuid.uuid4())[:8]


@pytest.fixture
def graph():
    return TopologyGraphAdapter(_SAMPLE_GRAPH)


@pytest.fixture
def db_conn(tmp_path):
    """In-memory SQLite with minimal nc_query_log schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY,
            name TEXT,
            graph_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nc_query_log (
            id TEXT PRIMARY KEY,
            topology_id TEXT,
            question TEXT,
            intent TEXT,
            answer TEXT,
            engine TEXT,
            ts TEXT
        )
    """)
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (?,?,?)",
        (_TOPO_ID, "Test Topology", json.dumps(_SAMPLE_GRAPH)),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# TopologyGraphAdapter tests
# ---------------------------------------------------------------------------


class TestTopologyGraphAdapter:
    def test_node_count(self, graph):
        # 6 nodes (including isolated workstation), links excluded
        assert graph.node_count == 6

    def test_edge_count(self, graph):
        assert graph.edge_count == 4

    def test_find_node_by_name_exact(self, graph):
        hits = graph.find_node_by_name("Core-Router")
        assert "r1" in hits

    def test_find_node_by_name_partial(self, graph):
        hits = graph.find_node_by_name("Server")
        node_ids = set(hits)
        assert "srv1" in node_ids or "srv2" in node_ids

    def test_nodes_by_type_router(self, graph):
        routers = graph.nodes_by_type("router")
        assert "r1" in routers

    def test_nodes_by_type_firewall(self, graph):
        fws = graph.nodes_by_type("firewall")
        assert "fw1" in fws

    def test_neighbors_of(self, graph):
        neighbors = graph.neighbors_of("r1")
        assert "sw1" in neighbors

    def test_label_of(self, graph):
        assert graph.label_of("r1") == "Core-Router"

    def test_compact_text(self, graph):
        text = graph.compact_text()
        assert "Core-Router" in text
        assert "LINK" in text

    def test_isolated_node_no_edges(self, graph):
        # iso1 should be present as node but have no neighbors
        assert graph.neighbors_of("iso1") == []


# ---------------------------------------------------------------------------
# QueryClassifier tests
# ---------------------------------------------------------------------------


class TestQueryClassifier:
    def test_classify_path(self):
        assert classify_query("Show all paths between Router-A and Server-B") == "path"

    def test_classify_path_route(self):
        assert classify_query("What route does traffic take from Core-Router to Web-Server?") == "path"

    def test_classify_failure(self):
        assert classify_query("What happens if Switch-3 goes down?") == "failure"

    def test_classify_failure_impact(self):
        assert classify_query("What is the blast radius if Edge-Firewall fails?") == "failure"

    def test_classify_neighbor(self):
        assert classify_query("What is directly connected to Core-Router?") == "neighbor"

    def test_classify_inventory_count(self):
        assert classify_query("How many firewalls are in the topology?") == "inventory"

    def test_classify_inventory_list(self):
        assert classify_query("List all servers") == "inventory"

    def test_classify_compliance(self):
        assert classify_query("Are there any CAT1 STIG findings?") == "compliance"

    def test_classify_general(self):
        assert classify_query("Explain the topology design") == "general"


# ---------------------------------------------------------------------------
# answer_query — deterministic engine tests (no Ollama)
# ---------------------------------------------------------------------------


class TestPathEngine:
    def test_path_found(self, db_conn):
        result = answer_query(_TOPO_ID, "Show all paths between Core-Router and Web-Server", db_conn)
        assert result["intent"] == "path"
        assert result["engine"] == "path"
        assert "Core-Router" in result["answer"]
        assert "Web-Server" in result["answer"]
        assert result["data"]["path_count"] >= 1

    def test_path_no_path_to_isolated(self, db_conn):
        result = answer_query(_TOPO_ID, "Show path from Core-Router to Isolated-WS", db_conn)
        assert result["intent"] == "path"
        # isolated node → no path
        assert "no path" in result["answer"].lower() or "not found" in result["answer"].lower()

    def test_shortest_path_hops(self, db_conn):
        result = answer_query(_TOPO_ID, "What is the shortest path from Core-Router to Web-Server?", db_conn)
        data = result["data"]
        # Core-Router → Core-Switch → Edge-Firewall → Web-Server = 3 hops
        assert data.get("shortest_hops") == 3


class TestFailureEngine:
    def test_failure_critical_node(self, db_conn):
        # Core-Switch is an articulation point (connects router to both servers)
        result = answer_query(_TOPO_ID, "What happens if Core-Switch goes down?", db_conn)
        assert result["intent"] == "failure"
        assert result["engine"] == "failure"
        data = result["data"]
        assert data["failed_device"] == "Core-Switch"
        assert data["is_critical"] is True

    def test_failure_lists_neighbors(self, db_conn):
        result = answer_query(_TOPO_ID, "What if Core-Router fails?", db_conn)
        data = result["data"]
        assert "Core-Switch" in data["direct_neighbors"]

    def test_failure_isolated_node(self, db_conn):
        result = answer_query(_TOPO_ID, "What if Isolated-WS goes down?", db_conn)
        data = result["data"]
        # Isolated node — not a critical bridge
        assert data["is_critical"] is False
        assert data["direct_neighbors"] == []


class TestNeighborEngine:
    def test_neighbors_of_switch(self, db_conn):
        result = answer_query(_TOPO_ID, "What is directly connected to Core-Switch?", db_conn)
        assert result["intent"] == "neighbor"
        data = result["data"]
        assert data["count"] == 3  # r1, fw1, srv2
        assert "Core-Router" in data["neighbors"]

    def test_neighbors_of_isolated(self, db_conn):
        result = answer_query(_TOPO_ID, "What is connected to Isolated-WS?", db_conn)
        assert "no connections" in result["answer"].lower()


class TestInventoryEngine:
    def test_count_firewalls(self, db_conn):
        result = answer_query(_TOPO_ID, "How many firewalls are there?", db_conn)
        assert result["intent"] == "inventory"
        assert result["data"]["count"] == 1

    def test_count_servers(self, db_conn):
        result = answer_query(_TOPO_ID, "List all servers", db_conn)
        assert result["data"]["count"] == 2

    def test_general_inventory_summary(self, db_conn):
        result = answer_query(_TOPO_ID, "Show me an inventory of all devices", db_conn)
        assert result["intent"] == "inventory"
        assert "Total nodes" in result["answer"]


# ---------------------------------------------------------------------------
# answer_query — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_question(self, db_conn):
        result = answer_query(_TOPO_ID, "", db_conn)
        assert "please" in result["answer"].lower()

    def test_invalid_topology_id(self, db_conn):
        result = answer_query("does-not-exist", "any question", db_conn)
        assert "not found" in result["answer"].lower()

    def test_query_logged(self, db_conn):
        answer_query(_TOPO_ID, "How many routers?", db_conn)
        row = db_conn.execute("SELECT * FROM nc_query_log WHERE topology_id=?", (_TOPO_ID,)).fetchone()
        assert row is not None
        assert row["question"] == "How many routers?"
        assert row["intent"] == "inventory"

    def test_returns_query_id(self, db_conn):
        result = answer_query(_TOPO_ID, "List all servers", db_conn)
        assert result.get("query_id")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
