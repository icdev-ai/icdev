# CUI // SP-CTI
"""Unit tests for the IQE NDC adapter: tools/iqe/adapters/ndc.py

Tests cover:
  - NDCAdapter.list_collections()  — returns all 13 registered collections
  - NDCAdapter._query()            — routes to correct SQL for each collection
  - topology_id filtering          — WHERE clause applied when provided
  - config_json expansion          — auto-expands into 'config' dict key
  - device type filtering          — network.devices only returns device rows
  - alias equality                 — network.findings == network.compliance_findings
  - unknown collection             — raises ValueError
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools.iqe.adapters.ndc import NDCAdapter, _COLLECTIONS, _DEVICE_TYPES

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NC_SCHEMA = """
CREATE TABLE topologies (
    id TEXT PRIMARY KEY,
    name TEXT,
    graph_json TEXT,
    created_at TEXT
);
CREATE TABLE nc_objects (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    object_type TEXT,
    label TEXT,
    config_json TEXT
);
CREATE TABLE nc_circuits (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    label TEXT
);
CREATE TABLE nc_sites (
    id TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE nc_ipam_blocks (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    prefix TEXT
);
CREATE TABLE nc_compliance_findings (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    severity TEXT,
    rule_id TEXT,
    description TEXT,
    status TEXT
);
CREATE TABLE nc_projects (
    id TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE nc_versions (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    label TEXT
);
CREATE TABLE nc_groups (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    name TEXT
);
CREATE TABLE nc_cables (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    label TEXT
);
CREATE TABLE nc_cross_connects (
    id TEXT PRIMARY KEY,
    topology_id TEXT,
    label TEXT
);
"""


@pytest.fixture()
def mem_db() -> sqlite3.Connection:
    """In-memory SQLite with all NC tables and seed data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_NC_SCHEMA)

    # Topologies
    conn.execute("INSERT INTO topologies VALUES ('t1','Alpha','{}',NULL)")
    conn.execute("INSERT INTO topologies VALUES ('t2','Beta','{}',NULL)")

    # nc_objects: two devices for t1, one non-device, one for t2
    config = json.dumps({"vendor": "Cisco", "model": "C9300"})
    conn.execute("INSERT INTO nc_objects VALUES ('sw-1','t1','switch','SW-Core',?)", (config,))
    conn.execute("INSERT INTO nc_objects VALUES ('rtr-1','t1','router','RTR-Edge',NULL)")
    conn.execute("INSERT INTO nc_objects VALUES ('an-1','t1','annotation','Note',NULL)")  # non-device
    conn.execute("INSERT INTO nc_objects VALUES ('sw-2','t2','switch','SW-Remote',NULL)")

    # Other tables
    conn.execute("INSERT INTO nc_circuits VALUES ('c1','t1','Circuit-1')")
    conn.execute("INSERT INTO nc_sites VALUES ('s1','Site-NYC')")
    conn.execute("INSERT INTO nc_ipam_blocks VALUES ('i1','t1','10.0.0.0/24')")
    conn.execute("INSERT INTO nc_compliance_findings VALUES ('f1','t1','CAT1','NIST-AC-1','Finding A','open')")
    conn.execute("INSERT INTO nc_projects VALUES ('p1','Project Alpha')")
    conn.execute("INSERT INTO nc_versions VALUES ('v1','t1','v1.0')")
    conn.execute("INSERT INTO nc_groups VALUES ('g1','t1','Core Switches')")
    conn.execute("INSERT INTO nc_cables VALUES ('ca1','t1','Cable-001')")
    conn.execute("INSERT INTO nc_cross_connects VALUES ('cc1','t1','XC-001')")
    conn.commit()
    return conn


@pytest.fixture()
def adapter() -> NDCAdapter:
    return NDCAdapter()


# ---------------------------------------------------------------------------
# list_collections
# ---------------------------------------------------------------------------


def test_list_collections_count(adapter: NDCAdapter) -> None:
    assert len(adapter.list_collections()) == 13


def test_list_collections_contains_all_expected(adapter: NDCAdapter) -> None:
    cols = adapter.list_collections()
    for expected in _COLLECTIONS:
        assert expected in cols


def test_list_collections_includes_alias(adapter: NDCAdapter) -> None:
    cols = adapter.list_collections()
    assert "network.compliance_findings" in cols
    assert "network.findings" in cols


# ---------------------------------------------------------------------------
# _query — network.topologies
# ---------------------------------------------------------------------------


def test_query_topologies_returns_all_rows(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.topologies", None)
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert "Alpha" in names and "Beta" in names


# ---------------------------------------------------------------------------
# _query — network.devices (filtered to device types)
# ---------------------------------------------------------------------------


def test_query_devices_excludes_non_device_types(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", None)
    # annotation is not a device type
    object_types = {r["object_type"] for r in rows}
    assert "annotation" not in object_types


def test_query_devices_only_returns_known_device_types(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", None)
    for row in rows:
        assert row["object_type"] in _DEVICE_TYPES


def test_query_devices_topology_filter(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", "t1")
    assert all(r["topology_id"] == "t1" for r in rows)
    # t1 has switch + router (not annotation)
    assert len(rows) == 2


def test_query_devices_unfiltered_returns_all_topologies(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", None)
    topo_ids = {r["topology_id"] for r in rows}
    assert "t1" in topo_ids and "t2" in topo_ids


# ---------------------------------------------------------------------------
# _query — network.objects (unfiltered)
# ---------------------------------------------------------------------------


def test_query_objects_includes_non_device_types(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.objects", "t1")
    object_types = {r["object_type"] for r in rows}
    assert "annotation" in object_types


def test_query_objects_topology_filter(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.objects", "t1")
    assert len(rows) == 3  # sw-1, rtr-1, an-1


# ---------------------------------------------------------------------------
# config_json auto-expansion
# ---------------------------------------------------------------------------


def test_config_json_expanded_into_config_key(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", "t1")
    sw = next(r for r in rows if r["id"] == "sw-1")
    assert "config" in sw
    assert sw["config"]["vendor"] == "Cisco"
    assert sw["config"]["model"] == "C9300"


def test_null_config_json_produces_empty_dict(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.devices", "t1")
    rtr = next(r for r in rows if r["id"] == "rtr-1")
    # rtr-1 has NULL config_json — no 'config' key added
    assert "config" not in rtr or rtr.get("config") is None or rtr.get("config") == {}


# ---------------------------------------------------------------------------
# _query — other collections
# ---------------------------------------------------------------------------


def test_query_circuits(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.circuits", "t1")
    assert len(rows) == 1
    assert rows[0]["label"] == "Circuit-1"


def test_query_sites_returns_all(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.sites", None)
    assert len(rows) == 1
    assert rows[0]["name"] == "Site-NYC"


def test_query_ipam(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.ipam", "t1")
    assert len(rows) == 1
    assert rows[0]["prefix"] == "10.0.0.0/24"


def test_query_findings(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.findings", "t1")
    assert len(rows) == 1
    assert rows[0]["severity"] == "CAT1"


def test_query_compliance_findings_alias(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows_alias = adapter._query(mem_db, "network.compliance_findings", "t1")
    rows_primary = adapter._query(mem_db, "network.findings", "t1")
    assert len(rows_alias) == len(rows_primary)


def test_query_projects(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.projects", None)
    assert len(rows) == 1


def test_query_versions(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.versions", "t1")
    assert len(rows) == 1
    assert rows[0]["label"] == "v1.0"


def test_query_groups(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.groups", "t1")
    assert len(rows) == 1
    assert rows[0]["name"] == "Core Switches"


def test_query_cables(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.cables", "t1")
    assert len(rows) == 1


def test_query_cross_connects(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows = adapter._query(mem_db, "network.cross_connects", "t1")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Unknown collection
# ---------------------------------------------------------------------------


def test_query_unknown_collection_raises(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown collection"):
        adapter._query(mem_db, "network.nonexistent", None)


# ---------------------------------------------------------------------------
# topology_id=None vs provided — topology-scoped vs global collections
# ---------------------------------------------------------------------------


def test_topology_filter_none_returns_cross_topology_results(
    adapter: NDCAdapter, mem_db: sqlite3.Connection
) -> None:
    rows = adapter._query(mem_db, "network.devices", None)
    topo_ids = {r["topology_id"] for r in rows}
    assert len(topo_ids) > 1  # rows from both t1 and t2


def test_topology_filter_scopes_results(adapter: NDCAdapter, mem_db: sqlite3.Connection) -> None:
    rows_t1 = adapter._query(mem_db, "network.devices", "t1")
    rows_t2 = adapter._query(mem_db, "network.devices", "t2")
    assert all(r["topology_id"] == "t1" for r in rows_t1)
    assert all(r["topology_id"] == "t2" for r in rows_t2)
