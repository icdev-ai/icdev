#!/usr/bin/env python3
# CUI // SP-PROPIN
"""LinkedIn CSV import for employees and contacts.

Supports two LinkedIn self-export CSVs (Settings > Get a copy of your data):
  - Profile.csv (own profile data) -> import as employee
  - Connections.csv                -> import as contacts or employees

Usage:
    python tools/erp/linkedin_importer.py --preview --file Connections.csv [--json]
    python tools/erp/linkedin_importer.py --import --file Connections.csv --target contact [--json]
    python tools/erp/linkedin_importer.py --import --file Profile.csv --target employee [--json]
    python tools/erp/linkedin_importer.py --history [--json]
"""

import argparse
import csv
import io
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

# LinkedIn CSV field mappings
CONNECTIONS_MAP = {
    "First Name": "first_name",
    "Last Name": "last_name",
    "Email Address": "email",
    "Company": "company",
    "Position": "title",
    "Connected On": "connected_on",
    "URL": "linkedin_url",
}

PROFILE_MAP = {
    "First Name": "first_name",
    "Last Name": "last_name",
    "Maiden Name": "_skip",
    "Address": "_skip",
    "Birth Date": "_skip",
    "Headline": "title",
    "Summary": "notes",
    "Industry": "department",
    "Zip Code": "_skip",
    "Geo Location": "location",
    "Twitter Handles": "_skip",
    "Websites": "linkedin_url",
    "Instant Messengers": "_skip",
}


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id():
    return str(uuid.uuid4())


def _detect_type(file_path: str) -> str:
    """Detect if CSV is Profile or Connections export."""
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return "unknown"
    if "Connected On" in header:
        return "connections"
    if "Headline" in header or "Summary" in header:
        return "profile"
    return "unknown"


def _parse_csv(file_path: str) -> list:
    """Parse LinkedIn CSV, handling BOM and encoding issues."""
    rows = []
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        # LinkedIn sometimes adds 3 preamble lines before the actual header
        raw = f.read()

    # Find the actual header row (skip any preamble)
    lines = raw.splitlines()
    header_idx = 0
    for i, line in enumerate(lines):
        if "First Name" in line or "Email Address" in line or "Company" in line:
            header_idx = i
            break

    csv_content = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_content))
    for row in reader:
        rows.append(dict(row))
    return rows


def preview(file_path: str) -> dict:
    """Preview CSV contents without importing."""
    csv_type = _detect_type(file_path)
    rows = _parse_csv(file_path)
    sample = rows[:5] if len(rows) > 5 else rows
    return {
        "success": True,
        "detected_type": csv_type,
        "total_records": len(rows),
        "sample": sample,
        "field_mapping": CONNECTIONS_MAP if csv_type == "connections" else PROFILE_MAP,
    }


def import_connections(file_path: str, target_type: str = "contact",
                       imported_by: str = "system") -> dict:
    rows = _parse_csv(file_path)
    conn = _db()
    imported = 0
    skipped = 0
    errors = []
    now = _now()

    try:
        # Determine default relationship type id for prospects
        rt_row = conn.execute(
            "SELECT id FROM relationship_types WHERE type_name = 'prospect'"
        ).fetchone()
        rt_id = rt_row["id"] if rt_row else None

        for row in rows:
            first = row.get("First Name", "").strip()
            last = row.get("Last Name", "").strip()
            full_name = f"{first} {last}".strip()
            if not full_name:
                skipped += 1
                continue

            email = row.get("Email Address", "").strip() or None
            company = row.get("Company", "").strip() or None
            title = row.get("Position", "").strip() or None
            linkedin_url = row.get("URL", "").strip() or None

            try:
                if target_type == "contact":
                    # Check for duplicate by email or name+company
                    if email:
                        dup = conn.execute(
                            "SELECT id FROM contacts WHERE email = ?", (email,)
                        ).fetchone()
                    else:
                        dup = conn.execute(
                            "SELECT id FROM contacts WHERE full_name = ? AND company = ?",
                            (full_name, company)
                        ).fetchone()

                    if dup:
                        skipped += 1
                        continue

                    cid = _new_id()
                    conn.execute("""
                        INSERT INTO contacts (
                            id, full_name, title, email, company,
                            relationship_type_id, linkedin_url,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """, (cid, full_name, title, email, company,
                          rt_id, linkedin_url, now, now))
                else:
                    # Import as employee
                    if email:
                        dup = conn.execute(
                            "SELECT id FROM employees WHERE email = ?", (email,)
                        ).fetchone()
                    else:
                        dup = conn.execute(
                            "SELECT id FROM employees WHERE full_name = ?",
                            (full_name,)
                        ).fetchone()

                    if dup:
                        skipped += 1
                        continue

                    eid = _new_id()
                    conn.execute("""
                        INSERT INTO employees (
                            id, full_name, job_title, email, company_type,
                            linkedin_url, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'partner', ?, ?, ?)
                    """, (eid, full_name, title, email, linkedin_url, now, now))

                imported += 1
            except sqlite3.Error as e:
                errors.append(f"{full_name}: {e}")
                skipped += 1

        conn.commit()

        # Log import
        lid = _new_id()
        conn.execute("""
            INSERT INTO linkedin_imports (
                id, import_type, filename, record_count,
                imported_count, skipped_count, target_type,
                field_mapping, status, imported_by, imported_at
            ) VALUES (?, 'connections', ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
        """, (lid, Path(file_path).name, len(rows), imported, skipped,
              target_type, json.dumps(CONNECTIONS_MAP),
              imported_by, now))
        conn.commit()

        return {
            "success": True,
            "import_id": lid,
            "total_records": len(rows),
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10],
            "target_type": target_type,
        }
    finally:
        conn.close()


def import_history() -> dict:
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT id, import_type, filename, record_count,
                   imported_count, skipped_count, target_type,
                   status, imported_by, imported_at
            FROM linkedin_imports
            ORDER BY imported_at DESC
        """).fetchall()
        return {"success": True, "imports": [dict(r) for r in rows]}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="LinkedIn CSV importer")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--preview", action="store_true")
    grp.add_argument("--import", action="store_true", dest="do_import")
    grp.add_argument("--history", action="store_true")

    parser.add_argument("--file", help="Path to LinkedIn CSV export")
    parser.add_argument("--target", choices=["employee", "contact"],
                        default="contact")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()

    if args.preview:
        if not args.file:
            parser.error("--file required")
        result = preview(args.file)
    elif args.do_import:
        if not args.file:
            parser.error("--file required")
        result = import_connections(args.file, args.target)
    elif args.history:
        result = import_history()
    else:
        result = {"error": "No action"}

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success", True) else 1)


if __name__ == "__main__":
    main()
