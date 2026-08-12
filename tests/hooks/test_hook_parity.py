# CUI // SP-CTI
"""exa-bench-06 — the two guard paths must run the SAME check set.

hgx-guard-01 moved every check into ``tools/hooks/shared_checks.py`` so the
Claude Code hook and the headless path "cannot drift apart". They still did, in
the one direction nothing was watching: a check can live in shared_checks, be
listed in ``hook_compat.HEADLESS_CHECKS``, and simply never be called from
``.claude/hooks/pre_tool_use.py::main()``.

``check_git_danger`` was exactly that for a whole slice. Measured consequence:
``git reset --hard origin/main`` and ``git clean -fdx`` were REFUSED headlessly
and ALLOWED in a Claude Code session — backwards, because the Claude Code session
is the one the ``--dangerously-skip-permissions`` adapter spawns with the vendor
permission system turned off (D394, docs/security/agent-vendor-permission-bypass.md).

``tests/hooks/test_headless_parity.py`` covers the headless list against
shared_checks. This module covers the OTHER side: the hook against the headless
list, and the hook's declaration against the hook's own code.

No database and no LLM — the hook is a subprocess reading JSON on stdin.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.airgap import hook_compat
from tools.hooks import shared_checks

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"


def _load_hook():
    """Import the hook module by path — it is not on any package path."""
    spec = importlib.util.spec_from_file_location("icdev_pre_tool_use_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def _run_hook(tool_name: str, tool_input: dict) -> tuple[int, str]:
    """Drive the hook the way Claude Code does: JSON on stdin, decision in the
    exit code. 2 = blocked, 0 = allowed."""
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    return proc.returncode, proc.stderr


# ---------------------------------------------------------------------------
# The parity assertion itself — the acceptance criterion
# ---------------------------------------------------------------------------
class TestTheTwoPathsRunTheSameChecks:
    """A check may not be wired into one path and missing from the other."""

    def test_hook_and_headless_run_the_same_check_set(self):
        """The assertion exa-bench-06 exists to add.

        A SET comparison, not a sequence one: the two paths order the middle
        checks differently (both put the cheap refusals first and the additive
        AGOV rule engine last, which is what actually matters), and pinning the
        order would fail for a difference that costs nothing.
        """
        hook_set = set(hook.HOOK_CHECKS)
        headless_set = set(hook_compat.HEADLESS_CHECKS)

        assert hook_set == headless_set, (
            "the Claude Code hook and the headless path no longer run the same "
            f"checks.\n  only in .claude/hooks/pre_tool_use.py: "
            f"{sorted(hook_set - headless_set)}\n  only in "
            f"hook_compat.HEADLESS_CHECKS: {sorted(headless_set - hook_set)}\n"
            "Wire the missing one into BOTH — a check that exists but is never "
            "called is the failure mode this test exists to catch."
        )

    def test_both_paths_cover_every_check_shared_checks_exposes(self):
        """Neither list may quietly omit a check that exists.

        Set-equal-to-each-other is satisfiable by two lists that are both wrong,
        so anchor them to the implementation as well.
        """
        available = {n for n in dir(shared_checks) if n.startswith("check_")}
        assert set(hook.HOOK_CHECKS) == available, (
            "shared_checks exposes checks the Claude Code hook never runs: "
            f"{sorted(available - set(hook.HOOK_CHECKS))}"
        )

    def test_agov_rules_run_after_every_hardcoded_block_in_both_paths(self):
        """agov-det-06: the data-driven check is additive, so it decides last.

        Pinned on both paths — it can only ever add a refusal to a call the
        hardcoded checks already allowed, never wave one through.
        """
        assert hook_compat.HEADLESS_CHECKS[-1] == "check_agent_rules"
        hardcoded = [c for c in hook.HOOK_CHECKS if c != "check_review_loop_precommit"]
        assert hardcoded[-1] == "check_agent_rules", (
            "check_agent_rules must be the last BLOCKING check in the hook; "
            "check_review_loop_precommit is warn-only and runs after it."
        )


class TestTheDeclarationIsNotALie:
    """HOOK_CHECKS is hand-maintained, so prove it describes the code.

    Without this, exa-bench-06 would have replaced a check that silently did not
    run with a list entry that silently did not correspond to anything.
    """

    def test_every_declared_check_has_a_callsite_mapping(self):
        assert set(hook.HOOK_CHECKS) == set(hook.HOOK_CHECK_CALLSITES), (
            "HOOK_CHECKS and HOOK_CHECK_CALLSITES disagree — every declared "
            "check needs the identifier main() calls it by."
        )

    @pytest.mark.parametrize("check", sorted(hook.HOOK_CHECKS))
    def test_every_declared_check_is_reached_from_main(self, check):
        """main() calls several checks through their ``is_*`` predicate rather
        than the ``check_*`` wrapper, so the mapping is what makes this
        checkable at all."""
        source = inspect.getsource(hook.main)
        callsite = hook.HOOK_CHECK_CALLSITES[check]
        assert f"{callsite}(" in source, (
            f"{check} is declared in HOOK_CHECKS but main() never calls "
            f"{callsite}() — the declaration is describing a check that does "
            "not run, which is the exact bug exa-bench-06 fixed."
        )

    def test_every_mapped_callsite_actually_exists_in_the_hook(self):
        missing = [
            name for name in hook.HOOK_CHECK_CALLSITES.values()
            if not callable(getattr(hook, name, None))
        ]
        assert not missing, f"HOOK_CHECK_CALLSITES names non-existent callables: {missing}"


# ---------------------------------------------------------------------------
# Behavioural proof — the declaration could still be right about dead code
# ---------------------------------------------------------------------------
#: The commands exa-bench-06 measured as ALLOWED by the hook and REFUSED
#: headlessly. Both are in GIT_DANGER_PATTERNS; only the headless path ran it.
GIT_DANGER_CASES = [
    ("hard reset",   "git reset --hard origin/main"),
    ("force clean",  "git clean -fdx"),
    ("force push",   "git push --force origin main"),
    ("branch -D",    "git branch -D feat/something"),
    ("rebase -i",    "git rebase -i HEAD~3"),
]


class TestGitDangerBlocksInBothPaths:
    """The regression that motivated the parity assertion, pinned end to end."""

    @pytest.mark.parametrize(
        "name,command", GIT_DANGER_CASES, ids=[c[0] for c in GIT_DANGER_CASES]
    )
    def test_the_claude_code_hook_blocks_it(self, name, command):
        code, stderr = _run_hook("Bash", {"command": command})
        assert code == 2, (
            f"{name!r} was ALLOWED by .claude/hooks/pre_tool_use.py. This is the "
            "exa-bench-06 regression: check_git_danger is in shared_checks and in "
            "HEADLESS_CHECKS but is not called from main()."
        )
        assert "BLOCKED" in stderr

    @pytest.mark.parametrize(
        "name,command", GIT_DANGER_CASES, ids=[c[0] for c in GIT_DANGER_CASES]
    )
    def test_the_headless_path_blocks_it_too(self, name, command, monkeypatch):
        monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
        result = hook_compat.run_pre_tool_check("Bash", {"command": command})
        assert result["allowed"] is False, f"{name!r} was ALLOWED headlessly"

    def test_ordinary_git_work_is_still_allowed(self):
        """A guard that blocks real work gets disabled, which is worse than none."""
        for command in (
            "git status",
            "git log --oneline -5",
            "git commit -m 'fix: thing'",
            "git push -u origin feat/slug",
            "git branch -d merged/branch",   # -d is safe; only -D is refused
            "git rebase main",               # non-interactive
        ):
            code, stderr = _run_hook("Bash", {"command": command})
            assert code == 0, f"{command!r} was wrongly BLOCKED: {stderr}"
