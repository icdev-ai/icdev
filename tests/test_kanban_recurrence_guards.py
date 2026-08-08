# CUI // SP-CTI
"""Guards for three failure modes that each cost a live incident on 2026-08-08.

1. `pr_watcher` completed a MANUAL GATE because a PR had been attached to the
   gate task. The gate released six self-modification slices, and every manual
   reset was undone within ~30s because pr_watcher re-applied `done` each cycle.
2. `task_factory` accepted a `task_type` the DB CHECK constraint forbids. SQLite
   does not enforce CHECK, so seeding "succeeded" locally and aborted mid-loop on
   PostgreSQL — the board ended up with nothing.
3. A seeder run from a git worktree wrote to a throwaway SQLite file, because
   `.env` is gitignored so the worktree had no PostgreSQL config. It reported
   "36/36 created" against a database that was deleted with the worktree.
"""
import sqlite3

import pytest

from tools.kanban.gates import is_manual_gate


# ── 1. pr_watcher must never complete a manual gate ───────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal connection: records writes, answers the title lookup."""

    def __init__(self, title=""):
        self.title = title
        self.writes = []
        self.committed = False

    def execute(self, sql, params=()):
        self.writes.append((" ".join(sql.split())[:60], params))
        if "SELECT" in sql.upper() and "title" in sql:
            return _FakeCursor([(self.title,)])
        return _FakeCursor([])

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _status_writes(conn):
    return [w for w in conn.writes if w[0].startswith("UPDATE kanban_tasks SET status")]


@pytest.mark.parametrize(
    "task_id,title",
    [
        ("hgx-gate-02", "MANUAL-MODE GATE - hold"),   # id shape AND title marker
        ("hgx-gate-01", ""),                          # id shape alone
        ("some-task", "MANUAL-MODE GATE - hold"),     # title marker alone
    ],
    ids=["both", "id-only", "title-only"],
)
def test_pr_watcher_refuses_to_complete_a_manual_gate(task_id, title):
    from tools.ci import pr_watcher

    conn = _FakeConn(title=title)
    ok = pr_watcher._set_task_status(
        lambda: conn, task_id, "done", reason="PR merged: https://example/pull/1"
    )

    assert ok is False, "completing a manual gate must be refused"
    assert not _status_writes(conn), "a manual gate's status must not be written"


def test_pr_watcher_still_completes_an_ordinary_task():
    """The guard must not break the watcher's actual job."""
    from tools.ci import pr_watcher

    conn = _FakeConn(title="Add a widget")
    ok = pr_watcher._set_task_status(
        lambda: conn, "hgx-par-01", "done", reason="PR merged: https://example/pull/2"
    )

    assert ok is True
    assert _status_writes(conn), "an ordinary task must still be completed"


def test_pr_watcher_may_still_move_a_gate_to_a_non_done_status():
    """Only completion is refused — the gate can still be parked or failed."""
    from tools.ci import pr_watcher

    conn = _FakeConn(title="MANUAL-MODE GATE - hold")
    ok = pr_watcher._set_task_status(lambda: conn, "hgx-gate-02", "failed")

    assert ok is True
    assert _status_writes(conn)


def test_the_predicate_itself_recognises_a_second_gate():
    """Regression cover for the id-shape half of the predicate."""
    assert is_manual_gate("hgx-gate-01", "")
    assert is_manual_gate("hgx-gate-00", "")
    assert not is_manual_gate("hgx-par-01", "")


# ── 2. task_factory validates task_type against the CHECK constraint ──────────


def test_create_tasks_rejects_a_task_type_the_db_forbids():
    from tools.kanban import task_factory

    with pytest.raises(ValueError) as exc:
        task_factory.create_tasks([
            {"id": "guard-test-01", "title": "t", "task_type": "bug"},
        ])
    assert "bug" in str(exc.value)
    assert "fix" in str(exc.value), "the error should name the legal values"


def test_valid_task_types_match_the_live_check_constraint():
    """If the DB vocabulary changes, this constant must change with it."""
    from tools.kanban.task_factory import VALID_TASK_TYPES

    assert VALID_TASK_TYPES == frozenset(
        {"build", "run", "fix", "research", "deploy", "test", "chore"}
    )


def test_create_tasks_accepts_every_legal_type(monkeypatch):
    from tools.kanban import task_factory

    seen = []
    monkeypatch.setattr(task_factory, "_assert_real_board", lambda conn: None)
    for t in sorted(task_factory.VALID_TASK_TYPES):
        seen.append(t)
    assert len(seen) == 7


# ── 3. board writes must not silently land in a throwaway SQLite DB ───────────


def test_assert_real_board_rejects_a_bare_sqlite_connection(monkeypatch):
    """conftest sets ICDEV_KANBAN_ALLOW_LOCAL_BOARD for the whole suite (tests
    legitimately seed a local board), so clear it to exercise the guard itself."""
    from tools.kanban.task_factory import BoardBackendError, _assert_real_board

    monkeypatch.delenv("ICDEV_KANBAN_ALLOW_LOCAL_BOARD", raising=False)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE kanban_tasks (id TEXT)")
    try:
        with pytest.raises(BoardBackendError) as exc:
            _assert_real_board(conn)
        assert "sqlite" in str(exc.value).lower()
    finally:
        conn.close()


def test_assert_real_board_can_be_overridden_for_tests(monkeypatch):
    """Tests and fresh installs legitimately seed an empty SQLite board."""
    from tools.kanban.task_factory import _assert_real_board

    monkeypatch.setenv("ICDEV_KANBAN_ALLOW_LOCAL_BOARD", "1")
    conn = sqlite3.connect(":memory:")
    try:
        _assert_real_board(conn)  # must not raise
    finally:
        conn.close()


# ── 4. the benchmark gate tolerates count drift but not content drift ─────────


_REPORT = """# Benchmark map

| # | Area | Tools | Position | Verdict | 48 | 2 |
| 2 | Delivery | Temporal | Ahead | **No adaptation needed** | 107 | 0 |

Measured: **107 modules** (floor 5) -> `built`. Surface: `tools/kanban/`.

**Verdict.** No adaptation needed; position **Ahead**.
"""


def _check(tmp_path, checked_in, regenerated):
    from tools.innovation import benchmark_report as br

    p = tmp_path / "map.md"
    p.write_text(checked_in, encoding="utf-8")
    return br.check_report(p, regenerated)


def test_count_only_drift_no_longer_fails_the_gate(tmp_path):
    """Adding a module shifts derived counts. That must not turn main red."""
    regenerated = _REPORT.replace("107", "108")
    result = _check(tmp_path, _REPORT, regenerated)
    assert result["in_sync"], result.get("diff")
    assert "counts drifted" in (result["reason"] or "")


def test_a_changed_verdict_still_fails_the_gate(tmp_path):
    """The gate's real job: catch content drift."""
    regenerated = _REPORT.replace("No adaptation needed", "Gap")
    result = _check(tmp_path, _REPORT, regenerated)
    assert not result["in_sync"]
    assert "Gap" in result["diff"]


def test_a_changed_position_still_fails_the_gate(tmp_path):
    regenerated = _REPORT.replace("position **Ahead**", "position **Behind**")
    result = _check(tmp_path, _REPORT, regenerated)
    assert not result["in_sync"]


def test_a_removed_row_still_fails_the_gate(tmp_path):
    regenerated = "\n".join(
        ln for ln in _REPORT.splitlines() if not ln.startswith("| 2 |")
    )
    result = _check(tmp_path, _REPORT, regenerated)
    assert not result["in_sync"]


def test_identical_content_is_in_sync(tmp_path):
    result = _check(tmp_path, _REPORT, _REPORT)
    assert result["in_sync"]
    assert result["reason"] is None
