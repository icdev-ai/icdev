#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Competitive intelligence tracking from FPDS and other sources.

Manages competitor profiles, tracks contract wins from FPDS data,
generates competitive analyses, provides head-to-head comparisons,
and produces market landscape assessments by NAICS and agency.

Usage:
    python tools/competitive/competitor_tracker.py --add --company-name "Acme Corp" --json
    python tools/competitive/competitor_tracker.py --track [--competitor-id COMP-001] [--agency "DoD"] --json
    python tools/competitive/competitor_tracker.py --analyze --competitor-id COMP-001 --json
    python tools/competitive/competitor_tracker.py --head-to-head --competitor-id COMP-001 --json
    python tools/competitive/competitor_tracker.py --landscape --naics 541512 [--agency "DoD"] --json
    python tools/competitive/competitor_tracker.py --list [--limit 20] --json
    python tools/competitive/competitor_tracker.py --get --competitor-id COMP-001 --json
"""

import json
import os
import secrets
import sqlite3
import sys
from collections import defaultdict
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
    import requests  # noqa: F401
except ImportError:
    requests = None

# FPDS API base URL (for real queries when available)
FPDS_API_BASE = os.environ.get(
    "FPDS_API_URL", "https://www.fpds.gov/ezsearch/LATEST"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _comp_id():
    """Generate a competitor ID: COMP- followed by 12 hex characters."""
    return "COMP-" + secrets.token_hex(6)


def _win_id():
    """Generate a competitor win record ID."""
    return "CW-" + secrets.token_hex(6)


def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled.

    Args:
        db_path: Optional path override. Falls back to DB_PATH.

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
        action: Human-readable description of the action.
        entity_type: Type of entity affected.
        entity_id: ID of the affected entity.
        details: Optional JSON-serializable details dict.
    """
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "competitor_tracker",
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
    """Serialize a list or comma-separated string to JSON array.

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


def _safe_divide(numerator, denominator, default=0.0):
    """Safely divide, returning default if denominator is zero."""
    if not denominator:
        return default
    return numerator / denominator


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def add_competitor(company_name, db_path=None, **kwargs):
    """Register a new competitor in the tracking system.

    Args:
        company_name: Full legal name of the competitor.
        db_path: Optional database path override.
        **kwargs: Additional competitor fields:
            cage_code: CAGE code.
            duns_number: DUNS/SAM UEI number.
            website: Company website URL.
            naics_codes: List or comma-separated NAICS codes.
            capabilities: Text description of capabilities.
            contract_vehicles: List or comma-separated contract vehicles.
            strengths: Known strengths.
            weaknesses: Known weaknesses.
            key_personnel: Key personnel JSON or text.
            revenue_estimate: Estimated annual revenue.
            employee_count: Estimated employee count.
            clearance_level: Facility clearance level.
            set_aside_status: Small business set-aside status.

    Returns:
        dict with the created competitor record.
    """
    comp_id = _comp_id()
    now = _now()

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO competitors "
            "(id, company_name, cage_code, duns_number, website, "
            "naics_codes, capabilities, contract_vehicles, key_personnel, "
            "revenue_estimate, employee_count, clearance_level, "
            "set_aside_status, strengths, weaknesses, notes, "
            "is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                comp_id,
                company_name,
                kwargs.get("cage_code"),
                kwargs.get("duns_number"),
                kwargs.get("website"),
                _serialize_list(kwargs.get("naics_codes")),
                kwargs.get("capabilities"),
                _serialize_list(kwargs.get("contract_vehicles")),
                kwargs.get("key_personnel"),
                kwargs.get("revenue_estimate"),
                kwargs.get("employee_count"),
                kwargs.get("clearance_level"),
                kwargs.get("set_aside_status"),
                kwargs.get("strengths"),
                kwargs.get("weaknesses"),
                kwargs.get("notes"),
                now, now,
            ),
        )

        _audit(
            conn, "competitor.add",
            f"Added competitor: {company_name}",
            "competitor", comp_id,
            {"company_name": company_name},
        )
        conn.commit()

        return {
            "id": comp_id,
            "company_name": company_name,
            "cage_code": kwargs.get("cage_code"),
            "duns_number": kwargs.get("duns_number"),
            "website": kwargs.get("website"),
            "naics_codes": _parse_json_field(
                _serialize_list(kwargs.get("naics_codes"))
            ),
            "capabilities": kwargs.get("capabilities"),
            "contract_vehicles": _parse_json_field(
                _serialize_list(kwargs.get("contract_vehicles"))
            ),
            "strengths": kwargs.get("strengths"),
            "weaknesses": kwargs.get("weaknesses"),
            "is_active": 1,
            "created_at": now,
        }
    finally:
        conn.close()


def track_wins(competitor_id=None, agency=None, naics=None, days_back=365,
               db_path=None):
    """Track competitor contract wins from FPDS data.

    Queries the FPDS API (or simulated data) for recent contract
    awards to the specified competitor or across all tracked competitors.
    Stores results in the competitor_wins table.

    Args:
        competitor_id: Optional specific competitor to track.
        agency: Optional agency name filter.
        naics: Optional NAICS code filter.
        days_back: Number of days to look back (default 365).
        db_path: Optional database path override.

    Returns:
        dict with tracking results and win counts.
    """
    conn = _get_db(db_path)
    try:
        now = _now()
        wins_added = 0

        # Get competitors to track
        if competitor_id:
            competitors = conn.execute(
                "SELECT * FROM competitors WHERE id = ? AND is_active = 1",
                (competitor_id,),
            ).fetchall()
        else:
            competitors = conn.execute(
                "SELECT * FROM competitors WHERE is_active = 1"
            ).fetchall()

        if not competitors:
            return {
                "status": "no_competitors",
                "message": "No active competitors to track",
            }

        for comp in competitors:
            comp_dict = _row_to_dict(comp)
            company = comp_dict["company_name"]

            # Attempt FPDS API query
            fpds_wins = _query_fpds(
                company_name=company,
                agency=agency,
                naics=naics,
                days_back=days_back,
            )

            for win in fpds_wins:
                # Check for duplicates by fpds_id
                if win.get("fpds_id"):
                    existing = conn.execute(
                        "SELECT id FROM competitor_wins WHERE fpds_id = ?",
                        (win["fpds_id"],),
                    ).fetchone()
                    if existing:
                        continue

                win_record_id = _win_id()
                conn.execute(
                    "INSERT INTO competitor_wins "
                    "(id, competitor_id, competitor_name, contract_number, "
                    "agency, award_date, award_amount, naics_code, "
                    "description, contract_type, set_aside_type, fpds_id, "
                    "source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        win_record_id,
                        comp_dict["id"],
                        company,
                        win.get("contract_number"),
                        win.get("agency"),
                        win.get("award_date"),
                        win.get("award_amount"),
                        win.get("naics_code"),
                        win.get("description"),
                        win.get("contract_type"),
                        win.get("set_aside_type"),
                        win.get("fpds_id"),
                        "fpds",
                        now,
                    ),
                )
                wins_added += 1

        _audit(
            conn, "competitor.track",
            f"Tracked wins for {len(competitors)} competitor(s), "
            f"added {wins_added} new records",
            details={
                "competitor_id": competitor_id,
                "agency": agency,
                "naics": naics,
                "days_back": days_back,
                "wins_added": wins_added,
            },
        )
        conn.commit()

        return {
            "status": "tracked",
            "competitors_checked": len(competitors),
            "wins_added": wins_added,
            "filters": {
                "competitor_id": competitor_id,
                "agency": agency,
                "naics": naics,
                "days_back": days_back,
            },
            "tracked_at": now,
        }
    finally:
        conn.close()


def _query_fpds(company_name, agency=None, naics=None, days_back=365):
    """Query FPDS for competitor contract awards.

    Attempts to use the FPDS API via requests. Falls back to returning
    an empty list if the requests library is unavailable or the API
    is unreachable.

    Args:
        company_name: Name of the company to search.
        agency: Optional agency filter.
        naics: Optional NAICS code filter.
        days_back: Number of days to look back.

    Returns:
        list of dicts with award data.
    """
    # If requests is available, attempt API query
    if requests is not None:
        try:
            params = {
                "q": f"VENDOR_FULL_NAME:\"{company_name}\"",
                "length": 25,
            }
            if agency:
                params["q"] += f" AGENCY_NAME:\"{agency}\""
            if naics:
                params["q"] += f" NAICS:\"{naics}\""

            resp = requests.get(
                FPDS_API_BASE,
                params=params,
                timeout=30,
            )
            if resp.status_code == 200:
                # Parse FPDS XML/Atom response (simplified)
                return _parse_fpds_response(resp.text)
        except (requests.RequestException, Exception):
            pass

    # Fallback: return empty list (no simulated data)
    return []


def _parse_fpds_response(response_text):
    """Parse FPDS API response into structured award records.

    Handles the Atom/XML format returned by the FPDS EZ Search API.
    Falls back gracefully if parsing fails.

    Args:
        response_text: Raw response text from FPDS API.

    Returns:
        list of dicts with parsed award data.
    """
    results = []
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response_text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "ns1": "https://www.fpds.gov/FPDS",
        }
        for entry in root.findall(".//atom:entry", ns):
            content = entry.find(".//atom:content", ns)
            if content is None:
                continue
            award = content.find(".//ns1:award", ns)
            if award is None:
                continue

            def _text(element, path, default=None):
                el = element.find(path, ns)
                return el.text if el is not None else default

            results.append({
                "fpds_id": _text(award, ".//ns1:PIID"),
                "contract_number": _text(award, ".//ns1:PIID"),
                "agency": _text(award, ".//ns1:agencyID/@name"),
                "award_date": _text(award, ".//ns1:signedDate"),
                "award_amount": float(
                    _text(award, ".//ns1:obligatedAmount", "0")
                ),
                "naics_code": _text(award, ".//ns1:NAICS/@code"),
                "description": _text(
                    award, ".//ns1:descriptionOfContractRequirement"
                ),
                "contract_type": _text(
                    award, ".//ns1:typeOfContractPricing/@description"
                ),
                "set_aside_type": _text(
                    award, ".//ns1:typeOfSetAside/@description"
                ),
            })
    except Exception:
        pass
    return results


def analyze_competitor(competitor_id, db_path=None):
    """Generate a comprehensive competitive profile for a competitor.

    Includes: win history summary, agency presence, NAICS focus,
    pricing patterns, and teaming patterns.

    Args:
        competitor_id: The competitor ID to analyze.
        db_path: Optional database path override.

    Returns:
        dict with full competitive analysis.

    Raises:
        ValueError: If competitor not found.
    """
    conn = _get_db(db_path)
    try:
        comp_row = conn.execute(
            "SELECT * FROM competitors WHERE id = ?", (competitor_id,)
        ).fetchone()
        if comp_row is None:
            raise ValueError(f"Competitor not found: {competitor_id}")

        comp = _row_to_dict(comp_row)

        # Win history
        wins = conn.execute(
            "SELECT * FROM competitor_wins WHERE competitor_id = ? "
            "ORDER BY award_date DESC",
            (competitor_id,),
        ).fetchall()
        wins_list = [_row_to_dict(w) for w in wins]

        total_value = sum(
            w.get("award_amount", 0) or 0 for w in wins_list
        )

        # Agency presence
        agency_counts = defaultdict(int)
        agency_value = defaultdict(float)
        for w in wins_list:
            ag = w.get("agency") or "Unknown"
            agency_counts[ag] += 1
            agency_value[ag] += w.get("award_amount", 0) or 0

        agency_presence = [
            {
                "agency": ag,
                "win_count": agency_counts[ag],
                "total_value": round(agency_value[ag], 2),
            }
            for ag in sorted(agency_counts,
                             key=lambda x: agency_counts[x], reverse=True)
        ]

        # NAICS focus
        naics_counts = defaultdict(int)
        naics_value = defaultdict(float)
        for w in wins_list:
            nc = w.get("naics_code") or "Unknown"
            naics_counts[nc] += 1
            naics_value[nc] += w.get("award_amount", 0) or 0

        naics_focus = [
            {
                "naics_code": nc,
                "win_count": naics_counts[nc],
                "total_value": round(naics_value[nc], 2),
            }
            for nc in sorted(naics_counts,
                             key=lambda x: naics_counts[x], reverse=True)
        ]

        # Pricing patterns
        amounts = [
            w.get("award_amount", 0) for w in wins_list
            if w.get("award_amount")
        ]
        pricing = {}
        if amounts:
            s = sorted(amounts)
            pricing = {
                "average_award": round(_safe_divide(sum(s), len(s)), 2),
                "median_award": round(
                    s[len(s) // 2] if len(s) % 2 == 1
                    else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2,
                    2,
                ),
                "min_award": round(s[0], 2),
                "max_award": round(s[-1], 2),
                "total_value": round(total_value, 2),
            }

        # Teaming patterns (from debriefs where this competitor won)
        teaming = []
        debrief_rows = conn.execute(
            "SELECT winning_contractor, proposal_id FROM debriefs "
            "WHERE winning_contractor = ?",
            (comp["company_name"],),
        ).fetchall()
        if debrief_rows:
            teaming.append({
                "context": "Won against us",
                "count": len(debrief_rows),
            })

        return {
            "competitor": {
                "id": comp["id"],
                "company_name": comp["company_name"],
                "cage_code": comp.get("cage_code"),
                "strengths": comp.get("strengths"),
                "weaknesses": comp.get("weaknesses"),
                "naics_codes": _parse_json_field(comp.get("naics_codes")),
                "contract_vehicles": _parse_json_field(
                    comp.get("contract_vehicles")
                ),
            },
            "win_history": {
                "total_wins": len(wins_list),
                "total_value": round(total_value, 2),
                "recent_wins": wins_list[:10],
            },
            "agency_presence": agency_presence[:10],
            "naics_focus": naics_focus[:10],
            "pricing_patterns": pricing,
            "teaming_patterns": teaming,
        }
    finally:
        conn.close()


def head_to_head(competitor_id, db_path=None):
    """Compare our company against a specific competitor.

    Analyzes: wins in the same NAICS/agency space, pricing comparison,
    and capability overlap.

    Args:
        competitor_id: The competitor ID to compare against.
        db_path: Optional database path override.

    Returns:
        dict with head-to-head comparison.

    Raises:
        ValueError: If competitor not found.
    """
    conn = _get_db(db_path)
    try:
        comp_row = conn.execute(
            "SELECT * FROM competitors WHERE id = ?", (competitor_id,)
        ).fetchone()
        if comp_row is None:
            raise ValueError(f"Competitor not found: {competitor_id}")

        comp = _row_to_dict(comp_row)
        company_name = comp["company_name"]

        # Our debriefs where this competitor won or we competed
        direct_losses = conn.execute(
            "SELECT d.*, o.agency, o.naics_code, o.title AS opp_title "
            "FROM debriefs d "
            "LEFT JOIN opportunities o ON d.opportunity_id = o.id "
            "WHERE d.winning_contractor = ? AND d.result = 'loss'",
            (company_name,),
        ).fetchall()

        direct_wins = conn.execute(
            "SELECT d.*, o.agency, o.naics_code, o.title AS opp_title "
            "FROM debriefs d "
            "LEFT JOIN opportunities o ON d.opportunity_id = o.id "
            "WHERE d.result = 'win' AND o.naics_code IN "
            "(SELECT DISTINCT naics_code FROM competitor_wins "
            " WHERE competitor_id = ?)",
            (competitor_id,),
        ).fetchall()

        # Pricing comparison
        their_prices = conn.execute(
            "SELECT AVG(award_amount) as avg_amount "
            "FROM competitor_wins WHERE competitor_id = ? "
            "AND award_amount IS NOT NULL",
            (competitor_id,),
        ).fetchone()

        our_prices = conn.execute(
            "SELECT AVG(d.evaluated_price) as avg_price "
            "FROM debriefs d WHERE d.evaluated_price IS NOT NULL"
        ).fetchone()

        # Capability overlap
        comp_naics = _parse_json_field(comp.get("naics_codes")) or []
        our_past_naics = conn.execute(
            "SELECT DISTINCT naics_code FROM past_performances "
            "WHERE is_active = 1 AND naics_code IS NOT NULL"
        ).fetchall()
        our_naics = [r["naics_code"] for r in our_past_naics]

        overlap_naics = (
            list(set(comp_naics) & set(our_naics))
            if isinstance(comp_naics, list) else []
        )

        return {
            "competitor": {
                "id": comp["id"],
                "company_name": company_name,
            },
            "direct_matchups": {
                "we_lost_to_them": len(direct_losses),
                "we_won_in_their_space": len(direct_wins),
                "losses_detail": [
                    {
                        "opportunity": _row_to_dict(d).get("opp_title"),
                        "agency": _row_to_dict(d).get("agency"),
                        "naics": _row_to_dict(d).get("naics_code"),
                    }
                    for d in direct_losses[:5]
                ],
            },
            "pricing_comparison": {
                "their_avg_award": round(
                    their_prices["avg_amount"], 2
                ) if their_prices and their_prices["avg_amount"] else None,
                "our_avg_bid": round(
                    our_prices["avg_price"], 2
                ) if our_prices and our_prices["avg_price"] else None,
            },
            "capability_overlap": {
                "shared_naics": overlap_naics,
                "overlap_count": len(overlap_naics),
                "their_naics_count": (
                    len(comp_naics) if isinstance(comp_naics, list) else 0
                ),
                "our_naics_count": len(our_naics),
            },
            "competitive_assessment": _assess_competitive_position(
                losses=len(direct_losses),
                wins=len(direct_wins),
                overlap=len(overlap_naics),
            ),
        }
    finally:
        conn.close()


def _assess_competitive_position(losses, wins, overlap):
    """Generate a competitive position assessment.

    Args:
        losses: Number of direct losses to this competitor.
        wins: Number of wins in the competitor's space.
        overlap: Number of overlapping NAICS codes.

    Returns:
        dict with assessment rating and narrative.
    """
    if losses > wins and losses > 2:
        rating = "disadvantaged"
        narrative = (
            "This competitor has beaten us multiple times. "
            "Consider teaming, differentiation, or niche targeting."
        )
    elif wins > losses:
        rating = "advantaged"
        narrative = (
            "We have a stronger track record in shared spaces. "
            "Maintain current approach and continue building incumbency."
        )
    elif overlap > 3:
        rating = "highly_competitive"
        narrative = (
            "Significant market overlap with frequent competition. "
            "Invest in competitive intelligence and discriminators."
        )
    else:
        rating = "neutral"
        narrative = (
            "Limited direct competition history. "
            "Monitor this competitor for emerging overlap."
        )

    return {"rating": rating, "narrative": narrative}


def market_landscape(naics_code, agency=None, db_path=None):
    """Generate market landscape analysis for a NAICS code.

    Identifies top competitors by contract wins, estimates market share,
    and provides trend analysis for the specified NAICS/agency space.

    Args:
        naics_code: NAICS code to analyze.
        agency: Optional agency name filter.
        db_path: Optional database path override.

    Returns:
        dict with market landscape including top competitors and trends.
    """
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT competitor_name, competitor_id, "
            "COUNT(*) as win_count, "
            "SUM(award_amount) as total_value, "
            "AVG(award_amount) as avg_value "
            "FROM competitor_wins "
            "WHERE naics_code = ? "
        )
        params = [naics_code]
        if agency:
            query += "AND agency = ? "
            params.append(agency)

        query += (
            "GROUP BY competitor_name "
            "ORDER BY total_value DESC"
        )

        rows = conn.execute(query, params).fetchall()

        # Calculate total market (from our data)
        total_market_value = sum(
            (r["total_value"] or 0) for r in rows
        )

        top_competitors = []
        for r in rows:
            tv = r["total_value"] or 0
            share = _safe_divide(tv, total_market_value) * 100
            top_competitors.append({
                "company_name": r["competitor_name"],
                "competitor_id": r["competitor_id"],
                "win_count": r["win_count"],
                "total_value": round(tv, 2),
                "average_value": round(r["avg_value"] or 0, 2),
                "estimated_share_pct": round(share, 1),
            })

        # Our position in this NAICS
        our_wins = conn.execute(
            "SELECT COUNT(*) as cnt FROM debriefs d "
            "LEFT JOIN opportunities o ON d.opportunity_id = o.id "
            "WHERE d.result = 'win' AND o.naics_code = ?",
            (naics_code,),
        ).fetchone()

        our_losses = conn.execute(
            "SELECT COUNT(*) as cnt FROM debriefs d "
            "LEFT JOIN opportunities o ON d.opportunity_id = o.id "
            "WHERE d.result = 'loss' AND o.naics_code = ?",
            (naics_code,),
        ).fetchone()

        our_bids = (our_wins["cnt"] or 0) + (our_losses["cnt"] or 0)
        our_win_rate = _safe_divide(our_wins["cnt"] or 0, our_bids)

        # Trend: recent vs older wins
        recent_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM competitor_wins "
            "WHERE naics_code = ? AND award_date >= date('now', '-180 days')",
            (naics_code,),
        ).fetchone()["cnt"]

        older_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM competitor_wins "
            "WHERE naics_code = ? AND award_date < date('now', '-180 days')",
            (naics_code,),
        ).fetchone()["cnt"]

        trend = "stable"
        if recent_count > older_count * 1.2:
            trend = "growing"
        elif recent_count < older_count * 0.8:
            trend = "declining"

        return {
            "naics_code": naics_code,
            "agency": agency,
            "total_competitors": len(top_competitors),
            "total_market_value_tracked": round(total_market_value, 2),
            "top_competitors": top_competitors[:20],
            "our_position": {
                "total_bids": our_bids,
                "wins": our_wins["cnt"] or 0,
                "losses": our_losses["cnt"] or 0,
                "win_rate": round(our_win_rate, 3),
            },
            "market_trend": trend,
            "trend_data": {
                "recent_awards_180d": recent_count,
                "older_awards": older_count,
            },
        }
    finally:
        conn.close()


def list_competitors(limit=20, db_path=None):
    """List registered competitors.

    Args:
        limit: Maximum number of competitors to return (default 20).
        db_path: Optional database path override.

    Returns:
        list of dicts with competitor records.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM competitor_wins "
            " WHERE competitor_id = c.id) as tracked_wins "
            "FROM competitors c "
            "WHERE c.is_active = 1 "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_competitor(competitor_id, db_path=None):
    """Get a single competitor by ID.

    Args:
        competitor_id: The competitor ID.
        db_path: Optional database path override.

    Returns:
        dict with competitor fields, or None if not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM competitor_wins "
            " WHERE competitor_id = c.id) as tracked_wins "
            "FROM competitors c WHERE c.id = ?",
            (competitor_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="GovProposal Competitive Intelligence Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --add --company-name 'Acme Corp' "
            "--naics-codes '541512,541511' --json\n"
            "  %(prog)s --track --competitor-id COMP-001 --agency 'DoD' "
            "--json\n"
            "  %(prog)s --analyze --competitor-id COMP-001 --json\n"
            "  %(prog)s --head-to-head --competitor-id COMP-001 --json\n"
            "  %(prog)s --landscape --naics 541512 --json\n"
            "  %(prog)s --list --limit 10 --json\n"
            "  %(prog)s --get --competitor-id COMP-001 --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--add", action="store_true",
                        help="Register a new competitor")
    action.add_argument("--track", action="store_true",
                        help="Track competitor contract wins")
    action.add_argument("--analyze", action="store_true",
                        help="Generate competitive profile")
    action.add_argument("--head-to-head", action="store_true",
                        help="Head-to-head comparison")
    action.add_argument("--landscape", action="store_true",
                        help="Market landscape analysis")
    action.add_argument("--list", action="store_true",
                        help="List competitors")
    action.add_argument("--get", action="store_true",
                        help="Get competitor details")

    parser.add_argument("--competitor-id", help="Competitor ID")
    parser.add_argument("--company-name", help="Company name for --add")
    parser.add_argument("--cage-code", help="CAGE code")
    parser.add_argument("--duns-number", help="DUNS/UEI number")
    parser.add_argument("--website", help="Company website")
    parser.add_argument("--naics-codes", help="Comma-separated NAICS codes")
    parser.add_argument("--naics", help="NAICS code filter")
    parser.add_argument("--capabilities", help="Capabilities description")
    parser.add_argument("--contract-vehicles",
                        help="Comma-separated contract vehicles")
    parser.add_argument("--strengths", help="Known strengths")
    parser.add_argument("--weaknesses", help="Known weaknesses")
    parser.add_argument("--agency", help="Agency name filter")
    parser.add_argument("--days-back", type=int, default=365,
                        help="Days to look back (default: 365)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max results (default: 20)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.add:
            if not args.company_name:
                parser.error("--add requires --company-name")
            kwargs = {}
            if args.cage_code:
                kwargs["cage_code"] = args.cage_code
            if args.duns_number:
                kwargs["duns_number"] = args.duns_number
            if args.website:
                kwargs["website"] = args.website
            if args.naics_codes:
                kwargs["naics_codes"] = args.naics_codes
            if args.capabilities:
                kwargs["capabilities"] = args.capabilities
            if args.contract_vehicles:
                kwargs["contract_vehicles"] = args.contract_vehicles
            if args.strengths:
                kwargs["strengths"] = args.strengths
            if args.weaknesses:
                kwargs["weaknesses"] = args.weaknesses
            result = add_competitor(
                company_name=args.company_name,
                db_path=db,
                **kwargs,
            )

        elif args.track:
            result = track_wins(
                competitor_id=args.competitor_id,
                agency=args.agency,
                naics=args.naics,
                days_back=args.days_back,
                db_path=db,
            )

        elif args.analyze:
            if not args.competitor_id:
                parser.error("--analyze requires --competitor-id")
            result = analyze_competitor(args.competitor_id, db_path=db)

        elif args.head_to_head:
            if not args.competitor_id:
                parser.error("--head-to-head requires --competitor-id")
            result = head_to_head(args.competitor_id, db_path=db)

        elif args.landscape:
            if not args.naics:
                parser.error("--landscape requires --naics")
            result = market_landscape(
                naics_code=args.naics,
                agency=args.agency,
                db_path=db,
            )

        elif args.list:
            result = list_competitors(limit=args.limit, db_path=db)

        elif args.get:
            if not args.competitor_id:
                parser.error("--get requires --competitor-id")
            result = get_competitor(args.competitor_id, db_path=db)
            if result is None:
                result = {"error": f"Competitor not found: {args.competitor_id}"}

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} competitors:")
                for c in result:
                    tracked = c.get("tracked_wins", 0)
                    print(
                        f"  [{c.get('id')}] {c.get('company_name')} "
                        f"({tracked} tracked wins)"
                    )
            elif isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, (dict, list)):
                        print(
                            f"  {key}: {json.dumps(value, default=str)}"
                        )
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
    import argparse
    main()
