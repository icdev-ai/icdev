# CUI // SP-CTI
"""Integration cover for the MCP human approval gate (dwo-mcp-02-d4).

A `requires_approval` tool must park a pending `node_type: human` gate and
block, dispatch once that gate is approved, and refuse once it is rejected.
The gate is driven through the *existing* HITL API
(`workflow_runner.approve_step` / `reject_step` / `get_pending_approvals`) —
if these tests ever need a d4-specific approval call, the "reuse the HITL
infrastructure, no new flag" requirement has been broken.

`terraform_apply` is the acceptance tool named on the card. Its real handler is
stubbed out: this is about the gate, not about mutating cloud infrastructure.
"""
from __future__ import annotations

import sys
import threading
import types
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import workflow_runner as wr
from tools.studio.executors import mcp_executor
from tools.studio.init_db import init_studio_tables

_TOOL = "terraform_apply"


@pytest.fixture(autouse=True)
def _schema():
    init_studio_tables()


@pytest.fixture(autouse=True)
def _privileged_caller(monkeypatch):
    """Run every dispatch here as an IL5 admin.

    `terraform_apply` is declared IL5 / admin in the MCP registry since
    exa-policy-07 -- it mutates live cloud infrastructure -- and the IL and role
    checks run BEFORE the approval gate. Without this the executor would refuse
    at the wrong gate and these tests would silently stop covering the human
    gate at all. Set through the caller environment rather than threaded into
    seven `run()` calls, so the tests keep exercising the default caller path.
    """
    monkeypatch.setenv(mcp_executor.CALLER_IL_ENV[0], "IL5")
    monkeypatch.setenv(mcp_executor.CALLER_ROLES_ENV, "admin")


@pytest.fixture
def run_id() -> str:
    """A run row the gate can hang off, so the parked-run status is observable."""
    rid = f"run-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, project_id) "
            "VALUES (%s, %s, %s, 'running', 'default')",
            (rid, f"wf-{uuid.uuid4().hex[:12]}", "Deploy"),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


@pytest.fixture
def stub_apply(monkeypatch):
    """Replace terraform_apply's handler; record every call it receives."""
    calls: list[dict] = []
    mod = types.ModuleType("tests_mcp_approval_stub")
    mod.handle_apply = lambda args: calls.append(args) or {"applied": True}
    monkeypatch.setitem(sys.modules, "tests_mcp_approval_stub", mod)

    from tools.mcp.tool_registry import TOOL_REGISTRY

    monkeypatch.setitem(TOOL_REGISTRY, _TOOL, {
        "category": "testing",
        "module": "tests_mcp_approval_stub",
        "handler": "handle_apply",
        "description": "Stubbed terraform apply.",
        "input_schema": {"type": "object", "properties": {}},
    })
    return calls


def _gate_row(run_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT step_run_id, step_id, step_name, tool, status FROM "
            "studio_workflow_run_steps WHERE run_id = %s AND step_id = %s",
            (run_id, mcp_executor.approval_step_id(_TOOL)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _run_status(run_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM studio_workflow_runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        return row["status"] if row else ""
    finally:
        conn.close()


def _dispatch_in_thread(run_id: str, wait: float = 20.0) -> tuple[threading.Thread, dict]:
    """Start a dispatch that will block on the gate. Returns (thread, outcome)."""
    outcome: dict = {}

    def _target():
        try:
            outcome["payload"] = mcp_executor.run(
                _TOOL, {}, run_id, approval_wait=wait
            )
        except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread, outcome


def _wait_for_gate(run_id: str, timeout: float = 10.0) -> dict:
    """Block until the executor has parked its gate."""
    deadline = threading.Event()
    for _ in range(int(timeout / 0.05)):
        row = _gate_row(run_id)
        if row:
            return row
        deadline.wait(0.05)
    pytest.fail(f"executor never parked a gate for run {run_id}")


# ── The gate blocks ────────────────────────────────────────────────────────

def test_dispatch_parks_a_pending_human_node_and_blocks(run_id, stub_apply):
    thread, outcome = _dispatch_in_thread(run_id)
    try:
        gate = _wait_for_gate(run_id)

        assert gate["status"] == "awaiting_approval"
        # No tool path is what makes this a human node rather than a tool step —
        # the same shape workflow_runner writes for `node_type: human`.
        assert (gate["tool"] or "") == ""
        assert _TOOL in gate["step_name"]
        # Visible to the shared HITL surface, not to a d4-only lookup.
        assert gate["step_run_id"] in wr.get_pending_approvals()
        # The run reads as parked, not as running.
        assert _run_status(run_id) == "awaiting_approval"
        # And the handler has not been reached.
        assert stub_apply == [], "dispatched before the gate was approved"
        assert thread.is_alive(), "dispatch returned without waiting for a decision"
    finally:
        wr.reject_step(_gate_row(run_id)["step_run_id"], reason="test teardown")
        thread.join(timeout=15)


# ── Approval lets it through ───────────────────────────────────────────────

def test_dispatch_proceeds_after_the_gate_is_approved(run_id, stub_apply):
    thread, outcome = _dispatch_in_thread(run_id)
    gate = _wait_for_gate(run_id)

    assert wr.approve_step(gate["step_run_id"], actor="isso") is True
    thread.join(timeout=15)
    assert not thread.is_alive(), "dispatch stayed blocked after approval"

    assert "error" not in outcome, outcome.get("error")
    payload = outcome["payload"]
    assert payload["result"] == {"applied": True}
    assert payload["approval"]["step_run_id"] == gate["step_run_id"]
    assert payload["approval"]["status"] == "approved"
    assert "isso" in payload["approval"]["decision_note"]
    assert stub_apply == [{}], "handler should have run exactly once"
    assert _run_status(run_id) == "running"


# ── Denial refuses it ──────────────────────────────────────────────────────

def test_dispatch_is_refused_after_the_gate_is_rejected(run_id, stub_apply):
    thread, outcome = _dispatch_in_thread(run_id)
    gate = _wait_for_gate(run_id)

    assert wr.reject_step(gate["step_run_id"], reason="not in the change window") is True
    thread.join(timeout=15)
    assert not thread.is_alive(), "dispatch stayed blocked after rejection"

    exc = outcome.get("error")
    assert isinstance(exc, mcp_executor.MCPWorkflowGateError), outcome
    assert exc.reason == "mcp_tool_approval_rejected"
    assert exc.step_run_id == gate["step_run_id"]
    assert "not in the change window" in str(exc)
    assert stub_apply == [], "handler ran despite a rejected gate"


# ── Fail-closed edges ──────────────────────────────────────────────────────

def test_undecided_gate_refuses_but_stays_parked(run_id, stub_apply):
    """Nobody decides: the dispatch is refused and the gate survives for resume."""
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)

    assert exc.value.reason == "mcp_tool_awaiting_human_approval"
    assert stub_apply == []
    gate = _gate_row(run_id)
    assert gate["status"] == "awaiting_approval"
    assert exc.value.step_run_id == gate["step_run_id"]


def test_resumed_dispatch_reattaches_to_the_same_gate(run_id, stub_apply):
    """A second dispatch must not open a second gate beside the first."""
    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)
    first = _gate_row(run_id)["step_run_id"]

    wr.approve_step(first, actor="isso")
    payload = mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)

    assert payload["approval"]["step_run_id"] == first
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT step_run_id FROM studio_workflow_run_steps "
            "WHERE run_id = %s AND step_id = %s",
            (run_id, mcp_executor.approval_step_id(_TOOL)),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "resume opened a duplicate gate"


def test_dispatch_without_a_run_has_no_gate_to_park(stub_apply):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(_TOOL, {}, "")
    assert exc.value.reason == "mcp_tool_approval_gate_unavailable"
    assert stub_apply == []


def test_unreachable_gate_store_refuses_rather_than_dispatching(
    run_id, stub_apply, monkeypatch
):
    def _boom():
        raise RuntimeError("gate store down")

    monkeypatch.setattr(mcp_executor, "_gate_connection", _boom)
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)
    assert exc.value.reason == "mcp_tool_approval_gate_unavailable"
    assert stub_apply == []


def test_allowlisted_tool_needs_no_gate(run_id):
    """An `allowed` tool must not grow a human gate as a side effect of d4."""
    mcp_executor.run("health_check", {}, run_id, approval_wait=0)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT step_id FROM studio_workflow_run_steps WHERE run_id = %s "
            "AND step_id LIKE 'approval:%%'",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


# ── Wait-window resolution ─────────────────────────────────────────────────

def test_wait_window_prefers_env_then_policy_then_default(monkeypatch):
    monkeypatch.delenv(mcp_executor.APPROVAL_WAIT_ENV, raising=False)
    assert mcp_executor.approval_wait_seconds({}) == mcp_executor.DEFAULT_APPROVAL_WAIT
    assert mcp_executor.approval_wait_seconds({"approval_wait_seconds": 30}) == 30.0

    monkeypatch.setenv(mcp_executor.APPROVAL_WAIT_ENV, "5")
    assert mcp_executor.approval_wait_seconds({"approval_wait_seconds": 30}) == 5.0

    # A typo in an operational knob must not read as a policy refusal.
    monkeypatch.setenv(mcp_executor.APPROVAL_WAIT_ENV, "soon")
    assert mcp_executor.approval_wait_seconds({}) == mcp_executor.DEFAULT_APPROVAL_WAIT


# ── The park is ATOMIC (hgx-park-01) ───────────────────────────────────────
# `test_dispatch_parks_a_pending_human_node_and_blocks` was flaky on the Windows
# CI runner and nowhere else, failing as `assert 'running' == 'awaiting_approval'`.
# It was reporting a REAL race, not runner noise: the step row and the run row
# were written through two functions that each open their own connection and
# commit separately, so between the two commits the gate said awaiting_approval
# while the run still said running. The test polls for the gate (which lands
# first) and then reads the run, so a slow second commit surfaces exactly there.
#
# The window is not only a test problem. `get_pending_approvals` joins BOTH
# statuses, so a just-parked gate is invisible to the HITL surface until the
# second commit lands — and reordering the writes just moves the window to the
# other table.

def test_the_park_writes_both_rows_in_one_transaction():
    """Structural, because the race itself is timing-dependent and a functional
    test for it would be flaky in exactly the way this replaces.

    If someone splits the park back into `_update_step_record` +
    `_update_run_status`, the window returns and this fails."""
    import inspect

    src = inspect.getsource(wr._park_for_approval)
    assert src.count("get_connection()") == 1, "one connection, or it is not atomic"
    assert src.count("conn.commit()") == 1, "one commit, or the window returns"
    assert "studio_workflow_run_steps" in src and "studio_workflow_runs" in src


def test_the_park_site_uses_the_atomic_writer():
    """Pins the CALL, not just the helper's existence — a correct helper nobody
    calls is the defect this repo ships most."""
    import inspect

    src = inspect.getsource(wr)
    i = src.index('if result["status"] == "awaiting_approval":')
    window = src[i:i + 900]
    assert "_park_for_approval(" in window
    assert "_update_run_status(run_id, \"awaiting_approval\")" not in window


def test_parking_leaves_both_rows_consistent(run_id, stub_apply):
    """The invariant the flaky assertion was really asserting: an observer never
    sees the gate parked while the run still reads running."""
    thread, _outcome = _dispatch_in_thread(run_id)
    try:
        gate = _wait_for_gate(run_id)
        assert gate["status"] == "awaiting_approval"
        # Read the run IMMEDIATELY — no settling wait. That is the whole point:
        # with the writes in one transaction, the gate's visibility implies the
        # run's.
        assert _run_status(run_id) == "awaiting_approval"
    finally:
        wr.reject_step(_gate_row(run_id)["step_run_id"], reason="test teardown")
        thread.join(timeout=15)


# ── The MCP park is ATOMIC TOO (rem-hyg-19) ────────────────────────────────
# `test_parking_leaves_both_rows_consistent` above kept failing on the Windows
# runner AFTER hgx-park-01 — twice in ninety minutes, on two branches that
# touch nothing under tools/studio/**. It was right both times, and hgx-park-01
# had not closed its window: that card made `workflow_runner._park_for_approval`
# atomic, which is the park an AUTHORED `node_type: human` step takes. These
# tests drive `mcp_executor.run`, which parks through its OWN pair of writes —
# `open_approval_gate` committed the step row and `_set_run_status` then
# committed the run row on a SECOND connection. Same defect, second site, and
# the structural tests above could not see it because they read the other
# function's source.
#
# The lead recorded on the card (workflow_runner.py's `_update_run_status(
# run_id, "running")` at worker start) is not the cause: no test here starts a
# workflow worker, and the `run_id` fixture writes `running` itself.


def test_the_mcp_park_writes_both_rows_in_one_transaction():
    """Structural, for the same reason as its `_park_for_approval` twin: the
    race is timing-dependent, so a functional test for it alone would be flaky
    in exactly the way it replaces.

    If someone splits the park back into an `open_approval_gate` commit plus a
    `_set_run_status` commit, the window returns and this fails."""
    import inspect

    src = inspect.getsource(mcp_executor.open_approval_gate)
    assert src.count("_gate_connection()") == 1, "one connection, or it is not atomic"
    assert src.count("conn.commit()") == 1, "one commit, or the window returns"
    assert "studio_workflow_run_steps" in src and "studio_workflow_runs" in src


def test_the_mcp_park_site_does_not_re_park_on_a_second_connection():
    """Pins the CALL SITE, not just the helper — `await_approval` used to park
    the run itself, right after the gate row had already been committed."""
    import inspect

    src = inspect.getsource(mcp_executor.await_approval)
    assert '_set_run_status(run_id, "awaiting_approval")' not in src
    # The un-park stays: the gate is decided by then, so there is no pair.
    assert '_set_run_status(run_id, "running")' in src


def test_no_commit_publishes_the_gate_onto_a_still_running_run(
    run_id, stub_apply, monkeypatch
):
    """The deterministic form of `test_parking_leaves_both_rows_consistent`.

    That test can only catch the race if it reads in the window; this one reads
    at EVERY commit the park makes, through a separate connection — which is
    what another process sees at that instant. On the two-commit park the first
    observation is `('awaiting_approval', 'running')`, every time.
    """
    observations: list[tuple[str, str]] = []
    real_connection = mcp_executor._gate_connection

    class _ObservedConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def commit(self):
            self._conn.commit()
            observations.append(
                ((_gate_row(run_id) or {}).get("status", ""), _run_status(run_id))
            )

    monkeypatch.setattr(
        mcp_executor, "_gate_connection", lambda: _ObservedConnection(real_connection())
    )

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)

    published = [(g, r) for g, r in observations if g == "awaiting_approval"]
    assert published, f"the gate never became visible at all: {observations}"
    assert all(run == "awaiting_approval" for _gate, run in published), (
        f"a commit published the parked gate while the run still read running: "
        f"{observations}"
    )


def test_resuming_onto_an_existing_gate_re_parks_the_run(run_id, stub_apply):
    """The re-attach branch parks too, and still opens no second gate.

    `_set_run_status` used to cover this case for free because it ran after
    every undecided `open_approval_gate`. Folding the write into the
    transaction has to keep covering it, or a resumed run reads `running` while
    it is blocked on a human.
    """
    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)
    first = _gate_row(run_id)["step_run_id"]

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_runs SET status = 'running' WHERE run_id = %s",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(_TOOL, {}, run_id, approval_wait=0)

    assert _run_status(run_id) == "awaiting_approval"
    assert _gate_row(run_id)["step_run_id"] == first, "resume opened a second gate"
