#!/usr/bin/env python3
# CUI // SP-CTI
"""The migration-canvas audit trail recorded nothing, and closing a session did not close it.

Two defects behind one route, both of which look like success from the outside.

`mc_net_api_update` is the endpoint the wizard's close control calls. It wrote no
audit row while every other mutating route in the blueprint did, and it answered
200 for a session id that does not exist — a silent zero-row UPDATE, which is how
a dead Close button looks like it worked.

Underneath, `_audit` itself has never persisted anything. `mc_audit` was declared
only in the canvas SCHEMA and never applied to PostgreSQL, and the bridge to
`audit_trail` passed a uuid string as an integer sequence-backed `id` under an
`event_type` the CHECK did not admit. Both failures were swallowed by
best-effort `except` blocks, so the canvas has produced zero audit rows.

The wizard control and the status vocabulary are covered by
test_migration_canvas_session_close.py; this file covers what that one cannot
reach.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from flask import Flask

from tools.migration_canvas import blueprint as bp_mod
from tools.migration_canvas.constants import NET_SESSION_TERMINAL_STATUSES
from tools.migration_canvas.db import init_db as init_db_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client on the migration-canvas blueprint over temp SQLite.

    Two database pointers, not one. `_audit` writes to the canvas DB *and*
    bridges to icdev's `audit_trail`, and both writes were failing independently
    in production behind a best-effort `except`. A fixture that only wires up the
    canvas DB would let the bridge keep silently failing and still pass.
    """
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "migration_canvas.db"))
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    monkeypatch.setattr(init_db_mod, "DB_PATH", tmp_path / "migration_canvas.db")
    init_db_mod.init_db()

    # audit_trail carries the same generated event_type CHECK production has, so
    # an event_type missing from VALID_EVENT_TYPES fails here rather than in prod.
    from tools.audit.audit_logger import VALID_EVENT_TYPES
    from tools.db.storage import get_connection as _icdev_conn

    allowed = ",".join(f"'{t}'" for t in VALID_EVENT_TYPES)
    with _icdev_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_trail ("  # nosec B608 - types from a code constant
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  project_id TEXT,"
            f" event_type TEXT NOT NULL CHECK(event_type IN ({allowed})),"
            "  actor TEXT NOT NULL,"
            "  action TEXT NOT NULL,"
            "  details TEXT,"
            "  classification TEXT DEFAULT 'CUI',"
            "  created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()

    app = Flask(__name__)
    app.secret_key = "test"  # nosec B105 - test fixture, not a credential
    app.register_blueprint(bp_mod.create_migration_blueprint(), url_prefix="/migration-canvas")
    return app.test_client()


def _bridged_rows(sid):
    from tools.db.storage import get_connection as _icdev_conn

    with _icdev_conn() as conn:
        rows = conn.execute(
            "SELECT action, details FROM audit_trail WHERE event_type='migration_canvas'"
        ).fetchall()
    return [r for r in rows if sid in (r["details"] or "")]


@pytest.fixture
def sid(client):
    _sid = f"nmig-{uuid.uuid4().hex[:12]}"
    with init_db_mod.get_connection() as conn:
        conn.execute(
            "INSERT INTO mc_net_sessions (id, src_model, tgt_model, status) VALUES (%s,%s,%s,%s)",
            (_sid, "MX204", "ASR-9901", "in_progress"),
        )
        conn.commit()
    return _sid


def _status(sid):
    with init_db_mod.get_connection() as conn:
        row = conn.execute("SELECT status FROM mc_net_sessions WHERE id=%s", (sid,)).fetchone()
    return row["status"] if row else None


def _audit_actions(sid):
    with init_db_mod.get_connection() as conn:
        rows = conn.execute("SELECT action FROM mc_audit WHERE design_id=%s", (sid,)).fetchall()
    return [r["action"] for r in rows]


def _patch(client, sid, body):
    return client.patch(f"/migration-canvas/api/network-migration/{sid}", json=body)


class TestClosingASession:
    """The close path itself is covered by test_migration_canvas_session_close.py
    (the wizard control and the status vocabulary). What is asserted here is the
    part that file does not reach: that the reflex agrees on what closed means,
    and that the write leaves a trail."""

    def test_the_reflex_honours_the_same_terminal_set(self):
        """A drifted copy would close the session in the UI and keep raising cards."""
        reflex = __import__(
            "tools.genesis.reflexes.migration_canvas", fromlist=["_TERMINAL_SESSION_STATUSES"]
        )
        assert set(reflex._TERMINAL_SESSION_STATUSES) == set(NET_SESSION_TERMINAL_STATUSES)

    def test_terminal_statuses_are_actually_settable(self, client, sid):
        """Closing must reach the DB, not just validate."""
        for status in NET_SESSION_TERMINAL_STATUSES:
            assert _patch(client, sid, {"status": status}).status_code == 200
            assert _status(sid) == status


class TestStatusValidation:
    def test_rejected_status_does_not_write(self, client, sid):
        """A 400 must leave the row alone, not half-apply the request."""
        _patch(client, sid, {"status": "closed"})
        assert _status(sid) == "in_progress"

    def test_unknown_session_is_404(self, client):
        """Silently updating zero rows made a dead 'Close' button look like it worked."""
        assert _patch(client, "nmig-does-not-exist", {"status": "archived"}).status_code == 404

    def test_unknown_session_writes_no_audit(self, client):
        assert _audit_actions("nmig-does-not-exist") == []


class TestAuditTrail:
    def test_status_change_is_audited(self, client, sid):
        """Every other mutating route in this blueprint audits; this one didn't."""
        _patch(client, sid, {"status": "archived"})
        assert "net_session_status_changed" in _audit_actions(sid)

    def test_field_update_is_audited_distinctly(self, client, sid):
        """A rename and a close must be tellable apart in the audit trail."""
        _patch(client, sid, {"src_site": "Fort Meade"})
        actions = _audit_actions(sid)
        assert "net_session_updated" in actions
        assert "net_session_status_changed" not in actions

    def test_compliance_bridge_row_lands(self, client, sid):
        """The other half of _audit, which had its own independent failure.

        It passed `str(uuid4())` as `audit_trail.id`, an integer backed by a
        sequence, so every bridged write raised and was swallowed — the canvas
        had zero rows in audit_trail in production.
        """
        _patch(client, sid, {"status": "archived"})
        rows = _bridged_rows(sid)
        assert rows, "lifecycle change must reach the compliance audit trail"
        assert rows[0]["action"] == "net_session_status_changed"

    def test_migration_canvas_is_an_admitted_event_type(self):
        """The bridge's event_type was never in the vocabulary its CHECK derives from."""
        from tools.audit.audit_logger import VALID_EVENT_TYPES

        assert "migration_canvas" in VALID_EVENT_TYPES
