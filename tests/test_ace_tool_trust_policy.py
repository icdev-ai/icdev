# CUI // SP-CTI
"""Trust-tier ↔ agent-tool policy regression tests (hgx-sess-03).

The defect these fence: ``args/ace/roles/qa_agent.yaml`` declared
``trust_tier: yellow`` and listed ``run_tool``, but the pre-tool hook in
``CoWorkerThread._run_agent_loop`` refuses ``run_tool`` below green. The role's
entire ``icdev_tools`` list is ``python tools/testing/…`` commands reachable
only through that tool, so every call it made was denied and the role could not
do its job — a contradiction nothing checked, at load or anywhere else.

Three properties are asserted, one per acceptance criterion:

1. qa_agent can execute its declared test commands (its whole toolset is
   callable at its own tier, and the narrow exec tool really runs a command).
2. A role declaring a tool its ``trust_tier`` forbids fails at load, by name.
3. The ladder still blocks write/exec for non-green roles — the narrow tool is
   a smaller command surface, not a hole in the ladder.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from icdev.tools.ace import tool_trust_policy as policy
from icdev.tools.ace.agent_tools import AgentToolRegistry
from icdev.tools.ace.role_loader import RoleTemplate
from icdev.tools.ace.step_executor import TrustKernelDeniedError
from icdev.tools.ace.tool_runner import (
    InvalidPrefixError,
    ProfileViolationError,
    ToolRunner,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ROLES_DIR = _REPO_ROOT / "args" / "ace" / "roles"
_QA_AGENT = _ROLES_DIR / "qa_agent.yaml"

# A real, cheap, side-effect-free command from qa_agent's own allowlist.
_QA_CMD = "python tools/testing/qa_agent_runner.py --discover-gaps --json"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# AC1 — qa_agent can execute its declared test commands
# ---------------------------------------------------------------------------


class TestQaAgentCanRunItsTools:
    def test_every_declared_tool_is_callable_at_its_own_tier(self):
        role = _load(_QA_AGENT)
        tier = role["trust_tier"]
        denied = [
            name
            for name in policy.effective_agent_tools(role.get("agent_tools"), role.get("mode"))
            if not policy.is_permitted(name, tier)
        ]
        assert denied == [], (
            f"qa_agent (trust_tier={tier!r}) declares tools it cannot call: {denied}"
        )

    def test_role_declares_the_narrow_exec_tool_not_the_green_only_one(self):
        """The fix is (b) — narrow the tool — not (a), promote the role."""
        role = _load(_QA_AGENT)
        assert role["trust_tier"] == "yellow", "qa_agent must stay off the green rung"
        assert "run_test_tool" in role["agent_tools"]
        assert "run_tool" not in role["agent_tools"]
        assert "write_file" not in role["agent_tools"]

    def test_every_icdev_tool_command_clears_the_test_profile(self):
        """Not one command in the allowlist may be stranded by the narrowing.

        The subprocess is stubbed: this asserts the guard chain, not the tools
        themselves (``--run`` would execute the whole Playwright suite).
        """
        role = _load(_QA_AGENT)
        runner = ToolRunner(role["icdev_tools"], repo_root=_REPO_ROOT)
        for cmd in role["icdev_tools"]:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
                try:
                    runner.run(
                        cmd, coworker_id="qa-1", instance_id="i-1",
                        trust_tier=role["trust_tier"], profile="test",
                    )
                except PermissionError as exc:
                    pytest.fail(
                        f"qa_agent command refused by the test profile: {cmd!r} — {exc}"
                    )
            assert mock_run.called, f"command never reached execution: {cmd!r}"

    def test_registry_actually_executes_a_declared_command_at_yellow(self):
        """End-to-end through the production seam: registry → handler → subprocess."""
        role = _load(_QA_AGENT)
        spec = types.SimpleNamespace(
            coworker_id="qa-1",
            trust_tier=role["trust_tier"],
            folder_access=role.get("folder_access", []),
            icdev_tools=role["icdev_tools"],
            coordination_namespace="ns-1",
        )
        _, handlers = AgentToolRegistry(spec, "inst-1").build(role["agent_tools"])
        assert "run_test_tool" in handlers

        result = handlers["run_test_tool"]({"command": _QA_CMD}, None)
        assert "exit_code=" in result
        assert "Permission denied" not in result
        assert "requires green trust tier" not in result


# ---------------------------------------------------------------------------
# AC2 — the contradiction is caught at load, not at run time
# ---------------------------------------------------------------------------


class TestLoadTimeValidation:
    def _spec(self, **over):
        base = {
            "role_id": "probe_role",
            "trust_tier": "yellow",
            "steps": [],
            "tool_permissions": [],
            "mode": "agent",
            "agent_tools": ["read_file", "done"],
        }
        base.update(over)
        return base

    def test_forbidden_tool_raises_at_load_naming_tool_and_tier(self):
        with pytest.raises(ValueError) as exc:
            RoleTemplate.from_dict(self._spec(agent_tools=["read_file", "run_tool"]))
        message = str(exc.value)
        assert "run_tool" in message
        assert "yellow" in message
        assert "probe_role" in message

    def test_write_file_is_caught_too(self):
        with pytest.raises(ValueError, match="write_file"):
            RoleTemplate.from_dict(self._spec(agent_tools=["write_file"]))

    def test_permitted_toolset_loads_cleanly(self):
        role = RoleTemplate.from_dict(
            self._spec(agent_tools=["read_file", "run_test_tool", "done"])
        )
        assert role.agent_tools == ["read_file", "run_test_tool", "done"]

    def test_green_role_may_still_declare_run_tool(self):
        role = RoleTemplate.from_dict(
            self._spec(trust_tier="green", agent_tools=["write_file", "run_tool"])
        )
        assert role.trust_tier == "green"

    def test_agent_mode_default_toolset_is_validated_not_just_declarations(self):
        """An empty agent_tools list still yields write_file/run_tool at runtime."""
        with pytest.raises(ValueError, match="default agent toolset"):
            RoleTemplate.from_dict(self._spec(agent_tools=[]))

    def test_steps_mode_with_no_agent_tools_is_unaffected(self):
        role = RoleTemplate.from_dict(self._spec(mode="steps", agent_tools=[]))
        assert role.mode == "steps"

    def test_unknown_trust_tier_fails_closed(self):
        problems = policy.validate_role_tools(
            "probe_role", "platinum", ["read_file", "run_test_tool"], "agent"
        )
        assert any("unknown trust_tier" in p for p in problems)
        assert not policy.is_permitted("run_test_tool", "platinum")

    def test_every_shipped_role_passes_the_gate(self):
        """The repo-wide sweep: no role on disk may declare beyond its tier."""
        report = policy.check_all_roles(_ROLES_DIR)
        assert report["checked"] > 0
        assert report["violations"] == [], report["violations"]
        assert report["unreadable"] == [], report["unreadable"]


# ---------------------------------------------------------------------------
# AC3 — the ladder still blocks write/exec for non-green roles
# ---------------------------------------------------------------------------


class TestLadderIntact:
    @pytest.mark.parametrize("tier", ["red", "orange", "yellow"])
    @pytest.mark.parametrize("tool", ["write_file", "run_tool"])
    def test_write_and_exec_refused_below_green(self, tier, tool):
        assert not policy.is_permitted(tool, tier)
        assert "Permission denied" in policy.denial_message(tool, tier)

    @pytest.mark.parametrize("tool", ["write_file", "run_tool"])
    def test_green_keeps_write_and_exec(self, tool):
        assert policy.is_permitted(tool, "green")

    @pytest.mark.parametrize("tier", ["red", "orange"])
    def test_narrow_tool_is_not_a_free_pass_for_every_tier(self, tier):
        assert not policy.is_permitted("run_test_tool", tier)

    def test_denial_message_points_at_the_narrow_substitute(self):
        message = policy.denial_message("run_tool", "yellow")
        assert "run_test_tool" in message

    def test_read_only_tools_are_unrestricted(self):
        for tool in ("read_file", "search_files", "grep_files", "read_result", "done"):
            assert policy.min_tier_for(tool) is None
            assert policy.is_permitted(tool, "red")

    def test_full_profile_still_requires_green(self):
        runner = ToolRunner([_QA_CMD], repo_root=_REPO_ROOT)
        for tier in ("red", "orange", "yellow"):
            with pytest.raises(TrustKernelDeniedError):
                runner.run(_QA_CMD, coworker_id="c", instance_id="i",
                           trust_tier=tier, profile="full")

    def test_test_profile_cannot_reach_a_non_testing_module(self):
        """The narrowing is on the command, so a role cannot widen it via icdev_tools."""
        cmd = "python tools/db/storage.py --health --json"
        runner = ToolRunner([cmd], repo_root=_REPO_ROOT)
        with pytest.raises(InvalidPrefixError):
            runner.run(cmd, coworker_id="c", instance_id="i",
                       trust_tier="green", profile="test")

    @pytest.mark.parametrize("flag", ["--apply", "--fix", "--write", "--heal", "--promote"])
    def test_test_profile_refuses_mutating_flags(self, flag):
        cmd = f"python tools/testing/selector_healer.py --selector btn {flag}"
        runner = ToolRunner([cmd], repo_root=_REPO_ROOT)
        with pytest.raises(ProfileViolationError):
            runner.run(cmd, coworker_id="c", instance_id="i",
                       trust_tier="yellow", profile="test")

    def test_unknown_profile_does_not_fall_through_to_the_widest(self):
        runner = ToolRunner([_QA_CMD], repo_root=_REPO_ROOT)
        with pytest.raises(ProfileViolationError):
            runner.run(_QA_CMD, coworker_id="c", instance_id="i",
                       trust_tier="green", profile="wide-open")

    def test_hook_and_loader_read_the_same_table(self):
        """Anything the loader accepts, the run-time hook must let through."""
        for tier in policy.TIER_ORDER:
            for tool in sorted(policy.MIN_TRUST_TIER):
                accepted_at_load = not policy.validate_role_tools(
                    "r", tier, [tool], "agent"
                )
                assert accepted_at_load == policy.is_permitted(tool, tier)


# ---------------------------------------------------------------------------
# Execution is argv, not a shell (OS-agnostic criterion on this card)
# ---------------------------------------------------------------------------


class TestArgvExecution:
    def test_interpreter_is_pinned_to_this_process(self):
        """Bare `python` resolves to the Store stub on Windows; sys.executable never does."""
        argv = ToolRunner._build_argv(_QA_CMD)
        assert argv[0] == sys.executable
        assert argv[1:] == ["tools/testing/qa_agent_runner.py", "--discover-gaps", "--json"]

    def test_module_form_is_split_not_shell_interpreted(self):
        argv = ToolRunner._build_argv("python -m icdev.tools.testing.route_smoke --all --json")
        assert argv[1:] == ["-m", "icdev.tools.testing.route_smoke", "--all", "--json"]

    def test_subprocess_is_invoked_without_a_shell(self):
        runner = ToolRunner([_QA_CMD], repo_root=_REPO_ROOT)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            runner.run(_QA_CMD, coworker_id="c", instance_id="i",
                       trust_tier="yellow", profile="test")
        args, kwargs = mock_run.call_args
        assert kwargs["shell"] is False
        assert isinstance(args[0], list), "command must be argv, not a shell string"


# ---------------------------------------------------------------------------
# Mirror parity for the new module (tools/ace is a byte-identical shim tree)
# ---------------------------------------------------------------------------


def test_policy_module_is_mirrored_to_the_shim_tree():
    canonical = _REPO_ROOT / "icdev" / "tools" / "ace" / "tool_trust_policy.py"
    shim = _REPO_ROOT / "tools" / "ace" / "tool_trust_policy.py"
    assert shim.exists(), "tool_trust_policy.py missing from tools/ace/"
    assert canonical.read_bytes() == shim.read_bytes()


def test_packaged_role_copy_matches_the_source_copy():
    packaged = _REPO_ROOT / "icdev" / "data" / "args" / "ace" / "roles" / "qa_agent.yaml"
    assert packaged.read_bytes() == _QA_AGENT.read_bytes()
