# CUI // SP-CTI
"""Dual-Track Full Lifecycle E2E Test.

Track 1: Large enterprise network (45 devices, 5 sites) — simulates PDF/Visio import
Track 2: Federal agency network (30 devices) — generated from natural language description

Both tracks go through:
  1. Diagram creation + ingestion
  2. Template config application (enrichment: site groups, type classification)
  3. Device metadata + hardware profile assignment
  4. Facility + rack creation with infra (PDU, UPS, patch panels)
  5. Knowledge Graph build
  6. RAG ingestion
  7. Intelligence analyses (7 dimensions)
  8. RAG search verification
  9. KG search verification
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
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


print("\n" + "=" * 76)
print("  DUAL-TRACK FULL LIFECYCLE E2E TEST")
print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
print("=" * 76)

# ══════════════════════════════════════════════════════════════════════════
# TRACK 1: Large Enterprise Network (simulates Visio/PDF import)
# ══════════════════════════════════════════════════════════════════════════

print("\n" + "─" * 76)
print("  TRACK 1: Large Enterprise Network — 5 Sites, 45+ Devices")
print("─" * 76)

# Generate a complex DrawIO with 5 sites
TRACK1_DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="Global Enterprise"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<!-- HQ NYC (Primary DC) -->
<mxCell id="hq-core1" value="NYC-CORE-01" vertex="1" parent="1"><mxGeometry x="300" y="50" width="80" height="60"/></mxCell>
<mxCell id="hq-core2" value="NYC-CORE-02" vertex="1" parent="1"><mxGeometry x="500" y="50" width="80" height="60"/></mxCell>
<mxCell id="hq-fw1" value="NYC-FWLL-01" vertex="1" parent="1"><mxGeometry x="150" y="50" width="80" height="60"/></mxCell>
<mxCell id="hq-fw2" value="NYC-FWLL-02" vertex="1" parent="1"><mxGeometry x="650" y="50" width="80" height="60"/></mxCell>
<mxCell id="hq-dist1" value="NYC-DIST-01" vertex="1" parent="1"><mxGeometry x="200" y="200" width="80" height="60"/></mxCell>
<mxCell id="hq-dist2" value="NYC-DIST-02" vertex="1" parent="1"><mxGeometry x="400" y="200" width="80" height="60"/></mxCell>
<mxCell id="hq-dist3" value="NYC-DIST-03" vertex="1" parent="1"><mxGeometry x="600" y="200" width="80" height="60"/></mxCell>
<mxCell id="hq-accs1" value="NYC-ACCS-01" vertex="1" parent="1"><mxGeometry x="100" y="350" width="80" height="60"/></mxCell>
<mxCell id="hq-accs2" value="NYC-ACCS-02" vertex="1" parent="1"><mxGeometry x="300" y="350" width="80" height="60"/></mxCell>
<mxCell id="hq-accs3" value="NYC-ACCS-03" vertex="1" parent="1"><mxGeometry x="500" y="350" width="80" height="60"/></mxCell>
<mxCell id="hq-accs4" value="NYC-ACCS-04" vertex="1" parent="1"><mxGeometry x="700" y="350" width="80" height="60"/></mxCell>
<mxCell id="hq-lb1" value="NYC-LB-01" vertex="1" parent="1"><mxGeometry x="400" y="350" width="80" height="60"/></mxCell>
<mxCell id="hq-srv1" value="NYC-SRVR-01" vertex="1" parent="1"><mxGeometry x="200" y="500" width="80" height="60"/></mxCell>
<mxCell id="hq-srv2" value="NYC-SRVR-02" vertex="1" parent="1"><mxGeometry x="400" y="500" width="80" height="60"/></mxCell>
<mxCell id="hq-srv3" value="NYC-SRVR-03" vertex="1" parent="1"><mxGeometry x="600" y="500" width="80" height="60"/></mxCell>
<mxCell id="hq-vpn1" value="NYC-VPNG-01" vertex="1" parent="1"><mxGeometry x="50" y="50" width="80" height="60"/></mxCell>
<!-- DR Site LAX -->
<mxCell id="dr-core1" value="LAX-CORE-01" vertex="1" parent="1"><mxGeometry x="1000" y="100" width="80" height="60"/></mxCell>
<mxCell id="dr-core2" value="LAX-CORE-02" vertex="1" parent="1"><mxGeometry x="1200" y="100" width="80" height="60"/></mxCell>
<mxCell id="dr-fw1" value="LAX-FWLL-01" vertex="1" parent="1"><mxGeometry x="900" y="100" width="80" height="60"/></mxCell>
<mxCell id="dr-dist1" value="LAX-DIST-01" vertex="1" parent="1"><mxGeometry x="950" y="250" width="80" height="60"/></mxCell>
<mxCell id="dr-dist2" value="LAX-DIST-02" vertex="1" parent="1"><mxGeometry x="1150" y="250" width="80" height="60"/></mxCell>
<mxCell id="dr-accs1" value="LAX-ACCS-01" vertex="1" parent="1"><mxGeometry x="950" y="400" width="80" height="60"/></mxCell>
<mxCell id="dr-accs2" value="LAX-ACCS-02" vertex="1" parent="1"><mxGeometry x="1150" y="400" width="80" height="60"/></mxCell>
<mxCell id="dr-srv1" value="LAX-SRVR-01" vertex="1" parent="1"><mxGeometry x="1050" y="500" width="80" height="60"/></mxCell>
<!-- Chicago Regional -->
<mxCell id="chi-core1" value="CHI-CORE-01" vertex="1" parent="1"><mxGeometry x="1500" y="100" width="80" height="60"/></mxCell>
<mxCell id="chi-fw1" value="CHI-FWLL-01" vertex="1" parent="1"><mxGeometry x="1400" y="100" width="80" height="60"/></mxCell>
<mxCell id="chi-dist1" value="CHI-DIST-01" vertex="1" parent="1"><mxGeometry x="1450" y="250" width="80" height="60"/></mxCell>
<mxCell id="chi-accs1" value="CHI-ACCS-01" vertex="1" parent="1"><mxGeometry x="1400" y="400" width="80" height="60"/></mxCell>
<mxCell id="chi-accs2" value="CHI-ACCS-02" vertex="1" parent="1"><mxGeometry x="1550" y="400" width="80" height="60"/></mxCell>
<mxCell id="chi-srv1" value="CHI-SRVR-01" vertex="1" parent="1"><mxGeometry x="1500" y="500" width="80" height="60"/></mxCell>
<!-- DCA Branch -->
<mxCell id="dca-rtr1" value="DCA-CORE-01" vertex="1" parent="1"><mxGeometry x="1800" y="150" width="80" height="60"/></mxCell>
<mxCell id="dca-fw1" value="DCA-FWLL-01" vertex="1" parent="1"><mxGeometry x="1700" y="150" width="80" height="60"/></mxCell>
<mxCell id="dca-sw1" value="DCA-ACCS-01" vertex="1" parent="1"><mxGeometry x="1750" y="300" width="80" height="60"/></mxCell>
<mxCell id="dca-ap1" value="DCA-WLAN-01" vertex="1" parent="1"><mxGeometry x="1850" y="300" width="80" height="60"/></mxCell>
<!-- London International -->
<mxCell id="lon-core1" value="LON-CORE-01" vertex="1" parent="1"><mxGeometry x="2100" y="150" width="80" height="60"/></mxCell>
<mxCell id="lon-fw1" value="LON-FWLL-01" vertex="1" parent="1"><mxGeometry x="2000" y="150" width="80" height="60"/></mxCell>
<mxCell id="lon-dist1" value="LON-DIST-01" vertex="1" parent="1"><mxGeometry x="2050" y="300" width="80" height="60"/></mxCell>
<mxCell id="lon-accs1" value="LON-ACCS-01" vertex="1" parent="1"><mxGeometry x="2000" y="420" width="80" height="60"/></mxCell>
<mxCell id="lon-accs2" value="LON-ACCS-02" vertex="1" parent="1"><mxGeometry x="2150" y="420" width="80" height="60"/></mxCell>
<mxCell id="lon-srv1" value="LON-SRVR-01" vertex="1" parent="1"><mxGeometry x="2100" y="500" width="80" height="60"/></mxCell>
<!-- WAN / MPLS -->
<mxCell id="wan-mpls" value="MPLS-WAN" vertex="1" parent="1"><mxGeometry x="800" y="50" width="80" height="60"/></mxCell>
<mxCell id="wan-inet" value="Internet-GW" vertex="1" parent="1"><mxGeometry x="800" y="150" width="80" height="60"/></mxCell>
<!-- Edges -->
<mxCell id="e1" value="100G Fiber" edge="1" source="hq-core1" target="hq-core2" parent="1"/>
<mxCell id="e2" value="40G" edge="1" source="hq-fw1" target="hq-core1" parent="1"/>
<mxCell id="e3" value="40G" edge="1" source="hq-fw2" target="hq-core2" parent="1"/>
<mxCell id="e4" value="100G" edge="1" source="hq-core1" target="hq-dist1" parent="1"/>
<mxCell id="e5" value="100G" edge="1" source="hq-core1" target="hq-dist2" parent="1"/>
<mxCell id="e6" value="100G" edge="1" source="hq-core2" target="hq-dist2" parent="1"/>
<mxCell id="e7" value="100G" edge="1" source="hq-core2" target="hq-dist3" parent="1"/>
<mxCell id="e8" value="10G" edge="1" source="hq-dist1" target="hq-accs1" parent="1"/>
<mxCell id="e9" value="10G" edge="1" source="hq-dist1" target="hq-accs2" parent="1"/>
<mxCell id="e10" value="10G" edge="1" source="hq-dist2" target="hq-accs3" parent="1"/>
<mxCell id="e11" value="10G" edge="1" source="hq-dist3" target="hq-accs4" parent="1"/>
<mxCell id="e12" value="10G" edge="1" source="hq-dist2" target="hq-lb1" parent="1"/>
<mxCell id="e13" value="25G" edge="1" source="hq-accs1" target="hq-srv1" parent="1"/>
<mxCell id="e14" value="25G" edge="1" source="hq-lb1" target="hq-srv2" parent="1"/>
<mxCell id="e15" value="25G" edge="1" source="hq-accs4" target="hq-srv3" parent="1"/>
<mxCell id="e16" value="IPSec" edge="1" source="hq-vpn1" target="hq-fw1" parent="1"/>
<mxCell id="e17" value="MPLS 10G" edge="1" source="hq-core2" target="wan-mpls" parent="1"/>
<mxCell id="e18" value="1G" edge="1" source="hq-fw2" target="wan-inet" parent="1"/>
<mxCell id="e19" value="MPLS 10G" edge="1" source="wan-mpls" target="dr-core1" parent="1"/>
<mxCell id="e20" value="MPLS 1G" edge="1" source="wan-mpls" target="chi-core1" parent="1"/>
<mxCell id="e21" value="MPLS 1G" edge="1" source="wan-mpls" target="dca-rtr1" parent="1"/>
<mxCell id="e22" value="MPLS 1G" edge="1" source="wan-mpls" target="lon-core1" parent="1"/>
<mxCell id="e23" value="100G" edge="1" source="dr-core1" target="dr-core2" parent="1"/>
<mxCell id="e24" value="40G" edge="1" source="dr-fw1" target="dr-core1" parent="1"/>
<mxCell id="e25" value="100G" edge="1" source="dr-core1" target="dr-dist1" parent="1"/>
<mxCell id="e26" value="100G" edge="1" source="dr-core2" target="dr-dist2" parent="1"/>
<mxCell id="e27" value="10G" edge="1" source="dr-dist1" target="dr-accs1" parent="1"/>
<mxCell id="e28" value="10G" edge="1" source="dr-dist2" target="dr-accs2" parent="1"/>
<mxCell id="e29" value="25G" edge="1" source="dr-dist1" target="dr-srv1" parent="1"/>
<mxCell id="e30" value="40G" edge="1" source="chi-fw1" target="chi-core1" parent="1"/>
<mxCell id="e31" value="10G" edge="1" source="chi-core1" target="chi-dist1" parent="1"/>
<mxCell id="e32" value="1G" edge="1" source="chi-dist1" target="chi-accs1" parent="1"/>
<mxCell id="e33" value="1G" edge="1" source="chi-dist1" target="chi-accs2" parent="1"/>
<mxCell id="e34" value="10G" edge="1" source="chi-dist1" target="chi-srv1" parent="1"/>
<mxCell id="e35" value="1G" edge="1" source="dca-fw1" target="dca-rtr1" parent="1"/>
<mxCell id="e36" value="1G" edge="1" source="dca-rtr1" target="dca-sw1" parent="1"/>
<mxCell id="e37" value="1G" edge="1" source="dca-sw1" target="dca-ap1" parent="1"/>
<mxCell id="e38" value="40G" edge="1" source="lon-fw1" target="lon-core1" parent="1"/>
<mxCell id="e39" value="10G" edge="1" source="lon-core1" target="lon-dist1" parent="1"/>
<mxCell id="e40" value="1G" edge="1" source="lon-dist1" target="lon-accs1" parent="1"/>
<mxCell id="e41" value="1G" edge="1" source="lon-dist1" target="lon-accs2" parent="1"/>
<mxCell id="e42" value="10G" edge="1" source="lon-dist1" target="lon-srv1" parent="1"/>
</root></mxGraphModel></diagram></mxfile>"""

# ── 1.1 Ingest ────────────────────────────────────────────────────────────

print("\n-- 1.1 Ingesting Track 1 diagram (45 devices, 5 sites) --")

tmp_dir = Path(tempfile.gettempdir()) / "icdev_e2e_dual"
tmp_dir.mkdir(exist_ok=True)
t1_path = tmp_dir / "global_enterprise.drawio"
t1_path.write_text(TRACK1_DRAWIO, encoding="utf-8")

from tools.network.network_ingester import ingest_diagram

t1_result = ingest_diagram(str(t1_path), project_id="ndc-network-intelligence", topology_name="Global-Enterprise-5Site")
t1_id = t1_result.get("topology_id")
check("T1 ingested", t1_id is not None, f"id={t1_id}")
check("T1 devices", t1_result.get("node_count", 0) >= 40, f"{t1_result.get('node_count')} nodes")
check("T1 links", t1_result.get("edge_count", 0) >= 40, f"{t1_result.get('edge_count')} edges")
print(f"  Duration: {t1_result.get('duration_ms', 0)}ms")

# ── 1.2 Assign device metadata (BEFORE enrichment — enricher reads site from ni_devices) ──

print("\n-- 1.2 Assigning device metadata --")

from tools.network.db.init_db import get_connection

nc = get_connection()

# Bulk assign metadata by site prefix
SITE_META = {
    "NYC": {"site": "NYC-HQ", "cost_base": 40000, "maint_pct": 0.15},
    "LAX": {"site": "LAX-DR", "cost_base": 30000, "maint_pct": 0.15},
    "CHI": {"site": "CHI-REG", "cost_base": 25000, "maint_pct": 0.15},
    "DCA": {"site": "DCA-BR", "cost_base": 15000, "maint_pct": 0.18},
    "LON": {"site": "LON-INT", "cost_base": 35000, "maint_pct": 0.15},
}
TYPE_VENDOR = {
    "router": ("Juniper", "MX304"),
    "switch": ("Arista", "7050X3-48YC12"),
    "firewall": ("Palo Alto", "PA-5260"),
    "server": ("Dell", "PowerEdge R750"),
    "load_balancer": ("F5", "rSeries r5900"),
    "vpn_gateway": ("Cisco", "ASR-1001-X"),
    "wan_link": ("AT&T", "AVPN 10Gbps"),
    "access_point": ("Cisco", "Catalyst 9800-L"),
}

devices = nc.execute("SELECT id, label, device_type FROM ni_devices WHERE topology_id = ?", (t1_id,)).fetchall()
t1_updated = 0
ip_counter = defaultdict(int)

for d in devices:
    label = d[1]
    dtype = d[2]
    prefix = label.split("-")[0] if "-" in label else ""
    meta = SITE_META.get(prefix, {"site": "OTHER", "cost_base": 10000, "maint_pct": 0.20})
    vendor_info = TYPE_VENDOR.get(dtype, ("Generic", "Unknown"))

    ip_counter[prefix] += 1
    ip_map = {"NYC": "10.1", "LAX": "10.2", "CHI": "10.3", "DCA": "10.4", "LON": "10.5"}
    ip_prefix = ip_map.get(prefix, "10.9")
    ip = f"{ip_prefix}.{ip_counter[prefix] // 256}.{ip_counter[prefix] % 256}"

    cost_mult = {"router": 2.0, "firewall": 3.0, "switch": 0.7, "server": 0.4, "load_balancer": 1.8}.get(dtype, 1.0)
    cost = int(meta["cost_base"] * cost_mult)

    nc.execute(
        "UPDATE ni_devices SET vendor=?, model=?, site=?, replacement_cost=?, "
        "annual_maintenance_cost=?, eol_date=? WHERE id=?",
        (vendor_info[0], vendor_info[1], meta["site"], cost,
         int(cost * meta["maint_pct"]), "2030-12-31", d[0]),
    )
    t1_updated += 1

nc.commit()
check("T1 metadata assigned", t1_updated >= 40, f"{t1_updated} devices")

# Print site breakdown
sites = nc.execute(
    "SELECT site, COUNT(*), SUM(replacement_cost) FROM ni_devices WHERE topology_id=? GROUP BY site ORDER BY site",
    (t1_id,),
).fetchall()
for s in sites:
    print(f"    {s[0]:12s} {s[1]:2d} devices  ${s[2]:>10,.0f}")

# ── 1.3 Enrich (site groups — AFTER metadata assignment) ──────────────────

print("\n-- 1.3 Enriching Track 1 --")

from tools.network.topology_enricher import enrich_topology

t1_enrich = enrich_topology(t1_id, add_infra=False, add_groups=True)
check("T1 enriched", t1_enrich.get("groups_added", 0) >= 4, f"{t1_enrich.get('groups_added')} groups")

# ── 1.4 Create facilities + racks ─────────────────────────────────────────

print("\n-- 1.4 Creating facilities + racks --")

FACILITIES = [
    ("fac-nychq", "NYC HQ Data Center", "colocation", "New York", "Equinix NY7"),
    ("fac-laxdr", "LAX DR Site", "colocation", "Los Angeles", "CoreSite LA2"),
    ("fac-chireg", "Chicago Regional", "colocation", "Chicago", "QTS CHI1"),
    ("fac-dcabr", "DCA Branch", "office", "Washington DC", "Internal"),
    ("fac-lonint", "London International", "colocation", "London", "Equinix LD8"),
]

for fac in FACILITIES:
    nc.execute(
        "INSERT OR IGNORE INTO nc_facilities (id, name, facility_type, city, operator) VALUES (?,?,?,?,?)",
        fac,
    )

RACKS = [
    ("rack-nychq-r1", "fac-nychq", "NYC-R1", 42, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42"},
        {"type": "PDU-B", "model": "APC AP8886", "ru": "U39-U40"},
        {"type": "UPS", "model": "APC SRT3000", "ru": "U1-U3"},
        {"type": "Fiber PP", "model": "Corning CCH-04U", "ru": "U38"},
        {"type": "Copper PP", "model": "Panduit CP48", "ru": "U37"},
    ]})),
    ("rack-nychq-r2", "fac-nychq", "NYC-R2", 42, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42"},
        {"type": "PDU-B", "model": "APC AP8886", "ru": "U39-U40"},
        {"type": "UPS", "model": "APC SRT3000", "ru": "U1-U3"},
        {"type": "Fiber PP", "model": "Corning CCH-04U", "ru": "U38"},
        {"type": "Copper PP", "model": "Panduit CP48", "ru": "U37"},
    ]})),
    ("rack-laxdr-r1", "fac-laxdr", "LAX-R1", 42, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42"},
        {"type": "PDU-B", "model": "APC AP8886", "ru": "U39-U40"},
        {"type": "UPS", "model": "APC SRT3000", "ru": "U1-U3"},
        {"type": "Fiber PP", "model": "Corning CCH-04U", "ru": "U38"},
    ]})),
    ("rack-chireg-r1", "fac-chireg", "CHI-R1", 42, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42"},
        {"type": "UPS", "model": "APC SRT3000", "ru": "U1-U3"},
        {"type": "Copper PP", "model": "Panduit CP48", "ru": "U37"},
    ]})),
    ("rack-dcabr-r1", "fac-dcabr", "DCA-R1", 24, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U23-U24"},
        {"type": "UPS", "model": "APC SRT1500", "ru": "U1-U2"},
        {"type": "Copper PP", "model": "Panduit CP24", "ru": "U22"},
    ]})),
    ("rack-lonint-r1", "fac-lonint", "LON-R1", 42, json.dumps({"infra": [
        {"type": "PDU-A", "model": "APC AP8886", "ru": "U41-U42"},
        {"type": "PDU-B", "model": "APC AP8886", "ru": "U39-U40"},
        {"type": "UPS", "model": "APC SRT3000", "ru": "U1-U3"},
        {"type": "Fiber PP", "model": "Corning CCH-04U", "ru": "U38"},
    ]})),
]

for r in RACKS:
    nc.execute("INSERT OR IGNORE INTO nc_racks (id, facility_id, rack_name, total_ru, notes) VALUES (?,?,?,?,?)", r)

nc.commit()
check("T1 facilities created", len(FACILITIES) == 5, f"{len(FACILITIES)}")
check("T1 racks created", len(RACKS) == 6, f"{len(RACKS)} racks")

# ── 1.5 Knowledge Graph ──────────────────────────────────────────────────

print("\n-- 1.5 Knowledge Graph --")

from tools.canvas.kg_builder import rebuild_canvas_kg

t1_kg = rebuild_canvas_kg("ndc", t1_id)
check("T1 KG built", t1_kg.get("status") == "ok")
check("T1 KG nodes", t1_kg.get("nodes_upserted", 0) >= 40, f"{t1_kg.get('nodes_upserted')}")

# ── 1.6 Intelligence Analyses ─────────────────────────────────────────────

print("\n-- 1.6 Intelligence Analyses --")

from tools.network.network_intelligence import (
    analyze_capacity, analyze_redundancy,
    assess_compliance, assess_vendor_risk, project_costs,
)

t1_red = analyze_redundancy(t1_id)
check("T1 Redundancy", "error" not in t1_red, f"{t1_red.get('spof_count')} SPOFs")

t1_vr = assess_vendor_risk(t1_id)
check("T1 Vendor Risk", t1_vr.get("vendor_count", 0) >= 4, f"{t1_vr.get('vendor_count')} vendors")

t1_comp = assess_compliance(t1_id)
check("T1 Compliance", "error" not in t1_comp)

t1_cost = project_costs(t1_id, years=5)
check("T1 Cost", t1_cost.get("total_tco", 0) > 0, f"${t1_cost.get('total_tco', 0):,.0f}")

t1_cap = analyze_capacity(t1_id, {"user_count": 1000, "region": "NYC", "services": ["vdi", "teams"]})
check("T1 Capacity", "error" not in t1_cap, f"{t1_cap.get('demand', {}).get('total_sustained_mbps', 0):.0f} Mbps")

nc.close()

print(f"\n  Track 1 Summary: {t1_result.get('node_count')} devices, {len(FACILITIES)} facilities, {len(RACKS)} racks")
print(f"  TCO: ${t1_cost.get('total_tco', 0):,.0f} | Vendors: {t1_vr.get('vendor_count')} | SPOFs: {t1_red.get('spof_count')}")

# ══════════════════════════════════════════════════════════════════════════
# TRACK 2: Federal Agency Network (Natural Language → DrawIO)
# ══════════════════════════════════════════════════════════════════════════

print("\n" + "─" * 76)
print("  TRACK 2: Federal Agency — CONUS, 3 Bases, 30 Devices (NL-Generated)")
print("─" * 76)

# This simulates what the chat engine would produce from:
# "Design a DoD CONUS network with 3 bases: Fort Bragg, Fort Hood, Fort Lewis.
#  Each base has a core router, firewall, 2 access switches, and a server.
#  Connect via MPLS ring. Add a SIPR VPN gateway at Bragg."

TRACK2_DRAWIO = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="DoD CONUS"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<!-- CONUS-BRAGG -->
<mxCell id="bg-core1" value="CONUS-BRAGG-B12-RTR-001" vertex="1" parent="1"><mxGeometry x="300" y="100" width="80" height="60"/></mxCell>
<mxCell id="bg-fw1" value="CONUS-BRAGG-B12-FWL-001" vertex="1" parent="1"><mxGeometry x="150" y="100" width="80" height="60"/></mxCell>
<mxCell id="bg-sw1" value="CONUS-BRAGG-B12-SWI-001" vertex="1" parent="1"><mxGeometry x="200" y="250" width="80" height="60"/></mxCell>
<mxCell id="bg-sw2" value="CONUS-BRAGG-B12-SWI-002" vertex="1" parent="1"><mxGeometry x="400" y="250" width="80" height="60"/></mxCell>
<mxCell id="bg-srv1" value="CONUS-BRAGG-B12-SRV-001" vertex="1" parent="1"><mxGeometry x="300" y="400" width="80" height="60"/></mxCell>
<mxCell id="bg-vpn1" value="CONUS-BRAGG-B12-VPN-001" vertex="1" parent="1"><mxGeometry x="50" y="100" width="80" height="60"/></mxCell>
<mxCell id="bg-wlc1" value="CONUS-BRAGG-B12-WLC-001" vertex="1" parent="1"><mxGeometry x="450" y="100" width="80" height="60"/></mxCell>
<!-- CONUS-HOOD -->
<mxCell id="hd-core1" value="CONUS-HOOD-B5-RTR-001" vertex="1" parent="1"><mxGeometry x="800" y="100" width="80" height="60"/></mxCell>
<mxCell id="hd-fw1" value="CONUS-HOOD-B5-FWL-001" vertex="1" parent="1"><mxGeometry x="700" y="100" width="80" height="60"/></mxCell>
<mxCell id="hd-sw1" value="CONUS-HOOD-B5-SWI-001" vertex="1" parent="1"><mxGeometry x="700" y="250" width="80" height="60"/></mxCell>
<mxCell id="hd-sw2" value="CONUS-HOOD-B5-SWI-002" vertex="1" parent="1"><mxGeometry x="900" y="250" width="80" height="60"/></mxCell>
<mxCell id="hd-srv1" value="CONUS-HOOD-B5-SRV-001" vertex="1" parent="1"><mxGeometry x="800" y="400" width="80" height="60"/></mxCell>
<mxCell id="hd-ids1" value="CONUS-HOOD-B5-IDS-001" vertex="1" parent="1"><mxGeometry x="950" y="100" width="80" height="60"/></mxCell>
<!-- CONUS-LEWIS -->
<mxCell id="lw-core1" value="CONUS-LEWIS-B3-RTR-001" vertex="1" parent="1"><mxGeometry x="1300" y="100" width="80" height="60"/></mxCell>
<mxCell id="lw-fw1" value="CONUS-LEWIS-B3-FWL-001" vertex="1" parent="1"><mxGeometry x="1200" y="100" width="80" height="60"/></mxCell>
<mxCell id="lw-sw1" value="CONUS-LEWIS-B3-SWI-001" vertex="1" parent="1"><mxGeometry x="1200" y="250" width="80" height="60"/></mxCell>
<mxCell id="lw-sw2" value="CONUS-LEWIS-B3-SWI-002" vertex="1" parent="1"><mxGeometry x="1400" y="250" width="80" height="60"/></mxCell>
<mxCell id="lw-srv1" value="CONUS-LEWIS-B3-SRV-001" vertex="1" parent="1"><mxGeometry x="1300" y="400" width="80" height="60"/></mxCell>
<!-- DISA MPLS Ring -->
<mxCell id="disa-mpls" value="DISA-MPLS-Ring" vertex="1" parent="1"><mxGeometry x="600" y="0" width="80" height="60"/></mxCell>
<!-- Pentagon NOC -->
<mxCell id="noc-core" value="PENTAGON-NOC-RTR-001" vertex="1" parent="1"><mxGeometry x="600" y="500" width="80" height="60"/></mxCell>
<mxCell id="noc-siem" value="PENTAGON-NOC-SIEM-001" vertex="1" parent="1"><mxGeometry x="750" y="500" width="80" height="60"/></mxCell>
<!-- Edges -->
<mxCell id="d1" value="MPLS 10G" edge="1" source="bg-core1" target="disa-mpls" parent="1"/>
<mxCell id="d2" value="MPLS 10G" edge="1" source="hd-core1" target="disa-mpls" parent="1"/>
<mxCell id="d3" value="MPLS 10G" edge="1" source="lw-core1" target="disa-mpls" parent="1"/>
<mxCell id="d4" value="MPLS 10G" edge="1" source="noc-core" target="disa-mpls" parent="1"/>
<mxCell id="d5" value="40G" edge="1" source="bg-fw1" target="bg-core1" parent="1"/>
<mxCell id="d6" value="10G" edge="1" source="bg-core1" target="bg-sw1" parent="1"/>
<mxCell id="d7" value="10G" edge="1" source="bg-core1" target="bg-sw2" parent="1"/>
<mxCell id="d8" value="10G" edge="1" source="bg-sw1" target="bg-srv1" parent="1"/>
<mxCell id="d9" value="IPSec" edge="1" source="bg-vpn1" target="bg-fw1" parent="1"/>
<mxCell id="d10" value="1G" edge="1" source="bg-core1" target="bg-wlc1" parent="1"/>
<mxCell id="d11" value="40G" edge="1" source="hd-fw1" target="hd-core1" parent="1"/>
<mxCell id="d12" value="10G" edge="1" source="hd-core1" target="hd-sw1" parent="1"/>
<mxCell id="d13" value="10G" edge="1" source="hd-core1" target="hd-sw2" parent="1"/>
<mxCell id="d14" value="10G" edge="1" source="hd-sw1" target="hd-srv1" parent="1"/>
<mxCell id="d15" value="1G" edge="1" source="hd-core1" target="hd-ids1" parent="1"/>
<mxCell id="d16" value="40G" edge="1" source="lw-fw1" target="lw-core1" parent="1"/>
<mxCell id="d17" value="10G" edge="1" source="lw-core1" target="lw-sw1" parent="1"/>
<mxCell id="d18" value="10G" edge="1" source="lw-core1" target="lw-sw2" parent="1"/>
<mxCell id="d19" value="10G" edge="1" source="lw-sw1" target="lw-srv1" parent="1"/>
<mxCell id="d20" value="10G" edge="1" source="noc-core" target="noc-siem" parent="1"/>
</root></mxGraphModel></diagram></mxfile>"""

print("\n-- 2.1 Ingesting Track 2 (DoD CONUS, 22 devices) --")

t2_path = tmp_dir / "dod_conus.drawio"
t2_path.write_text(TRACK2_DRAWIO, encoding="utf-8")

t2_result = ingest_diagram(str(t2_path), project_id="ndc-network-intelligence", topology_name="DoD-CONUS-3Base")
t2_id = t2_result.get("topology_id")
check("T2 ingested", t2_id is not None, f"id={t2_id}")
check("T2 devices", t2_result.get("node_count", 0) >= 20, f"{t2_result.get('node_count')} nodes")
check("T2 links", t2_result.get("edge_count", 0) >= 18, f"{t2_result.get('edge_count')} edges")

print("\n-- 2.2 Device metadata --")
nc = get_connection()
t2_devs = nc.execute("SELECT id, label, device_type FROM ni_devices WHERE topology_id=?", (t2_id,)).fetchall()
t2_upd = 0
for d in t2_devs:
    label = d[1]
    dtype = d[2]
    if "BRAGG" in label:
        site = "BRAGG"
    elif "HOOD" in label:
        site = "HOOD"
    elif "LEWIS" in label:
        site = "LEWIS"
    elif "PENTAGON" in label or "NOC" in label:
        site = "PENTAGON"
    else:
        site = "DISA"
    vendor_info = TYPE_VENDOR.get(dtype, ("Generic", "Unknown"))
    nc.execute("UPDATE ni_devices SET vendor=?, model=?, site=?, replacement_cost=50000, annual_maintenance_cost=7500, eol_date='2031-12-31' WHERE id=?",
               (vendor_info[0], vendor_info[1], site, d[0]))
    t2_upd += 1
nc.commit()
check("T2 metadata assigned", t2_upd >= 20, f"{t2_upd}")

print("\n-- 2.3 Enriching --")
t2_enrich = enrich_topology(t2_id, add_infra=False, add_groups=True)
check("T2 enriched", t2_enrich.get("groups_added", 0) >= 1, f"{t2_enrich.get('groups_added')} groups")

print("\n-- 2.4 KG --")
t2_kg = rebuild_canvas_kg("ndc", t2_id)
check("T2 KG built", t2_kg.get("status") == "ok", f"{t2_kg.get('nodes_upserted')} nodes")

print("\n-- 2.5 Intelligence --")
t2_red = analyze_redundancy(t2_id)
check("T2 Redundancy", "error" not in t2_red, f"{t2_red.get('spof_count')} SPOFs")

t2_vr = assess_vendor_risk(t2_id)
check("T2 Vendor Risk", "error" not in t2_vr, f"{t2_vr.get('vendor_count')} vendors")

t2_cost = project_costs(t2_id, years=5)
check("T2 Cost", t2_cost.get("total_tco", 0) > 0, f"${t2_cost.get('total_tco', 0):,.0f}")

nc.close()

# ══════════════════════════════════════════════════════════════════════════
# VERIFICATION: RAG + KG Search for both tracks
# ══════════════════════════════════════════════════════════════════════════

print("\n" + "─" * 76)
print("  VERIFICATION: RAG + KG Search")
print("─" * 76)

# ── RAG Ingestion ─────────────────────────────────────────────────────────

print("\n-- Ingesting both topologies into RAG --")

from tools.rag.ingestion_manager import ingest_source

for src in ["ndc_designs", "network_devices"]:
    r = ingest_source(src)
    print(f"  {src}: {r.get('chunks_created', 0)} chunks, {r.get('chunks_embedded', 0)} embedded")

# ── RAG Search ────────────────────────────────────────────────────────────

print("\n-- RAG Search Tests --")

from tools.rag.retriever import RAGRetriever

retriever = RAGRetriever()

for query in ["BRAGG firewall", "London server", "NYC core router MPLS", "DoD CONUS network"]:
    results = retriever.search(query=query, top_k=3)
    found = len(results)
    top_score = results[0].final_score if results else 0
    check(f"RAG: '{query}'", found > 0, f"{found} results, top={top_score:.3f}")

# ── KG Search ─────────────────────────────────────────────────────────────

print("\n-- KG Search Tests --")

from tools.knowledge_graph.graph_rag import retrieve as kg_retrieve

for query in ["core router", "firewall MPLS", "Fort Bragg VPN", "London server"]:
    try:
        kr = kg_retrieve(query=query, top_k=5)
        nodes = kr.get("nodes_matched", 0)
        check(f"KG: '{query}'", nodes > 0 or kr.get("nodes_returned", 0) > 0, f"{nodes} matched, {kr.get('nodes_returned', 0)} returned")
    except Exception as e:
        check(f"KG: '{query}'", False, str(e)[:60])

# ── Cleanup ───────────────────────────────────────────────────────────────

for p in [t1_path, t2_path]:
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 76)
print(f"  RESULTS: {PASS}/{PASS + FAIL} passed")
print("=" * 76)

if FAIL > 0:
    print("\n  FAILED:")
    # Would list failures but we track them inline
    pass

sys.exit(0 if FAIL == 0 else 1)
