# CUI // SP-CTI
"""DWO-DUR — Studio approval gates survive a process restart.

Regression cover for the durability defect: `_cleanup_orphaned_gates()` ran at
*import time* and force-failed every `awaiting_approval` run, so a workflow
parked on a human approver could not survive a dashboard restart or deploy.
See docs/spikes/dwo-00-superplane-workflow-adaptation.md (gap DUR).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.studio import workflow_runner as wr
from tools.studio.init_db import init_studio_tables

_TEMPLATE = """
canvas: ndc
steps:
  - id: step_1
    name: Prepare
    tool: ""
    node_type: tool
    depends_on: []
  - id: step_2
    name: Deploy Approval
    node_type: human
    role: approver
    depends_on: [step_1]
  - id: step_3
    name: Deploy
    tool: ""
    node_type: tool
    depends_on: [step_2]
"""


TERMINAL_RUN_STATUSES = ("success", "failed", "cancelled")


@pytest.fixture(autouse=True)
def _schema(tmp_path, monkeypatch):
    """One throwaway store per test, and no worker thread left behind.

    Both halves matter. `reconcile_runs_on_boot()` resumes *every* parked run it
    finds, so on a shared store it also re-attaches workers to the runs earlier
    tests in this file seeded and deliberately left parked. Those workers park on
    a gate nobody decides and poll the store on a 10s cycle for the rest of the
    session — long after the next test file has repointed `ICDEV_DB_PATH` at a
    database with no `studio_workflow_runs` in it. The resulting write fails on a
    background thread, and pytest attributes it to whichever test happens to be
    running, so the reported failure has no relation to its cause. That is the
    order-dependent flake this fixture removes.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    init_studio_tables()

    yield

    # Release every gate still parked so the workers this test started observe a
    # decision and exit while their store still exists. Rejecting once is not
    # enough: a worker that had not yet armed its gate rewrites the step row to
    # awaiting_approval afterwards and then waits out the whole approval window,
    # so keep deciding until the runs a worker owns have all finished.
    owned: list[str] = []
    deadline = wr.time.time() + 20
    while wr.time.time() < deadline:
        with wr._run_queues_lock:
            owned = list(wr._run_queues)
        owned = [r for r in owned if _run_status(r) not in TERMINAL_RUN_STATUSES]
        if not owned:
            break
        for step_run_id in wr.get_pending_approvals():
            wr.reject_step(step_run_id, reason="pytest teardown", actor="pytest")
        wr.time.sleep(0.1)
    assert not owned, f"worker threads outlived the test for runs: {owned}"

    wr._approval_events.clear()
    wr._approval_results.clear()
    wr._approval_reasons.clear()
    with wr._run_queues_lock:
        wr._run_queues.clear()


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


def _seed_parked_run(*, started_at: str | None = None, template: str = _TEMPLATE) -> tuple[str, str]:
    """Create a run parked on step_2's approval gate. Returns (run_id, step_run_id)."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    step_run_id = f"sr-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) VALUES (%s,%s,%s)",
            (workflow_id, "NDC Deploy", template),
        )
        conn.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, project_id) "
            "VALUES (%s,%s,%s,'awaiting_approval','default')",
            (run_id, workflow_id, "NDC Deploy"),
        )
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, started_at, stdout) "
            "VALUES (%s,%s,'step_1','Prepare','','success',%s,%s)",
            (f"sr-{uuid.uuid4().hex[:12]}", run_id, _now(-1),
             json.dumps({"artifacts": [{"name": "a", "path": "p", "type": "tf"}]})),
        )
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, started_at) "
            "VALUES (%s,%s,'step_2','Deploy Approval','','awaiting_approval',%s)",
            (step_run_id, run_id, started_at or _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id, step_run_id


def _run_status(run_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM studio_workflow_runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        return row["status"] if row else ""
    finally:
        conn.close()


def _step_status(step_run_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM studio_workflow_run_steps WHERE step_run_id=%s", (step_run_id,)
        ).fetchone()
        return row["status"] if row else ""
    finally:
        conn.close()


# ── The defect ─────────────────────────────────────────────────────────────

def test_importing_the_runner_writes_nothing():
    """The old module force-failed every parked run at import time."""
    run_id, step_run_id = _seed_parked_run()

    import importlib
    mod = importlib.import_module("tools.studio.workflow_runner")
    importlib.reload(mod)

    assert _run_status(run_id) == "awaiting_approval"
    assert _step_status(step_run_id) == "awaiting_approval"


def test_cleanup_orphaned_gates_is_gone():
    assert not hasattr(wr, "_cleanup_orphaned_gates")


def test_reconcile_keeps_a_live_gate_parked(monkeypatch):
    """A gate inside its approval window must survive the restart."""
    run_id, step_run_id = _seed_parked_run(started_at=_now())
    resumed: list[str] = []
    monkeypatch.setattr(wr, "resume_run", lambda rid: resumed.append(rid) or True)

    summary = wr.reconcile_runs_on_boot()

    assert run_id in summary["resumed"]
    assert run_id in resumed
    assert _run_status(run_id) == "awaiting_approval"
    assert _step_status(step_run_id) == "awaiting_approval"


def test_reconcile_expires_a_stale_gate():
    """Past its window, the gate is expired — not silently resurrected."""
    run_id, step_run_id = _seed_parked_run(started_at=_now(-48))

    summary = wr.reconcile_runs_on_boot()

    assert run_id in summary["expired"]
    assert _run_status(run_id) == "failed"
    assert _step_status(step_run_id) == "timeout"


def test_reconcile_fails_a_step_whose_process_died():
    """A 'running' step cannot be re-attached — its subprocess is gone."""
    run_id, _ = _seed_parked_run()
    orphan_id = f"sr-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, started_at) "
            "VALUES (%s,%s,'step_9','Long Job','','running',%s)",
            (orphan_id, run_id, _now(-1)),
        )
        conn.commit()
    finally:
        conn.close()

    summary = wr.reconcile_runs_on_boot()

    assert orphan_id in summary["orphaned_steps"]
    assert _step_status(orphan_id) == "failed"


# ── Gate deadlines are absolute, not per-process ────────────────────────────

def test_gate_deadline_is_anchored_to_started_at():
    """Elapsed time counts against the window across restarts."""
    step = {"approval_timeout": 3600}
    remaining = wr._gate_deadline(step, _now(-0.5)) - wr.time.time()
    assert 1500 < remaining < 1900  # ~30 min left of a 60 min window


def test_gate_deadline_expired_when_window_elapsed():
    assert wr._gate_deadline({"approval_timeout": 60}, _now(-2)) <= wr.time.time()


def test_gate_deadline_defaults_to_24h():
    remaining = wr._gate_deadline({}, _now()) - wr.time.time()
    assert 86000 < remaining <= 86400


def test_gate_deadline_survives_a_malformed_timestamp():
    """A bad started_at must degrade to a fresh window, not expire instantly."""
    remaining = wr._gate_deadline({"approval_timeout": 600}, "not-a-timestamp")
    assert remaining - wr.time.time() > 500


# ── Pending approvals are read from the database ────────────────────────────

def test_get_pending_approvals_reads_the_database():
    """Previously read the in-process _approval_events dict, which is empty
    after a restart even when gates are parked."""
    _, step_run_id = _seed_parked_run()
    wr._approval_events.clear()

    assert step_run_id in wr.get_pending_approvals()


def test_approve_step_releases_a_gate_parked_by_another_process():
    """No in-memory Event exists for a gate parked before the restart."""
    _, step_run_id = _seed_parked_run()
    wr._approval_events.clear()

    assert wr.approve_step(step_run_id, actor="tester") is True
    assert _step_status(step_run_id) == "approved"
    assert step_run_id not in wr.get_pending_approvals()


def test_reject_step_releases_a_gate_parked_by_another_process():
    _, step_run_id = _seed_parked_run()
    wr._approval_events.clear()

    assert wr.reject_step(step_run_id, reason="no", actor="tester") is True
    assert _step_status(step_run_id) == "rejected"


# ── Resume ─────────────────────────────────────────────────────────────────

def test_resume_run_refuses_a_finished_run():
    run_id, _ = _seed_parked_run()
    conn = get_connection()
    try:
        conn.execute("UPDATE studio_workflow_runs SET status='success' WHERE run_id=%s", (run_id,))
        conn.commit()
    finally:
        conn.close()

    assert wr.resume_run(run_id) is False


def test_resume_run_refuses_an_unknown_run():
    assert wr.resume_run("run-does-not-exist") is False


def test_resume_run_refuses_when_a_live_worker_owns_the_run():
    """Guards against two workers executing the same run concurrently."""
    run_id, _ = _seed_parked_run()
    with wr._run_queues_lock:
        wr._run_queues[run_id] = wr.queue.Queue()
    try:
        assert wr.resume_run(run_id) is False
    finally:
        with wr._run_queues_lock:
            wr._run_queues.pop(run_id, None)


def test_prior_steps_are_replayed_not_reexecuted():
    """A successful step must not run again on resume — terraform apply is not
    idempotent."""
    run_id, _ = _seed_parked_run()
    prior = wr._load_prior_steps(run_id)

    assert prior["step_1"]["status"] == "success"
    assert prior["step_2"]["status"] == "awaiting_approval"
    assert "step_3" not in prior


def test_resume_reattaches_the_existing_gate_id():
    """The parked step keeps its step_run_id so an approval issued before the
    restart still resolves it."""
    run_id, step_run_id = _seed_parked_run()
    prior = wr._load_prior_steps(run_id)

    assert prior["step_2"]["step_run_id"] == step_run_id


# ── End to end: the headline claim ─────────────────────────────────────────

def _wait_until(predicate, timeout: float = 20.0) -> bool:
    deadline = wr.time.time() + timeout
    while wr.time.time() < deadline:
        if predicate():
            return True
        wr.time.sleep(0.1)
    return False


def test_parked_run_survives_a_simulated_restart_and_completes():
    """The whole point of DUR: park a run on a human step, lose the process,
    reconcile, approve, and have the run finish.

    Before the fix, the restart alone was terminal — the run came back 'failed'
    and the step 'timeout'.
    """
    run_id, step_run_id = _seed_parked_run(started_at=_now())

    # Simulate a fresh process: no in-memory gates, no live run queues.
    wr._approval_events.clear()
    wr._approval_results.clear()
    wr._approval_reasons.clear()
    with wr._run_queues_lock:
        wr._run_queues.clear()

    summary = wr.reconcile_runs_on_boot()
    assert run_id in summary["resumed"], "reconciler did not re-attach the parked run"

    # The resumed worker must re-register the gate before we approve.
    assert _wait_until(lambda: step_run_id in wr._approval_events), "gate never re-armed"

    assert wr.approve_step(step_run_id, actor="tester") is True
    assert _wait_until(lambda: _run_status(run_id) in ("success", "failed"))

    assert _run_status(run_id) == "success"
    assert _step_status(step_run_id) == "approved"

    # step_3 was never reached before the restart; it must run now.
    final = wr._load_prior_steps(run_id)
    assert "step_3" in final, "the run did not continue past the gate"
