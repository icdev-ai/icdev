# CUI // SP-CTI
"""dwo-evt-01-d5 — canvas_bus event sources are wired onto the cross-canvas bus.

``studio_event_sources`` has always accepted a ``canvas_bus`` kind and
``event_sources.match_event`` has always known how to evaluate an event, but
nothing subscribed, so a ``canvas_bus`` source was inert.  These tests pin the
wire end to end:

    bus_subscriber.register()
      -> event_bus.subscribe(<canvas>, <event_type>, handler)
    event_bus.publish(<canvas>, <event_type>, {...})
      -> handler -> event_sources.dispatch_event -> match_event
      -> a run for every matching trigger + a studio_trigger_events row for
         every candidate, matched or not.

The bus writes ``canvas_events`` / ``audit_trail``; the studio tables live in
the conftest database.  Only the bus's two connections are redirected to a temp
SQLite file, so the studio side runs against its real schema.

``start_run`` is substituted — the run engine has its own tests, and starting a
real run here would spawn a subprocess to prove a subscription.

NIST 800-53: AU-2, SI-4
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import bus_subscriber, event_sources

_BUS_DDL = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id             TEXT NOT NULL PRIMARY KEY,
    source_canvas  TEXT NOT NULL,
    target_canvas  TEXT,
    event_type     TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT,
    consumed_at    TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    project_id      TEXT,
    event_type      TEXT,
    actor           TEXT,
    action          TEXT,
    details         TEXT,
    affected_files  TEXT,
    classification  TEXT
);
"""


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    """The real event bus, with a clean listener registry and a temp DB.

    ``_LISTENERS`` and ``bus_subscriber._REGISTERED`` are module globals; without
    resetting both, one test's subscriptions would fire during the next test's
    publish and the run counts below would be meaningless.
    """
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "bus.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_BUS_DDL)
    raw.commit()
    raw.close()

    def _conn():
        r = sqlite3.connect(str(db_path))
        r.row_factory = sqlite3.Row
        return StorageConnection(r, "sqlite")

    event_bus = importlib.import_module("tools.canvas.event_bus")
    monkeypatch.setattr(event_bus, "_LISTENERS", {}, raising=True)
    monkeypatch.setattr(event_bus, "get_connection", _conn, raising=True)
    monkeypatch.setattr(event_bus, "get_canvas_connection", _conn, raising=True)
    monkeypatch.setattr(bus_subscriber, "_REGISTERED", set(), raising=True)
    return event_bus


@pytest.fixture()
def started_runs(monkeypatch):
    """Substitute the run engine, keeping what it was asked to start.

    Patched through ``importlib`` because ``tools`` is a shim over
    ``icdev.tools`` — a string path could bind a different module object than
    the one ``dispatch_event`` imports.
    """
    runner = importlib.import_module("tools.studio.workflow_runner")
    started: list[tuple] = []

    def _start(workflow_id, project_id="default", **kwargs):
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        started.append((workflow_id, project_id, kwargs.get("inputs"), run_id))
        return run_id

    monkeypatch.setattr(runner, "start_run", _start)
    return started


def _canvas_source(canvas_id: str, **config) -> str:
    result = event_sources.create_event_source(
        f"dwo-evt-01-d5 {uuid.uuid4().hex[:8]}",
        "canvas_bus",
        config={"canvas_id": canvas_id, **config},
    )
    assert result["status"] == "ok", result
    return result["source_id"]


def _workflow() -> str:
    """A real studio_workflows row — studio_workflow_triggers has a foreign key
    to it, so a trigger cannot point at a workflow that does not exist."""
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, "dwo-evt-01-d5 test", "name: Test\nsteps: []\n"),
        )
        conn.commit()
    finally:
        conn.close()
    return workflow_id


def _trigger(source_id: str, *, event_type: str = "", filt=None, mapping=None) -> tuple[str, str]:
    workflow_id = _workflow()
    result = event_sources.create_workflow_trigger(
        workflow_id,
        source_id,
        event_type=event_type,
        event_filter=filt or [],
        input_mapping=mapping or {},
    )
    assert result["status"] == "ok", result
    return result["trigger_id"], workflow_id


# ── The wire ───────────────────────────────────────────────


def test_published_canvas_event_starts_the_matching_workflow(bus, started_runs):
    source_id = _canvas_source("qdc", event_types=["qdc.gate.executed"])
    trigger_id, workflow_id = _trigger(
        source_id,
        event_type="qdc.gate.executed",
        filt=[{"field": "passed", "operator": "equals", "value": "False"}],
        mapping={"gate": "event.payload.gate_id", "env": "staging"},
    )

    assert bus_subscriber.register()["subscriptions"] >= 1

    bus.publish("qdc", "qdc.gate.executed", {"gate_id": "stig-cat1", "passed": "False"})

    # Sources from other tests live in the same database and legitimately fire
    # on the same event, so assertions here are scoped to this test's workflow.
    mine = [r for r in started_runs if r[0] == workflow_id]
    assert len(mine) == 1
    _workflow_id, _project, inputs, run_id = mine[0]
    # The input mapping resolved against the canvas payload; the literal passed
    # through unchanged.
    assert inputs == {"gate": "stig-cat1", "env": "staging"}

    rows = event_sources.list_trigger_events(source_id=source_id)
    assert len(rows) == 1
    assert rows[0]["trigger_id"] == trigger_id
    assert rows[0]["matched"] is True
    assert rows[0]["run_id"] == run_id
    # Correlation back to the canvas_events row that caused the run.
    assert rows[0]["payload"]["_bus_event_id"]
    assert rows[0]["payload"]["_canvas_id"] == "qdc"


def test_non_matching_canvas_event_is_audited_not_dropped(bus, started_runs):
    source_id = _canvas_source("qdc", event_types=["qdc.gate.executed"])
    _trigger_id, workflow_id = _trigger(
        source_id,
        event_type="qdc.gate.executed",
        filt=[{"field": "gate_id", "operator": "equals", "value": "stig-cat1"}],
    )
    bus_subscriber.register()

    bus.publish("qdc", "qdc.gate.executed", {"gate_id": "unrelated"})

    assert [r for r in started_runs if r[0] == workflow_id] == []
    rows = event_sources.list_trigger_events(source_id=source_id)
    assert len(rows) == 1
    assert rows[0]["matched"] is False
    # A trigger that never fires has to say why.
    assert "gate_id" in rows[0]["reason"]


def test_event_type_outside_the_source_config_never_reaches_studio(bus, started_runs):
    """A source declaring one event type is not subscribed to the whole canvas."""
    source_id = _canvas_source("qdc", event_types=["qdc.gate.executed"])
    _trigger_id, workflow_id = _trigger(source_id)
    bus_subscriber.register()

    bus.publish("qdc", "qdc.design.saved", {"design_id": "d1"})

    assert [r for r in started_runs if r[0] == workflow_id] == []
    assert event_sources.list_trigger_events(source_id=source_id) == []


def test_wildcard_source_hears_every_event_type_on_its_canvas(bus, started_runs):
    source_id = _canvas_source("qdc")  # no event_types -> ["*"]
    _trigger_id, workflow_id = _trigger(source_id)
    bus_subscriber.register()

    bus.publish("qdc", "qdc.design.saved", {"design_id": "d1"})

    assert [r for r in started_runs if r[0] == workflow_id]
    assert event_sources.list_trigger_events(source_id=source_id)[0]["matched"] is True


def test_events_from_another_canvas_are_ignored(bus, started_runs):
    source_id = _canvas_source("qdc")
    _trigger_id, workflow_id = _trigger(source_id)
    bus_subscriber.register()

    bus.publish("bdc", "bdc.design.saved", {"design_id": "d1"})

    assert [r for r in started_runs if r[0] == workflow_id] == []
    assert event_sources.list_trigger_events(source_id=source_id) == []


# ── Registration hygiene ───────────────────────────────────


def test_registration_is_idempotent(bus, started_runs):
    """A second register() must not double-subscribe — one event, one run."""
    source_id = _canvas_source("qdc", event_types=["qdc.gate.executed"])
    _trigger(source_id)

    first = bus_subscriber.register()
    second = bus_subscriber.register()
    assert first["subscriptions"] == second["subscriptions"] >= 1
    assert second["new"] == 0

    bus.publish("qdc", "qdc.gate.executed", {})
    # Scoped to this source: a double subscription would show up as two
    # evaluated events for one publish.
    assert len(event_sources.list_trigger_events(source_id=source_id)) == 1


def test_source_without_a_canvas_id_is_not_wired(bus):
    """Subscribing to nothing is a bug, not a configuration — refuse it."""
    source_id = _canvas_source("")  # empty canvas_id
    assert bus_subscriber.register_source(
        {"source_id": source_id, "kind": "canvas_bus", "enabled": 1, "config": {}}
    ) == 0
    assert bus_subscriber.list_subscriptions() == []


def test_non_canvas_bus_sources_are_left_alone(bus):
    gateway = event_sources.create_event_source(
        f"dwo-evt-01-d5 {uuid.uuid4().hex[:8]}", "gateway_channel"
    )
    assert gateway["status"] == "ok"
    assert bus_subscriber.register_source(
        {"source_id": gateway["source_id"], "kind": "gateway_channel",
         "enabled": 1, "config": {"canvas_id": "qdc"}}
    ) == 0


def test_creating_a_canvas_bus_source_wires_it_immediately(bus, started_runs):
    """A source created from the Triggers panel must be live without a restart."""
    source_id = _canvas_source("qdc", event_types=["qdc.gate.executed"])
    _trigger_id, workflow_id = _trigger(source_id)

    # No register() call here — create_event_source did the wiring.
    bus.publish("qdc", "qdc.gate.executed", {})
    assert [r for r in started_runs if r[0] == workflow_id]
