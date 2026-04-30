#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed sg_vessel_tracks with realistic multi-waypoint vessel routes."""

import uuid
from datetime import datetime, timezone, timedelta
from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Vessel definitions + waypoints
# Each waypoint is (lat, lon, speed_kt, heading_deg)
# Timestamps are auto-spaced based on speed and distance
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 4, 28, 6, 0, 0, tzinfo=timezone.utc)

VESSELS = [
    # ── Black Sea ───────────────────────────────────────────────────────────
    {
        "mmsi": "273123456",
        "vessel_name": "SEVASTOPOL SHADOW",
        "vessel_type": "warship",
        "flag": "RU",
        "waypoints": [
            (44.62, 33.52, 14.0, 270),  # Sevastopol
            (44.45, 32.80, 14.0, 250),
            (44.20, 32.10, 12.0, 235),
            (43.95, 31.50, 12.0, 220),
            (43.60, 31.20, 10.0, 200),
            (43.30, 31.50, 10.0, 170),
            (43.10, 31.90, 12.0, 150),
            (43.00, 32.40, 14.0, 135),
            (43.20, 33.10, 14.0, 55),
            (43.60, 33.80, 12.0, 35),
            (44.10, 34.30, 12.0, 20),
            (44.62, 33.52, 14.0, 290),  # return
        ],
    },
    {
        "mmsi": "273456789",
        "vessel_name": "NOVORISK-7",
        "vessel_type": "cargo",
        "flag": "RU",
        "waypoints": [
            (44.72, 37.77, 11.0, 180),  # Novorossiysk
            (44.40, 37.50, 11.0, 195),
            (44.00, 37.00, 11.0, 205),
            (43.60, 36.20, 10.0, 220),
            (43.30, 35.40, 10.0, 230),
            (43.10, 34.60, 9.0,  245),
            (43.00, 33.80, 9.0,  260),
            (43.10, 33.00, 9.0,  275),
            (43.30, 32.10, 10.0, 290),
            (43.60, 31.60, 10.0, 305),
        ],
    },
    {
        "mmsi": "271987654",
        "vessel_name": "BOSPHORUS RUNNER",
        "vessel_type": "tanker",
        "flag": "TR",
        "waypoints": [
            (41.05, 29.05, 13.0, 0),   # Istanbul Bosphorus
            (41.40, 29.10, 13.0, 355),
            (41.80, 29.20, 12.0, 10),
            (42.30, 29.50, 12.0, 20),
            (42.80, 30.20, 11.0, 35),
            (43.30, 31.00, 11.0, 40),
            (43.80, 31.90, 11.0, 35),
            (44.30, 32.70, 11.0, 30),
            (44.70, 33.50, 12.0, 20),
            (45.10, 34.40, 12.0, 25),
            (45.50, 35.20, 13.0, 20),
            (46.00, 36.00, 13.0, 25),
        ],
    },
    # ── Taiwan Strait / South China Sea ─────────────────────────────────────
    {
        "mmsi": "413001234",
        "vessel_name": "LIAONING PATROL",
        "vessel_type": "warship",
        "flag": "CN",
        "waypoints": [
            (26.10, 120.60, 18.0, 10),  # East of Fujian
            (26.50, 120.80, 18.0, 15),
            (27.00, 121.20, 17.0, 25),
            (27.50, 121.60, 17.0, 20),
            (28.00, 121.90, 16.0, 15),
            (28.60, 122.10, 16.0, 5),
            (29.20, 122.00, 16.0, 355),
            (29.80, 121.80, 17.0, 345),
            (30.30, 121.50, 17.0, 340),
            (30.80, 121.10, 18.0, 335),
            (31.20, 120.70, 18.0, 330),
            (31.50, 120.30, 17.0, 320),
            (31.70, 119.80, 16.0, 310),
        ],
    },
    {
        "mmsi": "477654321",
        "vessel_name": "ORIENT TRADER",
        "vessel_type": "cargo",
        "flag": "HK",
        "waypoints": [
            (22.30, 114.10, 15.0, 135),  # Hong Kong
            (22.00, 114.50, 15.0, 145),
            (21.60, 115.10, 14.0, 150),
            (21.10, 115.80, 14.0, 155),
            (20.50, 116.60, 13.0, 160),
            (19.80, 117.50, 13.0, 155),
            (19.20, 118.30, 13.0, 150),
            (18.80, 119.20, 14.0, 140),
            (18.50, 120.10, 14.0, 130),
            (18.40, 121.00, 13.0, 120),
            (18.50, 121.90, 13.0, 80),
            (18.70, 122.70, 14.0, 60),
        ],
    },
    {
        "mmsi": "525789012",
        "vessel_name": "DARK PHOENIX",
        "vessel_type": "unknown",
        "flag": None,
        "waypoints": [
            (10.50, 109.50, 8.0,  320),  # Starting near Vietnam coast
            (11.00, 109.20, 8.0,  340),
            (11.60, 109.10, 7.0,  355),
            (12.30, 109.20, 7.0,  10),
            (13.00, 109.50, 7.0,  25),
            (13.60, 110.00, 8.0,  35),
            (14.20, 110.70, 8.0,  40),
            (14.70, 111.50, 8.0,  50),
            (15.10, 112.50, 7.0,  60),
            (15.30, 113.50, 7.0,  75),
            (15.40, 114.50, 6.0,  85),
            (15.30, 115.50, 6.0,  95),
            (15.00, 116.30, 7.0, 110),
            (14.60, 117.00, 8.0, 120),
            (14.20, 117.80, 8.0, 130),
        ],
    },
    # ── Yellow Sea ──────────────────────────────────────────────────────────
    {
        "mmsi": "441099876",
        "vessel_name": "BUSAN STAR",
        "vessel_type": "fishing",
        "flag": "KR",
        "waypoints": [
            (35.10, 128.90, 9.0, 285),  # Busan
            (35.20, 128.10, 9.0, 275),
            (35.40, 127.30, 8.0, 265),
            (35.60, 126.50, 8.0, 270),
            (35.80, 125.70, 8.0, 275),
            (36.10, 124.90, 7.0, 285),
            (36.30, 124.10, 7.0, 295),
            (36.50, 123.40, 8.0, 300),
            (36.70, 122.70, 8.0, 310),
            (36.80, 122.00, 7.0, 320),
            (36.70, 121.30, 7.0, 345),
            (36.50, 120.70, 7.0, 355),
            (36.20, 120.20, 8.0, 10),
        ],
    },
]


def _haversine_nm(lat1, lon1, lat2, lon2):
    """Approximate nautical miles between two points."""
    import math
    R = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def seed(conn=None):
    _conn = conn or get_connection()
    inserted = 0
    try:
        # Check if already seeded
        count = _conn.execute("SELECT COUNT(*) FROM sg_vessel_tracks").fetchone()[0]
        if count > 0:
            print(f"[seed_vessel_tracks] already seeded ({count} rows), skipping")
            return

        for vessel in VESSELS:
            t = BASE_TIME
            for i, (lat, lon, speed, heading) in enumerate(vessel["waypoints"]):
                if i > 0:
                    prev_lat, prev_lon = vessel["waypoints"][i-1][0], vessel["waypoints"][i-1][1]
                    dist_nm = _haversine_nm(prev_lat, prev_lon, lat, lon)
                    hours = dist_nm / max(speed, 1.0)
                    t = t + timedelta(hours=hours)

                _conn.execute(
                    "INSERT INTO sg_vessel_tracks (id, mmsi, vessel_name, vessel_type, flag, lat, lon, speed, heading, ts)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        vessel["mmsi"],
                        vessel["vessel_name"],
                        vessel["vessel_type"],
                        vessel["flag"],
                        lat, lon, speed, heading,
                        t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )
                inserted += 1

        _conn.commit()
        print(f"[seed_vessel_tracks] inserted {inserted} track points for {len(VESSELS)} vessels")
    except Exception as e:
        print(f"[seed_vessel_tracks] error: {e}")
        raise
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    seed()
