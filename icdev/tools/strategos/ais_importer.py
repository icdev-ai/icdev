#!/usr/bin/env python3
# CUI // SP-CTI
"""
AIS CSV Importer — loads NOAA Marine Cadastre AIS data into sg_vessel_tracks.

NOAA AIS CSV format (columns):
  MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO,
  CallSign, VesselType, Status, Length, Width, Draft, Cargo, TransceiverClass

Download URLs (no auth required):
  https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/AIS_2023_01_01.zip

Usage:
  python -m tools.strategos.ais_importer --csv path/to/AIS_2023_01_01.csv
  python -m tools.strategos.ais_importer --download 2023-01-01 --bbox 20,100,50,130
  python -m tools.strategos.ais_importer --csv data.csv --bbox 40,27,48,42 --sample 5 --clear
  python -m tools.strategos.ais_importer --list
"""

import argparse
import csv
import json
import math
import os
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional

# ── NOAA AIS column names ──────────────────────────────────────────────────
COL_MMSI      = "MMSI"
COL_TS        = "BaseDateTime"
COL_LAT       = "LAT"
COL_LON       = "LON"
COL_SOG       = "SOG"        # speed over ground (knots)
COL_COG       = "COG"        # course over ground (degrees)
COL_HEADING   = "Heading"
COL_NAME      = "VesselName"
COL_TYPE_CODE = "VesselType"  # AIS numeric type code
COL_STATUS    = "Status"

# AIS vessel type codes → human labels + internal type
# https://api.vtexplorer.com/docs/ref-aistypes.html
_AIS_TYPE_MAP = {
    range(0, 20):   ("Unknown",          "unknown"),
    range(20, 30):  ("Wing-In-Ground",   "unknown"),
    range(30, 35):  ("Fishing",          "fishing"),
    range(35, 40):  ("High Speed Craft", "cargo"),
    range(40, 50):  ("High Speed Craft", "cargo"),
    range(50, 60):  ("Special Craft",    "unknown"),
    range(60, 70):  ("Passenger",        "cargo"),
    range(70, 80):  ("Cargo",            "cargo"),
    range(80, 90):  ("Tanker",           "tanker"),
    range(90, 100): ("Other",            "unknown"),
}
# Military / naval
_WARSHIP_CODES = {35, 36, 37}

NOAA_BASE_URL = (
    "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/AIS_{date}.zip"
)

# ── Helpers ────────────────────────────────────────────────────────────────

def _ais_type_label(code: Optional[int]):
    if code is None:
        return "unknown", "unknown"
    if code in _WARSHIP_CODES:
        return "Warship / Naval", "warship"
    for r, (label, internal) in _AIS_TYPE_MAP.items():
        if code in r:
            return label, internal
    return "Unknown", "unknown"


def _safe_float(v, default=None):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _parse_ts(raw: str) -> Optional[str]:
    """Normalise NOAA datetime string → ISO 8601 UTC."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def _heading_from_cog(cog: Optional[float]) -> float:
    if cog is None:
        return 0.0
    return cog % 360


def parse_csv(
    path: str,
    bbox: Optional[tuple] = None,      # (min_lat, min_lon, max_lat, max_lon)
    sample: int = 1,                    # keep every Nth row per MMSI
    max_vessels: int = 200,
    max_pts_per_vessel: int = 500,
) -> list[dict]:
    """
    Parse a NOAA AIS CSV file and return a list of vessel dicts:
      { mmsi, vessel_name, vessel_type, flag, track: [{lat,lon,speed,heading,ts}] }
    """
    vessels: dict[str, dict] = {}
    mmsi_counter: dict[str, int] = {}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            mmsi = (row.get(COL_MMSI) or "").strip()
            if not mmsi:
                continue

            lat = _safe_float(row.get(COL_LAT))
            lon = _safe_float(row.get(COL_LON))
            if lat is None or lon is None:
                continue

            if bbox:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue

            # Sample: only keep every Nth row per MMSI
            mmsi_counter[mmsi] = mmsi_counter.get(mmsi, 0) + 1
            if (mmsi_counter[mmsi] - 1) % sample != 0:
                continue

            ts = _parse_ts(row.get(COL_TS, ""))
            if not ts:
                continue

            sog     = _safe_float(row.get(COL_SOG), 0.0)
            cog     = _safe_float(row.get(COL_COG))
            heading_raw = _safe_float(row.get(COL_HEADING))
            heading = heading_raw if heading_raw and 0 <= heading_raw < 360 else _heading_from_cog(cog)

            name = (row.get(COL_NAME) or "").strip() or f"VESSEL-{mmsi[-4:]}"
            type_code = None
            try:
                type_code = int(float(row.get(COL_TYPE_CODE) or 0))
            except (ValueError, TypeError):
                pass
            _, vessel_type = _ais_type_label(type_code)

            if mmsi not in vessels:
                if len(vessels) >= max_vessels:
                    continue
                vessels[mmsi] = {
                    "mmsi":        mmsi,
                    "vessel_name": name,
                    "vessel_type": vessel_type,
                    "flag":        None,
                    "track":       [],
                }

            if len(vessels[mmsi]["track"]) < max_pts_per_vessel:
                vessels[mmsi]["track"].append({
                    "lat":     lat,
                    "lon":     lon,
                    "speed":   sog or 0.0,
                    "heading": heading,
                    "ts":      ts,
                })

    # Sort each vessel's track by timestamp
    result = []
    for v in vessels.values():
        if len(v["track"]) < 2:
            continue
        v["track"].sort(key=lambda p: p["ts"])
        result.append(v)

    return result


def download_noaa(date_str: str, dest_dir: str = ".tmp") -> str:
    """
    Download a NOAA AIS zip for a given date (YYYY-MM-DD) and extract CSV.
    Returns path to extracted CSV.
    """
    import requests  # guarded import — not needed for local-only use

    date_str = date_str.replace("-", "_")  # 2023_01_01
    year = date_str[:4]
    url  = NOAA_BASE_URL.format(year=year, date=date_str)
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"AIS_{date_str}.zip")

    print(f"[ais_importer] Downloading {url} …", flush=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {pct}% ({downloaded:,} / {total:,} bytes)", end="", flush=True)
    print()

    print("[ais_importer] Extracting …")
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError(f"No CSV found in {zip_path}")
        csv_path = os.path.join(dest_dir, csv_names[0])
        zf.extract(csv_names[0], dest_dir)
        print(f"[ais_importer] Extracted to {csv_path}")
    return csv_path


def import_to_db(
    vessels: list[dict],
    clear: bool = False,
    conn=None,
) -> int:
    """Insert parsed vessel tracks into sg_vessel_tracks. Returns inserted row count."""
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    inserted = 0
    try:
        if clear:
            _conn.execute("DELETE FROM sg_vessel_tracks")
            print("[ais_importer] Cleared existing vessel tracks.")

        for v in vessels:
            for pt in v["track"]:
                _conn.execute(
                    "INSERT OR IGNORE INTO sg_vessel_tracks "
                    "(id, mmsi, vessel_name, vessel_type, flag, lat, lon, speed, heading, ts) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        v["mmsi"],
                        v["vessel_name"],
                        v["vessel_type"],
                        v["flag"],
                        pt["lat"], pt["lon"],
                        pt["speed"], pt["heading"], pt["ts"],
                    ),
                )
                inserted += 1
        _conn.commit()
    finally:
        if conn is None:
            _conn.close()
    return inserted


def list_vessels(conn=None) -> None:
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT mmsi, MIN(vessel_name) AS vessel_name, MIN(vessel_type) AS vessel_type, "
            "COUNT(*) AS pts, MIN(ts) AS first_seen, MAX(ts) AS last_seen "
            "FROM sg_vessel_tracks GROUP BY mmsi ORDER BY pts DESC LIMIT 50"
        ).fetchall()
        print(f"{'MMSI':>12}  {'Name':30}  {'Type':10}  {'Pts':>5}  First seen")
        print("-" * 80)
        for r in rows:
            print(f"{r['mmsi']:>12}  {(r['vessel_name'] or '')[:30]:30}  "
                  f"{(r['vessel_type'] or ''):10}  {r['pts']:>5}  {(r['first_seen'] or '')[:16]}")
        total = _conn.execute("SELECT COUNT(*) FROM sg_vessel_tracks").fetchone()[0]
        print(f"\nTotal: {total} track points across {len(rows)} vessels shown")
    finally:
        if conn is None:
            _conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_bbox(s: str):
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 4 values: min_lat,min_lon,max_lat,max_lon")
    return tuple(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AIS CSV → sg_vessel_tracks importer")
    ap.add_argument("--csv",      help="Path to NOAA AIS CSV file")
    ap.add_argument("--download", metavar="YYYY-MM-DD",
                    help="Download NOAA AIS data for a specific date and import")
    ap.add_argument("--bbox",     help="Bounding box: min_lat,min_lon,max_lat,max_lon")
    ap.add_argument("--sample",   type=int, default=3,
                    help="Keep every Nth position per MMSI (default: 3)")
    ap.add_argument("--max-vessels", type=int, default=100,
                    help="Max vessels to import (default: 100)")
    ap.add_argument("--max-pts",  type=int, default=200,
                    help="Max track points per vessel (default: 200)")
    ap.add_argument("--clear",    action="store_true",
                    help="Clear existing vessel tracks before importing")
    ap.add_argument("--list",     action="store_true",
                    help="List vessels currently in sg_vessel_tracks and exit")
    ap.add_argument("--json",     action="store_true",
                    help="Output results as JSON")
    args = ap.parse_args(argv)

    if args.list:
        list_vessels()
        return

    csv_path = args.csv
    if args.download:
        csv_path = download_noaa(args.download)

    if not csv_path:
        ap.print_help()
        sys.exit(1)

    if not os.path.exists(csv_path):
        print(f"[ais_importer] ERROR: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    bbox = _parse_bbox(args.bbox) if args.bbox else None

    print(f"[ais_importer] Parsing {csv_path} …")
    if bbox:
        print(f"[ais_importer] Bbox filter: lat {bbox[0]}–{bbox[2]}, lon {bbox[1]}–{bbox[3]}")

    vessels = parse_csv(
        csv_path,
        bbox=bbox,
        sample=args.sample,
        max_vessels=args.max_vessels,
        max_pts_per_vessel=args.max_pts,
    )
    print(f"[ais_importer] Parsed {len(vessels)} vessels")

    inserted = import_to_db(vessels, clear=args.clear)
    print(f"[ais_importer] Inserted {inserted} track points")

    if args.json:
        print(json.dumps({
            "status": "ok",
            "vessels": len(vessels),
            "inserted": inserted,
        }))


if __name__ == "__main__":
    main()
