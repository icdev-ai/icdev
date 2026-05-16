#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
"""CRM contact management.

Manages contacts (competitors, partners, frienemies, vendors, prospects, agency stakeholders)
with relationship classification, sector tracking, and interaction history.

Usage:
    python tools/crm/contact_manager.py --list [--sector DoD] [--rel competitor] [--json]
    python tools/crm/contact_manager.py --get <id> [--json]
    python tools/crm/contact_manager.py --add --name "John Smith" --company "Rival Corp" --rel competitor [--json]
    python tools/crm/contact_manager.py --log <id> --type meeting --notes "Discussed RFP" [--json]
    python tools/crm/contact_manager.py --link-opp <contact_id> --opp <opp_id> --role decision_maker [--json]
    python tools/crm/contact_manager.py --stats [--json]
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

def add_contact(
    full_name: str,
    title: str = None,
    email: str = None,
    phone: str = None,
    company: str = None,
    relationship_type: str = "prospect",  # type_name
    sector: str = None,
    agency: str = None,
    sub_agency: str = None,
    linkedin_url: str = None,
    sam_entity_id: str = None,
    notes: str = None,
) -> dict:
    conn = _db()
    try:
        # Resolve relationship type ID
        rt_row = conn.execute(
            "SELECT id FROM relationship_types WHERE type_name = ?",
            (relationship_type,)
        ).fetchone()
        rt_id = rt_row["id"] if rt_row else None

        cid = _new_id()
        now = _now()
        conn.execute("""
            INSERT INTO contacts (
                id, full_name, title, email, phone, company,
                relationship_type_id, sector, agency, sub_agency,
                linkedin_url, sam_entity_id, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cid, full_name, title, email, phone, company,
              rt_id, sector, agency, sub_agency,
              linkedin_url, sam_entity_id, notes, now, now))
        conn.commit()
        return {"success": True, "id": cid, "full_name": full_name,
                "relationship_type": relationship_type}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_contact(contact_id: str) -> dict:
    conn = _db()
    try:
        row = conn.execute("""
            SELECT c.*, rt.type_name AS relationship_type, rt.color_code
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            WHERE c.id = ?
        """, (contact_id,)).fetchone()
        if not row:
            return {"success": False, "error": "Contact not found"}

        contact = dict(row)

        # Interaction history
        contact["interactions"] = [dict(r) for r in conn.execute("""
            SELECT interaction_date, interaction_type, subject, notes,
                   outcome, next_action, next_action_date, logged_by
            FROM interactions
            WHERE contact_id = ?
            ORDER BY interaction_date DESC
            LIMIT 20
        """, (contact_id,)).fetchall()]

        # Linked opportunities
        contact["opportunities"] = [dict(r) for r in conn.execute("""
            SELECT o.title, o.agency, o.status, o.response_deadline,
                   pc.role, pc.influence_level
            FROM pipeline_contacts pc
            JOIN opportunities o ON pc.opportunity_id = o.id
            WHERE pc.contact_id = ?
            ORDER BY o.response_deadline DESC
        """, (contact_id,)).fetchall()]

        return {"success": True, "contact": contact}
    finally:
        conn.close()


def list_contacts(
    relationship_type: str = None,
    sector: str = None,
    status: str = "active",
    search: str = None,
) -> dict:
    conn = _db()
    try:
        query = """
            SELECT c.id, c.full_name, c.title, c.company, c.email,
                   c.sector, c.agency, c.status, c.last_contact_date,
                   rt.type_name AS relationship_type, rt.color_code,
                   COUNT(DISTINCT i.id) AS interaction_count,
                   COUNT(DISTINCT pc.opportunity_id) AS opp_count
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            LEFT JOIN interactions i ON i.contact_id = c.id
            LEFT JOIN pipeline_contacts pc ON pc.contact_id = c.id
            WHERE c.status = ?
        """
        params = [status]

        if relationship_type:
            query += " AND rt.type_name = ?"
            params.append(relationship_type)
        if sector:
            query += " AND c.sector = ?"
            params.append(sector)
        if search:
            query += " AND (c.full_name LIKE ? OR c.company LIKE ? OR c.email LIKE ?)"
            params.extend([f"%{search}%"] * 3)

        query += " GROUP BY c.id ORDER BY c.full_name LIMIT 200"

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "contacts": [dict(r) for r in rows],
                "count": len(rows)}
    finally:
        conn.close()


def update_contact(contact_id: str, **fields) -> dict:
    allowed = {"title", "email", "phone", "company", "sector", "agency",
               "sub_agency", "linkedin_url", "sam_entity_id", "notes",
               "status", "last_contact_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return {"success": False, "error": "No valid fields"}

    updates["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [contact_id]

    conn = _db()
    try:
        conn.execute(f"UPDATE contacts SET {cols} WHERE id = ?", vals)
        conn.commit()
        return {"success": True, "id": contact_id, "updated": list(updates.keys())}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# INTERACTION LOG
# ---------------------------------------------------------------------------

def log_interaction(
    contact_id: str,
    notes: str,
    interaction_type: str = "note",
    subject: str = None,
    outcome: str = None,
    next_action: str = None,
    next_action_date: str = None,
    opportunity_id: str = None,
    logged_by: str = None,
) -> dict:
    conn = _db()
    try:
        iid = _new_id()
        now = _now()
        conn.execute("""
            INSERT INTO interactions (
                id, contact_id, opportunity_id, interaction_date,
                interaction_type, subject, notes, outcome,
                next_action, next_action_date, logged_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (iid, contact_id, opportunity_id, now[:10],
              interaction_type, subject, notes, outcome,
              next_action, next_action_date, logged_by, now))

        # Update last contact date
        conn.execute(
            "UPDATE contacts SET last_contact_date = ?, updated_at = ? WHERE id = ?",
            (now[:10], now, contact_id)
        )
        conn.commit()
        return {"success": True, "interaction_id": iid,
                "contact_id": contact_id, "date": now[:10]}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PIPELINE LINKING
# ---------------------------------------------------------------------------

def link_to_opportunity(contact_id: str, opportunity_id: str,
                         role: str = None,
                         influence_level: str = "medium") -> dict:
    conn = _db()
    try:
        pid = _new_id()
        conn.execute("""
            INSERT INTO pipeline_contacts (
                id, contact_id, opportunity_id, role, influence_level
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(contact_id, opportunity_id)
            DO UPDATE SET role = excluded.role,
                influence_level = excluded.influence_level
        """, (pid, contact_id, opportunity_id, role, influence_level))
        conn.commit()
        return {"success": True, "contact_id": contact_id,
                "opportunity_id": opportunity_id, "role": role}
    except sqlite3.Error as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# STATS
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    conn = _db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE status = 'active'"
        ).fetchone()[0]
        by_type = {r[0] or "unknown": r[1] for r in conn.execute("""
            SELECT rt.type_name, COUNT(c.id)
            FROM contacts c
            LEFT JOIN relationship_types rt ON c.relationship_type_id = rt.id
            WHERE c.status = 'active'
            GROUP BY rt.type_name
        """).fetchall()}
        by_sector = {r[0] or "unknown": r[1] for r in conn.execute("""
            SELECT sector, COUNT(*)
            FROM contacts WHERE status = 'active'
            GROUP BY sector
        """).fetchall()}
        pending_actions = conn.execute("""
            SELECT COUNT(*) FROM interactions
            WHERE next_action IS NOT NULL
              AND (next_action_date IS NULL OR next_action_date >= date('now'))
              AND next_action_date <= date('now', '+14 days')
        """).fetchone()[0]
        return {
            "success": True,
            "total_active": total,
            "by_relationship_type": by_type,
            "by_sector": by_sector,
            "pending_actions_14d": pending_actions,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CRM contact management")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list", action="store_true")
    grp.add_argument("--get", metavar="ID")
    grp.add_argument("--add", action="store_true")
    grp.add_argument("--log", metavar="CONTACT_ID")
    grp.add_argument("--link-opp", metavar="CONTACT_ID")
    grp.add_argument("--stats", action="store_true")

    parser.add_argument("--name")
    parser.add_argument("--company")
    parser.add_argument("--email")
    parser.add_argument("--rel", default="prospect",
                        help="Relationship type name")
    parser.add_argument("--sector")
    parser.add_argument("--agency")
    parser.add_argument("--type", dest="interaction_type", default="note",
                        choices=["meeting", "call", "email", "conference",
                                 "site_visit", "note", "rfp_response",
                                 "bid_review", "demo"])
    parser.add_argument("--notes")
    parser.add_argument("--opp", help="Opportunity ID")
    parser.add_argument("--role")
    parser.add_argument("--search")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.list:
        result = list_contacts(relationship_type=args.rel,
                               sector=args.sector,
                               search=args.search)
    elif args.get:
        result = get_contact(args.get)
    elif args.add:
        if not args.name:
            parser.error("--name required")
        result = add_contact(
            full_name=args.name,
            company=args.company,
            email=args.email,
            relationship_type=args.rel,
            sector=args.sector,
            agency=args.agency,
        )
    elif args.log:
        if not args.notes:
            parser.error("--notes required")
        result = log_interaction(
            contact_id=args.log,
            notes=args.notes,
            interaction_type=args.interaction_type,
            opportunity_id=args.opp,
        )
    elif args.link_opp:
        if not args.opp:
            parser.error("--opp required")
        result = link_to_opportunity(args.link_opp, args.opp,
                                     role=args.role)
    elif args.stats:
        result = get_stats()
    else:
        result = {"error": "No action"}

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
