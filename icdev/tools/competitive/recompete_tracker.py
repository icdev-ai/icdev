#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Recompete / Incumbent Intelligence Tracker for government contracts.

Tracks expiring government contracts, builds incumbent profiles, assesses
displacement difficulty, and generates win strategies.  Government contracts
expire and agencies typically recompete -- incumbents hold massive advantages
(institutional knowledge, existing staff, customer relationships, CPARS
history).  Challengers need to understand incumbent strengths/weaknesses and
find credible displacement paths.

Usage:
    python tools/competitive/recompete_tracker.py --create --incumbent "Booz Allen" --agency "DIA" --json
    python tools/competitive/recompete_tracker.py --update --recompete-id REC-abc --status rfp_released --json
    python tools/competitive/recompete_tracker.py --get --recompete-id REC-abc --json
    python tools/competitive/recompete_tracker.py --list --agency "DIA" --json
    python tools/competitive/recompete_tracker.py --assess --recompete-id REC-abc --json
    python tools/competitive/recompete_tracker.py --upcoming --days 180 --json
    python tools/competitive/recompete_tracker.py --incumbent-profile --incumbent "Booz Allen" --json
    python tools/competitive/recompete_tracker.py --win-strategy --recompete-id REC-abc --json
    python tools/competitive/recompete_tracker.py --dashboard --json
"""

import json
import os
import re
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

# Valid enum values -- kept in sync with init_db.py CHECK constraints.
FOLLOW_ON_TYPES = (
    "recompete", "follow_on", "bridge", "sole_source",
    "new_requirement", "unknown",
)
PERFORMANCE_RATINGS = (
    "exceptional", "very_good", "satisfactory",
    "marginal", "unsatisfactory", "unknown",
)
DIFFICULTY_LEVELS = (
    "easy", "moderate", "difficult", "very_difficult", "unknown",
)
STATUSES = (
    "monitoring", "pre_rfp", "rfp_released", "proposal_submitted",
    "awarded_us", "awarded_incumbent", "awarded_other", "cancelled",
)

# Weights for displacement scoring factors.
DISPLACEMENT_WEIGHTS = {
    "incumbent_performance": 0.30,
    "contract_value": 0.15,
    "years_of_incumbency": 0.15,
    "customer_relationship": 0.20,
    "our_past_performance": 0.20,
}

# Performance rating numeric map (higher = harder to displace).
PERFORMANCE_SCORES = {
    "exceptional": 1.0,
    "very_good": 0.8,
    "satisfactory": 0.5,
    "marginal": 0.2,
    "unsatisfactory": 0.05,
    "unknown": 0.5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recomp_id():
    """Generate a recompete tracking ID: REC- followed by 12 hex chars."""
    return "REC-" + secrets.token_hex(6)


def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None,
           details=None):
    """Write an append-only audit trail record."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "recompete_tracker",
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
    """Serialize a list or comma-separated string to JSON array."""
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


def _days_until(date_str):
    """Return days from today until a date string (YYYY-MM-DD).

    Returns None if the date string is missing or unparseable.
    """
    if not date_str:
        return None
    try:
        target = datetime.strptime(date_str[:10], "%Y-%m-%d")
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        target = target.replace(tzinfo=timezone.utc)
        return (target - today).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def create_recompete(incumbent_name, agency, contract_number=None,
                     naics_code=None, current_value=None, pop_end_date=None,
                     db_path=None, **kwargs):
    """Create a new recompete tracking record.

    Args:
        incumbent_name: Name of the current contract holder.
        agency: Government agency that owns the contract.
        contract_number: Contract/PIID number (optional).
        naics_code: Primary NAICS code (optional).
        current_value: Current contract value in dollars (optional).
        pop_end_date: Period-of-performance end date YYYY-MM-DD (optional).
        db_path: Optional database path override.
        **kwargs: Additional fields -- opportunity_id, incumbent_cage,
            recompete_date, follow_on_type, incumbent_performance,
            incumbent_strengths, incumbent_weaknesses, our_strategy,
            intelligence_sources, notes.

    Returns:
        dict with the created record.
    """
    rec_id = _recomp_id()
    now = _now()

    follow_on = kwargs.get("follow_on_type", "recompete")
    if follow_on not in FOLLOW_ON_TYPES:
        raise ValueError(
            f"Invalid follow_on_type '{follow_on}'. "
            f"Choose from: {', '.join(FOLLOW_ON_TYPES)}"
        )

    perf = kwargs.get("incumbent_performance", "unknown")
    if perf not in PERFORMANCE_RATINGS:
        raise ValueError(
            f"Invalid incumbent_performance '{perf}'. "
            f"Choose from: {', '.join(PERFORMANCE_RATINGS)}"
        )

    intel = kwargs.get("intelligence_sources")
    if intel and isinstance(intel, (list, tuple)):
        intel = json.dumps(list(intel))

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO recompete_tracking "
            "(id, opportunity_id, contract_number, incumbent_name, "
            "incumbent_cage, agency, naics_code, current_value, "
            "pop_end_date, recompete_date, follow_on_type, "
            "incumbent_performance, displacement_difficulty, "
            "incumbent_strengths, incumbent_weaknesses, our_strategy, "
            "intelligence_sources, status, notes, classification, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec_id,
                kwargs.get("opportunity_id"),
                contract_number,
                incumbent_name,
                kwargs.get("incumbent_cage"),
                agency,
                naics_code,
                current_value,
                pop_end_date,
                kwargs.get("recompete_date"),
                follow_on,
                perf,
                "unknown",
                kwargs.get("incumbent_strengths"),
                kwargs.get("incumbent_weaknesses"),
                kwargs.get("our_strategy"),
                intel,
                "monitoring",
                kwargs.get("notes"),
                "CUI // SP-PROPIN",
                now, now,
            ),
        )
        _audit(
            conn, "recompete.create",
            f"Created recompete tracker: {incumbent_name} @ {agency}",
            "recompete", rec_id,
            {"incumbent_name": incumbent_name, "agency": agency,
             "contract_number": contract_number},
        )
        conn.commit()

        return {
            "id": rec_id,
            "incumbent_name": incumbent_name,
            "agency": agency,
            "contract_number": contract_number,
            "naics_code": naics_code,
            "current_value": current_value,
            "pop_end_date": pop_end_date,
            "status": "monitoring",
            "created_at": now,
        }
    finally:
        conn.close()


def update_recompete(recompete_id, updates, db_path=None):
    """Update a recompete tracking record.

    Args:
        recompete_id: The REC-... identifier.
        updates: dict of field names to new values.
        db_path: Optional database path override.

    Returns:
        dict with the updated record.

    Raises:
        ValueError: If recompete_id not found or invalid field values.
    """
    allowed = {
        "opportunity_id", "contract_number", "incumbent_name",
        "incumbent_cage", "agency", "naics_code", "current_value",
        "pop_end_date", "recompete_date", "follow_on_type",
        "incumbent_performance", "displacement_difficulty",
        "incumbent_strengths", "incumbent_weaknesses", "our_strategy",
        "intelligence_sources", "status", "notes",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise ValueError("No valid fields provided for update")

    # Validate enum fields.
    if "follow_on_type" in filtered and filtered["follow_on_type"] not in FOLLOW_ON_TYPES:
        raise ValueError(f"Invalid follow_on_type: {filtered['follow_on_type']}")
    if "incumbent_performance" in filtered and filtered["incumbent_performance"] not in PERFORMANCE_RATINGS:
        raise ValueError(f"Invalid incumbent_performance: {filtered['incumbent_performance']}")
    if "displacement_difficulty" in filtered and filtered["displacement_difficulty"] not in DIFFICULTY_LEVELS:
        raise ValueError(f"Invalid displacement_difficulty: {filtered['displacement_difficulty']}")
    if "status" in filtered and filtered["status"] not in STATUSES:
        raise ValueError(f"Invalid status: {filtered['status']}")

    if "intelligence_sources" in filtered:
        src = filtered["intelligence_sources"]
        if isinstance(src, (list, tuple)):
            filtered["intelligence_sources"] = json.dumps(list(src))

    conn = _get_db(db_path)
    try:
        existing = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(f"Recompete not found: {recompete_id}")

        old_status = existing["status"]
        new_status = filtered.get("status", old_status)

        set_parts = [f"{k} = ?" for k in filtered]
        set_parts.append("updated_at = ?")
        values = list(filtered.values()) + [_now(), recompete_id]

        conn.execute(
            f"UPDATE recompete_tracking SET {', '.join(set_parts)} "
            f"WHERE id = ?",
            values,
        )

        details = {"updates": filtered}
        if "status" in filtered and filtered["status"] != old_status:
            details["status_transition"] = f"{old_status} -> {new_status}"

        _audit(
            conn, "recompete.update",
            f"Updated recompete {recompete_id}",
            "recompete", recompete_id,
            details,
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_recompete(recompete_id, db_path=None):
    """Get an enriched recompete record.

    Includes linked opportunity info, incumbent's other wins from
    competitor_wins, and days until recompete/POP end.

    Args:
        recompete_id: The REC-... identifier.
        db_path: Optional database path override.

    Returns:
        dict with enriched recompete data.

    Raises:
        ValueError: If recompete_id not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Recompete not found: {recompete_id}")

        rec = _row_to_dict(row)
        rec["intelligence_sources"] = _parse_json_field(
            rec.get("intelligence_sources")
        )

        # Linked opportunity info.
        opp = None
        if rec.get("opportunity_id"):
            opp_row = conn.execute(
                "SELECT id, title, status, agency, naics_code, "
                "response_deadline, estimated_value_low, estimated_value_high "
                "FROM opportunities WHERE id = ?",
                (rec["opportunity_id"],),
            ).fetchone()
            opp = _row_to_dict(opp_row)
        rec["linked_opportunity"] = opp

        # Incumbent wins from competitor_wins.
        incumbent = rec["incumbent_name"]
        wins = conn.execute(
            "SELECT id, contract_number, agency, award_date, award_amount, "
            "naics_code, description "
            "FROM competitor_wins "
            "WHERE competitor_name = ? "
            "ORDER BY award_date DESC LIMIT 20",
            (incumbent,),
        ).fetchall()
        rec["incumbent_wins"] = [_row_to_dict(w) for w in wins]
        rec["incumbent_win_count"] = len(wins)

        # Days calculations.
        rec["days_until_pop_end"] = _days_until(rec.get("pop_end_date"))
        rec["days_until_recompete"] = _days_until(rec.get("recompete_date"))

        return rec
    finally:
        conn.close()


def list_recompetes(agency=None, status=None, db_path=None):
    """List recompete tracking records with optional filters.

    Args:
        agency: Filter by agency name (optional).
        status: Filter by tracking status (optional).
        db_path: Optional database path override.

    Returns:
        list of dicts with recompete records.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM recompete_tracking WHERE 1=1 "
        params = []
        if agency:
            query += "AND agency = ? "
            params.append(agency)
        if status:
            if status not in STATUSES:
                raise ValueError(f"Invalid status filter: {status}")
            query += "AND status = ? "
            params.append(status)
        query += "ORDER BY updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            d["days_until_pop_end"] = _days_until(d.get("pop_end_date"))
            d["days_until_recompete"] = _days_until(d.get("recompete_date"))
            d["intelligence_sources"] = _parse_json_field(
                d.get("intelligence_sources")
            )
            results.append(d)
        return results
    finally:
        conn.close()


def assess_displacement(recompete_id, db_path=None):
    """Assess how difficult it is to displace the incumbent.

    Factors: incumbent performance rating, contract value, years of
    incumbency, customer relationship depth, and our past performance
    at the same agency.

    Args:
        recompete_id: The REC-... identifier.
        db_path: Optional database path override.

    Returns:
        dict with difficulty, score, factors, and strategy_recommendations.

    Raises:
        ValueError: If recompete_id not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Recompete not found: {recompete_id}")

        rec = _row_to_dict(row)
        incumbent = rec["incumbent_name"]
        agency = rec["agency"]
        factors = []

        # Factor 1: Incumbent performance rating.
        perf = rec.get("incumbent_performance", "unknown")
        perf_score = PERFORMANCE_SCORES.get(perf, 0.5)
        factors.append({
            "factor": "incumbent_performance",
            "weight": DISPLACEMENT_WEIGHTS["incumbent_performance"],
            "value": perf,
            "score": perf_score,
            "assessment": (
                "Exceptional incumbents are extremely hard to displace"
                if perf == "exceptional" else
                "Marginal/unsatisfactory incumbents are vulnerable"
                if perf in ("marginal", "unsatisfactory") else
                f"Incumbent rated {perf} -- moderate incumbency advantage"
            ),
        })

        # Factor 2: Contract value (larger = harder to displace).
        value = rec.get("current_value") or 0
        if value >= 50_000_000:
            val_score = 1.0
            val_assess = "Large contract ($50M+) -- high switching cost"
        elif value >= 10_000_000:
            val_score = 0.7
            val_assess = "Mid-size contract ($10M-$50M) -- moderate barrier"
        elif value >= 1_000_000:
            val_score = 0.4
            val_assess = "Small-to-mid contract ($1M-$10M) -- manageable"
        elif value > 0:
            val_score = 0.2
            val_assess = "Small contract (<$1M) -- lower switching cost"
        else:
            val_score = 0.5
            val_assess = "Contract value unknown -- defaulting to moderate"
        factors.append({
            "factor": "contract_value",
            "weight": DISPLACEMENT_WEIGHTS["contract_value"],
            "value": value,
            "score": val_score,
            "assessment": val_assess,
        })

        # Factor 3: Years of incumbency (from competitor_wins).
        earliest_win = conn.execute(
            "SELECT MIN(award_date) as earliest "
            "FROM competitor_wins "
            "WHERE competitor_name = ? AND agency = ? "
            "AND award_date IS NOT NULL",
            (incumbent, agency),
        ).fetchone()

        years = 0.0
        if earliest_win and earliest_win["earliest"]:
            try:
                earliest_dt = datetime.strptime(
                    earliest_win["earliest"][:10], "%Y-%m-%d"
                )
                years = (datetime.now() - earliest_dt).days / 365.25
            except (ValueError, TypeError):
                pass

        if years >= 10:
            yr_score = 1.0
            yr_assess = f"Long-term incumbent ({years:.0f}+ years) -- deeply embedded"
        elif years >= 5:
            yr_score = 0.7
            yr_assess = f"Established incumbent ({years:.0f} years)"
        elif years >= 2:
            yr_score = 0.4
            yr_assess = f"Relatively new incumbent ({years:.1f} years)"
        else:
            yr_score = 0.2
            yr_assess = "Short incumbency or unknown history"
        factors.append({
            "factor": "years_of_incumbency",
            "weight": DISPLACEMENT_WEIGHTS["years_of_incumbency"],
            "value": round(years, 1),
            "score": yr_score,
            "assessment": yr_assess,
        })

        # Factor 4: Customer relationship depth (from customer_profiles).
        cust_row = conn.execute(
            "SELECT * FROM customer_profiles WHERE agency = ?",
            (agency,),
        ).fetchone()

        if cust_row:
            cust = _row_to_dict(cust_row)
            # Check if incumbent is mentioned in procurement_history or
            # key_personnel notes.
            text_blob = " ".join([
                cust.get("procurement_history") or "",
                cust.get("key_personnel") or "",
                cust.get("preferred_approaches") or "",
            ]).lower()
            if incumbent.lower() in text_blob:
                rel_score = 0.9
                rel_assess = "Incumbent referenced in customer profile -- strong relationship"
            else:
                rel_score = 0.4
                rel_assess = "Customer profile exists but no direct incumbent reference"
        else:
            rel_score = 0.3
            rel_assess = "No customer profile on file -- relationship depth unknown"
        factors.append({
            "factor": "customer_relationship",
            "weight": DISPLACEMENT_WEIGHTS["customer_relationship"],
            "value": "strong" if rel_score >= 0.7 else "moderate" if rel_score >= 0.4 else "unknown",
            "score": rel_score,
            "assessment": rel_assess,
        })

        # Factor 5: Our past performance at this agency.
        our_pp = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "AVG(CASE cpars_rating "
            "  WHEN 'exceptional' THEN 1.0 "
            "  WHEN 'very_good' THEN 0.8 "
            "  WHEN 'satisfactory' THEN 0.5 "
            "  WHEN 'marginal' THEN 0.2 "
            "  WHEN 'unsatisfactory' THEN 0.05 "
            "  ELSE NULL END) as avg_rating "
            "FROM past_performances "
            "WHERE agency = ? AND is_active = 1",
            (agency,),
        ).fetchone()

        pp_count = our_pp["cnt"] or 0
        pp_avg = our_pp["avg_rating"]

        if pp_count >= 3 and pp_avg and pp_avg >= 0.7:
            pp_score = 0.2  # low difficulty -- we have strong presence
            pp_assess = f"Strong agency presence ({pp_count} contracts, avg rating {pp_avg:.2f})"
        elif pp_count >= 1:
            pp_score = 0.5
            pp_assess = f"Some agency presence ({pp_count} contract(s))"
        else:
            pp_score = 0.8
            pp_assess = "No past performance at this agency -- significant disadvantage"
        factors.append({
            "factor": "our_past_performance",
            "weight": DISPLACEMENT_WEIGHTS["our_past_performance"],
            "value": pp_count,
            "score": pp_score,
            "assessment": pp_assess,
        })

        # Weighted score (0.0 = easy, 1.0 = very difficult).
        weighted = sum(f["score"] * f["weight"] for f in factors)

        if weighted >= 0.75:
            difficulty = "very_difficult"
        elif weighted >= 0.55:
            difficulty = "difficult"
        elif weighted >= 0.35:
            difficulty = "moderate"
        else:
            difficulty = "easy"

        # Update the record with assessed difficulty.
        conn.execute(
            "UPDATE recompete_tracking SET displacement_difficulty = ?, "
            "updated_at = ? WHERE id = ?",
            (difficulty, _now(), recompete_id),
        )

        # Generate strategy recommendations.
        recommendations = _displacement_strategies(
            difficulty, factors, rec,
        )

        _audit(
            conn, "recompete.assess",
            f"Displacement assessment for {recompete_id}: {difficulty}",
            "recompete", recompete_id,
            {"difficulty": difficulty, "score": round(weighted, 3)},
        )
        conn.commit()

        return {
            "recompete_id": recompete_id,
            "incumbent_name": incumbent,
            "agency": agency,
            "difficulty": difficulty,
            "score": round(weighted, 3),
            "factors": factors,
            "strategy_recommendations": recommendations,
        }
    finally:
        conn.close()


def _displacement_strategies(difficulty, factors, rec):
    """Generate displacement strategy recommendations.

    Args:
        difficulty: Assessed difficulty level.
        factors: List of factor dicts from assessment.
        rec: The recompete record dict.

    Returns:
        list of strategy recommendation strings.
    """
    strategies = []

    perf_factor = next(
        (f for f in factors if f["factor"] == "incumbent_performance"), None,
    )
    pp_factor = next(
        (f for f in factors if f["factor"] == "our_past_performance"), None,
    )

    if difficulty == "very_difficult":
        strategies.append(
            "Consider teaming with the incumbent or a sub with deep "
            "agency relationships rather than a direct challenge."
        )
        strategies.append(
            "Focus on innovation/modernization discriminators that the "
            "incumbent's existing staff may not deliver."
        )
    elif difficulty == "difficult":
        strategies.append(
            "Build agency relationships now -- schedule capability "
            "briefings and attend industry days."
        )
        strategies.append(
            "Identify concrete areas where incumbent has underperformed "
            "and propose measurable improvements."
        )

    if perf_factor and perf_factor["value"] in ("marginal", "unsatisfactory"):
        strategies.append(
            "Incumbent performance is weak -- highlight our superior "
            "CPARS track record and specific improvement metrics."
        )
        strategies.append(
            "Request and reference the incumbent's CPARS/past "
            "performance through FAPIIS if available."
        )

    if pp_factor and pp_factor["score"] >= 0.7:
        strategies.append(
            "No agency past performance -- pursue subcontracting or "
            "teaming on related work to establish presence before RFP."
        )

    if rec.get("current_value") and rec["current_value"] >= 10_000_000:
        strategies.append(
            "Large contract value -- consider aggressive pricing or "
            "phased approach to reduce perceived transition risk."
        )

    # Always include transition risk mitigation.
    strategies.append(
        "Develop a detailed transition plan addressing knowledge transfer, "
        "key personnel retention, and continuity of operations."
    )

    return strategies


def upcoming_recompetes(days_ahead=180, db_path=None):
    """Find recompetes coming up within N days.

    Queries recompete_tracking for records where pop_end_date or
    recompete_date falls within the window.  Also scans competitor_wins
    for contracts that may be ending soon.

    Args:
        days_ahead: Number of days to look ahead (default 180).
        db_path: Optional database path override.

    Returns:
        dict with upcoming recompetes sorted by days remaining.
    """
    conn = _get_db(db_path)
    try:
        # From recompete_tracking table.
        tracked_rows = conn.execute(
            "SELECT * FROM recompete_tracking "
            "WHERE status NOT IN ('awarded_us', 'awarded_incumbent', "
            "'awarded_other', 'cancelled') "
            "AND (pop_end_date IS NOT NULL OR recompete_date IS NOT NULL) "
            "ORDER BY COALESCE(recompete_date, pop_end_date) ASC",
        ).fetchall()

        upcoming = []
        for r in tracked_rows:
            d = _row_to_dict(r)
            end_date = d.get("recompete_date") or d.get("pop_end_date")
            days_rem = _days_until(end_date)
            if days_rem is not None and 0 <= days_rem <= days_ahead:
                upcoming.append({
                    "id": d["id"],
                    "source": "recompete_tracking",
                    "incumbent": d["incumbent_name"],
                    "agency": d["agency"],
                    "contract_number": d.get("contract_number"),
                    "end_date": end_date,
                    "days_remaining": days_rem,
                    "value": d.get("current_value"),
                    "status": d["status"],
                    "displacement_difficulty": d.get("displacement_difficulty"),
                })

        # Scan competitor_wins for contracts potentially ending soon.
        # Typical PoP is 1-5 years from award; flag awards from 1-5 years ago.
        potential = conn.execute(
            "SELECT cw.*, c.company_name AS comp_name "
            "FROM competitor_wins cw "
            "LEFT JOIN competitors c ON cw.competitor_id = c.id "
            "WHERE cw.award_date IS NOT NULL "
            "AND cw.award_date >= date('now', '-5 years') "
            "AND cw.award_date <= date('now', '-1 year') "
            "ORDER BY cw.award_date ASC "
            "LIMIT 50",
        ).fetchall()

        # Exclude those already tracked.
        tracked_contracts = {
            r.get("contract_number") for r in upcoming
            if r.get("contract_number")
        }

        for row in potential:
            pw = _row_to_dict(row)
            cn = pw.get("contract_number")
            if cn and cn in tracked_contracts:
                continue
            # Already in recompete_tracking at all?
            existing = conn.execute(
                "SELECT id FROM recompete_tracking WHERE contract_number = ?",
                (cn,),
            ).fetchone() if cn else None
            if existing:
                continue

            # Estimate end date assuming 1-year base PoP from award.
            award_str = pw.get("award_date")
            if not award_str:
                continue
            try:
                award_dt = datetime.strptime(award_str[:10], "%Y-%m-%d")
                # Assume 3-year average PoP.
                est_end = award_dt.replace(year=award_dt.year + 3)
                est_end_str = est_end.strftime("%Y-%m-%d")
                days_rem = _days_until(est_end_str)
            except (ValueError, TypeError):
                continue

            if days_rem is not None and 0 <= days_rem <= days_ahead:
                upcoming.append({
                    "id": pw.get("id"),
                    "source": "competitor_wins_estimate",
                    "incumbent": pw.get("competitor_name") or pw.get("comp_name"),
                    "agency": pw.get("agency"),
                    "contract_number": cn,
                    "end_date": est_end_str,
                    "days_remaining": days_rem,
                    "value": pw.get("award_amount"),
                    "status": "estimated",
                    "displacement_difficulty": None,
                })

        # Sort by days remaining.
        upcoming.sort(key=lambda x: x.get("days_remaining", 9999))

        return {
            "days_ahead": days_ahead,
            "total_upcoming": len(upcoming),
            "upcoming": upcoming,
            "generated_at": _now(),
        }
    finally:
        conn.close()


def incumbent_profile(incumbent_name, agency=None, db_path=None):
    """Build a comprehensive incumbent profile.

    Aggregates data from competitors, competitor_wins, and
    recompete_tracking to paint a full picture of the incumbent.

    Args:
        incumbent_name: Name of the incumbent company.
        agency: Optional agency filter to narrow scope.
        db_path: Optional database path override.

    Returns:
        dict with profile: wins, value, agencies, capabilities,
        recompete history, and overall assessment.
    """
    conn = _get_db(db_path)
    try:
        # From competitors table.
        comp_row = conn.execute(
            "SELECT * FROM competitors WHERE company_name = ? "
            "AND is_active = 1 LIMIT 1",
            (incumbent_name,),
        ).fetchone()
        comp = _row_to_dict(comp_row) if comp_row else {}

        # Wins query.
        wins_query = (
            "SELECT * FROM competitor_wins "
            "WHERE competitor_name = ? "
        )
        wins_params = [incumbent_name]
        if agency:
            wins_query += "AND agency = ? "
            wins_params.append(agency)
        wins_query += "ORDER BY award_date DESC"

        win_rows = conn.execute(wins_query, wins_params).fetchall()
        wins = [_row_to_dict(w) for w in win_rows]
        total_value = sum(w.get("award_amount") or 0 for w in wins)

        # Agency distribution.
        agency_counts = defaultdict(int)
        agency_value = defaultdict(float)
        for w in wins:
            ag = w.get("agency") or "Unknown"
            agency_counts[ag] += 1
            agency_value[ag] += w.get("award_amount") or 0

        agencies = [
            {"agency": ag, "win_count": agency_counts[ag],
             "total_value": round(agency_value[ag], 2)}
            for ag in sorted(agency_counts,
                             key=lambda x: agency_counts[x], reverse=True)
        ]

        # NAICS distribution.
        naics_counts = defaultdict(int)
        for w in wins:
            nc = w.get("naics_code") or "Unknown"
            naics_counts[nc] += 1

        naics_dist = [
            {"naics_code": nc, "win_count": naics_counts[nc]}
            for nc in sorted(naics_counts,
                             key=lambda x: naics_counts[x], reverse=True)
        ]

        # Recompete tracking records for this incumbent.
        rec_query = (
            "SELECT * FROM recompete_tracking WHERE incumbent_name = ? "
        )
        rec_params = [incumbent_name]
        if agency:
            rec_query += "AND agency = ? "
            rec_params.append(agency)
        rec_query += "ORDER BY updated_at DESC"

        rec_rows = conn.execute(rec_query, rec_params).fetchall()
        recompetes = [_row_to_dict(r) for r in rec_rows]

        # Overall assessment.
        if total_value >= 100_000_000 and len(wins) >= 10:
            threat = "high"
            narrative = (
                f"{incumbent_name} is a major player with "
                f"${total_value:,.0f} in tracked wins across "
                f"{len(agencies)} agencies."
            )
        elif total_value >= 10_000_000 or len(wins) >= 5:
            threat = "moderate"
            narrative = (
                f"{incumbent_name} has a solid presence with "
                f"{len(wins)} tracked wins worth ${total_value:,.0f}."
            )
        elif len(wins) >= 1:
            threat = "emerging"
            narrative = (
                f"{incumbent_name} has limited tracked history "
                f"({len(wins)} win(s), ${total_value:,.0f})."
            )
        else:
            threat = "unknown"
            narrative = (
                f"No tracked win data for {incumbent_name}. "
                f"Further intelligence gathering recommended."
            )

        return {
            "name": incumbent_name,
            "competitor_id": comp.get("id"),
            "capabilities": comp.get("capabilities"),
            "strengths": comp.get("strengths"),
            "weaknesses": comp.get("weaknesses"),
            "cage_code": comp.get("cage_code"),
            "contract_vehicles": _parse_json_field(
                comp.get("contract_vehicles")
            ),
            "total_wins": len(wins),
            "total_value": round(total_value, 2),
            "agencies": agencies[:15],
            "naics_distribution": naics_dist[:15],
            "recent_wins": wins[:10],
            "recompete_history": recompetes,
            "assessment": {
                "threat_level": threat,
                "narrative": narrative,
            },
        }
    finally:
        conn.close()


def win_strategy(recompete_id, db_path=None):
    """Generate a win strategy against the incumbent.

    Analyzes incumbent weaknesses vs our strengths, pricing pressure
    opportunities, teaming suggestions, and technical discriminators.

    Args:
        recompete_id: The REC-... identifier.
        db_path: Optional database path override.

    Returns:
        dict with strategy approaches, price_strategy,
        teaming_suggestions, and discriminators.

    Raises:
        ValueError: If recompete_id not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM recompete_tracking WHERE id = ?",
            (recompete_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Recompete not found: {recompete_id}")

        rec = _row_to_dict(row)
        incumbent = rec["incumbent_name"]
        agency = rec["agency"]
        naics = rec.get("naics_code")

        # Incumbent intel.
        comp_row = conn.execute(
            "SELECT * FROM competitors WHERE company_name = ? "
            "AND is_active = 1 LIMIT 1",
            (incumbent,),
        ).fetchone()
        comp = _row_to_dict(comp_row) if comp_row else {}

        # Our past performance at this agency.
        our_pp = conn.execute(
            "SELECT * FROM past_performances "
            "WHERE agency = ? AND is_active = 1 "
            "ORDER BY cpars_rating DESC",
            (agency,),
        ).fetchall()
        our_pp_list = [_row_to_dict(p) for p in our_pp]

        # Strategy approaches.
        strategies = []
        inc_weaknesses = rec.get("incumbent_weaknesses") or comp.get("weaknesses") or ""
        inc_strengths = rec.get("incumbent_strengths") or comp.get("strengths") or ""

        # Weakness-based strategies.
        if inc_weaknesses:
            strategies.append({
                "approach": "Exploit incumbent weaknesses",
                "rationale": (
                    f"Known weaknesses: {inc_weaknesses[:200]}. "
                    "Propose concrete solutions addressing these gaps."
                ),
                "confidence": "high" if len(inc_weaknesses) > 20 else "medium",
            })

        perf = rec.get("incumbent_performance", "unknown")
        if perf in ("marginal", "unsatisfactory"):
            strategies.append({
                "approach": "Performance displacement",
                "rationale": (
                    f"Incumbent rated '{perf}'. Propose metrics-driven "
                    "SLAs with penalties and a strong QA plan."
                ),
                "confidence": "high",
            })
        elif perf == "satisfactory":
            strategies.append({
                "approach": "Innovation uplift",
                "rationale": (
                    "Incumbent is adequate but not outstanding. Propose "
                    "modern approaches (cloud, automation, AI/ML) to "
                    "demonstrate superior value."
                ),
                "confidence": "medium",
            })

        if our_pp_list:
            strategies.append({
                "approach": "Leverage agency relationships",
                "rationale": (
                    f"We have {len(our_pp_list)} past performance(s) "
                    f"at {agency}. Reference these for credibility."
                ),
                "confidence": "high" if len(our_pp_list) >= 3 else "medium",
            })
        else:
            strategies.append({
                "approach": "Establish agency presence",
                "rationale": (
                    f"No past performance at {agency}. Pursue sub-contract "
                    "work or industry days to build relationships before RFP."
                ),
                "confidence": "low",
            })

        # Price strategy.
        inc_value = rec.get("current_value") or 0
        comp_awards = conn.execute(
            "SELECT AVG(award_amount) as avg_val "
            "FROM competitor_wins "
            "WHERE competitor_name = ? AND award_amount > 0",
            (incumbent,),
        ).fetchone()
        inc_avg = (comp_awards["avg_val"] or 0) if comp_awards else 0

        if inc_value > 0:
            price_strategy = {
                "current_contract_value": inc_value,
                "incumbent_avg_award": round(inc_avg, 2) if inc_avg else None,
                "recommendation": (
                    "Price 5-10% below incumbent's current value while "
                    "maintaining margins through efficiency gains."
                    if perf in ("marginal", "unsatisfactory", "satisfactory")
                    else "Match incumbent pricing and compete on technical "
                    "merit -- do not race to the bottom against a strong "
                    "incumbent."
                ),
                "target_range_low": round(inc_value * 0.90, 2),
                "target_range_high": round(inc_value * 1.05, 2),
            }
        else:
            price_strategy = {
                "current_contract_value": None,
                "recommendation": (
                    "Contract value unknown. Use FPDS research and pricing "
                    "benchmarks to establish competitive range."
                ),
            }

        # Teaming suggestions.
        teaming = []
        if naics:
            # Find competitors who are strong in this NAICS but are NOT
            # the incumbent -- potential teaming partners.
            partners = conn.execute(
                "SELECT competitor_name, COUNT(*) as wins "
                "FROM competitor_wins "
                "WHERE naics_code = ? AND competitor_name != ? "
                "GROUP BY competitor_name "
                "ORDER BY wins DESC LIMIT 5",
                (naics, incumbent),
            ).fetchall()
            for p in partners:
                teaming.append({
                    "company": p["competitor_name"],
                    "rationale": (
                        f"{p['wins']} wins in NAICS {naics} -- "
                        "established capability and past performance."
                    ),
                })

        if not teaming:
            teaming.append({
                "company": None,
                "rationale": (
                    "No strong teaming candidates identified from tracked "
                    "data. Consider industry outreach."
                ),
            })

        # Technical discriminators.
        discriminators = []
        if inc_weaknesses:
            for weakness in re.split(r'[;,\n]', inc_weaknesses):
                w = weakness.strip()
                if len(w) > 5:
                    discriminators.append(
                        f"Address: {w[:100]}"
                    )

        discriminators.extend([
            "Propose modern DevSecOps practices with automated CI/CD",
            "Offer enhanced cybersecurity posture (NIST 800-53 / Zero Trust)",
            "Include detailed transition plan with risk mitigation",
        ])

        _audit(
            conn, "recompete.strategy",
            f"Win strategy generated for {recompete_id}",
            "recompete", recompete_id,
            {"strategies": len(strategies), "teaming": len(teaming)},
        )
        conn.commit()

        return {
            "recompete_id": recompete_id,
            "incumbent_name": incumbent,
            "agency": agency,
            "strategy": strategies,
            "price_strategy": price_strategy,
            "teaming_suggestions": teaming,
            "discriminators": discriminators[:10],
            "generated_at": _now(),
        }
    finally:
        conn.close()


def dashboard_data(db_path=None):
    """Dashboard summary of recompete intelligence.

    Returns upcoming recompetes, counts by status and difficulty,
    and total value pipeline.

    Args:
        db_path: Optional database path override.

    Returns:
        dict with dashboard summary data.
    """
    conn = _get_db(db_path)
    try:
        # Total count.
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM recompete_tracking"
        ).fetchone()["cnt"]

        # By status.
        status_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt, "
            "COALESCE(SUM(current_value), 0) as total_value "
            "FROM recompete_tracking "
            "GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        by_status = [
            {"status": r["status"], "count": r["cnt"],
             "total_value": round(r["total_value"], 2)}
            for r in status_rows
        ]

        # By displacement difficulty.
        diff_rows = conn.execute(
            "SELECT displacement_difficulty, COUNT(*) as cnt "
            "FROM recompete_tracking "
            "GROUP BY displacement_difficulty ORDER BY cnt DESC"
        ).fetchall()
        by_difficulty = [
            {"difficulty": r["displacement_difficulty"] or "unassessed",
             "count": r["cnt"]}
            for r in diff_rows
        ]

        # Value pipeline (active only).
        pipeline = conn.execute(
            "SELECT COALESCE(SUM(current_value), 0) as total, "
            "COUNT(*) as cnt "
            "FROM recompete_tracking "
            "WHERE status NOT IN ('awarded_us', 'awarded_incumbent', "
            "'awarded_other', 'cancelled')"
        ).fetchone()

        # Upcoming (next 90 days).
        upcoming_rows = conn.execute(
            "SELECT * FROM recompete_tracking "
            "WHERE status NOT IN ('awarded_us', 'awarded_incumbent', "
            "'awarded_other', 'cancelled') "
            "AND (pop_end_date IS NOT NULL OR recompete_date IS NOT NULL) "
            "ORDER BY COALESCE(recompete_date, pop_end_date) ASC "
            "LIMIT 10"
        ).fetchall()

        upcoming = []
        for r in upcoming_rows:
            d = _row_to_dict(r)
            end_date = d.get("recompete_date") or d.get("pop_end_date")
            days_rem = _days_until(end_date)
            if days_rem is not None and days_rem <= 90:
                upcoming.append({
                    "id": d["id"],
                    "incumbent": d["incumbent_name"],
                    "agency": d["agency"],
                    "contract_number": d.get("contract_number"),
                    "end_date": end_date,
                    "days_remaining": days_rem,
                    "value": d.get("current_value"),
                    "difficulty": d.get("displacement_difficulty"),
                })

        # Top incumbents by tracked value.
        top_inc = conn.execute(
            "SELECT incumbent_name, COUNT(*) as cnt, "
            "COALESCE(SUM(current_value), 0) as total_value "
            "FROM recompete_tracking "
            "GROUP BY incumbent_name "
            "ORDER BY total_value DESC LIMIT 10"
        ).fetchall()
        top_incumbents = [
            {"incumbent": r["incumbent_name"], "contracts": r["cnt"],
             "total_value": round(r["total_value"], 2)}
            for r in top_inc
        ]

        return {
            "total_tracked": total,
            "by_status": by_status,
            "by_difficulty": by_difficulty,
            "active_pipeline": {
                "count": pipeline["cnt"],
                "total_value": round(pipeline["total"], 2),
            },
            "upcoming_90_days": upcoming,
            "top_incumbents": top_incumbents,
            "generated_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build the argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Recompete / Incumbent Intelligence Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --create --incumbent 'Booz Allen' --agency 'DIA' "
            "--contract FA8075-21-D-0001 --json\n"
            "  %(prog)s --update --recompete-id REC-abc --status rfp_released --json\n"
            "  %(prog)s --get --recompete-id REC-abc --json\n"
            "  %(prog)s --list --agency 'DIA' --json\n"
            "  %(prog)s --assess --recompete-id REC-abc --json\n"
            "  %(prog)s --upcoming --days 180 --json\n"
            "  %(prog)s --incumbent-profile --incumbent 'Booz Allen' --json\n"
            "  %(prog)s --win-strategy --recompete-id REC-abc --json\n"
            "  %(prog)s --dashboard --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true",
                        help="Create a recompete tracking record")
    action.add_argument("--update", action="store_true",
                        help="Update a recompete record")
    action.add_argument("--get", action="store_true",
                        help="Get enriched recompete details")
    action.add_argument("--list", action="store_true",
                        help="List recompete records")
    action.add_argument("--assess", action="store_true",
                        help="Assess displacement difficulty")
    action.add_argument("--upcoming", action="store_true",
                        help="Find upcoming recompetes")
    action.add_argument("--incumbent-profile", action="store_true",
                        help="Build incumbent company profile")
    action.add_argument("--win-strategy", action="store_true",
                        help="Generate win strategy against incumbent")
    action.add_argument("--dashboard", action="store_true",
                        help="Dashboard summary data")

    parser.add_argument("--recompete-id", help="Recompete tracking ID (REC-...)")
    parser.add_argument("--incumbent", help="Incumbent company name")
    parser.add_argument("--agency", help="Agency name")
    parser.add_argument("--contract", help="Contract number")
    parser.add_argument("--naics", help="NAICS code")
    parser.add_argument("--value", type=float, help="Contract value in dollars")
    parser.add_argument("--pop-end", help="Period of performance end (YYYY-MM-DD)")
    parser.add_argument("--status",
                        choices=list(STATUSES),
                        help="Tracking status")
    parser.add_argument("--days", type=int, default=180,
                        help="Days ahead for --upcoming (default: 180)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    import argparse  # noqa: F811
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.create:
            if not args.incumbent:
                parser.error("--create requires --incumbent")
            if not args.agency:
                parser.error("--create requires --agency")
            result = create_recompete(
                incumbent_name=args.incumbent,
                agency=args.agency,
                contract_number=args.contract,
                naics_code=args.naics,
                current_value=args.value,
                pop_end_date=args.pop_end,
                db_path=db,
            )

        elif args.update:
            if not args.recompete_id:
                parser.error("--update requires --recompete-id")
            updates = {}
            if args.status:
                updates["status"] = args.status
            if args.incumbent:
                updates["incumbent_name"] = args.incumbent
            if args.agency:
                updates["agency"] = args.agency
            if args.contract:
                updates["contract_number"] = args.contract
            if args.naics:
                updates["naics_code"] = args.naics
            if args.value is not None:
                updates["current_value"] = args.value
            if args.pop_end:
                updates["pop_end_date"] = args.pop_end
            if not updates:
                parser.error("--update requires at least one field to change")
            result = update_recompete(args.recompete_id, updates, db_path=db)

        elif args.get:
            if not args.recompete_id:
                parser.error("--get requires --recompete-id")
            result = get_recompete(args.recompete_id, db_path=db)

        elif args.list:
            result = list_recompetes(
                agency=args.agency, status=args.status, db_path=db,
            )

        elif args.assess:
            if not args.recompete_id:
                parser.error("--assess requires --recompete-id")
            result = assess_displacement(args.recompete_id, db_path=db)

        elif args.upcoming:
            result = upcoming_recompetes(days_ahead=args.days, db_path=db)

        elif args.incumbent_profile:
            if not args.incumbent:
                parser.error("--incumbent-profile requires --incumbent")
            result = incumbent_profile(
                args.incumbent, agency=args.agency, db_path=db,
            )

        elif args.win_strategy:
            if not args.recompete_id:
                parser.error("--win-strategy requires --recompete-id")
            result = win_strategy(args.recompete_id, db_path=db)

        elif args.dashboard:
            result = dashboard_data(db_path=db)

        # Output.
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Found {len(result)} recompete record(s):")
                for r in result:
                    days = r.get("days_until_recompete") or r.get("days_until_pop_end")
                    days_str = f" ({days}d)" if days is not None else ""
                    print(
                        f"  [{r.get('id')}] {r.get('incumbent_name')} "
                        f"@ {r.get('agency')} -- {r.get('status')}{days_str}"
                    )
            elif isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, (dict, list)):
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
