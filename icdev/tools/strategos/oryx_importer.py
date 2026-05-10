#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Oryx Equipment Loss Tracker importer.

Parses Oryx-format equipment loss data (JSON) and writes:
  sg_entities         — Equipment nodes (one per loss entry)
  sg_conflict_events  — ConflictEvent rows (source='oryx')

Oryx JSON schema (standardized pre-scraped format):
  [
    {
      "actor": "Russia",
      "category": "Tanks",
      "model": "T-72B3",
      "status": "destroyed",   # destroyed | captured | damaged | abandoned
      "count": 1,
      "evidence_url": "https://…"
    },
    ...
  ]

Deduplication: sg_entities uses (source, external_id) uniqueness;
               sg_conflict_events uses (source, external_id).

Usage:
  python -m tools.strategos.oryx_importer --json data/oryx_ukraine.json --theater ukraine
  python -m tools.strategos.oryx_importer --json data/oryx.json --dry-run --output-json
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"

# Actor → theater actor ID mapping
_ACTOR_MAP = {
    "russia": "RUS",
    "ukraine": "UKR",
    "russian": "RUS",
    "ukrainian": "UKR",
    "nato": "NATO",
    "us": "USA",
    "usa": "USA",
}

# Equipment category → domain
_DOMAIN_MAP = {
    "tank": "land",
    "armored": "land",
    "artillery": "land",
    "infantry": "land",
    "helicopter": "air",
    "aircraft": "air",
    "drone": "air",
    "uav": "air",
    "ship": "sea",
    "vessel": "sea",
    "naval": "sea",
    "missile": "land",
    "radar": "ew",
    "electronic": "ew",
}


def _domain_for(category: str) -> str:
    cat_lower = category.lower()
    for key, domain in _DOMAIN_MAP.items():
        if key in cat_lower:
            return domain
    return "land"


def _actor_id(actor: str) -> str:
    return _ACTOR_MAP.get(actor.strip().lower(), actor[:6].upper())


def _get_conn(sqlite_path: Path):
    try:
        from tools.db.storage import get_connection
        return get_connection(), False
    except Exception:
        import sqlite3
        return sqlite3.connect(str(sqlite_path)), True


def parse_oryx_json(path: Path, theater_id: str) -> tuple[list[dict], list[dict]]:
    """Parse Oryx JSON. Returns (entities, events)."""
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)

    entities: list[dict] = []
    events: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for i, row in enumerate(rows):
        actor = row.get("actor", "Unknown")
        category = row.get("category", "Equipment")
        model = row.get("model", "Unknown")
        status = row.get("status", "destroyed")
        count = int(row.get("count", 1))
        evidence_url = row.get("evidence_url", "")
        snapshot_date = row.get("date", row.get("snapshot_date", now[:10]))

        actor_id = _actor_id(actor)
        domain = _domain_for(category)
        ext_id = f"oryx-{actor_id}-{category[:8].replace(' ', '_')}-{i}"

        # Entity: one Equipment node per unique model/actor combination
        entity_id = str(uuid.uuid4())
        entities.append({
            "id": entity_id,
            "theater_id": theater_id,
            "name": f"{actor} {model}",
            "entity_type": "Equipment",
            "entity_subtype": category,
            "country_code": actor_id,
            "status": "destroyed" if status == "destroyed" else "active",
            "source": "oryx",
            "external_id": ext_id,
            "capabilities_json": json.dumps({"count": count, "model": model}),
            "metadata_json": json.dumps({
                "actor": actor,
                "category": category,
                "model": model,
                "status": status,
                "count": count,
                "evidence_url": evidence_url,
            }),
            "classification": "CUI // SP-CTI",
            "created_at": now,
            "updated_at": now,
        })

        # Event: one ConflictEvent per loss entry
        severity = "medium"
        if status == "destroyed":
            severity = "high"
        elif status == "captured":
            severity = "medium"
        elif status in ("damaged", "abandoned"):
            severity = "low"

        events.append({
            "id": str(uuid.uuid4()),
            "theater_id": theater_id,
            "external_id": f"evt-{ext_id}",
            "source": "oryx",
            "event_type": f"equipment_{status}",
            "event_date": snapshot_date,
            "event_ts": snapshot_date + "T00:00:00+00:00",
            "actor1": actor,
            "actor2": "",
            "lat": 0.0,
            "lon": 0.0,
            "location_wkt": "",
            "severity": severity,
            "description": f"{actor} {category} '{model}' {status} (x{count})",
            "source_url": evidence_url,
            "properties_json": json.dumps({
                "model": model,
                "category": category,
                "status": status,
                "count": count,
                "domain": domain,
            }),
            "metadata_json": json.dumps({"count": count}),
            "created_at": now,
        })

    return entities, events


def write_all(
    entities: list[dict],
    events: list[dict],
    dry_run: bool = False,
) -> dict:
    if dry_run:
        return {
            "entities_inserted": len(entities),
            "events_inserted": len(events),
            "dry_run": True,
        }

    conn, is_sqlite = _get_conn(_DB_PATH)
    ph = "%s" if not is_sqlite else "?"
    ent_inserted = ent_skipped = evt_inserted = evt_skipped = 0

    try:
        for ent in entities:
            try:
                conn.execute(
                    f"INSERT INTO sg_entities "
                    f"(id, theater_id, name, entity_type, entity_subtype, country_code, "
                    f"status, source, external_id, capabilities_json, metadata_json, "
                    f"classification, created_at, updated_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph}) ON CONFLICT DO NOTHING",
                    (
                        ent["id"], ent["theater_id"], ent["name"],
                        ent["entity_type"], ent["entity_subtype"], ent["country_code"],
                        ent["status"], ent["source"], ent["external_id"],
                        ent["capabilities_json"], ent["metadata_json"],
                        ent["classification"], ent["created_at"], ent["updated_at"],
                    ),
                )
                ent_inserted += 1
            except Exception:
                ent_skipped += 1

        for ev in events:
            try:
                conn.execute(
                    f"INSERT INTO sg_conflict_events "
                    f"(id, theater_id, external_id, source, event_type, event_date, event_ts, "
                    f"actor1, actor2, lat, lon, location_wkt, severity, description, "
                    f"source_url, properties_json, metadata_json, created_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph}) ON CONFLICT DO NOTHING",
                    (
                        ev["id"], ev["theater_id"], ev["external_id"], ev["source"],
                        ev["event_type"], ev["event_date"], ev["event_ts"],
                        ev["actor1"], ev["actor2"], ev["lat"], ev["lon"],
                        ev["location_wkt"], ev["severity"], ev["description"],
                        ev["source_url"], ev["properties_json"], ev["metadata_json"],
                        ev["created_at"],
                    ),
                )
                evt_inserted += 1
            except Exception:
                evt_skipped += 1

        conn.commit()
    finally:
        conn.close()

    return {
        "entities_inserted": ent_inserted,
        "entities_skipped": ent_skipped,
        "events_inserted": evt_inserted,
        "events_skipped": evt_skipped,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Oryx JSON -> sg_entities + sg_conflict_events")
    ap.add_argument("--json", dest="json_file", required=True, metavar="FILE")
    ap.add_argument("--theater", default="ukraine", help="Theater ID (default: ukraine)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", action="store_true")
    args = ap.parse_args()

    entities, events = parse_oryx_json(Path(args.json_file), args.theater)
    result = write_all(entities, events, dry_run=args.dry_run)
    result["theater_id"] = args.theater
    result["total_entities"] = len(entities)
    result["total_events"] = len(events)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        dr = " (dry-run)" if args.dry_run else ""
        print(
            f"[oryx_importer] entities: {result['entities_inserted']} in, "
            f"{result.get('entities_skipped', 0)} skip | "
            f"events: {result['events_inserted']} in, "
            f"{result.get('events_skipped', 0)} skip{dr}"
        )


if __name__ == "__main__":
    main()
