#!/usr/bin/env python3
# CUI // SP-CTI
"""
ADS-B Importer — fetches live aircraft tracks from OpenSky Network REST API
and stores them in sg_aircraft_tracks.

OpenSky Network (free, no API key for basic use):
  https://opensky-network.org/api/states/all?lamin=LAT_MIN&lomin=LON_MIN&lamax=LAT_MAX&lomax=LON_MAX

Air-gap compatible: use --file to load a previously saved JSON dump.

Usage:
  python -m tools.strategos.adsb_importer --bbox 20,60,50,140   # INDOPACOM
  python -m tools.strategos.adsb_importer --bbox 45,22,52,40    # Black Sea / Ukraine
  python -m tools.strategos.adsb_importer --file data/states.json
  python -m tools.strategos.adsb_importer --seed                 # insert demo data
  python -m tools.strategos.adsb_importer --clear                # drop all rows
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection, is_pg

OPENSKY_BASE = "https://opensky-network.org/api/states/all"
DEFAULT_TIMEOUT = 20

# OpenSky state vector field indices
_F_ICAO24        = 0
_F_CALLSIGN      = 1
_F_COUNTRY       = 2
_F_TIME_POS      = 3
_F_LAST_CONTACT  = 4
_F_LON           = 5
_F_LAT           = 6
_F_BARO_ALT      = 7
_F_ON_GROUND     = 8
_F_VELOCITY      = 9
_F_TRUE_TRACK    = 10
_F_VERT_RATE     = 11
_F_SQUAWK        = 14
_F_GEO_ALT       = 13

# Aircraft squawk codes that indicate military/special ops
_MILITARY_SQUAWKS = {"7500", "7600", "7700", "7777"}

# Known military ICAO24 prefixes (first 2 chars of ICAO hex)
_MILITARY_PREFIXES = {"ae", "43", "07", "48", "ac"}

_AIRCRAFT_TYPE_MAP: dict[str, str] = {
    "heli": "helicopter",
    "c17":  "transport",
    "c130": "transport",
    "f16":  "fighter",
    "f18":  "fighter",
    "b52":  "bomber",
    "p8":   "maritime_patrol",
    "e3":   "aew",
    "e8":   "jstars",
    "rc135": "recon",
    "u2":   "recon",
    "rq4":  "uav_large",
}


def _classify_aircraft(icao24: str, callsign: str, squawk: str) -> tuple[str, bool]:
    """Return (aircraft_type, military_flag)."""
    cs = (callsign or "").strip().lower()
    sq = (squawk or "").strip()
    ic = (icao24 or "").strip().lower()

    military = (sq in _MILITARY_SQUAWKS) or (ic[:2] in _MILITARY_PREFIXES)

    for kw, ac_type in _AIRCRAFT_TYPE_MAP.items():
        if kw in cs:
            return ac_type, military

    if military:
        return "military", True
    return "commercial", False


def fetch_opensky(
    bbox: tuple[float, float, float, float],
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch live states from OpenSky for the given bounding box."""
    lat_min, lon_min, lat_max, lon_max = bbox
    url = (
        f"{OPENSKY_BASE}"
        f"?lamin={lat_min}&lomin={lon_min}&lamax={lat_max}&lomax={lon_max}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ICDEV-Strategos/1.0 (intelligence research)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("states") or []


def states_to_rows(states: list, source: str = "opensky") -> list[dict]:
    """Convert OpenSky state vectors to row dicts for sg_aircraft_tracks."""
    now_ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for s in states:
        if s is None or s[_F_LAT] is None or s[_F_LON] is None:
            continue
        icao24 = s[_F_ICAO24] or ""
        callsign = (s[_F_CALLSIGN] or "").strip()
        squawk = str(s[_F_SQUAWK]) if s[_F_SQUAWK] else ""
        ac_type, mil_flag = _classify_aircraft(icao24, callsign, squawk)
        ts = s[_F_TIME_POS] or s[_F_LAST_CONTACT]
        if ts:
            track_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        else:
            track_ts = now_ts
        rows.append({
            "icao24":         icao24,
            "callsign":       callsign or icao24.upper(),
            "origin_country": s[_F_COUNTRY] or "Unknown",
            "lat":            float(s[_F_LAT]),
            "lon":            float(s[_F_LON]),
            "baro_altitude":  float(s[_F_BARO_ALT]) if s[_F_BARO_ALT] is not None else None,
            "geo_altitude":   float(s[_F_GEO_ALT])  if s[_F_GEO_ALT]  is not None else None,
            "velocity":       float(s[_F_VELOCITY])  if s[_F_VELOCITY]  is not None else None,
            "true_track":     float(s[_F_TRUE_TRACK]) if s[_F_TRUE_TRACK] is not None else 0.0,
            "vertical_rate":  float(s[_F_VERT_RATE]) if s[_F_VERT_RATE] is not None else 0.0,
            "on_ground":      1 if s[_F_ON_GROUND] else 0,
            "squawk":         squawk,
            "aircraft_type":  ac_type,
            "military_flag":  1 if mil_flag else 0,
            "track_ts":       track_ts,
            "source":         source,
        })
    return rows


def insert_rows(rows: list[dict]) -> int:
    """Insert rows into sg_aircraft_tracks; returns count inserted."""
    if not rows:
        return 0
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        cur = conn.cursor()
        inserted = 0
        for r in rows:
            cur.execute(
                f"INSERT INTO sg_aircraft_tracks "  # nosec B608
                f"(icao24,callsign,origin_country,lat,lon,baro_altitude,"
                f"geo_altitude,velocity,true_track,vertical_rate,on_ground,"
                f"squawk,aircraft_type,military_flag,track_ts,source) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (
                    r["icao24"], r["callsign"], r["origin_country"],
                    r["lat"], r["lon"], r["baro_altitude"], r["geo_altitude"],
                    r["velocity"], r["true_track"], r["vertical_rate"],
                    r["on_ground"], r["squawk"], r["aircraft_type"],
                    r["military_flag"], r["track_ts"], r["source"],
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def seed_demo_data(n: int = 30) -> int:
    """Insert synthetic demo aircraft tracks for testing."""
    random.seed(42)
    base_time = datetime.now(timezone.utc) - timedelta(hours=6)
    rows = []
    # Simulate aircraft in INDOPACOM + Europe theaters
    theaters = [
        (15, 100, 45, 145, "indo"),   # INDOPACOM
        (45, 22,  55,  40, "ukr"),    # Ukraine / Black Sea
    ]
    aircraft_pool = [
        ("ae1234", "USAF01", "United States", "military", True),
        ("43f5a2", "RUSS01", "Russia",         "fighter",  True),
        ("071abc", "RAF001", "United Kingdom",  "transport", True),
        ("480001", "CHNA01", "China",           "military", True),
        ("4b1234", "BA101",  "United Kingdom",  "commercial", False),
        ("ae9999", "USMC99", "United States",   "helicopter", True),
        ("300001", "FRA001", "France",          "fighter", True),
        ("e48001", "TUR001", "Turkey",          "commercial", False),
        ("0daabc", "IDF001", "Israel",          "military", True),
        ("7c1234", "QANT01", "Australia",       "commercial", False),
    ]
    for icao24, callsign, country, ac_type, mil in aircraft_pool * 3:
        t = theaters[random.randint(0, 1)]
        lat_min, lon_min, lat_max, lon_max = t[0], t[1], t[2], t[3]
        for step in range(8):
            ts = base_time + timedelta(minutes=step * 45 + random.randint(0, 10))
            lat = lat_min + random.uniform(0, lat_max - lat_min)
            lon = lon_min + random.uniform(0, lon_max - lon_min)
            rows.append({
                "icao24":         icao24,
                "callsign":       callsign,
                "origin_country": country,
                "lat":            round(lat, 4),
                "lon":            round(lon, 4),
                "baro_altitude":  round(random.uniform(5000, 40000), 0),
                "geo_altitude":   round(random.uniform(5000, 40000), 0),
                "velocity":       round(random.uniform(150, 550), 1),
                "true_track":     round(random.uniform(0, 360), 1),
                "vertical_rate":  round(random.uniform(-5, 5), 2),
                "on_ground":      0,
                "squawk":         "7000",
                "aircraft_type":  ac_type,
                "military_flag":  1 if mil else 0,
                "track_ts":       ts.isoformat(),
                "source":         "demo",
            })
    return insert_rows(rows)


def clear_all() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sg_aircraft_tracks")
        conn.commit()
        print("sg_aircraft_tracks cleared.")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="ADS-B / OpenSky importer for Strategos")
    p.add_argument("--bbox",    help="lat_min,lon_min,lat_max,lon_max")
    p.add_argument("--file",    help="Path to local OpenSky JSON dump")
    p.add_argument("--seed",    action="store_true", help="Insert demo data")
    p.add_argument("--clear",   action="store_true", help="Clear all rows")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = p.parse_args()

    if args.clear:
        clear_all()
        return

    if args.seed:
        n = seed_demo_data()
        print(f"Seeded {n} demo aircraft track rows.")
        return

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
        states = data.get("states") or data if isinstance(data, list) else []
        rows = states_to_rows(states, source="file")
    elif args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            sys.exit("--bbox must be lat_min,lon_min,lat_max,lon_max")
        states = fetch_opensky(tuple(parts), timeout=args.timeout)
        rows = states_to_rows(states)
    else:
        p.print_help()
        return

    n = insert_rows(rows)
    print(f"Inserted {n} aircraft track rows.")


if __name__ == "__main__":
    main()
