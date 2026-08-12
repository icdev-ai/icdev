# CUI // SP-CTI
"""hgx-guard-01 — the pre-tool safety checks have exactly one implementation.

``.claude/hooks/pre_tool_use.py`` ran eight blocking checks;
``tools/airgap/hook_compat.run_pre_tool_check`` — the function the standalone
agent runtime and every non-Claude-Code orchestrator calls — ran two. Neither
path was a superset of the other, so an agent running OUTSIDE Claude Code was
less guarded than one inside it.

These tests pin the extraction: both paths load ``tools/hooks/shared_checks``,
the Claude Code hook still blocks exactly what it blocked before, and no check
resolves a path from ``os.getcwd()`` (they run from git worktrees, where cwd is
not the repo root).

Bringing the headless path to full parity is hgx-guard-02 — deliberately NOT
asserted here, because this task must not change what either side blocks.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PATH = REPO_ROOT / "tools" / "hooks" / "shared_checks.py"
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shared():
    return _load("hgx_shared_checks", SHARED_PATH)


@pytest.fixture(scope="module")
def hook():
    return _load("hgx_pre_tool_use", HOOK_PATH)


# ── the module exists and both paths call it ──────────────────────────────


def test_shared_module_exists():
    assert SHARED_PATH.is_file(), "tools/hooks/shared_checks.py must exist"
    assert (REPO_ROOT / "icdev" / "tools" / "hooks" / "shared_checks.py").is_file(), (
        "tools/ modules must be mirrored to icdev/tools/"
    )


def test_claude_code_hook_calls_the_shared_module(hook):
    assert hook.shared_checks.__file__ == str(SHARED_PATH)
    # Not a copy of the logic — the same objects.
    assert hook.is_dangerous_rm_command is hook.shared_checks.is_dangerous_rm_command
    assert hook.is_env_file_access is hook.shared_checks.is_env_file_access
    assert hook.is_direct_sqlite_usage is hook.shared_checks.is_direct_sqlite_usage


def test_headless_path_calls_the_shared_module():
    from tools.airgap import hook_compat

    assert hook_compat.shared_checks.git_danger_reason is not None
    assert hook_compat._check_git_danger is hook_compat.shared_checks.git_danger_reason


def test_hook_defines_no_second_copy_of_a_check(hook):
    """The hook may hold DATA (APPEND_ONLY_TABLES) but not check LOGIC.

    Its remaining functions must all be one-liner delegations, so a future edit
    cannot reintroduce a Claude-only rule the headless path never sees.
    """
    tree = ast.parse(HOOK_PATH.read_text(encoding="utf-8"))
    delegating = {
        "is_append_only_table_modification",
        "check_file_access_tiers",
        "run_review_loop_precommit",
        "check_worktree_path",
        "check_branch_deletion",
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in delegating:
            source = ast.dump(node)
            assert "shared_checks" in source, (
                f"{node.name} must delegate to shared_checks, not reimplement the check"
            )


# ── no check reads os.getcwd() ────────────────────────────────────────────


def _attr_chain(node: ast.AST) -> str:
    """Dotted name of an attribute/name expression, e.g. ``os.getcwd``."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


@pytest.mark.parametrize("path", [SHARED_PATH, HOOK_PATH], ids=["shared", "hook"])
def test_no_check_reads_cwd(path):
    """These run from worktrees; cwd is the worktree root, not the repo root.

    Checked against the AST, not the text — the modules DOCUMENT the hazard, so
    a substring search would only ever find the explanation of the rule.
    """
    forbidden = {"os.getcwd", "os.curdir", "Path.cwd", "pathlib.Path.cwd"}
    offenders = [
        _attr_chain(node.func)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _attr_chain(node.func) in forbidden
    ]
    assert not offenders, f"{path.name} resolves paths from {offenders}"


def test_repo_root_resolves_from_file(shared):
    root = shared.default_repo_root()
    assert (root / "tools" / "hooks" / "shared_checks.py").is_file()
    assert root == REPO_ROOT


# ── the eight checks still block what they blocked ────────────────────────


@pytest.mark.parametrize(
    "tool_name,tool_input,blocked",
    [
        ("Read", {"file_path": ".env"}, True),
        ("Write", {"file_path": "/repo/.env"}, True),
        ("Read", {"file_path": ".env.sample"}, False),
        ("Read", {"file_path": "src/main.py"}, False),
        ("Bash", {"command": "cat .env"}, True),
        ("Bash", {"command": "ls -la"}, False),
    ],
)
def test_env_file_access(shared, tool_name, tool_input, blocked):
    assert bool(shared.check_env_file_access(tool_name, tool_input)) is blocked


@pytest.mark.parametrize(
    "command,blocked",
    [
        ("rm -rf /", True),
        ("rm -fr ~", True),
        ("rm --recursive --force build", True),
        ("rm -r . -f", True),
        ("rm file.txt", False),
        ("rmdir build", False),
    ],
)
def test_dangerous_rm(shared, command, blocked):
    assert bool(shared.check_dangerous_rm("Bash", {"command": command})) is blocked
    # Non-Bash tools never trip this check.
    assert shared.check_dangerous_rm("Read", {"command": command}) is None


@pytest.mark.parametrize(
    "command,blocked",
    [
        ("DELETE FROM audit_trail WHERE id=1", True),
        ("UPDATE hook_events SET x=1", True),
        ("DROP TABLE audit_trail", True),
        ("TRUNCATE audit_trail", True),
        ("SELECT * FROM audit_trail", False),
        ("INSERT INTO audit_trail VALUES (1)", False),
        ("delete from kanban_tasks", False),
    ],
)
def test_append_only_write(shared, command, blocked):
    tables = ["audit_trail", "hook_events"]
    reason = shared.check_append_only_write("Bash", {"command": command}, tables)
    assert bool(reason) is blocked


def test_append_only_pattern_cache_is_keyed_by_table_list(shared):
    """Two callers pass two different lists in one process — hgx-guard-02
    reconciles them, but until then the cache must not serve one list's
    patterns to the other."""
    wide = ["audit_trail", "hook_events"]
    narrow = ["hook_events"]
    call = {"command": "delete from audit_trail"}
    assert shared.is_append_only_table_modification("Bash", call, wide) is True
    assert shared.is_append_only_table_modification("Bash", call, narrow) is False
    assert shared.is_append_only_table_modification("Bash", call, wide) is True


def test_find_append_only_table_names_the_table(shared):
    tables = ["audit_trail", "hook_events"]
    assert shared.find_append_only_table("DELETE FROM hook_events", tables) == "hook_events"
    # The headless shape is UPDATE/DELETE only — DROP is not in its vocabulary.
    assert shared.find_append_only_table("DROP TABLE hook_events", tables) is None
    assert shared.find_append_only_table("SELECT 1", tables) is None


@pytest.mark.parametrize(
    "tool_name,tool_input,blocked",
    [
        ("Edit", {"file_path": "tools/foo/bar.py", "new_string": "sqlite3.connect('x')"}, True),
        ("Write", {"file_path": "tools/foo/bar.py", "content": "sqlite3.connect('x')"}, True),
        ("Edit", {"file_path": "tools/db/storage.py", "new_string": "sqlite3.connect('x')"}, False),
        ("Edit", {"file_path": "tools/canvas/init_db.py", "new_string": "sqlite3.connect('x')"}, False),
        ("Edit", {"file_path": "apps/x/y.py", "new_string": "sqlite3.connect('x')"}, False),
        ("Edit", {"file_path": "tools/foo/bar.py", "new_string": "get_connection()"}, False),
    ],
)
def test_direct_sqlite_usage(shared, tool_name, tool_input, blocked):
    assert bool(shared.check_direct_sqlite_usage(tool_name, tool_input)) is blocked


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git worktree add /tmp/x -b feat/y", "/tmp/x"),
        ("git worktree add -b feat/y /tmp/x", "/tmp/x"),
        ("git worktree add --detach /tmp/z", "/tmp/z"),
        ("git worktree list", None),
        ("git status", None),
    ],
)
def test_worktree_add_target(shared, command, expected):
    assert shared.worktree_add_target(command, posix=True) == expected


def test_worktree_add_target_is_two_sided_on_platform(shared):
    """Neither shlex mode is correct on both platforms — pick by OS, and keep
    both branches reachable from a single host."""
    win = "git worktree add C:\\Users\\u\\wt"
    assert shared.worktree_add_target(win, posix=False) == "C:\\Users\\u\\wt"
    assert shared.worktree_add_target("git worktree add /tmp/my\\ dir", posix=True) == "/tmp/my dir"


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git push origin --delete feat/x", ["feat/x"]),
        ("git push origin :feat/x", ["feat/x"]),
        ("gh api -X DELETE repos/o/r/git/refs/heads/feat/x", ["feat/x"]),
        ("git push origin main", []),
        # A LOCAL delete cannot close a PR, so it is deliberately not a target.
        ("git branch -D feat/x", []),
    ],
)
def test_remote_branch_delete_targets(shared, command, expected):
    assert shared.remote_branch_delete_targets(command) == expected


def test_guards_are_disablable_and_fail_open(shared, monkeypatch):
    call = ("Bash", {"command": "git worktree add /definitely/not/sanctioned"})
    monkeypatch.setenv("ICDEV_WORKTREE_GUARD", "0")
    assert shared.check_worktree_path(*call) is None
    monkeypatch.setenv("ICDEV_BRANCH_DELETE_GUARD", "0")
    assert shared.check_branch_deletion(
        "Bash", {"command": "git push origin --delete anything"}) is None


def test_review_loop_precommit_only_fires_on_git_commit(shared):
    assert shared.check_review_loop_precommit("Bash", {"command": "git status"}) is None
    assert shared.check_review_loop_precommit("Read", {"file_path": "x"}) is None


def test_review_loop_precommit_respects_its_off_switch(shared, monkeypatch):
    monkeypatch.setenv("ICDEV_REVIEW_LOOP_PRECOMMIT", "0")
    assert shared.check_review_loop_precommit("Bash", {"command": "git commit -m x"}) is None


# ── redirect targets feeding the D-ORCH-8 file tiers (exa-bench-06) ───────
#
# `_REDIRECT_TARGET_RE` was `>\s*([^\s|;&]+)`. Against
# `echo pubkey >> ~/.ssh/authorized_keys` the first `>` matched, `\s*` matched
# nothing, and the capture group took the SECOND `>` — so `file_path` became the
# literal string ">", which matches no tier pattern and the append was ALLOWED.
# The single-`>` form of the very same command was correctly blocked.


@pytest.mark.parametrize(
    "command,expected",
    [
        # The bug: append forms resolved to ">" instead of the real path.
        ("echo k >> ~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
        ("echo k>>~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
        ("cmd 1>> out.log", ["out.log"]),
        ("cmd 2>> err.log", ["err.log"]),
        ("cmd &>> all.log", ["all.log"]),
        # Already worked — must keep working.
        ("echo k > ~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
        ("cmd 2> err.log", ["err.log"]),
        ("cmd &> all.log", ["all.log"]),
        ("> fresh.txt", ["fresh.txt"]),
        # tee writes with no redirection operator at all.
        ("echo k | tee ~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
        ("echo k | tee -a ~/.ssh/authorized_keys", ["~/.ssh/authorized_keys"]),
        # fd duplication is NOT a write to a file named "1".
        ("make 2>&1", []),
        ("make 2>&1 | tee build.log", ["build.log"]),
        # Every target, not just the first.
        ("echo hi > notes.md 2>> ~/.ssh/err", ["notes.md", "~/.ssh/err"]),
        # Ordinary commands name nothing.
        ("git status", []),
        ("python -m pytest tests/ -q", []),
    ],
)
def test_bash_redirect_targets_resolve_to_the_real_path(shared, command, expected):
    writes = [p for p, is_write, _ in shared.bash_file_targets(command) if is_write]
    assert writes == expected
    assert ">" not in writes, "the capture group took the operator, not the path"


@pytest.mark.parametrize(
    "command",
    [
        "echo pubkey >> ~/.ssh/authorized_keys",   # the reported bypass
        "echo pubkey > ~/.ssh/authorized_keys",
        "echo pubkey >>~/.ssh/authorized_keys",
        "cat id >> /home/victim/.ssh/id_rsa",
        "echo k | tee -a ~/.ssh/authorized_keys",
        "cmd 2>> ~/.ssh/config",
        "echo hi > notes.md 2>> ~/.ssh/authorized_keys",  # second target counts
    ],
)
def test_append_redirect_into_a_zero_access_path_is_blocked(shared, command):
    """The acceptance criterion: `>>` reaches the tiers like `>` always did."""
    reason = shared.check_file_access_tiers("Bash", {"command": command})
    assert reason, f"{command!r} wrote into a zero_access path and was ALLOWED"
    assert "zero_access" in reason


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/ -q > /tmp/out.txt",
        "make 2>&1 | tee build.log",
        "echo '# notes' >> README.md",
        "git status",
    ],
)
def test_ordinary_redirects_are_still_allowed(shared, command):
    """Widening the pattern must not start blocking real work."""
    assert shared.check_file_access_tiers("Bash", {"command": command}) is None


# ── destructive git blocklist (both paths since exa-bench-06) ─────────────


@pytest.mark.parametrize(
    "command,blocked",
    [
        ("git push origin main --force", True),
        ("git push -f origin feature", True),
        ("git reset --hard HEAD~1", True),
        ("git reset --soft HEAD~1", False),
        ("git branch -D feature-xyz", True),
        ("git branch -d already-merged", False),
        ("git clean -fd", True),
        ("git clean -n", False),
        ("git checkout .", True),
        ("git checkout main", False),
        ("git rebase -i HEAD~5", True),
        ("GIT PUSH --FORCE origin main", True),
        ("git status", False),
    ],
)
def test_git_danger(shared, command, blocked):
    assert bool(shared.git_danger_reason(command)) is blocked
    assert bool(shared.check_git_danger("Bash", {"command": command})) is blocked
