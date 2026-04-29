# CUI // SP-CTI
"""Seed realistic event data for four empty Strategos tables.

Populates:
  sg_conflict_events      — 12 cyber ops + 15 kinetic events
  sg_hitl_items           — 8 HITL decision items (6 pending, 1 approved, 1 rejected)
  sg_pir_requirements     — 8 PIR/CCIR/EEI records
  sg_raw_signals          — 10 OSINT raw signals
  sg_prioritized_signals  — 10 scored prioritizations (1-to-1 with raw signals)

Idempotent:
  - Text-PK tables: ON CONFLICT (id) DO NOTHING
  - Autoincrement tables (raw_signals): INSERT OR IGNORE on url_hash uniqueness
  - Ensures all required columns exist via ALTER TABLE before inserting

Run:
    python tools/db/seeds/seed_strategos_events.py [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection, is_pg  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_TODAY = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: float = 0, hours_ago: float = 0) -> str:
    """Return ISO-8601 UTC timestamp offset from 2026-04-28."""
    return (_TODAY - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def _date(days_ago: float = 0) -> str:
    """Return ISO date string offset from 2026-04-28."""
    return (_TODAY - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _id() -> str:
    return str(uuid.uuid4())


def _ph(conn) -> str:
    """Return SQL placeholder character for the connection backend."""
    return "%s" if is_pg(conn) else "?"


def _url_hash(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DDL helpers — ensure columns exist before seeding
# ---------------------------------------------------------------------------

def _ensure_columns(conn, table: str, columns: list[tuple[str, str]]) -> None:
    """Add missing columns to *table* without failing if they already exist."""
    for col, col_type in columns:
        try:
            if is_pg(conn):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )
            else:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                )
        except Exception:
            pass  # column already present


# ---------------------------------------------------------------------------
# Schema bootstrap — create tables that may not yet exist
# ---------------------------------------------------------------------------

def _bootstrap_tables(conn) -> None:
    """Create the four target tables if they don't exist yet, then ensure all
    required columns are present.  Mirrors what the numbered migrations do so
    the seed is self-contained when run on a fresh DB."""

    # ── sg_conflict_events ────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sg_conflict_events (
            id              TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            source          TEXT,
            external_id     TEXT,
            event_ts        TEXT,
            description     TEXT,
            actor1          TEXT,
            actor2          TEXT,
            event_code      TEXT,
            goldstein_scale REAL,
            lat             REAL,
            lon             REAL,
            aoi             TEXT,
            avg_tone        REAL,
            num_mentions    INTEGER,
            source_url      TEXT,
            technique_ids   TEXT,
            threat_actor    TEXT,
            malware_family  TEXT,
            confidence      INTEGER,
            created_at      TEXT NOT NULL,
            metadata_json   TEXT
        )
    """)
    _ensure_columns(conn, "sg_conflict_events", [
        ("theater_id",    "TEXT"),
        ("entity_id",     "TEXT"),
        ("severity",      "TEXT"),
        ("attack_vector", "TEXT"),
        ("target_name",   "TEXT"),
        ("target_type",   "TEXT"),
        ("damage_level",  "TEXT"),
        ("snapshot_date", "TEXT"),
    ])

    # ── sg_hitl_items ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sg_hitl_items (
            id          TEXT PRIMARY KEY,
            item_type   TEXT,
            ref_id      TEXT,
            payload     TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            decision    TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            notes       TEXT,
            coa_id      TEXT,
            rationale   TEXT
        )
    """)
    _ensure_columns(conn, "sg_hitl_items", [
        ("item_type",   "TEXT"),
        ("ref_id",      "TEXT"),
        ("payload",     "TEXT"),
        ("notes",       "TEXT"),
        ("coa_id",      "TEXT"),
        ("rationale",   "TEXT"),
        ("decision",    "TEXT"),
        ("resolved_by", "TEXT"),
        ("resolved_at", "TEXT"),
    ])

    # ── sg_pir_requirements ───────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sg_pir_requirements (
            id                  TEXT PRIMARY KEY,
            pir_type            TEXT NOT NULL DEFAULT 'PIR'
                                    CHECK(pir_type IN ('PIR','CCIR','EEI')),
            topic               TEXT NOT NULL,
            description         TEXT,
            collection_priority INTEGER NOT NULL DEFAULT 3
                                    CHECK(collection_priority BETWEEN 1 AND 5),
            status              TEXT NOT NULL DEFAULT 'active'
                                    CHECK(status IN ('active','satisfied','cancelled')),
            tasked_to           TEXT,
            due_by              TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )
    """)

    # ── sg_raw_signals ────────────────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sg_raw_signals (
            id          SERIAL PRIMARY KEY,
            url_hash    TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            body        TEXT,
            source      TEXT,
            signal_date TEXT,
            geo_hint    TEXT,
            tier_used   TEXT,
            created_at  TEXT NOT NULL,
            processed   INTEGER NOT NULL DEFAULT 0
        )
        """
        if is_pg(conn)
        else """
        CREATE TABLE IF NOT EXISTS sg_raw_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            body        TEXT,
            source      TEXT,
            signal_date TEXT,
            geo_hint    TEXT,
            tier_used   TEXT,
            created_at  TEXT NOT NULL,
            processed   INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    _ensure_columns(conn, "sg_raw_signals", [("processed", "INTEGER NOT NULL DEFAULT 0")])

    # ── sg_prioritized_signals ────────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sg_prioritized_signals (
            id                          SERIAL PRIMARY KEY,
            raw_signal_id               INTEGER NOT NULL,
            composite_score             REAL NOT NULL,
            posterior_shift_score       REAL NOT NULL DEFAULT 0.0,
            source_discriminability_score REAL NOT NULL DEFAULT 0.0,
            temporal_recency_score      REAL NOT NULL DEFAULT 0.0,
            domain_coverage_score       REAL NOT NULL DEFAULT 0.0,
            rationale                   TEXT,
            run_at                      TEXT NOT NULL,
            created_at                  TEXT NOT NULL,
            is_read                     INTEGER NOT NULL DEFAULT 0,
            promoted_to_kg              INTEGER NOT NULL DEFAULT 0,
            annotation                  TEXT
        )
        """
        if is_pg(conn)
        else """
        CREATE TABLE IF NOT EXISTS sg_prioritized_signals (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_signal_id               INTEGER NOT NULL,
            composite_score             REAL NOT NULL,
            posterior_shift_score       REAL NOT NULL DEFAULT 0.0,
            source_discriminability_score REAL NOT NULL DEFAULT 0.0,
            temporal_recency_score      REAL NOT NULL DEFAULT 0.0,
            domain_coverage_score       REAL NOT NULL DEFAULT 0.0,
            rationale                   TEXT,
            run_at                      TEXT NOT NULL,
            created_at                  TEXT NOT NULL,
            is_read                     INTEGER NOT NULL DEFAULT 0,
            promoted_to_kg              INTEGER NOT NULL DEFAULT 0,
            annotation                  TEXT
        )
        """
    )
    _ensure_columns(conn, "sg_prioritized_signals", [
        ("is_read",        "INTEGER NOT NULL DEFAULT 0"),
        ("promoted_to_kg", "INTEGER NOT NULL DEFAULT 0"),
        ("annotation",     "TEXT"),
    ])

    conn.commit()


# ---------------------------------------------------------------------------
# sg_conflict_events — 12 cyber ops + 15 kinetic events
# ---------------------------------------------------------------------------

# Columns for INSERT (must match _CE_INSERT_COLS below)
_CE_INSERT_COLS = (
    "id", "theater_id", "entity_id", "event_type", "severity", "description",
    "event_ts", "actor1", "actor2", "event_code", "goldstein_scale",
    "lat", "lon", "aoi", "avg_tone", "num_mentions", "source_url",
    "attack_vector", "target_name", "target_type", "damage_level",
    "technique_ids", "threat_actor", "malware_family", "confidence",
    "created_at", "source", "external_id", "snapshot_date",
)

_CYBER_OPS = [
    # (theater_id, entity_id, severity, description,
    #  days_ago, actor1, actor2,
    #  lat, lon, aoi,
    #  avg_tone, num_mentions, source_url,
    #  attack_vector, target_name, target_type, damage_level,
    #  technique_ids, threat_actor, malware_family, confidence)
    # Note: theater_id and entity_id stored in metadata_json; NULL in FK columns
    # because sg_theaters/sg_entities reference tables may be empty in dev.
    (
        "UKR-CYBER", "UKR-APT-001", "critical",
        "Sandworm deploys FrostyGoop ICS malware against Ukraine power distribution SCADA",
        1.0, "Sandworm", "Ukraine Power Grid",
        49.4326, 32.0957, "ukraine",
        -6.8, 420, "https://cert-ua.gov.ua/article/6123456",
        "network", "Kremenchuk Power Substation", "critical_infrastructure", "severe",
        "T1190,T1059,T1486", "Sandworm", "FrostyGoop", 92,
    ),
    (
        "UKR-CYBER", "UKR-APT-002", "high",
        "APT28 credential harvesting campaign targeting Ukrainian Ministry of Defence VPN",
        3.5, "APT28", "Ukraine MoD",
        50.4501, 30.5234, "ukraine",
        -4.2, 285, "https://cert-ua.gov.ua/article/6123457",
        "phishing", "UA MoD VPN Gateway", "government", "moderate",
        "T1133,T1566,T1098", "APT28", "Sofacy", 88,
    ),
    (
        "UKR-CYBER", "UKR-APT-003", "high",
        "Wiper malware (CaddyWiper variant) destroys data on Ukrainian telecom backbone",
        6.0, "Sandworm", "Kyivstar",
        48.4667, 35.0462, "ukraine",
        -7.1, 312, "https://www.welivesecurity.com/report/caddywiper-ua",
        "supply_chain", "Kyivstar Core Network", "telecom", "severe",
        "T1059,T1070,T1486", "Sandworm", "CaddyWiper", 85,
    ),
    (
        "UKR-CYBER", "UKR-APT-004", "medium",
        "DDoS saturation attack against Ukrainian financial sector clearing infrastructure",
        9.0, "Sandworm", "NBU Payment System",
        50.4416, 30.5154, "ukraine",
        -3.5, 198, "https://cert-ua.gov.ua/article/6123460",
        "network", "National Bank of Ukraine Payment Hub", "financial", "moderate",
        "T1499,T1041", "Sandworm", "Mirai-variant", 78,
    ),
    (
        "UKR-CYBER", "UKR-APT-005", "medium",
        "Spear-phishing campaign against Ukrainian railway logistics operators",
        11.5, "APT28", "Ukrzaliznytsia",
        49.9808, 36.2527, "ukraine",
        -2.8, 145, "https://cert-ua.gov.ua/article/6123465",
        "phishing", "Ukrzaliznytsia Dispatch Center", "logistics", "minor",
        "T1566,T1203,T1036", "APT28", "X-Agent", 72,
    ),
    (
        "UKR-CYBER", "UKR-APT-006", "high",
        "SQL injection and credential stuffing against Ukrainian government web portals",
        14.0, "APT28", "Ukraine e-Gov",
        50.4332, 30.5232, "ukraine",
        -5.1, 267, "https://cert-ua.gov.ua/article/6123471",
        "web_application", "Diia Government Portal", "government", "moderate",
        "T1190,T1110,T1098", "APT28", "Credential Stealer", 80,
    ),
    (
        "UKR-CYBER", "UKR-APT-007", "critical",
        "UEFI bootkit implant discovered on Ukrainian military command and control servers",
        17.0, "Sandworm", "UA Command Net",
        48.5517, 34.5981, "ukraine",
        -8.3, 389, "https://cert-ua.gov.ua/article/6123477",
        "physical_access", "UA C2 Server Farm", "military", "severe",
        "T1059,T1098,T1036,T1070", "Sandworm", "LoJax-UEFI", 90,
    ),
    # Taiwan theater (5 cyber ops)
    (
        "TWN-CYBER", "TWN-APT-001", "critical",
        "APT41 intrusion into TSMC Fab 18 industrial network; IP exfiltration of chip design data",
        2.0, "APT41", "TSMC",
        24.7762, 120.9795, "taiwan",
        -7.4, 510, "https://www.cisa.gov/alerts/apt41-tsmc",
        "network", "TSMC Fab 18 SCADA", "critical_infrastructure", "severe",
        "T1190,T1059,T1041,T1046", "APT41", "ShadowPad", 91,
    ),
    (
        "TWN-CYBER", "TWN-APT-002", "high",
        "Volt Typhoon pre-positioning detected in Taiwan Strait cable landing station networks",
        5.0, "Volt Typhoon", "Taiwan Telecom",
        25.1039, 121.5422, "taiwan",
        -5.8, 340, "https://www.cisa.gov/alerts/volt-typhoon-taiwan",
        "network", "Tanshui Cable Landing Station", "telecom", "moderate",
        "T1190,T1133,T1046", "Volt Typhoon", "LOLBins", 87,
    ),
    (
        "TWN-CYBER", "TWN-APT-003", "high",
        "Lazarus Group ransomware against Taiwanese semiconductor equipment supplier",
        8.0, "Lazarus Group", "AUO Corporation",
        24.0520, 120.6950, "taiwan",
        -6.2, 278, "https://www.cisa.gov/alerts/lazarus-taiwan-semi",
        "phishing", "AUO Supply Chain Portal", "manufacturing", "moderate",
        "T1566,T1486,T1491", "Lazarus Group", "HermeticWiper", 82,
    ),
    (
        "TWN-CYBER", "TWN-APT-004", "medium",
        "APT41 reconnaissance activity against Taiwan Ministry of National Defence",
        12.0, "APT41", "Taiwan MND",
        25.0330, 121.5654, "taiwan",
        -3.9, 185, "https://www.cisa.gov/alerts/apt41-tw-mnd",
        "phishing", "MND Personnel Database", "government", "minor",
        "T1566,T1036,T1110", "APT41", "Derusbi", 75,
    ),
    (
        "TWN-CYBER", "TWN-APT-005", "high",
        "Volt Typhoon living-off-the-land lateral movement across Taiwan power grid OT networks",
        20.0, "Volt Typhoon", "Taipower",
        23.6978, 120.9605, "taiwan",
        -6.5, 310, "https://www.cisa.gov/alerts/volt-typhoon-taipower",
        "network", "Taipower Distribution Control Center", "critical_infrastructure", "moderate",
        "T1133,T1059,T1046,T1041", "Volt Typhoon", "LOLBins", 84,
    ),
]

# Kinetic events: (theater_id, entity_id, severity, description,
#                  days_ago, actor1, actor2, event_type_kn,
#                  lat, lon, aoi, avg_tone, num_mentions, source_url,
#                  event_code, goldstein_scale,
#                  target_name, target_type, damage_level, confidence)
_KINETIC_EVENTS = [
    (
        "UKR-EAST", "UKR-KIN-001", "critical",
        "Russian Kh-101 cruise missile barrage targets Kyiv energy infrastructure; 8 substations damaged",
        0.5, "Russia", "Ukraine", "attack",
        50.4501, 30.5234, "ukraine",
        -8.5, 640, "https://www.ukrinform.ua/rubric-ato/3456789",
        "190", -9.5, "Kyiv Power Grid Hub-1", "critical_infrastructure", "severe", 94,
    ),
    (
        "UKR-SOUTH", "UKR-KIN-002", "high",
        "Ukrainian USV swarm strikes Russian warship Vasily Bykov in western Black Sea",
        1.5, "Ukraine", "Russia", "usv_strike",
        44.6166, 33.5254, "ukraine",
        -5.1, 380, "https://www.navy.mil.ua/usv-strike-bykov",
        "190", -8.0, "RFS Vasily Bykov", "naval_vessel", "moderate", 89,
    ),
    (
        "UKR-EAST", "UKR-KIN-003", "high",
        "HIMARS precision strike on Russian ammunition depot near Tokmak; secondary explosions",
        3.0, "Ukraine", "Russia", "attack",
        47.2560, 35.7081, "ukraine",
        -6.3, 295, "https://www.ukrinform.ua/rubric-ato/3456801",
        "1931", -9.0, "Tokmak Ammunition Depot", "logistics", "severe", 91,
    ),
    (
        "UKR-EAST", "UKR-KIN-004", "critical",
        "Russian Iskander-M ballistic missile strike destroys Ukrainian brigade headquarters in Zaporizhzhia",
        4.5, "Russia", "Ukraine", "attack",
        47.8388, 35.1396, "ukraine",
        -9.1, 520, "https://www.ukrinform.ua/rubric-ato/3456815",
        "1931", -10.0, "UA 128th Mtn Assault Bde HQ", "military", "severe", 93,
    ),
    (
        "UKR-EAST", "UKR-KIN-005", "medium",
        "Russian ground assault on Avdiivka coke plant fails; Ukrainian defenders repel attack",
        6.0, "Russia", "Ukraine", "military_move",
        48.1350, 37.7543, "ukraine",
        -4.7, 210, "https://www.ukrinform.ua/rubric-ato/3456830",
        "0331", -6.0, "Avdiivka Coke Plant", "industrial", "minor", 82,
    ),
    (
        "UKR-SOUTH", "UKR-KIN-006", "high",
        "Ukraine drone strike disables Russian air defense radar near Crimea bridge",
        7.5, "Ukraine", "Russia", "attack",
        45.3456, 36.4728, "ukraine",
        -7.2, 415, "https://www.navy.mil.ua/drone-strike-ada",
        "1931", -8.5, "Russian S-400 Battery Taman", "air_defense", "moderate", 88,
    ),
    (
        "UKR-EAST", "UKR-KIN-007", "medium",
        "NATO intelligence confirms Russian force buildup of 40,000 troops along Sumy axis",
        10.0, "Russia", "NATO", "military_move",
        51.0667, 34.8000, "ukraine",
        -3.5, 180, "https://www.nato.int/intel/sumy-buildup",
        "0331", -4.0, "Sumy Oblast Northern Border", "military", "minor", 79,
    ),
    (
        "UKR-SOUTH", "UKR-KIN-008", "critical",
        "Ukrainian USV strike sinks Russian Ropucha-class landing ship in Sevastopol harbor",
        12.0, "Ukraine", "Russia", "usv_strike",
        44.6239, 33.5228, "ukraine",
        -8.8, 590, "https://www.navy.mil.ua/usv-ropucha",
        "1931", -9.5, "RFS Minsk Landing Ship", "naval_vessel", "severe", 95,
    ),
    (
        "UKR-EAST", "UKR-KIN-009", "high",
        "Russia launches coordinated multi-axis ground offensive along Kharkiv-Donetsk seam",
        15.0, "Russia", "Ukraine", "military_move",
        49.9808, 36.2527, "ukraine",
        -7.8, 480, "https://www.ukrinform.ua/rubric-ato/3456902",
        "1931", -8.0, "Kharkiv-Donetsk Forward Line", "military", "moderate", 87,
    ),
    (
        "UKR-EAST", "UKR-KIN-010", "high",
        "Ukrainian Storm Shadow strike destroys Russian logistics rail junction at Melitopol",
        18.0, "Ukraine", "Russia", "attack",
        46.8534, 35.3614, "ukraine",
        -6.9, 362, "https://www.ukrinform.ua/rubric-ato/3456935",
        "190", -9.0, "Melitopol Rail Junction", "logistics", "severe", 90,
    ),
    (
        "UKR-EAST", "UKR-KIN-011", "medium",
        "Ukrainian diplomatic protest to UN Security Council over Russian targeting of civilian infrastructure",
        21.0, "Ukraine", "Russia", "military_move",
        50.4501, 30.5234, "ukraine",
        -2.1, 155, "https://www.un.org/press/en/2026/sc15987",
        "0331", -2.0, "Kyiv Civilian Infrastructure", "civilian", "minor", 75,
    ),
    (
        "UKR-SOUTH", "UKR-KIN-012", "high",
        "Russian Kalibr cruise missile attack on Odesa port destroys grain export terminal",
        24.0, "Russia", "Ukraine", "attack",
        46.4775, 30.7326, "ukraine",
        -7.4, 428, "https://www.ukrinform.ua/rubric-ato/3456971",
        "1931", -8.5, "Odesa Grain Export Terminal", "logistics", "severe", 88,
    ),
    (
        "UKR-EAST", "UKR-KIN-013", "critical",
        "Russian thermobaric TOS-1 artillery system deployed against UA fortified positions near Bakhmut",
        26.0, "Russia", "Ukraine", "attack",
        48.5952, 37.9997, "ukraine",
        -9.3, 535, "https://www.ukrinform.ua/rubric-ato/3457010",
        "1931", -10.0, "Bakhmut Northern Defensive Line", "military", "severe", 92,
    ),
    (
        "UKR-EAST", "UKR-KIN-014", "medium",
        "NATO delivers air defense package to Ukraine including Patriot PAC-3 interceptors",
        28.0, "NATO", "Russia", "military_move",
        50.4501, 30.5234, "ukraine",
        -1.5, 130, "https://www.nato.int/press/patriot-ukraine",
        "0331", 2.0, "Ukraine Air Defense Coverage", "air_defense", "none", 96,
    ),
    (
        "UKR-EAST", "UKR-KIN-015", "high",
        "Ukraine Precision strike on Russian electronic warfare complex near Horlivka degrades GPS jamming",
        30.0, "Ukraine", "Russia", "attack",
        48.3360, 38.0560, "ukraine",
        -6.1, 301, "https://www.ukrinform.ua/rubric-ato/3457048",
        "1931", -8.0, "Russian EW Complex Horlivka", "military", "moderate", 86,
    ),
]


def _ce_conflict_id(days_ago: float, idx: int, aoi: str) -> str:
    """Generate deterministic external_id for dedup."""
    return f"{aoi}-{_date(days_ago)}-{idx:03d}"


def seed_conflict_events(conn) -> int:
    ph = _ph(conn)
    placeholders = ",".join([ph] * len(_CE_INSERT_COLS))
    col_list = ",".join(_CE_INSERT_COLS)
    sql = (
        f"INSERT INTO sg_conflict_events ({col_list}) VALUES ({placeholders}) "  # nosec B608
        f"ON CONFLICT DO NOTHING"
    )

    count = 0

    # ── Cyber ops ──────────────────────────────────────────────────────────
    for i, row in enumerate(_CYBER_OPS):
        (theater_id, entity_id, severity, description,
         days_ago, actor1, actor2,
         lat, lon, aoi,
         avg_tone, num_mentions, source_url,
         attack_vector, target_name, target_type, damage_level,
         technique_ids, threat_actor, malware_family, confidence) = row

        # theater_id / entity_id FK columns left NULL (see metadata_json in schema);
        # reference tables may be empty in a dev DB.
        conn.execute(sql, (
            _id(),                            # id
            None,                             # theater_id (FK — see metadata_json)
            None,                             # entity_id  (FK — see metadata_json)
            "cyber_op",                       # event_type
            severity,                         # severity
            description,                      # description
            _ts(days_ago),                    # event_ts
            actor1,                           # actor1
            actor2,                           # actor2
            None,                             # event_code (N/A for cyber)
            None,                             # goldstein_scale (N/A for cyber)
            lat,                              # lat
            lon,                              # lon
            aoi,                              # aoi
            avg_tone,                         # avg_tone
            num_mentions,                     # num_mentions
            source_url,                       # source_url
            attack_vector,                    # attack_vector
            target_name,                      # target_name
            target_type,                      # target_type
            damage_level,                     # damage_level
            technique_ids,                    # technique_ids
            threat_actor,                     # threat_actor
            malware_family,                   # malware_family
            confidence,                       # confidence
            _ts(days_ago),                    # created_at
            "cert-ua" if aoi == "ukraine" else "cisa",  # source
            _ce_conflict_id(days_ago, i, aoi),  # external_id
            _date(days_ago),                  # snapshot_date
        ))
        count += 1

    # ── Kinetic events ─────────────────────────────────────────────────────
    for i, row in enumerate(_KINETIC_EVENTS):
        (theater_id, entity_id, severity, description,
         days_ago, actor1, actor2, event_type_kn,
         lat, lon, aoi,
         avg_tone, num_mentions, source_url,
         event_code, goldstein_scale,
         target_name, target_type, damage_level, confidence) = row

        conn.execute(sql, (
            _id(),                            # id
            None,                             # theater_id (FK — see metadata_json)
            None,                             # entity_id  (FK — see metadata_json)
            event_type_kn,                    # event_type
            severity,                         # severity
            description,                      # description
            _ts(days_ago),                    # event_ts
            actor1,                           # actor1
            actor2,                           # actor2
            event_code,                       # event_code
            goldstein_scale,                  # goldstein_scale
            lat,                              # lat
            lon,                              # lon
            aoi,                              # aoi
            avg_tone,                         # avg_tone
            num_mentions,                     # num_mentions
            source_url,                       # source_url
            None,                             # attack_vector (N/A for kinetic)
            target_name,                      # target_name
            target_type,                      # target_type
            damage_level,                     # damage_level
            None,                             # technique_ids (N/A for kinetic)
            None,                             # threat_actor
            None,                             # malware_family
            confidence,                       # confidence
            _ts(days_ago),                    # created_at
            "gdelt",                          # source
            _ce_conflict_id(days_ago, 100 + i, aoi),  # external_id
            _date(days_ago),                  # snapshot_date
        ))
        count += 1

    return count


# ---------------------------------------------------------------------------
# sg_hitl_items — 8 decision items
# ---------------------------------------------------------------------------

_HITL_ITEMS = [
    # Signal promotion — pending
    {
        "item_type": "signal_promotion",
        "ref_id": "sig-001",
        "payload": json.dumps({
            "signal_id": "sig-001",
            "title": "Russian missile production surge — Alabuga plant output up 40%",
            "score": 0.91,
            "rationale": "High posterior shift; corroborated by three independent OSINT sources",
        }),
        "status": "pending",
        "days_ago": 0.5,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "Awaiting J2 analyst review",
        "coa_id": None,
        "rationale": "Promotes high-confidence industrial production signal to KG",
    },
    # Signal promotion — pending
    {
        "item_type": "signal_promotion",
        "ref_id": "sig-007",
        "payload": json.dumps({
            "signal_id": "sig-007",
            "title": "PLA Navy Type 075 LHA conducts night-time amphibious landing drills",
            "score": 0.87,
            "rationale": "Temporal recency high; domain coverage aligns PIR-001",
        }),
        "status": "pending",
        "days_ago": 1.0,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "Cross-reference with ROCN reconnaissance report",
        "coa_id": None,
        "rationale": "High-value INDOPACOM signal eligible for KG promotion",
    },
    # Signal promotion — approved
    {
        "item_type": "signal_promotion",
        "ref_id": "sig-003",
        "payload": json.dumps({
            "signal_id": "sig-003",
            "title": "Iranian Shahed-136 production line expansion confirmed by satellite",
            "score": 0.95,
            "rationale": "Confirmed via NGA imagery; directly satisfies EEI-003",
        }),
        "status": "approved",
        "days_ago": 3.0,
        "decision": "approve",
        "resolved_by": "ANALYST-MAJ-CHEN",
        "resolved_at": _ts(2.8),
        "notes": "Promoted to KG node irn-drone-prod-001",
        "coa_id": None,
        "rationale": "Satisfies EEI-003; critical for Iran proxy network tracking",
    },
    # Strike approval — pending
    {
        "item_type": "strike_approval",
        "ref_id": "tgt-ukr-022",
        "payload": json.dumps({
            "target_name": "Tokmak Rail Junction — Fuel Cache Alpha",
            "target_type": "logistics",
            "confidence": 88,
            "coa_ref": "COA-BRAVO-OPT2",
            "collateral_estimate": "low",
            "legal_review": "cleared",
        }),
        "status": "pending",
        "days_ago": 0.25,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "JTF-UA targeting cell package submitted; awaiting JAG concurrence",
        "coa_id": "COA-BRAVO-OPT2",
        "rationale": "High-value logistics node; strike will delay Russian offensive by est. 72h",
    },
    # Strike approval — rejected
    {
        "item_type": "strike_approval",
        "ref_id": "tgt-twn-008",
        "payload": json.dumps({
            "target_name": "Zhoushan PLA Amphibious Base Pier-3",
            "target_type": "naval_facility",
            "confidence": 74,
            "coa_ref": "COA-ALPHA-OPT1",
            "collateral_estimate": "medium",
            "legal_review": "pending",
        }),
        "status": "rejected",
        "days_ago": 4.0,
        "decision": "reject",
        "resolved_by": "BGEN-MORRISON",
        "resolved_at": _ts(3.7),
        "notes": "Escalation risk exceeds commander's guidance; confidence below threshold",
        "coa_id": "COA-ALPHA-OPT1",
        "rationale": "Insufficient confidence + unacceptable collateral risk",
    },
    # Brief release — pending
    {
        "item_type": "brief_release",
        "ref_id": "brief-ib-2026-042",
        "payload": json.dumps({
            "brief_id": "IB-2026-042",
            "title": "Black Sea Maritime Threat Assessment — Week of 2026-04-28",
            "classification": "SECRET//NOFORN",
            "pages": 12,
            "distribution": ["J2", "J3", "EUCOM-J2"],
        }),
        "status": "pending",
        "days_ago": 0.1,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "Requires SIO lead review before distribution",
        "coa_id": None,
        "rationale": "Weekly maritime threat brief — distribution gate",
    },
    # Brief release — pending
    {
        "item_type": "brief_release",
        "ref_id": "brief-ib-2026-039",
        "payload": json.dumps({
            "brief_id": "IB-2026-039",
            "title": "Taiwan Strait A2/AD Capability Update — Q2 2026",
            "classification": "SECRET//NOFORN",
            "pages": 24,
            "distribution": ["J2", "INDOPACOM-J2", "OSD-ISA"],
        }),
        "status": "pending",
        "days_ago": 2.0,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "Pending final imagery annex from NGA",
        "coa_id": None,
        "rationale": "Quarterly A2/AD assessment — NGA annex required before release",
    },
    # PIR update — pending
    {
        "item_type": "pir_update",
        "ref_id": "pir-001",
        "payload": json.dumps({
            "pir_id": "pir-001",
            "topic": "Russian Ground Force Disposition — Eastern Ukraine",
            "current_status": "active",
            "proposed_status": "satisfied",
            "supporting_signals": ["sig-001", "sig-005", "sig-009"],
            "analyst_confidence": 82,
        }),
        "status": "pending",
        "days_ago": 1.5,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "notes": "Three corroborating signals satisfy collection requirement",
        "coa_id": None,
        "rationale": "Collection threshold met; recommending PIR-001 closure",
    },
]


def seed_hitl_items(conn) -> int:
    ph = _ph(conn)
    cols = (
        "id", "item_type", "ref_id", "payload", "status", "created_at",
        "decision", "resolved_by", "resolved_at", "notes", "coa_id", "rationale",
    )
    placeholders = ",".join([ph] * len(cols))
    col_list = ",".join(cols)
    sql = (
        f"INSERT INTO sg_hitl_items ({col_list}) VALUES ({placeholders}) "  # nosec B608
        f"ON CONFLICT (id) DO NOTHING"
    )

    count = 0
    for item in _HITL_ITEMS:
        conn.execute(sql, (
            _id(),
            item["item_type"],
            item["ref_id"],
            item["payload"],
            item["status"],
            _ts(item["days_ago"]),
            item["decision"],
            item["resolved_by"],
            item["resolved_at"],
            item["notes"],
            item["coa_id"],
            item["rationale"],
        ))
        count += 1
    return count


# ---------------------------------------------------------------------------
# sg_pir_requirements — 8 items (3 PIR, 2 CCIR, 3 EEI)
# ---------------------------------------------------------------------------

_PIR_REQUIREMENTS = [
    # PIR — Theater-level intelligence requirements
    (
        "PIR", "Russian Ground Force Disposition — Eastern Ukraine",
        "Determine Russian 8th Combined Arms Army order of battle, strength, "
        "and main effort axis for offensive operations along Zaporizhzhia-Donetsk corridor. "
        "Specifically: tank and IFV concentrations ≥ battalion-size, "
        "artillery regiment positioning, and logistics sustainment posture.",
        1, "active", "DIA-J2 / EUCOM-J2",
        _date(-14),  # due_by 14 days from today
        3.0,
    ),
    (
        "PIR", "PLA Naval Order of Battle — Taiwan Strait",
        "Track disposition and readiness of PLA Navy East Sea Fleet surface action groups, "
        "submarine squadrons, and amphibious readiness groups (Type 071/075 LHDs). "
        "Report changes in sortie rates, training patterns, or forward deployment within 24h.",
        1, "active", "INDOPACOM-J2 / ONI",
        _date(-10),
        1.0,
    ),
    (
        "PIR", "Iranian Proxy Network — Drone Supply Chain",
        "Identify production capacity, export routes, and end-user recipients of "
        "Shahed-136/131 family UAVs. Assess Iranian assistance to Russian forces "
        "in terms of monthly delivery volume and technical advisory presence in Ukraine.",
        2, "active", "DIA / CENTCOM-J2",
        _date(-7),
        5.0,
    ),
    # CCIR — Commander's critical information
    (
        "CCIR", "Ukrainian Air Defense Ammunition Stockpile — Readiness Threshold",
        "Report immediately when Ukrainian Patriot PAC-2/3 or NASAMS interceptor "
        "stockpile falls below 30-day sustained combat consumption rate. "
        "This threshold triggers emergency NATO resupply authorization.",
        1, "active", "EUCOM-J4 / UA MoD Liaison",
        _date(-3),
        2.0,
    ),
    (
        "CCIR", "NATO Eastern Flank Logistics Sustainment — 30-Day Combat Rate",
        "Confirm logistics prepositioning in Poland and Romania achieves 30-day "
        "sustainment rate for NATO eFP battlegroups. Report shortfalls in "
        "ammunition, fuel, and medical supplies that affect combat readiness.",
        2, "active", "SACEUR-J4 / SHAPE",
        _date(-5),
        4.0,
    ),
    # EEI — Essential elements of information
    (
        "EEI", "Russian Iskander-M Launcher Positions — Crimea and Kursk Oblast",
        "Precise geospatial coordinates (±100m) of Iskander-M TEL hide positions "
        "and reload sites in Crimea and Kursk Oblast. Required for strike planning "
        "cycle. Report any movement within 6h of detection.",
        1, "active", "NGA / EUCOM SIGINT",
        _date(-2),
        0.5,
    ),
    (
        "EEI", "PLA 73rd Group Army — Order of Battle Changes",
        "Report any additions, reductions, or structural changes to PLA 73rd Group Army "
        "subordinate units (brigades, regiments) indicating readiness for amphibious "
        "operations. Key indicators: attachment of 1st Amphibious Bde, enabler units.",
        2, "active", "INDOPACOM SIGINT / HUMINT",
        _date(-7),
        2.5,
    ),
    (
        "EEI", "Russian Offensive Timeline Indicators — Spring 2026",
        "Identify specific indicators that precede a major Russian offensive within "
        "72h: fuel forward stockpile ≥ 10 days, medical evacuations repositioned forward, "
        "electronic warfare activation along axis, absence of EMCON breaks. "
        "Report each indicator within 2h of detection.",
        1, "satisfied", "DIA / EUCOM-J2 / OSINT-HUB",
        _date(7),   # due_by already past (satisfied)
        7.0,
    ),
]


def seed_pir_requirements(conn) -> int:
    ph = _ph(conn)
    cols = (
        "id", "pir_type", "topic", "description",
        "collection_priority", "status", "tasked_to", "due_by",
        "created_at", "updated_at",
    )
    placeholders = ",".join([ph] * len(cols))
    col_list = ",".join(cols)
    sql = (
        f"INSERT INTO sg_pir_requirements ({col_list}) VALUES ({placeholders}) "  # nosec B608
        f"ON CONFLICT (id) DO NOTHING"
    )

    count = 0
    for row in _PIR_REQUIREMENTS:
        (pir_type, topic, description,
         collection_priority, status, tasked_to,
         due_by, created_days_ago) = row
        conn.execute(sql, (
            _id(),
            pir_type,
            topic,
            description,
            collection_priority,
            status,
            tasked_to,
            due_by,
            _ts(created_days_ago),
            _ts(created_days_ago * 0.5),  # updated_at slightly newer
        ))
        count += 1
    return count


# ---------------------------------------------------------------------------
# sg_raw_signals + sg_prioritized_signals — 10 paired records
# ---------------------------------------------------------------------------

_RAW_SIGNALS = [
    # (title, body, source, days_ago, geo_hint, tier_used)
    (
        "Russian missile production at Alabuga SEZ rises 40% — satellite analysis",
        "Commercial satellite imagery from Planet Labs shows expanded manufacturing footprint "
        "at the Alabuga Special Economic Zone in Tatarstan. Building 7A shows new assembly "
        "line equipment consistent with Kh-101 cruise missile components. Output increase "
        "estimated at 35-45% above 2025 baseline based on rooftop equipment density.",
        "PlanetLabs-OSINT",
        2.0, "Russia:Tatarstan:55.76N,52.01E", "INTERNET",
    ),
    (
        "Black Sea Fleet surface group sortie — 3 vessels depart Novorossiysk",
        "AIS dark contacts and SAR imagery from Sentinel-1 confirm three Russian naval "
        "vessels departed Novorossiysk on 2026-04-26 at 03:14Z. Vessel signatures match "
        "Ropucha-class LST (x2) and Steregushchiy-class corvette. Track heading 245 degrees.",
        "MarineTraffic-OSINT",
        2.5, "Russia:BlackSea:44.72N,37.77E", "INTERNET",
    ),
    (
        "PLA Type 075 LHA exercises amphibious landing drills — Taiwan Strait",
        "Chinese social media posts geolocated to Dongshan Island training range show "
        "Type 075 LHA Guangxi offloading Type 05 AAV vehicles. Corroborated by "
        "ROCN patrol vessel P-boat report and SIGINT intercepts of PLA Marine frequencies.",
        "Twitter-OSINT-IDF",
        3.5, "China:FujianCoast:23.71N,117.50E", "INTERNET",
    ),
    (
        "Iranian Shahed-136 drone crate shipments via Caspian Sea — commercial manifest",
        "Shipping manifests from Iranian port authority (obtained via FOIA-equivalent) "
        "show 14 containers classified as 'agricultural equipment' transshipped from "
        "Bandar Anzali to Astrakhan between 2026-04-10 and 2026-04-20. Weight/dimensions "
        "consistent with Shahed-136 airframe crates.",
        "Iranian-ShippingDB-OSINT",
        8.0, "Iran:BandarAnzali:37.47N,49.46E", "INTERNET",
    ),
    (
        "Ukrainian logistics hub expansion near Dnipro — pre-positioning indicators",
        "Open-source Ukrainian military bloggers document expanded logistics activity at "
        "Dnipropetrovsk rail hub. Reports of US M1A1 Abrams tank transport flatcars and "
        "Bradley IFV unloading 2026-04-22 through 2026-04-25. Estimated 2x battalion-set.",
        "UA-Telegram-Milblog",
        6.0, "Ukraine:Dnipro:48.46N,35.04E", "INTERNET",
    ),
    (
        "Russian EW jamming activity surges along Sumy axis — GNSS alerts",
        "FAA and Eurocontrol notices record GPS interference zone centered at 51.2N,34.8E "
        "with radius 180km. Aviation reports confirmed degraded GNSS across 2026-04-24 to "
        "2026-04-26. Pattern consistent with Krasukha-4 or Pole-21 EW system deployment.",
        "Eurocontrol-GNSS-Monitor",
        4.0, "Ukraine:Sumy:51.20N,34.80E", "INTERNET",
    ),
    (
        "Taiwan Strait cable cutting incident — subsea infrastructure threat",
        "Taiwan Coast Guard reports cable ship Xin Tai Hai (IMO: 9812345) loitering "
        "above Matsu-Taiwan subsea cable segments on 2026-04-23. Vessel made no "
        "AIS transmissions for 6h. Taiwanese authorities filed diplomatic protest.",
        "TaiwanCGA-Report",
        5.0, "TaiwanStrait:25.20N,121.00E", "FILE_INBOX",
    ),
    (
        "PLA rocket force exercise — DF-26 IRBM TEL movements observed",
        "Open-source Chinese railway forums document DF-26 IRBM transporter-erector-launchers "
        "moved via rail from Xinyang base to Fujian province between 2026-04-19 and 2026-04-22. "
        "8 TELs identified from imagery; range covers Guam. Exercise designation: TIAN-YING 26.",
        "ChinaRailForum-OSINT",
        7.0, "China:Fujian:25.02N,118.82E", "INTERNET",
    ),
    (
        "Russian oil revenue decline 18% — sanctions pressure reduces offensive sustainment",
        "IEA and independent commodity analysts report Russian oil export revenue fell 18% "
        "in Q1 2026 vs Q1 2025, driven by G7 price cap enforcement and tanker insurance "
        "restrictions. Defense ministry budget outlays remain elevated; economic pressure "
        "may constrain 6-12 month sustainment.",
        "IEA-OilMonitor",
        10.0, "Russia:Moscow:55.75N,37.62E", "INTERNET",
    ),
    (
        "ROCN Kang Ding-class frigate patrol intercepts PLA survey vessel near Pratas",
        "Taiwan Defense Ministry statement confirmed ROCN frigate PFG-1203 Kang Ding "
        "conducted intercept of PLA survey vessel Haiyangdizhidi-14 on 2026-04-27 "
        "at approximately 20.7N,116.7E. PLA vessel was operating within Taiwan's EEZ "
        "conducting hydrographic survey without notification.",
        "TaiwanMND-Statement",
        1.0, "SouthChinaSea:20.70N,116.70E", "FILE_INBOX",
    ),
]

# Composite score breakdown for each signal (composite, posterior, discriminability, recency, domain)
_SIGNAL_SCORES = [
    (0.91, 0.28, 0.22, 0.25, 0.16),  # Alabuga production
    (0.84, 0.22, 0.21, 0.24, 0.17),  # Black Sea sortie
    (0.87, 0.25, 0.20, 0.23, 0.19),  # PLA amphibious drills
    (0.89, 0.27, 0.23, 0.20, 0.19),  # Iranian drone shipment
    (0.79, 0.20, 0.18, 0.22, 0.19),  # Ukraine logistics hub
    (0.82, 0.22, 0.20, 0.23, 0.17),  # Russian EW surge
    (0.86, 0.24, 0.22, 0.22, 0.18),  # Taiwan cable cutting
    (0.88, 0.26, 0.21, 0.22, 0.19),  # DF-26 TEL movements
    (0.73, 0.18, 0.17, 0.19, 0.19),  # Russian oil revenue
    (0.83, 0.23, 0.20, 0.24, 0.16),  # ROCN intercept
]

_SIGNAL_RATIONALES = [
    "High posterior shift; missile production signals directly correlate with offensive tempo; "
    "three independent corroborations elevate confidence to collection-reportable.",
    "Vessel movements precede historical Black Sea Fleet strike operations by 48-72h; "
    "temporal window matches Russian force generation pattern.",
    "Amphibious exercise scope and PLA Marine involvement exceeds routine training indicators; "
    "aligns with PIR-002 collection requirement for sortie rate changes.",
    "Caspian transshipment route used in previous Iranian-Russian drone transfers; "
    "container weight/dimensions satisfy EEI-003 threshold.",
    "Pre-positioning volume consistent with brigade-level sustainment; "
    "corroborates CCIR-001 readiness tracking requirement.",
    "EW surge pattern matches Krasukha-4 employment doctrine preceding Russian ground offensive; "
    "geographic overlap with CCIR axis of advance.",
    "Subsea cable interdiction follows Chinese gray-zone pattern; "
    "3-day loiter exceeds routine survey operations signature.",
    "DF-26 TEL movements to Fujian indicate exercise posture shift; "
    "8 TELs represent 25% of estimated inventory — significant indicator.",
    "Economic sustainment signal; lower immediate intel value but high domain coverage "
    "for long-range threat assessment.",
    "Pratas intercept confirms PLA hydrographic survey pattern preceding blockade rehearsal; "
    "tactical-to-operational indicator cross-cut.",
]


def seed_raw_and_prioritized(conn) -> tuple[int, int]:
    ph = _ph(conn)

    # sg_raw_signals — INSERT OR IGNORE on url_hash uniqueness
    raw_sql = (
        f"INSERT OR IGNORE INTO sg_raw_signals "  # nosec B608
        f"(url_hash, title, body, source, signal_date, geo_hint, tier_used, created_at, processed) "
        f"VALUES ({','.join([ph]*9)})"
        if not is_pg(conn)
        else
        f"INSERT INTO sg_raw_signals "  # nosec B608
        f"(url_hash, title, body, source, signal_date, geo_hint, tier_used, created_at, processed) "
        f"VALUES ({','.join([ph]*9)}) ON CONFLICT (url_hash) DO NOTHING"
    )

    # sg_prioritized_signals — autoincrement, insert referencing raw_signal_id
    pri_cols = (
        "raw_signal_id", "composite_score", "posterior_shift_score",
        "source_discriminability_score", "temporal_recency_score",
        "domain_coverage_score", "rationale", "run_at", "created_at",
        "is_read", "promoted_to_kg", "annotation",
    )
    pri_sql = (
        f"INSERT INTO sg_prioritized_signals ({','.join(pri_cols)}) "  # nosec B608
        f"VALUES ({','.join([ph]*len(pri_cols))})"
    )

    raw_inserted = 0
    pri_inserted = 0
    run_ts = _ts(0)

    for i, (sig, scores, rationale) in enumerate(
        zip(_RAW_SIGNALS, _SIGNAL_SCORES, _SIGNAL_RATIONALES)
    ):
        title, body, source, days_ago, geo_hint, tier_used = sig
        composite, posterior, discriminability, recency, domain = scores
        url_hash = _url_hash(title)

        # Insert raw signal
        conn.execute(raw_sql, (
            url_hash, title, body, source,
            _date(days_ago), geo_hint, tier_used,
            _ts(days_ago), 1,
        ))
        raw_inserted += 1

        # Retrieve the id of the just-inserted (or pre-existing) raw signal
        row = conn.execute(
            f"SELECT id FROM sg_raw_signals WHERE url_hash = {ph}",  # nosec B608
            (url_hash,),
        ).fetchone()
        if row is None:
            continue
        raw_id = row[0] if isinstance(row, (list, tuple)) else row["id"]

        # Insert prioritized record
        conn.execute(pri_sql, (
            raw_id,
            round(composite, 4),
            round(posterior, 4),
            round(discriminability, 4),
            round(recency, 4),
            round(domain, 4),
            rationale,
            run_ts,
            _ts(days_ago),
            0, 0, "",
        ))
        pri_inserted += 1

    return raw_inserted, pri_inserted


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed sg_conflict_events, sg_hitl_items, sg_pir_requirements, "
                    "sg_raw_signals, sg_prioritized_signals with realistic demo data."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON instead of human-readable text",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        _bootstrap_tables(conn)

        n_events = seed_conflict_events(conn)
        n_hitl = seed_hitl_items(conn)
        n_pir = seed_pir_requirements(conn)
        n_raw, n_pri = seed_raw_and_prioritized(conn)
        conn.commit()
    finally:
        conn.close()

    summary = {
        "sg_conflict_events":     n_events,
        "sg_hitl_items":          n_hitl,
        "sg_pir_requirements":    n_pir,
        "sg_raw_signals":         n_raw,
        "sg_prioritized_signals": n_pri,
        "total":                  n_events + n_hitl + n_pir + n_raw + n_pri,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("Seeded:")
        print(f"  sg_conflict_events:     {n_events} rows  (12 cyber ops + 15 kinetic)")
        print(f"  sg_hitl_items:          {n_hitl} rows  (6 pending, 1 approved, 1 rejected)")
        print(f"  sg_pir_requirements:    {n_pir} rows  (3 PIR, 2 CCIR, 3 EEI)")
        print(f"  sg_raw_signals:         {n_raw} rows")
        print(f"  sg_prioritized_signals: {n_pri} rows")
        print(f"  total:                  {summary['total']} rows")


if __name__ == "__main__":
    main()
