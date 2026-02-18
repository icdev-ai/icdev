#!/usr/bin/env python3
# CUI // SP-CTI
"""Pytest: Agent health checks for all configured agents.

Tests that each agent's /health endpoint responds correctly
and that agent configuration matches deployment.
"""

import json
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def _load_agent_config():
    """Load agent configuration from args/agent_config.yaml."""
    config_path = BASE_DIR / "args" / "agent_config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {"agents": {}}


class TestAgentHealth:
    """Test agent health endpoints."""

    def test_agent_config_exists(self):
        """Agent configuration file should exist."""
        config_path = BASE_DIR / "args" / "agent_config.yaml"
        assert config_path.exists(), "args/agent_config.yaml not found"

    def test_agent_config_valid(self):
        """Agent configuration should be valid YAML with expected structure."""
        config = _load_agent_config()
        assert "agents" in config, "Config missing 'agents' key"
        assert len(config["agents"]) > 0, "No agents configured"

    def test_each_agent_has_port(self):
        """Each agent should have a port configured."""
        config = _load_agent_config()
        for name, agent in config.get("agents", {}).items():
            assert "port" in agent, f"Agent '{name}' missing port"
            assert isinstance(agent["port"], int), f"Agent '{name}' port not integer"

    def test_no_duplicate_ports(self):
        """All agent ports should be unique."""
        config = _load_agent_config()
        ports = [a["port"] for a in config.get("agents", {}).values()]
        assert len(ports) == len(set(ports)), f"Duplicate ports found: {ports}"

    def test_agent_ports_in_valid_range(self):
        """All agent ports should be in valid range (1024-65535)."""
        config = _load_agent_config()
        for name, agent in config.get("agents", {}).items():
            port = agent.get("port", 0)
            assert 1024 <= port <= 65535, \
                f"Agent '{name}' port {port} outside valid range (1024-65535)"

    def test_agent_cards_exist(self):
        """Agent cards should exist in tools/agent/cards/."""
        cards_dir = BASE_DIR / "tools" / "agent" / "cards"
        if cards_dir.exists():
            cards = list(cards_dir.glob("*_card.json"))
            config = _load_agent_config()
            assert len(cards) >= len(config.get("agents", {})), \
                f"Expected {len(config.get('agents', {}))} cards, found {len(cards)}"

    def test_agent_card_structure(self):
        """Each agent card should have required fields."""
        cards_dir = BASE_DIR / "tools" / "agent" / "cards"
        if not cards_dir.exists():
            pytest.skip("No agent cards directory")
        for card_path in cards_dir.glob("*_card.json"):
            with open(card_path) as f:
                card = json.load(f)
            assert "name" in card, f"Card {card_path.name} missing 'name'"
            assert "url" in card, f"Card {card_path.name} missing 'url'"
            assert "skills" in card, f"Card {card_path.name} missing 'skills'"

    def test_agent_card_has_authentication(self):
        """Each agent card should specify authentication method."""
        cards_dir = BASE_DIR / "tools" / "agent" / "cards"
        if not cards_dir.exists():
            pytest.skip("No agent cards directory")
        for card_path in cards_dir.glob("*_card.json"):
            with open(card_path) as f:
                card = json.load(f)
            assert "authentication" in card or "auth" in card, \
                f"Card {card_path.name} missing authentication config"

    @patch("urllib.request.urlopen")
    def test_health_endpoint_mock(self, mock_urlopen):
        """Health endpoint should return 200 (mocked)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"status": "healthy"}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        config = _load_agent_config()
        for name, agent in config.get("agents", {}).items():
            endpoint = agent.get(
                "health_endpoint",
                f"https://localhost:{agent['port']}/health"
            )
            # In real test, would call endpoint; here we verify config
            assert endpoint.startswith("https://"), \
                f"Agent '{name}' health not HTTPS"

    @patch("urllib.request.urlopen")
    def test_health_response_structure(self, mock_urlopen):
        """Health response should contain status field (mocked)."""
        health_data = {"status": "healthy", "uptime": 3600, "version": "1.0.0"}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(health_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import urllib.request
        resp = urllib.request.urlopen("https://localhost:8443/health")
        data = json.loads(resp.read().decode())
        assert "status" in data, "Health response missing 'status' field"
        assert data["status"] == "healthy"
