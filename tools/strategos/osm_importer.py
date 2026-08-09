#!/usr/bin/env python3
# CUI // SP-CTI
"""OpenStreetMap GeoJSON logistics-node importer for Strategos.

Reads a GeoJSON file (FeatureCollection or single Feature) containing
Ukraine infrastructure — rail, road, bridge, and port nodes — and upserts
them into sg_entities (entity_type='logistics_node').

Column mapping to existing sg_entities schema:
  entity_type     = 'logistics_node'
  entity_subtype  = node_type  (rail | road | bridge | port)
  name            = OSM "name" / "name:en" tag, or "unnamed"
  country_code    = 'UA'
  location_wkt    = WKT POINT(lon lat) centroid
  source          = 'osm'
  external_id     = OSM "@id" property or feature-level id
  vulnerability_score = 0.5  (stub; updated by domain scorer)
  metadata_json   = full OSM properties as JSON

Deduplication via UNIQUE(source, external_id) — idx_sg_ent_source_ext.

Node-type classification (first matching rule wins):
  bridge → bridge=yes  OR  man_made=bridge
  rail   → any "railway" key present
  port   → amenity/landuse/man_made/waterway tags for docks, piers, ports
  road   → any "highway" key present

Geometry centroid (GeoJSON coordinate order: [lon, lat]):
  Point      → coordinates directly
  LineString → mean of all vertices
  Polygon    → mean of exterior ring vertices
  other      → location_wkt set to NULL

Requires: migration 061_sg_entities (adds vulnerability_score column).

Usage
-----
  python tools/strategos/osm_importer.py --file ukraine_logistics.geojson
  python tools/strategos/osm_importer.py --file data.geojson --node-types rail,port
  python tools/strategos/osm_importer.py --file data.geojson --dry-run --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (entity_subtype values must match any CHECK on sg_entities)
# ---------------------------------------------------------------------------
DEFAULT_VULNERABILITY_SCORE = 0.5
SOURCE                      = "osm"
COUNTRY_CODE                = "UA"

_PORT_AMENITY  = {"ferry_terminal", "port"}
_PORT_LANDUSE  = {"port", "harbour"}
_PORT_MAN_MADE = {"pier", "dock", "quay"}
_PORT_WATERWAY = {"dock", "port", "boatyard"}


# ---------------------------------------------------------------------------
# Node-type classifier
# ---------------------------------------------------------------------------
def _classify_node_type(props: dict) -> str | None:
    """Return the node_type string for an OSM properties dict, or None."""
    # Bridge first — a railway bridge is still a bridge
    if props.get("bridge") == "yes" or props.get("man_made") == "bridge":
        return "bridge"
    if "railway" in props:
        return "rail"
    if (
        props.get("amenity") in _PORT_AMENITY
        or props.get("landuse") in _PORT_LANDUSE
        or props.get("man_made") in _PORT_MAN_MADE
        or props.get("waterway") in _PORT_WATERWAY
    ):
        return "port"
    if "highway" in props:
        return "road"
    return None


# ---------------------------------------------------------------------------
# Geometry centroid — GeoJSON coordinate order is [lon, lat]
# ---------------------------------------------------------------------------
def _flatten_coords(coords) -> list[list[float]]:
    if not coords:
        return []
    if isinstance(coords[0], (int, float)):
        return [coords]
    result: list[list[float]] = []
    for sub in coords:
        result.extend(_flatten_coords(sub))
    return result


def _centroid(geometry: dict | None) -> tuple[float, float] | None:
    """Return (lat, lon) from a GeoJSON geometry dict, or None."""
    if not geometry:
        return None
    gtype  = geometry.get("type", "")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if gtype == "Point":
        if len(coords) >= 2:
            return float(coords[1]), float(coords[0])
        return None
    pairs = _flatten_coords(coords)
    if not pairs:
        return None
    return (
        sum(p[1] for p in pairs) / len(pairs),
        sum(p[0] for p in pairs) / len(pairs),
    )


# ---------------------------------------------------------------------------
# GeoJSON parser
# ---------------------------------------------------------------------------
def parse_geojson(
    data: dict,
    allowed_node_types: set[str] | None = None,
) -> list[dict]:
    """Parse GeoJSON; return rows ready for sg_entities insert."""
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif data.get("type") == "Feature":
        features = [data]
    else:
        logger.warning("Unsupported GeoJSON root type: %s", data.get("type"))
        return []

    rows: list[dict] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}

        node_type = _classify_node_type(props)
        if node_type is None:
            continue
        if allowed_node_types and node_type not in allowed_node_types:
            continue

        centroid = _centroid(feature.get("geometry"))
        location_wkt: str | None = None
        if centroid:
            lat, lon = centroid
            location_wkt = f"POINT({lon} {lat})"

        external_id: str | None = (
            props.get("@id")
            or (str(feature["id"]) if feature.get("id") is not None else None)
        )
        name: str = props.get("name") or props.get("name:en") or "unnamed"

        rows.append(
            {
                "external_id":   external_id,
                "node_type":     node_type,
                "name":          name,
                "location_wkt":  location_wkt,
                "metadata_json": json.dumps(props, ensure_ascii=False),
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Stable entity ID
# ---------------------------------------------------------------------------
def _make_entity_id(external_id: str | None, metadata_json: str) -> str:
    key = f"{SOURCE}:{external_id}" if external_id else f"{SOURCE}:{metadata_json}"
    return "ent_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# DB upsert — INSERT relies on UNIQUE(source, external_id) for dedup
# ---------------------------------------------------------------------------
def _upsert_rows(conn, rows: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    for row in rows:
        raw = row["metadata_json"]
        entity_id = _make_entity_id(row["external_id"], raw)
        try:
            conn.execute(
                """
                INSERT INTO sg_entities
                    (id, entity_type, entity_subtype, name,
                     country_code, location_wkt,
                     source, external_id,
                     vulnerability_score,
                     metadata_json)
                VALUES
                    (%s, 'logistics_node', %s, %s,
                     %s, %s,
                     'osm', %s,
                     %s,
                     %s)
                """,
                (
                    entity_id,
                    row["node_type"],
                    row["name"],
                    COUNTRY_CODE,
                    row["location_wkt"],
                    row["external_id"],
                    DEFAULT_VULNERABILITY_SCORE,
                    raw,
                ),
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


# ---------------------------------------------------------------------------
# Public run()
# ---------------------------------------------------------------------------
def run(
    *,
    file: Path,
    node_types: list[str] | None = None,
    dry_run: bool = False,
    as_json: bool = False,
) -> dict:
    """Run the OSM GeoJSON importer; return a summary dict."""
    allowed = set(node_types) if node_types else None

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except Exception as exc:
        msg = f"Failed to load {file}: {exc}"
        logger.error(msg)
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    rows = parse_geojson(data, allowed)

    type_counts: dict[str, int] = {}
    for row in rows:
        type_counts[row["node_type"]] = type_counts.get(row["node_type"], 0) + 1

    if dry_run:
        summary: dict = {
            "dry_run": True,
            "file": str(file),
            "total_features": len(rows),
            "by_node_type": type_counts,
        }
        if as_json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"[DRY RUN] {len(rows)} logistics nodes in {file.name}")
            for nt, cnt in sorted(type_counts.items()):
                print(f"  {nt}: {cnt}")
        return summary

    conn = get_connection()
    inserted, skipped = _upsert_rows(conn, rows)
    conn.commit()
    conn.close()

    summary = {
        "dry_run": False,
        "file": str(file),
        "total_features": len(rows),
        "by_node_type": type_counts,
        "inserted": inserted,
        "skipped_duplicates": skipped,
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"OSM import ({file.name}): {len(rows)} features → "
            f"{inserted} inserted, {skipped} duplicate(s) skipped"
        )
        print(f"  Node types: {type_counts}")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import OSM GeoJSON logistics nodes into sg_entities"
    )
    parser.add_argument(
        "--file", type=Path, required=True,
        help="GeoJSON input file (FeatureCollection or Feature)",
    )
    parser.add_argument(
        "--node-types", metavar="TYPES",
        help="Comma-separated filter: rail,road,bridge,port (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse without writing to DB")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    node_types = (
        [t.strip() for t in args.node_types.split(",") if t.strip()]
        if args.node_types else None
    )

    run(
        file=args.file,
        node_types=node_types,
        dry_run=args.dry_run,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    main()
