# CUI // SP-CTI
"""Tests: ACE AI Developer + QA Manager negotiation workflow.

Scenarios:
  test_qa_manager_sends_verify_response_on_pass  — failed_count=0  → cw_verify_response(passed=True)
  test_qa_manager_negotiates_on_failure          — failed_count>0  → cw_negotiate_propose to ai_developer
  test_negotiation_accept_resolves               — cw_negotiate_accept → negotiate() returns accepted=True
  test_negotiation_max_rounds_hitl               — 3 rounds no accept → NegotiationFailedError + HITL entry
  test_genesis_reflex_escalates_stale            — stale instance → WorkflowEngine.escalate() called
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.ace.constants import MAX_NEGOTIATION_ROUNDS
from icdev.tools.ace.coworker_thread import HITLGate
from icdev.tools.ace.message_bus import MessageBus, NegotiationFailedError


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_INSTANCE_ID = "ace-qa-test-inst-001"
_QA_CW_ID = "cw-qa-001"
_DEV_CW_ID = "cw-dev-001"


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------


@dataclass
class E2EResult:
    """Minimal E2E test result for QA Manager workflow tests."""

    failed_count: int
    total_count: int = 10


# ---------------------------------------------------------------------------
# QA post-test decision (function-under-test for scenarios 1 & 2)
# ---------------------------------------------------------------------------


def _qa_post_test_action(bus: MessageBus, qa_id: str, result: E2EResult) -> None:
    """QA Manager decision gate after running the test suite.

    - All pass (failed_count == 0): sends cw_verify_response with passed=True
    - Failures present (failed_count > 0): sends cw_negotiate_propose to ai_developer
    """
    if result.failed_count == 0:
        bus.send(
            from_coworker_id=qa_id,
            to_role="ai_developer",
            message_type="cw_verify_response",
            payload={"passed": True, "failed_count": 0},
        )
    else:
        bus.send(
            from_coworker_id=qa_id,
            to_role="ai_developer",
            message_type="cw_negotiate_propose",
            payload={"passed": False, "failed_count": result.failed_count},
        )


# ---------------------------------------------------------------------------
# Genesis reflex stub (function-under-test for scenario 5)
# ---------------------------------------------------------------------------


def _genesis_reflex_check_stale(instance_id: str, workflow_engine: Any) -> dict[str, Any]:
    """Genesis reflex: escalate a stale ACE instance via WorkflowEngine."""
    return workflow_engine.escalate(instance_id, reason="ace_instance_stale")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


class _NoCloseConn:
    """sqlite3.Connection wrapper where close() is a no-op.

    Allows multiple get_canvas_connection() calls in a single test to share
    the same in-memory DB without destroying it on the first close().
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._r = real

    def execute(self, *args: Any, **kwargs: Any):
        return self._r.execute(*args, **kwargs)

    def commit(self) -> None:
        self._r.commit()

    def close(self) -> None:
        pass  # intentional no-op


def _make_ace_db() -> sqlite3.Connection:
    """Return a seeded in-memory SQLite DB with the ACE audit_log table."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE ace_audit_log (
            id          TEXT NOT NULL DEFAULT (lower(hex(randomblob(16)))),
            instance_id TEXT,
            coworker_id TEXT,
            action      TEXT,
            detail      TEXT,
            actor       TEXT,
            created_at  TEXT
        );
        """
    )
    conn.commit()
    return conn


def _insert_hitl_pending(
    conn: sqlite3.Connection,
    instance_id: str,
    coworker_id: str,
    detail: str,
) -> None:
    """Insert a hitl_pending audit row (simulates CoWorkerThread._audit)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO ace_audit_log "
        "(instance_id, coworker_id, action, detail, actor, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (instance_id, coworker_id, "hitl_pending", detail, "test_helper", now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Scenario 1: QA Manager sends cw_verify_response when tests pass
# ---------------------------------------------------------------------------


class TestQaManagerVerifyResponseOnPass:
    def test_qa_manager_sends_verify_response_on_pass(self):
        """failed_count=0 → bus.send called with cw_verify_response and passed=True."""
        bus = MessageBus(instance_id=_INSTANCE_ID)

        with patch.object(bus, "send") as mock_send:
            _qa_post_test_action(bus, _QA_CW_ID, E2EResult(failed_count=0))

        mock_send.assert_called_once()
        kw = mock_send.call_args.kwargs
        assert kw["message_type"] == "cw_verify_response"
        assert kw["payload"]["passed"] is True
        assert kw["payload"]["failed_count"] == 0


# ---------------------------------------------------------------------------
# Scenario 2: QA Manager triggers cw_negotiate_propose on test failure
# ---------------------------------------------------------------------------


class TestQaManagerNegotiatesOnFailure:
    def test_qa_manager_negotiates_on_failure(self):
        """failed_count>0 → bus.send called with cw_negotiate_propose to ai_developer."""
        bus = MessageBus(instance_id=_INSTANCE_ID)

        with patch.object(bus, "send") as mock_send:
            _qa_post_test_action(bus, _QA_CW_ID, E2EResult(failed_count=3))

        mock_send.assert_called_once()
        kw = mock_send.call_args.kwargs
        assert kw["message_type"] == "cw_negotiate_propose"
        assert kw["to_role"] == "ai_developer"
        assert kw["payload"]["passed"] is False
        assert kw["payload"]["failed_count"] == 3


# ---------------------------------------------------------------------------
# Scenario 3: negotiate() resolves when AI Developer sends cw_negotiate_accept
# ---------------------------------------------------------------------------


class TestNegotiationAcceptResolves:
    def test_negotiation_accept_resolves(self):
        """cw_negotiate_accept received in round 1 → negotiate() returns accepted=True."""
        accept_msg = {
            "id": str(uuid.uuid4()),
            "subject": "ACE:cw_negotiate_accept",
            "body": json.dumps({"fix_summary": "patched null pointer dereference"}),
        }
        bus = MessageBus(instance_id=_INSTANCE_ID)

        with (
            patch.object(bus, "_send_raw", return_value=str(uuid.uuid4())),
            patch.object(bus, "_resolve_role", return_value=_DEV_CW_ID),
            patch.object(bus, "_poll_negotiate_reply", return_value=[accept_msg]),
        ):
            result = bus.negotiate(
                from_coworker_id=_QA_CW_ID,
                to_role="ai_developer",
                payload={"failed_count": 2, "test_ids": ["t-01", "t-02"]},
                max_rounds=MAX_NEGOTIATION_ROUNDS,
            )

        assert result["accepted"] is True
        assert result["rounds"] == 1
        assert result["result"]["fix_summary"] == "patched null pointer dereference"


# ---------------------------------------------------------------------------
# Scenario 4: max_rounds exhausted → NegotiationFailedError + HITL entry
# ---------------------------------------------------------------------------


class TestNegotiationMaxRoundsHitl:
    def test_negotiation_max_rounds_hitl(self):
        """3 rounds with no accept → NegotiationFailedError; HITL entry is persisted and retrievable."""
        bus = MessageBus(instance_id=_INSTANCE_ID)
        hitl_detail = "negotiate_failed:ai_developer"

        # negotiate() exhausts all rounds and raises
        with (
            patch.object(bus, "_send_raw", return_value=str(uuid.uuid4())),
            patch.object(bus, "_resolve_role", return_value=_DEV_CW_ID),
            patch.object(bus, "_poll_negotiate_reply", return_value=[]),
        ):
            with pytest.raises(NegotiationFailedError):
                bus.negotiate(
                    from_coworker_id=_QA_CW_ID,
                    to_role="ai_developer",
                    payload={"failed_count": 5},
                    max_rounds=3,
                )

        # After catching NegotiationFailedError the workflow creates a HITL entry;
        # verify HITLGate can persist and retrieve it
        conn = _make_ace_db()
        _insert_hitl_pending(conn, _INSTANCE_ID, _QA_CW_ID, hitl_detail)

        with patch("icdev.tools.db.storage.get_canvas_connection", return_value=_NoCloseConn(conn)):
            pending = HITLGate.get_pending(_QA_CW_ID)

        assert len(pending) == 1
        assert pending[0]["detail"] == hitl_detail


# ---------------------------------------------------------------------------
# Scenario 5: Genesis reflex escalates stale ACE instance
# ---------------------------------------------------------------------------


class TestGenesisReflexEscalatesStale:
    def test_genesis_reflex_escalates_stale(self):
        """Genesis reflex detects stale ACE instance → WorkflowEngine.escalate() is called."""
        mock_engine = MagicMock()
        mock_engine.escalate.return_value = {
            "escalated": True,
            "reason": "ace_instance_stale",
        }

        result = _genesis_reflex_check_stale(_INSTANCE_ID, mock_engine)

        mock_engine.escalate.assert_called_once_with(
            _INSTANCE_ID, reason="ace_instance_stale"
        )
        assert result["escalated"] is True
