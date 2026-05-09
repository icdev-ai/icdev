#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — ACLED Armed Conflict Location & Event Data importer.

Parses ACLED CSV exports or JSON API responses and writes rows to:
  sg_conflict_events  (one row per ACLED event, source='acled')

Deduplication: UNIQUE on (source, external_id) → re-import is safe.

ACLED CSV columns (case-insensitive):
  EVENT_ID_CNTY, EVENT_DATE, YEAR, DISORDER_TYPE, EVENT_TYPE, SUB_EVENT_TYPE,
  ACTOR1, ACTOR2, LOCATION, LATITUDE, LONGITUDE, ADMIN1, ADMIN2, FATALITIES,
  SOURCE, NOTES, TAGS

ACLED API (JSON):
  GET https://api.acleddata.com/acled/read?key=…&email=…&country=Ukraine&limit=500

Usage:
  python -m tools.strategos.acled_importer --csv data/ukraine_acled.csv --theater ukraine
  python -m tools.strategos.acled_importer --json data/acled.json --theater ukraine
  python -m tools.strategos.acled_importer --csv data.csv --dry-run --json
"""
from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"


# ── Column normalization ──────────────────────────────────────────────────────

def _norm(row: dict) -> dict:
    """Return a dict with lowercase stripped keys."""
    return {k.strip().lower(): v for k, v in row.items()}


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "NA", "N/A") else default
    except (TypeError, ValueError):
        return default


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "", "NA", "N/A") else default
    except (TypeError, ValueError):
        return default


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_row(row: dict, theater_id: str) -> dict | None:
    """Convert one ACLED row to an sg_conflict_events dict. Returns None if invalid."""
    r = _norm(row)

    lat = _float(r.get("latitude"))
    lon = _float(r.get("longitude"))
    if lat == 0.0 and lon == 0.0:
        return None

    event_date = r.get("event_date", r.get("event_date", ""))[:10]
    external_id = r.get("event_id_cnty", r.get("data_id", ""))
    if not external_id:
        external_id = f"acled-{uuid.uuid4().hex[:8]}"

    event_type = r.get("event_type", "Unknown")
    sub_event_type = r.get("sub_event_type", "")
    disorder_type = r.get("disorder_type", "")
    fatalities = _int(r.get("fatalities", 0))

    severity = "low"
    if fatalities >= 50:
        severity = "critical"
    elif fatalities >= 10:
        severity = "high"
    elif fatalities >= 1:
        severity = "medium"

    notes = r.get("notes", "")
    description = f"{event_type}: {notes[:300]}" if notes else event_type

    props = {
        "sub_event_type": sub_event_type,
        "disorder_type": disorder_type,
        "fatalities": fatalities,
        "admin1": r.get("admin1", ""),
        "admin2": r.get("admin2", ""),
        "location": r.get("location", ""),
        "region": r.get("region", ""),
        "country": r.get("country", ""),
        "source_scale": r.get("source_scale", ""),
        "tags": r.get("tags", ""),
        "civilian_targeting": r.get("civilian_targeting", ""),
        "notes": notes[:500],
    }

    return {
        "id": str(uuid.uuid4()),
        "theater_id": theater_id,
        "external_id": external_id,
        "source": "acled",
        "event_type": event_type,
        "event_date": event_date,
        "event_ts": event_date + "T00:00:00+00:00",
        "actor1": r.get("actor1", ""),
        "actor2": r.get("actor2", ""),
        "lat": lat,
        "lon": lon,
        "location_wkt": f"POINT({lon} {lat})",
        "severity": severity,
        "description": description,
        "source_url": r.get("source_url", ""),
        "properties_json": json.dumps(props),
        "metadata_json": json.dumps({"fatalities": fatalities}),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_csv(path: Path, theater_id: str) -> Iterator[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            parsed = _parse_row(row, theater_id)
            if parsed:
                yield parsed


def parse_json_file(path: Path, theater_id: str) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("data", [])
    for row in rows:
        parsed = _parse_row(row, theater_id)
        if parsed:
            yield parsed


# ── Writer ────────────────────────────────────────────────────────────────────

def _get_conn(sqlite_path: Path):
    try:
        from tools.db.storage import get_connection
        return get_connection(), False
    except Exception:
        import sqlite3
        return sqlite3.connect(str(sqlite_path)), True


def write_events(events: list[dict], dry_run: bool = False) -> dict:
    """Insert events into sg_conflict_events. Returns {inserted, skipped}."""
    if dry_run:
        return {"inserted": len(events), "skipped": 0, "dry_run": True}

    conn, is_sqlite = _get_conn(_DB_PATH)
    inserted = skipped = 0
    ph = "%s" if not is_sqlite else "?"

    try:
        for ev in events:
            try:
                conn.execute(
                    f"INSERT INTO sg_conflict_events "
                    f"(id, theater_id, external_id, source, event_type, event_date, event_ts, "
                    f"actor1, actor2, lat, lon, location_wkt, severity, description, "
                    f"source_url, properties_json, metadata_json, created_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT DO NOTHING",
                    (
                        ev["id"], ev["theater_id"], ev["external_id"], ev["source"],
                        ev["event_type"], ev["event_date"], ev["event_ts"],
                        ev["actor1"], ev["actor2"], ev["lat"], ev["lon"],
                        ev["location_wkt"], ev["severity"], ev["description"],
                        ev["source_url"], ev["properties_json"], ev["metadata_json"],
                        ev["created_at"],
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
    ap = argparse.ArgumentParser(description="ACLED CSV/JSON -> sg_conflict_events importer")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", metavar="FILE", help="ACLED CSV export path")
    src.add_argument("--json", dest="json_file", metavar="FILE", help="ACLED JSON file path")
    ap.add_argument("--theater", default="ukraine", help="Theater ID (default: ukraine)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", action="store_true", help="Print JSON result")
    args = ap.parse_args()

    if args.csv:
        events = list(parse_csv(Path(args.csv), args.theater))
    else:
        events = list(parse_json_file(Path(args.json_file), args.theater))

    result = write_events(events, dry_run=args.dry_run)
    result["theater_id"] = args.theater
    result["source_file"] = args.csv or args.json_file
    result["total_parsed"] = len(events)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[acled_importer] {result['inserted']} inserted, {result['skipped']} skipped"
              f"{' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
