# CUI // SP-CTI
"""hgx-guard-02 — headless guardrail parity with the Claude Code hook.

Three properties, all driven off the shared registry rather than off a
hand-written list of cases, so the two paths cannot silently diverge:

1. Every check the Claude Code hook runs, the headless path runs too. Asserted
   over ``shared_checks.CHECKS`` itself, so adding a Claude-Code-only check
   fails this test rather than quietly reopening the gap.
2. For a corpus of blocked calls, the headless path blocks whatever the Claude
   Code hook blocks — verified by actually running ``.claude/hooks/
   pre_tool_use.py`` as a subprocess and comparing exit codes.
3. An unrecognised mutating tool name is scanned, not allowed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOOK = ROOT / ".claude" / "hooks" / "pre_tool_use.py"
SHARED_TOOLS = ROOT / "tools" / "hooks" / "shared_checks.py"
SHARED_ICDEV = ROOT / "icdev" / "tools" / "hooks" / "shared_checks.py"


def _load_shared():
    spec = importlib.util.spec_from_file_location("icdev_shared_checks_test", SHARED_TOOLS)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules[cls.__module__].
    sys.modules["icdev_shared_checks_test"] = module
    spec.loader.exec_module(module)
    return module


checks = _load_shared()

from tools.airgap import hook_compat  # noqa: E402


# Calls that MUST be refused. Kept small and behavioural; the exhaustive
# per-check cases live with their own checks.
BLOCKED_CALLS = [
    ("Bash", {"command": "rm -rf /"}),
    ("Bash", {"command": "rm -rf ~/"}),
    ("Read", {"file_path": "/repo/.env"}),
    ("Write", {"file_path": ".env", "content": "SECRET=1"}),
    ("Bash", {"command": 'psql -c "DELETE FROM audit_trail WHERE id > 0"'}),
    ("Bash", {"command": "sqlite3 data/icdev.db 'DROP TABLE hook_events'"}),
    ("Write", {"file_path": "tools/foo/bar.py", "content": "import sqlite3\nsqlite3.connect('x')\n"}),
    ("Bash", {"command": "git worktree add -b feat/x /nowhere/bogus origin/main"}),
]

ALLOWED_CALLS = [
    ("Bash", {"command": "git status"}),
    ("Bash", {"command": "ls -la"}),
    ("Read", {"file_path": "README.md"}),
    ("Grep", {"pattern": "def foo", "path": "tools"}),
    ("Write", {"file_path": "docs/notes.md", "content": "hello"}),
]


def _run_claude_hook(tool_name: str, tool_input: dict) -> int:
    """Exit code of the real Claude Code hook. 2 = blocked, 0 = allowed."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
        shell=False,
    )
    return proc.returncode


# ── 1. Registry-level parity ───────────────────────────────────────────────


def test_headless_runs_every_claude_code_check():
    """No check may be Claude-Code-only.

    This is the anti-divergence invariant: the headless path must be a superset.
    A new check that forgets ``PATH_HEADLESS`` fails here instead of silently
    leaving unattended agents less guarded than interactive ones.
    """
    claude_only = [
        c.name for c in checks.CHECKS
        if checks.PATH_CLAUDE_CODE in c.paths and checks.PATH_HEADLESS not in c.paths
    ]
    assert claude_only == [], (
        f"checks run under Claude Code but not headlessly: {claude_only}"
    )


def test_every_check_declares_at_least_one_path():
    orphans = [c.name for c in checks.CHECKS if not c.paths]
    assert orphans == [], f"checks that run nowhere: {orphans}"


def test_headless_selection_covers_all_checks():
    selected = {c.name for c in checks.checks_for(checks.PATH_HEADLESS)}
    assert selected == {c.name for c in checks.CHECKS}


def test_git_danger_is_headless_only_by_declaration():
    """The one asymmetry is deliberate and opt-in, not an oversight."""
    git = next(c for c in checks.CHECKS if c.name == "git_danger")
    assert checks.PATH_CLAUDE_CODE not in git.paths
    assert git.opt_in_env == "ICDEV_GIT_DANGER_GUARD"


def test_git_danger_opt_in_reaches_the_claude_code_path(monkeypatch):
    monkeypatch.setenv("ICDEV_GIT_DANGER_GUARD", "1")
    names = {c.name for c in checks.checks_for(checks.PATH_CLAUDE_CODE)}
    assert "git_danger" in names


# ── 2. Behavioural parity against the real hook ────────────────────────────


@pytest.mark.parametrize("tool_name,tool_input", BLOCKED_CALLS)
def test_headless_blocks_everything_the_hook_blocks(tool_name, tool_input):
    hook_blocked = _run_claude_hook(tool_name, tool_input) == 2
    headless = hook_compat.run_pre_tool_check(tool_name, tool_input)
    if hook_blocked:
        assert headless["allowed"] is False, (
            f"{tool_name} {tool_input} is blocked by .claude/hooks/pre_tool_use.py "
            f"but allowed headlessly — the headless path is weaker."
        )


@pytest.mark.parametrize("tool_name,tool_input", BLOCKED_CALLS)
def test_blocked_calls_are_actually_blocked_on_both_paths(tool_name, tool_input):
    assert _run_claude_hook(tool_name, tool_input) == 2
    assert hook_compat.run_pre_tool_check(tool_name, tool_input)["allowed"] is False


@pytest.mark.parametrize("tool_name,tool_input", ALLOWED_CALLS)
def test_ordinary_calls_are_allowed_on_both_paths(tool_name, tool_input):
    assert _run_claude_hook(tool_name, tool_input) == 0
    assert hook_compat.run_pre_tool_check(tool_name, tool_input)["allowed"] is True


# ── 3. Unknown tools are scanned, not trusted ──────────────────────────────


UNKNOWN_MUTATORS = [
    ("TotallyUnknownMutator", {"command": "rm -rf /"}),
    ("mcp__somesrv__run_shell", {"command": "rm -rf ~"}),
    ("QueryRunner", {"sql": "DELETE FROM audit_trail"}),
    ("db_exec", {"query": "DROP TABLE hook_events"}),
    ("Bash2", {"command": 'psql -c "TRUNCATE audit_trail"'}),
    ("write_file", {"file_path": ".env", "content": "K=v"}),
]


@pytest.mark.parametrize("tool_name,tool_input", UNKNOWN_MUTATORS)
def test_unrecognised_mutating_tool_is_scanned_not_allowed(tool_name, tool_input):
    """The six-name short-circuit is gone.

    ``run_pre_tool_check`` used to return allowed for any tool whose name was
    not one of ("Bash","bash","shell","sql","Write","Edit"), so every one of
    these was waved through unscanned.
    """
    result = hook_compat.run_pre_tool_check(tool_name, tool_input)
    assert result["allowed"] is False, f"{tool_name} was waved through: {result}"
    assert result["check"], "a block must name the check that produced it"


@pytest.mark.parametrize("tool_name,tool_input", UNKNOWN_MUTATORS)
def test_unknown_mutators_are_blocked_on_the_claude_code_path_too(tool_name, tool_input):
    assert _run_claude_hook(tool_name, tool_input) == 2


def test_unknown_read_only_tool_stays_allowed():
    """Scanning unknown tools must not turn every unfamiliar name into a block."""
    result = hook_compat.run_pre_tool_check("mcp__docs__lookup", {"topic": "nist"})
    assert result["allowed"] is True


def test_classification_of_an_unknown_tool_uses_input_shape():
    call = checks.classify("some_new_tool", {"query": "SELECT 1", "file_path": "a.py"})
    assert call.recognised is False
    assert call.command == "SELECT 1"
    assert call.file_path == "a.py"


# ── Single source of truth ─────────────────────────────────────────────────


def test_shared_checks_mirror_is_byte_identical():
    """tools/ and icdev/tools/ copies must not drift.

    Both hook paths resolve to one of these two files, so a divergence here is
    exactly the failure this card exists to prevent.
    """
    assert SHARED_ICDEV.exists(), f"missing mirror: {SHARED_ICDEV}"
    assert SHARED_TOOLS.read_bytes() == SHARED_ICDEV.read_bytes(), (
        "tools/hooks/shared_checks.py and icdev/tools/hooks/shared_checks.py "
        "have diverged — re-run the mirror sync."
    )


def test_package_import_and_file_load_yield_the_same_rules():
    """The two load mechanisms must resolve to one set of rules.

    The Claude Code hook loads ``shared_checks`` from disk with importlib (it
    cannot afford the ~80 ms ``import tools`` costs on every tool call), while
    ``hook_compat`` imports ``tools.hooks.shared_checks`` normally. If those
    ever resolved to different files with different contents, the divergence
    this card closes would be back.
    """
    from tools.hooks import shared_checks as imported

    assert [c.name for c in imported.CHECKS] == [c.name for c in checks.CHECKS]
    assert set(imported.APPEND_ONLY_TABLES) == set(checks.APPEND_ONLY_TABLES)
    assert (
        pathlib.Path(imported.__file__).read_bytes() == SHARED_TOOLS.read_bytes()
    ), "tools.hooks.shared_checks resolves to a file that differs from the one the hook loads"


def test_hook_compat_reuses_the_shared_table_list():
    """The headless copy of APPEND_ONLY_TABLES is gone, not merely updated."""
    assert hook_compat.APPEND_ONLY_TABLES is checks.APPEND_ONLY_TABLES or (
        set(hook_compat.APPEND_ONLY_TABLES) == set(checks.APPEND_ONLY_TABLES)
    )
    # The old hand-maintained list held 22 entries against the hook's 350+.
    assert len(set(hook_compat.APPEND_ONLY_TABLES)) > 300


def test_claude_hook_holds_no_second_copy_of_the_rules():
    """The hook is an adapter; a re-introduced literal would be a fork."""
    source = HOOK.read_text(encoding="utf-8")
    assert "APPEND_ONLY_TABLES = [" not in source
    assert "shared_checks" in source


# ── post_tool_use / stop have headless implementations ─────────────────────


def test_post_tool_use_has_a_headless_implementation():
    assert callable(getattr(hook_compat, "run_post_tool_check", None))
    result = hook_compat.run_post_tool_check(
        "Read", tool_input={"file_path": "README.md"}, tool_output="body", is_error=False
    )
    assert set(result) >= {"recorded", "event_id", "dispatched"}


def test_post_tool_use_never_raises_on_bad_input():
    assert hook_compat.run_post_tool_check(
        "X", tool_input=None, tool_output=None, is_error=True
    ) is not None


def test_stop_has_a_headless_implementation():
    assert callable(getattr(hook_compat, "run_stop_check", None))
    result = hook_compat.run_stop_check(reason="completed")
    assert set(result) >= {"recorded", "event_id", "auto_commit"}
    # ICDEV_AUTO_COMMIT is off by default; the stop hook must not commit.
    assert result["auto_commit"]["committed"] is False


def test_agent_loop_hooks_match_the_loop_slots():
    """The loop's on_post_tool_use / on_stop slots now have something to bind."""
    hooks = hook_compat.agent_loop_hooks()
    assert set(hooks) == {"on_pre_tool_use", "on_post_tool_use", "on_stop"}

    # PreToolUseHook: (name, input) -> str | None
    assert hooks["on_pre_tool_use"]("Bash", {"command": "git status"}) is None
    blocked = hooks["on_pre_tool_use"]("Bash", {"command": "rm -rf /"})
    assert isinstance(blocked, str) and blocked.startswith("BLOCKED")

    # PostToolUseHook: (name, input, result_text, is_error) -> None
    assert hooks["on_post_tool_use"]("Read", {"file_path": "a"}, "out", False) is None


def test_agent_loop_stop_hook_accepts_an_agent_loop_result():
    from icdev.tools.llm.agent_loop import AgentLoopResult

    hooks = hook_compat.agent_loop_hooks()
    assert hooks["on_stop"](AgentLoopResult(done=True, turns=3, result_subtype="success")) is None


# ── A generated child app inherits a hook that actually guards ─────────────


def test_child_apps_inherit_the_shared_checks_module():
    """``tools/hooks`` must ship with every generated child app.

    ``child_app_generator`` copies ``.claude/hooks/pre_tool_use.py`` into each
    child. That hook is now a thin adapter that FAILS OPEN when it cannot load
    ``shared_checks`` — so without this directory a child app would ship a hook
    that guards nothing at all, which is strictly worse than the fat hook it
    replaced.
    """
    from tools.builder.child_app_generator import DIRECTORY_TREE, PARENT_ONLY_DIRS

    assert "tools/hooks" in DIRECTORY_TREE
    assert "tools/hooks" not in PARENT_ONLY_DIRS


def _attr_chain(node) -> str:
    """Dotted name of an attribute/name expression, e.g. ``os.getcwd``."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


@pytest.mark.parametrize("path", [SHARED_TOOLS, HOOK], ids=["shared", "hook"])
def test_no_check_resolves_paths_from_cwd(path):
    """Both guards run from worktrees, where cwd is the worktree root.

    A check that resolved ``args/file_access_tiers.yaml`` or an exemption list
    against cwd would silently apply the wrong rules — or none. Asserted against
    the AST rather than the text because both modules *document* the hazard, so
    a substring search would only ever match the explanation of the rule.
    """
    forbidden = {"os.getcwd", "os.curdir", "Path.cwd", "pathlib.Path.cwd"}
    offenders = [
        _attr_chain(node.func)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _attr_chain(node.func) in forbidden
    ]
    assert not offenders, f"{path.name} resolves paths from {offenders}"


def test_repo_root_resolves_from_file_not_cwd():
    root = checks.repo_root()
    assert (root / "tools" / "hooks" / "shared_checks.py").is_file()
    assert root == ROOT


def test_the_adapter_hook_fails_open_only_loudly():
    """Fail-open is deliberate, but it must announce itself on stderr.

    A guard that silently degrades to "allow everything" is indistinguishable
    from a working one.
    """
    source = HOOK.read_text(encoding="utf-8")
    assert "UNGUARDED" in source, "the fail-open path must warn that it is unguarded"
