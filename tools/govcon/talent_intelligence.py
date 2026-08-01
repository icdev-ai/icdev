#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Talent Intelligence Engine — competitive hiring signal analysis.

Analyzes job posting data for competitive signals.  Data is entered
manually or imported — NO web scraping.  Air-gap safe.

DB tables (read/write): pg_talent_signals, pg_talent_velocity
DB tables (read):       proposal_opportunities, sam_gov_opportunities
DB tables (write):      audit_trail

Usage:
    python tools/govcon/talent_intelligence.py \\
        --add-posting --competitor "Booz Allen" \\
        --role-title "Cloud Engineer" --json
    python tools/govcon/talent_intelligence.py \\
        --velocity --competitor "Booz Allen" --json
    python tools/govcon/talent_intelligence.py --clusters --json
    python tools/govcon/talent_intelligence.py --correlate --json
    python tools/govcon/talent_intelligence.py --salary-intel --json
    python tools/govcon/talent_intelligence.py \\
        --competitor-profile --competitor "Booz Allen" --json
"""

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.common.helpers import row_to_dict  # noqa: E402

# ── Signal type classification rules ──────────────────────────────────
# Must match CHECK constraint on pg_talent_signals.signal_type:
#   'velocity_spike', 'role_cluster', 'new_capability', 'pricing_intel', 'general'
_ROLE_KEYWORDS_PROPOSAL = [
    "proposal",
    "capture",
    "bid",
    "orals",
    "solutioning",
    "business development",
    "bd manager",
    "capture manager",
]
_CLEARANCE_CLASSIFIED = ["ts/sci", "ts", "sci", "top secret"]


def _classify_signal(role_title, clearance, salary_low, salary_high):
    """Auto-classify signal_type from posting fields."""
    role_lower = (role_title or "").lower()
    clr_lower = (clearance or "").lower()

    # Proposal-related role → new_capability (closest match for bid activity)
    if any(kw in role_lower for kw in _ROLE_KEYWORDS_PROPOSAL):
        return "new_capability"

    # Classified clearance → role_cluster (indicates classified contract surge)
    if any(kw in clr_lower for kw in _CLEARANCE_CLASSIFIED):
        return "role_cluster"

    # Salary data present → pricing_intel
    if salary_low is not None or salary_high is not None:
        return "pricing_intel"

    return "general"


# ── Helpers ───────────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="ts"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, project_id=None):
    det = json.dumps(details) if isinstance(details, dict) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, "
            "details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                event_type,
                "talent_intelligence",
                action,
                det,
                project_id,
                None,
            ),
        )
    except Exception:
        # Fallback: schema may use created_at instead
        try:
            conn.execute(
                "INSERT INTO audit_trail "
                "(project_id, event_type, actor, "
                "action, details, session_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    project_id,
                    event_type,
                    "talent_intelligence",
                    action,
                    det,
                    None,
                ),
            )
        except Exception:
            pass  # audit is best-effort


def _categorize_role(role_title):
    """Extract a broad role category from a title for aggregation."""
    lower = (role_title or "").lower()
    categories = [
        ("engineer", ["engineer", "developer", "swe", "sde", "programmer"]),
        ("analyst", ["analyst", "intelligence", "research"]),
        ("manager", ["manager", "director", "lead", "supervisor", "vp"]),
        ("architect", ["architect", "principal"]),
        ("admin", ["administrator", "sysadmin", "devops", "sre"]),
        ("security", ["security", "cyber", "infosec", "isso", "issm", "ciso"]),
        (
            "data",
            [
                "data scientist",
                "data engineer",
                "ml engineer",
                "ai ",
                "machine learning",
            ],
        ),
        ("consultant", ["consultant", "advisor", "sme"]),
        ("proposal", ["proposal", "capture", "bd ", "business development"]),
    ]
    for cat, keywords in categories:
        if any(kw in lower for kw in keywords):
            return cat
    return "other"


# ── Core Functions ────────────────────────────────────────────────────


def add_posting(
    competitor_name,
    role_title,
    location=None,
    clearance=None,
    salary_low=None,
    salary_high=None,
    tools_mentioned=None,
    certs_mentioned=None,
    source_url=None,
    posting_date=None,
):
    """Store a talent signal in pg_talent_signals.

    Auto-classifies signal_type from keywords in role_title, clearance,
    and salary fields.
    """
    conn = _get_db()
    now = _now()
    signal_id = _gen_id("ts")
    signal_type = _classify_signal(role_title, clearance, salary_low, salary_high)

    # Normalise list fields to comma-separated strings
    if isinstance(tools_mentioned, list):
        tools_mentioned = ",".join(tools_mentioned)
    if isinstance(certs_mentioned, list):
        certs_mentioned = ",".join(certs_mentioned)

    conn.execute(
        "INSERT INTO pg_talent_signals "
        "(id, competitor_name, role_title, location, clearance_required, "
        "salary_low, salary_high, tools_mentioned, certifications_mentioned, "
        "source_url, posting_date, scan_date, correlated_opportunity_id, "
        "signal_type, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            signal_id,
            competitor_name,
            role_title,
            location,
            clearance,
            salary_low,
            salary_high,
            tools_mentioned,
            certs_mentioned,
            source_url,
            posting_date or now[:10],
            now[:10],
            None,
            signal_type,
            now,
        ),
    )

    _audit(
        conn,
        "talent.add_posting",
        f"Added talent signal for {competitor_name}",
        {"signal_id": signal_id, "competitor": competitor_name, "role": role_title, "signal_type": signal_type},
    )

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "signal_id": signal_id,
        "competitor": competitor_name,
        "role_title": role_title,
        "signal_type": signal_type,
    }


def compute_velocity(competitor_name, weeks_back=12):
    """Compute hiring velocity per ISO week for a competitor.

    Groups pg_talent_signals by ISO week, calculates mean / stddev,
    z-scores each week, and detects spikes (z > 2.0).
    Stores results in pg_talent_velocity.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT id, role_title, location, clearance_required, scan_date "
        "FROM pg_talent_signals "
        "WHERE competitor_name = %s "
        "AND scan_date >= date('now', %s) "
        "ORDER BY scan_date",
        (competitor_name, f"-{weeks_back * 7} days"),
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "status": "ok",
            "competitor": competitor_name,
            "weeks": [],
            "mean": 0.0,
            "stddev": 0.0,
            "current_velocity": 0,
            "message": "No signals found in the given period",
        }

    # Group by ISO week (YYYY-WNN)
    week_buckets = {}
    for r in rows:
        rd = row_to_dict(r)
        scan = rd.get("scan_date", "")
        try:
            if "T" in scan:
                dt = datetime.fromisoformat(scan)
            else:
                dt = datetime.strptime(scan, "%Y-%m-%d")
            iso = dt.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
        except (ValueError, TypeError):
            continue
        week_buckets.setdefault(wk, []).append(rd)

    # Build ordered week list
    sorted_weeks = sorted(week_buckets.keys())
    counts = [len(week_buckets[w]) for w in sorted_weeks]

    n = len(counts)
    mean = sum(counts) / n if n else 0.0
    variance = sum((c - mean) ** 2 for c in counts) / n if n else 0.0
    stddev = math.sqrt(variance) if variance > 0 else 0.0

    now = _now()
    result_weeks = []
    for i, wk in enumerate(sorted_weeks):
        c = counts[i]
        zscore = (c - mean) / stddev if stddev > 0 else 0.0
        spike = zscore > 2.0

        # Dominant attributes for this week
        postings = week_buckets[wk]
        roles = [_categorize_role(p.get("role_title")) for p in postings]
        locs = [p.get("location") or "unknown" for p in postings]
        clrs = [p.get("clearance_required") or "none" for p in postings]

        dom_role = max(set(roles), key=roles.count) if roles else None
        dom_loc = max(set(locs), key=locs.count) if locs else None
        dom_clr = max(set(clrs), key=clrs.count) if clrs else None

        vel_id = _gen_id("tv")
        # Monday of the ISO week
        try:
            yr, wn = wk.split("-W")
            fmt_in = f"{yr}-W{wn}-1"
            week_start = datetime.strptime(
                fmt_in,
                "%G-W%V-%u",
            ).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            week_start = wk

        conn.execute(
            "INSERT OR REPLACE INTO pg_talent_velocity "
            "(id, competitor_name, week_start, posting_count, velocity_zscore, "
            "dominant_role_category, dominant_location, "
            "dominant_clearance, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (vel_id, competitor_name, week_start, c, round(zscore, 4), dom_role, dom_loc, dom_clr, now),
        )

        result_weeks.append(
            {
                "week": wk,
                "week_start": week_start,
                "count": c,
                "zscore": round(zscore, 4),
                "spike": spike,
                "dominant_role": dom_role,
                "dominant_location": dom_loc,
            }
        )

    _audit(
        conn,
        "talent.velocity",
        f"Computed velocity for {competitor_name}",
        {
            "competitor": competitor_name,
            "weeks_back": weeks_back,
            "total_signals": sum(counts),
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
        },
    )

    conn.commit()
    conn.close()

    current_velocity = counts[-1] if counts else 0

    return {
        "status": "ok",
        "competitor": competitor_name,
        "weeks": result_weeks,
        "mean": round(mean, 2),
        "stddev": round(stddev, 2),
        "current_velocity": current_velocity,
        "spike_weeks": [w for w in result_weeks if w["spike"]],
    }


def detect_clusters(competitor_name=None, min_cluster_size=3):
    """Find role/location clusters in recent postings (last 30 days).

    Groups by (competitor, location, clearance) and flags groups
    with count >= min_cluster_size.
    """
    conn = _get_db()

    query = (
        "SELECT competitor_name, location, clearance_required, role_title "
        "FROM pg_talent_signals "
        "WHERE scan_date >= date('now', '-30 days')"
    )
    params = []
    if competitor_name:
        query += " AND competitor_name = ?"
        params.append(competitor_name)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return {"status": "ok", "clusters": [], "message": "No recent signals found"}

    # Group by (competitor, location, clearance)
    groups = {}
    for r in rows:
        rd = row_to_dict(r)
        key = (
            rd.get("competitor_name", "unknown"),
            rd.get("location") or "unspecified",
            rd.get("clearance_required") or "none",
        )
        groups.setdefault(key, []).append(rd.get("role_title", ""))

    clusters = []
    for (comp, loc, clr), roles in groups.items():
        if len(roles) >= min_cluster_size:
            # Assessment heuristic
            if clr.lower() in ("ts/sci", "ts", "sci", "top secret"):
                assessment = "Classified program staffing surge"
            elif len(set(roles)) <= 2:
                assessment = "Concentrated team build-up for specific role"
            else:
                assessment = "Broad hiring campaign in region"

            clusters.append(
                {
                    "competitor": comp,
                    "location": loc,
                    "clearance": clr,
                    "count": len(roles),
                    "roles": list(set(roles)),
                    "assessment": assessment,
                }
            )

    clusters.sort(key=lambda c: c["count"], reverse=True)

    return {
        "status": "ok",
        "clusters": clusters,
        "total_clusters": len(clusters),
    }


def correlate_with_opportunities(competitor_name=None):
    """Cross-reference talent signals with active SAM.gov / proposal opportunities.

    Scoring: location_match(0.4) + clearance_match(0.3) + keyword_match(0.3)
    """
    conn = _get_db()

    # Get recent talent signals (last 60 days)
    sig_q = (
        "SELECT DISTINCT competitor_name, location, clearance_required, "
        "tools_mentioned, certifications_mentioned, role_title "
        "FROM pg_talent_signals "
        "WHERE scan_date >= date('now', '-60 days')"
    )
    sig_params = []
    if competitor_name:
        sig_q += " AND competitor_name = ?"
        sig_params.append(competitor_name)

    signals = conn.execute(sig_q, sig_params).fetchall()

    # Get active opportunities
    try:
        opps = conn.execute(
            "SELECT id, title, agency, naics_code, set_aside_type "
            "FROM proposal_opportunities "
            "WHERE status IN ('intake', 'bid_no_bid', 'go', 'writing')",
        ).fetchall()
    except Exception:
        opps = []

    # Also check SAM.gov cache
    try:
        sam_opps = conn.execute(
            "SELECT id, title, agency, naics_code, "
            "set_aside_type, place_of_performance "
            "FROM sam_gov_opportunities "
            "WHERE active = 'true'",
        ).fetchall()
    except Exception:
        sam_opps = []

    conn.close()

    if not signals:
        return {
            "status": "ok",
            "correlations": [],
            "message": "No recent talent signals",
        }

    # Merge opportunity sources
    all_opps = []
    for o in opps:
        od = row_to_dict(o)
        od["source"] = "proposal_opportunities"
        all_opps.append(od)
    for o in sam_opps:
        od = row_to_dict(o)
        od["source"] = "sam_gov_opportunities"
        all_opps.append(od)

    if not all_opps:
        return {
            "status": "ok",
            "correlations": [],
            "message": "No active opportunities to correlate",
        }

    correlations = []
    for sig in signals:
        sd = row_to_dict(sig)
        sig_loc = (sd.get("location") or "").lower()
        sig_clr = (sd.get("clearance_required") or "").lower()
        sig_tools = (sd.get("tools_mentioned") or "").lower()
        sig_certs = (sd.get("certifications_mentioned") or "").lower()
        sig_role = (sd.get("role_title") or "").lower()
        sig_keywords = set((sig_tools + " " + sig_certs + " " + sig_role).split())

        for opp in all_opps:
            opp_title = (opp.get("title") or "").lower()
            opp_loc = (opp.get("place_of_performance") or "").lower()
            opp_naics = (opp.get("naics_code") or "").lower()
            opp_keywords = set(opp_title.split())
            if opp_naics:
                opp_keywords.add(opp_naics)

            # Scoring
            loc_score = 0.0
            if sig_loc and opp_loc and sig_loc in opp_loc:
                loc_score = 1.0
            elif sig_loc and opp_loc:
                # Partial match (state/city overlap)
                sig_parts = set(sig_loc.replace(",", " ").split())
                opp_parts = set(opp_loc.replace(",", " ").split())
                overlap = sig_parts & opp_parts
                if overlap:
                    loc_score = 0.5

            clr_score = 0.0
            if sig_clr and sig_clr != "none":
                # Any clearance requirement on the signal is a match indicator
                clr_score = 0.6
                if any(kw in opp_title for kw in ["classified", "secret", "cleared"]):
                    clr_score = 1.0

            kw_score = 0.0
            if sig_keywords and opp_keywords:
                stop = {
                    "",
                    "the",
                    "and",
                    "for",
                    "of",
                    "a",
                    "to",
                    "in",
                }
                common = sig_keywords & opp_keywords - stop
                if common:
                    kw_score = min(1.0, len(common) / 3.0)

            composite = loc_score * 0.4 + clr_score * 0.3 + kw_score * 0.3

            if composite >= 0.2:
                evidence = []
                if loc_score > 0:
                    evidence.append(f"location_match({loc_score:.1f})")
                if clr_score > 0:
                    evidence.append(f"clearance_match({clr_score:.1f})")
                if kw_score > 0:
                    evidence.append(f"keyword_match({kw_score:.1f})")

                correlations.append(
                    {
                        "competitor": sd.get("competitor_name"),
                        "opportunity_id": opp.get("id"),
                        "opportunity_title": opp.get("title"),
                        "opportunity_source": opp.get("source"),
                        "correlation_score": round(composite, 3),
                        "evidence": evidence,
                    }
                )

    # Deduplicate: keep highest score per (competitor, opportunity_id)
    best = {}
    for c in correlations:
        key = (c["competitor"], c["opportunity_id"])
        if key not in best or c["correlation_score"] > best[key]["correlation_score"]:
            best[key] = c

    result = sorted(best.values(), key=lambda x: x["correlation_score"], reverse=True)

    return {
        "status": "ok",
        "correlations": result[:50],
        "total_correlations": len(result),
    }


def get_salary_intelligence(role_category=None):
    """Aggregate salary data for Price-to-Win feeding.

    Groups by role category and computes median, p25, p75.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT role_title, salary_low, salary_high "
        "FROM pg_talent_signals "
        "WHERE salary_low IS NOT NULL OR salary_high IS NOT NULL",
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "salary_intel": [],
            "message": "No salary data available",
        }

    # Group by category
    cat_salaries = {}
    for r in rows:
        rd = row_to_dict(r)
        cat = _categorize_role(rd.get("role_title"))
        if role_category and cat != role_category:
            continue

        low = rd.get("salary_low")
        high = rd.get("salary_high")
        # Use midpoint if both present, otherwise the one available
        if low is not None and high is not None:
            mid = (float(low) + float(high)) / 2.0
        elif low is not None:
            mid = float(low)
        elif high is not None:
            mid = float(high)
        else:
            continue

        cat_salaries.setdefault(cat, []).append(mid)

    results = []
    for cat, salaries in sorted(cat_salaries.items()):
        salaries.sort()
        n = len(salaries)
        if n % 2:
            median = salaries[n // 2]
        else:
            median = (salaries[n // 2 - 1] + salaries[n // 2]) / 2.0
        p25_idx = max(0, n // 4)
        p75_idx = min(n - 1, (3 * n) // 4)
        p25 = salaries[p25_idx]
        p75 = salaries[p75_idx]

        results.append(
            {
                "role_category": cat,
                "median": round(median, 2),
                "p25": round(p25, 2),
                "p75": round(p75, 2),
                "sample_size": n,
            }
        )

    return {
        "status": "ok",
        "salary_intel": results,
    }


def generate_competitor_profile(competitor_name):
    """Generate comprehensive competitor profile from talent data.

    Includes hiring velocity, top roles, tech stack, clearance profile,
    geographic footprint, and active bid signals.
    """
    conn = _get_db()

    signals = conn.execute(
        "SELECT * FROM pg_talent_signals WHERE competitor_name = %s ORDER BY scan_date DESC",
        (competitor_name,),
    ).fetchall()
    conn.close()

    if not signals:
        return {
            "status": "ok",
            "competitor": competitor_name,
            "message": "No talent data available for this competitor",
            "profile_markdown": (f"# Talent Profile: {competitor_name}\n\nNo data available."),
        }

    all_signals = [row_to_dict(s) for s in signals]
    total = len(all_signals)

    # ── Top roles ──
    role_counts = {}
    for s in all_signals:
        cat = _categorize_role(s.get("role_title"))
        role_counts[cat] = role_counts.get(cat, 0) + 1
    top_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # ── Tech stack ──
    tech_counts = {}
    for s in all_signals:
        tools = s.get("tools_mentioned") or ""
        for t in tools.split(","):
            t = t.strip()
            if t:
                tech_counts[t.lower()] = tech_counts.get(t.lower(), 0) + 1
    top_tech = sorted(tech_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # ── Clearance profile ──
    clr_counts = {}
    for s in all_signals:
        clr = s.get("clearance_required") or "none"
        clr_counts[clr] = clr_counts.get(clr, 0) + 1

    # ── Geographic footprint ──
    loc_counts = {}
    for s in all_signals:
        loc = s.get("location") or "unspecified"
        loc_counts[loc] = loc_counts.get(loc, 0) + 1
    top_locations = sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # ── Bid signals ──
    bid_signals = [s for s in all_signals if s.get("signal_type") == "new_capability"]

    # ── Salary range ──
    salaries = []
    for s in all_signals:
        low = s.get("salary_low")
        high = s.get("salary_high")
        if low is not None:
            salaries.append(float(low))
        if high is not None:
            salaries.append(float(high))
    salary_range = None
    if salaries:
        salary_range = {"min": min(salaries), "max": max(salaries)}

    # ── Velocity (quick summary) ──
    velocity = compute_velocity(competitor_name, weeks_back=12)

    # ── Build markdown ──
    md_lines = [
        f"# Talent Intelligence Profile: {competitor_name}",
        "",
        f"**Total Signals:** {total}",
        f"**Avg Weekly Postings:** {velocity.get('mean', 0)}",
        f"**Current Velocity:** {velocity.get('current_velocity', 0)} postings/week",
        "",
        "## Top Roles",
    ]
    for role, cnt in top_roles:
        md_lines.append(f"- {role}: {cnt} postings ({cnt * 100 // total}%)")

    md_lines.append("")
    md_lines.append("## Tech Stack")
    for tech, cnt in top_tech:
        md_lines.append(f"- {tech}: {cnt} mentions")

    md_lines.append("")
    md_lines.append("## Clearance Profile")
    for clr, cnt in sorted(clr_counts.items(), key=lambda x: x[1], reverse=True):
        md_lines.append(f"- {clr}: {cnt} ({cnt * 100 // total}%)")

    md_lines.append("")
    md_lines.append("## Geographic Footprint")
    for loc, cnt in top_locations:
        md_lines.append(f"- {loc}: {cnt} postings")

    if bid_signals:
        md_lines.append("")
        md_lines.append("## Active Bid Signals")
        n_bid = len(bid_signals)
        md_lines.append(f"**{n_bid} potential bid-related postings detected**")
        for bs in bid_signals[:5]:
            md_lines.append(f"- {bs.get('role_title')} ({bs.get('location', 'N/A')})")

    if salary_range:
        md_lines.append("")
        md_lines.append("## Salary Range")
        md_lines.append(f"- Min: ${salary_range['min']:,.0f}")
        md_lines.append(f"- Max: ${salary_range['max']:,.0f}")

    spike_weeks = velocity.get("spike_weeks", [])
    if spike_weeks:
        md_lines.append("")
        md_lines.append("## Velocity Spikes")
        for sw in spike_weeks:
            zs = sw["zscore"]
            md_lines.append(f"- {sw['week']}: {sw['count']} postings (z={zs:.2f})")

    profile_md = "\n".join(md_lines)

    return {
        "status": "ok",
        "competitor": competitor_name,
        "total_signals": total,
        "top_roles": [{"role": r, "count": c} for r, c in top_roles],
        "top_tech": [{"tool": t, "count": c} for t, c in top_tech],
        "clearance_profile": clr_counts,
        "geographic_footprint": [{"location": loc, "count": c} for loc, c in top_locations],
        "bid_signal_count": len(bid_signals),
        "salary_range": salary_range,
        "velocity_summary": {
            "mean": velocity.get("mean", 0),
            "stddev": velocity.get("stddev", 0),
            "current": velocity.get("current_velocity", 0),
            "spike_weeks": len(spike_weeks),
        },
        "profile_markdown": profile_md,
    }


# ── CLI ───────────────────────────────────────────────────────────────


def _build_parser():
    p = argparse.ArgumentParser(
        prog="talent_intelligence",
        description="Talent Intelligence Engine — competitive hiring signal analysis",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--add-posting",
        action="store_true",
        help="Add a talent signal",
    )
    g.add_argument(
        "--velocity",
        action="store_true",
        help="Compute hiring velocity",
    )
    g.add_argument(
        "--clusters",
        action="store_true",
        help="Detect role/location clusters",
    )
    g.add_argument(
        "--correlate",
        action="store_true",
        help="Correlate with opportunities",
    )
    g.add_argument(
        "--salary-intel",
        action="store_true",
        help="Aggregate salary intelligence",
    )
    g.add_argument(
        "--competitor-profile",
        action="store_true",
        help="Generate competitor profile",
    )

    p.add_argument("--competitor", help="Competitor name")
    p.add_argument("--role-title", help="Job role title")
    p.add_argument("--location", help="Job location")
    p.add_argument("--clearance", help="Clearance requirement")
    p.add_argument("--salary-low", type=float, help="Salary range low")
    p.add_argument("--salary-high", type=float, help="Salary range high")
    p.add_argument("--tools-mentioned", help="Comma-separated tools mentioned")
    p.add_argument("--certs-mentioned", help="Comma-separated certifications")
    p.add_argument("--source-url", help="Source URL")
    p.add_argument("--posting-date", help="Posting date (YYYY-MM-DD)")
    p.add_argument("--weeks-back", type=int, default=12, help="Weeks back for velocity")
    p.add_argument("--min-cluster-size", type=int, default=3, help="Min cluster size")
    p.add_argument("--role-category", help="Filter salary intel by role category")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--human", action="store_true", help="Human-readable output")
    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    result = {}

    if args.add_posting:
        if not args.competitor or not args.role_title:
            parser.error("--add-posting requires --competitor and --role-title")
        result = add_posting(
            competitor_name=args.competitor,
            role_title=args.role_title,
            location=args.location,
            clearance=args.clearance,
            salary_low=args.salary_low,
            salary_high=args.salary_high,
            tools_mentioned=args.tools_mentioned,
            certs_mentioned=args.certs_mentioned,
            source_url=args.source_url,
            posting_date=args.posting_date,
        )

    elif args.velocity:
        if not args.competitor:
            parser.error("--velocity requires --competitor")
        result = compute_velocity(args.competitor, weeks_back=args.weeks_back)

    elif args.clusters:
        result = detect_clusters(
            competitor_name=args.competitor,
            min_cluster_size=args.min_cluster_size,
        )

    elif args.correlate:
        result = correlate_with_opportunities(competitor_name=args.competitor)

    elif args.salary_intel:
        result = get_salary_intelligence(role_category=args.role_category)

    elif args.competitor_profile:
        if not args.competitor:
            parser.error("--competitor-profile requires --competitor")
        result = generate_competitor_profile(args.competitor)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        if "profile_markdown" in result:
            print(result["profile_markdown"])
        else:
            for k, v in result.items():
                if isinstance(v, (list, dict)):
                    print(f"{k}: {json.dumps(v, indent=2, default=str)}")
                else:
                    print(f"{k}: {v}")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
