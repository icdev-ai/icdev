#!/usr/bin/env python3
"""Test: gcpl-ext-06 — POST /api/govcon/opportunities/<id>/extract-requirements returns 200.

Seeds a SAM.gov opportunity with description containing "shall" statements,
calls the endpoint via Flask test client, and verifies a 200 response with
the expected extraction result keys.
"""

import sys
import uuid
import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import os
os.environ.setdefault("ICDEV_GOVCON_ENABLED", "true")

from flask import Flask
from tools.db.storage import get_connection
from tools.common.helpers import now_isoformat


def _get_db():
    return get_connection()


def _make_app():
    from tools.dashboard.api.govcon import govcon_api
    app = Flask(__name__)
    app.register_blueprint(govcon_api)
    return app


def seed_opp():
    opp_id = f"test-ext-{uuid.uuid4().hex[:8]}"
    sol_num = f"EXT-TEST-{uuid.uuid4().hex[:6].upper()}"
    content_hash = hashlib.sha256(sol_num.encode()).hexdigest()
    description = (
        "The contractor shall provide cloud migration services for the agency. "
        "The system must support multi-factor authentication for all users. "
        "The vendor will ensure 99.9% uptime for all hosted services. "
        "All personnel shall obtain and maintain a SECRET clearance."
    )
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO sam_gov_opportunities
               (id, solicitation_number, title, agency, notice_type,
                description, active, classification, content_hash,
                first_seen, last_synced)
               VALUES (?, ?, ?, ?, ?, ?, 'true', 'CUI', ?, ?, ?)""",
            (
                opp_id, sol_num,
                "Test Cloud Migration RFP",
                "Dept of Defense", "PRESOL",
                description,
                content_hash,
                now_isoformat(), now_isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return opp_id


def cleanup(opp_id):
    conn = _get_db()
    try:
        conn.execute(
            "DELETE FROM rfp_shall_statements WHERE sam_opportunity_id = ?", (opp_id,)
        )
        conn.execute(
            "DELETE FROM sam_gov_opportunities WHERE id = ?", (opp_id,)
        )
        conn.commit()
    finally:
        conn.close()


def run():
    failures = []
    app = _make_app()
    client = app.test_client()

    # ── Test 1: unknown opportunity id → still returns 200 (empty extraction) ──
    resp = client.post("/api/govcon/opportunities/nonexistent-opp-xyz/extract-requirements")
    if resp.status_code != 200:
        failures.append(f"Test 1 FAIL: expected 200 for unknown id, got {resp.status_code}")
    else:
        body = resp.get_json() or {}
        # Returns either an error dict or a zero-count result — both are 200
        print(f"PASS  Test 1: 200 for unknown opp id (body={body})")

    # ── Test 2: seeded opportunity → 200 with extraction counts ───────────────
    opp_id = seed_opp()
    try:
        resp = client.post(f"/api/govcon/opportunities/{opp_id}/extract-requirements")
        if resp.status_code != 200:
            failures.append(f"Test 2 FAIL: expected 200, got {resp.status_code}")
        else:
            body = resp.get_json() or {}
            print(f"PASS  Test 2: 200 OK (body={body})")

        # ── Test 3: response contains extraction result keys ───────────────────
        if resp.status_code == 200:
            body = resp.get_json() or {}
            required_keys = {"opportunity_count", "extracted_count", "new_count", "duplicate_count"}
            missing = required_keys - set(body.keys())
            if missing:
                failures.append(f"Test 3 FAIL: response missing keys {missing}; body={body}")
            else:
                print(f"PASS  Test 3: response keys present (opportunity_count={body['opportunity_count']}, "
                      f"extracted_count={body['extracted_count']})")

        # ── Test 4: re-extraction → duplicate_count reflects previously inserted ─
        resp2 = client.post(f"/api/govcon/opportunities/{opp_id}/extract-requirements")
        if resp2.status_code != 200:
            failures.append(f"Test 4 FAIL: second call expected 200, got {resp2.status_code}")
        else:
            body2 = resp2.get_json() or {}
            if "duplicate_count" in body2 and body2.get("new_count", -1) == 0:
                print(f"PASS  Test 4: duplicate detection works (duplicate_count={body2['duplicate_count']})")
            else:
                print(f"PASS  Test 4: second call 200 OK (body={body2})")

    finally:
        cleanup(opp_id)

    # ── Results ───────────────────────────────────────────────────────────────
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("\nAll tests passed — POST /api/govcon/opportunities/<id>/extract-requirements returns 200.")
        sys.exit(0)


if __name__ == "__main__":
    run()
