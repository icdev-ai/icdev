# CUI // SP-CTI
"""Canonical task seeder — never use raw INSERT directly.

Usage:
    from tools.kanban.task_factory import create_tasks

    created = create_tasks([{
        "id": "my-task-01",
        "title": "Do the thing",
        "description": "...",
        "task_type": "build",          # optional, default 'build'
        "priority": "medium",          # optional, default 'high'
        "status": "backlog",           # optional, default 'backlog'
        "depends_on_task_id": None,    # optional
        "source_doc_id": "abc123",     # optional — DIC document source
        "source_collection_id": "c1", # optional — DIC collection source
    }])
    # returns list of IDs that were actually inserted (skips duplicates)
"""
from __future__ import annotations

import os
import sqlite3
from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


class BoardBackendError(RuntimeError):
    """Raised when a board write would land somewhere that is not the board."""


#: Mirrors the live CHECK constraint ``kanban_tasks_task_type_check``.
#:
#: Note there is no ``bug`` — use ``fix``. SQLite does NOT enforce CHECK
#: constraints, so an illegal value seeds cleanly against a fallback database and
#: only aborts part-way through the insert loop on PostgreSQL, rolling the whole
#: batch back. Validating in Python means the one backend that would not tell you
#: no longer has to.
VALID_TASK_TYPES = frozenset(
    {"build", "run", "fix", "research", "deploy", "test", "chore"}
)

#: Escape hatch for tests and genuinely fresh installs, which legitimately seed
#: an empty local board.
_ALLOW_LOCAL_BOARD_ENV = "ICDEV_KANBAN_ALLOW_LOCAL_BOARD"


def _assert_real_board(conn) -> None:
    """Refuse to write the board when the connection is a local SQLite fallback.

    ``.env`` is gitignored, so a git worktree has no PostgreSQL config and
    ``get_connection()`` silently falls back to SQLite at
    ``<worktree>/data/icdev.db``. On 2026-08-08 a seeder run that way reported
    "36/36 created" against a database that was then deleted with the worktree —
    the PR merged and the board had nothing on it. A write that goes nowhere must
    fail loudly rather than succeed.

    Set ``ICDEV_KANBAN_ALLOW_LOCAL_BOARD=1`` when a local board is what you mean.
    """
    if os.environ.get(_ALLOW_LOCAL_BOARD_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return
    backend = getattr(conn, "backend", None) or getattr(conn, "_backend", None)
    is_sqlite = isinstance(conn, sqlite3.Connection) or (
        isinstance(backend, str) and "sqlite" in backend.lower()
    )
    if not is_sqlite:
        return
    raise BoardBackendError(
        "refusing to write kanban tasks to a SQLite fallback database. This is "
        "almost always a seeder run from a git worktree, where .env is absent so "
        "get_connection() fell back to a local file that dies with the worktree. "
        "Copy .env into the worktree, or set "
        f"{_ALLOW_LOCAL_BOARD_ENV}=1 if a local board is genuinely what you want."
    )


def create_tasks(task_specs: list[dict]) -> list[str]:
    """Insert tasks that don't already exist. Returns list of inserted IDs.

    Raises ``ValueError`` for a ``task_type`` the DB forbids, and
    ``BoardBackendError`` when the write would land in a throwaway local
    database — both BEFORE anything is inserted, so a batch never half-lands.
    """
    if not task_specs:
        return []

    bad_types = sorted({
        str(t.get("task_type"))
        for t in task_specs
        if t.get("task_type") is not None
        and t.get("task_type") not in VALID_TASK_TYPES
    })
    if bad_types:
        raise ValueError(
            f"task_type {', '.join(repr(b) for b in bad_types)} violates "
            f"kanban_tasks_task_type_check; allowed: {sorted(VALID_TASK_TYPES)}. "
            "(There is no 'bug' — use 'fix'.)"
        )

    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables
    from tools.kanban import policy_drift

    init_kanban_tables()
    now = datetime.now(timezone.utc).isoformat()

    # kax-merge-02: stamp the card's operating policy as a DELIMITED block
    # rather than letting each seeder paste its own copy. A pasted copy is
    # frozen at seed time — correcting the card then leaves every existing row
    # saying the old thing, which is how 35 hgx rows went on telling sessions to
    # open --draft after the card said not to. A block is re-synced against
    # `policy:` in args/projects.yaml by tools/kanban/policy_drift.py.
    # Loaded ONCE per batch, and a no-op for the ~156 cards with no `policy:`.
    # load_exemptions (not load_rules) so a malformed rule can never take down
    # every seeder — the seeder needs the exemption veto, not the rules.
    _projects = policy_drift.load_projects()
    _ruleset = policy_drift.load_exemptions()

    created: list[str] = []
    conn = get_connection()
    _assert_real_board(conn)
    try:
        for t in task_specs:
            task_id = str(t.get("id") or "").strip()
            if not task_id:
                logger.warning("task_factory: skipping task with no id: %s", t.get("title"))
                continue

            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            if existing:
                logger.debug("task_factory: skip existing task %s", task_id)
                continue

            # Idempotency key check — deduplicates webhook/automation retries
            idem_key = t.get("idempotency_key") or None
            if idem_key:
                idem_exists = conn.execute(
                    "SELECT id FROM kanban_tasks WHERE idempotency_key = %s", (idem_key,)
                ).fetchone()
                if idem_exists:
                    logger.debug(
                        "task_factory: skip duplicate idempotency_key=%s (task %s)",
                        idem_key, task_id,
                    )
                    continue

            max_retries = int(t.get("max_retries") or 5)
            max_runtime_seconds = t.get("max_runtime_seconds")
            if max_runtime_seconds is not None:
                max_runtime_seconds = int(max_runtime_seconds)

            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    depends_on_task_id, source_prediction_id,
                    source_doc_id, source_collection_id,
                    dispatch_source, idempotency_key, max_retries,
                    max_runtime_seconds, loop_type, adversarial_enabled,
                    acceptance_criteria,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    task_id,
                    str(t.get("title", "Untitled task"))[:255],
                    policy_drift.apply_policy_block(
                        t.get("description") or "", task_id, _projects, _ruleset
                    ),
                    t.get("task_type", "build"),
                    t.get("priority", "high"),
                    t.get("status", "backlog"),
                    t.get("depends_on_task_id"),
                    t.get("source_prediction_id"),
                    t.get("source_doc_id"),
                    t.get("source_collection_id"),
                    t.get("dispatch_source", "dic_notebook"),
                    idem_key,
                    max_retries,
                    max_runtime_seconds,
                    t.get("loop_type", "deterministic"),
                    1 if t.get("adversarial_enabled") else 0,
                    # Persisted so the dispatcher can put it in the prompt.
                    # Without this the column stayed empty on every seeded task
                    # (0 of 2427 populated), which left review_conformance
                    # unable to judge and the agent with no machine-checkable
                    # definition of done.
                    t.get("acceptance_criteria"),
                    now,
                    now,
                ),
            )
            created.append(task_id)

        conn.commit()
    except Exception as exc:
        logger.error("task_factory: commit failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    logger.info("task_factory: inserted %d / %d tasks", len(created), len(task_specs))
    return created
