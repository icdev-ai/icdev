#!/usr/bin/env python3
# CUI // SP-CTI
"""ISA/MOU Lifecycle Manager — track Interconnection Security Agreements
and Memoranda of Understanding for interconnected systems.

Manages the full ISA lifecycle: create, review, renew, revoke, and query
expiring or review-due agreements.

Table used:
  - isa_agreements (id TEXT PK, project_id, agreement_type, partner_system,
        partner_org, status, signed_date, expiry_date, data_types_shared,
        ports_protocols, security_controls, poc_name, poc_email, document_path,
        review_cadence_days, next_review_date, classification, created_at, updated_at)

CLI:
  python tools/supply_chain/isa_manager.py --project-id <id> --create ...
  python tools/supply_chain/isa_manager.py --project-id <id> --expiring --days 90 --json
  python tools/supply_chain/isa_manager.py --project-id <id> --review-due --json
  python tools/supply_chain/isa_manager.py --project-id <id> --list --json
  python tools/supply_chain/isa_manager.py --renew --isa-id <id> --new-expiry 2027-01-01 --json
  python tools/supply_chain/isa_manager.py --revoke --isa-id <id> --reason "Decomissioned" --json
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

AGREEMENT_TYPES = ("isa", "mou", "moa", "sla", "ila")
ISA_STATUSES = ("draft", "review", "signed", "active", "expiring", "expired", "terminated")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append-only audit trail entry."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, event_type, "icdev-supply-chain-agent", action,
             json.dumps(details) if isinstance(details, dict) else str(details),
             "CUI"),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict, parsing JSON fields."""
    d = dict(row)
    for key in ("data_types_shared", "ports_protocols", "security_controls"):
        if key in d and d[key] and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def create_isa(project_id, partner_system, data_types_shared,
               authorization_date, expiry_date,
               agreement_type="isa", partner_org=None,
               review_cadence_days=365, db_path=None):
    """Create a new ISA/MOU agreement.

    Args:
        project_id: Project identifier.
        partner_system: Name of the partner/target system.
        data_types_shared: list of data type strings (e.g. ["CUI", "PII"]).
        authorization_date: Signing date (YYYY-MM-DD).
        expiry_date: Expiration date (YYYY-MM-DD).
        agreement_type: isa / mou / moa / sla / ila.
        partner_org: Optional partner organization name.
        review_cadence_days: Days between reviews (default 365).
        db_path: Optional DB path override.

    Returns:
        dict with isa_id, partner_system, status, expiry_date.
    """
    if agreement_type not in AGREEMENT_TYPES:
        raise ValueError(f"agreement_type must be one of {AGREEMENT_TYPES}")

    isa_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Compute next review date from signing
    try:
        auth_dt = datetime.strptime(authorization_date, "%Y-%m-%d")
        next_review = (auth_dt + timedelta(days=review_cadence_days)).strftime("%Y-%m-%d")
    except ValueError:
        next_review = None

    # Determine initial status
    try:
        exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        today = datetime.utcnow().date()
        if exp_dt.date() < today:
            status = "expired"
        elif (exp_dt.date() - today).days <= 90:
            status = "expiring"
        else:
            status = "active"
    except ValueError:
        status = "active"

    data_json = json.dumps(data_types_shared) if isinstance(data_types_shared, list) else data_types_shared

    conn = _get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO isa_agreements
               (id, project_id, agreement_type, partner_system, partner_org,
                status, signed_date, expiry_date, data_types_shared,
                review_cadence_days, next_review_date, classification,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (isa_id, project_id, agreement_type, partner_system, partner_org,
             status, authorization_date, expiry_date, data_json,
             review_cadence_days, next_review, "CUI",
             now, now),
        )
        conn.commit()
        _log_audit(conn, project_id, "isa_created",
                   f"Created {agreement_type.upper()}: {partner_system}",
                   {"isa_id": isa_id, "expiry_date": expiry_date,
                    "status": status})
        return {
            "isa_id": isa_id,
            "partner_system": partner_system,
            "agreement_type": agreement_type,
            "status": status,
            "expiry_date": expiry_date,
        }
    finally:
        conn.close()


def get_expiring(project_id, days_ahead=90, db_path=None):
    """Find ISAs expiring within N days.

    Returns:
        dict with project_id, days_ahead, expiring list with days_until_expiry
        and renewal_urgency.
    """
    conn = _get_connection(db_path)
    try:
        cutoff = (datetime.utcnow() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        today = datetime.utcnow().strftime("%Y-%m-%d")

        rows = conn.execute(
            """SELECT * FROM isa_agreements
               WHERE project_id = ?
                 AND expiry_date IS NOT NULL
                 AND expiry_date <= ?
                 AND status NOT IN ('terminated', 'expired')
               ORDER BY expiry_date ASC""",
            (project_id, cutoff),
        ).fetchall()

        results = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                exp_dt = datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()
                today_dt = datetime.utcnow().date()
                days_left = (exp_dt - today_dt).days
            except (ValueError, TypeError):
                days_left = -1

            if days_left < 0:
                urgency = "critical"
            elif days_left <= 30:
                urgency = "critical"
            elif days_left <= 60:
                urgency = "high"
            elif days_left <= 90:
                urgency = "medium"
            else:
                urgency = "low"

            d["days_until_expiry"] = days_left
            d["renewal_urgency"] = urgency
            results.append(d)

        return {
            "project_id": project_id,
            "days_ahead": days_ahead,
            "expiring_count": len(results),
            "expiring": results,
        }
    finally:
        conn.close()


def get_review_due(project_id, db_path=None):
    """Find ISAs past their review cadence.

    Returns:
        dict with project_id, review_due list with days_overdue.
    """
    conn = _get_connection(db_path)
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT * FROM isa_agreements
               WHERE project_id = ?
                 AND next_review_date IS NOT NULL
                 AND next_review_date <= ?
                 AND status NOT IN ('terminated', 'expired')
               ORDER BY next_review_date ASC""",
            (project_id, today),
        ).fetchall()

        results = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                review_dt = datetime.strptime(d["next_review_date"], "%Y-%m-%d").date()
                today_dt = datetime.utcnow().date()
                days_overdue = (today_dt - review_dt).days
            except (ValueError, TypeError):
                days_overdue = 0

            d["days_overdue"] = days_overdue
            d["last_review_date"] = d.get("next_review_date")
            results.append(d)

        return {
            "project_id": project_id,
            "review_due_count": len(results),
            "review_due": results,
        }
    finally:
        conn.close()


def renew_isa(isa_id, new_expiry_date, notes=None, db_path=None):
    """Renew an ISA by updating its expiry date and resetting the review cycle.

    Returns:
        dict with updated ISA fields.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM isa_agreements WHERE id = ?", (isa_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"ISA '{isa_id}' not found.")

        d = _row_to_dict(row)
        cadence = d.get("review_cadence_days", 365) or 365
        now = datetime.utcnow()
        next_review = (now + timedelta(days=cadence)).strftime("%Y-%m-%d")

        conn.execute(
            """UPDATE isa_agreements
               SET expiry_date = ?, next_review_date = ?, status = 'active',
                   updated_at = ?
               WHERE id = ?""",
            (new_expiry_date, next_review, now.isoformat(), isa_id),
        )
        conn.commit()

        _log_audit(conn, d["project_id"], "isa_renewed",
                   f"Renewed ISA {isa_id}: new expiry {new_expiry_date}",
                   {"isa_id": isa_id, "new_expiry": new_expiry_date,
                    "notes": notes})

        d["expiry_date"] = new_expiry_date
        d["next_review_date"] = next_review
        d["status"] = "active"
        d["updated_at"] = now.isoformat()
        if notes:
            d["renewal_notes"] = notes
        return d
    finally:
        conn.close()


def revoke_isa(isa_id, reason, db_path=None):
    """Revoke an ISA agreement.

    Returns:
        dict confirming revocation.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM isa_agreements WHERE id = ?", (isa_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"ISA '{isa_id}' not found.")

        d = _row_to_dict(row)
        now = datetime.utcnow().isoformat()

        conn.execute(
            """UPDATE isa_agreements
               SET status = 'terminated', updated_at = ?
               WHERE id = ?""",
            (now, isa_id),
        )
        conn.commit()

        _log_audit(conn, d["project_id"], "isa_expired",
                   f"Revoked ISA {isa_id}: {reason}",
                   {"isa_id": isa_id, "reason": reason})

        return {
            "isa_id": isa_id,
            "partner_system": d["partner_system"],
            "status": "terminated",
            "reason": reason,
            "revoked_at": now,
        }
    finally:
        conn.close()


def list_isas(project_id, status=None, db_path=None):
    """List all ISAs for a project, optionally filtered by status.

    Returns:
        dict with project_id, total, and agreements list.
    """
    conn = _get_connection(db_path)
    try:
        if status:
            if status not in ISA_STATUSES:
                raise ValueError(f"status must be one of {ISA_STATUSES}")
            rows = conn.execute(
                """SELECT * FROM isa_agreements
                   WHERE project_id = ? AND status = ?
                   ORDER BY expiry_date ASC""",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM isa_agreements
                   WHERE project_id = ?
                   ORDER BY expiry_date ASC""",
                (project_id,),
            ).fetchall()

        agreements = [_row_to_dict(r) for r in rows]
        return {
            "project_id": project_id,
            "total": len(agreements),
            "filter_status": status,
            "agreements": agreements,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ISA/MOU Lifecycle Manager (RICOAS)")
    parser.add_argument("--project-id", help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Create
    parser.add_argument("--create", action="store_true",
                        help="Create a new ISA/MOU")
    parser.add_argument("--source-system", help="Source system name (this system)")
    parser.add_argument("--target-system", help="Partner/target system name")
    parser.add_argument("--agreement-type", choices=AGREEMENT_TYPES,
                        default="isa", help="Agreement type (default: isa)")
    parser.add_argument("--partner-org", help="Partner organization")
    parser.add_argument("--data-types", help="JSON array of data types shared")
    parser.add_argument("--auth-date", help="Authorization/signing date (YYYY-MM-DD)")
    parser.add_argument("--expiry-date", help="Expiration date (YYYY-MM-DD)")
    parser.add_argument("--cadence", type=int, default=365,
                        help="Review cadence in days (default: 365)")

    # Query
    parser.add_argument("--expiring", action="store_true",
                        help="Show ISAs expiring soon")
    parser.add_argument("--days", type=int, default=90,
                        help="Days ahead for expiring query (default: 90)")
    parser.add_argument("--review-due", action="store_true",
                        help="Show ISAs past review cadence")
    parser.add_argument("--list", action="store_true",
                        help="List all ISAs")
    parser.add_argument("--status", choices=ISA_STATUSES,
                        help="Filter by status")

    # Update
    parser.add_argument("--renew", action="store_true",
                        help="Renew an ISA")
    parser.add_argument("--revoke", action="store_true",
                        help="Revoke an ISA")
    parser.add_argument("--isa-id", help="ISA ID for renew/revoke")
    parser.add_argument("--new-expiry", help="New expiry date (YYYY-MM-DD)")
    parser.add_argument("--reason", help="Reason for revocation")
    parser.add_argument("--notes", help="Optional notes for renewal")

    args = parser.parse_args()

    try:
        result = None

        if args.create:
            if not args.project_id:
                parser.error("--create requires --project-id")
            if not args.target_system:
                parser.error("--create requires --target-system")
            if not args.auth_date or not args.expiry_date:
                parser.error("--create requires --auth-date and --expiry-date")
            data_types = []
            if args.data_types:
                try:
                    data_types = json.loads(args.data_types)
                except json.JSONDecodeError:
                    data_types = [s.strip() for s in args.data_types.split(",")]
            result = create_isa(
                args.project_id, args.target_system, data_types,
                args.auth_date, args.expiry_date,
                agreement_type=args.agreement_type,
                partner_org=args.partner_org,
                review_cadence_days=args.cadence)

        elif args.expiring:
            if not args.project_id:
                parser.error("--expiring requires --project-id")
            result = get_expiring(args.project_id, args.days)

        elif args.review_due:
            if not args.project_id:
                parser.error("--review-due requires --project-id")
            result = get_review_due(args.project_id)

        elif args.list:
            if not args.project_id:
                parser.error("--list requires --project-id")
            result = list_isas(args.project_id, args.status)

        elif args.renew:
            if not args.isa_id or not args.new_expiry:
                parser.error("--renew requires --isa-id and --new-expiry")
            result = renew_isa(args.isa_id, args.new_expiry, args.notes)

        elif args.revoke:
            if not args.isa_id or not args.reason:
                parser.error("--revoke requires --isa-id and --reason")
            result = revoke_isa(args.isa_id, args.reason)

        else:
            parser.print_help()
            sys.exit(0)

        if result is not None:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(data):
    """Pretty-print result dict for human consumption."""
    if "isa_id" in data and "partner_system" in data and "agreements" not in data:
        print(f"ISA {data.get('agreement_type', 'ISA').upper()}: "
              f"{data.get('partner_system', 'N/A')}")
        print(f"  ID: {data['isa_id']}")
        print(f"  Status: {data.get('status', 'N/A')}")
        print(f"  Expiry: {data.get('expiry_date', 'N/A')}")
        if "reason" in data:
            print(f"  Reason: {data['reason']}")
        if "renewal_notes" in data:
            print(f"  Notes: {data['renewal_notes']}")
    elif "expiring" in data:
        print(f"Expiring ISAs for {data['project_id']} "
              f"(within {data['days_ahead']} days): {data['expiring_count']}")
        for isa in data["expiring"]:
            urgency = isa.get("renewal_urgency", "?")
            days = isa.get("days_until_expiry", "?")
            print(f"  [{urgency.upper()}] {isa.get('partner_system', 'N/A')} "
                  f"- expires in {days} days ({isa.get('expiry_date', '?')})")
    elif "review_due" in data:
        print(f"ISAs past review for {data['project_id']}: "
              f"{data['review_due_count']}")
        for isa in data["review_due"]:
            overdue = isa.get("days_overdue", 0)
            print(f"  {isa.get('partner_system', 'N/A')} "
                  f"- {overdue} days overdue (due: {isa.get('next_review_date', '?')})")
    elif "agreements" in data:
        filt = f" (status={data['filter_status']})" if data.get("filter_status") else ""
        print(f"ISAs for {data['project_id']}{filt}: {data['total']}")
        for isa in data["agreements"]:
            print(f"  [{isa.get('status', '?').upper()}] "
                  f"{isa.get('agreement_type', 'ISA').upper()} "
                  f"<-> {isa.get('partner_system', 'N/A')} "
                  f"(expires {isa.get('expiry_date', '?')})")
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
