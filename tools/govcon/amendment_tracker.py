#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""RFP Amendment Tracker — version tracking, diff engine, Q&A response capture.

Tracks RFP amendments/revisions with auto-diff between versions using
Python stdlib ``difflib`` (D-QTG-3, air-gap safe, zero deps).

Features:
    1. Upload amendment from file (plain text / PDF) or pasted text
    2. Auto-increment version numbers per opportunity
    3. Compute unified diff between consecutive versions
    4. Record government Q&A responses linked to questions
    5. Auto-update question status to 'answered' when response recorded

Usage:
    python tools/govcon/amendment_tracker.py --upload --opp-id <id> --file <path> --title "Amendment 1" --json
    python tools/govcon/amendment_tracker.py --upload-text --opp-id <id> --text "..." --title "Amendment 1" --json
    python tools/govcon/amendment_tracker.py --diff --amendment-id <id> --json
    python tools/govcon/amendment_tracker.py --list --opp-id <id> --json
    python tools/govcon/amendment_tracker.py --record-response --question-id <id> --response "..." --json
"""

import argparse
import difflib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))


# =========================================================================
# HELPERS
# =========================================================================

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="amendment_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "govcon.amendment", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _extract_text_from_file(file_path):
    """Extract text from a file.  Plain text first, pypdf fallback for PDF."""
    fp = Path(file_path)
    if not fp.exists():
        return None

    # PDF handling
    if fp.suffix.lower() == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(fp))
            pages = [p.extract_text() or "" for p in reader.pages]
            return "\n".join(pages).strip() or None
        except ImportError:
            return None
        except Exception:
            return None

    # Plain text
    try:
        return fp.read_text(encoding="utf-8", errors="replace").strip() or None
    except Exception:
        return None


# =========================================================================
# CORE FUNCTIONS
# =========================================================================

def upload_amendment(opp_id, title, file_path=None, text=None,
                     description=None, amendment_date=None, uploaded_by=None):
    """Upload a new RFP amendment (file or text).

    Auto-increments version_number, auto-computes diff against previous version.

    Returns:
        dict with amendment record and diff summary.
    """
    if not file_path and not text:
        return {"status": "error", "message": "Provide --file or --text"}

    # Extract text from file if provided
    amendment_text = text
    source_type = "text"
    if file_path:
        amendment_text = _extract_text_from_file(file_path)
        if not amendment_text:
            return {"status": "error", "message": f"Could not extract text from {file_path}"}
        source_type = "file"

    conn = _get_db()
    try:
        # Verify opportunity exists
        opp = conn.execute(
            "SELECT id, title FROM proposal_opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        if not opp:
            return {"status": "error", "message": f"Opportunity {opp_id} not found"}

        # Get next version number
        row = conn.execute(
            "SELECT MAX(version_number) as max_ver FROM proposal_amendments WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchone()
        next_version = (row["max_ver"] or 0) + 1

        # Auto-diff against previous version
        diff_data = {}
        diff_summary = ""
        changes_detected = 0
        if next_version > 1:
            prev = conn.execute(
                "SELECT amendment_text FROM proposal_amendments "
                "WHERE opportunity_id = ? AND version_number = ? ",
                (opp_id, next_version - 1),
            ).fetchone()
            if prev and prev["amendment_text"]:
                diff_result = compute_diff_text(prev["amendment_text"], amendment_text)
                diff_data = diff_result.get("diff_data", {})
                diff_summary = diff_result.get("summary", "")
                changes_detected = diff_result.get("changes_detected", 0)

        amendment_id = _uuid()
        now = _now()
        conn.execute(
            "INSERT INTO proposal_amendments "
            "(id, opportunity_id, version_number, title, description, amendment_date, "
            " source_type, file_path, amendment_text, diff_summary, diff_data, "
            " changes_detected, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                amendment_id, opp_id, next_version, title,
                description or "", amendment_date or now[:10],
                source_type, str(file_path) if file_path else None,
                amendment_text, diff_summary, json.dumps(diff_data),
                changes_detected, uploaded_by or "system", now,
            ),
        )

        # Update amendment_count on opportunity
        conn.execute(
            "UPDATE proposal_opportunities SET amendment_count = ?, updated_at = ? WHERE id = ?",
            (next_version, now, opp_id),
        )

        # Status history (id is AUTOINCREMENT, created_at has default)
        conn.execute(
            "INSERT INTO proposal_status_history "
            "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("amendment", amendment_id, "", "uploaded",
             uploaded_by or "system",
             f"Amendment v{next_version}: {title}"),
        )

        _audit(conn, f"amendment_uploaded v{next_version}",
               f"opp={opp_id}, title={title}, changes={changes_detected}")
        conn.commit()

        return {
            "status": "ok",
            "amendment_id": amendment_id,
            "version_number": next_version,
            "title": title,
            "source_type": source_type,
            "diff_summary": diff_summary,
            "changes_detected": changes_detected,
        }
    finally:
        conn.close()


def compute_diff_text(old_text, new_text):
    """Compute unified diff between two text strings.

    Returns structured diff data with summary statistics.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = list(difflib.unified_diff(old_lines, new_lines,
                                     fromfile="Previous Version",
                                     tofile="Current Version",
                                     lineterm=""))

    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    changes_detected = additions + deletions

    # Build structured hunks
    hunks = []
    current_hunk = None
    for line in diff:
        if line.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = {"header": line.strip(), "lines": []}
        elif current_hunk is not None:
            current_hunk["lines"].append(line.rstrip("\n"))

    if current_hunk:
        hunks.append(current_hunk)

    summary_parts = []
    if additions:
        summary_parts.append(f"+{additions} lines added")
    if deletions:
        summary_parts.append(f"-{deletions} lines removed")
    summary = ", ".join(summary_parts) if summary_parts else "No changes detected"

    return {
        "diff_data": {
            "hunks": hunks,
            "additions": additions,
            "deletions": deletions,
            "unified_diff": "\n".join(diff),
        },
        "summary": summary,
        "changes_detected": changes_detected,
    }


def compute_diff(amendment_id):
    """Compute or retrieve diff for a stored amendment.

    If diff_data is already populated, returns it.  Otherwise computes
    against the previous version and stores.
    """
    conn = _get_db()
    try:
        amend = conn.execute(
            "SELECT * FROM proposal_amendments WHERE id = ?", (amendment_id,)
        ).fetchone()
        if not amend:
            return {"status": "error", "message": f"Amendment {amendment_id} not found"}

        # Return existing diff if already computed
        existing = json.loads(amend["diff_data"] or "{}")
        if existing.get("hunks") is not None:
            return {
                "status": "ok",
                "amendment_id": amendment_id,
                "version_number": amend["version_number"],
                "diff_data": existing,
                "diff_summary": amend["diff_summary"],
                "changes_detected": amend["changes_detected"],
            }

        # Need previous version to diff
        if amend["version_number"] <= 1:
            return {
                "status": "ok",
                "amendment_id": amendment_id,
                "version_number": 1,
                "diff_data": {"hunks": [], "additions": 0, "deletions": 0, "unified_diff": ""},
                "diff_summary": "Initial version — no previous version to compare",
                "changes_detected": 0,
            }

        prev = conn.execute(
            "SELECT amendment_text FROM proposal_amendments "
            "WHERE opportunity_id = ? AND version_number = ?",
            (amend["opportunity_id"], amend["version_number"] - 1),
        ).fetchone()

        if not prev or not prev["amendment_text"]:
            return {"status": "error", "message": "Previous version text not available"}

        result = compute_diff_text(prev["amendment_text"], amend["amendment_text"] or "")

        # Store computed diff
        conn.execute(
            "UPDATE proposal_amendments SET diff_data = ?, diff_summary = ?, changes_detected = ? WHERE id = ?",
            (json.dumps(result["diff_data"]), result["summary"], result["changes_detected"], amendment_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "amendment_id": amendment_id,
            "version_number": amend["version_number"],
            "diff_data": result["diff_data"],
            "diff_summary": result["summary"],
            "changes_detected": result["changes_detected"],
        }
    finally:
        conn.close()


def list_amendments(opp_id):
    """List all amendments for an opportunity, ordered by version."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, version_number, title, description, amendment_date, "
            "source_type, diff_summary, changes_detected, uploaded_by, created_at "
            "FROM proposal_amendments WHERE opportunity_id = ? ORDER BY version_number ASC",
            (opp_id,),
        ).fetchall()

        return {
            "status": "ok",
            "opportunity_id": opp_id,
            "count": len(rows),
            "amendments": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def record_response(question_id, response_text, amendment_id=None,
                    response_date=None, impacts_requirements=False,
                    impact_notes=None, recorded_by=None):
    """Record a government Q&A response linked to a question.

    Auto-updates the question status to 'answered'.

    Args:
        question_id: ID of the question being answered
        response_text: The government's response text
        amendment_id: Optional link to the amendment containing this response
        response_date: Date of response (defaults to today)
        impacts_requirements: Whether the response changes requirements
        impact_notes: Notes about requirement impact
        recorded_by: User recording the response

    Returns:
        dict with response record.
    """
    conn = _get_db()
    try:
        # Verify question exists and get opportunity_id
        q = conn.execute(
            "SELECT id, opportunity_id, status FROM proposal_questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        if not q:
            return {"status": "error", "message": f"Question {question_id} not found"}

        now = _now()
        response_id = _uuid()

        # Insert response (append-only table)
        conn.execute(
            "INSERT INTO proposal_question_responses "
            "(id, question_id, opportunity_id, amendment_id, response_text, "
            " response_date, impacts_requirements, impact_notes, recorded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                response_id, question_id, q["opportunity_id"],
                amendment_id, response_text,
                response_date or now[:10],
                1 if impacts_requirements else 0,
                impact_notes or "", recorded_by or "system", now,
            ),
        )

        # Update question status to 'answered'
        old_status = q["status"]
        conn.execute(
            "UPDATE proposal_questions SET status = 'answered', updated_at = ? WHERE id = ?",
            (now, question_id),
        )

        # Status history (id is AUTOINCREMENT, created_at has default)
        conn.execute(
            "INSERT INTO proposal_status_history "
            "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("question", question_id, old_status, "answered",
             recorded_by or "system",
             f"Government response recorded (impacts_requirements={impacts_requirements})"),
        )

        _audit(conn, "response_recorded",
               f"question={question_id}, impacts={impacts_requirements}")
        conn.commit()

        return {
            "status": "ok",
            "response_id": response_id,
            "question_id": question_id,
            "opportunity_id": q["opportunity_id"],
            "impacts_requirements": impacts_requirements,
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================

def _build_parser():
    p = argparse.ArgumentParser(description="RFP Amendment Tracker (D-QTG-3)")
    p.add_argument("--opp-id", help="Opportunity ID")
    p.add_argument("--amendment-id", help="Amendment ID (for --diff)")
    p.add_argument("--question-id", help="Question ID (for --record-response)")

    # Actions
    p.add_argument("--upload", action="store_true", help="Upload amendment from file")
    p.add_argument("--upload-text", action="store_true", help="Upload amendment from text")
    p.add_argument("--diff", action="store_true", help="Compute/retrieve diff for amendment")
    p.add_argument("--list", action="store_true", help="List amendments for opportunity")
    p.add_argument("--record-response", action="store_true", help="Record government response")

    # Upload params
    p.add_argument("--file", help="Path to amendment file (text or PDF)")
    p.add_argument("--text", help="Amendment text content")
    p.add_argument("--title", help="Amendment title", default="Untitled Amendment")
    p.add_argument("--description", help="Amendment description")
    p.add_argument("--amendment-date", help="Amendment date (YYYY-MM-DD)")
    p.add_argument("--uploaded-by", help="Uploader identity")

    # Response params
    p.add_argument("--response", help="Government response text")
    p.add_argument("--response-date", help="Response date (YYYY-MM-DD)")
    p.add_argument("--impacts", action="store_true", help="Response impacts requirements")
    p.add_argument("--impact-notes", help="Impact description")
    p.add_argument("--recorded-by", help="Recorder identity")

    # Output
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--human", action="store_true", help="Human-readable output")
    return p


def main():
    args = _build_parser().parse_args()

    result = None

    if args.upload:
        if not args.opp_id or not args.file:
            result = {"status": "error", "message": "--upload requires --opp-id and --file"}
        else:
            result = upload_amendment(
                opp_id=args.opp_id, title=args.title,
                file_path=args.file, description=args.description,
                amendment_date=args.amendment_date, uploaded_by=args.uploaded_by,
            )

    elif args.upload_text:
        if not args.opp_id or not args.text:
            result = {"status": "error", "message": "--upload-text requires --opp-id and --text"}
        else:
            result = upload_amendment(
                opp_id=args.opp_id, title=args.title,
                text=args.text, description=args.description,
                amendment_date=args.amendment_date, uploaded_by=args.uploaded_by,
            )

    elif args.diff:
        if not args.amendment_id:
            result = {"status": "error", "message": "--diff requires --amendment-id"}
        else:
            result = compute_diff(args.amendment_id)

    elif args.list:
        if not args.opp_id:
            result = {"status": "error", "message": "--list requires --opp-id"}
        else:
            result = list_amendments(args.opp_id)

    elif args.record_response:
        if not args.question_id or not args.response:
            result = {"status": "error", "message": "--record-response requires --question-id and --response"}
        else:
            result = record_response(
                question_id=args.question_id, response_text=args.response,
                amendment_id=args.amendment_id, response_date=args.response_date,
                impacts_requirements=args.impacts, impact_notes=args.impact_notes,
                recorded_by=args.recorded_by,
            )

    else:
        result = {"status": "error", "message": "Specify --upload, --upload-text, --diff, --list, or --record-response"}

    if args.human and result:
        _print_human(result)
    else:
        print(json.dumps(result, indent=2, default=str))


def _print_human(result):
    """Human-readable output."""
    status = result.get("status", "unknown")
    print(f"\n{'='*60}")
    print(f"  Amendment Tracker — {status.upper()}")
    print(f"{'='*60}")

    if status == "error":
        print(f"  ERROR: {result.get('message', '')}")
        return

    # List output
    if "amendments" in result:
        print(f"  Opportunity: {result.get('opportunity_id', '')}")
        print(f"  Total amendments: {result.get('count', 0)}")
        for a in result["amendments"]:
            print(f"\n  v{a['version_number']}: {a['title']}")
            print(f"    Date: {a.get('amendment_date', 'N/A')}")
            print(f"    Source: {a.get('source_type', 'N/A')}")
            print(f"    Changes: {a.get('changes_detected', 0)}")
            if a.get("diff_summary"):
                print(f"    Diff: {a['diff_summary']}")
        return

    # Single amendment or response
    for key in ("amendment_id", "response_id", "version_number",
                "diff_summary", "changes_detected", "impacts_requirements"):
        if key in result:
            print(f"  {key}: {result[key]}")

    print()


if __name__ == "__main__":
    main()
