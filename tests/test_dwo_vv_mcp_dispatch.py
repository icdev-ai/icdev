# CUI // SP-CTI
"""dwo-vv-02 — the MCP executor's dispatch decisions.

What already exists elsewhere covers the *edges* of this surface and none of
its middle: `test_dwo_mcp_allowlist.py` asserts the shape of the policy in
`args/security_gates.yaml`, and `test_dwo_mcp_node_type.py` asserts that
`workflow_runner` builds the right command line for a `node_type: mcp` step.
Between the two sits the executor itself — the thing that decides whether a
registered tool actually runs — and nothing calls it.

This file calls it. Three properties, all of them refusals, because this
executor's job is to say no before a handler is imported:

  * parameters that violate the tool's schema are rejected, and the handler is
    never reached;
  * a tool the allowlist does not name is refused with a machine-readable
    reason, before the registry is touched, and the refusal is appended to the
    dispatch audit;
  * a `requires_approval` tool blocks on a human gate — the same
    `studio_workflow_run_steps` gate an authored `node_type: human` step parks
    on, decidable through `workflow_runner.approve_step`.

Dispatch is exercised against a stub module registered in `sys.modules` rather
than a real registry tool, so "did the handler run" is answerable without
running terraform.

SKIPPED ON `main`: `tools/studio/executors/mcp_executor.py` is delivered by
dwo-mcp-01/02 and, as of this commit, lives only on the unmerged branches
`kanban/dwo-mcp-01` … `kanban/dwo-mcp-02-d5-audit`.

These tests are not speculative. The file was run against
`origin/kanban/dwo-mcp-02-d5-audit` (at `4e31d9c47`) in a detached worktree:
all 30 tests pass, including the `@audited` block that d5 shipped without
tests of its own. The module-level skip below is therefore hiding working
tests from an incomplete checkout, not deferring unfinished ones. If it fails
after those branches merge, the executor changed — re-verify against the
branch before editing the assertions. See the dwo-vv-02 handoff note.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_EXECUTOR_PATH = _ROOT / "tools" / "studio" / "executors" / "mcp_executor.py"

try:
    from tools.studio.executors import mcp_executor
except ImportError:  # pragma: no cover - exercised only before dwo-mcp-01 merges
    pytest.skip(
        "tools.studio.executors.mcp_executor is not present in this checkout "
        "(dwo-mcp-01/02, unmerged). These tests activate when it lands.",
        allow_module_level=True,
    )

from tools.db.storage import get_connection  # noqa: E402
from tools.studio import workflow_runner as wr  # noqa: E402
from tools.studio.init_db import init_studio_tables  # noqa: E402

#: A tool the policy names in `allowed` — dispatches with no human gate.
ALLOWED_TOOL = "health_check"

#: A tool the policy names in `requires_approval` — dispatches only behind an
#: approved gate.
APPROVAL_TOOL = "terraform_apply"

#: A name no registry entry and no allowlist entry uses.
UNLISTED_TOOL = "dwo_vv_02_definitely_not_a_registered_tool"

_STUB_MODULE = "_dwo_vv_02_stub_tool"

_SCHEMA = {
    "type": "object",
    "properties": {"target": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["target"],
}


@pytest.fixture(autouse=True)
def studio_db(tmp_path, monkeypatch):
    """A throwaway store so a parked gate is this test's and nobody else's."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    init_studio_tables()
    wr._approval_events.clear()
    wr._approval_results.clear()
    wr._approval_reasons.clear()
    yield
    wr._approval_events.clear()


@pytest.fixture()
def handler_calls(monkeypatch):
    """Register a stub tool module and return the list of calls it received."""
    calls: list[dict] = []
    module = types.ModuleType(_STUB_MODULE)
    module.handler = lambda params: calls.append(dict(params)) or {"ok": True}
    monkeypatch.setitem(sys.modules, _STUB_MODULE, module)
    return calls


def _entry(schema: dict | None = None) -> dict:
    return {
        "module": _STUB_MODULE,
        "handler": "handler",
        "category": "test",
        "input_schema": schema or {},
    }


def _use_stub(monkeypatch, schema: dict | None = None) -> None:
    """Point registry lookups at the stub, leaving the gate logic untouched."""
    monkeypatch.setattr(mcp_executor, "resolve_entry", lambda tool: _entry(schema))


def _gate_rows(run_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT step_run_id, step_id, status FROM studio_workflow_run_steps "
            "WHERE run_id = %s", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Parameter schema validation ────────────────────────────────────────────

def test_validate_params_names_the_offending_field():
    violations = mcp_executor.validate_params({"count": 3}, _SCHEMA)

    assert violations, "a missing required property produced no violation"
    assert any("target" in v for v in violations)


def test_validate_params_flags_a_wrong_type():
    violations = mcp_executor.validate_params({"target": "x", "count": "many"}, _SCHEMA)

    assert any(v.startswith("count:") for v in violations)


def test_validate_params_accepts_a_conforming_object():
    assert mcp_executor.validate_params({"target": "x", "count": 3}, _SCHEMA) == []


def test_an_empty_schema_constrains_nothing():
    """Most registry entries declare no schema; they must stay dispatchable."""
    assert mcp_executor.validate_params({"anything": 1}, {}) == []


def test_bad_params_are_refused_before_the_handler_is_reached(monkeypatch, handler_calls):
    """Validation sits between lookup and dispatch, so a malformed step fails
    without side effects."""
    _use_stub(monkeypatch, _SCHEMA)

    with pytest.raises(ValueError) as exc:
        mcp_executor.run(ALLOWED_TOOL, {"count": 3})

    assert "target" in str(exc.value)
    assert handler_calls == [], "the handler ran despite invalid params"


def test_conforming_params_reach_the_handler(monkeypatch, handler_calls):
    _use_stub(monkeypatch, _SCHEMA)

    payload = mcp_executor.run(ALLOWED_TOOL, {"target": "prod", "count": 1})

    assert handler_calls == [{"target": "prod", "count": 1}]
    assert payload["result"] == {"ok": True}


@pytest.mark.parametrize("raw", ["[]", '"str"', "3", "not json"])
def test_params_that_are_not_a_json_object_are_rejected(raw):
    with pytest.raises(ValueError):
        mcp_executor.parse_params(raw)


# ── The allowlist is default-deny ──────────────────────────────────────────

def test_an_unlisted_tool_is_refused_before_the_registry_is_touched(monkeypatch):
    """A refused tool must never be imported — that is the point of gating
    first rather than dispatching and checking."""
    def _boom(tool):  # pragma: no cover - the assertion is that this never runs
        raise AssertionError(f"resolve_entry was called for a refused tool: {tool}")

    monkeypatch.setattr(mcp_executor, "resolve_entry", _boom)

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(UNLISTED_TOOL, {})

    assert exc.value.tool == UNLISTED_TOOL
    assert exc.value.reason == "mcp_tool_not_allowlisted"


def test_the_refusal_names_the_tool_and_the_policy():
    """The step's stdout is the only thing an operator sees, so the message has
    to be actionable on its own."""
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_tool_allowed(UNLISTED_TOOL)

    message = str(exc.value)
    assert UNLISTED_TOOL in message
    assert mcp_executor.GATE_POLICY_KEY in message


def test_dispositions_separate_unattended_from_gated_tools():
    assert mcp_executor.check_tool_allowed(ALLOWED_TOOL) == (
        mcp_executor.DISPOSITION_ALLOWED
    )
    assert mcp_executor.check_tool_allowed(APPROVAL_TOOL) == (
        mcp_executor.DISPOSITION_REQUIRES_APPROVAL
    )


def test_the_cli_reports_a_refusal_as_a_machine_readable_record(tmp_path):
    """The refusal record dwo-mcp-02-d5 audits: a single-line JSON object on
    stdout carrying `error_type`, and exit 1 so the step fails."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["ICDEV_DB_PATH"] = str(tmp_path / "icdev.db")

    proc = subprocess.run(
        [sys.executable, str(_EXECUTOR_PATH), "--tool", UNLISTED_TOOL, "--params", "{}"],
        capture_output=True, text=True, cwd=str(_ROOT), env=env, timeout=180,
    )

    assert proc.returncode == 1
    record = json.loads(proc.stdout.strip().splitlines()[-1])
    assert record["status"] == "failed"
    assert record["error_type"] == "mcp_tool_not_allowlisted"
    assert record["tool"] == UNLISTED_TOOL


# ── requires_approval blocks on a human gate ───────────────────────────────

def test_an_approval_tool_parks_a_gate_and_refuses_to_dispatch(monkeypatch, handler_calls):
    """Undecided means refused, not dispatched — and the gate stays parked so
    resuming the run picks the decision up."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    assert exc.value.reason == "mcp_tool_awaiting_human_approval"
    assert handler_calls == [], "a state-changing tool dispatched without approval"

    parked = [r for r in _gate_rows(run_id) if r["status"] == "awaiting_approval"]
    assert len(parked) == 1
    assert parked[0]["step_id"] == mcp_executor.approval_step_id(APPROVAL_TOOL)


def test_the_gate_is_the_shared_hitl_surface(monkeypatch):
    """No second approval vocabulary: the gate must show up in the same
    pending-approvals list the Details modal and Telegram listener read."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    gate_id = _gate_rows(run_id)[0]["step_run_id"]
    assert gate_id in wr.get_pending_approvals()


def test_an_approved_gate_releases_the_dispatch(monkeypatch, handler_calls):
    """The decision is read from the row, so the approver may be any process."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    gate_id = _gate_rows(run_id)[0]["step_run_id"]
    assert wr.approve_step(gate_id, actor="tester") is True

    payload = mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    assert len(handler_calls) == 1
    assert payload["approval"]["status"] == "approved"
    assert payload["approval"]["step_run_id"] == gate_id


def test_a_rejected_gate_refuses_the_dispatch(monkeypatch, handler_calls):
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    gate_id = _gate_rows(run_id)[0]["step_run_id"]
    assert wr.reject_step(gate_id, reason="not this window", actor="tester") is True

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    assert exc.value.reason == "mcp_tool_approval_rejected"
    assert handler_calls == []


def test_a_second_dispatch_reuses_the_gate_rather_than_opening_another(monkeypatch):
    """A resumed run must re-attach to the gate an approver was already shown."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    first = mcp_executor.open_approval_gate(run_id, APPROVAL_TOOL)
    second = mcp_executor.open_approval_gate(run_id, APPROVAL_TOOL)

    assert first["step_run_id"] == second["step_run_id"]
    assert len(_gate_rows(run_id)) == 1


def test_a_dispatch_with_no_run_has_nowhere_to_park_a_gate(monkeypatch, handler_calls):
    """Fail-closed: without a run there is no approver to ask, so refuse."""
    _use_stub(monkeypatch)

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(APPROVAL_TOOL, {}, run_id="", approval_wait=0)

    assert exc.value.reason == "mcp_tool_approval_gate_unavailable"
    assert handler_calls == []


def test_approval_is_the_last_check_before_dispatch(monkeypatch, handler_calls):
    """Nobody should be woken to approve a call the schema would have refused."""
    _use_stub(monkeypatch, _SCHEMA)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(ValueError):
        mcp_executor.run(APPROVAL_TOOL, {"count": 1}, run_id=run_id, approval_wait=0)

    assert _gate_rows(run_id) == [], "a gate was parked for a call that could not run"
    assert handler_calls == []


# ── Every attempt lands in the append-only audit (dwo-mcp-02-d5) ───────────
#
# d5 shipped without tests of its own. These skip if only d1-d4 has merged.

audited = pytest.mark.skipif(
    not hasattr(mcp_executor, "record_dispatch_audit"),
    reason="dispatch audit is dwo-mcp-02-d5; not in this checkout",
)


@audited
def test_a_refusal_is_audited_not_just_reported(monkeypatch):
    """The step's stderr is discarded when the run row is pruned. The refusal
    has to survive that."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(UNLISTED_TOOL, {}, run_id=run_id)

    rows = mcp_executor.query_dispatch_audit(run_id=run_id)
    assert len(rows) == 1, "an attempt produced other than exactly one audit row"
    assert rows[0]["decision"] == mcp_executor.DECISION_REFUSED
    assert rows[0]["reason"] == "mcp_tool_not_allowlisted"
    assert rows[0]["tool"] == UNLISTED_TOOL


@audited
def test_the_audit_reason_is_the_reason_the_cli_reports(monkeypatch):
    """One cause, named identically in the row and in the step's stdout, so an
    operator does not have to correlate two vocabularies."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(UNLISTED_TOOL, {}, run_id=run_id)

    assert mcp_executor.query_dispatch_audit(run_id=run_id)[0]["reason"] == exc.value.reason


@audited
def test_a_completed_dispatch_is_audited_as_allowed(monkeypatch, handler_calls):
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    mcp_executor.run(ALLOWED_TOOL, {"target": "prod"}, run_id=run_id)

    rows = mcp_executor.query_dispatch_audit(run_id=run_id)
    assert [r["decision"] for r in rows] == [mcp_executor.DECISION_ALLOWED]
    assert rows[0]["reason"] == mcp_executor.REASON_DISPATCHED


@audited
def test_a_parked_gate_is_audited_as_pending_rather_than_refused(monkeypatch):
    """A gate awaiting a decision is a live attempt. Recording it as a refusal
    would make an approval queue look like a wall of denials."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(APPROVAL_TOOL, {}, run_id=run_id, approval_wait=0)

    rows = mcp_executor.query_dispatch_audit(run_id=run_id)
    assert [r["decision"] for r in rows] == [mcp_executor.DECISION_PENDING_APPROVAL]


@audited
def test_the_audit_records_the_actor_not_only_the_tool(monkeypatch, handler_calls):
    """"Who dispatched this" is the question an audit exists to answer."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    caller = {
        "principal_id": "p-1", "tenant_id": "t-1", "impact_level": "IL5",
        "roles": ("operator",), "source": "argument",
    }

    mcp_executor.run(ALLOWED_TOOL, {}, run_id=run_id, caller=caller)

    row = mcp_executor.query_dispatch_audit(run_id=run_id)[0]
    assert row["principal_id"] == "p-1"
    assert row["tenant_id"] == "t-1"
    assert row["caller_il"] == "IL5"
    assert "operator" in row["caller_roles"]
    assert row["caller_source"] == "argument"


@audited
def test_the_audit_stores_a_digest_rather_than_the_parameters(monkeypatch, handler_calls):
    """Params carry CUI and credentials. The audit answers "were these the same
    arguments the approver saw" without widening the store's blast radius."""
    _use_stub(monkeypatch)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    params = {"target": "prod", "token": "s3cret-value"}

    mcp_executor.run(ALLOWED_TOOL, params, run_id=run_id)

    row = mcp_executor.query_dispatch_audit(run_id=run_id)[0]
    assert row["params_sha256"] == mcp_executor.params_digest(params)
    assert "s3cret-value" not in json.dumps(row, default=str)


@audited
def test_the_digest_ignores_key_order():
    assert mcp_executor.params_digest({"a": 1, "b": 2}) == (
        mcp_executor.params_digest({"b": 2, "a": 1})
    )


@audited
def test_an_unknown_decision_is_refused_rather_than_written():
    """The vocabulary is closed; a typo must not reach the CHECK constraint as
    a silent write failure."""
    with pytest.raises(ValueError):
        mcp_executor.record_dispatch_audit(ALLOWED_TOOL, {}, "approved", "typo")


@audited
def test_the_decision_vocabulary_matches_the_schema_constraint():
    """`init_db.py`'s CHECK mirrors `DECISIONS`; drift would turn a legitimate
    decision into a dropped audit row."""
    ddl = (_ROOT / "tools" / "studio" / "init_db.py").read_text(encoding="utf-8")
    declared = "CHECK(decision IN (" + ",".join(f"'{d}'" for d in mcp_executor.DECISIONS) + "))"

    assert declared in ddl.replace("\n", " ").replace("  ", " ")
