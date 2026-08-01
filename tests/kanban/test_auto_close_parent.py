# CUI // SP-CTI
"""Tests for the decomposed-parent auto-close and decomposer
placeholder-filter fixes (2026-04-19 follow-ups).

Regression targets:
  1. _auto_close_decomposed_parent — when the last open child completes,
     the parent row flips from 'decomposed' to 'done' with a bypass
     verification audit row.
  2. _decompose_batch_tasks — lines like "All subjects share the same
     rule. A single fix may resolve all." and "Source prediction IDs:
     ..." are stripped from the Subjects block (no orphan child spawn).

Isolation (opx-kan-01):
  These tests used to mutate the LIVE ``data/icdev.db`` via the production
  ``get_connection()`` (5 call sites), which fails with ``no such table``
  in a fresh worktree / CI (order-dependent green) and strands
  ``task-test-*`` rows on the real board when teardown is skipped (``-x`` /
  interrupt). They now run entirely against the ``icdev_db`` temp-DB fixture
  (``tests/conftest.py`` — ``MINIMAL_ICDEV_SCHEMA`` already ships
  ``kanban_tasks`` / ``kanban_verifications`` / ``kanban_status_transitions``).

  The code under test (``tools.genesis.reflexes.kanban``) calls the
  module-level ``get_connection`` for its own connections, so we SHIM-AWARE
  monkeypatch that name on the module the code actually imports (``tools.*``
  and ``icdev.tools.*`` resolve to DISTINCT module objects). The reflex SQL
  is ``%s``-authored, so a bare ``sqlite3.Connection`` would fail — each
  connection is wrapped in ``StorageConnection(raw, "sqlite")`` which
  translates ``%s`` -> ``?``. A guard fixture asserts the repo-root
  ``data/icdev.db`` is never opened or modified.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Module under test — the code calls its OWN module-level get_connection.
_KANBAN_MODULE_NAMES = (
    "tools.genesis.reflexes.kanban",
    "icdev.tools.genesis.reflexes.kanban",
)

# The real board file the tests must NEVER open or mutate.
_REPO_ROOT_DB = ROOT / "data" / "icdev.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stat_signature(path: Path):
    """(exists, size, mtime_ns) snapshot used to prove the file is untouched."""
    try:
        st = path.stat()
        return (True, st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return (False, None, None)


@pytest.fixture
def kanban_conn_factory(icdev_db, monkeypatch):
    """Patch the kanban reflex's ``get_connection`` onto the temp ``icdev_db``.

    Yields a callable that returns a fresh ``StorageConnection`` bound to the
    same temp DB (the code closes its connections, and several helpers open
    their own, so every call must hand back a live connection to one file).
    Also guards that the repo-root ``data/icdev.db`` is never touched.
    """
    from tools.db.storage import StorageConnection

    db_path = Path(icdev_db)
    # Sanity: the fixture DB must be a temp file, never the live board.
    assert db_path.resolve() != _REPO_ROOT_DB.resolve()
    tmp_root = str(db_path.parent).lower()
    assert "temp" in tmp_root or "tmp" in tmp_root or "pytest" in tmp_root

    opened_paths: list[str] = []

    def _factory(*_a, **_kw):
        opened_paths.append(str(db_path))
        raw = sqlite3.connect(str(db_path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    # SHIM-AWARE: patch the name on whichever kanban module objects exist.
    # The reflex bound ``get_connection`` at module load (top-level import), so
    # patching the storage module alone would not rebind it here.
    patched = False
    for mod_name in _KANBAN_MODULE_NAMES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _factory, raising=False)
        patched = True
    assert patched, "could not import any kanban reflex module to patch"

    # Also patch the storage module itself so transitive LAZY importers pulled
    # in by the code under test (e.g. tools.workflow.lesson_learned does
    # ``from tools.db.storage import get_connection`` inside the function) never
    # fall through to the real board and create/mutate repo-root data/icdev.db.
    for mod_name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _factory, raising=False)

    before = _stat_signature(_REPO_ROOT_DB)
    yield _factory
    after = _stat_signature(_REPO_ROOT_DB)

    # Guard: the code under test must not have opened anything but the temp DB,
    # and the live board file must be byte-for-byte unchanged.
    assert all(p == str(db_path) for p in opened_paths), opened_paths
    assert before == after, (
        f"repo-root data/icdev.db was modified during the test: {before} -> {after}"
    )


@pytest.fixture
def ephemeral_parent_and_children(kanban_conn_factory):
    """Insert one decomposed parent + two open children into the temp DB."""
    sp = f"op-test-auto-close-{uuid.uuid4().hex[:10]}"
    parent_id = f"task-test-parent-{uuid.uuid4().hex[:8]}"
    child_a = f"task-test-child-a-{uuid.uuid4().hex[:6]}"
    child_b = f"task-test-child-b-{uuid.uuid4().hex[:6]}"
    now = _now()

    conn = kanban_conn_factory()
    for tid, title, status in [
        (parent_id, "[Batch] test_rule: 2 gap findings to address", "decomposed"),
        (child_a, "test_rule gap: subject-a", "backlog"),
        (child_b, "test_rule gap: subject-b", "backlog"),
    ]:
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
            "status, executor_type, source_prediction_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (tid, title, "test fixture", "chore", "low", status, "claude_cli", sp, now, now),
        )
    conn.commit()
    conn.close()

    return {"sp": sp, "parent": parent_id, "child_a": child_a, "child_b": child_b}


def test_auto_close_waits_while_siblings_open(kanban_conn_factory, ephemeral_parent_and_children):
    """Parent must NOT close when some siblings are still open."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent

    ids = ephemeral_parent_and_children
    conn = kanban_conn_factory()
    # Mark only child_a done; child_b still backlog.
    conn.execute(
        "UPDATE kanban_tasks SET status='done', completed_at=%s, updated_at=%s WHERE id=%s",
        (_now(), _now(), ids["child_a"]),
    )
    conn.commit()
    conn.close()

    closed = _auto_close_decomposed_parent(ids["child_a"], actor="test")
    assert closed is None, "parent must stay decomposed while siblings are open"

    conn = kanban_conn_factory()
    row = conn.execute(
        "SELECT status FROM kanban_tasks WHERE id=%s", (ids["parent"],)
    ).fetchone()
    conn.close()
    assert dict(row)["status"] == "decomposed"


def test_auto_close_fires_when_last_child_completes(kanban_conn_factory, ephemeral_parent_and_children):
    """When the LAST open child completes, parent auto-closes to done."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent

    ids = ephemeral_parent_and_children
    conn = kanban_conn_factory()
    conn.execute("UPDATE kanban_tasks SET status='done', completed_at=%s, updated_at=%s WHERE id=%s",
                 (_now(), _now(), ids["child_a"]))
    conn.execute("UPDATE kanban_tasks SET status='done', completed_at=%s, updated_at=%s WHERE id=%s",
                 (_now(), _now(), ids["child_b"]))
    conn.commit()
    conn.close()

    closed = _auto_close_decomposed_parent(ids["child_b"], actor="test")
    assert closed == ids["parent"], f"expected parent {ids['parent']} closed, got {closed}"

    conn = kanban_conn_factory()
    row = conn.execute("SELECT status, completed_at FROM kanban_tasks WHERE id=%s",
                       (ids["parent"],)).fetchone()
    d = dict(row)
    assert d["status"] == "done"
    assert d["completed_at"] is not None

    # Bypass verification row must be present so guard-22 stays consistent.
    vrow = conn.execute(
        "SELECT result, reason FROM kanban_verifications WHERE task_id=%s "
        "ORDER BY verified_at DESC LIMIT 1",
        (ids["parent"],),
    ).fetchone()
    conn.close()
    assert vrow is not None
    v = dict(vrow)
    assert v["result"] == "bypassed"
    assert "auto_close" in v["reason"].lower() or "last child" in v["reason"].lower()


def test_auto_close_returns_none_for_non_child(kanban_conn_factory):
    """Tasks with no source_prediction_id must no-op safely."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent
    assert _auto_close_decomposed_parent("task-does-not-exist", actor="test") is None


def test_decomposer_skips_placeholder_subjects(kanban_conn_factory):
    """Meta-subjects like 'All subjects share the same rule' must be filtered."""
    from tools.genesis.reflexes.kanban import _decompose_batch_tasks

    sp = f"op-test-placeholder-{uuid.uuid4().hex[:10]}"
    parent_id = f"task-test-placeholder-{uuid.uuid4().hex[:8]}"
    now = _now()

    conn = kanban_conn_factory()
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, executor_type, source_prediction_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (parent_id, "[Batch] test_rule: placeholder-only subjects",
         "Subjects:\n- All subjects share the same rule. A single fix may resolve all.\n- Source prediction IDs: op-x, op-y\n- tools/real/one.py",
         "chore", "low", "backlog", "claude_cli", sp, now, now),
    )
    conn.commit()

    tasks_in = [{
        "id": parent_id,
        "title": "[Batch] test_rule: placeholder-only subjects",
        "description": ("Subjects:\n"
                        "- All subjects share the same rule. A single fix may resolve all.\n"
                        "- Source prediction IDs: op-x, op-y\n"
                        "- tools/real/one.py"),
        "priority": "low",
        "task_type": "chore",
        "source_prediction_id": sp,
    }]
    try:
        _decompose_batch_tasks(tasks_in, conn)
        # Only the real subject should have been materialized — 2 placeholder lines skipped.
        created = conn.execute(
            "SELECT id, title FROM kanban_tasks "
            "WHERE source_prediction_id = %s AND id <> %s",
            (sp, parent_id),
        ).fetchall()
        created_titles = [dict(r)["title"] for r in created]
        assert len(created) == 1, f"expected 1 child, got {len(created)}: {created_titles}"
        assert "tools/real/one.py" in created_titles[0]
        # No placeholder children
        for t in created_titles:
            assert "All subjects share" not in t
            assert "Source prediction IDs" not in t
    finally:
        conn.close()


def test_never_touches_repo_root_db(kanban_conn_factory):
    """Explicit guard: exercising the reflex uses only the temp DB; the
    fixture teardown asserts the live board path stays byte-for-byte
    unchanged and that no connection ever targeted it."""
    conn = kanban_conn_factory()
    conn.execute("SELECT COUNT(*) AS n FROM kanban_tasks").fetchone()
    conn.close()
# CUI // SP-CTI
