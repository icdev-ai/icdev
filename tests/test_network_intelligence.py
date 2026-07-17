# [CUI // SP-CTI]
"""Tests for Network Infrastructure Intelligence engine."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────────────

class _NoCloseConn:
    """Wrapper that prevents .close() from actually closing the connection."""
    def __init__(self, conn):
        self._conn = conn
    def __getattr__(self, name):
        if name == "close":
            return lambda: None  # no-op
        return getattr(self._conn, name)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


@pytest.fixture
def mem_db():
    """In-memory SQLite with NII schema (close-protected for mock reuse)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")

    # Minimal schema
    conn.executescript("""
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY, name TEXT, description TEXT,
            graph_json TEXT DEFAULT '{"nodes":[],"edges":[]}',
            template_id TEXT, classification TEXT DEFAULT 'CUI',
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE ni_devices (
            id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT,
            label TEXT, device_type TEXT, vendor TEXT, model TEXT,
            firmware_version TEXT, eol_date TEXT, eos_date TEXT,
            purchase_date TEXT, purchase_cost REAL DEFAULT 0,
            annual_maintenance_cost REAL DEFAULT 0,
            replacement_cost REAL DEFAULT 0,
            site TEXT, rack_location TEXT,
            criticality_score REAL DEFAULT 0,
            downstream_count INTEGER DEFAULT 0,
            notes TEXT, properties_json TEXT DEFAULT '{}',
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE ni_analyses (
            id TEXT PRIMARY KEY, topology_id TEXT,
            analysis_type TEXT, query_text TEXT,
            input_json TEXT DEFAULT '{}', result_json TEXT DEFAULT '{}',
            result_summary TEXT, created_at TEXT
        );
        CREATE TABLE ni_state_snapshots (
            id TEXT PRIMARY KEY, topology_id TEXT,
            snapshot_type TEXT DEFAULT 'manual',
            graph_json TEXT, device_count INTEGER DEFAULT 0,
            link_count INTEGER DEFAULT 0, change_description TEXT,
            created_at TEXT
        );
        CREATE TABLE ni_device_configs (
            id TEXT PRIMARY KEY, device_id TEXT,
            config_type TEXT, config_text TEXT,
            config_hash TEXT, source TEXT,
            version INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE nc_circuits (
            id TEXT PRIMARY KEY, topology_id TEXT,
            circuit_id TEXT, carrier TEXT, circuit_type TEXT,
            bandwidth TEXT, handoff_a TEXT, handoff_z TEXT,
            customer TEXT, site TEXT,
            monthly_cost_usd REAL DEFAULT 0,
            contract_start TEXT, contract_end TEXT,
            sla_uptime_pct REAL DEFAULT 99.9,
            install_status TEXT DEFAULT 'active',
            notes TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE nc_cross_connects (
            id TEXT PRIMARY KEY, topology_id TEXT,
            xconn_id TEXT, facility TEXT, meet_me_room TEXT,
            src_device TEXT, src_port TEXT, dst_device TEXT, dst_port TEXT,
            media_type TEXT, bandwidth TEXT,
            provider_a TEXT, provider_z TEXT,
            loa_status TEXT, monthly_cost_usd REAL DEFAULT 0,
            install_date TEXT, status TEXT, notes TEXT,
            created_at TEXT, updated_at TEXT
        );
    """)
    # Wrap in StorageConnection so functions-under-test that author PG-native %s
    # placeholders (network_intelligence uses the NC get_connection() helper) have
    # them translated to ? for this SQLite backend — a raw sqlite3 conn rejects %s.
    from tools.db.storage import StorageConnection

    return _NoCloseConn(StorageConnection(conn, "sqlite"))


@pytest.fixture
def sample_graph():
    """A simple network graph: A -- B -- C -- D (chain)."""
    return {
        "nodes": [
            {"id": "A", "label": "Core-Router-NYC", "type": "router", "x": 0, "y": 0},
            {"id": "B", "label": "FW-NYC", "type": "firewall", "x": 100, "y": 0},
            {"id": "C", "label": "Switch-NYC-1", "type": "switch", "x": 200, "y": 0},
            {"id": "D", "label": "Server-NYC-1", "type": "server", "x": 300, "y": 0},
        ],
        "edges": [
            {"id": "e1", "source": "A", "target": "B", "label": "10G", "type": "fiber"},
            {"id": "e2", "source": "B", "target": "C", "label": "1G", "type": "ethernet"},
            {"id": "e3", "source": "C", "target": "D", "label": "1G", "type": "ethernet"},
        ],
    }


@pytest.fixture
def topology_id(mem_db, sample_graph):
    """Insert sample topology and devices into mem_db, return topology_id."""
    tid = "test-topo-1"
    mem_db.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (?, ?, ?)",
        (tid, "NYC DC", json.dumps(sample_graph)),
    )
    for n in sample_graph["nodes"]:
        mem_db.execute(
            "INSERT INTO ni_devices (id, topology_id, node_id, label, device_type, site) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"dev-{n['id']}", tid, n["id"], n["label"], n["type"], "NYC"),
        )
    mem_db.commit()
    return tid


# ── Device Type Classification ───────────────────────────────────────────────

class TestDeviceClassification:
    def test_router(self):
        from tools.network.network_ingester import _classify_device_type
        assert _classify_device_type("Cisco ASR 1001-X") == "router"

    def test_firewall(self):
        from tools.network.network_ingester import _classify_device_type
        assert _classify_device_type("Palo Alto PA-5250") == "firewall"

    def test_switch(self):
        from tools.network.network_ingester import _classify_device_type
        assert _classify_device_type("Arista 7050X3") == "switch"

    def test_load_balancer(self):
        from tools.network.network_ingester import _classify_device_type
        assert _classify_device_type("F5 BIG-IP") == "load_balancer"

    def test_unknown(self):
        from tools.network.network_ingester import _classify_device_type
        assert _classify_device_type("Mystery Box") == "unknown"


# ── Draw.io Import ───────────────────────────────────────────────────────────

class TestDrawioImport:
    def test_parse_basic(self):
        from tools.network.export_import import import_drawio
        xml = (
            '<?xml version="1.0"?>'
            '<mxfile><diagram name="test">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="Router-1" vertex="1" parent="1">'
            '<mxGeometry x="10" y="20" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="n2" value="Switch-1" vertex="1" parent="1">'
            '<mxGeometry x="200" y="20" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="10G" edge="1" source="n1" target="n2" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        result = import_drawio(xml)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["label"] == "Router-1"


# ── Redundancy Analysis ─────────────────────────────────────────────────────

class TestRedundancy:
    def test_chain_has_spofs(self, mem_db, topology_id):
        """A chain topology (A-B-C-D) should have articulation points."""
        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db):
            from tools.network.network_intelligence import analyze_redundancy
            result = analyze_redundancy(topology_id)
            assert result["spof_count"] > 0
            assert not result["fully_redundant"]

    def test_mesh_no_spofs(self, mem_db):
        """A fully meshed graph should have no SPOFs."""
        mesh = {
            "nodes": [
                {"id": "A", "label": "R1", "type": "router", "x": 0, "y": 0},
                {"id": "B", "label": "R2", "type": "router", "x": 100, "y": 0},
                {"id": "C", "label": "R3", "type": "router", "x": 50, "y": 100},
            ],
            "edges": [
                {"id": "e1", "source": "A", "target": "B", "label": ""},
                {"id": "e2", "source": "B", "target": "C", "label": ""},
                {"id": "e3", "source": "A", "target": "C", "label": ""},
            ],
        }
        tid = "mesh-topo"
        mem_db.execute(
            "INSERT INTO topologies (id, name, graph_json) VALUES (?, ?, ?)",
            (tid, "Mesh", json.dumps(mesh)),
        )
        mem_db.commit()

        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db):
            from tools.network.network_intelligence import analyze_redundancy
            result = analyze_redundancy(tid)
            assert result["spof_count"] == 0
            assert result["fully_redundant"]


# ── Blast Radius ─────────────────────────────────────────────────────────────

class TestBlastRadius:
    def test_decay_per_hop(self, mem_db, topology_id):
        """Impact should decay 0.8 per hop in a chain."""
        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db):
            from tools.network.network_intelligence import analyze_blast_radius
            result = analyze_blast_radius(topology_id, "A")
            assert result["total_affected"] == 3  # B, C, D
            # First hop should have highest impact
            first = result["affected_devices"][0]
            assert first["hops"] == 1
            assert first["impact_score"] == 0.8


# ── Query Classification ────────────────────────────────────────────────────

class TestQueryClassification:
    def test_redundancy(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("Do I need a redundant link in NYC?") == "redundancy"

    def test_eol(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("Which devices are approaching end of life?") == "eol"

    def test_blast_radius(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("What is the blast radius if Router-1 fails?") == "blast_radius"

    def test_capacity(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("If I add 250 VDI users what is the impact?") == "capacity"

    def test_cost(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("Show me 5-year cost analysis") == "cost"

    def test_vendor_risk(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("What is our vendor concentration risk?") == "vendor_risk"

    def test_config(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("Show me config drift status") == "config"

    def test_circuit(self):
        from tools.network.network_query_router import classify_query
        assert classify_query("Which circuit contracts expire soon?") == "circuit"


# ── EOL Analysis ─────────────────────────────────────────────────────────────

class TestEOL:
    def test_eol_with_devices(self, mem_db, topology_id):
        """EOL analysis should find devices with eol_date set."""
        mem_db.execute(
            "UPDATE ni_devices SET eol_date = '2027-06-01', replacement_cost = 15000 "
            "WHERE node_id = 'A'"
        )
        mem_db.commit()

        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db), \
             patch("tools.network.device_manager._get_conn", return_value=mem_db):
            from tools.network.network_intelligence import analyze_eol
            result = analyze_eol(topology_id, 5)
            assert result["total_eol_devices"] >= 1
            assert result["monte_carlo"]["total_replacement_cost"]["p50"] > 0


# ── Compliance ───────────────────────────────────────────────────────────────

class TestCompliance:
    def test_basic_compliance(self, mem_db, topology_id):
        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db):
            from tools.network.network_intelligence import assess_compliance
            result = assess_compliance(topology_id)
            assert result["total"] > 0
            assert "SC-7" in [c["control"] for c in result["checks"]]


# ── Vendor Risk ──────────────────────────────────────────────────────────────

class TestVendorRisk:
    def test_single_vendor(self, mem_db, topology_id):
        """All devices same vendor = high concentration."""
        mem_db.execute("UPDATE ni_devices SET vendor = 'Cisco' WHERE topology_id = ?", (topology_id,))
        mem_db.commit()

        with patch("tools.network.network_intelligence._get_nc_conn", return_value=mem_db):
            from tools.network.network_intelligence import assess_vendor_risk
            result = assess_vendor_risk(topology_id)
            assert result["single_vendor_dependency"]
            assert result["vendors"][0]["percentage"] == 100.0
