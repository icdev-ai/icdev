# CUI // SP-CTI
"""Tests for auto_resolver ↔ heartbeat_daemon integration (acw-heal-01).

Acceptance: stale ACE instance detected by heartbeat → resolve_component()
→ state=cancelled within a single synchronous call.
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_db() -> tuple[sqlite3.Connection, Path]:
    """Create a temporary SQLite DB with the minimal required tables."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ace_instances (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL DEFAULT '',
            role_id     TEXT NOT NULL DEFAULT '',
            state       TEXT NOT NULL DEFAULT 'pending',
            trust_tier  TEXT NOT NULL DEFAULT 'yellow',
            config_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ace_trust_ledger (
            id              TEXT PRIMARY KEY,
            role_id         TEXT NOT NULL,
            delta           REAL NOT NULL,
            reason          TEXT NOT NULL,
            new_score       REAL NOT NULL,
            source_task_id  TEXT NOT NULL DEFAULT '',
            recorded_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS heartbeat_checks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type  TEXT    NOT NULL,
            last_run    TEXT    NOT NULL,
            next_run    TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            result_summary TEXT,
            items_found INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _seed_stale_instance(db_path: Path, instance_id: str, role_id: str) -> None:
    """Insert an ACE instance that appears stale (updated_at 2 hours ago)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
        "VALUES (?, 'Test Instance', ?, 'active', datetime('now', '-2 hours'))",
        (instance_id, role_id),
    )
    conn.commit()
    conn.close()


def _seed_trust_ledger(db_path: Path, role_id: str, trust_score: float) -> None:
    """Seed ace_trust_ledger with a known score for role_id."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
        "VALUES (?, ?, 0.0, 'seed', ?)",
        (f"trust-test-{role_id[:8]}", role_id, trust_score),
    )
    conn.commit()
    conn.close()


def _get_instance_state(db_path: Path, instance_id: str) -> str:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT state FROM ace_instances WHERE id=?", (instance_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else "NOT_FOUND"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db(tmp_path):
    """Create a fresh SQLite DB for each test."""
    db_path = tmp_path / "test_heartbeat.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ace_instances (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            role_id TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'pending',
            trust_tier TEXT NOT NULL DEFAULT 'yellow',
            config_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ace_trust_ledger (
            id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL,
            delta REAL NOT NULL,
            reason TEXT NOT NULL,
            new_score REAL NOT NULL,
            source_task_id TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS heartbeat_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type TEXT NOT NULL,
            last_run TEXT NOT NULL,
            next_run TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_summary TEXT,
            items_found INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Patch get_connection to use our test DB
# ---------------------------------------------------------------------------

def _patch_get_connection(db_path: Path):
    """Return a patcher that routes get_connection to the test SQLite file."""

    def _fake_get_connection(db_path=None, **kwargs):
        # Go through tools.db.storage, NOT raw sqlite3. Runtime SQL is authored for
        # PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
        # translates them to SQLite's ?. A raw connection makes every %s a syntax
        # error, which the heartbeat checks swallow into a bogus "ok".
        from tools.db.storage import get_connection as _real_get_connection

        return _real_get_connection(db_path=str(db_path))

    return _fake_get_connection


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveComponent:
    """Unit tests for auto_resolver.resolve_component."""

    def test_trusted_tier_cancels_stale_instance(self, test_db):
        """Stale ACE instance with trusted/autonomous role → state set to cancelled."""
        instance_id = "inst-test-001"
        role_id = "role-trusted-01"

        # Seed stale instance
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES (?, 'Stale Instance', ?, 'active', datetime('now', '-2 hours'))",
            (instance_id, role_id),
        )
        # Seed autonomous trust score (≥ 0.8)
        conn.execute(
            "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
            "VALUES ('trust-t01', ?, 0.0, 'seed', 0.85)",
            (role_id,),
        )
        conn.commit()
        conn.close()

        # Patch get_connection to use the test DB
        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.auto_resolver._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor.auto_resolver import resolve_component
            result = resolve_component(instance_id, "ace_instance_stale", db_path=test_db)

        assert result["status"] == "resolved", f"Expected resolved, got {result}"
        assert result["trust_tier"] in ("trusted", "autonomous")

        # Verify state was updated to cancelled
        state = _get_instance_state(test_db, instance_id)
        assert state == "cancelled", f"Expected cancelled, got {state}"

    def test_supervised_tier_creates_hitl_escalation(self, test_db):
        """Supervised role → HITL escalation (no auto-restart)."""
        instance_id = "inst-test-002"
        role_id = "role-supervised-01"

        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES (?, 'Supervised Instance', ?, 'active', datetime('now', '-2 hours'))",
            (instance_id, role_id),
        )
        # Supervised trust score (0.3–0.6)
        conn.execute(
            "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
            "VALUES ('trust-s01', ?, 0.0, 'seed', 0.45)",
            (role_id,),
        )
        conn.commit()
        conn.close()

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.auto_resolver._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor import auto_resolver
            # Force reimport to pick up patched connection
            result = auto_resolver.resolve_component(instance_id, "ace_instance_stale", db_path=test_db)

        assert result["status"] == "escalated"
        assert result["trust_tier"] in ("supervised", "probationary")

        # State should NOT be cancelled for supervised instances
        state = _get_instance_state(test_db, instance_id)
        assert state == "active", f"Supervised instance should remain active, got {state}"

    def test_unknown_component_defaults_to_supervised(self, test_db):
        """Component with no trust history → supervised (safe default)."""
        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.auto_resolver._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor import auto_resolver
            result = auto_resolver.resolve_component("no-such-component", "ace_instance_stale", db_path=test_db)

        assert result["trust_tier"] in ("supervised", "probationary")
        assert result["status"] == "escalated"

    def test_backoff_respected(self, test_db):
        """Second call within backoff window returns backoff status."""
        instance_id = "inst-test-003"
        role_id = "role-trusted-02"

        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES (?, 'Backoff Instance', ?, 'active', datetime('now', '-3 hours'))",
            (instance_id, role_id),
        )
        conn.execute(
            "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
            "VALUES ('trust-b01', ?, 0.0, 'seed', 0.9)",
            (role_id,),
        )
        conn.commit()
        conn.close()

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.auto_resolver._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor import auto_resolver
            # First call resolves
            r1 = auto_resolver.resolve_component(instance_id, "ace_instance_stale", db_path=test_db)
            assert r1["status"] == "resolved"
            # Second immediate call should hit backoff
            r2 = auto_resolver.resolve_component(instance_id, "ace_instance_stale", db_path=test_db)
            assert r2["status"] == "backoff"


class TestHeartbeatChecks:
    """Unit tests for the 3 new heartbeat check functions."""

    def test_check_ace_instance_stale_detects_stale(self, test_db):
        """check_ace_instance_stale returns critical + items for stale active instances."""
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES ('stale-inst-1', 'Old Instance', 'role-x', 'active', datetime('now', '-60 minutes'))"
        )
        conn.commit()
        conn.close()

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.heartbeat_daemon._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor.heartbeat_daemon import check_ace_instance_stale
            result = check_ace_instance_stale(
                config={"stale_threshold_minutes": 30},
                db_path=test_db,
            )

        assert result["status"] == "critical"
        assert result["count"] >= 1
        assert any(i.get("result") == "dead" for i in result["items"])

    def test_check_ace_instance_stale_ok_when_fresh(self, test_db):
        """check_ace_instance_stale returns ok when no stale instances."""
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES ('fresh-inst-1', 'New Instance', 'role-y', 'active', datetime('now', '-5 minutes'))"
        )
        conn.commit()
        conn.close()

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.heartbeat_daemon._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor.heartbeat_daemon import check_ace_instance_stale
            result = check_ace_instance_stale(
                config={"stale_threshold_minutes": 30},
                db_path=test_db,
            )

        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_kanban_stale(self, test_db):
        """check_kanban_stale detects in_progress tasks stuck for too long."""
        conn = sqlite3.connect(str(test_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kanban_tasks (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                updated_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, updated_at) "
            "VALUES ('stale-task-1', 'Old Task', 'in_progress', datetime('now', '-6 hours'))"
        )
        conn.commit()
        conn.close()

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with patch("tools.monitor.heartbeat_daemon._get_connection", side_effect=lambda p=None: _fake_conn(p)):
            from tools.monitor.heartbeat_daemon import check_kanban_stale
            result = check_kanban_stale(
                config={"stale_threshold_hours": 4},
                db_path=test_db,
            )

        assert result["status"] == "warning"
        assert result["count"] >= 1

    def test_check_a2a_agent_health(self, test_db):
        """check_a2a_agent_health returns critical when agents are unreachable."""
        fake_agents = [{"id": "agent-001", "url": "http://127.0.0.1:19999"}]

        import urllib.error

        def _raise_url_error(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        with (
            patch(
                "tools.monitor.heartbeat_daemon._get_connection",
                side_effect=lambda p=None: __import__("sqlite3").connect(str(test_db)),
            ),
            patch(
                "tools.a2a.agent_registry.discover_agents",
                return_value=fake_agents,
                create=True,
            ),
            patch("urllib.request.urlopen", side_effect=_raise_url_error),
        ):
            # Reload to pick up fresh imports
            import importlib
            import tools.monitor.heartbeat_daemon as hb_mod
            importlib.reload(hb_mod)
            result = hb_mod.check_a2a_agent_health(
                config={"dead_alert_minutes": 0, "request_timeout_seconds": 1},
                db_path=test_db,
            )

        assert result["count"] >= 1
        assert any(i.get("result") == "dead" for i in result["items"])


class TestHeartbeatAutoResolverIntegration:
    """Integration: run_single_check triggers resolve_component for target check types."""

    def test_run_single_check_ace_instance_stale_triggers_resolver(self, test_db):
        """run_single_check('ace_instance_stale') → auto_resolver called for dead items."""
        instance_id = "inst-integration-01"
        role_id = "role-int-01"

        conn = sqlite3.connect(str(test_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeat_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_type TEXT NOT NULL,
                last_run TEXT NOT NULL,
                next_run TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_summary TEXT,
                items_found INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, updated_at) "
            "VALUES (?, 'Int Instance', ?, 'active', datetime('now', '-2 hours'))",
            (instance_id, role_id),
        )
        conn.execute(
            "INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score) "
            "VALUES ('trust-int-01', ?, 0.0, 'seed', 0.9)",
            (role_id,),
        )
        conn.commit()
        conn.close()

        resolved_calls = []

        def _mock_resolve(component_id, failure_type, db_path=None):
            resolved_calls.append({"component_id": component_id, "failure_type": failure_type})

        def _fake_conn(db_path=None, **kw):
            # Go through tools.db.storage, NOT raw sqlite3: runtime SQL is authored
            # for PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper
            # translates them to SQLite's ?. A raw connection makes every %s a
            # `near "%": syntax error` inside auto_resolver.
            from tools.db.storage import get_connection as _real_get_connection

            return _real_get_connection(db_path=str(test_db))

        with (
            patch("tools.monitor.heartbeat_daemon._get_connection", side_effect=lambda p=None: _fake_conn(p)),
            patch("tools.monitor.heartbeat_daemon._notify"),
            patch("tools.monitor.auto_resolver.resolve_component", side_effect=_mock_resolve),
            patch(
                "tools.monitor.heartbeat_daemon._trigger_auto_resolver",
                side_effect=lambda ct, r, dp=None: _mock_resolve(
                    instance_id, ct
                ) if r.get("count", 0) > 0 else None,
            ),
        ):
            from tools.monitor import heartbeat_daemon
            result = heartbeat_daemon.run_single_check(
                "ace_instance_stale",
                config={"checks": {"ace_instance_stale": {"stale_threshold_minutes": 30}}},
                db_path=test_db,
            )

        assert result["status"] == "critical"
        assert len(resolved_calls) >= 1
        assert resolved_calls[0]["failure_type"] == "ace_instance_stale"
