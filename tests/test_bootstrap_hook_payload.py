# CUI // SP-CTI
"""A project scaffolded by `icdev init` must actually get a PreToolUse guard.

THE BUG (exa-bench-10)
======================
`claude/hooks/pre_tool_use.py` resolves its implementation as
``REPO_ROOT / "tools" / "hooks" / "shared_checks.py"`` and loads it with
``spec_from_file_location`` + ``exec_module``. In this repo that file exists. In
a project scaffolded by `icdev init` it does not — the bootstrap payload shipped
no ``tools/`` tree at all — so the hook raised ``FileNotFoundError`` and exited
non-zero on EVERY tool call.

What made it invisible: the loader is deliberately NOT wrapped in try/except
("a guard that cannot load must fail loudly, not silently stop guarding"), but
the generated ``settings.json`` wraps every hook in ``|| true``, so the shell
returned 0 and the failure never surfaced. Every `icdev init` project believed
it had a guard and had none.

These tests scaffold into a temp directory and run the hook the way Claude Code
runs it — the only check that would have caught this.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_HOOKS = REPO_ROOT / "icdev" / "data" / "claude_bootstrap" / "claude" / "hooks"

checker = importlib.import_module("tools.workflow.coherence_checker")
init_mod = importlib.import_module("tools.cli.init")


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> Path:
    """A real `icdev init` into a clean directory. Module-scoped — it copies ~600 files."""
    target = tmp_path_factory.mktemp("icdev-scaffold")
    result = init_mod.init_project(target, force=True, profile=None)
    assert result["missing"] == 0, f"scaffold incomplete: {result['actions']}"
    return target


def _run_hook(hook: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------- #
# The hook loads, and still guards, in a scaffolded project
# --------------------------------------------------------------------------- #


def test_hook_ships_with_the_scaffold(scaffold: Path):
    assert (scaffold / ".claude" / "hooks" / "pre_tool_use.py").is_file()


def test_hook_loads_and_allows_a_benign_tool_call(scaffold: Path):
    """Exit 0 and no traceback. This is the regression: it used to exit 1, always."""
    proc = _run_hook(
        scaffold / ".claude" / "hooks" / "pre_tool_use.py",
        {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "FileNotFoundError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"stderr: {proc.stderr}"


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, "rm"),
        ({"tool_name": "Read", "tool_input": {"file_path": ".env"}}, ".env"),
    ],
)
def test_hook_still_blocks_in_a_scaffolded_project(scaffold: Path, payload, fragment):
    """Loading is not enough — a hook that loads and refuses nothing is the same defect."""
    proc = _run_hook(scaffold / ".claude" / "hooks" / "pre_tool_use.py", payload)
    assert proc.returncode == 2, f"expected block, got {proc.returncode}: {proc.stderr}"
    assert fragment in proc.stderr


def test_self_test_reports_which_checks_are_inert(scaffold: Path):
    """The subset is MEASURED, not claimed.

    Three checks need ICDEV's own ``tools/`` modules and cannot run in a
    generated project. They fail open inside shared_checks, which is correct and
    completely invisible — so the hook names them.
    """
    proc = subprocess.run(
        [sys.executable, str(scaffold / ".claude" / "hooks" / "pre_tool_use.py"),
         "--self-test"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    status = json.loads(proc.stdout)
    inert = {entry["check"] for entry in status["inert"]}
    assert inert == {
        "check_worktree_path",
        "check_agent_rules",
        "check_review_loop_precommit",
    }, f"unexpected inert set: {inert}"
    # The portable ones must genuinely be reported active, or the subset claim
    # in docs/features/exa-bench-10-packaged-hook-payload.md is fiction.
    for portable in ("check_env_file_access", "check_dangerous_rm",
                     "check_git_danger", "check_append_only_write",
                     "check_direct_sqlite_usage", "check_branch_deletion"):
        assert portable in status["active"]


# --------------------------------------------------------------------------- #
# The gate that keeps it fixed
# --------------------------------------------------------------------------- #


def test_every_declared_payload_module_shipped():
    """Derived from the packaged hooks' own PAYLOAD_MODULES, not from a list here."""
    declared = checker._packaged_hook_payload_modules()
    assert declared, "no packaged hook declares PAYLOAD_MODULES — the rule checks nothing"
    for hook_name, module in declared:
        packaged = PACKAGED_HOOKS / module
        assert packaged.is_file(), (
            f"claude/hooks/{hook_name} loads {module} by path but it did not ship"
        )
        source = REPO_ROOT / "tools" / "hooks" / module
        assert source.read_bytes() == packaged.read_bytes(), (
            f"packaged {module} is stale — run tools/installer/prebuild_bootstrap.py"
        )


def test_payload_rule_is_green_on_the_tree_as_committed():
    assert checker._bootstrap_payload_defects() == []
    assert checker.check_bootstrap_parity().status == "pass"


def test_gate_fails_when_a_payload_module_is_absent(tmp_path, monkeypatch):
    """Prove the gate can go red — a rule that cannot fail is not a gate."""
    fake_hooks = tmp_path / "hooks"
    fake_hooks.mkdir()
    (fake_hooks / "pre_tool_use.py").write_text(
        'PAYLOAD_MODULES = ("shared_checks.py",)\n', encoding="utf-8"
    )
    monkeypatch.setattr(checker, "_BOOTSTRAP_HOOKS_DIR", fake_hooks)

    defects = checker._bootstrap_payload_defects()
    assert len(defects) == 1
    assert "shared_checks.py" in defects[0]
    assert checker.check_bootstrap_parity().status == "fail"


def test_gate_fails_when_a_payload_module_is_stale(tmp_path, monkeypatch):
    fake_hooks = tmp_path / "hooks"
    fake_hooks.mkdir()
    (fake_hooks / "pre_tool_use.py").write_text(
        'PAYLOAD_MODULES = ("shared_checks.py",)\n', encoding="utf-8"
    )
    (fake_hooks / "shared_checks.py").write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(checker, "_BOOTSTRAP_HOOKS_DIR", fake_hooks)

    defects = checker._bootstrap_payload_defects()
    assert len(defects) == 1
    assert "stale implementation" in defects[0]
