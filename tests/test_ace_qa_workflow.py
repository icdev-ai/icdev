# CUI // SP-CTI
"""Tests for QA Manager + AI Developer workflow scenarios.

Covers:
  - test_qa_manager_sends_verify_response_on_pass     : failed_count=0 → send cw_verify_response with passed=True
  - test_qa_manager_negotiates_on_failure             : failed_count>0 → negotiate cw_negotiate_propose to ai_developer
  - test_negotiation_accept_resolves                  : ai_developer sends cw_negotiate_accept → negotiate() returns
  - test_negotiation_max_rounds_hitl                  : 3 rounds, no accept → NegotiationFailedError raised
  - test_genesis_reflex_escalates_stale               : stale hitl_pending instance → WorkflowEngine.escalate() called

Run: pytest tests/test_ace_qa_workflow.py -v
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from icdev.tools.ace.message_bus import MessageBus, NegotiationFailedError


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    """Temp ACE SQLite DB with the full ACE schema."""
    db_path = tmp_path / "ace_qa_workflow.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))

    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return db_path


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed(instance_id: str, coworkers: list[tuple[str, str]], state: str = "idle") -> None:
    """Insert ace_instances + ace_coworkers rows."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, trust_tier) "
            "VALUES (?, ?, ?, 'assembling', 'yellow')",
            (instance_id, instance_id, coworkers[0][1] if coworkers else ""),
        )
        for cw_id, role_id in coworkers:
            conn.execute(
                "INSERT INTO ace_coworkers "
                "(id, instance_id, role_id, display_name, state, trust_tier) "
                "VALUES (?, ?, ?, ?, ?, 'yellow')",
                (cw_id, instance_id, role_id, role_id, state),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# test_qa_manager_sends_verify_response_on_pass
# ---------------------------------------------------------------------------


def test_qa_manager_sends_verify_response_on_pass(ace_db, monkeypatch):
    """e2e_result.failed_count=0 → MessageBus.send is called with cw_verify_response(passed=True)."""
    instance_id = "ace-qa-pass-01"
    _seed(instance_id, [("cw-qa", "qa_manager"), ("cw-dev", "ai_developer")])

    sent: list[dict] = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return "msg-pass-01"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)

    e2e_result = {"failed_count": 0, "passed_count": 5, "failures": []}

    # The verify_response step fires when failed_count == 0 (condition is False).
    # Directly call MessageBus.send as the step executor would.
    bus.send(
        from_coworker_id="cw-qa",
        to_role="ai_developer",
        message_type="cw_verify_response",
        payload={"passed": e2e_result["failed_count"] == 0, "details": e2e_result},
    )

    assert len(sent) == 1
    subject = sent[0]["subject"]
    assert subject == "ACE:cw_verify_response"
    body = json.loads(sent[0]["body"])
    assert body["passed"] is True
    assert body["details"]["failed_count"] == 0


# ---------------------------------------------------------------------------
# test_qa_manager_negotiates_on_failure
# ---------------------------------------------------------------------------


def test_qa_manager_negotiates_on_failure(ace_db, monkeypatch):
    """e2e_result.failed_count>0 → negotiate sends cw_negotiate_proposal to ai_developer."""
    instance_id = "ace-qa-fail-01"
    _seed(instance_id, [("cw-qa", "qa_manager"), ("cw-dev", "ai_developer")])

    sent: list[dict] = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return "msg-neg-01"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)
    # Accept on the first round so negotiate() returns cleanly.
    monkeypatch.setattr(
        bus,
        "_poll_negotiate_reply",
        lambda coworker_id, timeout_s: [
            {"subject": "ACE:cw_negotiate_accept", "body": json.dumps({"agreed": True})}
        ],
    )

    e2e_result = {"failed_count": 2, "failures": ["test_a", "test_b"]}

    result = bus.negotiate(
        from_coworker_id="cw-qa",
        to_role="ai_developer",
        payload={"failures": e2e_result["failures"]},
        max_rounds=3,
    )

    # One proposal must have been sent with the negotiate subject.
    assert len(sent) >= 1
    subjects = [s["subject"] for s in sent]
    assert "ACE:cw_negotiate_proposal" in subjects
    # negotiate() reported acceptance.
    assert result["accepted"] is True


# ---------------------------------------------------------------------------
# test_negotiation_accept_resolves
# ---------------------------------------------------------------------------


def test_negotiation_accept_resolves(ace_db, monkeypatch):
    """ai_developer replies cw_negotiate_accept → negotiate() returns with accepted=True, rounds=1."""
    instance_id = "ace-qa-accept-01"
    _seed(instance_id, [("cw-qa", "qa_manager"), ("cw-dev", "ai_developer")])

    monkeypatch.setattr(
        "icdev.tools.agent.mailbox.send",
        lambda **kw: "msg-acc-01",
    )

    bus = MessageBus(instance_id)

    accept_payload = {"fixed": True, "commit": "abc123"}
    monkeypatch.setattr(
        bus,
        "_poll_negotiate_reply",
        lambda coworker_id, timeout_s: [
            {
                "subject": "ACE:cw_negotiate_accept",
                "body": json.dumps(accept_payload),
            }
        ],
    )

    result = bus.negotiate(
        from_coworker_id="cw-qa",
        to_role="ai_developer",
        payload={"failures": ["test_x"]},
        max_rounds=3,
    )

    assert result["accepted"] is True
    assert result["rounds"] == 1
    assert result["result"]["fixed"] is True
    assert result["result"]["commit"] == "abc123"


# ---------------------------------------------------------------------------
# test_negotiation_max_rounds_hitl
# ---------------------------------------------------------------------------


def test_negotiation_max_rounds_hitl(ace_db, monkeypatch):
    """After 3 rounds with no accept, NegotiationFailedError is raised (HITL path)."""
    instance_id = "ace-qa-maxrounds-01"
    _seed(instance_id, [("cw-qa", "qa_manager"), ("cw-dev", "ai_developer")])

    proposal_rounds: list[int] = []

    def fake_send(**kwargs):
        body = json.loads(kwargs.get("body", "{}"))
        if kwargs.get("subject") == "ACE:cw_negotiate_proposal":
            proposal_rounds.append(body.get("_round", 0))
        return "msg-maxrnd"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)
    # Never return an accept → exhaust all rounds.
    monkeypatch.setattr(bus, "_poll_negotiate_reply", lambda coworker_id, timeout_s: [])

    with pytest.raises(NegotiationFailedError):
        bus.negotiate(
            from_coworker_id="cw-qa",
            to_role="ai_developer",
            payload={"failures": ["test_y"]},
            max_rounds=3,
        )

    # A proposal was sent for each round.
    assert proposal_rounds == [1, 2, 3]


# ---------------------------------------------------------------------------
# test_genesis_reflex_escalates_stale
# ---------------------------------------------------------------------------


def test_genesis_reflex_escalates_stale(monkeypatch):
    """Genesis reflex finds a stale hitl_pending instance and calls WorkflowEngine.escalate()."""
    instance_id = "ace-qa-stale-01"

    # Stub WorkflowEngine so we can capture escalate() calls.
    escalated: list[str] = []

    class _FakeWorkflowEngine:
        def escalate(self, iid: str, reason: str = "") -> None:
            escalated.append(iid)

    fake_engine_mod = types.ModuleType("icdev.tools.workflow_hitl.engine")
    fake_engine_mod.WorkflowEngine = _FakeWorkflowEngine  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "icdev.tools.workflow_hitl.engine", fake_engine_mod)

    # Stub genesis_audit write so it doesn't need the full main DB.
    monkeypatch.setattr(
        "icdev.tools.ace.genesis_reflex._log_genesis_audit",
        lambda stale_running, stale_hitl: None,
    )

    # Mock the canvas connection to return a fake stale hitl_pending row.
    # ace_instances CHECK constraint only allows known states; we bypass DB
    # entirely so we can test the reflex logic against the full state name.
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [(instance_id, "hitl_pending")]
    fake_conn = MagicMock()
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.execute.return_value = fake_cursor

    import icdev.tools.db.storage as _storage

    monkeypatch.setattr(_storage, "get_canvas_connection", lambda env_var: fake_conn)
    monkeypatch.setattr(_storage, "is_pg", lambda conn: False)

    from icdev.tools.ace import genesis_reflex

    result = genesis_reflex.run({}, None)

    assert result["stale_hitl"] >= 1
    assert instance_id in escalated
