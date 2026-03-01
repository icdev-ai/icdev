# CUI // SP-CTI
# ICDEV GovCon Competitor Profiler — Phase 59 (D367)
# Aggregate competitor intelligence from award data.

"""
Competitor Profiler — build competitive intelligence from SAM.gov awards.

Aggregates:
    - Total awards and contract value per vendor
    - Common agencies and NAICS codes
    - Win rate estimates by domain
    - Leaderboard rankings

Usage:
    python tools/govcon/competitor_profiler.py --profile --vendor "Booz Allen" --json
    python tools/govcon/competitor_profiler.py --leaderboard --json
    python tools/govcon/competitor_profiler.py --leaderboard --naics 541512 --json
    python tools/govcon/competitor_profiler.py --compare --vendors "Booz Allen,Deloitte" --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── profiling ─────────────────────────────────────────────────────────

def profile_vendor(vendor_name):
    """Build comprehensive profile for a vendor."""
    conn = _get_db()

    # Aggregate awards
    awards = conn.execute(
        "SELECT * FROM govcon_awards WHERE awardee_name LIKE ? ORDER BY award_date DESC",
        (f"%{vendor_name}%",),
    ).fetchall()

    if not awards:
        conn.close()
        return {"status": "ok", "vendor": vendor_name, "message": "No awards found"}

    total_value = sum(float(a["award_amount"] or 0) for a in awards)

    # Agency breakdown
    agencies = {}
    for a in awards:
        agency = a["agency"] or "Unknown"
        if agency not in agencies:
            agencies[agency] = {"count": 0, "value": 0}
        agencies[agency]["count"] += 1
        agencies[agency]["value"] += float(a["award_amount"] or 0)

    # NAICS breakdown
    naics = {}
    for a in awards:
        code = a["naics_code"] or "Unknown"
        if code not in naics:
            naics[code] = {"count": 0, "value": 0}
        naics[code]["count"] += 1
        naics[code]["value"] += float(a["award_amount"] or 0)

    # Set-aside breakdown
    set_asides = {}
    for a in awards:
        sa = a["set_aside_type"] or "Full & Open"
        if sa not in set_asides:
            set_asides[sa] = 0
        set_asides[sa] += 1

    # Recent awards
    recent = [dict(a) for a in awards[:10]]

    conn.close()

    return {
        "status": "ok",
        "vendor": vendor_name,
        "total_awards": len(awards),
        "total_value": total_value,
        "avg_award_value": total_value / len(awards) if awards else 0,
        "agencies": dict(sorted(agencies.items(), key=lambda x: x[1]["count"], reverse=True)),
        "naics": dict(sorted(naics.items(), key=lambda x: x[1]["count"], reverse=True)),
        "set_asides": set_asides,
        "recent_awards": recent,
    }


def get_leaderboard(naics=None, agency=None, limit=20):
    """Generate vendor leaderboard ranked by award count."""
    conn = _get_db()

    query = """
        SELECT
            awardee_name,
            COUNT(*) as award_count,
            SUM(CAST(award_amount AS REAL)) as total_value,
            COUNT(DISTINCT naics_code) as naics_diversity,
            COUNT(DISTINCT agency) as agency_diversity,
            MIN(award_date) as first_award,
            MAX(award_date) as latest_award
        FROM govcon_awards
        WHERE 1=1
    """
    params = []
    if naics:
        query += " AND naics_code = ?"
        params.append(str(naics))
    if agency:
        query += " AND agency LIKE ?"
        params.append(f"%{agency}%")

    query += " GROUP BY awardee_name ORDER BY award_count DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    leaderboard = []
    for i, row in enumerate(rows, 1):
        leaderboard.append({
            "rank": i,
            "vendor": row["awardee_name"],
            "awards": row["award_count"],
            "total_value": row["total_value"],
            "avg_value": row["total_value"] / row["award_count"] if row["award_count"] > 0 else 0,
            "naics_diversity": row["naics_diversity"],
            "agency_diversity": row["agency_diversity"],
            "first_award": row["first_award"],
            "latest_award": row["latest_award"],
        })

    return {
        "status": "ok",
        "filters": {"naics": naics, "agency": agency},
        "leaderboard": leaderboard,
    }


def compare_vendors(vendor_names):
    """Side-by-side comparison of multiple vendors."""
    profiles = []
    for name in vendor_names:
        p = profile_vendor(name.strip())
        profiles.append(p)

    return {
        "status": "ok",
        "vendors_compared": len(profiles),
        "profiles": profiles,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Competitor Profiler (D367)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile", action="store_true", help="Profile a vendor")
    group.add_argument("--leaderboard", action="store_true", help="Vendor leaderboard")
    group.add_argument("--compare", action="store_true", help="Compare vendors")

    parser.add_argument("--vendor", help="Vendor name for --profile")
    parser.add_argument("--vendors", help="Comma-separated vendor names for --compare")
    parser.add_argument("--naics", help="Filter by NAICS code")
    parser.add_argument("--agency", help="Filter by agency")
    parser.add_argument("--limit", type=int, default=20, help="Leaderboard limit")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.profile:
        if not args.vendor:
            print("Error: --vendor required", file=sys.stderr)
            sys.exit(1)
        result = profile_vendor(args.vendor)
    elif args.leaderboard:
        result = get_leaderboard(naics=args.naics, agency=args.agency, limit=args.limit)
    elif args.compare:
        if not args.vendors:
            print("Error: --vendors required (comma-separated)", file=sys.stderr)
            sys.exit(1)
        result = compare_vendors(args.vendors.split(","))

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
