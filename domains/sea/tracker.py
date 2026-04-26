"""AIS track processor — vessel position history from sg_tracks.

Provides:
  get_vessel_history(mmsi, limit)   — ordered position list for one vessel
  get_latest_positions()            — most-recent fix per MMSI
  get_vessel_summary()              — fleet-level counts and activity window
  get_kalibr_positions()            — latest fix for each Kalibr-capable vessel

All queries run against apps/geosigint/data/geosigint.db via the shared
get_connection() helper; no duplicate connections opened.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from apps.geosigint.models import get_connection  # noqa: E402


def get_vessel_history(mmsi: str, limit: int = 200) -> list[dict]:
    """Return chronological position history for *mmsi* (oldest-first).

    Each entry: {track_id, mmsi, lat, lon, speed, heading, timestamp,
                 vessel_type, msg_type}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT track_id, mmsi, lat, lon, speed, heading,
                   timestamp, vessel_type, msg_type
            FROM   sg_tracks
            WHERE  mmsi = ?
            ORDER  BY timestamp ASC
            LIMIT  ?
            """,
            (mmsi, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_positions(limit: int = 2000) -> list[dict]:
    """Return the most-recent track fix per MMSI across the whole fleet.

    Each entry: {mmsi, lat, lon, speed, heading, timestamp, vessel_type}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.mmsi, t.lat, t.lon, t.speed, t.heading,
                   t.timestamp, t.vessel_type
            FROM   sg_tracks t
            INNER  JOIN (
                SELECT mmsi, MAX(timestamp) AS ts
                FROM   sg_tracks
                GROUP  BY mmsi
            ) latest ON t.mmsi = latest.mmsi AND t.timestamp = latest.ts
            ORDER  BY t.timestamp DESC
            LIMIT  ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_vessel_summary() -> dict:
    """Fleet-level statistics derived from sg_tracks."""
    conn = get_connection()
    try:
        total_tracks = conn.execute(
            "SELECT COUNT(*) c FROM sg_tracks"
        ).fetchone()["c"]
        unique_vessels = conn.execute(
            "SELECT COUNT(DISTINCT mmsi) c FROM sg_tracks"
        ).fetchone()["c"]
        row = conn.execute(
            "SELECT MIN(timestamp) earliest, MAX(timestamp) latest FROM sg_tracks"
        ).fetchone()
        type_dist = [
            dict(r) for r in conn.execute(
                """
                SELECT vessel_type, COUNT(DISTINCT mmsi) AS vessel_count
                FROM   sg_tracks
                GROUP  BY vessel_type
                ORDER  BY vessel_count DESC
                """
            ).fetchall()
        ]
        return {
            "total_tracks": total_tracks,
            "unique_vessels": unique_vessels,
            "earliest": row["earliest"],
            "latest": row["latest"],
            "type_distribution": type_dist,
        }
    finally:
        conn.close()


def build_orbat_kg_nodes() -> dict:
    """Sync vessel_orbat → vessel_kg_nodes.

    Creates or updates one KG node per ORBAT entry so the /api/orbat/kg-nodes
    endpoint and the knowledge-graph overlay stay current.

    Returns {"status", "synced", "skipped"}.
    """
    from datetime import datetime, timezone
    import json as _json

    conn = get_connection()
    synced = 0
    skipped = 0
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vessel_orbat'"
        ).fetchone()
        if tbl is None:
            return {"status": "ok", "synced": 0, "skipped": 0, "note": "vessel_orbat table absent"}

        rows = conn.execute("SELECT * FROM vessel_orbat").fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            node_id = f"vessel:{row['mmsi']}"
            props = _json.dumps({
                "hull_number": row["hull_number"],
                "displacement_t": row["displacement_t"],
                "kalibr_range_km": row["kalibr_range_km"],
                "notes": row["notes"],
            })
            conn.execute(
                """
                INSERT INTO vessel_kg_nodes
                    (node_id, mmsi, vessel_name, nation, entity_type,
                     kalibr_capable, properties_json, created_at)
                VALUES (?, ?, ?, ?, 'vessel', ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    vessel_name    = excluded.vessel_name,
                    nation         = excluded.nation,
                    kalibr_capable = excluded.kalibr_capable,
                    properties_json = excluded.properties_json
                """,
                (node_id, row["mmsi"], row["vessel_name"], row["nation"],
                 row["kalibr_capable"], props, now),
            )
            synced += 1
        conn.commit()
        return {"status": "ok", "synced": synced, "skipped": skipped}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "synced": synced, "skipped": skipped}
    finally:
        conn.close()


def get_kalibr_positions() -> list[dict]:
    """Latest position for each Kalibr-capable vessel in vessel_orbat.

    Joins sg_tracks (latest fix per MMSI) with vessel_orbat where
    kalibr_capable = 1.  Returns empty list when the orbat table is
    absent or has no Kalibr entries with track data.

    Each entry: {mmsi, lat, lon, timestamp, vessel_name, vessel_class,
                 nation, kalibr_capable}
    """
    conn = get_connection()
    try:
        # Guard: table may not exist in older DB instances
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vessel_orbat'"
        ).fetchone()
        if tbl is None:
            return []

        rows = conn.execute(
            """
            SELECT t.mmsi, t.lat, t.lon, t.timestamp,
                   o.vessel_name, o.vessel_class, o.nation, o.kalibr_capable
            FROM   vessel_orbat o
            INNER  JOIN sg_tracks t ON t.mmsi = o.mmsi
            INNER  JOIN (
                SELECT mmsi, MAX(timestamp) AS ts
                FROM   sg_tracks
                GROUP  BY mmsi
            ) latest ON t.mmsi = latest.mmsi AND t.timestamp = latest.ts
            WHERE  o.kalibr_capable = 1
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
