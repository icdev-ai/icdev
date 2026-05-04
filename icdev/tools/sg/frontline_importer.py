#!/usr/bin/env python3
# CUI // SP-CTI
"""Import DeepState daily GeoJSON frontline snapshots into sg_conflict_events.

DeepState publishes daily GeoJSON FeatureCollections of Ukraine/Russia
frontline positions and controlled-area boundaries for each calendar date.
Each Feature is stored as one row with event_type='frontline_position'.

Usage:
  # Single day
  python tools/sg/frontline_importer.py --date 2024-01-15

  # Date range (bulk load)
  python tools/sg/frontline_importer.py --start-date 2024-01-01 --end-date 2024-01-31

  # Incremental delta — only dates not yet in the DB
  python tools/sg/frontline_importer.py --since 2024-01-01

  # Force re-import (delete + replace existing snapshot)
  python tools/sg/frontline_importer.py --date 2024-01-15 --force

  # Dry-run preview
  python tools/sg/frontline_importer.py --since 2024-01-01 --dry-run

  # JSON output
  python tools/sg/frontline_importer.py --since 2024-01-01 --json

Environment:
  DEEPSTATE_URL_TEMPLATE  URL pattern with {date} placeholder (YYYY-MM-DD).
                          Defaults to the public DeepState history API.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection, is_pg  # noqa: E402

_DEFAULT_URL_TEMPLATE = (
    "https://deepstatemap.live/api/history/{date}"
)

EVENT_TYPE = "frontline_position"
SOURCE = "deepstate"


def _url_template() -> str:
    return os.environ.get("DEEPSTATE_URL_TEMPLATE", _DEFAULT_URL_TEMPLATE)


def fetch_deepstate_geojson(snapshot_date: str) -> list:
    """Download and validate DeepState GeoJSON for *snapshot_date* (YYYY-MM-DD).

    Enforces https:// scheme, validates GeoJSON FeatureCollection structure,
    and returns the features list.

    Args:
        snapshot_date: Date string in YYYY-MM-DD format.

    Returns:
        List of GeoJSON Feature dicts.

    Raises:
        ValueError: If the URL template does not use https:// or the response
                    is not a valid GeoJSON FeatureCollection.
        requests.HTTPError: On non-2xx HTTP response.
        requests.RequestException: On network-level failure.
        json.JSONDecodeError: If the response body is not valid JSON.
    """
    url = _url_template().format(date=snapshot_date)
    if not url.startswith("https://"):
        raise ValueError(f"URL must use https:// scheme; got: {url!r}")

    resp = requests.get(
        url,
        headers={"Accept": "application/geo+json,application/json,*/*"},
        timeout=30,
    )
    resp.raise_for_status()

    try:
        fc = resp.json()
    except ValueError as exc:
        raise json.JSONDecodeError(
            f"Response body is not valid JSON: {exc}", doc="", pos=0
        ) from exc

    if not isinstance(fc, dict):
        raise ValueError(f"Expected a JSON object; got {type(fc).__name__}")
    if fc.get("type") != "FeatureCollection":
        raise ValueError(
            f"Expected GeoJSON type 'FeatureCollection'; got {fc.get('type')!r}"
        )
    features = fc.get("features")
    if not isinstance(features, list):
        raise ValueError(
            f"Expected 'features' to be a list; got {type(features).__name__}"
        )

    return features


def _fetch_geojson(snapshot_date: str) -> dict:
    """Download GeoJSON for a snapshot date. Returns parsed dict."""
    url = _url_template().format(date=snapshot_date)
    resp = requests.get(
        url,
        headers={"Accept": "application/geo+json,application/json,*/*"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _feature_id(snapshot_date: str, idx: int, feat: dict) -> str:
    """Deterministic 16-char hex ID for a (date, feature) pair."""
    fid = feat.get("id") or f"idx:{idx}"
    return hashlib.sha256(f"{snapshot_date}:{fid}".encode()).hexdigest()[:16]


def _get_imported_dates(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT snapshot_date FROM sg_conflict_events WHERE source = ?",
        (SOURCE,),
    ).fetchall()
    return {r[0] for r in rows}


def _date_range(start: date, end: date) -> list[str]:
    days = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _delete_snapshot(conn, snapshot_date: str) -> None:
    conn.execute(
        "DELETE FROM sg_conflict_events WHERE snapshot_date = ? AND source = ?",
        (snapshot_date, SOURCE),
    )


def _import_snapshot(conn, snapshot_date: str, fc: dict, dry_run: bool) -> dict:
    """Parse a GeoJSON FeatureCollection and upsert rows for snapshot_date."""
    features = fc.get("features") or []
    now_ts = datetime.now(timezone.utc).isoformat()
    # event_ts: midnight UTC on the snapshot date
    event_ts = f"{snapshot_date}T00:00:00+00:00"

    inserted = updated = skipped = failed = 0
    errors: list[dict] = []

    for idx, feat in enumerate(features):
        try:
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            row_id = _feature_id(snapshot_date, idx, feat)
            geom_type = geom.get("type") or ""
            geom_json = json.dumps(geom)
            props_json = json.dumps(props)
            name = (
                props.get("name")
                or props.get("Name")
                or props.get("title")
                or f"{geom_type}:{idx}"
            )
            metadata = {
                "snapshot_date": snapshot_date,
                "feature_id": str(feat.get("id") or idx),
                "geometry_type": geom_type,
            }

            if dry_run:
                skipped += 1
                continue

            existing = conn.execute(
                "SELECT created_at FROM sg_conflict_events WHERE id = ?",
                (row_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE sg_conflict_events SET
                       geometry_json=?, properties_json=?, description=?,
                       metadata_json=?, snapshot_date=?
                       WHERE id=?""",
                    (geom_json, props_json, name, json.dumps(metadata), snapshot_date, row_id),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO sg_conflict_events
                       (id, event_type, severity,
                        description, event_ts, metadata_json, created_at,
                        source, external_id, snapshot_date,
                        geometry_json, properties_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        EVENT_TYPE,
                        "low",
                        name,
                        event_ts,
                        json.dumps(metadata),
                        now_ts,
                        SOURCE,
                        f"{snapshot_date}:{feat.get('id') or idx}",
                        snapshot_date,
                        geom_json,
                        props_json,
                    ),
                )
                inserted += 1

        except Exception as exc:
            failed += 1
            errors.append({"snapshot_date": snapshot_date, "idx": idx, "error": str(exc)})

    return {
        "snapshot_date": snapshot_date,
        "total_features": len(features),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def run(
    dates: list[str],
    *,
    force: bool = False,
    dry_run: bool = False,
    as_json: bool = False,
) -> dict:
    """Import GeoJSON snapshots for each date in *dates*.

    Args:
        dates: List of YYYY-MM-DD strings to import.
        force: Delete existing rows before importing (re-import).
        dry_run: Fetch + parse but do not write to DB.
        as_json: Print JSON summary to stdout.

    Returns:
        Summary dict with per-date stats.
    """
    conn = None if dry_run else get_connection()

    imported_dates: set[str] = set()
    if conn and not force:
        imported_dates = _get_imported_dates(conn)

    per_date: list[dict] = []
    fetch_errors: list[dict] = []
    total_inserted = total_updated = total_skipped = total_failed = 0

    for snapshot_date in dates:
        if not dry_run and not force and snapshot_date in imported_dates:
            per_date.append({"snapshot_date": snapshot_date, "action": "skipped_existing"})
            continue

        try:
            fc = _fetch_geojson(snapshot_date)
        except Exception as exc:
            fetch_errors.append({"snapshot_date": snapshot_date, "error": str(exc)})
            continue

        if conn and force:
            _delete_snapshot(conn, snapshot_date)

        stats = _import_snapshot(conn, snapshot_date, fc, dry_run=dry_run)
        per_date.append(stats)
        total_inserted += stats.get("inserted", 0)
        total_updated += stats.get("updated", 0)
        total_skipped += stats.get("skipped", 0)
        total_failed += stats.get("failed", 0)

    if conn:
        conn.commit()
        conn.close()

    summary = {
        "dry_run": dry_run,
        "force": force,
        "dates_requested": len(dates),
        "dates_processed": len(per_date),
        "fetch_errors": len(fetch_errors),
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "per_date": per_date,
        "fetch_error_detail": fetch_errors,
    }

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        tag = "[DRY RUN] " if dry_run else ""
        print(
            f"{tag}Processed {len(per_date)}/{len(dates)} snapshots — "
            f"inserted={total_inserted} updated={total_updated} "
            f"failed={total_failed} fetch_errors={len(fetch_errors)}"
        )
        if fetch_errors:
            for fe in fetch_errors:
                print(f"  FETCH ERROR {fe['snapshot_date']}: {fe['error']}", file=sys.stderr)

    return summary


def _build_row(snapshot_date: str, idx: int, feat: dict, now_ts: str) -> tuple:
    """Return a parameter tuple for a single GeoJSON feature."""
    geom = feat.get("geometry") or {}
    props = feat.get("properties") or {}
    row_id = _feature_id(snapshot_date, idx, feat)
    geom_type = geom.get("type") or ""
    name = (
        props.get("name")
        or props.get("Name")
        or props.get("title")
        or f"{geom_type}:{idx}"
    )
    metadata = {
        "snapshot_date": snapshot_date,
        "feature_id": str(feat.get("id") or idx),
        "geometry_type": geom_type,
    }
    return (
        row_id,
        EVENT_TYPE,
        "low",
        name,
        f"{snapshot_date}T00:00:00+00:00",
        json.dumps(metadata),
        now_ts,
        SOURCE,
        f"{snapshot_date}:{feat.get('id') or idx}",
        snapshot_date,
        json.dumps(geom),
        json.dumps(props),
    )


def bulk_import(
    start_date,
    end_date,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Batch-import DeepState GeoJSON snapshots for a date range.

    Fetches each date's FeatureCollection, transforms every Feature into an
    sg_conflict_events row (event_type='frontline_position'), and writes the
    full batch in a single parameterized executemany call.  Duplicate rows
    (same deterministic id) are silently skipped via ON CONFLICT / INSERT OR
    IGNORE, so re-running the same range is always safe.

    Args:
        start_date: Start date inclusive — YYYY-MM-DD string or date object.
        end_date:   End date inclusive — YYYY-MM-DD string or date object.
        force:      Delete existing rows for each date before inserting.
        dry_run:    Fetch and transform without writing to the database.

    Returns:
        Summary dict with keys: dates_requested, total_inserted,
        total_skipped, total_failed, fetch_errors, per_date.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")

    dates = _date_range(start_date, end_date)

    conn = None if dry_run else get_connection()
    imported_dates: set[str] = set()
    if conn and not force:
        imported_dates = _get_imported_dates(conn)

    if is_pg():
        insert_sql = """
            INSERT INTO sg_conflict_events
                (id, event_type, severity, description, event_ts,
                 metadata_json, created_at, source, external_id,
                 snapshot_date, geometry_json, properties_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
        """
    else:
        insert_sql = """
            INSERT OR IGNORE INTO sg_conflict_events
                (id, event_type, severity, description, event_ts,
                 metadata_json, created_at, source, external_id,
                 snapshot_date, geometry_json, properties_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """

    now_ts = datetime.now(timezone.utc).isoformat()
    per_date: list[dict] = []
    fetch_errors: list[dict] = []
    total_inserted = total_skipped = total_failed = 0

    for snapshot_date in dates:
        if not dry_run and not force and snapshot_date in imported_dates:
            per_date.append({"snapshot_date": snapshot_date, "action": "skipped_existing"})
            total_skipped += 1
            continue

        try:
            features = fetch_deepstate_geojson(snapshot_date)
        except Exception as exc:
            fetch_errors.append({"snapshot_date": snapshot_date, "error": str(exc)})
            total_failed += 1
            continue

        if dry_run:
            per_date.append({
                "snapshot_date": snapshot_date,
                "action": "dry_run",
                "features": len(features),
            })
            continue

        if force:
            _delete_snapshot(conn, snapshot_date)

        rows: list[tuple] = []
        build_errors: list[dict] = []
        for idx, feat in enumerate(features):
            try:
                rows.append(_build_row(snapshot_date, idx, feat, now_ts))
            except Exception as exc:
                build_errors.append({"idx": idx, "error": str(exc)})

        inserted = 0
        if rows:
            cursor = conn.cursor()
            cursor.executemany(insert_sql, rows)
            inserted = cursor.rowcount if cursor.rowcount >= 0 else len(rows)

        total_inserted += inserted
        total_failed += len(build_errors)
        per_date.append({
            "snapshot_date": snapshot_date,
            "total_features": len(features),
            "inserted": inserted,
            "build_errors": len(build_errors),
        })

    if conn:
        conn.commit()
        conn.close()

    return {
        "dry_run": dry_run,
        "force": force,
        "dates_requested": len(dates),
        "total_inserted": total_inserted,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "fetch_errors": len(fetch_errors),
        "fetch_error_detail": fetch_errors,
        "per_date": per_date,
    }


def incremental_import(
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Import only dates not yet in sg_conflict_events (source='deepstate').

    Queries max(snapshot_date) for source='deepstate', then fetches GeoJSON
    from the next day through yesterday via bulk_import().  Returns an empty
    summary with 'no_baseline' when the table has no deepstate rows yet, or
    'already_current' when max(snapshot_date) is already yesterday.

    Args:
        force:   Delete existing rows before re-importing.
        dry_run: Fetch and transform without writing to the database.

    Returns:
        Summary dict (same shape as bulk_import) plus optional diagnostic keys.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT max(snapshot_date) FROM sg_conflict_events WHERE source = ?",
        (SOURCE,),
    ).fetchone()
    conn.close()

    max_date_str = row[0] if row else None
    if not max_date_str:
        return {
            "dry_run": dry_run,
            "no_baseline": True,
            "dates_requested": 0,
            "total_inserted": 0,
            "total_skipped": 0,
            "total_failed": 0,
            "fetch_errors": 0,
            "per_date": [],
        }

    start_date = date.fromisoformat(max_date_str) + timedelta(days=1)
    yesterday = date.today() - timedelta(days=1)

    if start_date > yesterday:
        return {
            "dry_run": dry_run,
            "already_current": True,
            "last_date": max_date_str,
            "dates_requested": 0,
            "total_inserted": 0,
            "total_skipped": 0,
            "total_failed": 0,
            "fetch_errors": 0,
            "per_date": [],
        }

    return bulk_import(start_date, yesterday, force=force, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import DeepState GeoJSON frontline snapshots into sg_conflict_events"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", metavar="YYYY-MM-DD", help="Import a single snapshot date")
    mode.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        help="Start of date range (requires --end-date)",
    )
    mode.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Import all dates from SINCE to yesterday (incremental delta)",
    )
    mode.add_argument(
        "--bulk",
        nargs=2,
        metavar=("START", "END"),
        help="Bulk import date range START..END inclusive (YYYY-MM-DD YYYY-MM-DD)",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Import only dates after max(snapshot_date) in DB through yesterday",
    )
    parser.add_argument(
        "--end-date",
        metavar="YYYY-MM-DD",
        help="End of date range inclusive (used with --start-date)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and re-import dates already in the DB",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch+parse without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    today = date.today()

    if args.bulk:
        start = date.fromisoformat(args.bulk[0])
        end = date.fromisoformat(args.bulk[1])
        if end < start:
            parser.error("--bulk END must be >= START")
        result = bulk_import(start, end, force=args.force, dry_run=args.dry_run)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            tag = "[DRY RUN] " if args.dry_run else ""
            print(
                f"{tag}bulk {args.bulk[0]}..{args.bulk[1]}: "
                f"inserted={result.get('total_inserted', 0)} "
                f"skipped={result.get('total_skipped', 0)} "
                f"failed={result.get('total_failed', 0)}"
            )
        if result.get("total_failed", 0) > 0 or result.get("fetch_errors", 0) > 0:
            sys.exit(1)
        return

    if args.incremental:
        result = incremental_import(force=args.force, dry_run=args.dry_run)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("no_baseline"):
                print("--incremental: no deepstate rows in DB yet; nothing to delta from.")
            elif result.get("already_current"):
                print(f"--incremental: already current through {result['last_date']}.")
            else:
                tag = "[DRY RUN] " if args.dry_run else ""
                print(
                    f"{tag}incremental: "
                    f"inserted={result.get('total_inserted', 0)} "
                    f"skipped={result.get('total_skipped', 0)} "
                    f"failed={result.get('total_failed', 0)}"
                )
        if result.get("total_failed", 0) > 0 or result.get("fetch_errors", 0) > 0:
            sys.exit(1)
        return

    if args.date:
        dates = [args.date]
    elif args.since:
        since = date.fromisoformat(args.since)
        yesterday = today - timedelta(days=1)
        if since > yesterday:
            print(f"--since {args.since} is not before today; nothing to import.")
            sys.exit(0)
        dates = _date_range(since, yesterday)
    else:
        if not args.end_date:
            parser.error("--start-date requires --end-date")
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
        if end < start:
            parser.error("--end-date must be >= --start-date")
        dates = _date_range(start, end)

    result = run(dates, force=args.force, dry_run=args.dry_run, as_json=args.as_json)
    if result.get("total_failed", 0) > 0 or result.get("fetch_errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
