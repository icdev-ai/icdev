# CUI // SP-CTI
"""ACE and workflow_hitl in one approval queue (agov-inbox-05).

The adapters are a MIRROR, never a replacement, and the four things that has to
mean each have a class here:

  1. An ACE ``hitl_pending`` shows up in the unified inbox, once, through the
     seam every ACE gate already writes.
  2. Answering it in the inbox RELEASES the parked ``CoWorkerThread`` — proved
     with a real thread parked in the real ``_wait_for_hitl_resolution``, not by
     asserting a row was written. A mirror that collects answers nobody acts on
     is worse than no mirror: the operator believes the agent was unblocked.
  3. Answering it the old way — ``POST /api/ace/<id>/hitl``, the route the ACE
     UI's Approve button calls — settles the mirrored item, so the queue never
     shows an ask that is already decided.
  4. ``ace_audit_log`` stays APPEND-ONLY. Every statement the flow issues against
     the ACE database is captured and asserted to be an INSERT; the pause row is
     still there, byte for byte, after the resolution.

``tools/integration/approval_manager.py`` is explicitly out of scope — different
lifetime, multi-reviewer semantics — and :class:`TestApprovalManagerIsUntouched`
pins that nothing here reached into it.

Tables are built from the DDL the runtime itself ships (the migration's own
``up.sql`` for ``approval_items``, ``tools/ace/db/init_db.py`` for
``ace_audit_log``) rather than a hand-written copy, so a column added in one
place and not the other fails here instead of at runtime inside a swallowed
exception (CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import approval_inbox, inbox_adapters
from tools.agent_runtime.approval_inbox import (
    ORIGIN_ACE,
    ORIGIN_WORKFLOW_HITL,
    RESOLUTION_APPROVED,
    RESOLUTION_DENIED,
    STATE_PENDING,
    STATE_RESOLVED,
)
from tools.agent_runtime.inbox_adapters import (
    InboxAdapterError,
    ace_item_id,
    mirror_ace_pending,
    mirror_workflow_pending,
    settle_workflow,
    wfh_item_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

INSTANCE = "ace-inst-agov-05"
COWORKER = "cw-agov-05"
ROLE = "ai_developer"
DETAIL = "step=draft_requirements"

# The waiting thread parks on a threading.Event, so a correct release is
# observed in milliseconds. This bound only has to be far below the 30 s
# cross-process fallback poll -- clearing it via the fallback would mean the
# Event was never set, which is the regression worth catching.
WAKE_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Schema — from the DDL the runtime itself ships
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


def _ace_audit_ddl() -> str:
    """The live ``ace_audit_log`` DDL, lifted out of the ACE canvas initialiser.

    Extracted rather than copied so this test cannot pass against a schema the
    canvas no longer has. The surrounding module is one big ``.format()``
    template, hence the ``{{}}`` unescaping.
    """
    source = (REPO_ROOT / "tools" / "ace" / "db" / "init_db.py").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS ace_audit_log \(.*?\n\);", source, re.DOTALL
    )
    assert match, "ace_audit_log DDL not found in tools/ace/db/init_db.py"
    return match.group(0).replace("{{", "{").replace("}}", "}")


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
class _RecordingConnection:
    """A translating connection that remembers every statement it ran.

    The append-only assertion needs the SQL itself: a test that only checks the
    final rows cannot tell an INSERT-then-INSERT from an UPDATE that happened to
    leave the same values behind.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = None):
        self.statements.append(sql)
        return self._conn.execute(sql, params) if params is not None else self._conn.execute(sql)

    def close(self) -> None:  # the code under test closes eagerly; the DB outlives it
        pass

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    A named factory rather than an inline ``translating(...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS; a name bound straight from the imported helper is
    not propagated, so a correctly-wrapped fixture reads to that gate as a raw
    sqlite3 handle.
    """
    return translating(raw, unclosable=True)


def _shim_storage():
    """The module ``approval_inbox`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, while
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage`` —
    two different objects. The store imports the shim from inside ``_connect``,
    so patching the canonical module (what monkeypatch's string form resolves
    to) would patch nothing and every assertion below would pass against its own
    no-op.
    """
    return sys.modules["tools.db.storage"]


def _ace_storage():
    """The module ACE resolves ``get_canvas_connection`` from.

    ACE imports ``icdev.tools.db.storage`` explicitly, and the ACE canvas lives
    in its OWN database (``ICDEV_ACE_DB_URL``) with no ``classification`` column
    — which is why it uses ``get_canvas_connection`` and not ``get_connection``.
    """
    import icdev.tools.db.storage as ace_storage

    return ace_storage


@pytest.fixture
def inbox_db(monkeypatch, tmp_path):
    """``approval_items`` + ``agent_approval_log``, behind the %s translation."""
    raw = sqlite3.connect(str(tmp_path / "inbox.db"), check_same_thread=False)
    raw.executescript(_approval_items_ddl())
    raw.executescript(_approval_log_ddl())
    storage = _shim_storage()
    conn = _translating_conn(raw)
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    yield raw
    raw.close()


@pytest.fixture
def ace_db(monkeypatch, tmp_path):
    """The real ``ace_audit_log``, recording every statement issued against it."""
    raw = sqlite3.connect(str(tmp_path / "ace.db"), check_same_thread=False)
    raw.executescript(_ace_audit_ddl())
    conn = _RecordingConnection(_translating_conn(raw))
    monkeypatch.setattr(
        _ace_storage(), "get_canvas_connection", lambda *a, **k: conn
    )
    yield conn


def _rows(raw: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# The ACE thread under test — the real class, the real gate
# ---------------------------------------------------------------------------
def _coworker_thread():
    """A real ``CoWorkerThread``, from the copy ACE itself runs.

    ``icdev.tools.ace.coworker_thread`` and ``tools.ace.coworker_thread`` are
    distinct module objects with distinct in-process HITL wake registries, and
    ``ACEController`` builds threads from the ``icdev`` one — so that is the copy
    a release has to reach. Constructing it touches no database; every write goes
    through the patched canvas connection.
    """
    from icdev.tools.ace.coworker_thread import CoWorkerThread
    from icdev.tools.ace.team_assembler import CoWorkerSpec

    spec = CoWorkerSpec(
        coworker_id=COWORKER,
        role_id=ROLE,
        role_slot=f"{ROLE}-0",
        mailbox_id=f"mb-{COWORKER}",
        llm_function="code_generation",
    )
    return CoWorkerThread(
        spec, INSTANCE, message_bus=None, trust_kernel=None, monitor_interval=10
    )


def _hitl_gate():
    from icdev.tools.ace.coworker_thread import HITLGate

    return HITLGate


# ---------------------------------------------------------------------------
# 1. An ACE pause reaches the unified inbox
# ---------------------------------------------------------------------------
class TestAcePauseReachesTheInbox:
    def test_hitl_pending_appears_in_the_inbox(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)

        pending = inbox_adapters.pending(origin=ORIGIN_ACE)
        assert len(pending) == 1
        item = pending[0]
        assert item.item_id == ace_item_id(COWORKER, DETAIL)
        assert item.origin == ORIGIN_ACE
        assert item.state == STATE_PENDING
        assert item.session_id == INSTANCE
        assert COWORKER in item.tool_name
        # The gate is still ACE's own: the audit row was written too, and it is
        # what HITLGate reports as open.
        assert [r["detail"] for r in _hitl_gate().get_pending(COWORKER)] == [DETAIL]
        assert [r["action"] for r in _rows(_raw_of(ace_db), "ace_audit_log")] == [
            "hitl_pending"
        ]

    def test_one_queue_holds_both_origins(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)
        mirror_workflow_pending(
            approval_id="wfa-deadbeef01", instance_id="wfi-1", stage="Technical Review"
        )
        origins = {item.origin for item in inbox_adapters.pending()}
        assert origins == {ORIGIN_ACE, ORIGIN_WORKFLOW_HITL}

    def test_mirroring_the_same_pause_twice_is_one_item(self, ace_db, inbox_db):
        thread = _coworker_thread()
        thread._audit("hitl_pending", DETAIL)
        thread._audit("hitl_pending", DETAIL)
        assert len(inbox_adapters.pending(origin=ORIGIN_ACE)) == 1

    def test_other_audit_actions_are_not_mirrored(self, ace_db, inbox_db):
        thread = _coworker_thread()
        thread._audit("step_completed", "step=1")
        thread._audit("hitl_auto_approved", "role pre-authorised")
        assert inbox_adapters.pending() == []

    def test_an_unavailable_inbox_does_not_break_the_ace_gate(
        self, ace_db, monkeypatch, tmp_path
    ):
        """No ``approval_items`` table at all: ACE must behave exactly as today.

        Failing the gate closed on a mirror error would make an optional
        delivery channel load-bearing; failing it open would turn a missing
        table into an approval. Neither — ACE is simply unchanged.
        """
        storage = _shim_storage()
        monkeypatch.setattr(storage, "table_exists", lambda c, t: False)

        _coworker_thread()._audit("hitl_pending", DETAIL)

        gate_rows = _hitl_gate().get_pending(COWORKER)
        assert [r["detail"] for r in gate_rows] == [DETAIL]


# ---------------------------------------------------------------------------
# 2. Answering in the inbox releases the parked thread
# ---------------------------------------------------------------------------
class TestResolvingFromTheInboxReleasesTheThread:
    def test_the_waiting_coworker_thread_wakes(self, ace_db, inbox_db):
        thread = _coworker_thread()
        thread._audit("hitl_pending", DETAIL)
        item_id = ace_item_id(COWORKER, DETAIL)

        parked = threading.Event()
        outcome: dict[str, Any] = {}

        def wait() -> None:
            parked.set()
            outcome["released"] = thread._wait_for_hitl_resolution()

        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        assert parked.wait(WAKE_TIMEOUT), "the waiter never started"

        result = inbox_adapters.resolve(
            item_id, approved=True, resolved_by="ops-oncall", reason="reviewed"
        )
        assert result["ok"] and result["released"]
        assert result["origin_key"] == DETAIL

        waiter.join(WAKE_TIMEOUT)
        assert not waiter.is_alive(), "the co-worker thread is still parked"
        assert outcome["released"] is True

        item = approval_inbox.get(item_id)
        assert item.state == STATE_RESOLVED
        assert item.resolution == RESOLUTION_APPROVED

    def test_denying_from_the_inbox_does_not_release(self, ace_db, inbox_db):
        """A refusal settles the item and records ``hitl_rejected`` — and
        ``get_pending`` deliberately still reports the gate as open, because a
        refusal is not permission to continue."""
        _coworker_thread()._audit("hitl_pending", DETAIL)
        item_id = ace_item_id(COWORKER, DETAIL)

        result = inbox_adapters.resolve(item_id, approved=False, reason="not authorised")
        assert result["ok"] and result["released"]

        actions = [r["action"] for r in _rows(_raw_of(ace_db), "ace_audit_log")]
        assert actions == ["hitl_pending", "hitl_rejected"]
        assert [r["detail"] for r in _hitl_gate().get_pending(COWORKER)] == [DETAIL]
        assert approval_inbox.get(item_id).resolution == RESOLUTION_DENIED

    def test_a_second_answer_is_refused_not_replayed(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)
        item_id = ace_item_id(COWORKER, DETAIL)

        assert inbox_adapters.resolve(item_id, approved=True)["ok"]
        again = inbox_adapters.resolve(item_id, approved=True)
        assert not again["ok"]
        assert again["state"] == STATE_RESOLVED

        actions = [r["action"] for r in _rows(_raw_of(ace_db), "ace_audit_log")]
        assert actions.count("hitl_resolved") == 1

    def test_resolving_an_item_ace_no_longer_holds_is_refused(self, ace_db, inbox_db):
        """The detail is recovered from ACE's live pending list, not stored here.

        So an item whose ACE-side gate has already cleared cannot be used to
        INSERT a ``hitl_resolved`` row for a pause nobody is waiting on.
        """
        mirror_ace_pending(
            coworker_id=COWORKER, detail=DETAIL, instance_id=INSTANCE, role_id=ROLE
        )  # mirrored WITHOUT an ace_audit_log pause behind it
        with pytest.raises(InboxAdapterError):
            inbox_adapters.resolve(ace_item_id(COWORKER, DETAIL), approved=True)


# ---------------------------------------------------------------------------
# 3. Answering the old way settles the mirror
# ---------------------------------------------------------------------------
class TestResolvingInAceSettlesTheMirror:
    def _post_hitl(self, approved: bool):
        """Call the real ``POST /api/ace/<id>/hitl`` view, the ACE UI's button."""
        from flask import Flask

        from icdev.tools.ace.blueprint import api_hitl_resolve

        app = Flask(__name__)
        with app.test_request_context(
            json={"coworker_id": COWORKER, "detail": DETAIL, "approved": approved}
        ):
            return api_hitl_resolve(INSTANCE)

    def test_approving_through_the_ace_route_resolves_the_item(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)
        item_id = ace_item_id(COWORKER, DETAIL)
        assert approval_inbox.get(item_id).state == STATE_PENDING

        response = self._post_hitl(True)
        assert response.get_json()["approved"] is True

        item = approval_inbox.get(item_id)
        assert item.state == STATE_RESOLVED
        assert item.resolution == RESOLUTION_APPROVED
        assert inbox_adapters.pending(origin=ORIGIN_ACE) == []

    def test_rejecting_through_the_ace_route_denies_the_item(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)
        self._post_hitl(False)
        item = approval_inbox.get(ace_item_id(COWORKER, DETAIL))
        assert item.state == STATE_RESOLVED
        assert item.resolution == RESOLUTION_DENIED

    def test_the_permanent_decision_lands_in_agent_approval_log(self, ace_db, inbox_db):
        """The mutable item can be pruned; the decision may not be lost with it."""
        _coworker_thread()._audit("hitl_pending", DETAIL)
        self._post_hitl(True)

        decisions = _rows(inbox_db, "agent_approval_log")
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "approved"
        assert COWORKER in decisions[0]["tool_name"]

    def test_a_pause_with_no_mirror_still_resolves(self, ace_db, monkeypatch, tmp_path):
        """The ACE route must not start depending on the inbox being migrated."""
        storage = _shim_storage()
        monkeypatch.setattr(storage, "table_exists", lambda c, t: False)
        _coworker_thread()._audit("hitl_pending", DETAIL)

        response = self._post_hitl(True)
        assert response.get_json()["resolved"] is True
        assert _hitl_gate().get_pending(COWORKER) == []


# ---------------------------------------------------------------------------
# 4. ace_audit_log stays append-only
# ---------------------------------------------------------------------------
class TestAceAuditLogStaysAppendOnly:
    def test_no_statement_mutates_ace_audit_log(self, ace_db, inbox_db):
        thread = _coworker_thread()
        thread._audit("hitl_pending", DETAIL)
        inbox_adapters.resolve(ace_item_id(COWORKER, DETAIL), approved=True)

        touching = [
            sql for sql in ace_db.statements if "ace_audit_log" in sql.lower()
        ]
        assert touching, "the flow never touched ace_audit_log — fixture is wrong"
        for sql in touching:
            head = sql.strip().split(None, 1)[0].upper()
            assert head in ("INSERT", "SELECT"), f"mutating statement: {sql}"

    def test_the_pause_row_survives_its_resolution(self, ace_db, inbox_db):
        _coworker_thread()._audit("hitl_pending", DETAIL)
        before = _rows(_raw_of(ace_db), "ace_audit_log")
        assert len(before) == 1

        inbox_adapters.resolve(ace_item_id(COWORKER, DETAIL), approved=True)

        after = _rows(_raw_of(ace_db), "ace_audit_log")
        assert len(after) == 2
        assert after[0] == before[0], "the hitl_pending row was modified"
        assert after[1]["action"] == "hitl_resolved"
        assert after[1]["detail"] == DETAIL


# ---------------------------------------------------------------------------
# workflow_hitl — the same treatment
# ---------------------------------------------------------------------------
APPROVAL = "wfa-000000000001"


class TestWorkflowHitlAdapter:
    def test_a_pending_gate_is_mirrored_once(self, inbox_db):
        first = mirror_workflow_pending(
            approval_id=APPROVAL, instance_id="wfi-9", stage="Security Review",
            task_id="task-7",
        )
        second = mirror_workflow_pending(
            approval_id=APPROVAL, instance_id="wfi-9", stage="Security Review"
        )
        assert first.item_id == second.item_id == wfh_item_id(APPROVAL)
        assert len(inbox_adapters.pending(origin=ORIGIN_WORKFLOW_HITL)) == 1
        assert first.session_id == "wfi-9"

    def test_deciding_in_the_review_ui_settles_the_item(self, inbox_db):
        mirror_workflow_pending(approval_id=APPROVAL, instance_id="wfi-9", stage="S")
        settle_workflow(APPROVAL, approved=True, resolved_by="reviewer-1")
        item = approval_inbox.get(wfh_item_id(APPROVAL))
        assert item.state == STATE_RESOLVED
        assert item.resolution == RESOLUTION_APPROVED

    def test_answering_in_the_inbox_advances_the_stage(self, inbox_db, monkeypatch):
        """The release goes through ``submit_feedback`` — the same path the
        review UI uses — so kickback reasons, feedback rows and stage advance
        keep working rather than being re-implemented here."""
        calls: list[tuple] = []
        import tools.workflow_hitl.feedback as feedback_module

        monkeypatch.setattr(
            feedback_module,
            "submit_feedback",
            lambda *a, **k: calls.append((a, k)) or "wff-test",
        )
        mirror_workflow_pending(approval_id=APPROVAL, instance_id="wfi-9", stage="S")

        result = inbox_adapters.resolve(
            wfh_item_id(APPROVAL), approved=True, resolved_by="ops"
        )
        assert result["ok"] and result["released"]
        assert calls and calls[0][0][:3] == (APPROVAL, "approve", "ops")

    def test_a_denial_carries_a_kickback_reason(self, inbox_db, monkeypatch):
        """``submit_feedback`` rejects a kickback with no reason when
        ICDEV_HITL_REQUIRE_FEEDBACK is on, which is the default."""
        calls: list[tuple] = []
        import tools.workflow_hitl.feedback as feedback_module

        monkeypatch.setattr(
            feedback_module,
            "submit_feedback",
            lambda *a, **k: calls.append((a, k)) or "wff-test",
        )
        mirror_workflow_pending(approval_id=APPROVAL, instance_id="wfi-9", stage="S")
        inbox_adapters.resolve(wfh_item_id(APPROVAL), approved=False)

        assert calls[0][0][1] == "kickback"
        assert calls[0][1]["kickback_reason"]


# ---------------------------------------------------------------------------
# Out of scope, and provably so
# ---------------------------------------------------------------------------
class TestApprovalManagerIsUntouched:
    """``tools/integration/approval_manager.py`` is document-, COA- and
    boundary-level approval with multi-reviewer lists — a different lifetime and
    a different audience from a mid-run gate, whose reviewer semantics do not
    survive being flattened into one item with one ``resolved_by``. Folding it in
    would be scope creep, so nothing here may reach into it."""

    def test_nothing_in_the_inbox_path_imports_it(self):
        sources = [
            REPO_ROOT / "tools" / "agent_runtime" / "inbox_adapters.py",
            REPO_ROOT / "tools" / "agent_runtime" / "approval_inbox.py",
        ]
        for path in sources:
            assert "approval_manager" not in path.read_text(encoding="utf-8").replace(
                "approval_manager.py", ""
            ), f"{path.name} reaches into the out-of-scope approval manager"

    def test_it_does_not_know_about_the_inbox_either(self):
        text = (
            REPO_ROOT / "tools" / "integration" / "approval_manager.py"
        ).read_text(encoding="utf-8")
        assert "approval_items" not in text
        assert "inbox_adapters" not in text


def _raw_of(recording: Any) -> sqlite3.Connection:
    """The underlying sqlite3 handle behind the recording+translating wrappers."""
    return recording._conn._conn
