# CUI // SP-CTI
"""dwo-evt-01-d3 — event source CRUD, match_event filtering, trigger audit trail.

Two acceptance criteria, one file:

  1. ``match_event`` filters events correctly using *every* operator in
     ``automation_builder.CONDITION_OPERATORS``.  The parametrisation is
     checked against that list, so adding a ninth operator without a matching
     case here fails the suite rather than silently shipping an untested
     branch of the filter DSL.

  2. Every evaluated event creates a row in ``studio_trigger_events`` carrying
     its match status, and the ``run_id`` when it matched.  A trigger that
     never fires has to be diagnosable, so non-matches are logged too — and an
     event that reaches a source with no triggers at all still leaves a trace.

``dispatch_event`` starts real runs, which means real subprocesses.  The run
engine is dwo-evt-04's contract and is pinned by its own tests; here
``start_run`` is substituted so these tests pin *dispatch's* bookkeeping — that
a matched trigger records the run it started — without re-testing the runner.
"""

from __future__ import annotations

import importlib
import json
import uuid
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.studio import event_sources
from tools.studio.automation_builder import CONDITION_OPERATORS

REPO_ROOT = Path(__file__).resolve().parents[1]

# field, operator, value, payload that must match, payload that must not
OPERATOR_CASES = [
    ("branch", "equals", "main", {"branch": "main"}, {"branch": "dev"}),
    ("branch", "not_equals", "main", {"branch": "dev"}, {"branch": "main"}),
    ("branch", "contains", "hotfix", {"branch": "release/hotfix-9"}, {"branch": "main"}),
    ("severity", "greater_than", "5", {"severity": 9}, {"severity": 1}),
    ("severity", "less_than", "5", {"severity": 1}, {"severity": 9}),
    ("env", "in_list", "staging,prod", {"env": "prod"}, {"env": "dev"}),
    ("note", "is_empty", "", {"note": ""}, {"note": "set"}),
    ("note", "is_not_empty", "", {"note": "set"}, {"note": ""}),
]


def _source(kind: str = "gateway_channel", **config) -> str:
    """A fresh source per test — match_event scopes to a source, so isolation
    here is what keeps one test's triggers out of another's results."""
    result = event_sources.create_event_source(
        f"dwo-evt-01-d3 {uuid.uuid4().hex[:8]}", kind, config=config or {}
    )
    assert result["status"] == "ok", result
    return result["source_id"]


def _workflow() -> str:
    """A real studio_workflows row — studio_workflow_triggers carries a foreign
    key to it, so a trigger cannot be bound to a workflow that does not exist."""
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, "dwo-evt-01-d3 test", "name: Test\nsteps: []\n"),
        )
        conn.commit()
    finally:
        conn.close()
    return workflow_id


def _trigger(source_id: str, *, event_type: str = "", filt=None, mapping=None) -> str:
    result = event_sources.create_workflow_trigger(
        _workflow(),
        source_id,
        event_type=event_type,
        event_filter=filt or [],
        input_mapping=mapping or {},
    )
    assert result["status"] == "ok", result
    return result["trigger_id"]


def _events_for(source_id: str) -> list[dict]:
    return event_sources.list_trigger_events(source_id=source_id)


# ── 1. match_event covers the whole operator vocabulary ────


def test_operator_cases_cover_every_condition_operator():
    """The guard: a new operator in the DSL must arrive with a case here."""
    assert {case[1] for case in OPERATOR_CASES} == {op["id"] for op in CONDITION_OPERATORS}


@pytest.mark.parametrize(
    "field,operator,value,hit,miss",
    OPERATOR_CASES,
    ids=[case[1] for case in OPERATOR_CASES],
)
def test_match_event_filters_with_operator(field, operator, value, hit, miss):
    source_id = _source()
    trigger_id = _trigger(
        source_id, filt=[{"field": field, "operator": operator, "value": value}]
    )

    matched = event_sources.match_event(event_sources.normalize_event(source_id, "", hit))
    assert len(matched) == 1
    assert matched[0]["trigger"]["trigger_id"] == trigger_id
    assert matched[0]["matched"] is True, matched[0]["reason"]

    rejected = event_sources.match_event(event_sources.normalize_event(source_id, "", miss))
    assert len(rejected) == 1
    assert rejected[0]["matched"] is False
    # A non-match without a reason is undiagnosable, which is the bug this
    # module exists to prevent.
    assert field in rejected[0]["reason"]


def test_match_event_requires_every_condition():
    """Conditions are ANDed — one failure sinks the trigger."""
    source_id = _source()
    _trigger(
        source_id,
        filt=[
            {"field": "branch", "operator": "equals", "value": "main"},
            {"field": "env", "operator": "in_list", "value": "staging,prod"},
        ],
    )

    both = event_sources.match_event(
        event_sources.normalize_event(source_id, "", {"branch": "main", "env": "prod"})
    )
    assert both[0]["matched"] is True

    one = event_sources.match_event(
        event_sources.normalize_event(source_id, "", {"branch": "main", "env": "dev"})
    )
    assert one[0]["matched"] is False
    assert "env" in one[0]["reason"]


def test_match_event_filters_on_event_type():
    source_id = _source()
    _trigger(source_id, event_type="pr.merged")

    assert event_sources.match_event(
        event_sources.normalize_event(source_id, "pr.merged", {})
    )[0]["matched"] is True

    wrong = event_sources.match_event(
        event_sources.normalize_event(source_id, "pr.opened", {})
    )
    assert wrong[0]["matched"] is False
    assert "pr.merged" in wrong[0]["reason"]


def test_match_event_ignores_disabled_and_foreign_triggers():
    mine, theirs = _source(), _source()
    trigger_id = _trigger(mine)
    _trigger(theirs)

    results = event_sources.match_event(event_sources.normalize_event(mine, "", {}))
    assert [r["trigger"]["trigger_id"] for r in results] == [trigger_id]

    event_sources.toggle_workflow_trigger(trigger_id, False)
    assert event_sources.match_event(event_sources.normalize_event(mine, "", {})) == []


def test_matched_trigger_resolves_its_input_mapping():
    """Inputs are resolved only for matches — an unfired trigger has none."""
    source_id = _source()
    _trigger(
        source_id,
        filt=[{"field": "branch", "operator": "equals", "value": "main"}],
        mapping={"branch": "event.payload.branch", "env": "staging"},
    )

    hit = event_sources.match_event(
        event_sources.normalize_event(source_id, "", {"branch": "main"})
    )[0]
    assert hit["inputs"] == {"branch": "main", "env": "staging"}

    miss = event_sources.match_event(
        event_sources.normalize_event(source_id, "", {"branch": "dev"})
    )[0]
    assert miss["inputs"] == {}


# ── 2. every evaluated event lands in the append-only audit ──


@pytest.fixture()
def fake_start_run(monkeypatch):
    """Substitute the run engine, keeping the run ids it was asked to start.

    Patched through ``importlib`` because ``tools`` is a compatibility shim over
    ``icdev.tools`` — patching the string path could otherwise bind a different
    module object than the one ``dispatch_event`` imports.
    """
    runner = importlib.import_module("tools.studio.workflow_runner")
    started: list[tuple] = []

    def _start(workflow_id, project_id="default", **kwargs):
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        started.append((workflow_id, project_id, kwargs.get("inputs"), run_id))
        return run_id

    monkeypatch.setattr(runner, "start_run", _start)
    return started


def test_matched_event_logs_row_with_run_id(fake_start_run):
    source_id = _source()
    trigger_id = _trigger(
        source_id,
        filt=[{"field": "branch", "operator": "equals", "value": "main"}],
        mapping={"branch": "event.payload.branch"},
    )

    result = event_sources.dispatch_event(source_id, "push", {"branch": "main"})
    assert result["matched"] == 1
    run_id = result["runs"][0]
    assert fake_start_run[0][2] == {"branch": "main"}

    rows = _events_for(source_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["trigger_id"] == trigger_id
    assert row["matched"] is True
    assert row["run_id"] == run_id
    assert row["event_type"] == "push"
    assert row["payload"] == {"branch": "main"}


def test_unmatched_event_is_still_logged(fake_start_run):
    source_id = _source()
    trigger_id = _trigger(
        source_id, filt=[{"field": "branch", "operator": "equals", "value": "main"}]
    )

    result = event_sources.dispatch_event(source_id, "push", {"branch": "dev"})
    assert result["matched"] == 0
    assert fake_start_run == []

    row = _events_for(source_id)[0]
    assert row["trigger_id"] == trigger_id
    assert row["matched"] is False
    assert row["run_id"] is None
    assert "branch" in row["reason"]


def test_event_with_no_triggers_is_logged_once(fake_start_run):
    """An event that went nowhere is the hardest kind to debug, so it is the
    one case that most needs a row."""
    source_id = _source()

    result = event_sources.dispatch_event(source_id, "push", {"branch": "main"})
    assert result["matched"] == 0

    rows = _events_for(source_id)
    assert len(rows) == 1
    assert rows[0]["trigger_id"] is None
    assert rows[0]["matched"] is False
    assert rows[0]["run_id"] is None


def test_one_row_per_candidate_trigger(fake_start_run):
    """Two triggers, one event: two rows, one matched and one not."""
    source_id = _source()
    hit = _trigger(source_id, filt=[{"field": "branch", "operator": "equals", "value": "main"}])
    miss = _trigger(source_id, filt=[{"field": "branch", "operator": "equals", "value": "dev"}])

    event_sources.dispatch_event(source_id, "push", {"branch": "main"})

    by_trigger = {r["trigger_id"]: r for r in _events_for(source_id)}
    assert set(by_trigger) == {hit, miss}
    assert by_trigger[hit]["matched"] is True and by_trigger[hit]["run_id"]
    assert by_trigger[miss]["matched"] is False and by_trigger[miss]["run_id"] is None


def test_trigger_event_survives_its_trigger(fake_start_run):
    """Deleting a trigger must not erase why past runs started — the audit is
    append-only, so history outlives configuration."""
    source_id = _source()
    trigger_id = _trigger(source_id)
    event_sources.dispatch_event(source_id, "push", {})

    assert event_sources.delete_workflow_trigger(trigger_id)["deleted"] is True
    assert event_sources.get_workflow_trigger(trigger_id) is None
    assert [r["trigger_id"] for r in _events_for(source_id)] == [trigger_id]


def test_trigger_event_for_run_explains_a_run(fake_start_run):
    source_id = _source()
    _trigger(source_id)
    run_id = event_sources.dispatch_event(source_id, "push", {"branch": "main"})["runs"][0]

    evt = event_sources.trigger_event_for_run(run_id)
    assert evt is not None
    assert evt["matched"] is True
    assert evt["payload"] == {"branch": "main"}
    assert evt["source_kind"] == "gateway_channel"

    # A manually started run has no trigger event, which is how the UI tells a
    # human-started run from an event-started one.
    assert event_sources.trigger_event_for_run("run-never-triggered") is None


# ── CRUD ───────────────────────────────────────────────────


def test_create_event_source_validates_kind_and_name():
    assert event_sources.create_event_source("", "gateway_channel")["status"] == "error"
    bad = event_sources.create_event_source("x", "carrier_pigeon")
    assert bad["status"] == "error"
    assert "kind must be one of" in bad["error"]


def test_create_trigger_rejects_unknown_source():
    result = event_sources.create_workflow_trigger("wf-1", "src-does-not-exist")
    assert result["status"] == "error"
    assert "Unknown event source" in result["error"]


def test_trigger_crud_roundtrip():
    source_id = _source()
    trigger_id = _trigger(source_id, event_type="push")

    assert [t["trigger_id"] for t in event_sources.list_workflow_triggers(source_id=source_id)] == [
        trigger_id
    ]

    event_sources.update_workflow_trigger(
        trigger_id,
        event_type="pr.merged",
        event_filter=[{"field": "branch", "operator": "equals", "value": "main"}],
        input_mapping={"branch": "event.payload.branch"},
    )
    updated = event_sources.get_workflow_trigger(trigger_id)
    assert updated["event_type"] == "pr.merged"
    assert updated["filter"][0]["field"] == "branch"
    assert updated["input_mapping"] == {"branch": "event.payload.branch"}

    assert event_sources.toggle_workflow_trigger(trigger_id, False)["enabled"] is False
    assert event_sources.get_workflow_trigger(trigger_id)["enabled"] is False
    assert event_sources.list_workflow_triggers(source_id=source_id, enabled_only=True) == []

    assert event_sources.delete_workflow_trigger(trigger_id)["deleted"] is True
    assert event_sources.get_workflow_trigger(trigger_id) is None


def test_event_source_config_is_parsed_in_python():
    """JSON columns are read raw and parsed with json.loads — never with SQL
    JSON functions, which are not portable between PostgreSQL and SQLite."""
    source_id = _source(sample_payload={"branch": "main"})
    source = next(s for s in event_sources.list_event_sources() if s["source_id"] == source_id)
    assert source["config"]["sample_payload"] == {"branch": "main"}


def test_simulate_starts_nothing_and_logs_nothing(fake_start_run):
    source_id = _source(sample_payload={"branch": "main"})
    trigger_id = _trigger(
        source_id,
        filt=[{"field": "branch", "operator": "equals", "value": "main"}],
        mapping={"branch": "event.payload.branch"},
    )

    result = event_sources.simulate_workflow_trigger(trigger_id)
    assert result["would_fire"] is True
    assert result["dry_run"] is True
    assert result["resolved_inputs"] == {"branch": "main"}
    assert fake_start_run == []
    assert _events_for(source_id) == []


def test_trigger_events_are_never_updated():
    """The table is registered in APPEND_ONLY_TABLES; this pins that the module
    itself only ever inserts into it."""
    source = event_sources.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    assert "UPDATE studio_trigger_events" not in body
    assert "DELETE FROM studio_trigger_events" not in body


def test_registered_as_append_only():
    from tools.studio.init_db import APPEND_ONLY_TABLES

    assert "studio_trigger_events" in APPEND_ONLY_TABLES

    hook = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
    assert '"studio_trigger_events"' in hook.read_text(encoding="utf-8")


def test_log_trigger_event_persists_payload_json():
    source_id = _source()
    event_id = event_sources.log_trigger_event(
        source_id, None, "push", {"branch": "main"}, matched=False, reason="test"
    )
    assert event_id.startswith("evt-")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json, matched, run_id FROM studio_trigger_events "
            "WHERE event_id = %s",
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["payload_json"]) == {"branch": "main"}
    assert not row["matched"]
    assert row["run_id"] is None
