#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Vendor and partner capability assessments for CRM.

Tracks technical areas, certifications, CPARS ratings, and
contract vehicles for partners, competitors, vendors, and subs.

Usage:
    python tools/crm/vendor_assessor.py --list [--type partner] [--json]
    python tools/crm/vendor_assessor.py --get <id> [--json]
    python tools/crm/vendor_assessor.py --assess --name "Acme Corp" --type partner [--json]
    python tools/crm/vendor_assessor.py --score <id> [--json]
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


CPARS_WEIGHT = {
    "exceptional": 1.0,
    "very_good": 0.8,
    "satisfactory": 0.6,
    "marginal": 0.3,
    "unsatisfactory": 0.0,
}


def assess_vendor(
    company_name: str,
    company_type: str = "partner",
    company_id: str = None,
    technical_areas: list = None,
    certifications: list = None,
    contract_vehicles: list = None,
    clearance_level: str = None,
    employee_count: int = None,
    sam_score: float = None,
    cpars_rating: str = None,
    notes: str = None,
) -> dict:
    conn = _db()
    try:
        # Check existing
        existing = conn.execute(
            "SELECT id FROM vendor_capabilities WHERE company_name = ? AND company_type = ?",
            (company_name, company_type)
        ).fetchone()

        now = _now()
        if existing:
            conn.execute("""
                UPDATE vendor_capabilities SET
                    technical_areas = ?, certifications = ?,
                    contract_vehicles = ?, clearance_level = ?,
                    employee_count = ?, sam_score = ?,
                    cpars_rating = ?, notes = ?,
                    last_assessed = ?, updated_at = ?
                WHERE id = ?
            """, (json.dumps(technical_areas or []),
                  json.dumps(certifications or []),
                  json.dumps(contract_vehicles or []),
                  clearance_level, employee_count, sam_score,
                  cpars_rating, notes, now[:10], now, existing["id"]))
            vid = existing["id"]
            action = "updated"
        else:
            vid = _new_id()
            conn.execute("""
                INSERT INTO vendor_capabilities (
                    id, company_id, company_type, company_name,
                    technical_areas, certifications, contract_vehicles,
                    clearance_level, employee_count, sam_score, cpars_rating,
                    notes, last_assessed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (vid, company_id, company_type, company_name,
                  json.dumps(technical_areas or []),
                  json.dumps(certifications or []),
                  json.dumps(contract_vehicles or []),
                  clearance_level, employee_count, sam_score, cpars_rating,
                  notes, now[:10], now, now))
            action = "created"

        conn.commit()
        return {"success": True, "id": vid, "company_name": company_name,
                "action": action}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def score_vendor(vendor_id: str) -> dict:
    """Compute a capability scorecard for a vendor.

    Scoring (0-100):
      - Technical breadth:   20 pts (10 areas = max)
      - Certifications:      25 pts (5 certs = max)
      - Contract vehicles:   20 pts (4 vehicles = max)
      - Clearance:           15 pts
      - CPARS performance:   20 pts
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM vendor_capabilities WHERE id = ?", (vendor_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "Vendor not found"}

        v = dict(row)
        tech = json.loads(v.get("technical_areas") or "[]")
        certs = json.loads(v.get("certifications") or "[]")
        vehicles = json.loads(v.get("contract_vehicles") or "[]")

        score_tech = min(len(tech) * 2, 20)
        score_cert = min(len(certs) * 5, 25)
        score_vehicle = min(len(vehicles) * 5, 20)

        clearance_pts = {
            "ts_sci_poly": 15, "ts_sci": 13, "top_secret": 11,
            "secret": 8, "public_trust": 4, "none": 0,
        }
        score_clearance = clearance_pts.get(v.get("clearance_level") or "none", 0)

        cpars_pts = CPARS_WEIGHT.get(v.get("cpars_rating") or "", 0.5) * 20
        score_cpars = round(cpars_pts, 1)

        total = score_tech + score_cert + score_vehicle + score_clearance + score_cpars

        return {
            "success": True,
            "vendor": {"id": vendor_id, "company_name": v["company_name"],
                       "company_type": v["company_type"]},
            "scorecard": {
                "technical_breadth": {"score": score_tech, "max": 20,
                                      "areas": tech},
                "certifications": {"score": score_cert, "max": 25,
                                   "certs": certs},
                "contract_vehicles": {"score": score_vehicle, "max": 20,
                                      "vehicles": vehicles},
                "clearance": {"score": score_clearance, "max": 15,
                              "level": v.get("clearance_level")},
                "cpars_performance": {"score": score_cpars, "max": 20,
                                      "rating": v.get("cpars_rating")},
                "total_score": round(total, 1),
                "grade": "A" if total >= 80 else "B" if total >= 60
                         else "C" if total >= 40 else "D",
            },
        }
    finally:
        conn.close()


def list_vendors(company_type: str = None) -> dict:
    conn = _db()
    try:
        query = """
            SELECT id, company_name, company_type, clearance_level,
                   cpars_rating, sam_score, last_assessed, employee_count
            FROM vendor_capabilities
            WHERE 1=1
        """
        params = []
        if company_type:
            query += " AND company_type = ?"
            params.append(company_type)
        query += " ORDER BY company_name"
        rows = conn.execute(query, params).fetchall()
        return {"success": True, "vendors": [dict(r) for r in rows]}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Vendor capability assessments")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list", action="store_true")
    grp.add_argument("--get", metavar="ID")
    grp.add_argument("--assess", action="store_true")
    grp.add_argument("--score", metavar="ID")

    parser.add_argument("--name")
    parser.add_argument("--type", default="partner",
                        choices=["partner", "competitor", "vendor", "subcontractor"])
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.list:
        result = list_vendors(company_type=args.type if args.type != "partner" else None)
    elif args.get:
        conn = _db()
        row = conn.execute(
            "SELECT * FROM vendor_capabilities WHERE id = ?", (args.get,)
        ).fetchone()
        conn.close()
        result = {"success": bool(row), "vendor": dict(row) if row else None}
    elif args.assess:
        if not args.name:
            parser.error("--name required")
        result = assess_vendor(args.name, company_type=args.type)
    elif args.score:
        result = score_vendor(args.score)
    else:
        result = {"error": "No action"}

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
