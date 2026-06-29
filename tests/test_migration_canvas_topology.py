#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Network Migration topology auto-generation (Phase B)."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.migration_canvas.db import init_db as init_db_mod
from tools.migration_canvas import network_migration as nm


@pytest.fixture
def mc_db_path(tmp_path, monkeypatch):
    """Point migration_canvas DB to a temp SQLite file."""
    db_path = tmp_path / "migration_canvas_topology.db"
    monkeypatch.setenv("MC_DB_PATH", str(db_path))
    monkeypatch.setattr(init_db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(nm, "_MC_DB_PATH", db_path)
    init_db_mod.init_db()
    return db_path


@pytest.fixture
def session_id(mc_db_path):
    """Create a network migration session with a parsed config."""
    sid = f"nmig-topo-test-{uuid.uuid4().hex[:8]}"
    raw_config = """
set system host-name core-rtr-01
set interfaces et-0/0/0 description "Uplink to core-rtr-02"
set interfaces et-0/0/0 unit 0 family inet address 10.0.0.1/30
set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members 100
set interfaces ge-0/0/1 unit 0 family inet address 192.168.1.1/24
set vlans VLAN100 vlan-id 100
set interfaces lo0 unit 0 family inet address 127.0.0.1/32
set protocols bgp group external neighbor 10.0.0.2 peer-as 65001
set routing-options static route 0.0.0.0/0 next-hop 192.168.1.254
"""
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, src_config_raw, src_device_name, tgt_device_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "Juniper MX204", "Cisco ASR-9901", raw_config, "core-rtr-01", "core-rtr-01-new"),
        )
        conn.commit()
    return sid


def test_discover_neighbors_extracts_bgp_and_static(session_id):
    """Neighbor discovery extracts BGP peers and static next-hops."""
    neighbors = nm.discover_neighbors(session_id)
    relationships = {n["relationship"] for n in neighbors}
    assert "bgp_peer" in relationships
    assert "downstream" in relationships
    bgp = next(n for n in neighbors if n["relationship"] == "bgp_peer")
    assert bgp["neighbor_ip"] == "10.0.0.2"


def test_discover_neighbors_returns_empty_for_blank_session(mc_db_path):
    """A session with no config produces an empty neighbor list."""
    sid = f"nmig-topo-empty-{uuid.uuid4().hex[:8]}"
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, src_config_raw) "
            "VALUES (?, ?, ?, ?)",
            (sid, "Juniper MX204", "Cisco ASR-9901", ""),
        )
        conn.commit()
    assert nm.discover_neighbors(sid) == []


def test_build_topology_creates_source_target_and_interface_nodes(session_id):
    """build_topology produces source/target device nodes and interface media nodes."""
    result = nm.build_topology(session_id)
    graph = result["graph_json"]
    assert result["source"] == "generated"

    node_ids = {n["id"] for n in graph["nodes"]}
    assert any("topo-src-" in nid for nid in node_ids)
    assert any("topo-tgt-" in nid for nid in node_ids)
    assert any("topo-iface-" in nid for nid in node_ids)
    # loopback should be skipped
    assert not any("lo0" in nid for nid in node_ids)

    types = {n["type"] for n in graph["nodes"]}
    assert "router" in types
    # Interface media nodes use either "media-*" speed shapes or optic shapes.
    media_types = {"media-ge", "media-10ge", "media-25ge", "media-40ge", "media-100ge", "qsfp", "qsfp-dd", "sfp", "sfp-plus"}
    assert any(t in media_types for t in types)

    edges = graph["edges"]
    assert edges
    sources = {e["source"] for e in edges}
    targets = {e["target"] for e in edges}
    # Source is connected to at least one interface or neighbor
    assert any("topo-src-" in n for n in sources | targets)


def test_build_topology_creates_vlan_and_neighbor_nodes(session_id):
    """VLAN and inferred neighbor nodes appear in the generated graph."""
    result = nm.build_topology(session_id)
    graph = result["graph_json"]
    node_ids = {n["id"] for n in graph["nodes"]}
    assert any("topo-vlan-" in nid and nid.endswith("-100") for nid in node_ids)
    assert any("topo-neigh-" in nid for nid in node_ids)


def test_build_topology_persists_neighbors_and_json(session_id):
    """The generated topology and neighbor rows are persisted to the canvas DB."""
    result = nm.build_topology(session_id)
    with init_db_mod.get_connection() as conn:
        row = conn.execute(
            "SELECT topology_json, topology_neighbors_json FROM mc_net_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        assert row["topology_json"]
        assert row["topology_neighbors_json"]
        rows = conn.execute(
            "SELECT relationship FROM mc_net_topology_neighbors WHERE session_id=?",
            (session_id,),
        ).fetchall()
        rels = {r["relationship"] for r in rows}
    assert "bgp_peer" in rels
    assert len(rows) == len(result["neighbors"])


def test_build_topology_returns_stored_on_second_call(session_id):
    """Without refresh, the second call returns the stored topology."""
    first = nm.build_topology(session_id)
    second = nm.build_topology(session_id)
    assert first["source"] == "generated"
    assert second["source"] == "stored"
    assert first["graph_json"] == second["graph_json"]


def test_build_topology_refresh_regenerates(session_id):
    """A refresh rebuilds the topology from the stored config."""
    nm.build_topology(session_id)
    refreshed = nm.build_topology(session_id, refresh=True)
    assert refreshed["source"] == "generated"
    assert refreshed["graph_json"]["nodes"]
    assert refreshed["graph_json"]["edges"]
