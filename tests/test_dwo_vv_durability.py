# CUI // SP-CTI
"""dwo-vv-02 — durability claims that only a separate process can prove.

`tests/test_dwo_gate_durability.py` already covers the reconciler's branches and
an in-process restart simulation (clear the module-level dicts, reconcile,
approve). That simulation cannot falsify the claim it is standing in for: the
approving code and the parked worker share an interpreter, so a gate that still
lived in `_approval_events` would pass it.

This file covers the three properties that require real process boundaries or a
row-level before/after comparison:

  * a parked gate is visible and decidable from a genuinely separate process;
  * importing the runner in a cold process writes nothing to the store;
  * resuming a run replays its satisfied steps without rewriting their rows and
    without forking a second run (`RESUME_MODE == "in_place"`).

Everything runs against a throwaway SQLite database pointed at by
``ICDEV_DB_PATH`` so the subprocesses join the same store the test seeded and
nothing touches the developer's ``data/icdev.db``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.studio import workflow_runner as wr
from tools.studio.init_db import init_studio_tables

_ROOT = Path(__file__).resolve().parents[1]

# step_1 is already done, step_2 is the human gate, step_3 is what must still
# run after the interruption. Empty `tool` keeps the steps free of subprocesses
# so the test measures the gate, not an executor.
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

# _await_gate polls the database on a 10s cycle when no in-process Event fires,
# so a cross-process decision needs a window wider than one cycle.
_CROSS_PROCESS_TIMEOUT = 45.0


@pytest.fixture()
def studio_db(tmp_path, monkeypatch):
    """A throwaway store both this process and its subprocesses can reach."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    init_studio_tables()
    return db_path


@pytest.fixture(autouse=True)
def _cold_process_state():
    """Drop the runner's in-process gate state before and after every test.

    Without this a gate left armed by one test could release another test's
    run through the fast path, which is exactly the confusion these tests
    exist to rule out.
    """
    def _clear() -> None:
        wr._approval_events.clear()
        wr._approval_results.clear()
        wr._approval_reasons.clear()
        with wr._run_queues_lock:
            wr._run_queues.clear()

    _clear()
    yield
    _clear()


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat()


def _seed_parked_run(*, started_at: str | None = None) -> tuple[str, str, str]:
    """Create a run parked on step_2's gate.

    Returns ``(run_id, gate_step_run_id, done_step_run_id)`` — the last being
    step_1's already-successful row, which resume must leave alone.
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    gate_id = f"sr-{uuid.uuid4().hex[:12]}"
    done_id = f"sr-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) VALUES (%s,%s,%s)",
            (workflow_id, "NDC Deploy", _TEMPLATE),
        )
        conn.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, project_id) "
            "VALUES (%s,%s,%s,'awaiting_approval','default')",
            (run_id, workflow_id, "NDC Deploy"),
        )
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, started_at, "
            " completed_at, stdout, exit_code, duration_ms) "
            "VALUES (%s,%s,'step_1','Prepare','','success',%s,%s,%s,0,1234)",
            (done_id, run_id, _now(-1), _now(-1),
             json.dumps({"artifacts": [{"name": "main.tf", "path": "p", "type": "tf"}]})),
        )
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, started_at) "
            "VALUES (%s,%s,'step_2','Deploy Approval','','awaiting_approval',%s)",
            (gate_id, run_id, started_at or _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id, gate_id, done_id


def _step_row(step_run_id: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT step_run_id, run_id, step_id, step_name, status, started_at, "
            "completed_at, stdout, exit_code, duration_ms "
            "FROM studio_workflow_run_steps WHERE step_run_id=%s", (step_run_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _run_status(run_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM studio_workflow_runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        return row["status"] if row else ""
    finally:
        conn.close()


def _rows_for_step(run_id: str, step_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT step_run_id, status FROM studio_workflow_run_steps "
            "WHERE run_id=%s AND step_id=%s", (run_id, step_id)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _subprocess(code: str, db_path: Path) -> subprocess.CompletedProcess:
    """Run ``code`` in a cold interpreter joined to the same store."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(_ROOT), env=env, timeout=180,
    )


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


# ── The gate is state in the store, not state in a process ─────────────────

def test_a_parked_gate_is_visible_from_a_cold_process(studio_db):
    """`get_pending_approvals()` must answer from the database.

    The gate was parked by this process; a second interpreter that has never
    seen `_approval_events` must still find it.
    """
    _, gate_id, _ = _seed_parked_run()

    proc = _subprocess(
        "import json\n"
        "from tools.studio.workflow_runner import get_pending_approvals\n"
        "print(json.dumps(get_pending_approvals()))\n",
        studio_db,
    )

    assert proc.returncode == 0, proc.stderr
    assert gate_id in json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_cold_process_can_decide_a_gate_it_never_parked(studio_db):
    """Approval is a database transition, so any process may perform it."""
    _, gate_id, _ = _seed_parked_run()

    proc = _subprocess(
        "from tools.studio.workflow_runner import approve_step\n"
        f"print(approve_step({gate_id!r}, actor='other-process'))\n",
        studio_db,
    )

    assert proc.returncode == 0, proc.stderr
    assert _step_row(gate_id)["status"] == "approved"
    assert gate_id not in wr.get_pending_approvals()


def test_importing_the_runner_in_a_cold_process_writes_nothing(studio_db):
    """The durability defect was import-time state mutation.

    The in-process reload test cannot see a module that was already imported;
    a fresh interpreter executes the module body for real.
    """
    run_id, gate_id, done_id = _seed_parked_run()
    before = (_run_status(run_id), _step_row(gate_id), _step_row(done_id))

    proc = _subprocess("import tools.studio.workflow_runner  # noqa: F401\n", studio_db)

    assert proc.returncode == 0, proc.stderr
    assert (_run_status(run_id), _step_row(gate_id), _step_row(done_id)) == before


# ── Cross-process approval releases the run ────────────────────────────────

def test_approval_from_a_separate_process_releases_the_run(studio_db):
    """The headline durability claim, with a real process boundary.

    A worker in this process parks on the gate; a different interpreter — the
    dashboard, the Telegram listener, the CLI — approves it; the parked run
    must notice and finish. Nothing in `_approval_events` can shortcut this:
    the deciding process has no access to it.
    """
    run_id, gate_id, _ = _seed_parked_run(started_at=_now())

    assert wr.resume_run(run_id) is True
    assert _wait_until(lambda: gate_id in wr._approval_events, 20.0), "gate never armed"

    proc = _subprocess(
        "from tools.studio.workflow_runner import approve_step\n"
        f"approve_step({gate_id!r}, actor='cross-process')\n",
        studio_db,
    )
    assert proc.returncode == 0, proc.stderr

    assert _wait_until(
        lambda: _run_status(run_id) in ("success", "failed"), _CROSS_PROCESS_TIMEOUT
    ), "the parked run never observed the out-of-process decision"

    assert _run_status(run_id) == "success"
    assert _step_row(gate_id)["status"] == "approved"
    # step_3 sat behind the gate and had never been recorded before the resume.
    assert _rows_for_step(run_id, "step_3"), "the run did not continue past the gate"


# ── Resume replays; it does not rewrite ────────────────────────────────────

def test_resume_leaves_a_satisfied_step_row_untouched(studio_db):
    """A step already recorded success is replayed from its row, not re-run.

    `studio_workflow_run_steps` is the audit trail: resume must not restate an
    outcome that already happened, and must not append a second row claiming
    the step ran twice.
    """
    run_id, gate_id, done_id = _seed_parked_run(started_at=_now())
    before = _step_row(done_id)

    assert wr.resume_run(run_id) is True
    assert _wait_until(lambda: gate_id in wr._approval_events, 20.0), "gate never armed"
    assert wr.approve_step(gate_id, actor="tester") is True
    assert _wait_until(lambda: _run_status(run_id) in ("success", "failed"), 30.0)

    assert _step_row(done_id) == before, "resume rewrote a completed step's record"
    assert [r["step_run_id"] for r in _rows_for_step(run_id, "step_1")] == [done_id], (
        "resume appended a second row for a step that had already succeeded"
    )


def test_resume_continues_the_original_run_rather_than_forking_one(studio_db):
    """`RESUME_MODE == "in_place"`: no second run row, same run_id.

    Forking would strand every approval issued before the interruption against
    a run nobody is executing — the gate rows are keyed to the original run_id.
    """
    run_id, gate_id, _ = _seed_parked_run(started_at=_now())

    conn = get_connection()
    try:
        workflow_id = conn.execute(
            "SELECT workflow_id FROM studio_workflow_runs WHERE run_id=%s", (run_id,)
        ).fetchone()["workflow_id"]
    finally:
        conn.close()

    assert wr.resume_run(run_id) is True
    assert _wait_until(lambda: gate_id in wr._approval_events, 20.0), "gate never armed"
    assert wr.approve_step(gate_id, actor="tester") is True
    assert _wait_until(lambda: _run_status(run_id) in ("success", "failed"), 30.0)

    conn = get_connection()
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT run_id FROM studio_workflow_runs WHERE workflow_id=%s", (workflow_id,)
        ).fetchall()]
    finally:
        conn.close()

    assert wr.RESUME_MODE == "in_place"
    assert [r["run_id"] for r in runs] == [run_id]


def test_a_gate_decided_before_the_resume_is_honoured_not_reasked(studio_db):
    """An approval issued while no worker was running must survive.

    This is why the parked step keeps its step_run_id across the interruption:
    the decision is already in the row the resumed worker re-attaches to.
    """
    run_id, gate_id, _ = _seed_parked_run(started_at=_now())

    # Decided by another process while nothing was executing the run.
    proc = _subprocess(
        "from tools.studio.workflow_runner import approve_step\n"
        f"approve_step({gate_id!r}, actor='offline-approver')\n",
        studio_db,
    )
    assert proc.returncode == 0, proc.stderr
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_runs SET status='awaiting_approval' WHERE run_id=%s",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    assert wr.resume_run(run_id) is True
    assert _wait_until(lambda: _run_status(run_id) in ("success", "failed"), 30.0)

    assert _run_status(run_id) == "success"
    assert _step_row(gate_id)["status"] == "approved"
    assert _rows_for_step(run_id, "step_3"), "the honoured decision did not release the run"
