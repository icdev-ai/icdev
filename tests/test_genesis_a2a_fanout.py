# CUI // SP-CTI
"""Tests for A2A fan-out in tools/genesis/daemon.py.

Acceptance criteria (acw-a2a-01):
  1. Reflexes tagged a2a_eligible:true run via A2A submit_task (not inline).
  2. submit_task is NOT called when a2a.enabled is false.
  3. submit_task is NOT called for reflexes without a2a_eligible:true.
  4. A2A dispatch failure returns a failure tuple (does not fall back to inline).
  5. A successful dispatch records a row in agent_a2a_tasks.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daemon(extra_config: Dict[str, Any] = None):
    """Return a GenesisDaemon with a minimal in-memory config."""
    from tools.genesis.daemon import GenesisDaemon

    cfg = {
        "enabled": True,
        "trust_mode": "full",
        "trust_kernel": {
            "circuit_breaker": {"max_consecutive_failures": 3},
            "risk_tiers": {
                "green": {"approval": "auto", "sandbox": False},
                "yellow": {"approval": "auto", "sandbox": True},
                "orange": {"approval": "human", "sandbox": True},
            },
        },
        "defaults": {"reflex_timeout_seconds": 300, "stub_loc_min": 10, "stub_loc_full": 15},
        "a2a": {"enabled": False, "gateway_url": "https://localhost:8443"},
    }
    if extra_config:
        cfg.update(extra_config)
    return GenesisDaemon(cfg)


def _make_trust(daemon):
    return daemon.trust  # already created from full config in DaemonBase.__init__


# ---------------------------------------------------------------------------
# _submit_reflex_a2a — unit tests with mocked A2AAgentClient
# ---------------------------------------------------------------------------

class TestSubmitReflexA2A:
    """Direct tests of _submit_reflex_a2a via a mocked A2AAgentClient."""

    def _run(self, submit_return, daemon=None, reflex_cfg=None):
        """Call _submit_reflex_a2a with a mocked client and return the result."""
        d = daemon or _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = reflex_cfg or {"risk_tier": "green", "a2a_eligible": True}

        mock_client = MagicMock()
        mock_client.submit_task.return_value = submit_return

        # Patch DB write so no real SQLite needed
        with patch.object(d, "_record_a2a_task") as mock_record, \
             patch("tools.genesis.daemon.A2AAgentClient", return_value=mock_client):
            result = d._submit_reflex_a2a("research", cfg)
        return result, mock_client, mock_record

    def test_success_calls_submit_task(self):
        result, client, _ = self._run({"id": "task-abc123", "status": "submitted"})
        client.submit_task.assert_called_once()
        call_kwargs = client.submit_task.call_args
        assert call_kwargs.kwargs["skill_id"] == "research"
        assert "reflex_name" in call_kwargs.kwargs["input_data"]

    def test_success_returns_a2a_submitted_status(self):
        result, _, _ = self._run({"id": "task-abc123", "status": "submitted"})
        ok, metric, details = result
        assert ok is True
        assert details["status"] == "a2a_submitted"
        assert details["task_id"] == "task-abc123"

    def test_success_records_a2a_task(self):
        _, _, mock_record = self._run({"id": "task-abc123", "status": "submitted"})
        mock_record.assert_called_once()
        call_args = mock_record.call_args
        assert call_args.kwargs.get("status") == "submitted" or call_args.args[4] == "submitted"

    def test_failure_returns_false_tuple(self):
        d = _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = {"risk_tier": "green", "a2a_eligible": True}

        mock_client = MagicMock()
        mock_client.submit_task.side_effect = ConnectionError("agent unreachable")

        with patch.object(d, "_record_a2a_task"), \
             patch("tools.genesis.daemon.A2AAgentClient", return_value=mock_client):
            ok, metric, details = d._submit_reflex_a2a("research", cfg)

        assert ok is False
        assert "a2a_dispatch" in details.get("stage", "")

    def test_failure_records_error_row(self):
        d = _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = {"risk_tier": "green", "a2a_eligible": True}

        mock_client = MagicMock()
        mock_client.submit_task.side_effect = RuntimeError("boom")

        with patch.object(d, "_record_a2a_task") as mock_record, \
             patch("tools.genesis.daemon.A2AAgentClient", return_value=mock_client):
            d._submit_reflex_a2a("research", cfg)

        call_args = mock_record.call_args
        # status="error" is either in kwargs or positional arg index 4
        status = call_args.kwargs.get("status") or call_args.args[4]
        assert status == "error"


# ---------------------------------------------------------------------------
# _run_reflex_impl_inner — routing logic
# ---------------------------------------------------------------------------

class TestRunReflexImplInnerRouting:
    """Verify the A2A gate in _run_reflex_impl_inner routes correctly."""

    def _inner(self, daemon, name, reflex_cfg):
        trust = _make_trust(daemon)
        with patch.object(daemon, "_submit_reflex_a2a") as mock_a2a, \
             patch("importlib.import_module", side_effect=ImportError):
            daemon._run_reflex_impl_inner(name, reflex_cfg, trust)
        return mock_a2a

    def test_eligible_reflex_dispatched_via_a2a_when_enabled(self):
        d = _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = {"risk_tier": "green", "a2a_eligible": True}
        trust = _make_trust(d)

        with patch.object(d, "_submit_reflex_a2a", return_value=(True, 0.0, {})) as mock_a2a:
            d._run_reflex_impl_inner("research", cfg, trust)

        mock_a2a.assert_called_once_with("research", cfg)

    def test_a2a_disabled_skips_a2a_dispatch(self):
        d = _make_daemon({"a2a": {"enabled": False}})
        cfg = {"risk_tier": "green", "a2a_eligible": True}
        trust = _make_trust(d)

        with patch.object(d, "_submit_reflex_a2a") as mock_a2a, \
             patch("importlib.import_module", side_effect=ImportError):
            d._run_reflex_impl_inner("research", cfg, trust)

        mock_a2a.assert_not_called()

    def test_non_eligible_reflex_skips_a2a_dispatch(self):
        d = _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = {"risk_tier": "green"}  # no a2a_eligible key
        trust = _make_trust(d)

        with patch.object(d, "_submit_reflex_a2a") as mock_a2a, \
             patch("importlib.import_module", side_effect=ImportError):
            d._run_reflex_impl_inner("heal", cfg, trust)

        mock_a2a.assert_not_called()

    def test_orange_tier_skips_a2a_and_returns_awaiting(self):
        d = _make_daemon({"a2a": {"enabled": True, "gateway_url": "https://localhost:8443"}})
        cfg = {"risk_tier": "orange", "a2a_eligible": True}
        trust = _make_trust(d)

        with patch.object(d, "_submit_reflex_a2a", return_value=(True, 0.0, {})) as mock_a2a:
            ok, _, details = d._run_reflex_impl_inner("evolve", cfg, trust)

        # ORANGE tier human-approval gate fires before A2A check
        mock_a2a.assert_not_called()
        assert details.get("status") == "awaiting_human_approval"


# ---------------------------------------------------------------------------
# _record_a2a_task — DB write
# ---------------------------------------------------------------------------

class TestRecordA2ATask:
    """Verify _record_a2a_task inserts a row into agent_a2a_tasks."""

    def test_inserts_row_into_agent_a2a_tasks(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_a2a_tasks (
                id TEXT PRIMARY KEY, reflex_name TEXT NOT NULL,
                skill_id TEXT NOT NULL, agent_url TEXT NOT NULL,
                task_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'submitted',
                input_data TEXT, result TEXT, error TEXT,
                submitted_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        import os
        os.environ["ICDEV_DB_PATH"] = str(db_path)
        os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

        d = _make_daemon()

        with patch("tools.genesis.daemon.get_connection") as mock_conn_factory:
            # Translating wrapper — the daemon authors %s for PostgreSQL, and
            # its insert sits inside a best-effort except, so a bare connection
            # silently wrote nothing and this read as a missing feature.
            from _sql_compat import connect as _tconnect

            inner_conn = _tconnect(db_path, row_factory=False)
            mock_conn_factory.return_value = inner_conn

            d._record_a2a_task(
                reflex_name="research",
                agent_url="https://localhost:8443",
                task_id="task-xyz",
                input_data={"reflex_name": "research"},
                status="submitted",
            )

        conn2 = sqlite3.connect(str(db_path))
        rows = conn2.execute("SELECT * FROM agent_a2a_tasks").fetchall()
        conn2.close()
        assert len(rows) == 1
        assert rows[0][1] == "research"   # reflex_name
        assert rows[0][5] == "submitted"  # status

    def test_error_row_stores_error_message(self, tmp_path):
        db_path = tmp_path / "test2.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_a2a_tasks (
                id TEXT PRIMARY KEY, reflex_name TEXT NOT NULL,
                skill_id TEXT NOT NULL, agent_url TEXT NOT NULL,
                task_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'submitted',
                input_data TEXT, result TEXT, error TEXT,
                submitted_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        d = _make_daemon()

        with patch("tools.genesis.daemon.get_connection") as mock_conn_factory:
            # Translating wrapper — the daemon authors %s for PostgreSQL, and
            # its insert sits inside a best-effort except, so a bare connection
            # silently wrote nothing and this read as a missing feature.
            from _sql_compat import connect as _tconnect

            inner_conn = _tconnect(db_path, row_factory=False)
            mock_conn_factory.return_value = inner_conn

            d._record_a2a_task(
                reflex_name="scout",
                agent_url="https://localhost:8443",
                task_id="",
                input_data={},
                status="error",
                error="agent unreachable",
            )

        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute("SELECT * FROM agent_a2a_tasks").fetchone()
        conn2.close()
        assert row[5] == "error"
        assert "unreachable" in (row[8] or "")
