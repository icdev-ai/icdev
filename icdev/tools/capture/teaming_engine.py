#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Teaming Partner Identification and Gap Analysis for GovProposal.

Identifies capability gaps between opportunity requirements and company
knowledge base, then searches the teaming_partners table for companies
that fill those gaps.  Partners are scored on capability match, clearance
compatibility, contract vehicle access, past collaboration history, and
relationship strength.

SAM.gov Entity Search: Queries the SAM.gov Entity Management API to
discover potential teaming partners matching capability gaps, NAICS codes,
and set-aside requirements.  Requires SAM_GOV_API_KEY env var.

Usage:
    python tools/capture/teaming_engine.py --find --opp-id OPP-abc123 --json
    python tools/capture/teaming_engine.py --add --company-name "Acme Corp" --capabilities "cloud,DevSecOps" --json
    python tools/capture/teaming_engine.py --get --partner-id TP-abc123 --json
    python tools/capture/teaming_engine.py --list [--capability cloud] [--limit 20] --json
    python tools/capture/teaming_engine.py --gap-analysis --opp-id OPP-abc123 --json
    python tools/capture/teaming_engine.py --discover --opp-id OPP-abc123 --json
    python tools/capture/teaming_engine.py --sam-search --keywords "cloud,DevSecOps" --naics 541512 --json
    python tools/capture/teaming_engine.py --import-entity --uei ABCDE12345 --json
"""

import json
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# Optional imports
try:
    import yaml  # noqa: F401
except ImportError:
    yaml = None

try:
    import requests as _requests
except ImportError:
    _requests = None

SAM_GOV_API_KEY = os.environ.get("SAM_GOV_API_KEY", "")
SAM_ENTITY_API_URL = "https://api.sam.gov/entity-information/v3/entities"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tp_id():
    """Generate a teaming-partner ID: TP- followed by 12 hex characters."""
    return "TP-" + secrets.token_hex(6)


def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled.

    Args:
        db_path: Optional path override.  Falls back to DB_PATH.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None,
           details=None):
    """Write an append-only audit trail record.

    Args:
        conn: Active database connection.
        event_type: Category of event.
        action: Human-readable description.
        entity_type: Type of entity affected.
        entity_id: ID of the affected entity.
        details: Optional JSON-serializable details dict.
    """
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "teaming_engine",
            action,
            entity_type,
            entity_id,
            json.dumps(details) if details else None,
            _now(),
        ),
    )


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value):
    """Safely parse a JSON string field."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _serialize_list(value):
    """Serialize a list or comma-separated string to JSON array for storage.

    Args:
        value: A list, comma-separated string, or None.

    Returns:
        JSON array string, or None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    return json.dumps([str(value)])


def _extract_keywords(text):
    """Extract lowercase significant keywords from a text block.

    Strips common stop words and returns a set of terms >= 4 chars.
    """
    if not text:
        return set()
    stops = {
        "that", "this", "with", "from", "will", "have", "been", "their",
        "they", "them", "than", "also", "what", "when", "which", "each",
        "into", "more", "some", "such", "only", "other", "about", "over",
        "must", "shall", "should", "provide", "including", "support",
        "services", "service", "contractor", "government", "required",
    }
    words = set(re.findall(r"[a-z]{4,}", text.lower()))
    return words - stops


# ---------------------------------------------------------------------------
# Internal analysis helpers
# ---------------------------------------------------------------------------

def _load_opportunity(conn, opp_id):
    """Load opportunity record and raise if not found."""
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Opportunity not found: {opp_id}")
    return _row_to_dict(row)


def _load_company_capabilities(conn, limit=50):
    """Load all active company capabilities from the knowledge base.

    Returns a set of keywords representing areas of internal capability.
    """
    rows = conn.execute(
        "SELECT title, content, tags FROM kb_entries "
        "WHERE is_active = 1 AND entry_type IN "
        "('capability', 'domain_expertise', 'tool_technology', "
        " 'methodology', 'solution_architecture') "
        "ORDER BY quality_score DESC NULLS LAST LIMIT ?",
        (limit,),
    ).fetchall()
    kw_set = set()
    for r in rows:
        kw_set |= _extract_keywords(r["title"])
        kw_set |= _extract_keywords(r["content"])
        tags = _parse_json_field(r["tags"])
        if isinstance(tags, list):
            for t in tags:
                kw_set |= _extract_keywords(t)
    return kw_set


def _identify_gaps(opp_keywords, company_keywords):
    """Return the set of opportunity keywords NOT covered by company KB.

    Args:
        opp_keywords: set of requirement-derived keywords.
        company_keywords: set of company-capability keywords.

    Returns:
        set of gap keywords.
    """
    return opp_keywords - company_keywords


def _score_partner(partner, gap_keywords, opp):
    """Score a teaming partner against opportunity gaps and requirements.

    Scoring dimensions (each 0.0-1.0):
      capability_match  (0.30)
      clearance_match   (0.20)
      vehicle_access    (0.15)
      past_collaboration(0.15)
      relationship_score(0.20)

    Args:
        partner: dict of teaming_partners row.
        gap_keywords: set of capability gap keywords.
        opp: opportunity dict.

    Returns:
        dict with dimension scores and weighted overall.
    """
    weights = {
        "capability_match": 0.30,
        "clearance_match": 0.20,
        "vehicle_access": 0.15,
        "past_collaboration": 0.15,
        "relationship_score": 0.20,
    }

    # --- capability_match: overlap between partner capabilities and gaps ---
    partner_caps = _extract_keywords(partner.get("capabilities") or "")
    partner_naics = _parse_json_field(partner.get("naics_codes")) or []
    if isinstance(partner_naics, str):
        partner_naics = [partner_naics]
    for code in partner_naics:
        partner_caps.add(code)

    if gap_keywords:
        overlap = gap_keywords & partner_caps
        cap_score = len(overlap) / max(len(gap_keywords), 1)
    else:
        cap_score = 0.5  # no gaps means any partner is neutral

    # --- clearance_match ---
    # Simple ordinal comparison
    clearance_order = {
        "ts_sci_poly": 6, "ts_sci": 5, "top_secret": 4,
        "secret": 3, "public_trust": 2, "none": 1,
    }
    partner_cl = (partner.get("clearance_level") or "none").lower()
    partner_cl_val = clearance_order.get(partner_cl, 1)
    # If opportunity doesn't specify, default to secret
    cl_score = min(partner_cl_val / 4.0, 1.0)

    # --- vehicle_access ---
    partner_vehicles = _parse_json_field(
        partner.get("contract_vehicles")
    ) or []
    if isinstance(partner_vehicles, str):
        partner_vehicles = [partner_vehicles]
    veh_score = 0.3  # default when we can't determine
    if partner_vehicles:
        veh_score = min(len(partner_vehicles) / 3.0, 1.0)

    # --- past_collaboration ---
    collabs = _parse_json_field(partner.get("past_collaborations")) or []
    if isinstance(collabs, str):
        collabs = [collabs]
    collab_score = min(len(collabs) / 3.0, 1.0)

    # --- relationship_score (from DB or computed) ---
    rel_score = partner.get("relationship_score") or 0.3
    if isinstance(rel_score, str):
        try:
            rel_score = float(rel_score)
        except (ValueError, TypeError):
            rel_score = 0.3

    scores = {
        "capability_match": round(min(cap_score, 1.0), 3),
        "clearance_match": round(cl_score, 3),
        "vehicle_access": round(veh_score, 3),
        "past_collaboration": round(collab_score, 3),
        "relationship_score": round(min(rel_score, 1.0), 3),
    }

    overall = sum(scores[d] * weights[d] for d in scores)
    scores["overall"] = round(overall, 3)

    return scores


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def find_partners(opp_id, db_path=None):
    """Identify teaming partners that fill capability gaps for an opportunity.

    Analyzes opportunity requirements against the company knowledge base,
    identifies gaps, then searches the teaming_partners table for companies
    that address those gaps.  Each partner is scored on five dimensions.

    Args:
        opp_id: Opportunity ID to analyze.
        db_path: Optional database path override.

    Returns:
        dict with gap_keywords, partner_scores (sorted best-first), and
        recommendation text.

    Raises:
        ValueError: If opportunity not found.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        opp_text = " ".join(filter(None, [
            opp.get("title"), opp.get("description"), opp.get("full_text"),
        ]))
        opp_keywords = _extract_keywords(opp_text)
        company_keywords = _load_company_capabilities(conn)
        gaps = _identify_gaps(opp_keywords, company_keywords)

        # Fetch active teaming partners
        rows = conn.execute(
            "SELECT * FROM teaming_partners WHERE is_active = 1 "
            "ORDER BY relationship_score DESC NULLS LAST"
        ).fetchall()
        partners = [_row_to_dict(r) for r in rows]

        scored = []
        for p in partners:
            scores = _score_partner(p, gaps, opp)
            scored.append({
                "partner_id": p["id"],
                "company_name": p["company_name"],
                "capabilities": _parse_json_field(p.get("capabilities")),
                "clearance_level": p.get("clearance_level"),
                "scores": scores,
                "overall_score": scores["overall"],
            })

        # Sort best-first
        scored.sort(key=lambda x: x["overall_score"], reverse=True)

        result = {
            "opportunity_id": opp_id,
            "gap_keywords": sorted(gaps)[:30],
            "gap_count": len(gaps),
            "partners_evaluated": len(scored),
            "recommended_partners": scored[:10],
        }

        _audit(conn, "capture.teaming_find",
               f"Evaluated {len(scored)} partners for {opp_id}",
               "teaming_partners", opp_id,
               {"gap_count": len(gaps),
                "top_partner": scored[0]["company_name"] if scored else None})
        conn.commit()
        return result
    finally:
        conn.close()


def add_partner(company_name, capabilities, db_path=None, **kwargs):
    """Add a new teaming partner to the database.

    Args:
        company_name: Company legal name.
        capabilities: Comma-separated or list of capability keywords.
        db_path: Optional database path override.
        **kwargs: Optional fields — cage_code, duns_number, naics_codes,
            set_aside_status, clearance_level, contract_vehicles,
            past_collaborations, contact_name, contact_email,
            relationship_score, notes.

    Returns:
        dict of the created partner record.
    """
    partner_id = _tp_id()
    now = _now()

    caps_json = _serialize_list(capabilities)
    naics_json = _serialize_list(kwargs.get("naics_codes"))
    vehicles_json = _serialize_list(kwargs.get("contract_vehicles"))
    collabs_json = _serialize_list(kwargs.get("past_collaborations"))

    rel_score = kwargs.get("relationship_score")
    if rel_score is not None:
        try:
            rel_score = float(rel_score)
        except (ValueError, TypeError):
            rel_score = None

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO teaming_partners "
            "(id, company_name, cage_code, duns_number, naics_codes, "
            " capabilities, set_aside_status, clearance_level, "
            " contract_vehicles, past_collaborations, contact_name, "
            " contact_email, relationship_score, notes, "
            " is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                partner_id, company_name,
                kwargs.get("cage_code"), kwargs.get("duns_number"),
                naics_json, caps_json,
                kwargs.get("set_aside_status"), kwargs.get("clearance_level"),
                vehicles_json, collabs_json,
                kwargs.get("contact_name"), kwargs.get("contact_email"),
                rel_score, kwargs.get("notes"),
                now, now,
            ),
        )
        _audit(conn, "capture.teaming_add",
               f"Added teaming partner: {company_name}",
               "teaming_partners", partner_id)
        conn.commit()

        return {
            "id": partner_id,
            "company_name": company_name,
            "capabilities": _parse_json_field(caps_json),
            "naics_codes": _parse_json_field(naics_json),
            "clearance_level": kwargs.get("clearance_level"),
            "contract_vehicles": _parse_json_field(vehicles_json),
            "relationship_score": rel_score,
            "is_active": 1,
            "created_at": now,
        }
    finally:
        conn.close()


def get_partner(partner_id, db_path=None):
    """Get a single teaming partner by ID.

    Args:
        partner_id: The partner ID (e.g. 'TP-abc123def456').
        db_path: Optional database path override.

    Returns:
        dict with partner fields, or None if not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM teaming_partners WHERE id = ?", (partner_id,)
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        for field in ("capabilities", "naics_codes", "contract_vehicles",
                       "past_collaborations"):
            d[field] = _parse_json_field(d.get(field))
        return d
    finally:
        conn.close()


def list_partners(capability=None, limit=20, db_path=None):
    """List teaming partners with optional capability filter.

    Args:
        capability: Optional keyword to filter on capabilities.
        limit: Maximum partners to return (default 20).
        db_path: Optional database path override.

    Returns:
        list of partner dicts.
    """
    conn = _get_db(db_path)
    try:
        if capability:
            rows = conn.execute(
                "SELECT * FROM teaming_partners "
                "WHERE is_active = 1 AND capabilities LIKE ? "
                "ORDER BY relationship_score DESC NULLS LAST, "
                "updated_at DESC LIMIT ?",
                (f"%{capability}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM teaming_partners "
                "WHERE is_active = 1 "
                "ORDER BY relationship_score DESC NULLS LAST, "
                "updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = _row_to_dict(r)
            for field in ("capabilities", "naics_codes",
                           "contract_vehicles", "past_collaborations"):
                d[field] = _parse_json_field(d.get(field))
            results.append(d)
        return results
    finally:
        conn.close()


def gap_analysis(opp_id, db_path=None):
    """Return capability gaps between opportunity requirements and company KB.

    Args:
        opp_id: Opportunity ID to analyze.
        db_path: Optional database path override.

    Returns:
        dict with opportunity summary, company_keywords count,
        gap_keywords (sorted), and coverage_ratio.

    Raises:
        ValueError: If opportunity not found.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        opp_text = " ".join(filter(None, [
            opp.get("title"), opp.get("description"), opp.get("full_text"),
        ]))
        opp_keywords = _extract_keywords(opp_text)
        company_keywords = _load_company_capabilities(conn)
        gaps = _identify_gaps(opp_keywords, company_keywords)
        covered = opp_keywords - gaps

        coverage_ratio = (
            len(covered) / max(len(opp_keywords), 1)
        )

        result = {
            "opportunity_id": opp_id,
            "opportunity_title": opp.get("title"),
            "requirement_keywords": len(opp_keywords),
            "company_capability_keywords": len(company_keywords),
            "covered_keywords": len(covered),
            "gap_keywords": sorted(gaps)[:50],
            "gap_count": len(gaps),
            "coverage_ratio": round(coverage_ratio, 3),
            "assessment": (
                "Strong coverage" if coverage_ratio >= 0.7
                else "Moderate coverage — teaming recommended"
                if coverage_ratio >= 0.4
                else "Significant gaps — teaming essential"
            ),
        }

        _audit(conn, "capture.teaming_gap",
               f"Gap analysis for {opp_id}: {result['assessment']}",
               "opportunity", opp_id,
               {"coverage_ratio": result["coverage_ratio"],
                "gap_count": result["gap_count"]})
        conn.commit()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SAM.gov Entity Search
# ---------------------------------------------------------------------------

def search_sam_entities(keywords=None, naics_codes=None, set_aside=None,
                        state=None, limit=25):
    """Search SAM.gov Entity Management API for potential partners.

    Queries the public SAM.gov Entity API to find registered entities
    matching the given criteria.  Requires SAM_GOV_API_KEY environment
    variable (free registration at sam.gov).

    Args:
        keywords: List or comma-separated capability keywords.
        naics_codes: List or comma-separated NAICS codes.
        set_aside: Set-aside type filter (e.g. 'SBA', '8A', 'SDVOSB',
            'HUBZ', 'WOSB').
        state: US state abbreviation (e.g. 'VA', 'MD').
        limit: Max results (default 25, max 100).

    Returns:
        dict with entities list and metadata.
    """
    if not SAM_GOV_API_KEY:
        return {
            "status": "no_api_key",
            "message": (
                "SAM_GOV_API_KEY not set.  Register for a free API key "
                "at https://sam.gov/content/entity-information and set "
                "the SAM_GOV_API_KEY environment variable."
            ),
            "entities": [],
        }

    if _requests is None:
        return {
            "status": "missing_dependency",
            "message": "requests library required. Install with: pip install requests",
            "entities": [],
        }

    # Build query parameters
    params = {
        "api_key": SAM_GOV_API_KEY,
        "registrationStatus": "A",  # Active registrations only
        "purposeOfRegistrationCode": "Z2",  # Government business
        "includeSections": "entityRegistration,coreData",
        "page": 0,
        "size": min(int(limit), 100),
    }

    if keywords:
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        # SAM API uses q parameter for keyword search
        params["q"] = " ".join(keywords[:5])

    if naics_codes:
        if isinstance(naics_codes, str):
            naics_codes = [n.strip() for n in naics_codes.split(",")
                           if n.strip()]
        params["naicsCode"] = naics_codes[0]  # API accepts single NAICS

    if set_aside:
        # Map common abbreviations to SAM API business type codes
        set_aside_map = {
            "SBA": "2X",
            "8A": "A6",
            "SDVOSB": "QF",
            "HUBZ": "A2",
            "WOSB": "A5",
            "EDWOSB": "XX",
        }
        code = set_aside_map.get(set_aside.upper(), set_aside)
        params["businessTypeCode"] = code

    if state:
        params["physicalAddressProvinceOrStateCode"] = state.upper()

    try:
        resp = _requests.get(SAM_ENTITY_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except _requests.exceptions.RequestException as exc:
        err_msg = str(exc)
        if SAM_GOV_API_KEY and SAM_GOV_API_KEY in err_msg:
            err_msg = err_msg.replace(SAM_GOV_API_KEY, "***REDACTED***")
        return {
            "status": "api_error",
            "message": f"SAM.gov API error: {err_msg}",
            "entities": [],
        }

    # Parse response into simplified entity records
    entities = []
    for entity_data in data.get("entityData", []):
        reg = entity_data.get("entityRegistration", {})
        core = entity_data.get("coreData", {})
        general = core.get("generalInformation", {})
        phys_addr = core.get("physicalAddress", {})

        entity = {
            "uei": reg.get("ueiSAM", ""),
            "cage_code": reg.get("cageCode", ""),
            "legal_name": reg.get("legalBusinessName", ""),
            "dba_name": reg.get("dbaName", ""),
            "registration_status": reg.get("registrationStatus", ""),
            "purpose_of_registration": reg.get(
                "purposeOfRegistrationDesc", ""
            ),
            "naics_codes": [
                n.get("naicsCode", "")
                for n in core.get("federalHierarchy", {}).get(
                    "naicsList", []
                )
            ] if core.get("federalHierarchy") else [],
            "business_types": [
                bt.get("businessTypeDesc", "")
                for bt in general.get("businessTypes", [])
            ] if general.get("businessTypes") else [],
            "city": phys_addr.get("city", ""),
            "state": phys_addr.get("stateOrProvinceCode", ""),
            "country": phys_addr.get("countryCode", "US"),
            "sam_profile_url": (
                f"https://sam.gov/entity/{reg.get('ueiSAM', '')}/coreData"
                if reg.get("ueiSAM") else ""
            ),
        }
        entities.append(entity)

    return {
        "status": "success",
        "query": {
            "keywords": keywords,
            "naics_codes": naics_codes,
            "set_aside": set_aside,
            "state": state,
        },
        "total_records": data.get("totalRecords", len(entities)),
        "entities": entities,
    }


def discover_partners(opp_id, db_path=None, limit=15):
    """Discover potential teaming partners from SAM.gov for an opportunity.

    Runs gap_analysis to find capability gaps, then queries SAM.gov for
    entities matching the gap keywords, opportunity NAICS, and required
    set-aside type.  Discovered entities are scored on capability match,
    set-aside match, and registration status.

    Args:
        opp_id: Opportunity ID to discover partners for.
        db_path: Optional database path override.
        limit: Max entities to return (default 15).

    Returns:
        dict with gap analysis, SAM.gov results, and scored recommendations.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)

        # Run gap analysis
        opp_text = " ".join(filter(None, [
            opp.get("title"), opp.get("description"), opp.get("full_text"),
        ]))
        opp_keywords = _extract_keywords(opp_text)
        company_keywords = _load_company_capabilities(conn)
        gaps = _identify_gaps(opp_keywords, company_keywords)

        # Extract search parameters from opportunity
        naics = opp.get("naics_code")
        set_aside = opp.get("set_aside_type")

        # Use top gap keywords as search terms
        gap_search = sorted(gaps)[:10]

        # Query SAM.gov
        sam_results = search_sam_entities(
            keywords=gap_search,
            naics_codes=naics,
            set_aside=set_aside,
            limit=limit,
        )

        if sam_results.get("status") != "success":
            # Return what we have even without SAM.gov
            _audit(conn, "capture.discover_partners",
                   f"Discovery for {opp_id} — SAM.gov unavailable: "
                   f"{sam_results.get('message', 'unknown')}",
                   "opportunity", opp_id,
                   {"status": sam_results.get("status")})
            conn.commit()
            return {
                "opportunity_id": opp_id,
                "gap_keywords": sorted(gaps)[:30],
                "sam_status": sam_results.get("status"),
                "sam_message": sam_results.get("message"),
                "discovered_entities": [],
                "recommendation": (
                    "SAM.gov search unavailable.  Use --find to search "
                    "existing teaming partners or set SAM_GOV_API_KEY."
                ),
            }

        # Score discovered entities
        scored_entities = []
        for entity in sam_results.get("entities", []):
            # Capability match: overlap between entity NAICS/name and gaps
            entity_keywords = _extract_keywords(
                entity.get("legal_name", "")
            )
            for nc in entity.get("naics_codes", []):
                entity_keywords.add(nc)
            cap_overlap = gaps & entity_keywords
            cap_score = len(cap_overlap) / max(len(gaps), 1) if gaps else 0.5

            # Set-aside match
            sa_score = 0.5
            if set_aside and entity.get("business_types"):
                sa_lower = set_aside.lower()
                bt_text = " ".join(
                    bt.lower() for bt in entity["business_types"]
                )
                if sa_lower in bt_text:
                    sa_score = 1.0

            # Registration status
            reg_score = 1.0 if entity.get(
                "registration_status"
            ) == "Active" else 0.3

            overall = round(
                cap_score * 0.50 + sa_score * 0.30 + reg_score * 0.20,
                3,
            )
            scored_entities.append({
                **entity,
                "scores": {
                    "capability_match": round(cap_score, 3),
                    "set_aside_match": round(sa_score, 3),
                    "registration_status": round(reg_score, 3),
                    "overall": overall,
                },
            })

        # Sort by overall score
        scored_entities.sort(
            key=lambda x: x["scores"]["overall"], reverse=True
        )

        result = {
            "opportunity_id": opp_id,
            "opportunity_title": opp.get("title"),
            "gap_keywords": sorted(gaps)[:30],
            "gap_count": len(gaps),
            "sam_status": "success",
            "total_found": sam_results.get("total_records", 0),
            "discovered_entities": scored_entities[:limit],
        }

        _audit(conn, "capture.discover_partners",
               f"Discovered {len(scored_entities)} SAM.gov entities "
               f"for {opp_id}",
               "opportunity", opp_id,
               {"gap_count": len(gaps),
                "entities_found": len(scored_entities)})
        conn.commit()
        return result
    finally:
        conn.close()


def import_sam_entity(uei, db_path=None):
    """Import a SAM.gov entity as a teaming partner.

    Queries SAM.gov for the entity by UEI (Unique Entity Identifier),
    then creates a teaming_partners record with the entity data.

    Args:
        uei: SAM.gov Unique Entity Identifier (UEI).
        db_path: Optional database path override.

    Returns:
        dict with created partner record, or error if entity not found.
    """
    # Search SAM.gov by UEI
    if not SAM_GOV_API_KEY:
        return {
            "error": "SAM_GOV_API_KEY not set",
            "message": (
                "Register at https://sam.gov/content/entity-information "
                "for a free API key."
            ),
        }

    if _requests is None:
        return {
            "error": "requests library required",
            "message": "Install with: pip install requests",
        }

    params = {
        "api_key": SAM_GOV_API_KEY,
        "ueiSAM": uei,
        "includeSections": "entityRegistration,coreData",
    }

    try:
        resp = _requests.get(SAM_ENTITY_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except _requests.exceptions.RequestException as exc:
        err_msg = str(exc)
        if SAM_GOV_API_KEY and SAM_GOV_API_KEY in err_msg:
            err_msg = err_msg.replace(SAM_GOV_API_KEY, "***REDACTED***")
        return {"error": f"SAM.gov API error: {err_msg}"}

    entities = data.get("entityData", [])
    if not entities:
        return {"error": f"No entity found for UEI: {uei}"}

    entity = entities[0]
    reg = entity.get("entityRegistration", {})
    core = entity.get("coreData", {})
    general = core.get("generalInformation", {})

    # Extract fields
    legal_name = reg.get("legalBusinessName", uei)
    cage_code = reg.get("cageCode")
    naics_list = [
        n.get("naicsCode", "")
        for n in core.get("federalHierarchy", {}).get("naicsList", [])
    ] if core.get("federalHierarchy") else []

    business_types = [
        bt.get("businessTypeDesc", "")
        for bt in general.get("businessTypes", [])
    ] if general.get("businessTypes") else []

    # Determine set-aside from business types
    set_aside = None
    for bt in business_types:
        bt_lower = bt.lower()
        if "8(a)" in bt_lower:
            set_aside = "8a"
            break
        if "service-disabled" in bt_lower:
            set_aside = "SDVOSB"
            break
        if "hubzone" in bt_lower:
            set_aside = "HUBZone"
            break
        if "woman-owned" in bt_lower:
            set_aside = "WOSB"
            break

    # Create teaming partner
    result = add_partner(
        company_name=legal_name,
        capabilities=", ".join(business_types[:5]),
        db_path=db_path,
        cage_code=cage_code,
        duns_number=uei,  # UEI replaces DUNS
        naics_codes=",".join(naics_list),
        set_aside_status=set_aside,
        notes=f"Imported from SAM.gov (UEI: {uei})",
    )

    result["sam_source"] = {
        "uei": uei,
        "legal_name": legal_name,
        "cage_code": cage_code,
        "naics_codes": naics_list,
        "business_types": business_types,
    }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Teaming Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --find --opp-id OPP-abc123 --json\n"
            "  %(prog)s --add --company-name 'Acme Corp' "
            "--capabilities 'cloud,DevSecOps' --json\n"
            "  %(prog)s --get --partner-id TP-abc123 --json\n"
            "  %(prog)s --list --capability cloud --limit 10 --json\n"
            "  %(prog)s --gap-analysis --opp-id OPP-abc123 --json\n"
            "  %(prog)s --discover --opp-id OPP-abc123 --json\n"
            "  %(prog)s --sam-search --keywords 'cloud,AI' "
            "--naics 541512 --json\n"
            "  %(prog)s --import-entity --uei ABCDE12345 --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--find", action="store_true",
                        help="Find teaming partners for an opportunity")
    action.add_argument("--add", action="store_true",
                        help="Add a new teaming partner")
    action.add_argument("--get", action="store_true",
                        help="Get a partner by ID")
    action.add_argument("--list", action="store_true",
                        help="List teaming partners")
    action.add_argument("--gap-analysis", action="store_true",
                        help="Run capability gap analysis")
    action.add_argument("--discover", action="store_true",
                        help="Discover partners from SAM.gov for an opp")
    action.add_argument("--sam-search", action="store_true",
                        help="Search SAM.gov entities directly")
    action.add_argument("--import-entity", action="store_true",
                        help="Import a SAM.gov entity as a teaming partner")

    parser.add_argument("--opp-id", help="Opportunity ID")
    parser.add_argument("--uei", help="SAM.gov Unique Entity Identifier")
    parser.add_argument("--keywords",
                        help="Comma-separated search keywords (for --sam-search)")
    parser.add_argument("--partner-id", help="Teaming partner ID")
    parser.add_argument("--company-name", help="Company name (for --add)")
    parser.add_argument("--capabilities",
                        help="Comma-separated capabilities (for --add)")
    parser.add_argument("--capability",
                        help="Filter keyword (for --list)")
    parser.add_argument("--cage-code", help="CAGE code")
    parser.add_argument("--duns-number", help="DUNS number")
    parser.add_argument("--naics-codes",
                        help="Comma-separated NAICS codes")
    parser.add_argument("--clearance-level", help="Clearance level")
    parser.add_argument("--set-aside-status", help="Set-aside status")
    parser.add_argument("--contract-vehicles",
                        help="Comma-separated contract vehicles")
    parser.add_argument("--contact-name", help="POC name")
    parser.add_argument("--contact-email", help="POC email")
    parser.add_argument("--relationship-score", type=float,
                        help="Relationship score (0.0-1.0)")
    parser.add_argument("--notes", help="Free-text notes")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max results for --list (default: 20)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.find:
            if not args.opp_id:
                parser.error("--find requires --opp-id")
            result = find_partners(args.opp_id, db_path=db)

        elif args.add:
            if not args.company_name:
                parser.error("--add requires --company-name")
            if not args.capabilities:
                parser.error("--add requires --capabilities")
            result = add_partner(
                company_name=args.company_name,
                capabilities=args.capabilities,
                db_path=db,
                cage_code=args.cage_code,
                duns_number=args.duns_number,
                naics_codes=args.naics_codes,
                clearance_level=args.clearance_level,
                set_aside_status=args.set_aside_status,
                contract_vehicles=args.contract_vehicles,
                contact_name=args.contact_name,
                contact_email=args.contact_email,
                relationship_score=args.relationship_score,
                notes=args.notes,
            )

        elif args.get:
            if not args.partner_id:
                parser.error("--get requires --partner-id")
            result = get_partner(args.partner_id, db_path=db)
            if result is None:
                result = {"error": f"Partner not found: {args.partner_id}"}

        elif args.list:
            result = list_partners(
                capability=args.capability,
                limit=args.limit,
                db_path=db,
            )

        elif args.gap_analysis:
            if not args.opp_id:
                parser.error("--gap-analysis requires --opp-id")
            result = gap_analysis(args.opp_id, db_path=db)

        elif args.discover:
            if not args.opp_id:
                parser.error("--discover requires --opp-id")
            result = discover_partners(
                args.opp_id, db_path=db, limit=args.limit,
            )

        elif args.sam_search:
            result = search_sam_entities(
                keywords=args.keywords,
                naics_codes=args.naics_codes,
                set_aside=args.set_aside_status,
                limit=args.limit,
            )

        elif args.import_entity:
            if not args.uei:
                parser.error("--import-entity requires --uei")
            result = import_sam_entity(args.uei, db_path=db)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} partner(s):")
                for p in result:
                    name = p.get("company_name", "?")
                    pid = p.get("id", "?")
                    print(f"  [{pid}] {name}")
            elif isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, (list, dict)):
                        print(f"  {key}: {json.dumps(value, default=str)}")
                    else:
                        print(f"  {key}: {value}")

    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        if args.json:
            print(json.dumps({"error": f"Database error: {exc}"}, indent=2))
        else:
            print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
