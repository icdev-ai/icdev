"""AIS vessel data importer — parses NMEA AIS files and persists to sg_tracks.

Supports message types:
  1/2/3  — Class A Position Report (lat, lon, speed, heading)
  5      — Static and Voyage Related Data (vessel_type name; no position)
  18     — Class B CS Position Report (lat, lon, speed, heading)
  21     — Aid-to-Navigation Report (lat, lon, name/type)

Air-gap safe: reads local NMEA files; no network I/O.

Usage:
  python tools/ais/ais_importer.py --file data.nmea --json
  python tools/ais/ais_importer.py --file data.nmea --file path2.nmea
  python tools/ais/ais_importer.py --dir /path/to/nmea/ --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- pyais import (required) -------------------------------------------
try:
    from pyais.stream import FileReaderStream
    from pyais.exceptions import UnknownMessageException
    PYAIS_AVAILABLE = True
except ImportError:
    PYAIS_AVAILABLE = False

# --- database ----------------------------------------------------------
# sg_tracks lives in the geosigint app DB; import its connection helper.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from apps.geosigint.models import get_connection, init_db  # noqa: E402

# ITU ship-type codes → human-readable category (ITU-R M.1371-5, Table 22)
_SHIP_TYPE_MAP: dict[int, str] = {
    0: "Not available",
    # 1-19: reserved
    20: "Wing in ground",
    21: "Wing in ground — hazardous cat A",
    22: "Wing in ground — hazardous cat B",
    23: "Wing in ground — hazardous cat C",
    24: "Wing in ground — hazardous cat D",
    30: "Fishing",
    31: "Towing",
    32: "Towing (large)",
    33: "Dredging/underwater ops",
    34: "Diving ops",
    35: "Military ops",
    36: "Sailing",
    37: "Pleasure craft",
    40: "High speed craft",
    41: "High speed craft — hazardous cat A",
    42: "High speed craft — hazardous cat B",
    43: "High speed craft — hazardous cat C",
    44: "High speed craft — hazardous cat D",
    50: "Pilot vessel",
    51: "Search and rescue",
    52: "Tug",
    53: "Port tender",
    54: "Anti-pollution",
    55: "Law enforcement",
    58: "Medical transport",
    59: "Non-combatant ship",
    60: "Passenger",
    61: "Passenger — hazardous cat A",
    62: "Passenger — hazardous cat B",
    63: "Passenger — hazardous cat C",
    64: "Passenger — hazardous cat D",
    70: "Cargo",
    71: "Cargo — hazardous cat A",
    72: "Cargo — hazardous cat B",
    73: "Cargo — hazardous cat C",
    74: "Cargo — hazardous cat D",
    80: "Tanker",
    81: "Tanker — hazardous cat A",
    82: "Tanker — hazardous cat B",
    83: "Tanker — hazardous cat C",
    84: "Tanker — hazardous cat D",
    90: "Other",
}


def _ship_type_label(code: int | None) -> str:
    if code is None:
        return "Unknown"
    if code in _SHIP_TYPE_MAP:
        return _SHIP_TYPE_MAP[code]
    # Ranges not explicitly listed
    if 1 <= code <= 19:
        return "Reserved"
    if 25 <= code <= 29:
        return "Wing in ground (other)"
    if 38 <= code <= 39:
        return "Reserved"
    if 45 <= code <= 49:
        return "High speed craft (other)"
    if 56 <= code <= 57:
        return "Spare"
    if 65 <= code <= 69:
        return "Passenger (other)"
    if 75 <= code <= 79:
        return "Cargo (other)"
    if 85 <= code <= 89:
        return "Tanker (other)"
    if 91 <= code <= 99:
        return "Other"
    return f"Type {code}"


def _safe_float(value) -> float | None:
    """Return float or None for sentinel/invalid AIS coordinate values."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # AIS uses 91° / 181° as 'not available' sentinels
    if f in (91.0, -91.0, 181.0, -181.0, 0.0) and f != 0.0:
        return None
    return f


def _safe_heading(value) -> float | None:
    try:
        h = float(value)
    except (TypeError, ValueError):
        return None
    return None if h == 511.0 else h  # 511 = not available


def _safe_speed(value) -> float | None:
    try:
        s = float(value)
    except (TypeError, ValueError):
        return None
    return None if s >= 102.2 else s  # 102.2+ = not available


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_file(
    nmea_path: Path,
    vessel_type_cache: dict[str, str],
) -> tuple[list[dict], list[str]]:
    """Parse one NMEA file, return (rows, errors).

    Two-pass logic:
      Pass 1 — collect vessel_type from type 5 messages (keyed by mmsi).
      Pass 2 — build position rows from types 1/2/3/18/21.
    """
    errors: list[str] = []
    type5_data: dict[str, str] = {}  # mmsi → vessel_type label
    position_rows: list[dict] = []

    source = str(nmea_path)

    # Pass 1: harvest type 5 vessel types
    try:
        for msg in FileReaderStream(source):
            try:
                decoded = msg.decode()
            except (UnknownMessageException, Exception):
                continue
            if decoded.msg_type != 5:
                continue
            try:
                mmsi = str(decoded.mmsi)
                ship_type = getattr(decoded, "ship_type", None)
                label = _ship_type_label(int(ship_type) if ship_type is not None else None)
                type5_data[mmsi] = label
            except Exception as exc:
                errors.append(f"type5 mmsi parse error: {exc}")
    except Exception as exc:
        errors.append(f"pass1 stream error on {source}: {exc}")

    # Merge into persistent cache for multi-file runs
    vessel_type_cache.update(type5_data)

    # Pass 2: position messages
    try:
        for msg in FileReaderStream(source):
            try:
                decoded = msg.decode()
            except (UnknownMessageException, Exception):
                continue

            mt = decoded.msg_type
            if mt not in (1, 2, 3, 18, 21):
                continue

            try:
                mmsi = str(decoded.mmsi)

                if mt in (1, 2, 3):
                    lat = _safe_float(getattr(decoded, "lat", None))
                    lon = _safe_float(getattr(decoded, "lon", None))
                    speed = _safe_speed(getattr(decoded, "speed", None))
                    heading = _safe_heading(getattr(decoded, "heading", None))
                    vessel_type = vessel_type_cache.get(mmsi, "Class A")

                elif mt == 18:
                    lat = _safe_float(getattr(decoded, "lat", None))
                    lon = _safe_float(getattr(decoded, "lon", None))
                    speed = _safe_speed(getattr(decoded, "speed", None))
                    heading = _safe_heading(getattr(decoded, "heading", None))
                    vessel_type = vessel_type_cache.get(mmsi, "Class B")

                elif mt == 21:
                    lat = _safe_float(getattr(decoded, "lat", None))
                    lon = _safe_float(getattr(decoded, "lon", None))
                    speed = None
                    heading = None
                    vessel_type = "AtoN"

                else:
                    continue

                if lat is None or lon is None:
                    continue  # position not available — skip

                position_rows.append({
                    "mmsi": mmsi,
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "heading": heading,
                    "timestamp": _now_utc(),
                    "vessel_type": vessel_type,
                    "source_file": source,
                    "msg_type": mt,
                })

            except Exception as exc:
                errors.append(f"row parse error (type {mt}): {exc}")

    except Exception as exc:
        errors.append(f"pass2 stream error on {source}: {exc}")

    return position_rows, errors


def import_files(
    paths: list[Path],
    *,
    batch_size: int = 500,
) -> dict:
    """Parse NMEA files and insert tracks into sg_tracks.

    Returns a summary dict compatible with --json output.
    """
    if not PYAIS_AVAILABLE:
        return {
            "status": "error",
            "error": "pyais is not installed. Run: pip install pyais>=2.0",
        }

    if not paths:
        return {"status": "error", "error": "No NMEA files provided."}

    # Ensure schema is current (idempotent)
    init_db()

    vessel_type_cache: dict[str, str] = {}
    all_rows: list[dict] = []
    all_errors: list[str] = []
    files_processed = 0

    for path in paths:
        if not path.exists():
            all_errors.append(f"File not found: {path}")
            continue
        rows, errs = _parse_file(path, vessel_type_cache)
        all_rows.extend(rows)
        all_errors.extend(errs)
        files_processed += 1

    # Batch insert
    inserted = 0
    conn = get_connection()
    try:
        for i in range(0, len(all_rows), batch_size):
            batch = all_rows[i : i + batch_size]
            conn.executemany(
                """
                INSERT INTO sg_tracks
                    (mmsi, lat, lon, speed, heading, timestamp,
                     vessel_type, source_file, msg_type)
                VALUES
                    (:mmsi, :lat, :lon, :speed, :heading, :timestamp,
                     :vessel_type, :source_file, :msg_type)
                """,
                batch,
            )
            conn.commit()
            inserted += len(batch)
    finally:
        conn.close()

    # Invalidate ghost track predictions for every MMSI that just reported in.
    # Import lazily so the importer remains functional if ghost_track is absent.
    if all_rows:
        try:
            from apps.geosigint.ghost_track import invalidate_prediction  # noqa: E402
            for mmsi in {r["mmsi"] for r in all_rows}:
                invalidate_prediction(mmsi)
        except Exception:
            pass  # invalidation is best-effort; never block an import

    return {
        "status": "ok",
        "files_processed": files_processed,
        "tracks_inserted": inserted,
        "errors": all_errors,
        "vessel_types_seen": len(vessel_type_cache),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import NMEA AIS files into sg_tracks."
    )
    parser.add_argument(
        "--file",
        dest="files",
        metavar="PATH",
        action="append",
        default=[],
        help="NMEA file to import (repeatable).",
    )
    parser.add_argument(
        "--dir",
        dest="dirs",
        metavar="DIR",
        action="append",
        default=[],
        help="Directory of *.nmea / *.txt files (repeatable).",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Emit JSON summary to stdout.",
    )
    args = parser.parse_args()

    paths: list[Path] = [Path(f) for f in args.files]
    for d in args.dirs:
        dp = Path(d)
        paths.extend(dp.glob("*.nmea"))
        paths.extend(dp.glob("*.txt"))

    result = import_files(paths)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        if result["status"] == "ok":
            print(
                f"Imported {result['tracks_inserted']} tracks from "
                f"{result['files_processed']} file(s). "
                f"Vessel types resolved: {result['vessel_types_seen']}."
            )
            if result["errors"]:
                print(f"Warnings ({len(result['errors'])}):")
                for e in result["errors"][:10]:
                    print(f"  {e}")
        else:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
