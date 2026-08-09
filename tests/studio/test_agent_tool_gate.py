# CUI // SP-CTI
"""AGENT-WF-001 — authorization of agent-node tool calls (hgx-agent-02).

The card's three acceptance criteria, each asserted end-to-end:

  1. An agent node cannot call a tool outside its allowlist; the refusal is
     audited.
  2. A mutating tool in an agent node blocks until its human gate is approved.
  3. A caller whose IL is below the tool's declared ceiling is refused.

Two layers are covered because the gate is enforced twice: `authorize_toolset`
withholds an unauthorized tool before the model is told about it, and the hook
from `build_gate_hook` refuses the call even if something offered it anyway.
The second is the one that actually decides, so the refusal tests drive the hook
— that is the path a real call takes.

The human gate is driven through the EXISTING HITL API
(`workflow_runner.approve_step` / `reject_step` / `get_pending_approvals`). If
these tests ever need an agent-specific approval call, the "extend the existing
mechanism, do not build a parallel one" requirement has been broken.
"""
from __future__ import annotations

import importlib
import threading
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import workflow_runner as wr
from tools.studio.executors import agent_tool_gate as gate
from tools.studio.executors import mcp_executor
from tools.studio.init_db import init_studio_tables

_MUTATING = "write_file"
_READ_ONLY = "read_file"
_IL5_ONLY = "run_command"


@pytest.fixture(autouse=True)
def _schema():
    init_studio_tables()


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
            (rid, f"wf-{uuid.uuid4().hex[:12]}", "Agent build"),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


@pytest.fixture
def caller() -> dict:
    """An IL4 principal with an identity, so the audit actor fields are filled."""
    return {
        "principal_id": "p-agent",
        "tenant_id": "t-agent",
        "impact_level": "IL4",
        "roles": ("developer",),
        "source": "argument",
    }


@pytest.fixture
def il5_caller(caller) -> dict:
    return {**caller, "impact_level": "IL5"}


def _audit(run_id: str, tool: str = "") -> list[dict]:
    return mcp_executor.query_dispatch_audit(run_id=run_id, tool=tool)


def _gate_row(run_id: str, tool: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT step_run_id, step_id, step_name, tool, status FROM "
            "studio_workflow_run_steps WHERE run_id = %s AND step_id = %s",
            (run_id, gate.approval_step_id(tool)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _wait_for_gate(run_id: str, tool: str, timeout: float = 10.0) -> dict:
    waiter = threading.Event()
    for _ in range(int(timeout / 0.05)):
        row = _gate_row(run_id, tool)
        if row:
            return row
        waiter.wait(0.05)
    pytest.fail(f"the hook never parked a gate for {tool} in run {run_id}")


# ══════════════════════════════════════════════════════════════
# AC 1 — a tool outside the allowlist cannot be called, and the
#        refusal is audited
# ══════════════════════════════════════════════════════════════

def test_an_unallowlisted_tool_is_refused(caller):
    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.check_tool_allowed("curl_the_internet")
    assert exc.value.reason == gate.REASON_NOT_ALLOWLISTED


def test_the_hook_blocks_an_unallowlisted_call_and_audits_it(run_id, caller):
    hook = gate.build_gate_hook(caller=caller, run_id=run_id, step_id="build")

    message = hook("curl_the_internet", {"url": "http://example.test"})

    assert message is not None, "an unallowlisted tool was allowed to run"
    assert "BLOCKED" in message
    assert gate.REASON_NOT_ALLOWLISTED in message

    rows = _audit(run_id, "curl_the_internet")
    assert len(rows) == 1
    assert rows[0]["decision"] == "refused"
    assert rows[0]["reason"] == gate.REASON_NOT_ALLOWLISTED
    assert rows[0]["principal_id"] == caller["principal_id"]
    assert rows[0]["step_id"] == "build"
    # The digest, never the arguments themselves.
    assert rows[0]["params_sha256"]
    assert "example.test" not in str(rows[0])


def test_an_unauthorized_tool_is_withheld_from_the_model(run_id, caller):
    """Offer time: the model is never told about a tool it may not use."""
    authorized, refusals = gate.authorize_toolset(
        [_READ_ONLY, _IL5_ONLY, "curl_the_internet"],
        caller=caller,
        run_id=run_id,
        step_id="build",
    )

    assert set(authorized) == {_READ_ONLY}
    assert {r["tool"] for r in refusals} == {_IL5_ONLY, "curl_the_internet"}
    # Withheld, but still on the record: "the step wanted this and could not
    # have it" is what an operator needs when the output looks thin.
    reasons = {r["reason"] for r in _audit(run_id)}
    assert reasons == {gate.REASON_NOT_OFFERED}


def test_a_read_only_tool_is_allowed_and_audited(run_id, caller):
    hook = gate.build_gate_hook(caller=caller, run_id=run_id, step_id="build")

    assert hook(_READ_ONLY, {"path": "README.md"}) is None

    rows = _audit(run_id, _READ_ONLY)
    assert len(rows) == 1
    assert rows[0]["decision"] == "allowed"
    assert rows[0]["reason"] == gate.REASON_CALLED


def test_the_authorization_gate_runs_before_the_reversibility_gate(run_id, caller):
    """An unauthorized tool is refused without asking anyone about its arguments."""
    consulted: list[str] = []
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id,
        chain=lambda name, inp: consulted.append(name) or None,
    )

    assert hook("curl_the_internet", {}) is not None
    assert consulted == [], "the reversibility gate saw an unauthorized call"

    assert hook(_READ_ONLY, {"path": "README.md"}) is None
    assert consulted == [_READ_ONLY], "an authorized call skipped the second gate"


def test_a_chained_block_still_stops_an_authorized_call(run_id, caller):
    """Both gates must pass. Authorization is not a bypass of reversibility."""
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id,
        chain=lambda name, inp: "BLOCKED by the approval gate: nope",
    )
    assert hook(_READ_ONLY, {"path": "README.md"}) == (
        "BLOCKED by the approval gate: nope"
    )


# ══════════════════════════════════════════════════════════════
# AC 2 — a mutating tool blocks until its human gate is approved
# ══════════════════════════════════════════════════════════════

def test_a_mutating_tool_is_classified_as_requiring_approval():
    assert gate.check_tool_allowed(_MUTATING) == gate.DISPOSITION_REQUIRES_APPROVAL


def _call_in_thread(hook, tool: str, tool_input: dict) -> tuple[threading.Thread, dict]:
    outcome: dict = {}

    def _target():
        try:
            outcome["result"] = hook(tool, tool_input)
        except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread, outcome


def test_a_mutating_call_parks_a_human_gate_and_blocks(run_id, caller):
    reached: list[str] = []
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id, step_id="build", approval_wait=20.0,
        chain=lambda name, inp: reached.append(name) or None,
    )
    thread, _ = _call_in_thread(hook, _MUTATING, {"path": "a.py", "content": "x"})
    try:
        row = _wait_for_gate(run_id, _MUTATING)

        assert row["status"] == "awaiting_approval"
        # No tool path is what makes this a human node — the same shape
        # workflow_runner writes for an authored `node_type: human` step.
        assert (row["tool"] or "") == ""
        assert _MUTATING in row["step_name"]
        # Decidable from the shared HITL surface, not an agent-only lookup.
        assert row["step_run_id"] in wr.get_pending_approvals()
        assert reached == [], "the call proceeded before the gate was decided"
        assert thread.is_alive(), "the hook returned without waiting for a decision"
    finally:
        wr.reject_step(_gate_row(run_id, _MUTATING)["step_run_id"],
                       reason="test teardown")
        thread.join(timeout=15)


def test_a_mutating_call_proceeds_once_the_gate_is_approved(run_id, caller):
    reached: list[str] = []
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id, step_id="build", approval_wait=20.0,
        chain=lambda name, inp: reached.append(name) or None,
    )
    thread, outcome = _call_in_thread(hook, _MUTATING, {"path": "a.py", "content": "x"})
    row = _wait_for_gate(run_id, _MUTATING)

    assert wr.approve_step(row["step_run_id"], actor="isso") is True
    thread.join(timeout=15)

    assert not thread.is_alive(), "the hook stayed blocked after approval"
    assert outcome.get("result") is None, outcome
    assert reached == [_MUTATING]

    allowed = [r for r in _audit(run_id, _MUTATING) if r["decision"] == "allowed"]
    assert len(allowed) == 1
    assert row["step_run_id"] in allowed[0]["detail"], (
        "the audit row does not say which gate authorized the call"
    )


def test_a_rejected_gate_blocks_the_call(run_id, caller):
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id, step_id="build", approval_wait=20.0,
    )
    thread, outcome = _call_in_thread(hook, _MUTATING, {"path": "a.py", "content": "x"})
    row = _wait_for_gate(run_id, _MUTATING)

    assert wr.reject_step(row["step_run_id"], reason="not this file") is True
    thread.join(timeout=15)

    assert gate.REASON_APPROVAL_REJECTED in (outcome.get("result") or "")
    rows = [r for r in _audit(run_id, _MUTATING)
            if r["reason"] == gate.REASON_APPROVAL_REJECTED]
    assert rows and rows[0]["decision"] == "refused"


def test_an_undecided_gate_is_audited_as_pending_not_denied(run_id, caller):
    """`approval_wait=0` parks the gate without blocking. Nobody has said no."""
    hook = gate.build_gate_hook(
        caller=caller, run_id=run_id, step_id="build", approval_wait=0.0,
    )

    message = hook(_MUTATING, {"path": "a.py", "content": "x"})

    assert gate.REASON_AWAITING_APPROVAL in (message or "")
    assert _gate_row(run_id, _MUTATING)["status"] == "awaiting_approval"
    rows = [r for r in _audit(run_id, _MUTATING)
            if r["reason"] == gate.REASON_AWAITING_APPROVAL]
    assert rows and rows[0]["decision"] == "pending_approval"


def test_a_mutating_tool_with_no_run_to_park_a_gate_on_is_refused(caller):
    """Fail-closed: no run means no approver, so it does not mean "go ahead"."""
    hook = gate.build_gate_hook(caller=caller, run_id="")

    message = hook(_MUTATING, {"path": "a.py", "content": "x"})

    assert gate.REASON_GATE_UNAVAILABLE in (message or "")


def test_the_agent_gate_has_its_own_namespace(run_id):
    """Approving an mcp step's tool must not authorize an agent loop's.

    The two are different questions — one was reviewed in a template, the other
    was chosen by a model mid-run — so they are two gates even in one run.
    """
    assert gate.approval_step_id(_MUTATING) != mcp_executor.approval_step_id(_MUTATING)
    assert gate.approval_step_id(_MUTATING).startswith("approval:agent:")


# ══════════════════════════════════════════════════════════════
# AC 3 — a caller below the tool's declared IL is refused
# ══════════════════════════════════════════════════════════════

def test_a_caller_below_the_declared_il_is_refused(caller):
    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.check_caller_authorized(_IL5_ONLY, caller)
    assert exc.value.reason == gate.REASON_EXCEEDS_IL
    assert "IL5" in str(exc.value)


def test_a_caller_that_meets_the_declared_il_is_authorized(il5_caller):
    limits = gate.check_caller_authorized(_IL5_ONLY, il5_caller)
    assert limits["min_il"] == "IL5"


def test_the_hook_blocks_and_audits_an_il_refusal(run_id, caller):
    hook = gate.build_gate_hook(caller=caller, run_id=run_id, step_id="build")

    message = hook(_IL5_ONLY, {"command": "python tools/testing/health_check.py"})

    assert gate.REASON_EXCEEDS_IL in (message or "")
    rows = _audit(run_id, _IL5_ONLY)
    assert len(rows) == 1
    assert rows[0]["decision"] == "refused"
    assert rows[0]["reason"] == gate.REASON_EXCEEDS_IL
    assert rows[0]["caller_il"] == "IL4"


def test_an_unknown_caller_il_is_refused_not_defaulted(caller):
    """The gate does not guess what an unrecognized level permits."""
    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.check_caller_authorized(_READ_ONLY, {**caller, "impact_level": "IL9"})
    assert exc.value.reason == gate.REASON_EXCEEDS_IL


def test_a_tool_with_no_declared_limit_runs_at_the_platform_baseline(caller):
    limits = gate.tool_limits(_READ_ONLY)
    assert limits["min_il"] == gate.load_policy()["default_min_il"]
    assert gate.check_caller_authorized(_READ_ONLY, caller)["required_roles"] == ()


# ── Role limits ────────────────────────────────────────────────────────────

def test_a_caller_missing_a_required_role_is_refused(caller, monkeypatch):
    policy = dict(gate.load_policy())
    policy["tool_limits"] = {**policy["tool_limits"], _READ_ONLY: {"required_roles": ["isso"]}}

    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.check_caller_authorized(_READ_ONLY, caller, policy)
    assert exc.value.reason == gate.REASON_MISSING_ROLE
    assert "isso" in str(exc.value)

    # Holding any one of the required roles clears it.
    held = {**caller, "roles": ("developer", "isso")}
    assert gate.check_caller_authorized(_READ_ONLY, held, policy)


# ── Fail-closed policy ─────────────────────────────────────────────────────

def test_a_policy_that_is_not_default_deny_is_refused(tmp_path):
    path = tmp_path / "security_gates.yaml"
    path.write_text(
        "agent_workflow_tools:\n  default: allow\n  allowed: [read_file]\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.load_policy(path, refresh=True)
    assert exc.value.reason == gate.REASON_POLICY_UNAVAILABLE


def test_a_missing_policy_section_is_refused(tmp_path):
    path = tmp_path / "security_gates.yaml"
    path.write_text("gates: []\n", encoding="utf-8")
    with pytest.raises(gate.AgentToolGateError) as exc:
        gate.load_policy(path, refresh=True)
    assert exc.value.reason == gate.REASON_POLICY_UNAVAILABLE


def test_the_two_policy_sections_do_not_share_a_cache_entry():
    """Both surfaces read one file through one loader; they are two allowlists."""
    agent = gate.load_policy(refresh=True)
    mcp = mcp_executor.load_gate_policy(refresh=True)

    assert set(agent["allowed"]) != set(mcp["allowed"])
    assert gate.load_policy()["allowed"] == agent["allowed"]


# ══════════════════════════════════════════════════════════════
# The executor is wired to the gate
# ══════════════════════════════════════════════════════════════

executor = importlib.import_module("tools.studio.executors.agent_executor")
agent_loop = importlib.import_module("icdev.tools.llm.agent_loop")


@pytest.fixture
def _fake_loop(monkeypatch) -> dict:
    """Replace `run_agent_loop`; return the dict its kwargs land in."""
    seen: dict = {}

    def _fake(router, **kwargs):
        seen.update(kwargs)
        return agent_loop.AgentLoopResult(done=True, turns=1)

    monkeypatch.setattr(agent_loop, "run_agent_loop", _fake)
    monkeypatch.setattr(executor, "write_run_memory", lambda *a, **k: (True, ""))
    return seen


def test_the_step_toolset_is_a_subset_of_what_the_gate_allows(
    _fake_loop, tmp_path, caller
):
    """`terminal` names run_command; an IL4 step is not handed it."""
    payload = executor.run(
        "do it",
        bundles=["worktree_read", "terminal"],
        work_dir=str(tmp_path),
        router=object(),
        caller=caller,
    )

    assert _IL5_ONLY not in payload["tools_offered"]
    assert _IL5_ONLY not in _fake_loop["tool_handlers"]
    assert {t["function"]["name"] for t in _fake_loop["tools"]} == set(
        payload["tools_offered"]
    )
    # Named in the payload, not silently dropped.
    assert [r["tool"] for r in payload["tools_refused"]] == [_IL5_ONLY]
    assert payload["tools_refused"][0]["reason"] == gate.REASON_EXCEEDS_IL
    assert payload["caller_il"] == "IL4"


def test_a_caller_that_clears_the_limit_is_handed_the_tool(
    _fake_loop, tmp_path, il5_caller
):
    payload = executor.run(
        "do it",
        bundles=["worktree_read", "terminal"],
        work_dir=str(tmp_path),
        router=object(),
        caller=il5_caller,
    )

    assert _IL5_ONLY in payload["tools_offered"]
    assert "tools_refused" not in payload


def test_a_step_whose_every_tool_is_withheld_is_refused(
    _fake_loop, tmp_path, caller, monkeypatch
):
    """Better than handing an agent an empty toolbox and letting it find out."""
    monkeypatch.setattr(
        gate, "allowed_tools", lambda policy=None: frozenset()
    )
    monkeypatch.setattr(
        gate, "approval_tools", lambda policy=None: frozenset()
    )

    with pytest.raises(executor.AgentStepError) as exc:
        executor.run(
            "do it", bundles=["worktree_read"], work_dir=str(tmp_path),
            router=object(), caller=caller,
        )
    assert exc.value.reason == "agent_step_all_tools_refused"


def test_an_unreadable_policy_refuses_the_step_rather_than_ungating_it(
    tmp_path, caller, monkeypatch
):
    """Fail-closed. No policy must mean no toolset, not an unbounded one."""
    def _boom(*a, **k):
        raise gate.AgentToolGateError("no policy", reason=gate.REASON_POLICY_UNAVAILABLE)

    monkeypatch.setattr(gate, "authorize_toolset", _boom)

    with pytest.raises(executor.AgentStepError) as exc:
        executor.run(
            "do it", bundles=["worktree_read"], work_dir=str(tmp_path),
            router=object(), caller=caller,
        )
    assert exc.value.reason == "agent_step_gate_unavailable"


def test_the_executor_hook_chains_both_gates(tmp_path, run_id, caller):
    """The hook handed to the loop is the authorization gate wrapping ars-appr-01."""
    hook = executor._build_approval_hook(
        run_id, "off", caller=caller, step_id="build",
    )

    # Authorization refuses first: the reversibility gate would have let a read
    # of an unknown tool through as `unknown`-tier only after asking.
    assert gate.REASON_NOT_ALLOWLISTED in (hook("curl_the_internet", {}) or "")
    assert hook(_READ_ONLY, {"path": "README.md"}) is None


def test_a_template_cannot_declare_its_own_impact_level():
    """Otherwise an authored step could raise itself past the gate's limits."""
    runner = importlib.import_module("tools.studio.workflow_runner")
    flags = {flag for _, flag in runner._AGENT_STEP_FLAGS}

    assert not flags & {"--caller-il", "--caller-roles", "--caller-id", "--tenant-id"}

    cmd = runner._build_agent_command(
        {"id": "build", "node_type": "agent", "prompt": "go",
         "agent_tools": ["worktree_read"], "caller_il": "IL6"},
        "proj", "run-1",
    )
    assert "IL6" not in cmd
