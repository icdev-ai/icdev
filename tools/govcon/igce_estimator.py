#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""IGCE Estimator — pre-bid Independent Government Cost Estimate generator.

Satisfies the system requirement: *System shall produce IGCE estimates
within 10% of vendor actuals, validated against GSA Schedule pricing or
market data.*

The estimator builds IGCEs by:
  1. Looking up each line item's labor category in the GSA Schedule
     reference table (``gsa_schedule_rates``). The median rate across
     contractors is used as the unit-price anchor.
  2. Falling back to ``gsa_market_rates`` (FPDS-NG, BLS OEWS, etc.) when
     no GSA Schedule data is available.
  3. Falling back to a deterministic default rate when neither source
     has a sample, and reporting low confidence so reviewers know to
     backfill a benchmark.
  4. Reporting a per-line confidence score based on benchmark sample
     size and source quality. Higher confidence = higher likelihood the
     estimate will land within 10% of vendor actuals.
  5. Persisting the IGCE in ``igce_estimates`` + ``igce_estimate_line_items``
     so the post-bid ``procurement_quote_compare`` flow can calibrate
     actuals back into ``igce_calibration_log`` and refine future
     estimates.

DB tables (write): gsa_schedule_rates, gsa_market_rates, igce_estimates,
                   igce_estimate_line_items, igce_calibration_log, audit_trail

Usage:
    # Seed a representative GSA Schedule snapshot
    python tools/govcon/igce_estimator.py --seed-gsa --year 2026 --json

    # Store a single rate
    python tools/govcon/igce_estimator.py \\
        --add-gsa-rate --labor-category "Software Developers" \\
        --soc-code 15-1252 --sin 132-51 --hourly-rate 125.00 \\
        --year 2026 --contractor "Acme Federal" --json

    # Generate an IGCE from a line-item spec
    python tools/govcon/igce_estimator.py \\
        --generate --title "Cyber Range Build" --agency "USACE" \\
        --solicitation "W912DY-26-R-0007" \\
        --procurement-id "PROC-2026-001" \\
        --lines-json '[
          {"clin":"0001","description":"Junior SWE",
           "unit":"hour","quantity":160,
           "labor_category":"Software Developers","bls_soc_code":"15-1252"},
          {"clin":"0002","description":"PM",
           "unit":"hour","quantity":40,
           "labor_category":"Project Management Specialists","bls_soc_code":"13-1082"}
        ]' --json

    # Get/fetch/list
    python tools/govcon/igce_estimator.py --get --igce-id "igce-xxxxxxxx" --json
    python tools/govcon/igce_estimator.py --list --json
    python tools/govcon/igce_estimator.py --list --procurement-id "PROC-2026-001" --json

    # Calibrate against actuals
    python tools/govcon/igce_estimator.py \\
        --calibrate --igce-id "igce-xxxxxxxx" \\
        --procurement-id "PROC-2026-001" \\
        --actuals-json '[
          {"clin":"0001","actual_unit_cost":112.50,"actual_vendor":"Acme"}
        ]' --json

    # System-wide accuracy report
    python tools/govcon/igce_estimator.py --accuracy --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── Public constants ───────────────────────────────────────────────────

# Source labels used in benchmark_source column.
GSA_SOURCE = "gsa_schedule"
MARKET_SOURCE = "market"
BLENDED_SOURCE = "blended"
HISTORICAL_SOURCE = "historical"
FALLBACK_SOURCE = "fallback"

# Confidence tiers (0.0-1.0).
CONFIDENCE_GSA_GOOD = 0.85   # GSA Schedule, sample_size >= MIN_BENCHMARK_SAMPLE_SIZE
CONFIDENCE_GSA_FAIR = 0.70   # GSA Schedule, small sample
CONFIDENCE_MARKET_ONLY = 0.55  # Only market data, sample_size >= MIN
CONFIDENCE_MARKET_LOW = 0.40  # Only market data, low sample
CONFIDENCE_FALLBACK = 0.20   # No benchmark at all

# Default fallback hourly rate used when no benchmark is available.
# 100.0 is a deliberately conservative mid-IT-services value — reviewers
# are expected to override before submission.
FALLBACK_HOURLY_RATE = 100.0

# Minimum sample size for a benchmark to be considered "good".
MIN_BENCHMARK_SAMPLE_SIZE = 3

# Target accuracy: system shall produce estimates within 10% of vendor
# actuals.  Exposed for accuracy_report().
TARGET_ACCURACY_PCT = 10.0

# Allowed enumeration values.
STATUSES = ("draft", "reviewed", "submitted", "archived")
STATUS_DRAFT = "draft"
STATUS_REVIEWED = "reviewed"
STATUS_SUBMITTED = "submitted"
STATUS_ARCHIVED = "archived"

METHODS = ("deterministic", "historical_blend", "market_only")

# Reasonable default: 1,880 working hours per year per FTE
HOURS_PER_FTE_YEAR = 1880

# Reasonable default: 173.33 hours per month per FTE
HOURS_PER_FTE_MONTH = 173.33

# Audit actor name
ACTOR = "igce_estimator"


# ── Helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(
    conn,
    event_type: str,
    action: str,
    details: Dict[str, Any],
    project_id: Optional[str] = None,
) -> None:
    """Append-only audit row. Never UPDATE/DELETE."""
    det = json.dumps(details) if isinstance(details, (dict, list)) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                event_type,
                ACTOR,
                action,
                det,
                project_id,
                None,
            ),
        )
    except Exception:
        # Audit must never break the operation.
        pass


# ── DB bootstrap ───────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    """Create IGCE estimator tables if they don't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsa_schedule_rates (
            id                  TEXT PRIMARY KEY,
            labor_category      TEXT NOT NULL,
            bls_soc_code        TEXT,
            sin                 TEXT NOT NULL DEFAULT '',
            schedule_contractor TEXT NOT NULL DEFAULT '',
            hourly_rate         REAL NOT NULL,
            year                INTEGER NOT NULL,
            region              TEXT,
            education_level     TEXT,
            min_years_experience INTEGER,
            source              TEXT NOT NULL DEFAULT 'gsa_schedule',
            created_at          TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsa_market_rates (
            id              TEXT PRIMARY KEY,
            labor_category  TEXT NOT NULL,
            bls_soc_code    TEXT,
            source          TEXT NOT NULL,
            p25_hourly      REAL,
            median_hourly   REAL NOT NULL,
            p75_hourly      REAL,
            sample_size     INTEGER DEFAULT 0,
            year            INTEGER NOT NULL,
            region          TEXT,
            notes           TEXT,
            created_at      TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS igce_estimates (
            id                      TEXT PRIMARY KEY,
            procurement_id          TEXT,
            opportunity_id          TEXT,
            solicitation            TEXT NOT NULL DEFAULT '',
            agency                  TEXT NOT NULL DEFAULT '',
            title                   TEXT NOT NULL DEFAULT '',
            period_of_performance   TEXT,
            estimation_method       TEXT NOT NULL DEFAULT 'deterministic',
            status                  TEXT NOT NULL DEFAULT 'draft',
            total_estimated_cost    REAL NOT NULL DEFAULT 0.0,
            total_low_estimate      REAL NOT NULL DEFAULT 0.0,
            total_high_estimate     REAL NOT NULL DEFAULT 0.0,
            within_10pct_confidence REAL,
            benchmark_source        TEXT,
            benchmark_sample_size   INTEGER DEFAULT 0,
            notes                   TEXT,
            created_by              TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS igce_estimate_line_items (
            id                      TEXT PRIMARY KEY,
            igce_estimate_id        TEXT NOT NULL,
            clin                    TEXT NOT NULL DEFAULT '',
            description             TEXT NOT NULL,
            unit                    TEXT NOT NULL DEFAULT 'each',
            quantity                REAL NOT NULL DEFAULT 1.0,
            unit_cost_estimate      REAL NOT NULL DEFAULT 0.0,
            unit_cost_low           REAL,
            unit_cost_high          REAL,
            extended_cost           REAL NOT NULL DEFAULT 0.0,
            bls_soc_code            TEXT,
            labor_category          TEXT,
            benchmark_source        TEXT,
            benchmark_rate          REAL,
            benchmark_year          INTEGER,
            benchmark_n             INTEGER DEFAULT 0,
            confidence              REAL,
            rationale               TEXT,
            created_at              TEXT NOT NULL,
            FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS igce_calibration_log (
            id                  TEXT PRIMARY KEY,
            igce_estimate_id    TEXT NOT NULL,
            procurement_id      TEXT,
            clin                TEXT NOT NULL DEFAULT '',
            estimated_unit_cost REAL NOT NULL,
            actual_unit_cost    REAL NOT NULL,
            actual_vendor       TEXT,
            variance_pct        REAL NOT NULL,
            within_10pct        INTEGER NOT NULL,
            benchmark_source    TEXT,
            confidence_predicted REAL,
            captured_at         TEXT NOT NULL,
            FOREIGN KEY (igce_estimate_id) REFERENCES igce_estimates(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gsa_rate_lc ON gsa_schedule_rates(labor_category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gsa_rate_soc ON gsa_schedule_rates(bls_soc_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gsa_rate_year ON gsa_schedule_rates(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_rate_lc ON gsa_market_rates(labor_category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_rate_source ON gsa_market_rates(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_est_proc ON igce_estimates(procurement_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_est_opp ON igce_estimates(opportunity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_est_status ON igce_estimates(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_line_est ON igce_estimate_line_items(igce_estimate_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_cal_est ON igce_calibration_log(igce_estimate_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_igce_cal_proc ON igce_calibration_log(procurement_id)")


# ── Reference data: GSA Schedule rates ────────────────────────────────

def add_gsa_rate(
    labor_category: str,
    hourly_rate: float,
    year: int,
    bls_soc_code: Optional[str] = None,
    sin: str = "",
    schedule_contractor: str = "",
    region: Optional[str] = None,
    education_level: Optional[str] = None,
    min_years_experience: Optional[int] = None,
) -> Dict[str, Any]:
    """Store a single GSA Schedule rate row.

    Multiple rows per labor_category represent multiple contractors; the
    estimator's ``lookup_gsa_rate`` aggregates them via the median.
    """
    if not labor_category:
        return {"status": "error", "message": "labor_category is required"}
    if hourly_rate is None or hourly_rate <= 0:
        return {"status": "error", "message": "hourly_rate must be positive"}
    if year is None:
        return {"status": "error", "message": "year is required"}

    conn = _get_db()
    _ensure_tables(conn)

    rid = _gen_id("gsa")
    conn.execute(
        """
        INSERT INTO gsa_schedule_rates
            (id, labor_category, bls_soc_code, sin, schedule_contractor,
             hourly_rate, year, region, education_level, min_years_experience,
             source, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'gsa_schedule', %s)
        """,
        (
            rid, labor_category, bls_soc_code, sin, schedule_contractor,
            hourly_rate, year, region, education_level, min_years_experience,
            _now(),
        ),
    )
    _audit(conn, "gsa_rate.add", f"Stored GSA rate: {labor_category} ${hourly_rate}/hr",
           {"id": rid, "labor_category": labor_category, "year": year}, None)
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "id": rid,
        "labor_category": labor_category,
        "hourly_rate": hourly_rate,
        "year": year,
    }


# ── Reference data: market rates ──────────────────────────────────────

def add_market_rate(
    labor_category: str,
    source: str,
    median_hourly: float,
    year: int,
    bls_soc_code: Optional[str] = None,
    p25_hourly: Optional[float] = None,
    p75_hourly: Optional[float] = None,
    sample_size: int = 0,
    region: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a market-data benchmark (FPDS-NG, BLS OEWS, vendor awards, etc.)."""
    if not labor_category:
        return {"status": "error", "message": "labor_category is required"}
    if not source:
        return {"status": "error", "message": "source is required"}
    if median_hourly is None or median_hourly <= 0:
        return {"status": "error", "message": "median_hourly must be positive"}
    if year is None:
        return {"status": "error", "message": "year is required"}

    conn = _get_db()
    _ensure_tables(conn)

    rid = _gen_id("mkt")
    conn.execute(
        """
        INSERT INTO gsa_market_rates
            (id, labor_category, bls_soc_code, source,
             p25_hourly, median_hourly, p75_hourly, sample_size,
             year, region, notes, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            rid, labor_category, bls_soc_code, source,
            p25_hourly, median_hourly, p75_hourly, sample_size,
            year, region, notes, _now(),
        ),
    )
    _audit(conn, "market_rate.add", f"Stored {source} rate: {labor_category} ${median_hourly}/hr",
           {"id": rid, "labor_category": labor_category, "source": source, "year": year}, None)
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "id": rid,
        "labor_category": labor_category,
        "source": source,
        "median_hourly": median_hourly,
        "year": year,
    }


# ── Lookup helpers ────────────────────────────────────────────────────

def lookup_gsa_rate(
    labor_category: str,
    soc_code: Optional[str] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """Return median GSA Schedule rate for the given category.

    Prefers exact SOC-code matches; falls back to category-only match.
    When ``year`` is given, prefers rates for that year; otherwise picks
    the most recent year.
    """
    conn = _get_db()
    _ensure_tables(conn)

    if not labor_category:
        conn.close()
        return {"status": "error", "message": "labor_category is required"}

    params: List[Any] = [labor_category]
    query = "SELECT hourly_rate, year, bls_soc_code FROM gsa_schedule_rates WHERE labor_category = ?"
    if soc_code:
        query += " AND bls_soc_code = ?"
        params.append(soc_code)
    if year is not None:
        query += " AND year = ?"
        params.append(year)
    query += " ORDER BY year DESC"

    rows = conn.execute(query, params).fetchall()
    # If we filtered by year+SOC and got nothing, drop the year filter
    if not rows and year is not None:
        params2: List[Any] = [labor_category]
        query2 = "SELECT hourly_rate, year, bls_soc_code FROM gsa_schedule_rates WHERE labor_category = ?"
        if soc_code:
            query2 += " AND bls_soc_code = ?"
            params2.append(soc_code)
        query2 += " ORDER BY year DESC"
        rows = conn.execute(query2, params2).fetchall()
    # If still nothing and we filtered by SOC, drop the SOC filter too
    if not rows and soc_code:
        rows = conn.execute(
            "SELECT hourly_rate, year, bls_soc_code FROM gsa_schedule_rates "
            "WHERE labor_category = %s ORDER BY year DESC",
            (labor_category,),
        ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "source": FALLBACK_SOURCE,
            "labor_category": labor_category,
            "soc_code": soc_code,
            "median_rate": None,
            "sample_size": 0,
            "year": year,
        }

    rates = [r[0] if not isinstance(r, dict) else r["hourly_rate"] for r in rows]
    median = statistics.median(rates) if rates else None
    years = [r[1] if not isinstance(r, dict) else r["year"] for r in rows]
    return {
        "status": "ok",
        "source": GSA_SOURCE,
        "labor_category": labor_category,
        "soc_code": soc_code,
        "median_rate": round(median, 2) if median is not None else None,
        "sample_size": len(rates),
        "year": max(years) if years else year,
    }


def lookup_market_rate(
    labor_category: str,
    soc_code: Optional[str] = None,
    year: Optional[int] = None,
    prefer_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return median market rate for the given category.

    Picks the most recent year available. If ``prefer_source`` is given
    (e.g. ``"fpds_ng"``) it takes precedence over other sources.
    """
    conn = _get_db()
    _ensure_tables(conn)

    if not labor_category:
        conn.close()
        return {"status": "error", "message": "labor_category is required"}

    params: List[Any] = [labor_category]
    query = (
        "SELECT source, median_hourly, p25_hourly, p75_hourly, sample_size, year "
        "FROM gsa_market_rates WHERE labor_category = ?"
    )
    if soc_code:
        query += " AND bls_soc_code = ?"
        params.append(soc_code)
    if year is not None:
        query += " AND year = ?"
        params.append(year)
    if prefer_source:
        query += " AND source = ?"
        params.append(prefer_source)
    query += " ORDER BY year DESC, sample_size DESC LIMIT 1"

    row = conn.execute(query, params).fetchone()
    if not row and year is not None:
        # Drop the year filter, keep SOC + source
        params2: List[Any] = [labor_category]
        q2 = (
            "SELECT source, median_hourly, p25_hourly, p75_hourly, sample_size, year "
            "FROM gsa_market_rates WHERE labor_category = ?"
        )
        if soc_code:
            q2 += " AND bls_soc_code = ?"
            params2.append(soc_code)
        if prefer_source:
            q2 += " AND source = ?"
            params2.append(prefer_source)
        q2 += " ORDER BY year DESC, sample_size DESC LIMIT 1"
        row = conn.execute(q2, params2).fetchone()
    if not row and (soc_code or prefer_source):
        # Drop both filters, just match labor_category
        row = conn.execute(
            "SELECT source, median_hourly, p25_hourly, p75_hourly, sample_size, year "
            "FROM gsa_market_rates WHERE labor_category = %s "
            "ORDER BY year DESC, sample_size DESC LIMIT 1",
            (labor_category,),
        ).fetchone()
    conn.close()

    if not row:
        return {
            "status": "ok",
            "source": FALLBACK_SOURCE,
            "labor_category": labor_category,
            "soc_code": soc_code,
            "median_rate": None,
            "p25_hourly": None,
            "p75_hourly": None,
            "sample_size": 0,
            "year": year,
        }

    if isinstance(row, dict):
        return {
            "status": "ok",
            "source": row.get("source"),
            "labor_category": labor_category,
            "soc_code": soc_code,
            "median_rate": row.get("median_hourly"),
            "p25_hourly": row.get("p25_hourly"),
            "p75_hourly": row.get("p75_hourly"),
            "sample_size": row.get("sample_size", 0),
            "year": row.get("year"),
        }
    return {
        "status": "ok",
        "source": row[0],
        "labor_category": labor_category,
        "soc_code": soc_code,
        "median_rate": row[1],
        "p25_hourly": row[2],
        "p75_hourly": row[3],
        "sample_size": row[4] or 0,
        "year": row[5],
    }


# ── Confidence scoring ────────────────────────────────────────────────

def _score_confidence(benchmark_source: str, sample_size: int) -> float:
    """Return a 0.0-1.0 confidence in the estimate hitting within 10%."""
    if benchmark_source == GSA_SOURCE:
        if sample_size >= MIN_BENCHMARK_SAMPLE_SIZE:
            return CONFIDENCE_GSA_GOOD
        if sample_size >= 1:
            return CONFIDENCE_GSA_FAIR
    if benchmark_source == MARKET_SOURCE:
        if sample_size >= MIN_BENCHMARK_SAMPLE_SIZE:
            return CONFIDENCE_MARKET_ONLY
        if sample_size >= 1:
            return CONFIDENCE_MARKET_LOW
    if benchmark_source == BLENDED_SOURCE:
        return CONFIDENCE_GSA_FAIR
    if benchmark_source == HISTORICAL_SOURCE:
        return CONFIDENCE_MARKET_ONLY
    return CONFIDENCE_FALLBACK


def _build_rationale(source: str, sample_size: int, year: Optional[int]) -> str:
    if source == GSA_SOURCE:
        yr = year if year is not None else "n/a"
        return f"GSA Schedule median across {sample_size} contractor(s), year {yr}"
    if source == MARKET_SOURCE:
        return f"Market data median (sample n={sample_size}, year {year or 'n/a'})"
    if source == FALLBACK_SOURCE:
        return (
            "No benchmark available — using fallback rate ${:.2f}/hr. "
            "Reviewer MUST replace with a sourced rate before submission."
        ).format(FALLBACK_HOURLY_RATE)
    return f"Benchmark source: {source}"


# ── Variance math ─────────────────────────────────────────────────────

def _variance_pct(actual: Optional[float], estimate: Optional[float]) -> Optional[float]:
    """Return (actual - estimate) / estimate * 100, rounded to 2 dp.

    None when either input is missing or estimate is zero/negative.
    """
    if estimate is None or estimate <= 0:
        return None
    if actual is None:
        return None
    return round(((actual - estimate) / estimate) * 100.0, 2)


# ── Bulk GSA seed ─────────────────────────────────────────────────────

# Default GSA Schedule snapshot (representative FY2026 rates).
# Each entry: (labor_category, bls_soc_code, sin, hourly_rate)
# Rates drawn from typical GSA MAS 70 / 132-51 published ceilings for
# mid-tier federal IT services.  Used for demos + the seed_gsa_catalog
# helper.  Real GSA rates should replace this before live use.
_GSA_CATALOG_2026: List[Tuple[str, str, str, float]] = [
    ("Software Developers", "15-1252", "132-51", 110.0),
    ("Software Developers", "15-1252", "132-51", 125.0),
    ("Software Developers", "15-1252", "132-51", 100.0),
    ("Information Security Analysts", "15-1212", "132-51", 130.0),
    ("Information Security Analysts", "15-1212", "132-51", 145.0),
    ("Information Security Analysts", "15-1212", "132-51", 118.0),
    ("Computer Systems Analysts", "15-1211", "132-51", 100.0),
    ("Computer Systems Analysts", "15-1211", "132-51", 92.0),
    ("Computer Systems Analysts", "15-1211", "132-51", 110.0),
    ("Project Management Specialists", "13-1082", "132-51", 115.0),
    ("Project Management Specialists", "13-1082", "132-51", 125.0),
    ("Project Management Specialists", "13-1082", "132-51", 105.0),
    ("Network and Computer Systems Administrators", "15-1244", "132-51", 95.0),
    ("Network and Computer Systems Administrators", "15-1244", "132-51", 105.0),
    ("Database Administrators and Architects", "15-1245", "132-51", 120.0),
    ("Database Administrators and Architects", "15-1245", "132-51", 130.0),
    ("Data Scientists and Mathematical Science Occupations", "15-2051", "132-51", 135.0),
    ("Management Analysts", "13-1111", "132-51", 110.0),
    ("Computer and Information Systems Managers", "11-3021", "132-51", 165.0),
    ("Technical Writers", "27-3042", "132-51", 80.0),
]


def seed_gsa_catalog(year: int = 2026) -> Dict[str, Any]:
    """Bulk-load a representative GSA Schedule snapshot for ``year``.

    Idempotent: existing rows for the same (labor_category, SOC, SIN,
    contractor) are not duplicated.  The seed uses the labor category
    as the contractor label, which makes the dedupe check safe.
    """
    conn = _get_db()
    _ensure_tables(conn)

    inserted = 0
    skipped = 0
    for lc, soc, sin, rate in _GSA_CATALOG_2026:
        contractor = f"seed-{lc[:8]}-{int(rate)}"
        existing = conn.execute(
            "SELECT 1 FROM gsa_schedule_rates "
            "WHERE labor_category = %s AND bls_soc_code = %s AND sin = %s "
            "AND schedule_contractor = %s AND year = %s",
            (lc, soc, sin, contractor, year),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO gsa_schedule_rates
                (id, labor_category, bls_soc_code, sin, schedule_contractor,
                 hourly_rate, year, region, education_level, min_years_experience,
                 source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, 'gsa_schedule', %s)
            """,
            (_gen_id("gsa"), lc, soc, sin, contractor, rate, year, _now()),
        )
        inserted += 1

    _audit(conn, "gsa_rate.seed", f"Seeded GSA catalog year={year}",
           {"inserted": inserted, "skipped": skipped}, None)
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "year": year,
        "rows_inserted": inserted,
        "rows_skipped": skipped,
    }


# ── Main estimator: generate_igce ─────────────────────────────────────

def _benchmark_line(
    labor_category: str,
    soc_code: Optional[str],
    year: Optional[int],
) -> Dict[str, Any]:
    """Resolve benchmark (rate + low/high band + source) for one line.

    Lookup order: GSA Schedule → market data → fallback.
    """
    gsa = lookup_gsa_rate(labor_category, soc_code=soc_code, year=year)
    if gsa.get("median_rate") is not None and gsa.get("source") == GSA_SOURCE:
        median = float(gsa["median_rate"])
        n = int(gsa.get("sample_size", 0))
        # GSA band: ±10% around median
        return {
            "source": GSA_SOURCE,
            "rate": round(median, 2),
            "low": round(median * 0.9, 2),
            "high": round(median * 1.1, 2),
            "year": gsa.get("year"),
            "sample_size": n,
        }

    mkt = lookup_market_rate(labor_category, soc_code=soc_code, year=year)
    if mkt.get("median_rate") is not None and mkt.get("source") != FALLBACK_SOURCE:
        median = float(mkt["median_rate"])
        p25 = mkt.get("p25_hourly")
        p75 = mkt.get("p75_hourly")
        n = int(mkt.get("sample_size", 0))
        if p25 is not None and p75 is not None and p25 > 0 and p75 > 0:
            low, high = float(p25), float(p75)
        else:
            # No IQR — fall back to ±15% of median
            low, high = median * 0.85, median * 1.15
        return {
            "source": MARKET_SOURCE,
            "rate": round(median, 2),
            "low": round(low, 2),
            "high": round(high, 2),
            "year": mkt.get("year"),
            "sample_size": n,
        }

    return {
        "source": FALLBACK_SOURCE,
        "rate": FALLBACK_HOURLY_RATE,
        "low": FALLBACK_HOURLY_RATE * 0.85,
        "high": FALLBACK_HOURLY_RATE * 1.15,
        "year": year,
        "sample_size": 0,
    }


def _validate_line_spec(line: Dict[str, Any]) -> Optional[str]:
    if not isinstance(line, dict):
        return "line must be a dict"
    if not line.get("description"):
        return "description is required"
    if not line.get("labor_category"):
        return "labor_category is required"
    qty = line.get("quantity")
    if qty is None or qty <= 0:
        return "quantity must be > 0"
    unit = line.get("unit") or "each"
    if not isinstance(unit, str):
        return "unit must be a string"
    return None


def generate_igce(
    title: str,
    line_specs: List[Dict[str, Any]],
    agency: str = "",
    solicitation: str = "",
    procurement_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    period_of_performance: Optional[str] = None,
    estimation_method: str = "deterministic",
    created_by: Optional[str] = None,
    notes: Optional[str] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate an IGCE from a list of line-item specifications.

    Each line spec is a dict with keys: clin, description, unit, quantity,
    labor_category, bls_soc_code.  Each line is benchmarked against GSA
    Schedule first, then market data, then a documented fallback rate.

    Returns the persisted IGCE (header + lines + confidence).
    """
    if not title:
        return {"status": "error", "message": "title is required"}
    if not line_specs:
        return {"status": "error", "message": "line_specs must not be empty"}
    if estimation_method not in METHODS:
        return {
            "status": "error",
            "message": f"estimation_method must be one of {METHODS}",
        }

    # Validate line specs up front
    for i, line in enumerate(line_specs):
        err = _validate_line_spec(line)
        if err is not None:
            return {
                "status": "error",
                "message": f"line[{i}]: {err}",
                "line_index": i,
            }

    target_year = year or datetime.now(timezone.utc).year

    conn = _get_db()
    _ensure_tables(conn)

    # Resolve each line
    resolved_lines: List[Dict[str, Any]] = []
    total = 0.0
    total_low = 0.0
    total_high = 0.0
    total_confidence = 0.0
    bench_source_counts: Dict[str, int] = {}
    total_sample = 0

    for line in line_specs:
        lc = line["labor_category"]
        soc = line.get("bls_soc_code")
        bench = _benchmark_line(lc, soc, target_year)
        qty = float(line["quantity"])
        est_unit = float(bench["rate"])
        low_unit = float(bench["low"])
        high_unit = float(bench["high"])
        ext = qty * est_unit
        ext_low = qty * low_unit
        ext_high = qty * high_unit
        confidence = _score_confidence(bench["source"], bench["sample_size"])
        rationale = _build_rationale(bench["source"], bench["sample_size"], bench["year"])

        resolved_lines.append({
            "clin": line.get("clin", ""),
            "description": line["description"],
            "unit": line.get("unit", "each"),
            "quantity": qty,
            "labor_category": lc,
            "bls_soc_code": soc or "",
            "unit_cost_estimate": round(est_unit, 2),
            "unit_cost_low": round(low_unit, 2),
            "unit_cost_high": round(high_unit, 2),
            "extended_cost": round(ext, 2),
            "extended_low": round(ext_low, 2),
            "extended_high": round(ext_high, 2),
            "benchmark_source": bench["source"],
            "benchmark_rate": round(est_unit, 2),
            "benchmark_year": bench["year"],
            "benchmark_n": bench["sample_size"],
            "confidence": round(confidence, 3),
            "rationale": rationale,
        })
        total += ext
        total_low += ext_low
        total_high += ext_high
        total_confidence += confidence
        bench_source_counts[bench["source"]] = bench_source_counts.get(bench["source"], 0) + 1
        total_sample += bench["sample_size"]

    # Pick the dominant benchmark source for the IGCE header
    dominant_source = max(bench_source_counts, key=bench_source_counts.get) if bench_source_counts else FALLBACK_SOURCE
    avg_confidence = total_confidence / len(resolved_lines) if resolved_lines else 0.0

    # Persist header
    igce_id = _gen_id("igce")
    now = _now()
    conn.execute(
        """
        INSERT INTO igce_estimates
            (id, procurement_id, opportunity_id, solicitation, agency, title,
             period_of_performance, estimation_method, status,
             total_estimated_cost, total_low_estimate, total_high_estimate,
             within_10pct_confidence, benchmark_source, benchmark_sample_size,
             notes, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            igce_id, procurement_id, opportunity_id, solicitation, agency, title,
            period_of_performance, estimation_method,
            round(total, 2), round(total_low, 2), round(total_high, 2),
            round(avg_confidence, 3), dominant_source, total_sample,
            notes, created_by, now, now,
        ),
    )

    # Persist line items
    line_ids: List[str] = []
    persisted_lines: List[Dict[str, Any]] = []
    for line in resolved_lines:
        lid = _gen_id("igln")
        conn.execute(
            """
            INSERT INTO igce_estimate_line_items
                (id, igce_estimate_id, clin, description, unit, quantity,
                 unit_cost_estimate, unit_cost_low, unit_cost_high, extended_cost,
                 bls_soc_code, labor_category,
                 benchmark_source, benchmark_rate, benchmark_year, benchmark_n,
                 confidence, rationale, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lid, igce_id, line["clin"], line["description"], line["unit"], line["quantity"],
                line["unit_cost_estimate"], line["unit_cost_low"], line["unit_cost_high"],
                line["extended_cost"],
                line["bls_soc_code"], line["labor_category"],
                line["benchmark_source"], line["benchmark_rate"], line["benchmark_year"],
                line["benchmark_n"], line["confidence"], line["rationale"], now,
            ),
        )
        line_ids.append(lid)
        line["id"] = lid
        persisted_lines.append(line)

    _audit(conn, "igce.generate", f"Generated IGCE: {title}",
           {
               "igce_id": igce_id,
               "line_count": len(persisted_lines),
               "total_estimated_cost": round(total, 2),
               "benchmark_source": dominant_source,
               "avg_confidence": round(avg_confidence, 3),
           },
           igce_id)
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "igce_id": igce_id,
        "line_count": len(persisted_lines),
        "lines": persisted_lines,
        "line_ids": line_ids,
        "total_estimated_cost": round(total, 2),
        "total_low_estimate": round(total_low, 2),
        "total_high_estimate": round(total_high, 2),
        "within_10pct_confidence": round(avg_confidence, 3),
        "benchmark_source": dominant_source,
        "benchmark_sample_size": total_sample,
        "status_label": STATUS_DRAFT,
        "created_at": now,
    }


# ── CRUD: get / list / append line ────────────────────────────────────

def get_igce(igce_id: str) -> Dict[str, Any]:
    """Fetch a single IGCE with all its line items."""
    if not igce_id:
        return {"status": "error", "message": "igce_id is required"}

    conn = _get_db()
    _ensure_tables(conn)
    row = conn.execute(
        "SELECT * FROM igce_estimates WHERE id = %s", (igce_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"igce_id {igce_id} not found"}

    igce = dict(row) if hasattr(row, "keys") else {
        "id": row[0], "procurement_id": row[1], "opportunity_id": row[2],
        "solicitation": row[3], "agency": row[4], "title": row[5],
        "period_of_performance": row[6], "estimation_method": row[7],
        "status": row[8], "total_estimated_cost": row[9],
        "total_low_estimate": row[10], "total_high_estimate": row[11],
        "within_10pct_confidence": row[12], "benchmark_source": row[13],
        "benchmark_sample_size": row[14], "notes": row[15], "created_by": row[16],
        "created_at": row[17], "updated_at": row[18],
    }

    line_rows = conn.execute(
        "SELECT * FROM igce_estimate_line_items WHERE igce_estimate_id = %s ORDER BY clin, created_at",
        (igce_id,),
    ).fetchall()
    lines = []
    total = 0.0
    for r in line_rows:
        d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "igce_estimate_id": r[1], "clin": r[2],
            "description": r[3], "unit": r[4], "quantity": r[5],
            "unit_cost_estimate": r[6], "unit_cost_low": r[7], "unit_cost_high": r[8],
            "extended_cost": r[9], "bls_soc_code": r[10], "labor_category": r[11],
            "benchmark_source": r[12], "benchmark_rate": r[13],
            "benchmark_year": r[14], "benchmark_n": r[15], "confidence": r[16],
            "rationale": r[17], "created_at": r[18],
        }
        lines.append(d)
        total += float(d.get("extended_cost") or 0.0)
    conn.close()
    return {
        "status": "ok",
        "igce": igce,
        "lines": lines,
        "line_count": len(lines),
    }


def list_igces(
    procurement_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """List IGCE estimates with optional filters."""
    conn = _get_db()
    _ensure_tables(conn)

    query = "SELECT * FROM igce_estimates WHERE 1=1"
    params: List[Any] = []
    if procurement_id:
        query += " AND procurement_id = ?"
        params.append(procurement_id)
    if opportunity_id:
        query += " AND opportunity_id = ?"
        params.append(opportunity_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    igces = [dict(r) if hasattr(r, "keys") else {"id": r[0]} for r in rows]
    return {
        "status": "ok",
        "count": len(igces),
        "igces": igces,
    }


def add_igce_line(
    igce_estimate_id: str,
    description: str,
    unit_cost_estimate: float,
    unit: str = "each",
    quantity: float = 1.0,
    clin: str = "",
    labor_category: Optional[str] = None,
    bls_soc_code: Optional[str] = None,
    rationale: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a manually-priced line to an existing IGCE.

    Used by reviewers to add lines the estimator cannot derive (e.g.
    ODCs, travel, equipment rentals).  The IGCE totals are recomputed
    after the line is added.
    """
    if not igce_estimate_id:
        return {"status": "error", "message": "igce_estimate_id is required"}
    if not description:
        return {"status": "error", "message": "description is required"}
    if quantity is None or quantity <= 0:
        return {"status": "error", "message": "quantity must be > 0"}
    if unit_cost_estimate is None or unit_cost_estimate < 0:
        return {"status": "error", "message": "unit_cost_estimate must be >= 0"}

    conn = _get_db()
    _ensure_tables(conn)
    existing = conn.execute(
        "SELECT id FROM igce_estimates WHERE id = %s", (igce_estimate_id,)
    ).fetchone()
    if not existing:
        conn.close()
        return {"status": "error", "message": f"igce_estimate_id {igce_estimate_id} not found"}

    lid = _gen_id("igln")
    extended = round(float(quantity) * float(unit_cost_estimate), 2)
    now = _now()
    conn.execute(
        """
        INSERT INTO igce_estimate_line_items
            (id, igce_estimate_id, clin, description, unit, quantity,
             unit_cost_estimate, unit_cost_low, unit_cost_high, extended_cost,
             bls_soc_code, labor_category,
             benchmark_source, benchmark_rate, benchmark_year, benchmark_n,
             confidence, rationale, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, 'manual', %s, NULL, 0, NULL, %s, %s)
        """,
        (
            lid, igce_estimate_id, clin, description, unit, float(quantity),
            float(unit_cost_estimate), extended, bls_soc_code or "", labor_category or "",
            float(unit_cost_estimate), rationale, now,
        ),
    )

    # Recompute totals
    line_rows = conn.execute(
        "SELECT extended_cost FROM igce_estimate_line_items WHERE igce_estimate_id = %s",
        (igce_estimate_id,),
    ).fetchall()
    new_total = sum(
        float(r[0]) if not isinstance(r, dict) else float(r["extended_cost"])
        for r in line_rows
    )
    conn.execute(
        "UPDATE igce_estimates SET total_estimated_cost = %s, updated_at = %s WHERE id = %s",
        (round(new_total, 2), now, igce_estimate_id),
    )
    _audit(conn, "igce.add_line", f"Added line {clin or '(no clin)'} to {igce_estimate_id}",
           {"line_id": lid, "extended_cost": extended, "new_total": round(new_total, 2)},
           igce_estimate_id)
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "line_id": lid,
        "extended_cost": extended,
        "igce_total": round(new_total, 2),
        "action": "created",
    }


# ── Calibration: actuals vs estimate ──────────────────────────────────

def calibrate_igce(
    igce_estimate_id: str,
    actuals: List[Dict[str, Any]],
    procurement_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Log vendor actuals against an IGCE and flag within-10% hits.

    Each entry in ``actuals`` is a dict with keys:
      - clin (str) — must match an IGCE line item
      - actual_unit_cost (float) — winning vendor's actual unit price
      - actual_vendor (str, optional)
    """
    if not igce_estimate_id:
        return {"status": "error", "message": "igce_estimate_id is required"}
    if not actuals:
        return {"status": "error", "message": "actuals must not be empty"}

    conn = _get_db()
    _ensure_tables(conn)
    existing = conn.execute(
        "SELECT id FROM igce_estimates WHERE id = %s", (igce_estimate_id,)
    ).fetchone()
    if not existing:
        conn.close()
        return {"status": "error", "message": f"igce_estimate_id {igce_estimate_id} not found"}

    # Build a {clin: line} map for fast lookup
    line_rows = conn.execute(
        "SELECT clin, unit_cost_estimate, benchmark_source, confidence "
        "FROM igce_estimate_line_items WHERE igce_estimate_id = %s",
        (igce_estimate_id,),
    ).fetchall()
    line_map: Dict[str, Dict[str, Any]] = {}
    for r in line_rows:
        if isinstance(r, dict):
            line_map[r.get("clin", "")] = {
                "estimate": r.get("unit_cost_estimate"),
                "source": r.get("benchmark_source"),
                "confidence": r.get("confidence"),
            }
        else:
            line_map[r[0] or ""] = {
                "estimate": r[1],
                "source": r[2],
                "confidence": r[3],
            }

    rows: List[Dict[str, Any]] = []
    now = _now()
    for a in actuals:
        clin = a.get("clin", "")
        actual = a.get("actual_unit_cost")
        vendor = a.get("actual_vendor")
        line = line_map.get(clin)
        if line is None:
            rows.append({
                "clin": clin,
                "variance_pct": None,
                "within_10pct": None,
                "error": "no matching IGCE line for clin",
            })
            continue
        estimate = line.get("estimate")
        var = _variance_pct(actual, estimate)
        within = bool(var is not None and abs(var) <= TARGET_ACCURACY_PCT)
        cid = _gen_id("igcal")
        conn.execute(
            """
            INSERT INTO igce_calibration_log
                (id, igce_estimate_id, procurement_id, clin,
                 estimated_unit_cost, actual_unit_cost, actual_vendor,
                 variance_pct, within_10pct, benchmark_source,
                 confidence_predicted, captured_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cid, igce_estimate_id, procurement_id, clin,
                float(estimate or 0.0), float(actual or 0.0), vendor or "",
                float(var or 0.0), 1 if within else 0, line.get("source") or "",
                float(line.get("confidence") or 0.0), now,
            ),
        )
        rows.append({
            "clin": clin,
            "estimated_unit_cost": float(estimate or 0.0),
            "actual_unit_cost": float(actual or 0.0),
            "variance_pct": var,
            "within_10pct": within,
            "benchmark_source": line.get("source"),
            "actual_vendor": vendor,
        })

    _audit(conn, "igce.calibrate",
           f"Calibrated {len(rows)} actuals against {igce_estimate_id}",
           {"calibrated": len([r for r in rows if r.get("variance_pct") is not None]),
            "within_10pct": len([r for r in rows if r.get("within_10pct")])},
           igce_estimate_id)
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "igce_estimate_id": igce_estimate_id,
        "calibrated": len([r for r in rows if r.get("variance_pct") is not None]),
        "rows": rows,
    }


def accuracy_report(limit: int = 10000) -> Dict[str, Any]:
    """Roll-up: how often do our IGCEs land within 10% of vendor actuals?

    Reads from ``igce_calibration_log``.  Returns the within-10% hit rate
    and whether the system is meeting the target.
    """
    conn = _get_db()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT variance_pct, within_10pct, benchmark_source, confidence_predicted "
        "FROM igce_calibration_log ORDER BY captured_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "sample_size": 0,
            "within_10pct_count": 0,
            "within_10pct_rate": None,
            "mean_abs_variance_pct": None,
            "target_pct": TARGET_ACCURACY_PCT,
            "meets_target": False,
            "by_source": {},
        }

    within_count = 0
    abs_variances: List[float] = []
    by_source: Dict[str, Dict[str, int]] = {}
    for r in rows:
        if isinstance(r, dict):
            within = bool(r.get("within_10pct"))
            var = r.get("variance_pct")
            src = r.get("benchmark_source") or "unknown"
        else:
            within = bool(r[1])
            var = r[0]
            src = r[2] or "unknown"
        if within:
            within_count += 1
        if var is not None:
            abs_variances.append(abs(float(var)))
        bucket = by_source.setdefault(src, {"total": 0, "within_10pct": 0})
        bucket["total"] += 1
        if within:
            bucket["within_10pct"] += 1

    sample = len(rows)
    rate = within_count / sample if sample else 0.0
    mean_abs = round(sum(abs_variances) / len(abs_variances), 2) if abs_variances else None

    # System meets target if the rate is at or above 1.0 — i.e., all
    # observed actuals were within 10%. (System requirement: "within
    # 10% of vendor actuals".)
    meets_target = rate >= 1.0 if sample else False

    return {
        "status": "ok",
        "sample_size": sample,
        "within_10pct_count": within_count,
        "within_10pct_rate": round(rate, 4),
        "mean_abs_variance_pct": mean_abs,
        "target_pct": TARGET_ACCURACY_PCT,
        "meets_target": meets_target,
        "by_source": by_source,
    }


# ── Module-level aliases for backwards compat with skill invocation ──

def run(config: Optional[Dict[str, Any]] = None, trust: Any = None) -> Dict[str, Any]:
    """Module-level entry point used by the proposal_genesis reflex dispatcher.

    Returns the system-wide accuracy report.
    """
    return accuracy_report()


MAIN = "main"

# CLI entry point
main_module = MAIN  # alias for tests that import MAIN

# Re-export for icdev shim
__all__ = [
    # Reference data
    "add_gsa_rate", "add_market_rate", "lookup_gsa_rate", "lookup_market_rate",
    "seed_gsa_catalog",
    # IGCE lifecycle
    "generate_igce", "get_igce", "list_igces", "add_igce_line",
    # Calibration
    "calibrate_igce", "accuracy_report",
    # Helpers
    "_ensure_tables", "_variance_pct",
    # Constants
    "GSA_SOURCE", "MARKET_SOURCE", "BLENDED_SOURCE", "HISTORICAL_SOURCE",
    "FALLBACK_SOURCE", "STATUSES", "STATUS_DRAFT", "STATUS_REVIEWED",
    "STATUS_SUBMITTED", "STATUS_ARCHIVED", "METHODS",
    "TARGET_ACCURACY_PCT", "MIN_BENCHMARK_SAMPLE_SIZE",
    "CONFIDENCE_FALLBACK", "CONFIDENCE_GSA_GOOD", "CONFIDENCE_GSA_FAIR",
    "CONFIDENCE_MARKET_ONLY", "CONFIDENCE_MARKET_LOW",
    "FALLBACK_HOURLY_RATE", "HOURS_PER_FTE_YEAR", "HOURS_PER_FTE_MONTH",
    "ACTOR",
    # Module-level
    "main", "run",
]


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> int:
    """CLI entry point. Returns 0 on success, 1 on validation error."""
    parser = argparse.ArgumentParser(
        description="IGCE Estimator — pre-bid cost estimates within 10% of vendor actuals",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--seed-gsa", action="store_true",
                       help="Bulk-load a representative GSA Schedule snapshot")
    group.add_argument("--add-gsa-rate", action="store_true",
                       help="Store a single GSA Schedule rate")
    group.add_argument("--add-market-rate", action="store_true",
                       help="Store a single market-data benchmark")
    group.add_argument("--generate", action="store_true",
                       help="Generate an IGCE from a line-item spec")
    group.add_argument("--get", action="store_true", help="Fetch a single IGCE")
    group.add_argument("--list", action="store_true", help="List IGCEs")
    group.add_argument("--calibrate", action="store_true",
                       help="Log vendor actuals against an IGCE")
    group.add_argument("--accuracy", action="store_true",
                       help="System-wide accuracy report")

    parser.add_argument("--igce-id", help="IGCE estimate id (--get, --calibrate)")
    parser.add_argument("--procurement-id", help="Procurement id (--generate, --list, --calibrate)")
    parser.add_argument("--opportunity-id", help="Opportunity id (--generate, --list)")
    parser.add_argument("--solicitation", help="Solicitation number (--generate)")
    parser.add_argument("--agency", help="Agency / customer (--generate)")
    parser.add_argument("--title", help="IGCE title (--generate)")
    parser.add_argument("--period-of-performance", help="PoP string (--generate)")
    parser.add_argument("--year", type=int, help="Rate year (--seed-gsa, --add-*)")
    parser.add_argument("--labor-category", help="Labor category (--add-*)")
    parser.add_argument("--soc-code", help="BLS SOC code (--add-gsa-rate, lookup)")
    parser.add_argument("--sin", help="GSA Schedule SIN (--add-gsa-rate)")
    parser.add_argument("--contractor", help="Schedule contractor (--add-gsa-rate)")
    parser.add_argument("--source", help="Market data source (--add-market-rate)")
    parser.add_argument("--hourly-rate", type=float, help="Hourly rate (--add-gsa-rate)")
    parser.add_argument("--median-hourly", type=float, help="Median hourly (--add-market-rate)")
    parser.add_argument("--p25-hourly", type=float, help="P25 hourly (--add-market-rate)")
    parser.add_argument("--p75-hourly", type=float, help="P75 hourly (--add-market-rate)")
    parser.add_argument("--sample-size", type=int, default=0, help="Sample size (--add-market-rate)")
    parser.add_argument("--notes", help="Free-form notes")
    parser.add_argument("--lines-json", help="JSON list of line specs (--generate)")
    parser.add_argument("--actuals-json", help="JSON list of actuals (--calibrate)")
    parser.add_argument("--json", action="store_true", help="JSON output (default)")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result: Dict[str, Any] = {}

    try:
        if args.seed_gsa:
            year = args.year or datetime.now(timezone.utc).year
            result = seed_gsa_catalog(year=year)
        elif args.add_gsa_rate:
            if not all([args.labor_category, args.hourly_rate, args.year]):
                result = {"status": "error", "message": "Provide --labor-category, --hourly-rate, --year"}
            else:
                result = add_gsa_rate(
                    labor_category=args.labor_category,
                    hourly_rate=args.hourly_rate,
                    year=args.year,
                    bls_soc_code=args.soc_code,
                    sin=args.sin or "",
                    schedule_contractor=args.contractor or "",
                )
        elif args.add_market_rate:
            if not all([args.labor_category, args.source, args.median_hourly, args.year]):
                result = {"status": "error",
                          "message": "Provide --labor-category, --source, --median-hourly, --year"}
            else:
                result = add_market_rate(
                    labor_category=args.labor_category,
                    source=args.source,
                    median_hourly=args.median_hourly,
                    year=args.year,
                    bls_soc_code=args.soc_code,
                    p25_hourly=args.p25_hourly,
                    p75_hourly=args.p75_hourly,
                    sample_size=args.sample_size,
                )
        elif args.generate:
            if not args.title:
                result = {"status": "error", "message": "Provide --title"}
            elif not args.lines_json:
                result = {"status": "error", "message": "Provide --lines-json"}
            else:
                try:
                    lines = json.loads(args.lines_json)
                except json.JSONDecodeError as exc:
                    result = {"status": "error", "message": f"--lines-json is not valid JSON: {exc}"}
                else:
                    result = generate_igce(
                        title=args.title,
                        line_specs=lines,
                        agency=args.agency or "",
                        solicitation=args.solicitation or "",
                        procurement_id=args.procurement_id,
                        opportunity_id=args.opportunity_id,
                        period_of_performance=args.period_of_performance,
                        notes=args.notes,
                    )
        elif args.get:
            if not args.igce_id:
                result = {"status": "error", "message": "Provide --igce-id"}
            else:
                result = get_igce(args.igce_id)
        elif args.list:
            result = list_igces(
                procurement_id=args.procurement_id,
                opportunity_id=args.opportunity_id,
            )
        elif args.calibrate:
            if not args.igce_id:
                result = {"status": "error", "message": "Provide --igce-id"}
            elif not args.actuals_json:
                result = {"status": "error", "message": "Provide --actuals-json"}
            else:
                try:
                    actuals = json.loads(args.actuals_json)
                except json.JSONDecodeError as exc:
                    result = {"status": "error", "message": f"--actuals-json is not valid JSON: {exc}"}
                else:
                    result = calibrate_igce(
                        igce_estimate_id=args.igce_id,
                        actuals=actuals,
                        procurement_id=args.procurement_id,
                    )
        elif args.accuracy:
            result = accuracy_report()
        else:
            parser.print_help()
            return 0
    except Exception as exc:  # pragma: no cover
        result = {"status": "error", "message": f"unexpected: {exc}"}

    if args.human:
        # Simple human rendering
        if "igce" in result and "lines" in result:
            igce = result["igce"]
            print(f"\n  IGCE: {igce.get('title', '')} ({result['igce_id']})")
            print(f"  Agency: {igce.get('agency', '')}  Solicitation: {igce.get('solicitation', '')}")
            print(f"  Total: ${igce.get('total_estimated_cost', 0):,.2f}")
            print(f"  Range: ${igce.get('total_low_estimate', 0):,.2f} - ${igce.get('total_high_estimate', 0):,.2f}")
            print(f"  Within-10% confidence: {igce.get('within_10pct_confidence', 0):.0%}")
            print(f"  Benchmark: {igce.get('benchmark_source', 'n/a')} (n={igce.get('benchmark_sample_size', 0)})")
            print(f"\n  {'CLIN':<8} {'Qty':>6} {'Unit':<8} {'Est Unit':>10} {'Ext':>14} {'Conf':>6} {'Source':<14}")
            print("  " + "-" * 76)
            for ln in result.get("lines", []):
                print(f"  {ln.get('clin', ''):<8} {ln.get('quantity', 0):>6.0f} "
                      f"{ln.get('unit', 'each'):<8} "
                      f"${float(ln.get('unit_cost_estimate', 0)):>9.2f} "
                      f"${float(ln.get('extended_cost', 0)):>13,.2f} "
                      f"{float(ln.get('confidence', 0)):>6.0%} "
                      f"{ln.get('benchmark_source', ''):<14}")
        elif "rows" in result and "calibrated" in result:
            print(f"\n  Calibrated {result['calibrated']} actuals")
            for r in result["rows"]:
                var = r.get("variance_pct")
                if var is None:
                    print(f"  {r.get('clin', '')}: skipped ({r.get('error', 'no variance')})")
                else:
                    flag = "WITHIN" if r.get("within_10pct") else "OUTSIDE"
                    print(f"  {r.get('clin', '')}: ${r.get('actual_unit_cost', 0):.2f} "
                          f"vs ${r.get('estimated_unit_cost', 0):.2f} "
                          f"({var:+.2f}%) [{flag}]")
        elif "within_10pct_rate" in result:
            r = result
            print(f"\n  IGCE Accuracy Report (n={r['sample_size']})")
            print(f"  Within-10% rate: {r['within_10pct_rate']:.1%} "
                  f"({r['within_10pct_count']}/{r['sample_size']})")
            if r.get("mean_abs_variance_pct") is not None:
                print(f"  Mean abs variance: {r['mean_abs_variance_pct']:.2f}%")
            print(f"  Target: ±{r['target_pct']:.0f}%  Meets: {r['meets_target']}")
            for src, b in (r.get("by_source") or {}).items():
                rate = b["within_10pct"] / b["total"] if b["total"] else 0
                print(f"  {src}: {b['within_10pct']}/{b['total']} = {rate:.1%}")
        else:
            print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# CUI // SP-CTI
