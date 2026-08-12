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
        # ── catastrophic or unrecoverable targets ──────────────────────────
        ("rm -rf /", True),
        ("rm -fr ~", True),
        ("rm -rf ~/projects", True),
        ("rm -rf $HOME/.ssh", True),
        ("rm -r . -f", True),
        ("rm -rf ./*", True),
        ("rm -rf *", True),
        ("rm -rf ../sibling", True),
        ("rm -rf /etc", True),
        ("rm -rf C:/Users", True),
        # git history is the one thing inside a checkout git cannot restore
        ("rm -rf .git", True),
        ("rm -rf /repo/wt/.git", True),
        # a recursive rm whose target this parser cannot see fails CLOSED
        ("rm -rf", True),
        # ── scoped deletes: recoverable, and 288 of them in 30 days ───────
        # exa-bench-05 changed this case. `rm --recursive --force build` used
        # to refuse on the FLAGS alone, which made the rule "no rm -rf, ever" —
        # and a rule that refuses every scratch cleanup is a rule that has to
        # stay switched off, which is how this hook came to be advisory in the
        # first place. What makes rm -rf dangerous is the target.
        ("rm --recursive --force build", False),
        ("rm -rf .tmp/probe", False),
        ("rm -rf node_modules", False),
        ("rm file.txt", False),
        ("rmdir build", False),
        # ── not an rm at all ───────────────────────────────────────────────
        # `\brm` matches inside `--rm`: every `docker run --rm` in the corpus
        # scored as a dangerous delete.
        ("docker run --rm -v /w:/w -e X=1 python:3.11 bash -c 'pytest -q'", False),
        # the flags of a LATER command are not the flags of an earlier rm
        ("rm -f coverage.json; grep -rln foo tests/", False),
        # echoing a dangerous command is not running it — this is how the
        # corpus tests this very hook
        ("""echo '{"tool_input":{"command":"rm -rf /"}}' | python hook.py""", False),
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


# ── destructive git blocklist (headless path today) ───────────────────────


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


# ── exa-bench-05: shell-aware scanning ────────────────────────────────────
#
# These checks are about to become hard blocks, so what they read as "the
# command" has to be right. Measured over 86,612 real tool calls, whole-string
# matching was the single largest source of false refusals.


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git status", ["git status"]),
        ("cd x && ruff check .", ["cd x", "ruff check ."]),
        ("a; b | c", ["a", "b", "c"]),
        ("a && b || c", ["a", "b", "c"]),
        # a separator inside quotes is data, not a separator
        ("""psql -c "DELETE FROM t WHERE a|b" """, ['psql -c "DELETE FROM t WHERE a|b"']),
        ("echo 'a && b'", ["echo 'a && b'"]),
    ],
)
def test_command_segments(shared, command, expected):
    assert shared.command_segments(command) == expected


def test_heredoc_body_is_data_for_a_writer_and_code_for_an_interpreter(shared):
    """A PR body quoting a dangerous command is not that command.

    ``gh pr create --body "$(cat <<EOF … EOF)"`` and ``git commit -F - <<EOF``
    put prose on the command line. ``python - <<PY`` puts a program there. The
    opener's command word is what tells them apart.
    """
    prose = "git commit -F - <<'EOF'\nfix: stop DELETE FROM audit_trail\nEOF"
    assert "audit_trail" not in shared.strip_heredoc_data(prose)

    program = "python - <<'PY'\nconn.execute('DELETE FROM audit_trail')\nPY"
    assert "audit_trail" in shared.strip_heredoc_data(program)

    # an unterminated heredoc drops to end-of-string rather than raising
    assert shared.strip_heredoc_data("cat > f <<'EOF'\nbody") == "cat > f <<'EOF'"


@pytest.mark.parametrize(
    "segment,expected",
    [
        ("grep -r x .", "grep"),
        ("VAR=1 sudo /usr/bin/grep -r x", "grep"),
        ("  python3 -c 'x'", "python3"),
        ("", ""),
    ],
)
def test_command_word(shared, segment, expected):
    assert shared.command_word(segment) == expected


@pytest.mark.parametrize(
    "command,blocked",
    [
        # real operands
        ("cat .env", True),
        ("cat /repo/wt/.env", True),
        ("head -c 20 .env", True),
        ("echo 'K=v' > .env", True),
        ("printf 'K=v\n' >> .env", True),
        # mentions, not operands — every one of these refused before
        ('grep -v "process.env" src/app.js', False),
        (r'grep -n "^\.env" .gitignore', False),
        ("cat .env.example", False),
        ("""python -c "from dotenv import load_dotenv; load_dotenv('.env')" """, False),
        ("set -a && . ./.env; set +a; python tools/kanban/cli.py --show x", False),
        ("cp /repo/.env .env", False),
        ("ls -la .env", False),
        ("gh pr create --body 'fixes the .env loader'", False),
    ],
)
def test_env_file_access_matches_operands_not_mentions(shared, command, blocked):
    assert bool(shared.check_env_file_access("Bash", {"command": command})) is blocked


@pytest.mark.parametrize(
    "file_path,blocked",
    [
        (".env", True),
        ("/repo/.env", True),
        (".env.sample", False),
        # D-ORCH-8 excludes these in args/file_access_tiers.yaml; this check
        # refused them anyway, which is 24 of its 71 measured refusals.
        (".env.example", False),
        (".env.template", False),
        ("/wt/.env.local-copy.template", False),
    ],
)
def test_env_template_files_are_not_secrets(shared, file_path, blocked):
    assert bool(shared.check_env_file_access("Read", {"file_path": file_path})) is blocked


def test_append_only_ignores_searches_and_prose(shared):
    tables = ["audit_trail", "hook_events"]

    def blocked(command):
        return bool(shared.check_append_only_write("Bash", {"command": command}, tables))

    assert blocked("psql -c 'DELETE FROM audit_trail'")
    # searching FOR the statement, and describing it, are not executing it
    assert not blocked('grep -n "DELETE FROM audit_trail" tests/test_chain.py')
    assert not blocked('gh pr create --title "fix: UPDATE audit_trail path"')
    # a security test tampering with its own throwaway chain
    assert not blocked(
        'ICDEV_DB_PATH=/tmp/chain_demo.db python - <<\'PY\'\n'
        "conn.execute('DELETE FROM audit_trail WHERE id=3')\nPY"
    )


def test_direct_sqlite_only_flags_source_that_writes(shared):
    def blocked(tool, ti):
        return bool(shared.check_direct_sqlite_usage(tool, ti))

    assert blocked("Edit", {"file_path": "tools/foo/bar.py",
                            "new_string": "sqlite3.connect('x')"})
    # documentation about the pattern is not the pattern
    assert not blocked("Edit", {"file_path": "tools/manifest/safety-hooks.md",
                                "new_string": "blocks sqlite3.connect('x')"})
    # the check's own implementation names the string it looks for
    assert not blocked("Write", {"file_path": "tools/hooks/shared_checks.py",
                                 "content": 'if "sqlite3.connect(" in new_content:'})
    # the storage layer's own package
    assert not blocked("Write", {"file_path": "tools/db/shadowed_migration_replay.py",
                                 "content": "sqlite3.connect(p)"})
    # a read-only diagnostic writes nothing anywhere
    assert not blocked("Bash", {
        "command": "python -c \"import sqlite3; "
                   "print(sqlite3.connect('data/icdev.db').execute('select 1').fetchone())\""})
    assert blocked("Bash", {
        "command": "python -c \"import sqlite3; c=sqlite3.connect('data/icdev.db'); "
                   "c.execute('DROP TABLE heartbeat_checks'); c.commit()\""})


def test_worktree_target_is_unknown_when_the_shell_would_expand_it(shared):
    """The convention CLAUDE.md mandates must not be what the guard refuses.

    ``P=$(python -m tools.git.worktree_paths --path cli <slug>) && git worktree
    add --detach "$P"`` is the prescribed form. The hook cannot expand ``$P``, so
    the target is unknown — and unknown is the documented allow case, not a
    violation. 640 of this check's 652 measured refusals were this shape.
    """
    assert shared.worktree_add_target(
        'git worktree add --detach "$P" origin/main', posix=True) is None
    assert shared.worktree_add_target(
        "git worktree add -b feat/x ${WT} origin/main", posix=True) is None
    assert shared.worktree_add_target(
        "git worktree add /tmp/literal", posix=True) == "/tmp/literal"
    # only the segment that runs the command is parsed
    assert shared.worktree_add_target(
        "cd /repo && git fetch origin && git worktree add /tmp/x", posix=True
    ) == "/tmp/x"


def test_tier_exclusions_match_the_same_candidates_as_inclusions(shared):
    """`.env.*` caught a basename; `!.env.example` only ever tried a full path."""
    patterns = [".env", ".env.*", "!.env.sample", "!.env.example"]
    assert shared._matches_tier("C:/wt/repo/.env", patterns)
    assert not shared._matches_tier("C:/wt/repo/.env.example", patterns)
    assert not shared._matches_tier(".env.sample", patterns)
