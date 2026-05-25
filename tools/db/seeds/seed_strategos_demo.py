"""Seed realistic demo data for Strategos pages.

Populates: sg_orbat_units, sg_ghost_signals, sg_iw_effects,
           sg_supply_nodes, sg_kg_nodes, sg_kg_edges, sg_wargames

Run:
    python tools/db/seeds/seed_strategos_demo.py
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


# ── ORBAT Units ───────────────────────────────────────────────────────────────

ORBAT_UNITS = [
    # US Pacific Fleet / INDOPACOM
    ("US 7th Fleet HQ", "headquarters",  "US",    12000, "Yokosuka, Japan",      35.29, 139.67, "active"),
    ("USS Gerald Ford (CVN-78)", "carrier_strike_group", "US", 5000, "South China Sea", 14.5, 117.2, "active"),
    ("US 3rd Marine Div", "ground",      "US",    18000, "Okinawa, Japan",       26.33, 127.79, "active"),
    ("1st Special Forces Grp", "special_operations", "US", 1400, "Joint Base Lewis-McChord", 47.10, 122.58, "active"),
    ("USS Antietam (CG-54)", "surface_combatant", "US", 400, "Philippine Sea", 16.2, 131.5, "active"),
    # PLA Navy / Eastern Theater
    ("PLA Eastern Theater HQ", "headquarters", "China", 50000, "Nanjing, China",    32.06, 118.78, "active"),
    ("CV Shandong (Hull 17)", "carrier_strike_group", "China", 6000, "Western Pacific", 22.1, 125.5, "active"),
    ("PLA 71st Group Army", "ground",     "China", 45000, "Xuzhou, China",       34.26, 117.19, "active"),
    ("PLA Navy 3rd Sub Flotilla", "submarine", "China", 2200, "Qingdao, China",   36.07, 120.38, "active"),
    ("PLA 1st Amphibious Bde", "amphibious", "China", 8000, "Zhoushan, China",   29.98, 122.20, "active"),
    # Russia Black Sea Fleet
    ("Black Sea Fleet HQ",  "headquarters", "Russia", 25000, "Sevastopol, Crimea", 44.62, 33.53, "inactive"),
    ("BSF Surface Group",   "surface_combatant", "Russia", 1200, "Black Sea",     44.0, 34.5, "inactive"),
    ("Russian 58th Army",   "ground",      "Russia", 65000, "Zaporizhzhia Front", 47.4, 35.1, "active"),
    ("Russian 14th Air Army", "air",       "Russia", 8000, "Rostov-on-Don",     47.23, 39.72, "active"),
    # Taiwan / ROCN
    ("ROCN Fleet HQ",       "headquarters", "Taiwan", 45000, "Zuoying, Kaohsiung", 22.70, 120.27, "active"),
]


def seed_orbat(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    sql = (
        f"INSERT INTO sg_orbat_units "
        f"(id, unit_name, unit_type, nation, strength, location, lat, lon, status, created_at, updated_at) "
        f"VALUES ({','.join([ph]*11)}) ON CONFLICT (id) DO NOTHING"
    )
    count = 0
    for u in ORBAT_UNITS:
        uid = _id()
        conn.execute(sql, (uid, u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], _now(), _now()))
        count += 1
    return count


# ── Ghost Signals ─────────────────────────────────────────────────────────────

GHOST_SIGNALS = [
    # Dark vessels / AIS spoofing
    ("ais_dark",        "441234567", 36.5,  28.3, 0.85, "MarineTraffic",   6.0),
    ("ais_dark",        "371000001", 43.1,  31.7, 0.78, "Global Fishing Watch", 18.0),
    ("ais_spoofing",    "636012345", 14.2,  117.8, 0.92, "ExactEarth",    2.0),
    ("ais_spoofing",    "372000002", 39.9,  124.5, 0.88, "SpaceQuest",    4.5),
    ("ais_dark",        "667001234", 22.3,  114.2, 0.70, "MarineTraffic", 12.0),
    # GPS spoofing / GNSS anomalies
    ("gnss_spoofing",   None,         35.7,  36.1, 0.95, "OSINT-GNSS",    1.0),
    ("gnss_spoofing",   None,         44.0,  33.5, 0.91, "OSINT-GNSS",    3.0),
    ("gnss_spoofing",   None,         47.5,  38.2, 0.87, "OSINT-GNSS",    8.0),
    # USV anomalies
    ("usv_contact",     None,         44.7,  32.9, 0.75, "UAV-ISR",       5.0),
    ("usv_contact",     None,         31.5,  121.8, 0.80, "Sentinel-1",  10.0),
    # Submarine activity
    ("subsurface_contact", None,      16.5,  119.5, 0.65, "P-8 SOSUS",   24.0),
    ("subsurface_contact", None,      26.0,  125.0, 0.72, "P-8 SOSUS",   36.0),
    # Electronic emissions
    ("emcon_break",     None,         38.5,  120.5, 0.83, "SIGINT-NSA",   2.5),
    ("rf_anomaly",      None,         24.5,  119.8, 0.68, "SIGINT",       6.0),
    ("rf_anomaly",      None,         47.2,  39.5,  0.74, "SIGINT",      14.0),
    # Unlit vessel
    ("ais_dark",        "412005678", 29.0,  122.5, 0.77, "SAR-Sentinel", 20.0),
    ("ais_dark",        "525001234", 10.5,  109.5, 0.71, "MarineTraffic", 30.0),
    ("ais_spoofing",    "477001234", 22.5,  114.0, 0.89, "ExactEarth",    7.0),
    ("gnss_spoofing",   None,         52.5,  41.5,  0.93, "OSINT-GNSS",   0.5),
    ("usv_contact",     None,         43.8,  33.1,  0.79, "UAV-ISR",     16.0),
]


def seed_ghost(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    sql = (
        f"INSERT INTO sg_ghost_signals "
        f"(id, signal_type, mmsi, lat, lon, confidence, source, detected_at, behavior_profile_json) "
        f"VALUES ({','.join([ph]*9)}) ON CONFLICT (id) DO NOTHING"
    )
    count = 0
    for g in GHOST_SIGNALS:
        profile = json.dumps({"anomaly_score": round(g[4], 2), "speed_knots": round(8 + g[4] * 5, 1)})
        conn.execute(sql, (_id(), g[0], g[1], g[2], g[3], g[4], g[5], _now(g[6]), profile))
        count += 1
    return count


# ── IW Effects ───────────────────────────────────────────────────────────────

IW_EFFECTS = [
    # Cyber operations
    ("cyber",         "TSMC Fab 18 Power Grid",      "critical", 0.88, "Ransomware deployed; 14h outage",  25.0, 121.3, 2.0),
    ("cyber",         "Ukraine Power Grid",           "critical", 0.95, "Sandworm FrostyGoop malware",     50.4, 30.5,  48.0),
    ("cyber",         "Yokosuka Naval Base IT",       "elevated", 0.72, "Spear-phishing credential theft", 35.3, 139.7, 6.0),
    ("cyber",         "Taiwan MOFA Network",          "elevated", 0.81, "APT41 intrusion detected",        25.1, 121.5, 10.0),
    ("cyber",         "Korean Logistics Hub SCADA",   "moderate", 0.65, "ICS reconnaissance activity",     37.5, 127.0, 24.0),
    # Deception / PSYOP
    ("deception",     "South China Sea Media",        "moderate", 0.79, "AI-generated footage of USN collision", 12.0, 116.0, 4.0),
    ("deception",     "Taiwan Election Narrative",    "elevated", 0.85, "Coordinated inauthentic behavior on X",  25.0, 121.5, 8.0),
    ("deception",     "Japan Alliance Credibility",   "moderate", 0.71, "Fabricated Okinawa protest footage",     26.3, 127.8, 12.0),
    # Influence / Disinformation
    ("disinformation","RIMPAC Casualty Reports",      "moderate", 0.83, "False civilian casualty narrative pushed via Telegram", 21.3, -157.8, 3.0),
    ("disinformation","NATO Eastern Flank Readiness", "elevated", 0.90, "State-sponsored narrative: NATO logistic failures", 52.0, 20.0, 6.0),
    ("disinformation","USAF F-35 Technical Issues",   "low",      0.60, "Exaggerated serviceability claims on social media",  35.0, 127.0, 18.0),
    # Influence campaigns
    ("influence",     "Philippine South China Sea",   "elevated", 0.87, "Trolling network targeting Manila media", 14.6, 121.0, 5.0),
    ("influence",     "European Energy Security",     "moderate", 0.74, "RT narrative amplification via bot network", 48.8, 2.3, 10.0),
    # Kinetic-information fusion
    ("psyop",         "Black Sea Coastal Pop",        "critical", 0.91, "Leaflet drops + radio jamming ahead of USV strike", 45.0, 30.0, 1.0),
    ("supply",        "Odessa Port Labor Disruption", "moderate", 0.68, "Coordinated strike supported by disinfo campaign", 46.5, 30.7, 36.0),
]


def seed_iw(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    sql = (
        f"INSERT INTO sg_iw_effects "
        f"(id, effect_type, target, severity, confidence, description, lat, lon, detected_at) "
        f"VALUES ({','.join([ph]*9)}) ON CONFLICT (id) DO NOTHING"
    )
    count = 0
    for e in IW_EFFECTS:
        conn.execute(sql, (_id(), e[0], e[1], e[2], e[3], e[4], e[5], e[6], _now(e[7])))
        count += 1
    return count


# ── Supply Nodes ──────────────────────────────────────────────────────────────

SUPPLY_NODES = [
    ("port-kaohsiung",  "Kaohsiung Container Port",  "port",     "critical",  0.15, "Taiwan", 22.62, 120.28),
    ("port-keelung",    "Keelung Naval Base",         "port",     "critical",  0.10, "Taiwan", 25.12, 121.74),
    ("port-yokosuka",   "Yokosuka US Navy Base",      "port",     "critical",  0.12, "US",     35.29, 139.67),
    ("port-guam",       "Apra Harbor, Guam",          "port",     "critical",  0.08, "US",     13.45, 144.65),
    ("port-busan",      "Busan Port Complex",         "port",     "high",      0.20, "ROK",    35.10, 129.04),
    ("port-okinawa",    "Naha Military Port",         "port",     "critical",  0.12, "US",     26.22, 127.68),
    ("airfield-kadena", "Kadena Air Base",            "airfield", "critical",  0.08, "US",     26.35, 127.76),
    ("airfield-misawa", "Misawa Air Base",            "airfield", "high",      0.12, "US",     40.70, 141.37),
    ("airfield-osan",   "Osan Air Base",              "airfield", "high",      0.10, "US",     37.09, 127.03),
    ("airfield-andersen","Andersen AFB, Guam",        "airfield", "critical",  0.08, "US",     13.58, 144.93),
    ("depot-camp-zama", "Camp Zama Logistics",        "depot",    "high",      0.18, "US",     35.49, 139.38),
    ("depot-red-hill",  "Red Hill Fuel Storage",      "depot",    "high",      0.15, "US",     21.38, -157.97),
    ("depot-sasebo",    "Sasebo Naval Depot",         "depot",    "high",      0.15, "US",     33.18, 129.73),
    ("port-sevastopol", "Sevastopol Port (denied)",   "port",     "critical",  0.05, "Russia", 44.62, 33.52),
    ("port-novorossiysk","Novorossiysk Port",         "port",     "high",      0.22, "Russia", 44.72, 37.77),
    ("depot-crimea",    "Crimea Ammo Depot",          "depot",    "critical",  0.05, "Russia", 45.30, 33.90),
    ("airfield-saki",   "Saki Air Base (damaged)",    "airfield", "critical",  0.05, "Russia", 45.10, 33.60),
    ("port-qingdao",    "Qingdao PLA Naval Base",     "port",     "critical",  0.12, "China",  36.07, 120.38),
    ("port-zhoushan",   "Zhoushan Naval Base",        "port",     "high",      0.14, "China",  29.98, 122.20),
    ("airfield-longhua","Longhua Air Base (PLA)",     "airfield", "high",      0.14, "China",  31.19, 121.35),
]


def seed_supply(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    sql = (
        f"INSERT INTO sg_supply_nodes "
        f"(node_id, label, node_type, criticality, substitutability, controlled_by, lat, lon, "
        f"metadata_json, created_at, updated_at) "
        f"VALUES ({','.join([ph]*11)}) ON CONFLICT (node_id) DO NOTHING"
    )
    count = 0
    for n in SUPPLY_NODES:
        meta = json.dumps({"source": "seed_strategos_demo", "tier": 1 if n[3] == "critical" else 2})
        conn.execute(sql, (n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], meta, _now(), _now()))
        count += 1
    return count


# ── KG Nodes & Edges ─────────────────────────────────────────────────────────

KG_NODES = [
    # Actors
    ("actor-us-7thfleet", "actor",  "US 7th Fleet"),
    ("actor-pla-east",    "actor",  "PLA Eastern Theater"),
    ("actor-russia-bsf",  "actor",  "Russia Black Sea Fleet"),
    ("actor-taiwan-rocn", "actor",  "Taiwan ROCN"),
    ("actor-ukraine",     "actor",  "Ukraine Armed Forces"),
    ("actor-apt41",       "actor",  "APT41 (China)"),
    ("actor-sandworm",    "actor",  "Sandworm (Russia)"),
    # Locations
    ("loc-taiwan-strait", "location", "Taiwan Strait"),
    ("loc-south-china-sea","location","South China Sea"),
    ("loc-black-sea",     "location", "Black Sea"),
    ("loc-okinawa",       "location", "Okinawa"),
    ("loc-guam",          "location", "Guam"),
    # Capabilities
    ("cap-carrier-strike","capability","Carrier Strike Group"),
    ("cap-a2ad-china",   "capability","PLA A2/AD Network"),
    ("cap-usv-ukraine",  "capability","Ukraine USV Swarm"),
    ("cap-cyber-pla",    "capability","PLA Cyber Operations"),
    ("cap-df21d",        "capability","DF-21D ASBM"),
    # Events
    ("evt-usv-strike-1", "event",    "Crimea Bridge USV Strike"),
    ("evt-sevastopol-1", "event",    "Sevastopol Port Attack"),
    ("evt-tsmc-cyber",   "event",    "TSMC Cyber Intrusion"),
    # Supply nodes as KG nodes
    ("sup-kaohsiung",    "supply_node", "Kaohsiung Port"),
    ("sup-yokosuka",     "supply_node", "Yokosuka Naval Base"),
    ("sup-guam",         "supply_node", "Apra Harbor"),
    ("sup-qingdao",      "supply_node", "Qingdao Naval Base"),
]

KG_EDGES = [
    # Actor–capability
    ("actor-us-7thfleet",  "actor-pla-east",   "adversary_of"),
    ("actor-us-7thfleet",  "cap-carrier-strike","operates"),
    ("actor-pla-east",     "cap-a2ad-china",    "operates"),
    ("actor-pla-east",     "cap-df21d",         "operates"),
    ("actor-pla-east",     "cap-cyber-pla",     "operates"),
    ("actor-ukraine",      "cap-usv-ukraine",   "operates"),
    ("actor-russia-bsf",   "loc-black-sea",     "operates_in"),
    ("actor-ukraine",      "loc-black-sea",     "contests"),
    ("actor-apt41",        "cap-cyber-pla",     "linked_to"),
    ("actor-sandworm",     "actor-russia-bsf",  "supports"),
    # Actor–location
    ("actor-us-7thfleet",  "loc-south-china-sea","patrols"),
    ("actor-us-7thfleet",  "loc-okinawa",       "based_at"),
    ("actor-us-7thfleet",  "loc-guam",          "based_at"),
    ("actor-pla-east",     "loc-taiwan-strait", "threatens"),
    ("actor-taiwan-rocn",  "loc-taiwan-strait", "defends"),
    # Supply–actor
    ("sup-kaohsiung",      "actor-taiwan-rocn", "supports"),
    ("sup-yokosuka",       "actor-us-7thfleet", "supports"),
    ("sup-guam",           "actor-us-7thfleet", "supports"),
    ("sup-qingdao",        "actor-pla-east",    "supports"),
    # Capability–location
    ("cap-a2ad-china",     "loc-taiwan-strait", "covers"),
    ("cap-df21d",          "loc-south-china-sea","covers"),
    ("cap-carrier-strike", "loc-south-china-sea","patrols"),
    # Events
    ("evt-usv-strike-1",   "actor-ukraine",     "executed_by"),
    ("evt-usv-strike-1",   "actor-russia-bsf",  "targeted"),
    ("evt-sevastopol-1",   "actor-ukraine",     "executed_by"),
    ("evt-sevastopol-1",   "sup-kaohsiung",     "disrupted"),  # noqa: demonstration
    ("evt-tsmc-cyber",     "actor-apt41",       "executed_by"),
    ("evt-tsmc-cyber",     "sup-kaohsiung",     "targeted_supply"),
    ("cap-usv-ukraine",    "loc-black-sea",     "operates_in"),
    ("actor-sandworm",     "evt-tsmc-cyber",    "linked_to"),
]


def seed_kg(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    node_sql = (
        f"INSERT INTO sg_kg_nodes (node_id, node_type, label) "
        f"VALUES ({ph},{ph},{ph}) ON CONFLICT (node_id) DO NOTHING"
    )
    edge_sql = (
        f"INSERT INTO sg_kg_edges (source_id, target_id, relation, weight) "
        f"VALUES ({ph},{ph},{ph},{ph}) ON CONFLICT DO NOTHING"
    )
    for n in KG_NODES:
        conn.execute(node_sql, n)
    for e in KG_EDGES:
        conn.execute(edge_sql, (*e, 1.0))
    return len(KG_NODES), len(KG_EDGES)


# ── Wargames ──────────────────────────────────────────────────────────────────

WARGAMES = [
    ("Taiwan Strait Contingency 2025",
     "PLA amphibious assault on Taiwan western coast; Blue defends with carrier strike group + ROCN",
     "active", "INDOPACOM (US + Taiwan)", "PLA Eastern Theater",
     1000, 800, 0.012, 0.010),
    ("Black Sea Maritime Control",
     "Ukraine USV swarm attrition vs Russia Black Sea Fleet surface group",
     "active", "Ukraine Maritime Force", "Russia Black Sea Fleet",
     150, 320, 0.018, 0.008),
    ("South China Sea Freedom of Navigation",
     "US CSG transit through contested waters vs PLA A2/AD response",
     "pending", "US 7th Fleet (CSG-12)", "PLA South Sea Fleet",
     600, 900, 0.010, 0.015),
    ("Korean Peninsula Escalation",
     "DPRK long-range artillery barrage; ROK/US respond with precision strike",
     "pending", "US Forces Korea + ROK Army", "DPRK Ground Forces",
     2000, 1400, 0.008, 0.011),
    ("EW/Cyber Phase Zero",
     "Multi-domain grey-zone campaign — information warfare + cyber preceding kinetic action",
     "complete", "Blue Coalition", "Red Peer Competitor",
     500, 500, 0.005, 0.005),
]


def seed_wargames(conn) -> int:
    ph = "%s" if _is_pg(conn) else "?"
    sql = (
        f"INSERT INTO sg_wargames "
        f"(id, name, scenario, state, blue_force, red_force, outcome, created_at) "
        f"VALUES ({','.join([ph]*8)}) ON CONFLICT (id) DO NOTHING"
    )
    count = 0
    for i, w in enumerate(WARGAMES):
        outcome = json.dumps({
            "blue_strength": w[5], "red_strength": w[6],
            "beta": w[7], "rho": w[8],
        })
        conn.execute(sql, (_id(), w[0], w[1], w[2], w[3], w[4], outcome, _now(i * 24)))
        count += 1
    return count


def _is_pg(conn) -> bool:
    try:
        return "psycopg2" in type(conn).__module__ or "postgres" in str(type(conn)).lower()
    except Exception:
        return False


def main() -> None:
    conn = get_connection()
    try:
        n_orbat = seed_orbat(conn)
        n_ghost = seed_ghost(conn)
        n_iw = seed_iw(conn)
        n_supply = seed_supply(conn)
        n_nodes, n_edges = seed_kg(conn)
        n_wargames = seed_wargames(conn)
        conn.commit()
        print("Seeded:")
        print(f"  sg_orbat_units:   {n_orbat} units")
        print(f"  sg_ghost_signals: {n_ghost} signals")
        print(f"  sg_iw_effects:    {n_iw} effects")
        print(f"  sg_supply_nodes:  {n_supply} nodes")
        print(f"  sg_kg_nodes:      {n_nodes} nodes")
        print(f"  sg_kg_edges:      {n_edges} edges")
        print(f"  sg_wargames:      {n_wargames} scenarios")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
