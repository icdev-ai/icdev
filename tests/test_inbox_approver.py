# CUI // SP-CTI
"""The inbox approver, injected into the EXISTING gate (agov-inbox-02).

Four things have to be true, and each has a test class here:

  1. Injected into ``build_approval_hook``, an irreversible tool call creates a
     ``pending`` item and BLOCKS. Not "is recorded and proceeds" — blocks.
  2. A resolution written by another PROCESS wakes the waiter. The in-process
     ``threading.Event`` is an optimisation; the DB poll is the load-bearing
     path, and the test proves the wake came from the poll by making the Event
     record every ``set()`` and asserting there were none.
  3. An expired item yields ``ApprovalDecision(approved=False)`` with a reason
     naming the expiry. A timeout that allowed would turn the whole feature into
     an auto-approver on exactly the calls that reach an approver at all.
  4. ``approval_gate.py``'s public signature is unchanged. The point of this
     card is that ``Approver`` was already an injectable seam — if the gate had
     to change, the seam was not there.

Both tables come from their own migrations' DDL rather than a hand-written
schema, so a column added to one and not the other fails here instead of at
runtime inside a swallowed exception (CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import inspect
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import approval_gate, inbox_approver as approver_mod
from tools.agent_runtime.approval_gate import (
    ApprovalDecision,
    ApprovalRequest,
    build_approval_hook,
    classify,
)
from tools.agent_runtime.approval_inbox import (
    STATE_EXPIRED,
    STATE_PENDING,
    STATE_RESOLVED,
    TABLE,
    expire_due,
    get,
    list_pending,
    resolve,
)
from tools.agent_runtime.inbox_approver import (
    inbox_approver,
    make_inbox_approver,
    wait_for_resolution,
    wake,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# An irreversible tool by the policy's own list, and an argument value that must
# never reach either table. `git_push` is enumerated irreversible and is not a
# command_tool, so the classification does not depend on pattern matching.
TOOL = "git_push"
SECRET = "s3cr3t-token-DO-NOT-PERSIST"
TOOL_INPUT = {"remote": f"https://user:{SECRET}@example.invalid/repo.git", "branch": "main"}

# Fast enough that the suite does not crawl, slow enough that "it blocked" is
# observable rather than a race.
POLL = 0.05
GRACE = 10.0


# ---------------------------------------------------------------------------
# Schema — from the migrations themselves
# ---------------------------------------------------------------------------
def _approval_items_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260809203855_agov_approval_items" / "up.sql"
    ).read_text(encoding="utf-8")


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module the store and ``record_decision`` actually resolve from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    — two different objects. Both import it from inside their functions, so
    patching the canonical module would patch nothing and every assertion below
    would be asserting its own no-op.
    """
    return sys.modules["tools.db.storage"]


@pytest.fixture
def inbox_db(monkeypatch, tmp_path):
    """Both real tables in one file DB, behind the production %s translation.

    A FILE rather than an in-memory DB, and a FRESH connection per call rather
    than one shared object, because the approver blocks on one thread while the
    test resolves on another — which is the whole point of the feature and is
    exactly what a single ``check_same_thread`` connection forbids. Opening per
    call is also what production does.
    """
    db_path = tmp_path / "inbox.db"
    boot = sqlite3.connect(str(db_path))
    boot.executescript(_approval_items_ddl())
    boot.executescript(_approval_log_ddl())
    boot.commit()
    boot.close()

    def _open(*_a, **_k):
        return translating(sqlite3.connect(str(db_path), timeout=30.0))

    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", _open)
    monkeypatch.setattr(storage, "table_exists", lambda _c, _t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    monkeypatch.delenv("ICDEV_APPROVAL_INBOX_TIMEOUT", raising=False)
    monkeypatch.delenv("ICDEV_APPROVAL_INBOX_POLL", raising=False)
    monkeypatch.delenv("ICDEV_APPROVAL_INBOX", raising=False)
    yield db_path


def _rows(db_path: Path, table: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _request(tool_name: str = TOOL, tool_input: Optional[dict] = None) -> ApprovalRequest:
    payload = TOOL_INPUT if tool_input is None else tool_input
    return ApprovalRequest(
        tool_name=tool_name,
        tool_input=payload,
        classification=classify(tool_name, payload),
        actor="agent",
    )


def _await_pending(db_path: Path, *, timeout: float = GRACE) -> dict[str, Any]:
    """Block until exactly one pending row exists. Fails the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [r for r in _rows(db_path, TABLE) if r["state"] == STATE_PENDING]
        if rows:
            return rows[0]
        time.sleep(0.01)
    pytest.fail(f"no pending {TABLE} row appeared within {timeout}s")


class _Runner(threading.Thread):
    """Runs a callable on another thread and keeps its result."""

    def __init__(self, fn, *args):
        super().__init__(daemon=True)
        self._fn, self._args = fn, args
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.finished = threading.Event()

    def run(self) -> None:
        try:
            self.result = self._fn(*self._args)
        except BaseException as exc:  # noqa: BLE001 — re-raised by the assertion
            self.error = exc
        finally:
            self.finished.set()

    def join_ok(self, timeout: float = GRACE) -> Any:
        self.finished.wait(timeout)
        assert self.finished.is_set(), "the approver never returned"
        if self.error is not None:
            raise self.error
        return self.result


# ---------------------------------------------------------------------------
# 1. Injected into the existing gate: gated, queued, blocked
# ---------------------------------------------------------------------------
class TestGatedAndBlocking:
    def _hook(self, **kwargs):
        return build_approval_hook(
            approver=make_inbox_approver(
                inbox="ops", timeout_seconds=GRACE, poll_seconds=POLL, **kwargs
            ),
            mode="enforce",
            actor="test-operator",
            # The headless hard-block list is a separate mechanism with its own
            # tests; consulting it here would decide the call before the
            # approver ever ran.
            consult_pre_tool_check=False,
        )

    def test_irreversible_call_queues_a_pending_item_and_blocks(self, inbox_db):
        hook = self._hook()
        runner = _Runner(hook, TOOL, TOOL_INPUT)
        runner.start()

        row = _await_pending(inbox_db)
        assert row["tool_name"] == TOOL
        assert row["tier"] == "irreversible"
        assert row["inbox"] == "ops"
        assert row["origin"] == "sag"
        assert row["expires_at"], "the item must carry its own deadline for the sweep"

        # BLOCKED: still parked several poll intervals later, with no verdict.
        assert not runner.finished.wait(POLL * 6), "the approver did not block"

        # Now answer it. The hook returns None only when the call may proceed.
        assert resolve(row["item_id"], approved=True, resolved_by="alice") is not None
        assert runner.join_ok() is None
        assert get(row["item_id"]).state == STATE_RESOLVED

    def test_a_denial_halts_the_call_with_the_gate_s_own_message(self, inbox_db):
        hook = self._hook()
        runner = _Runner(hook, TOOL, TOOL_INPUT)
        runner.start()
        row = _await_pending(inbox_db)

        resolve(row["item_id"], approved=False, resolved_by="bob", reason="not today")
        blocked = runner.join_ok()

        assert isinstance(blocked, str)
        assert "BLOCKED by the approval gate" in blocked
        assert row["item_id"] in blocked

    def test_a_reversible_call_is_never_queued(self, inbox_db):
        """The approver is not consulted below the approval tiers — unchanged."""
        hook = self._hook()
        assert hook("read_file", {"path": "README.md"}) is None
        assert _rows(inbox_db, TABLE) == []

    def test_no_argument_value_reaches_either_table(self, inbox_db):
        hook = self._hook()
        runner = _Runner(hook, TOOL, TOOL_INPUT)
        runner.start()
        row = _await_pending(inbox_db)
        resolve(row["item_id"], approved=True, resolved_by="alice")
        runner.join_ok()

        blob = repr(_rows(inbox_db, TABLE)) + repr(_rows(inbox_db, "agent_approval_log"))
        assert SECRET not in blob
        assert "example.invalid" not in blob

    def test_an_unqueueable_ask_fails_closed(self, inbox_db, monkeypatch):
        """A queue that cannot accept the ask denies — it never proceeds."""
        monkeypatch.setattr(_storage_module(), "table_exists", lambda _c, _t: False)
        hook = self._hook()
        blocked = hook(TOOL, TOOL_INPUT)
        assert isinstance(blocked, str)
        assert "approval inbox unavailable" in blocked
        assert "failing closed" in blocked


# ---------------------------------------------------------------------------
# 2. Out-of-process resolution — the poll path, not the Event
# ---------------------------------------------------------------------------
class _RecordingEvent(threading.Event):
    """A threading.Event that counts the wakeups it was actually given."""

    def __init__(self) -> None:
        super().__init__()
        self.set_calls = 0

    def set(self) -> None:  # noqa: D102
        self.set_calls += 1
        super().set()


class TestOutOfProcessResolution:
    def test_the_poll_path_alone_observes_another_process_s_answer(
        self, inbox_db, monkeypatch
    ):
        """Nobody sets the in-process Event, and the waiter still wakes.

        ``approval_inbox.resolve`` knows nothing about this module's wake
        registry — which is precisely the cross-process case: the interpreter
        that answers is not the one that asked, so it cannot set an Event here.
        Substituting a counting Event turns "it used the poll" from a plausible
        story into an assertion.
        """
        events: dict[str, _RecordingEvent] = {}

        def _recording_event(item_id: str) -> _RecordingEvent:
            return events.setdefault(item_id, _RecordingEvent())

        monkeypatch.setattr(approver_mod, "_get_wake_event", _recording_event)

        approve = make_inbox_approver(timeout_seconds=GRACE, poll_seconds=POLL)
        runner = _Runner(approve, _request())
        runner.start()
        row = _await_pending(inbox_db)

        # Let it park through several polls before answering, so the wake cannot
        # be the first pre-wait read.
        time.sleep(POLL * 4)
        resolve(row["item_id"], approved=True, resolved_by="out-of-process")

        decision = runner.join_ok()
        assert isinstance(decision, ApprovalDecision)
        assert decision.approved is True
        assert decision.actor == "out-of-process"
        assert row["item_id"] in decision.reason

        ev = events[row["item_id"]]
        assert ev.set_calls == 0, (
            "the waiter was woken by an in-process Event; this test is supposed "
            "to prove the DB poll path works on its own"
        )

    def test_an_in_process_resolver_may_still_wake_it_early(self, inbox_db):
        """The Event is the optimisation, and it is wired the right way round."""
        approve = make_inbox_approver(timeout_seconds=GRACE, poll_seconds=GRACE)
        runner = _Runner(approve, _request())
        runner.start()
        row = _await_pending(inbox_db)

        resolve(row["item_id"], approved=True, resolved_by="alice")
        # poll_seconds == GRACE, so without the Event this would not return
        # before join_ok's own timeout.
        assert wake(row["item_id"]) is True
        assert runner.join_ok(timeout=GRACE / 2).approved is True

    def test_waking_an_unknown_item_is_a_no_op(self, inbox_db):
        assert wake("ai-nobody-is-waiting") is False

    def test_a_vanished_item_is_not_an_approval(self, inbox_db):
        """An item that cannot be read is denied, not allowed."""
        assert wait_for_resolution(
            "ai-does-not-exist", timeout_seconds=0.2, poll_seconds=POLL
        ) is None


# ---------------------------------------------------------------------------
# 3. Expiry is a DENIAL — the invariant the feature lives or dies on
# ---------------------------------------------------------------------------
class TestTimeoutFailsClosed:
    def test_a_lapsed_deadline_denies_and_names_the_expiry(self, inbox_db):
        approve = make_inbox_approver(timeout_seconds=0.2, poll_seconds=POLL)
        decision = approve(_request())

        assert isinstance(decision, ApprovalDecision)
        assert decision.approved is False
        assert "EXPIRED" in decision.reason
        assert "timeout is never an approval" in decision.reason

        rows = _rows(inbox_db, TABLE)
        assert len(rows) == 1
        assert rows[0]["state"] == STATE_EXPIRED
        assert rows[0]["resolution"] == "denied"
        assert list_pending() == []

    def test_the_expiry_is_recorded_as_a_denied_decision(self, inbox_db):
        approve = make_inbox_approver(timeout_seconds=0.2, poll_seconds=POLL)
        approve(_request())

        log = _rows(inbox_db, "agent_approval_log")
        assert log, "an expiry is a decision and must be audited"
        assert all(r["decision"] == "denied" for r in log)
        assert any("expired" in (r["reason"] or "").lower() for r in log)

    def test_an_item_expired_by_the_sweep_denies_the_waiter(self, inbox_db):
        """The cross-process sweep reaches the same verdict the waiter would.

        The waiter's own budget is generous here; the item's ``expires_at`` is
        not. ``expire_due`` runs in the sweep's stead and the waiter observes a
        terminal state through the poll.
        """
        approve = make_inbox_approver(timeout_seconds=GRACE, poll_seconds=POLL)
        runner = _Runner(approve, _request())
        runner.start()
        row = _await_pending(inbox_db)

        # Backdate the deadline, then sweep — no in-process Event is set.
        conn = sqlite3.connect(str(inbox_db))
        try:
            conn.execute(
                f"UPDATE {TABLE} SET expires_at = ? WHERE item_id = ?",
                ("2000-01-01T00:00:00+00:00", row["item_id"]),
            )
            conn.commit()
        finally:
            conn.close()
        assert [i.item_id for i in expire_due()] == [row["item_id"]]

        decision = runner.join_ok()
        assert decision.approved is False
        assert "EXPIRED" in decision.reason

    def test_the_gate_halts_the_call_on_expiry(self, inbox_db):
        """End to end: through the real hook, a timeout does not run the tool."""
        hook = build_approval_hook(
            approver=make_inbox_approver(timeout_seconds=0.2, poll_seconds=POLL),
            mode="enforce",
            consult_pre_tool_check=False,
        )
        blocked = hook(TOOL, TOOL_INPUT)
        assert isinstance(blocked, str), "an expired ask must not return None"
        assert "BLOCKED by the approval gate" in blocked
        assert "EXPIRED" in blocked

    def test_a_race_at_the_deadline_honours_the_real_answer(self, inbox_db, monkeypatch):
        """Approved in the gap between the deadline and the expiry UPDATE.

        Not fail-open: the approval is a real one, by a real actor, and
        ``expire`` refused to move an already-settled item rather than this
        module assuming anything.
        """
        real_expire = approver_mod.expire

        def _expire_after_someone_answered(item_id, **kwargs):
            resolve(item_id, approved=True, resolved_by="alice", reason="just in time")
            return real_expire(item_id, **kwargs)

        monkeypatch.setattr(approver_mod, "expire", _expire_after_someone_answered)

        decision = make_inbox_approver(timeout_seconds=0.2, poll_seconds=POLL)(_request())
        assert decision.approved is True
        assert decision.actor == "alice"
        assert get(_rows(inbox_db, TABLE)[0]["item_id"]).state == STATE_RESOLVED

    def test_a_zero_or_negative_timeout_falls_back_rather_than_disabling_the_wait(self):
        """A misconfigured budget must not become "never wait" — or "wait forever"."""
        assert approver_mod.resolve_timeout(0) == approver_mod.DEFAULT_TIMEOUT_SECONDS
        assert approver_mod.resolve_timeout(-5) == approver_mod.DEFAULT_TIMEOUT_SECONDS
        assert approver_mod.resolve_timeout("nonsense") == approver_mod.DEFAULT_TIMEOUT_SECONDS
        assert approver_mod.resolve_poll(0) == approver_mod.DEFAULT_POLL_SECONDS

    def test_the_module_level_approver_is_a_drop_in(self, inbox_db, monkeypatch):
        """``inbox_approver`` itself, with env config and no factory call."""
        monkeypatch.setenv("ICDEV_APPROVAL_INBOX_TIMEOUT", "0.2")
        monkeypatch.setenv("ICDEV_APPROVAL_INBOX_POLL", str(POLL))
        monkeypatch.setenv("ICDEV_APPROVAL_INBOX", "ops")

        decision = inbox_approver(_request())
        assert decision.approved is False
        assert "EXPIRED" in decision.reason
        assert _rows(inbox_db, TABLE)[0]["inbox"] == "ops"


# ---------------------------------------------------------------------------
# 4. The gate did not have to change
# ---------------------------------------------------------------------------
# Frozen at the commit this card branched from. The premise of agov-inbox-02 is
# that ``Approver`` was ALREADY an injectable seam, so an inbox approver is a
# drop-in. If any line here has to be edited to make the suite pass, that premise
# was wrong and the change needs saying out loud rather than absorbing.
_FROZEN_SIGNATURES = {
    "classify": "(tool_name: 'str', tool_input: 'Any' = None, *, policy: 'Optional[dict[str, Any]]' = None) -> 'Classification'",
    "console_approver": "(request: 'ApprovalRequest') -> 'ApprovalDecision'",
    "deny_all_approver": "(request: 'ApprovalRequest') -> 'ApprovalDecision'",
    "resolve_mode": "(mode: 'Optional[str]' = None) -> 'str'",
    "resolve_actor": "(actor: 'Optional[str]' = None) -> 'str'",
    "flatten_input": "(tool_input: 'Any') -> 'str'",
    "load_policy": "(*, refresh: 'bool' = False) -> 'dict[str, Any]'",
    "build_approval_hook": (
        "(*, approver: 'Optional[Approver]' = None, mode: 'Optional[str]' = None, "
        "actor: 'Optional[str]' = None, policy: 'Optional[dict[str, Any]]' = None, "
        "session_id: 'str' = '', on_event: 'Optional[Callable[[GateEvent], None]]' = None, "
        "consult_pre_tool_check: 'bool' = True) -> 'Callable[[str, dict[str, Any]], Optional[str]]'"
    ),
}

_FROZEN_DATACLASS_FIELDS = {
    "ApprovalRequest": ["tool_name", "tool_input", "classification", "actor"],
    "ApprovalDecision": ["approved", "reason", "actor"],
    "Classification": ["tool_name", "tier", "rule", "detail", "requires_approval"],
    "GateEvent": [
        "tool_name", "tier", "requires_approval", "allowed", "reason", "actor",
        "recorded", "extra",
    ],
}


class TestGateSignatureUnchanged:
    @pytest.mark.parametrize("name, expected", sorted(_FROZEN_SIGNATURES.items()))
    def test_public_callable_signature(self, name, expected):
        assert str(inspect.signature(getattr(approval_gate, name))) == expected

    @pytest.mark.parametrize("name, fields", sorted(_FROZEN_DATACLASS_FIELDS.items()))
    def test_public_dataclass_fields(self, name, fields):
        import dataclasses

        assert [f.name for f in dataclasses.fields(getattr(approval_gate, name))] == fields

    def test_the_approver_protocol_itself(self):
        """The seam this card was supposed to use, unchanged.

        Compared structurally rather than by ``str()``: 3.11 renders the union
        as ``typing.Union[bool, X]`` and 3.14 as ``bool | X``, and a suite that
        fails on a Python upgrade is not evidence about this gate.
        """
        import typing

        args = typing.get_args(approval_gate.Approver)
        assert args[0] == [approval_gate.ApprovalRequest]
        assert set(typing.get_args(args[1])) == {bool, approval_gate.ApprovalDecision}

    def test_the_gate_does_not_import_the_inbox(self):
        """No fork, no back-reference, no circular dependency.

        The dependency runs one way only: the approver knows about the gate. A
        gate that had to know about an inbox would not have been an injectable
        seam in the first place. Checked over the IMPORTS rather than the text —
        the gate's prose may well mention the inbox, and a substring search
        would call that a violation.
        """
        import ast

        tree = ast.parse(
            (REPO_ROOT / "tools" / "agent_runtime" / "approval_gate.py").read_text(
                encoding="utf-8"
            )
        )
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not [m for m in imported if "inbox" in m], imported

    def test_the_default_approver_is_still_the_console_prompt(self):
        """Injecting an inbox approver is opt-in; nothing was made the default."""
        src = inspect.getsource(approval_gate.build_approval_hook)
        assert "approver or console_approver" in src

    def test_record_decision_stays_backward_compatible(self):
        """Named rather than frozen, and here is why.

        This card changes nothing in ``approval_gate.py``. agov-inbox-01 does:
        ``record_decision`` gains ``arg_keys`` / ``input_sha256``, both
        defaulting to ``None`` → derived from ``tool_input`` exactly as before,
        so a deferred resolution can emit a faithful row instead of a lossy
        reconstruction. Freezing that signature byte-for-byte here would make
        THIS suite fail for a change on ANOTHER branch, so the assertion is the
        property that actually matters: every parameter the gate had keeps its
        name and position, and anything added is optional. A required new
        parameter would break every existing caller and must not pass silently.
        """
        params = list(inspect.signature(approval_gate.record_decision).parameters.values())
        original = [
            "tool_name", "tool_input", "classification", "decision", "mode", "session_id",
        ]
        assert [p.name for p in params][: len(original)] == original
        for extra in params[len(original):]:
            assert extra.default is not inspect.Parameter.empty, (
                f"record_decision gained a REQUIRED parameter {extra.name!r}; "
                "every existing caller breaks"
            )
