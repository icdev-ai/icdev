# [CUI // SP-CTI]
"""Tests for tools.network.layout and tools.network.config_import."""
from __future__ import annotations

from pathlib import Path

from tools.network.config_import import (
    detect_vendor,
    import_config,
    synthesize_topology,
)
from tools.network.layout import auto_layout, classify_tier


# ── Layout ────────────────────────────────────────────────────────────


def test_classify_tier_known_labels():
    assert classify_tier("isp-handoff", "wan_link") == "external"
    assert classify_tier("edge-firewall") == "edge"
    assert classify_tier("aws-tgw") == "cloud"
    assert classify_tier("core-switch") == "core"
    assert classify_tier("dist-switch") == "dist"
    assert classify_tier("access-switch") == "access"
    assert classify_tier("server-01") == "endpoint"
    assert classify_tier("widget-7") == "unknown"


def test_auto_layout_assigns_distinct_lanes():
    g = {"nodes": [
        {"id": "1", "label": "isp", "type": "wan_link", "x": 0, "y": 0},
        {"id": "2", "label": "edge-router", "x": 0, "y": 0},
        {"id": "3", "label": "core-switch", "x": 0, "y": 0},
        {"id": "4", "label": "server-01", "x": 0, "y": 0},
    ], "edges": []}
    auto_layout(g)
    ys = sorted({n["y"] for n in g["nodes"]})
    assert len(ys) == 4, f"expected 4 distinct lanes, got {ys}"
    # External tier is on top
    isp = next(n for n in g["nodes"] if n["label"] == "isp")
    server = next(n for n in g["nodes"] if n["label"] == "server-01")
    assert isp["y"] < server["y"], "external should be above endpoint"


def test_auto_layout_de_overlaps_same_lane():
    g = {"nodes": [
        {"id": str(i), "label": f"server-{i:02d}", "x": 0, "y": 0}
        for i in range(1, 6)
    ], "edges": []}
    auto_layout(g, min_node_spacing=120)
    xs = sorted(n["x"] for n in g["nodes"])
    for a, b in zip(xs, xs[1:]):
        assert b - a >= 120, f"siblings too close: {xs}"


def test_auto_layout_idempotent():
    g = {"nodes": [
        {"id": "1", "label": "core-switch", "x": 0, "y": 0},
        {"id": "2", "label": "dist-switch", "x": 0, "y": 0},
    ], "edges": []}
    auto_layout(g)
    snap1 = [(n["x"], n["y"]) for n in g["nodes"]]
    auto_layout(g)
    snap2 = [(n["x"], n["y"]) for n in g["nodes"]]
    assert snap1 == snap2


# ── Config import — vendor detection ──────────────────────────────────


def test_detect_vendor_cisco():
    cfg = """
hostname core-rtr-01
!
interface GigabitEthernet0/1
 description uplink to dist-sw01
 ip address 10.1.1.1 255.255.255.252
!
router ospf 1
 network 10.1.1.0 0.0.0.3 area 0
"""
    assert detect_vendor(cfg) == "cisco"


def test_detect_vendor_juniper_set():
    cfg = """
set system host-name edge-mx-01
set interfaces ge-0/0/0 unit 0 family inet address 10.2.2.1/30
set interfaces ge-0/0/0 description "uplink to core-mx-02"
"""
    assert detect_vendor(cfg) == "juniper"


def test_detect_vendor_juniper_curly():
    cfg = """
system {
    host-name edge-mx-01;
}
interfaces {
    ge-0/0/0 {
        unit 0 {
            family inet {
                address 10.2.2.1/30;
            }
        }
    }
}
"""
    assert detect_vendor(cfg) == "juniper"


# ── Cisco parsing ─────────────────────────────────────────────────────


def test_cisco_parses_hostname_and_interfaces(tmp_path: Path):
    cfg = """
hostname core-rtr-01
!
interface GigabitEthernet0/1
 description uplink to dist-sw01
 ip address 10.1.1.1 255.255.255.252
!
interface GigabitEthernet0/2
 description peer to edge-fw01
 ip address 10.1.2.1 255.255.255.252
 speed 1000
!
interface Vlan10
 ip address 192.168.10.1 255.255.255.0
"""
    p = tmp_path / "core-rtr-01.cfg"
    p.write_text(cfg)
    d = import_config(str(p))
    assert d["hostname"] == "core-rtr-01"
    assert d["vendor"] == "cisco"
    names = {i["name"] for i in d["interfaces"]}
    assert {"GigabitEthernet0/1", "GigabitEthernet0/2", "Vlan10"} <= names
    gi1 = next(i for i in d["interfaces"] if i["name"] == "GigabitEthernet0/1")
    assert gi1["ip"] == "10.1.1.1"
    assert gi1["mask"] == "30"
    assert "uplink to dist-sw01" in gi1["description"]


def test_cisco_extracts_cdp_neighbors(tmp_path: Path):
    cfg = """
hostname core-rtr-01
!
Device ID: dist-sw01.example.com
Entry address(es):
  IP address: 10.1.1.2
Platform: cisco WS-C3850, Capabilities: Switch
Interface: GigabitEthernet0/1, Port ID (outgoing port): GigabitEthernet0/24
"""
    p = tmp_path / "show.cfg"
    p.write_text(cfg)
    d = import_config(str(p))
    assert any(n["peer"] == "dist-sw01" for n in d["neighbors"])


# ── Junos parsing ─────────────────────────────────────────────────────


def test_junos_set_format(tmp_path: Path):
    cfg = """
set system host-name edge-mx-01
set interfaces ge-0/0/0 description "uplink to core-mx-02"
set interfaces ge-0/0/0 unit 0 family inet address 10.2.2.1/30
set interfaces ge-0/0/1 description "to dist-sw03"
set interfaces ge-0/0/1 unit 0 family inet address 10.2.3.1/30
"""
    p = tmp_path / "edge-mx-01.conf"
    p.write_text(cfg)
    d = import_config(str(p))
    assert d["hostname"] == "edge-mx-01"
    assert d["vendor"] == "juniper"
    names = {i["name"] for i in d["interfaces"]}
    assert {"ge-0/0/0", "ge-0/0/1"} <= names
    g0 = next(i for i in d["interfaces"] if i["name"] == "ge-0/0/0")
    assert g0["ip"] == "10.2.2.1"
    assert g0["mask"] == "30"


# ── Adjacency synthesis ───────────────────────────────────────────────


def test_topology_subnet_inference(tmp_path: Path):
    """Two devices on the same /30 must be auto-linked."""
    a = tmp_path / "a.cfg"
    b = tmp_path / "b.cfg"
    a.write_text("""hostname rtr-a
interface GigabitEthernet0/1
 ip address 10.1.1.1 255.255.255.252
""")
    b.write_text("""hostname rtr-b
interface GigabitEthernet0/1
 ip address 10.1.1.2 255.255.255.252
""")
    devices = [import_config(str(a)), import_config(str(b))]
    g = synthesize_topology(devices)
    assert len(g["nodes"]) == 2
    assert len(g["edges"]) == 1
    e = g["edges"][0]
    assert e["properties"]["discovery"] == "subnet"
    assert e["properties"]["confidence"] >= 0.85


def test_topology_description_inference(tmp_path: Path):
    """Description naming a peer device must create an edge."""
    a = tmp_path / "a.cfg"
    b = tmp_path / "b.cfg"
    a.write_text("""hostname core-sw01
interface GigabitEthernet0/24
 description uplink to dist-sw01 gi0/1
""")
    b.write_text("""hostname dist-sw01
interface GigabitEthernet0/1
""")
    g = synthesize_topology([import_config(str(a)), import_config(str(b))])
    assert len(g["edges"]) == 1
    assert g["edges"][0]["properties"]["discovery"] == "description"


def test_topology_mixed_vendors(tmp_path: Path):
    """Cisco ↔ Juniper on same subnet should still link."""
    cisco = tmp_path / "cisco.cfg"
    junos = tmp_path / "junos.conf"
    cisco.write_text("""hostname core-cisco
interface GigabitEthernet0/1
 ip address 10.99.0.1 255.255.255.252
""")
    junos.write_text("""set system host-name edge-junos
set interfaces ge-0/0/0 unit 0 family inet address 10.99.0.2/30
""")
    g = synthesize_topology([import_config(str(cisco)), import_config(str(junos))])
    assert len(g["nodes"]) == 2
    assert len(g["edges"]) == 1


def test_topology_with_auto_layout(tmp_path: Path):
    """End-to-end: parse 4 configs → synthesize → auto-layout → exports cleanly."""
    files = {
        "edge-fw01.cfg": "hostname edge-fw01\ninterface GigabitEthernet0/1\n ip address 10.0.0.1 255.255.255.252\n",
        "core-sw01.cfg": "hostname core-sw01\ninterface GigabitEthernet0/1\n ip address 10.0.0.2 255.255.255.252\ninterface GigabitEthernet0/2\n ip address 10.0.1.1 255.255.255.252\n",
        "dist-sw01.cfg": "hostname dist-sw01\ninterface GigabitEthernet0/1\n ip address 10.0.1.2 255.255.255.252\ninterface GigabitEthernet0/2\n ip address 10.0.2.1 255.255.255.252\n",
        "server-01.cfg": "hostname server-01\ninterface eth0\n ip address 10.0.2.2 255.255.255.252\n",
    }
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    devices = [import_config(str(tmp_path / n)) for n in files]
    g = synthesize_topology(devices)
    auto_layout(g)
    assert len(g["nodes"]) == 4
    # Three /30s → three edges (chain)
    assert len(g["edges"]) == 3
    # Lanes assigned — at least 3 distinct y values across 4 nodes
    ys = {n["y"] for n in g["nodes"]}
    assert len(ys) >= 3
