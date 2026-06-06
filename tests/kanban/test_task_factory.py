# CUI // SP-CTI
"""Tests for tools/kanban/task_factory.py — the deadlock-safe creation path."""
import sqlite3

import pytest

from tools.db.storage import get_connection
from tools.kanban.task_factory import TaskSpec, create_tasks, SeedValidationError

_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT DEFAULT 'build',
    priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'backlog', scheduled_at TEXT,
    created_at TEXT, updated_at TEXT, completed_at TEXT, depends_on_task_id TEXT,
    failure_count INTEGER DEFAULT 0, last_failure_reason TEXT, completed_via_bypass INTEGER DEFAULT 0,
    project_id TEXT, classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS kanban_task_deps (
    task_id TEXT NOT NULL, depends_on_id TEXT NOT NULL, created_at TEXT,
    PRIMARY KEY (task_id, depends_on_id)
);
CREATE TABLE IF NOT EXISTS kanban_status_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, from_status TEXT, to_status TEXT,
    actor TEXT, reason TEXT, created_at TEXT
);
"""


@pytest.fixture(autouse=True)
def _kanban_db(tmp_path, monkeypatch):
    db_path = tmp_path / "factory_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    yield

GOOD_DESC = (
    "Implement the loader in tools/foo/loader.py, reusing the parser in "
    "tools/foo/parser.py. Acceptance criteria: load() returns a dict and raises "
    "on bad input. Test plan: add tests/foo/test_loader.py with pytest covering "
    "the happy path and error path; run pytest -q to verify."
)


def _spec(tid, **kw):
    kw.setdefault("title", tid)
    kw.setdefault("description", GOOD_DESC)
    kw.setdefault("task_type", "build")
    kw.setdefault("priority", "high")
    return TaskSpec(id=tid, **kw)


def _status(tid):
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM kanban_tasks WHERE id=?", (tid,)).fetchone()
    return dict(row)["status"] if row else None


def test_create_defaults_to_backlog():
    r = create_tasks("tf", [_spec("tf-a-01")], register_project=False, llm_grade=False)
    assert r.seeded == ["tf-a-01"]
    assert _status("tf-a-01") == "backlog"


def test_idempotent_skip_existing():
    create_tasks("tf", [_spec("tf-b-01")], register_project=False, llm_grade=False)
    r2 = create_tasks("tf", [_spec("tf-b-01")], register_project=False, llm_grade=False)
    assert r2.seeded == []
    assert r2.skipped == ["tf-b-01"]


def test_scheduled_without_scheduled_at_raises():
    with pytest.raises(SeedValidationError) as exc:
        create_tasks("tf", [_spec("tf-c-01", status="scheduled")],
                     register_project=False, llm_grade=False)
    assert "scheduled_at" in str(exc.value)


def test_scheduled_with_time_allowed():
    r = create_tasks(
        "tf",
        [_spec("tf-d-01", status="scheduled", scheduled_at="2030-01-01T00:00:00+00:00")],
        register_project=False, llm_grade=False,
    )
    assert r.seeded == ["tf-d-01"]
    assert _status("tf-d-01") == "scheduled"


def test_non_strict_coerces_scheduled_null_to_backlog():
    r = create_tasks("tf", [_spec("tf-e-01", status="scheduled")],
                     register_project=False, strict=False, llm_grade=False)
    assert "tf-e-01" in r.healed
    assert _status("tf-e-01") == "backlog"


def test_writes_scalar_and_junction_deps():
    specs = [
        _spec("tf-f-01"),
        _spec("tf-f-02", depends_on_task_id="tf-f-01", depends_on=["tf-f-01"]),
    ]
    create_tasks("tf", specs, register_project=False, llm_grade=False)
    with get_connection() as conn:
        dep = conn.execute(
            "SELECT depends_on_task_id FROM kanban_tasks WHERE id=?", ("tf-f-02",)
        ).fetchone()
        junc = conn.execute(
            "SELECT depends_on_id FROM kanban_task_deps WHERE task_id=?", ("tf-f-02",)
        ).fetchall()
    assert dict(dep)["depends_on_task_id"] == "tf-f-01"
    assert [dict(j)["depends_on_id"] for j in junc] == ["tf-f-01"]


def test_dry_run_writes_nothing():
    r = create_tasks("tf", [_spec("tf-g-01")], dry_run=True,
                     register_project=False, llm_grade=False)
    assert r.dry_run is True
    assert _status("tf-g-01") is None


def test_project_id_defaults_to_project_key():
    create_tasks("tf", [_spec("tf-h-01")], register_project=False, llm_grade=False)
    with get_connection() as conn:
        row = conn.execute("SELECT project_id FROM kanban_tasks WHERE id=?", ("tf-h-01",)).fetchone()
    assert dict(row)["project_id"] == "tf"
