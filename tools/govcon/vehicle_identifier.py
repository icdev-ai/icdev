#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Procurement Vehicle Identifier — match initiatives to viable vehicles and flag gaps.

Catalogs known federal procurement vehicles (GWACs, IDIQs, BPAs, GSA MAS,
sole-source bridges, open market) and matches them to initiatives based
on agency, NAICS, scope, dollar ceiling, and set-aside eligibility.
Initiatives with no viable vehicle are flagged with a recommended path
(open market RFP, sole-source justification, or program cancel).

Deterministic, air-gap safe (no LLM).

DB tables (write/read): pgv_procurement_vehicles, pgv_vehicle_match,
                        pgv_initiative_viability, audit_trail

Usage:
    python tools/govcon/vehicle_identifier.py --seed --json
    python tools/govcon/vehicle_identifier.py --list-vehicles --json
    python tools/govcon/vehicle_identifier.py --match-initiative \\
        --initiative-code INIT-2026-CLD \\
        --title "Cloud Modernization Phase 2" \\
        --agency DoD --naics 541512 --ceiling 1500000 --json
    python tools/govcon/vehicle_identifier.py --list-matches --json
    python tools/govcon/vehicle_identifier.py --flag-unviable --json
    python tools/govcon/vehicle_identifier.py --summary --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.common.helpers import row_to_dict  # noqa: E402


# ---------------------------------------------------------------------------
# Constants — vehicle catalog and matching rules
# ---------------------------------------------------------------------------

# Vehicle types
VTYPE_GWAC = "gwac"
VTYPE_IDIQ = "idiq"
VTYPE_BPA = "bpa"
VTYPE_GSA_SCHED = "gsa_sched"
VTYPE_SOLE_SOURCE = "sole_source"
VTYPE_OPEN_MARKET = "open_market"
VTYPE_BRIDGE = "bridge"

VALID_VEHICLE_TYPES = {
    VTYPE_GWAC, VTYPE_IDIQ, VTYPE_BPA, VTYPE_GSA_SCHED,
    VTYPE_SOLE_SOURCE, VTYPE_OPEN_MARKET, VTYPE_BRIDGE,
}

# Default vehicle ceiling for open market is considered unlimited; for
# others we store max_ceiling_usd. 0 means unknown / agency-defined.
DEFAULT_VEHICLES: List[Dict[str, Any]] = [
    # Government-wide Acquisition Contracts (GWACs)
    {
        "vehicle_name": "OASIS+",
        "vehicle_type": VTYPE_GWAC,
        "agency": "GSA",
        "contract_holders": "Multiple (Pool 1/2/3/4/5/6)",
        "domains": "Management & Advisory, Technical & Engineering, Scientific, Logistics, Environmental",
        "naics_prefixes": "541512,541511,541330,541715,541690,541990,562910",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": True,
        "notes": "Best-in-class GWAC; covers most ICDEV services",
    },
    {
        "vehicle_name": "Polaris",
        "vehicle_type": VTYPE_GWAC,
        "agency": "GSA",
        "contract_holders": "Small business pool (HUBZone, SDVOSB, WOSB, etc.)",
        "domains": "IT Services",
        "naics_prefixes": "541511,541512,541519,541715",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": True,
        "notes": "Small business set-aside only",
    },
    {
        "vehicle_name": "CIO-SP4",
        "vehicle_type": VTYPE_GWAC,
        "agency": "NIH NITAAC",
        "contract_holders": "Multiple (HUBZone, SDVOSB, WOSB, unrestricted)",
        "domains": "IT Services, Cybersecurity, Cloud, Health IT",
        "naics_prefixes": "541511,541512,541519,541715,621111",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": True,
        "notes": "Best for federal civilian / Health IT",
    },
    {
        "vehicle_name": "VETS-2",
        "vehicle_type": VTYPE_IDIQ,
        "agency": "VA",
        "contract_holders": "SDVOSB holders",
        "domains": "IT Services, Custom Software, Cybersecurity",
        "naics_prefixes": "541511,541512,541519,541715",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": True,
        "notes": "Service-disabled veteran-owned set-aside",
    },
    # GSA Schedule
    {
        "vehicle_name": "GSA MAS IT-70",
        "vehicle_type": VTYPE_GSA_SCHED,
        "agency": "GSA",
        "contract_holders": "Multiple (any size)",
        "domains": "IT Professional Services, Cloud, Cyber, Software",
        "naics_prefixes": "541511,541512,541519,541715,511210,541330",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": False,
        "notes": "Most flexible; broadly available",
    },
    # BPA placeholder (typically agency-specific)
    {
        "vehicle_name": "Agency BPA",
        "vehicle_type": VTYPE_BPA,
        "agency": "Any",
        "contract_holders": "Established under existing contract",
        "domains": "Variable",
        "naics_prefixes": "",  # anything
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": False,
        "fair_opportunity_required": False,
        "notes": "Bypass full-and-open if BPA already exists for the scope",
    },
    # Sole-source (justification required)
    {
        "vehicle_name": "Sole-Source (FAR 6.302)",
        "vehicle_type": VTYPE_SOLE_SOURCE,
        "agency": "Any",
        "contract_holders": "Brand-name or follow-on only",
        "domains": "Any",
        "naics_prefixes": "",  # any
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": False,
        "fair_opportunity_required": False,
        "notes": "Requires J&A per FAR 6.303; brand-name justification or follow-on rationale",
    },
    # Bridge contract (interim, time-limited)
    {
        "vehicle_name": "Bridge Contract (FAR 16.104)",
        "vehicle_type": VTYPE_BRIDGE,
        "agency": "Any",
        "contract_holders": "Incumbent only",
        "domains": "Existing scope only",
        "naics_prefixes": "",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": False,
        "fair_opportunity_required": False,
        "notes": "Up to 6 months, justified for continuity of service",
    },
    # Open market fallback
    {
        "vehicle_name": "Open Market RFP",
        "vehicle_type": VTYPE_OPEN_MARKET,
        "agency": "Any",
        "contract_holders": "Any offeror",
        "domains": "Any",
        "naics_prefixes": "",
        "min_ceiling_usd": 0.0,
        "max_ceiling_usd": 0.0,
        "set_aside_eligible": True,
        "fair_opportunity_required": True,
        "notes": "Default fallback; longer lead time",
    },
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_VEHICLES_DDL = """
CREATE TABLE IF NOT EXISTS pgv_procurement_vehicles (
    id TEXT PRIMARY KEY,
    vehicle_name TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    agency TEXT NOT NULL DEFAULT '',
    contract_holders TEXT NOT NULL DEFAULT '',
    domains TEXT NOT NULL DEFAULT '',
    naics_prefixes TEXT NOT NULL DEFAULT '',
    min_ceiling_usd REAL NOT NULL DEFAULT 0.0,
    max_ceiling_usd REAL NOT NULL DEFAULT 0.0,
    set_aside_eligible INTEGER NOT NULL DEFAULT 0,
    fair_opportunity_required INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
)
"""

CREATE_MATCHES_DDL = """
CREATE TABLE IF NOT EXISTS pgv_vehicle_match (
    id TEXT PRIMARY KEY,
    initiative_code TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    viability_score REAL NOT NULL DEFAULT 0.0,
    rationale TEXT NOT NULL DEFAULT '',
    recommended INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(initiative_code, vehicle_id),
    FOREIGN KEY (vehicle_id) REFERENCES pgv_procurement_vehicles(id)
)
"""

CREATE_VIABILITY_DDL = """
CREATE TABLE IF NOT EXISTS pgv_initiative_viability (
    id TEXT PRIMARY KEY,
    initiative_code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    naics TEXT NOT NULL DEFAULT '',
    ceiling_usd REAL NOT NULL DEFAULT 0.0,
    viable INTEGER NOT NULL DEFAULT 0,
    recommended_vehicle TEXT NOT NULL DEFAULT '',
    recommended_path TEXT NOT NULL DEFAULT '',
    flagged_reason TEXT NOT NULL DEFAULT '',
    evaluated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pgv_vehicles_vehicle_type ON pgv_procurement_vehicles(vehicle_type)",
    "CREATE INDEX IF NOT EXISTS idx_pgv_vehicles_status ON pgv_procurement_vehicles(status)",
    "CREATE INDEX IF NOT EXISTS idx_pgv_match_initiative ON pgv_vehicle_match(initiative_code)",
    "CREATE INDEX IF NOT EXISTS idx_pgv_match_recommended ON pgv_vehicle_match(recommended)",
    "CREATE INDEX IF NOT EXISTS idx_pgv_viability_viable ON pgv_initiative_viability(viable)",
]


def ensure_tables(conn) -> None:
    """Idempotent schema bootstrap."""
    conn.execute(CREATE_VEHICLES_DDL)
    conn.execute(CREATE_MATCHES_DDL)
    conn.execute(CREATE_VIABILITY_DDL)
    for idx in CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    conn = get_connection()
    ensure_tables(conn)
    return conn


def _row_to_dict(row) -> Dict[str, Any]:
    d = row_to_dict(row)
    # Normalize booleans stored as integers
    for k in ("set_aside_eligible", "fair_opportunity_required",
              "recommended", "viable"):
        if k in d and d[k] is not None:
            d[k] = bool(int(d[k])) if isinstance(d[k], (int, float)) else bool(d[k])
    return d


def _naics_matches(initiative_naics: str, vehicle_prefixes: str) -> bool:
    """True if initiative's NAICS exactly matches one of the vehicle's accepted NAICS codes.

    A vehicle declares the full 6-digit NAICS codes it covers (comma-separated).
    Open/blank vehicle prefixes mean the vehicle is the catch-all fallback —
    but only when the initiative's NAICS is also unknown (e.g. capture phase
    before NAICS is assigned).
    """
    if not vehicle_prefixes.strip():
        # Open vehicle: matches ONLY when initiative NAICS is also unspecified.
        return not initiative_naics.strip()
    if not initiative_naics.strip():
        return False  # no NAICS declared → prefix-restricted vehicles can't match
    prefixes = {p.strip() for p in vehicle_prefixes.split(",") if p.strip()}
    return initiative_naics.strip() in prefixes


# ---------------------------------------------------------------------------
# Catalog management
# ---------------------------------------------------------------------------

def seed_default_vehicles() -> Dict[str, Any]:
    """Insert default vehicle catalog (idempotent via UNIQUE on vehicle_name)."""
    conn = _get_conn()
    inserted = 0
    skipped = 0
    for v in DEFAULT_VEHICLES:
        existing = conn.execute(
            "SELECT id FROM pgv_procurement_vehicles WHERE vehicle_name = ?",
            (v["vehicle_name"],),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        vid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO pgv_procurement_vehicles
               (id, vehicle_name, vehicle_type, agency, contract_holders,
                domains, naics_prefixes, min_ceiling_usd, max_ceiling_usd,
                set_aside_eligible, fair_opportunity_required, notes,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (vid, v["vehicle_name"], v["vehicle_type"], v["agency"],
             v["contract_holders"], v["domains"], v["naics_prefixes"],
             v["min_ceiling_usd"], v["max_ceiling_usd"],
             1 if v["set_aside_eligible"] else 0,
             1 if v["fair_opportunity_required"] else 0,
             v["notes"], _now(), _now()),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_catalog": len(DEFAULT_VEHICLES),
    }


def list_vehicles(vehicle_type: Optional[str] = None,
                  active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM pgv_procurement_vehicles WHERE 1=1"
    params: List[Any] = []
    if vehicle_type:
        sql += " AND vehicle_type = ?"
        params.append(vehicle_type)
    if active_only:
        sql += " AND status = 'active'"
    sql += " ORDER BY vehicle_type, vehicle_name"
    conn = _get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def _score_vehicle_for_initiative(
    vehicle: Dict[str, Any],
    naics: str,
    ceiling_usd: float,
) -> float:
    """Score 0-100: how well does this vehicle fit the initiative?
    100 = perfect fit, 0 = no fit.
    """
    score = 0.0

    # NAICS match (40 pts)
    if _naics_matches(naics, vehicle["naics_prefixes"]):
        score += 40.0
    else:
        return 0.0  # hard fail: NAICS is the most selective filter

    # Vehicle type preference (30 pts — GWACs/IDIQs > Schedules > Sole-source > Open)
    type_weight = {
        VTYPE_GWAC: 30.0,
        VTYPE_IDIQ: 28.0,
        VTYPE_BPA: 25.0,
        VTYPE_GSA_SCHED: 22.0,
        VTYPE_BRIDGE: 15.0,
        VTYPE_SOLE_SOURCE: 10.0,
        VTYPE_OPEN_MARKET: 8.0,
    }
    score += type_weight.get(vehicle["vehicle_type"], 5.0)

    # Ceiling fit (20 pts) — 0 means unlimited/open
    if vehicle["max_ceiling_usd"] == 0.0:
        score += 20.0
    elif ceiling_usd <= vehicle["max_ceiling_usd"]:
        score += 20.0
    else:
        score += 5.0  # over ceiling

    # Set-aside flexibility (10 pts — bonus for being broadly available)
    if vehicle["set_aside_eligible"]:
        score += 10.0
    elif vehicle["vehicle_type"] in (VTYPE_SOLE_SOURCE, VTYPE_BRIDGE, VTYPE_BPA):
        score += 5.0

    return min(score, 100.0)


def match_initiative(
    initiative_code: str,
    title: str = "",
    agency: str = "",
    naics: str = "",
    ceiling_usd: float = 0.0,
) -> Dict[str, Any]:
    """Score all vehicles for the given initiative and persist the top matches.

    Returns the recommended path (top vehicle) and a viability flag.
    """
    if not initiative_code.strip():
        raise ValueError("initiative_code is required")

    vehicles = list_vehicles(active_only=True)
    scored: List[Dict[str, Any]] = []
    for v in vehicles:
        s = _score_vehicle_for_initiative(v, naics, ceiling_usd)
        if s <= 0.0:
            continue
        scored.append({
            "vehicle_id": v["id"],
            "vehicle_name": v["vehicle_name"],
            "vehicle_type": v["vehicle_type"],
            "score": s,
            "rationale": _build_rationale(v, naics, ceiling_usd),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Persist matches
    conn = _get_conn()
    # Clear prior matches for this initiative
    conn.execute("DELETE FROM pgv_vehicle_match WHERE initiative_code = ?",
                 (initiative_code,))
    for idx, m in enumerate(scored):
        mid = str(uuid.uuid4())
        recommended = 1 if idx == 0 else 0
        conn.execute(
            """INSERT INTO pgv_vehicle_match
               (id, initiative_code, vehicle_id, viability_score,
                rationale, recommended, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, initiative_code, m["vehicle_id"], m["score"],
             m["rationale"], recommended, _now()),
        )
    conn.commit()
    conn.close()

    # Determine viability
    if scored:
        top = scored[0]
        viable = True
        recommended_vehicle = top["vehicle_name"]
        recommended_path = _build_path_recommendation(top, agency, ceiling_usd)
        flagged_reason = ""
    else:
        viable = False
        recommended_vehicle = ""
        recommended_path = _build_fallback_path(naics, ceiling_usd, agency)
        flagged_reason = "No catalog vehicle matches initiative NAICS or scope"

    # Persist viability assessment
    _upsert_viability(
        initiative_code=initiative_code,
        title=title,
        agency=agency,
        naics=naics,
        ceiling_usd=ceiling_usd,
        viable=viable,
        recommended_vehicle=recommended_vehicle,
        recommended_path=recommended_path,
        flagged_reason=flagged_reason,
    )

    return {
        "initiative_code": initiative_code,
        "title": title,
        "agency": agency,
        "naics": naics,
        "ceiling_usd": ceiling_usd,
        "viable": viable,
        "recommended_vehicle": recommended_vehicle,
        "recommended_path": recommended_path,
        "flagged_reason": flagged_reason,
        "top_matches": scored[:5],
        "total_matches": len(scored),
    }


def _build_rationale(vehicle: Dict[str, Any], naics: str, ceiling_usd: float) -> str:
    parts = [f"NAICS {naics or 'unspecified'} matches {vehicle['vehicle_name']}"]
    if vehicle["max_ceiling_usd"] > 0:
        if ceiling_usd <= vehicle["max_ceiling_usd"]:
            parts.append(f"ceiling ${ceiling_usd:,.0f} within ${vehicle['max_ceiling_usd']:,.0f}")
        else:
            parts.append(f"ceiling ${ceiling_usd:,.0f} exceeds ${vehicle['max_ceiling_usd']:,.0f}")
    if vehicle["fair_opportunity_required"]:
        parts.append("fair opportunity per FAR 16.505 required")
    if vehicle["set_aside_eligible"]:
        parts.append("set-aside eligible")
    return "; ".join(parts)


def _build_path_recommendation(top: Dict[str, Any], agency: str, ceiling_usd: float) -> str:
    vtype = top["vehicle_type"]
    if vtype == VTYPE_GWAC:
        return (f"Issue fair-opportunity task order against {top['vehicle_name']} "
                f"to eligible contract holders; attach required compliance docs")
    if vtype == VTYPE_IDIQ:
        return (f"Issue task order call against {top['vehicle_name']} "
                f"under existing IDIQ pool")
    if vtype == VTYPE_BPA:
        return "Place call against existing BPA scope (verify scope match)"
    if vtype == VTYPE_GSA_SCHED:
        return "Issue RFQ to GSA MAS IT-70 contract holders"
    if vtype == VTYPE_SOLE_SOURCE:
        return "Draft J&A per FAR 6.303; obtain approval authority signature"
    if vtype == VTYPE_BRIDGE:
        return "Issue bridge (max 6 months) per FAR 16.104; concurrently compete follow-on"
    if vtype == VTYPE_OPEN_MARKET:
        return f"Issue full-and-open solicitation for {agency or 'agency'}"
    return "Manual review required"


def _build_fallback_path(naics: str, ceiling_usd: float, agency: str) -> str:
    if not naics:
        return ("No NAICS code declared — assign NAICS, then re-run. "
                "Failing that, prepare open-market RFP")
    if ceiling_usd > 7_000_000:
        return ("No standing vehicle covers scope at this dollar level. "
                "Recommend open-market RFP or seek CAS-aligned bridge to existing contract")
    return (f"No standing vehicle covers NAICS {naics}. "
            "Recommend open-market RFP or hybrid (e.g. GSA MAS RFQ if available)")


def _upsert_viability(
    initiative_code: str,
    title: str,
    agency: str,
    naics: str,
    ceiling_usd: float,
    viable: bool,
    recommended_vehicle: str,
    recommended_path: str,
    flagged_reason: str,
) -> None:
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id FROM pgv_initiative_viability WHERE initiative_code = ?",
        (initiative_code,),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE pgv_initiative_viability
               SET title=?, agency=?, naics=?, ceiling_usd=?, viable=?,
                   recommended_vehicle=?, recommended_path=?, flagged_reason=?,
                   evaluated_at=?
               WHERE initiative_code=?""",
            (title, agency, naics, ceiling_usd, 1 if viable else 0,
             recommended_vehicle, recommended_path, flagged_reason,
             _now(), initiative_code),
        )
    else:
        conn.execute(
            """INSERT INTO pgv_initiative_viability
               (id, initiative_code, title, agency, naics, ceiling_usd,
                viable, recommended_vehicle, recommended_path, flagged_reason, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), initiative_code, title, agency, naics, ceiling_usd,
             1 if viable else 0, recommended_vehicle, recommended_path,
             flagged_reason, _now()),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Read / report
# ---------------------------------------------------------------------------

def list_matches(initiative_code: Optional[str] = None,
                 recommended_only: bool = False) -> List[Dict[str, Any]]:
    sql = """SELECT m.*, v.vehicle_name, v.vehicle_type, v.agency
             FROM pgv_vehicle_match m
             JOIN pgv_procurement_vehicles v ON v.id = m.vehicle_id
             WHERE 1=1"""
    params: List[Any] = []
    if initiative_code:
        sql += " AND m.initiative_code = ?"
        params.append(initiative_code)
    if recommended_only:
        sql += " AND m.recommended = 1"
    sql += " ORDER BY m.initiative_code, m.viability_score DESC"
    conn = _get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def flag_unviable_initiatives() -> List[Dict[str, Any]]:
    """Return all initiatives that have no viable vehicle match."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM pgv_initiative_viability
           WHERE viable = 0
           ORDER BY evaluated_at DESC"""
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def list_viability() -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM pgv_initiative_viability ORDER BY viable ASC, evaluated_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_viability_summary() -> Dict[str, Any]:
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM pgv_initiative_viability").fetchone()["n"]
    viable = conn.execute(
        "SELECT COUNT(*) AS n FROM pgv_initiative_viability WHERE viable = 1"
    ).fetchone()["n"]
    flagged = total - viable
    vehicles = conn.execute(
        "SELECT COUNT(*) AS n FROM pgv_procurement_vehicles WHERE status = 'active'"
    ).fetchone()["n"]
    matches = conn.execute(
        "SELECT COUNT(*) AS n FROM pgv_vehicle_match"
    ).fetchone()["n"]
    conn.close()
    return {
        "initiatives_total": total,
        "initiatives_viable": viable,
        "initiatives_flagged": flagged,
        "flag_pct": (flagged / total * 100.0) if total > 0 else 0.0,
        "vehicles_cataloged": vehicles,
        "matches_persisted": matches,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Procurement Vehicle Identifier — match initiatives to vehicles",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--seed", action="store_true", help="Seed default vehicle catalog")
    p.add_argument("--list-vehicles", action="store_true", help="List catalog vehicles")
    p.add_argument("--vehicle-type", choices=sorted(VALID_VEHICLE_TYPES),
                   help="Filter by vehicle type")
    p.add_argument("--match-initiative", action="store_true",
                   help="Score all vehicles for an initiative")
    p.add_argument("--initiative-code", help="Initiative code (e.g. INIT-2026-CLD)")
    p.add_argument("--title", default="", help="Initiative title")
    p.add_argument("--agency", default="", help="Agency (DoD, GSA, ...)")
    p.add_argument("--naics", default="", help="NAICS code (e.g. 541512)")
    p.add_argument("--ceiling", type=float, default=0.0,
                   help="Dollar ceiling for the initiative")
    p.add_argument("--list-matches", action="store_true",
                   help="List persisted matches")
    p.add_argument("--recommended-only", action="store_true",
                   help="Only show recommended matches")
    p.add_argument("--flag-unviable", action="store_true",
                   help="List initiatives with no viable vehicle")
    p.add_argument("--list-viability", action="store_true",
                   help="List all viability assessments")
    p.add_argument("--summary", action="store_true",
                   help="Rollup counts")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.seed:
            result = seed_default_vehicles()
            payload = {"ok": True, "action": "seed", **result}
        elif args.list_vehicles:
            payload = {
                "ok": True,
                "action": "list_vehicles",
                "vehicles": list_vehicles(args.vehicle_type),
            }
        elif args.match_initiative:
            if not args.initiative_code:
                raise ValueError("--initiative-code required with --match-initiative")
            payload = {
                "ok": True,
                "action": "match_initiative",
                "result": match_initiative(
                    initiative_code=args.initiative_code,
                    title=args.title,
                    agency=args.agency,
                    naics=args.naics,
                    ceiling_usd=args.ceiling,
                ),
            }
        elif args.list_matches:
            payload = {
                "ok": True,
                "action": "list_matches",
                "matches": list_matches(
                    initiative_code=args.initiative_code,
                    recommended_only=args.recommended_only,
                ),
            }
        elif args.flag_unviable:
            payload = {
                "ok": True,
                "action": "flag_unviable",
                "initiatives": flag_unviable_initiatives(),
            }
        elif args.list_viability:
            payload = {
                "ok": True,
                "action": "list_viability",
                "viability": list_viability(),
            }
        elif args.summary:
            payload = {
                "ok": True,
                "action": "summary",
                "summary": get_viability_summary(),
            }
        else:
            parser.print_help()
            return 1
    except Exception as e:
        payload = {"ok": False, "error": str(e), "error_type": type(e).__name__}
        print(json.dumps(payload, indent=2))
        return 2

    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
