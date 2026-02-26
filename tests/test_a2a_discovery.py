#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for A2A v0.3 Agent Card Generator and Discovery Server (Phase 55, D344)."""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Agent Card Generator Tests
# ---------------------------------------------------------------------------

class TestAgentCardGenerator:
    """Test A2A v0.3 Agent Card generation."""

    def test_generate_card_basic(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("orchestrator")
        assert card["name"]
        assert card["protocolVersion"] == "0.3"
        assert "capabilities" in card
        assert "skills" in card

    def test_card_has_v03_fields(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("builder")
        assert "contextId" in card
        assert "tasks" in card
        assert "sendSubscribe" in card["tasks"]
        assert card["tasks"]["sendSubscribe"]["path"] == "/tasks/subscribe"

    def test_card_capabilities(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("compliance")
        caps = card["capabilities"]
        assert "taskSubscription" in caps
        assert "contextPreservation" in caps
        assert "asyncNotifications" in caps
        assert caps["taskSubscription"] is True

    def test_card_skills_populated(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("builder")
        assert len(card["skills"]) > 0
        skill = card["skills"][0]
        assert "id" in skill
        assert "name" in skill
        assert "description" in skill
        assert "inputModes" in skill
        assert "outputModes" in skill

    def test_card_authentication(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("security")
        assert "authentication" in card
        assert "mutual_tls" in card["authentication"]["schemes"]

    def test_card_metadata(self):
        from tools.agent.a2a_agent_card_generator import generate_agent_card
        card = generate_agent_card("orchestrator")
        assert card["metadata"]["tier"] == "core"
        assert card["metadata"]["classification"] == "CUI // SP-CTI"

    def test_generate_all_cards(self):
        from tools.agent.a2a_agent_card_generator import generate_all_cards
        cards = generate_all_cards()
        assert len(cards) >= 15  # At least 15 agents
        for agent_id, card in cards.items():
            assert card["protocolVersion"] == "0.3"
            assert "skills" in card

    def test_list_agents(self):
        from tools.agent.a2a_agent_card_generator import list_agents
        agents = list_agents()
        assert len(agents) >= 15
        for agent in agents:
            assert "agent_id" in agent
            assert "protocol_version" in agent
            assert agent["protocol_version"] == "0.3"


# ---------------------------------------------------------------------------
# Agent Card Tier Tests
# ---------------------------------------------------------------------------

class TestAgentTiers:
    """Test agent tier classification."""

    def test_core_agents(self):
        from tools.agent.a2a_agent_card_generator import _get_agent_tier
        assert _get_agent_tier("orchestrator") == "core"
        assert _get_agent_tier("architect") == "core"

    def test_support_agents(self):
        from tools.agent.a2a_agent_card_generator import _get_agent_tier
        assert _get_agent_tier("knowledge") == "support"
        assert _get_agent_tier("monitor") == "support"

    def test_domain_agents(self):
        from tools.agent.a2a_agent_card_generator import _get_agent_tier
        assert _get_agent_tier("builder") == "domain"
        assert _get_agent_tier("compliance") == "domain"
        assert _get_agent_tier("security") == "domain"


# ---------------------------------------------------------------------------
# Discovery Server Tests
# ---------------------------------------------------------------------------

class TestDiscoveryServer:
    """Test A2A v0.3 Agent Discovery."""

    def test_discover_agents(self):
        from tools.agent.a2a_discovery_server import discover_agents
        agents = discover_agents()
        assert len(agents) >= 15
        for agent in agents:
            assert "agent_id" in agent
            assert "card" in agent
            assert "health" in agent

    def test_find_agent_for_skill(self):
        from tools.agent.a2a_discovery_server import find_agent_for_skill
        matches = find_agent_for_skill("ssp_generate")
        assert len(matches) >= 1
        assert matches[0]["agent_id"] == "compliance"

    def test_find_agent_for_unknown_skill(self):
        from tools.agent.a2a_discovery_server import find_agent_for_skill
        matches = find_agent_for_skill("nonexistent_skill_xyz")
        assert len(matches) == 0

    def test_find_agents_by_capability(self):
        from tools.agent.a2a_discovery_server import find_agents_by_capability
        matches = find_agents_by_capability("taskSubscription")
        assert len(matches) >= 15  # All agents support task subscription

    def test_discovery_summary(self):
        from tools.agent.a2a_discovery_server import get_discovery_summary
        summary = get_discovery_summary()
        assert summary["total_agents"] >= 15
        assert summary["protocol_version"] == "0.3"
        assert summary["total_skills"] > 0
        assert "tier_distribution" in summary
        assert "capability_coverage" in summary
