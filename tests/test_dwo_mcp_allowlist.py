"""dwo-mcp-02-d1 — the MCP workflow-tool allowlist schema.

A `node_type: mcp` workflow step dispatches an arbitrary MCP tool, so the
policy that decides which tools a step may call is a security surface. These
tests pin the schema itself: default-deny, disjoint lists, and tool names that
actually exist in tools/mcp/tool_registry.py (a typo reads as "not
allowlisted" and only surfaces at runtime).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "args" / "security_gates.yaml"
GATES_MIRROR = REPO_ROOT / "icdev" / "data" / "args" / "security_gates.yaml"
TOOL_REGISTRY = REPO_ROOT / "tools" / "mcp" / "tool_registry.py"

# Tools that must never be dispatched unattended from a workflow step.
DESTRUCTIVE = {"terraform_apply", "k8s_deploy", "ansible_run", "rollback", "self_heal"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy() -> dict:
    return _load(GATES)["mcp_workflow_tools"]


@pytest.fixture(scope="module")
def registry_tool_names() -> set:
    # Parsed rather than imported: tool_registry pulls in the whole MCP surface.
    src = TOOL_REGISTRY.read_text(encoding="utf-8")
    return set(re.findall(r'^    "([a-z0-9_]+)": \{', src, re.M))


def test_default_is_deny(policy):
    assert policy["default"] == "deny"


def test_both_lists_present_and_populated(policy):
    assert len(policy["allowed"]) >= 5
    assert len(policy["requires_approval"]) >= 5


def test_lists_are_disjoint(policy):
    overlap = set(policy["allowed"]) & set(policy["requires_approval"])
    assert not overlap, f"tool in both lists: {sorted(overlap)}"


def test_no_duplicate_entries(policy):
    for key in ("allowed", "requires_approval"):
        entries = policy[key]
        assert len(entries) == len(set(entries)), f"duplicates in {key}"


def test_destructive_tools_require_approval(policy):
    approval = set(policy["requires_approval"])
    allowed = set(policy["allowed"])
    for tool in DESTRUCTIVE:
        assert tool in approval, f"{tool} must require a human gate"
        assert tool not in allowed


def test_read_only_examples_are_allowed(policy):
    allowed = set(policy["allowed"])
    for tool in ("health_check", "rag_search", "kg_search", "nist_lookup"):
        assert tool in allowed


def test_every_named_tool_exists_in_the_registry(policy, registry_tool_names):
    named = set(policy["allowed"]) | set(policy["requires_approval"])
    missing = sorted(named - registry_tool_names)
    assert not missing, f"allowlist names no such MCP tool: {missing}"


def test_gate_registry_entry_points_at_the_policy():
    gates = _load(GATES)["gates"]
    entry = next(g for g in gates if g["id"] == "MCP-WF-001")
    assert entry["policy"] == "mcp_workflow_tools"
    assert "mcp_tool_not_allowlisted" in entry["block_on"]
    assert "mcp_tool_awaiting_human_approval" in entry["block_on"]


def test_package_mirror_matches_the_root_copy():
    assert GATES_MIRROR.read_text(encoding="utf-8") == GATES.read_text(encoding="utf-8")
