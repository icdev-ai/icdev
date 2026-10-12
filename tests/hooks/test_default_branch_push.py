"""omx-guard-08: an agent may not push to the repository's default branch.

MEASURED 2026-10-11 (omx-smoke-03): opencode on kimi-k3 committed on its kanban
branch, then ran ``git push origin kanban/omx-smoke-03:main`` and
``git push origin main`` against a remote with no branch protection, and its
work landed on the default branch with no PR. ``check_git_danger`` stops a
force push and ``check_branch_deletion`` a delete; a fast-forward push to main
is neither, so nothing refused it.

Every test here runs against REAL throwaway git repositories (a bare remote
and a clone) so the default branch is resolved the way the guard resolves it
in the field, never assumed to be ``main``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.hooks import shared_checks as sc

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
ENV = sc.DEFAULT_BRANCH_PUSH_GUARD_ENV


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def _make_repo(tmp: Path, default: str) -> Path:
    """A clone of a bare remote whose default branch is *default*.

    Built source -> ``clone --bare`` -> ``clone`` so the work tree carries the
    ``refs/remotes/origin/HEAD`` symref a real clone has.
    """
    src = tmp / f"src-{default}"
    src.mkdir()
    _git(src, "init", "-q", "-b", default)
    (src / "README").write_text("x\n", encoding="utf-8")
    _git(src, "add", "README")
    _git(src, "commit", "-q", "-m", "init")
    bare = tmp / f"remote-{default}.git"
    _git(tmp, "clone", "-q", "--bare", str(src), str(bare))
    work = tmp / f"work-{default}"
    _git(tmp, "clone", "-q", str(bare), str(work))
    # A kanban-style feature branch created from the default branch, so it
    # records origin/<default> as its upstream -- exactly what a worktree
    # created off origin/main looks like.
    _git(work, "branch", "-q", "--track", "kanban/t-01", f"origin/{default}")
    return work


@pytest.fixture(scope="module")
def main_repo(tmp_path_factory) -> Path:
    return _make_repo(tmp_path_factory.mktemp("dbp-main"), "main")


@pytest.fixture(scope="module")
def master_repo(tmp_path_factory) -> Path:
    return _make_repo(tmp_path_factory.mktemp("dbp-master"), "master")


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _check(command: str, cwd: Path):
    return sc.check_default_branch_push("Bash", {"command": command}, cwd=str(cwd))


# ── every refspec form is refused ─────────────────────────────────────────

REFUSED_FORMS = [
    ("remote branch", "git push origin {d}"),
    ("HEAD:dst", "git push origin HEAD:{d}"),
    ("src:dst", "git push origin kanban/t-01:{d}"),
    ("src:refs/heads/dst", "git push origin kanban/t-01:refs/heads/{d}"),
    ("+src:dst", "git push origin +kanban/t-01:{d}"),
    ("--all", "git push --all origin"),
    ("--branches", "git push origin --branches"),
    ("--mirror", "git push --mirror origin"),
    ("-u remote branch", "git push -u origin {d}"),
    ("delete :dst", "git push origin :{d}"),
    ("--delete", "git push origin --delete {d}"),
    ("matching spec", "git push origin :"),
    ("glob", "git push origin 'refs/heads/*:refs/heads/*'"),
    ("-o value before", "git push -o ci.skip origin {d}"),
    ("the smoke-03 pair", "git push origin kanban/omx-smoke-03:{d} && git push origin {d}"),
    ("after a commit", "git add -A && git commit -m wip && git push origin {d}"),
    ("subshell", "(git push origin {d})"),
    ("env prefix", "GIT_TRACE=0 git push origin {d}"),
]


@pytest.mark.parametrize("name,form", REFUSED_FORMS, ids=[f[0] for f in REFUSED_FORMS])
@pytest.mark.parametrize("repo", ["main_repo", "master_repo"])
def test_every_refspec_form_is_refused(request, repo, name, form):
    work = request.getfixturevalue(repo)
    default = "main" if repo == "main_repo" else "master"
    reason = _check(form.format(d=default), work)
    assert reason and reason.startswith("BLOCKED"), f"{name!r} was allowed"
    assert default in reason or "--" in reason


def test_git_dash_c_is_resolved_against_that_directory(master_repo):
    """``git -C <dir>`` names the repository; its default branch decides."""
    elsewhere = master_repo.parent
    assert _check(f"git -C {master_repo.name} push origin master", elsewhere)
    assert _check(f"git -C {master_repo} push origin HEAD:master", REPO_ROOT)
    # The same command against a main-default repo pushes a non-default branch.
    assert not _check(f"git -C {master_repo.name} push origin main", elsewhere)


def test_cd_before_the_push_is_followed(master_repo):
    assert _check(f"cd {master_repo} && git push origin master", REPO_ROOT)
    assert not _check(f"cd {master_repo} && git push origin main", REPO_ROOT)


# ── what stays allowed ────────────────────────────────────────────────────

ALLOWED = [
    "git push origin kanban/t-01",
    "git push -u origin kanban/t-01",
    "git push -u origin feat/new-thing",
    "git push origin HEAD:kanban/t-01",
    "git push origin kanban/t-01:refs/heads/kanban/t-01",
    "git push --force-with-lease origin kanban/t-01",
    "git push origin --delete kanban/old",
    "git push origin v1.0",
    "git push origin refs/tags/v1.0",
    "git push origin --tags",
    "git push --dry-run origin main",
    "git push -n origin main",
    "git status && git log --oneline -1",
    "echo git push origin main",
    "git commit -m 'never git push origin main'",
    "grep -rn 'git push origin main' docs/",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_feature_branch_and_non_branch_pushes_pass(main_repo, command):
    assert _check(command, main_repo) is None


def test_default_branch_comes_from_the_repo_not_a_literal_main(master_repo):
    assert sc.resolve_default_branches("origin", str(master_repo)) == ("master",)
    assert _check("git push origin master", master_repo)
    # `main` is just another branch name in a repo whose default is master.
    assert _check("git push origin main", master_repo) is None
    assert _check("git push origin HEAD:main", master_repo) is None


def test_a_remote_without_a_head_ref_is_asked_directly(tmp_path, master_repo):
    """``git remote add`` records no ``refs/remotes/<r>/HEAD``; a local bare
    remote is then asked for its own HEAD rather than guessed at."""
    work = tmp_path / "added"
    work.mkdir()
    _git(work, "init", "-q", "-b", "trunk")
    bare = master_repo.parent / "remote-master.git"
    _git(work, "remote", "add", "upstream", str(bare))
    assert sc.resolve_default_branches("upstream", str(work)) == ("master",)
    assert _check("git push upstream trunk:master", work)
    assert _check("git push upstream trunk", work) is None


def test_unreadable_directory_still_refuses_an_explicit_main_or_master(tmp_path):
    nowhere = tmp_path / "does-not-exist"
    assert sc.resolve_default_branches("origin", str(nowhere)) == ("main", "master")
    assert _check("git push origin main", nowhere)
    assert _check("git push origin master", nowhere)
    assert _check("git push origin feat/x", nowhere) is None
    # A bare push needs branch state that is not there: fail open.
    assert _check("git push", nowhere) is None


# ── bare `git push` ───────────────────────────────────────────────────────


def test_bare_push_on_the_default_branch_is_refused(master_repo):
    _git(master_repo, "checkout", "-q", "master")
    reason = _check("git push", master_repo)
    assert reason and "master" in reason
    assert _check("git push origin", master_repo)


def test_bare_push_on_a_feature_branch_tracking_default_follows_push_default(master_repo):
    """Under ``simple`` (git's default) a branch whose upstream has another
    name is refused by git itself, so nothing reaches master and the guard
    does not refuse; under ``upstream`` the push WOULD write master."""
    _git(master_repo, "checkout", "-q", "kanban/t-01")
    try:
        assert _check("git push", master_repo) is None
        _git(master_repo, "config", "push.default", "upstream")
        reason = _check("git push", master_repo)
        assert reason and "tracks `origin/master`" in reason
        _git(master_repo, "config", "push.default", "matching")
        assert _check("git push origin", master_repo)
        _git(master_repo, "config", "push.default", "nothing")
        assert _check("git push", master_repo) is None
    finally:
        _git(master_repo, "config", "--unset", "push.default")
        _git(master_repo, "checkout", "-q", "master")


def test_configured_remote_push_refspecs_are_judged(master_repo):
    _git(master_repo, "checkout", "-q", "kanban/t-01")
    try:
        _git(master_repo, "config", "remote.origin.push", "HEAD:refs/heads/master")
        assert _check("git push", master_repo)
    finally:
        _git(master_repo, "config", "--unset-all", "remote.origin.push")
        _git(master_repo, "checkout", "-q", "master")


# ── kill switch, and what it is NOT ───────────────────────────────────────


def test_kill_switch_stands_the_check_down(monkeypatch, main_repo):
    monkeypatch.setenv(ENV, "0")
    assert _check("git push origin main", main_repo) is None


def test_an_inline_assignment_in_the_command_is_not_a_kill_switch(main_repo):
    """The agent writes the command, so a switch it can type is no switch."""
    assert _check(f"{ENV}=0 git push origin main", main_repo)


def test_the_switch_is_in_the_hooks_kill_switch_table():
    text = HOOK_PATH.read_text(encoding="utf-8")
    assert f'"default_branch_push": "{ENV}"' in text


# ── every guarded path refuses it, from the same code ─────────────────────


def _run_hook(command: str, cwd: Path, env_extra=None) -> tuple:
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(cwd)}),
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
        env={**os.environ, **(env_extra or {})},
    )
    return proc.returncode, proc.stderr


def test_claude_code_hook_refuses_using_the_payload_cwd(master_repo):
    code, err = _run_hook("git push origin master", master_repo)
    assert code == 2 and "default branch `master`" in err
    # The hook's own REPO_ROOT defaults to main; the payload's repo does not.
    assert _run_hook("git push origin main", master_repo)[0] == 0
    assert _run_hook("git push origin master", master_repo, {ENV: "0"})[0] == 0


def test_headless_path_refuses(master_repo):
    from tools.airgap import hook_compat

    verdict = hook_compat.run_pre_tool_check(
        "Bash", {"command": "git push origin HEAD:master", "cwd": str(master_repo)})
    assert verdict["allowed"] is False and "default branch" in verdict["reason"]
    assert "check_default_branch_push" in hook_compat.HEADLESS_CHECKS


@pytest.mark.parametrize("harness,tool", [("opencode", "bash"), ("pi", "bash")])
def test_l1_harness_bridge_refuses_in_the_runs_directory(master_repo, harness, tool):
    """The bridge runs from the ICDEV root; the plugin sends the run's cwd."""
    from tools.hooks import harness_guard as hg

    verdict = hg.decide(harness, tool, {"command": "git push origin master"},
                        cwd=str(master_repo))
    assert verdict["allowed"] is False and "master" in verdict["reason"]
    assert hg.decide(harness, tool, {"command": "git push origin kanban/t-01"},
                     cwd=str(master_repo))["allowed"] is True


def test_l1_plugins_send_their_directory():
    for harness in ("opencode", "pi"):
        src = (REPO_ROOT / "icdev" / "data" / "harness_plugins" / harness
               / "icdev-guard.ts").read_text(encoding="utf-8")
        assert "cwd: directory" in src, harness


def test_l2_guarded_shell_refuses_in_its_own_directory(monkeypatch, master_repo):
    """The shim runs IN the shell's directory, so nothing has to be passed."""
    from tools.hooks import guard_shell

    monkeypatch.chdir(master_repo)
    assert guard_shell.decide_text("git push origin master")["allowed"] is False
    assert guard_shell.decide_text(
        "source snap && eval 'git push origin HEAD:master' < /dev/null")["allowed"] is False
    assert guard_shell.decide_text("git push origin kanban/t-01")["allowed"] is True
