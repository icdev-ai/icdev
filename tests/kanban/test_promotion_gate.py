# CUI // SP-CTI
"""The record-not-card disposition is RE-DERIVED at promotion (autonomy-act-07).

The rule itself (``merge_after_escalation`` / ``ledger_landing`` /
``card_disposition``) is left REAL in every test here — a test that stubbed the
predicate would be testing the stub. What is faked is only the four READS the
gate needs: the projection, the two audit row sets and the subject's board
status. Patched by ``importlib.import_module`` + ``setattr`` so the object the
gate resolves is the object the test rebound (CLAUDE.md's namespace rule).
"""
from __future__ import annotations

import ast
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

DF = importlib.import_module("tools.kanban.detector_findings")
PG = importlib.import_module("tools.kanban.promotion_gate")

SUBJECT = "artifact-fresh-a7486018c7"
CARD = "task-det-04931fe8de"
FINDING = "04931fe8de9b8dd9"

_T0 = datetime(2026, 9, 12, 16, 58, 54, tzinfo=timezone.utc)


def _iso(minutes: int) -> str:
    return (_T0 + timedelta(minutes=minutes)).isoformat()


def _escalate(subject: str = SUBJECT, minutes: int = 0) -> dict:
    return {"action": "pr_watcher.escalate",
            "d": json.dumps({"task_id": subject, "reason": "conflict, budget spent"}),
            "created_at": _iso(minutes)}


def _watcher_merge(subject: str = SUBJECT, minutes: int = 264) -> dict:
    return {"action": "pr_watcher.merge",
            "d": json.dumps({"task_id": subject, "reason": "auto-merge ok"}),
            "created_at": _iso(minutes)}


def _ledger_landing(subject: str = SUBJECT, minutes: int = 264) -> dict:
    return {"action": f"{DF.LEDGER_ACTION_PREFIX}{subject}",
            "d": json.dumps({"task_id": subject, "source": DF.LEDGER_SOURCE}),
            "created_at": _iso(minutes)}


def _finding(subject: str = SUBJECT, task_id: str = CARD) -> dict:
    return {"finding_id": FINDING, "detector": DF.DETECTOR_RECOVERY,
            "subject": subject, "task_id": task_id, "status": DF.FINDING_ACTIVE,
            "title": f"[NEEDED-A-HUMAN] {subject}", "priority": "medium",
            "seen_count": 1, "card_count": 1,
            # The verdict AS STORED AT SEEDING: the escalation was the newer
            # row. Every test that expects a `record` proves this was NOT read.
            "evidence": {"merge_after_escalation": {
                "measurable": True, "superseded": False, "merged_at": None,
                "reason": "the escalation is the newer of the two rows"}}}


@pytest.fixture
def board(monkeypatch):
    """Rebind the four reads; return a mutable dict the test fills in."""
    state = {"findings": [_finding()], "watcher": [], "ledger": [],
             "status": {}, "tables": True}
    monkeypatch.setattr(DF, "tables_present", lambda conn: state["tables"])
    monkeypatch.setattr(DF, "list_findings",
                        lambda conn=None, **kw: list(state["findings"]))
    monkeypatch.setattr(DF, "watcher_outcome_rows",
                        lambda conn, **kw: list(state["watcher"]))
    monkeypatch.setattr(DF, "_ledger_rows_safe", lambda conn: list(state["ledger"]))
    monkeypatch.setattr(DF, "_task_status",
                        lambda conn, task_id: state["status"].get(str(task_id)))
    monkeypatch.delenv(PG.MODE_ENV, raising=False)
    return state


# ── the defect itself ────────────────────────────────────────────────────────
def test_a_merge_that_landed_after_seeding_turns_the_card_into_a_record(board):
    """21:22 on 2026-09-12: the subject merged while the card sat in the queue."""
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["disposition"] == DF.DISPOSITION_RECORD
    assert v["withheld"] is True
    assert v["landed_via"] == DF.LANDED_VIA_WATCHER
    # Derived from the rows, not from the seeding-time evidence above.
    assert "nothing is left to land" in v["reason"]


def test_the_stored_seeding_verdict_is_never_read(board):
    """The finding still SAYS `superseded: False`; the live rows say otherwise."""
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}
    stored = board["findings"][0]["evidence"]["merge_after_escalation"]
    assert stored["superseded"] is False        # the stale verdict, unchanged

    assert PG.verdicts([CARD], conn=object())[CARD]["withheld"] is True


def test_a_land_py_subject_is_recorded_through_the_ledger_door(board):
    """No `pr_watcher.merge` row at all — the second door carries it (act-06)."""
    board["watcher"] = [_escalate()]
    board["ledger"] = [_ledger_landing()]
    board["status"] = {SUBJECT: "done"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["withheld"] is True
    assert v["landed_via"] == DF.LANDED_VIA_LEDGER


def test_filter_promotable_drops_only_the_record_and_keeps_the_order(board):
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}
    tasks = [{"id": "a"}, {"id": CARD}, {"id": "b"}]

    keep, held = PG.filter_promotable(tasks, conn=object(), door="test")

    assert [t["id"] for t in keep] == ["a", "b"]
    assert set(held) == {CARD}
    assert held[CARD]["finding_id"] == FINDING


# ── every unknown still dispatches (acceptance criterion 3) ──────────────────
def test_an_unreadable_order_dispatches(board):
    """No escalation to order against: UNMEASURABLE, and `superseded` is None."""
    board["watcher"] = [_watcher_merge()]           # a merge, no escalation
    board["status"] = {SUBJECT: "done"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["disposition"] == DF.DISPOSITION_CARD
    assert v["withheld"] is False
    order = DF.merge_after_escalation(board["watcher"], SUBJECT)
    assert order["measurable"] is False
    assert order["superseded"] is None            # never False


def test_a_subject_not_on_the_board_dispatches(board):
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {}                          # _task_status -> None

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["withheld"] is False
    assert "not on the board" in v["reason"]


def test_a_subject_still_in_flight_dispatches(board):
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "in_progress"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["withheld"] is False
    assert "not closed" in v["reason"]


def test_a_subject_that_never_landed_dispatches(board):
    board["watcher"] = [_escalate()]
    board["status"] = {SUBJECT: "done"}           # closed, but nothing landed

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["withheld"] is False


def test_an_unreadable_board_dispatches(board, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(DF, "list_findings", _boom)

    assert PG.verdicts([CARD], conn=object()) == {}


def test_an_absent_projection_dispatches(board):
    board["tables"] = False

    assert PG.verdicts([CARD], conn=object()) == {}


def test_a_task_with_no_finding_gets_no_opinion(board):
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}

    assert "some-other-task" not in PG.verdicts(["some-other-task"], conn=object())


def test_no_candidates_reads_nothing(board, monkeypatch):
    """The common case must not pay for the two big audit reads."""
    def _never(*_a, **_k):
        raise AssertionError("the audit trail was read with no candidates")

    monkeypatch.setattr(DF, "watcher_outcome_rows", _never)
    monkeypatch.setattr(DF, "_ledger_rows_safe", _never)

    assert PG.verdicts([], conn=object()) == {}


# ── the finding is kept, never rewritten (acceptance criterion 2) ────────────
def test_the_gate_writes_nothing(board, monkeypatch):
    """`_upsert_finding`, `seen_count` and `_clear_missing` are untouched."""
    for name in ("_upsert_finding", "_clear_missing", "consume"):
        monkeypatch.setattr(DF, name, _forbidden(name))
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["withheld"] is True
    # The projection row the gate was handed is byte-identical afterwards.
    assert board["findings"][0] == _finding()


def _forbidden(name):
    def _f(*_a, **_k):
        raise AssertionError(f"promotion_gate must never call {name}")
    return _f


# ── modes ────────────────────────────────────────────────────────────────────
def test_report_mode_derives_the_verdict_and_withholds_nothing(board, monkeypatch):
    monkeypatch.setenv(PG.MODE_ENV, "report")
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}

    v = PG.verdicts([CARD], conn=object())[CARD]

    assert v["would_withhold"] is True
    assert v["withheld"] is False
    keep, held = PG.filter_promotable([{"id": CARD}], conn=object())
    assert [t["id"] for t in keep] == [CARD]
    assert held == {}


def test_off_mode_reads_nothing(board, monkeypatch):
    monkeypatch.setenv(PG.MODE_ENV, "off")

    def _never(*_a, **_k):
        raise AssertionError("the projection was read with the gate off")

    monkeypatch.setattr(DF, "list_findings", _never)

    assert PG.verdicts([CARD], conn=object()) == {}


def test_the_default_is_enforce(monkeypatch):
    monkeypatch.delenv(PG.MODE_ENV, raising=False)
    assert PG.mode() == PG.MODE_ENFORCE
    monkeypatch.setenv(PG.MODE_ENV, "0")
    assert PG.mode() == PG.MODE_OFF
    monkeypatch.setenv(PG.MODE_ENV, "nonsense")
    assert PG.mode() == PG.MODE_ENFORCE     # an unreadable setting never disarms


# ── the doors are wired (acceptance criterion 2: withheld from DISPATCH) ─────
def test_promote_backlog_to_scheduled_withholds_a_record(board, monkeypatch):
    board["watcher"] = [_escalate(), _watcher_merge()]
    board["status"] = {SUBJECT: "done"}
    mod = importlib.import_module("tools.kanban.promote_backlog_to_scheduled")

    rows = [{"id": CARD, "title": "[NEEDED-A-HUMAN] " + SUBJECT, "priority": "medium",
             "project_id": None, "depends_on_task_id": None},
            {"id": "ordinary-01", "title": "ordinary work", "priority": "medium",
             "project_id": None, "depends_on_task_id": None}]
    conn = _FakeConn(rows)
    monkeypatch.setattr(mod, "get_connection", lambda: conn)
    monkeypatch.setattr(mod, "_deps_satisfied", lambda tid, c: True)
    monkeypatch.setattr(mod, "_is_manual_gate", lambda tid, title=None: False)
    monkeypatch.setattr(mod, "_is_test_fixture", lambda tid, title=None: False)

    promoted = mod.promote()

    assert promoted == ["ordinary-01"]
    assert CARD not in "".join(conn.writes)


def _asks_the_gate(module_name: str, func_name: str) -> bool:
    """Does ``func_name`` in ``module_name`` import AND call the gate?

    An AST walk of the function's own body, not a substring of the file: the
    import lives inside these functions (a module-level one would drag the
    board reader into every dispatcher import), so "somewhere in the file" is
    not the question being asked.
    """
    path = importlib.import_module(module_name).__file__
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        imported = any(
            isinstance(n, ast.ImportFrom) and n.module == "tools.kanban.promotion_gate"
            and any(a.name == "filter_promotable" for a in n.names)
            for n in ast.walk(node))
        called = any(
            isinstance(n, ast.Call) and getattr(n.func, "id", "") == "filter_promotable"
            for n in ast.walk(node))
        return imported and called
    raise AssertionError(f"{module_name}.{func_name} not found")


def test_the_dispatcher_asks_the_gate_too():
    """The second door, for a card that reached `scheduled` without the first."""
    assert _asks_the_gate("tools.genesis.reflexes.kanban", "_get_due_tasks")


def test_the_dashboard_promote_all_door_asks_the_gate():
    assert _asks_the_gate("tools.dashboard.api.kanban", "promote_all_suggested")


class _FakeConn:
    """Just enough connection for ``promote_backlog_to_scheduled.promote``."""

    def __init__(self, rows):
        self._rows = rows
        self.writes: list[str] = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            return _FakeCursor(self._rows)
        self.writes.append(f"{sql} {params}")
        return _FakeCursor([])

    def commit(self):
        return None

    def close(self):
        return None


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None
