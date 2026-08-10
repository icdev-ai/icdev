# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/cpmp_monitor.py — card identity and dedup.

Two regressions are pinned here, both observed on the live board:

1. ``create_contract()`` defaults ``contract_number`` to ``''``, so the reflex's
   ``contract.get("contract_number", "N/A")`` never fired its default. Every
   unnumbered contract rendered the same title — ``"[CPMP] : Overdue
   Deliverables"`` — and the title-based dedup then collapsed findings from
   different contracts onto one card that named no contract at all.

2. The dedup matched ``dispatch_source = created_by``, but the kanban scheduler
   UPDATEs ``dispatch_source`` to ``'genesis_scheduler'`` when it dispatches a
   card (``kanban.py::_tag_task_source``). After the first dispatch the dedup
   stopped matching, so each later 3h pass re-filed a card already on the board.
"""
from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests._sql_compat import translating

_KANBAN_DDL = """
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
CREATE TABLE cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT,
    title           TEXT,
    status          TEXT
);
"""


def _storage_conn(raw):
    """Wrap *raw* the way the runtime wraps its connections.

    ``translating`` keeps the runtime's %s -> ? rewrite in front of sqlite3; a
    bare sqlite3 connection here would make every reflex query raise
    ``near "%": syntax error`` inside the caller's except, and the test would
    assert against a no-op it caused itself.

    ``unclosable`` because the reflex close()s every connection it opens, and an
    in-memory database dies with its connection.
    """
    conn = translating(raw, unclosable=True)
    # The reflex calls set_security_context() on each connection it opens;
    # TranslatingConnection would delegate that to sqlite3 and raise AttributeError.
    conn.set_security_context = lambda _ctx: None
    return conn


@pytest.fixture
def board():
    """One shared in-memory board the reflex can open repeatedly."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_KANBAN_DDL)
    conn = _storage_conn(raw)
    with patch("tools.db.storage.get_connection", return_value=conn):
        yield conn
    raw.close()


def _add_contract(conn, cid, number, title="Test Contract", status="active"):
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (%s, %s, %s, %s)",
        (cid, number, title, status),
    )
    conn.commit()


def _titles(conn):
    return [r["title"] for r in conn.execute("SELECT title FROM kanban_tasks").fetchall()]


_OVERDUE = {
    "type": "overdue_deliverables",
    "severity": "high",
    "source": "deliverables",
    "description": "5 CDRL(s) are past due and not yet accepted.",
    "suggested_action": "Submit or escalate overdue CDRLs immediately.",
}


def _run_reflex(issues):
    """Run the full pass with every detector but auto_detect_issues stubbed out."""
    from tools.genesis.reflexes.cpmp_monitor import run

    with ExitStack() as stack:
        stack.enter_context(patch(
            "tools.govcon.pmo_ai_advisor.auto_detect_issues",
            return_value={"status": "ok", "issues": issues, "total": len(issues)},
        ))
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
        return run()


# ---------------------------------------------------------------------------
# Regression 1: unnumbered contracts must not produce anonymous, colliding cards
# ---------------------------------------------------------------------------

class TestUnnumberedContracts:
    def test_blank_contract_number_files_no_card(self, board):
        _add_contract(board, "c-blank", "", title="Untitled Contract")

        result = _run_reflex([_OVERDUE])

        assert result["cards_created"] == 0
        assert result["contracts_unnumbered"] == 1
        assert _titles(board) == []

    def test_several_unnumbered_contracts_are_all_counted(self, board):
        for i in range(3):
            _add_contract(board, f"c-{i}", "", title="Untitled Contract")

        result = _run_reflex([_OVERDUE])

        assert result["contracts_scanned"] == 3
        assert result["contracts_unnumbered"] == 3
        assert _titles(board) == []

    def test_whitespace_only_number_counts_as_unnumbered(self, board):
        _add_contract(board, "c-ws", "   ")

        result = _run_reflex([_OVERDUE])

        assert result["contracts_unnumbered"] == 1
        assert _titles(board) == []

    def test_numbered_contract_still_files_a_card_naming_it(self, board):
        _add_contract(board, "c-real", "W15P7T-24-C-0001")

        result = _run_reflex([_OVERDUE])

        assert result["cards_created"] == 1
        assert result["contracts_unnumbered"] == 0
        assert _titles(board) == ["[CPMP] W15P7T-24-C-0001: Overdue Deliverables"]

    def test_two_numbered_contracts_get_distinct_cards(self, board):
        _add_contract(board, "c-a", "W15P7T-24-C-0001")
        _add_contract(board, "c-b", "W15P7T-24-C-0002")

        result = _run_reflex([_OVERDUE])

        # Before the fix both rendered "[CPMP] : Overdue Deliverables" and the
        # second was silently swallowed by the title dedup.
        assert result["cards_created"] == 2
        assert sorted(_titles(board)) == [
            "[CPMP] W15P7T-24-C-0001: Overdue Deliverables",
            "[CPMP] W15P7T-24-C-0002: Overdue Deliverables",
        ]


# ---------------------------------------------------------------------------
# Regression 2: dedup must survive the scheduler's dispatch_source rewrite
# ---------------------------------------------------------------------------

class TestDedupAfterDispatch:
    def _file(self, title="[CPMP] W15P7T-24-C-0001: Overdue Deliverables"):
        from tools.genesis.reflexes.cpmp_monitor import _suggest_kanban_card

        _suggest_kanban_card(
            title=title,
            description="5 CDRL(s) are past due.",
            priority="medium",
            context_data={"contract_id": "c-real"},
            created_by="cpmp_monitor",
        )

    def test_open_card_is_not_refiled(self, board):
        self._file()
        self._file()

        assert len(_titles(board)) == 1

    def test_dispatched_card_is_not_refiled(self, board):
        self._file()
        # The scheduler rewrites dispatch_source when it dispatches the card.
        board.execute(
            "UPDATE kanban_tasks SET dispatch_source = %s, status = %s",
            ("genesis_scheduler", "in_progress"),
        )
        board.commit()

        self._file()

        assert len(_titles(board)) == 1, "card was re-filed after dispatch rewrote dispatch_source"

    def test_distinct_titles_still_file_separately(self, board):
        self._file("[CPMP] W15P7T-24-C-0001: Overdue Deliverables")
        self._file("[CPMP] W15P7T-24-C-0002: Overdue Deliverables")

        assert len(_titles(board)) == 2

    def test_closed_card_may_be_refiled(self, board):
        """A done card should not suppress a recurrence — behaviour predates this fix."""
        self._file()
        board.execute("UPDATE kanban_tasks SET status = %s", ("done",))
        board.commit()

        self._file()

        assert len(_titles(board)) == 2
