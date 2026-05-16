#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
"""LCAT (Labor Category) management and cost structure.

Manages LCAT definitions, employee assignments, and DCAA-compliant rate cards.
Auto-suggests LCAT assignments from existing FPDS pricing_benchmarks data.

Usage:
    python tools/erp/lcat_manager.py --list-lcats [--json]
    python tools/erp/lcat_manager.py --add-lcat --code "PM-III" --name "Program Manager III" [--json]
    python tools/erp/lcat_manager.py --set-rate --lcat <id> --dlr 95.0 --fringe 0.28 [--json]
    python tools/erp/lcat_manager.py --assign --employee <id> --lcat <id> [--json]
    python tools/erp/lcat_manager.py --cost-sheet --lcat <id> [--json]
    python tools/erp/lcat_manager.py --suggest --employee <id> [--json]
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id():
    return str(uuid.uuid4())


def _compute_wrap_rate(dlr, fringe, overhead, ga, fee):
    """Compute fully-burdened wrap rate multiplier.

    Wrap Rate = DLR × (1 + fringe + overhead + G&A + fee)
    Fully-burdened = DLR × wrap_rate
    """
    return round(1.0 + fringe + overhead + ga + fee, 4)


# ---------------------------------------------------------------------------
# LCAT CRUD
# ---------------------------------------------------------------------------

def add_lcat(code: str, name: str, description: str = None,
             naics_code: str = None, min_education: str = None,
             min_experience_years: int = None, typical_skills: list = None) -> dict:
    conn = _db()
    try:
        lid = _new_id()
        conn.execute("""
            INSERT INTO lcats (id, lcat_code, lcat_name, description,
                naics_code, min_education, min_experience_years, typical_skills)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (lid, code, name, description, naics_code, min_education,
              min_experience_years,
              json.dumps(typical_skills) if typical_skills else None))
        conn.commit()
        return {"success": True, "id": lid, "lcat_code": code}
    except sqlite3.IntegrityError:
        return {"success": False, "error": f"LCAT code '{code}' already exists"}
    finally:
        conn.close()


def list_lcats() -> dict:
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT l.id, l.lcat_code, l.lcat_name, l.naics_code,
                   l.min_experience_years,
                   COUNT(DISTINCT el.employee_id) AS employee_count,
                   lr.direct_labor_rate, lr.wrap_rate
            FROM lcats l
            LEFT JOIN employee_lcats el ON el.lcat_id = l.id AND el.end_date IS NULL
            LEFT JOIN lcat_rates lr ON lr.lcat_id = l.id
                AND lr.effective_date = (
                    SELECT MAX(effective_date) FROM lcat_rates
                    WHERE lcat_id = l.id AND effective_date <= date('now')
                )
            GROUP BY l.id
            ORDER BY l.lcat_code
        """).fetchall()
        return {"success": True, "lcats": [dict(r) for r in rows]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# RATE CARD
# ---------------------------------------------------------------------------

def set_rate(lcat_id: str, direct_labor_rate: float,
             fringe_rate: float = 0.0, overhead_rate: float = 0.0,
             ga_rate: float = 0.0, fee_rate: float = 0.0,
             effective_date: str = None, cost_type: str = "cost_reimbursable",
             contract_vehicle: str = None, agency: str = None,
             notes: str = None) -> dict:
    wrap = _compute_wrap_rate(direct_labor_rate, fringe_rate,
                              overhead_rate, ga_rate, fee_rate)
    fully_burdened = round(direct_labor_rate * wrap, 2)
    eff = effective_date or _now()[:10]
    rid = _new_id()

    conn = _db()
    try:
        conn.execute("""
            INSERT INTO lcat_rates (
                id, lcat_id, effective_date, direct_labor_rate,
                fringe_rate, overhead_rate, ga_rate, fee_rate,
                wrap_rate, cost_type, contract_vehicle, agency, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (rid, lcat_id, eff, direct_labor_rate, fringe_rate,
              overhead_rate, ga_rate, fee_rate, wrap,
              cost_type, contract_vehicle, agency, notes))
        conn.commit()
        return {
            "success": True, "id": rid, "lcat_id": lcat_id,
            "direct_labor_rate": direct_labor_rate,
            "wrap_rate": wrap,
            "fully_burdened_rate": fully_burdened,
            "effective_date": eff,
        }
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def cost_sheet(lcat_id: str, hours: float = 2080.0) -> dict:
    """Compute full annual cost breakdown for a LCAT."""
    conn = _db()
    try:
        lcat = conn.execute(
            "SELECT * FROM lcats WHERE id = ?", (lcat_id,)
        ).fetchone()
        if not lcat:
            return {"success": False, "error": "LCAT not found"}

        rate = conn.execute("""
            SELECT * FROM lcat_rates
            WHERE lcat_id = ? AND effective_date <= date('now')
            ORDER BY effective_date DESC LIMIT 1
        """, (lcat_id,)).fetchone()

        if not rate:
            return {"success": False, "error": "No rate card found for LCAT",
                    "lcat": dict(lcat)}

        dlr = rate["direct_labor_rate"]
        fringe = rate["fringe_rate"] or 0.0
        oh = rate["overhead_rate"] or 0.0
        ga = rate["ga_rate"] or 0.0
        fee = rate["fee_rate"] or 0.0
        wrap = _compute_wrap_rate(dlr, fringe, oh, ga, fee)
        fbr = round(dlr * wrap, 2)

        # FPDS benchmark comparison
        fpds = conn.execute("""
            SELECT labor_category, average_rate, median_rate,
                   percentile_25, percentile_75
            FROM pricing_benchmarks
            WHERE labor_category LIKE ?
            ORDER BY data_period DESC LIMIT 3
        """, (f"%{lcat['lcat_name'].split()[0]}%",)).fetchall()

        return {
            "success": True,
            "lcat": dict(lcat),
            "rate_card": {
                "direct_labor_rate": dlr,
                "fringe_rate": f"{fringe:.1%}",
                "overhead_rate": f"{oh:.1%}",
                "ga_rate": f"{ga:.1%}",
                "fee_rate": f"{fee:.1%}",
                "wrap_rate": wrap,
                "fully_burdened_rate": fbr,
                "effective_date": rate["effective_date"],
            },
            "annual_cost": {
                "hours": hours,
                "direct_labor": round(dlr * hours, 2),
                "fringe": round(dlr * fringe * hours, 2),
                "overhead": round(dlr * oh * hours, 2),
                "ga": round(dlr * ga * hours, 2),
                "fee": round(dlr * fee * hours, 2),
                "fully_burdened_total": round(fbr * hours, 2),
            },
            "fpds_benchmarks": [dict(r) for r in fpds],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# EMPLOYEE ASSIGNMENT
# ---------------------------------------------------------------------------

def assign_lcat(employee_id: str, lcat_id: str,
                effective_date: str = None, is_primary: bool = True) -> dict:
    eff = effective_date or _now()[:10]
    conn = _db()
    try:
        # Close previous primary if this is primary
        if is_primary:
            conn.execute("""
                UPDATE employee_lcats SET end_date = ?
                WHERE employee_id = ? AND is_primary = 1 AND end_date IS NULL
            """, (eff, employee_id))

        aid = _new_id()
        conn.execute("""
            INSERT INTO employee_lcats (id, employee_id, lcat_id,
                effective_date, is_primary)
            VALUES (?, ?, ?, ?, ?)
        """, (aid, employee_id, lcat_id, eff, 1 if is_primary else 0))
        conn.commit()
        return {"success": True, "id": aid, "employee_id": employee_id,
                "lcat_id": lcat_id, "effective_date": eff}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def suggest_lcat(employee_id: str) -> dict:
    """Suggest LCAT assignments from FPDS benchmarks based on employee data."""
    conn = _db()
    try:
        emp = conn.execute(
            "SELECT * FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if not emp:
            return {"success": False, "error": "Employee not found"}

        # Get employee skills
        skills = [r["skill_name"] for r in conn.execute("""
            SELECT s.skill_name FROM employee_skills es
            JOIN skills s ON es.skill_id = s.id
            WHERE es.employee_id = ?
        """, (employee_id,)).fetchall()]

        # Look for FPDS benchmarks matching title keywords
        title_word = (emp["job_title"] or "").split()[0] if emp["job_title"] else ""
        benchmarks = []
        if title_word:
            benchmarks = conn.execute("""
                SELECT DISTINCT labor_category, average_rate, median_rate,
                       percentile_75, naics_code, agency
                FROM pricing_benchmarks
                WHERE labor_category LIKE ?
                ORDER BY data_period DESC LIMIT 10
            """, (f"%{title_word}%",)).fetchall()

        # Match existing LCAT definitions
        lcat_matches = []
        for lcat in conn.execute("SELECT * FROM lcats").fetchall():
            score = 0
            lcat_name_lower = lcat["lcat_name"].lower()
            if title_word.lower() in lcat_name_lower:
                score += 3
            for skill in skills:
                if skill.lower() in lcat_name_lower:
                    score += 1
                typical = json.loads(lcat["typical_skills"] or "[]")
                if any(skill.lower() in t.lower() for t in typical):
                    score += 2
            if score > 0:
                lcat_matches.append({"lcat": dict(lcat), "match_score": score})

        lcat_matches.sort(key=lambda x: x["match_score"], reverse=True)

        return {
            "success": True,
            "employee": {"id": emp["id"], "full_name": emp["full_name"],
                         "job_title": emp["job_title"]},
            "suggested_lcats": lcat_matches[:5],
            "fpds_benchmarks": [dict(r) for r in benchmarks[:5]],
            "employee_skills": skills,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LCAT and cost structure manager")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list-lcats", action="store_true")
    grp.add_argument("--add-lcat", action="store_true")
    grp.add_argument("--set-rate", action="store_true")
    grp.add_argument("--assign", action="store_true")
    grp.add_argument("--cost-sheet", action="store_true")
    grp.add_argument("--suggest", action="store_true")

    parser.add_argument("--code")
    parser.add_argument("--name")
    parser.add_argument("--lcat", help="LCAT ID")
    parser.add_argument("--employee", help="Employee ID")
    parser.add_argument("--dlr", type=float, help="Direct labor rate $/hr")
    parser.add_argument("--fringe", type=float, default=0.0)
    parser.add_argument("--overhead", type=float, default=0.0)
    parser.add_argument("--ga", type=float, default=0.0)
    parser.add_argument("--fee", type=float, default=0.0)
    parser.add_argument("--hours", type=float, default=2080.0,
                        help="Annual hours for cost sheet (default: 2080)")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.list_lcats:
        result = list_lcats()
    elif args.add_lcat:
        if not args.code or not args.name:
            parser.error("--code and --name required")
        result = add_lcat(args.code, args.name)
    elif args.set_rate:
        if not args.lcat or not args.dlr:
            parser.error("--lcat and --dlr required")
        result = set_rate(args.lcat, args.dlr, args.fringe,
                          args.overhead, args.ga, args.fee)
    elif args.assign:
        if not args.employee or not args.lcat:
            parser.error("--employee and --lcat required")
        result = assign_lcat(args.employee, args.lcat)
    elif args.cost_sheet:
        if not args.lcat:
            parser.error("--lcat required")
        result = cost_sheet(args.lcat, args.hours)
    elif args.suggest:
        if not args.employee:
            parser.error("--employee required")
        result = suggest_lcat(args.employee)
    else:
        result = {"error": "No action"}

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
