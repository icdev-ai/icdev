# CUI // SP-CTI
"""Tests for check_a2a_agent_health in tools/monitor/heartbeat_daemon.py.

Validates that the A2A endpoint health check:
- probes /.well-known/agent.json via HEAD request for each registered agent
- marks unreachable agents result='dead' and reachable ones result='alive'
- writes a heartbeat_checks row with check_type='a2a_agent_health'
- sends a Telegram alert when an agent has been dead for >= dead_alert_minutes
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"  # force sqlite so writes land in db_path, not PG

import tools.monitor.heartbeat_daemon as _hb_mod
from tools.monitor.heartbeat_daemon import (
    _build_default_a2a_agents,
    _load_config,
    check_a2a_agent_health,
    run_single_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_db(db_path: Path) -> None:
    """Create the minimal schema needed by the heartbeat daemon."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            capabilities TEXT,
            last_heartbeat TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _minutes_ago(n: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class _MockHTTPResp:
    """Minimal context-manager mock for urllib.request.urlopen."""

    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


_SAMPLE_AGENTS = [
    {"id": "orchestrator", "name": "Orchestrator", "url": "https://localhost:8443", "status": "active"},
    {"id": "architect",    "name": "Architect",    "url": "https://localhost:8444", "status": "active"},
]


# ---------------------------------------------------------------------------
# Fixtures: clear module-level dead-since state between tests
# ---------------------------------------------------------------------------

def setup_function():
    _hb_mod._a2a_dead_since.clear()


# ===================================================================
# TestBuildDefaultAgents
# ===================================================================

class TestBuildDefaultAgents:
    def test_returns_list(self):
        agents = _build_default_a2a_agents()
        assert isinstance(agents, list)
        assert len(agents) >= 1

    def test_covers_port_range(self):
        agents = _build_default_a2a_agents()
        ports = {int(a["url"].split(":")[-1]) for a in agents}
        assert 8443 in ports
        assert 8460 in ports

    def test_entries_have_required_keys(self):
        for a in _build_default_a2a_agents():
            assert "id" in a
            assert "name" in a
            assert "url" in a


# ===================================================================
# TestCheckA2AAgentHealthHTTP
# ===================================================================

class TestCheckA2AAgentHealthHTTP:
    def test_all_alive_returns_ok(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", return_value=_MockHTTPResp(200)):
                result = check_a2a_agent_health(config={}, db_path=db)

        assert result["status"] == "ok"
        assert result["count"] == 0
        assert result["items"] == []
        assert result["alive_count"] == 2

    def test_one_dead_returns_critical(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        call_count = [0]

        def _urlopen_side_effect(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("connection refused")
            return _MockHTTPResp(200)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", side_effect=_urlopen_side_effect):
                result = check_a2a_agent_health(config={}, db_path=db)

        assert result["status"] == "critical"
        assert result["count"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["result"] == "dead"
        assert item["agent_id"] == "orchestrator"

    def test_dead_item_has_required_fields(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = check_a2a_agent_health(config={}, db_path=db)

        assert result["count"] == 2
        for item in result["items"]:
            assert "agent_id" in item
            assert "name" in item
            assert "url" in item
            assert item["result"] == "dead"
            assert "dead_since" in item

    def test_all_dead_status_critical(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = check_a2a_agent_health(config={}, db_path=db)

        assert result["status"] == "critical"
        assert result["count"] == 2

    def test_alive_agent_removed_from_dead_since(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        # Pre-mark orchestrator as dead
        _hb_mod._a2a_dead_since["orchestrator"] = _minutes_ago(5)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", return_value=_MockHTTPResp(200)):
                check_a2a_agent_health(config={}, db_path=db)

        # Should be cleared since it's now alive
        assert "orchestrator" not in _hb_mod._a2a_dead_since

    def test_fallback_to_default_agents_when_registry_empty(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=[]):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                result = check_a2a_agent_health(config={}, db_path=db)

        # Should have probed the default agent list (18 entries)
        assert result["count"] == 18

    def test_registry_unavailable_returns_ok(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", side_effect=Exception("DB error")):
            result = check_a2a_agent_health(config={}, db_path=db)

        assert result["status"] == "ok"
        assert result["count"] == 0
        assert "note" in result

    def test_returns_expected_keys(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=[]):
            with patch("urllib.request.urlopen", return_value=_MockHTTPResp(200)):
                result = check_a2a_agent_health(config={}, db_path=db)

        assert "status" in result
        assert "count" in result
        assert "items" in result


# ===================================================================
# TestHeartbeatChecksTableRow
# ===================================================================

class TestHeartbeatChecksTableRow:
    def test_run_single_check_writes_row(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", return_value=_MockHTTPResp(200)):
                run_single_check("a2a_agent_health", config=_load_config(), db_path=db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM heartbeat_checks WHERE check_type = 'a2a_agent_health'"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        row = dict(rows[0])
        assert row["check_type"] == "a2a_agent_health"
        assert row["status"] in ("ok", "warning", "critical", "error")

    def test_dead_agent_recorded_as_critical(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                run_single_check("a2a_agent_health", config=_load_config(), db_path=db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM heartbeat_checks WHERE check_type = 'a2a_agent_health'"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1
        assert dict(rows[0])["status"] == "critical"

    def test_result_summary_contains_dead_agents(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS[:1]):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                run_single_check("a2a_agent_health", config=_load_config(), db_path=db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_summary FROM heartbeat_checks WHERE check_type = 'a2a_agent_health'"
        ).fetchone()
        conn.close()

        summary = json.loads(row["result_summary"])
        assert isinstance(summary, list)
        assert len(summary) >= 1
        assert summary[0]["result"] == "dead"


# ===================================================================
# TestTelegramAlertThreshold
# ===================================================================

class TestTelegramAlertThreshold:
    def test_no_alert_below_threshold(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        # Agent just became dead (0 minutes ago)
        _hb_mod._a2a_dead_since["orchestrator"] = _utcnow()

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS[:1]):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                with patch("tools.notifications.adapters.telegram.send") as mock_send:
                    check_a2a_agent_health(
                        config={"dead_alert_minutes": 10, "request_timeout_seconds": 1},
                        db_path=db,
                    )

        mock_send.assert_not_called()

    def test_alert_sent_after_threshold(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        # Orchestrator has been dead for 11 minutes
        _hb_mod._a2a_dead_since["orchestrator"] = _minutes_ago(11)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS[:1]):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                with patch("tools.notifications.adapters.telegram.send") as mock_send:
                    check_a2a_agent_health(
                        config={"dead_alert_minutes": 10, "request_timeout_seconds": 1},
                        db_path=db,
                    )

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["severity"] == "critical" or call_kwargs[0][2] == "critical"

    def test_alert_contains_agent_name(self, tmp_path):
        db = tmp_path / "icdev.db"
        _init_db(db)

        _hb_mod._a2a_dead_since["orchestrator"] = _minutes_ago(15)

        with patch("tools.a2a.agent_registry.discover_agents", return_value=_SAMPLE_AGENTS[:1]):
            with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                with patch("tools.notifications.adapters.telegram.send") as mock_send:
                    check_a2a_agent_health(
                        config={"dead_alert_minutes": 10, "request_timeout_seconds": 1},
                        db_path=db,
                    )

        assert mock_send.called
        body_arg = mock_send.call_args[1].get("body", "") or mock_send.call_args[0][1]
        assert "Orchestrator" in body_arg
