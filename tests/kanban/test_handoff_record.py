#!/usr/bin/env python3
"""A run that did not finish leaves EVIDENCE, not one truncated clause. CUI // SP-CTI

xrv-run-02. ``_get_retry_coaching`` prepended ``failure_count`` plus a
500-char ``last_failure_reason`` and nothing else, so every retry re-derived
what the previous attempt had already learned: which files it had written,
what its pytest run said, whether it had already opened a PR.

The three things these tests pin, and each is a thing that would fail SILENTLY:

1. The record lands in the EXISTING ``last_run_metadata`` column, schema 1,
   with ``None`` -- never ``[]`` or ``0`` -- for every field whose source could
   not be read. An unreadable worktree that reported ``files_touched: []``
   would read as an agent that wrote nothing, which is a different repair.
2. The next dispatch's prompt actually CONTAINS the rendered block. A record
   written and never read is the declared-but-never-consumed defect, and
   nothing about the write path can see it.
3. A task with NO record renders exactly what it renders today. Additive or
   it is a regression to every retry on the board.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from icdev.tools.db.storage import get_connection  # noqa: E402
from icdev.tools.kanban import handoff_record as hr  # noqa: E402

_TASK = "xrvrun02-fixture"

_VALIDATION = {
    "codelens_passed": False,
    "ruff_issues": 7,
    "bandit_issues": 0,
    "coherence_passed": True,
    "e2e_ran": False,
    "e2e_passed": None,
    "modified_files": 3,
    "modified_py": 2,
}

# Columns match tools/db/schema/pg_consolidated.sql — the schema-drift census
# scans test DDL too, and rightly: a fixture table that disagrees with the
# schema of record proves the code against a shape production never has.
_DDL = """
CREATE TABLE kanban_tasks (
    id                  TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT DEFAULT '',
    task_type           TEXT DEFAULT 'build' NOT NULL,
    status              TEXT DEFAULT 'backlog',
    priority            TEXT DEFAULT 'high',
    failure_count       INTEGER DEFAULT 0,
    last_failure_reason TEXT,
    last_failure_at     TEXT,
    last_run_summary    TEXT,
    last_run_metadata   TEXT,
    executor_url        TEXT,
    depends_on_task_id  TEXT,
    updated_at          TEXT,
    PRIMARY KEY (id)
)
"""


@pytest.fixture()
def board(tmp_path):
    """A throwaway board holding one in-flight task.

    tmp_path, never the ambient database: a bare ``get_connection()`` in a test
    writes the real checkout's board.
    """
    db = tmp_path / "board.db"
    conn = get_connection(db_path=str(db))
    try:
        conn.execute(_DDL)
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, failure_count) "
            "VALUES (%s, %s, %s, %s)",
            (_TASK, "fixture task", "in_progress", 0),
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _stored(db) -> str:
    conn = get_connection(db_path=str(db))
    try:
        row = conn.execute(
            "SELECT last_run_metadata FROM kanban_tasks WHERE id = %s", (_TASK,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row)["last_run_metadata"]


# --------------------------------------------------------------------------- #
# 1. the record, and the null rule
# --------------------------------------------------------------------------- #

def test_unreadable_validation_is_null_and_a_run_that_had_one_carries_it(board, tmp_path):
    """``validation`` is None when none was captured, and the dict when one was.

    Both directions in one test on purpose: an assertion that only showed the
    None case would also pass for an implementation that never carried
    validation at all.
    """
    gone = tmp_path / "no-such-worktree"

    without = hr.record_run(
        _TASK, trigger=hr.TRIGGER_TIMEOUT, attempt=1,
        reason="TIMEOUT after 902s (max 900s)",
        validation=None, work_dir=str(gone), db_path=str(board),
    )
    assert without is not None
    assert without["schema"] == hr.SCHEMA_VERSION
    assert without["validation"] is None

    with_metrics = hr.record_run(
        _TASK, trigger=hr.TRIGGER_FAILURE, attempt=2,
        reason="VALIDATION FAILED: ruff reported 7 issues",
        validation=_VALIDATION, work_dir=str(gone), db_path=str(board),
    )
    assert with_metrics is not None
    assert with_metrics["validation"] == _VALIDATION
    assert with_metrics["blockers"], "a measured codelens failure is a blocker"


def test_every_unreadable_field_is_null_and_never_an_empty_list(board, tmp_path):
    """An unreadable worktree is None, not [] — they send you to different fixes."""
    record = hr.record_run(
        _TASK, trigger=hr.TRIGGER_TIMEOUT, attempt=1, reason="TIMEOUT after 902s",
        work_dir=str(tmp_path / "gone"), db_path=str(board),
    )
    assert record["files_touched"] is None
    assert record["commits"] is None
    assert record["files_touched_total"] is None
    assert record["commits_total"] is None
    assert record["tests_run"] is None
    assert record["cost_usd"] is None, "no attributed row is not a free run"


def test_the_record_is_persisted_into_the_existing_column(board, tmp_path):
    """No migration: it lands in ``kanban_tasks.last_run_metadata``."""
    hr.record_run(
        _TASK, trigger=hr.TRIGGER_TOKEN_EXHAUSTED, attempt=3,
        reason="token exhaustion: retry 3/60", work_dir=str(tmp_path / "gone"),
        db_path=str(board),
    )
    stored = json.loads(_stored(board))
    assert stored["schema"] == hr.SCHEMA_VERSION
    assert stored["task_id"] == _TASK
    assert stored["trigger"] == hr.TRIGGER_TOKEN_EXHAUSTED
    assert hr.load_record(_TASK, db_path=str(board))["attempt"] == 3


def test_a_real_worktree_reports_the_files_it_touched(board, tmp_path):
    """The positive control: a readable worktree yields a LIST, not None.

    Without it, an implementation whose git calls always failed would pass
    every null assertion above.
    """
    import subprocess  # noqa: PLC0415

    wt = tmp_path / "wt"
    wt.mkdir()
    env_args = dict(cwd=str(wt), capture_output=True, text=True, timeout=60)
    subprocess.run(["git", "init", "-q"], **env_args)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.email", "t@t"], **env_args)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.name", "t"], **env_args)  # noqa: S603,S607
    (wt / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], **env_args)  # noqa: S603,S607
    subprocess.run(["git", "commit", "-qm", "seed"], **env_args)  # noqa: S603,S607
    # STAGED, and never committed — the shape a bare `git diff --name-only`
    # reports as an empty list, i.e. as an agent that wrote nothing.
    (wt / "staged_by_the_attempt.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], **env_args)  # noqa: S603,S607
    # UNTRACKED, never added — the commonest shape of an interrupted run.
    (wt / "untracked_by_the_attempt.py").write_text("y = 2\n", encoding="utf-8")
    # MODIFIED in place.
    (wt / "seed.txt").write_text("seed edited\n", encoding="utf-8")

    record = hr.record_run(
        _TASK, trigger=hr.TRIGGER_FAILURE, attempt=1, reason="ruff failed",
        work_dir=str(wt), base_ref="HEAD", db_path=str(board),
    )
    assert record["files_touched"] == [
        "seed.txt", "staged_by_the_attempt.py", "untracked_by_the_attempt.py",
    ]
    assert record["files_touched_total"] == 3
    assert record["commits"] == [], "no commit beyond the base is a MEASURED zero"
    assert record["commits_total"] == 0


def test_the_rendered_block_keeps_absence_and_emptiness_apart():
    """"could not be read" and "changed no files" are different sentences."""
    unreadable = {"schema": 1, "attempt": 1, "trigger": "failure",
                  "files_touched": None, "commits": None}
    empty = {"schema": 1, "attempt": 1, "trigger": "failure",
             "files_touched": [], "commits": []}
    assert "could not be read" in hr.render_record(unreadable)
    assert "changed no files" in hr.render_record(empty)
    assert "changed no files" not in hr.render_record(unreadable)


def test_the_block_is_bounded_and_names_what_it_dropped():
    """A trimmed list that says nothing reads as a shorter attempt than it was."""
    from icdev.tools.llm.context_budget import estimate_tokens  # noqa: PLC0415

    record = {
        "schema": 1, "attempt": 2, "trigger": "failure",
        "failure_class": "ruff_lint", "reason": "r" * 600,
        "files_touched": [f"tools/module_{i}.py" for i in range(hr._MAX_FILES)],
        "files_touched_total": 500,
        "commits": [f"abc{i} a commit subject line" for i in range(hr._MAX_COMMITS)],
        "commits_total": 90,
        "blockers": [f"blocker {i}" for i in range(hr._MAX_BLOCKERS)],
    }
    block = hr.render_record(record)
    assert estimate_tokens(block) <= hr.DEFAULT_RENDER_TOKEN_BUDGET
    assert "and 460 more" in block, "the build-time trim must be counted too"
    assert "and 70 more" in block


def test_the_sections_follow_handoff_generator_order():
    """The two records read alike — same order, so a reader learns one shape."""
    block = hr.render_record({"schema": 1, "attempt": 1, "trigger": "failure"})
    positions = [block.index(h) for h in
                 ("## Current Position", "## Priority Actions", "## Blockers")]
    assert positions == sorted(positions)
    # NOT "## Recent Decisions": a file an agent touched is not a decision it
    # recorded, and borrowing that title would claim a rationale it never gave.
    assert "## Evidence from the last attempt" in block
    assert "## Recent Decisions" not in block


def test_load_record_refuses_anything_that_is_not_one_of_ours():
    """An agent's own handoff POST writes the same column. It is not a record."""
    assert hr.load_record("x", raw="") is None
    assert hr.load_record("x", raw="not json") is None
    assert hr.load_record("x", raw=json.dumps({"summary": "agent said so"})) is None
    assert hr.load_record("x", raw=json.dumps({"schema": 99})) is None
    assert hr.load_record("x", raw=json.dumps({"schema": 1, "attempt": 4}))["attempt"] == 4


# --------------------------------------------------------------------------- #
# 2. + 3. the scheduler wiring
# --------------------------------------------------------------------------- #

@pytest.fixture()
def wired(board, tmp_path, monkeypatch):
    """The scheduler, pointed at the throwaway board and a dead worktree."""
    from tools.genesis.reflexes import kanban as k  # noqa: PLC0415

    def _conn(*_args, **_kwargs):
        return get_connection(db_path=str(board))

    monkeypatch.setattr(k, "get_connection", _conn)
    monkeypatch.setattr(hr, "get_connection", _conn)
    monkeypatch.setattr(k, "_work_dir_for", lambda _tid: str(tmp_path / "gone"))
    monkeypatch.setattr(k, "_default_base_ref", lambda: "origin/main")
    k._LAST_VALIDATION.pop(_TASK, None)
    return k


def test_a_failed_run_leaves_a_record_carrying_the_validation_that_ran(wired, board):
    """The failure path writes the record, with the metrics the run already had."""
    wired._LAST_VALIDATION[_TASK] = dict(_VALIDATION)
    wired._record_failure_and_maybe_flag(_TASK, "VALIDATION FAILED: ruff 7 issues")

    record = hr.load_record(_TASK, db_path=str(board))
    assert record is not None, "the failure path must leave a record"
    assert record["schema"] == 1
    assert record["attempt"] == 1
    assert record["trigger"] == "failure"
    assert record["validation"] == _VALIDATION
    # The captured metrics are consumed, so attempt N+1 cannot report N's.
    assert _TASK not in wired._LAST_VALIDATION


def test_the_next_prompt_contains_the_rendered_handoff(wired, board):
    """A record nothing reads is the declared-but-never-consumed defect."""
    wired._LAST_VALIDATION[_TASK] = dict(_VALIDATION)
    wired._record_failure_and_maybe_flag(_TASK, "VALIDATION FAILED: ruff 7 issues")

    coaching = wired._get_retry_coaching(_TASK)
    assert "## Current Position" in coaching
    assert "## Evidence from the last attempt" in coaching
    assert "RETRY ATTEMPT #2" in coaching, "the existing preamble is unchanged"


def test_the_composed_dispatch_instruction_carries_the_block(wired, board):
    """The PROMPT, not just the helper.

    ``_build_instruction`` is what an executor actually receives, and it is
    where a block could be dropped between being rendered and being sent —
    a gap no assertion on ``_get_retry_coaching`` alone can see.
    """
    wired._LAST_VALIDATION[_TASK] = dict(_VALIDATION)
    wired._record_failure_and_maybe_flag(_TASK, "VALIDATION FAILED: ruff 7 issues")

    instruction = wired._build_instruction(
        _TASK, "fixture task", "Do the thing.", "/tmp/prompt.md")
    assert "## Current Position" in instruction
    assert "## Evidence from the last attempt" in instruction
    assert "Do the thing." in instruction, "the task's own prompt is still there"


def test_a_task_with_no_record_gets_todays_coaching_unchanged(wired, board):
    """Additive, or it is a regression to every retry on the board."""
    conn = get_connection(db_path=str(board))
    try:
        conn.execute(
            "UPDATE kanban_tasks SET failure_count = 1, last_failure_reason = %s, "
            "last_run_metadata = NULL WHERE id = %s",
            ("ruff reported 3 issues", _TASK),
        )
        conn.commit()
    finally:
        conn.close()

    coaching = wired._get_retry_coaching(_TASK)
    assert coaching, "a task with a failure still gets coaching"
    assert "## Current Position" not in coaching
    assert "## Evidence from the last attempt" not in coaching
    assert "RETRY ATTEMPT #2" in coaching


def test_a_first_run_gets_no_coaching_and_no_block(wired, board):
    """No failure, no record, nothing prepended."""
    assert wired._get_retry_coaching(_TASK) == ""


def test_the_handoff_block_never_raises_into_a_dispatch(wired, monkeypatch):
    """An unreadable board must not stop a task being dispatched."""
    def _boom(*_a, **_kw):
        raise RuntimeError("board unreachable")

    monkeypatch.setattr(hr, "load_record", _boom)
    assert wired._handoff_block(_TASK) == ""
