# CUI // SP-CTI
"""Overdue-CDRL marking: the writer, its state machine, and its missing caller.

``contract_manager.compute_overdue_deliverables`` is the ONLY writer of
``cpmp_deliverables.status = 'overdue'`` and of ``days_overdue``. It was
reachable only through its own ``--compute-overdue`` CLI flag, which no
scheduler, route or goal invokes. Measured on the live board 2026-08-13: 26
past-due deliverables, ZERO marked overdue, ZERO with days_overdue > 0 — while
cpmp_monitor kept filing "N CDRL(s) are past due" cards, because
pmo_ai_advisor counts by date arithmetic rather than by status. The finding was
visible and the state it describes was never written, so nothing went red.

Five readers were silently pinned to zero by that, the worst being
``negative_event_tracker``, whose delinquent-delivery arm gates on
``days_overdue > 0`` and therefore could never record a negative event for a
late CDRL.

These tests pin, in order: that the marker runs, that it keeps the day count
current, that it does not blame the contractor for the government's review
queue, that the state machine can express every transition it performs, and —
the regression that actually mattered — that the reflex calls it at all.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import StorageConnection


# ---------------------------------------------------------------------------
# A real SQLite database behind contract_manager._get_db
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE cpmp_deliverables (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    cdrl_number TEXT,
    title TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    -- Real column, and load-bearing: OVERDUE_DELIVERABLE_SQL filters on
    -- `submitted_date IS NULL` so a CDRL that was actually handed over is
    -- never swept, whatever its status says. Omitting it here does not make
    -- the test lenient — SQLite raises `no such column`, so every sweep test
    -- errors out.
    submitted_date TEXT,
    status TEXT DEFAULT 'not_started',
    days_overdue INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE cpmp_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT
);
CREATE TABLE audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    session_id TEXT
);
"""


def _Conn(path):
    """A SQLite connection behind the real StorageConnection wrapper.

    Runtime code here is authored for PostgreSQL and uses %s placeholders.
    Handing it a bare sqlite3 connection would raise 'near "%": syntax error',
    which contract_manager._audit swallows — a silently green test. Going
    through StorageConnection uses the same translation the production path
    does instead of a hand-rolled stand-in that can drift from it.
    """
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """contract_manager wired to a throwaway SQLite db, plus a helper API."""
    import tools.govcon.contract_manager as mod

    db = tmp_path / "cpmp.db"
    boot = sqlite3.connect(db)
    boot.executescript(_SCHEMA)
    boot.commit()
    boot.close()

    monkeypatch.setattr(mod, "_get_db", lambda: _Conn(db))

    class _Helper:
        module = mod

        @staticmethod
        def add(did, status, due_days_ago=45, days_overdue=0, contract_id="c1"):
            c = _Conn(db)
            c.execute(
                "INSERT INTO cpmp_deliverables "
                "(id, contract_id, cdrl_number, title, due_date, status, days_overdue) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (did, contract_id, "A001", f"CDRL {did}", _days_ago(due_days_ago), status, days_overdue),
            )
            c.commit()
            c.close()

        @staticmethod
        def row(did):
            c = _Conn(db)
            r = c.execute("SELECT * FROM cpmp_deliverables WHERE id = %s", (did,)).fetchone()
            c.close()
            return dict(r) if r else None

        @staticmethod
        def history(did):
            c = _Conn(db)
            rows = c.execute(
                "SELECT * FROM cpmp_status_history WHERE entity_id = %s", (did,)
            ).fetchall()
            c.close()
            return [dict(r) for r in rows]

    return _Helper()


# ---------------------------------------------------------------------------
# The marker writes the state five readers depend on
# ---------------------------------------------------------------------------


def test_past_due_predelivery_cdrl_is_marked_overdue_with_a_day_count(cm):
    cm.add("d1", "not_started", due_days_ago=45)

    result = cm.module.compute_overdue_deliverables()

    row = cm.row("d1")
    assert row["status"] == "overdue"
    # negative_event_tracker gates on days_overdue > 0; a marked row with a
    # zero counter is still invisible to it.
    assert row["days_overdue"] >= 44, row
    assert result["overdue_count"] == 1


def test_not_yet_due_cdrl_is_left_alone(cm):
    cm.add("d1", "in_progress", due_days_ago=-10)  # due in the future

    cm.module.compute_overdue_deliverables()

    assert cm.row("d1")["status"] == "in_progress"


def test_contract_id_filter_scopes_the_marking(cm):
    cm.add("d1", "in_progress", contract_id="c1")
    cm.add("d2", "in_progress", contract_id="c2")

    cm.module.compute_overdue_deliverables("c1")

    assert cm.row("d1")["status"] == "overdue"
    assert cm.row("d2")["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Discriminating: the previous query excluded 'overdue', freezing the counter
# ---------------------------------------------------------------------------


def test_already_overdue_row_has_its_day_count_refreshed(cm):
    # Detected once at 5 days late; it is now 60 days late.
    cm.add("d1", "overdue", due_days_ago=60, days_overdue=5)

    result = cm.module.compute_overdue_deliverables()

    # Frozen at 5, negative_event_tracker scores this 'low' (>7 medium,
    # >14 high, >30 critical) forever, however late it actually gets.
    assert cm.row("d1")["days_overdue"] >= 59, cm.row("d1")
    assert result["days_refreshed"] == 1
    # A refresh is not a transition, so it must not be counted as one.
    assert result["overdue_count"] == 0


def test_refresh_does_not_append_a_status_history_row_every_cycle(cm):
    cm.add("d1", "in_progress", due_days_ago=45)

    cm.module.compute_overdue_deliverables()
    cm.module.compute_overdue_deliverables()
    cm.module.compute_overdue_deliverables()

    # cpmp_status_history is append-only (NIST AU-2). The real transition is
    # one event; re-running the reflex every 3h must not forge three more.
    history = cm.history("d1")
    assert len(history) == 1, history
    assert history[0]["old_status"] == "in_progress"
    assert history[0]["new_status"] == "overdue"


# ---------------------------------------------------------------------------
# Discriminating: delivered CDRLs are not delinquent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["submitted", "government_review", "resubmitted"])
def test_delivered_cdrl_awaiting_the_government_is_not_marked_delinquent(cm, status):
    cm.add("d1", status, due_days_ago=45)

    cm.module.compute_overdue_deliverables()

    # The contractor met the delivery date; the artifact is in the
    # government's queue. Marking it overdue would file a
    # 'delinquent_delivery' negative event against the wrong party.
    assert cm.row("d1")["status"] == status


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_closed_cdrl_is_not_reopened_as_overdue(cm, status):
    cm.add("d1", status, due_days_ago=45)

    cm.module.compute_overdue_deliverables()

    assert cm.row("d1")["status"] == status


@pytest.mark.parametrize(
    "status", ["not_started", "in_progress", "draft_complete", "internal_review"]
)
def test_every_predelivery_status_is_markable(cm, status):
    cm.add("d1", status, due_days_ago=45)

    cm.module.compute_overdue_deliverables()

    # draft_complete and internal_review are just as undelivered as
    # not_started; only in_progress was reachable before.
    assert cm.row("d1")["status"] == "overdue"


# ---------------------------------------------------------------------------
# Discriminating: the state machine must be able to express what we write
# ---------------------------------------------------------------------------


def test_every_predelivery_status_declares_the_overdue_transition():
    from tools.govcon.contract_manager import (
        DELIVERABLE_TRANSITIONS,
        PREDELIVERY_DELIVERABLE_STATUSES,
    )

    for status in PREDELIVERY_DELIVERABLE_STATUSES:
        assert "overdue" in DELIVERABLE_TRANSITIONS.get(status, []), (
            f"compute_overdue_deliverables() drives {status} -> overdue, but "
            f"transition_deliverable() would reject it as invalid"
        )


def test_delivered_statuses_are_absent_from_the_predelivery_set():
    from tools.govcon.contract_manager import PREDELIVERY_DELIVERABLE_STATUSES

    for status in ("submitted", "government_review", "accepted", "rejected", "resubmitted"):
        assert status not in PREDELIVERY_DELIVERABLE_STATUSES


# ---------------------------------------------------------------------------
# The regression that mattered: the writer had no caller
# ---------------------------------------------------------------------------


class _ReflexConn:
    """Minimal cpmp_contracts source for the reflex's contract loop."""

    def set_security_context(self, _ctx):
        pass

    def execute(self, sql, params=()):
        assert "FROM cpmp_contracts" in sql, sql

        class _R:
            @staticmethod
            def fetchall():
                return [{"id": "c1", "contract_number": "", "title": "Untitled Contract"}]

        return _R()

    def close(self):
        pass


def _patch_every_alias(monkeypatch, module_suffix, attr, value):
    """Patch ``attr`` on EVERY sys.modules object for ``tools.<suffix>``.

    ``tools/`` is a shim over ``icdev.tools``, and the two names do NOT always
    resolve to one object: ``import tools.db.storage as s`` binds the icdev
    module, while ``from tools.db.storage import get_connection`` — what the
    reflex does — reads ``sys.modules['tools.db.storage']``, a DIFFERENT
    object. Patching only the one you imported leaves the fake uninstalled and
    the test silently runs against the live Postgres board instead of failing.
    """
    import importlib
    import sys

    patched = 0
    for name in (f"tools.{module_suffix}", f"icdev.tools.{module_suffix}"):
        try:
            importlib.import_module(name)
        except Exception:
            continue
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, value)
            patched += 1
    assert patched, f"no module alias for tools.{module_suffix} exposed {attr}"
    return patched


def test_patch_helper_covers_the_shim_aliases(monkeypatch):
    """Guard the guard: if the shim stops splitting, this test says so."""
    _patch_every_alias(monkeypatch, "db.storage", "get_connection", lambda *a, **k: None)
    from tools.db.storage import get_connection

    assert get_connection() is None, (
        "from-import still resolved the real get_connection; a reflex test "
        "using this helper would hit the live board"
    )


@pytest.fixture
def reflex_run(monkeypatch):
    """Run cpmp_monitor's deliverables pass, recording marker invocations."""
    import tools.genesis.reflexes.cpmp_monitor as rx

    _patch_every_alias(monkeypatch, "db.storage", "get_connection", lambda *a, **k: _ReflexConn())
    _patch_every_alias(
        monkeypatch, "govcon.cdrl_generator", "generate_all_due", lambda *a, **k: {"generated": 0}
    )
    monkeypatch.setattr(rx, "_write_memory_log", lambda _r: None)

    calls = []

    def _spy(contract_id=None):
        calls.append(contract_id)
        return {"status": "ok", "overdue_count": 2, "days_refreshed": 0}

    _patch_every_alias(monkeypatch, "govcon.contract_manager", "compute_overdue_deliverables", _spy)
    return rx, calls


def test_reflex_deliverables_pass_invokes_the_overdue_marker(reflex_run):
    rx, calls = reflex_run

    results = rx.run({"pass_type": "deliverables"})

    # Without this call the marker is dead code behind a CLI flag, and every
    # past-due CDRL on the board stays not_started with days_overdue = 0.
    #
    # Called ONCE and UNSCOPED (contract_id=None), not once per contract. The
    # per-contract placement this test originally pinned was dropped when
    # origin/main turned out to have fixed the same defect with an unscoped
    # sweep ahead of the contract loop; keeping both auto-merged cleanly into a
    # genuine double sweep. Unscoped is the stronger contract — the loop walks
    # status='active' only, while portfolio_manager counts overdue CDRLs across
    # ('active','option_pending'), and an option-pending contract's deliverables
    # are no less late. `[None]`, not `["c1"]`, is therefore the assertion, and
    # the length pins that the duplicate call does not come back.
    assert calls == [None], f"marker never invoked exactly once; calls={calls}"
    assert results["deliverables_marked_overdue"] == 2
    assert not results["errors"], results["errors"]


def test_full_pass_also_maintains_deliverable_state(reflex_run):
    rx, calls = reflex_run

    rx.run({"pass_type": "full"})

    assert calls == [None]


def test_marker_failure_is_recorded_and_does_not_abort_the_reflex(monkeypatch, reflex_run):
    rx, _ = reflex_run

    def _boom(contract_id=None):
        raise RuntimeError("db down")

    _patch_every_alias(monkeypatch, "govcon.contract_manager", "compute_overdue_deliverables", _boom)

    results = rx.run({"pass_type": "deliverables"})

    # A reflex that returns no 'status' key is scored a failure forever and
    # self-circuit-breaks, so a single bad contract must not take it down.
    assert results["status"] == "ok"
    assert any("Overdue sweep" in e for e in results["errors"]), results["errors"]


# ---------------------------------------------------------------------------
# Mirror parity — the reflex ships in both trees
# ---------------------------------------------------------------------------


def test_reflex_mirror_is_in_sync():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    a = root / "tools" / "genesis" / "reflexes" / "cpmp_monitor.py"
    b = root / "icdev" / "tools" / "genesis" / "reflexes" / "cpmp_monitor.py"
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8"), (
        "cpmp_monitor.py differs between tools/ and icdev/tools/"
    )
