#!/usr/bin/env python3
# CUI // SP-CTI
"""Editing CLAUDE.md without regenerating the packaged bootstrap is refused at commit (mfx-ci-04).

THE INCIDENT. PR #2137 added 74 lines to CLAUDE.md and never ran
`python tools/installer/prebuild_bootstrap.py`. `check_bootstrap_parity` found
one stale packaged file and the PR was red for HOURS on a test whose name says
"payload rule" -- with zero payload defects. The fix was one documented command;
the cost was the feedback delay, because `.githooks/pre-commit` mentioned the
bootstrap zero times and the first thing to notice was a full CI run.

WHAT THESE TESTS PIN
  * the hook's scope is READ OUT OF prebuild_bootstrap.py (its SOURCES literal
    plus the AI_PLATFORM_FILES literal it extends from) and equals the script's
    own runtime list -- never a second copy;
  * a commit staging nothing scaffolded never reaches the check;
  * a stale packaged copy is refused, the refusal names the file and prints the
    exact regeneration command, and the hook REGENERATES NOTHING;
  * a regenerated-but-unstaged packaged copy is refused too, naming `git add`,
    against a REAL git index -- the working tree is in parity, the commit is not;
  * a check that cannot run, an unreadable report and the check's own `warn`
    all allow the commit: CI is the backstop;
  * the hook script names the gate (the card measured zero hits for
    `bootstrap` / `prebuild` there) and this file is gated in CI.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from tools.installer import prebuild_bootstrap
from tools.testing import pre_commit_check

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CLAUDE_MD = "icdev/data/claude_bootstrap/CLAUDE.md"


# --------------------------------------------------------------------------- #
# Scope: read out of the script, never a second copy
# --------------------------------------------------------------------------- #
def test_scope_is_read_out_of_the_script_and_equals_its_runtime_sources() -> None:
    """The ast-derived list IS the script's SOURCES, in order, including the
    ten platform files the script extends SOURCES with at import time."""
    derived = pre_commit_check._scaffolded_sources()
    runtime = [rel_src for rel_src, _rel_dst, _kind in prebuild_bootstrap.SOURCES]
    assert derived == runtime
    for must in ("CLAUDE.md", "AGENTS.md", ".claude/commands", ".claude/hooks",
                 "tools/hooks/shared_checks.py", ".cursor/rules/icdev.mdc"):
        assert must in derived


def test_scope_is_empty_for_a_commit_staging_nothing_scaffolded() -> None:
    sources = pre_commit_check._scaffolded_sources()
    staged = [
        ("M", "docs/notes.md"), ("A", "tests/test_x.py"), ("M", "tools/kanban/cli.py"),
        ("M", "icdev/tools/testing/pre_commit_check.py"), ("M", "CLAUDE.md.bak"),
        ("M", ".claude/commandsx/y.md"), ("D", "CLAUDE.md"),
    ]
    assert pre_commit_check._bootstrap_scope(staged, sources) == []


def test_scope_takes_files_directories_and_platform_files_on_add_modify_rename() -> None:
    sources = pre_commit_check._scaffolded_sources()
    staged = [
        ("M", "CLAUDE.md"), ("A", ".claude/commands/new.md"), ("R100", ".claude/hooks/stop.py"),
        ("M", "tools/hooks/shared_checks.py"), ("M", ".cursor/rules/icdev.mdc"),
        ("M", "AGENTS.md"), ("D", ".claude/commands/old.md"), ("M", "docs/x.md"),
    ]
    assert pre_commit_check._bootstrap_scope(staged, sources) == [
        "CLAUDE.md", ".claude/commands/new.md", ".claude/hooks/stop.py",
        "tools/hooks/shared_checks.py", ".cursor/rules/icdev.mdc", "AGENTS.md",
    ]


def test_an_unreadable_or_non_literal_declaration_narrows_scope_to_nothing(tmp_path: Path) -> None:
    """A hook that cannot resolve its own scope allows rather than guesses."""
    assert pre_commit_check._scaffolded_sources(root=tmp_path) == []
    script = tmp_path / pre_commit_check.PREBUILD_BOOTSTRAP_TOOL
    script.parent.mkdir(parents=True)
    script.write_text("SOURCES = build_list()\n", encoding="utf-8")
    assert pre_commit_check._scaffolded_sources(root=tmp_path) == []
    script.write_text("def broken(:\n", encoding="utf-8")
    assert pre_commit_check._scaffolded_sources(root=tmp_path) == []


def test_scope_replays_through_a_caller_supplied_reader() -> None:
    """The survey replays history through THIS function with a `git show` reader."""
    def read(rel: Path) -> str | None:
        if rel == pre_commit_check.PREBUILD_BOOTSTRAP_TOOL:
            return 'SOURCES: list = [("CLAUDE.md", "CLAUDE.md", "file"), (".claude/hooks", "claude/hooks", "dir")]\n'
        if rel == pre_commit_check.AI_PLATFORMS_MODULE:
            return 'AI_PLATFORM_FILES = (("codex", "AGENTS.md"),)\n'
        return None

    assert pre_commit_check._scaffolded_sources(read=read) == [
        "CLAUDE.md", ".claude/hooks", "AGENTS.md",
    ]


def test_commit_staging_nothing_scaffolded_never_reaches_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path must cost nothing, or the hook gets ripped out."""

    def _boom(*_a, **_k):
        raise AssertionError("the bootstrap check ran for a commit that stages nothing it scaffolds")

    monkeypatch.setattr(pre_commit_check, "_get_staged_name_status",
                        lambda *_a, **_k: [("M", "docs/notes.md")])
    monkeypatch.setattr(pre_commit_check, "_run_domain_leak_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(pre_commit_check, "_run_bootstrap_parity", _boom)
    assert pre_commit_check.main() == 0


# --------------------------------------------------------------------------- #
# The refusal: names the file, prints the command, regenerates nothing
# --------------------------------------------------------------------------- #
def _check_result(status: str, extra: list[str] = (), message: str = "msg") -> subprocess.CompletedProcess:
    payload = json.dumps({"checks": [{
        "check_id": "bootstrap_parity", "status": status,
        "extra": list(extra), "missing": [], "message": message,
    }]})
    return subprocess.CompletedProcess(args=[], returncode=0 if status == "pass" else 1,
                                       stdout=payload, stderr="")


def _fake_check_runner(monkeypatch: pytest.MonkeyPatch, status: str, extra: list[str] = ()) -> list[list[str]]:
    """Answer the coherence shell-out with a canned verdict; let git through untouched."""
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        if str(cmd[0]) == "git":
            return real_run(cmd, **kwargs)
        assert "coherence_checker.py" in str(cmd[1]) and "bootstrap_parity" in cmd
        return _check_result(status, extra)

    monkeypatch.setattr(pre_commit_check.subprocess, "run", fake_run)
    return calls


def test_a_stale_packaged_copy_is_refused_naming_the_file_and_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    drift = "CLAUDE.md != icdev/data/claude_bootstrap/CLAUDE.md (41234 vs 39001 bytes)"
    calls = _fake_check_runner(monkeypatch, "fail", [drift])
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"]) is False
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert drift in out
    assert "python tools/installer/prebuild_bootstrap.py" in out
    assert "git add icdev/data/claude_bootstrap" in out
    # NOTHING regenerated: no subprocess ever spawned the prebuild script.
    assert not any("prebuild_bootstrap" in " ".join(c) for c in calls)


def test_the_hook_never_imports_the_regenerator() -> None:
    """A hook that fixes what it checks gates nothing. Pinned by AST: the module
    may print the command, it may never import or exec the script behind it."""
    tree = ast.parse(Path(pre_commit_check.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [a.name for a in node.names]
        else:
            continue
        assert not any("prebuild_bootstrap" in n for n in names), names


def test_a_pass_verdict_with_both_sides_staged_is_allowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    _fake_check_runner(monkeypatch, "pass")
    monkeypatch.setattr(pre_commit_check, "_bootstrap_unstaged_twins", lambda *_a, **_k: [])
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"]) is True
    assert "Bootstrap parity: OK" in capsys.readouterr().out


def test_the_checks_own_warn_never_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing pair is the check's `warn`; refusing on it would block a commit
    CI would have passed."""
    _fake_check_runner(monkeypatch, "warn")
    monkeypatch.setattr(pre_commit_check, "_bootstrap_unstaged_twins", lambda *_a, **_k: [])
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"]) is True


def test_a_check_that_cannot_run_or_be_read_allows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    def raise_run(cmd, **_k):
        raise OSError("no python here")

    monkeypatch.setattr(pre_commit_check.subprocess, "run", raise_run)
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"]) is True
    assert "SKIPPED" in capsys.readouterr().out

    def garbage_run(cmd, **_k):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="not json", stderr="")

    monkeypatch.setattr(pre_commit_check.subprocess, "run", garbage_run)
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"]) is True
    assert "SKIPPED" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# The index guard, against a REAL git index
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=False, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A committed repo whose CLAUDE.md and packaged copy are in parity."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "gate@example.test")
    _git(root, "config", "user.name", "Gate Test")
    _write(root / "CLAUDE.md", "# rules v1\n")
    _write(root / PACKAGED_CLAUDE_MD, "# rules v1\n")
    _write(root / "args" / "bootstrap_parity.yaml",
           "must_match:\n  - target: CLAUDE.md\n    source: data/claude_bootstrap/CLAUDE.md\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root


def test_baseline_index_has_no_unstaged_twin(repo: Path) -> None:
    assert pre_commit_check._bootstrap_must_match(repo) == [("CLAUDE.md", PACKAGED_CLAUDE_MD)]
    assert pre_commit_check._bootstrap_unstaged_twins(["CLAUDE.md"], root=repo) == []


def test_regenerated_but_unstaged_packaged_copy_is_refused_and_names_git_add(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """Working tree in parity, commit not: the case the coherence check cannot see."""
    _write(repo / "CLAUDE.md", "# rules v2\n")
    _git(repo, "add", "CLAUDE.md")
    _write(repo / PACKAGED_CLAUDE_MD, "# rules v2\n")   # regenerated, NOT staged
    assert pre_commit_check._bootstrap_unstaged_twins(["CLAUDE.md"], root=repo) == [
        ("CLAUDE.md", PACKAGED_CLAUDE_MD),
    ]

    _fake_check_runner(monkeypatch, "pass")   # the tree on disk IS in parity
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"], root=repo) is False
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert f"git add CLAUDE.md {PACKAGED_CLAUDE_MD}" in out

    _git(repo, "add", PACKAGED_CLAUDE_MD)
    assert pre_commit_check._run_bootstrap_parity(["CLAUDE.md"], root=repo) is True
    assert "Bootstrap parity: OK" in capsys.readouterr().out


def test_a_packaged_copy_the_index_never_held_is_the_checks_warn_not_a_refusal(repo: Path) -> None:
    _git(repo, "rm", "-q", "--cached", PACKAGED_CLAUDE_MD)
    _write(repo / "CLAUDE.md", "# rules v2\n")
    _git(repo, "add", "CLAUDE.md")
    assert pre_commit_check._bootstrap_unstaged_twins(["CLAUDE.md"], root=repo) == []


def test_an_unstaged_target_is_out_of_the_index_guard(repo: Path) -> None:
    """Only a STAGED target is compared: the guard answers for this commit."""
    _write(repo / PACKAGED_CLAUDE_MD, "# rules v2\n")
    _git(repo, "add", PACKAGED_CLAUDE_MD)
    assert pre_commit_check._bootstrap_unstaged_twins(["docs/x.md"], root=repo) == []


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_hook_script_names_the_bootstrap_gate() -> None:
    """The card measured `grep bootstrap .githooks/pre-commit` at zero hits."""
    text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "bootstrap" in text
    assert "prebuild_bootstrap" in text


def test_this_file_is_gated_in_ci() -> None:
    fragment = REPO_ROOT / "args" / "ci_test_files" / "core.d" / "mfx-ci-04.txt"
    assert fragment.is_file()
    rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    assert rel in fragment.read_text(encoding="utf-8").splitlines()


def test_the_live_tree_is_in_parity_so_this_commit_would_pass_its_own_hook() -> None:
    """Dogfood: this card edits CLAUDE.md, so it regenerated the payload."""
    assert (REPO_ROOT / "CLAUDE.md").read_bytes() == (REPO_ROOT / PACKAGED_CLAUDE_MD).read_bytes()
