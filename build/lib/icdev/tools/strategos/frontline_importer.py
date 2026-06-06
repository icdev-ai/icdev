#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Frontline GeoJSON importer.

Parses GeoJSON FeatureCollections representing battle frontlines and writes:
  sg_frontline_segments  — LineString/MultiLineString segments per snapshot date

GeoJSON schema expected:
  {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},
        "properties": {
          "date": "2024-01-15",       # ISO date of snapshot
          "advance_km": 2.3,          # optional: net advance in km
          "source": "ISW"             # optional: data source label
        }
      },
      ...
    ]
  }

Also accepts a directory of dated GeoJSON files:
  frontlines/2024-01-15.geojson
  frontlines/2024-01-16.geojson

Usage:
  python -m tools.strategos.frontline_importer --file data/frontline.geojson --theater ukraine
  python -m tools.strategos.frontline_importer --dir data/frontlines/ --theater ukraine
  python -m tools.strategos.frontline_importer --file fl.geojson --dry-run --output-json
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _get_conn(sqlite_path: Path):
    try:
        from tools.db.storage import get_connection
        return get_connection(), False
    except Exception:
        import sqlite3
        return sqlite3.connect(str(sqlite_path)), True


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_geojson(data: dict, theater_id: str, default_source: str = "imported") -> list[dict]:
    """Convert a GeoJSON FeatureCollection to sg_frontline_segments rows."""
    features = data.get("features", [])
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for feat in features:
        geom = feat.get("geometry", {})
        if not geom or geom.get("type") not in ("LineString", "MultiLineString"):
            continue
        props = feat.get("properties") or {}
        snapshot_date = props.get("date", props.get("snapshot_date", now[:10]))
        advance_km = float(props.get("advance_km", 0.0) or 0.0)
        source = props.get("source", default_source)

        rows.append({
            "id": str(uuid.uuid4()),
            "snapshot_date": snapshot_date,
            "geometry_json": json.dumps(geom),
            "advance_km": advance_km,
            "theater": theater_id,
            "source": source,
            "created_at": now,
        })
    return rows


def load_file(path: Path, theater_id: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    default_source = path.stem
    return parse_geojson(data, theater_id, default_source)


def load_directory(directory: Path, theater_id: str) -> list[dict]:
    rows: list[dict] = []
    for geojson_file in sorted(directory.glob("*.geojson")):
        rows.extend(load_file(geojson_file, theater_id))
    for geojson_file in sorted(directory.glob("*.json")):
        rows.extend(load_file(geojson_file, theater_id))
    return rows


# ── Writer ────────────────────────────────────────────────────────────────────

def write_segments(rows: list[dict], dry_run: bool = False) -> dict:
    if dry_run:
        return {"inserted": len(rows), "skipped": 0, "dry_run": True}

    conn, is_sqlite = _get_conn(_DB_PATH)
    ph = "%s" if not is_sqlite else "?"
    inserted = skipped = 0

    try:
        for row in rows:
            try:
                conn.execute(
                    f"INSERT INTO sg_frontline_segments "
                    f"(id, snapshot_date, geometry_json, advance_km, theater, source, created_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph}) ON CONFLICT DO NOTHING",
                    (
                        row["id"], row["snapshot_date"], row["geometry_json"],
                        row["advance_km"], row["theater"], row["source"], row["created_at"],
                    ),
                )
                inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
    finally:
        conn.close()

    return {"inserted": inserted, "skipped": skipped}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Frontline GeoJSON -> sg_frontline_segments")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", metavar="GEOJSON", help="Single GeoJSON file")
    src.add_argument("--dir", metavar="DIR", help="Directory of GeoJSON files")
    ap.add_argument("--theater", default="ukraine", help="Theater ID (default: ukraine)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", action="store_true")
    args = ap.parse_args()

    if args.file:
        rows = load_file(Path(args.file), args.theater)
    else:
        rows = load_directory(Path(args.dir), args.theater)

    result = write_segments(rows, dry_run=args.dry_run)
    result["theater_id"] = args.theater
    result["total_parsed"] = len(rows)

    if args.output_json:
        import json as _json
        print(_json.dumps(result, indent=2))
    else:
        dr = " (dry-run)" if args.dry_run else ""
        print(f"[frontline_importer] {result['inserted']} inserted, {result['skipped']} skipped{dr}")


if __name__ == "__main__":
    main()
