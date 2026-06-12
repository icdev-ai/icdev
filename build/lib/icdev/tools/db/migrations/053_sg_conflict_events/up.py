# CUI // SP-CTI
"""Migration 053 — Add GDELT narrative-event columns to sg_conflict_events.

sg_conflict_events already exists with core columns (event_type, source,
external_id, event_ts, metadata_json, …).  This migration adds the GDELT-
specific fields needed by gdelt_importer.py so they are queryable as first-
class columns rather than buried in metadata_json.

Added columns
-------------
actor1          TEXT  — GDELT Actor1Name
actor2          TEXT  — GDELT Actor2Name
event_code      TEXT  — CAMEO event code
goldstein_scale REAL  — Goldstein conflict scale (−10 to +10)
lat             REAL  — ActionGeo latitude
lon             REAL  — ActionGeo longitude
aoi             TEXT  — 'ukraine' or 'taiwan'
avg_tone        REAL  — GDELT AvgTone
num_mentions    INTEGER — GDELT NumMentions
source_url      TEXT  — original article URL
"""
from tools.db.storage import get_connection, is_pg

_NEW_COLUMNS = [
    ("actor1",          "TEXT"),
    ("actor2",          "TEXT"),
    ("event_code",      "TEXT"),
    ("goldstein_scale", "REAL"),
    ("lat",             "REAL"),
    ("lon",             "REAL"),
    ("aoi",             "TEXT"),
    ("avg_tone",        "REAL"),
    ("num_mentions",    "INTEGER"),
    ("source_url",      "TEXT"),
]


def up(conn=None) -> None:
    conn = get_connection()

    for col, col_type in _NEW_COLUMNS:
        try:
            if is_pg():
                conn.execute(
                    f"ALTER TABLE sg_conflict_events ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )
            else:
                conn.execute(
                    f"ALTER TABLE sg_conflict_events ADD COLUMN {col} {col_type}"
                )
        except Exception:
            pass  # column already present

    # Index on aoi for AOI-scoped queries
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ce_aoi "
            "ON sg_conflict_events(aoi)"
        )
    except Exception:
        pass

    # Index on event_code for CAMEO filtering
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ce_event_code "
            "ON sg_conflict_events(event_code)"
        )
    except Exception:
        pass

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 053 applied.")
