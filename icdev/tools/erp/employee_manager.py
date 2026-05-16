#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
"""Employee master management for ERP module.

Manages employees from own company, subcontractors, and teaming partners.
Supports clearance tracking, availability, and linkage to resumes for proposals.

Usage:
    python tools/erp/employee_manager.py --list [--json]
    python tools/erp/employee_manager.py --get <id> [--json]
    python tools/erp/employee_manager.py --add --name "Jane Smith" --title "Sr. Engineer" [--json]
    python tools/erp/employee_manager.py --update <id> --status inactive [--json]
    python tools/erp/employee_manager.py --stats [--json]
    python tools/erp/employee_manager.py --clearance-report [--json]
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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_employee(
    full_name: str,
    title: str = None,
    email: str = None,
    phone: str = None,
    company_type: str = "own",
    partner_firm_id: str = None,
    department: str = None,
    hire_date: str = None,
    clearance_level: str = None,
    clearance_status: str = None,
    clearance_expiry: str = None,
    years_experience: int = None,
    location: str = None,
    availability: str = "available",
    resume_id: str = None,
    education: str = None,
    linkedin_url: str = None,
    notes: str = None,
) -> dict:
    conn = _db()
    try:
        eid = _new_id()
        now = _now()
        conn.execute("""
            INSERT INTO employees (
                id, full_name, email, phone, job_title, company_type,
                partner_firm_id, department, hire_date, clearance_level,
                clearance_status, clearance_expiry, years_experience,
                location, availability, resume_id, education,
                linkedin_url, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (eid, full_name, email, phone, title, company_type,
              partner_firm_id, department, hire_date, clearance_level,
              clearance_status, clearance_expiry, years_experience,
              location, availability, resume_id, education,
              linkedin_url, notes, now, now))
        conn.commit()
        return {"success": True, "id": eid, "full_name": full_name}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_employee(employee_id: str) -> dict:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Employee not found"}

        emp = dict(row)

        # Attach skills
        emp["skills"] = [dict(r) for r in conn.execute("""
            SELECT s.skill_name, s.category, es.proficiency, es.years_used
            FROM employee_skills es
            JOIN skills s ON es.skill_id = s.id
            WHERE es.employee_id = ?
            ORDER BY es.proficiency DESC
        """, (employee_id,)).fetchall()]

        # Attach certifications
        emp["certifications"] = [dict(r) for r in conn.execute("""
            SELECT cert_name, issuing_body, expiry_date, status
            FROM certifications
            WHERE employee_id = ?
            ORDER BY expiry_date DESC NULLS LAST
        """, (employee_id,)).fetchall()]

        # Attach current LCAT
        lcat_row = conn.execute("""
            SELECT l.lcat_code, l.lcat_name, el.effective_date, el.is_primary
            FROM employee_lcats el
            JOIN lcats l ON el.lcat_id = l.id
            WHERE el.employee_id = ? AND el.end_date IS NULL
            ORDER BY el.is_primary DESC, el.effective_date DESC
        """, (employee_id,)).fetchone()
        emp["current_lcat"] = dict(lcat_row) if lcat_row else None

        return {"success": True, "employee": emp}
    finally:
        conn.close()


def list_employees(
    company_type: str = None,
    clearance_level: str = None,
    availability: str = None,
    status: str = "active",
) -> dict:
    conn = _db()
    try:
        query = """
            SELECT e.id, e.full_name, e.job_title, e.company_type,
                   e.clearance_level, e.availability, e.status,
                   e.location, e.years_experience,
                   l.lcat_code, l.lcat_name
            FROM employees e
            LEFT JOIN employee_lcats el ON el.employee_id = e.id
                AND el.end_date IS NULL AND el.is_primary = 1
            LEFT JOIN lcats l ON el.lcat_id = l.id
            WHERE 1=1
        """
        params = []
        if status:
            query += " AND e.status = ?"
            params.append(status)
        if company_type:
            query += " AND e.company_type = ?"
            params.append(company_type)
        if clearance_level:
            query += " AND e.clearance_level = ?"
            params.append(clearance_level)
        if availability:
            query += " AND e.availability = ?"
            params.append(availability)
        query += " ORDER BY e.full_name"

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "employees": [dict(r) for r in rows],
                "count": len(rows)}
    finally:
        conn.close()


def update_employee(employee_id: str, **fields) -> dict:
    allowed = {"email", "phone", "job_title", "company_type", "partner_firm_id",
               "department", "hire_date", "status", "clearance_level",
               "clearance_status", "clearance_expiry", "years_experience",
               "location", "availability", "resume_id", "education",
               "linkedin_url", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return {"success": False, "error": "No valid fields to update"}

    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [employee_id]

    conn = _db()
    try:
        conn.execute(f"UPDATE employees SET {cols} WHERE id = ?", vals)
        conn.commit()
        return {"success": True, "id": employee_id, "updated": list(updates.keys())}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    conn = _db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE status = 'active'"
        ).fetchone()[0]
        by_type = {r[0]: r[1] for r in conn.execute(
            "SELECT company_type, COUNT(*) FROM employees WHERE status='active' GROUP BY company_type"
        ).fetchall()}
        by_clearance = {r[0] or "none": r[1] for r in conn.execute(
            "SELECT clearance_level, COUNT(*) FROM employees WHERE status='active' GROUP BY clearance_level"
        ).fetchall()}
        by_avail = {r[0] or "unknown": r[1] for r in conn.execute(
            "SELECT availability, COUNT(*) FROM employees WHERE status='active' GROUP BY availability"
        ).fetchall()}
        expiring_certs = conn.execute("""
            SELECT COUNT(*) FROM certifications
            WHERE status = 'active'
              AND expiry_date IS NOT NULL
              AND expiry_date <= date('now', '+90 days')
        """).fetchone()[0]
        return {
            "success": True,
            "total_active": total,
            "by_company_type": by_type,
            "by_clearance": by_clearance,
            "by_availability": by_avail,
            "certs_expiring_90d": expiring_certs,
        }
    finally:
        conn.close()


def clearance_report() -> dict:
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT e.full_name, e.job_title, e.clearance_level,
                   e.clearance_status, e.clearance_expiry,
                   l.lcat_code
            FROM employees e
            LEFT JOIN employee_lcats el ON el.employee_id = e.id
                AND el.end_date IS NULL AND el.is_primary = 1
            LEFT JOIN lcats l ON el.lcat_id = l.id
            WHERE e.status = 'active'
              AND e.clearance_level IS NOT NULL
              AND e.clearance_level != 'none'
            ORDER BY
                CASE e.clearance_level
                    WHEN 'ts_sci_poly' THEN 0 WHEN 'ts_sci' THEN 1
                    WHEN 'top_secret' THEN 2 WHEN 'secret' THEN 3
                    WHEN 'public_trust' THEN 4 ELSE 5
                END
        """).fetchall()
        return {"success": True, "cleared_employees": [dict(r) for r in rows],
                "count": len(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Employee master management")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list", action="store_true")
    grp.add_argument("--get", metavar="ID")
    grp.add_argument("--add", action="store_true")
    grp.add_argument("--update", metavar="ID")
    grp.add_argument("--stats", action="store_true")
    grp.add_argument("--clearance-report", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--title")
    parser.add_argument("--email")
    parser.add_argument("--company-type", default="own",
                        choices=["own", "sub", "partner"])
    parser.add_argument("--clearance")
    parser.add_argument("--availability",
                        choices=["available", "committed", "partial"])
    parser.add_argument("--status", default="active")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.list:
        result = list_employees(status=args.status)
    elif args.get:
        result = get_employee(args.get)
    elif args.add:
        if not args.name:
            parser.error("--name required with --add")
        result = add_employee(
            full_name=args.name,
            title=args.title,
            email=args.email,
            company_type=args.company_type,
            clearance_level=args.clearance,
            availability=args.availability,
        )
    elif args.update:
        updates = {}
        if args.status:
            updates["status"] = args.status
        if args.clearance:
            updates["clearance_level"] = args.clearance
        if args.availability:
            updates["availability"] = args.availability
        result = update_employee(args.update, **updates)
    elif args.stats:
        result = get_stats()
    elif args.clearance_report:
        result = clearance_report()
    else:
        result = {"error": "No action specified"}

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
