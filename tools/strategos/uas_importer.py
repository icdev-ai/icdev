#!/usr/bin/env python3
# CUI // SP-CTI
"""
UAS/Drone Importer — loads unmanned aerial system tracks into sg_uas_tracks.

Sources:
  - FAA DroneZone public registration data (CSV export)
  - ADS-B exchange filtered for small/low-altitude squawks
  - Manual JSON ingestion for analyst-reported UAS sightings

Air-gap compatible: --file for local data, --seed for demo.

Usage:
  python -m tools.strategos.uas_importer --file data/uas_sightings.json
  python -m tools.strategos.uas_importer --seed
  python -m tools.strategos.uas_importer --clear
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection, is_pg

# UAS type classification
UAS_TYPES = [
    "fixed_wing", "rotary", "multi_rotor", "hybrid",
    "kamikaze", "loitering_munition", "navy_uav", "surveillance",
]

THREAT_LEVELS = ["low", "medium", "high", "critical"]

# Demo UAS operators / actors for synthetic data
_DEMO_ACTORS = [
    ("UA-001", "Ukrainian AF",    "Ukraine",   "multi_rotor",       "surveillance", "low"),
    ("RU-001", "Russian Forces",  "Russia",    "fixed_wing",         "kamikaze",     "critical"),
    ("RU-002", "Russian Forces",  "Russia",    "loitering_munition", "kamikaze",     "critical"),
    ("CN-001", "PLAN",            "China",     "navy_uav",           "surveillance", "high"),
    ("US-001", "US Air Force",    "USA",       "fixed_wing",         "recon",        "low"),
    ("IR-001", "IRGC",            "Iran",      "loitering_munition", "kamikaze",     "high"),
    ("NK-001", "KPA",             "North Korea","fixed_wing",         "surveillance", "medium"),
    ("TR-001", "Turkish AF",      "Turkey",    "multi_rotor",        "surveillance", "low"),
    ("UA-002", "Ukrainian Mil",   "Ukraine",   "rotary",             "strike",       "medium"),
    ("GZ-001", "Hamas",           "Gaza",      "multi_rotor",        "ied_drop",     "high"),
]

_THEATER_BBOXES = {
    "ukraine":   (46, 28, 52, 40),
    "taiwan":    (22, 118, 27, 123),
    "middleeast":(29, 34, 34, 40),
    "southchina":(10, 110, 25, 125),
}


def insert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        cur = conn.cursor()
        inserted = 0
        for r in rows:
            cur.execute(
                f"INSERT INTO sg_uas_tracks "  # nosec B608
                f"(uas_id,operator,uas_type,lat,lon,altitude_m,speed_kts,"
                f"heading,threat_level,operator_country,payload,track_ts,"
                f"source,anomaly_flag) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (
                    r["uas_id"], r["operator"], r["uas_type"],
                    r["lat"], r["lon"], r["altitude_m"], r["speed_kts"],
                    r["heading"], r["threat_level"], r["operator_country"],
                    r.get("payload", ""), r["track_ts"], r["source"],
                    r.get("anomaly_flag", 0),
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def load_json_file(path: str) -> list[dict]:
    """Load UAS sighting records from a JSON file."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    return data.get("uas_tracks") or data.get("records") or []


def json_records_to_rows(records: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in records:
        rows.append({
            "uas_id":          r.get("uas_id") or r.get("id") or "UNK",
            "operator":        r.get("operator") or "Unknown",
            "uas_type":        r.get("uas_type") or r.get("type") or "fixed_wing",
            "lat":             float(r.get("lat") or r.get("latitude") or 0),
            "lon":             float(r.get("lon") or r.get("longitude") or 0),
            "altitude_m":      float(r.get("altitude_m") or r.get("alt") or 500),
            "speed_kts":       float(r.get("speed_kts") or r.get("speed") or 50),
            "heading":         float(r.get("heading") or 0),
            "threat_level":    r.get("threat_level") or "medium",
            "operator_country":r.get("operator_country") or r.get("country") or "Unknown",
            "payload":         r.get("payload") or "",
            "track_ts":        r.get("track_ts") or r.get("timestamp") or now,
            "source":          r.get("source") or "analyst",
            "anomaly_flag":    int(bool(r.get("anomaly_flag") or r.get("anomaly"))),
        })
    return rows


def seed_demo_data(n_per_actor: int = 12) -> int:
    random.seed(99)
    base_time = datetime.now(timezone.utc) - timedelta(hours=8)
    rows = []
    for actor in _DEMO_ACTORS:
        uas_id, operator, country, uas_type, payload, threat = actor
        # Pick theater based on country
        if country in ("Ukraine", "Russia"):
            bbox = _THEATER_BBOXES["ukraine"]
        elif country in ("China", "USA"):
            bbox = _THEATER_BBOXES["taiwan"]
        elif country in ("Iran", "Hamas"):
            bbox = _THEATER_BBOXES["middleeast"]
        else:
            bbox = _THEATER_BBOXES["southchina"]

        lat_min, lon_min, lat_max, lon_max = bbox
        base_lat = lat_min + random.uniform(0, lat_max - lat_min)
        base_lon = lon_min + random.uniform(0, lon_max - lon_min)
        heading  = random.uniform(0, 360)
        speed    = random.uniform(20, 120)

        for step in range(n_per_actor):
            ts = base_time + timedelta(minutes=step * 10 + random.randint(0, 5))
            # simple linear propagation
            lat = base_lat + step * 0.01 * math.cos(math.radians(heading))
            lon = base_lon + step * 0.01 * math.sin(math.radians(heading))
            lat = max(lat_min, min(lat_max, lat))
            lon = max(lon_min, min(lon_max, lon))
            anomaly = 1 if threat in ("high", "critical") and step > n_per_actor // 2 else 0
            rows.append({
                "uas_id":           uas_id,
                "operator":         operator,
                "uas_type":         uas_type,
                "lat":              round(lat, 4),
                "lon":              round(lon, 4),
                "altitude_m":       round(random.uniform(50, 3000), 0),
                "speed_kts":        round(speed + random.uniform(-5, 5), 1),
                "heading":          round(heading + random.uniform(-10, 10), 1) % 360,
                "threat_level":     threat,
                "operator_country": country,
                "payload":          payload,
                "track_ts":         ts.isoformat(),
                "source":           "demo",
                "anomaly_flag":     anomaly,
            })
    return insert_rows(rows)


def clear_all() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sg_uas_tracks")
        conn.commit()
        print("sg_uas_tracks cleared.")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="UAS/Drone importer for Strategos")
    p.add_argument("--file",  help="Path to local JSON UAS sightings file")
    p.add_argument("--seed",  action="store_true", help="Insert demo data")
    p.add_argument("--clear", action="store_true", help="Clear all rows")
    args = p.parse_args()

    if args.clear:
        clear_all()
        return

    if args.seed:
        n = seed_demo_data()
        print(f"Seeded {n} demo UAS track rows.")
        return

    if args.file:
        records = load_json_file(args.file)
        rows = json_records_to_rows(records)
        n = insert_rows(rows)
        print(f"Inserted {n} UAS track rows from {args.file}.")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
