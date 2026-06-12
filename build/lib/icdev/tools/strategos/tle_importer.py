#!/usr/bin/env python3
# CUI // SP-CTI
"""
TLE Importer — fetches Two-Line Element sets from CelesTrak and computes
satellite ground tracks + pass windows, stored in sg_satellite_passes.

Sources (free, no auth):
  CelesTrak GP data:  https://celestrak.org/pub/TLE/military.txt
  CelesTrak catalog:  https://celestrak.org/pub/TLE/visual.txt

Optional dependency: pip install sgp4
If sgp4 is unavailable, falls back to a simplified Keplerian propagator.

Air-gap compatible: use --file to load a local TLE text file.

Usage:
  python -m tools.strategos.tle_importer --category military
  python -m tools.strategos.tle_importer --file data/military.txt
  python -m tools.strategos.tle_importer --seed
  python -m tools.strategos.tle_importer --clear
"""
from __future__ import annotations

import argparse
import json
import math
import random
import urllib.request
from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection, is_pg

try:
    from sgp4.api import Satrec, jday
    HAS_SGP4 = True
except ImportError:
    HAS_SGP4 = False

CELESTRAK_BASE = "https://celestrak.org/pub/TLE"
_CATEGORIES = {
    "military": "military.txt",
    "visual":   "visual.txt",
    "starlink": "starlink.txt",
    "weather":  "weather.txt",
    "gnss":     "gnss.txt",
    "isr":      "military.txt",  # same as military
}

# Satellite type classification by name keywords
_SAT_TYPE_MAP = [
    (["COSMOS", "GLONASS", "GONETS"], "russian_military"),
    (["USA ",  "NROL", "NAVSTAR"],    "us_military"),
    (["CZ-",   "YAOGAN", "TIANLIAN"], "chinese_military"),
    (["STARLINK"],                     "commercial_leo"),
    (["NOAA",  "METEOSAT"],           "weather"),
    (["GPS",   "NAVSTAR"],            "gnss"),
    (["ISS"],                          "space_station"),
    (["SENTINEL", "LANDSAT"],         "eo_isr"),
]

RE_M     = 6.371e6          # Earth radius, metres
GM       = 3.986e14         # Standard gravitational parameter
SECS_DAY = 86400.0


def _sat_type(name: str) -> tuple[str, bool]:
    """Return (sat_type, military_flag)."""
    n = name.upper()
    for keywords, sat_type in _SAT_TYPE_MAP:
        if any(k in n for k in keywords):
            mil = "military" in sat_type or sat_type == "eo_isr"
            return sat_type, mil
    return "other", False


def fetch_tle(category: str = "military", timeout: int = 20) -> str:
    fname = _CATEGORIES.get(category, category)
    url = f"{CELESTRAK_BASE}/{fname}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ICDEV-Strategos/1.0 (intelligence research)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_tle_text(text: str) -> list[tuple[str, str, str]]:
    """Parse 3-line TLE format; return list of (name, line1, line2)."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    entries = []
    i = 0
    while i < len(lines) - 2:
        if lines[i].startswith("1 ") or lines[i].startswith("2 "):
            i += 1
            continue
        name = lines[i].strip()
        l1 = lines[i + 1].strip()
        l2 = lines[i + 2].strip()
        if l1.startswith("1 ") and l2.startswith("2 "):
            entries.append((name, l1, l2))
            i += 3
        else:
            i += 1
    return entries


def _norad_from_line1(line1: str) -> str:
    return line1[2:7].strip()


def _mean_motion(line2: str) -> float:
    """Revs per day from TLE line 2."""
    return float(line2[52:63].strip())


def _inclination(line2: str) -> float:
    return float(line2[8:16].strip())


def _simple_ground_track(
    name: str,
    line1: str,
    line2: str,
    steps: int = 24,
) -> list[dict]:
    """
    Simplified ground track without sgp4.
    Propagates along an approximate great circle using period + inclination.
    Returns a list of {lat, lon} dicts.
    """
    try:
        inc = math.radians(_inclination(line2))
        n   = _mean_motion(line2)            # rev/day
        period_s = SECS_DAY / n             # seconds per orbit

        epoch_day = float(line1[18:32])
        year_2d = int(line1[18:20])
        year = 2000 + year_2d if year_2d < 57 else 1900 + year_2d
        epoch = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=epoch_day - 1
        )

        now = datetime.now(timezone.utc)
        elapsed_s = (now - epoch).total_seconds() % period_s

        # RAAN from line2
        raan = math.radians(float(line2[17:25].strip()))

        track = []
        for step in range(steps):
            t = elapsed_s + step * (period_s / steps)
            angle = 2 * math.pi * (t / period_s)
            # approximate lat/lon (not precise, just for visualization)
            lat = math.degrees(math.asin(math.sin(inc) * math.sin(angle)))
            lon_rad = raan + angle - math.pi * (
                (now + timedelta(seconds=t - elapsed_s)).timestamp() / 43200
            )
            lon = math.degrees(lon_rad) % 360
            if lon > 180:
                lon -= 360
            track.append({"lat": round(lat, 3), "lon": round(lon, 3)})
        return track
    except Exception:
        return []


def _sgp4_ground_track(
    line1: str,
    line2: str,
    steps: int = 24,
) -> list[dict]:
    """Precise ground track using the sgp4 library."""
    sat = Satrec.twoline2rv(line1, line2)
    now = datetime.now(timezone.utc)
    _norad_from_line1(line1)
    try:
        n = _mean_motion(line2)
        period_s = SECS_DAY / n
    except Exception:
        period_s = 5400.0  # ~90 min default

    track = []
    for i in range(steps):
        t = now + timedelta(seconds=i * period_s / steps)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)
        e, r, _ = sat.sgp4(jd, fr)
        if e != 0:
            continue
        x, y, z = r  # ECI km
        r_xy = math.sqrt(x**2 + y**2)
        lat = math.degrees(math.atan2(z, r_xy))
        lon = math.degrees(math.atan2(y, x))
        # approximate sidereal adjustment (simplified)
        gmst = (280.46061837 + 360.98564736629 * (
            (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
        )) % 360
        lon_geo = (lon - gmst) % 360
        if lon_geo > 180:
            lon_geo -= 360
        track.append({"lat": round(lat, 3), "lon": round(lon_geo, 3)})
    return track


def tle_to_rows(
    entries: list[tuple[str, str, str]],
    observer_lat: float = 25.0,
    observer_lon: float = 90.0,
    source: str = "celestrak",
) -> list[dict]:
    """Convert TLE entries to row dicts for sg_satellite_passes."""
    rows = []
    now = datetime.now(timezone.utc)
    for name, line1, line2 in entries:
        norad_id = _norad_from_line1(line1)
        sat_type, mil_flag = _sat_type(name)

        if HAS_SGP4:
            track = _sgp4_ground_track(line1, line2)
        else:
            track = _simple_ground_track(name, line1, line2)

        try:
            n = _mean_motion(line2)
            period_s = SECS_DAY / n
        except Exception:
            period_s = 5400.0

        # Estimate a rough pass window (simplified)
        pass_start = now.isoformat()
        pass_end   = (now + timedelta(seconds=period_s / 4)).isoformat()

        # Max elevation: simplified estimate based on orbit altitude
        try:
            inc = _inclination(line2)
            max_el = max(0.0, 90.0 - abs(observer_lat - inc))
        except Exception:
            max_el = 30.0

        rows.append({
            "norad_id":         norad_id,
            "sat_name":         name[:120],
            "sat_type":         sat_type,
            "tle_line1":        line1,
            "tle_line2":        line2,
            "pass_start":       pass_start,
            "pass_end":         pass_end,
            "max_elevation":    round(max_el, 1),
            "aos_az":           round(random.uniform(0, 360), 1),
            "los_az":           round(random.uniform(0, 360), 1),
            "ground_track_json": json.dumps(track),
            "observer_lat":     observer_lat,
            "observer_lon":     observer_lon,
            "military_flag":    1 if mil_flag else 0,
            "source":           source,
        })
    return rows


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
                f"INSERT INTO sg_satellite_passes "  # nosec B608
                f"(norad_id,sat_name,sat_type,tle_line1,tle_line2,pass_start,pass_end,"
                f"max_elevation,aos_az,los_az,ground_track_json,observer_lat,"
                f"observer_lon,military_flag,source) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (
                    r["norad_id"], r["sat_name"], r["sat_type"],
                    r["tle_line1"], r["tle_line2"],
                    r["pass_start"], r["pass_end"],
                    r["max_elevation"], r["aos_az"], r["los_az"],
                    r["ground_track_json"],
                    r["observer_lat"], r["observer_lon"],
                    r["military_flag"], r["source"],
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def seed_demo_data() -> int:
    """Insert synthetic demo TLE entries."""
    demo_tles = [
        (
            "USA 224 (KH-11 KENNEN)",
            "1 37988U 11002A   24001.50000000  .00000001  00000-0  15000-4 0  9999",
            "2 37988  97.8900 100.0000 0001000  90.0000 270.0000 14.80000000000001",
        ),
        (
            "YAOGAN-39",
            "1 55914U 23015A   24001.50000000  .00000050  00000-0  30000-4 0  9995",
            "2 55914  97.4000 200.0000 0010000 100.0000 260.0000 14.30000000000000",
        ),
        (
            "COSMOS 2551 (BARS-M)",
            "1 49044U 21062A   24001.50000000  .00000020  00000-0  10000-4 0  9997",
            "2 49044  97.6000 150.0000 0005000 120.0000 240.0000 14.70000000000000",
        ),
        (
            "NAVSTAR 81 (GPS BIIF-12)",
            "1 40730U 15033A   24001.50000000 -.00000020  00000-0  00000+0 0  9991",
            "2 40730  55.0000  80.0000 0002000 100.0000 260.0000  2.00563620000000",
        ),
        (
            "ISS (ZARYA)",
            "1 25544U 98067A   24001.50000000  .00005000  00000-0  89000-4 0  9994",
            "2 25544  51.6400 100.0000 0006900 110.0000 250.0000 15.49999999000000",
        ),
    ]
    rows = tle_to_rows(demo_tles, source="demo")
    return insert_rows(rows)


def clear_all() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sg_satellite_passes")
        conn.commit()
        print("sg_satellite_passes cleared.")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="CelesTrak TLE importer for Strategos")
    p.add_argument("--category", default="military",
                   choices=list(_CATEGORIES), help="CelesTrak category to fetch")
    p.add_argument("--file",     help="Local TLE text file")
    p.add_argument("--seed",     action="store_true", help="Insert demo data")
    p.add_argument("--clear",    action="store_true", help="Clear all rows")
    p.add_argument("--observer", default="25.0,90.0",
                   help="Observer lat,lon for pass prediction (default INDOPACOM center)")
    p.add_argument("--max-sats", type=int, default=50,
                   help="Maximum number of satellites to process")
    args = p.parse_args()

    if args.clear:
        clear_all()
        return

    if args.seed:
        n = seed_demo_data()
        print(f"Seeded {n} demo satellite pass rows.")
        return

    obs_lat, obs_lon = [float(x) for x in args.observer.split(",")]

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
        source = "file"
    else:
        print(f"Fetching TLE data for category '{args.category}' from CelesTrak...")
        text = fetch_tle(args.category)
        source = "celestrak"

    entries = parse_tle_text(text)[: args.max_sats]
    print(f"Parsed {len(entries)} TLE entries.")
    rows = tle_to_rows(entries, observer_lat=obs_lat, observer_lon=obs_lon, source=source)
    n = insert_rows(rows)
    print(f"Inserted {n} satellite pass rows.")


if __name__ == "__main__":
    main()
