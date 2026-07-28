# CUI // SP-CTI
"""dwo-evt-02 — gateway events routed into workflow triggers.

Covers the four acceptance criteria on the card:
  1. A cleared event on a gateway channel starts the bound workflow run.
  2. The same event replayed with the same delivery id starts exactly one run.
  3. An event that never clears the security chain starts nothing.
  4. An event whose classification exceeds the workflow's IL is refused.
"""

from __future__ import annotations

import importlib

import pytest

from tools.gateway.event_envelope import CommandEnvelope


@pytest.fixture()
def studio_db(tmp_path, monkeypatch):
    """A temp SQLite DB that get_connection() resolves to, with studio tables."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.studio.init_db import init_studio_tables

    init_studio_tables()
    return db_path


@pytest.fixture()
def registry(studio_db):
    """Registry modules bound to the test database, with the tables created."""
    event_sources = importlib.import_module("tools.studio.event_sources")
    event_dispatch = importlib.import_module("tools.studio.event_dispatch")
    return event_sources, event_dispatch


@pytest.fixture()
def started_runs(monkeypatch):
    """Capture start_run calls instead of spawning real workflow threads."""
    calls: list[tuple[str, str]] = []

    def fake_start_run(workflow_id, project_id="default"):
        calls.append((workflow_id, project_id))
        return f"run-{len(calls):04d}"

    runner = importlib.import_module("tools.studio.workflow_runner")
    monkeypatch.setattr(runner, "start_run", fake_start_run)
    return calls


def _workflow(workflow_id="wf-deploy"):
    """studio_workflow_triggers.workflow_id is a real FK — the target must exist."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflows (workflow_id, name, template_yaml)
               VALUES (%s, %s, %s)""",
            (workflow_id, "Deploy", "nodes: []"),
        )
        conn.commit()
    finally:
        conn.close()
    return workflow_id


def _bind(event_sources, *, channel="github", workflow_il="IL6", filters=None, source_il="IL2"):
    _workflow()
    source_id = event_sources.create_event_source(channel, "gateway_channel", max_il=source_il)
    trigger_id = event_sources.create_trigger(
        source_id,
        "wf-deploy",
        event_type="push",
        filters=filters,
        workflow_il=workflow_il,
    )
    return source_id, trigger_id


def _envelope(channel="github"):
    return CommandEnvelope(channel=channel, channel_user_id="u1", command="deploy")


def _outcomes(_db=None):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT outcome, run_id, reason FROM studio_trigger_events ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [tuple(r) for r in rows]


# ── 1. A cleared event starts the bound run ────────────────────────────


def test_cleared_event_starts_bound_workflow_run(registry, studio_db, started_runs):
    event_sources, event_dispatch = registry
    _bind(event_sources)

    result = event_dispatch.dispatch_envelope(
        _envelope(),
        channel_config={"max_il": "IL5"},
        payload={"type": "push", "repository": {"full_name": "icdev-ai/icdev"}},
        headers={"X-GitHub-Delivery": "d-1"},
    )

    assert result["matched"] == 1
    assert len(result["started"]) == 1
    assert started_runs == [("wf-deploy", "default")]

    outcomes = [o for o, _run, _r in _outcomes()]
    assert "matched" in outcomes and "run_started" in outcomes


def test_filters_reject_a_non_matching_payload(registry, studio_db, started_runs):
    event_sources, event_dispatch = registry
    _bind(
        event_sources,
        filters=[{"field": "repository.full_name", "operator": "equals", "value": "icdev-ai/icdev"}],
    )

    result = event_dispatch.dispatch_envelope(
        _envelope(),
        channel_config={"max_il": "IL5"},
        payload={"type": "push", "repository": {"full_name": "someone/else"}},
        headers={"X-GitHub-Delivery": "d-2"},
    )

    assert result["matched"] == 0
    assert started_runs == []
    assert [o for o, _run, _r in _outcomes()] == ["no_match"]


# ── 2. Replay with the same delivery id starts exactly one run ─────────


def test_replayed_delivery_id_starts_exactly_one_run(registry, studio_db, started_runs):
    event_sources, event_dispatch = registry
    _bind(event_sources)

    payload = {"type": "push"}
    headers = {"X-GitHub-Delivery": "delivery-abc"}
    envelope = _envelope()

    first = event_dispatch.dispatch_envelope(envelope, {"max_il": "IL5"}, payload, headers)
    # A fresh envelope, as the platform would send on retry — same delivery id.
    second = event_dispatch.dispatch_envelope(_envelope(), {"max_il": "IL5"}, payload, headers)

    assert len(first["started"]) == 1
    assert second["started"] == []
    assert len(started_runs) == 1, "webhook retry must not start a second run"


def test_distinct_delivery_ids_each_start_a_run(registry, studio_db, started_runs):
    event_sources, event_dispatch = registry
    _bind(event_sources)

    for delivery in ("d-10", "d-11"):
        event_dispatch.dispatch_envelope(
            _envelope(), {"max_il": "IL5"}, {"type": "push"}, {"X-GitHub-Delivery": delivery}
        )

    assert len(started_runs) == 2


# ── 3. An event that never cleared the chain starts nothing ────────────


def test_unregistered_source_starts_nothing_and_is_audited(registry, studio_db, started_runs):
    """An event arriving from a channel nobody registered matches no trigger.

    The gateway itself rejects a bad signature or a failed gate before dispatch
    is ever called; this asserts the other half — that dispatch does not invent
    a binding for a source that was never declared.
    """
    event_sources, event_dispatch = registry
    _bind(event_sources, channel="github")

    result = event_dispatch.dispatch_envelope(
        _envelope(channel="rogue-channel"),
        {"max_il": "IL5"},
        {"type": "push"},
        {"X-GitHub-Delivery": "d-3"},
    )

    assert result["matched"] == 0
    assert started_runs == []
    assert [o for o, _run, _r in _outcomes()] == ["no_match"]


def test_gateway_dispatches_only_after_the_security_chain(registry):
    """The dispatch call sits after gate evaluation, not before it."""
    source = (
        importlib.import_module("tools.gateway.gateway_agent").__file__
    )
    text = open(source, encoding="utf-8").read()
    chain_at = text.index("run_security_chain(envelope")
    dispatch_at = text.index("dispatch_envelope_async")
    reject_at = text.index('"status": "rejected"')

    assert chain_at < dispatch_at, "dispatch must run after the 8-gate chain"
    assert reject_at < dispatch_at, "a failed chain returns before dispatch"


# ── 4. Classification above the workflow's IL is refused ───────────────


def test_event_above_workflow_il_is_refused_and_audited(registry, studio_db, started_runs):
    event_sources, event_dispatch = registry
    _bind(event_sources, workflow_il="IL4")

    result = event_dispatch.dispatch_envelope(
        _envelope(),
        channel_config={"max_il": "IL6"},  # SECRET-capable channel
        payload={"type": "push"},
        headers={"X-GitHub-Delivery": "d-4"},
    )

    assert result["started"] == []
    assert len(result["refused"]) == 1
    assert started_runs == []

    outcomes = _outcomes()
    assert outcomes[0][0] == "refused_classification"
    assert "IL6" in outcomes[0][2] and "IL4" in outcomes[0][2]


def test_event_at_or_below_workflow_il_is_permitted(registry, started_runs):
    event_sources, event_dispatch = registry
    _bind(event_sources, workflow_il="IL5")

    result = event_dispatch.dispatch_envelope(
        _envelope(), {"max_il": "IL5"}, {"type": "push"}, {"X-GitHub-Delivery": "d-5"}
    )

    assert len(result["started"]) == 1


def test_source_ceiling_raises_the_event_classification(registry, started_runs):
    """A source declared at IL5 is treated as IL5 even on an IL4 channel."""
    event_sources, event_dispatch = registry
    _bind(event_sources, workflow_il="IL4", source_il="IL5")

    result = event_dispatch.dispatch_envelope(
        _envelope(), {"max_il": "IL4"}, {"type": "push"}, {"X-GitHub-Delivery": "d-6"}
    )

    assert result["classification"] == "IL5"
    assert len(result["refused"]) == 1
    assert started_runs == []


# ── Supporting behaviour ───────────────────────────────────────────────


def test_delivery_id_falls_back_to_payload_then_none(registry):
    _event_sources, event_dispatch = registry

    assert event_dispatch.extract_delivery_id({}, {"X-GitHub-Delivery": "h"}) == "h"
    assert event_dispatch.extract_delivery_id({"event_id": "p"}, {}) == "p"
    assert event_dispatch.extract_delivery_id({}, {}) is None


def test_filter_language_reuses_automation_builder_operators(registry):
    """No second condition DSL — the operators come from automation_builder."""
    event_sources, _dispatch = registry
    from tools.studio import automation_builder

    assert event_sources._evaluate_condition is automation_builder._evaluate_condition

    payload = {"severity": "high", "count": 7}
    assert event_sources.evaluate_filters(
        payload, [{"field": "severity", "operator": "in_list", "value": "high,critical"}]
    )
    assert not event_sources.evaluate_filters(
        payload, [{"field": "count", "operator": "greater_than", "value": "10"}]
    )


def test_dispatch_is_asynchronous_relative_to_the_response(registry, started_runs):
    """dispatch_envelope_async returns without waiting on the workflow."""
    event_sources, event_dispatch = registry
    _bind(event_sources)

    event_dispatch.dispatch_envelope_async(
        _envelope(), {"max_il": "IL5"}, {"type": "push"}, {"X-GitHub-Delivery": "d-async"}
    )

    import threading
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(t.name.startswith("studio-event-dispatch") for t in threading.enumerate()):
            break
        time.sleep(0.05)

    assert len(started_runs) == 1
