# CUI // SP-CTI
"""Native task dependency support — migration 015 + listener gating + API shape.

Covers the five moving parts of depends_on_task_id:

  * migration 015 idempotency + column / index shape
  * listener _get_due_tasks excludes blocked tasks, surfaces them once done
  * dashboard kanban API create with dependency + rejection of cycles/self-ref
  * dashboard kanban API list returns is_blocked + dependency metadata
  * chained integration test A → B → C unblocks progressively
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


from tools.db.storage import get_connection as _real_get_connection

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Migration 015 — column + index shape
# ---------------------------------------------------------------------------


class TestMigration015:
    def _legacy_kanban(self, path: Path) -> sqlite3.Connection:
        """Create a kanban_tasks table WITHOUT depends_on_task_id (pre-015)."""
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE kanban_tasks (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                description  TEXT,
                task_type    TEXT DEFAULT 'chore',
                priority     TEXT DEFAULT 'low',
                status       TEXT DEFAULT 'backlog',
                scheduled_at TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        return conn

    def test_up_adds_column_and_index(self, tmp_path):
        from icdev.tools.db.migrations import __init__  # noqa: F401 — ensure pkg importable
        import importlib.util

        mig = PROJECT_ROOT / "tools" / "db" / "migrations" / "015_kanban_depends_on" / "up.py"
        spec = importlib.util.spec_from_file_location("mig015_up", str(mig))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        conn = self._legacy_kanban(tmp_path / "icdev.db")
        result = mod.up(conn)
        assert result["status"] == "applied"
        assert "column_added" in result["actions"]
        assert "index_ensured" in result["actions"]

        cols = {r[1] for r in conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()}
        assert "depends_on_task_id" in cols

        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='kanban_tasks'"
            ).fetchall()
        }
        assert "idx_kanban_depends" in indexes
        conn.close()

    def test_up_is_idempotent(self, tmp_path):
        import importlib.util

        mig = PROJECT_ROOT / "tools" / "db" / "migrations" / "015_kanban_depends_on" / "up.py"
        spec = importlib.util.spec_from_file_location("mig015_up", str(mig))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        conn = self._legacy_kanban(tmp_path / "icdev.db")
        mod.up(conn)
        second = mod.up(conn)
        assert second["status"] == "applied"
        assert "column_exists" in second["actions"]
        conn.close()

    def test_up_skips_when_table_missing(self, tmp_path):
        import importlib.util

        mig = PROJECT_ROOT / "tools" / "db" / "migrations" / "015_kanban_depends_on" / "up.py"
        spec = importlib.util.spec_from_file_location("mig015_up", str(mig))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        conn = sqlite3.connect(str(tmp_path / "empty.db"))
        result = mod.up(conn)
        assert result["status"] == "skipped"
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard kanban API — create with dependency + cycle detection
# ---------------------------------------------------------------------------


@pytest.fixture
def kanban_flask_client(tmp_path, monkeypatch):
    """Spin up a minimal Flask app with the kanban blueprint + a fresh sqlite DB."""
    from flask import Flask

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id                 TEXT PRIMARY KEY,
            title              TEXT NOT NULL,
            description        TEXT,
            task_type          TEXT DEFAULT 'build',
            priority           TEXT DEFAULT 'medium',
            status             TEXT DEFAULT 'backlog',
            scheduled_at       TEXT,
            created_at         TEXT,
            updated_at         TEXT,
            completed_at       TEXT,
            executor_type      TEXT,
            execution_id       TEXT,
            executor_url       TEXT,
            source_prediction_id TEXT,
            depends_on_task_id TEXT,
            start_date         TEXT,
            target_date        TEXT,
            completed_via_bypass INTEGER DEFAULT 0,
            dispatch_source    TEXT DEFAULT 'unknown',
            failure_count      INTEGER DEFAULT 0,
            last_failure_reason TEXT,
            last_failure_at    TEXT,
            hitl_stage         TEXT,
            source_doc_id      TEXT,
            source_collection_id TEXT
        );
        CREATE TABLE oracle_predictions (
            id              TEXT PRIMARY KEY,
            confidence      REAL,
            prediction_text TEXT,
            lens_name       TEXT,
            prediction_type TEXT
        );
        CREATE TABLE kanban_task_deps (
            task_id       TEXT NOT NULL,
            depends_on_id TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (task_id, depends_on_id)
        );
        CREATE TABLE kanban_verifications (
            id           TEXT PRIMARY KEY,
            task_id      TEXT NOT NULL,
            verified_at  TEXT NOT NULL,
            result       TEXT NOT NULL,
            phantom_ratio REAL DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

    # Patch get_connection inside the kanban module to point at our temp DB.
    def _fake_conn():
        # Go through tools.db.storage, NOT raw sqlite3. Runtime SQL is authored for
        # PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper translates
        # them to SQLite's ?. A raw sqlite3 connection makes every %s a syntax error.
        # _real_get_connection is bound at import time, so patching storage's attribute
        # below cannot recurse back into this.
        return _real_get_connection(db_path=str(db_path))

    from icdev.tools.dashboard.api import kanban as kanban_mod

    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    # SSE is best-effort; stub to a no-op so tests don't need the real manager.
    class _StubSSE:
        def broadcast(self, *args, **kwargs):
            pass

    monkeypatch.setattr(kanban_mod, "sse_manager", _StubSSE())

    app = Flask(__name__)
    app.register_blueprint(kanban_mod.kanban_api)
    client = app.test_client()
    return client, db_path


class TestKanbanApiDependency:
    def test_create_without_dependency_is_backward_compatible(self, kanban_flask_client):
        client, _ = kanban_flask_client
        r = client.post("/api/kanban/tasks", json={"title": "Standalone"})
        assert r.status_code == 201
        assert r.get_json()["status"] == "created"

    def test_create_with_valid_dependency(self, kanban_flask_client):
        client, _ = kanban_flask_client
        a = client.post("/api/kanban/tasks", json={"title": "A"}).get_json()["id"]
        b = client.post(
            "/api/kanban/tasks",
            json={"title": "B", "depends_on_task_id": a},
        )
        assert b.status_code == 201

    def test_create_rejects_unknown_dependency(self, kanban_flask_client):
        client, _ = kanban_flask_client
        r = client.post(
            "/api/kanban/tasks",
            json={"title": "Orphan", "depends_on_task_id": "task-doesnotexist"},
        )
        assert r.status_code == 400
        assert "not found" in r.get_json()["error"].lower()

    def test_create_rejects_two_hop_cycle(self, kanban_flask_client):
        client, db_path = kanban_flask_client
        # Manually craft A with depends_on_task_id pointing at a future B so that
        # POSTing B depends_on A would form a 2-hop cycle.
        a = client.post("/api/kanban/tasks", json={"title": "A"}).get_json()["id"]
        b = client.post(
            "/api/kanban/tasks", json={"title": "B", "depends_on_task_id": a}
        ).get_json()["id"]
        # Now try to create C that depends on A, then flip A's depends to point at C
        # via PATCH — the PATCH should reject because A depending on C, and C depending
        # on nothing initially is fine, but we simulate a cycle by patching A to depend
        # on B which already depends on A.
        r = client.patch(
            f"/api/kanban/tasks/{a}",
            json={"depends_on_task_id": b},
        )
        assert r.status_code == 400
        assert "cycle" in r.get_json()["error"].lower()

    def test_create_rejects_self_reference(self, kanban_flask_client):
        # Self-reference is only reachable via PATCH because create_task generates
        # a fresh id — the creator can't know it in advance.
        client, _ = kanban_flask_client
        a = client.post("/api/kanban/tasks", json={"title": "A"}).get_json()["id"]
        r = client.patch(f"/api/kanban/tasks/{a}", json={"depends_on_task_id": a})
        assert r.status_code == 400
        assert "itself" in r.get_json()["error"].lower()

    def test_list_surfaces_is_blocked(self, kanban_flask_client):
        client, _ = kanban_flask_client
        a = client.post("/api/kanban/tasks", json={"title": "A"}).get_json()["id"]
        client.post(
            "/api/kanban/tasks",
            json={"title": "B", "depends_on_task_id": a},
        )
        r = client.get("/api/kanban/tasks")
        tasks = {t["title"]: t for t in r.get_json()["tasks"]}
        assert tasks["A"]["is_blocked"] is False
        assert tasks["B"]["is_blocked"] is True
        assert tasks["B"]["depends_on_task_id"] == a
        assert tasks["B"]["depends_on_title"] == "A"

    def test_list_unblocks_when_parent_done(self, kanban_flask_client):
        client, _ = kanban_flask_client
        a = client.post("/api/kanban/tasks", json={"title": "A"}).get_json()["id"]
        b = client.post(
            "/api/kanban/tasks", json={"title": "B", "depends_on_task_id": a}
        ).get_json()["id"]
        # Mark A done via the move endpoint (guard-22: bypass verification
        # gate for this unit-test fixture; we're exercising dependency
        # unblocking, not the verification pipeline).
        client.post(
            f"/api/kanban/tasks/{a}/move",
            json={
                "status": "done",
                "bypass_verification": True,
                "bypass_reason": "unit test: test_list_unblocks_when_parent_done",
            },
        )
        tasks = {t["id"]: t for t in client.get("/api/kanban/tasks").get_json()["tasks"]}
        assert tasks[b]["is_blocked"] is False
        assert tasks[b]["depends_on_status"] == "done"


# ---------------------------------------------------------------------------
# Listener _get_due_tasks — native gating
# ---------------------------------------------------------------------------


class TestListenerDependencyGating:
    @pytest.fixture
    def seeded_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "icdev.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE kanban_tasks (
                id                   TEXT PRIMARY KEY,
                title                TEXT NOT NULL,
                description          TEXT,
                task_type            TEXT DEFAULT 'build',
                priority             TEXT DEFAULT 'medium',
                status               TEXT DEFAULT 'backlog',
                scheduled_at         TEXT,
                created_at           TEXT,
                updated_at           TEXT,
                completed_at         TEXT,
                executor_type        TEXT,
                execution_id         TEXT,
                executor_url         TEXT,
                source_prediction_id TEXT,
                depends_on_task_id   TEXT,
                failure_count        INTEGER DEFAULT 0,
                last_failure_reason  TEXT,
                last_failure_at      TEXT,
                dispatch_source      TEXT DEFAULT 'unknown'
            );
            CREATE TABLE kanban_task_deps (
                task_id       TEXT NOT NULL,
                depends_on_id TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (task_id, depends_on_id)
            );
            """
        )
        # Seed a chain A → B → C, all in backlog, updated > 10 min ago to bypass
        # the cooldown filter.
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, priority, status, created_at, updated_at, depends_on_task_id) "
            "VALUES ('A', 'A', 'high', 'backlog', ?, ?, NULL)",
            (long_ago, long_ago),
        )
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, priority, status, created_at, updated_at, depends_on_task_id) "
            "VALUES ('B', 'B', 'high', 'backlog', ?, ?, 'A')",
            (long_ago, long_ago),
        )
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, priority, status, created_at, updated_at, depends_on_task_id) "
            "VALUES ('C', 'C', 'high', 'backlog', ?, ?, 'B')",
            (long_ago, long_ago),
        )
        conn.commit()

        # Patch the listener's get_connection, in-progress count, and pending prompts.
        from icdev.tools.genesis.reflexes import kanban as listener

        def _fake_conn():
            # Storage wrapper, not raw sqlite3 — see the note in the fixture above.
            return _real_get_connection(db_path=str(db_path))

        monkeypatch.setattr(listener, "get_connection", _fake_conn)
        monkeypatch.setattr(listener, "_count_in_progress", lambda: 0)
        monkeypatch.setattr(listener, "_count_pending_prompts", lambda: 0)

        yield db_path, conn
        conn.close()

    def test_only_unblocked_task_returned(self, seeded_db):
        from icdev.tools.genesis.reflexes.kanban import _get_due_tasks

        due = _get_due_tasks()
        ids = [t["id"] for t in due]
        assert "A" in ids
        assert "B" not in ids
        assert "C" not in ids

    def test_chain_progression(self, seeded_db):
        from icdev.tools.genesis.reflexes.kanban import _get_due_tasks

        db_path, _ = seeded_db

        # Mark A done
        c = sqlite3.connect(str(db_path))
        c.execute("UPDATE kanban_tasks SET status='done' WHERE id='A'")
        c.commit()
        c.close()

        due = _get_due_tasks()
        ids = [t["id"] for t in due]
        assert "B" in ids
        assert "C" not in ids

        # Mark B done
        c = sqlite3.connect(str(db_path))
        c.execute("UPDATE kanban_tasks SET status='done' WHERE id='B'")
        c.commit()
        c.close()

        due = _get_due_tasks()
        ids = [t["id"] for t in due]
        assert "C" in ids

    def test_scheduled_task_also_gated(self, seeded_db):
        """A scheduled-and-due task with a blocked dependency must not be returned."""
        from icdev.tools.genesis.reflexes.kanban import _get_due_tasks

        db_path, _ = seeded_db
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        c = sqlite3.connect(str(db_path))
        c.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, priority, status, scheduled_at, created_at, updated_at, "
            " depends_on_task_id) "
            "VALUES ('D', 'D', 'high', 'scheduled', ?, ?, ?, 'C')",
            (past, past, past),
        )
        c.commit()
        c.close()

        due = _get_due_tasks()
        ids = [t["id"] for t in due]
        assert "D" not in ids  # D is blocked by C which is still backlog


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
