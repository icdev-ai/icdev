# CUI // SP-CTI
"""Neither dispatch door may hand a worker a REPARK card whose subject is done.

kph-repark-qa-fail-aed33993b690221d stopped the suggested-decay sweep from
re-queueing such a card. kph-repark-qa-fail-a873b6f8944c7b5d had been
decay-promoted BEFORE that fix landed, with its subject already done
(2026-10-17), and was dispatched anyway: held ``token_exhausted`` by a
provider failover, then re-dispatched by the token-retry door with nothing to
do -- and the run was failed ``no_commits``. A card that is already
``scheduled`` or ``token_exhausted`` never passes through the decay sweep, so
the two dispatch doors ask the same question themselves.
"""
from __future__ import annotations

import ast
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
    monkeypatch.setattr(reflex, "_hand_parked_task_to_pr_watcher", lambda *a, **k: None)
    monkeypatch.setattr(
        reflex, "_load_resume_at",
        lambda _tid: datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    monkeypatch.setattr(reflex, "_circuit_breaker_tripped", lambda t: (False, 0, 5))
    monkeypatch.setattr(reflex, "_get_retry_count", lambda _tid: 0)
    return reflex


def _insert(raw, tid, status):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, task_type, priority, status, "
        "updated_at, failure_count) VALUES (?,?,?,?,?,?,0)",
        (tid, tid, "chore", "high", status, _ago(hours=1)),
    )
    raw.commit()


def test_token_retry_skips_a_repark_card_whose_subject_is_done(reflex, db):
    _insert(db, "qa-fail-y1", "done")
    _insert(db, "kph-repark-qa-fail-y1", "token_exhausted")

    ready = [t["id"] for t in reflex._check_token_exhausted_tasks()]

    assert "kph-repark-qa-fail-y1" not in ready, (
        "the token-retry door re-dispatched a REPARK card whose subject is done"
    )


def test_token_retry_still_retries_a_repark_card_with_a_live_subject(reflex, db):
    """Positive control: the skip is about the SUBJECT, not the prefix."""
    _insert(db, "qa-fail-y2", "validating")
    _insert(db, "kph-repark-qa-fail-y2", "token_exhausted")

    ready = [t["id"] for t in reflex._check_token_exhausted_tasks()]

    assert ready == ["kph-repark-qa-fail-y2"]


def test_due_task_selection_asks_the_repark_predicate():
    """`_get_due_tasks` must filter beside the manual-gate and fixture checks.

    Static, like the fixture door's own guard in
    ``tests/kanban/test_e2e_fixture_not_dispatchable.py``: the full selection
    pulls in capacity, backpressure and respawn guards that are not the subject.
    """
    src = (ROOT / "tools" / "genesis" / "reflexes" / "kanban.py").read_text(encoding="utf-8")
    fn = next(
        (n for n in ast.walk(ast.parse(src))
         if isinstance(n, ast.FunctionDef) and n.name == "_get_due_tasks"),
        None,
    )
    assert fn is not None, "_get_due_tasks not found"
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_repark_subject_is_terminal" in called, (
        "_get_due_tasks no longer skips a REPARK card whose subject is done -- a "
        "card already 'scheduled' never meets the decay-sweep skip"
    )
