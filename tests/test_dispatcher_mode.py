#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Dispatcher-Only Orchestrator Mode (Phase 61, D-DISP-1).

Covers:
  - Config loading (defaults + YAML)
  - Tool allowed/blocked logic
  - Tool filtering
  - Per-project overrides (enable/disable/custom tools)
  - Redirect agent mapping
  - Status report
  - CLI output (JSON + human)
  - Integration with team_orchestrator mock
  - Integration with skill_router mock
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.agent.dispatcher_mode import (
    _ensure_table,
    _load_dispatcher_config,
    disable_for_project,
    enable_for_project,
    filter_tools_for_dispatcher,
    get_blocked_tools,
    get_dispatch_tools,
    get_redirect_agent,
    get_status,
    is_dispatcher_mode,
    is_tool_allowed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def dispatcher_db(tmp_path):
    """Temporary database with dispatcher_mode_overrides table."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispatcher_mode_overrides (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            custom_dispatch_tools TEXT DEFAULT '[]',
            custom_blocked_tools TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by TEXT NOT NULL DEFAULT 'system'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispatcher_mode_project
        ON dispatcher_mode_overrides(project_id)
    """)
    # Also create audit_trail so _audit doesn't fail
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            session_id TEXT,
            source_ip TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def enabled_config():
    """Dispatcher mode config dict with enabled=True."""
    return {
        "enabled": True,
        "dispatch_only_tools": [
            "task_dispatch", "agent_status", "agent_mailbox",
            "workflow_status", "prompt_chain_execute",
        ],
        "blocked_when_dispatching": [
            "scaffold", "generate_code", "write_tests", "run_tests",
            "lint", "format", "ssp_generate", "poam_generate",
            "stig_check", "sbom_generate", "terraform_plan",
            "terraform_apply", "ansible_run", "k8s_deploy",
        ],
    }


@pytest.fixture
def disabled_config():
    """Dispatcher mode config dict with enabled=False."""
    return {
        "enabled": False,
        "dispatch_only_tools": [
            "task_dispatch", "agent_status", "agent_mailbox",
            "workflow_status", "prompt_chain_execute",
        ],
        "blocked_when_dispatching": [
            "scaffold", "generate_code", "write_tests", "run_tests",
            "lint", "format", "ssp_generate", "poam_generate",
            "stig_check", "sbom_generate", "terraform_plan",
            "terraform_apply", "ansible_run", "k8s_deploy",
        ],
    }


@pytest.fixture
def yaml_config_file(tmp_path):
    """Create a temporary agent_config.yaml with dispatcher_mode enabled."""
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed")

    config = {
        "agents": {
            "orchestrator": {
                "name": "Orchestrator Agent",
                "port": 8443,
                "dispatcher_mode": {
                    "enabled": True,
                    "dispatch_only_tools": [
                        "task_dispatch", "agent_status", "agent_mailbox",
                        "workflow_status", "prompt_chain_execute",
                    ],
                    "blocked_when_dispatching": [
                        "scaffold", "generate_code", "write_tests",
                        "run_tests", "lint", "format",
                        "ssp_generate", "poam_generate",
                        "stig_check", "sbom_generate",
                        "terraform_plan", "terraform_apply",
                        "ansible_run", "k8s_deploy",
                    ],
                },
            },
        },
    }

    config_path = tmp_path / "agent_config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------
class TestConfigLoading:
    """Tests for _load_dispatcher_config."""

    def test_defaults_when_no_config_file(self):
        """Returns defaults when agent_config.yaml does not exist."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent/config.yaml")):
            config = _load_dispatcher_config()

        assert config["enabled"] is False
        assert "task_dispatch" in config["dispatch_only_tools"]
        assert "scaffold" in config["blocked_when_dispatching"]
        assert len(config["dispatch_only_tools"]) == 5
        assert len(config["blocked_when_dispatching"]) == 14

    def test_defaults_when_yaml_missing(self):
        """Returns defaults when PyYAML is not importable."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent/config.yaml")):
            config = _load_dispatcher_config()

        assert isinstance(config, dict)
        assert "enabled" in config
        assert "dispatch_only_tools" in config
        assert "blocked_when_dispatching" in config

    def test_loads_yaml_config(self, yaml_config_file):
        """Loads configuration from YAML file."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            config = _load_dispatcher_config()

        assert config["enabled"] is True
        assert "task_dispatch" in config["dispatch_only_tools"]
        assert "scaffold" in config["blocked_when_dispatching"]

    def test_defaults_when_yaml_malformed(self, tmp_path):
        """Returns defaults when YAML is malformed."""
        bad_yaml = tmp_path / "agent_config.yaml"
        bad_yaml.write_text(": invalid: [yaml: {broken")
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", bad_yaml):
            config = _load_dispatcher_config()

        assert config["enabled"] is False

    def test_defaults_when_no_dispatcher_mode_section(self, tmp_path):
        """Returns defaults when orchestrator has no dispatcher_mode section."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        config = {"agents": {"orchestrator": {"name": "Orchestrator Agent", "port": 8443}}}
        config_path = tmp_path / "agent_config.yaml"
        config_path.write_text(yaml.dump(config))

        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", config_path):
            result = _load_dispatcher_config()

        assert result["enabled"] is False


# ---------------------------------------------------------------------------
# is_dispatcher_mode tests
# ---------------------------------------------------------------------------
class TestIsDispatcherMode:
    """Tests for is_dispatcher_mode."""

    def test_disabled_by_default(self, dispatcher_db):
        """Dispatcher mode is disabled by default (no config override)."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            assert is_dispatcher_mode(db_path=dispatcher_db) is False

    def test_enabled_via_global_config(self, yaml_config_file, dispatcher_db):
        """Dispatcher mode enabled via global config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_dispatcher_mode(db_path=dispatcher_db) is True

    def test_per_project_override_enabled(self, dispatcher_db):
        """Per-project override enables dispatcher mode even when global is off."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project("proj-test", created_by="admin", db_path=dispatcher_db)
            assert is_dispatcher_mode(project_id="proj-test", db_path=dispatcher_db) is True

    def test_per_project_override_disabled(self, yaml_config_file, dispatcher_db):
        """Per-project override can disable dispatcher mode for a specific project."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            enable_for_project("proj-test", created_by="admin", db_path=dispatcher_db)
            disable_for_project("proj-test", disabled_by="admin", db_path=dispatcher_db)
            assert is_dispatcher_mode(project_id="proj-test", db_path=dispatcher_db) is False

    def test_no_override_falls_back_to_global(self, yaml_config_file, dispatcher_db):
        """Without a per-project override, falls back to global config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_dispatcher_mode(project_id="proj-no-override", db_path=dispatcher_db) is True


# ---------------------------------------------------------------------------
# is_tool_allowed tests
# ---------------------------------------------------------------------------
class TestIsToolAllowed:
    """Tests for is_tool_allowed."""

    def test_all_tools_allowed_when_disabled(self, dispatcher_db):
        """All tools allowed when dispatcher mode is disabled."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            assert is_tool_allowed("scaffold", db_path=dispatcher_db) is True
            assert is_tool_allowed("task_dispatch", db_path=dispatcher_db) is True
            assert is_tool_allowed("random_tool", db_path=dispatcher_db) is True

    def test_dispatch_tools_allowed_when_enabled(self, yaml_config_file, dispatcher_db):
        """Dispatch-only tools are allowed in dispatcher mode."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_tool_allowed("task_dispatch", db_path=dispatcher_db) is True
            assert is_tool_allowed("agent_status", db_path=dispatcher_db) is True
            assert is_tool_allowed("agent_mailbox", db_path=dispatcher_db) is True
            assert is_tool_allowed("workflow_status", db_path=dispatcher_db) is True
            assert is_tool_allowed("prompt_chain_execute", db_path=dispatcher_db) is True

    def test_blocked_tools_denied_when_enabled(self, yaml_config_file, dispatcher_db):
        """Blocked tools are denied in dispatcher mode."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_tool_allowed("scaffold", db_path=dispatcher_db) is False
            assert is_tool_allowed("generate_code", db_path=dispatcher_db) is False
            assert is_tool_allowed("ssp_generate", db_path=dispatcher_db) is False
            assert is_tool_allowed("terraform_plan", db_path=dispatcher_db) is False

    def test_unknown_tools_denied_when_enabled(self, yaml_config_file, dispatcher_db):
        """Tools not in either list are denied (default deny in dispatcher mode)."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_tool_allowed("unknown_tool", db_path=dispatcher_db) is False

    def test_per_project_tool_check(self, dispatcher_db):
        """Tool check respects per-project overrides."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project("proj-test", created_by="admin", db_path=dispatcher_db)
            assert is_tool_allowed("scaffold", project_id="proj-test", db_path=dispatcher_db) is False
            assert is_tool_allowed("task_dispatch", project_id="proj-test", db_path=dispatcher_db) is True


# ---------------------------------------------------------------------------
# filter_tools_for_dispatcher tests
# ---------------------------------------------------------------------------
class TestFilterTools:
    """Tests for filter_tools_for_dispatcher."""

    def test_no_filtering_when_disabled(self, dispatcher_db):
        """Full tool list returned when dispatcher mode is disabled."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            tools = ["scaffold", "task_dispatch", "generate_code", "agent_status"]
            result = filter_tools_for_dispatcher(tools, db_path=dispatcher_db)
            assert result == tools

    def test_filters_blocked_tools_when_enabled(self, yaml_config_file, dispatcher_db):
        """Blocked tools are removed when dispatcher mode is enabled."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            tools = ["scaffold", "task_dispatch", "generate_code", "agent_status"]
            result = filter_tools_for_dispatcher(tools, db_path=dispatcher_db)
            assert "scaffold" not in result
            assert "generate_code" not in result
            assert "task_dispatch" in result
            assert "agent_status" in result

    def test_empty_list_returns_empty(self, yaml_config_file, dispatcher_db):
        """Empty tool list returns empty."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            result = filter_tools_for_dispatcher([], db_path=dispatcher_db)
            assert result == []


# ---------------------------------------------------------------------------
# get_redirect_agent tests
# ---------------------------------------------------------------------------
class TestGetRedirectAgent:
    """Tests for get_redirect_agent."""

    def test_builder_tools_redirect_to_builder(self):
        """Builder domain tools redirect to builder-agent."""
        assert get_redirect_agent("scaffold") == "builder-agent"
        assert get_redirect_agent("generate_code") == "builder-agent"
        assert get_redirect_agent("write_tests") == "builder-agent"
        assert get_redirect_agent("run_tests") == "builder-agent"
        assert get_redirect_agent("lint") == "builder-agent"
        assert get_redirect_agent("format") == "builder-agent"

    def test_compliance_tools_redirect_to_compliance(self):
        """Compliance domain tools redirect to compliance-agent."""
        assert get_redirect_agent("ssp_generate") == "compliance-agent"
        assert get_redirect_agent("poam_generate") == "compliance-agent"
        assert get_redirect_agent("stig_check") == "compliance-agent"
        assert get_redirect_agent("sbom_generate") == "compliance-agent"

    def test_infra_tools_redirect_to_infra(self):
        """Infrastructure domain tools redirect to infra-agent."""
        assert get_redirect_agent("terraform_plan") == "infra-agent"
        assert get_redirect_agent("terraform_apply") == "infra-agent"
        assert get_redirect_agent("ansible_run") == "infra-agent"
        assert get_redirect_agent("k8s_deploy") == "infra-agent"

    def test_unknown_tool_returns_none(self):
        """Unknown tool returns None for redirect."""
        assert get_redirect_agent("unknown_tool") is None
        assert get_redirect_agent("task_dispatch") is None


# ---------------------------------------------------------------------------
# Per-project override DB tests
# ---------------------------------------------------------------------------
class TestProjectOverrides:
    """Tests for enable_for_project and disable_for_project."""

    def test_enable_creates_override(self, dispatcher_db):
        """Enable creates a new override record."""
        result = enable_for_project(
            "proj-abc", created_by="admin", db_path=dispatcher_db,
        )
        assert result["project_id"] == "proj-abc"
        assert result["enabled"] is True
        assert result["created_by"] == "admin"
        assert result["id"].startswith("dmo-")

    def test_enable_with_custom_tools(self, dispatcher_db):
        """Enable with custom dispatch and blocked tools."""
        result = enable_for_project(
            "proj-custom",
            created_by="isso",
            custom_dispatch_tools=["custom_dispatch_1"],
            custom_blocked_tools=["custom_block_1"],
            db_path=dispatcher_db,
        )
        assert "custom_dispatch_1" in result["custom_dispatch_tools"]
        assert "custom_block_1" in result["custom_blocked_tools"]

    def test_disable_sets_enabled_false(self, dispatcher_db):
        """Disable sets enabled=0 in the override."""
        enable_for_project("proj-dis", created_by="admin", db_path=dispatcher_db)
        result = disable_for_project("proj-dis", disabled_by="admin", db_path=dispatcher_db)
        assert result["enabled"] is False

        # Verify in DB
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            assert is_dispatcher_mode(project_id="proj-dis", db_path=dispatcher_db) is False

    def test_enable_upserts_on_conflict(self, dispatcher_db):
        """Enabling twice for the same project upserts (no duplicate)."""
        enable_for_project("proj-upsert", created_by="admin", db_path=dispatcher_db)
        enable_for_project(
            "proj-upsert", created_by="isso",
            custom_dispatch_tools=["new_tool"],
            db_path=dispatcher_db,
        )

        # Should still have exactly one row
        conn = sqlite3.connect(str(dispatcher_db))
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM dispatcher_mode_overrides WHERE project_id = ?",
            ("proj-upsert",),
        ).fetchone()["cnt"]
        conn.close()
        assert count == 1

    def test_custom_tools_merged_into_lists(self, dispatcher_db):
        """Custom dispatch tools are merged with global dispatch tools."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project(
                "proj-merge",
                created_by="admin",
                custom_dispatch_tools=["extra_dispatch"],
                db_path=dispatcher_db,
            )
            dispatch = get_dispatch_tools(project_id="proj-merge", db_path=dispatcher_db)
            assert "task_dispatch" in dispatch
            assert "extra_dispatch" in dispatch


# ---------------------------------------------------------------------------
# get_dispatch_tools / get_blocked_tools tests
# ---------------------------------------------------------------------------
class TestToolLists:
    """Tests for get_dispatch_tools and get_blocked_tools."""

    def test_get_dispatch_tools_defaults(self, dispatcher_db):
        """Returns default dispatch tools from config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            tools = get_dispatch_tools(db_path=dispatcher_db)
            assert "task_dispatch" in tools
            assert "agent_status" in tools
            assert len(tools) == 5

    def test_get_blocked_tools_defaults(self, dispatcher_db):
        """Returns default blocked tools from config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            tools = get_blocked_tools(db_path=dispatcher_db)
            assert "scaffold" in tools
            assert "generate_code" in tools
            assert len(tools) == 14

    def test_get_blocked_tools_with_project_custom(self, dispatcher_db):
        """Project custom blocked tools are merged."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project(
                "proj-blk",
                created_by="admin",
                custom_blocked_tools=["custom_block"],
                db_path=dispatcher_db,
            )
            tools = get_blocked_tools(project_id="proj-blk", db_path=dispatcher_db)
            assert "scaffold" in tools
            assert "custom_block" in tools


# ---------------------------------------------------------------------------
# get_status tests
# ---------------------------------------------------------------------------
class TestGetStatus:
    """Tests for get_status."""

    def test_status_without_project(self, dispatcher_db):
        """Status report without project ID."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            status = get_status(db_path=dispatcher_db)
            assert "global_config" in status
            assert "project_override" in status
            assert "effective_dispatcher_mode" in status
            assert "dispatch_only_tools" in status
            assert "blocked_tools" in status
            assert status["classification"] == "CUI"
            assert status["project_override"] is None

    def test_status_with_project_override(self, dispatcher_db):
        """Status report includes project override when present."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project("proj-status", created_by="admin", db_path=dispatcher_db)
            status = get_status(project_id="proj-status", db_path=dispatcher_db)
            assert status["project_override"] is not None
            assert status["effective_dispatcher_mode"] is True

    def test_status_effective_mode_reflects_config(self, yaml_config_file, dispatcher_db):
        """Effective mode reflects global config when no project override."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            status = get_status(db_path=dispatcher_db)
            assert status["effective_dispatcher_mode"] is True


# ---------------------------------------------------------------------------
# _ensure_table tests
# ---------------------------------------------------------------------------
class TestEnsureTable:
    """Tests for _ensure_table."""

    def test_creates_table_if_not_exists(self, tmp_path):
        """Creates dispatcher_mode_overrides table in fresh DB."""
        db_path = tmp_path / "fresh.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        _ensure_table(db_path)

        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatcher_mode_overrides'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_idempotent(self, dispatcher_db):
        """Calling _ensure_table multiple times is safe."""
        _ensure_table(dispatcher_db)
        _ensure_table(dispatcher_db)
        # Should not raise


# ---------------------------------------------------------------------------
# CLI output tests
# ---------------------------------------------------------------------------
class TestCLI:
    """Tests for CLI entry point."""

    def test_status_json_output(self, dispatcher_db, capsys):
        """--status --json produces valid JSON."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            with patch("sys.argv", ["dispatcher_mode.py", "--status", "--json",
                                     "--db-path", str(dispatcher_db)]):
                from tools.agent.dispatcher_mode import main
                main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "global_config" in data
        assert "effective_dispatcher_mode" in data

    def test_check_tool_json_output(self, dispatcher_db, capsys):
        """--check-tool --json produces valid JSON."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            with patch("sys.argv", ["dispatcher_mode.py", "--check-tool", "scaffold",
                                     "--json", "--db-path", str(dispatcher_db)]):
                from tools.agent.dispatcher_mode import main
                main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["tool_name"] == "scaffold"
        assert "allowed" in data

    def test_enable_requires_project_id(self, dispatcher_db, capsys):
        """--enable without --project-id fails with error."""
        with patch("sys.argv", ["dispatcher_mode.py", "--enable",
                                 "--db-path", str(dispatcher_db)]):
            from tools.agent.dispatcher_mode import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_status_human_output(self, dispatcher_db, capsys):
        """--status without --json produces human-readable text."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            with patch("sys.argv", ["dispatcher_mode.py", "--status",
                                     "--db-path", str(dispatcher_db)]):
                from tools.agent.dispatcher_mode import main
                main()

        captured = capsys.readouterr()
        assert "Dispatcher Mode Status" in captured.out
        assert "CUI" in captured.out


# ---------------------------------------------------------------------------
# Integration mock tests
# ---------------------------------------------------------------------------
class TestTeamOrchestratorIntegration:
    """Tests for dispatcher mode integration with team_orchestrator."""

    def test_subtask_redirect_when_dispatcher_enabled(self, yaml_config_file, dispatcher_db):
        """Blocked tool gets redirected to domain agent in _execute_subtask."""
        # Simulate the dispatcher mode check that team_orchestrator does
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_dispatcher_mode(db_path=dispatcher_db) is True
            assert is_tool_allowed("scaffold", db_path=dispatcher_db) is False
            redirect = get_redirect_agent("scaffold")
            assert redirect == "builder-agent"

    def test_dispatch_tools_pass_through(self, yaml_config_file, dispatcher_db):
        """Dispatch tools pass through without redirection."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            assert is_tool_allowed("task_dispatch", db_path=dispatcher_db) is True
            # No redirect needed for allowed tools
            redirect = get_redirect_agent("task_dispatch")
            assert redirect is None


class TestSkillRouterIntegration:
    """Tests for dispatcher mode integration with skill_router."""

    def test_blocked_skill_redirected(self, yaml_config_file, dispatcher_db):
        """Skill router should redirect blocked skills to domain agents."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            # Simulate skill_router check
            skill_id = "ssp_generate"
            assert is_dispatcher_mode(db_path=dispatcher_db) is True
            assert is_tool_allowed(skill_id, db_path=dispatcher_db) is False
            redirect = get_redirect_agent(skill_id)
            assert redirect == "compliance-agent"

    def test_allowed_skill_not_redirected(self, yaml_config_file, dispatcher_db):
        """Allowed skills should not be redirected."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", yaml_config_file):
            skill_id = "agent_mailbox"
            assert is_tool_allowed(skill_id, db_path=dispatcher_db) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    """Edge case tests."""

    def test_none_project_id_uses_global(self, dispatcher_db):
        """None project_id falls back to global config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            assert is_dispatcher_mode(project_id=None, db_path=dispatcher_db) is False

    def test_empty_project_id_uses_global(self, dispatcher_db):
        """Empty string project_id falls back to global config."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            assert is_dispatcher_mode(project_id="", db_path=dispatcher_db) is False

    def test_all_14_blocked_tools_covered_by_redirect(self):
        """Every tool in the default blocked list has a redirect agent."""
        blocked = [
            "scaffold", "generate_code", "write_tests", "run_tests",
            "lint", "format", "ssp_generate", "poam_generate",
            "stig_check", "sbom_generate", "terraform_plan",
            "terraform_apply", "ansible_run", "k8s_deploy",
        ]
        for tool in blocked:
            redirect = get_redirect_agent(tool)
            assert redirect is not None, f"No redirect agent for blocked tool: {tool}"

    def test_concurrent_projects_isolated(self, dispatcher_db):
        """Different projects can have independent overrides."""
        with patch("tools.agent.dispatcher_mode.CONFIG_PATH", Path("/nonexistent.yaml")):
            enable_for_project("proj-A", created_by="admin", db_path=dispatcher_db)
            # proj-B has no override, so global (disabled) applies
            assert is_dispatcher_mode(project_id="proj-A", db_path=dispatcher_db) is True
            assert is_dispatcher_mode(project_id="proj-B", db_path=dispatcher_db) is False
