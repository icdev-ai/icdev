# CUI // SP-CTI
"""Runtime tests for the ACE (ANVIL Co-Worker Engine) canvas.

Covers the three runtime building blocks:
  * MessageBus  — inter-coworker messaging over the agent mailbox + ace_messages
  * CoWorkerThread — per-coworker step execution loop
  * ACEController — non-blocking launch + instance persistence

Canonical schema of record: ``icdev.tools.ace.db.init_db`` (id / state / trust_tier).
All ACE traffic is persisted to a dedicated temp SQLite DB pointed to by
``ICDEV_ACE_DB_URL`` so ``get_canvas_connection`` resolves to it.

Run: pytest tests/test_ace_runtime.py -v
"""
from __future__ import annotations

import json
import re
import types
from unittest.mock import MagicMock

import pytest

from icdev.tools.ace.message_bus import MessageBus, NegotiationFailedError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    """Point ICDEV_ACE_DB_URL at a fresh temp SQLite DB with ACE tables created."""
    db_path = tmp_path / "ace_canvas.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))

    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return db_path


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed(instance_id: str, coworkers: list[tuple[str, str]], state: str = "idle") -> None:
    """Insert one ace_instances row and the given (coworker_id, role_id) rows."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, trust_tier) "
            "VALUES (?, ?, ?, 'assembling', 'yellow')",
            (instance_id, instance_id, coworkers[0][1] if coworkers else ""),
        )
        for cw_id, role_id in coworkers:
            conn.execute(
                "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, trust_tier) "
                "VALUES (?, ?, ?, ?, ?, 'yellow')",
                (cw_id, instance_id, role_id, role_id, state),
            )
        conn.commit()
    finally:
        conn.close()


def _make_executor(record: list, raise_map: dict | None = None):
    """Return a StepExecutor stand-in class whose .run() records step IDs.

    The class is instantiable with no args (matching ``StepExecutor()``) and
    exposes ``run(step, context, spec, trust_kernel)`` — the real contract the
    CoWorkerThread relies on.  This is the monkeypatch contract that fixes the
    historical "'dict' object has no attribute 'run'" failure: patch the *class*,
    not an instance/dict.
    """
    raise_map = raise_map or {}

    class _RecordingExecutor:
        def run(self, step, context, spec, trust_kernel):  # noqa: D401
            step_id = step.get("id")
            record.append(step_id)
            if step_id in raise_map:
                raise raise_map[step_id]
            return {"step": step_id}

    return _RecordingExecutor


def _fake_loader_cls(steps):
    """Return a RoleLoader stand-in class whose get_role() yields fixed steps."""

    class _Loader:
        def __init__(self, *args, **kwargs):
            pass

        def get_role(self, role_id):
            return types.SimpleNamespace(steps=list(steps))

    return _Loader


def _spec(coworker_id: str = "cw-1", role_id: str = "ai_developer"):
    from icdev.tools.ace.team_assembler import CoWorkerSpec

    return CoWorkerSpec(
        coworker_id=coworker_id,
        role_id=role_id,
        role_slot=role_id,
        mailbox_id=f"mailbox:{coworker_id}",
        llm_function="code_generation",
        tool_permissions=[],
        trust_tier="yellow",
    )


# ---------------------------------------------------------------------------
# MessageBus
# ---------------------------------------------------------------------------


def test_message_bus_send_inserts_ace_messages(ace_db, monkeypatch):
    """send() writes one ace_messages row and returns the mailbox id."""
    instance_id = "ace-inst-send"
    _seed(instance_id, [("cw-sender", "ai_developer"), ("cw-qa", "qa_manager")])

    sent: list[dict] = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return "mbox-msg-1"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)
    msg_id = bus.send("cw-sender", "qa_manager", "cw_verify_request", {"x": 1})

    assert msg_id == "mbox-msg-1"
    # mailbox.send was invoked with the ACE subject convention
    assert len(sent) == 1
    assert sent[0]["subject"] == "ACE:cw_verify_request"
    assert sent[0]["message_type"] == "notification"

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT message_type, coworker_id, content FROM ace_messages WHERE instance_id = ?",
            (instance_id,),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "cw_verify_request"
    assert rows[0][1] == "cw-sender"
    assert json.loads(rows[0][2]) == {"x": 1}


def test_message_bus_broadcast_fans_out(ace_db, monkeypatch):
    """broadcast() reaches every coworker except the sender."""
    instance_id = "ace-inst-bcast"
    _seed(
        instance_id,
        [("cw-sender", "ai_developer"), ("cw-a", "qa_manager"), ("cw-b", "reviewer")],
    )

    recipients: list[str] = []
    counter = {"n": 0}

    def fake_send(**kwargs):
        recipients.append(kwargs["to_agent_id"])
        counter["n"] += 1
        return f"mbox-{counter['n']}"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)
    message_ids = bus.broadcast("cw-sender", "cw_broadcast", {"event": "done"})

    assert len(message_ids) == 2  # 2 coworkers, sender excluded
    assert set(recipients) == {"cw-a", "cw-b"}
    assert "cw-sender" not in recipients


def test_message_bus_negotiate_accept(ace_db, monkeypatch):
    """negotiate() returns accepted=True when the peer accepts in round 1."""
    instance_id = "ace-inst-neg-ok"
    _seed(instance_id, [("cw-init", "ai_developer"), ("cw-peer", "qa_manager")])

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", lambda **kw: "mbox-x")

    bus = MessageBus(instance_id)
    monkeypatch.setattr(
        bus,
        "_poll_negotiate_reply",
        lambda coworker_id, timeout_s: [
            {"subject": "ACE:cw_negotiate_accept", "body": json.dumps({"agreed": True})}
        ],
    )

    result = bus.negotiate("cw-init", "qa_manager", {"price": 100})

    assert result["accepted"] is True
    assert result["rounds"] == 1
    assert result["result"] == {"agreed": True}


def test_message_bus_negotiate_max_rounds(ace_db, monkeypatch):
    """negotiate() raises NegotiationFailedError after max_rounds with no accept."""
    instance_id = "ace-inst-neg-fail"
    _seed(instance_id, [("cw-init", "ai_developer"), ("cw-peer", "qa_manager")])

    send_calls = {"n": 0}

    def fake_send(**kwargs):
        send_calls["n"] += 1
        return "mbox-y"

    monkeypatch.setattr("icdev.tools.agent.mailbox.send", fake_send)

    bus = MessageBus(instance_id)
    # No reply ever arrives → every round exhausts without consensus.
    monkeypatch.setattr(bus, "_poll_negotiate_reply", lambda coworker_id, timeout_s: [])

    with pytest.raises(NegotiationFailedError):
        bus.negotiate("cw-init", "qa_manager", {"x": 1}, max_rounds=3)

    # One proposal sent per round.
    assert send_calls["n"] == 3


# ---------------------------------------------------------------------------
# CoWorkerThread
# ---------------------------------------------------------------------------


def test_coworker_thread_step_sequence(ace_db, monkeypatch):
    """The thread runs role steps in order via StepExecutor, then broadcasts done."""
    record: list[str] = []
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record)
    )
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.RoleLoader",
        _fake_loader_cls(["s1", "s2", "s3"]),
    )

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-seq"),
        instance_id="ace-inst-seq",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    thread._run_inner()

    assert record == ["s1", "s2", "s3"]
    bus.broadcast.assert_called_once()


def test_coworker_thread_trust_denied(ace_db, monkeypatch):
    """A TrustKernelDeniedError on a required step halts the thread (no later steps)."""
    from icdev.tools.ace.step_executor import TrustKernelDeniedError

    record: list[str] = []
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.StepExecutor",
        _make_executor(record, raise_map={"s1": TrustKernelDeniedError("denied")}),
    )
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.RoleLoader",
        _fake_loader_cls([{"id": "s1", "required": True}, {"id": "s2"}]),
    )

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-deny"),
        instance_id="ace-inst-deny",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    # Simulate the operator never resolving the HITL gate / stop signalled.
    thread._handle_hitl_required = lambda step, exc: False

    thread._run_inner()

    assert record == ["s1"]  # halted at the denied required step
    bus.broadcast.assert_not_called()


def test_coworker_soul_preamble_injected_to_context(ace_db, monkeypatch):
    """CoWorkerThread injects soul preamble containing '## Identity & Values' into context."""
    fake_preamble = (
        "---\n"
        "**[NOVA SOUL — ai_developer]** Identity context injected by soul_manager.\n\n"
        "## Identity & Values\nBe helpful, precise, and cautious.\n"
        "---\n"
    )

    import icdev.tools.ace.soul_manager as _sm
    monkeypatch.setattr(_sm, "build_identity_preamble", lambda role_id: fake_preamble)

    record: list[str] = []
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record))
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.RoleLoader", _fake_loader_cls(["s1"]))

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-soul-inject"),
        instance_id="ace-soul-inject",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    thread._run_inner()

    assert "soul_preamble" in thread._context, "soul_preamble must be set in dispatch context"
    assert "## Identity & Values" in thread._context["soul_preamble"]
    assert record == ["s1"]


def test_coworker_soul_preamble_empty_no_crash(ace_db, monkeypatch):
    """CoWorkerThread handles empty preamble (role with no SOUL.md) without crashing."""
    import icdev.tools.ace.soul_manager as _sm
    monkeypatch.setattr(_sm, "build_identity_preamble", lambda role_id: "")

    record: list[str] = []
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record))
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.RoleLoader", _fake_loader_cls(["s1"]))

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-soul-empty"),
        instance_id="ace-soul-empty",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    thread._run_inner()

    # Empty preamble → soul_preamble not set in context; no crash
    assert thread._context.get("soul_preamble", "") == ""
    assert record == ["s1"]


# ---------------------------------------------------------------------------
# ACEController
# ---------------------------------------------------------------------------


def test_controller_launch_returns_instance_id(ace_db, monkeypatch):
    """launch() is non-blocking (returns an id immediately) and the run path
    persists an ace_instances row at state=assembling."""
    from icdev.tools.ace.controller import ACEController

    # --- Phase 1: non-blocking — submit is recorded but not executed ---
    ctrl = ACEController()
    captured: dict = {}

    class _RecordOnlyExecutor:
        def submit(self, fn, *args):
            captured["fn"] = fn
            captured["args"] = args
            return None

    ctrl._executor = _RecordOnlyExecutor()

    instance_id = ctrl.launch("build a data pipeline", "cli", "ref-1")

    assert re.match(r"^ace-[0-9a-f]{12}$", instance_id)
    assert captured["fn"] == ctrl._run
    assert captured["args"][0] == instance_id

    # _run never executed → no row was written (proves it didn't block on the work).
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM ace_instances WHERE id = ?", (instance_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is None

    # --- Phase 2: drive the run synchronously, frozen at assembling ---
    class _FakeClassifier:
        def __init__(self, text):
            self.text = text

        def run(self):
            from icdev.tools.ace.problem_classifier import RoleSlot, TeamManifest

            return TeamManifest(slots=[RoleSlot("ai_developer", 1)])

    class _FakeThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(
        "icdev.tools.ace.problem_classifier.ProblemClassifierLens", _FakeClassifier
    )
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.CoWorkerThread", _FakeThread
    )

    states: list[str] = []
    monkeypatch.setattr(
        ctrl, "_set_instance_state", lambda iid, state: states.append(state)
    )

    class _SyncExecutor:
        def submit(self, fn, *args):
            fn(*args)
            return None

    ctrl._executor = _SyncExecutor()

    instance_id2 = ctrl.launch("build a data pipeline", "cli", "ref-2")

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT state FROM ace_instances WHERE id = ?", (instance_id2,)
        ).fetchone()
        coworkers = conn.execute(
            "SELECT id FROM ace_coworkers WHERE instance_id = ?", (instance_id2,)
        ).fetchall()
    finally:
        conn.close()

    assert row is not None
    # TeamAssembler persists state='assembling'; _set_instance_state is stubbed,
    # so the row stays at assembling rather than advancing to active/complete.
    assert row[0] == "assembling"
    assert len(coworkers) == 1
    # The controller still attempted the normal lifecycle transitions.
    assert "active" in states and "complete" in states
