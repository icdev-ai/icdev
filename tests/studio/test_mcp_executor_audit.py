# CUI // SP-CTI
"""Append-only audit of every MCP dispatch attempt (dwo-mcp-02-d5).

The card's acceptance is a query: after an allowed dispatch, an allowlist
refusal, an IL refusal and a parked approval, does the audit log return one
correct row per attempt with every required field present? These tests ask
exactly that, through the public reader (`query_dispatch_audit`) rather than
by reaching into the table, so the reader is covered too.

The one thing that must NOT be in a row is the parameters themselves — tool
arguments carry CUI and credentials, so only their SHA-256 digest is stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import types
import uuid
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.studio.executors import mcp_executor
from tools.studio.init_db import init_studio_tables

_ROOT = Path(__file__).resolve().parents[2]

#: Fields the card requires on every row, whatever the outcome.
_REQUIRED = (
    "tool", "params_sha256", "run_id", "step_id", "principal_id",
    "caller_il", "decision", "reason", "classification", "recorded_at",
)


@pytest.fixture(autouse=True)
def _schema():
    init_studio_tables()


@pytest.fixture
def run_id() -> str:
    rid = f"run-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, project_id) "
            "VALUES (%s, %s, %s, 'running', 'default')",
            (rid, f"wf-{uuid.uuid4().hex[:12]}", "Audited"),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


@pytest.fixture
def caller() -> dict:
    """An IL4 principal with an identity, so the actor fields are non-empty."""
    return {
        "principal_id": "p-audit",
        "tenant_id": "t-audit",
        "impact_level": "IL4",
        "roles": ("admin",),
        "source": "argument",
    }


@pytest.fixture
def stub_tool(monkeypatch):
    """Point an allowlisted tool at a stub handler so nothing real is called."""
    mod = types.ModuleType("tests_mcp_audit_stub")
    mod.handle = lambda args: {"ok": True}
    monkeypatch.setitem(sys.modules, "tests_mcp_audit_stub", mod)

    from tools.mcp.tool_registry import TOOL_REGISTRY

    tool = sorted(mcp_executor.allowed_tools())[0]
    monkeypatch.setitem(TOOL_REGISTRY, tool, {
        "category": "testing",
        "module": "tests_mcp_audit_stub",
        "handler": "handle",
        "description": "Stub.",
        "input_schema": {"type": "object", "properties": {}},
    })
    return tool


def _only_row(run_id: str) -> dict:
    rows = mcp_executor.query_dispatch_audit(run_id=run_id)
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"
    row = rows[0]
    for field in _REQUIRED:
        assert str(row.get(field) or "").strip(), f"audit row is missing {field}"
    return row


# ── The four acceptance paths ──────────────────────────────────────────────

def test_allowed_dispatch_is_audited(run_id, caller, stub_tool):
    params = {"b": 2, "a": 1}
    payload = mcp_executor.run(stub_tool, params, run_id, "step-1", caller)
    assert payload["audit_written"] is True

    row = _only_row(run_id)
    assert row["decision"] == mcp_executor.DECISION_ALLOWED
    assert row["reason"] == mcp_executor.REASON_DISPATCHED
    assert row["tool"] == stub_tool
    assert row["step_id"] == "step-1"
    assert row["principal_id"] == "p-audit"
    assert row["tenant_id"] == "t-audit"
    assert row["caller_il"] == "IL4"
    assert row["caller_roles"] == "admin"


def test_allowlist_refusal_is_audited(run_id, caller):
    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run("definitely_not_a_registered_tool", {}, run_id, "", caller)

    row = _only_row(run_id)
    assert row["decision"] == mcp_executor.DECISION_REFUSED
    assert row["reason"] == "mcp_tool_not_allowlisted"


def test_il_refusal_is_audited(run_id, stub_tool):
    """An IL2 caller cannot reach an IL4 platform tool — and that is recorded."""
    low = {"principal_id": "p-low", "tenant_id": "t-audit",
           "impact_level": "IL2", "roles": (), "source": "argument"}
    with pytest.raises(mcp_executor.MCPWorkflowGateError):
        mcp_executor.run(stub_tool, {}, run_id, "", low)

    row = _only_row(run_id)
    assert row["decision"] == mcp_executor.DECISION_REFUSED
    assert row["reason"] == "mcp_tool_exceeds_caller_il"
    assert row["caller_il"] == "IL2"


def test_pending_approval_is_audited_as_pending_not_refused(
    run_id, caller, monkeypatch
):
    """A parked gate is a live attempt, not a denial — it audits as pending."""
    tool = sorted(mcp_executor.approval_tools())[0]
    mod = types.ModuleType("tests_mcp_audit_approval_stub")
    mod.handle = lambda args: {"ok": True}
    monkeypatch.setitem(sys.modules, "tests_mcp_audit_approval_stub", mod)
    from tools.mcp.tool_registry import TOOL_REGISTRY

    monkeypatch.setitem(TOOL_REGISTRY, tool, {
        "category": "testing", "module": "tests_mcp_audit_approval_stub",
        "handler": "handle", "description": "Stub.",
        "input_schema": {"type": "object", "properties": {}},
    })

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run(tool, {}, run_id, "", caller, approval_wait=0.0)
    assert exc.value.reason == "mcp_tool_awaiting_human_approval"

    row = _only_row(run_id)
    assert row["decision"] == mcp_executor.DECISION_PENDING_APPROVAL
    assert row["reason"] == "mcp_tool_awaiting_human_approval"


# ── Digest, marking, and the CHECK/constant contract ───────────────────────

def test_params_are_digested_not_stored(run_id, caller, stub_tool):
    secret = {"api_key": "sk-live-do-not-log", "region": "us-gov-west-1"}
    mcp_executor.run(stub_tool, secret, run_id, "step-1", caller)

    row = _only_row(run_id)
    canonical = json.dumps(secret, sort_keys=True, separators=(",", ":"))
    assert row["params_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert "sk-live-do-not-log" not in json.dumps(row)


def test_digest_is_order_independent():
    assert (mcp_executor.params_digest({"a": 1, "b": 2})
            == mcp_executor.params_digest({"b": 2, "a": 1}))
    assert (mcp_executor.params_digest({"a": 1})
            != mcp_executor.params_digest({"a": 2}))


def test_classification_tracks_impact_level_and_is_not_hardcoded():
    """IL6 must mark SECRET; the marking comes from classification_manager."""
    from tools.compliance.classification_manager import get_classification_for_il

    for level in ("IL2", "IL4", "IL5", "IL6"):
        assert mcp_executor.audit_classification(level) == get_classification_for_il(level)
    # An unrecognized level falls back to the platform baseline, not a literal.
    assert mcp_executor.audit_classification("IL99") == get_classification_for_il(
        mcp_executor.DEFAULT_CALLER_IL
    )


def test_check_constraint_mirrors_the_python_decision_constants():
    """CLAUDE.md: a CHECK list derives from Python constants, never drifts."""
    sql = (_ROOT / "tools/db/migrations/307_studio_mcp_dispatch_audit.sql").read_text(
        encoding="utf-8"
    )
    match = re.search(r"CHECK\(decision IN \(([^)]*)\)\)", sql)
    assert match, "migration 307 has no decision CHECK constraint"
    in_sql = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert in_sql == set(mcp_executor.DECISIONS)


def test_unknown_decision_is_rejected_before_it_reaches_the_table():
    with pytest.raises(ValueError, match="Unknown audit decision"):
        mcp_executor.record_dispatch_audit("t", {}, "maybe", "r")


def test_audit_failure_does_not_decide_the_dispatch(run_id, caller, stub_tool,
                                                    monkeypatch):
    """An unreachable audit store must not turn a legitimate dispatch into a
    failure — the payload says the audit did not land instead."""
    monkeypatch.setattr(
        mcp_executor, "_gate_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("audit store down")),
    )
    payload = mcp_executor.run(stub_tool, {}, run_id, "step-1", caller)
    assert payload["result"] == {"ok": True}
    assert payload["audit_written"] is False
    assert "audit store down" in payload["audit_skipped"]


def test_table_is_registered_append_only():
    hook = (_ROOT / "tools/hooks/shared_checks.py").read_text(encoding="utf-8")
    assert f'"{mcp_executor.AUDIT_TABLE}"' in hook
