#!/usr/bin/env python3
# CUI // SP-CTI
"""Defense Industrial Base (DIB) Mapper — war-economy supply chain graph.

Models weapons production nodes for Ukraine and Russia as DIB supply-chain
vendors (domain_profile=war_economy, vendor_type=defense_contractor).

Nodes:
  Ukraine DIB: Ukroboronprom factories, repair depots, NATO partner producers
               (Lockheed HIMARS, BAE AS-90, Rheinmetall 155mm)
  Russia DIB:  Novator (Kalibr), KamAZ (trucks), Uralvagonzavod (T-72/T-90),
               61 Armored Repair Plant, Lenets (Orlan-10), HESA (Shahed-136)

Supply graph: factory → depot → logistics_node → unit

Writes PRODUCES and DEPENDS_ON_SUPPLY edges to the KG.

CLI:
  python tools/strategos/dib_mapper.py --build
  python tools/strategos/dib_mapper.py --critical-path --json
  python tools/strategos/dib_mapper.py --nodes --json
  python tools/strategos/dib_mapper.py --edges --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Static DIB node catalogue
# Each entry: id, name, side, node_type, country, lat, lon,
#             systems, blast_radius (0-1), criticality
# ---------------------------------------------------------------------------

DIB_NODES: list[dict] = [
    # ── Ukraine / NATO ──────────────────────────────────────────────────────
    {
        "id": "ukr-ukroboronprom",
        "node_name": "Ukroboronprom (State Concern)",
        "side": "ukraine",
        "node_type": "factory",
        "country": "Ukraine",
        "lat": 50.4501, "lon": 30.5234,
        "systems_produced": ["BMP-1/2 overhaul", "T-64BV", "BTR-4", "MLRS components"],
        "blast_radius": 0.85,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Central state defense conglomerate; umbrella for 130+ enterprises",
    },
    {
        "id": "ukr-malyshev",
        "node_name": "Malyshev Factory (KMDB)",
        "side": "ukraine",
        "node_type": "factory",
        "country": "Ukraine",
        "lat": 49.9935, "lon": 36.2304,
        "systems_produced": ["T-64", "T-84 Oplot", "BM Oplot upgrades"],
        "blast_radius": 0.80,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Kharkiv Morozov Machine Building Design Bureau; primary MBT producer",
    },
    {
        "id": "ukr-lbtz",
        "node_name": "Lviv Armored Repair Plant (LBTZ)",
        "side": "ukraine",
        "node_type": "repair_depot",
        "country": "Ukraine",
        "lat": 49.8397, "lon": 24.0297,
        "systems_produced": ["T-72 repair", "T-80 repair", "IFV overhaul"],
        "blast_radius": 0.70,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Primary western-Ukraine armor repair hub; NATO equipment integration",
    },
    {
        "id": "ukr-kyiv-depot",
        "node_name": "Kyiv Logistics Hub",
        "side": "ukraine",
        "node_type": "logistics_node",
        "country": "Ukraine",
        "lat": 50.4501, "lon": 30.6234,
        "systems_produced": [],
        "blast_radius": 0.65,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Central rail and road logistics node for eastern redistribution",
    },
    {
        "id": "ukr-dnipro-hub",
        "node_name": "Dnipro Rail / Ammunition Hub",
        "side": "ukraine",
        "node_type": "logistics_node",
        "country": "Ukraine",
        "lat": 48.4647, "lon": 35.0462,
        "systems_produced": [],
        "blast_radius": 0.75,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Critical junction rail hub; ammunition staging for eastern front",
    },
    {
        "id": "ukr-10ac",
        "node_name": "Ukrainian 10th Army Corps",
        "side": "ukraine",
        "node_type": "unit",
        "country": "Ukraine",
        "lat": 50.0145, "lon": 36.2292,
        "systems_produced": [],
        "blast_radius": 0.50,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Kharkiv defense sector; principal consumer of KMDB-produced armor",
    },
    # ── NATO Partners ────────────────────────────────────────────────────────
    {
        "id": "nato-lockheed-himars",
        "node_name": "Lockheed Martin (HIMARS / MLRS)",
        "side": "nato_partner",
        "node_type": "factory",
        "country": "USA",
        "lat": 32.7357, "lon": -97.1081,
        "systems_produced": ["M142 HIMARS", "M270 MLRS", "GMLRS rockets", "ATACMS"],
        "blast_radius": 0.90,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Sole HIMARS/MLRS producer; Fort Worth TX; 24-month lead time on new frames",
    },
    {
        "id": "nato-bae-as90",
        "node_name": "BAE Systems (AS-90 / Braveheart)",
        "side": "nato_partner",
        "node_type": "factory",
        "country": "United Kingdom",
        "lat": 54.1134, "lon": -3.2227,
        "systems_produced": ["AS-90 SP howitzer", "155mm ammunition"],
        "blast_radius": 0.72,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Barrow-in-Furness; refurbished UK stocks transferred to Ukraine",
    },
    {
        "id": "nato-rheinmetall-155",
        "node_name": "Rheinmetall (155mm / PzH 2000)",
        "side": "nato_partner",
        "node_type": "factory",
        "country": "Germany",
        "lat": 51.2217, "lon": 6.7762,
        "systems_produced": ["PzH 2000", "155mm DM121 / DM131 shells", "Marder IFV"],
        "blast_radius": 0.88,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Düsseldorf; building Ukraine plant; 155mm shell production ~600k/yr",
    },
    {
        "id": "nato-przemysl",
        "node_name": "Przemyśl Transfer Point (Poland)",
        "side": "nato_partner",
        "node_type": "logistics_node",
        "country": "Poland",
        "lat": 49.7838, "lon": 22.7677,
        "systems_produced": [],
        "blast_radius": 0.60,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Primary NATO-Ukraine border logistics node; rail gauge change point",
    },
    # ── Russia DIB ───────────────────────────────────────────────────────────
    {
        "id": "rus-novator",
        "node_name": "NPO Novator (Kalibr / 3M14)",
        "side": "russia",
        "node_type": "factory",
        "country": "Russia",
        "lat": 56.8519, "lon": 60.6122,
        "systems_produced": ["3M14 Kalibr cruise missile", "3M54 Kalibr-NK", "Oniks"],
        "blast_radius": 0.95,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Yekaterinburg; primary Kalibr producer; estimated 40-50 missiles/month",
    },
    {
        "id": "rus-kamaz",
        "node_name": "KamAZ (Military Trucks)",
        "side": "russia",
        "node_type": "factory",
        "country": "Russia",
        "lat": 55.7430, "lon": 52.4043,
        "systems_produced": ["KamAZ-5350 military truck", "KamAZ-63501", "Typhoon MRAP"],
        "blast_radius": 0.80,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Naberezhnye Chelny; backbone of Russian military logistics; ~40k units/yr",
    },
    {
        "id": "rus-uvz",
        "node_name": "Uralvagonzavod (T-72/T-90)",
        "side": "russia",
        "node_type": "factory",
        "country": "Russia",
        "lat": 57.9109, "lon": 59.9697,
        "systems_produced": ["T-72B3M", "T-90M Proryv", "T-14 Armata (limited)"],
        "blast_radius": 0.92,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Nizhny Tagil; world's largest tank factory; ~250 T-90M/yr at full rate",
    },
    {
        "id": "rus-61arp",
        "node_name": "61st Armored Repair Plant",
        "side": "russia",
        "node_type": "repair_depot",
        "country": "Russia",
        "lat": 59.9500, "lon": 30.3200,
        "systems_produced": ["T-72/80/90 overhaul", "BMP overhaul", "recovery vehicles"],
        "blast_radius": 0.70,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "St. Petersburg; primary armor overhaul plant for war-loss replacement",
    },
    {
        "id": "rus-lenets",
        "node_name": "Lenets Design Bureau (Orlan-10)",
        "side": "russia",
        "node_type": "factory",
        "country": "Russia",
        "lat": 59.9311, "lon": 30.3609,
        "systems_produced": ["Orlan-10 ISR drone", "Orlan-30"],
        "blast_radius": 0.75,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "St. Petersburg; Orlan-10 backbone of Russian ISR; ~300 produced/yr",
    },
    {
        "id": "iran-hesa-shahed",
        "node_name": "HESA Shahed Line (Isfahan)",
        "side": "iran",
        "node_type": "factory",
        "country": "Iran",
        "lat": 32.6539, "lon": 51.6660,
        "systems_produced": ["Shahed-136 loitering munition", "Shahed-131"],
        "blast_radius": 0.85,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Isfahan Aircraft Manufacturing Industries; ~300 Shahed-136/month exported to Russia",
    },
    {
        "id": "rus-rostov-hub",
        "node_name": "Rostov-on-Don Logistics Hub",
        "side": "russia",
        "node_type": "logistics_node",
        "country": "Russia",
        "lat": 47.2357, "lon": 39.7015,
        "systems_produced": [],
        "blast_radius": 0.82,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Primary southern military district logistics hub; rail and road nexus",
    },
    {
        "id": "rus-belgorod-staging",
        "node_name": "Belgorod Staging Area",
        "side": "russia",
        "node_type": "logistics_node",
        "country": "Russia",
        "lat": 50.5977, "lon": 36.5856,
        "systems_produced": [],
        "blast_radius": 0.78,
        "criticality": "critical",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Forward staging for Ukrainian theater; armor and supply concentration point",
    },
    {
        "id": "rus-8caa",
        "node_name": "8th Combined Arms Army",
        "side": "russia",
        "node_type": "unit",
        "country": "Russia",
        "lat": 47.7167, "lon": 40.2000,
        "systems_produced": [],
        "blast_radius": 0.55,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Novocherkassk; primary southern front consumer; ~400 MBT on strength",
    },
    {
        "id": "rus-58caa",
        "node_name": "58th Combined Arms Army",
        "side": "russia",
        "node_type": "unit",
        "country": "Russia",
        "lat": 43.0235, "lon": 44.6825,
        "systems_produced": [],
        "blast_radius": 0.50,
        "criticality": "high",
        "domain_profile": "war_economy",
        "vendor_type": "defense_contractor",
        "notes": "Vladikavkaz; reserve and southern thrust; rotates through Ukrainian theater",
    },
]

# ---------------------------------------------------------------------------
# Supply graph edges: factory → depot → logistics → unit
# Each edge: source_id, target_id, edge_type, system, throughput_pct
# ---------------------------------------------------------------------------

DIB_EDGES: list[dict] = [
    # Ukraine supply chain
    {"source": "ukr-malyshev",        "target": "ukr-lbtz",          "edge_type": "PRODUCES",         "system": "T-64BV", "throughput_pct": 0.80},
    {"source": "ukr-lbtz",            "target": "ukr-dnipro-hub",     "edge_type": "DEPENDS_ON_SUPPLY", "system": "armor", "throughput_pct": 0.90},
    {"source": "ukr-dnipro-hub",      "target": "ukr-10ac",           "edge_type": "DEPENDS_ON_SUPPLY", "system": "combined_arms", "throughput_pct": 0.85},
    {"source": "ukr-ukroboronprom",   "target": "ukr-kyiv-depot",     "edge_type": "PRODUCES",         "system": "BMP/BTR", "throughput_pct": 0.75},
    {"source": "ukr-kyiv-depot",      "target": "ukr-dnipro-hub",     "edge_type": "DEPENDS_ON_SUPPLY", "system": "logistics", "throughput_pct": 0.95},
    # NATO supply chain into Ukraine
    {"source": "nato-lockheed-himars","target": "nato-przemysl",      "edge_type": "PRODUCES",         "system": "HIMARS/ATACMS", "throughput_pct": 0.70},
    {"source": "nato-rheinmetall-155","target": "nato-przemysl",      "edge_type": "PRODUCES",         "system": "155mm shells", "throughput_pct": 0.80},
    {"source": "nato-bae-as90",       "target": "nato-przemysl",      "edge_type": "PRODUCES",         "system": "AS-90", "throughput_pct": 0.60},
    {"source": "nato-przemysl",       "target": "ukr-lbtz",           "edge_type": "DEPENDS_ON_SUPPLY", "system": "NATO_equipment", "throughput_pct": 0.85},
    # Russia supply chain
    {"source": "rus-novator",         "target": "rus-rostov-hub",     "edge_type": "PRODUCES",         "system": "Kalibr_3M14", "throughput_pct": 0.85},
    {"source": "rus-uvz",             "target": "rus-61arp",          "edge_type": "PRODUCES",         "system": "T-90M", "throughput_pct": 0.90},
    {"source": "rus-61arp",           "target": "rus-belgorod-staging","edge_type": "DEPENDS_ON_SUPPLY","system": "armor_repaired", "throughput_pct": 0.80},
    {"source": "rus-kamaz",           "target": "rus-rostov-hub",     "edge_type": "PRODUCES",         "system": "KamAZ_trucks", "throughput_pct": 0.95},
    {"source": "iran-hesa-shahed",    "target": "rus-rostov-hub",     "edge_type": "PRODUCES",         "system": "Shahed-136", "throughput_pct": 0.75},
    {"source": "rus-lenets",          "target": "rus-belgorod-staging","edge_type": "PRODUCES",         "system": "Orlan-10", "throughput_pct": 0.85},
    {"source": "rus-rostov-hub",      "target": "rus-belgorod-staging","edge_type": "DEPENDS_ON_SUPPLY","system": "logistics", "throughput_pct": 0.90},
    {"source": "rus-belgorod-staging","target": "rus-8caa",           "edge_type": "DEPENDS_ON_SUPPLY", "system": "combined_arms", "throughput_pct": 0.88},
    {"source": "rus-belgorod-staging","target": "rus-58caa",          "edge_type": "DEPENDS_ON_SUPPLY", "system": "combined_arms", "throughput_pct": 0.70},
]

# ---------------------------------------------------------------------------
# DIB-specific table DDL (created on first run if absent)
# ---------------------------------------------------------------------------

_DDL_NODES = """
CREATE TABLE IF NOT EXISTS dib_nodes (
    id TEXT PRIMARY KEY,
    node_name TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('ukraine','russia','nato_partner','iran')),
    node_type TEXT NOT NULL CHECK(node_type IN ('factory','repair_depot','logistics_node','unit','export_supplier')),
    country TEXT,
    lat REAL,
    lon REAL,
    systems_produced TEXT DEFAULT '[]',
    blast_radius REAL DEFAULT 0.5,
    criticality TEXT DEFAULT 'medium' CHECK(criticality IN ('critical','high','medium','low')),
    domain_profile TEXT DEFAULT 'war_economy',
    vendor_type TEXT DEFAULT 'defense_contractor',
    notes TEXT,
    kg_node_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dib_nodes_side ON dib_nodes(side);
CREATE INDEX IF NOT EXISTS idx_dib_nodes_type ON dib_nodes(node_type);
"""

_DDL_EDGES = """
CREATE TABLE IF NOT EXISTS dib_supply_edges (
    id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL REFERENCES dib_nodes(id),
    target_node_id TEXT NOT NULL REFERENCES dib_nodes(id),
    edge_type TEXT NOT NULL CHECK(edge_type IN ('PRODUCES','DEPENDS_ON_SUPPLY','SUPPLIES_TO','REPAIRS')),
    system TEXT,
    throughput_pct REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dib_edges_src ON dib_supply_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_dib_edges_tgt ON dib_supply_edges(target_node_id);
"""

_KG_GRAPH_ID = "dib-war-economy"
_KG_GRAPH_NAME = "DIB War Economy"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gid(prefix: str = "n") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_conn(db_path: str | None = None):
    path = db_path or str(BASE_DIR / "data" / "icdev.db")
    conn = get_connection(db_path=path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_tables(conn) -> None:
    conn.executescript(_DDL_NODES)
    conn.executescript(_DDL_EDGES)
    conn.commit()


# ---------------------------------------------------------------------------
# Build / Sync
# ---------------------------------------------------------------------------


def build_dib_graph(db_path: str | None = None) -> dict:
    """Insert / upsert all DIB nodes and edges into the DB."""
    conn = _get_conn(db_path)
    try:
        _ensure_tables(conn)

        nodes_written = 0
        for node in DIB_NODES:
            conn.execute(
                """INSERT INTO dib_nodes
                   (id, node_name, side, node_type, country, lat, lon,
                    systems_produced, blast_radius, criticality,
                    domain_profile, vendor_type, notes, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(id) DO UPDATE SET
                     blast_radius=excluded.blast_radius,
                     notes=excluded.notes""",
                (
                    node["id"],
                    node["node_name"],
                    node["side"],
                    node["node_type"],
                    node.get("country"),
                    node.get("lat"),
                    node.get("lon"),
                    json.dumps(node.get("systems_produced", [])),
                    node.get("blast_radius", 0.5),
                    node.get("criticality", "medium"),
                    node.get("domain_profile", "war_economy"),
                    node.get("vendor_type", "defense_contractor"),
                    node.get("notes"),
                    _now(),
                ),
            )
            nodes_written += 1

        edges_written = 0
        for edge in DIB_EDGES:
            eid = _gid("de")
            conn.execute(
                """INSERT INTO dib_supply_edges
                   (id, source_node_id, target_node_id, edge_type, system, throughput_pct, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (
                    eid,
                    edge["source"],
                    edge["target"],
                    edge["edge_type"],
                    edge.get("system"),
                    edge.get("throughput_pct", 1.0),
                    _now(),
                ),
            )
            edges_written += 1

        conn.commit()
        return {"status": "ok", "nodes_written": nodes_written, "edges_written": edges_written}
    finally:
        conn.close()


def write_kg_edges(db_path: str | None = None) -> dict:
    """Write DIB nodes and PRODUCES/DEPENDS_ON_SUPPLY edges to the KG tables."""
    conn = _get_conn(db_path)
    try:
        _ensure_tables(conn)
        now = _now()

        # Ensure KG graph exists
        existing = conn.execute(
            "SELECT id FROM kg_graphs WHERE id = %s", (_KG_GRAPH_ID,)
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO kg_graphs (id, project_id, name, entity_count, edge_count, created_at, updated_at)
                   VALUES (%s,%s,%s,0,0,%s,%s)
                   ON CONFLICT(id) DO NOTHING""",
                (_KG_GRAPH_ID, "strategos", _KG_GRAPH_NAME, now, now),
            )

        # Insert KG nodes for each DIB node
        node_id_map: dict[str, str] = {}
        for node in DIB_NODES:
            kg_node_id = f"dib:{node['id']}"
            node_id_map[node["id"]] = kg_node_id
            conn.execute(
                """INSERT INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(id) DO UPDATE SET label=excluded.label, properties=excluded.properties""",
                (
                    kg_node_id,
                    _KG_GRAPH_ID,
                    node["node_name"],
                    "dib_node",
                    json.dumps({
                        "side": node["side"],
                        "node_type": node["node_type"],
                        "country": node.get("country"),
                        "lat": node.get("lat"),
                        "lon": node.get("lon"),
                        "blast_radius": node.get("blast_radius"),
                        "criticality": node.get("criticality"),
                        "systems": node.get("systems_produced", []),
                        "vendor_type": node.get("vendor_type"),
                        "domain_profile": node.get("domain_profile"),
                    }),
                    now,
                ),
            )

        # Insert KG edges
        edges_written = 0
        for edge in DIB_EDGES:
            src_kg = node_id_map.get(edge["source"])
            tgt_kg = node_id_map.get(edge["target"])
            if not src_kg or not tgt_kg:
                continue
            ke_id = _gid("ke")
            conn.execute(
                """INSERT INTO kg_edges
                   (id, graph_id, source_id, target_id, relationship, weight, properties, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (
                    ke_id,
                    _KG_GRAPH_ID,
                    src_kg,
                    tgt_kg,
                    edge["edge_type"],
                    edge.get("throughput_pct", 1.0),
                    json.dumps({"system": edge.get("system")}),
                    now,
                ),
            )
            edges_written += 1

        # Update graph counters
        conn.execute(
            """UPDATE kg_graphs SET entity_count=%s, edge_count=%s, updated_at=%s WHERE id=%s""",
            (len(DIB_NODES), edges_written, now, _KG_GRAPH_ID),
        )
        conn.commit()
        return {"status": "ok", "kg_nodes": len(DIB_NODES), "kg_edges": edges_written}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Critical-path analysis
# ---------------------------------------------------------------------------


def critical_path(db_path: str | None = None, side: str | None = None) -> list[dict]:
    """Return adversary targeting priority list ranked by blast_radius descending.

    blast_radius 0-1 represents the operational impact of disrupting this node.
    Higher = more critical disruption value.
    """
    conn = _get_conn(db_path)
    try:
        _ensure_tables(conn)
        build_dib_graph(db_path)

        _sql_all = (
            "SELECT id, node_name, side, node_type, country, lat, lon,"
            " systems_produced, blast_radius, criticality, notes"
            " FROM dib_nodes ORDER BY blast_radius DESC, criticality ASC"
        )
        _sql_side = (
            "SELECT id, node_name, side, node_type, country, lat, lon,"
            " systems_produced, blast_radius, criticality, notes"
            " FROM dib_nodes WHERE side = ?"
            " ORDER BY blast_radius DESC, criticality ASC"
        )

        if side:
            rows = conn.execute(_sql_side, (side,)).fetchall()
        else:
            rows = conn.execute(_sql_all).fetchall()

        result = []
        for rank, row in enumerate(rows, 1):
            r = dict(row)
            r["rank"] = rank
            r["systems_produced"] = json.loads(r.get("systems_produced") or "[]")
            result.append(r)
        return result
    finally:
        conn.close()


def get_nodes(db_path: str | None = None) -> list[dict]:
    conn = _get_conn(db_path)
    try:
        _ensure_tables(conn)
        build_dib_graph(db_path)
        rows = conn.execute("SELECT * FROM dib_nodes ORDER BY blast_radius DESC").fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["systems_produced"] = json.loads(r.get("systems_produced") or "[]")
            result.append(r)
        return result
    finally:
        conn.close()


def get_edges(db_path: str | None = None) -> list[dict]:
    conn = _get_conn(db_path)
    try:
        _ensure_tables(conn)
        rows = conn.execute("SELECT * FROM dib_supply_edges").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main():
    parser = argparse.ArgumentParser(description="DIB Mapper — war-economy supply chain graph")
    parser.add_argument("--build",         action="store_true", help="Load all DIB nodes + edges into DB")
    parser.add_argument("--kg",            action="store_true", help="Write PRODUCES/DEPENDS_ON_SUPPLY edges to KG")
    parser.add_argument("--critical-path", action="store_true", help="Output adversary targeting priority list")
    parser.add_argument("--nodes",         action="store_true", help="List all DIB nodes")
    parser.add_argument("--edges",         action="store_true", help="List all supply edges")
    parser.add_argument("--side",          choices=["ukraine","russia","nato_partner","iran"],
                        help="Filter by side (for --critical-path / --nodes)")
    parser.add_argument("--json",          action="store_true", help="Output as JSON")
    parser.add_argument("--db",            help="Override DB path")
    args = parser.parse_args()

    db = args.db or None

    if args.build:
        result = build_dib_graph(db)
        print(json.dumps(result) if args.json else f"Built: {result['nodes_written']} nodes, {result['edges_written']} edges")

    if args.kg:
        result = write_kg_edges(db)
        print(json.dumps(result) if args.json else f"KG: {result['kg_nodes']} nodes, {result['kg_edges']} edges")

    if args.critical_path:
        rows = critical_path(db, side=args.side)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"\n{'RANK':<5} {'NODE':<42} {'SIDE':<14} {'TYPE':<16} {'BLAST':>6} {'CRIT':<10}")
            print("-" * 100)
            for r in rows:
                systems = ", ".join(r["systems_produced"][:2]) or "—"
                print(f"{r['rank']:<5} {r['node_name'][:40]:<42} {r['side']:<14} {r['node_type']:<16} {r['blast_radius']:>6.2f} {r['criticality']:<10}")
                if systems:
                    print(f"       Systems: {systems}")

    if args.nodes:
        rows = get_nodes(db)
        if args.side:
            rows = [r for r in rows if r["side"] == args.side]
        print(json.dumps(rows, indent=2) if args.json else f"{len(rows)} nodes")

    if args.edges:
        rows = get_edges(db)
        print(json.dumps(rows, indent=2) if args.json else f"{len(rows)} edges")

    if not any([args.build, args.kg, args.critical_path, args.nodes, args.edges]):
        parser.print_help()


if __name__ == "__main__":
    _main()
