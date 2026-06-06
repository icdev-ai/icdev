#!/usr/bin/env python3
"""Test: gcpl-disc-10 — POST /api/govcon/sam/import/<id> imports opp into proposals.

Calls import_sam_to_proposal() directly inside a minimal Flask app context,
so the fixed code is exercised without requiring a full server restart.
"""

import sys
import uuid
import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import os
os.environ.setdefault("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))
os.environ.setdefault("ICDEV_GOVCON_ENABLED", "true")

from flask import Flask
from tools.db.storage import get_connection
from tools.common.helpers import now_isoformat

DB_PATH = str(BASE_DIR / "data" / "icdev.db")


def _get_db():
    conn = get_connection(db_path=DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _make_app():
    """Minimal Flask app with only the govcon blueprint."""
    from tools.dashboard.api.govcon import govcon_api
    app = Flask(__name__)
    app.register_blueprint(govcon_api)
    return app


def seed_sam_opp():
    sam_id = f"test-sam-{uuid.uuid4().hex[:8]}"
    sol_num = f"TEST-SOL-{uuid.uuid4().hex[:6].upper()}"
    content_hash = hashlib.sha256(sol_num.encode()).hexdigest()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO sam_gov_opportunities
               (id, solicitation_number, title, agency, agency_hierarchy,
                naics_code, notice_type, posted_date, response_deadline,
                active, classification, first_seen, last_synced, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CUI', ?, ?, ?)""",
            (
                sam_id, sol_num,
                "Test AI Platform Modernization Services",
                "Dept of Defense", "DISA",
                "541511", "PRESOL",
                now_isoformat(), "2026-07-01T17:00:00Z",
                now_isoformat(), now_isoformat(), content_hash,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return sam_id, sol_num


def cleanup(sam_id, prop_id=None):
    conn = _get_db()
    try:
        conn.execute("DELETE FROM sam_gov_opportunities WHERE id = ?", (sam_id,))
        if prop_id:
            conn.execute("DELETE FROM proposal_status_history WHERE entity_id = ?", (prop_id,))
            conn.execute("DELETE FROM proposal_opportunities WHERE id = ?", (prop_id,))
        conn.commit()
    finally:
        conn.close()


def run():
    failures = []
    app = _make_app()
    client = app.test_client()

    def post(url):
        resp = client.post(url, json={})
        try:
            data = resp.get_json() or {}
        except Exception:
            data = {}
        return resp.status_code, data

    # ── Test 1: 404 when SAM opp not found ────────────────────────────────────
    status, body = post("/api/govcon/sam/import/nonexistent-sam-id-xyz")
    if status != 404:
        failures.append(f"Test 1 FAIL: expected 404 for unknown id, got {status}: {body}")
    else:
        print("PASS  Test 1: 404 for unknown SAM id")

    # ── Test 2: successful import ──────────────────────────────────────────────
    sam_id, sol_num = seed_sam_opp()
    prop_id = None
    try:
        status, body = post(f"/api/govcon/sam/import/{sam_id}")
        if status not in (200, 201):
            failures.append(f"Test 2 FAIL: expected 200/201, got {status}: {body}")
        elif "proposal_id" not in body:
            failures.append(f"Test 2 FAIL: response missing proposal_id: {body}")
        else:
            prop_id = body["proposal_id"]
            print(f"PASS  Test 2: import OK, proposal_id={prop_id}")

        # ── Test 3: proposal_opportunities row created ─────────────────────────
        if prop_id:
            conn = _get_db()
            try:
                row = dict(conn.execute(
                    "SELECT * FROM proposal_opportunities WHERE id = ?", (prop_id,)
                ).fetchone() or {})
            finally:
                conn.close()

            if not row:
                failures.append("Test 3 FAIL: proposal_opportunities row not found")
            elif row.get("solicitation_number") != sol_num:
                failures.append(f"Test 3 FAIL: solicitation_number mismatch: {row.get('solicitation_number')!r}")
            elif row.get("status") != "intake":
                failures.append(f"Test 3 FAIL: status={row.get('status')!r}, expected 'intake'")
            elif row.get("proposal_type") != "other":
                failures.append(f"Test 3 FAIL: proposal_type={row.get('proposal_type')!r}, expected 'other'")
            else:
                print(f"PASS  Test 3: proposal row (status={row['status']}, type={row['proposal_type']})")

        # ── Test 4: SAM record linked back ────────────────────────────────────
        if prop_id:
            conn = _get_db()
            try:
                sam_row = conn.execute(
                    "SELECT proposal_opportunity_id FROM sam_gov_opportunities WHERE id = ?",
                    (sam_id,)
                ).fetchone()
            finally:
                conn.close()

            if not sam_row or sam_row["proposal_opportunity_id"] != prop_id:
                failures.append(f"Test 4 FAIL: SAM record not linked back; got {dict(sam_row) if sam_row else None}")
            else:
                print("PASS  Test 4: SAM record linked -> proposal_opportunity_id set")

        # ── Test 5: status history entry created ──────────────────────────────
        if prop_id:
            conn = _get_db()
            try:
                hist = conn.execute(
                    "SELECT * FROM proposal_status_history WHERE entity_id = ?", (prop_id,)
                ).fetchone()
            finally:
                conn.close()

            if not hist:
                failures.append("Test 5 FAIL: proposal_status_history entry missing")
            else:
                hist = dict(hist)
                if hist.get("new_status") != "intake":
                    failures.append(f"Test 5 FAIL: new_status={hist.get('new_status')!r}, expected 'intake'")
                else:
                    print("PASS  Test 5: status_history entry (new_status=intake)")

        # ── Test 6: duplicate import returns 409 ─────────────────────────────
        status2, body2 = post(f"/api/govcon/sam/import/{sam_id}")
        if status2 != 409:
            failures.append(f"Test 6 FAIL: expected 409 on duplicate, got {status2}: {body2}")
        else:
            print("PASS  Test 6: duplicate import returns 409 conflict")

    finally:
        cleanup(sam_id, prop_id)

    # ── Results ───────────────────────────────────────────────────────────────
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("\nAll 6 tests passed — POST /api/govcon/sam/import/<id> works correctly.")
        sys.exit(0)


if __name__ == "__main__":
    run()
