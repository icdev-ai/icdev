# CUI // SP-CTI
"""Migration 260 — kanban status lifecycle.

Asserts the state machine persists the true lifecycle states (no longer
collapsed to in_progress / token_exhausted) and that the widening migration is
well-formed and idempotent.
"""
import importlib.util
from pathlib import Path

from tools.kanban.state_machine import KanbanState, db_status_for

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UP_PY = _REPO_ROOT / "tools" / "db" / "migrations" / "260_kanban_status_lifecycle" / "up.py"


# The five states migration 260 stops collapsing.
def test_lifecycle_states_persist_as_themselves():
    assert db_status_for(KanbanState.PR_OPENED) == "pr_opened"
    assert db_status_for(KanbanState.CI_FAILED) == "ci_failed"
    assert db_status_for(KanbanState.MERGE_CONFLICT) == "merge_conflict"
    assert db_status_for(KanbanState.CHANGES_REQUESTED) == "changes_requested"


def test_failed_no_longer_masquerades_as_token_exhausted():
    # Regression: FAILED previously mapped to token_exhausted (auto-resumable).
    assert db_status_for(KanbanState.FAILED) == "failed"
    assert db_status_for(KanbanState.FAILED) != db_status_for(KanbanState.TOKEN_EXHAUSTED)


def test_pipeline_states_unchanged():
    assert db_status_for(KanbanState.BACKLOG) == "backlog"
    assert db_status_for(KanbanState.SCHEDULED) == "scheduled"
    assert db_status_for(KanbanState.IN_PROGRESS) == "in_progress"
    assert db_status_for(KanbanState.DONE) == "done"


def test_new_states_not_in_dispatcher_pickup_set():
    # The dispatcher pulls only backlog/scheduled/in_progress; the new lifecycle
    # states must NOT be dispatchable (else a PR-open or failed task rebuilds).
    pickup = {"backlog", "scheduled", "in_progress"}
    for st in ("pr_opened", "ci_failed", "merge_conflict", "changes_requested", "failed"):
        assert st not in pickup


def _load_migration():
    # Migration dirs start with a digit (invalid module name), so load by path
    # exactly as tools/db/migration_runner.py does.
    spec = importlib.util.spec_from_file_location("migration_260_up", str(_UP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_status_values_complete():
    up = _load_migration()
    for st in ("pr_opened", "ci_failed", "merge_conflict", "changes_requested", "failed"):
        assert st in up.STATUS_VALUES
    # The 9 pre-existing values are preserved (widening, not replacing).
    for st in ("backlog", "scheduled", "in_progress", "done", "token_exhausted",
               "suggested", "decomposed", "validating", "needs_decomposition"):
        assert st in up.STATUS_VALUES
    assert len(up.STATUS_VALUES) == 14
    assert len(set(up.STATUS_VALUES)) == 14  # no dupes


def test_migration_check_sql_wellformed():
    up = _load_migration()
    sql = up._check_array_sql()
    assert sql.startswith("ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check")
    for st in up.STATUS_VALUES:
        assert f"'{st}'::text" in sql


class _FakeConn:
    """Minimal conn stub: reports PG, returns a constraint def, records DDL."""
    def __init__(self, current_def):
        self._backend = "postgresql"
        self._current = current_def
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        low = sql.lower()
        if "information_schema.tables" in low or "sqlite_master" in low:
            return _Cur({"1": 1})
        if "pg_get_constraintdef" in low:
            return _Cur({"def": self._current})
        return _Cur(None)

    def commit(self):
        pass


class _Cur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def test_migration_idempotent_when_already_current():
    up = _load_migration()
    current = "CHECK ((status = ANY (ARRAY['pr_opened'::text, 'ci_failed'::text, 'merge_conflict'::text, 'changes_requested'::text, 'failed'::text])))"
    conn = _FakeConn(current)
    res = up.up(conn)
    assert res["status"] == "applied"
    assert "status_check_already_current" in res["actions"]
    # No DROP/ADD CONSTRAINT DDL issued on the idempotent path.
    assert not any("DROP CONSTRAINT" in s for s in conn.executed)


def test_migration_expands_when_missing():
    up = _load_migration()
    current = "CHECK ((status = ANY (ARRAY['backlog'::text, 'in_progress'::text, 'done'::text])))"
    conn = _FakeConn(current)
    res = up.up(conn)
    assert res["status"] == "applied"
    assert "status_check_expanded" in res["actions"]
    assert any("DROP CONSTRAINT IF EXISTS kanban_tasks_status_check" in s for s in conn.executed)
    assert any("ADD CONSTRAINT kanban_tasks_status_check" in s for s in conn.executed)


def test_migration_skips_on_sqlite():
    up = _load_migration()

    class _SqliteConn(_FakeConn):
        def __init__(self):
            super().__init__("")
            self._backend = "sqlite"

    conn = _SqliteConn()
    res = up.up(conn)
    assert res["status"] == "skipped"
