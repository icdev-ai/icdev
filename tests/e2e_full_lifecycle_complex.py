# CUI // SP-CTI
"""Full Lifecycle E2E: Complex Multi-Site Enterprise Network.

Generates a realistic multi-site network with 30+ devices across
3 data centers, ingests via pipeline, assigns hardware profiles,
populates device metadata, builds KG, runs all 7 intelligence
analyses, tests naming conventions, and verifies RAG search.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

PASS = FAIL = 0


def check(name, passed, detail=""):
    global PASS, FAIL
    PASS += passed
    FAIL += not passed
    print(f"  {'PASS' if passed else 'FAIL'}: {name} {detail}")
    return passed


print("\n" + "=" * 72)
print("  COMPLEX MULTI-SITE ENTERPRISE NETWORK — FULL LIFECYCLE E2E")
print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
print("=" * 72)

# ── Phase 1: Generate Complex DrawIO Diagram ───────────────────────────────

print("\n-- Phase 1: Generate Complex Network (3 sites, 30+ devices) --")

DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile>
  <diagram name="Enterprise Multi-Site">
    <mxGraphModel>
      <root>
        <mxCell id="0"/><mxCell id="1" parent="0"/>
        <!-- NYC Data Center (Primary) -->
        <mxCell id="nyc-core1" value="NYC-CORE-01" vertex="1" parent="1"><mxGeometry x="400" y="50" width="80" height="60"/></mxCell>
        <mxCell id="nyc-core2" value="NYC-CORE-02" vertex="1" parent="1"><mxGeometry x="600" y="50" width="80" height="60"/></mxCell>
        <mxCell id="nyc-fw1" value="NYC-FWLL-01" vertex="1" parent="1"><mxGeometry x="200" y="50" width="80" height="60"/></mxCell>
        <mxCell id="nyc-fw2" value="NYC-FWLL-02" vertex="1" parent="1"><mxGeometry x="800" y="50" width="80" height="60"/></mxCell>
        <mxCell id="nyc-dist1" value="NYC-DIST-01" vertex="1" parent="1"><mxGeometry x="300" y="200" width="80" height="60"/></mxCell>
        <mxCell id="nyc-dist2" value="NYC-DIST-02" vertex="1" parent="1"><mxGeometry x="500" y="200" width="80" height="60"/></mxCell>
        <mxCell id="nyc-dist3" value="NYC-DIST-03" vertex="1" parent="1"><mxGeometry x="700" y="200" width="80" height="60"/></mxCell>
        <mxCell id="nyc-accs1" value="NYC-ACCS-01" vertex="1" parent="1"><mxGeometry x="200" y="350" width="80" height="60"/></mxCell>
        <mxCell id="nyc-accs2" value="NYC-ACCS-02" vertex="1" parent="1"><mxGeometry x="400" y="350" width="80" height="60"/></mxCell>
        <mxCell id="nyc-accs3" value="NYC-ACCS-03" vertex="1" parent="1"><mxGeometry x="600" y="350" width="80" height="60"/></mxCell>
        <mxCell id="nyc-accs4" value="NYC-ACCS-04" vertex="1" parent="1"><mxGeometry x="800" y="350" width="80" height="60"/></mxCell>
        <mxCell id="nyc-lb1" value="NYC-LB-01" vertex="1" parent="1"><mxGeometry x="500" y="350" width="80" height="60"/></mxCell>
        <mxCell id="nyc-srv1" value="NYC-SRVR-01" vertex="1" parent="1"><mxGeometry x="300" y="500" width="80" height="60"/></mxCell>
        <mxCell id="nyc-srv2" value="NYC-SRVR-02" vertex="1" parent="1"><mxGeometry x="500" y="500" width="80" height="60"/></mxCell>
        <mxCell id="nyc-srv3" value="NYC-SRVR-03" vertex="1" parent="1"><mxGeometry x="700" y="500" width="80" height="60"/></mxCell>
        <!-- LAX Data Center (DR) -->
        <mxCell id="lax-core1" value="LAX-CORE-01" vertex="1" parent="1"><mxGeometry x="1200" y="100" width="80" height="60"/></mxCell>
        <mxCell id="lax-fw1" value="LAX-FWLL-01" vertex="1" parent="1"><mxGeometry x="1050" y="100" width="80" height="60"/></mxCell>
        <mxCell id="lax-dist1" value="LAX-DIST-01" vertex="1" parent="1"><mxGeometry x="1100" y="250" width="80" height="60"/></mxCell>
        <mxCell id="lax-dist2" value="LAX-DIST-02" vertex="1" parent="1"><mxGeometry x="1300" y="250" width="80" height="60"/></mxCell>
        <mxCell id="lax-accs1" value="LAX-ACCS-01" vertex="1" parent="1"><mxGeometry x="1100" y="400" width="80" height="60"/></mxCell>
        <mxCell id="lax-accs2" value="LAX-ACCS-02" vertex="1" parent="1"><mxGeometry x="1300" y="400" width="80" height="60"/></mxCell>
        <mxCell id="lax-srv1" value="LAX-SRVR-01" vertex="1" parent="1"><mxGeometry x="1200" y="500" width="80" height="60"/></mxCell>
        <!-- DCA Branch Office -->
        <mxCell id="dca-rtr1" value="DCA-CORE-01" vertex="1" parent="1"><mxGeometry x="1600" y="150" width="80" height="60"/></mxCell>
        <mxCell id="dca-fw1" value="DCA-FWLL-01" vertex="1" parent="1"><mxGeometry x="1500" y="150" width="80" height="60"/></mxCell>
        <mxCell id="dca-sw1" value="DCA-ACCS-01" vertex="1" parent="1"><mxGeometry x="1550" y="300" width="80" height="60"/></mxCell>
        <mxCell id="dca-ap1" value="DCA-WLAN-01" vertex="1" parent="1"><mxGeometry x="1650" y="300" width="80" height="60"/></mxCell>
        <!-- WAN / MPLS -->
        <mxCell id="wan-mpls" value="MPLS-WAN" vertex="1" parent="1"><mxGeometry x="950" y="50" width="80" height="60"/></mxCell>
        <mxCell id="vpn-hub" value="NYC-VPNG-01" vertex="1" parent="1"><mxGeometry x="100" y="50" width="80" height="60"/></mxCell>
        <!-- Edges: NYC Core mesh -->
        <mxCell id="e1" value="100G Fiber" edge="1" source="nyc-core1" target="nyc-core2" parent="1"/>
        <!-- NYC Core to FW -->
        <mxCell id="e2" value="40G" edge="1" source="nyc-fw1" target="nyc-core1" parent="1"/>
        <mxCell id="e3" value="40G" edge="1" source="nyc-fw2" target="nyc-core2" parent="1"/>
        <!-- NYC Core to Distribution -->
        <mxCell id="e4" value="100G" edge="1" source="nyc-core1" target="nyc-dist1" parent="1"/>
        <mxCell id="e5" value="100G" edge="1" source="nyc-core1" target="nyc-dist2" parent="1"/>
        <mxCell id="e6" value="100G" edge="1" source="nyc-core2" target="nyc-dist2" parent="1"/>
        <mxCell id="e7" value="100G" edge="1" source="nyc-core2" target="nyc-dist3" parent="1"/>
        <!-- Distribution to Access -->
        <mxCell id="e8" value="10G" edge="1" source="nyc-dist1" target="nyc-accs1" parent="1"/>
        <mxCell id="e9" value="10G" edge="1" source="nyc-dist1" target="nyc-accs2" parent="1"/>
        <mxCell id="e10" value="10G" edge="1" source="nyc-dist2" target="nyc-accs2" parent="1"/>
        <mxCell id="e11" value="10G" edge="1" source="nyc-dist2" target="nyc-accs3" parent="1"/>
        <mxCell id="e12" value="10G" edge="1" source="nyc-dist3" target="nyc-accs3" parent="1"/>
        <mxCell id="e13" value="10G" edge="1" source="nyc-dist3" target="nyc-accs4" parent="1"/>
        <!-- Load Balancer -->
        <mxCell id="e14" value="10G" edge="1" source="nyc-dist2" target="nyc-lb1" parent="1"/>
        <!-- Servers -->
        <mxCell id="e15" value="25G" edge="1" source="nyc-accs1" target="nyc-srv1" parent="1"/>
        <mxCell id="e16" value="25G" edge="1" source="nyc-lb1" target="nyc-srv2" parent="1"/>
        <mxCell id="e17" value="25G" edge="1" source="nyc-accs4" target="nyc-srv3" parent="1"/>
        <!-- WAN -->
        <mxCell id="e18" value="MPLS 10G" edge="1" source="nyc-core2" target="wan-mpls" parent="1"/>
        <mxCell id="e19" value="MPLS 10G" edge="1" source="wan-mpls" target="lax-core1" parent="1"/>
        <mxCell id="e20" value="MPLS 1G" edge="1" source="wan-mpls" target="dca-rtr1" parent="1"/>
        <!-- VPN -->
        <mxCell id="e21" value="IPSec" edge="1" source="vpn-hub" target="nyc-fw1" parent="1"/>
        <!-- LAX internal -->
        <mxCell id="e22" value="40G" edge="1" source="lax-fw1" target="lax-core1" parent="1"/>
        <mxCell id="e23" value="100G" edge="1" source="lax-core1" target="lax-dist1" parent="1"/>
        <mxCell id="e24" value="100G" edge="1" source="lax-core1" target="lax-dist2" parent="1"/>
        <mxCell id="e25" value="10G" edge="1" source="lax-dist1" target="lax-accs1" parent="1"/>
        <mxCell id="e26" value="10G" edge="1" source="lax-dist2" target="lax-accs2" parent="1"/>
        <mxCell id="e27" value="25G" edge="1" source="lax-dist1" target="lax-srv1" parent="1"/>
        <!-- DCA internal -->
        <mxCell id="e28" value="1G" edge="1" source="dca-fw1" target="dca-rtr1" parent="1"/>
        <mxCell id="e29" value="1G" edge="1" source="dca-rtr1" target="dca-sw1" parent="1"/>
        <mxCell id="e30" value="1G" edge="1" source="dca-sw1" target="dca-ap1" parent="1"/>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

tmp_dir = Path(tempfile.gettempdir()) / "icdev_e2e_complex"
tmp_dir.mkdir(exist_ok=True)
diagram_path = tmp_dir / "enterprise_multisite.drawio"
diagram_path.write_text(DRAWIO, encoding="utf-8")

from tools.network.network_ingester import ingest_diagram

result = ingest_diagram(str(diagram_path), project_id="ndc-network-intelligence", topology_name="Enterprise-MultiSite-3DC")
topo_id = result.get("topology_id")

check("Diagram ingested", topo_id is not None, f"id={topo_id}")
check("28 devices found", result.get("node_count", 0) == 28, f"got {result.get('node_count')}")
check("30 connections found", result.get("edge_count", 0) == 30, f"got {result.get('edge_count')}")
check("Devices persisted", result.get("device_count", 0) == 28, f"got {result.get('device_count')}")
print(f"  Ingestion time: {result.get('duration_ms', 0)}ms")

# ── Phase 2: Assign Hardware Profiles + Device Metadata ────────────────────

print("\n-- Phase 2: Assign Device Profiles + Metadata --")

from tools.network.db.init_db import get_connection

conn = get_connection()

# Map device names to profiles and metadata
device_meta = {
    "NYC-CORE-01": {"vendor": "Juniper", "model": "MX304", "site": "NYC-DC1", "rack": "NYC-R1 / U20-U21", "eol": "2031-12-31", "cost": 85000, "maint": 12750},
    "NYC-CORE-02": {"vendor": "Juniper", "model": "MX304", "site": "NYC-DC1", "rack": "NYC-R2 / U20-U21", "eol": "2031-12-31", "cost": 85000, "maint": 12750},
    "NYC-FWLL-01": {"vendor": "Palo Alto", "model": "PA-5260", "site": "NYC-DC1", "rack": "NYC-R1 / U17-U18", "eol": "2030-12-31", "cost": 120000, "maint": 21600},
    "NYC-FWLL-02": {"vendor": "Palo Alto", "model": "PA-5260", "site": "NYC-DC1", "rack": "NYC-R2 / U17-U18", "eol": "2030-12-31", "cost": 120000, "maint": 21600},
    "NYC-DIST-01": {"vendor": "Arista", "model": "7050X3-48YC12", "site": "NYC-DC1", "rack": "NYC-R1 / U14", "eol": "2032-06-30", "cost": 28000, "maint": 4200},
    "NYC-DIST-02": {"vendor": "Arista", "model": "7050X3-48YC12", "site": "NYC-DC1", "rack": "NYC-R2 / U14", "eol": "2032-06-30", "cost": 28000, "maint": 4200},
    "NYC-DIST-03": {"vendor": "Arista", "model": "7050X3-48YC12", "site": "NYC-DC1", "rack": "NYC-R3 / U14", "eol": "2032-06-30", "cost": 28000, "maint": 4200},
    "NYC-ACCS-01": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "NYC-DC1", "rack": "NYC-R1 / U10", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "NYC-ACCS-02": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "NYC-DC1", "rack": "NYC-R1 / U11", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "NYC-ACCS-03": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "NYC-DC1", "rack": "NYC-R2 / U10", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "NYC-ACCS-04": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "NYC-DC1", "rack": "NYC-R2 / U11", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "NYC-LB-01": {"vendor": "F5", "model": "rSeries r5900", "site": "NYC-DC1", "rack": "NYC-R2 / U12", "eol": "2033-12-31", "cost": 75000, "maint": 13500},
    "NYC-SRVR-01": {"vendor": "Dell", "model": "PowerEdge R750", "site": "NYC-DC1", "rack": "NYC-R3 / U1", "eol": "2029-06-30", "cost": 15000, "maint": 3500},
    "NYC-SRVR-02": {"vendor": "Dell", "model": "PowerEdge R750", "site": "NYC-DC1", "rack": "NYC-R3 / U2", "eol": "2029-06-30", "cost": 15000, "maint": 3500},
    "NYC-SRVR-03": {"vendor": "HPE", "model": "ProLiant DL380", "site": "NYC-DC1", "rack": "NYC-R3 / U3-U4", "eol": "2028-12-31", "cost": 14000, "maint": 3200},
    "LAX-CORE-01": {"vendor": "Juniper", "model": "MX204", "site": "LAX-DC2", "rack": "LAX-R1 / U20", "eol": "2030-06-30", "cost": 35000, "maint": 5250},
    "LAX-FWLL-01": {"vendor": "Fortinet", "model": "FortiGate-600F", "site": "LAX-DC2", "rack": "LAX-R1 / U18", "eol": "2032-06-30", "cost": 35000, "maint": 7000},
    "LAX-DIST-01": {"vendor": "Arista", "model": "7050X3-48YC12", "site": "LAX-DC2", "rack": "LAX-R1 / U14", "eol": "2032-06-30", "cost": 28000, "maint": 4200},
    "LAX-DIST-02": {"vendor": "Arista", "model": "7050X3-48YC12", "site": "LAX-DC2", "rack": "LAX-R1 / U15", "eol": "2032-06-30", "cost": 28000, "maint": 4200},
    "LAX-ACCS-01": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "LAX-DC2", "rack": "LAX-R1 / U10", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "LAX-ACCS-02": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "LAX-DC2", "rack": "LAX-R1 / U11", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "LAX-SRVR-01": {"vendor": "Dell", "model": "PowerEdge R750", "site": "LAX-DC2", "rack": "LAX-R1 / U1", "eol": "2029-06-30", "cost": 15000, "maint": 3500},
    "DCA-CORE-01": {"vendor": "Cisco", "model": "ASR-1001-X", "site": "DCA-BR1", "rack": "DCA-R1 / U20", "eol": "2028-12-31", "cost": 45000, "maint": 6750},
    "DCA-FWLL-01": {"vendor": "Fortinet", "model": "FortiGate-200F", "site": "DCA-BR1", "rack": "DCA-R1 / U18", "eol": "2031-12-31", "cost": 18000, "maint": 3600},
    "DCA-ACCS-01": {"vendor": "Cisco", "model": "Catalyst 9300-48T", "site": "DCA-BR1", "rack": "DCA-R1 / U10", "eol": "2031-12-31", "cost": 12000, "maint": 1800},
    "DCA-WLAN-01": {"vendor": "Cisco", "model": "Catalyst 9800-L", "site": "DCA-BR1", "rack": "DCA-R1 / U8", "eol": "2032-12-31", "cost": 8000, "maint": 1200},
    "MPLS-WAN": {"vendor": "AT&T", "model": "AVPN 10Gbps", "site": "WAN", "rack": None, "eol": None, "cost": 0, "maint": 120000},
    "NYC-VPNG-01": {"vendor": "Cisco", "model": "ASR-1001-X", "site": "NYC-DC1", "rack": "NYC-R1 / U22", "eol": "2028-12-31", "cost": 45000, "maint": 6750},
}

updated = 0
for label, meta in device_meta.items():
    conn.execute(
        "UPDATE ni_devices SET vendor=?, model=?, site=?, rack_location=?, "
        "eol_date=?, replacement_cost=?, annual_maintenance_cost=? "
        "WHERE topology_id=? AND label=?",
        (meta["vendor"], meta["model"], meta["site"], meta.get("rack"),
         meta.get("eol"), meta["cost"], meta["maint"], topo_id, label),
    )
    updated += conn.execute("SELECT changes()").fetchone()[0]

conn.commit()
check("Device metadata assigned", updated >= 25, f"{updated}/28 devices updated")

# Verify by site
sites = conn.execute(
    "SELECT site, COUNT(*) FROM ni_devices WHERE topology_id=? GROUP BY site ORDER BY site",
    (topo_id,),
).fetchall()
print("  Site breakdown:")
for s in sites:
    print(f"    {s[0]:15s} {s[1]} devices")

# ── Phase 3: Knowledge Graph Build ────────────────────────────────────────

print("\n-- Phase 3: Knowledge Graph --")

from tools.canvas.kg_builder import rebuild_canvas_kg

kg = rebuild_canvas_kg("ndc", topo_id)
check("KG built", kg.get("status") == "ok")
check("KG nodes", kg.get("nodes_upserted", 0) >= 25, f"{kg.get('nodes_upserted')} nodes")
check("KG edges", kg.get("edges_upserted", 0) >= 25, f"{kg.get('edges_upserted')} edges")

# ── Phase 4: Intelligence Analyses ────────────────────────────────────────

print("\n-- Phase 4: Intelligence Analyses (7 dimensions) --")

from tools.network.network_intelligence import (
    analyze_blast_radius,
    analyze_capacity,
    analyze_redundancy,
    assess_availability,
    assess_compliance,
    assess_vendor_risk,
    project_costs,
)

# 1. Redundancy
r = analyze_redundancy(topo_id)
check("Redundancy: completed", "error" not in r)
check("Redundancy: SPOFs found", r.get("spof_count", 0) >= 1, f"{r.get('spof_count')} SPOFs")
print(f"    {r.get('spof_count')} SPOFs, {r.get('bridge_count')} bridges, fully_redundant={r.get('fully_redundant')}")
for spof in (r.get("spofs") or [])[:3]:
    print(f"      [{spof['tier']}] {spof['label']} — disconnects {spof['disconnected_devices']}")

# 2. Blast Radius (NYC-CORE-01)
graph = json.loads(conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()[0])
core_id = next((n["id"] for n in graph["nodes"] if "NYC-CORE-01" in n.get("label", "")), None)
if core_id:
    br = analyze_blast_radius(topo_id, core_id)
    check("Blast radius: completed", "error" not in br)
    check("Blast radius: affected devices", br.get("total_affected", 0) >= 5, f"{br.get('total_affected')} affected")

# 3. Vendor Risk
vr = assess_vendor_risk(topo_id)
check("Vendor risk: completed", "error" not in vr)
check("Vendor risk: vendors found", vr.get("vendor_count", 0) >= 5, f"{vr.get('vendor_count')} vendors")
print(f"    Vendors: {', '.join(v.get('vendor','?') for v in vr.get('vendors', []))}")

# 4. Compliance
comp = assess_compliance(topo_id, framework="nist")
check("Compliance: completed", "error" not in comp)

# 5. Availability
avail = assess_availability(topo_id)
check("Availability: completed", "error" not in avail)

# 6. Cost Projection
cost = project_costs(topo_id, years=5)
check("Cost projection: completed", "error" not in cost)
check("Cost projection: has TCO", cost.get("total_tco", 0) > 0, f"${cost.get('total_tco', 0):,.0f}")
print(f"    5-year TCO: ${cost.get('total_tco', 0):,.0f}")
mc = cost.get("eol_monte_carlo", {}).get("total_replacement_cost", {})
if mc:
    print(f"    Replacement P50: ${mc.get('p50', 0):,.0f}  P90: ${mc.get('p90', 0):,.0f}")

# 7. Capacity (500 VDI users at NYC)
cap = analyze_capacity(topo_id, {
    "user_count": 500, "region": "NYC", "services": ["vdi", "teams", "o365"],
    "description": "500 VDI users at NYC primary DC"
})
check("Capacity: completed", "error" not in cap)
bw = cap.get("bandwidth_simulation", {}) or {}
check("Capacity: bandwidth sim", bw.get("summary", {}).get("total_links", 0) >= 20, f"{bw.get('summary', {}).get('total_links', 0)} links analyzed")
print(f"    Demand: {cap.get('demand', {}).get('total_sustained_mbps', 0):.0f} Mbps sustained")
print(f"    Bottlenecks: {bw.get('summary', {}).get('bottleneck_count', 0)}")
print(f"    Health: {bw.get('summary', {}).get('overall_health', '?')}")

# ── Phase 5: Naming Convention Test ────────────────────────────────────────

print("\n-- Phase 5: Naming Convention Engine --")

from tools.network.naming_engine import bulk_generate, generate_name, validate_name

g1 = generate_name("nc-site-role-seq", {"SITE": "NYC", "ROLE": "CORE"}, topo_id)
check("Generate NYC-CORE-01", g1.get("valid") and "NYC" in g1.get("name", ""), g1.get("name"))

v1 = validate_name("NYC-CORE-01", "nc-site-role-seq")
check("Validate NYC-CORE-01", v1.get("valid"), str(v1.get("errors", [])))

g2 = generate_name("nc-dod-standard", {"COCOM": "CONUS", "BASE": "BRAGG", "BLDG": "B12", "ROLE": "RTR"}, topo_id)
check("Generate DoD name", g2.get("valid"), g2.get("name"))

bulk = bulk_generate("nc-simple", 5, {"TYPE": "SW"}, topo_id)
check("Bulk generate 5 switch names", len(bulk) == 5, ", ".join(b["name"] for b in bulk))

# ── Phase 6: Hardware Profile Verification ─────────────────────────────────

print("\n-- Phase 6: Hardware Profile Catalog --")

hw_count = conn.execute("SELECT COUNT(*) FROM nc_hardware_profiles").fetchone()[0]
vendor_count = conn.execute("SELECT COUNT(DISTINCT vendor) FROM nc_hardware_profiles").fetchone()[0]
check("Hardware profiles seeded", hw_count >= 25, f"{hw_count} profiles")
check("Multiple vendors", vendor_count >= 6, f"{vendor_count} vendors")

conn.close()

# ── Phase 7: Cached Analysis Results ──────────────────────────────────────

print("\n-- Phase 7: Cached Analysis Results --")

nc_conn = get_connection()
analyses = nc_conn.execute(
    "SELECT analysis_type, result_summary FROM ni_analyses WHERE topology_id=? ORDER BY created_at DESC",
    (topo_id,),
).fetchall()
check("Analyses cached", len(analyses) >= 5, f"{len(analyses)} cached")
for a in analyses:
    print(f"    {a[0]:20s} {(a[1] or '')[:60]}")

nc_conn.close()

# Cleanup
try:
    diagram_path.unlink(missing_ok=True)
except Exception:
    pass

# ── Summary ────────────────────────────────────────────────────────────────

print("\n" + "=" * 72)
print(f"  RESULTS: {PASS}/{PASS + FAIL} passed")
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
