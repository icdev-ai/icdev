#!/usr/bin/env python3
# CUI // SP-CTI
"""A network migration session had no way to ever end.

The PATCH endpoint allowlisted `status` and worked, but nothing in the wizard
ever called it with one, so every session created stayed `in_progress` forever.
After 7 days the migration_canvas Genesis reflex flagged it as stale and raised
a kanban card — and kept raising one, because the session could not be closed.
249 `[NMCE]` cards accumulated from 48 sessions that way.

These cover the close path end to end: the wizard control that calls it, the
status vocabulary it is allowed to set, and the audit row a lifecycle change
must leave behind (every other mutating route in the blueprint audits; this one
silently did not).
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from flask import Flask

from tools.migration_canvas import blueprint as bp_mod
from tools.migration_canvas.db import init_db as init_db_mod

_WIZARD = (
    Path(__file__).resolve().parent.parent
    / "tools" / "dashboard" / "templates" / "migration_canvas" / "network_wizard.html"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client on the migration-canvas blueprint over temp SQLite."""
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "migration_canvas.db"))
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    monkeypatch.setattr(init_db_mod, "DB_PATH", tmp_path / "migration_canvas.db")
    init_db_mod.init_db()

    app = Flask(__name__)
    app.secret_key = "test"  # nosec B105 - test fixture, not a credential
    app.register_blueprint(bp_mod.create_migration_blueprint(), url_prefix="/migration-canvas")
    return app.test_client()


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
    def test_archiving_persists(self, client, sid):
        """The whole point: a session can reach a state the reflex ignores."""
        assert _patch(client, sid, {"status": "archived"}).status_code == 200
        assert _status(sid) == "archived"

    @pytest.mark.parametrize("status", bp_mod.SESSION_STATUSES)
    def test_every_declared_status_is_settable(self, client, sid, status):
        """The vocabulary the UI offers must be the vocabulary the API accepts."""
        assert _patch(client, sid, {"status": status}).status_code == 200
        assert _status(sid) == status

    def test_archived_is_terminal_to_the_reflex(self):
        """Both halves must agree on which statuses actually stop a card."""
        reflex = __import__(
            "tools.genesis.reflexes.migration_canvas", fromlist=["_TERMINAL_SESSION_STATUSES"]
        )
        assert set(reflex._TERMINAL_SESSION_STATUSES) == set(bp_mod.TERMINAL_SESSION_STATUSES)


class TestStatusValidation:
    def test_unknown_status_is_rejected(self, client, sid):
        """'closed', 'done', 'complete ' — a typo must not create a fourth state."""
        resp = _patch(client, sid, {"status": "closed"})
        assert resp.status_code == 400
        assert "closed" in resp.get_json()["error"]

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


class TestWizardControl:
    """The API was never the gap — the missing UI control was."""

    def test_wizard_offers_a_close_control(self):
        html = _WIZARD.read_text(encoding="utf-8")
        assert "toggleSessionLifecycle" in html
        assert "Close / Archive session" in html

    def test_control_patches_a_terminal_status(self):
        html = _WIZARD.read_text(encoding="utf-8")
        assert "{status: next}" in html
        assert "'archived'" in html

    def test_wizard_terminal_list_matches_the_server(self):
        """A drifted client list would show 'Close' on an already-closed session."""
        html = _WIZARD.read_text(encoding="utf-8")
        expected = ", ".join(f"'{s}'" for s in bp_mod.TERMINAL_SESSION_STATUSES)
        assert f"TERMINAL_SESSION_STATUSES = [{expected}]" in html
