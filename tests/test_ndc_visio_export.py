# CUI // SP-CTI
"""Unit tests for the NDC Visio VSDX exporter (ndc-qa-02).

Covers ``tools/network/visio_export.py``:
  - export_vsdx: a seeded topology exports to a structurally valid .vsdx ZIP
    containing the expected OOXML part names and embedding node labels.
  - enrichment metadata is merged into shape data properties.
  - empty topology still produces a valid ZIP with the package skeleton.
  - export_ops_csvs: derives device/circuit/IP/peering CSV strings.
  - malformed graph_json (None) raises cleanly rather than corrupting output.

Structural assertions only — no Visio rendering. The seeded-topology test also
round-trips graph_json through a temp SQLite ``topologies`` row to mirror the
canvas persistence path (init_db.get_connection monkeypatched).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from tools.network.visio_export import export_ops_csvs, export_vsdx

_EXPECTED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "visio/document.xml",
    "visio/_rels/document.xml.rels",
    "visio/pages/pages.xml",
    "visio/pages/_rels/pages.xml.rels",
    "visio/pages/page1.xml",
}


def _sample_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "n1",
                "label": "CoreRouter1",
                "type": "router",
                "x": 96,
                "y": 96,
                "config": {"hostname": "core-rtr-01", "ip": "10.0.0.1/24", "model": "Cisco ASR9000"},
            },
            {
                "id": "n2",
                "label": "EdgeFirewall",
                "type": "firewall",
                "x": 288,
                "y": 96,
                "config": {"hostname": "fw-edge-01", "ip": "10.0.0.2", "asn": "65001", "peer_asn": "65002", "peer_ip": "10.0.0.3"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "label": "10GbE-uplink"},
        ],
    }


# ── export_vsdx structural checks ──────────────────────────────────────────────

def test_export_vsdx_is_valid_zip_with_ooxml_parts():
    data = export_vsdx("MyTopology", _sample_graph())
    assert isinstance(data, (bytes, bytearray))
    assert zipfile.is_zipfile(io.BytesIO(data))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert _EXPECTED_PARTS <= names


def test_export_vsdx_embeds_node_labels_and_title():
    data = export_vsdx("MyTopology", _sample_graph())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
        document = zf.read("visio/document.xml").decode("utf-8")

    assert "CoreRouter1" in page1
    assert "EdgeFirewall" in page1
    # The connector label is embedded too.
    assert "10GbE-uplink" in page1
    # Topology name lands in the document title.
    assert "MyTopology" in document


def test_export_vsdx_embeds_shape_data_properties():
    data = export_vsdx("MyTopology", _sample_graph())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
    # Config values are written as Visio shape-data property rows.
    assert "core-rtr-01" in page1
    assert "Cisco ASR9000" in page1
    assert '<Section N="Property">' in page1


def test_export_vsdx_merges_enrichment_metadata():
    graph = _sample_graph()
    data = export_vsdx("Enriched", graph, enrichment={"n1": {"rack": "R42", "site": "DC-East"}})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
    assert "R42" in page1
    assert "DC-East" in page1


def test_export_vsdx_empty_topology_still_valid():
    data = export_vsdx("Empty", {"nodes": [], "edges": []})
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert _EXPECTED_PARTS <= names
        # page1 exists and is well-formed even with no shapes.
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
        assert "<PageContents" in page1


def test_export_vsdx_missing_keys_defaults_to_empty():
    # graph_json with neither nodes nor edges -> treated as empty, valid ZIP.
    data = export_vsdx("Bare", {})
    assert zipfile.is_zipfile(io.BytesIO(data))


def test_export_vsdx_none_graph_raises_cleanly():
    # Malformed graph_json (None) is a programming error surfaced as a clean
    # Python exception, not a corrupt/partial file.
    with pytest.raises(AttributeError):
        export_vsdx("Bad", None)


def test_export_vsdx_escapes_xml_special_chars():
    graph = {"nodes": [{"id": "n1", "label": "R&D <core>", "x": 0, "y": 0}], "edges": []}
    data = export_vsdx("A & B", graph)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
        document = zf.read("visio/document.xml").decode("utf-8")
    # Raw ampersand/angle brackets must be escaped, not injected verbatim.
    assert "R&amp;D" in page1
    assert "&lt;core&gt;" in page1
    assert "A &amp; B" in document
    # The XML parts must remain parseable.
    import xml.etree.ElementTree as ET
    ET.fromstring(page1)
    ET.fromstring(document)


# ── seeded-topology round-trip via temp SQLite ─────────────────────────────────

def test_export_vsdx_from_seeded_topology_row(tmp_path, monkeypatch):
    from tools.network.db import init_db

    db_file = tmp_path / "nc_topos.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)

    conn = init_db.get_connection()
    conn.execute(
        "CREATE TABLE topologies (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)"
    )
    graph = _sample_graph()
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (%s, %s, %s)",
        ("t1", "SeededTopo", json.dumps(graph)),
    )
    conn.commit()

    row = conn.execute(
        "SELECT name, graph_json FROM topologies WHERE id=%s", ("t1",)
    ).fetchone()
    conn.close()

    data = export_vsdx(row["name"], json.loads(row["graph_json"]))
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        page1 = zf.read("visio/pages/page1.xml").decode("utf-8")
    assert "CoreRouter1" in page1


# ── export_ops_csvs ────────────────────────────────────────────────────────────

def test_export_ops_csvs_produces_expected_files():
    result = export_ops_csvs("My Topology", _sample_graph())
    # Filenames are sanitized (spaces -> underscore) and cover all 5 reports.
    assert "My_Topology_device_inventory.csv" in result
    assert "My_Topology_circuit_list.csv" in result
    assert "My_Topology_cable_schedule.csv" in result
    assert "My_Topology_ip_allocation.csv" in result
    assert "My_Topology_peering_matrix.csv" in result

    inv = result["My_Topology_device_inventory.csv"]
    assert inv.splitlines()[0].startswith("hostname,type,model")
    assert "core-rtr-01" in inv

    # The firewall node carries peer_asn/peer_ip -> one peering row.
    peering = result["My_Topology_peering_matrix.csv"]
    assert "65002" in peering


def test_export_ops_csvs_empty_topology_has_headers_only():
    result = export_ops_csvs("Empty", {"nodes": [], "edges": []})
    inv = result["Empty_device_inventory.csv"]
    # Header row present, no data rows.
    assert len(inv.strip().splitlines()) == 1
