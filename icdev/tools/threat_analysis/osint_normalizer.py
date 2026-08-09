# CUI // SP-CTI
"""OSINT Record Normalizer — maps raw sg_raw_signals rows to sg_osint_normalized.

Pipeline per record:
  1. parse_timestamp(signal_date)  → ISO 8601 UTC string
  2. normalize_geo(geo_hint)       → (latitude, longitude) | (None, None)
  3. classify_event_type(title, body) → (event_type, confidence)
  4. INSERT into sg_osint_normalized
  5. Mark raw record processed=1

CLI:
  python -m icdev.tools.threat_analysis.osint_normalizer [--limit N] [--json]
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection, is_pg  # noqa: E402

logger = get_logger(__name__)

# ── country centroid lookup (offline, ~ISO 3166-1 top entries) ────────────────
# Covers the countries most frequently appearing in OSINT feeds.
_COUNTRY_CENTROIDS: Dict[str, Tuple[float, float]] = {
    "afghanistan": (33.94, 67.71),
    "albania": (41.15, 20.17),
    "algeria": (28.03, 1.66),
    "angola": (-11.20, 17.87),
    "argentina": (-38.42, -63.62),
    "armenia": (40.07, 45.04),
    "australia": (-25.27, 133.78),
    "austria": (47.52, 14.55),
    "azerbaijan": (40.14, 47.58),
    "bahrain": (26.02, 50.55),
    "bangladesh": (23.68, 90.36),
    "belarus": (53.71, 27.95),
    "belgium": (50.50, 4.47),
    "bolivia": (-16.29, -63.59),
    "bosnia": (43.92, 17.68),
    "brazil": (-14.24, -51.93),
    "bulgaria": (42.73, 25.49),
    "burkina faso": (12.36, -1.53),
    "burundi": (-3.37, 29.92),
    "cambodia": (12.57, 104.99),
    "cameroon": (3.85, 11.50),
    "canada": (56.13, -106.35),
    "central african republic": (6.61, 20.94),
    "chad": (15.45, 18.73),
    "chile": (-35.68, -71.54),
    "china": (35.86, 104.20),
    "colombia": (4.57, -74.30),
    "congo": (-0.23, 15.83),
    "democratic republic of the congo": (-4.04, 21.76),
    "drc": (-4.04, 21.76),
    "cuba": (21.52, -77.78),
    "czech republic": (49.82, 15.47),
    "denmark": (56.26, 9.50),
    "djibouti": (11.83, 42.59),
    "ecuador": (-1.83, -78.18),
    "egypt": (26.82, 30.80),
    "eritrea": (15.18, 39.78),
    "ethiopia": (9.15, 40.49),
    "finland": (61.92, 25.75),
    "france": (46.23, 2.21),
    "georgia": (42.32, 43.36),
    "germany": (51.17, 10.45),
    "ghana": (7.95, -1.02),
    "greece": (39.07, 21.82),
    "guatemala": (15.78, -90.23),
    "guinea": (9.95, -11.61),
    "haiti": (18.97, -72.29),
    "honduras": (15.20, -86.24),
    "hungary": (47.16, 19.50),
    "india": (20.59, 78.96),
    "indonesia": (-0.79, 113.92),
    "iran": (32.43, 53.69),
    "iraq": (33.22, 43.68),
    "ireland": (53.14, -8.24),
    "israel": (31.05, 34.85),
    "italy": (41.87, 12.57),
    "ivory coast": (7.54, -5.55),
    "japan": (36.20, 138.25),
    "jordan": (30.59, 36.24),
    "kazakhstan": (48.02, 66.92),
    "kenya": (-0.02, 37.91),
    "kosovo": (42.57, 20.90),
    "kuwait": (29.31, 47.48),
    "kyrgyzstan": (41.20, 74.77),
    "laos": (19.86, 102.50),
    "lebanon": (33.85, 35.86),
    "libya": (26.34, 17.23),
    "madagascar": (-18.77, 46.87),
    "malawi": (-13.25, 34.30),
    "malaysia": (4.21, 108.96),
    "mali": (17.57, -3.99),
    "mauritania": (21.01, -10.94),
    "mexico": (23.63, -102.55),
    "moldova": (47.41, 28.37),
    "mongolia": (46.86, 103.85),
    "morocco": (31.79, -7.09),
    "mozambique": (-18.67, 35.53),
    "myanmar": (16.87, 96.08),
    "namibia": (-22.96, 18.49),
    "nepal": (28.39, 84.12),
    "netherlands": (52.13, 5.29),
    "nicaragua": (12.87, -85.21),
    "niger": (17.61, 8.08),
    "nigeria": (9.08, 8.68),
    "north korea": (40.34, 127.51),
    "norway": (60.47, 8.47),
    "oman": (21.51, 55.92),
    "pakistan": (30.38, 69.35),
    "palestine": (31.95, 35.23),
    "gaza": (31.35, 34.31),
    "west bank": (31.95, 35.30),
    "panama": (8.54, -80.78),
    "paraguay": (-23.44, -58.44),
    "peru": (-9.19, -75.02),
    "philippines": (12.88, 121.77),
    "poland": (51.92, 19.15),
    "portugal": (39.40, -8.22),
    "qatar": (25.35, 51.18),
    "romania": (45.94, 24.97),
    "russia": (61.52, 105.32),
    "rwanda": (-1.94, 29.87),
    "saudi arabia": (23.89, 45.08),
    "senegal": (14.50, -14.45),
    "serbia": (44.02, 21.01),
    "sierra leone": (8.46, -11.78),
    "somalia": (5.15, 46.20),
    "south africa": (-30.56, 22.94),
    "south korea": (35.91, 127.77),
    "south sudan": (6.88, 31.31),
    "spain": (40.46, -3.75),
    "sri lanka": (7.87, 80.77),
    "sudan": (12.86, 30.22),
    "sweden": (60.13, 18.64),
    "switzerland": (46.82, 8.23),
    "syria": (34.80, 38.99),
    "taiwan": (23.70, 120.96),
    "tajikistan": (38.86, 71.28),
    "tanzania": (-6.37, 34.89),
    "thailand": (15.87, 100.99),
    "turkey": (38.96, 35.24),
    "turkmenistan": (38.97, 59.56),
    "uganda": (1.37, 32.29),
    "ukraine": (48.38, 31.17),
    "united arab emirates": (23.42, 53.85),
    "uae": (23.42, 53.85),
    "united kingdom": (55.38, -3.44),
    "uk": (55.38, -3.44),
    "united states": (37.09, -95.71),
    "usa": (37.09, -95.71),
    "uruguay": (-32.52, -55.77),
    "uzbekistan": (41.38, 64.59),
    "venezuela": (6.42, -66.59),
    "vietnam": (14.06, 108.28),
    "yemen": (15.55, 48.52),
    "zambia": (-13.13, 27.85),
    "zimbabwe": (-19.02, 29.15),
}

# Aliases / multi-word substrings (checked in order)
_COUNTRY_ALIASES: List[Tuple[str, str]] = [
    ("democratic republic of the congo", "democratic republic of the congo"),
    ("south sudan", "south sudan"),
    ("south korea", "south korea"),
    ("north korea", "north korea"),
    ("saudi arabia", "saudi arabia"),
    ("south africa", "south africa"),
    ("west bank", "west bank"),
    ("burkina faso", "burkina faso"),
    ("ivory coast", "ivory coast"),
    ("sierra leone", "sierra leone"),
    ("united arab emirates", "united arab emirates"),
    ("central african republic", "central african republic"),
    ("united states", "united states"),
    ("united kingdom", "united kingdom"),
]

# ── event type keyword sets ────────────────────────────────────────────────────
_EVENT_KEYWORDS: List[Tuple[str, frozenset]] = [
    ("conflict", frozenset({
        "attack", "attacks", "attacked", "explosion", "bomb", "bombing",
        "shooting", "airstrike", "airstrikes", "killed", "wounded", "casualties",
        "ambush", "offensive", "military", "troops", "forces", "assault",
        "battle", "war", "warfare", "insurgent", "insurgency", "rebel",
        "terrorist", "terrorism", "missile", "rocket", "mortar", "artillery",
        "shelling", "gunfire", "sniper", "ied", "clashes", "fighting",
        "combat", "siege", "blockade", "convoy",
    })),
    ("cyber", frozenset({
        "malware", "ransomware", "phishing", "breach", "hack", "hacked",
        "hacking", "vulnerability", "exploit", "zero-day", "cve",
        "compromise", "compromised", "ddos", "intrusion", "credential",
        "data leak", "data breach", "cyber", "cyberattack", "backdoor",
        "trojan", "botnet", "apt", "threat actor", "espionage", "spyware",
        "keylogger", "exfiltration", "lateral movement", "command and control",
    })),
    ("geopolitical", frozenset({
        "sanction", "sanctions", "election", "elections", "diplomatic",
        "treaty", "summit", "bilateral", "referendum", "coup", "minister",
        "president", "government", "parliament", "policy", "nuclear",
        "negotiate", "negotiation", "alliance", "nato", "un resolution",
        "ceasefire", "peace talks", "trade war", "tariff",
    })),
    ("natural_disaster", frozenset({
        "earthquake", "flood", "flooding", "hurricane", "tsunami",
        "wildfire", "cyclone", "tornado", "drought", "landslide",
        "eruption", "volcanic", "typhoon", "storm", "monsoon",
        "avalanche", "famine",
    })),
    ("protest", frozenset({
        "protest", "protests", "rally", "demonstration", "march",
        "strike", "riot", "unrest", "uprising", "civil unrest",
        "demonstrators", "activists", "occupy", "blockade",
    })),
    ("intelligence", frozenset({
        "spy", "spying", "surveillance", "leak", "leaked", "espionage",
        "classified", "defection", "defector", "agent", "covert",
        "intelligence agency", "cia", "fsb", "gru", "mossad", "mi6",
        "signals intelligence", "humint", "sigint", "psyop",
    })),
]

# ── coordinate pattern: "12.34, -56.78" or "12.34N 56.78E" etc. ──────────────
_COORD_RE = re.compile(
    r"(-?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)"
)


# ── public API ────────────────────────────────────────────────────────────────

def parse_timestamp(signal_date: Optional[str]) -> str:
    """Normalize a date string to ISO 8601 UTC.  Falls back to utcnow."""
    if not signal_date:
        return _utcnow_iso()

    s = signal_date.strip()

    # Already ISO 8601 / RFC 3339
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    # YYYYMMDD (GDELT)
    if re.fullmatch(r"\d{8}", s):
        try:
            dt = datetime.strptime(s, "%Y%m%d")
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    # RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(s)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    # Epoch integer / float string
    try:
        ts = float(s)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass

    logger.debug("Unrecognized date format '%s', using utcnow", s)
    return _utcnow_iso()


def normalize_geo(geo_hint: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Extract (latitude, longitude) from a geo_hint string.

    Resolution order:
      1. Explicit "lat, lon" coordinate pattern in the string
      2. Country name substring match against centroid table
      3. (None, None) — could not resolve
    """
    if not geo_hint:
        return None, None

    text = geo_hint.strip()

    # Try explicit coordinate pair
    m = _COORD_RE.search(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return round(lat, 5), round(lon, 5)

    lower = text.lower()

    # Multi-word aliases first (order matters)
    for alias, key in _COUNTRY_ALIASES:
        if alias in lower and key in _COUNTRY_CENTROIDS:
            lat, lon = _COUNTRY_CENTROIDS[key]
            return lat, lon

    # Single-word country lookup
    for country, (lat, lon) in _COUNTRY_CENTROIDS.items():
        if country in lower:
            return lat, lon

    return None, None


def classify_event_type(title: str, body: str) -> Tuple[str, float]:
    """Classify an OSINT record into an event_type with a confidence score.

    Returns (event_type, confidence) where confidence is in [0.0, 1.0].
    """
    combined = f"{title} {body}".lower()
    tokens = set(re.findall(r"[a-z][\w-]*", combined))

    scores: Dict[str, int] = {}
    for etype, keywords in _EVENT_KEYWORDS:
        hit = len(keywords & tokens)
        if hit:
            scores[etype] = hit

    if not scores:
        return "other", 0.0

    best_type = max(scores, key=lambda k: scores[k])
    best_hits = scores[best_type]
    # Confidence: hits / keyword_set_size, capped at 1.0
    kw_set_size = len(dict(_EVENT_KEYWORDS)[best_type])
    confidence = min(best_hits / kw_set_size, 1.0)
    return best_type, round(confidence, 3)


def normalize_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map one sg_raw_signals row to the sg_osint_normalized schema."""
    timestamp = parse_timestamp(raw.get("signal_date"))
    lat, lon = normalize_geo(raw.get("geo_hint"))
    event_type, confidence = classify_event_type(
        raw.get("title") or "",
        raw.get("body") or "",
    )
    return {
        "raw_signal_id": raw.get("id"),
        "timestamp": timestamp,
        "source": (raw.get("source") or "").strip(),
        "latitude": lat,
        "longitude": lon,
        "event_type": event_type,
        "confidence": confidence,
        "geo_hint": raw.get("geo_hint"),
        "title": (raw.get("title") or "")[:500],
        "created_at": _utcnow_iso(),
    }


def run_normalization(limit: int = 500) -> Dict[str, Any]:
    """Read unprocessed raw signals, normalize, and store.

    Returns summary dict: {inserted, skipped, errors, total_processed}.
    """
    _ensure_normalized_table()

    conn = get_connection()
    pg = is_pg()

    if pg:
        raw_rows = conn.execute(
            "SELECT id, title, body, source, signal_date, geo_hint "
            "FROM sg_raw_signals WHERE processed = 0 LIMIT %s",
            (limit,),
        ).fetchall()
    else:
        raw_rows = conn.execute(
            "SELECT id, title, body, source, signal_date, geo_hint "
            "FROM sg_raw_signals WHERE processed = 0 LIMIT %s",
            (limit,),
        ).fetchall()
    conn.close()

    inserted = skipped = errors = 0

    for row in raw_rows:
        raw = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "title": row[1], "body": row[2],
            "source": row[3], "signal_date": row[4], "geo_hint": row[5],
        }
        try:
            norm = normalize_record(raw)
            _insert_normalized(norm)
            _mark_processed(raw["id"])
            inserted += 1
        except Exception as exc:
            logger.warning("Normalization failed for raw id=%s: %s", raw.get("id"), exc)
            errors += 1

    total = inserted + skipped + errors
    logger.info("OSINT normalization: inserted=%d skipped=%d errors=%d", inserted, skipped, errors)
    return {"inserted": inserted, "skipped": skipped, "errors": errors, "total_processed": total}


# ── internals ─────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_normalized_table() -> None:
    try:
        from tools.db.migrations._157_sg_osint_normalized.up import up  # noqa: F401
        up()
    except Exception:
        # Inline fallback
        try:
            conn = get_connection()
            pg = is_pg()
            if pg:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS sg_osint_normalized ("
                    "id SERIAL PRIMARY KEY, raw_signal_id INTEGER, "
                    "timestamp TIMESTAMPTZ NOT NULL, source TEXT NOT NULL DEFAULT '', "
                    "latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, "
                    "event_type TEXT NOT NULL DEFAULT 'other', confidence REAL NOT NULL DEFAULT 0.0, "
                    "geo_hint TEXT, title TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
                )
            else:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS sg_osint_normalized ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, raw_signal_id INTEGER, "
                    "timestamp TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', "
                    "latitude REAL, longitude REAL, "
                    "event_type TEXT NOT NULL DEFAULT 'other', confidence REAL NOT NULL DEFAULT 0.0, "
                    "geo_hint TEXT, title TEXT, created_at TEXT NOT NULL)"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_ts ON sg_osint_normalized(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sg_osint_norm_type ON sg_osint_normalized(event_type)"
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Could not ensure sg_osint_normalized table: %s", exc)


def _insert_normalized(norm: Dict[str, Any]) -> None:
    conn = get_connection()
    pg = is_pg()
    if pg:
        conn.execute(
            "INSERT INTO sg_osint_normalized "
            "(raw_signal_id, timestamp, source, latitude, longitude, event_type, confidence, geo_hint, title, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                norm["raw_signal_id"], norm["timestamp"], norm["source"],
                norm["latitude"], norm["longitude"], norm["event_type"],
                norm["confidence"], norm["geo_hint"], norm["title"], norm["created_at"],
            ),
        )
    else:
        conn.execute(
            "INSERT INTO sg_osint_normalized "
            "(raw_signal_id, timestamp, source, latitude, longitude, event_type, confidence, geo_hint, title, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                norm["raw_signal_id"], norm["timestamp"], norm["source"],
                norm["latitude"], norm["longitude"], norm["event_type"],
                norm["confidence"], norm["geo_hint"], norm["title"], norm["created_at"],
            ),
        )
    conn.commit()
    conn.close()


def _mark_processed(raw_id: int) -> None:
    conn = get_connection()
    if is_pg():
        conn.execute("UPDATE sg_raw_signals SET processed = 1 WHERE id = %s", (raw_id,))
    else:
        conn.execute("UPDATE sg_raw_signals SET processed = 1 WHERE id = %s", (raw_id,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="ICDEV™ OSINT Record Normalizer")
    parser.add_argument("--limit", type=int, default=500, help="Max raw records to process")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    result = run_normalization(limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Inserted : {result['inserted']}")
        print(f"Skipped  : {result['skipped']}")
        print(f"Errors   : {result['errors']}")
        print(f"Processed: {result['total_processed']}")
