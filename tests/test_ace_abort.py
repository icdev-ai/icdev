# CUI // SP-CTI
"""ACE abort: a stop takes effect at once, needs no approval, and is audited (icx-cnf-04).

Covers PM-ACE-07 in args/platform_model/ace.yaml:
  * ``ACEController.abort`` signals every coworker thread and the run's abort
    event, marks the instance ``cancelled``, and appends an ``abort`` row to
    ace_audit_log naming who stopped it;
  * an unknown instance is a LookupError, and the route maps it to a 404;
  * ``POST /api/ace/<id>/abort`` stops the run in the same request, with no
    approval step and no HITL row in between;
  * the MCP handler ``handle_ace_abort`` does the same, recording ``mcp``.

Uses the real storage layer on a throwaway SQLite file (ICDEV_ACE_DB_URL).
"""
from __future__ import annotations

import importlib
import threading

import pytest
from flask import Flask


@pytest.fixture()
def ace_db(tmp_path, monkeypatch):
    db_path = tmp_path / "ace_abort.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return db_path


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed(instance_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, trust_tier) "
            "VALUES (%s, %s, 'ai_developer', 'active', 'yellow')",
            (instance_id, instance_id),
        )
        conn.commit()
    finally:
        conn.close()


def _state(instance_id: str) -> str:
    conn = _conn()
    try:
        return conn.execute("SELECT state FROM ace_instances WHERE id = %s",
                            (instance_id,)).fetchone()[0]
    finally:
        conn.close()


def _audit(instance_id: str) -> list[tuple]:
    conn = _conn()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT action, actor, detail, control_refs FROM ace_audit_log "
            "WHERE instance_id = %s ORDER BY id", (instance_id,)).fetchall()]
    finally:
        conn.close()


class _Thread:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture()
def controller(monkeypatch):
    """A fresh controller, installed as the singleton the route and MCP tool use."""
    from icdev.tools.ace.controller import ACEController

    ctrl = ACEController()
    monkeypatch.setattr(ACEController, "_instance", ctrl)
    yield ctrl
    ctrl._executor.shutdown(wait=False)


def test_abort_stops_every_thread_and_marks_the_run_cancelled(ace_db, controller):
    _seed("ace-ab-1")
    threads, event = [_Thread(), _Thread()], threading.Event()
    controller._threads["ace-ab-1"] = threads
    controller._abort_events["ace-ab-1"] = event

    controller.abort("ace-ab-1", actor="u-ops")

    assert all(t.stopped for t in threads)
    assert event.is_set()
    assert _state("ace-ab-1") == "cancelled"


def test_abort_is_audited_with_its_actor(ace_db, controller):
    _seed("ace-ab-2")
    controller._threads["ace-ab-2"] = [_Thread()]
    controller.abort("ace-ab-2", actor="u-ops")

    rows = _audit("ace-ab-2")
    assert len(rows) == 1
    action, actor, detail, refs = rows[0]
    assert (action, actor) == ("abort", "u-ops")
    assert "1 coworker thread(s) signalled" in detail
    assert "IR-4" in refs


def test_abort_of_an_unknown_instance_raises_and_writes_nothing(ace_db, controller):
    with pytest.raises(LookupError):
        controller.abort("ace-nope")
    assert _audit("ace-nope") == []


@pytest.fixture()
def client(ace_db, controller):
    bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    bp_mod._state["db_ready"] = True
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_mod.ace_bp)
    if "ace_api" not in app.blueprints:
        app.register_blueprint(bp_mod.ace_api_bp)
    with app.test_client() as c:
        yield c


def test_the_route_stops_the_run_in_one_request_with_no_approval(client, controller):
    _seed("ace-ab-3")
    thread = _Thread()
    controller._threads["ace-ab-3"] = [thread]

    res = client.post("/api/ace/ace-ab-3/abort")

    assert res.status_code == 200
    assert res.get_json() == {"instance_id": "ace-ab-3", "state": "cancelled", "aborted": True}
    assert thread.stopped and _state("ace-ab-3") == "cancelled"
    actions = [a for a, *_ in _audit("ace-ab-3")]
    # Nothing waits between the request and the stop: no HITL row, no approval.
    assert actions == ["abort"]


def test_the_route_returns_404_for_an_unknown_instance(client):
    res = client.post("/api/ace/ace-missing/abort")
    assert res.status_code == 404
    assert res.get_json()["error"] == "not_found"


def test_the_mcp_tool_aborts_and_records_mcp_as_the_actor(ace_db, controller):
    from tools.mcp.gap_handlers import handle_ace_abort

    _seed("ace-ab-4")
    out = handle_ace_abort({"instance_id": "ace-ab-4"})

    assert out == {"instance_id": "ace-ab-4", "state": "cancelled", "aborted": True}
    assert _state("ace-ab-4") == "cancelled"
    assert _audit("ace-ab-4")[0][:2] == ("abort", "mcp")
    assert "error" in handle_ace_abort({})
