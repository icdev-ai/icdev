#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Skills matrix tracking and capability aggregation.

Manages the skills registry and employee skill assignments.
Auto-aggregates company capabilities from employee skill profiles.

Usage:
    python tools/erp/skills_tracker.py --list-skills [--json]
    python tools/erp/skills_tracker.py --add-skill --name "Python" --category technical [--json]
    python tools/erp/skills_tracker.py --assign --employee <id> --skill <id> --proficiency advanced [--json]
    python tools/erp/skills_tracker.py --matrix [--clearance secret] [--json]
    python tools/erp/skills_tracker.py --aggregate-capabilities [--json]
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


PROFICIENCY_RANK = {"beginner": 1, "intermediate": 2, "advanced": 3, "expert": 4}


def add_skill(skill_name: str, category: str = "technical",
              description: str = None) -> dict:
    conn = _db()
    try:
        sid = _new_id()
        conn.execute(
            "INSERT INTO skills (id, skill_name, category, description) VALUES (?, ?, ?, ?)",
            (sid, skill_name, category, description)
        )
        conn.commit()
        return {"success": True, "id": sid, "skill_name": skill_name}
    except sqlite3.IntegrityError:
        return {"success": False, "error": f"Skill '{skill_name}' already exists"}
    finally:
        conn.close()


def list_skills(category: str = None) -> dict:
    conn = _db()
    try:
        query = """
            SELECT s.id, s.skill_name, s.category, s.description,
                   COUNT(es.employee_id) AS employee_count
            FROM skills s
            LEFT JOIN employee_skills es ON es.skill_id = s.id
            WHERE 1=1
        """
        params = []
        if category:
            query += " AND s.category = ?"
            params.append(category)
        query += " GROUP BY s.id ORDER BY s.category, s.skill_name"
        rows = conn.execute(query, params).fetchall()
        return {"success": True, "skills": [dict(r) for r in rows]}
    finally:
        conn.close()


def assign_skill(employee_id: str, skill_id: str,
                 proficiency: str = "intermediate",
                 years_used: int = None) -> dict:
    conn = _db()
    try:
        eid = _new_id()
        conn.execute("""
            INSERT INTO employee_skills (id, employee_id, skill_id,
                proficiency, years_used)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(employee_id, skill_id)
            DO UPDATE SET proficiency=excluded.proficiency,
                years_used=excluded.years_used
        """, (eid, employee_id, skill_id, proficiency, years_used))
        conn.commit()
        return {"success": True, "employee_id": employee_id,
                "skill_id": skill_id, "proficiency": proficiency}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def skills_matrix(clearance_level: str = None,
                  availability: str = None) -> dict:
    """Build a skills matrix: employees × skills with proficiency."""
    conn = _db()
    try:
        # Get active employees
        emp_query = """
            SELECT e.id, e.full_name, e.job_title, e.clearance_level,
                   e.availability, l.lcat_code
            FROM employees e
            LEFT JOIN employee_lcats el ON el.employee_id = e.id
                AND el.end_date IS NULL AND el.is_primary = 1
            LEFT JOIN lcats l ON el.lcat_id = l.id
            WHERE e.status = 'active'
        """
        params = []
        if clearance_level:
            emp_query += " AND e.clearance_level >= ?"
            params.append(clearance_level)
        if availability:
            emp_query += " AND e.availability = ?"
            params.append(availability)
        emp_query += " ORDER BY e.full_name"
        employees = conn.execute(emp_query, params).fetchall()

        # Get all skills with employee assignments
        skill_rows = conn.execute("""
            SELECT s.id, s.skill_name, s.category,
                   es.employee_id, es.proficiency
            FROM employee_skills es
            JOIN skills s ON es.skill_id = s.id
        """).fetchall()

        # Build skill map per employee
        emp_skill_map = {}
        for row in skill_rows:
            emp_skill_map.setdefault(row["employee_id"], {})[row["skill_name"]] = {
                "proficiency": row["proficiency"],
                "rank": PROFICIENCY_RANK.get(row["proficiency"], 0),
            }

        # Get all unique skills
        all_skills = conn.execute(
            "SELECT id, skill_name, category FROM skills ORDER BY category, skill_name"
        ).fetchall()

        matrix = []
        for emp in employees:
            emp_dict = dict(emp)
            emp_dict["skills"] = emp_skill_map.get(emp["id"], {})
            matrix.append(emp_dict)

        return {
            "success": True,
            "matrix": matrix,
            "all_skills": [dict(s) for s in all_skills],
            "employee_count": len(matrix),
        }
    finally:
        conn.close()


def aggregate_capabilities() -> dict:
    """Auto-generate company capabilities from employee skill profiles.

    Groups skills by category, counts proficient employees,
    and upserts into capabilities table.
    """
    conn = _db()
    try:
        # Aggregate skills: count employees per skill with proficiency >= intermediate
        skill_agg = conn.execute("""
            SELECT s.skill_name, s.category,
                   COUNT(CASE WHEN es.proficiency IN ('intermediate','advanced','expert')
                              THEN 1 END) AS employee_count,
                   MAX(es.proficiency) AS top_proficiency
            FROM employee_skills es
            JOIN skills s ON es.skill_id = s.id
            JOIN employees e ON es.employee_id = e.id
            WHERE e.status = 'active'
            GROUP BY s.id
            HAVING employee_count >= 1
            ORDER BY employee_count DESC, s.category
        """).fetchall()

        upserted = []
        now = _now()
        for row in skill_agg:
            # Check if capability already exists
            existing = conn.execute(
                "SELECT id FROM capabilities WHERE capability_name = ?",
                (row["skill_name"],)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE capabilities SET
                        employee_count = ?,
                        proficiency_avg = ?,
                        evidence_source = 'employees',
                        updated_at = ?
                    WHERE id = ?
                """, (row["employee_count"], row["top_proficiency"],
                      now, existing["id"]))
            else:
                cid = _new_id()
                conn.execute("""
                    INSERT INTO capabilities (
                        id, capability_name, category,
                        employee_count, proficiency_avg, evidence_source,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'employees', ?, ?)
                """, (cid, row["skill_name"], row["category"],
                      row["employee_count"], row["top_proficiency"], now, now))
            upserted.append(row["skill_name"])

        conn.commit()
        return {
            "success": True,
            "capabilities_updated": len(upserted),
            "capabilities": upserted,
        }
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_capabilities() -> dict:
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT capability_name, category, description,
                   employee_count, proficiency_avg, proposal_count, last_used
            FROM capabilities WHERE is_active = 1
            ORDER BY employee_count DESC, category
        """).fetchall()
        return {"success": True, "capabilities": [dict(r) for r in rows]}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Skills matrix management")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list-skills", action="store_true")
    grp.add_argument("--add-skill", action="store_true")
    grp.add_argument("--assign", action="store_true")
    grp.add_argument("--matrix", action="store_true")
    grp.add_argument("--aggregate-capabilities", action="store_true")
    grp.add_argument("--list-capabilities", action="store_true")

    parser.add_argument("--name")
    parser.add_argument("--category", default="technical")
    parser.add_argument("--employee")
    parser.add_argument("--skill")
    parser.add_argument("--proficiency",
                        choices=["beginner", "intermediate", "advanced", "expert"],
                        default="intermediate")
    parser.add_argument("--clearance")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.list_skills:
        result = list_skills(args.category)
    elif args.add_skill:
        if not args.name:
            parser.error("--name required")
        result = add_skill(args.name, args.category)
    elif args.assign:
        if not args.employee or not args.skill:
            parser.error("--employee and --skill required")
        result = assign_skill(args.employee, args.skill, args.proficiency)
    elif args.matrix:
        result = skills_matrix(clearance_level=args.clearance)
    elif args.aggregate_capabilities:
        result = aggregate_capabilities()
    elif args.list_capabilities:
        result = list_capabilities()
    else:
        result = {"error": "No action"}

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
