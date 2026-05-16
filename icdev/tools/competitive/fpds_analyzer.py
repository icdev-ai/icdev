#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""FPDS (Federal Procurement Data System) market analyzer.

Queries FPDS award data by agency and NAICS code, builds pricing benchmarks,
generates market landscape assessments, analyzes award trends over fiscal
years, and produces detailed set-aside breakdowns.

Usage:
    python tools/competitive/fpds_analyzer.py --analyze-agency --agency "DIA" --json
    python tools/competitive/fpds_analyzer.py --analyze-naics --naics 541512 --json
    python tools/competitive/fpds_analyzer.py --build-benchmarks --naics 541512 --json
    python tools/competitive/fpds_analyzer.py --landscape --naics 541512 --json
    python tools/competitive/fpds_analyzer.py --trends --naics 541512 --years 5 --json
    python tools/competitive/fpds_analyzer.py --set-aside --naics 541512 --agency "DIA" --json
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
    import requests  # noqa: F401
except ImportError:
    requests = None

FPDS_API_BASE = os.environ.get(
    "FPDS_API_URL", "https://www.fpds.gov/ezsearch/LATEST"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            "fpds_analyzer",
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


def _win_id():
    """Generate a competitor win record ID."""
    return "CW-" + secrets.token_hex(6)


def _median(values):
    """Calculate median of a list of numbers.

    Args:
        values: List of numeric values (must be non-empty).

    Returns:
        Median value as float.
    """
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _percentile(values, pct):
    """Calculate a percentile of a sorted list.

    Args:
        values: List of numeric values (must be non-empty).
        pct: Percentile (0-100).

    Returns:
        Interpolated percentile value as float.
    """
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    k = (pct / 100.0) * (n - 1)
    f = int(k)
    c = f + 1 if f + 1 < n else f
    d = k - f
    return float(s[f]) + d * (float(s[c]) - float(s[f]))


# ---------------------------------------------------------------------------
# FPDS API interaction
# ---------------------------------------------------------------------------

def _query_fpds(agency=None, naics=None, days_back=730):
    """Query FPDS for contract awards filtered by agency and/or NAICS.

    Attempts to use the FPDS API via requests. Falls back to returning
    an empty list if the requests library is unavailable or the API
    is unreachable.

    Args:
        agency: Optional agency name filter.
        naics: Optional NAICS code filter.
        days_back: Number of days to look back.

    Returns:
        list of dicts with award data.
    """
    if requests is None:
        return []

    try:
        parts = []
        if agency:
            parts.append(f'AGENCY_NAME:"{agency}"')
        if naics:
            parts.append(f'NAICS:"{naics}"')
        if not parts:
            return []

        params = {
            "q": " ".join(parts),
            "length": 100,
        }

        resp = requests.get(FPDS_API_BASE, params=params, timeout=30)
        if resp.status_code == 200:
            return _parse_fpds_response(resp.text)
    except (requests.RequestException, Exception):
        pass

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

            try:
                amount = float(_text(award, ".//ns1:obligatedAmount", "0"))
            except (ValueError, TypeError):
                amount = 0.0

            results.append({
                "fpds_id": _text(award, ".//ns1:PIID"),
                "contract_number": _text(award, ".//ns1:PIID"),
                "agency": _text(award, ".//ns1:agencyID/@name"),
                "award_date": _text(award, ".//ns1:signedDate"),
                "award_amount": amount,
                "naics_code": _text(award, ".//ns1:NAICS/@code"),
                "vendor_name": _text(award, ".//ns1:vendorName"),
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


def _store_fpds_wins(conn, awards):
    """Store FPDS award records in competitor_wins, deduplicating by fpds_id.

    Args:
        conn: Active database connection.
        awards: List of award dicts from _parse_fpds_response.

    Returns:
        Number of new records inserted.
    """
    inserted = 0
    now = _now()
    for award in awards:
        if award.get("fpds_id"):
            existing = conn.execute(
                "SELECT id FROM competitor_wins WHERE fpds_id = ?",
                (award["fpds_id"],),
            ).fetchone()
            if existing:
                continue

        # Try to match vendor to a tracked competitor
        vendor = award.get("vendor_name") or "Unknown"
        comp_row = conn.execute(
            "SELECT id FROM competitors WHERE company_name = ? "
            "AND is_active = 1",
            (vendor,),
        ).fetchone()
        comp_id = comp_row["id"] if comp_row else None

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
                comp_id,
                vendor,
                award.get("contract_number"),
                award.get("agency"),
                award.get("award_date"),
                award.get("award_amount"),
                award.get("naics_code"),
                award.get("description"),
                award.get("contract_type"),
                award.get("set_aside_type"),
                award.get("fpds_id"),
                "fpds",
                now,
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def analyze_by_agency(agency, naics=None, days_back=730, db_path=None):
    """Query FPDS for awards by agency, aggregate and store results.

    Args:
        agency: Agency name to search.
        naics: Optional NAICS code filter.
        days_back: Number of days to look back (default 730).
        db_path: Optional database path override.

    Returns:
        dict with aggregated analysis: total awards, total dollar value,
        average award size, top contractors, set-aside distribution,
        contract type distribution.
    """
    conn = _get_db(db_path)
    try:
        # Fetch from FPDS and store
        fpds_awards = _query_fpds(agency=agency, naics=naics,
                                  days_back=days_back)
        new_records = 0
        if fpds_awards:
            new_records = _store_fpds_wins(conn, fpds_awards)
            conn.commit()

        # Build query against stored data
        query = "SELECT * FROM competitor_wins WHERE agency = ? "
        params = [agency]
        if naics:
            query += "AND naics_code = ? "
            params.append(str(naics))

        rows = conn.execute(query, params).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.analyze_agency",
                   f"No awards found for agency={agency}", details={
                       "agency": agency, "naics": naics})
            conn.commit()
            return {
                "agency": agency,
                "naics_filter": naics,
                "total_awards": 0,
                "total_value": 0.0,
                "average_award_size": 0.0,
                "fpds_records_added": new_records,
                "top_contractors": [],
                "set_aside_distribution": {},
                "contract_type_distribution": {},
            }

        amounts = [w.get("award_amount", 0) or 0 for w in wins]
        total_value = sum(amounts)

        # Top contractors
        contractor_value = defaultdict(float)
        contractor_count = defaultdict(int)
        for w in wins:
            name = w.get("competitor_name") or "Unknown"
            contractor_value[name] += w.get("award_amount", 0) or 0
            contractor_count[name] += 1

        top_contractors = sorted(
            contractor_value.keys(),
            key=lambda k: contractor_value[k],
            reverse=True,
        )[:15]
        top_contractors_list = [
            {
                "company_name": c,
                "win_count": contractor_count[c],
                "total_value": round(contractor_value[c], 2),
                "market_share_pct": round(
                    _safe_divide(contractor_value[c], total_value) * 100, 1
                ),
            }
            for c in top_contractors
        ]

        # Set-aside distribution
        sa_counts = defaultdict(int)
        sa_value = defaultdict(float)
        for w in wins:
            sa = w.get("set_aside_type") or "Not Specified"
            sa_counts[sa] += 1
            sa_value[sa] += w.get("award_amount", 0) or 0
        set_aside_dist = {
            sa: {"count": sa_counts[sa], "value": round(sa_value[sa], 2)}
            for sa in sorted(sa_counts, key=lambda x: sa_counts[x],
                             reverse=True)
        }

        # Contract type distribution
        ct_counts = defaultdict(int)
        ct_value = defaultdict(float)
        for w in wins:
            ct = w.get("contract_type") or "Not Specified"
            ct_counts[ct] += 1
            ct_value[ct] += w.get("award_amount", 0) or 0
        contract_type_dist = {
            ct: {"count": ct_counts[ct], "value": round(ct_value[ct], 2)}
            for ct in sorted(ct_counts, key=lambda x: ct_counts[x],
                             reverse=True)
        }

        _audit(conn, "fpds.analyze_agency",
               f"Analyzed agency={agency}: {len(wins)} awards",
               details={
                   "agency": agency, "naics": naics,
                   "total_awards": len(wins),
                   "total_value": round(total_value, 2),
                   "fpds_records_added": new_records,
               })
        conn.commit()

        return {
            "agency": agency,
            "naics_filter": naics,
            "total_awards": len(wins),
            "total_value": round(total_value, 2),
            "average_award_size": round(
                _safe_divide(total_value, len(wins)), 2
            ),
            "fpds_records_added": new_records,
            "top_contractors": top_contractors_list,
            "set_aside_distribution": set_aside_dist,
            "contract_type_distribution": contract_type_dist,
        }
    finally:
        conn.close()


def analyze_by_naics(naics_code, agency=None, days_back=730, db_path=None):
    """Query FPDS for awards by NAICS code, aggregate and store results.

    Args:
        naics_code: NAICS code to search.
        agency: Optional agency name filter.
        days_back: Number of days to look back (default 730).
        db_path: Optional database path override.

    Returns:
        dict with market size, top contractors, agency distribution,
        and award trends.
    """
    conn = _get_db(db_path)
    try:
        # Fetch from FPDS and store
        fpds_awards = _query_fpds(agency=agency, naics=str(naics_code),
                                  days_back=days_back)
        new_records = 0
        if fpds_awards:
            new_records = _store_fpds_wins(conn, fpds_awards)
            conn.commit()

        # Build query against stored data
        query = "SELECT * FROM competitor_wins WHERE naics_code = ? "
        params = [str(naics_code)]
        if agency:
            query += "AND agency = ? "
            params.append(agency)

        rows = conn.execute(query, params).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.analyze_naics",
                   f"No awards found for NAICS={naics_code}", details={
                       "naics_code": naics_code, "agency": agency})
            conn.commit()
            return {
                "naics_code": str(naics_code),
                "agency_filter": agency,
                "market_size": 0.0,
                "total_awards": 0,
                "fpds_records_added": new_records,
                "top_contractors": [],
                "agency_distribution": {},
                "award_trends": [],
            }

        amounts = [w.get("award_amount", 0) or 0 for w in wins]
        market_size = sum(amounts)

        # Top contractors
        contractor_value = defaultdict(float)
        contractor_count = defaultdict(int)
        for w in wins:
            name = w.get("competitor_name") or "Unknown"
            contractor_value[name] += w.get("award_amount", 0) or 0
            contractor_count[name] += 1

        top_names = sorted(
            contractor_value.keys(),
            key=lambda k: contractor_value[k],
            reverse=True,
        )[:15]
        top_contractors = [
            {
                "company_name": c,
                "win_count": contractor_count[c],
                "total_value": round(contractor_value[c], 2),
                "market_share_pct": round(
                    _safe_divide(contractor_value[c], market_size) * 100, 1
                ),
            }
            for c in top_names
        ]

        # Agency distribution
        agency_counts = defaultdict(int)
        agency_value = defaultdict(float)
        for w in wins:
            ag = w.get("agency") or "Unknown"
            agency_counts[ag] += 1
            agency_value[ag] += w.get("award_amount", 0) or 0
        agency_dist = {
            ag: {"count": agency_counts[ag],
                 "value": round(agency_value[ag], 2)}
            for ag in sorted(agency_counts, key=lambda x: agency_counts[x],
                             reverse=True)
        }

        # Award trends by year
        year_counts = defaultdict(int)
        year_value = defaultdict(float)
        for w in wins:
            ad = w.get("award_date") or ""
            year = ad[:4] if len(ad) >= 4 else "Unknown"
            year_counts[year] += 1
            year_value[year] += w.get("award_amount", 0) or 0

        award_trends = [
            {
                "year": yr,
                "award_count": year_counts[yr],
                "total_value": round(year_value[yr], 2),
            }
            for yr in sorted(year_counts.keys())
        ]

        _audit(conn, "fpds.analyze_naics",
               f"Analyzed NAICS={naics_code}: {len(wins)} awards",
               details={
                   "naics_code": str(naics_code), "agency": agency,
                   "total_awards": len(wins),
                   "market_size": round(market_size, 2),
                   "fpds_records_added": new_records,
               })
        conn.commit()

        return {
            "naics_code": str(naics_code),
            "agency_filter": agency,
            "market_size": round(market_size, 2),
            "total_awards": len(wins),
            "average_award_size": round(
                _safe_divide(market_size, len(wins)), 2
            ),
            "fpds_records_added": new_records,
            "top_contractors": top_contractors,
            "agency_distribution": agency_dist,
            "award_trends": award_trends,
        }
    finally:
        conn.close()


def build_pricing_benchmarks(naics_code, agency=None, db_path=None):
    """Aggregate competitor_wins data to calculate labor rate benchmarks.

    Updates the pricing_benchmarks table with computed statistics.

    Args:
        naics_code: NAICS code to build benchmarks for.
        agency: Optional agency filter.
        db_path: Optional database path override.

    Returns:
        dict with benchmark statistics.
    """
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT award_amount, contract_type, agency "
            "FROM competitor_wins "
            "WHERE naics_code = ? AND award_amount IS NOT NULL "
            "AND award_amount > 0 "
        )
        params = [str(naics_code)]
        if agency:
            query += "AND agency = ? "
            params.append(agency)

        rows = conn.execute(query, params).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.build_benchmarks",
                   f"No pricing data for NAICS={naics_code}", details={
                       "naics_code": naics_code, "agency": agency})
            conn.commit()
            return {
                "naics_code": str(naics_code),
                "agency": agency,
                "sample_size": 0,
                "message": "No award data available for benchmarking",
            }

        amounts = [w["award_amount"] for w in wins]
        avg_rate = _safe_divide(sum(amounts), len(amounts))
        med_rate = _median(amounts)
        p25 = _percentile(amounts, 25)
        p75 = _percentile(amounts, 75)

        now = _now()

        # Benchmarks by contract type
        ct_amounts = defaultdict(list)
        for w in wins:
            ct = w.get("contract_type") or "All"
            ct_amounts[ct].append(w["award_amount"])

        benchmarks = []
        for ct, ct_vals in ct_amounts.items():
            benchmark_id = "PB-" + secrets.token_hex(6)
            ct_avg = _safe_divide(sum(ct_vals), len(ct_vals))
            ct_med = _median(ct_vals)
            ct_p25 = _percentile(ct_vals, 25)
            ct_p75 = _percentile(ct_vals, 75)

            # Upsert pricing_benchmarks
            existing = conn.execute(
                "SELECT id FROM pricing_benchmarks "
                "WHERE naics_code = ? AND contract_type = ? "
                "AND (agency = ? OR (agency IS NULL AND ? IS NULL))",
                (str(naics_code), ct, agency, agency),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE pricing_benchmarks SET "
                    "average_rate = ?, median_rate = ?, "
                    "percentile_25 = ?, percentile_75 = ?, "
                    "sample_size = ?, data_period = ?, "
                    "source = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        round(ct_avg, 2), round(ct_med, 2),
                        round(ct_p25, 2), round(ct_p75, 2),
                        len(ct_vals), "competitor_wins",
                        "fpds_analyzer", now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO pricing_benchmarks "
                    "(id, naics_code, agency, contract_type, "
                    "average_rate, median_rate, percentile_25, "
                    "percentile_75, sample_size, data_period, "
                    "source, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        benchmark_id, str(naics_code), agency, ct,
                        round(ct_avg, 2), round(ct_med, 2),
                        round(ct_p25, 2), round(ct_p75, 2),
                        len(ct_vals), "competitor_wins",
                        "fpds_analyzer", now, now,
                    ),
                )

            benchmarks.append({
                "contract_type": ct,
                "average_rate": round(ct_avg, 2),
                "median_rate": round(ct_med, 2),
                "percentile_25": round(ct_p25, 2),
                "percentile_75": round(ct_p75, 2),
                "sample_size": len(ct_vals),
            })

        _audit(conn, "fpds.build_benchmarks",
               f"Built pricing benchmarks for NAICS={naics_code}",
               entity_type="pricing_benchmark",
               details={
                   "naics_code": str(naics_code), "agency": agency,
                   "sample_size": len(amounts),
                   "benchmarks_count": len(benchmarks),
               })
        conn.commit()

        return {
            "naics_code": str(naics_code),
            "agency": agency,
            "overall": {
                "average_rate": round(avg_rate, 2),
                "median_rate": round(med_rate, 2),
                "percentile_25": round(p25, 2),
                "percentile_75": round(p75, 2),
                "sample_size": len(amounts),
            },
            "by_contract_type": benchmarks,
        }
    finally:
        conn.close()


def market_landscape(naics_code=None, agency=None, db_path=None):
    """Generate comprehensive market landscape analysis.

    Computes market size, growth trends, competitive concentration (HHI),
    top contractors, set-aside breakdown, and contract vehicle analysis.

    Args:
        naics_code: Optional NAICS code filter.
        agency: Optional agency name filter.
        db_path: Optional database path override.

    Returns:
        dict with full market landscape assessment.
    """
    conn = _get_db(db_path)
    try:
        # Build filter
        conditions = []
        params = []
        if naics_code:
            conditions.append("naics_code = ?")
            params.append(str(naics_code))
        if agency:
            conditions.append("agency = ?")
            params.append(agency)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"SELECT * FROM competitor_wins {where}", params
        ).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.landscape",
                   "No data for market landscape", details={
                       "naics_code": naics_code, "agency": agency})
            conn.commit()
            return {
                "naics_code": naics_code,
                "agency": agency,
                "market_size": 0.0,
                "total_awards": 0,
                "hhi": 0,
                "concentration": "no_data",
                "top_contractors": [],
                "set_aside_breakdown": {},
                "contract_type_breakdown": {},
                "growth_trend": "no_data",
            }

        amounts = [w.get("award_amount", 0) or 0 for w in wins]
        market_size = sum(amounts)

        # Top contractors + HHI calculation
        contractor_value = defaultdict(float)
        contractor_count = defaultdict(int)
        for w in wins:
            name = w.get("competitor_name") or "Unknown"
            contractor_value[name] += w.get("award_amount", 0) or 0
            contractor_count[name] += 1

        # HHI = sum of squared market shares (each as percentage)
        hhi = 0.0
        for name, value in contractor_value.items():
            share = _safe_divide(value, market_size) * 100
            hhi += share ** 2
        hhi = round(hhi, 0)

        if hhi >= 2500:
            concentration = "highly_concentrated"
        elif hhi >= 1500:
            concentration = "moderately_concentrated"
        else:
            concentration = "competitive"

        top_names = sorted(
            contractor_value.keys(),
            key=lambda k: contractor_value[k],
            reverse=True,
        )[:20]
        top_contractors = [
            {
                "company_name": c,
                "win_count": contractor_count[c],
                "total_value": round(contractor_value[c], 2),
                "market_share_pct": round(
                    _safe_divide(contractor_value[c], market_size) * 100, 1
                ),
            }
            for c in top_names
        ]

        # Set-aside breakdown
        sa_counts = defaultdict(int)
        sa_value = defaultdict(float)
        for w in wins:
            sa = w.get("set_aside_type") or "Full and Open"
            sa_counts[sa] += 1
            sa_value[sa] += w.get("award_amount", 0) or 0
        set_aside_breakdown = {
            sa: {
                "count": sa_counts[sa],
                "value": round(sa_value[sa], 2),
                "pct_of_awards": round(
                    _safe_divide(sa_counts[sa], len(wins)) * 100, 1
                ),
                "pct_of_value": round(
                    _safe_divide(sa_value[sa], market_size) * 100, 1
                ),
            }
            for sa in sorted(sa_counts, key=lambda x: sa_counts[x],
                             reverse=True)
        }

        # Contract type breakdown
        ct_counts = defaultdict(int)
        ct_value = defaultdict(float)
        for w in wins:
            ct = w.get("contract_type") or "Not Specified"
            ct_counts[ct] += 1
            ct_value[ct] += w.get("award_amount", 0) or 0
        contract_type_breakdown = {
            ct: {
                "count": ct_counts[ct],
                "value": round(ct_value[ct], 2),
                "pct_of_value": round(
                    _safe_divide(ct_value[ct], market_size) * 100, 1
                ),
            }
            for ct in sorted(ct_counts, key=lambda x: ct_counts[x],
                             reverse=True)
        }

        # Growth trend (recent 180d vs older)
        recent_value = sum(
            w.get("award_amount", 0) or 0 for w in wins
            if (w.get("award_date") or "") >= (
                datetime.now(timezone.utc).strftime("%Y-%m-%d")[:4]
            )
        )
        recent_count = 0
        older_count = 0
        for w in wins:
            ad = w.get("award_date") or ""
            # Simple 180-day split using created_at as proxy
            ca = w.get("created_at") or ""
            if len(ca) >= 10:
                recent_count += 1
            else:
                older_count += 1

        # Use DB-based date comparison for accuracy
        recent_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM competitor_wins {where}"
            + (" AND " if where else " WHERE ")
            + "award_date >= date('now', '-180 days')",
            params,
        ).fetchone()
        older_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM competitor_wins {where}"
            + (" AND " if where else " WHERE ")
            + "(award_date < date('now', '-180 days') "
            + "OR award_date IS NULL)",
            params,
        ).fetchone()

        recent_cnt = recent_row["cnt"] if recent_row else 0
        older_cnt = older_row["cnt"] if older_row else 0

        if older_cnt == 0 and recent_cnt > 0:
            growth_trend = "new_market"
        elif recent_cnt > older_cnt * 1.2:
            growth_trend = "growing"
        elif recent_cnt < older_cnt * 0.8:
            growth_trend = "declining"
        else:
            growth_trend = "stable"

        _audit(conn, "fpds.landscape",
               "Generated market landscape", details={
                   "naics_code": naics_code, "agency": agency,
                   "total_awards": len(wins), "hhi": hhi,
                   "concentration": concentration,
               })
        conn.commit()

        return {
            "naics_code": naics_code,
            "agency": agency,
            "market_size": round(market_size, 2),
            "total_awards": len(wins),
            "average_award_size": round(
                _safe_divide(market_size, len(wins)), 2
            ),
            "unique_contractors": len(contractor_value),
            "hhi": int(hhi),
            "concentration": concentration,
            "top_contractors": top_contractors,
            "set_aside_breakdown": set_aside_breakdown,
            "contract_type_breakdown": contract_type_breakdown,
            "growth_trend": growth_trend,
            "trend_data": {
                "recent_awards_180d": recent_cnt,
                "older_awards": older_cnt,
            },
        }
    finally:
        conn.close()


def award_trends(naics_code=None, agency=None, fiscal_years=5, db_path=None):
    """Analyze award trends over fiscal years.

    Computes volume trends, dollar trends, new entrant rate, and average
    award size trends for each fiscal year in the window.

    Args:
        naics_code: Optional NAICS code filter.
        agency: Optional agency name filter.
        fiscal_years: Number of fiscal years to analyze (default 5).
        db_path: Optional database path override.

    Returns:
        dict with per-year trend data and summary statistics.
    """
    conn = _get_db(db_path)
    try:
        conditions = []
        params = []
        if naics_code:
            conditions.append("naics_code = ?")
            params.append(str(naics_code))
        if agency:
            conditions.append("agency = ?")
            params.append(agency)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"SELECT * FROM competitor_wins {where} "
            "ORDER BY award_date ASC",
            params,
        ).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.award_trends",
                   "No data for trend analysis", details={
                       "naics_code": naics_code, "agency": agency})
            conn.commit()
            return {
                "naics_code": naics_code,
                "agency": agency,
                "fiscal_years_requested": fiscal_years,
                "years": [],
                "summary": {"data_available": False},
            }

        # Current fiscal year (federal FY starts Oct 1)
        now = datetime.now(timezone.utc)
        current_fy = now.year if now.month >= 10 else now.year
        start_fy = current_fy - fiscal_years + 1

        # Bucket by fiscal year
        fy_data = defaultdict(lambda: {
            "awards": 0, "value": 0.0, "contractors": set()
        })
        all_prior_contractors = set()

        for w in wins:
            ad = w.get("award_date") or ""
            if len(ad) < 4:
                continue
            try:
                year = int(ad[:4])
                month = int(ad[5:7]) if len(ad) >= 7 else 1
            except (ValueError, IndexError):
                continue

            # Federal fiscal year: Oct-Sep
            fy = year if month >= 10 else year
            if fy < start_fy:
                all_prior_contractors.add(
                    w.get("competitor_name") or "Unknown"
                )
                continue

            fy_data[fy]["awards"] += 1
            fy_data[fy]["value"] += w.get("award_amount", 0) or 0
            fy_data[fy]["contractors"].add(
                w.get("competitor_name") or "Unknown"
            )

        years_result = []
        prev_contractors = set(all_prior_contractors)

        for fy in range(start_fy, current_fy + 1):
            data = fy_data.get(fy, {"awards": 0, "value": 0.0,
                                    "contractors": set()})
            new_entrants = data["contractors"] - prev_contractors
            total_contractors = len(data["contractors"])

            years_result.append({
                "fiscal_year": fy,
                "award_count": data["awards"],
                "total_value": round(data["value"], 2),
                "average_award_size": round(
                    _safe_divide(data["value"], data["awards"]), 2
                ),
                "unique_contractors": total_contractors,
                "new_entrants": len(new_entrants),
                "new_entrant_rate": round(
                    _safe_divide(len(new_entrants), total_contractors), 3
                ) if total_contractors else 0.0,
            })
            prev_contractors = prev_contractors | data["contractors"]

        # Summary
        counts = [y["award_count"] for y in years_result]
        values = [y["total_value"] for y in years_result]
        volume_trend = "stable"
        if len(counts) >= 2 and counts[-1] > counts[0] * 1.2:
            volume_trend = "increasing"
        elif len(counts) >= 2 and counts[-1] < counts[0] * 0.8:
            volume_trend = "decreasing"

        dollar_trend = "stable"
        if len(values) >= 2 and values[-1] > values[0] * 1.2:
            dollar_trend = "increasing"
        elif len(values) >= 2 and values[-1] < values[0] * 0.8:
            dollar_trend = "decreasing"

        _audit(conn, "fpds.award_trends",
               f"Analyzed {fiscal_years}-year award trends", details={
                   "naics_code": naics_code, "agency": agency,
                   "fiscal_years": fiscal_years,
                   "volume_trend": volume_trend,
                   "dollar_trend": dollar_trend,
               })
        conn.commit()

        return {
            "naics_code": naics_code,
            "agency": agency,
            "fiscal_years_requested": fiscal_years,
            "years": years_result,
            "summary": {
                "data_available": True,
                "volume_trend": volume_trend,
                "dollar_trend": dollar_trend,
                "total_awards_in_window": sum(counts),
                "total_value_in_window": round(sum(values), 2),
            },
        }
    finally:
        conn.close()


def set_aside_analysis(naics_code=None, agency=None, db_path=None):
    """Produce a detailed set-aside breakdown.

    Computes percentages and dollar amounts for: small business, 8(a),
    HUBZone, SDVOSB, WOSB, full and open, and other categories.

    Args:
        naics_code: Optional NAICS code filter.
        agency: Optional agency name filter.
        db_path: Optional database path override.

    Returns:
        dict with set-aside categories, counts, values, and percentages.
    """
    conn = _get_db(db_path)
    try:
        conditions = []
        params = []
        if naics_code:
            conditions.append("naics_code = ?")
            params.append(str(naics_code))
        if agency:
            conditions.append("agency = ?")
            params.append(agency)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"SELECT set_aside_type, award_amount "
            f"FROM competitor_wins {where}",
            params,
        ).fetchall()
        wins = [_row_to_dict(r) for r in rows]

        if not wins:
            _audit(conn, "fpds.set_aside",
                   "No data for set-aside analysis", details={
                       "naics_code": naics_code, "agency": agency})
            conn.commit()
            return {
                "naics_code": naics_code,
                "agency": agency,
                "total_awards": 0,
                "categories": {},
            }

        total_count = len(wins)
        total_value = sum(w.get("award_amount", 0) or 0 for w in wins)

        # Normalize set-aside types into standard categories
        category_map = {
            "small business": "Small Business",
            "sba": "Small Business",
            "small business set-aside": "Small Business",
            "8(a)": "8(a)",
            "8a": "8(a)",
            "hubzone": "HUBZone",
            "hubzone small business": "HUBZone",
            "sdvosb": "SDVOSB",
            "service-disabled veteran": "SDVOSB",
            "service disabled veteran owned small business": "SDVOSB",
            "wosb": "WOSB",
            "women-owned": "WOSB",
            "women owned small business": "WOSB",
            "economically disadvantaged women-owned": "EDWOSB",
            "full and open": "Full and Open",
            "full and open competition": "Full and Open",
            "none": "Full and Open",
        }

        cat_counts = defaultdict(int)
        cat_value = defaultdict(float)

        for w in wins:
            raw_sa = (w.get("set_aside_type") or "").strip()
            normalized = category_map.get(raw_sa.lower(), raw_sa or "Other")
            cat_counts[normalized] += 1
            cat_value[normalized] += w.get("award_amount", 0) or 0

        categories = {}
        for cat in sorted(cat_counts, key=lambda x: cat_value[x],
                          reverse=True):
            categories[cat] = {
                "award_count": cat_counts[cat],
                "total_value": round(cat_value[cat], 2),
                "pct_of_awards": round(
                    _safe_divide(cat_counts[cat], total_count) * 100, 1
                ),
                "pct_of_value": round(
                    _safe_divide(cat_value[cat], total_value) * 100, 1
                ),
            }

        # Small business aggregate (all SB categories combined)
        sb_categories = {
            "Small Business", "8(a)", "HUBZone", "SDVOSB", "WOSB", "EDWOSB"
        }
        sb_count = sum(
            cat_counts[c] for c in sb_categories if c in cat_counts
        )
        sb_value = sum(
            cat_value[c] for c in sb_categories if c in cat_counts
        )

        _audit(conn, "fpds.set_aside",
               "Generated set-aside analysis", details={
                   "naics_code": naics_code, "agency": agency,
                   "total_awards": total_count,
                   "categories_found": len(categories),
               })
        conn.commit()

        return {
            "naics_code": naics_code,
            "agency": agency,
            "total_awards": total_count,
            "total_value": round(total_value, 2),
            "small_business_aggregate": {
                "count": sb_count,
                "value": round(sb_value, 2),
                "pct_of_awards": round(
                    _safe_divide(sb_count, total_count) * 100, 1
                ),
                "pct_of_value": round(
                    _safe_divide(sb_value, total_value) * 100, 1
                ),
            },
            "categories": categories,
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
        description="GovProposal FPDS Market Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --analyze-agency --agency 'DIA' --json\n"
            "  %(prog)s --analyze-naics --naics 541512 --json\n"
            "  %(prog)s --build-benchmarks --naics 541512 "
            "--agency 'DIA' --json\n"
            "  %(prog)s --landscape --naics 541512 --json\n"
            "  %(prog)s --trends --naics 541512 --years 5 --json\n"
            "  %(prog)s --set-aside --naics 541512 --agency 'DIA' --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--analyze-agency", action="store_true",
                        help="Analyze FPDS awards by agency")
    action.add_argument("--analyze-naics", action="store_true",
                        help="Analyze FPDS awards by NAICS code")
    action.add_argument("--build-benchmarks", action="store_true",
                        help="Build pricing benchmarks from award data")
    action.add_argument("--landscape", action="store_true",
                        help="Generate market landscape analysis")
    action.add_argument("--trends", action="store_true",
                        help="Analyze award trends over fiscal years")
    action.add_argument("--set-aside", action="store_true",
                        help="Detailed set-aside breakdown")

    parser.add_argument("--agency", help="Agency name filter")
    parser.add_argument("--naics", help="NAICS code filter")
    parser.add_argument("--days-back", type=int, default=730,
                        help="Days to look back (default: 730)")
    parser.add_argument("--years", type=int, default=5,
                        help="Fiscal years for trend analysis (default: 5)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.analyze_agency:
            if not args.agency:
                parser.error("--analyze-agency requires --agency")
            result = analyze_by_agency(
                agency=args.agency,
                naics=args.naics,
                days_back=args.days_back,
                db_path=db,
            )

        elif args.analyze_naics:
            if not args.naics:
                parser.error("--analyze-naics requires --naics")
            result = analyze_by_naics(
                naics_code=args.naics,
                agency=args.agency,
                days_back=args.days_back,
                db_path=db,
            )

        elif args.build_benchmarks:
            if not args.naics:
                parser.error("--build-benchmarks requires --naics")
            result = build_pricing_benchmarks(
                naics_code=args.naics,
                agency=args.agency,
                db_path=db,
            )

        elif args.landscape:
            result = market_landscape(
                naics_code=args.naics,
                agency=args.agency,
                db_path=db,
            )

        elif args.trends:
            result = award_trends(
                naics_code=args.naics,
                agency=args.agency,
                fiscal_years=args.years,
                db_path=db,
            )

        elif args.set_aside:
            result = set_aside_analysis(
                naics_code=args.naics,
                agency=args.agency,
                db_path=db,
            )

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
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
    main()
