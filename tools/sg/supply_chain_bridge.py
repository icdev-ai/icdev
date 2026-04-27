#!/usr/bin/env python3
# CUI // SP-CTI
"""Theater Supply Chain Bridge — map sg_entities → supply_chain_dependencies.

Bridges FathomDesk supply chain tables to STRATEGOS theater context using
domain_profile=war_economy.

Architecture:
  Source:  apps/geosigint/data/geosigint.db
             sg_entities    — theater logistics nodes (Ukraine corridor)
             sg_kg_nodes    — theater KG nodes (5 new node types)
  Target:  data/icdev.db
             supply_chain_vendors      — NATO defense contractors (SCRM)
             supply_chain_dependencies — logistics node dependency graph

Ukraine theater seed:
  12 logistics nodes (UA/PL/RO/DE corridors)
  10 NATO SCRM vendors (defense_contractor type)

KG node types added:
  DefenseIndustrialNode, SupplyRoute, CriticalMaterial,
  SanctionsEvader, HumanitarianCorridor

CLI:
  python tools/sg/supply_chain_bridge.py --init
  python tools/sg/supply_chain_bridge.py --seed
  python tools/sg/supply_chain_bridge.py --sync
  python tools/sg/supply_chain_bridge.py --all --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from apps.geosigint.models import get_connection as _geo_conn  # noqa: E402
from tools.db.storage import get_connection as _icdev_conn     # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THEATER = "ukraine"
DOMAIN_PROFILE = "war_economy"
THEATER_PROJECT_ID = "ukraine-theater"
THEATER_PROJECT_NAME = "Ukraine Theater — War Economy Supply Chain"

# KG node types added by this bridge
KG_NODE_TYPES = (
    "DefenseIndustrialNode",
    "SupplyRoute",
    "CriticalMaterial",
    "SanctionsEvader",
    "HumanitarianCorridor",
)

# ---------------------------------------------------------------------------
# Ukraine theater — 12 logistics nodes
# ---------------------------------------------------------------------------

LOGISTICS_NODES: list[dict] = [
    {
        "entity_id": "ua-lviv-rail",
        "entity_name": "Lviv Rail Hub",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 49.840,
        "lon": 24.030,
        "capacity_json": {"throughput_tpd": 8000, "modes": ["rail"], "gauge": "broad_1520mm"},
        "criticality": "critical",
        "notes": "Primary NATO resupply gateway; broad/standard gauge interchange point",
    },
    {
        "entity_id": "ua-odessa-port",
        "entity_name": "Odessa Port",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 46.480,
        "lon": 30.730,
        "capacity_json": {"throughput_tpd": 15000, "modes": ["sea", "rail", "road"]},
        "criticality": "critical",
        "notes": "Black Sea gateway; grain and military cargo; contested under blockade",
    },
    {
        "entity_id": "ua-dnipro-hub",
        "entity_name": "Dnipro Logistics Hub",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 48.460,
        "lon": 35.050,
        "capacity_json": {"throughput_tpd": 5000, "modes": ["rail", "road", "river"]},
        "criticality": "high",
        "notes": "Central distribution hub; Dnieper river crossing; forward staging",
    },
    {
        "entity_id": "ua-kharkiv-depot",
        "entity_name": "Kharkiv Forward Depot",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 49.990,
        "lon": 36.230,
        "capacity_json": {"throughput_tpd": 3000, "modes": ["rail", "road"]},
        "criticality": "high",
        "notes": "Northeast forward supply depot; proximity to Kharkiv front",
    },
    {
        "entity_id": "ua-zaporizhzhia",
        "entity_name": "Zaporizhzhia Transit Node",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 47.840,
        "lon": 35.140,
        "capacity_json": {"throughput_tpd": 2500, "modes": ["rail", "road"]},
        "criticality": "high",
        "notes": "Southern front logistics relay; power plant proximity risk",
    },
    {
        "entity_id": "ua-mykolaiv-port",
        "entity_name": "Mykolaiv Port",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 46.970,
        "lon": 32.000,
        "capacity_json": {"throughput_tpd": 4000, "modes": ["sea", "road"]},
        "criticality": "high",
        "notes": "Southern Black Sea port; humanitarian and military resupply",
    },
    {
        "entity_id": "ua-kramatorsk-rail",
        "entity_name": "Kramatorsk Rail Depot",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 48.730,
        "lon": 37.570,
        "capacity_json": {"throughput_tpd": 2000, "modes": ["rail", "road"]},
        "criticality": "high",
        "notes": "Donbas region rail hub; contested zone forward staging area",
    },
    {
        "entity_id": "ua-uzhhorod-border",
        "entity_name": "Uzhhorod Border Crossing",
        "entity_type": "logistics_node",
        "country": "UA",
        "lat": 48.620,
        "lon": 22.300,
        "capacity_json": {"throughput_tpd": 6000, "modes": ["road", "rail"], "gauge": "mixed"},
        "criticality": "critical",
        "notes": "Western Ukraine–Slovakia/Hungary border; primary NATO ground entry",
    },
    {
        "entity_id": "pl-rzeszow-depot",
        "entity_name": "Rzeszów Supply Depot",
        "entity_type": "logistics_node",
        "country": "PL",
        "lat": 50.040,
        "lon": 22.000,
        "capacity_json": {"throughput_tpd": 10000, "modes": ["road", "rail", "air"]},
        "criticality": "critical",
        "notes": "NATO forward logistics hub in Poland; US Army Jasionka pre-positioning",
    },
    {
        "entity_id": "ro-isaccea-bridge",
        "entity_name": "Isaccea Rail Bridge",
        "entity_type": "logistics_node",
        "country": "RO",
        "lat": 45.270,
        "lon": 28.460,
        "capacity_json": {"throughput_tpd": 3500, "modes": ["rail", "road"]},
        "criticality": "high",
        "notes": "Danube crossing; Romania–Ukraine grain corridor; Black Sea bypass route",
    },
    {
        "entity_id": "ro-constanta-port",
        "entity_name": "Constanta Port",
        "entity_type": "logistics_node",
        "country": "RO",
        "lat": 44.170,
        "lon": 28.650,
        "capacity_json": {"throughput_tpd": 20000, "modes": ["sea", "rail", "road"]},
        "criticality": "critical",
        "notes": "Romania Black Sea deep-water port; NATO maritime hub; Danube feeder",
    },
    {
        "entity_id": "de-ramstein-ab",
        "entity_name": "Ramstein Air Base",
        "entity_type": "logistics_node",
        "country": "DE",
        "lat": 49.440,
        "lon": 7.600,
        "capacity_json": {"throughput_tpd": 5000, "modes": ["air"]},
        "criticality": "critical",
        "notes": "USAF European hub; arms transfer coordination center; strategic airlift node",
    },
]

# ---------------------------------------------------------------------------
# NATO SCRM vendors
# ---------------------------------------------------------------------------

NATO_VENDORS: list[dict] = [
    {
        "vendor_name": "NSPA (NATO Support and Procurement Agency)",
        "vendor_type": "defense_contractor",
        "country_of_origin": "LU",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "BAE Systems plc",
        "vendor_type": "defense_contractor",
        "country_of_origin": "GB",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "Rheinmetall AG",
        "vendor_type": "defense_contractor",
        "country_of_origin": "DE",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "KNDS Group",
        "vendor_type": "defense_contractor",
        "country_of_origin": "DE",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "Leonardo S.p.A.",
        "vendor_type": "defense_contractor",
        "country_of_origin": "IT",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "Thales Group",
        "vendor_type": "defense_contractor",
        "country_of_origin": "FR",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "General Dynamics Corporation",
        "vendor_type": "defense_contractor",
        "country_of_origin": "US",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "Saab AB",
        "vendor_type": "defense_contractor",
        "country_of_origin": "SE",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "Kongsberg Defence & Aerospace",
        "vendor_type": "defense_contractor",
        "country_of_origin": "NO",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
    {
        "vendor_name": "PGZ (Polska Grupa Zbrojeniowa)",
        "vendor_type": "defense_contractor",
        "country_of_origin": "PL",
        "scrm_risk_tier": "low",
        "section_889_status": "compliant",
    },
]

# Supply route edges: (source_id, target_id, dependency_type, criticality)
SUPPLY_ROUTES: list[tuple[str, str, str, str]] = [
    ("de-ramstein-ab",     "pl-rzeszow-depot",    "supplies",      "critical"),
    ("pl-rzeszow-depot",   "ua-uzhhorod-border",  "supplies",      "critical"),
    ("ua-uzhhorod-border", "ua-lviv-rail",        "supplies",      "critical"),
    ("ua-lviv-rail",       "ua-dnipro-hub",       "depends_on",    "high"),
    ("ua-lviv-rail",       "ua-kharkiv-depot",    "depends_on",    "high"),
    ("ua-lviv-rail",       "ua-kramatorsk-rail",  "depends_on",    "high"),
    ("ua-dnipro-hub",      "ua-zaporizhzhia",     "depends_on",    "high"),
    ("ro-constanta-port",  "ro-isaccea-bridge",   "supplies",      "high"),
    ("ro-isaccea-bridge",  "ua-odessa-port",      "integrates_with", "high"),
    ("ua-odessa-port",     "ua-mykolaiv-port",    "integrates_with", "high"),
    ("ua-mykolaiv-port",   "ua-dnipro-hub",       "supplies",      "high"),
]

# KG seed data for the 5 new node types
KG_SEED_NODES: list[dict] = [
    # DefenseIndustrialNode — NATO production facilities
    {
        "entity_id": "din-rheinmetall-de",
        "entity_type": "DefenseIndustrialNode",
        "label": "Rheinmetall Düsseldorf Factory",
        "theater": THEATER,
        "country": "DE",
        "lat": 51.220, "lon": 6.780,
        "properties": {"product": "155mm_artillery_shells", "capacity_year": 700000},
    },
    {
        "entity_id": "din-bae-uk",
        "entity_type": "DefenseIndustrialNode",
        "label": "BAE Systems Glascoed Ordnance Factory",
        "theater": THEATER,
        "country": "GB",
        "lat": 51.693, "lon": -2.895,
        "properties": {"product": "armoured_vehicles", "capacity_year": 120},
    },
    # SupplyRoute — logical transport corridors
    {
        "entity_id": "sr-western-corridor",
        "entity_type": "SupplyRoute",
        "label": "Western NATO Corridor (DE→PL→UA)",
        "theater": THEATER,
        "country": None,
        "lat": None, "lon": None,
        "properties": {
            "nodes": ["de-ramstein-ab", "pl-rzeszow-depot",
                      "ua-uzhhorod-border", "ua-lviv-rail"],
            "mode": "road_rail",
            "throughput_tpd": 5000,
        },
    },
    {
        "entity_id": "sr-black-sea-corridor",
        "entity_type": "SupplyRoute",
        "label": "Black Sea–Danube Corridor (RO→UA)",
        "theater": THEATER,
        "country": None,
        "lat": None, "lon": None,
        "properties": {
            "nodes": ["ro-constanta-port", "ro-isaccea-bridge",
                      "ua-odessa-port", "ua-mykolaiv-port"],
            "mode": "sea_rail",
            "throughput_tpd": 8000,
        },
    },
    # CriticalMaterial
    {
        "entity_id": "cm-155mm-shells",
        "entity_type": "CriticalMaterial",
        "label": "155mm Artillery Shells",
        "theater": THEATER,
        "country": None,
        "lat": None, "lon": None,
        "properties": {
            "category": "ammunition",
            "daily_consumption_rounds": 5000,
            "stockpile_days": 14,
            "primary_producers": ["DE", "FR", "GB", "US"],
        },
    },
    {
        "entity_id": "cm-atgm",
        "entity_type": "CriticalMaterial",
        "label": "Anti-Tank Guided Missiles",
        "theater": THEATER,
        "country": None,
        "lat": None, "lon": None,
        "properties": {
            "category": "guided_munitions",
            "types": ["Javelin", "NLAW", "Brimstone", "Panzerfaust_3"],
            "stockpile_days": 30,
        },
    },
    # SanctionsEvader
    {
        "entity_id": "se-russian-ghost-fleet",
        "entity_type": "SanctionsEvader",
        "label": "Russian Shadow Fleet (Black Sea)",
        "theater": THEATER,
        "country": "XX",
        "lat": 44.0, "lon": 33.0,
        "properties": {
            "method": "flag_of_convenience",
            "flags": ["Palau", "Comoros", "Gabon"],
            "cargo": ["dual_use_electronics", "jet_fuel"],
            "risk_score": 0.87,
        },
    },
    {
        "entity_id": "se-iran-drone-network",
        "entity_type": "SanctionsEvader",
        "label": "Iran–Russia Shahed Drone Supply Network",
        "theater": THEATER,
        "country": "IR",
        "lat": 32.0, "lon": 53.0,
        "properties": {
            "method": "third_country_transshipment",
            "intermediaries": ["AE", "TR"],
            "cargo": ["Shahed-136_components", "microelectronics"],
            "risk_score": 0.95,
        },
    },
    # HumanitarianCorridor
    {
        "entity_id": "hc-grain-initiative",
        "entity_type": "HumanitarianCorridor",
        "label": "Black Sea Grain Initiative Corridor",
        "theater": THEATER,
        "country": None,
        "lat": 46.0, "lon": 31.5,
        "properties": {
            "status": "suspended",
            "tonnage_exported_mt": 32000000,
            "beneficiary_nations": 40,
            "inspection_body": "UN_JCCC",
        },
    },
    {
        "entity_id": "hc-moldova-passage",
        "entity_type": "HumanitarianCorridor",
        "label": "Moldova Civilian Evacuation Passage",
        "theater": THEATER,
        "country": "MD",
        "lat": 47.0, "lon": 28.9,
        "properties": {
            "status": "active",
            "displaced_persons": 500000,
            "crossing_points": ["Palanca", "Tudora", "Cuciurgan"],
        },
    },
]


# ---------------------------------------------------------------------------
# Geosigint DB helpers
# ---------------------------------------------------------------------------

def _ensure_geo_schema() -> None:
    """Ensure sg_entities and sg_kg_nodes tables exist in geosigint.db."""
    from apps.geosigint.models import init_db
    init_db()


def _geo_upsert_entity(conn, node: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sg_entities
            (entity_id, entity_name, entity_type, theater, country,
             lat, lon, domain_profile, capacity_json, criticality, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_id) DO UPDATE SET
            entity_name    = excluded.entity_name,
            criticality    = excluded.criticality,
            capacity_json  = excluded.capacity_json,
            notes          = excluded.notes
        """,
        (
            node["entity_id"],
            node["entity_name"],
            node["entity_type"],
            THEATER,
            node["country"],
            node.get("lat"),
            node.get("lon"),
            DOMAIN_PROFILE,
            json.dumps(node.get("capacity_json", {})),
            node.get("criticality", "high"),
            node.get("status", "active"),
            node.get("notes"),
            now,
        ),
    )


def _geo_upsert_kg_node(conn, node: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    node_id = f"{node['entity_type']}:{node['entity_id']}"
    conn.execute(
        """
        INSERT INTO sg_kg_nodes
            (node_id, entity_id, entity_type, label, theater, country,
             lat, lon, properties_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            label           = excluded.label,
            properties_json = excluded.properties_json
        """,
        (
            node_id,
            node["entity_id"],
            node["entity_type"],
            node["label"],
            node.get("theater"),
            node.get("country"),
            node.get("lat"),
            node.get("lon"),
            json.dumps(node.get("properties", {})),
            now,
        ),
    )


# ---------------------------------------------------------------------------
# ICDEV DB helpers
# ---------------------------------------------------------------------------

def _ensure_theater_project(conn) -> None:
    """Insert the ukraine-theater project row if it doesn't exist."""
    existing = conn.execute(
        "SELECT id FROM projects WHERE id = ?", (THEATER_PROJECT_ID,)
    ).fetchone()
    if existing:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO projects
            (id, name, description, type, classification, status,
             directory_path, impact_level, cloud_environment, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            THEATER_PROJECT_ID,
            THEATER_PROJECT_NAME,
            "STRATEGOS theater-scoped supply chain registry (domain_profile=war_economy)",
            "data_pipeline",
            "CUI",
            "active",
            "tools/sg",
            "IL5",
            "aws-govcloud",
            now,
            now,
        ),
    )
    conn.commit()


def _icdev_upsert_vendor(conn, vendor: dict) -> str:
    """Insert or return existing vendor id."""
    existing = conn.execute(
        "SELECT id FROM supply_chain_vendors WHERE project_id = ? AND vendor_name = ?",
        (THEATER_PROJECT_ID, vendor["vendor_name"]),
    ).fetchone()
    if existing:
        return existing[0]

    vendor_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO supply_chain_vendors
            (id, project_id, vendor_name, vendor_type, country_of_origin,
             scrm_risk_tier, section_889_status, dod_approved, isa_required,
             last_assessed, classification, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vendor_id,
            THEATER_PROJECT_ID,
            vendor["vendor_name"],
            vendor["vendor_type"],
            vendor["country_of_origin"],
            vendor["scrm_risk_tier"],
            vendor["section_889_status"],
            1,   # dod_approved
            0,   # isa_required
            now,
            "CUI",
            now,
            now,
        ),
    )
    return vendor_id


def _icdev_upsert_dependency(
    conn,
    source_id: str,
    target_id: str,
    dependency_type: str,
    criticality: str,
    metadata: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO supply_chain_dependencies
            (project_id, source_type, source_id, target_type, target_id,
             dependency_type, criticality, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            THEATER_PROJECT_ID,
            "system",
            source_id,
            "system",
            target_id,
            dependency_type,
            criticality,
            json.dumps(metadata) if metadata else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_schema() -> dict:
    """Ensure all required tables exist in both databases."""
    _ensure_geo_schema()

    icdev = _icdev_conn()
    try:
        _ensure_theater_project(icdev)
    finally:
        icdev.close()

    return {"status": "ok", "theater": THEATER, "domain_profile": DOMAIN_PROFILE}


def seed_logistics_nodes() -> dict:
    """Seed 12 Ukraine-theater logistics nodes into geosigint.db sg_entities."""
    geo = _geo_conn()
    seeded = 0
    try:
        for node in LOGISTICS_NODES:
            _geo_upsert_entity(geo, node)
            seeded += 1
        geo.commit()
    finally:
        geo.close()
    return {"status": "ok", "seeded_nodes": seeded, "theater": THEATER}


def seed_nato_vendors() -> dict:
    """Seed NATO defense contractor vendors into icdev.db supply_chain_vendors."""
    icdev = _icdev_conn()
    seeded = 0
    try:
        _ensure_theater_project(icdev)
        for vendor in NATO_VENDORS:
            _icdev_upsert_vendor(icdev, vendor)
            seeded += 1
        icdev.commit()
    finally:
        icdev.close()
    return {"status": "ok", "seeded_vendors": seeded, "vendor_type": "defense_contractor"}


def sync_to_supply_chain() -> dict:
    """Sync sg_entities (logistics_node) → supply_chain_dependencies in icdev.db.

    Reads all sg_entities for the ukraine theater from geosigint.db,
    writes each as a 'system' node in supply_chain_dependencies, then
    seeds the predefined supply route edges.
    """
    geo = _geo_conn()
    icdev = _icdev_conn()
    synced_nodes = 0
    synced_edges = 0
    try:
        _ensure_theater_project(icdev)

        # Sync each logistics node as an entity reference dependency
        rows = geo.execute(
            "SELECT entity_id, entity_name, entity_type, country, criticality, capacity_json "
            "FROM sg_entities WHERE theater = ?",
            (THEATER,),
        ).fetchall()

        for row in rows:
            meta = {
                "entity_name": row["entity_name"],
                "entity_type": row["entity_type"],
                "country": row["country"],
                "domain_profile": DOMAIN_PROFILE,
                "capacity": json.loads(row["capacity_json"] or "{}"),
            }
            # Self-referential "system" entry: project → node
            _icdev_upsert_dependency(
                icdev,
                source_id=THEATER_PROJECT_ID,
                target_id=row["entity_id"],
                dependency_type="depends_on",
                criticality=row["criticality"],
                metadata=meta,
            )
            synced_nodes += 1

        # Seed supply route edges
        for src, tgt, dep_type, crit in SUPPLY_ROUTES:
            _icdev_upsert_dependency(
                icdev, src, tgt, dep_type, crit,
                metadata={"route": True, "domain_profile": DOMAIN_PROFILE},
            )
            synced_edges += 1

        icdev.commit()
    finally:
        geo.close()
        icdev.close()

    return {
        "status": "ok",
        "synced_nodes": synced_nodes,
        "synced_edges": synced_edges,
        "theater": THEATER,
    }


def seed_kg_nodes() -> dict:
    """Seed theater KG nodes (5 new node types) into geosigint.db sg_kg_nodes.

    Also adds logistics_node KG entries for all seeded sg_entities.
    """
    geo = _geo_conn()
    seeded = 0
    try:
        # 1. Seed the 5 new node types from KG_SEED_NODES
        for node in KG_SEED_NODES:
            _geo_upsert_kg_node(geo, node)
            seeded += 1

        # 2. Add logistics_node KG entries for all sg_entities in theater
        rows = geo.execute(
            "SELECT entity_id, entity_name, country, lat, lon FROM sg_entities WHERE theater = ?",
            (THEATER,),
        ).fetchall()
        for row in rows:
            _geo_upsert_kg_node(geo, {
                "entity_id": row["entity_id"],
                "entity_type": "logistics_node",
                "label": row["entity_name"],
                "theater": THEATER,
                "country": row["country"],
                "lat": row["lat"],
                "lon": row["lon"],
                "properties": {"domain_profile": DOMAIN_PROFILE},
            })
            seeded += 1

        geo.commit()
    finally:
        geo.close()

    return {
        "status": "ok",
        "seeded_kg_nodes": seeded,
        "kg_node_types": list(KG_NODE_TYPES),
    }


def run_all() -> dict:
    """Full pipeline: init → seed → sync → KG."""
    results = {}
    results["init"] = init_schema()
    results["logistics_nodes"] = seed_logistics_nodes()
    results["nato_vendors"] = seed_nato_vendors()
    results["supply_chain_sync"] = sync_to_supply_chain()
    results["kg_nodes"] = seed_kg_nodes()
    results["status"] = "ok"
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(
        description="Theater Supply Chain Bridge (domain_profile=war_economy)"
    )
    p.add_argument("--init",   action="store_true", help="Ensure schema and theater project")
    p.add_argument("--seed",   action="store_true", help="Seed logistics nodes and NATO vendors")
    p.add_argument("--sync",   action="store_true", help="Sync sg_entities → supply_chain_dependencies")
    p.add_argument("--kg",     action="store_true", help="Seed theater KG nodes")
    p.add_argument("--all",    action="store_true", help="Run full pipeline")
    p.add_argument("--json",   action="store_true", help="Output JSON")
    return p.parse_args()


def main():
    args = _parse_args()
    result = {}

    if args.all:
        result = run_all()
    else:
        if args.init:
            result["init"] = init_schema()
        if args.seed:
            result["logistics_nodes"] = seed_logistics_nodes()
            result["nato_vendors"] = seed_nato_vendors()
        if args.sync:
            result["supply_chain_sync"] = sync_to_supply_chain()
        if args.kg:
            result["kg_nodes"] = seed_kg_nodes()

    if not result:
        print("No action specified. Use --all, --init, --seed, --sync, or --kg.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"[{k}] {v}")


if __name__ == "__main__":
    main()
