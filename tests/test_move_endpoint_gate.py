# CUI // SP-CTI
"""Regression tests for guard-22 — the move-to-done verification gate on
POST /api/kanban/tasks/<id>/move.

These cover the eight paths exercised during the hardening smoke test so
the gate can't silently regress:

    [A] plain move to done, no verification row     -> 409 verification_required
    [B] bypass_verification=true, no reason         -> 400 bypass_reason_required
    [C] bypass_verification=true + reason           -> 200, audit row written
    [D] non-done transition (no gate)               -> 200
    [E] reverting a done task back to backlog       -> 200 (only *to* done is gated)
    [F] passing kanban_verifications row            -> 200
    [G] failing kanban_verifications row            -> 409
    [H] ICDEV_KANBAN_VERIFY_GATE=false kill switch  -> 200

The scheduler's internal `_move_task` writes to the DB directly so the
gate only applies to external callers (dashboard drag-drop, curl, tests).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest


@pytest.fixture
def gated_kanban_client(icdev_db, monkeypatch):
    """Flask client over the canonical kanban schema, via the storage layer.

    Two things here are deliberate, and both are why this suite went red:

    1. The schema comes from conftest's MINIMAL_ICDEV_SCHEMA (the ``icdev_db``
       fixture), not from a CREATE TABLE pasted into this file. The private copy
       listed 15 columns; production ``create_task`` had since grown
       ``start_date``/``target_date`` and a ``kanban_task_deps`` junction table,
       and the fixture never learned — so every request 500'd against a table
       shape that no longer exists anywhere but in this file.

    2. ``get_connection`` is stubbed with a StorageConnection, not a bare
       sqlite3.Connection. Runtime SQL in tools/dashboard/api/kanban.py is
       authored for PostgreSQL (``%s`` placeholders) and relies on
       StorageConnection's cursor running translate_sql to rewrite them to ``?``
       on SQLite. Talking straight to sqlite3 skips that and every statement
       raises ``near "%": syntax error``.
    """
    from flask import Flask

    from tools.db.storage import StorageConnection

    db_path = icdev_db

    def _fake_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    from tools.dashboard.api import kanban as kanban_mod

    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    class _StubSSE:
        def broadcast(self, *a, **kw):
            pass

    monkeypatch.setattr(kanban_mod, "sse_manager", _StubSSE())

    # Make the gate's default behavior deterministic regardless of the
    # developer's shell env.
    monkeypatch.delenv("ICDEV_KANBAN_VERIFY_GATE", raising=False)

    app = Flask(__name__)
    app.register_blueprint(kanban_mod.kanban_api)
    return app.test_client(), db_path


def _create_task(client, title="t", status="in_progress"):
    r = client.post(
        "/api/kanban/tasks",
        json={"title": title, "status": status},
    )
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["id"]


def _insert_verification(db_path, task_id, **cols):
    defaults = {
        "id": f"kv-{uuid.uuid4().hex[:10]}",
        "task_id": task_id,
        "verified_at": "2026-04-14T00:00:00+00:00",
        "result": "passed",
        "reason": None,
        "codelens_passed": 1,
        "coherence_passed": 1,
        "e2e_ran": 0,
        "e2e_passed": None,
        "companion_synced": 1,
    }
    defaults.update(cols)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_verifications "
        "(id, task_id, verified_at, result, reason, "
        " codelens_passed, coherence_passed, e2e_ran, e2e_passed, companion_synced) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            defaults["id"], defaults["task_id"], defaults["verified_at"],
            defaults["result"], defaults["reason"],
            defaults["codelens_passed"], defaults["coherence_passed"],
            defaults["e2e_ran"], defaults["e2e_passed"],
            defaults["companion_synced"],
        ),
    )
    conn.commit()
    conn.close()


class TestMoveEndpointGate:
    # [A]
    def test_plain_move_to_done_without_verification_blocked(self, gated_kanban_client):
        client, _ = gated_kanban_client
        tid = _create_task(client)
        r = client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "done"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error"] == "verification_required"
        assert "bypass_verification" in body["detail"]

    # [B]
    def test_bypass_without_reason_rejected(self, gated_kanban_client):
        client, _ = gated_kanban_client
        tid = _create_task(client)
        r = client.post(
            f"/api/kanban/tasks/{tid}/move",
            json={"status": "done", "bypass_verification": True},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "bypass_reason_required"

    # [C]
    def test_bypass_with_reason_succeeds_and_audits(self, gated_kanban_client):
        client, db_path = gated_kanban_client
        tid = _create_task(client)
        r = client.post(
            f"/api/kanban/tasks/{tid}/move",
            json={
                "status": "done",
                "bypass_verification": True,
                "bypass_reason": "migration fixture",
            },
        )
        assert r.status_code == 200
        assert r.get_json()["new_status"] == "done"

        # audit trail: a bypass row was inserted
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result, reason FROM kanban_verifications "
            "WHERE task_id = ? ORDER BY verified_at DESC LIMIT 1",
            (tid,),
        ).fetchone()
        assert row is not None
        assert row["result"] == "bypassed"
        assert row["reason"] == "migration fixture"

        # metadata flag: completed_via_bypass must be 1 on the task row
        task_row = conn.execute(
            "SELECT completed_via_bypass FROM kanban_tasks WHERE id = ?",
            (tid,),
        ).fetchone()
        conn.close()
        assert task_row is not None
        assert task_row["completed_via_bypass"] == 1

    # [D]
    def test_non_done_transition_not_gated(self, gated_kanban_client):
        client, _ = gated_kanban_client
        tid = _create_task(client, status="backlog")
        r = client.post(
            f"/api/kanban/tasks/{tid}/move", json={"status": "in_progress"}
        )
        assert r.status_code == 200
        assert r.get_json()["new_status"] == "in_progress"

    # [E]
    def test_reverting_done_to_backlog_not_gated(self, gated_kanban_client):
        client, db_path = gated_kanban_client
        tid = _create_task(client)
        _insert_verification(db_path, tid, result="passed")
        # First move to done via a passing verification
        assert (
            client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "done"}).status_code
            == 200
        )
        # Now revert — this direction must not be gated
        r = client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "backlog"})
        assert r.status_code == 200
        assert r.get_json()["new_status"] == "backlog"

    # [F]
    def test_passing_verification_row_unblocks(self, gated_kanban_client):
        client, db_path = gated_kanban_client
        tid = _create_task(client)
        _insert_verification(db_path, tid, result="passed")
        r = client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "done"})
        assert r.status_code == 200
        assert r.get_json()["new_status"] == "done"

    # [G]
    def test_failing_verification_row_blocks(self, gated_kanban_client):
        client, db_path = gated_kanban_client
        tid = _create_task(client)
        _insert_verification(
            db_path, tid,
            result="failed",
            codelens_passed=0,
            coherence_passed=None,
            reason="ruff: 4 issues",
        )
        r = client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "done"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error"] == "verification_required"
        # Failing reason bubbles up for operator visibility
        assert body["last_reason"] == "ruff: 4 issues"

    # [H]
    @pytest.mark.parametrize("value", ["false", "0", "no", "off"])
    def test_kill_switch_disables_gate(self, gated_kanban_client, monkeypatch, value):
        client, _ = gated_kanban_client
        monkeypatch.setenv("ICDEV_KANBAN_VERIFY_GATE", value)
        tid = _create_task(client)
        r = client.post(f"/api/kanban/tasks/{tid}/move", json={"status": "done"})
        assert r.status_code == 200
        assert r.get_json()["new_status"] == "done"
