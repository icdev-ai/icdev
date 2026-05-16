#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Customer Intelligence Gathering for GovProposal.

Builds and maintains customer profiles with agency-level intelligence
drawn from internal data: opportunities, proposals, competitor wins,
past performances, and CRM contacts.  Provides deep agency analysis,
spending pattern detection, and relationship mapping to inform capture
strategy.

Usage:
    python tools/capture/customer_intel.py --create --agency "DIA" --json
    python tools/capture/customer_intel.py --create --agency "DIA" --sub-agency "J6" --office "CIO" --json
    python tools/capture/customer_intel.py --update --profile-id "CP-abc" --field strategic_priorities --value '["cloud","AI"]' --json
    python tools/capture/customer_intel.py --get --agency "DIA" --json
    python tools/capture/customer_intel.py --list --json
    python tools/capture/customer_intel.py --analyze --agency "DIA" --json
    python tools/capture/customer_intel.py --spending --json
    python tools/capture/customer_intel.py --spending --agency "DIA" --json
    python tools/capture/customer_intel.py --relationships --agency "DIA" --json
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

try:
    import requests  # noqa: F401
except ImportError:
    requests = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cust_id():
    """Generate a customer-profile ID: CP- followed by 12 hex characters."""
    return "CP-" + secrets.token_hex(6)


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
            "customer_intel",
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
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return json.dumps(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        value = [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value)
    return json.dumps([str(value)])


# JSON fields on customer_profiles that need parse/serialize treatment
_JSON_FIELDS = (
    "strategic_priorities", "budget_trends", "key_personnel",
    "procurement_history", "preferred_approaches", "pain_points",
)


def _enrich_profile(row_dict):
    """Parse JSON fields in a profile dict."""
    if row_dict is None:
        return None
    for field in _JSON_FIELDS:
        row_dict[field] = _parse_json_field(row_dict.get(field))
    return row_dict


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def create_profile(agency, sub_agency=None, office=None, mission=None,
                   db_path=None):
    """Create a new customer profile or return existing one for the agency.

    If a profile already exists for the given agency (case-insensitive),
    the existing profile is returned instead of creating a duplicate.

    Args:
        agency: Federal agency name (e.g. 'DIA', 'DoD CIO').
        sub_agency: Sub-agency or directorate.
        office: Specific office within the agency.
        mission: Mission statement text.
        db_path: Optional database path override.

    Returns:
        dict of the created or existing profile.
    """
    conn = _get_db(db_path)
    try:
        # Check for existing profile
        existing = conn.execute(
            "SELECT * FROM customer_profiles WHERE LOWER(agency) = LOWER(?)",
            (agency,),
        ).fetchone()
        if existing:
            result = _enrich_profile(_row_to_dict(existing))
            result["_note"] = "existing_profile"
            return result

        profile_id = _cust_id()
        now = _now()
        conn.execute(
            "INSERT INTO customer_profiles "
            "(id, agency, sub_agency, office, mission_statement, "
            " classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id, agency, sub_agency, office, mission,
                "CUI // SP-PROPIN", now, now,
            ),
        )

        _audit(conn, "capture.customer_create",
               f"Created customer profile for {agency}",
               "customer_profiles", profile_id,
               {"agency": agency, "sub_agency": sub_agency})
        conn.commit()

        return {
            "id": profile_id,
            "agency": agency,
            "sub_agency": sub_agency,
            "office": office,
            "mission_statement": mission,
            "strategic_priorities": None,
            "budget_trends": None,
            "key_personnel": None,
            "procurement_history": None,
            "preferred_approaches": None,
            "pain_points": None,
            "notes": None,
            "classification": "CUI // SP-PROPIN",
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


def update_profile(profile_id, updates_dict, db_path=None):
    """Update fields on a customer profile.

    JSON fields (strategic_priorities, budget_trends, key_personnel,
    procurement_history, preferred_approaches, pain_points) are serialized
    automatically.

    Args:
        profile_id: Profile ID (e.g. 'CP-abc123def456').
        updates_dict: Dict of field_name -> new_value.
        db_path: Optional database path override.

    Returns:
        dict of the updated profile.

    Raises:
        ValueError: If profile not found or no valid fields provided.
    """
    allowed_fields = {
        "agency", "sub_agency", "office", "mission_statement",
        "strategic_priorities", "budget_trends", "key_personnel",
        "procurement_history", "preferred_approaches", "pain_points",
        "notes",
    }
    filtered = {k: v for k, v in updates_dict.items() if k in allowed_fields}
    if not filtered:
        raise ValueError(
            f"No valid fields to update. Allowed: {sorted(allowed_fields)}"
        )

    conn = _get_db(db_path)
    try:
        existing = conn.execute(
            "SELECT * FROM customer_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Customer profile not found: {profile_id}")

        # Serialize JSON fields
        for field in _JSON_FIELDS:
            if field in filtered:
                filtered[field] = _serialize_list(filtered[field])

        set_clauses = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values())
        now = _now()
        values.append(now)
        values.append(profile_id)

        conn.execute(
            f"UPDATE customer_profiles SET {set_clauses}, updated_at = ? "
            f"WHERE id = ?",
            values,
        )
        _audit(conn, "capture.customer_update",
               f"Updated profile {profile_id}: {list(filtered.keys())}",
               "customer_profiles", profile_id,
               {"fields_updated": list(filtered.keys())})
        conn.commit()

        row = conn.execute(
            "SELECT * FROM customer_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return _enrich_profile(_row_to_dict(row))
    finally:
        conn.close()


def get_profile(agency=None, profile_id=None, db_path=None):
    """Retrieve a customer profile enriched with computed stats.

    Computed fields from DB:
      - opportunity_count: number of opportunities from this agency
      - total_estimated_value: sum of estimated_value_high from opportunities
      - our_win_rate: win / (win + loss) from proposals
      - average_deal_size: avg competitor_wins award_amount at this agency

    Args:
        agency: Agency name to look up.
        profile_id: Profile ID to look up.
        db_path: Optional database path override.

    Returns:
        dict with profile fields and computed stats, or None if not found.

    Raises:
        ValueError: If neither agency nor profile_id provided.
    """
    if not agency and not profile_id:
        raise ValueError("Must provide --agency or --profile-id")

    conn = _get_db(db_path)
    try:
        if profile_id:
            row = conn.execute(
                "SELECT * FROM customer_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM customer_profiles "
                "WHERE LOWER(agency) = LOWER(?) LIMIT 1",
                (agency,),
            ).fetchone()

        if row is None:
            return None

        profile = _enrich_profile(_row_to_dict(row))
        ag = profile["agency"]

        # Opportunity stats
        opp_row = conn.execute(
            "SELECT COUNT(*) AS cnt, "
            "COALESCE(SUM(estimated_value_high), 0) AS total_val "
            "FROM opportunities WHERE LOWER(agency) = LOWER(?)",
            (ag,),
        ).fetchone()
        profile["opportunity_count"] = opp_row["cnt"]
        profile["total_estimated_value"] = opp_row["total_val"]

        # Win rate from proposals linked to this agency's opportunities
        wr = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN p.result = 'win' THEN 1 ELSE 0 END) AS wins, "
            "  SUM(CASE WHEN p.result = 'loss' THEN 1 ELSE 0 END) AS losses "
            "FROM proposals p "
            "JOIN opportunities o ON p.opportunity_id = o.id "
            "WHERE LOWER(o.agency) = LOWER(?) AND p.result IS NOT NULL",
            (ag,),
        ).fetchone()
        wins = wr["wins"] or 0
        losses = wr["losses"] or 0
        total_decided = wins + losses
        profile["our_win_rate"] = (
            round(wins / total_decided, 3) if total_decided > 0 else None
        )
        profile["our_wins"] = wins
        profile["our_losses"] = losses

        # Average deal size from competitor wins at this agency
        avg_row = conn.execute(
            "SELECT AVG(award_amount) AS avg_deal, "
            "COUNT(*) AS award_count "
            "FROM competitor_wins "
            "WHERE LOWER(agency) = LOWER(?) AND award_amount > 0",
            (ag,),
        ).fetchone()
        profile["average_deal_size"] = (
            round(avg_row["avg_deal"], 2) if avg_row["avg_deal"] else None
        )
        profile["total_tracked_awards"] = avg_row["award_count"]

        return profile
    finally:
        conn.close()


def list_profiles(db_path=None):
    """List all customer profiles with summary statistics.

    Args:
        db_path: Optional database path override.

    Returns:
        list of profile summary dicts.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM customer_profiles ORDER BY agency"
        ).fetchall()

        results = []
        for r in rows:
            profile = _enrich_profile(_row_to_dict(r))
            ag = profile["agency"]

            opp_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM opportunities "
                "WHERE LOWER(agency) = LOWER(?)",
                (ag,),
            ).fetchone()
            profile["opportunity_count"] = opp_row["cnt"]

            wr = conn.execute(
                "SELECT "
                "  SUM(CASE WHEN p.result = 'win' THEN 1 ELSE 0 END) AS w, "
                "  SUM(CASE WHEN p.result = 'loss' THEN 1 ELSE 0 END) AS l "
                "FROM proposals p "
                "JOIN opportunities o ON p.opportunity_id = o.id "
                "WHERE LOWER(o.agency) = LOWER(?) AND p.result IS NOT NULL",
                (ag,),
            ).fetchone()
            w = wr["w"] or 0
            l_ = wr["l"] or 0
            decided = w + l_
            profile["our_win_rate"] = (
                round(w / decided, 3) if decided > 0 else None
            )

            results.append(profile)
        return results
    finally:
        conn.close()


def analyze_agency(agency, db_path=None):
    """Deep agency analysis from internal data.

    Returns a comprehensive intelligence report covering:
      - Opportunities: count, total estimated value, NAICS distribution,
        set-aside preferences
      - Our history: proposals submitted, win/loss record, total awarded
      - Competitor presence: top competitors winning at this agency
      - Past performance: our CPARS ratings at this agency
      - Procurement patterns: average deal size, contract types, timing

    Args:
        agency: Agency name to analyze.
        db_path: Optional database path override.

    Returns:
        dict with detailed intelligence report.
    """
    conn = _get_db(db_path)
    try:
        report = {"agency": agency, "generated_at": _now()}

        # --- Opportunity landscape ---
        opp_rows = conn.execute(
            "SELECT naics_code, set_aside_type, contract_type, "
            "estimated_value_high, posted_date, response_deadline, status "
            "FROM opportunities WHERE LOWER(agency) = LOWER(?)",
            (agency,),
        ).fetchall()

        naics_dist = defaultdict(int)
        set_aside_dist = defaultdict(int)
        contract_type_dist = defaultdict(int)
        total_est_value = 0.0
        months = defaultdict(int)

        for o in opp_rows:
            od = _row_to_dict(o)
            if od["naics_code"]:
                naics_dist[od["naics_code"]] += 1
            if od["set_aside_type"]:
                set_aside_dist[od["set_aside_type"]] += 1
            if od["contract_type"]:
                contract_type_dist[od["contract_type"]] += 1
            total_est_value += od["estimated_value_high"] or 0.0
            if od["posted_date"]:
                month = od["posted_date"][:7]  # YYYY-MM
                months[month] += 1

        report["opportunities"] = {
            "count": len(opp_rows),
            "total_estimated_value": round(total_est_value, 2),
            "naics_distribution": dict(
                sorted(naics_dist.items(), key=lambda x: x[1], reverse=True)
            ),
            "set_aside_preferences": dict(
                sorted(set_aside_dist.items(), key=lambda x: x[1],
                       reverse=True)
            ),
            "contract_types": dict(contract_type_dist),
            "posting_months": dict(
                sorted(months.items())[-12:]
            ) if months else {},
        }

        # --- Our proposal history ---
        prop_rows = conn.execute(
            "SELECT p.id, p.status, p.result, o.estimated_value_high "
            "FROM proposals p "
            "JOIN opportunities o ON p.opportunity_id = o.id "
            "WHERE LOWER(o.agency) = LOWER(?)",
            (agency,),
        ).fetchall()

        wins = sum(1 for p in prop_rows if p["result"] == "win")
        losses = sum(1 for p in prop_rows if p["result"] == "loss")
        decided = wins + losses
        awarded_value = sum(
            (p["estimated_value_high"] or 0)
            for p in prop_rows if p["result"] == "win"
        )

        report["our_history"] = {
            "proposals_submitted": len(prop_rows),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / decided, 3) if decided > 0 else None,
            "total_awarded_value": round(awarded_value, 2),
        }

        # --- Competitor presence ---
        comp_rows = conn.execute(
            "SELECT competitor_name, COUNT(*) AS win_count, "
            "SUM(award_amount) AS total_awarded, "
            "AVG(award_amount) AS avg_award "
            "FROM competitor_wins "
            "WHERE LOWER(agency) = LOWER(?) "
            "GROUP BY competitor_name "
            "ORDER BY win_count DESC LIMIT 10",
            (agency,),
        ).fetchall()

        report["competitor_presence"] = [
            {
                "competitor": c["competitor_name"],
                "win_count": c["win_count"],
                "total_awarded": round(c["total_awarded"] or 0, 2),
                "avg_award": round(c["avg_award"] or 0, 2),
            }
            for c in comp_rows
        ]

        # --- Past performance at agency ---
        pp_rows = conn.execute(
            "SELECT contract_name, contract_value, cpars_rating, "
            "role, period_of_performance_start, period_of_performance_end "
            "FROM past_performances "
            "WHERE LOWER(agency) = LOWER(?) AND is_active = 1 "
            "ORDER BY period_of_performance_end DESC",
            (agency,),
        ).fetchall()

        cpars_counts = defaultdict(int)
        for pp in pp_rows:
            if pp["cpars_rating"]:
                cpars_counts[pp["cpars_rating"]] += 1

        report["past_performance"] = {
            "contract_count": len(pp_rows),
            "cpars_distribution": dict(cpars_counts),
            "contracts": [
                {
                    "name": pp["contract_name"],
                    "value": pp["contract_value"],
                    "rating": pp["cpars_rating"],
                    "role": pp["role"],
                    "pop_end": pp["period_of_performance_end"],
                }
                for pp in pp_rows[:10]
            ],
        }

        # --- Procurement patterns ---
        cw_rows = conn.execute(
            "SELECT award_amount, naics_code, contract_type, "
            "set_aside_type, award_date "
            "FROM competitor_wins "
            "WHERE LOWER(agency) = LOWER(?) AND award_amount > 0",
            (agency,),
        ).fetchall()

        cw_naics = defaultdict(float)
        cw_years = defaultdict(float)
        award_amounts = []
        for cw in cw_rows:
            d = _row_to_dict(cw)
            amt = d["award_amount"] or 0
            award_amounts.append(amt)
            if d["naics_code"]:
                cw_naics[d["naics_code"]] += amt
            if d["award_date"]:
                year = d["award_date"][:4]
                cw_years[year] += amt

        avg_deal = (
            round(sum(award_amounts) / len(award_amounts), 2)
            if award_amounts else None
        )

        report["procurement_patterns"] = {
            "total_tracked_awards": len(cw_rows),
            "average_deal_size": avg_deal,
            "spending_by_naics": {
                k: round(v, 2)
                for k, v in sorted(
                    cw_naics.items(), key=lambda x: x[1], reverse=True
                )[:10]
            },
            "spending_by_year": {
                k: round(v, 2) for k, v in sorted(cw_years.items())
            },
        }

        # Load profile if exists
        prof_row = conn.execute(
            "SELECT * FROM customer_profiles "
            "WHERE LOWER(agency) = LOWER(?) LIMIT 1",
            (agency,),
        ).fetchone()
        if prof_row:
            profile = _enrich_profile(_row_to_dict(prof_row))
            report["profile_id"] = profile["id"]
            report["mission_statement"] = profile.get("mission_statement")
            report["strategic_priorities"] = profile.get(
                "strategic_priorities"
            )
            report["pain_points"] = profile.get("pain_points")

        _audit(conn, "capture.customer_analyze",
               f"Agency analysis for {agency}",
               "customer_profiles", agency,
               {"opp_count": len(opp_rows),
                "competitors_tracked": len(comp_rows)})
        conn.commit()

        return report
    finally:
        conn.close()


def spending_analysis(agency=None, db_path=None):
    """Analyze spending patterns from competitor wins data.

    When agency is provided, scopes to that agency.  Otherwise analyzes
    across all agencies.

    Returns:
      - Total spending by NAICS code
      - Year-over-year trends
      - Growing/shrinking program areas
      - Average award sizes by contract type
      - Set-aside utilization rates

    Args:
        agency: Optional agency name to scope analysis.
        db_path: Optional database path override.

    Returns:
        dict with spending analysis.
    """
    conn = _get_db(db_path)
    try:
        where = ""
        params = []
        if agency:
            where = "WHERE LOWER(agency) = LOWER(?)"
            params = [agency]

        rows = conn.execute(
            f"SELECT competitor_name, agency, award_amount, naics_code, "
            f"contract_type, set_aside_type, award_date "
            f"FROM competitor_wins {where}",
            params,
        ).fetchall()

        by_naics = defaultdict(float)
        by_year = defaultdict(float)
        by_contract_type = defaultdict(lambda: {"total": 0.0, "count": 0})
        by_set_aside = defaultdict(int)
        by_naics_year = defaultdict(lambda: defaultdict(float))
        total_awards = 0

        for r in rows:
            d = _row_to_dict(r)
            amt = d["award_amount"] or 0
            total_awards += 1

            naics = d["naics_code"] or "unknown"
            by_naics[naics] += amt

            year = (d["award_date"] or "")[:4]
            if year:
                by_year[year] += amt
                by_naics_year[naics][year] += amt

            ct = d["contract_type"] or "unknown"
            by_contract_type[ct]["total"] += amt
            by_contract_type[ct]["count"] += 1

            sa = d["set_aside_type"] or "full_and_open"
            by_set_aside[sa] += 1

        # Identify growing/shrinking areas (compare last 2 years)
        sorted_years = sorted(by_year.keys())
        trends = {}
        if len(sorted_years) >= 2:
            prev_year = sorted_years[-2]
            curr_year = sorted_years[-1]
            all_naics = set(by_naics_year.keys())
            for nc in all_naics:
                prev_val = by_naics_year[nc].get(prev_year, 0)
                curr_val = by_naics_year[nc].get(curr_year, 0)
                if prev_val > 0:
                    pct_change = round(
                        ((curr_val - prev_val) / prev_val) * 100, 1
                    )
                elif curr_val > 0:
                    pct_change = 100.0
                else:
                    pct_change = 0.0
                trends[nc] = {
                    f"{prev_year}_spend": round(prev_val, 2),
                    f"{curr_year}_spend": round(curr_val, 2),
                    "pct_change": pct_change,
                    "direction": (
                        "growing" if pct_change > 10
                        else "shrinking" if pct_change < -10
                        else "stable"
                    ),
                }

        # Average award by contract type
        avg_by_ct = {}
        for ct, data in by_contract_type.items():
            if data["count"] > 0:
                avg_by_ct[ct] = {
                    "average_award": round(
                        data["total"] / data["count"], 2
                    ),
                    "total_spend": round(data["total"], 2),
                    "award_count": data["count"],
                }

        # Set-aside rates
        sa_total = sum(by_set_aside.values()) or 1
        set_aside_rates = {
            sa: {
                "count": cnt,
                "pct": round(cnt / sa_total * 100, 1),
            }
            for sa, cnt in sorted(
                by_set_aside.items(), key=lambda x: x[1], reverse=True
            )
        }

        result = {
            "scope": agency or "all_agencies",
            "total_tracked_awards": total_awards,
            "spending_by_naics": {
                k: round(v, 2)
                for k, v in sorted(
                    by_naics.items(), key=lambda x: x[1], reverse=True
                )[:20]
            },
            "spending_by_year": {
                k: round(v, 2) for k, v in sorted(by_year.items())
            },
            "naics_trends": dict(
                sorted(trends.items(),
                       key=lambda x: abs(x[1]["pct_change"]),
                       reverse=True)[:15]
            ) if trends else {},
            "average_by_contract_type": avg_by_ct,
            "set_aside_utilization": set_aside_rates,
            "generated_at": _now(),
        }

        _audit(conn, "capture.spending_analysis",
               f"Spending analysis: {agency or 'all agencies'}",
               "customer_profiles", agency,
               {"total_awards": total_awards})
        conn.commit()

        return result
    finally:
        conn.close()


def relationship_map(agency, db_path=None):
    """Map our relationships with an agency.

    Gathers:
      - Key personnel from customer profile
      - CRM contacts at this agency
      - Past proposals and their outcomes
      - Past performance contracts
      - Teaming partners used with this agency

    Args:
        agency: Agency name to map.
        db_path: Optional database path override.

    Returns:
        dict with relationship intelligence.
    """
    conn = _get_db(db_path)
    try:
        result = {"agency": agency, "generated_at": _now()}

        # Key personnel from profile
        prof_row = conn.execute(
            "SELECT * FROM customer_profiles "
            "WHERE LOWER(agency) = LOWER(?) LIMIT 1",
            (agency,),
        ).fetchone()
        if prof_row:
            profile = _enrich_profile(_row_to_dict(prof_row))
            result["profile_id"] = profile["id"]
            result["key_personnel"] = profile.get("key_personnel") or []
        else:
            result["profile_id"] = None
            result["key_personnel"] = []

        # CRM contacts at this agency
        contacts = []
        try:
            contact_rows = conn.execute(
                "SELECT c.id, c.full_name, c.title, c.email, c.company, "
                "c.last_contact_date, c.status, rt.type_name "
                "FROM contacts c "
                "LEFT JOIN relationship_types rt "
                "  ON c.relationship_type_id = rt.id "
                "WHERE LOWER(c.agency) = LOWER(?) AND c.status = 'active' "
                "ORDER BY c.last_contact_date DESC NULLS LAST",
                (agency,),
            ).fetchall()
            contacts = [_row_to_dict(r) for r in contact_rows]
        except sqlite3.OperationalError:
            # contacts table may not exist if CRM migration not run
            pass
        result["crm_contacts"] = contacts

        # Past proposals and outcomes
        prop_rows = conn.execute(
            "SELECT p.id, p.title, p.status, p.result, p.result_details, "
            "o.title AS opp_title, o.naics_code, o.estimated_value_high, "
            "o.response_deadline "
            "FROM proposals p "
            "JOIN opportunities o ON p.opportunity_id = o.id "
            "WHERE LOWER(o.agency) = LOWER(?) "
            "ORDER BY o.response_deadline DESC NULLS LAST",
            (agency,),
        ).fetchall()
        result["proposals"] = [_row_to_dict(r) for r in prop_rows[:15]]

        # Past performance contracts
        pp_rows = conn.execute(
            "SELECT id, contract_name, contract_number, contract_value, "
            "cpars_rating, role, period_of_performance_end "
            "FROM past_performances "
            "WHERE LOWER(agency) = LOWER(?) AND is_active = 1 "
            "ORDER BY period_of_performance_end DESC NULLS LAST",
            (agency,),
        ).fetchall()
        result["past_performance_contracts"] = [
            _row_to_dict(r) for r in pp_rows[:15]
        ]

        # Teaming partners used on proposals at this agency
        teaming = []
        try:
            team_rows = conn.execute(
                "SELECT DISTINCT tp.id, tp.company_name, "
                "tp.capabilities, tp.clearance_level, tp.relationship_score "
                "FROM teaming_partners tp "
                "WHERE tp.is_active = 1 "
                "AND tp.past_collaborations LIKE ?",
                (f"%{agency}%",),
            ).fetchall()
            teaming = [
                {
                    **_row_to_dict(r),
                    "capabilities": _parse_json_field(r["capabilities"]),
                }
                for r in team_rows
            ]
        except sqlite3.OperationalError:
            pass
        result["teaming_partners"] = teaming

        # Summary counts
        result["summary"] = {
            "key_personnel_count": len(result["key_personnel"]),
            "crm_contacts_count": len(contacts),
            "proposals_count": len(prop_rows),
            "past_performance_count": len(pp_rows),
            "teaming_partners_count": len(teaming),
        }

        _audit(conn, "capture.relationship_map",
               f"Relationship map for {agency}",
               "customer_profiles", agency,
               result["summary"])
        conn.commit()

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Customer Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --create --agency 'DIA' --json\n"
            "  %(prog)s --create --agency 'DIA' --sub-agency 'J6' "
            "--office 'CIO' --json\n"
            "  %(prog)s --update --profile-id 'CP-abc' "
            "--field strategic_priorities "
            "--value '[\"cloud\",\"AI\"]' --json\n"
            "  %(prog)s --get --agency 'DIA' --json\n"
            "  %(prog)s --list --json\n"
            "  %(prog)s --analyze --agency 'DIA' --json\n"
            "  %(prog)s --spending --json\n"
            "  %(prog)s --spending --agency 'DIA' --json\n"
            "  %(prog)s --relationships --agency 'DIA' --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true",
                        help="Create a customer profile")
    action.add_argument("--update", action="store_true",
                        help="Update a customer profile field")
    action.add_argument("--get", action="store_true",
                        help="Get a customer profile with computed stats")
    action.add_argument("--list", action="store_true",
                        help="List all customer profiles")
    action.add_argument("--analyze", action="store_true",
                        help="Deep agency analysis")
    action.add_argument("--spending", action="store_true",
                        help="Spending pattern analysis")
    action.add_argument("--relationships", action="store_true",
                        help="Relationship map for an agency")

    parser.add_argument("--agency", help="Agency name")
    parser.add_argument("--sub-agency", help="Sub-agency or directorate")
    parser.add_argument("--office", help="Office within agency")
    parser.add_argument("--mission", help="Mission statement text")
    parser.add_argument("--profile-id", help="Customer profile ID")
    parser.add_argument("--field",
                        help="Field name to update (for --update)")
    parser.add_argument("--value",
                        help="New value for field (for --update)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.create:
            if not args.agency:
                parser.error("--create requires --agency")
            result = create_profile(
                agency=args.agency,
                sub_agency=args.sub_agency,
                office=args.office,
                mission=args.mission,
                db_path=db,
            )

        elif args.update:
            if not args.profile_id:
                parser.error("--update requires --profile-id")
            if not args.field or args.value is None:
                parser.error("--update requires --field and --value")
            result = update_profile(
                profile_id=args.profile_id,
                updates_dict={args.field: args.value},
                db_path=db,
            )

        elif args.get:
            if not args.agency and not args.profile_id:
                parser.error("--get requires --agency or --profile-id")
            result = get_profile(
                agency=args.agency,
                profile_id=args.profile_id,
                db_path=db,
            )
            if result is None:
                result = {"error": "Profile not found"}

        elif args.list:
            result = list_profiles(db_path=db)

        elif args.analyze:
            if not args.agency:
                parser.error("--analyze requires --agency")
            result = analyze_agency(args.agency, db_path=db)

        elif args.spending:
            result = spending_analysis(agency=args.agency, db_path=db)

        elif args.relationships:
            if not args.agency:
                parser.error("--relationships requires --agency")
            result = relationship_map(args.agency, db_path=db)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} profile(s):")
                for p in result:
                    pid = p.get("id", "?")
                    ag = p.get("agency", "?")
                    opps = p.get("opportunity_count", 0)
                    wr = p.get("our_win_rate")
                    wr_str = f"{wr:.0%}" if wr is not None else "N/A"
                    print(f"  [{pid}] {ag}  opps={opps}  win_rate={wr_str}")
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
