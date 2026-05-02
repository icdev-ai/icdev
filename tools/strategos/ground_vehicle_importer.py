#!/usr/bin/env python3
# CUI // SP-CTI
"""
Ground Vehicle Importer — loads OSINT/GDELT-derived ground vehicle events
into sg_ground_vehicle_events.

Sources:
  - GDELT GKG (Global Knowledge Graph) filtered for vehicle/armor events
  - Oryx project (vehicle losses) — CSV/JSON export
  - Analyst-submitted JSON sightings
  - Demo synthetic data for testing

Air-gap compatible: --file for local data, --seed for demo.

Usage:
  python -m tools.strategos.ground_vehicle_importer --file data/oryx_losses.json
  python -m tools.strategos.ground_vehicle_importer --gdelt --days 3
  python -m tools.strategos.ground_vehicle_importer --seed
  python -m tools.strategos.ground_vehicle_importer --clear
"""
from __future__ import annotations

import argparse
import json
import random
import urllib.request
from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection, is_pg

GDELT_GKG_API = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query={query}&mode=artlist&maxrecords=50"
    "&format=json&startdatetime={start}&sort=DateDesc"
)

VEHICLE_TYPES = [
    "tank", "armored_vehicle", "ifv", "apc", "artillery",
    "mlrs", "air_defense", "logistics", "engineering",
    "military_truck", "unknown_armor",
]

THREAT_LEVELS = {
    "tank":         "critical",
    "air_defense":  "critical",
    "mlrs":         "high",
    "artillery":    "high",
    "ifv":          "high",
    "armored_vehicle": "medium",
    "apc":          "medium",
    "logistics":    "low",
    "engineering":  "low",
    "military_truck": "low",
    "unknown_armor":   "medium",
}

_GDELT_KEYWORDS = "tank armored vehicle military convoy artillery armor"

# Theater event pools for demo data
_DEMO_EVENTS = [
    # (country, actor, vehicle_type, lat_range, lon_range, n_events)
    ("Ukraine",  "Russian Forces",    "tank",             (47, 31, 52, 38), 15),
    ("Ukraine",  "Ukrainian Forces",  "armored_vehicle",  (47, 31, 52, 38), 12),
    ("Ukraine",  "Russian Forces",    "mlrs",             (47, 31, 52, 38), 8),
    ("Ukraine",  "Wagner PMC",        "ifv",              (47, 31, 52, 38), 6),
    ("Taiwan",   "PLAN",              "armored_vehicle",  (22, 118, 27, 122), 4),
    ("Taiwan",   "ROC Army",          "tank",             (22, 118, 27, 122), 5),
    ("Israel",   "IDF",               "tank",             (29, 33, 34, 36), 7),
    ("Gaza",     "Hamas",             "apc",              (31, 34, 32, 35), 5),
    ("Yemen",    "Houthi",            "air_defense",      (13, 44, 17, 48), 4),
    ("Syria",    "SAA",               "logistics",        (33, 36, 37, 42), 6),
]


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
                f"INSERT INTO sg_ground_vehicle_events "  # nosec B608
                f"(event_id,vehicle_type,lat,lon,country,actor,description,"
                f"event_ts,threat_level,source,source_url) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (
                    r.get("event_id") or "",
                    r.get("vehicle_type") or "unknown_armor",
                    r.get("lat") or 0.0,
                    r.get("lon") or 0.0,
                    r.get("country") or "Unknown",
                    r.get("actor") or "Unknown",
                    r.get("description") or "",
                    r.get("event_ts") or datetime.now(timezone.utc).isoformat(),
                    r.get("threat_level") or "medium",
                    r.get("source") or "osint",
                    r.get("source_url") or "",
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def fetch_gdelt_events(days: int = 3) -> list[dict]:
    """Fetch vehicle-related events from GDELT API."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d%H%M%S")
    url = GDELT_GKG_API.format(query=_GDELT_KEYWORDS.replace(" ", "+"), start=start)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ICDEV-Strategos/1.0 (intelligence research)"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    articles = data.get("articles") or []
    rows = []
    for i, art in enumerate(articles):
        rows.append({
            "event_id":    f"gdelt-{start}-{i}",
            "vehicle_type": "unknown_armor",
            "lat":          float(art.get("latitude") or 0),
            "lon":          float(art.get("longitude") or 0),
            "country":      art.get("location") or "Unknown",
            "actor":        "Unknown",
            "description":  (art.get("title") or "")[:500],
            "event_ts":     art.get("seendate", datetime.now(timezone.utc).isoformat()),
            "threat_level": "medium",
            "source":       "gdelt",
            "source_url":   art.get("url") or "",
        })
    return rows


def load_json_file(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    return data.get("events") or data.get("records") or []


def seed_demo_data() -> int:
    random.seed(77)
    base_time = datetime.now(timezone.utc) - timedelta(days=7)
    rows = []
    for entry in _DEMO_EVENTS:
        country, actor, vtype, (lat_min, lon_min, lat_max, lon_max), n = entry
        for i in range(n):
            lat = lat_min + random.uniform(0, lat_max - lat_min)
            lon = lon_min + random.uniform(0, lon_max - lon_min)
            ts = base_time + timedelta(hours=random.uniform(0, 168))
            lost = random.random() > 0.5
            desc = (
                f"{'Destroyed' if lost else 'Observed'} {vtype.replace('_',' ')} "
                f"near {'grid ' + str(random.randint(100, 999))}. "
                f"Source: OSINT visual confirmation."
            )
            rows.append({
                "event_id":    f"demo-{country[:3].upper()}-{i:04d}",
                "vehicle_type": vtype,
                "lat":          round(lat, 4),
                "lon":          round(lon, 4),
                "country":      country,
                "actor":        actor,
                "description":  desc,
                "event_ts":     ts.isoformat(),
                "threat_level": THREAT_LEVELS.get(vtype, "medium"),
                "source":       "demo",
                "source_url":   "",
            })
    return insert_rows(rows)


def clear_all() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sg_ground_vehicle_events")
        conn.commit()
        print("sg_ground_vehicle_events cleared.")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Ground vehicle event importer for Strategos")
    p.add_argument("--file",  help="Local JSON file of vehicle events")
    p.add_argument("--gdelt", action="store_true", help="Fetch from GDELT API")
    p.add_argument("--days",  type=int, default=3, help="Days back for GDELT fetch")
    p.add_argument("--seed",  action="store_true", help="Insert demo data")
    p.add_argument("--clear", action="store_true", help="Clear all rows")
    args = p.parse_args()

    if args.clear:
        clear_all()
        return

    if args.seed:
        n = seed_demo_data()
        print(f"Seeded {n} demo ground vehicle event rows.")
        return

    if args.file:
        records = load_json_file(args.file)
        n = insert_rows(records)
        print(f"Inserted {n} ground vehicle event rows from {args.file}.")
        return

    if args.gdelt:
        rows = fetch_gdelt_events(days=args.days)
        n = insert_rows(rows)
        print(f"Inserted {n} GDELT vehicle event rows.")
        return

    p.print_help()


if __name__ == "__main__":
    main()
