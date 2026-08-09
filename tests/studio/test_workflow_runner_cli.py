# CUI // SP-CTI
"""The headless run surface on workflow_runner — CLI + studio_run_* (hgx-cx-03).

Before this, a graph run could only be started from the dashboard: the runner
had no ``__main__`` block, and none of the four ``studio_*`` MCP tools started
or resumed anything. These tests pin the contract an agent or a cron job now
depends on — no DB, no LLM, air-gap safe: ``start_run``/``resume_run``/the two
row readers are stubbed, so what is under test is the CLI and the handlers, not
the execution engine underneath them.
"""
from __future__ import annotations

import json

import pytest

from tools.mcp import gap_handlers
from tools.studio import workflow_runner as wr


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def fake_runs(monkeypatch):
    """In-memory stand-ins for the run/step tables plus start_run/resume_run.

    Returns the state dict so a test can seed a run row and read back what the
    CLI asked the engine to do.
    """
    state = {
        "runs": {},        # run_id -> row
        "steps": {},       # run_id -> [rows]
        "started": [],     # (workflow_id, project_id, inputs)
        "resumed": [],     # run_id
        "resume_ok": True,
        "start_error": None,
    }

    def _start_run(workflow_id, project_id="default", inputs=None):
        if state["start_error"]:
            raise state["start_error"]
        state["started"].append((workflow_id, project_id, inputs))
        run_id = f"run-{len(state['started']):04d}"
        state["runs"][run_id] = {
            "run_id": run_id, "workflow_id": workflow_id,
            "workflow_name": "probe", "project_id": project_id,
            "status": "success", "started_at": "2026-08-09T00:00:00+00:00",
            "completed_at": "2026-08-09T00:00:01+00:00",
            "summary_json": json.dumps({"total": 1, "success": 1}),
        }
        state["steps"][run_id] = [{
            "step_run_id": "sr-1", "step_id": "probe", "step_name": "Probe",
            "tool": "tools/studio/executors/mcp_executor.py",
            "status": "success", "exit_code": 0, "duration_ms": 5,
        }]
        return run_id

    def _resume_run(run_id):
        state["resumed"].append(run_id)
        return state["resume_ok"]

    monkeypatch.setattr(wr, "start_run", _start_run)
    monkeypatch.setattr(wr, "resume_run", _resume_run)
    monkeypatch.setattr(wr, "get_run", lambda rid: state["runs"].get(rid))
    monkeypatch.setattr(wr, "get_run_steps", lambda rid: state["steps"].get(rid, []))
    return state


def _run_cli(argv, capsys):
    code = wr.main(argv)
    return code, capsys.readouterr().out


# ── --start ────────────────────────────────────────────────────────────────

def test_start_prints_the_run_id_and_exits_zero(fake_runs, capsys):
    """The card's acceptance criterion, exactly."""
    code, out = _run_cli(["--start", "wf-1", "--json"], capsys)
    payload = json.loads(out)
    assert code == wr.EXIT_OK
    assert payload["run_id"] == "run-0001"
    assert payload["run_status"] == "success"
    assert payload["steps"][0]["step_run_id"] == "sr-1"


def test_start_waits_by_default(fake_runs, capsys):
    """Waiting is the default because the worker dies with this process."""
    _, out = _run_cli(["--start", "wf-1", "--json"], capsys)
    assert json.loads(out)["waited"] is True
    _, out = _run_cli(["--start", "wf-1", "--no-wait", "--json"], capsys)
    assert json.loads(out)["waited"] is False


def test_absent_inputs_stay_null_not_empty_dict(fake_runs, capsys):
    """``None`` (no inputs recorded) must stay distinct from ``{}``."""
    _run_cli(["--start", "wf-1", "--json"], capsys)
    _run_cli(["--start", "wf-1", "--inputs", "{}", "--json"], capsys)
    _run_cli(["--start", "wf-1", "--inputs", '{"k": "v"}', "--json"], capsys)
    assert [call[2] for call in fake_runs["started"]] == [None, {}, {"k": "v"}]


def test_project_id_is_forwarded(fake_runs, capsys):
    _run_cli(["--start", "wf-1", "--project-id", "proj-9", "--json"], capsys)
    assert fake_runs["started"][0][1] == "proj-9"


@pytest.mark.parametrize("raw", ["[1, 2]", "not json", '"a string"'])
def test_malformed_inputs_are_refused_before_a_run_row_exists(fake_runs, capsys, raw):
    code, out = _run_cli(["--start", "wf-1", "--inputs", raw, "--json"], capsys)
    assert code == wr.EXIT_FAILED
    assert json.loads(out)["error_type"] == "invalid_request"
    assert fake_runs["started"] == [], "a bad payload must not start a run"


def test_unknown_workflow_reports_invalid_request(fake_runs, capsys):
    fake_runs["start_error"] = ValueError("Workflow not found: wf-nope")
    code, out = _run_cli(["--start", "wf-nope", "--json"], capsys)
    assert code == wr.EXIT_FAILED
    assert json.loads(out)["error_type"] == "invalid_request"


# ── exit codes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "run_status,expected",
    [
        ("success", wr.EXIT_OK),
        ("failed", wr.EXIT_FAILED),
        # Neither of these is a broken run, and a cron caller has to be able to
        # tell them apart from one — hence the third code.
        ("awaiting_approval", wr.EXIT_UNFINISHED),
        ("running", wr.EXIT_UNFINISHED),
        (None, wr.EXIT_UNFINISHED),
    ],
)
def test_exit_code_distinguishes_failed_from_unfinished(run_status, expected):
    assert wr._cli_exit_code(run_status) == expected


def test_a_parked_gate_is_settled_for_waiting_purposes():
    """No amount of waiting resolves a human decision."""
    assert "awaiting_approval" in wr._CLI_SETTLED_STATUSES
    assert "running" not in wr._CLI_SETTLED_STATUSES


def test_wait_returns_immediately_once_the_run_settles(fake_runs, monkeypatch):
    slept = []
    monkeypatch.setattr(wr.time, "sleep", lambda s: slept.append(s))
    fake_runs["runs"]["run-x"] = {"run_id": "run-x", "status": "success"}
    assert wr.wait_for_run("run-x", 30.0)["status"] == "success"
    assert slept == []


def test_wait_gives_up_at_the_timeout(fake_runs, monkeypatch):
    """A never-settling run must not hang the caller forever."""
    monkeypatch.setattr(wr.time, "sleep", lambda s: None)
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(wr.time, "monotonic", lambda: next(ticks))
    fake_runs["runs"]["run-x"] = {"run_id": "run-x", "status": "running"}
    assert wr.wait_for_run("run-x", 1.0)["status"] == "running"


# ── --status / --resume ────────────────────────────────────────────────────

def test_status_reports_a_known_run(fake_runs, capsys):
    _run_cli(["--start", "wf-1", "--json"], capsys)
    code, out = _run_cli(["--status", "run-0001", "--json"], capsys)
    assert code == wr.EXIT_OK
    assert json.loads(out)["run_status"] == "success"


def test_status_of_an_unknown_run_is_an_error_not_an_empty_report(fake_runs, capsys):
    code, out = _run_cli(["--status", "run-nope", "--json"], capsys)
    assert code == wr.EXIT_FAILED
    assert json.loads(out)["error_type"] == "run_not_found"


def test_resume_reattaches_and_reports(fake_runs, capsys):
    _run_cli(["--start", "wf-1", "--json"], capsys)
    code, out = _run_cli(["--resume", "run-0001", "--json"], capsys)
    assert code == wr.EXIT_OK
    assert fake_runs["resumed"] == ["run-0001"]
    assert json.loads(out)["run_id"] == "run-0001"


def test_resume_of_a_finished_run_is_refused(fake_runs, capsys):
    fake_runs["resume_ok"] = False
    code, out = _run_cli(["--resume", "run-0001", "--json"], capsys)
    assert code == wr.EXIT_FAILED
    assert json.loads(out)["error_type"] == "run_not_resumable"


def test_an_action_is_required(capsys):
    with pytest.raises(SystemExit):
        wr.main([])


def test_actions_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        wr.main(["--start", "wf-1", "--status", "run-1"])


# ── cross-backend row coercion ─────────────────────────────────────────────

def test_report_is_json_serializable_on_a_postgres_shaped_row(fake_runs):
    """PG hands back ``datetime`` where SQLite hands back ``str``.

    Without coercion the identical command would print on one backend and raise
    on the other — the report is what both the CLI and the MCP handlers emit.
    """
    from datetime import datetime, timezone

    fake_runs["runs"]["run-pg"] = {
        "run_id": "run-pg", "workflow_id": "wf-1", "workflow_name": "probe",
        "project_id": "default", "status": "success",
        "started_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "summary_json": {"total": 1},
    }
    fake_runs["steps"]["run-pg"] = []
    json.dumps(wr.run_report("run-pg"))  # must not raise


def test_unparseable_summary_is_kept_not_dropped(fake_runs):
    fake_runs["runs"]["run-x"] = {
        "run_id": "run-x", "status": "failed", "summary_json": "{not json",
    }
    fake_runs["steps"]["run-x"] = []
    assert wr.run_report("run-x")["summary"] == "{not json"


# ── MCP handlers ───────────────────────────────────────────────────────────

def test_run_start_handler_returns_the_report(fake_runs):
    out = gap_handlers.handle_studio_run_start({"workflow_id": "wf-1"})
    assert out["run_id"] == "run-0001"
    assert fake_runs["started"] == [("wf-1", "default", None)]


@pytest.mark.parametrize(
    "handler,args",
    [
        (gap_handlers.handle_studio_run_start, {}),
        (gap_handlers.handle_studio_run_status, {}),
        (gap_handlers.handle_studio_run_resume, {"run_id": "   "}),
    ],
)
def test_handlers_require_their_identifier(fake_runs, handler, args):
    assert "required" in handler(args)["error"]


def test_run_start_handler_rejects_non_object_inputs(fake_runs):
    out = gap_handlers.handle_studio_run_start({"workflow_id": "wf-1", "inputs": [1]})
    assert "JSON object" in out["error"]
    assert fake_runs["started"] == []


def test_run_resume_handler_names_the_resumable_statuses_on_refusal(fake_runs):
    fake_runs["resume_ok"] = False
    out = gap_handlers.handle_studio_run_resume({"run_id": "run-1"})
    assert out["resumable_statuses"] == list(wr.RESUMABLE_RUN_STATUSES)


@pytest.mark.parametrize(
    "requested,expected",
    [(None, 0.0), (0, 0.0), (-5, 0.0), ("bad", 0.0), (30, 30.0), (99999, 900.0)],
)
def test_wait_seconds_is_clamped(requested, expected):
    """A gate's 24h window must never hold an MCP call open that long."""
    assert gap_handlers._studio_wait_seconds({"wait_seconds": requested}) == expected


# ── registration ───────────────────────────────────────────────────────────

def test_the_three_tools_are_registered_and_declared():
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, TOOL_REGISTRY

    for name in ("studio_run_start", "studio_run_status", "studio_run_resume"):
        assert TOOL_REGISTRY[name]["category"] == "studio"
        assert name in READ_ONLY_DECLARATIONS
    # A mutating tool wrongly marked read-only lands in the agent loop's
    # parallel partition before any gate can object.
    assert READ_ONLY_DECLARATIONS["studio_run_status"] is True
    assert READ_ONLY_DECLARATIONS["studio_run_start"] is False
    assert READ_ONLY_DECLARATIONS["studio_run_resume"] is False


def test_starting_a_run_from_a_workflow_step_needs_a_human_gate():
    """MCP-WF-001: a step that spawns another run can recurse. Gate it."""
    from tools.studio.executors.mcp_executor import load_gate_policy

    policy = load_gate_policy()
    assert "studio_run_status" in policy["allowed"]
    for name in ("studio_run_start", "studio_run_resume"):
        assert name in policy["requires_approval"]
        assert name not in policy["allowed"]
