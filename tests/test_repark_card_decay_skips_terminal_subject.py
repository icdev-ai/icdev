# CUI // SP-CTI
"""The suggested-decay sweep must not re-queue a REPARK card whose subject is done.

``orphan_requeue`` seeds ``kph-repark-<id>`` as ``suggested`` while ``<id>`` is
parked. When ``<id>`` is then finished by other means the card's premise is
gone, but ``_promote_stale_suggested`` re-queued it 48 h later regardless; the
dispatched card was parked by the same worktree guard and seeded
``kph-repark-kph-repark-<id>`` about itself (kph-repark-qa-fail-aed33993b690221d:
10 of 24 open REPARK cards had a ``done`` subject on 2026-10-19).
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import translating  # noqa: E402


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.fixture()
def db(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "k.db"))
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT,
            priority TEXT, status TEXT, updated_at TEXT, scheduled_at TEXT,
            branch_name TEXT, depends_on_task_id TEXT,
            failure_count INTEGER DEFAULT 0, last_failure_at TEXT,
            last_failure_reason TEXT
        );
        CREATE TABLE kanban_status_transitions (
            id TEXT PRIMARY KEY, task_id TEXT, from_status TEXT, to_status TEXT,
            actor TEXT, reason TEXT, recorded_at TEXT
        );
        """
    )
    yield raw
    raw.close()


@pytest.fixture()
def reflex(monkeypatch, db):
    from tools.genesis.reflexes import kanban as reflex

    def conn(*_a, **_kw):
        return translating(db, unclosable=True)

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", conn)
    monkeypatch.setattr(reflex, "get_connection", conn)
    monkeypatch.setattr(reflex, "_revive_quarantined_suggested", lambda c: None)
    monkeypatch.setattr(reflex, "_unblock_dep_chain", lambda c: None)
    return reflex


def _insert(raw, tid, status, updated_at=None):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, task_type, priority, status, "
        "updated_at, failure_count) VALUES (?,?,?,?,?,?,0)",
        (tid, tid, "chore", "high", status, updated_at or _ago(hours=72)),
    )
    raw.commit()


def _status(raw, tid):
    return raw.execute("SELECT status FROM kanban_tasks WHERE id = ?", (tid,)).fetchone()[0]


@pytest.mark.parametrize("subject_status", ["done", "cancelled", "superseded"])
def test_repark_card_with_terminal_subject_stays_out_of_dispatch(reflex, db, subject_status):
    _insert(db, "qa-fail-x1", subject_status, updated_at=_ago(hours=1))
    _insert(db, "kph-repark-qa-fail-x1", "suggested")

    reflex._promote_stale_suggested()

    assert _status(db, "kph-repark-qa-fail-x1") == "suggested", (
        "a REPARK card whose subject already finished was re-queued for dispatch"
    )


def test_repark_card_with_live_subject_is_still_promoted(reflex, db):
    """Positive control: the skip is about the SUBJECT, not the prefix."""
    _insert(db, "qa-fail-x2", "validating", updated_at=_ago(hours=1))
    _insert(db, "kph-repark-qa-fail-x2", "suggested")

    reflex._promote_stale_suggested()

    assert _status(db, "kph-repark-qa-fail-x2") == "scheduled"


def test_nested_repark_card_reads_its_immediate_subject(reflex, db):
    """``kph-repark-kph-repark-<id>`` is about the card ``kph-repark-<id>``."""
    _insert(db, "qa-fail-x3", "done", updated_at=_ago(hours=1))
    _insert(db, "kph-repark-qa-fail-x3", "done", updated_at=_ago(hours=1))
    _insert(db, "kph-repark-kph-repark-qa-fail-x3", "suggested")

    reflex._promote_stale_suggested()

    assert _status(db, "kph-repark-kph-repark-qa-fail-x3") == "suggested"


def test_orphan_card_with_done_subject_stays_out_of_dispatch(reflex, db):
    """``kph-orphan-<id>`` was decay-promoted 34 h after ``<id>`` was forced done
    (kph-orphan-qa-fail-aed33993b690221d, 2026-10-19)."""
    _insert(db, "qa-fail-x4", "done", updated_at=_ago(hours=1))
    _insert(db, "kph-orphan-qa-fail-x4", "suggested")

    reflex._promote_stale_suggested()

    assert _status(db, "kph-orphan-qa-fail-x4") == "suggested"


def test_stranded_card_with_done_subject_is_still_promoted(reflex, db):
    """A done subject IS a ``kph-stranded-`` card's premise -- not a reason to skip."""
    _insert(db, "qa-fail-x5", "done", updated_at=_ago(hours=1))
    _insert(db, "kph-stranded-qa-fail-x5", "suggested")

    reflex._promote_stale_suggested()

    assert _status(db, "kph-stranded-qa-fail-x5") == "scheduled"
