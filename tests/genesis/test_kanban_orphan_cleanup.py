# CUI // SP-CTI
"""Regression — orphan prompt-file cleaner must catch ALL naming conventions.

Bug (2026-04-29): _count_pending_prompts() used glob("task-*.md"), which
silently skipped prompt files named by their task ID directly (e.g.
"sg-import-ds-vault.md"). Those files accumulated indefinitely, blocking
backlog promotion, and were never cleaned up after the task completed.

Fix: broadened glob to "*.md" so any .md file in PROMPT_DIR is evaluated.
The stem is always used as the task ID regardless of any prefix.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.db.storage import get_connection as _storage_get_connection


_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'medium',
    status                TEXT DEFAULT 'backlog',
    scheduled_at          TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    completed_at          TEXT,
    executor_type         TEXT,
    execution_id          TEXT,
    executor_url          TEXT,
    source_prediction_id  TEXT,
    depends_on_task_id    TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    dispatch_source       TEXT,
    dispatch_attempt_id   TEXT
);
"""


@pytest.fixture
def orphan_ctx(tmp_path, monkeypatch):
    """Isolated DB + PROMPT_DIR + patched get_connection."""
    db_path = tmp_path / "k.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    prompt_dir = tmp_path / "kanban"
    prompt_dir.mkdir()

    def _fake_conn(*_a, **_kw):
        # Go through tools.db.storage, NOT raw sqlite3. Runtime SQL is authored for
        # PostgreSQL (%s placeholders, per CLAUDE.md); the storage wrapper translates
        # them to SQLite's ?. Handing the code under test a raw sqlite3 connection
        # makes every %s a `near "%": syntax error`.
        return _storage_get_connection(db_path=str(db_path))

    import tools.genesis.reflexes.kanban as km
    monkeypatch.setattr(km, "get_connection", _fake_conn)
    monkeypatch.setattr(km, "PROMPT_DIR", prompt_dir)

    return {"km": km, "db": db_path, "prompt_dir": prompt_dir}


def _insert_task(db_path, task_id, status):
    c = sqlite3.connect(str(db_path))
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
        (task_id, f"Title for {task_id}", status),
    )
    c.commit()
    c.close()


class TestOrphanCleanupGlob:
    """_count_pending_prompts must handle both task- prefixed and bare-ID filenames."""

    def test_task_prefixed_done_file_is_deleted(self, orphan_ctx):
        """task-abc.md whose task is 'done' must be deleted (existing behavior)."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]
        _insert_task(orphan_ctx["db"], "task-abc", "done")
        pf = prompt_dir / "task-abc.md"
        pf.write_text("# prompt")

        km._count_pending_prompts()

        assert not pf.exists(), "done task's prompt file must be cleaned up"

    def test_bare_task_id_done_file_is_deleted(self, orphan_ctx):
        """sg-import-ds-vault.md whose task is 'done' must be deleted (regression)."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]
        _insert_task(orphan_ctx["db"], "sg-import-ds-vault", "done")
        pf = prompt_dir / "sg-import-ds-vault.md"
        pf.write_text("# vault prompt")

        km._count_pending_prompts()

        assert not pf.exists(), (
            "bare task-ID prompt file for a 'done' task must be cleaned up; "
            "glob('task-*.md') would have missed this"
        )

    def test_bare_task_id_missing_from_db_is_deleted(self, orphan_ctx):
        """A prompt file with no DB row (task never existed or was purged) is orphaned."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]
        pf = prompt_dir / "sg-import-ds-aws.md"
        pf.write_text("# aws prompt")

        km._count_pending_prompts()

        assert not pf.exists(), (
            "prompt file with no matching DB task must be treated as orphaned and deleted"
        )

    def test_bare_task_id_in_progress_is_counted(self, orphan_ctx):
        """Prompt file for an in-progress task (bare name) is NOT deleted; counts toward limit."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]
        _insert_task(orphan_ctx["db"], "sg-import-ds-vault", "in_progress")
        pf = prompt_dir / "sg-import-ds-vault.md"
        pf.write_text("# vault prompt")

        count = km._count_pending_prompts()

        assert pf.exists(), "in-progress task prompt must NOT be deleted"
        assert count == 1

    def test_mixed_naming_only_done_deleted(self, orphan_ctx):
        """Mix of task- prefix and bare IDs — only done/missing ones are cleaned."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]

        _insert_task(orphan_ctx["db"], "task-active", "in_progress")
        _insert_task(orphan_ctx["db"], "sg-import-ds-vault", "done")
        _insert_task(orphan_ctx["db"], "sg-import-ds-aws", "done")

        (prompt_dir / "task-active.md").write_text("# active")
        (prompt_dir / "sg-import-ds-vault.md").write_text("# vault")
        (prompt_dir / "sg-import-ds-aws.md").write_text("# aws")

        count = km._count_pending_prompts()

        assert (prompt_dir / "task-active.md").exists(), "in_progress prompt must survive"
        assert not (prompt_dir / "sg-import-ds-vault.md").exists(), "done prompt must be deleted"
        assert not (prompt_dir / "sg-import-ds-aws.md").exists(), "done prompt must be deleted"
        assert count == 1

    def test_no_false_deletion_for_backlog_task(self, orphan_ctx):
        """Backlog/scheduled/token_exhausted tasks: prompt kept, not counted."""
        km = orphan_ctx["km"]
        prompt_dir = orphan_ctx["prompt_dir"]
        _insert_task(orphan_ctx["db"], "sg-pending-task", "backlog")
        pf = prompt_dir / "sg-pending-task.md"
        pf.write_text("# pending")

        count = km._count_pending_prompts()

        assert pf.exists(), "backlog task prompt must not be deleted"
        assert count == 0, "backlog task must not be counted toward in-progress cap"

    def test_empty_prompt_dir_returns_zero(self, orphan_ctx):
        """No prompt files → count is 0."""
        assert orphan_ctx["km"]._count_pending_prompts() == 0
