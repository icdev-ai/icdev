# CUI // SP-CTI
"""Tests for the CPMP overdue-deliverable state (cpmp-d70a553747).

``cpmp_deliverables.status = 'overdue'`` and ``days_overdue`` have exactly one
writer — ``contract_manager.compute_overdue_deliverables()`` — and five readers:
``get_contract().overdue_count``, ``portfolio_manager._score_deliverables``,
``portfolio_manager.get_portfolio_summary``, ``cpars_predictor._score_schedule``
and ``negative_event_tracker.auto_detect_delinquent``.

The writer had no caller but its own ``--compute-overdue`` CLI flag, so those
two fields were never written. Measured on the live board 2026-08-13: 27
deliverables, 26 of them past due by 44 days, **0** rows with
``status='overdue'``, **0** rows with ``days_overdue > 0``, ``health='green'``
on all nine contracts — while the cpmp_monitor reflex filed a high-priority
card reading "5 CDRL(s) are past due", because ``pmo_ai_advisor`` counted the
same condition a second, date-based way.

Pinned here:
  1. the reflex calls the sweep, so the flag is maintained rather than declared;
  2. one predicate — a CDRL delivered on time is not delinquent in either count;
  3. ``days_overdue`` is refreshed, not frozen at its marking-day value;
  4. one unparseable due_date cannot silently undo the whole sweep.
"""
from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from tests._sql_compat import translating

_DDL = """
CREATE TABLE cpmp_deliverables (
    id             TEXT PRIMARY KEY,
    contract_id    TEXT,
    title          TEXT,
    due_date       TEXT,
    submitted_date TEXT,
    status         TEXT,
    days_overdue   INTEGER DEFAULT 0,
    updated_at     TEXT
);
CREATE TABLE cpmp_status_history (
    id          TEXT PRIMARY KEY,
    entity_type TEXT,
    entity_id   TEXT,
    old_status  TEXT,
    new_status  TEXT,
    changed_by  TEXT,
    reason      TEXT,
    changed_at  TEXT
);
CREATE TABLE cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT,
    title           TEXT,
    status          TEXT
);
-- _gather_contract_context() wraps all six of its queries in ONE try/except,
-- so a single missing table drops every later key from the returned dict.
-- These are declared so the overdue count under test is reached on the real
-- code path rather than on an early bail-out.
CREATE TABLE cpmp_evm_periods (
    id TEXT PRIMARY KEY, contract_id TEXT, period_date TEXT,
    cpi REAL, spi REAL, eac REAL, vac REAL, tcpi REAL
);
CREATE TABLE cpmp_risks (
    id TEXT PRIMARY KEY, contract_id TEXT, status TEXT, exposure INTEGER
);
CREATE TABLE cpmp_subcontractors (
    id TEXT PRIMARY KEY, contract_id TEXT, status TEXT,
    flow_down_complete INTEGER, cybersecurity_compliant INTEGER
);
CREATE TABLE cpmp_cpars_assessments (
    id TEXT PRIMARY KEY, contract_id TEXT, period_end TEXT,
    overall_rating TEXT, overall_score REAL
);
CREATE TABLE cpmp_contract_mods (
    id TEXT PRIMARY KEY, contract_id TEXT, status TEXT
);
CREATE TABLE kanban_tasks (
    id              TEXT PRIMARY KEY,
    task_type       TEXT,
    title           TEXT,
    description     TEXT,
    status          TEXT,
    priority        TEXT,
    tags            TEXT,
    dispatch_source TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
"""

# A FIXED date, not "now" (fli-flk-01).
#
# The UTC-vs-local hazard was already handled here: compute_overdue_deliverables
# derives days_overdue from datetime.now(timezone.utc).date(), and date.today()
# is LOCAL, so on any runner west of UTC the two disagreed for part of the day —
# green in the morning, red after 8pm ET.
#
# What survived that fix is subtler and had the same effect. `_TODAY` was still a
# SNAPSHOT taken when this module was imported, while production recomputes the
# date on every call. Cross midnight UTC between the two and the deliverable is
# one day older than the literal 44 these tests assert.
#
# That is not theoretical: CI failed PR #1712 at 2026-08-16T00:01:47Z with
# `assert 45 == 44`, on a PR that touched the route-smoke hook and nothing
# whatsoever to do with CPMP. This file is CI-gated, so for the first minute of
# every UTC day it could fail whatever happened to be running, and the author was
# sent to debug a subsystem they never touched. It passes on a re-run, which is
# what makes it expensive: the lesson taught is "CI is flaky", and that is how a
# red gate stops being read.
#
# So the clock is FROZEN rather than raced — see the `frozen_clock` autouse
# fixture below, which makes production read this very date. The assertions can
# then keep saying 44, which is the point: the number is what proves the sweep
# computes the interval correctly, and widening it to a range to dodge the flake
# would have thrown away the assertion to keep the test.
_TODAY = date(2026, 6, 15)
_LATE = (_TODAY - timedelta(days=44)).isoformat()
_FUTURE = (_TODAY + timedelta(days=10)).isoformat()


class _FrozenDatetime(datetime):
    """`datetime` with `now()` pinned to _TODAY. Everything else is real.

    A subclass, not a Mock: these modules do `from datetime import date,
    datetime, timezone` and use them for more than now(), so a stub answering
    only now() breaks the rest — and isinstance checks keep working against a
    real datetime subclass.
    """

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - matches datetime.now
        return datetime(_TODAY.year, _TODAY.month, _TODAY.day, 12, 0, tzinfo=tz)


class _FrozenDate(date):
    """`date` with `today()` pinned to _TODAY."""

    @classmethod
    def today(cls):  # noqa: D102 - matches date.today
        return _TODAY


#: Every module in this flow that reads a clock, and which name it reads.
#:
#: TWO of them, which is the part worth writing down. contract_manager computes
#: days_overdue from ``datetime.now(timezone.utc).date()``; pmo_ai_advisor
#: computes due_in_30_days from ``date.today()``. Freezing only the first left
#: the second on the real clock, so a _FUTURE date ten days after the frozen
#: _TODAY was already in the PAST for it and due_in_30_days came back 0. Half a
#: frozen clock is worse than none: it fails deterministically rather than
#: nightly, but it fails.
_CLOCK_SITES = (
    ("tools.govcon.contract_manager", "datetime", _FrozenDatetime),
    ("icdev.tools.govcon.contract_manager", "datetime", _FrozenDatetime),
    ("tools.govcon.pmo_ai_advisor", "date", _FrozenDate),
    ("icdev.tools.govcon.pmo_ai_advisor", "date", _FrozenDate),
)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin the date production reads, so the expected interval cannot drift.

    Autouse on purpose: every test in this file asserts a day count or a date
    window, so any one of them can be the one that straddles midnight. Opting in
    per-test would leave exactly the gap this fixture exists to close.

    Patched on BOTH namespaces. ``tools.govcon.x`` and ``icdev.tools.govcon.x``
    are distinct module objects — the shim does not alias sys.modules — so
    patching one leaves the other on the real clock, and which one gets resolved
    depends on how the process was launched.
    """
    import importlib

    patched = 0
    for name, attr, replacement in _CLOCK_SITES:
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — the icdev mirror is absent in some trees
            continue
        monkeypatch.setattr(module, attr, replacement, raising=False)
        patched += 1

    assert patched, (
        "no clock site could be imported — the tests below would be racing the "
        "real clock again while looking frozen"
    )


def _conn(raw):
    conn = translating(raw, unclosable=True)
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def db():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)
    conn = _conn(raw)
    # contract_manager binds get_connection at import time, so patching
    # tools.db.storage.get_connection alone would leave it on the real board.
    with ExitStack() as stack:
        stack.enter_context(patch("tools.db.storage.get_connection", return_value=conn))
        stack.enter_context(patch("tools.govcon.contract_manager.get_connection", return_value=conn))
        stack.enter_context(patch("tools.govcon.pmo_ai_advisor.get_connection", return_value=conn))
        yield conn
    raw.close()


def _add(conn, did, due, status="not_started", submitted=None, days=0, cid="c-1"):
    conn.execute(
        "INSERT INTO cpmp_deliverables "
        "(id, contract_id, title, due_date, submitted_date, status, days_overdue) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (did, cid, f"CDRL {did}", due, submitted, status, days),
    )
    conn.commit()


def _row(conn, did):
    return dict(conn.execute("SELECT * FROM cpmp_deliverables WHERE id = %s", (did,)).fetchone())


# ---------------------------------------------------------------------------
# 1. The writer is actually called
# ---------------------------------------------------------------------------

class TestTheSweepHasAConsumer:
    def test_reflex_runs_the_overdue_sweep(self, db):
        """The whole defect in one assertion: before this, the only caller of
        compute_overdue_deliverables() was its own CLI flag, so every screen
        that reads status='overdue' read a column nothing ever wrote."""
        from tools.genesis.reflexes.cpmp_monitor import run

        db.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) "
            "VALUES ('c-1', 'W15P7T-24-C-0001', 'Test', 'active')"
        )
        _add(db, "d-late", _LATE)
        db.commit()

        with ExitStack() as stack:
            stack.enter_context(patch(
                "tools.govcon.cpars_predictor.predict_cpars", return_value={"predicted_score": 1.0}))
            stack.enter_context(patch(
                "tools.govcon.cpars_predictor.get_cpars_trend", return_value={"trend": []}))
            stack.enter_context(patch(
                "tools.govcon.subcontractor_tracker.detect_noncompliance",
                return_value={"noncompliance": []}))
            stack.enter_context(patch(
                "tools.govcon.cdrl_generator.generate_all_due", return_value={"generated": 0}))
            stack.enter_context(patch("tools.memory.memory_write.write_to_db", return_value=None))
            result = run()

        assert result["overdue_marked"] == 1, result.get("errors")
        assert _row(db, "d-late")["status"] == "overdue"
        assert _row(db, "d-late")["days_overdue"] == 44

    def test_sweep_covers_contracts_the_reflex_loop_does_not_visit(self, db):
        """The loop visits status='active' contracts only, but the portfolio
        rollup counts overdue CDRLs across ('active','option_pending')."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-opt", _LATE, cid="c-option-pending")

        assert compute_overdue_deliverables()["overdue_count"] == 1
        assert _row(db, "d-opt")["status"] == "overdue"


# ---------------------------------------------------------------------------
# 2. One predicate — delivered on time is not delinquent
# ---------------------------------------------------------------------------

class TestDeliveredIsNotDelinquent:
    @pytest.mark.parametrize("status", ["submitted", "government_review", "resubmitted"])
    def test_a_cdrl_in_government_hands_is_not_marked_overdue(self, db, status):
        """The contractor met the date; the government's review clock is not
        theirs to be charged for. Marking it overdue would degrade the CPARS
        schedule dimension on a delivery they actually made."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-sent", _LATE, status=status, submitted=_LATE)

        assert compute_overdue_deliverables()["overdue_count"] == 0
        assert _row(db, "d-sent")["status"] == status

    def test_a_submitted_date_alone_is_enough(self, db):
        """Seeded and imported rows carry a submitted_date without ever going
        through transition_deliverable(), so the status is not the only signal."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-seeded", _LATE, status="in_progress", submitted=_LATE)

        assert compute_overdue_deliverables()["overdue_count"] == 0

    @pytest.mark.parametrize("status", ["accepted", "rejected"])
    def test_terminal_statuses_are_left_alone(self, db, status):
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-done", _LATE, status=status)

        assert compute_overdue_deliverables()["overdue_count"] == 0
        assert _row(db, "d-done")["status"] == status

    def test_a_future_due_date_is_not_overdue(self, db):
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-future", _FUTURE)

        assert compute_overdue_deliverables()["overdue_count"] == 0

    @pytest.mark.parametrize("due", [None, ""])
    def test_a_missing_due_date_is_not_a_missed_one(self, db, due):
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-nodate", due)

        assert compute_overdue_deliverables()["overdue_count"] == 0
        assert _row(db, "d-nodate")["status"] == "not_started"

    def test_advisor_count_matches_what_the_sweep_marks(self, db):
        """The two numbers a PM sees — the board card's and the dashboard's —
        come from the same predicate, so they cannot disagree. Before the fix
        the advisor counted the submitted-and-in-review CDRL below and the
        sweep did not."""
        from tools.govcon.contract_manager import compute_overdue_deliverables
        from tools.govcon.pmo_ai_advisor import _gather_contract_context

        db.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) "
            "VALUES ('c-1', 'W15P7T-24-C-0001', 'Test', 'active')"
        )
        _add(db, "d-late-1", _LATE)
        _add(db, "d-late-2", _LATE)
        _add(db, "d-in-review", _LATE, status="government_review", submitted=_LATE)
        db.commit()

        marked = compute_overdue_deliverables("c-1")["overdue_count"]
        ctx = _gather_contract_context("c-1")

        assert marked == 2
        assert ctx["overdue_deliverables"] == 2

    def test_delivered_cdrl_is_not_an_upcoming_action_item_either(self, db):
        """due_in_30_days shares the status vocabulary — keeping a second one
        next door is exactly the drift that produced this bug."""
        from tools.govcon.pmo_ai_advisor import _gather_contract_context

        db.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) "
            "VALUES ('c-1', 'W15P7T-24-C-0001', 'Test', 'active')"
        )
        _add(db, "d-soon", _FUTURE)
        _add(db, "d-soon-sent", _FUTURE, status="submitted", submitted=_TODAY.isoformat())
        db.commit()

        assert _gather_contract_context("c-1")["due_in_30_days"] == 1


# ---------------------------------------------------------------------------
# 3. days_overdue must not freeze at its marking-day value
# ---------------------------------------------------------------------------

class TestDaysOverdueIsRefreshed:
    def test_an_already_overdue_row_gets_its_lateness_updated(self, db):
        """A CDRL caught the day it slips is stamped days_overdue = 1. Frozen
        there it is wrong on screen and, more quietly, still > 0 — which is the
        only thing negative_event_tracker.auto_detect_delinquent checks, so a
        CDRL now 44 days late escalates forever as a one-day slip."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-stale", _LATE, status="overdue", days=1)

        result = compute_overdue_deliverables()

        assert result["overdue_count"] == 0, "already marked — not a new finding"
        assert result["days_refreshed"] == 1
        assert _row(db, "d-stale")["days_overdue"] == 44

    def test_no_status_history_row_for_a_mere_refresh(self, db):
        """A refresh is not a transition; writing history every 3h would bury
        the real transitions under one row per sweep per deliverable."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-stale", _LATE, status="overdue", days=1)
        compute_overdue_deliverables()

        rows = db.execute("SELECT COUNT(*) c FROM cpmp_status_history").fetchone()["c"]
        assert rows == 0

    def test_marking_does_record_the_transition(self, db):
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-new", _LATE, status="in_progress")
        compute_overdue_deliverables()

        hist = dict(db.execute("SELECT * FROM cpmp_status_history").fetchone())
        assert (hist["old_status"], hist["new_status"]) == ("in_progress", "overdue")


# ---------------------------------------------------------------------------
# 4. One bad row must not undo the sweep
# ---------------------------------------------------------------------------

class TestOneBadRowCannotUndoTheSweep:
    def test_unparseable_due_date_is_counted_and_stepped_over(self, db):
        """due_date is TEXT. The bare fromisoformat() this replaced raised mid
        loop, before commit — so a single imported row in the wrong format
        silently reverted every mark the sweep had just made, every 3h,
        forever."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-bad", "06/30/2026")
        _add(db, "d-good", _LATE)

        result = compute_overdue_deliverables()

        assert result["unparseable_due_dates"] == 1
        assert result["overdue_count"] == 1
        assert _row(db, "d-good")["status"] == "overdue", "the good row still got marked"
        assert _row(db, "d-bad")["status"] == "not_started"

    def test_an_iso_timestamp_due_date_parses(self, db):
        """Some writers store a full ISO timestamp rather than a bare date."""
        from tools.govcon.contract_manager import compute_overdue_deliverables

        _add(db, "d-ts", f"{_LATE}T00:00:00+00:00")

        assert compute_overdue_deliverables()["overdue_count"] == 1
        assert _row(db, "d-ts")["days_overdue"] == 44


# ---------------------------------------------------------------------------
# 5. The clock is controlled, not raced (fli-flk-01)
# ---------------------------------------------------------------------------


class TestTheClockIsFrozenNotRaced:
    """Waiting until 00:00 UTC is not a test. Moving the clock is.

    The defect: _TODAY was a snapshot taken at MODULE IMPORT while production
    recomputed the date per call, so crossing midnight UTC between the two made
    every `days_overdue == 44` assertion read 45. CI hit it on PR #1712 at
    00:01:47Z — a red gate on a PR that had nothing to do with CPMP.
    """

    def test_the_expected_interval_does_not_come_from_the_real_clock(self):
        """If _TODAY still tracked now(), this file is one midnight from red."""
        assert _TODAY == date(2026, 6, 15), (
            "_TODAY must be a FIXED date. Deriving it from datetime.now() at "
            "import time is the defect: production recomputes per call, so the "
            "two disagree across a midnight boundary."
        )

    def test_moving_the_frozen_day_moves_the_answer(self, db, monkeypatch):
        """Proves the freeze is load-bearing AND that the sweep tracks the date.

        A frozen clock that production never reads would make these tests pass
        for the wrong reason — green because nothing moved, not because the
        sweep is right. So: same row, clock advanced one day, answer must
        advance one day.
        """
        import importlib

        from tools.govcon.contract_manager import compute_overdue_deliverables

        db.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) "
            "VALUES ('c-1', 'W15P7T-24-C-0001', 'Test', 'active')"
        )
        _add(db, "d-late", _LATE)
        db.commit()

        compute_overdue_deliverables()
        assert _row(db, "d-late")["days_overdue"] == 44

        # Cross midnight deliberately — the exact transition that used to be a
        # coin flip decided by when CI happened to start.
        tomorrow = _TODAY + timedelta(days=1)

        class _NextDay(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 1, tzinfo=tz)

        for name in ("tools.govcon.contract_manager",
                     "icdev.tools.govcon.contract_manager"):
            try:
                monkeypatch.setattr(importlib.import_module(name), "datetime",
                                    _NextDay, raising=False)
            except Exception:  # noqa: BLE001
                continue

        compute_overdue_deliverables()
        assert _row(db, "d-late")["days_overdue"] == 45, (
            "the sweep must recompute against the current date — and the test "
            "must be the thing that decides what 'current' means"
        )
