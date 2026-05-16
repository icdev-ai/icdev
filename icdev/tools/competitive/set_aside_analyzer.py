#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Small Business Set-Aside Intelligence analyzer.

Analyzes federal small business set-aside patterns, checks SBA eligibility,
forecasts opportunities, maps competitive landscapes, and evaluates agency
goaling performance against statutory small business targets.

Usage:
    python tools/competitive/set_aside_analyzer.py --analyze --naics 541512 [--agency "DoD"] --json
    python tools/competitive/set_aside_analyzer.py --eligibility --naics 541512,541519 [--revenue 15000000] [--employees 100] [--certs "8a,sdvosb"] --json
    python tools/competitive/set_aside_analyzer.py --forecast --naics 541512 --set-aside small_business [--agency "DoD"] --json
    python tools/competitive/set_aside_analyzer.py --landscape --naics 541512 --set-aside sdvosb [--agency "DoD"] --json
    python tools/competitive/set_aside_analyzer.py --size-standard --naics 541512 --json
    python tools/competitive/set_aside_analyzer.py --report --naics 541512 [--agency "DoD"] --json
    python tools/competitive/set_aside_analyzer.py --goaling --agency "DoD" [--fy 2025] --json
    python tools/competitive/set_aside_analyzer.py --dashboard --json
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

# SBA Size Standards — common IT / professional services NAICS codes
# Source: SBA Table of Small Business Size Standards (13 CFR 121.201)
SBA_SIZE_STANDARDS = {
    "518210": {"description": "Computing Infrastructure Providers", "type": "revenue", "threshold": 40_000_000},
    "541330": {"description": "Engineering Services", "type": "employees", "threshold": 500, "revenue_alt": 25_500_000},
    "541380": {"description": "Testing Laboratories and Services", "type": "revenue", "threshold": 19_000_000},
    "541511": {"description": "Custom Computer Programming Services", "type": "revenue", "threshold": 34_000_000},
    "541512": {"description": "Computer Systems Design Services", "type": "revenue", "threshold": 34_000_000},
    "541513": {"description": "Computer Facilities Management Services", "type": "revenue", "threshold": 34_000_000},
    "541519": {"description": "Other Computer Related Services", "type": "revenue", "threshold": 34_000_000},
    "541611": {"description": "Administrative Management Consulting", "type": "revenue", "threshold": 19_000_000},
    "541612": {"description": "Human Resources Consulting Services", "type": "revenue", "threshold": 24_500_000},
    "541613": {"description": "Marketing Consulting Services", "type": "revenue", "threshold": 19_000_000},
    "541614": {"description": "Logistics Consulting Services", "type": "revenue", "threshold": 19_000_000},
    "541618": {"description": "Other Management Consulting Services", "type": "revenue", "threshold": 19_000_000},
    "541690": {"description": "Other Scientific and Technical Consulting", "type": "revenue", "threshold": 19_000_000},
    "541715": {"description": "R&D in Physical/Engineering/Life Sciences", "type": "employees", "threshold": 1000},
    "541990": {"description": "All Other Professional/Scientific/Technical", "type": "revenue", "threshold": 19_000_000},
    "561210": {"description": "Facilities Support Services", "type": "revenue", "threshold": 47_000_000},
    "561320": {"description": "Temporary Help Services", "type": "revenue", "threshold": 34_000_000},
    "611420": {"description": "Computer Training", "type": "revenue", "threshold": 14_000_000},
    "611430": {"description": "Professional Development Training", "type": "revenue", "threshold": 14_000_000},
}

SET_ASIDE_TYPES = {
    "small_business": {"label": "Small Business (SB)", "cert_required": None},
    "8a":             {"label": "8(a) Business Development", "cert_required": "8a"},
    "hubzone":        {"label": "HUBZone", "cert_required": "hubzone"},
    "sdvosb":         {"label": "Service-Disabled Veteran-Owned SB", "cert_required": "sdvosb"},
    "wosb":           {"label": "Women-Owned Small Business", "cert_required": "wosb"},
    "edwosb":         {"label": "Economically Disadvantaged WOSB", "cert_required": "edwosb"},
}

FEDERAL_SB_GOALS = {"small_business": 0.23, "small_disadvantaged": 0.05,
                    "sdvosb": 0.03, "hubzone": 0.03, "wosb": 0.05}

_SA_TO_GOAL = {"small_business": "small_business", "8a": "small_disadvantaged",
               "sdvosb": "sdvosb", "hubzone": "hubzone", "wosb": "wosb", "edwosb": "wosb"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sa_id():
    return "SA-" + secrets.token_hex(6)

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _get_db(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "set_aside_analyzer", action, entity_type, entity_id,
         json.dumps(details) if details else None, _now()))

def _row_to_dict(row):
    return dict(row) if row else None

def _parse_json_field(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value

def _serialize_list(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    return json.dumps(list(value)) if isinstance(value, (list, tuple)) else json.dumps([str(value)])

def _safe_div(n, d, default=0.0):
    return n / d if d else default

def _current_fy():
    now = datetime.now(timezone.utc)
    return now.year + 1 if now.month >= 10 else now.year

def _extract_fy(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    return dt.year + 1 if dt.month >= 10 else dt.year

# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def analyze_set_aside(naics_code, agency=None, db_path=None):
    """Set-aside analysis: awards, value, share, and YoY trends by type."""
    conn = _get_db(db_path)
    try:
        wh = "WHERE naics_code = ?"
        p = [naics_code]
        if agency:
            wh += " AND agency = ?"; p.append(agency)

        rows = conn.execute(
            f"SELECT set_aside_type, COUNT(*) AS awards, "
            f"SUM(award_amount) AS tv, AVG(award_amount) AS av "
            f"FROM competitor_wins {wh} GROUP BY set_aside_type ORDER BY tv DESC", p
        ).fetchall()

        total_v = sum((r["tv"] or 0) for r in rows)
        breakdown = []
        for r in rows:
            tv = r["tv"] or 0
            sa = r["set_aside_type"]
            tp = list(p) + [sa]
            recent = conn.execute(
                f"SELECT COUNT(*) c FROM competitor_wins {wh} AND set_aside_type=? "
                f"AND award_date>=date('now','-365 days')", tp).fetchone()["c"]
            older = conn.execute(
                f"SELECT COUNT(*) c FROM competitor_wins {wh} AND set_aside_type=? "
                f"AND award_date<date('now','-365 days') AND award_date>=date('now','-730 days')", tp
            ).fetchone()["c"]
            trend = ("growing" if recent > older * 1.2 else
                     "declining" if older and recent < older * 0.8 else
                     "new" if not older and recent else "stable")
            breakdown.append({
                "set_aside_type": sa or "full_and_open",
                "label": SET_ASIDE_TYPES.get(sa, {}).get("label", sa or "Full & Open"),
                "awards": r["awards"], "total_value": round(tv, 2),
                "average_award": round(r["av"] or 0, 2),
                "market_share_pct": round(_safe_div(tv, total_v) * 100, 1),
                "trend": trend, "recent_12mo": recent, "prior_12mo": older,
            })

        opp_p = [naics_code] + ([agency] if agency else [])
        opp_cnt = conn.execute(
            "SELECT COUNT(*) c FROM opportunities WHERE naics_code=? "
            "AND status NOT IN ('archived','no_bid','lost')"
            + (" AND agency=?" if agency else ""), opp_p).fetchone()["c"]
        std = SBA_SIZE_STANDARDS.get(naics_code, {})

        _audit(conn, "set_aside.analyze", f"Set-aside analysis NAICS {naics_code}",
               "naics", naics_code, {"agency": agency})
        conn.commit()
        return {"naics_code": naics_code, "naics_description": std.get("description", ""),
                "agency": agency, "total_awards_tracked": sum(r["awards"] for r in rows),
                "total_market_value": round(total_v, 2), "active_opportunities": opp_cnt,
                "size_standard": std.get("threshold"), "size_type": std.get("type"),
                "set_aside_breakdown": breakdown, "analyzed_at": _now()}
    finally:
        conn.close()


def eligibility_check(naics_codes, company_revenue=None, employee_count=None,
                      certifications=None, db_path=None):
    """Check SBA size-standard eligibility and certification status."""
    naics_list = ([n.strip() for n in naics_codes.split(",") if n.strip()]
                  if isinstance(naics_codes, str) else list(naics_codes or []))
    cert_set = ({c.strip().lower() for c in (certifications.split(",") if isinstance(certifications, str) else certifications) if c.strip()}
                if certifications else set())

    eligible, recs = [], []
    for naics in naics_list:
        std = SBA_SIZE_STANDARDS.get(naics)
        if not std:
            eligible.append({"naics_code": naics, "size_eligible": None,
                             "reason": "NAICS not in reference; check SBA.gov", "set_asides": []})
            continue

        size_ok, reason = None, ""
        if std["type"] == "revenue" and company_revenue is not None:
            size_ok = company_revenue <= std["threshold"]
            reason = f"Revenue ${company_revenue:,.0f} {'<=' if size_ok else '>'} ${std['threshold']:,.0f}"
        elif std["type"] == "employees" and employee_count is not None:
            size_ok = employee_count <= std["threshold"]
            reason = f"{employee_count} employees {'<=' if size_ok else '>'} {std['threshold']}"
            if not size_ok and company_revenue is not None and "revenue_alt" in std:
                if company_revenue <= std["revenue_alt"]:
                    size_ok = True
                    reason += f" (revenue ${company_revenue:,.0f} <= ${std['revenue_alt']:,.0f} alt)"
        else:
            reason = f"{'Revenue' if std['type'] == 'revenue' else 'Employee count'} not provided"

        sa_results = []
        for sa_key, sa_info in SET_ASIDE_TYPES.items():
            cr = sa_info["cert_required"]
            has_cert = cr is None or cr in cert_set
            ok = (size_ok is True) and has_cert
            parts = []
            if size_ok is False: parts.append("Exceeds size standard")
            if size_ok is None: parts.append("Size not determinable")
            if cr and cr not in cert_set: parts.append(f"Missing {cr.upper()} cert")
            sa_results.append({"set_aside_type": sa_key, "label": sa_info["label"],
                               "eligible": ok, "size_eligible": size_ok,
                               "cert_required": cr, "cert_held": has_cert,
                               "reason": "; ".join(parts) or "Eligible"})

        eligible.append({"naics_code": naics, "description": std["description"],
                         "size_standard": std["threshold"], "size_type": std["type"],
                         "size_eligible": size_ok, "size_reason": reason, "set_asides": sa_results})
        et = [s for s in sa_results if s["eligible"]]
        mc = [s["cert_required"] for s in sa_results if s["size_eligible"] and not s["cert_held"] and s["cert_required"]]
        if et: recs.append(f"NAICS {naics}: eligible for " + ", ".join(s["label"] for s in et))
        if mc: recs.append(f"NAICS {naics}: consider obtaining " + ", ".join(c.upper() for c in set(mc)))
        if size_ok is False: recs.append(f"NAICS {naics}: exceeds size standard; consider JV/mentor-protege")

    conn = _get_db(db_path)
    try:
        _audit(conn, "set_aside.eligibility", f"Eligibility check NAICS {','.join(naics_list)}",
               details={"naics": naics_list, "revenue": company_revenue, "employees": employee_count})
        conn.commit()
    finally:
        conn.close()
    return {"naics_codes": naics_list, "company_revenue": company_revenue,
            "employee_count": employee_count, "certifications_held": sorted(cert_set),
            "eligible_set_asides": eligible, "recommendations": recs, "checked_at": _now()}


def opportunity_forecast(naics_code, set_aside_type, agency=None, db_path=None):
    """Forecast next 12 months of opportunity volume from historical patterns."""
    conn = _get_db(db_path)
    try:
        wh = "WHERE naics_code=? AND set_aside_type=?"
        p = [naics_code, set_aside_type]
        if agency: wh += " AND agency=?"; p.append(agency)

        rows = conn.execute(
            f"SELECT award_date, award_amount FROM competitor_wins {wh} "
            f"AND award_date IS NOT NULL ORDER BY award_date", p).fetchall()
        if not rows:
            return {"naics_code": naics_code, "set_aside_type": set_aside_type,
                    "agency": agency, "status": "insufficient_data",
                    "forecast_12_months": [], "confidence": "none"}

        mc, mv = defaultdict(int), defaultdict(float)
        for r in rows:
            try: month = int(r["award_date"][5:7])
            except (ValueError, TypeError): continue
            mc[month] += 1; mv[month] += r["award_amount"] or 0

        yr_span = max(1, len({_extract_fy(r["award_date"]) for r in rows if _extract_fy(r["award_date"])}))
        now = datetime.now(timezone.utc)
        forecast, tot_a, tot_v = [], 0.0, 0.0
        for i in range(1, 13):
            m = ((now.month - 1 + i) % 12) + 1
            y = now.year + ((now.month - 1 + i) // 12)
            ea, ev = _safe_div(mc.get(m, 0), yr_span), _safe_div(mv.get(m, 0), yr_span)
            forecast.append({"month": f"{y}-{m:02d}", "expected_awards": round(ea, 1),
                             "expected_value": round(ev, 2), "historical_count": mc.get(m, 0)})
            tot_a += ea; tot_v += ev

        seasonal = []
        if mc:
            pk = max(mc, key=mc.get); lo = min(mc, key=mc.get)
            seasonal.append({"peak_month": pk, "low_month": lo,
                             "peak_note": "End-of-FY surge" if pk in (8, 9) else "Historical peak"})

        dp = len(rows)
        conf = ("high" if dp >= 50 and yr_span >= 3 else
                "medium" if dp >= 15 and yr_span >= 2 else
                "low" if dp >= 3 else "very_low")

        rid = _sa_id()
        conn.execute(
            "INSERT INTO set_aside_intelligence (id, naics_code, agency, set_aside_type, "
            "fiscal_year, total_awards, total_value, average_award, opportunity_forecast, "
            "market_trend, naics_description, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, naics_code, agency, set_aside_type, _current_fy(),
             round(tot_a), round(tot_v, 2), round(_safe_div(tot_v, max(tot_a, 1)), 2),
             json.dumps({"months": forecast, "total_awards": round(tot_a, 1), "total_value": round(tot_v, 2)}),
             "growing" if tot_a > dp / max(yr_span, 1) else "stable",
             SBA_SIZE_STANDARDS.get(naics_code, {}).get("description", ""),
             "CUI // SP-PROPIN", _now(), _now()))
        _audit(conn, "set_aside.forecast", f"Forecast NAICS {naics_code}/{set_aside_type}",
               "set_aside_intelligence", rid, {"confidence": conf, "data_points": dp})
        conn.commit()
        return {"naics_code": naics_code, "set_aside_type": set_aside_type, "agency": agency,
                "record_id": rid, "forecast_12_months": forecast,
                "total_expected_awards": round(tot_a, 1), "total_expected_value": round(tot_v, 2),
                "confidence": conf, "data_points": dp, "years_of_data": yr_span,
                "seasonal_patterns": seasonal, "generated_at": _now()}
    finally:
        conn.close()


def competitive_landscape(naics_code, set_aside_type, agency=None, db_path=None):
    """Top competitors, HHI concentration, new entrants, incumbent tenure."""
    conn = _get_db(db_path)
    try:
        wh = "WHERE naics_code=? AND set_aside_type=?"
        p = [naics_code, set_aside_type]
        if agency: wh += " AND agency=?"; p.append(agency)

        rows = conn.execute(
            f"SELECT competitor_name, COUNT(*) w, SUM(award_amount) tv, "
            f"AVG(award_amount) av, MIN(award_date) fw, MAX(award_date) lw "
            f"FROM competitor_wins {wh} AND competitor_name IS NOT NULL "
            f"GROUP BY competitor_name ORDER BY tv DESC", p).fetchall()

        tot_mkt = sum((r["tv"] or 0) for r in rows)
        comps, shares = [], []
        for r in rows:
            tv = r["tv"] or 0
            s = _safe_div(tv, tot_mkt) * 100 if tot_mkt else 0
            shares.append(s)
            comps.append({"name": r["competitor_name"], "wins": r["w"],
                          "total_value": round(tv, 2), "average_award": round(r["av"] or 0, 2),
                          "market_share_pct": round(s, 1), "first_win": r["fw"], "last_win": r["lw"]})

        hhi = sum(s ** 2 for s in shares)
        conc = "highly_concentrated" if hhi > 2500 else "moderately_concentrated" if hhi > 1500 else "competitive"

        new_ent = conn.execute(
            f"SELECT competitor_name FROM competitor_wins {wh} AND competitor_name IS NOT NULL "
            f"GROUP BY competitor_name HAVING MIN(award_date)>=date('now','-365 days')", p).fetchall()

        tenures = []
        for r in rows:
            if r["fw"] and r["lw"] and r["fw"] != r["lw"]:
                try:
                    d = (datetime.strptime(r["lw"][:10], "%Y-%m-%d") - datetime.strptime(r["fw"][:10], "%Y-%m-%d")).days / 365.25
                    tenures.append(d)
                except (ValueError, TypeError): pass
        avg_ten = _safe_div(sum(tenures), len(tenures)) if tenures else 0

        barriers = []
        if hhi > 2500: barriers.append("High concentration favors incumbents")
        if avg_ten > 3: barriers.append(f"Strong incumbency (avg {avg_ten:.1f}yr)")
        if not new_ent and sum(r["w"] for r in rows) > 10: barriers.append("No new entrants in 12mo")
        cr = SET_ASIDE_TYPES.get(set_aside_type, {}).get("cert_required")
        if cr: barriers.append(f"Requires {cr.upper()} certification")

        _audit(conn, "set_aside.landscape", f"Landscape NAICS {naics_code}/{set_aside_type}",
               "naics", naics_code, {"competitors": len(rows), "hhi": round(hhi, 1)})
        conn.commit()
        return {"naics_code": naics_code, "set_aside_type": set_aside_type, "agency": agency,
                "total_market_value": round(tot_mkt, 2), "total_awards": sum(r["w"] for r in rows),
                "unique_competitors": len(rows), "top_competitors": comps[:20],
                "concentration": {"hhi": round(hhi, 1), "classification": conc},
                "new_entrants": {"count": len(new_ent), "names": [_row_to_dict(n)["competitor_name"] for n in new_ent]},
                "incumbent_tenure": {"average_years": round(avg_ten, 1), "measured": len(tenures)},
                "barriers_to_entry": barriers, "analyzed_at": _now()}
    finally:
        conn.close()


def size_standard_lookup(naics_code, db_path=None):
    """Look up SBA size standard for a NAICS code."""
    std = SBA_SIZE_STANDARDS.get(naics_code)
    if not std:
        return {"naics_code": naics_code, "found": False,
                "message": "Not in reference; check SBA.gov",
                "available_naics": sorted(SBA_SIZE_STANDARDS.keys())}
    r = {"naics_code": naics_code, "found": True, "description": std["description"],
         "size_type": std["type"], "threshold": std["threshold"],
         "threshold_formatted": (f"${std['threshold']:,.0f} avg annual revenue"
                                 if std["type"] == "revenue"
                                 else f"{std['threshold']:,} employees")}
    if "revenue_alt" in std:
        r["revenue_alternative"] = std["revenue_alt"]
    return r


def market_intelligence_report(naics_code, agency=None, db_path=None):
    """Full market intelligence: set-aside + landscape + forecast + win rate."""
    sa = analyze_set_aside(naics_code, agency=agency, db_path=db_path)
    landscapes = {}
    for s in sa.get("set_aside_breakdown", [])[:5]:
        st = s["set_aside_type"]
        if st and s["awards"] >= 2:
            landscapes[st] = competitive_landscape(naics_code, st, agency=agency, db_path=db_path)

    conn = _get_db(db_path)
    try:
        wp = [naics_code] + ([agency] if agency else [])
        aw = " AND o.agency=?" if agency else ""
        wins = conn.execute(f"SELECT COUNT(*) c FROM debriefs d LEFT JOIN opportunities o "
                            f"ON d.opportunity_id=o.id WHERE o.naics_code=?{aw} AND d.result='win'", wp).fetchone()["c"]
        bids = conn.execute(f"SELECT COUNT(*) c FROM debriefs d LEFT JOIN opportunities o "
                            f"ON d.opportunity_id=o.id WHERE o.naics_code=?{aw}", wp).fetchone()["c"]
        pipe = conn.execute("SELECT COUNT(*) c FROM opportunities WHERE naics_code=? "
                            "AND status NOT IN ('archived','no_bid','lost','awarded','submitted')"
                            + (" AND agency=?" if agency else ""),
                            [naics_code] + ([agency] if agency else [])).fetchone()["c"]
    finally:
        conn.close()

    wr = _safe_div(wins, bids) if bids else None
    recs = []
    best = max((s for s in sa.get("set_aside_breakdown", []) if s["trend"] != "declining"),
               key=lambda x: x["total_value"], default=None)
    if best: recs.append(f"Strongest: {best['label']} (${best['total_value']:,.0f}, {best['trend']})")
    grow = [s for s in sa.get("set_aside_breakdown", []) if s["trend"] == "growing"]
    if grow: recs.append("Growing: " + ", ".join(s["label"] for s in grow))
    for st, ls in landscapes.items():
        c = ls.get("concentration", {}).get("classification")
        if c == "competitive": recs.append(f"{st}: competitive — differentiate on past performance")
        elif c == "highly_concentrated": recs.append(f"{st}: concentrated — consider teaming")
    if wr is not None and wr < 0.3 and bids >= 3:
        recs.append(f"Low win rate ({wr:.0%}); review loss debriefs")

    return {"naics_code": naics_code, "agency": agency,
            "naics_description": SBA_SIZE_STANDARDS.get(naics_code, {}).get("description", ""),
            "set_aside_analysis": sa, "competitive_landscapes": landscapes,
            "our_performance": {"bids": bids, "wins": wins,
                                "win_rate": round(wr, 3) if wr is not None else None,
                                "active_pipeline": pipe},
            "recommendations": recs, "generated_at": _now()}


def goaling_analysis(agency, fiscal_year=None, db_path=None):
    """Agency SB goaling: actual vs statutory targets (23% SB, 5% SDB, etc.)."""
    fy = fiscal_year or _current_fy()
    fy_s, fy_e = f"{fy - 1}-10-01", f"{fy}-09-30"
    conn = _get_db(db_path)
    try:
        tr = conn.execute("SELECT COUNT(*) c, SUM(award_amount) tv FROM competitor_wins "
                          "WHERE agency=? AND award_date>=? AND award_date<=?", (agency, fy_s, fy_e)).fetchone()
        tot_v = tr["tv"] or 0

        sa_rows = conn.execute(
            "SELECT set_aside_type, COUNT(*) c, SUM(award_amount) tv FROM competitor_wins "
            "WHERE agency=? AND award_date>=? AND award_date<=? GROUP BY set_aside_type",
            (agency, fy_s, fy_e)).fetchall()

        by_cat = defaultdict(lambda: {"cnt": 0, "value": 0.0})
        for r in sa_rows:
            cat = _SA_TO_GOAL.get(r["set_aside_type"])
            if cat:
                by_cat[cat]["cnt"] += r["c"]; by_cat[cat]["value"] += r["tv"] or 0
        # SB total = sum of all set-aside categories
        sb_v = sum(d["value"] for d in by_cat.values())
        by_cat["small_business"] = {"cnt": sum(d["cnt"] for d in by_cat.values()), "value": sb_v}

        gva, gaps = [], []
        for cat, goal in FEDERAL_SB_GOALS.items():
            d = by_cat.get(cat, {"cnt": 0, "value": 0.0})
            act = _safe_div(d["value"], tot_v) if tot_v else 0
            gap = goal - act
            status = "on_track" if act >= goal else "behind"
            gva.append({"category": cat, "goal_pct": round(goal * 100, 1),
                         "actual_pct": round(act * 100, 1), "gap_pct": round(gap * 100, 1),
                         "actual_value": round(d["value"], 2), "award_count": d["cnt"], "status": status})
            if status == "behind": gaps.append(cat)

        if not gaps:
            assess, lvl = f"{agency} meeting all SB goals FY{fy}.", "normal"
        elif len(gaps) >= 3:
            assess = f"{agency} behind on {len(gaps)} categories ({', '.join(gaps)}). High set-aside likelihood."
            lvl = "high"
        else:
            assess = f"{agency} behind on {', '.join(gaps)}. Moderate targeted set-aside likelihood."
            lvl = "moderate"

        _audit(conn, "set_aside.goaling", f"Goaling {agency} FY{fy}", "agency", agency,
               {"fy": fy, "gaps": gaps, "level": lvl})
        conn.commit()
        return {"agency": agency, "fiscal_year": fy, "total_awards": tr["c"] or 0,
                "total_value": round(tot_v, 2), "goals_vs_actual": gva, "gaps": gaps,
                "assessment": assess, "opportunity_level": lvl, "analyzed_at": _now()}
    finally:
        conn.close()


def dashboard_data(db_path=None):
    """Summary for the set-aside dashboard widget."""
    conn = _get_db(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) c FROM set_aside_intelligence").fetchone()["c"]
        sa_sum = [_row_to_dict(r) for r in conn.execute(
            "SELECT set_aside_type, COUNT(*) awards, SUM(award_amount) total_value "
            "FROM competitor_wins WHERE set_aside_type IS NOT NULL "
            "GROUP BY set_aside_type ORDER BY total_value DESC LIMIT 6").fetchall()]
        naics_sum = conn.execute(
            "SELECT naics_code, COUNT(*) awards, SUM(award_amount) total_value "
            "FROM competitor_wins WHERE set_aside_type IS NOT NULL AND naics_code IS NOT NULL "
            "GROUP BY naics_code ORDER BY total_value DESC LIMIT 10").fetchall()
        active = conn.execute(
            "SELECT COUNT(*) c FROM opportunities WHERE set_aside_type IS NOT NULL "
            "AND status NOT IN ('archived','no_bid','lost')").fetchone()["c"]
        recent = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, naics_code, agency, set_aside_type, total_awards, total_value, "
            "market_trend, created_at FROM set_aside_intelligence ORDER BY created_at DESC LIMIT 5").fetchall()]
        return {"total_intelligence_records": total, "active_set_aside_opportunities": active,
                "set_aside_summary": sa_sum,
                "top_naics": [{**_row_to_dict(r),
                               "description": SBA_SIZE_STANDARDS.get(r["naics_code"], {}).get("description", "")}
                              for r in naics_sum],
                "recent_forecasts": recent,
                "federal_sb_goals": {k: f"{v:.0%}" for k, v in FEDERAL_SB_GOALS.items()},
                "generated_at": _now()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Small Business Set-Aside Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s --analyze --naics 541512 --json\n"
               "  %(prog)s --eligibility --naics 541512,541519 --revenue 15000000 --json\n"
               "  %(prog)s --forecast --naics 541512 --set-aside small_business --json\n"
               "  %(prog)s --landscape --naics 541512 --set-aside sdvosb --json\n"
               "  %(prog)s --size-standard --naics 541512 --json\n"
               "  %(prog)s --report --naics 541512 --agency 'DoD' --json\n"
               "  %(prog)s --goaling --agency 'DoD' --fy 2025 --json\n"
               "  %(prog)s --dashboard --json\n")
    act = parser.add_mutually_exclusive_group(required=True)
    act.add_argument("--analyze", action="store_true", help="Set-aside analysis by NAICS")
    act.add_argument("--eligibility", action="store_true", help="Check eligibility")
    act.add_argument("--forecast", action="store_true", help="Forecast opportunities")
    act.add_argument("--landscape", action="store_true", help="Competitive landscape")
    act.add_argument("--size-standard", action="store_true", help="SBA size standard lookup")
    act.add_argument("--report", action="store_true", help="Full market intelligence report")
    act.add_argument("--goaling", action="store_true", help="Agency SB goaling analysis")
    act.add_argument("--dashboard", action="store_true", help="Dashboard summary")
    parser.add_argument("--naics", help="NAICS code(s), comma-separated for eligibility")
    parser.add_argument("--agency", help="Agency name filter")
    parser.add_argument("--set-aside", dest="set_aside", help="Set-aside type")
    parser.add_argument("--revenue", type=float, help="Company annual revenue")
    parser.add_argument("--employees", type=int, help="Company employee count")
    parser.add_argument("--certs", help="Certifications held (e.g., '8a,sdvosb')")
    parser.add_argument("--fy", type=int, help="Fiscal year for goaling")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")
    return parser


def main():
    import argparse  # noqa: F811
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path
    try:
        if args.analyze:
            if not args.naics: parser.error("--analyze requires --naics")
            result = analyze_set_aside(args.naics, agency=args.agency, db_path=db)
        elif args.eligibility:
            if not args.naics: parser.error("--eligibility requires --naics")
            result = eligibility_check(args.naics, company_revenue=args.revenue,
                                       employee_count=args.employees, certifications=args.certs, db_path=db)
        elif args.forecast:
            if not args.naics: parser.error("--forecast requires --naics")
            if not args.set_aside: parser.error("--forecast requires --set-aside")
            result = opportunity_forecast(args.naics, args.set_aside, agency=args.agency, db_path=db)
        elif args.landscape:
            if not args.naics: parser.error("--landscape requires --naics")
            if not args.set_aside: parser.error("--landscape requires --set-aside")
            result = competitive_landscape(args.naics, args.set_aside, agency=args.agency, db_path=db)
        elif args.size_standard:
            if not args.naics: parser.error("--size-standard requires --naics")
            result = size_standard_lookup(args.naics, db_path=db)
        elif args.report:
            if not args.naics: parser.error("--report requires --naics")
            result = market_intelligence_report(args.naics, agency=args.agency, db_path=db)
        elif args.goaling:
            if not args.agency: parser.error("--goaling requires --agency")
            result = goaling_analysis(args.agency, fiscal_year=args.fy, db_path=db)
        elif args.dashboard:
            result = dashboard_data(db_path=db)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for key, value in (result if isinstance(result, dict) else {}).items():
                print(f"  {key}: {json.dumps(value, default=str) if isinstance(value, (dict, list)) else value}")
    except ValueError as exc:
        if args.json: print(json.dumps({"error": str(exc)}, indent=2))
        else: print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        if args.json: print(json.dumps({"error": f"Database error: {exc}"}, indent=2))
        else: print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
