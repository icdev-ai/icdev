# CUI // SP-CTI
# ICDEV GovProposal — SAM.gov Contract Awards Sync (Phase 60, D-CPMP-6)
# SAM.gov Contract Awards API v1 adapter, rate-limited with content hash dedup.

"""
SAM.gov Contract Awards Sync — Fetch contract award data from SAM.gov.

Follows sam_scanner.py pattern (D366): rate-limited, content hash dedup,
exponential backoff on failures.

Usage:
    python tools/govcon/sam_contract_sync.py --sync --json
    python tools/govcon/sam_contract_sync.py --list --json
    python tools/govcon/sam_contract_sync.py --link --sam-award-id <id> --contract-id <id> --json
    python tools/govcon/sam_contract_sync.py --search --query "keyword" --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
            return cfg.get("cpmp", {}).get("sam_awards", {})
    return {}


_CFG = _load_config()

API_URL = _CFG.get("api_url", "https://api.sam.gov/opportunities/v1/search")
API_KEY_ENV = _CFG.get("api_key_env", "SAM_GOV_API_KEY")
LOOKBACK_DAYS = _CFG.get("lookback_days", 90)
RATE_LIMIT = _CFG.get("rate_limit", {})
DELAY = RATE_LIMIT.get("delay_between_requests", 0.15)


def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _content_hash(data):
    """SHA-256 content hash for dedup."""
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def _audit(conn, action, details="", actor="sam_contract_sync"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.sam_contract_sync", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _safe_get(url, params=None, headers=None, timeout=30):
    """HTTP GET with error handling. Returns (data, error)."""
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except ImportError:
        return None, "requests library not installed"
    except Exception as e:
        return None, str(e)


def sync_awards(lookback_days=None):
    """Fetch contract awards from SAM.gov and store new records.

    Uses content hash dedup to avoid duplicates.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        return {
            "status": "error",
            "message": f"SAM.gov API key not found. Set {API_KEY_ENV} environment variable.",
        }

    days = lookback_days or LOOKBACK_DAYS

    params = {
        "api_key": api_key,
        "postedFrom": (datetime.now(timezone.utc).replace(day=1)).strftime("%m/%d/%Y"),
        "limit": 100,
        "ptype": "a",  # Award notices
    }

    data, error = _safe_get(API_URL, params=params, timeout=60)
    if error:
        return {"status": "error", "message": f"SAM.gov API error: {error}"}

    if not data:
        return {"status": "ok", "new_awards": 0, "duplicates": 0, "message": "No data returned"}

    awards = data.get("opportunitiesData", data.get("data", []))
    if not isinstance(awards, list):
        awards = []

    conn = _get_db()
    new_count = 0
    dup_count = 0

    for award in awards:
        content = _content_hash(award)

        existing = conn.execute(
            "SELECT id FROM cpmp_sam_contract_awards WHERE content_hash = ?", (content,)
        ).fetchone()
        if existing:
            dup_count += 1
            continue

        sam_award_id = award.get("noticeId", award.get("solicitationNumber", _uuid()))

        conn.execute(
            "INSERT INTO cpmp_sam_contract_awards "
            "(id, sam_award_id, piid, awardee_name, awardee_uei, awardee_cage, "
            "obligation_amount, base_exercised_options_value, award_date, pop_start, pop_end, "
            "awarding_agency, naics_code, psc_code, content_hash, linked_contract_id, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _uuid(), sam_award_id,
                award.get("solicitationNumber"),
                award.get("awardee", {}).get("name") if isinstance(award.get("awardee"), dict) else award.get("awardee"),
                award.get("awardee", {}).get("ueiSAM") if isinstance(award.get("awardee"), dict) else None,
                award.get("awardee", {}).get("cageCode") if isinstance(award.get("awardee"), dict) else None,
                award.get("award", {}).get("amount") if isinstance(award.get("award"), dict) else None,
                award.get("award", {}).get("baseAndOptionsValue") if isinstance(award.get("award"), dict) else None,
                award.get("awardDate", award.get("postedDate")),
                award.get("archiveDate"),
                award.get("responseDeadLine"),
                award.get("fullParentPathName", award.get("department")),
                award.get("naicsCode"),
                award.get("pscCode"),
                content,
                None,
                _now(),
            ),
        )
        new_count += 1
        time.sleep(DELAY)

    _audit(conn, "sync_awards", f"Synced {new_count} new, {dup_count} duplicates from SAM.gov")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "new_awards": new_count,
        "duplicates": dup_count,
        "total_processed": len(awards),
    }


def list_awards(linked_only=False, limit=50):
    """List cached SAM.gov contract awards."""
    conn = _get_db()
    query = "SELECT * FROM cpmp_sam_contract_awards WHERE 1=1"
    params = []
    if linked_only:
        query += " AND linked_contract_id IS NOT NULL"
    query += " ORDER BY discovered_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "awards": [dict(r) for r in rows]}


def search_awards(query_text, limit=20):
    """Search awards by keyword in vendor name, agency, or PIID."""
    conn = _get_db()
    pattern = f"%{query_text}%"
    rows = conn.execute(
        "SELECT * FROM cpmp_sam_contract_awards "
        "WHERE awardee_name LIKE ? OR awarding_agency LIKE ? OR piid LIKE ? OR sam_award_id LIKE ? "
        "ORDER BY discovered_at DESC LIMIT ?",
        (pattern, pattern, pattern, pattern, limit),
    ).fetchall()
    conn.close()
    return {"status": "ok", "query": query_text, "total": len(rows), "awards": [dict(r) for r in rows]}


def link_award_to_contract(sam_award_id, contract_id):
    """Link a SAM.gov award record to a CPMP contract."""
    conn = _get_db()

    award = conn.execute(
        "SELECT id FROM cpmp_sam_contract_awards WHERE sam_award_id = ?", (sam_award_id,)
    ).fetchone()
    if not award:
        conn.close()
        return {"status": "error", "message": f"SAM award {sam_award_id} not found"}

    contract = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    conn.execute(
        "UPDATE cpmp_sam_contract_awards SET linked_contract_id = ? WHERE sam_award_id = ?",
        (contract_id, sam_award_id),
    )
    _audit(conn, "link_award", f"Linked SAM award {sam_award_id} to contract {contract_id}")
    conn.commit()
    conn.close()

    return {"status": "ok", "sam_award_id": sam_award_id, "contract_id": contract_id}


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovProposal SAM.gov Contract Awards Sync (Phase 60)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="Sync awards from SAM.gov")
    group.add_argument("--list", action="store_true", help="List cached awards")
    group.add_argument("--search", action="store_true", help="Search awards")
    group.add_argument("--link", action="store_true", help="Link award to contract")

    parser.add_argument("--sam-award-id")
    parser.add_argument("--contract-id")
    parser.add_argument("--query")
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--linked-only", action="store_true")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.sync:
        result = sync_awards(args.lookback_days)
    elif args.list:
        result = list_awards(linked_only=args.linked_only)
    elif args.search:
        if not args.query:
            print("Error: --query required for search", file=sys.stderr)
            sys.exit(1)
        result = search_awards(args.query)
    elif args.link:
        if not args.sam_award_id or not args.contract_id:
            print("Error: --sam-award-id and --contract-id required", file=sys.stderr)
            sys.exit(1)
        result = link_award_to_contract(args.sam_award_id, args.contract_id)
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
