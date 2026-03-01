# CUI // SP-CTI
# ICDEV GovCon Award Tracker — Phase 59 (D367)
# Tracks award notices from SAM.gov and builds competitor database.

"""
Award Tracker — poll SAM.gov for award notices, extract vendor data.

Reads from:
    - SAM.gov Opportunities API (award notice type 'a')
    - args/govcon_config.yaml (polling config)

Writes to:
    - govcon_awards (append-only)
    - creative_competitors (cross-ref with source='sam_gov')

Usage:
    python tools/govcon/award_tracker.py --scan --json
    python tools/govcon/award_tracker.py --list --json
    python tools/govcon/award_tracker.py --list --vendor "Booz Allen" --json
    python tools/govcon/award_tracker.py --list --naics 541512 --json
    python tools/govcon/award_tracker.py --stats --json
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _audit(conn, action, details="", actor="award_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.award_tracking", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _load_config():
    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _safe_get(url, params=None, headers=None, timeout=30):
    """Safe HTTP GET with error handling."""
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


# ── award scanning ────────────────────────────────────────────────────

def scan_awards():
    """Poll SAM.gov for award notices.

    Uses award notice types from config (default: 'a' = Award Notice).
    Extracts vendor name, contract value, NAICS, agency.
    """
    import os
    import time

    cfg = _load_config()
    sam_cfg = cfg.get("sam_gov", {})
    award_cfg = cfg.get("award_tracking", {})

    api_key = os.environ.get(sam_cfg.get("api_key_env", "SAM_GOV_API_KEY"), "")
    if not api_key:
        return {"status": "error", "message": "SAM_GOV_API_KEY not set in environment"}

    api_url = sam_cfg.get("api_url", "https://api.sam.gov/opportunities/v2/search")
    lookback_days = award_cfg.get("lookback_days", 90)
    notice_types = sam_cfg.get("award_notice_types", ["a"])
    naics_codes = sam_cfg.get("naics_codes", [])
    delay = sam_cfg.get("rate_limit", {}).get("delay_between_requests", 0.15)

    posted_from = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
    posted_to = datetime.now(timezone.utc).strftime("%m/%d/%Y")

    conn = _get_db()
    new_awards = 0
    total_fetched = 0

    for notice_type in notice_types:
        params = {
            "api_key": api_key,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "ptype": notice_type,
            "limit": sam_cfg.get("max_per_poll", 100),
        }
        if naics_codes:
            params["ncode"] = ",".join(str(n) for n in naics_codes)

        data = _safe_get(api_url, params=params)
        if "error" in data:
            continue

        opportunities = data.get("opportunitiesData", [])
        total_fetched += len(opportunities)

        for opp in opportunities:
            award = _extract_award_data(opp)
            if not award:
                continue

            # Dedup by content hash
            chash = _content_hash(f"{award['solicitation_number']}|{award['awardee_name']}|{award['award_amount']}")
            existing = conn.execute(
                "SELECT id FROM govcon_awards WHERE content_hash = ?", (chash,)
            ).fetchone()
            if existing:
                continue

            award_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO govcon_awards "
                "(id, sam_opportunity_id, solicitation_number, title, agency, "
                "awardee_name, awardee_duns, awardee_cage, "
                "award_amount, award_date, naics_code, set_aside_type, "
                "contract_type, period_of_performance, "
                "description, content_hash, "
                "metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    award_id,
                    award.get("notice_id", ""),
                    award.get("solicitation_number", ""),
                    award.get("title", ""),
                    award.get("agency", ""),
                    award.get("awardee_name", ""),
                    award.get("awardee_duns", ""),
                    award.get("awardee_cage", ""),
                    award.get("award_amount", 0),
                    award.get("award_date", ""),
                    award.get("naics_code", ""),
                    award.get("set_aside_type", ""),
                    award.get("contract_type", ""),
                    award.get("period_of_performance", ""),
                    (award.get("description", "") or "")[:5000],
                    chash,
                    json.dumps(award.get("metadata", {})),
                    _now(),
                ),
            )
            new_awards += 1

            # Cross-register to creative_competitors
            if award_cfg.get("auto_create_competitor", True):
                _register_competitor(conn, award)

        time.sleep(delay)

    _audit(conn, "scan_awards", f"Fetched {total_fetched}, new awards: {new_awards}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "total_fetched": total_fetched,
        "new_awards": new_awards,
        "notice_types": notice_types,
        "lookback_days": lookback_days,
    }


def _extract_award_data(opp):
    """Extract award-relevant data from SAM.gov opportunity."""
    award = opp.get("award", {}) or {}
    awardee = award.get("awardee", {}) or {}

    # Only process if there's an awardee
    awardee_name = awardee.get("name", "") or ""
    if not awardee_name:
        # Try extracting from description
        desc = opp.get("description", "") or ""
        # Common pattern: "Award to: Company Name"
        match = re.search(r"(?:award(?:ed)?\s+to|contractor|vendor)[:\s]+([A-Z][A-Za-z\s&.,]+)", desc)
        if match:
            awardee_name = match.group(1).strip()

    if not awardee_name:
        return None

    return {
        "notice_id": opp.get("noticeId", ""),
        "solicitation_number": opp.get("solicitationNumber", ""),
        "title": opp.get("title", ""),
        "agency": opp.get("fullParentPathName", "") or opp.get("department", ""),
        "awardee_name": awardee_name,
        "awardee_duns": awardee.get("ueiSAM", "") or awardee.get("duns", ""),
        "awardee_cage": awardee.get("cageCode", ""),
        "award_amount": award.get("amount", 0) or 0,
        "award_date": award.get("date", "") or opp.get("postedDate", ""),
        "naics_code": opp.get("naicsCode", ""),
        "set_aside_type": opp.get("typeOfSetAside", ""),
        "contract_type": opp.get("archiveType", ""),
        "period_of_performance": "",
        "description": (opp.get("description", "") or "")[:5000],
        "metadata": {
            "notice_type": opp.get("type", ""),
            "office": opp.get("officeAddress", {}).get("city", ""),
        },
    }


def _register_competitor(conn, award):
    """Cross-register awardee to creative_competitors table."""
    name = award.get("awardee_name", "")
    if not name:
        return

    existing = conn.execute(
        "SELECT id FROM creative_competitors WHERE name = ?", (name,)
    ).fetchone()

    if existing:
        return  # Already tracked

    try:
        conn.execute(
            "INSERT INTO creative_competitors "
            "(id, name, domain, source, description, website, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                name,
                "govcon",
                "sam_gov",
                f"Discovered from SAM.gov award: {award.get('title', '')[:200]}",
                "",
                "discovered",
                _now(), _now(),
            ),
        )
    except Exception:
        pass


# ── listing and querying ──────────────────────────────────────────────

def list_awards(vendor=None, naics=None, agency=None, limit=50):
    """List tracked awards with optional filters."""
    conn = _get_db()

    query = "SELECT * FROM govcon_awards WHERE 1=1"
    params = []
    if vendor:
        query += " AND awardee_name LIKE ?"
        params.append(f"%{vendor}%")
    if naics:
        query += " AND naics_code = ?"
        params.append(str(naics))
    if agency:
        query += " AND agency LIKE ?"
        params.append(f"%{agency}%")
    query += " ORDER BY award_date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "status": "ok",
        "total": len(rows),
        "awards": [dict(r) for r in rows],
    }


def get_stats():
    """Get aggregate award statistics."""
    conn = _get_db()

    # Total awards and value
    totals = conn.execute("""
        SELECT
            COUNT(*) as total_awards,
            SUM(CAST(award_amount AS REAL)) as total_value,
            COUNT(DISTINCT awardee_name) as unique_vendors,
            COUNT(DISTINCT naics_code) as unique_naics,
            COUNT(DISTINCT agency) as unique_agencies
        FROM govcon_awards
    """).fetchone()

    # Top vendors by count
    top_vendors = conn.execute("""
        SELECT awardee_name, COUNT(*) as award_count,
               SUM(CAST(award_amount AS REAL)) as total_value
        FROM govcon_awards
        GROUP BY awardee_name
        ORDER BY award_count DESC
        LIMIT 15
    """).fetchall()

    # Top NAICS codes
    top_naics = conn.execute("""
        SELECT naics_code, COUNT(*) as count,
               SUM(CAST(award_amount AS REAL)) as total_value
        FROM govcon_awards
        WHERE naics_code != ''
        GROUP BY naics_code
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return {
        "status": "ok",
        "totals": dict(totals) if totals else {},
        "top_vendors": [dict(v) for v in top_vendors],
        "top_naics": [dict(n) for n in top_naics],
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Award Tracker (D367)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Scan SAM.gov for award notices")
    group.add_argument("--list", action="store_true", help="List tracked awards")
    group.add_argument("--stats", action="store_true", help="Award statistics")

    parser.add_argument("--vendor", help="Filter by vendor name")
    parser.add_argument("--naics", help="Filter by NAICS code")
    parser.add_argument("--agency", help="Filter by agency")
    parser.add_argument("--limit", type=int, default=50, help="Result limit")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.scan:
        result = scan_awards()
    elif args.list:
        result = list_awards(vendor=args.vendor, naics=args.naics, agency=args.agency, limit=args.limit)
    elif args.stats:
        result = get_stats()

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
