# CUI // SP-CTI — GeoINT Multi-Source Ingestor
"""
Free sources (no API key):
  - USGS Earthquake Feed (significant + all M2.5+ past week)
  - GDACS Global Disaster Alert (GeoRSS)
  - ReliefWeb Disasters API (humanitarian events)
  - OpenSky Network (live aircraft, anonymous, limited)

Usage:
    python tools/geoint/geoint_ingestor.py --fetch --json
    python tools/geoint/geoint_ingestor.py --fetch --source usgs
    python tools/geoint/geoint_ingestor.py --list --json
    python tools/geoint/geoint_ingestor.py --stats --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.geoint")

_SOURCES = {
    "usgs_significant": {
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson",
        "label": "USGS Significant Earthquakes (7 days)",
    },
    "usgs_m25": {
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
        "label": "USGS M2.5+ Earthquakes (24h)",
    },
    "gdacs": {
        "url": "https://www.gdacs.org/xml/rss.xml",
        "label": "GDACS Global Disaster Alert",
    },
    "reliefweb": {
        "url": "https://api.reliefweb.int/v1/disasters?appname=icdev&limit=50&fields[include][]=name&fields[include][]=glide&fields[include][]=date&fields[include][]=country&fields[include][]=type&fields[include][]=status",
        "label": "ReliefWeb Active Disasters",
    },
    "opensky": {
        "url": "https://opensky-network.org/api/states/all?lamin=25&lomin=-130&lamax=50&lomax=-60",
        "label": "OpenSky CONUS Aircraft (anonymous)",
    },
}


def _req(url: str, timeout: int = 20) -> bytes | None:
    headers = {"User-Agent": "ICDEV-GeoINT/1.0 (open-source research)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        logger.warning("fetch %s failed: %s", url, e)
        return None


def _event_id(source: str, value: str) -> str:
    return "geo-" + hashlib.sha256(f"{source}:{value}".encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_from_ms(ms: int | float) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return _now_iso()


# ── Severity helpers ────────────────────────────────────────────────────────

def _eq_severity(mag: float) -> str:
    if mag >= 7.0:   return "critical"
    if mag >= 6.0:   return "high"
    if mag >= 5.0:   return "medium"
    return "low"


def _gdacs_severity(alert: str) -> str:
    return {"red": "critical", "orange": "high", "green": "low"}.get(
        (alert or "").lower(), "medium")


# ── Parsers ─────────────────────────────────────────────────────────────────

def _parse_usgs(raw: bytes, source_key: str) -> list[dict]:
    data = json.loads(raw)
    out = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        geom  = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None, None])
        mag = props.get("mag") or 0.0
        place = props.get("place") or "Unknown"
        out.append({
            "id": _event_id(source_key, feat.get("id", place)),
            "source": source_key,
            "event_type": "earthquake",
            "title": f"M{mag:.1f} Earthquake — {place}",
            "description": f"Magnitude {mag} at depth {coords[2] or 0:.1f} km. "
                           f"Felt reports: {props.get('felt') or 0}.",
            "lat": coords[1],
            "lon": coords[0],
            "magnitude": float(mag),
            "severity": _eq_severity(float(mag)),
            "country": "",
            "occurred_at": _ts_from_ms(props.get("time", 0)),
            "raw_json": json.dumps({"mag": mag, "place": place,
                                     "url": props.get("url", "")}),
        })
    return out


def _parse_gdacs(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    ns = {
        "gdacs": "http://www.gdacs.org",
        "geo":   "http://www.w3.org/2003/01/geo/wgs84_pos#",
        "dc":    "http://purl.org/dc/elements/1.1/",
    }
    out = []
    channel = root.find("channel")
    if channel is None:
        return []
    for item in channel.findall("item"):
        title    = (item.findtext("title") or "").strip()
        link     = item.findtext("link") or ""
        pub      = item.findtext("pubDate") or _now_iso()
        alert    = item.findtext("gdacs:alertlevel", "", ns) or ""
        ev_type  = item.findtext("gdacs:eventtype", "", ns) or "disaster"
        country  = item.findtext("gdacs:country", "", ns) or ""
        lat_str  = item.findtext("geo:lat", "", ns) or item.findtext("gdacs:latitude", "", ns)
        lon_str  = item.findtext("geo:long", "", ns) or item.findtext("gdacs:longitude", "", ns)
        try:
            lat = float(lat_str) if lat_str else None
            lon = float(lon_str) if lon_str else None
        except ValueError:
            lat = lon = None
        out.append({
            "id": _event_id("gdacs", title + pub),
            "source": "gdacs",
            "event_type": ev_type.lower(),
            "title": title,
            "description": item.findtext("description") or "",
            "lat": lat,
            "lon": lon,
            "magnitude": None,
            "severity": _gdacs_severity(alert),
            "country": country,
            "occurred_at": pub,
            "raw_json": json.dumps({"alert": alert, "url": link, "type": ev_type}),
        })
    return out


def _parse_reliefweb(raw: bytes) -> list[dict]:
    data = json.loads(raw)
    out = []
    for item in data.get("data", []):
        fields = item.get("fields", {})
        name   = fields.get("name", "")
        glide  = fields.get("glide", "")
        status = fields.get("status", "")
        ev_types = [t.get("name", "") for t in (fields.get("type") or [])]
        countries = [c.get("name", "") for c in (fields.get("country") or [])]
        date_str = ((fields.get("date") or {}).get("event") or
                    (fields.get("date") or {}).get("created") or _now_iso())
        out.append({
            "id": _event_id("reliefweb", glide or name),
            "source": "reliefweb",
            "event_type": ev_types[0].lower() if ev_types else "disaster",
            "title": name,
            "description": f"GLIDE: {glide} | Status: {status} | "
                           f"Countries: {', '.join(countries)}",
            "lat": None,
            "lon": None,
            "magnitude": None,
            "severity": "high" if status == "current" else "medium",
            "country": countries[0] if countries else "",
            "occurred_at": date_str,
            "raw_json": json.dumps({"glide": glide, "status": status,
                                     "types": ev_types, "countries": countries}),
        })
    return out


def _parse_opensky(raw: bytes) -> list[dict]:
    data = json.loads(raw)
    states = data.get("states") or []
    out = []
    # Only sample interesting flights (military callsigns, unknown, high altitude)
    for s in states[:200]:
        # [icao24, callsign, origin_country, time_position, last_contact,
        #  longitude, latitude, baro_altitude, on_ground, velocity,
        #  true_track, vertical_rate, sensors, geo_altitude, squawk, spi, position_source]
        if len(s) < 12:
            continue
        icao24   = s[0] or ""
        callsign = (s[1] or "").strip()
        country  = s[2] or ""
        lon      = s[5]
        lat      = s[6]
        alt      = s[7] or 0
        on_ground = s[8]
        squawk   = s[14] if len(s) > 14 else ""
        if on_ground or not lat or not lon:
            continue
        # Flag: emergency squawks (7500 hijack, 7600 radio, 7700 emergency)
        is_emergency = squawk in ("7500", "7600", "7700")
        if not callsign and not is_emergency:
            continue  # skip anonymous non-emergency
        severity = "critical" if is_emergency else "low"
        title = f"Aircraft {callsign or icao24} [{country}]"
        if is_emergency:
            title = f"EMERGENCY {squawk}: {callsign or icao24} [{country}]"
        out.append({
            "id": _event_id("opensky", icao24 + str(s[3] or "")),
            "source": "opensky",
            "event_type": "aircraft",
            "title": title,
            "description": f"ICAO: {icao24} | Alt: {alt:.0f}m | Squawk: {squawk or 'none'}",
            "lat": lat,
            "lon": lon,
            "magnitude": None,
            "severity": severity,
            "country": country,
            "occurred_at": _now_iso(),
            "raw_json": json.dumps({"icao24": icao24, "callsign": callsign,
                                     "squawk": squawk, "alt": alt}),
        })
    return out


_PARSERS = {
    "usgs_significant": lambda r: _parse_usgs(r, "usgs_significant"),
    "usgs_m25":         lambda r: _parse_usgs(r, "usgs_m25"),
    "gdacs":            _parse_gdacs,
    "reliefweb":        _parse_reliefweb,
    "opensky":          _parse_opensky,
}


# ── DB helpers ──────────────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geoint_events (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            event_type TEXT,
            title TEXT,
            description TEXT,
            lat DOUBLE PRECISION,
            lon DOUBLE PRECISION,
            magnitude DOUBLE PRECISION,
            severity TEXT DEFAULT 'medium',
            country TEXT,
            occurred_at TEXT,
            fetched_at TEXT,
            raw_json TEXT,
            classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_source ON geoint_events(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_type ON geoint_events(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_sev ON geoint_events(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_fetched ON geoint_events(fetched_at)")
    conn.commit()


def _upsert(conn, events: list[dict]) -> int:
    now = _now_iso()
    inserted = 0
    for e in events:
        try:
            conn.execute(
                """INSERT INTO geoint_events
                   (id, source, event_type, title, description, lat, lon,
                    magnitude, severity, country, occurred_at, fetched_at, raw_json, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (e["id"], e["source"], e.get("event_type",""), e.get("title",""),
                 e.get("description",""), e.get("lat"), e.get("lon"),
                 e.get("magnitude"), e.get("severity","medium"),
                 e.get("country",""), e.get("occurred_at", now),
                 now, e.get("raw_json","{}"), "CUI")
            )
            inserted += 1
        except Exception as ex:
            logger.debug("upsert skip %s: %s", e.get("id"), ex)
    conn.commit()
    return inserted


# ── Public API ──────────────────────────────────────────────────────────────

def fetch_source(source_key: str) -> list[dict]:
    cfg = _SOURCES.get(source_key)
    if not cfg:
        raise ValueError(f"Unknown source: {source_key}")
    raw = _req(cfg["url"])
    if not raw:
        return []
    parser = _PARSERS.get(source_key)
    if not parser:
        return []
    try:
        return parser(raw)
    except Exception as e:
        logger.warning("parse %s failed: %s", source_key, e)
        return []


def ingest(sources: list[str] | None = None) -> dict:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    _ensure_tables(conn)
    sources = sources or list(_SOURCES.keys())
    results: dict[str, int] = {}
    for key in sources:
        events = fetch_source(key)
        inserted = _upsert(conn, events)
        results[key] = inserted
        logger.info("geoint %s: %d fetched, %d new", key, len(events), inserted)
    conn.close()
    return results


def list_events(limit: int = 200, source: str | None = None,
                event_type: str | None = None,
                has_coords: bool = True) -> list[dict]:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    _ensure_tables(conn)
    where, params = [], []
    if source:
        where.append("source = %s"); params.append(source)
    if event_type:
        where.append("event_type = %s"); params.append(event_type)
    if has_coords:
        where.append("lat IS NOT NULL AND lon IS NOT NULL")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"SELECT id,source,event_type,title,description,lat,lon,magnitude,"
        f"severity,country,occurred_at,fetched_at,raw_json,classification "
        f"FROM geoint_events {clause} ORDER BY fetched_at DESC LIMIT %s",
        [*params, limit]
    ).fetchall()
    conn.close()
    cols = ["id","source","event_type","title","description","lat","lon",
            "magnitude","severity","country","occurred_at","fetched_at",
            "raw_json","classification"]
    return [dict(zip(cols, r)) for r in rows]


def event_stats() -> dict:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    try:
        _ensure_tables(conn)
        total = (conn.execute("SELECT COUNT(*) FROM geoint_events").fetchone() or [0])[0]
        by_type = {}
        for row in conn.execute(
            "SELECT event_type, COUNT(*) FROM geoint_events GROUP BY event_type"
        ).fetchall():
            by_type[row[0]] = row[1]
        by_sev = {}
        for row in conn.execute(
            "SELECT severity, COUNT(*) FROM geoint_events GROUP BY severity"
        ).fetchall():
            by_sev[row[0]] = row[1]
        return {"total": total, "by_type": by_type, "by_severity": by_sev}
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="ICDEV GeoINT Ingestor")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--source", help="Source key (usgs_significant|usgs_m25|gdacs|reliefweb|opensky)")
    p.add_argument("--type", dest="event_type", help="Filter event type")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.fetch:
        sources = [args.source] if args.source else None
        result = ingest(sources)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"  {k}: {v} new events")
    elif args.list:
        events = list_events(source=args.source, event_type=args.event_type)
        if args.json:
            print(json.dumps(events, indent=2, default=str))
        else:
            for e in events:
                lat = f"{e['lat']:.2f}" if e['lat'] else "?"
                lon = f"{e['lon']:.2f}" if e['lon'] else "?"
                print(f"[{e['severity'].upper():8}] [{e['source']:18}] "
                      f"{e['title'][:60]} ({lat},{lon})")
    elif args.stats:
        stats = event_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"Total: {stats['total']}")
            for k, v in stats.get("by_type", {}).items():
                print(f"  {k}: {v}")
    else:
        p.print_help()
