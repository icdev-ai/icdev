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
import os
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


# ── the composed Bash target scan (exa-bench-05 × exa-bench-06) ───────────
#
# `check_file_access_tiers` was refactored independently on two branches and
# NEITHER version subsumed the other, so taking either alone silently dropped a
# property. exa-bench-06 made the scan return ALL targets (`a > log 2>> err`
# left the rest unexamined); exa-bench-05 segmented the command first. The
# resolution runs the all-targets scan INSIDE the per-segment split, and these
# two cases are what tell the halves apart — each fails if its half is reverted.


def test_a_real_target_in_a_later_segment_still_blocks(shared):
    """Segmenting must not lose a target: the property exa-bench-05 could drop."""
    command = "cd wt && ruff check tests/x.py && rm -f .env"
    reason = shared.check_file_access_tiers("Bash", {"command": command})
    assert reason and "zero_access" in reason, (
        "the `.env` delete in the third segment was ALLOWED — segmenting the "
        "command dropped a target the whole-command scan used to see"
    )


def test_a_later_segment_does_not_complete_an_earlier_one(shared):
    """The false-fire the segmentation exists to remove.

    `.env.example` is an argument to `grep`, not a target of the `rm` before
    it. Its own tier explicitly exempts it, and the survey counted refusals of
    exactly this shape.
    """
    command = "rm -f build.tmp ; grep -r pattern .env.example"
    assert shared.check_file_access_tiers("Bash", {"command": command}) is None


def test_every_target_within_one_segment_is_examined(shared):
    """Per-segment must not go back to first-match-only: exa-bench-06's half."""
    command = "echo hi > notes.md 2>> ~/.ssh/authorized_keys"
    reason = shared.check_file_access_tiers("Bash", {"command": command})
    assert reason and "zero_access" in reason, (
        "only the FIRST redirect target of the segment was examined — the "
        "second one wrote into a zero_access path and was allowed"
    )


def test_a_heredoc_body_is_not_read_as_commands(shared):
    """What composing them BUYS, over either half alone.

    `command_segments` strips heredoc data first, so a non-interpreter heredoc
    whose body happens to contain shell text no longer contributes targets. The
    whole-command scan on its own had no way to tell body from command.
    """
    command = "cat > notes.md <<'EOF'\nrm -f .env\nEOF"
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


# ── exa-bench-05: shell-aware scanning ────────────────────────────────────
#
# These checks became hard blocks when the `|| true` came out of
# .claude/settings.json, so what they read as "the command" has to be right.
# Measured over 96,649 real tool calls, whole-string matching was the single
# largest source of false refusals.


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


# ── worktree write containment (exa-bench-07) ─────────────────────────────
#
# The unit-level half. The end-to-end half — both guard paths refusing the same
# writes — lives in tests/test_skip_permissions_compensating_controls.py, which
# is where the decision doc's coverage matrix is measured.


@pytest.mark.parametrize(
    "command,expected",
    [
        # Verbs that write with no redirection operator, which is exactly why
        # the tier extractor (redirect + tee only) never saw these at all.
        ("touch /home/victim/.ssh/authorized_keys",
         ["/home/victim/.ssh/authorized_keys"]),
        ("mkdir -p /etc/cron.d/persist", ["/etc/cron.d/persist"]),
        ("sudo mkdir /etc/evil", ["/etc/evil"]),          # wrapper stripped
        ("ICDEV_X=1 touch /etc/evil", ["/etc/evil"]),     # env assignment stripped
        ("cp payload.sh /usr/local/bin/pwn", ["/usr/local/bin/pwn"]),  # dest is last
        ("mv a.txt b.txt", ["b.txt"]),
        ("dd if=/dev/zero of=/etc/shadow", ["/etc/shadow"]),
        ("curl https://x/y -o /usr/local/bin/pwn", ["/usr/local/bin/pwn"]),
        ("wget https://x/y -O /tmp/z", ["/tmp/z"]),
        # A read is not a write, and a source is not a destination.
        ("cat /etc/passwd", []),
        ("git status", []),
    ],
)
def test_bash_write_targets(shared, command, expected):
    assert shared.bash_write_targets(command) == expected


def test_bash_write_targets_includes_the_redirect_forms(shared):
    """Superset of bash_file_targets' write half, not a replacement for it."""
    targets = shared.bash_write_targets("echo k >> ~/.ssh/authorized_keys")
    assert "~/.ssh/authorized_keys" in targets


@pytest.mark.parametrize(
    "raw,outside",
    [
        ("/etc/cron.d/pwn", True),
        ("~/.bashrc", True),
        ("$HOME/.bashrc", True),                 # a shell would have expanded it
        ("C:/Windows/System32/drivers/etc/hosts", True),  # true on POSIX too
        (r"\\attacker\share\payload", True),     # UNC — a host we cannot judge
        ("C:notes.txt", True),                   # drive-RELATIVE — per-drive cwd
        ("tools/foo.py", False),
        (".tmp/scratch/report.json", False),
        ("/dev/null", False),                    # not a file; a very common sink
        ("2>&1", False),                         # never a path
    ],
)
def test_outside_write_root(shared, raw, outside):
    assert bool(shared.outside_write_root(raw, repo_root=REPO_ROOT)) is outside


def test_a_real_unc_path_is_judged_by_construction_not_resolution(shared):
    r"""A UNC path must be refused WITHOUT being resolved, on every platform.

    TWO backslashes is what reaches the UNC branch. It names a host this check
    cannot reason about, so the verdict must not depend on Path() resolving it —
    which is what makes it platform-independent.
    """
    raw = r"\\attacker\share\payload"
    sentinel = shared.resolve_write_target(raw, REPO_ROOT)
    assert sentinel is shared.UNRESOLVABLE_TARGET, (
        f"{raw!r} must be judged unresolvable by construction, not resolved to "
        f"{sentinel!r}"
    )
    assert shared.outside_write_root(raw, repo_root=REPO_ROOT)


@pytest.mark.skipif(os.name != "nt", reason="root-relative is a Windows concept")
def test_a_root_relative_windows_path_is_outside(shared):
    r"""A single leading `\` is root-relative on Windows and resolves onto the
    current drive, so it lands outside the worktree.

    Split out from the parametrized case rather than folded into it, because the
    platforms genuinely disagree and BOTH are right. On POSIX a backslash is an
    ordinary filename character, so `\attacker\share\payload` is a RELATIVE name
    that correctly resolves INSIDE the worktree — a file by that name really is
    in there. Widening the guard to treat one backslash as UNC makes both
    platforms "agree" by refusing a legitimate in-worktree POSIX write, which
    contradicts the guard's own rule that it fails OPEN on a path it cannot
    place. The UNC case next to this one needs TWO leading backslashes to reach
    that branch at all; with one it passed on Windows only by accident of this
    resolution, and this file was not in the CI gate to notice.
    """
    assert shared.outside_write_root(r"\attacker\share\payload", repo_root=REPO_ROOT)


def test_the_boundary_check_is_writes_only(shared):
    """Reads are exa-bench-09's territory. Refusing one here would look like
    coverage of a gap this check does not close."""
    assert shared.check_write_outside_worktree(
        "Read", {"file_path": "/home/victim/.ssh/id_rsa"}, repo_root=REPO_ROOT
    ) is None


def test_the_boundary_check_is_disablable_and_has_a_monitor_mode(shared, monkeypatch):
    call = ("Write", {"file_path": "/etc/cron.d/pwn", "content": "x"})
    assert shared.check_write_outside_worktree(*call, repo_root=REPO_ROOT)

    monkeypatch.setenv(shared.WRITE_BOUNDARY_GUARD_ENV, "0")
    assert shared.check_write_outside_worktree(*call, repo_root=REPO_ROOT) is None

    monkeypatch.setenv(shared.WRITE_BOUNDARY_GUARD_ENV, "monitor")
    assert shared.check_write_outside_worktree(*call, repo_root=REPO_ROOT) is None


def test_extra_roots_can_sanction_a_path(shared, monkeypatch, tmp_path):
    """The operator escape hatch, so the answer to a false positive is a root
    rather than turning the guard off."""
    target = tmp_path / "elsewhere" / "out.json"
    monkeypatch.setenv(shared.WRITE_BOUNDARY_EXTRA_ROOTS_ENV, str(tmp_path / "elsewhere"))
    assert shared.outside_write_root(str(target), repo_root=REPO_ROOT) is None


def test_the_main_checkout_is_sanctioned_from_a_linked_worktree(shared, tmp_path):
    """A worktree's anchor is itself; the checkout it is linked to is the second
    root. Read from `<anchor>/.git` — no `git rev-parse` on the per-call path."""
    main = tmp_path / "repo"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
    )
    roots = shared.sanctioned_write_roots(worktree)
    assert main.resolve() in roots
    assert worktree.resolve() in roots


# ── exa-bench-05: what the write-boundary survey narrowed ─────────────────
#
# `check_write_outside_worktree` shipped ENFORCING (exa-bench-07) at a time when
# `|| true` in .claude/settings.json was discarding every refusal, so it had
# never actually refused anything and its rate had never been measured. Removing
# that wrapper is what makes the rate matter. Measured first: 2,526 of 96,799
# real tool calls, 2.61% — and every class of it was a PARSE defect. Each test
# below pins one of those classes, with the count it was worth. Together they
# take the check to 850 (0.878%), whose residue is writes into the
# `C:\AI\.worktrees` / `C:\AI\.wt*` sprawl that `check_worktree_path` already
# refuses to create — i.e. the finding, which is why the check stays enforcing.


def test_the_check_is_enforcing_by_default(shared):
    """The property exa-bench-07 shipped and the survey had to justify keeping.

    Standing a check down because its rate is inconvenient is how the hook came
    to be advisory in the first place. If this constant is ever flipped, the
    survey has to say why.
    """
    assert shared.WRITE_BOUNDARY_DEFAULT_MODE == "enforce"


def test_a_redirect_inside_a_quoted_string_is_not_a_redirect(shared):
    """370 fires. `--jq '"#\\(.n) -> \\(.state)"'` has a `>` in it and redirects
    nothing; the regex this replaced returned `\\(.state)"'` as a path."""
    command = "gh pr view 1563 --json state --jq '\"1563 -> \\(.state)\"'"
    assert shared.bash_write_targets(command) == []
    assert shared.check_write_outside_worktree(
        "Bash", {"command": command}, repo_root=REPO_ROOT
    ) is None


def test_a_command_substitution_does_not_glue_its_paren_to_the_target(shared):
    """641 fires. `$(… 2>/dev/null)` left `/dev/null)` on the token, which misses
    `_NULL_SINKS` and reads as a write to `C:\\dev`."""
    command = 'm=$(git log origin/main --format=%H -1 2>/dev/null)'
    assert shared.bash_write_targets(command) == ["/dev/null"]
    assert shared.check_write_outside_worktree(
        "Bash", {"command": command}, repo_root=REPO_ROOT
    ) is None


def test_a_heredoc_body_contributes_no_write_targets(shared):
    """758 fires — the largest single class. `cat > .tmp/prbody.md <<'EOF'`
    followed by a PR body that names a path is a write to the FILE, not to the
    paths the prose mentions."""
    command = (
        "cat > .tmp/prbody.md <<'EOF'\n"
        "The packaged hook resolved /tools/hooks/shared_checks.py wrongly.\n"
        "EOF"
    )
    assert shared.bash_write_targets(command) == [".tmp/prbody.md"]


@pytest.mark.parametrize(
    "command,expected",
    [
        # A quoted target with a space is a target, and used to be invisible.
        ('echo hi > "out file.txt"', ["out file.txt"]),
        # 38 fires: `\` is a path separator here, not an escape. The double-quote
        # escape rule applies to `" \ $ \`` and to nothing else.
        (r'python x.py > "C:\Users\schuo\AppData\Local\Temp\r.json"',
         [r"C:\Users\schuo\AppData\Local\Temp\r.json"]),
        (r'echo hi > "say \"hi\".txt"', ['say "hi".txt']),
    ],
)
def test_a_quoted_redirect_target_survives_its_quotes(shared, command, expected):
    assert shared.bash_write_targets(command) == expected


@pytest.mark.skipif(os.name != "nt", reason="MSYS drive spelling is Windows-only")
def test_the_msys_spelling_of_this_worktree_is_inside_it(shared):
    """539 fires. Git Bash spells `C:\\AI\\ICDev` as `/c/AI/ICDev`, and the Bash
    tool IS Git Bash here — so these were sessions writing into their OWN
    worktree, refused because `/c/...` resolved to `C:\\c\\...`."""
    drive = REPO_ROOT.drive.rstrip(":").lower()
    inside = "/" + drive + "/" + str(REPO_ROOT)[3:].replace("\\", "/") + "/.tmp/coh.json"
    assert shared.outside_write_root(inside, repo_root=REPO_ROOT) is None
    # The translation must not turn an outside path into an inside one.
    assert shared.outside_write_root("/" + drive + "/Windows/System32/x",
                                     repo_root=REPO_ROOT)


def test_the_harness_own_claude_dirs_are_sanctioned_but_its_config_is_not(shared):
    """371 fires were `~/.claude/plans`: plan mode writes the plan file and no
    session names it, so refusing it refuses plan mode. `~/.claude` itself stays
    outside — `settings.json` there wires this hook and `hooks/` implements it,
    and a guard that permits writes to its own configuration is not a guard."""
    home = Path.home() / ".claude"
    for name in shared._CLAUDE_HARNESS_DIRS:
        assert shared.outside_write_root(
            str(home / name / "x.md"), repo_root=REPO_ROOT
        ) is None, f"~/.claude/{name} is harness scratch and was refused"
    for denied in ("settings.json", "hooks/pre_tool_use.py", "CLAUDE.md"):
        assert shared.outside_write_root(
            str(home / denied), repo_root=REPO_ROOT
        ), f"~/.claude/{denied} configures the guard and must stay outside it"
