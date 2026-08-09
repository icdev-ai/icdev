#!/usr/bin/env python3
# CUI // SP-CTI
"""
Copernicus Data Space EO Importer — Sentinel-2 scenes for GeoSIGINT theater AOIs.

Queries the Copernicus Data Space Ecosystem OData API (public, no auth required)
for Sentinel-2 L2A scenes intersecting each theater's area_wkt. Filters cloud_pct
< 20 and sensing_date within the last N days. Results are written to sg_eo_signals
via get_connection().

Fallback: returns empty list with status='api_unavailable' if network is unreachable.

Usage:
  python -m tools.strategos.eo_importer                        # all theaters
  python -m tools.strategos.eo_importer --theater INDOPACOM   # specific theater
  python -m tools.strategos.eo_importer --days 7              # last 7 days
  python -m tools.strategos.eo_importer --dry-run --json      # preview without DB write
  python -m tools.strategos.eo_importer --list                # list sg_eo_signals
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CDSE_ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
HTTP_TIMEOUT = 30          # seconds
MAX_RESULTS_PER_AOI = 50   # OData $top per theater query
CLOUD_THRESHOLD = 20.0     # cloud_pct < 20 filter
DEFAULT_DAYS = 30          # lookback window

_WKT_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# WKT helpers
# ---------------------------------------------------------------------------
def _bbox_from_wkt(wkt: str) -> tuple[float, float, float, float] | None:
    """
    Extract (min_lon, min_lat, max_lon, max_lat) from a WKT POLYGON.
    WKT coordinate order is (lon lat) per ISO 19125.
    """
    # Strip SRID prefix if present: "geography'SRID=4326;POLYGON(...)'"
    clean = re.sub(r"geography'SRID=\d+;", "", wkt, flags=re.IGNORECASE).strip("'")
    # Find the POLYGON(...) body
    m = re.search(r"POLYGON\s*\((.+)\)\s*$", clean, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    nums = [float(v) for v in _WKT_NUM_RE.findall(m.group(1))]
    if len(nums) < 8 or len(nums) % 2 != 0:
        return None
    lons = nums[0::2]
    lats = nums[1::2]
    return min(lons), min(lats), max(lons), max(lats)


def _bbox_to_wkt(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return (
        f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
        f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
    )


# ---------------------------------------------------------------------------
# Copernicus OData query builder
# ---------------------------------------------------------------------------
def _build_query_url(
    bbox_wkt: str,
    start_dt: datetime,
    end_dt: datetime,
    max_results: int = MAX_RESULTS_PER_AOI,
) -> str:
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    footprint = f"geography'SRID=4326;{bbox_wkt}'"
    # Cloud cover filter applied in Python after fetch (OData attribute filter varies by API version)
    filter_expr = " and ".join([
        "Collection/Name eq 'SENTINEL-2'",
        f"OData.CSC.Intersects(area={footprint})",
        f"ContentDate/Start gt {start_str}",
        f"ContentDate/Start lt {end_str}",
    ])
    params = {
        "$filter": filter_expr,
        "$orderby": "ContentDate/Start desc",
        "$top": str(max_results),
        "$expand": "Attributes",
    }
    return CDSE_ODATA_BASE + "?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------
def _fetch_scenes(url: str) -> list[dict] | None:
    """
    Fetch Sentinel-2 scene records from Copernicus OData.
    Returns None if the network is unreachable (triggers api_unavailable fallback).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http"):
        logger.error("Rejected non-HTTP URL scheme: %s", parsed.scheme)
        return None
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # nosec B310 — scheme validated above
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("value", [])
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Copernicus API unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Scene → sg_eo_signals row
# ---------------------------------------------------------------------------
def _cloud_cover(attrs: list[dict]) -> float | None:
    for attr in attrs:
        if attr.get("Name") == "cloudCover":
            try:
                return float(attr["Value"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _thumbnail_url(scene_id: str, assets: list[dict]) -> str:
    for asset in assets:
        if "quicklook" in (asset.get("Type") or "").lower():
            link = asset.get("DownloadLink") or asset.get("S3Path")
            if link:
                return link
    # Construct quicklook URL from product ID (Copernicus standard path)
    return f"{CDSE_ODATA_BASE}('{scene_id}')/quicklook"


def _scene_to_row(
    scene: dict,
    aoi_tag: str,
    cloud_threshold: float = CLOUD_THRESHOLD,
) -> dict | None:
    """Convert an OData scene record to a sg_eo_signals insert dict. Returns None to skip."""
    scene_id = scene.get("Id") or ""
    if not scene_id:
        return None

    attrs = scene.get("Attributes") or []
    cloud_pct = _cloud_cover(attrs)
    if cloud_pct is None or cloud_pct >= cloud_threshold:
        return None

    # Sensing date from ContentDate.Start
    content_date = scene.get("ContentDate") or {}
    start_raw = content_date.get("Start") or ""
    try:
        sensing_date = datetime.fromisoformat(
            start_raw.replace("Z", "+00:00")
        ).date().isoformat()
    except (ValueError, AttributeError):
        sensing_date = None

    # BBox WKT from OData Footprint geography string, falling back to GeoFootprint
    footprint_raw = scene.get("Footprint") or ""
    bbox_wkt: str | None = None
    if footprint_raw:
        bbox = _bbox_from_wkt(footprint_raw)
        if bbox:
            bbox_wkt = _bbox_to_wkt(*bbox)
    if not bbox_wkt:
        geo = scene.get("GeoFootprint") or {}
        coords_flat = _flatten_geojson_coords(geo.get("coordinates") or [])
        if coords_flat:
            lons = [c[0] for c in coords_flat]
            lats = [c[1] for c in coords_flat]
            bbox_wkt = _bbox_to_wkt(min(lons), min(lats), max(lons), max(lats))

    assets = scene.get("Assets") or []
    return {
        "id": scene_id,          # Copernicus UUID used as PK for natural dedup
        "scene_id": scene.get("Name") or scene_id,
        "satellite": "Sentinel-2",
        "bbox_wkt": bbox_wkt,
        "cloud_pct": cloud_pct,
        "sensing_date": sensing_date,
        "thumbnail_url": _thumbnail_url(scene_id, assets),
        "relevance_score": round(max(0.0, 1.0 - cloud_pct / 100.0), 4),
        "aoi_tag": aoi_tag,
        "status": "new",
    }


def _flatten_geojson_coords(coords) -> list:
    """Recursively flatten nested GeoJSON coordinate arrays to [(lon, lat), ...]."""
    if not coords:
        return []
    if isinstance(coords[0], (int, float)):
        return [coords[:2]]
    flat = []
    for item in coords:
        flat.extend(_flatten_geojson_coords(item))
    return flat


# ---------------------------------------------------------------------------
# Theater loader
# ---------------------------------------------------------------------------
def _load_theaters(conn, theater_code: str | None = None) -> list[dict]:
    sql = "SELECT id, name, code, area_wkt FROM sg_theaters WHERE status='active'"
    params: tuple = ()
    if theater_code:
        sql += " AND code=?"
        params = (theater_code.upper(),)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("sg_theaters query failed (table may not be seeded): %s", exc)
        return []


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------
OSINT_RELEVANCE_GATE = 0.5

_INSERT_SQL = (
    "INSERT OR IGNORE INTO sg_eo_signals "
    "(id, scene_id, satellite, bbox_wkt, cloud_pct, sensing_date, "
    "thumbnail_url, relevance_score, aoi_tag, status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_FINDINGS_INSERT_SQL = (
    "INSERT OR IGNORE INTO sg_osint_results "
    "(id, scan_id, target, source, finding_type, title, data_json, status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _write_signals(conn, signals: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    for sig in signals:
        try:
            conn.execute(
                _INSERT_SQL,
                (
                    sig["id"], sig["scene_id"], sig["satellite"], sig["bbox_wkt"],
                    sig["cloud_pct"], sig["sensing_date"], sig["thumbnail_url"],
                    sig["relevance_score"], sig["aoi_tag"], sig["status"],
                ),
            )
            inserted += 1
        except Exception as exc:
            logger.debug("Skipped scene %s: %s", sig.get("scene_id"), exc)
            skipped += 1
    return inserted, skipped


def _write_osint_findings(conn, scan_id: str, signals: list[dict]) -> int:
    """Promote EO signals with relevance_score >= OSINT_RELEVANCE_GATE into sg_osint_results."""
    inserted = 0
    for sig in signals:
        if (sig.get("relevance_score") or 0.0) < OSINT_RELEVANCE_GATE:
            continue
        data_json = json.dumps({
            "bbox_wkt": sig.get("bbox_wkt"),
            "sensing_date": sig.get("sensing_date"),
            "thumbnail_url": sig.get("thumbnail_url"),
        })
        finding_id = f"{scan_id}-{sig['id']}"
        try:
            conn.execute(
                _FINDINGS_INSERT_SQL,
                (
                    finding_id,
                    scan_id,
                    sig.get("aoi_tag"),
                    "copernicus",
                    "geo_intelligence",
                    sig.get("scene_id"),
                    data_json,
                    "new",
                ),
            )
            inserted += 1
        except Exception as exc:
            logger.debug("Skipped osint finding for %s: %s", sig.get("scene_id"), exc)
    return inserted


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def fetch_for_theaters(
    *,
    theater_code: str | None = None,
    days: int = DEFAULT_DAYS,
    dry_run: bool = False,
    conn=None,
) -> dict:
    """
    Fetch Sentinel-2 EO scenes for all active theaters (or one by code).

    Returns a dict with keys:
      status            — 'ok', 'api_unavailable', 'dry_run', or 'no_theaters'
      theaters          — number of theaters queried
      scenes_fetched    — total scenes passing cloud/date filters
      inserted          — rows inserted into sg_eo_signals
      skipped           — rows skipped (duplicates or errors)
      findings_inserted — rows promoted to sg_osint_results (relevance >= 0.5)
      scan_id           — unique identifier for this import run
      signals           — list of row dicts (dry_run only)

    On network failure with no partial results: status='api_unavailable', signals=[].
    """
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    scan_id = f"eo-{uuid.uuid4().hex[:16]}"

    _conn = conn or get_connection()
    try:
        theaters = _load_theaters(_conn, theater_code)
        if not theaters:
            return {
                "status": "no_theaters",
                "theaters": 0,
                "scenes_fetched": 0,
                "inserted": 0,
                "skipped": 0,
                "findings_inserted": 0,
                "scan_id": scan_id,
                "signals": [],
            }

        all_signals: list[dict] = []
        api_unavailable = False

        for theater in theaters:
            bbox = _bbox_from_wkt(theater.get("area_wkt") or "")
            if not bbox:
                logger.warning(
                    "Skipping theater %s — cannot parse area_wkt: %r",
                    theater["code"], theater.get("area_wkt"),
                )
                continue

            url = _build_query_url(_bbox_to_wkt(*bbox), start_dt, end_dt)
            logger.debug("Theater %s query: %s", theater["code"], url)

            scenes = _fetch_scenes(url)
            if scenes is None:
                api_unavailable = True
                logger.warning("API unavailable for theater %s — skipping", theater["code"])
                continue

            for scene in scenes:
                row = _scene_to_row(scene, theater["code"])
                if row:
                    all_signals.append(row)

            logger.info(
                "Theater %s: %d scenes from API → %d passed filters",
                theater["code"], len(scenes), sum(
                    1 for s in scenes
                    if _cloud_cover(s.get("Attributes") or []) is not None
                    and (_cloud_cover(s.get("Attributes") or []) or 100) < CLOUD_THRESHOLD
                ),
            )

        if api_unavailable and not all_signals:
            return {
                "status": "api_unavailable",
                "theaters": len(theaters),
                "scenes_fetched": 0,
                "inserted": 0,
                "skipped": 0,
                "findings_inserted": 0,
                "scan_id": scan_id,
                "signals": [],
            }

        if dry_run:
            return {
                "status": "dry_run",
                "theaters": len(theaters),
                "scenes_fetched": len(all_signals),
                "inserted": 0,
                "skipped": 0,
                "findings_inserted": 0,
                "scan_id": scan_id,
                "signals": all_signals,
            }

        inserted, skipped = _write_signals(_conn, all_signals)
        findings_inserted = _write_osint_findings(_conn, scan_id, all_signals)
        _conn.commit()

        return {
            "status": "api_unavailable" if api_unavailable else "ok",
            "theaters": len(theaters),
            "scenes_fetched": len(all_signals),
            "inserted": inserted,
            "skipped": skipped,
            "findings_inserted": findings_inserted,
            "scan_id": scan_id,
        }
    finally:
        if conn is None:
            _conn.close()


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------
def list_signals(limit: int = 50, conn=None) -> None:
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT aoi_tag, satellite, sensing_date, cloud_pct, relevance_score, status "
            "FROM sg_eo_signals ORDER BY sensing_date DESC, relevance_score DESC "
            "LIMIT %s",
            (int(limit),),
        ).fetchall()
        total = (_conn.execute("SELECT COUNT(*) FROM sg_eo_signals").fetchone() or [0])[0]
        print(f"{'AOI':12}  {'Satellite':12}  {'Date':10}  {'Cloud%':>7}  {'Score':>6}  Status")
        print("-" * 70)
        for r in rows:
            print(
                f"{(r['aoi_tag'] or ''):12}  {(r['satellite'] or ''):12}  "
                f"{(r['sensing_date'] or ''):10}  {(r['cloud_pct'] or 0):>7.1f}  "
                f"{(r['relevance_score'] or 0):>6.4f}  {r['status']}"
            )
        print(f"\nShowing {len(rows)} of {total} total rows")
    finally:
        if conn is None:
            _conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Import Copernicus Sentinel-2 EO scenes into sg_eo_signals"
    )
    ap.add_argument("--theater", metavar="CODE",
                    help="Restrict to one theater code (e.g. INDOPACOM, EUCOM)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"Lookback window in days (default: {DEFAULT_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview results without writing to DB")
    ap.add_argument("--list", action="store_true",
                    help="List existing sg_eo_signals rows and exit")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="Output results as JSON")
    ap.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if args.list:
        list_signals()
        return

    result = fetch_for_theaters(
        theater_code=args.theater,
        days=args.days,
        dry_run=args.dry_run,
    )

    if args.as_json:
        out = result.copy()
        out.pop("signals", None)  # omit large signal list from JSON summary by default
        if args.dry_run:
            out["signals"] = result.get("signals", [])
        print(json.dumps(out, indent=2, default=str))
    else:
        status = result.get("status", "unknown")
        print(f"[eo_importer] status={status}  scan_id={result.get('scan_id', '')}")
        print(f"  theaters         : {result.get('theaters', 0)}")
        print(f"  scenes_fetched   : {result.get('scenes_fetched', 0)}")
        print(f"  inserted         : {result.get('inserted', 0)}")
        print(f"  skipped          : {result.get('skipped', 0)}")
        print(f"  findings_inserted: {result.get('findings_inserted', 0)}")
        if args.dry_run and result.get("signals"):
            print(f"  (dry-run: {len(result['signals'])} signals would be written)")

    if result.get("status") == "api_unavailable":
        sys.exit(2)


if __name__ == "__main__":
    main()
