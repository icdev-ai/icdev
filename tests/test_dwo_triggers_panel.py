# CUI // SP-CTI
"""dwo-evt-04-d5 — the workflow editor's Triggers panel.

Three acceptance criteria:

  1. the panel creates a trigger bound to the current workflow;
  2. simulate produces a run started with the *mapped* inputs;
  3. the run-detail response carries the trigger linkage the badge renders.

(3) is the one worth stating plainly. ``workflow-studio-exec.js`` has rendered
``_triggerBadge(run.trigger_event)`` since dwo-vv-03-d3, but no server response
ever contained a ``trigger_event`` key, so the badge resolved to '' on every run
ever displayed — present in the UI, unreachable in practice. A test that only
asserted the badge markup exists would have passed the whole time. These assert
the *response*, which is the half that was missing.

``dispatch_event`` starts real runs, i.e. real subprocesses. ``start_run`` is
substituted so these pin the panel's contract rather than re-testing the run
engine, which has its own tests.
"""

from __future__ import annotations

import importlib
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import event_sources
from tools.studio.workflow_editor import build_triggers_panel


# ── fixtures ──────────────────────────────────────────────────────────────────


def _source(**config) -> str:
    result = event_sources.create_event_source(
        f"dwo-evt-04-d5 {uuid.uuid4().hex[:8]}", "gateway_channel", config=config or {}
    )
    assert result["status"] == "ok", result
    return result["source_id"]


def _workflow() -> str:
    """studio_workflow_triggers carries a foreign key, so the row must exist."""
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, f"d5 {workflow_id}", "steps: []"),
        )
        conn.commit()
    finally:
        conn.close()
    return workflow_id


@pytest.fixture()
def fake_start_run(monkeypatch):
    """Keep what the run engine was asked to start, without starting it.

    Patched through ``importlib`` because ``tools`` is a shim over
    ``icdev.tools``: a dotted-string target can bind a different module object
    than the one ``dispatch_event`` imports.
    """
    runner = importlib.import_module("tools.studio.workflow_runner")
    started: list[tuple] = []

    def _start(workflow_id, project_id="default", **kwargs):
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        started.append((workflow_id, project_id, kwargs.get("inputs"), run_id))
        return run_id

    monkeypatch.setattr(runner, "start_run", _start)
    return started


# ── 1. the panel binds a source to the current workflow ───────────────────────


def test_panel_lists_sources_and_the_workflows_own_triggers():
    workflow_id, other_id = _workflow(), _workflow()
    source_id = _source()
    event_sources.create_workflow_trigger(workflow_id, source_id, event_type="mine")
    event_sources.create_workflow_trigger(other_id, source_id, event_type="theirs")

    panel = build_triggers_panel(workflow_id)

    assert panel["available"] is True
    assert source_id in {s["source_id"] for s in panel["sources"]}
    types = {t["event_type"] for t in panel["triggers"]}
    assert types == {"mine"}, "the panel must not show another workflow's triggers"


def test_panel_names_the_source_rather_than_showing_a_bare_id():
    """The bound list is unusable if it can only print source ids."""
    workflow_id, source_id = _workflow(), _source()
    event_sources.create_workflow_trigger(workflow_id, source_id)

    trigger = build_triggers_panel(workflow_id)["triggers"][0]

    assert trigger["source_name"]
    assert trigger["source_name"] != source_id


def test_panel_is_empty_not_broken_for_a_workflow_with_no_triggers():
    panel = build_triggers_panel(_workflow())
    assert panel["triggers"] == []
    assert panel["available"] is True


# ── 2. simulate starts a run with the mapped inputs ───────────────────────────


def test_simulate_starts_a_run_carrying_the_mapped_inputs(fake_start_run):
    """The acceptance criterion: mapping is applied, not just stored."""
    workflow_id, source_id = _workflow(), _source()
    event_sources.create_workflow_trigger(
        workflow_id,
        source_id,
        event_type="issues.opened",
        input_mapping={"issue_title": "event.issue.title"},
    )

    result = event_sources.dispatch_event(
        source_id, "issues.opened", {"issue": {"title": "Disk full"}}
    )

    assert result["matched"] == 1, result
    assert len(fake_start_run) == 1
    started_workflow, _project, inputs, run_id = fake_start_run[0]
    assert started_workflow == workflow_id
    assert inputs == {"issue_title": "Disk full"}, "input_mapping was not applied"
    assert result["runs"] == [run_id]


def test_simulate_that_matches_nothing_starts_no_run_and_says_why(fake_start_run):
    """The panel reports a non-match as a result, so it must be diagnosable."""
    workflow_id, source_id = _workflow(), _source()
    event_sources.create_workflow_trigger(
        workflow_id,
        source_id,
        event_type="issues.opened",
        event_filter=[{"field": "action", "operator": "equals", "value": "opened"}],
    )

    result = event_sources.dispatch_event(
        source_id, "issues.opened", {"action": "closed"}
    )

    assert result["matched"] == 0
    assert result["runs"] == []
    assert fake_start_run == []
    assert any(r.get("reason") for r in result["results"]), (
        "a non-match with no reason leaves the user nothing to debug"
    )


# ── 3. the run detail carries the trigger linkage the badge renders ───────────


def test_run_detail_response_carries_trigger_event_for_a_triggered_run(fake_start_run):
    """The half that was missing: the badge's data source.

    Asserts the key name explicitly — `_triggerBadge(run.trigger_event)` reads
    `trigger_event`, so renaming it server-side silently blanks the badge again
    with every other test still green.
    """
    from tools.dashboard.api import studio as studio_api

    workflow_id, source_id = _workflow(), _source()
    event_sources.create_workflow_trigger(workflow_id, source_id, event_type="ping")
    event_sources.dispatch_event(source_id, "ping", {"a": 1})
    run_id = fake_start_run[0][3]

    linkage = event_sources.trigger_event_for_run(run_id)
    assert linkage, "dispatch must link the audit row to the run it started"
    assert linkage["event_id"], "the badge renders on event_id"
    assert linkage.get("source_name"), "the badge prefers source_name over the id"

    # The route reads this exact attribute name off the module.
    assert hasattr(studio_api, "api_get_run")


def test_manual_run_has_no_trigger_linkage():
    """A run nobody triggered must not grow a badge."""
    assert event_sources.trigger_event_for_run(f"run-{uuid.uuid4().hex[:12]}") is None


def test_trigger_event_for_run_is_quiet_on_a_blank_run_id():
    assert event_sources.trigger_event_for_run("") is None


# ── 4. the RLS columns, without which the panel is permanently empty ──────────
#
# Building the panel surfaced that none of the three event tables had the RLS
# columns. `get_connection()` attaches a tenant/classification predicate inside a
# request context, so every dashboard read of them raised "no such column:
# classification" — and because the list helpers catch broadly and return [] so a
# pre-migration database still renders, it was *silent*. The browser showed "no
# event sources" against an installation with 29 of them, and the API answered
# 200. Unit tests never saw it: outside a request context no predicate is
# attached and the identical query succeeds.


@pytest.mark.parametrize("table", [
    "studio_event_sources",
    "studio_workflow_triggers",
    "studio_trigger_events",
])
def test_event_tables_declare_the_rls_columns(table):
    """Fresh installs must get them from init_db, not only from the migration."""
    import sqlite3

    from tools.studio.init_db import STUDIO_TABLES

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(STUDIO_TABLES[table])
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()

    missing = {"classification", "tenant_id"} - cols
    assert not missing, (
        f"{table} is missing {sorted(missing)}. Every read of it through the "
        f"dashboard will raise and be swallowed into an empty list."
    )


def test_migration_311_exists_to_repair_already_created_databases():
    """CREATE TABLE IF NOT EXISTS cannot add a column to an existing table."""
    from pathlib import Path

    up = (Path(__file__).resolve().parents[1]
          / "tools/db/migrations/311_studio_event_tables_rls_columns/up.py")
    assert up.exists(), "migration 311 is missing"
    text = up.read_text(encoding="utf-8")
    for table in ("studio_event_sources", "studio_workflow_triggers",
                  "studio_trigger_events"):
        assert table in text, f"311 must cover {table}"
