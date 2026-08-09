# CUI // SP-CTI
"""hgx-agent-02 — the `agent_workflow_tools` allowlist schema (AGENT-WF-001).

A `node_type: agent` workflow step hands a MODEL a set of tools and lets it
choose, so the policy deciding which tools reach the model is a security
surface. These tests pin the policy DATA — default-deny, disjoint lists, nothing
mutating in the unattended list, names that a real toolset actually offers, and
a gate registry entry whose block_on conditions match the reasons the enforcing
module raises. No DB, no LLM: air-gap safe.

The mechanism itself is covered in tests/studio/test_agent_tool_gate.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES = REPO_ROOT / "args" / "security_gates.yaml"
GATE_MIRRORS = (
    REPO_ROOT / "icdev" / "args" / "security_gates.yaml",
    REPO_ROOT / "icdev" / "data" / "args" / "security_gates.yaml",
)
WORKTREE_TOOLS = REPO_ROOT / "tools" / "genesis" / "rubric_build_tools.py"
GATE_MODULE = REPO_ROOT / "tools" / "studio" / "executors" / "agent_tool_gate.py"

POLICY_KEY = "agent_workflow_tools"
GATE_ID = "AGENT-WF-001"

#: Anything that writes, executes, or leaves the worktree. None of these may be
#: callable without an approved human gate.
MUTATING = {"write_file", "patch_file", "run_command"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gates() -> dict:
    return _load(GATES)


@pytest.fixture(scope="module")
def policy(gates) -> dict:
    return gates[POLICY_KEY]


@pytest.fixture(scope="module")
def gate_entry(gates) -> dict:
    for entry in gates["gates"]:
        if entry.get("id") == GATE_ID:
            return entry
    pytest.fail(f"{GATE_ID} is not registered in the gates: list")


@pytest.fixture(scope="module")
def worktree_tool_names() -> set:
    """Tool names build_worktree_toolset actually offers.

    Parsed rather than imported so this stays a pure data test — and because a
    name in the policy that no toolset offers is inert rather than an error, so
    nothing else would catch the typo.
    """
    src = WORKTREE_TOOLS.read_text(encoding="utf-8")
    return set(re.findall(r'"name": "([a-z0-9_]+)"', src))


# ── Default-deny ───────────────────────────────────────────────────────────

def test_default_is_deny(policy):
    assert policy["default"] == "deny"


def test_both_lists_present_and_populated(policy):
    assert len(policy["allowed"]) >= 3
    assert len(policy["requires_approval"]) >= 1


def test_lists_are_disjoint(policy):
    overlap = set(policy["allowed"]) & set(policy["requires_approval"])
    assert not overlap, f"tool in both lists: {sorted(overlap)}"


def test_no_duplicate_entries(policy):
    for key in ("allowed", "requires_approval"):
        entries = policy[key]
        assert len(entries) == len(set(entries)), f"duplicates in {key}"


# ── What may run unattended ────────────────────────────────────────────────

def test_mutating_tools_require_a_human_gate(policy):
    approval = set(policy["requires_approval"])
    allowed = set(policy["allowed"])
    for tool in MUTATING:
        assert tool in approval, f"{tool} must require a human gate"
        assert tool not in allowed, f"{tool} must not be callable unattended"


def test_read_only_inspection_is_allowed(policy):
    allowed = set(policy["allowed"])
    for tool in ("read_file", "list_files", "grep_files", "done"):
        assert tool in allowed


def test_every_named_tool_is_one_a_toolset_offers(policy, worktree_tool_names):
    named = set(policy["allowed"]) | set(policy["requires_approval"])
    missing = sorted(named - worktree_tool_names)
    assert not missing, (
        f"named in {POLICY_KEY} but offered by no toolset (a typo here silently "
        f"removes a tool rather than raising): {missing}"
    )


# ── Per-tool limits ────────────────────────────────────────────────────────

def test_a_platform_baseline_is_declared(policy):
    assert policy["default_min_il"] in ("IL2", "IL4", "IL5", "IL6")


def test_command_execution_is_held_above_the_baseline(policy):
    """Executing code is not merely a worktree edit; it may not run at baseline."""
    order = {"IL2": 2, "IL4": 4, "IL5": 5, "IL6": 6}
    limits = policy["tool_limits"]["run_command"]
    assert order[limits["min_il"]] > order[policy["default_min_il"]]


def test_tool_limits_only_names_allowlisted_tools(policy):
    named = set(policy["allowed"]) | set(policy["requires_approval"])
    stray = sorted(set(policy.get("tool_limits") or {}) - named)
    assert not stray, f"tool_limits entry for a tool no list names: {stray}"


def test_tool_limits_declare_a_known_impact_level(policy):
    for tool, limits in (policy.get("tool_limits") or {}).items():
        assert limits["min_il"] in ("IL2", "IL4", "IL5", "IL6"), tool


# ── Gate registry entry ────────────────────────────────────────────────────

def test_the_gate_points_at_this_policy_and_its_enforcer(gate_entry):
    assert gate_entry["policy"] == POLICY_KEY
    assert gate_entry["severity"] == "HIGH"
    assert (REPO_ROOT / gate_entry["tool"]).is_file(), (
        "the gate names an enforcing module that does not exist"
    )


def test_the_gate_covers_the_access_control_and_audit_controls(gate_entry):
    controls = set(gate_entry["nist_controls"])
    assert {"AC-3", "AC-6", "AU-2", "AU-12"} <= controls


def test_every_block_on_reason_is_raised_by_the_enforcing_module(gate_entry):
    """A block_on condition nothing raises is decoration, not a gate."""
    src = GATE_MODULE.read_text(encoding="utf-8")
    for reason in gate_entry["block_on"]:
        assert f'"{reason}"' in src, f"{reason} appears in no refusal path"


def test_the_gate_blocks_on_an_unreadable_policy(gate_entry):
    """Fail-closed: no policy must mean no toolset, not an unbounded one."""
    assert "agent_gate_policy_unavailable" in gate_entry["block_on"]


# ── Mirrors ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mirror", GATE_MIRRORS, ids=lambda p: p.parent.as_posix()[-20:])
def test_the_policy_is_mirrored_verbatim(policy, gate_entry, mirror):
    """A wheel or the icdev/ package must enforce the same allowlist.

    The gate reader probes `args/` and `data/args/` at every level, so a stale
    mirror is not a cosmetic drift — it is a different allowlist depending on
    where the executor was launched from.
    """
    mirrored = _load(mirror)
    assert mirrored[POLICY_KEY] == policy
    assert [g for g in mirrored["gates"] if g.get("id") == GATE_ID] == [gate_entry]
