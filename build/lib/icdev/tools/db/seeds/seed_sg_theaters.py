#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed sg_theaters and derive initial sg_tracks from sg_vessel_tracks.

Theaters: INDOPACOM, EUCOM, CENTCOM, AFRICOM with bounding-box AOR WKT.
Tracks: promote each distinct vessel's latest position to sg_tracks
        assigned to the matching theater.

Idempotent — INSERT ... WHERE NOT EXISTS.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Theater definitions: (id, name, code, min_lat, max_lat, min_lon, max_lon, commander, priority)
THEATERS = [
    (
        "theater-indopacom",
        "Indo-Pacific Command",
        "INDOPACOM",
        -10.0, 60.0, 90.0, 180.0,
        "ADM Samuel Paparo",
        1,
    ),
    (
        "theater-eucom",
        "European Command",
        "EUCOM",
        30.0, 72.0, -15.0, 50.0,
        "GEN Christopher Cavoli",
        2,
    ),
    (
        "theater-centcom",
        "Central Command",
        "CENTCOM",
        5.0, 45.0, 30.0, 75.0,
        "GEN Michael Kurilla",
        3,
    ),
    (
        "theater-africom",
        "Africa Command",
        "AFRICOM",
        -35.0, 38.0, -20.0, 55.0,
        "GEN Michael Langley",
        4,
    ),
]


def _assign_theater(lat: float, lon: float) -> str | None:
    for tid, _, _, min_lat, max_lat, min_lon, max_lon, _, _ in THEATERS:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return tid
    return None


def run() -> dict:
    conn = get_connection()
    theaters_inserted = 0
    tracks_inserted = 0
    now = _now()

    try:
        # --- seed theaters ---
        for tid, name, code, min_lat, max_lat, min_lon, max_lon, cmd, pri in THEATERS:
            exists = conn.execute(
                "SELECT 1 FROM sg_theaters WHERE id=%s", (tid,)
            ).fetchone()
            if not exists:
                aor_wkt = (
                    f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
                    f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
                )
                conn.execute(
                    "INSERT INTO sg_theaters "
                    "(id, name, code, area_wkt, commander, status, priority, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s)",
                    (tid, name, code, aor_wkt, cmd, pri, now, now),
                )
                theaters_inserted += 1

        # --- derive tracks from vessel positions ---
        vessels = conn.execute(
            "SELECT DISTINCT ON (mmsi) mmsi, vessel_name, vessel_type, lat, lon, speed, heading, ts "
            "FROM sg_vessel_tracks ORDER BY mmsi, ts DESC"
        ).fetchall()

        for v in vessels:
            lat = float(v["lat"] or 0)
            lon = float(v["lon"] or 0)
            theater_id = _assign_theater(lat, lon)
            if not theater_id:
                continue
            entity_id = v["mmsi"]
            location_wkt = f"POINT({lon} {lat})"
            # ensure entity exists (sg_tracks.entity_id FK → sg_entities.id)
            conn.execute(
                "INSERT INTO sg_entities "
                "(id, theater_id, name, entity_type, classification, location_wkt, "
                "status, source, created_at, updated_at) "
                "VALUES (%s,%s,%s,'vessel','UNCLASSIFIED',%s,'active','seeded',%s,%s) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    entity_id, theater_id,
                    v["vessel_name"] or f"MMSI-{entity_id}",
                    location_wkt, now, now,
                ),
            )
            exists = conn.execute(
                "SELECT 1 FROM sg_tracks WHERE entity_id=%s", (entity_id,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO sg_tracks "
                    "(id, theater_id, entity_id, track_type, status, location_wkt, "
                    "velocity_mps, heading_deg, classification, track_ts, metadata_json, created_at) "
                    "VALUES (%s,%s,%s,'vessel','active',%s,%s,%s,'UNCLASSIFIED',%s,%s,%s)",
                    (
                        str(uuid.uuid4()),
                        theater_id,
                        entity_id,
                        location_wkt,
                        float(v["speed"] or 0) * 0.514,  # knots → m/s
                        float(v["heading"] or 0),
                        v["ts"] or now,
                        json.dumps({"vessel_name": v["vessel_name"], "vessel_type": v["vessel_type"]}),
                        now,
                    ),
                )
                tracks_inserted += 1

        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        return {"status": "error", "error": str(exc)}

    conn.close()
    return {
        "status": "ok",
        "theaters_inserted": theaters_inserted,
        "tracks_inserted": tracks_inserted,
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))
