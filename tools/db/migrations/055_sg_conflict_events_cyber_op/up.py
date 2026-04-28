# CUI // SP-CTI
"""Migration 055 — Create sg_conflict_events and add cyber_op columns.

sg_conflict_events is the canonical Strategos conflict/threat event table.
This migration creates it if absent (fresh DB), then adds the STIX/cyber_op
columns needed by stix_importer.py.  Running on a DB that already has the
table from a prior session is safe — each ALTER is wrapped in try/except.

Full column set after this migration
--------------------------------------
id              TEXT PRIMARY KEY
event_type      TEXT NOT NULL          — 'narrative_event' | 'cyber_op' | …
source          TEXT                   — 'gdelt' | 'cert-ua' | 'stix' | …
external_id     TEXT                   — dedup key per source
event_ts        TEXT                   — ISO-8601 event timestamp
description     TEXT
actor1          TEXT                   — GDELT Actor1Name or threat-actor
actor2          TEXT
event_code      TEXT                   — CAMEO code (GDELT)
goldstein_scale REAL
lat             REAL
lon             REAL
aoi             TEXT                   — 'ukraine' | 'taiwan' | NULL
avg_tone        REAL
num_mentions    INTEGER
source_url      TEXT
technique_ids   TEXT                   — comma-separated MITRE T-codes
threat_actor    TEXT                   — attributed threat-actor name
malware_family  TEXT                   — malware / tool family names
confidence      INTEGER                — STIX confidence 0–100
metadata_json   TEXT
"""
from tools.db.storage import get_connection, is_pg

_CREATE_TABLE = """
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
    metadata_json   TEXT
)
"""

_NEW_COLUMNS = [
    ("technique_ids",  "TEXT"),
    ("threat_actor",   "TEXT"),
    ("malware_family", "TEXT"),
    ("confidence",     "INTEGER"),
]

_INDEXES = [
    (
        "idx_sg_ce_source_ext",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sg_ce_source_ext "
        "ON sg_conflict_events(source, external_id)",
    ),
    (
        "idx_sg_ce_event_type",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_event_type "
        "ON sg_conflict_events(event_type)",
    ),
    (
        "idx_sg_ce_aoi",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_aoi "
        "ON sg_conflict_events(aoi)",
    ),
    (
        "idx_sg_ce_event_code",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_event_code "
        "ON sg_conflict_events(event_code)",
    ),
    (
        "idx_sg_ce_technique_ids",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_technique_ids "
        "ON sg_conflict_events(technique_ids)",
    ),
]


def up(conn=None) -> None:
    conn = get_connection()

    conn.execute(_CREATE_TABLE)

    for col, col_type in _NEW_COLUMNS:
        try:
            if is_pg():
                conn.execute(
                    f"ALTER TABLE sg_conflict_events "
                    f"ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )
            else:
                conn.execute(
                    f"ALTER TABLE sg_conflict_events ADD COLUMN {col} {col_type}"
                )
        except Exception:
            pass  # column already present

    for _name, stmt in _INDEXES:
        try:
            conn.execute(stmt)
        except Exception:
            pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 055 applied.")
