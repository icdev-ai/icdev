# CUI // SP-CTI
"""Manual-mode gates must not consume autonomous execution slots.

``_count_in_progress()`` bounds dispatch against ``MAX_IN_PROGRESS`` (default
3). A manual-mode gate is a SENTINEL held ``in_progress`` FOREVER by design —
it never executes — but the counter was a bare
``COUNT(*) WHERE status='in_progress'`` and counted it anyway.

Observed on the live board 2026-07-31: ``aca-gate-00`` and ``tsr-gate-00`` were
both parked, so two of the three execution slots were permanently occupied and
the loop could build exactly one task at a time. Nothing reported it — a gate
showing as in_progress is correct on the board, it just is not work. The more
cards a board gates, the less it can build.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools.kanban.gates import GATE_TITLE_MARKER, is_manual_gate

_KANBAN_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'backlog',
    priority    TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT
);
"""

_ROWS = [
    # Two parked gates — sentinels, never executed.
    ("aca-gate-00", f"{GATE_TITLE_MARKER} - ACA integrity (held)", "in_progress"),
    ("tsr-gate-00", f"{GATE_TITLE_MARKER} - TSR remediation (held)", "in_progress"),
    # One task genuinely executing.
    ("tsr-core-01-d3", "Triage failures against shared vs clean checkout", "in_progress"),
    # Not in_progress at all — must never count.
    ("tsr-ai-01", "LLM, RAG, Cortex & memory test failures", "scheduled"),
    ("tsr-doc-01", "Document intelligence & quality test failures", "backlog"),
]


@pytest.fixture()
def board(tmp_path, monkeypatch):
    """A temp board carrying two parked gates and one executing task."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_KANBAN_DDL)
    conn.executemany(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)", _ROWS
    )
    conn.commit()
    conn.close()

    reflex = importlib.import_module("tools.genesis.reflexes.kanban")
    storage = importlib.import_module("tools.db.storage")
    real_get_connection = storage.get_connection

    # Real storage layer, temp path: the counter runs %s-free SQL here, but
    # routing through StorageConnection keeps this honest if that changes.
    monkeypatch.setattr(
        reflex, "get_connection", lambda *a, **kw: real_get_connection(str(db))
    )
    return reflex


def test_parked_gates_do_not_occupy_execution_slots(board):
    """Three rows are in_progress; only one of them is work."""
    assert board._count_in_progress() == 1


def test_slots_remain_available_despite_gates(board):
    """The regression that mattered: gates must not starve the loop.

    With the old bare COUNT(*) this was 3 of 3 occupied and zero free, so the
    loop dispatched nothing while two of the three "busy" tasks were sentinels
    that would never finish.
    """
    free = board.MAX_IN_PROGRESS - board._count_in_progress()
    assert free == board.MAX_IN_PROGRESS - 1
    assert free > 0, "a board with parked gates must still be able to dispatch"


def test_gate_predicate_matches_id_or_title():
    """The counter must use the shared predicate, not an inline LIKE.

    is_manual_gate matches on EITHER the id suffix or the title marker, so a
    gate stays recognised when one of the two is renamed.
    """
    assert is_manual_gate("prem-gate-00", "anything")
    assert is_manual_gate("some-other-id", f"{GATE_TITLE_MARKER} - held")
    assert not is_manual_gate("tsr-core-01-d3", "Triage failures")


def test_counter_counts_only_in_progress(board, tmp_path):
    """scheduled/backlog rows are not slots — guards an over-broad fix."""
    assert board._count_in_progress() == 1  # not 3, not 5
