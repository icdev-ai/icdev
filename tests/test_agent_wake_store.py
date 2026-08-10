# CUI // SP-CTI
"""The wake store — agent-scheduled resumption (agov-wake-01).

Four things have to be true, and each has a class here:

  1. :func:`due` returns ONLY wakes whose condition is actually met. A timer
     whose time has not come, a job that has not completed and an event that has
     not fired are all still ``pending``, and a caller that treats ``pending`` as
     ``due`` resumes an agent early — silently, because an agent resumed early
     just does the wrong work rather than crashing.
  2. The machine is one-directional. Nothing takes a wake from ``fired`` back to
     ``due``; a spent wake is spent.
  3. Two concurrent :func:`mark_fired` calls produce EXACTLY ONE transition.
     ICDEV runs many sessions against one database and the tick (agov-wake-03)
     can overlap with itself, so this is the invariant that stops one suspension
     resuming an agent twice. Tested both deterministically (two connections,
     interleaved by hand) and for real (two threads through a barrier).
  4. A cancelled wake never appears in :func:`due`, including a cancelled timer
     whose ``fire_at`` later elapses.

The table is built from the migration's own ``up.sql`` rather than from a
hand-written schema, so a column added to :data:`wake._DDL` and not to the
migration — or the reverse — fails here rather than at runtime inside a swallowed
exception (CLAUDE.md: "every column in an INSERT must exist in the LIVE schema").
"""
from __future__ import annotations

import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import wake as wake_module
from tools.agent_runtime.wake import (
    COLUMNS,
    KIND_COMPLETION,
    KIND_EVENT,
    KIND_TIMER,
    STATE_CANCELLED,
    STATE_DUE,
    STATE_FIRED,
    STATE_PENDING,
    TABLE,
    Wake,
    WakeStoreUnavailable,
    add_completion,
    add_event,
    add_timer,
    add_timer_in,
    cancel,
    complete_job,
    due,
    fire_event,
    get,
    mark_fired,
    pending,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "20260809221051_agov_agent_wakes"

SESSION = "sess-agov-wake"
OTHER_SESSION = "sess-other"


# ---------------------------------------------------------------------------
# Schema — from the migration itself
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    path = REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    return path.read_text(encoding="utf-8")


def _live_columns(raw: sqlite3.Connection) -> list[str]:
    return [r[1] for r in raw.execute(f"PRAGMA table_info({TABLE})").fetchall()]


def _build(path) -> sqlite3.Connection:
    raw = sqlite3.connect(str(path))
    raw.executescript(_migration_ddl())
    return raw


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    A named factory rather than an inline ``translating(...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS; a name bound straight from the imported helper is
    not propagated, so a correctly-wrapped fixture reads to that gate as a raw
    sqlite3 handle.
    """
    return translating(raw, unclosable=True)


def _storage_module():
    """The module ``wake`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, while
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage`` —
    two different objects. ``wake`` imports the shim from inside ``_connect``, so
    patching the canonical module (what monkeypatch's string form resolves to)
    would patch nothing and every test below would assert its own no-op.
    """
    return sys.modules["tools.db.storage"]


@pytest.fixture
def wake_db(monkeypatch, tmp_path):
    """The real table, behind the production ``%s`` translation."""
    raw = _build(tmp_path / "wake.db")
    conn = _translating_conn(raw)
    monkeypatch.setattr(_storage_module(), "get_connection", lambda *a, **k: conn)
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {TABLE}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _state(raw: sqlite3.Connection, wake_id: str) -> str | None:
    row = raw.execute(f"SELECT state FROM {TABLE} WHERE wake_id = ?", (wake_id,)).fetchone()
    return row[0] if row else None


def _minutes(n: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=n)


# ---------------------------------------------------------------------------
# 0. The INSERT's columns match the live schema
# ---------------------------------------------------------------------------
class TestSchema:
    def test_columns_match_the_migration(self, wake_db):
        assert list(COLUMNS) == _live_columns(wake_db)

    def test_self_created_schema_matches_the_migration(self, tmp_path):
        """A checkout that never migrated must get the SAME table.

        ``wake._ensure_schema`` exists so an un-migrated checkout still works.
        If its DDL and the migration's drift, the INSERT succeeds on one and
        raises on the other, and which one you get depends on deploy history.
        """
        migrated = sqlite3.connect(str(tmp_path / "migrated.db"))
        migrated.executescript(_migration_ddl())
        selfmade = sqlite3.connect(str(tmp_path / "selfmade.db"))
        selfmade.executescript(wake_module._DDL)
        try:
            assert _live_columns(selfmade) == _live_columns(migrated)
        finally:
            migrated.close()
            selfmade.close()

    def test_insert_names_only_live_columns(self, wake_db):
        w = add_timer(SESSION, _minutes(5), note="waiting on the nightly sweep")
        rows = _rows(wake_db)
        assert len(rows) == 1
        row = rows[0]
        # An INSERT naming a phantom column would have raised out of add_timer
        # rather than reaching here; this pins the other direction — that the
        # store writes every column the table has.
        assert set(row) == set(COLUMNS)
        assert row["wake_id"] == w.wake_id
        assert row["session_id"] == SESSION
        assert row["kind"] == KIND_TIMER
        assert row["state"] == STATE_PENDING
        assert row["job_id"] is None
        assert row["event_key"] is None
        assert row["classification"] == "CUI"
        assert row["created_at"] and row["updated_at"]

    def test_every_kind_round_trips(self, wake_db):
        t = add_timer(SESSION, _minutes(5))
        c = add_completion(SESSION, "job-7")
        e = add_event(SESSION, "pr:1342:ci_green")
        assert get(t.wake_id).condition == t.fire_at
        assert get(c.wake_id).condition == "job-7"
        assert get(e.wake_id).condition == "pr:1342:ci_green"
        assert {r["kind"] for r in _rows(wake_db)} == {
            KIND_TIMER, KIND_COMPLETION, KIND_EVENT
        }

    def test_fire_at_is_written_at_a_fixed_width(self, wake_db):
        """Lexicographic order must equal chronological order.

        The timer sweep compares ``fire_at`` as TEXT. A timestamp written with
        microseconds elided (``...:15+00:00``) sorts against one written with
        them (``...:15.000001+00:00``) by ASCII, so a ragged format turns the
        sweep into a subtly wrong comparison that only misfires near a whole
        second — the kind of bug that reproduces once a week.
        """
        add_timer(SESSION, datetime(2026, 8, 9, 22, 10, 15, tzinfo=timezone.utc))
        add_timer(SESSION, "2026-08-09T22:10:16.5+00:00")
        stamps = [r["fire_at"] for r in _rows(wake_db)]
        for stamp in stamps:
            assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", stamp), stamp
        assert sorted(stamps) == sorted(stamps, key=datetime.fromisoformat)

    def test_naive_fire_at_is_read_as_utc(self, wake_db):
        w = add_timer(SESSION, datetime(2026, 8, 9, 22, 10, 15))
        assert w.fire_at == "2026-08-09T22:10:15.000000+00:00"


class TestRefusedBeforeTheInsert:
    """A wake that could never fire must not be persisted."""

    def test_unknown_kind(self, wake_db):
        with pytest.raises(ValueError, match="unknown wake kind"):
            wake_module._insert(
                session_id=SESSION, kind="telepathy", fire_at=None, job_id=None,
                event_key=None, note="", tenant_id="", classification="CUI", conn=None,
            )
        assert _rows(wake_db) == []

    def test_missing_session_id(self, wake_db):
        with pytest.raises(ValueError, match="session_id is required"):
            add_timer("   ", _minutes(5))
        assert _rows(wake_db) == []

    def test_missing_condition(self, wake_db):
        # A conditionless wake sits pending forever: never promoted, never
        # fired, never visible as a bug.
        with pytest.raises(ValueError, match="condition"):
            wake_module._insert(
                session_id=SESSION, kind=KIND_EVENT, fire_at=None, job_id=None,
                event_key=None, note="", tenant_id="", classification="CUI", conn=None,
            )
        with pytest.raises(ValueError, match="job_id is required"):
            add_completion(SESSION, "")
        with pytest.raises(ValueError, match="event_key is required"):
            add_event(SESSION, "")
        assert _rows(wake_db) == []

    def test_a_dropped_write_raises_rather_than_vanishing(self, monkeypatch, tmp_path):
        """An agent that suspends against a store which did not accept the row
        never comes back, and nothing reports it. So the write path raises."""
        raw = sqlite3.connect(str(tmp_path / "empty.db"))  # no table, no DDL
        conn = _translating_conn(raw)
        monkeypatch.setattr(_storage_module(), "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(wake_module, "_ensure_schema", lambda c: None)
        try:
            with pytest.raises(WakeStoreUnavailable):
                add_event(SESSION, "pr:1342:ci_green")
        finally:
            raw.close()


# ---------------------------------------------------------------------------
# 1. due() returns only wakes whose condition is met
# ---------------------------------------------------------------------------
class TestDueOnlyReturnsMetConditions:
    def test_an_unelapsed_timer_is_not_due(self, wake_db):
        w = add_timer(SESSION, _minutes(30))
        assert due() == []
        assert _state(wake_db, w.wake_id) == STATE_PENDING
        assert [x.wake_id for x in pending(SESSION)] == [w.wake_id]

    def test_an_elapsed_timer_is_due(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        assert [x.wake_id for x in due()] == [w.wake_id]
        assert _state(wake_db, w.wake_id) == STATE_DUE
        # ...and it is no longer "pending": due is a different set.
        assert pending(SESSION) == []

    def test_due_evaluates_the_clock_at_call_time(self, wake_db):
        w = add_timer(SESSION, _minutes(30))
        assert due() == []
        assert [x.wake_id for x in due(now=_minutes(31))] == [w.wake_id]

    def test_a_zero_delay_timer_is_due_on_the_next_sweep(self, wake_db):
        w = add_timer_in(SESSION, 0)
        assert [x.wake_id for x in due()] == [w.wake_id]

    def test_a_completion_wake_waits_for_its_own_job(self, wake_db):
        w = add_completion(SESSION, "job-7")
        assert due() == []

        assert complete_job("job-other") == []          # wrong job: no promotion
        assert due() == []
        assert _state(wake_db, w.wake_id) == STATE_PENDING

        assert complete_job("job-7") == [w.wake_id]
        assert [x.wake_id for x in due()] == [w.wake_id]

    def test_an_event_wake_waits_for_its_own_key(self, wake_db):
        w = add_event(SESSION, "pr:1342:ci_green")
        assert due() == []

        assert fire_event("pr:1342:ci_red") == []       # wrong key: no promotion
        assert due() == []

        assert fire_event("pr:1342:ci_green") == [w.wake_id]
        assert [x.wake_id for x in due()] == [w.wake_id]

    def test_a_signal_with_no_listeners_is_not_an_error(self, wake_db):
        assert complete_job("job-nobody-awaits") == []
        assert fire_event("event:nobody:awaits") == []

    def test_one_key_promotes_every_listener_across_sessions(self, wake_db):
        a = add_event(SESSION, "pr:1342:ci_green")
        b = add_event(OTHER_SESSION, "pr:1342:ci_green")
        c = add_event(SESSION, "pr:9999:ci_green")
        assert sorted(fire_event("pr:1342:ci_green")) == sorted([a.wake_id, b.wake_id])
        assert sorted(x.wake_id for x in due()) == sorted([a.wake_id, b.wake_id])
        assert _state(wake_db, c.wake_id) == STATE_PENDING

    def test_due_can_be_scoped_to_one_session(self, wake_db):
        mine = add_timer(SESSION, _minutes(-1))
        theirs = add_timer(OTHER_SESSION, _minutes(-1))
        assert [x.wake_id for x in due(session_id=SESSION)] == [mine.wake_id]
        assert [x.wake_id for x in due(session_id=OTHER_SESSION)] == [theirs.wake_id]

    def test_a_fired_wake_is_no_longer_due(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        assert [x.wake_id for x in due()] == [w.wake_id]
        assert mark_fired(w.wake_id) is True
        assert due() == []

    def test_pending_is_scoped_to_its_session(self, wake_db):
        mine = add_event(SESSION, "k")
        add_event(OTHER_SESSION, "k")
        assert [x.wake_id for x in pending(SESSION)] == [mine.wake_id]

    def test_a_read_failure_reports_no_due_wakes_rather_than_wedging(
        self, monkeypatch, wake_db
    ):
        """``due`` is the reflex tick's entry point; a raise there wedges the
        Genesis daemon for every other reflex."""
        add_timer(SESSION, _minutes(-1))

        def boom(*a, **k):
            raise sqlite3.OperationalError("no such table: agent_wakes")

        monkeypatch.setattr(wake_module, "_select", boom)
        assert due() == []
        assert pending(SESSION) == []
        assert get("wake-whatever") is None


# ---------------------------------------------------------------------------
# 2. One-directional: nothing goes back
# ---------------------------------------------------------------------------
class TestOneDirectional:
    def test_fired_cannot_return_to_due(self, wake_db):
        w = add_event(SESSION, "pr:1342:ci_green")
        fire_event("pr:1342:ci_green")
        assert mark_fired(w.wake_id) is True
        assert _state(wake_db, w.wake_id) == STATE_FIRED

        # The event fires again — a re-run of pr_watcher, a duplicate webhook.
        assert fire_event("pr:1342:ci_green") == []
        assert _state(wake_db, w.wake_id) == STATE_FIRED
        assert due() == []

    def test_a_fired_completion_wake_cannot_be_repromoted(self, wake_db):
        w = add_completion(SESSION, "job-7")
        complete_job("job-7")
        assert mark_fired(w.wake_id) is True
        assert complete_job("job-7") == []
        assert _state(wake_db, w.wake_id) == STATE_FIRED

    def test_a_fired_timer_is_not_repromoted_by_a_later_sweep(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        due()
        assert mark_fired(w.wake_id) is True
        assert due(now=_minutes(60)) == []
        assert _state(wake_db, w.wake_id) == STATE_FIRED

    def test_pending_cannot_be_fired_directly(self, wake_db):
        """Promotion is what evaluates the condition. Firing from ``pending``
        would resume an agent on a condition nobody ever checked."""
        w = add_timer(SESSION, _minutes(30))
        assert mark_fired(w.wake_id) is False
        assert _state(wake_db, w.wake_id) == STATE_PENDING

    def test_mark_fired_is_idempotent(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        due()
        assert mark_fired(w.wake_id) is True
        # A retried tick. No exception, no second transition.
        assert mark_fired(w.wake_id) is False
        assert mark_fired(w.wake_id) is False
        assert _state(wake_db, w.wake_id) == STATE_FIRED

    def test_mark_fired_on_an_unknown_id_is_false(self, wake_db):
        assert mark_fired("wake-does-not-exist") is False

    def test_a_transition_stamps_updated_at_and_leaves_created_at(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        due()
        mark_fired(w.wake_id)
        row = _rows(wake_db)[0]
        assert row["created_at"] == w.created_at
        assert row["updated_at"] > w.updated_at


# ---------------------------------------------------------------------------
# 3. Exactly one transition under concurrency
# ---------------------------------------------------------------------------
class TestExactlyOnce:
    def test_two_interleaved_connections_produce_one_transition(self, tmp_path):
        """The deterministic half: two connections, hand-interleaved.

        This is the shape read-then-write gets wrong. Both connections observe
        ``due``; only the conditional UPDATE decides a winner.
        """
        raw = _build(tmp_path / "race.db")
        conn_a = _translating_conn(sqlite3.connect(str(tmp_path / "race.db")))
        conn_b = _translating_conn(sqlite3.connect(str(tmp_path / "race.db")))
        try:
            w = add_timer(SESSION, _minutes(-1), conn=conn_a)
            assert [x.wake_id for x in due(conn=conn_a)] == [w.wake_id]
            # Both ticks have now seen the same due wake.
            assert [x.wake_id for x in due(conn=conn_b)] == [w.wake_id]

            first = mark_fired(w.wake_id, conn=conn_a)
            second = mark_fired(w.wake_id, conn=conn_b)
            assert [first, second] == [True, False]
            assert _state(raw, w.wake_id) == STATE_FIRED
        finally:
            for c in (conn_a, conn_b):
                c._conn.close()
            raw.close()

    def test_two_threads_produce_exactly_one_transition(self, tmp_path):
        """The real half: two threads released together through a barrier."""
        db = tmp_path / "threads.db"
        raw = _build(db)
        setup = _translating_conn(raw)
        w = add_timer(SESSION, _minutes(-1), conn=setup)
        assert [x.wake_id for x in due(conn=setup)] == [w.wake_id]

        barrier = threading.Barrier(2)
        results: list[bool] = []
        lock = threading.Lock()

        def fire() -> None:
            conn = _translating_conn(
                sqlite3.connect(str(db), timeout=30, check_same_thread=False)
            )
            try:
                barrier.wait(timeout=30)
                won = mark_fired(w.wake_id, conn=conn)
                with lock:
                    results.append(won)
            finally:
                conn._conn.close()

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        try:
            assert sorted(results) == [False, True], results
            assert _state(raw, w.wake_id) == STATE_FIRED
        finally:
            raw.close()

    def test_two_signals_for_one_key_promote_it_once(self, tmp_path):
        """The same guarantee one step earlier: pending -> due is also
        exactly-once, so two emitters firing the same key do not both claim it."""
        raw = _build(tmp_path / "signal.db")
        conn_a = _translating_conn(sqlite3.connect(str(tmp_path / "signal.db")))
        conn_b = _translating_conn(sqlite3.connect(str(tmp_path / "signal.db")))
        try:
            w = add_event(SESSION, "pr:1342:ci_green", conn=conn_a)
            first = fire_event("pr:1342:ci_green", conn=conn_a)
            second = fire_event("pr:1342:ci_green", conn=conn_b)
            assert first == [w.wake_id]
            assert second == []
            assert _state(raw, w.wake_id) == STATE_DUE
        finally:
            for c in (conn_a, conn_b):
                c._conn.close()
            raw.close()


# ---------------------------------------------------------------------------
# 4. A cancelled wake never appears in due()
# ---------------------------------------------------------------------------
class TestCancel:
    def test_a_cancelled_pending_timer_never_becomes_due(self, wake_db):
        w = add_timer(SESSION, _minutes(30))
        assert cancel(w.wake_id) is True
        assert _state(wake_db, w.wake_id) == STATE_CANCELLED
        # Its time comes and goes. It stays cancelled.
        assert due(now=_minutes(31)) == []
        assert due(now=_minutes(600)) == []
        assert _state(wake_db, w.wake_id) == STATE_CANCELLED

    def test_a_cancelled_wake_is_not_pending(self, wake_db):
        w = add_event(SESSION, "k")
        cancel(w.wake_id)
        assert pending(SESSION) == []

    def test_a_cancelled_wake_ignores_its_signal(self, wake_db):
        c = add_completion(SESSION, "job-7")
        e = add_event(SESSION, "pr:1342:ci_green")
        assert cancel(c.wake_id) is True
        assert cancel(e.wake_id) is True
        assert complete_job("job-7") == []
        assert fire_event("pr:1342:ci_green") == []
        assert due() == []

    def test_a_due_wake_can_still_be_cancelled(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        assert [x.wake_id for x in due()] == [w.wake_id]
        assert cancel(w.wake_id) is True
        assert due() == []
        # And cancelling closes the door on firing.
        assert mark_fired(w.wake_id) is False
        assert _state(wake_db, w.wake_id) == STATE_CANCELLED

    def test_a_fired_wake_cannot_be_cancelled(self, wake_db):
        w = add_timer(SESSION, _minutes(-1))
        due()
        assert mark_fired(w.wake_id) is True
        # Cancelling a spent wake would rewrite history and prevents nothing.
        assert cancel(w.wake_id) is False
        assert _state(wake_db, w.wake_id) == STATE_FIRED

    def test_cancel_is_idempotent(self, wake_db):
        w = add_event(SESSION, "k")
        assert cancel(w.wake_id) is True
        assert cancel(w.wake_id) is False
        assert cancel("wake-does-not-exist") is False

    def test_cancelling_one_wake_leaves_its_siblings_alone(self, wake_db):
        keep = add_event(SESSION, "pr:1342:ci_green")
        drop = add_event(SESSION, "pr:1342:ci_green")
        assert cancel(drop.wake_id) is True
        assert fire_event("pr:1342:ci_green") == [keep.wake_id]


# ---------------------------------------------------------------------------
# The record type
# ---------------------------------------------------------------------------
class TestWakeRecord:
    def test_predicates(self):
        w = Wake(wake_id="w", session_id=SESSION, kind=KIND_TIMER, state=STATE_PENDING)
        assert (w.is_pending, w.is_due, w.is_spent) == (True, False, False)
        for state, spent in (
            (STATE_DUE, False), (STATE_FIRED, True), (STATE_CANCELLED, True),
        ):
            assert Wake(
                wake_id="w", session_id=SESSION, kind=KIND_TIMER, state=state
            ).is_spent is spent

    def test_to_dict_covers_every_column(self):
        w = Wake(wake_id="w", session_id=SESSION, kind=KIND_EVENT,
                 state=STATE_PENDING, event_key="k")
        assert set(w.to_dict()) == set(COLUMNS)
