#!/usr/bin/env python3
# CUI // SP-CTI
"""GDELT 2.0 narrative-events bulk importer for Strategos.

Downloads GDELT 2.0 export CSV files, filters rows to Ukraine and Taiwan
AOIs, and upserts them into sg_conflict_events (type = narrative_event).

The table uses source='gdelt' and external_id=GLOBALEVENTID for
deduplication via the existing UNIQUE INDEX idx_sg_ce_source_ext.

AOI definitions
---------------
ukraine  : ActionGeo country code "UP" (FIPS 10-4)
             OR lat [44.0, 53.0] / lon [21.0, 41.0]
taiwan   : ActionGeo country code "TW" (FIPS 10-4)
             OR lat [21.0, 26.5] / lon [119.0, 124.5]

GDELT 2.0 export CSV column layout (tab-delimited, no header, 61 cols)
-----------------------------------------------------------------------
 0  GLOBALEVENTID     1  SQLDATE
 6  Actor1Name       16  Actor2Name
26  EventCode        30  GoldsteinScale   31  NumMentions
34  AvgTone          52  ActionGeo_FullName
53  ActionGeo_CountryCode  56  ActionGeo_Lat  57  ActionGeo_Long
60  SOURCEURL

Usage
-----
  python tools/strategos/gdelt_importer.py               # last 15-min file
  python tools/strategos/gdelt_importer.py --days 3      # last 3 days
  python tools/strategos/gdelt_importer.py --date 20240101  # specific date
  python tools/strategos/gdelt_importer.py --file /tmp/events.csv
  python tools/strategos/gdelt_importer.py --dry-run
  python tools/strategos/gdelt_importer.py --json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# GDELT 2.0 URLs
# ---------------------------------------------------------------------------
GDELT_LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_V2_BASE = "http://data.gdeltproject.org/gdeltv2"
DOWNLOAD_TIMEOUT = 60  # seconds

# ---------------------------------------------------------------------------
# GDELT 2.0 export CSV column indices
# ---------------------------------------------------------------------------
COL_EVENT_ID     = 0
COL_DATE         = 1
COL_ACTOR1_NAME  = 6
COL_ACTOR2_NAME  = 16
COL_EVENT_CODE   = 26
COL_GOLDSTEIN    = 30
COL_NUM_MENTIONS = 31
COL_AVG_TONE     = 34
COL_GEO_FULLNAME = 52
COL_COUNTRY_CODE = 53
COL_LAT          = 56
COL_LON          = 57
COL_SOURCE_URL   = 60
MIN_COLS         = 61

# ---------------------------------------------------------------------------
# AOI definitions
# ---------------------------------------------------------------------------
_AOIS: dict[str, dict] = {
    "ukraine": {
        "country_codes": {"UP"},
        "lat": (44.0, 53.0),
        "lon": (21.0, 41.0),
    },
    "taiwan": {
        "country_codes": {"TW"},
        "lat": (21.0, 26.5),
        "lon": (119.0, 124.5),
    },
}


def _match_aoi(country_code: str, lat: float | None, lon: float | None) -> str | None:
    for name, aoi in _AOIS.items():
        if country_code and country_code.upper() in aoi["country_codes"]:
            return name
        if (
            lat is not None
            and lon is not None
            and aoi["lat"][0] <= lat <= aoi["lat"][1]
            and aoi["lon"][0] <= lon <= aoi["lon"][1]
        ):
            return name
    return None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def _fetch_lastupdate_urls() -> list[str]:
    """Return export CSV ZIP URLs from GDELT lastupdate.txt."""
    try:
        with urllib.request.urlopen(GDELT_LASTUPDATE_URL, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            text = resp.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch lastupdate.txt: {exc}") from exc

    urls = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[2].endswith(".export.CSV.zip"):
            urls.append(parts[2])
    return urls


def _build_date_urls(date_str: str) -> list[str]:
    """Return all 96 possible 15-min export CSV ZIP URLs for YYYYMMDD."""
    urls = []
    for hour in range(24):
        for minute in (0, 15, 30, 45):
            ts = f"{date_str}{hour:02d}{minute:02d}00"
            urls.append(f"{GDELT_V2_BASE}/{ts}.export.CSV.zip")
    return urls


def _build_days_urls(days: int) -> list[str]:
    """Return export CSV ZIP URLs for the last N days (newest first)."""
    now = datetime.now(timezone.utc)
    urls = []
    for d in range(days):
        day = now - timedelta(days=d)
        urls.extend(_build_date_urls(day.strftime("%Y%m%d")))
    return urls


# ---------------------------------------------------------------------------
# Download + parse
# ---------------------------------------------------------------------------
def _download_zip(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            return resp.read()
    except Exception as exc:
        logger.debug("Skip %s — %s", url, exc)
        return None


def _open_zip_csv(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        csv_name = next(
            n for n in zf.namelist() if n.upper().endswith(".CSV")
        )
        return zf.read(csv_name)


def _load_local_file(path: Path) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            csv_name = next(
                n for n in zf.namelist() if n.upper().endswith(".CSV")
            )
            return zf.read(csv_name)
    return path.read_bytes()


def _parse_rows(csv_bytes: bytes) -> list[dict]:
    """Parse GDELT 2.0 export CSV and return AOI-matched rows."""
    rows = []
    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    for cols in reader:
        if len(cols) < MIN_COLS:
            continue

        country_code = cols[COL_COUNTRY_CODE].strip()
        try:
            lat = float(cols[COL_LAT]) if cols[COL_LAT].strip() else None
            lon = float(cols[COL_LON]) if cols[COL_LON].strip() else None
        except ValueError:
            lat = lon = None

        aoi = _match_aoi(country_code, lat, lon)
        if aoi is None:
            continue

        def _float(v: str) -> float | None:
            try:
                return float(v) if v.strip() else None
            except ValueError:
                return None

        def _int(v: str) -> int | None:
            try:
                return int(v) if v.strip() else None
            except ValueError:
                return None

        rows.append(
            {
                "event_id":        cols[COL_EVENT_ID].strip(),
                "event_date":      cols[COL_DATE].strip() or None,
                "actor1":          cols[COL_ACTOR1_NAME].strip() or None,
                "actor2":          cols[COL_ACTOR2_NAME].strip() or None,
                "event_code":      cols[COL_EVENT_CODE].strip() or None,
                "goldstein_scale": _float(cols[COL_GOLDSTEIN]),
                "lat":             lat,
                "lon":             lon,
                "geo_fullname":    cols[COL_GEO_FULLNAME].strip() or None,
                "aoi":             aoi,
                "avg_tone":        _float(cols[COL_AVG_TONE]),
                "num_mentions":    _int(cols[COL_NUM_MENTIONS]),
                "source_url":      cols[COL_SOURCE_URL].strip() or None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# DB upsert — uses existing (source, external_id) UNIQUE constraint
# ---------------------------------------------------------------------------
def _upsert_rows(conn, rows: list[dict], now_ts: str) -> tuple[int, int]:
    """Insert rows; skip duplicates. Returns (inserted, skipped)."""
    inserted = skipped = 0
    for row in rows:
        raw = json.dumps(row)
        row_id = f"gdelt_{row['event_id']}" if row["event_id"] else f"gdelt_{hash(raw)}"
        description = row.get("geo_fullname") or (
            f"{row['actor1'] or ''} vs {row['actor2'] or ''}".strip(" vs") or ""
        )
        try:
            conn.execute(
                """
                INSERT INTO sg_conflict_events
                    (id, event_type, source, external_id, event_ts, description,
                     actor1, actor2, event_code, goldstein_scale, lat, lon,
                     aoi, avg_tone, num_mentions, source_url, metadata_json)
                VALUES
                    (%s, 'narrative_event', 'gdelt', %s, %s, %s,
                     %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s)
                """,
                (
                    row_id,
                    row["event_id"],
                    row["event_date"],
                    description,
                    row["actor1"],
                    row["actor2"],
                    row["event_code"],
                    row["goldstein_scale"],
                    row["lat"],
                    row["lon"],
                    row["aoi"],
                    row["avg_tone"],
                    row["num_mentions"],
                    row["source_url"],
                    raw,
                ),
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------
def run(
    *,
    file: Path | None = None,
    date: str | None = None,
    days: int | None = None,
    dry_run: bool = False,
    as_json: bool = False,
) -> dict:
    """Run the GDELT importer.

    Priority: file > date > days > last-update (default).
    """
    now_ts = datetime.now(timezone.utc).isoformat()
    all_rows: list[dict] = []
    files_attempted = files_ok = 0

    if file is not None:
        files_attempted += 1
        try:
            csv_bytes = _load_local_file(file)
            all_rows.extend(_parse_rows(csv_bytes))
            files_ok += 1
        except Exception as exc:
            logger.error("Failed to load %s: %s", file, exc)
    else:
        if date is not None:
            urls = _build_date_urls(date)
        elif days is not None:
            urls = _build_days_urls(days)
        else:
            urls = _fetch_lastupdate_urls()

        logger.info("Fetching %d GDELT file(s)", len(urls))
        for url in urls:
            files_attempted += 1
            data = _download_zip(url)
            if data is None:
                continue
            try:
                csv_bytes = _open_zip_csv(data)
                rows = _parse_rows(csv_bytes)
                all_rows.extend(rows)
                files_ok += 1
                logger.debug("  %s -> %d AOI rows", url.split("/")[-1], len(rows))
            except Exception as exc:
                logger.warning("Parse error for %s: %s", url, exc)

    # In-memory dedup by event_id
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in all_rows:
        eid = row["event_id"]
        if eid and eid not in seen:
            seen.add(eid)
            deduped.append(row)
        elif not eid:
            deduped.append(row)

    aoi_counts: dict[str, int] = {}
    for row in deduped:
        aoi_counts[row["aoi"]] = aoi_counts.get(row["aoi"], 0) + 1

    if dry_run:
        summary = {
            "dry_run": True,
            "files_attempted": files_attempted,
            "files_ok": files_ok,
            "total_aoi_rows": len(deduped),
            "by_aoi": aoi_counts,
        }
        if as_json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"[DRY RUN] {files_ok}/{files_attempted} files OK")
            print(f"  {len(deduped)} AOI rows: {aoi_counts}")
        return summary

    conn = get_connection()
    inserted, skipped = _upsert_rows(conn, deduped, now_ts)
    conn.commit()
    conn.close()

    summary = {
        "dry_run": False,
        "files_attempted": files_attempted,
        "files_ok": files_ok,
        "total_aoi_rows": len(deduped),
        "by_aoi": aoi_counts,
        "inserted": inserted,
        "skipped_duplicates": skipped,
    }

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"GDELT import: {files_ok}/{files_attempted} files — "
            f"{inserted} inserted, {skipped} duplicate(s) skipped"
        )
        print(f"  AOI breakdown: {aoi_counts}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import GDELT narrative events for Ukraine/Taiwan AOIs"
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--file", type=Path, help="Local CSV or ZIP file")
    src.add_argument("--date", metavar="YYYYMMDD", help="All 15-min files for a date")
    src.add_argument("--days", type=int, metavar="N", help="Last N days of files")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    result = run(
        file=args.file,
        date=args.date,
        days=args.days,
        dry_run=args.dry_run,
        as_json=args.as_json,
    )
    if result.get("files_ok", 1) == 0 and result.get("files_attempted", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
