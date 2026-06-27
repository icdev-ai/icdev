#!/usr/bin/env python3
# CUI // SP-CTI
"""Load 23 labeled historical baseline cases into sg_war_readiness_events.

Cases: 9 pre_war, 7 exercise, 7 coercive.
Source: tools/sg/data/baselines.json

The importer extends the existing sg_war_readiness_events table with
baseline-specific columns (label, case_id, conflict_name, etc.) if they
are not already present, then upserts all 23 cases.

Usage:
  python tools/sg/baseline_importer.py
  python tools/sg/baseline_importer.py --dry-run
  python tools/sg/baseline_importer.py --json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

BASELINES_PATH = Path(__file__).parent / "data" / "baselines.json"
VALID_LABELS = {"pre_war", "exercise", "coercive"}

_BASELINE_COLUMNS = [
    ("case_id",       "TEXT"),
    ("label",         "TEXT"),
    ("conflict_name", "TEXT"),
    ("year",          "INTEGER"),
    ("start_date",    "TEXT"),
    ("aggressor",     "TEXT"),
    ("defender",      "TEXT"),
    ("region",        "TEXT"),
    ("duration_days", "INTEGER"),
    ("metadata_json", "TEXT"),
    ("source",        "TEXT"),
]


def _ensure_columns(conn) -> None:
    """Idempotently add baseline columns to the existing table."""
    for col, col_type in _BASELINE_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE sg_war_readiness_events ADD COLUMN {col} {col_type}"
            )
        except Exception:
            pass  # column already exists
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_sg_wre_label ON sg_war_readiness_events(label)",
        "CREATE INDEX IF NOT EXISTS idx_sg_wre_case_id ON sg_war_readiness_events(case_id)",
    ):
        try:
            conn.execute(idx_sql)
        except Exception:
            pass
    conn.commit()


def _load_cases() -> list[dict]:
    with open(BASELINES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _upsert_case(conn, case: dict) -> str:
    """Insert or update a baseline case. Returns 'inserted' or 'updated'."""
    label = case["label"]
    if label not in VALID_LABELS:
        raise ValueError(f"Invalid label '{label}' for case {case['case_id']}")

    indicators = case.get("indicators", {})
    indicators_json = json.dumps(indicators)
    metadata_json = json.dumps(case.get("metadata", {}))
    readiness_level = indicators.get("mobilization_level", 0)
    now_ts = datetime.now(timezone.utc).isoformat()

    row = conn.execute(
        "SELECT id FROM sg_war_readiness_events WHERE case_id = %s",
        (case["case_id"],),
    ).fetchone()

    if row:
        conn.execute(
            """UPDATE sg_war_readiness_events SET
               label=%s, conflict_name=%s, year=%s, start_date=%s,
               aggressor=%s, defender=%s, region=%s, duration_days=%s,
               indicators_json=%s, metadata_json=%s, source='baseline_importer',
               readiness_level=%s, assessment=%s
               WHERE case_id=%s""",
            (
                label,
                case["conflict_name"],
                case["year"],
                case.get("start_date"),
                case.get("aggressor"),
                case.get("defender"),
                case.get("region"),
                case.get("duration_days"),
                indicators_json,
                metadata_json,
                readiness_level,
                label,
                case["case_id"],
            ),
        )
        return "updated"
    else:
        conn.execute(
            """INSERT INTO sg_war_readiness_events
               (id, case_id, label, conflict_name, year, start_date,
                aggressor, defender, region, duration_days,
                indicators_json, metadata_json, source,
                readiness_level, assessment, event_ts, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'baseline_importer',%s,%s,%s,%s)""",
            (
                case["case_id"],
                case["case_id"],
                label,
                case["conflict_name"],
                case["year"],
                case.get("start_date"),
                case.get("aggressor"),
                case.get("defender"),
                case.get("region"),
                case.get("duration_days"),
                indicators_json,
                metadata_json,
                readiness_level,
                label,
                case.get("start_date") or now_ts,
                now_ts,
            ),
        )
        return "inserted"


def run(dry_run: bool = False, as_json: bool = False) -> dict:
    cases = _load_cases()
    total = len(cases)
    label_counts: dict[str, int] = {"pre_war": 0, "exercise": 0, "coercive": 0}

    if dry_run:
        results = []
        for case in cases:
            lbl = case["label"]
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
            results.append({"case_id": case["case_id"], "label": lbl, "action": "dry_run"})
        summary = {"dry_run": True, "total": total, "by_label": label_counts, "cases": results}
        if as_json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"[DRY RUN] Would import {total} cases: {label_counts}")
        return summary

    conn = get_connection()
    _ensure_columns(conn)

    inserted = updated = failed = 0
    errors: list[dict] = []

    for case in cases:
        try:
            action = _upsert_case(conn, case)
            lbl = case["label"]
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            failed += 1
            errors.append({"case_id": case.get("case_id", "?"), "error": str(exc)})

    conn.commit()
    conn.close()

    summary = {
        "dry_run": False,
        "total": total,
        "inserted": inserted,
        "updated": updated,
        "failed": failed,
        "by_label": label_counts,
        "errors": errors,
    }

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"Imported {total} baseline cases — "
            f"{inserted} inserted, {updated} updated, {failed} failed"
        )
        print(
            f"  pre_war={label_counts['pre_war']}  "
            f"exercise={label_counts['exercise']}  "
            f"coercive={label_counts['coercive']}"
        )
        if errors:
            for e in errors:
                print(f"  ERROR {e['case_id']}: {e['error']}", file=sys.stderr)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import historical war readiness baseline cases")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run, as_json=args.as_json)
    if result.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
