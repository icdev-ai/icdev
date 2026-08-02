# CUI // SP-CTI
"""Tests for the cross-agency transfer audit API blueprint.

Verifies that:
- POST /transfers writes an audit record and returns event IDs
- POST /transfers with missing fields returns 400 or 422
- GET /transfers/<id> returns events for a known transfer
- GET /transfers/<id> for an unknown transfer returns an empty list
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Pre-import the logger module so it is registered in sys.modules under the
# 'tools.audit.cross_agency_transfer_logger' key before any Flask request is
# made.  Without this, the lazy import inside the route handler can create a
# second module object that is not covered by the patch, causing the mock DB
# connection to be ignored and _table_exists() to see an empty database.

# ---------------------------------------------------------------------------
# Minimal DB schema shared by logger + API tests
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cross_agency_transfers (
    id                  TEXT PRIMARY KEY,
    transfer_id         TEXT NOT NULL,
    event_type          TEXT NOT NULL CHECK(event_type IN (
                            'initiated', 'completed', 'failed', 'rejected')),
    source_agency       TEXT NOT NULL,
    target_agency       TEXT NOT NULL,
    data_type           TEXT,
    data_classification TEXT NOT NULL DEFAULT 'CUI',
    actor               TEXT NOT NULL DEFAULT '',
    project_id          TEXT,
    bytes_transferred   INTEGER,
    checksum            TEXT,
    duration_ms         INTEGER,
    rejection_reason    TEXT,
    error_code          TEXT,
    details             TEXT,
    occurred_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id            TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    project_id    TEXT,
    details       TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id    TEXT,
    source_ip     TEXT,
    recorded_at   TEXT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_LOGGER_MODULE = "tools.audit.cross_agency_transfer_logger"


@pytest.fixture()
def db(tmp_path):
    """Schema-loaded connection, shared with the code under test.

    This connection is handed straight to production code via the
    ``get_connection`` patches below, and that code authors ``%s`` placeholders
    for PostgreSQL. A bare ``sqlite3.connect`` raises ``near "%": syntax
    error`` on every such statement. ``unclosable`` keeps the shared handle
    alive across the ``with get_connection() as conn:`` blocks the request path
    uses, which would otherwise close it after the first write.
    """
    from _sql_compat import translating

    path = tmp_path / "test_api.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return translating(conn, unclosable=True), str(path)


@pytest.fixture()
def flask_app(db):
    """Create a minimal Flask app with the blueprint mounted."""
    conn, db_path = db
    from flask import Flask
    from tools.dashboard.api.cross_agency_transfer import cross_agency_transfer_api

    app = Flask(__name__)
    app.register_blueprint(cross_agency_transfer_api, url_prefix="/api/v1/cross-agency-transfer")
    app.config["TESTING"] = True
    return app, conn


def _mock_get_connection(conn):
    """Return a context manager that routes all get_connection() calls to *conn*.

    The Flask request path goes through multiple namespaces:
    - tools.audit.cross_agency_transfer_logger (canonical logger)
    - icdev.tools.audit.cross_agency_transfer_logger (icdev-namespace logger used by hook_transfer)
    - tools.audit.audit_logger (called by icdev logger's _mirror_to_audit_trail)

    All three are patched so that whichever is active at call-time receives the
    same test-scoped mock DB connection.
    """
    # Eagerly load both logger namespaces into sys.modules before patching.
    import tools.audit.cross_agency_transfer_logger  # noqa: F401
    import icdev.tools.audit.cross_agency_transfer_logger  # noqa: F401
    import tools.audit.audit_logger  # noqa: F401

    from contextlib import ExitStack, contextmanager

    @contextmanager
    def _all():
        with ExitStack() as stack:
            stack.enter_context(
                patch(f"{_LOGGER_MODULE}.get_connection", return_value=conn)
            )
            stack.enter_context(
                patch(f"icdev.{_LOGGER_MODULE}.get_connection", return_value=conn)
            )
            # audit_logger.get_connection is called by icdev logger's _mirror_to_audit_trail
            # when it delegates to tools.audit.audit_logger.log_event
            stack.enter_context(
                patch("tools.audit.audit_logger.get_connection", return_value=conn)
            )
            yield

    return _all()


def _valid_request():
    return {
        "transfer_id": f"TXF-{uuid.uuid4().hex[:8]}",
        "source_agency": "DoD",
        "target_agency": "DHS",
        "data_type": "threat_intel",
        "actor": "agent-007",
        "data_classification": "CUI",
        # ABAC requires subject with entitlement + clearance >= CUI (1)
        "subject": {
            "user_id": "agent-007",
            "clearance_level": 1,
            "entitlements": ["cross_domain_pull"],
            "compartments": [],
        },
    }


# ---------------------------------------------------------------------------
# POST /transfers
# ---------------------------------------------------------------------------

class TestSubmitTransfer:
    def test_valid_request_returns_200(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        req = _valid_request()
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                resp = client.post(
                    "/api/v1/cross-agency-transfer/transfers",
                    data=json.dumps(req),
                    content_type="application/json",
                )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["transfer_id"] == req["transfer_id"]
        assert body["event_id"]  # non-empty UUID

    def test_audit_record_written_to_db(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        req = _valid_request()
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                client.post(
                    "/api/v1/cross-agency-transfer/transfers",
                    data=json.dumps(req),
                    content_type="application/json",
                )
        rows = db_conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=?",
            (req["transfer_id"],),
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["source_agency"] == "DoD"
        assert rows[0]["target_agency"] == "DHS"
        assert rows[0]["actor"] == "agent-007"
        assert rows[0]["data_classification"] == "CUI"

    def test_audit_record_is_immutable_initiated_type(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        req = _valid_request()
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                client.post(
                    "/api/v1/cross-agency-transfer/transfers",
                    data=json.dumps(req),
                    content_type="application/json",
                )
        row = db_conn.execute(
            "SELECT event_type FROM cross_agency_transfers WHERE transfer_id=?",
            (req["transfer_id"],),
        ).fetchone()
        assert row["event_type"] == "initiated"

    def test_empty_body_returns_400(self, flask_app, db):
        app, _ = flask_app
        with app.test_client() as client:
            resp = client.post(
                "/api/v1/cross-agency-transfer/transfers",
                data="",
                content_type="application/json",
            )
        assert resp.status_code == 400

    def test_dual_write_to_audit_trail(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        req = _valid_request()
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                client.post(
                    "/api/v1/cross-agency-transfer/transfers",
                    data=json.dumps(req),
                    content_type="application/json",
                )
        rows = db_conn.execute(
            "SELECT * FROM audit_trail WHERE event_type LIKE 'cross_agency_transfer_%'",
        ).fetchall()
        assert len(rows) >= 1
        assert "DoD" in rows[0]["action"] or "DHS" in rows[0]["action"]


# ---------------------------------------------------------------------------
# GET /transfers/<transfer_id>
# ---------------------------------------------------------------------------

class TestGetTransferEvents:
    def test_known_transfer_returns_events(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        req = _valid_request()
        # Write a record first
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                client.post(
                    "/api/v1/cross-agency-transfer/transfers",
                    data=json.dumps(req),
                    content_type="application/json",
                )
                resp = client.get(
                    f"/api/v1/cross-agency-transfer/transfers/{req['transfer_id']}",
                )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["transfer_id"] == req["transfer_id"]
        assert len(body["events"]) >= 1

    def test_unknown_transfer_returns_empty_list(self, flask_app, db):
        app, conn = flask_app
        db_conn, _ = db
        with _mock_get_connection(db_conn):
            with app.test_client() as client:
                resp = client.get(
                    "/api/v1/cross-agency-transfer/transfers/UNKNOWN-999",
                )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["events"] == []
