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
import threading
import time
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
            return types.SimpleNamespace(steps=list(steps), communication={})

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


def _trust_the_gate(monkeypatch, score: float = 0.9) -> None:
    """Force the confidence gate open by patching get_trust_score to a trusted
    value at its source module.

    Historically four CoWorkerThread tests hung forever: every role defaults to
    trust 0.5 < TRUST_SUPERVISED (0.6), so _run_inner entered the confidence
    gate and polled an empty DB indefinitely. These tests do not exercise the
    gate, so we patch the score above the threshold. coworker_thread imports
    get_trust_score locally (``from ...trust_calibrator import get_trust_score``)
    so the patch must target the source module attribute.
    """
    monkeypatch.setattr(
        "icdev.tools.ace.trust_calibrator.get_trust_score",
        lambda role_id: score,
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
    _trust_the_gate(monkeypatch)  # role trusted → confidence gate does not fire
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

    _trust_the_gate(monkeypatch)  # isolate the required-step gate from the confidence gate
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

    _trust_the_gate(monkeypatch)  # skip the confidence gate; this test is about SOUL
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

    # Dispatch context is stored on _ace_context (renamed from _context to avoid
    # clobbering threading.Thread._context on Python 3.14).
    assert "soul_preamble" in thread._ace_context, "soul_preamble must be set in dispatch context"
    assert "## Identity & Values" in thread._ace_context["soul_preamble"]
    assert record == ["s1"]


def test_coworker_soul_preamble_empty_no_crash(ace_db, monkeypatch):
    """CoWorkerThread handles empty preamble (role with no SOUL.md) without crashing."""
    import icdev.tools.ace.soul_manager as _sm
    monkeypatch.setattr(_sm, "build_identity_preamble", lambda role_id: "")

    _trust_the_gate(monkeypatch)  # skip the confidence gate; this test is about SOUL
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
    assert thread._ace_context.get("soul_preamble", "") == ""
    assert record == ["s1"]


# ---------------------------------------------------------------------------
# Confidence gate — event-driven HITL wake (hcx-ace-05 / hcx-ace-09)
# ---------------------------------------------------------------------------


def _coworker_state(coworker_id: str) -> str | None:
    """Read ace_coworkers.state for a coworker (None if the row is absent)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT state FROM ace_coworkers WHERE id = ?", (coworker_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _wait_until(pred, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """Poll ``pred`` until it returns truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _low_trust_thread(monkeypatch, instance_id, coworker_id, role_id="ai_developer"):
    """Build a CoWorkerThread whose confidence gate WILL fire (trust 0.4 < 0.6).

    Seeds the instance + coworker rows, patches RoleLoader/StepExecutor so the
    post-gate step loop runs instantly, and returns (thread, record).
    """
    _seed(instance_id, [(coworker_id, role_id)])
    monkeypatch.setattr(
        "icdev.tools.ace.trust_calibrator.get_trust_score", lambda rid: 0.4
    )
    record: list[str] = []
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record)
    )
    monkeypatch.setattr(
        "icdev.tools.ace.coworker_thread.RoleLoader", _fake_loader_cls(["s1"])
    )

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []
    thread = CoWorkerThread(
        spec=_spec(coworker_id, role_id),
        instance_id=instance_id,
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    return thread, record


def test_confidence_gate_fires_sets_pending_and_registers_event(ace_db, monkeypatch):
    """A low-trust coworker enters hitl_pending and registers a wake Event."""
    import icdev.tools.ace.coworker_thread as ct

    instance_id, coworker_id = "ace-gate-fires", "cw-gate-fires"
    thread, _record = _low_trust_thread(monkeypatch, instance_id, coworker_id)

    runner = threading.Thread(target=thread._run_inner, daemon=True)
    runner.start()
    try:
        # State flips to hitl_pending and the wake Event is registered.
        assert _wait_until(lambda: _coworker_state(coworker_id) == "hitl_pending")
        assert _wait_until(lambda: coworker_id in ct._hitl_events)

        # A hitl_pending audit row exists and is unresolved.
        pending = ct.HITLGate.get_pending(coworker_id)
        assert pending, "expected an unresolved hitl_pending audit row"
        assert "low_confidence" in pending[0]["detail"]
    finally:
        thread.stop()  # sets stop_event AND signals the wake Event
        runner.join(timeout=5)
    assert not runner.is_alive(), "stop() must wake the HITL wait promptly"


def test_hitl_resolve_wakes_thread_under_poll_interval(ace_db, monkeypatch):
    """HITLGate.resolve sets the Event → the parked thread proceeds fast.

    The 30 s cross-process fallback means a busy-poll or fallback-only wake would
    take far longer; asserting the thread advances in < 10 s proves the in-process
    Event fired.
    """
    import icdev.tools.ace.coworker_thread as ct

    instance_id, coworker_id = "ace-gate-wake", "cw-gate-wake"
    thread, record = _low_trust_thread(monkeypatch, instance_id, coworker_id)

    runner = threading.Thread(target=thread._run_inner, daemon=True)
    runner.start()
    try:
        assert _wait_until(lambda: _coworker_state(coworker_id) == "hitl_pending")
        detail = ct.HITLGate.get_pending(coworker_id)[0]["detail"]

        t0 = time.monotonic()
        ct.HITLGate.resolve(coworker_id, detail, instance_id)

        # Thread wakes, clears the gate, runs its step, and finishes.
        assert _wait_until(
            lambda: _coworker_state(coworker_id) in ("working", "done"), timeout=10.0
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 10.0, f"wake took {elapsed:.2f}s — event-driven wake failed"
    finally:
        thread.stop()
        runner.join(timeout=5)

    assert record == ["s1"], "post-gate step loop must run after approval"


def test_auto_approve_roles_skips_gate(ace_db, monkeypatch):
    """A role in auto_approve_roles bypasses the confidence gate entirely."""
    import icdev.tools.ace.coworker_thread as ct

    instance_id, coworker_id, role_id = "ace-auto", "cw-auto", "ai_developer"
    _seed(instance_id, [(coworker_id, role_id)])
    # Trust is LOW — only the auto_approve_roles override lets it through.
    monkeypatch.setattr(
        "icdev.tools.ace.trust_calibrator.get_trust_score", lambda rid: 0.1
    )
    monkeypatch.setattr(
        ct,
        "_load_trust_overrides",
        lambda: {"initial_trust": {}, "auto_approve_roles": [role_id]},
    )
    record: list[str] = []
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record))
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.RoleLoader", _fake_loader_cls(["s1"]))

    bus = MagicMock()
    bus.poll_inbox.return_value = []
    thread = ct.CoWorkerThread(
        spec=_spec(coworker_id, role_id),
        instance_id=instance_id,
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    # No thread needed: the gate is skipped, so _run_inner returns synchronously.
    thread._run_inner()

    assert record == ["s1"]
    assert not ct.HITLGate.get_pending(coworker_id), "gate must not fire for auto-approved role"


def test_initial_trust_override_honored(ace_db, monkeypatch):
    """initial_trust seeds the gate above threshold, so it does not fire."""
    import icdev.tools.ace.coworker_thread as ct

    instance_id, coworker_id, role_id = "ace-init-trust", "cw-init-trust", "ai_developer"
    _seed(instance_id, [(coworker_id, role_id)])
    # Learned score is LOW, but the initial_trust override (0.9) wins for the gate.
    monkeypatch.setattr(
        "icdev.tools.ace.trust_calibrator.get_trust_score", lambda rid: 0.1
    )
    monkeypatch.setattr(
        ct,
        "_load_trust_overrides",
        lambda: {"initial_trust": {role_id: 0.9}, "auto_approve_roles": []},
    )
    record: list[str] = []
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.StepExecutor", _make_executor(record))
    monkeypatch.setattr("icdev.tools.ace.coworker_thread.RoleLoader", _fake_loader_cls(["s1"]))

    bus = MagicMock()
    bus.poll_inbox.return_value = []
    thread = ct.CoWorkerThread(
        spec=_spec(coworker_id, role_id),
        instance_id=instance_id,
        message_bus=bus,
        trust_kernel=MagicMock(),
    )
    thread._run_inner()

    assert record == ["s1"]
    assert not ct.HITLGate.get_pending(coworker_id), "initial_trust >= 0.6 must skip the gate"


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
        def submit(self, fn, *args, **kwargs):
            captured["fn"] = fn
            captured["args"] = args
            captured["kwargs"] = kwargs
            return None

    ctrl._executor = _RecordOnlyExecutor()

    instance_id = ctrl.launch("build a data pipeline", "cli", "ref-1")

    assert re.match(r"^ace-[0-9a-f]{12}$", instance_id)
    assert captured["fn"] == ctrl._run
    assert captured["args"][0] == instance_id

    # launch() synchronously writes a 'pending' stub row before submitting the
    # executor task — proves it doesn't block on the full pipeline but does
    # ensure the instance is immediately visible to status checks.
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, state FROM ace_instances WHERE id = ?", (instance_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] == "pending"

    # --- Phase 2: drive the run synchronously, frozen at assembling ---
    class _FakeClassifier:
        def __init__(self, text):
            self.text = text

        def run(self):
            from icdev.tools.ace.problem_classifier import RoleSlot, TeamManifest

            return TeamManifest(slots=[RoleSlot("ai_developer", 1)])

    # A real threading.Thread so the controller's new dispatch contract works:
    # per-role semaphore wraps .run(), threads are started directly, and the
    # controller-level join loop calls .join(timeout=...) / .is_alive().  run()
    # is a no-op so the "thread" completes immediately.
    class _FakeThread(threading.Thread):
        def __init__(self, **kwargs):
            super().__init__(daemon=True)
            self.spec = kwargs.get("spec")
            self._stop_event = threading.Event()

        def run(self):
            pass

        def stop(self):
            self._stop_event.set()

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
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)
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


def test_controller_launch_threads_role_ids_to_run(ace_db):
    """Regression: launch() must pass role_ids to _run as the ``role_ids``
    parameter, not as the preceding ``webhook_url`` positional. The original
    submit call passed role_ids as the 7th positional arg, which landed in
    ``_run``'s ``webhook_url`` slot — so ``_run`` always saw ``role_ids=None``
    and fell back to the problem classifier even when explicit roles were
    requested. The fix submits role_ids as a keyword arg."""
    from icdev.tools.ace.controller import ACEController

    ctrl = ACEController()
    captured: dict = {}

    class _KwargAwareExecutor:
        def submit(self, fn, *args, **kwargs):
            captured["fn"] = fn
            captured["args"] = args
            captured["kwargs"] = kwargs
            return None

    ctrl._executor = _KwargAwareExecutor()

    ctrl.launch(
        "build a data pipeline", "cli", "ref-roles", role_ids=["agent_developer"]
    )

    assert captured["fn"] == ctrl._run
    # role_ids must arrive as the `role_ids` kwarg, not silently swallowed by
    # the `webhook_url` positional slot.
    assert captured["kwargs"].get("role_ids") == ["agent_developer"]
    # And the positional slots are exactly the 6 leading scalars — role_ids is
    # NOT among them (it is not passed positionally into webhook_url's place).
    assert len(captured["args"]) == 6
