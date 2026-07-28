"""dwo-evt-03 — external_event trigger + run_workflow action in automation_builder.

Acceptance:
  - get_trigger_types() includes external_event; get_action_types() includes
    run_workflow (workflow_id + input mapping).
  - simulate_automation() on an external_event automation returns a
    per-condition pass/fail trace against a sample external payload.
  - An automation with a run_workflow action starts a real run when triggered.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tools.studio import automation_builder as ab
from tools.studio.init_db import STUDIO_TABLES

_TABLES = [
    "studio_workflows",
    "studio_automations",
    "studio_automation_runs",
    "studio_event_sources",
]


@pytest.fixture()
def db(monkeypatch) -> sqlite3.Connection:
    """A live sqlite connection wired into automation_builder.get_connection.

    get_connection() callers close the connection, so hand out a proxy whose
    close() is a no-op and keep the real one open for assertions.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for name in _TABLES:
        conn.executescript(STUDIO_TABLES[name])
    conn.commit()

    class _Proxy:
        def execute(self, sql, params=()):
            return conn.execute(sql.replace("%s", "?"), params)

        def commit(self):
            conn.commit()

        def close(self):
            pass

    monkeypatch.setattr(ab, "get_connection", lambda: _Proxy())
    yield conn
    conn.close()


# ── Vocabulary ────────────────────────────────────────────────────────


def test_external_event_trigger_is_registered():
    trigger = next((t for t in ab.get_trigger_types() if t["id"] == "external_event"), None)
    assert trigger is not None, "external_event missing from TRIGGER_TYPES"
    assert trigger["category"] == "system"
    # UI needs icon/colour to render it alongside the existing types.
    assert trigger["icon"] and trigger["color"]
    fields = {f["name"] for f in trigger.get("config_fields", [])}
    assert "source_id" in fields, "external_event must name a studio_event_sources row"


def test_run_workflow_action_takes_workflow_and_input_mapping():
    action = next((a for a in ab.get_action_types() if a["id"] == "run_workflow"), None)
    assert action is not None, "run_workflow missing from ACTION_TYPES"
    assert "workflow_id" in action["params"]
    assert "input_mapping" in action["params"]


def test_one_condition_vocabulary():
    """Triggers and automations share CONDITION_OPERATORS — no second DSL."""
    op_ids = {o["id"] for o in ab.get_condition_operators()}
    assert {"equals", "contains", "in_list", "is_empty"} <= op_ids


# ── Simulation ────────────────────────────────────────────────────────


def _make_external_automation(db, *, source_id="src-gh", conditions=None, actions=None):
    result = ab.create_automation(
        "GitHub push → workflow",
        {"type": "external_event", "config": {"source_id": source_id, "event_type": "push"}},
        actions if actions is not None else [{"type": "log_event", "config": {}}],
        conditions=conditions if conditions is not None else [],
    )
    return result["automation_id"]


def test_simulate_external_event_returns_condition_trace(db):
    db.execute(
        "INSERT INTO studio_event_sources (source_id, name, kind, config_json) VALUES (?,?,?,?)",
        ("src-gh", "GitHub webhook", "gateway_channel", json.dumps({"sample_payload": {"branch": "main", "author": "sam"}})),
    )
    db.commit()

    auto_id = _make_external_automation(
        db,
        conditions=[
            {"field": "branch", "operator": "equals", "value": "main"},
            {"field": "author", "operator": "equals", "value": "nobody"},
        ],
    )

    sim = ab.simulate_automation(auto_id)
    assert sim["status"] == "ok"
    assert sim["trigger_matched"] is True
    # The sample payload comes from the registered source, not invented.
    assert sim["sample_event"]["branch"] == "main"
    trace = {c["field"]: c["met"] for c in sim["condition_results"]}
    assert trace == {"branch": True, "author": False}
    assert sim["conditions_met"] is False
    assert all(a["would_execute"] is False for a in sim["actions_preview"])


def test_simulate_reports_a_source_mismatch(db):
    auto_id = _make_external_automation(db, source_id="src-gh")
    sim = ab.simulate_automation(auto_id, {"type": "external_event", "source_id": "src-other", "event_type": "push"})
    assert sim["trigger_matched"] is False
    assert "src-gh" in sim["trigger_reason"]


def test_simulate_previews_resolved_workflow_inputs(db):
    auto_id = _make_external_automation(
        db,
        actions=[
            {
                "type": "run_workflow",
                "config": {
                    "workflow_id": "wf-1",
                    "input_mapping": {"branch": "event.branch", "origin": "webhook"},
                },
            }
        ],
    )
    sim = ab.simulate_automation(
        auto_id, {"type": "external_event", "source_id": "src-gh", "event_type": "push", "branch": "release"}
    )
    preview = sim["actions_preview"][0]
    assert preview["workflow_id"] == "wf-1"
    assert preview["resolved_inputs"] == {"branch": "release", "origin": "webhook"}


def test_existing_trigger_types_still_simulate(db):
    """Untouched automations keep working."""
    auto_id = ab.create_automation(
        "Critical finding",
        {"type": "finding_detected"},
        [{"type": "log_event", "config": {}}],
        conditions=[{"field": "severity", "operator": "equals", "value": "critical"}],
    )["automation_id"]
    sim = ab.simulate_automation(auto_id, {"type": "finding_detected", "severity": "critical"})
    assert sim["trigger_matched"] is True
    assert sim["conditions_met"] is True


# ── Live execution ────────────────────────────────────────────────────


def test_trigger_starts_a_real_workflow_run(db, monkeypatch):
    started: list[tuple[str, str]] = []
    seeded: dict[str, object] = {}

    monkeypatch.setattr(
        "tools.studio.workflow_runner.start_run",
        lambda wf_id, project_id="default": started.append((wf_id, project_id)) or "run-abc",
    )
    monkeypatch.setattr("tools.studio.run_memory.set", lambda run_id, k, v: seeded.update({k: v}))

    auto_id = _make_external_automation(
        db,
        actions=[
            {"type": "run_workflow", "config": {"workflow_id": "wf-1", "input_mapping": {"branch": "event.branch"}}}
        ],
    )
    result = ab.trigger_automation(
        auto_id, {"type": "external_event", "source_id": "src-gh", "event_type": "push", "branch": "main"}
    )

    assert result["status"] == "success"
    assert started == [("wf-1", "default")]
    assert seeded == {"branch": "main"}
    assert result["action_results"][0]["run_id"] == "run-abc"

    # The outcome is in the append-only run log.
    row = db.execute(
        "SELECT status, result_json FROM studio_automation_runs WHERE automation_id=?", (auto_id,)
    ).fetchone()
    assert row["status"] == "success"
    assert json.loads(row["result_json"])["action_results"][0]["run_id"] == "run-abc"


def test_trigger_skips_when_conditions_fail(db, monkeypatch):
    monkeypatch.setattr(
        "tools.studio.workflow_runner.start_run",
        lambda wf_id, project_id="default": pytest.fail("workflow must not start"),
    )
    auto_id = _make_external_automation(
        db,
        conditions=[{"field": "branch", "operator": "equals", "value": "main"}],
        actions=[{"type": "run_workflow", "config": {"workflow_id": "wf-1"}}],
    )
    result = ab.trigger_automation(
        auto_id, {"type": "external_event", "source_id": "src-gh", "event_type": "push", "branch": "topic"}
    )
    assert result["status"] == "skipped"
    assert result["condition_results"][0]["met"] is False


def test_trigger_skips_on_source_mismatch(db, monkeypatch):
    monkeypatch.setattr(
        "tools.studio.workflow_runner.start_run",
        lambda wf_id, project_id="default": pytest.fail("workflow must not start"),
    )
    auto_id = _make_external_automation(db, actions=[{"type": "run_workflow", "config": {"workflow_id": "wf-1"}}])
    result = ab.trigger_automation(auto_id, {"type": "external_event", "source_id": "src-nope", "event_type": "push"})
    assert result["status"] == "skipped"
    assert result["trigger_matched"] is False
