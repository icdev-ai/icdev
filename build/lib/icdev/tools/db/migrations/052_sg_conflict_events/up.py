#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 052 — Create sg_conflict_events table.

Base conflict/threat event store for the Strategos geospatial intelligence
engine.  Records frontline positions, GDELT narrative events, STIX/cyber
operations, kinetic strikes, humanitarian incidents, and diplomatic activities.

Column notes
------------
geometry   : WKT string (SQLite) or PostGIS GEOMETRY (PostgreSQL, requires
             the postgis extension).  Stores the spatial footprint of the event
             as a point, linestring, or polygon in EPSG:4326.
event_date : calendar date of the event, separate from the ingestion timestamp.
metadata   : arbitrary JSON payload (TEXT in SQLite, JSONB in PostgreSQL for
             server-side indexing and filtering).
created_at : ingestion timestamp (ISO-8601 text, always UTC).
updated_at : last modification timestamp (ISO-8601 text, always UTC).

Subsequent migrations extend this table:
  053 — add GDELT narrative columns (actor1/2, event_code, goldstein_scale, …)
  055 — add STIX/cyber-op columns (technique_ids, threat_actor, malware_family, …)
"""
from tools.db.storage import get_connection, is_pg

# Keep in sync with any Python constants that reference event_type values.
_EVENT_TYPES = (
    "frontline_position",
    "narrative_event",
    "cyber_op",
    "kinetic",
    "humanitarian",
    "diplomatic",
    "logistics",
)

_INDEXES = [
    (
        "idx_sg_ce_event_type",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_event_type "
        "ON sg_conflict_events(event_type)",
    ),
    (
        "idx_sg_ce_event_date",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_event_date "
        "ON sg_conflict_events(event_date)",
    ),
    (
        "idx_sg_ce_source",
        "CREATE INDEX IF NOT EXISTS idx_sg_ce_source "
        "ON sg_conflict_events(source)",
    ),
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        event_check = "', '".join(_EVENT_TYPES)

        if is_pg():
            # Prefer PostGIS GEOMETRY; fall back to TEXT if the extension is absent.
            # Use pg_extension (a catalog table) to avoid aborting the transaction.
            row = conn.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'postgis'"
            ).fetchone()
            geometry_type = "GEOMETRY" if row else "TEXT"

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS sg_conflict_events (
                    id          TEXT     PRIMARY KEY,
                    event_type  TEXT     NOT NULL
                                         CHECK(event_type IN ('{event_check}')),
                    geometry    {geometry_type},
                    event_date  DATE,
                    source      TEXT,
                    metadata    JSONB,
                    created_at  TEXT     NOT NULL,
                    updated_at  TEXT     NOT NULL
                )
                """
            )
            # Add columns to existing tables (idempotent for fresh DBs that
            # had the table created by a prior migration without these columns).
            for col, col_type in [
                ("geometry",   geometry_type),
                ("event_date", "DATE"),
                ("metadata",   "JSONB"),
                ("updated_at", "TEXT"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE sg_conflict_events "
                        f"ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )
                except Exception:
                    pass  # column already present
        else:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS sg_conflict_events (
                    id          TEXT NOT NULL PRIMARY KEY,
                    event_type  TEXT NOT NULL
                                     CHECK(event_type IN ('{event_check}')),
                    geometry    TEXT,
                    event_date  TEXT,
                    source      TEXT,
                    metadata    TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
                """
            )
            for col, col_type in [
                ("geometry",   "TEXT"),
                ("event_date", "TEXT"),
                ("metadata",   "TEXT"),
                ("updated_at", "TEXT"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE sg_conflict_events ADD COLUMN {col} {col_type}"
                    )
                except Exception:
                    pass  # column already present

        for _name, stmt in _INDEXES:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # index already exists

        conn.commit()
        print("Migration 052 (sg_conflict_events) applied.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
