"""ACE runtime tests — message bus, coworker thread, controller.

Covers:
  * MessageBus.send inserts ace_messages row
  * MessageBus.broadcast fans out to 2+ coworkers
  * MessageBus.negotiate accept + max-rounds failure
  * CoWorkerThread step sequence + TrustKernelDeniedError halt
  * ACEController.launch returns instance_id and persists ace_instances row
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import MINIMAL_ICDEV_SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ace_db_path(tmp_path: Path) -> Path:
    """Temp SQLite file with ACE schema pre-created."""
    db_file = tmp_path / "ace_runtime.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def ace_env(monkeypatch, ace_db_path: Path):
    """Point ACE canvas DB to the temp file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(ace_db_path))
    # Ensure init runs against the temp DB
    from icdev.tools.ace.db.init_db import init as _init_ace_db

    _init_ace_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_db(ace_db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(ace_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _insert_instance(conn: sqlite3.Connection, inst_id: str) -> None:
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state, trust_tier, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (inst_id, f"test-{inst_id}", "ai_developer", "assembling", "yellow", "{}"),
    )
    conn.commit()


def _insert_coworker(conn: sqlite3.Connection, cw_id: str, inst_id: str, role_id: str) -> None:
    conn.execute(
        "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, trust_tier) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cw_id, inst_id, role_id, f"Test {role_id}", "idle", "yellow"),
    )
    conn.commit()


def _make_role(steps: list) -> MagicMock:
    """Return a mock RoleTemplate with the given steps."""
    role = MagicMock()
    role.steps = steps
    role.llm_function = "code_generation"
    role.tool_permissions = ["Read"]
    role.trust_tier = "yellow"
    return role


# ---------------------------------------------------------------------------
# MessageBus
# ---------------------------------------------------------------------------


class TestMessageBus:
    """ACE MessageBus send, broadcast, negotiate."""

    def test_message_bus_send_inserts_ace_messages(self, ace_env, ace_db_path: Path) -> None:
        """send() creates an ace_messages row."""
        from icdev.tools.ace.message_bus import MessageBus

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, "cw-1", inst_id, "ai_developer")
        conn.close()

        bus = MessageBus(instance_id=inst_id)
        with patch("icdev.tools.agent.mailbox.send", return_value="msg-123"):
            bus.send("cw-1", "ai_developer", "cw_verify_request", {"foo": "bar"})

        conn = _open_db(ace_db_path)
        rows = conn.execute(
            "SELECT message_type, content FROM ace_messages WHERE instance_id = ?", (inst_id,)
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["message_type"] == "cw_verify_request"
        payload = json.loads(rows[0]["content"])
        assert payload["foo"] == "bar"

    def test_message_bus_broadcast_fans_out(self, ace_env, ace_db_path: Path) -> None:
        """broadcast() sends to every coworker in this instance (excluding self)."""
        from icdev.tools.ace.message_bus import MessageBus

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, "cw-a", inst_id, "ai_developer")
        _insert_coworker(conn, "cw-b", inst_id, "qa_manager")
        conn.close()

        bus = MessageBus(instance_id=inst_id)
        sent_to: list[str] = []

        def fake_send(*, to_agent_id: str, **kwargs) -> str:
            sent_to.append(to_agent_id)
            return f"msg-{len(sent_to)}"

        with patch("icdev.tools.agent.mailbox.send", side_effect=fake_send):
            bus.broadcast("cw-a", "cw_broadcast", {"event": "ping"})

        assert "cw-b" in sent_to
        assert "cw-a" not in sent_to

    def test_message_bus_negotiate_accept(self, ace_env, ace_db_path: Path) -> None:
        """Propose + accept completes in one round."""
        from icdev.tools.ace.message_bus import MessageBus

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, "cw-1", inst_id, "ai_developer")
        conn.close()

        bus = MessageBus(instance_id=inst_id)

        def fake_receive(*, agent_id: str, **kwargs):
            return [
                {
                    "id": "resp-1",
                    "subject": "ACE:cw_negotiate_accept",
                    "body": json.dumps({"ok": True}),
                }
            ]

        with patch("icdev.tools.agent.mailbox.send", return_value="msg-123"):
            with patch("icdev.tools.agent.mailbox.receive", side_effect=fake_receive):
                with patch("icdev.tools.agent.mailbox.mark_read", return_value=True):
                    result = bus.negotiate("cw-1", "ai_developer", {"price": 100})

        assert result["accepted"] is True
        assert result["rounds"] == 1

    def test_message_bus_negotiate_max_rounds(self, ace_env, ace_db_path: Path) -> None:
        """Only counters → NegotiationFailedError after 3 rounds."""
        from icdev.tools.ace.message_bus import MessageBus, NegotiationFailedError

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, "cw-1", inst_id, "ai_developer")
        conn.close()

        bus = MessageBus(instance_id=inst_id)

        def fake_receive(*, agent_id: str, **kwargs):
            return [
                {
                    "id": "resp-1",
                    "subject": "ACE:cw_negotiate_counter",
                    "body": json.dumps({"price": 200}),
                }
            ]

        with patch("icdev.tools.agent.mailbox.send", return_value="msg-123"):
            with patch("icdev.tools.agent.mailbox.receive", side_effect=fake_receive):
                with patch("icdev.tools.agent.mailbox.mark_read", return_value=True):
                    with pytest.raises(NegotiationFailedError):
                        bus.negotiate("cw-1", "ai_developer", {"price": 100}, max_rounds=3)


# ---------------------------------------------------------------------------
# CoWorkerThread
# ---------------------------------------------------------------------------


class TestCoWorkerThread:
    """CoWorkerThread step execution and error handling."""

    def test_coworker_thread_step_sequence(self, ace_env, ace_db_path: Path) -> None:
        """Mock StepExecutor: steps run in declared order."""
        from icdev.tools.ace.coworker_thread import CoWorkerThread
        from icdev.tools.ace.message_bus import MessageBus
        from icdev.tools.ace.team_assembler import CoWorkerSpec
        from icdev.tools.ace.step_executor import StepExecutor

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        spec = CoWorkerSpec(
            coworker_id="cw-seq",
            role_id="ai_developer",
            role_slot="ai_developer",
            mailbox_id="mailbox:cw-seq",
            llm_function="code_generation",
            tool_permissions=["Read"],
            trust_tier="yellow",
        )

        # Pre-insert DB rows so _set_state / _audit UPDATEs succeed
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, spec.coworker_id, inst_id, spec.role_id)
        conn.close()

        bus = MessageBus(instance_id=inst_id)
        tk = MagicMock()
        tk.can_execute.return_value = True

        thread = CoWorkerThread(spec=spec, instance_id=inst_id, message_bus=bus, trust_kernel=tk)
        executed: list[str] = []

        def fake_run(step, ctx, s, tk):
            executed.append(step.get("id"))
            return {"ok": True}

        with patch("icdev.tools.ace.role_loader.RoleLoader.get_role", return_value=_make_role(["step-alpha", "step-beta", "step-gamma"])):
            with patch.object(StepExecutor, "run", side_effect=fake_run):
                with patch("icdev.tools.agent.mailbox.send", return_value="msg-bcast"):
                    thread.run()

        assert executed == ["step-alpha", "step-beta", "step-gamma"]

        conn = _open_db(ace_db_path)
        row = conn.execute(
            "SELECT state FROM ace_coworkers WHERE id = ?", (spec.coworker_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["state"] == "done"

    def test_coworker_thread_trust_denied(self, ace_env, ace_db_path: Path) -> None:
        """TrustKernelDeniedError on required step → HITL pending → thread halts."""
        from icdev.tools.ace.coworker_thread import CoWorkerThread
        from icdev.tools.ace.message_bus import MessageBus
        from icdev.tools.ace.team_assembler import CoWorkerSpec
        from icdev.tools.ace.step_executor import StepExecutor, TrustKernelDeniedError

        inst_id = f"ace-{uuid.uuid4().hex[:12]}"
        spec = CoWorkerSpec(
            coworker_id="cw-trust",
            role_id="ai_developer",
            role_slot="ai_developer",
            mailbox_id="mailbox:cw-trust",
            llm_function="code_generation",
            tool_permissions=["Read"],
            trust_tier="yellow",
        )

        # Pre-insert DB rows so _set_state / _audit UPDATEs succeed
        conn = _open_db(ace_db_path)
        _insert_instance(conn, inst_id)
        _insert_coworker(conn, spec.coworker_id, inst_id, spec.role_id)
        conn.close()

        bus = MessageBus(instance_id=inst_id)
        tk = MagicMock()
        tk.can_execute.return_value = False

        thread = CoWorkerThread(spec=spec, instance_id=inst_id, message_bus=bus, trust_kernel=tk)

        def fake_run(step, ctx, s, tk):
            raise TrustKernelDeniedError("trust denied")

        hitl_pending_calls = 0

        def fake_get_pending(coworker_id: str):
            nonlocal hitl_pending_calls
            hitl_pending_calls += 1
            return [{"id": "h1", "detail": "step=danger-step", "created_at": "2026-01-01T00:00:00"}]

        with patch("icdev.tools.ace.role_loader.RoleLoader.get_role", return_value=_make_role([{"id": "danger-step", "required": True}])):
            with patch.object(StepExecutor, "run", side_effect=fake_run):
                with patch("icdev.tools.ace.coworker_thread.HITLGate.get_pending", side_effect=fake_get_pending):
                    with patch("icdev.tools.ace.coworker_thread._HITL_POLL_INTERVAL", 0.01):
                        # Signal stop after a short window so the thread exits
                        def stopper():
                            time.sleep(0.05)
                            thread.stop()

                        import threading

                        t = threading.Thread(target=stopper, daemon=True)
                        t.start()
                        thread.run()

        conn = _open_db(ace_db_path)
        row = conn.execute(
            "SELECT state FROM ace_coworkers WHERE id = ?", (spec.coworker_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["state"] == "hitl_pending"


# ---------------------------------------------------------------------------
# ACEController
# ---------------------------------------------------------------------------


class TestController:
    """ACEController launch and status."""

    def test_controller_launch_returns_instance_id(self, ace_env, ace_db_path: Path) -> None:
        """launch() returns instance_id and creates ace_instances row."""
        from icdev.tools.ace.controller import ACEController
        from icdev.tools.ace.problem_classifier import TeamManifest, RoleSlot

        # Reset singleton so prior tests don't pollute state
        ACEController._instance = None

        ctrl = ACEController.get_instance()

        manifest = TeamManifest(slots=[RoleSlot("ai_developer", 1)])

        with patch(
            "icdev.tools.ace.problem_classifier.ProblemClassifierLens.run", return_value=manifest
        ):
            with patch("icdev.tools.ace.coworker_thread.CoWorkerThread") as MockThread:
                mock_t = MagicMock()
                MockThread.return_value = mock_t
                mock_t.start = MagicMock()
                mock_t.join = MagicMock()

                instance_id = ctrl.launch(
                    problem_text="test run",
                    trigger_source="test",
                    trigger_ref="ctrl-01",
                )

        assert instance_id.startswith("ace-")
        assert len(instance_id) > 4

        conn = _open_db(ace_db_path)
        row = conn.execute(
            "SELECT state FROM ace_instances WHERE id = ?", (instance_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        # _run() progresses: pending → active → complete (threads mocked to no-op)
        assert row["state"] in ("pending", "active", "complete")
