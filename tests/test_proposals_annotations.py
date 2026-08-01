# CUI // SP-CTI
"""Tests for proposal inline annotation endpoints.

Covers:
  POST   /api/proposals/sections/<sec_id>/annotations
  GET    /api/proposals/sections/<sec_id>/annotations
  PUT    /api/proposals/annotations/<ann_id>
  DELETE /api/proposals/annotations/<ann_id>
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Minimal schema fixtures
# ---------------------------------------------------------------------------

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
CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    volume_id TEXT,
    section_number TEXT,
    title TEXT NOT NULL DEFAULT 'Test Section',
    status TEXT DEFAULT 'drafting',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_section_annotations (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL,
    draft_id TEXT,
    selected_text TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN (
        'question','improvement','compliance','strength','weakness','risk','editorial'
    )),
    comment TEXT NOT NULL,
    author TEXT DEFAULT 'reviewer',
    status TEXT DEFAULT 'open' CHECK(status IN ('open','resolved')),
    resolution_note TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    session_id TEXT
);
"""

_OPP_ID = "opp-ann-test-001"
_SEC_ID = "sec-ann-test-001"


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test_annotations.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, solicitation_number, title, agency, due_date, proposal_type, status) VALUES (?,?,?,?,?,?,?)",
        (_OPP_ID, "SOL-ANN-001", "Annotation Test Opp", "DOD", "2026-12-31", "FFP", "intake"),
    )
    conn.execute(
        "INSERT INTO proposal_sections (id, opportunity_id, title, status) VALUES (?,?,?,?)",
        (_SEC_ID, _OPP_ID, "5.1.2 Methodology", "drafting"),
    )
    conn.commit()
    conn.close()

    flask_app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "tools" / "dashboard" / "templates"))
    flask_app.config["TESTING"] = True

    def _get_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    with patch("tools.dashboard.api.proposals._get_db", _get_db):
        from tools.dashboard.api.proposals import proposals_api
        @flask_app.before_request
        def _inject_fake_auth_0():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        flask_app.register_blueprint(proposals_api, url_prefix="/api/proposals")
        yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ann_payload(**overrides):
    base = {
        "selected_text": "acquisition planning and coordination",
        "category": "compliance",
        "comment": "This needs to reference FAR 15.305 explicitly.",
        "author": "s.chuon",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_annotation_returns_201(client):
    resp = client.post(
        f"/api/proposals/sections/{_SEC_ID}/annotations",
        json=_ann_payload(),
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert "id" in data
    assert data["category"] == "compliance"
    assert data["status"] == "open"


def test_list_annotations_for_section(client):
    client.post(f"/api/proposals/sections/{_SEC_ID}/annotations", json=_ann_payload(category="strength"))
    client.post(f"/api/proposals/sections/{_SEC_ID}/annotations", json=_ann_payload(category="weakness", comment="Too vague"))

    resp = client.get(f"/api/proposals/sections/{_SEC_ID}/annotations")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "annotations" in data
    assert len(data["annotations"]) == 2


def test_list_annotations_filter_by_status(client):
    r1 = client.post(f"/api/proposals/sections/{_SEC_ID}/annotations", json=_ann_payload(category="risk")).get_json()
    client.post(f"/api/proposals/sections/{_SEC_ID}/annotations", json=_ann_payload(category="question", comment="Who owns this?"))

    # Resolve first annotation
    client.put(f"/api/proposals/annotations/{r1['id']}", json={"status": "resolved"})

    resp = client.get(f"/api/proposals/sections/{_SEC_ID}/annotations?status=open")
    assert resp.status_code == 200
    data = resp.get_json()
    assert all(a["status"] == "open" for a in data["annotations"])
    assert len(data["annotations"]) == 1


def test_create_annotation_stores_author(client):
    resp = client.post(
        f"/api/proposals/sections/{_SEC_ID}/annotations",
        json=_ann_payload(author="j.smith"),
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["author"] == "j.smith"
    assert data["created_at"] is not None


def test_update_annotation_status_to_resolved(client):
    created = client.post(
        f"/api/proposals/sections/{_SEC_ID}/annotations",
        json=_ann_payload(category="editorial"),
    ).get_json()
    ann_id = created["id"]

    resp = client.put(
        f"/api/proposals/annotations/{ann_id}",
        json={"status": "resolved", "resolution_note": "Updated in v2 draft.", "resolved_by": "s.chuon"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "resolved"
    assert data["resolution_note"] == "Updated in v2 draft."
    assert data["resolved_by"] == "s.chuon"
    assert data["resolved_at"] is not None


def test_delete_annotation(client):
    created = client.post(
        f"/api/proposals/sections/{_SEC_ID}/annotations",
        json=_ann_payload(category="improvement"),
    ).get_json()
    ann_id = created["id"]

    del_resp = client.delete(f"/api/proposals/annotations/{ann_id}")
    assert del_resp.status_code == 200

    list_resp = client.get(f"/api/proposals/sections/{_SEC_ID}/annotations")
    ids = [a["id"] for a in list_resp.get_json()["annotations"]]
    assert ann_id not in ids


def test_create_annotation_invalid_category_returns_400(client):
    resp = client.post(
        f"/api/proposals/sections/{_SEC_ID}/annotations",
        json=_ann_payload(category="invalid_cat"),
    )
    assert resp.status_code == 400
    assert "category" in resp.get_json().get("error", "").lower()
