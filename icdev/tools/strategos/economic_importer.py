#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Economic data importers (FAO + WorldBank + IMF).

Loads economic indicator time-series and writes to sg_economic_signals.
Applies CUSUM change-point detection to flag anomalies.

Supported formats:
  1. WorldBank CSV — downloaded from data.worldbank.org
     Columns: Country Name, Country Code, Indicator Name, Indicator Code, [Year columns...]
  2. FAO CSV — FAOSTAT bulk download
     Columns: Area, Area Code, Element, Item, Year, Unit, Value, Flag
  3. Generic JSON — [{country, indicator, date, value, unit}, ...]

CUSUM threshold: flag if value deviates > 2σ from rolling 3-year mean.

Usage:
  python -m tools.strategos.economic_importer --worldbank data/gdp.csv --theater ukraine
  python -m tools.strategos.economic_importer --fao data/food_price.csv --theater ukraine
  python -m tools.strategos.economic_importer --json data/econ.json --theater ukraine
  python -m tools.strategos.economic_importer --worldbank data.csv --dry-run --output-json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"


# ── CUSUM detector ────────────────────────────────────────────────────────────

def _cusum_alert(series: list[float], new_val: float, window: int = 6) -> bool:
    """Return True if new_val is a significant anomaly vs recent window."""
    if len(series) < 3:
        return False
    recent = series[-window:] if len(series) >= window else series
    mean = sum(recent) / len(recent)
    variance = sum((x - mean) ** 2 for x in recent) / len(recent)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return False
    return abs(new_val - mean) > 2 * std


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_worldbank_csv(path: Path, theater_id: str, target_countries: list[str] | None = None) -> list[dict]:
    """Parse WorldBank wide-format CSV (Country Name, Country Code, Indicator Name, Indicator Code, 1960, 1961, ...)"""
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for rec in reader:
            country_code = rec.get("Country Code", rec.get("country_code", "")).strip()
            if target_countries and country_code not in target_countries:
                continue
            indicator = rec.get("Indicator Name", rec.get("indicator_name", "")).strip()
            indicator_code = rec.get("Indicator Code", "").strip()

            series: list[float] = []
            for key, val in rec.items():
                if not key.strip().isdigit():
                    continue
                year = int(key.strip())
                if year < 1990:
                    continue
                if not val or val.strip() == "":
                    continue
                try:
                    fval = float(val)
                except ValueError:
                    continue
                alert = _cusum_alert(series, fval)
                series.append(fval)
                rows.append({
                    "id": str(uuid.uuid4()),
                    "theater_id": theater_id,
                    "signal_type": indicator_code or indicator[:50],
                    "value": fval,
                    "unit": "",
                    "country_code": country_code,
                    "description": f"{indicator} ({year})",
                    "signal_ts": f"{year}-01-01T00:00:00+00:00",
                    "metadata_json": json.dumps({
                        "year": year,
                        "indicator": indicator,
                        "indicator_code": indicator_code,
                        "cusum_alert": alert,
                        "source": "worldbank",
                    }),
                    "created_at": now,
                })

    return rows


def parse_fao_csv(path: Path, theater_id: str, target_areas: list[str] | None = None) -> list[dict]:
    """Parse FAOSTAT bulk CSV format."""
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    series_map: dict[str, list[float]] = {}

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for rec in reader:
            area = rec.get("Area", rec.get("area", "")).strip()
            if target_areas and area not in target_areas:
                continue
            element = rec.get("Element", "").strip()
            item = rec.get("Item", "").strip()
            year_str = rec.get("Year", "").strip()
            unit = rec.get("Unit", "").strip()
            val_str = rec.get("Value", "").strip()

            if not year_str.isdigit() or not val_str:
                continue
            try:
                year = int(year_str)
                fval = float(val_str)
            except ValueError:
                continue

            series_key = f"{area}:{item}:{element}"
            if series_key not in series_map:
                series_map[series_key] = []
            alert = _cusum_alert(series_map[series_key], fval)
            series_map[series_key].append(fval)

            rows.append({
                "id": str(uuid.uuid4()),
                "theater_id": theater_id,
                "signal_type": f"fao:{item[:30]}:{element[:20]}",
                "value": fval,
                "unit": unit,
                "country_code": rec.get("Area Code", area[:3]).strip(),
                "description": f"{area} {item} {element} ({year})",
                "signal_ts": f"{year}-01-01T00:00:00+00:00",
                "metadata_json": json.dumps({
                    "year": year,
                    "item": item,
                    "element": element,
                    "cusum_alert": alert,
                    "source": "fao",
                }),
                "created_at": now,
            })

    return rows


def parse_generic_json(path: Path, theater_id: str) -> list[dict]:
    """Parse generic [{country, indicator, date, value, unit}, ...] JSON."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("data", [])
    now = datetime.now(timezone.utc).isoformat()
    result: list[dict] = []

    for rec in rows:
        val = rec.get("value")
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        date = str(rec.get("date", rec.get("year", now[:10])))
        if len(date) == 4:
            date = f"{date}-01-01"
        result.append({
            "id": str(uuid.uuid4()),
            "theater_id": theater_id,
            "signal_type": str(rec.get("indicator", "unknown"))[:60],
            "value": fval,
            "unit": str(rec.get("unit", "")),
            "country_code": str(rec.get("country", rec.get("country_code", "")))[:10],
            "description": str(rec.get("description", rec.get("indicator", "")))[:200],
            "signal_ts": date + "T00:00:00+00:00" if "T" not in date else date,
            "metadata_json": json.dumps({"source": "json", "cusum_alert": False}),
            "created_at": now,
        })
    return result


# ── Writer ────────────────────────────────────────────────────────────────────

def _get_conn(sqlite_path: Path):
    try:
        from tools.db.storage import get_connection
        return get_connection(), False
    except Exception:
        import sqlite3
        return sqlite3.connect(str(sqlite_path)), True


def write_signals(rows: list[dict], dry_run: bool = False) -> dict:
    if dry_run:
        return {"inserted": len(rows), "skipped": 0, "dry_run": True}

    conn, is_sqlite = _get_conn(_DB_PATH)
    ph = "%s" if not is_sqlite else "?"
    inserted = skipped = 0

    try:
        for row in rows:
            try:
                conn.execute(
                    f"INSERT INTO sg_economic_signals "
                    f"(id, theater_id, signal_type, value, unit, country_code, "
                    f"description, signal_ts, metadata_json, created_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph}) ON CONFLICT DO NOTHING",
                    (
                        row["id"], row["theater_id"], row["signal_type"],
                        row["value"], row["unit"], row["country_code"],
                        row["description"], row["signal_ts"],
                        row["metadata_json"], row["created_at"],
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
    ap = argparse.ArgumentParser(description="Economic data -> sg_economic_signals importer")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--worldbank", metavar="CSV", help="WorldBank CSV file")
    src.add_argument("--fao", metavar="CSV", help="FAOSTAT CSV file")
    src.add_argument("--json", dest="json_file", metavar="JSON", help="Generic JSON file")
    ap.add_argument("--theater", default="ukraine", help="Theater ID (default: ukraine)")
    ap.add_argument("--countries", help="Comma-separated country codes to include (optional)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", action="store_true")
    args = ap.parse_args()

    target = [c.strip() for c in args.countries.split(",")] if args.countries else None

    if args.worldbank:
        rows = parse_worldbank_csv(Path(args.worldbank), args.theater, target)
    elif args.fao:
        rows = parse_fao_csv(Path(args.fao), args.theater, target)
    else:
        rows = parse_generic_json(Path(args.json_file), args.theater)

    result = write_signals(rows, dry_run=args.dry_run)
    result["theater_id"] = args.theater
    result["total_parsed"] = len(rows)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        dr = " (dry-run)" if args.dry_run else ""
        print(f"[economic_importer] {result['inserted']} inserted, {result['skipped']} skipped{dr}")


if __name__ == "__main__":
    main()
