#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Historical Pre-War Baselines Importer.

Seeds sg_war_readiness_events with 23 historical conflict onset cases.
Used as training data for Bayesian I&W models, DTW pattern matching,
and GBM Exercise vs Pre-War classifier.

Each case captures the composite I&W signal profile in the 90 days before
conflict onset (T-90 to T-0), enabling comparison against current theater signals.

Cases covered (23):
  Conventional: Korea 1950, Suez 1956, Six Day 1967, Yom Kippur 1973,
    Falklands 1982, Gulf War 1991, Kosovo 1999, Iraq 2003, Georgia 2008,
    Ukraine 2014, Libya 2011, Syria 2011, Ethiopia 2020, Ukraine 2022
  Amphibious: Inchon 1950, Normandy 1944 (historical), Falklands 1982,
    Taiwan Strait 1995 (near-miss)
  Nuclear: Cuba 1962 (near-miss), India-Pakistan 1999, North Korea 2017 signal
  Maritime: Gulf Tanker War 1984, Strait of Hormuz 2019

Usage:
  python -m tools.strategos.historical_baselines --seed --theater ukraine
  python -m tools.strategos.historical_baselines --seed --dry-run --output-json
  python -m tools.strategos.historical_baselines --list
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"

# ── Historical case registry ─────────────────────────────────────────────────

HISTORICAL_CASES: list[dict] = [
    {
        "case_id": "hb-korea-1950",
        "conflict_name": "Korean War Onset",
        "year": 1950,
        "start_date": "1950-06-25",
        "aggressor": "DPRK",
        "defender": "ROK",
        "region": "East Asia",
        "duration_days": 1128,
        "readiness_level": 8,
        "assessment": "Full conventional invasion; rapid force buildup along 38th parallel over 6 months pre-invasion",
        "indicators": {
            "military_buildup": 0.92,
            "logistics_surge": 0.88,
            "diplomatic_failure": 0.85,
            "economic_strain": 0.45,
            "info_warfare": 0.60,
            "cyber": 0.0,
            "nuclear_signaling": 0.10,
            "composite": 0.72,
        },
    },
    {
        "case_id": "hb-suez-1956",
        "conflict_name": "Suez Crisis",
        "year": 1956,
        "start_date": "1956-10-29",
        "aggressor": "Israel/UK/France",
        "defender": "Egypt",
        "region": "Middle East",
        "duration_days": 8,
        "readiness_level": 7,
        "assessment": "Coordinated surprise; extensive political-military coordination; deliberate deception operation",
        "indicators": {
            "military_buildup": 0.75,
            "logistics_surge": 0.70,
            "diplomatic_failure": 0.90,
            "economic_strain": 0.60,
            "info_warfare": 0.55,
            "cyber": 0.0,
            "nuclear_signaling": 0.05,
            "composite": 0.65,
        },
    },
    {
        "case_id": "hb-six-day-1967",
        "conflict_name": "Six-Day War",
        "year": 1967,
        "start_date": "1967-06-05",
        "aggressor": "Israel",
        "defender": "Egypt/Syria/Jordan",
        "region": "Middle East",
        "duration_days": 6,
        "readiness_level": 9,
        "assessment": "Preemptive air strike; 90-day crisis escalation; blockade casus belli; high military signal density",
        "indicators": {
            "military_buildup": 0.95,
            "logistics_surge": 0.90,
            "diplomatic_failure": 0.95,
            "economic_strain": 0.65,
            "info_warfare": 0.80,
            "cyber": 0.0,
            "nuclear_signaling": 0.20,
            "composite": 0.84,
        },
    },
    {
        "case_id": "hb-yom-kippur-1973",
        "conflict_name": "Yom Kippur War",
        "year": 1973,
        "start_date": "1973-10-06",
        "aggressor": "Egypt/Syria",
        "defender": "Israel",
        "region": "Middle East",
        "duration_days": 19,
        "readiness_level": 9,
        "assessment": "Strategic surprise despite indicators; MASKIROVKA effective; strongest pre-war signal pattern in ME",
        "indicators": {
            "military_buildup": 0.88,
            "logistics_surge": 0.92,
            "diplomatic_failure": 0.70,
            "economic_strain": 0.55,
            "info_warfare": 0.65,
            "cyber": 0.0,
            "nuclear_signaling": 0.30,
            "composite": 0.75,
        },
    },
    {
        "case_id": "hb-falklands-1982",
        "conflict_name": "Falklands War",
        "year": 1982,
        "start_date": "1982-04-02",
        "aggressor": "Argentina",
        "defender": "UK",
        "region": "South Atlantic",
        "duration_days": 74,
        "readiness_level": 7,
        "assessment": "Amphibious surprise; domestic political pressure on junta; short I&W window (2-3 weeks)",
        "indicators": {
            "military_buildup": 0.70,
            "logistics_surge": 0.75,
            "diplomatic_failure": 0.80,
            "economic_strain": 0.85,
            "info_warfare": 0.50,
            "cyber": 0.0,
            "nuclear_signaling": 0.0,
            "composite": 0.68,
        },
    },
    {
        "case_id": "hb-cuba-1962",
        "conflict_name": "Cuban Missile Crisis (near-miss)",
        "year": 1962,
        "start_date": "1962-10-16",
        "aggressor": "USSR",
        "defender": "USA",
        "region": "Caribbean",
        "duration_days": 13,
        "readiness_level": 10,
        "assessment": "Nuclear near-miss; DEFCON 2; full strategic nuclear signaling; blockade vs invasion COA",
        "indicators": {
            "military_buildup": 0.95,
            "logistics_surge": 0.80,
            "diplomatic_failure": 0.90,
            "economic_strain": 0.40,
            "info_warfare": 0.70,
            "cyber": 0.0,
            "nuclear_signaling": 0.98,
            "composite": 0.88,
        },
    },
    {
        "case_id": "hb-gulf-1991",
        "conflict_name": "Gulf War (Desert Storm)",
        "year": 1991,
        "start_date": "1991-01-17",
        "aggressor": "Coalition",
        "defender": "Iraq",
        "region": "Middle East",
        "duration_days": 43,
        "readiness_level": 8,
        "assessment": "Largest coalition buildup since WWII; 6-month Desert Shield buildup; clear I&W pattern",
        "indicators": {
            "military_buildup": 1.0,
            "logistics_surge": 1.0,
            "diplomatic_failure": 0.95,
            "economic_strain": 0.70,
            "info_warfare": 0.75,
            "cyber": 0.05,
            "nuclear_signaling": 0.15,
            "composite": 0.86,
        },
    },
    {
        "case_id": "hb-kosovo-1999",
        "conflict_name": "Kosovo War (NATO intervention)",
        "year": 1999,
        "start_date": "1999-03-24",
        "aggressor": "NATO",
        "defender": "Serbia",
        "region": "Europe",
        "duration_days": 78,
        "readiness_level": 7,
        "assessment": "Air campaign; failed Rambouillet diplomacy; first major info-war / hacker conflict",
        "indicators": {
            "military_buildup": 0.75,
            "logistics_surge": 0.70,
            "diplomatic_failure": 0.98,
            "economic_strain": 0.65,
            "info_warfare": 0.70,
            "cyber": 0.35,
            "nuclear_signaling": 0.05,
            "composite": 0.72,
        },
    },
    {
        "case_id": "hb-iraq-2003",
        "conflict_name": "Iraq War (OIF)",
        "year": 2003,
        "start_date": "2003-03-20",
        "aggressor": "USA/Coalition",
        "defender": "Iraq",
        "region": "Middle East",
        "duration_days": 21,
        "readiness_level": 8,
        "assessment": "Deliberate invasion; 9-month buildup; UNSCR diplomacy; ISR-heavy",
        "indicators": {
            "military_buildup": 0.98,
            "logistics_surge": 0.95,
            "diplomatic_failure": 0.90,
            "economic_strain": 0.60,
            "info_warfare": 0.80,
            "cyber": 0.30,
            "nuclear_signaling": 0.20,
            "composite": 0.82,
        },
    },
    {
        "case_id": "hb-georgia-2008",
        "conflict_name": "Russo-Georgian War",
        "year": 2008,
        "start_date": "2008-08-07",
        "aggressor": "Russia",
        "defender": "Georgia",
        "region": "Caucasus",
        "duration_days": 5,
        "readiness_level": 8,
        "assessment": "First hybrid war; cyber pre-attack; proxy provocation; rapid conventional escalation",
        "indicators": {
            "military_buildup": 0.80,
            "logistics_surge": 0.75,
            "diplomatic_failure": 0.85,
            "economic_strain": 0.50,
            "info_warfare": 0.85,
            "cyber": 0.75,
            "nuclear_signaling": 0.05,
            "composite": 0.79,
        },
    },
    {
        "case_id": "hb-ukraine-2014",
        "conflict_name": "Crimea Annexation / Donbas War",
        "year": 2014,
        "start_date": "2014-02-27",
        "aggressor": "Russia",
        "defender": "Ukraine",
        "region": "Eastern Europe",
        "duration_days": 21,
        "readiness_level": 8,
        "assessment": "Hybrid warfare template: little green men, proxy forces, energy coercion, info-war, cyber",
        "indicators": {
            "military_buildup": 0.75,
            "logistics_surge": 0.65,
            "diplomatic_failure": 0.80,
            "economic_strain": 0.75,
            "info_warfare": 0.95,
            "cyber": 0.80,
            "nuclear_signaling": 0.15,
            "composite": 0.81,
        },
    },
    {
        "case_id": "hb-ukraine-2022",
        "conflict_name": "Russia Full Invasion of Ukraine",
        "year": 2022,
        "start_date": "2022-02-24",
        "aggressor": "Russia",
        "defender": "Ukraine",
        "region": "Eastern Europe",
        "duration_days": 0,
        "readiness_level": 9,
        "assessment": "3-month buildup; exercise maskirovka; full-spectrum: cyber, info, economic, conventional, nuclear signaling",
        "indicators": {
            "military_buildup": 0.97,
            "logistics_surge": 0.95,
            "diplomatic_failure": 0.95,
            "economic_strain": 0.80,
            "info_warfare": 0.90,
            "cyber": 0.90,
            "nuclear_signaling": 0.40,
            "composite": 0.93,
        },
    },
    {
        "case_id": "hb-syria-2011",
        "conflict_name": "Syrian Civil War Onset",
        "year": 2011,
        "start_date": "2011-03-15",
        "aggressor": "Assad Government",
        "defender": "Opposition",
        "region": "Middle East",
        "duration_days": 0,
        "readiness_level": 6,
        "assessment": "Arab Spring escalation; internal conflict; foreign intervention signals; chemical weapon use",
        "indicators": {
            "military_buildup": 0.60,
            "logistics_surge": 0.55,
            "diplomatic_failure": 0.70,
            "economic_strain": 0.85,
            "info_warfare": 0.80,
            "cyber": 0.40,
            "nuclear_signaling": 0.10,
            "composite": 0.67,
        },
    },
    {
        "case_id": "hb-libya-2011",
        "conflict_name": "Libyan Civil War / NATO Intervention",
        "year": 2011,
        "start_date": "2011-02-15",
        "aggressor": "NATO/Opposition",
        "defender": "Gaddafi",
        "region": "North Africa",
        "duration_days": 250,
        "readiness_level": 7,
        "assessment": "R2P intervention; fast escalation from protest to armed conflict; air superiority campaign",
        "indicators": {
            "military_buildup": 0.70,
            "logistics_surge": 0.65,
            "diplomatic_failure": 0.80,
            "economic_strain": 0.65,
            "info_warfare": 0.75,
            "cyber": 0.25,
            "nuclear_signaling": 0.0,
            "composite": 0.69,
        },
    },
    {
        "case_id": "hb-ethiopia-2020",
        "conflict_name": "Tigray War",
        "year": 2020,
        "start_date": "2020-11-04",
        "aggressor": "Ethiopia/Eritrea",
        "defender": "TPLF",
        "region": "East Africa",
        "duration_days": 726,
        "readiness_level": 7,
        "assessment": "Drone warfare; Eritrean allied entry; strategic surprise; Internet blackout as I&W indicator",
        "indicators": {
            "military_buildup": 0.72,
            "logistics_surge": 0.68,
            "diplomatic_failure": 0.75,
            "economic_strain": 0.70,
            "info_warfare": 0.80,
            "cyber": 0.50,
            "nuclear_signaling": 0.0,
            "composite": 0.71,
        },
    },
    {
        "case_id": "hb-taiwan-strait-1995",
        "conflict_name": "Taiwan Strait Crisis 1995-96 (near-miss)",
        "year": 1995,
        "start_date": "1995-07-21",
        "aggressor": "China",
        "defender": "Taiwan/USA",
        "region": "Indo-Pacific",
        "duration_days": 210,
        "readiness_level": 8,
        "assessment": "Missile exercises; carrier battle group response; A2/AD early indicators; election-cycle pressure",
        "indicators": {
            "military_buildup": 0.85,
            "logistics_surge": 0.80,
            "diplomatic_failure": 0.85,
            "economic_strain": 0.55,
            "info_warfare": 0.65,
            "cyber": 0.20,
            "nuclear_signaling": 0.35,
            "composite": 0.79,
        },
    },
    {
        "case_id": "hb-india-pakistan-1999",
        "conflict_name": "Kargil War",
        "year": 1999,
        "start_date": "1999-05-03",
        "aggressor": "Pakistan",
        "defender": "India",
        "region": "South Asia",
        "duration_days": 88,
        "readiness_level": 9,
        "assessment": "Nuclear-armed neighbors; Lahore summit as deception; infiltration-based seizure",
        "indicators": {
            "military_buildup": 0.78,
            "logistics_surge": 0.80,
            "diplomatic_failure": 0.70,
            "economic_strain": 0.55,
            "info_warfare": 0.65,
            "cyber": 0.15,
            "nuclear_signaling": 0.75,
            "composite": 0.77,
        },
    },
    {
        "case_id": "hb-gulf-tanker-1984",
        "conflict_name": "Tanker War (Gulf War phase)",
        "year": 1984,
        "start_date": "1984-05-01",
        "aggressor": "Iraq/Iran",
        "defender": "Neutral shipping",
        "region": "Persian Gulf",
        "duration_days": 1460,
        "readiness_level": 6,
        "assessment": "Maritime economic warfare; AIS precursor; flag state targeting; Strait of Hormuz chokepoint stress",
        "indicators": {
            "military_buildup": 0.65,
            "logistics_surge": 0.55,
            "diplomatic_failure": 0.75,
            "economic_strain": 0.90,
            "info_warfare": 0.50,
            "cyber": 0.0,
            "nuclear_signaling": 0.0,
            "composite": 0.63,
        },
    },
    {
        "case_id": "hb-strait-of-hormuz-2019",
        "conflict_name": "Strait of Hormuz Tanker Seizures",
        "year": 2019,
        "start_date": "2019-06-20",
        "aggressor": "Iran",
        "defender": "UK/USA",
        "region": "Persian Gulf",
        "duration_days": 90,
        "readiness_level": 6,
        "assessment": "Grey-zone maritime coercion; drone shoot-down; tanker seizures; JCPOA withdrawal escalation",
        "indicators": {
            "military_buildup": 0.60,
            "logistics_surge": 0.55,
            "diplomatic_failure": 0.85,
            "economic_strain": 0.80,
            "info_warfare": 0.75,
            "cyber": 0.65,
            "nuclear_signaling": 0.20,
            "composite": 0.71,
        },
    },
    {
        "case_id": "hb-normandy-1944",
        "conflict_name": "D-Day / Operation Overlord (historical)",
        "year": 1944,
        "start_date": "1944-06-06",
        "aggressor": "Allied",
        "defender": "Germany",
        "region": "Western Europe",
        "duration_days": 90,
        "readiness_level": 10,
        "assessment": "Largest amphibious operation in history; FORTITUDE deception; maximum logistical signal",
        "indicators": {
            "military_buildup": 1.0,
            "logistics_surge": 1.0,
            "diplomatic_failure": 1.0,
            "economic_strain": 0.95,
            "info_warfare": 0.85,
            "cyber": 0.0,
            "nuclear_signaling": 0.0,
            "composite": 0.91,
        },
    },
    {
        "case_id": "hb-nk-nuclear-2017",
        "conflict_name": "North Korea ICBM / Nuclear Signal (near-miss)",
        "year": 2017,
        "start_date": "2017-07-04",
        "aggressor": "DPRK",
        "defender": "USA/ROK",
        "region": "East Asia",
        "duration_days": 180,
        "readiness_level": 8,
        "assessment": "H-bomb test + ICBM tests; DEFCON threat language; carrier surge; fire and fury rhetoric",
        "indicators": {
            "military_buildup": 0.80,
            "logistics_surge": 0.70,
            "diplomatic_failure": 0.90,
            "economic_strain": 0.65,
            "info_warfare": 0.85,
            "cyber": 0.70,
            "nuclear_signaling": 0.95,
            "composite": 0.85,
        },
    },
    {
        "case_id": "hb-inchon-1950",
        "conflict_name": "Inchon Amphibious Landing",
        "year": 1950,
        "start_date": "1950-09-15",
        "aggressor": "USA/UN",
        "defender": "DPRK",
        "region": "East Asia",
        "duration_days": 14,
        "readiness_level": 8,
        "assessment": "MacArthur's strategic surprise; complex tidal conditions; deception and rapid buildup",
        "indicators": {
            "military_buildup": 0.90,
            "logistics_surge": 0.92,
            "diplomatic_failure": 1.0,
            "economic_strain": 0.60,
            "info_warfare": 0.55,
            "cyber": 0.0,
            "nuclear_signaling": 0.10,
            "composite": 0.78,
        },
    },
    {
        "case_id": "hb-azerbaijan-2020",
        "conflict_name": "Nagorno-Karabakh War (Second)",
        "year": 2020,
        "start_date": "2020-09-27",
        "aggressor": "Azerbaijan",
        "defender": "Armenia",
        "region": "South Caucasus",
        "duration_days": 44,
        "readiness_level": 7,
        "assessment": "Drone-centric warfare; Bayraktar TB2 debut; Turkish ISR support; info-war / social media battle",
        "indicators": {
            "military_buildup": 0.82,
            "logistics_surge": 0.78,
            "diplomatic_failure": 0.80,
            "economic_strain": 0.65,
            "info_warfare": 0.90,
            "cyber": 0.55,
            "nuclear_signaling": 0.0,
            "composite": 0.78,
        },
    },
]


# ── Writer ────────────────────────────────────────────────────────────────────

def _get_conn(sqlite_path: Path):
    try:
        from tools.db.storage import get_connection
        return get_connection(), False
    except Exception:
        import sqlite3
        return sqlite3.connect(str(sqlite_path)), True


def seed_baselines(theater_id: str = "ukraine", dry_run: bool = False) -> dict:
    """Insert all historical baseline cases into sg_war_readiness_events."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for case in HISTORICAL_CASES:
        rows.append({
            "id": str(uuid.uuid4()),
            "theater_id": theater_id,
            "case_id": case["case_id"],
            "label": "historical_baseline",
            "conflict_name": case["conflict_name"],
            "year": case["year"],
            "start_date": case["start_date"],
            "aggressor": case["aggressor"],
            "defender": case["defender"],
            "region": case["region"],
            "duration_days": case["duration_days"],
            "readiness_level": case["readiness_level"],
            "assessment": case["assessment"],
            "indicators_json": json.dumps(case["indicators"]),
            "metadata_json": json.dumps({"type": "historical_baseline", "case_id": case["case_id"]}),
            "source": "strategos_historical_db",
            "event_ts": case["start_date"] + "T00:00:00+00:00",
            "created_at": now,
        })

    if dry_run:
        return {"seeded": len(rows), "dry_run": True, "cases": [r["case_id"] for r in rows]}

    conn, is_sqlite = _get_conn(_DB_PATH)
    ph = "%s" if not is_sqlite else "?"
    inserted = skipped = 0

    try:
        for row in rows:
            try:
                conn.execute(
                    f"INSERT INTO sg_war_readiness_events "
                    f"(id, theater_id, case_id, label, conflict_name, year, start_date, "
                    f"aggressor, defender, region, duration_days, readiness_level, "
                    f"assessment, indicators_json, metadata_json, source, event_ts, created_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph},{ph},"
                    f"{ph},{ph},{ph},{ph},{ph},{ph}) ON CONFLICT DO NOTHING",
                    (
                        row["id"], row["theater_id"], row["case_id"], row["label"],
                        row["conflict_name"], row["year"], row["start_date"],
                        row["aggressor"], row["defender"], row["region"],
                        row["duration_days"], row["readiness_level"],
                        row["assessment"], row["indicators_json"], row["metadata_json"],
                        row["source"], row["event_ts"], row["created_at"],
                    ),
                )
                inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
    finally:
        conn.close()

    return {"seeded": inserted, "skipped": skipped, "total": len(rows)}


def list_cases() -> None:
    print(f"{'Case ID':<30} {'Year':<6} {'Composite':>9}  Conflict Name")
    print("-" * 80)
    for case in sorted(HISTORICAL_CASES, key=lambda x: x["year"]):
        comp = case["indicators"]["composite"]
        print(f"{case['case_id']:<30} {case['year']:<6} {comp:>9.2f}  {case['conflict_name']}")


def get_case(case_id: str) -> dict | None:
    for case in HISTORICAL_CASES:
        if case["case_id"] == case_id:
            return case
    return None


def get_pattern_matrix() -> list[dict]:
    """Return all cases as a DTW-ready feature matrix."""
    features = ["military_buildup", "logistics_surge", "diplomatic_failure",
                "economic_strain", "info_warfare", "cyber", "nuclear_signaling"]
    return [
        {
            "case_id": c["case_id"],
            "conflict_name": c["conflict_name"],
            "year": c["year"],
            "readiness_level": c["readiness_level"],
            "vector": [c["indicators"][f] for f in features],
            "composite": c["indicators"]["composite"],
        }
        for c in HISTORICAL_CASES
    ]


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Historical pre-war baselines seeder")
    ap.add_argument("--seed", action="store_true", help="Seed baselines into DB")
    ap.add_argument("--list", action="store_true", help="List all cases")
    ap.add_argument("--theater", default="ukraine", help="Theater ID (default: ukraine)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_cases()
        return

    if args.seed:
        result = seed_baselines(args.theater, dry_run=args.dry_run)
        if args.output_json:
            print(json.dumps(result, indent=2))
        else:
            dr = " (dry-run)" if args.dry_run else ""
            print(f"[historical_baselines] seeded {result.get('seeded', 0)}/{result.get('total', len(HISTORICAL_CASES))} cases{dr}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
