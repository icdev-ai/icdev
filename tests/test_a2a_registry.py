"""Tests for A2A discovery registry (adapt-iii-03)."""
from pathlib import Path


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

def test_module_importable():
    from tools.agents.a2a_registry import AgentRegistry, AgentEntry, get_registry
    assert AgentRegistry is not None
    assert AgentEntry is not None
    assert callable(get_registry)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_agent():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("test_agent", 9999, ["code_generation", "testing"], description="Test agent")
    entry = r.get("test_agent")
    assert entry is not None
    assert entry.port == 9999
    assert "code_generation" in entry.capabilities


def test_register_multiple_capabilities():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("multi", 9000, ["a", "b", "c"])
    assert r.get("multi") is not None
    assert len(r.get("multi").capabilities) == 3


def test_all_agents_returns_registered():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("ag1", 9001, ["foo"])
    r.register("ag2", 9002, ["bar"])
    names = {a.name for a in r.all_agents()}
    assert "ag1" in names
    assert "ag2" in names


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_exact_capability():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("security", 8447, ["security_scan", "vulnerability"])
    r.register("builder", 8445, ["code_generation"])
    found = r.discover("security_scan")
    assert len(found) == 1
    assert found[0].name == "security"


def test_discover_prefix_fallback():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("devsecops", 8456, ["devsecops", "zero_trust"])
    found = r.discover("devsec")  # prefix of "devsecops"
    assert any(a.name == "devsecops" for a in found)


def test_discover_unknown_capability_returns_empty():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("agent_x", 9000, ["some_capability"])
    found = r.discover("nonexistent_capability_xyz_123")
    assert found == []


def test_discover_returns_all_matching_agents():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("a1", 9001, ["shared_cap"])
    r.register("a2", 9002, ["shared_cap"])
    found = r.discover("shared_cap")
    assert len(found) == 2


# ---------------------------------------------------------------------------
# Seed from defaults (no YAML required)
# ---------------------------------------------------------------------------

def test_seed_defaults_registers_15_agents():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    count = r._seed_defaults()
    assert count == 15


def test_seed_defaults_has_core_agents():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r._seed_defaults()
    assert r.get("orchestrator") is not None
    assert r.get("security") is not None
    assert r.get("compliance") is not None


def test_seed_defaults_port_range():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r._seed_defaults()
    for agent in r.all_agents():
        assert 8443 <= agent.port <= 8458, f"{agent.name} port {agent.port} out of expected range"


def test_seed_from_config_fallback_to_defaults_when_missing():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    count = r.seed_from_config(config_path=Path("/nonexistent/path.yaml"))
    assert count == 15


# ---------------------------------------------------------------------------
# AgentEntry
# ---------------------------------------------------------------------------

def test_agent_entry_base_url():
    from tools.agents.a2a_registry import AgentEntry
    entry = AgentEntry(name="test", port=8443, capabilities=["x"], host="127.0.0.1")
    assert entry.base_url() == "https://127.0.0.1:8443"


def test_agent_entry_default_health_url():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("myagent", 8888, ["foo"])
    entry = r.get("myagent")
    assert "8888" in entry.health_url
    assert "health" in entry.health_url


# ---------------------------------------------------------------------------
# ping_all (mocked)
# ---------------------------------------------------------------------------

def test_ping_all_returns_dict_per_agent():
    from tools.agents.a2a_registry import AgentRegistry
    from unittest.mock import patch, MagicMock
    r = AgentRegistry()
    r.register("agent1", 9001, ["cap1"])
    r.register("agent2", 9002, ["cap2"])

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.get", return_value=mock_resp):
        results = r.ping_all(timeout=1)

    assert "agent1" in results
    assert "agent2" in results
    assert results["agent1"]["status"] == "ok"


def test_ping_all_handles_connection_error():
    from tools.agents.a2a_registry import AgentRegistry
    from unittest.mock import patch
    r = AgentRegistry()
    r.register("offline_agent", 9999, ["test"])

    with patch("requests.get", side_effect=ConnectionError("refused")):
        results = r.ping_all(timeout=1)

    assert results["offline_agent"]["status"] == "error"
    assert results["offline_agent"]["error"] is not None


# ---------------------------------------------------------------------------
# list_capabilities
# ---------------------------------------------------------------------------

def test_list_capabilities_includes_registered():
    from tools.agents.a2a_registry import AgentRegistry
    r = AgentRegistry()
    r.register("a", 9000, ["alpha", "beta"])
    caps = r.list_capabilities()
    assert "alpha" in caps
    assert "beta" in caps
    assert caps == sorted(caps)  # must be sorted


# ---------------------------------------------------------------------------
# get_registry singleton
# ---------------------------------------------------------------------------

def test_get_registry_is_seeded():
    from tools.agents.a2a_registry import get_registry
    r = get_registry()
    agents = r.all_agents()
    assert len(agents) >= 15
