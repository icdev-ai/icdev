# CUI // SP-CTI
"""Tests for HITL reviewer assignment + hand-off (prop-rev-09).

Covers:
  GET  /api/proposals/reviews/<rev_id>/assignments
  POST /api/proposals/reviews/<rev_id>/assign
  POST /api/proposals/assignments/<asgn_id>/accept
  POST /api/proposals/assignments/<asgn_id>/reject
  POST /api/proposals/assignments/<asgn_id>/reassign
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, g

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_opportunities (
    id TEXT PRIMARY KEY,
    solicitation_number TEXT NOT NULL DEFAULT 'SOL-TEST',
    title TEXT NOT NULL DEFAULT 'Test Opp',
    agency TEXT NOT NULL DEFAULT 'DOD',
    due_date TEXT NOT NULL DEFAULT '2026-12-31',
    proposal_type TEXT NOT NULL DEFAULT 'FFP',
    status TEXT NOT NULL DEFAULT 'intake',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_reviews (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    review_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_date TEXT,
    started_at TEXT,
    completed_at TEXT,
    lead_reviewer TEXT,
    participants TEXT,
    summary TEXT,
    overall_rating TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_reviewer_assignments (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    assigned_at TEXT DEFAULT (datetime('now')),
    accepted_at TEXT,
    rejected_at TEXT,
    rejection_reason TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_OPP_ID = "opp-asgn-test-001"
_REV_ID = "rev-asgn-test-001"


def _make_client(role, db_path, patcher_holder):
    flask_app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent / "tools" / "dashboard" / "templates"),
    )
    flask_app.config["TESTING"] = True

    def _get_db():
        from tools.db.storage import get_connection
        return get_connection(db_path=str(db_path))

    @flask_app.before_request
    def _inject_test_user():
        g.current_user = {
            "id": "test-user-01",
            "email": "test@icdev.local",
            "role": role,
            "username": "testuser",
        }

    from tools.dashboard.api.proposals import proposals_api
    flask_app.register_blueprint(proposals_api, url_prefix="/api/proposals")

    patcher = patch("tools.dashboard.api.proposals._get_db", _get_db)
    patcher.start()
    patcher_holder.append(patcher)
    return flask_app.test_client()


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test_reviewer_assignment.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, solicitation_number, title, agency, due_date, proposal_type, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (_OPP_ID, "SOL-ASGN-001", "Assignment Test Opp", "DOD", "2026-12-31", "FFP", "intake"),
    )
    conn.execute(
        "INSERT INTO proposal_reviews (id, opportunity_id, review_type, status) VALUES (?,?,?,?)",
        (_REV_ID, _OPP_ID, "pink_team", "scheduled"),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def _patchers():
    holder = []
    yield holder
    for patcher in holder:
        patcher.stop()


@pytest.fixture()
def reviewer_client(db_path, _patchers):
    return _make_client("reviewer", db_path, _patchers)


@pytest.fixture()
def admin_client(db_path, _patchers):
    return _make_client("admin", db_path, _patchers)


@pytest.fixture()
def writer_client(db_path, _patchers):
    return _make_client("writer", db_path, _patchers)


# ---------------------------------------------------------------------------
# assign
# ---------------------------------------------------------------------------

def test_assign_reviewer_returns_201(reviewer_client):
    resp = reviewer_client.post(
        f"/api/proposals/reviews/{_REV_ID}/assign",
        json={"reviewer": "j.smith", "assigned_by": "lead.writer"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["reviewer"] == "j.smith"
    assert body["status"] == "pending"


def test_assign_reviewer_requires_role(writer_client):
    resp = writer_client.post(
        f"/api/proposals/reviews/{_REV_ID}/assign",
        json={"reviewer": "j.smith", "assigned_by": "lead.writer"},
    )
    assert resp.status_code == 403


def test_assign_reviewer_missing_reviewer_400(reviewer_client):
    resp = reviewer_client.post(
        f"/api/proposals/reviews/{_REV_ID}/assign",
        json={"assigned_by": "lead.writer"},
    )
    assert resp.status_code == 400


def test_assign_reviewer_review_not_found_404(reviewer_client):
    resp = reviewer_client.post(
        "/api/proposals/reviews/does-not-exist/assign",
        json={"reviewer": "j.smith", "assigned_by": "lead.writer"},
    )
    assert resp.status_code == 404


def test_list_assignments_after_assign(reviewer_client):
    reviewer_client.post(
        f"/api/proposals/reviews/{_REV_ID}/assign",
        json={"reviewer": "j.smith", "assigned_by": "lead.writer"},
    )
    resp = reviewer_client.get(f"/api/proposals/reviews/{_REV_ID}/assignments")
    assert resp.status_code == 200
    assignments = resp.get_json()["assignments"]
    assert len(assignments) == 1
    assert assignments[0]["reviewer"] == "j.smith"


# ---------------------------------------------------------------------------
# accept / reject
# ---------------------------------------------------------------------------

def _assign(client, reviewer="j.smith"):
    resp = client.post(
        f"/api/proposals/reviews/{_REV_ID}/assign",
        json={"reviewer": reviewer, "assigned_by": "lead.writer"},
    )
    return resp.get_json()["id"]


def test_accept_assignment_transitions_review_to_in_progress(reviewer_client):
    asgn_id = _assign(reviewer_client)
    resp = reviewer_client.post(f"/api/proposals/assignments/{asgn_id}/accept", json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_accept_assignment_twice_returns_409(reviewer_client):
    asgn_id = _assign(reviewer_client)
    reviewer_client.post(f"/api/proposals/assignments/{asgn_id}/accept", json={})
    resp = reviewer_client.post(f"/api/proposals/assignments/{asgn_id}/accept", json={})
    assert resp.status_code == 409


def test_accept_assignment_requires_role(writer_client):
    resp = writer_client.post("/api/proposals/assignments/some-id/accept", json={})
    assert resp.status_code == 403


def test_reject_assignment_requires_reason_field_accepted(reviewer_client):
    asgn_id = _assign(reviewer_client)
    resp = reviewer_client.post(
        f"/api/proposals/assignments/{asgn_id}/reject",
        json={"rejection_reason": "Conflict of interest"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rejected"


def test_reject_assignment_not_found_404(reviewer_client):
    resp = reviewer_client.post(
        "/api/proposals/assignments/does-not-exist/reject",
        json={"rejection_reason": "n/a"},
    )
    assert resp.status_code == 404


def test_reject_completed_assignment_returns_409(reviewer_client):
    asgn_id = _assign(reviewer_client)
    reviewer_client.post(f"/api/proposals/assignments/{asgn_id}/accept", json={})
    reviewer_client.post(
        f"/api/proposals/assignments/{asgn_id}/reject", json={"rejection_reason": "changed mind"}
    )
    resp = reviewer_client.post(
        f"/api/proposals/assignments/{asgn_id}/reject", json={"rejection_reason": "again"}
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# reassign
# ---------------------------------------------------------------------------

def test_reassign_creates_new_pending_assignment(admin_client):
    asgn_id = _assign(admin_client)
    resp = admin_client.post(
        f"/api/proposals/assignments/{asgn_id}/reassign",
        json={"reviewer": "a.jones", "assigned_by": "pm.lead"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["reviewer"] == "a.jones"
    assert body["status"] == "pending"
    assert body["supersedes"] == asgn_id

    listing = admin_client.get(f"/api/proposals/reviews/{_REV_ID}/assignments").get_json()
    statuses = {a["id"]: a["status"] for a in listing["assignments"]}
    assert statuses[asgn_id] == "reassigned"
    assert statuses[body["id"]] == "pending"


def test_reassign_requires_admin_role(reviewer_client):
    asgn_id = _assign(reviewer_client)
    resp = reviewer_client.post(
        f"/api/proposals/assignments/{asgn_id}/reassign",
        json={"reviewer": "a.jones", "assigned_by": "pm.lead"},
    )
    assert resp.status_code == 403


def test_reassign_missing_reviewer_400(admin_client):
    asgn_id = _assign(admin_client)
    resp = admin_client.post(
        f"/api/proposals/assignments/{asgn_id}/reassign",
        json={"assigned_by": "pm.lead"},
    )
    assert resp.status_code == 400
