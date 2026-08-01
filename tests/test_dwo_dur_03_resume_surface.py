"""dwo-dur-03 — resume surface: retries, resume API, resume UI control.

The 18 tests from dwo-dur-01/02 cover the resume *mechanics* (replay, gate
re-attachment, single-owner guard). These cover the surface built on top of
them: per-step retries honoured from the template YAML, the resume endpoint,
and the Studio run-detail control.
"""
# CUI // SP-CTI

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from flask import Flask

runner = importlib.import_module("tools.studio.workflow_runner")

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "tools" / "dashboard" / "static" / "js" / "workflow-studio-exec.js"


# ── Retry policy ───────────────────────────────────────────

def test_retry_policy_defaults_to_no_retry():
    """A step declaring nothing must behave exactly as it did before."""
    assert runner._retry_policy({"id": "s1"}) == (0, 0.0)


def test_retry_policy_reads_template_values():
    assert runner._retry_policy(
        {"id": "s1", "retries": 3, "retry_backoff_seconds": 2.5}
    ) == (3, 2.5)


def test_retry_policy_caps_backoff():
    _, backoff = runner._retry_policy({"id": "s1", "retry_backoff_seconds": 86400})
    assert backoff == runner._MAX_RETRY_BACKOFF


@pytest.mark.parametrize("bad", ["nope", None, -4, {}])
def test_retry_policy_degrades_on_garbage(bad):
    retries, backoff = runner._retry_policy(
        {"id": "s1", "retries": bad, "retry_backoff_seconds": bad}
    )
    assert retries == 0
    assert backoff == 0.0


# ── Retry execution ────────────────────────────────────────

def _fake_exec(statuses, calls):
    """_exec_step stub returning `statuses` in order, recording each call."""
    def _inner(step, project_id, run_id=""):
        calls.append(step["id"])
        return {"step_id": step["id"], "status": statuses[len(calls) - 1],
                "stdout": None, "stderr": None, "exit_code": 1, "duration_ms": 1}
    return _inner


def test_no_retries_means_exactly_one_attempt(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec(["failed"], calls))
    result = runner._exec_step_with_retries({"id": "s1"}, "default")
    assert len(calls) == 1
    assert result["status"] == "failed"
    assert "attempts" not in result  # payload unchanged for unretried steps


def test_failed_step_is_retried_until_it_succeeds(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec(["failed", "failed", "success"], calls))
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    result = runner._exec_step_with_retries(
        {"id": "s1", "retries": 2, "retry_backoff_seconds": 5}, "default"
    )
    assert len(calls) == 3
    assert result["status"] == "success"
    assert result["attempts"] == 3


def test_retries_are_bounded(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec(["failed"] * 5, calls))
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    result = runner._exec_step_with_retries({"id": "s1", "retries": 2}, "default")
    assert len(calls) == 3  # first attempt + 2 retries, no more
    assert result["status"] == "failed"


def test_timeout_is_retried(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec(["timeout", "success"], calls))
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    assert runner._exec_step_with_retries({"id": "s1", "retries": 1}, "default")["status"] == "success"
    assert len(calls) == 2


@pytest.mark.parametrize("status", ["skipped", "awaiting_approval", "success"])
def test_non_retryable_outcomes_are_not_retried(monkeypatch, status):
    """A skipped step has nothing to retry; a gate is decided by a person."""
    calls: list[str] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec([status] * 4, calls))
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    runner._exec_step_with_retries({"id": "s1", "retries": 3}, "default")
    assert len(calls) == 1


def test_backoff_is_linear_and_capped(monkeypatch):
    calls: list[str] = []
    slept: list[float] = []
    monkeypatch.setattr(runner, "_exec_step", _fake_exec(["failed"] * 4, calls))
    monkeypatch.setattr(runner.time, "sleep", lambda s: slept.append(s))
    runner._exec_step_with_retries(
        {"id": "s1", "retries": 3, "retry_backoff_seconds": 2}, "default"
    )
    assert slept == [2, 4, 6]


def test_worker_executes_fresh_steps_through_the_retry_wrapper():
    """The retry policy is dead code unless _worker calls the wrapper."""
    src = (_ROOT / "tools" / "studio" / "workflow_runner.py").read_text(encoding="utf-8")
    worker = src.split("def _worker(", 1)[1].split("\ndef ", 1)[0]
    assert "_exec_step_with_retries(step, project_id, run_id)" in worker


# ── Resume contract ────────────────────────────────────────

def test_resume_mode_is_in_place():
    """The documented decision: continue the original run, do not fork one."""
    assert runner.RESUME_MODE == "in_place"


def test_failed_runs_are_resumable():
    assert "failed" in runner.RESUMABLE_RUN_STATUSES


def test_terminal_runs_are_not_resumable():
    for status in ("success", "cancelled"):
        assert status not in runner.RESUMABLE_RUN_STATUSES


def test_resume_run_refuses_a_terminal_run(monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda _rid: {"run_id": "r1", "status": "success"})
    assert runner.resume_run("r1") is False


def test_resume_run_refuses_a_missing_run(monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda _rid: None)
    assert runner.resume_run("nope") is False


def test_resume_decision_is_documented():
    doc = _ROOT / "docs" / "features" / "dwo-durable-workflow-orchestration.md"
    text = doc.read_text(encoding="utf-8")
    assert "resumed_from_run_id" in text
    assert "in_place" in text


# ── Resume API ─────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    from tools.dashboard.api.studio import studio_api

    app = Flask(__name__)
    app.register_blueprint(studio_api)
    return app.test_client()


def test_resume_endpoint_starts_a_worker(client, monkeypatch):
    resumed: list[str] = []
    monkeypatch.setattr(runner, "get_run", lambda rid: {"run_id": rid, "status": "failed"})
    monkeypatch.setattr(runner, "resume_run", lambda rid: resumed.append(rid) or True)

    resp = client.post("/api/studio/runs/run-abc/resume", json={})
    assert resp.status_code == 202
    assert resp.get_json() == {"status": "resuming", "run_id": "run-abc", "mode": "in_place"}
    assert resumed == ["run-abc"]


def test_resume_endpoint_alias_under_workflows(client, monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda rid: {"run_id": rid, "status": "awaiting_approval"})
    monkeypatch.setattr(runner, "resume_run", lambda _rid: True)
    assert client.post("/api/studio/workflows/runs/run-abc/resume", json={}).status_code == 202


def test_resume_endpoint_404s_on_unknown_run(client, monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda _rid: None)
    resp = client.post("/api/studio/runs/nope/resume", json={})
    assert resp.status_code == 404


def test_resume_endpoint_409s_on_a_finished_run(client, monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda rid: {"run_id": rid, "status": "success"})
    monkeypatch.setattr(runner, "resume_run", lambda _rid: pytest.fail("must not be called"))
    resp = client.post("/api/studio/runs/run-abc/resume", json={})
    assert resp.status_code == 409
    assert "success" in resp.get_json()["error"]


def test_resume_endpoint_409s_when_a_live_worker_owns_the_run(client, monkeypatch):
    monkeypatch.setattr(runner, "get_run", lambda rid: {"run_id": rid, "status": "running"})
    monkeypatch.setattr(runner, "resume_run", lambda _rid: False)
    assert client.post("/api/studio/runs/run-abc/resume", json={}).status_code == 409


# ── Resume UI ──────────────────────────────────────────────

def test_run_detail_renders_a_resume_control():
    js = _JS.read_text(encoding="utf-8")
    assert "_resumeControl(runId, run.status)" in js
    assert "StudioWF.resumeRun" in js
    assert "/api/studio/runs/${encodeURIComponent(runId)}/resume" in js


def test_ui_resumable_statuses_match_the_python_constant():
    """A UI that offers Resume on a status the API rejects is a 409 generator."""
    js = _JS.read_text(encoding="utf-8")
    match = re.search(r"_RESUMABLE_RUN_STATUSES\s*=\s*\[([^\]]*)\]", js)
    assert match, "UI status list not found"
    ui = tuple(s.strip().strip("'\"") for s in match.group(1).split(",") if s.strip())
    assert ui == runner.RESUMABLE_RUN_STATUSES
