# CUI // SP-CTI
"""Worktree reclamation after merge (ars-wt-01).

Creation was bounded; reclamation was not. Measured 2026-08-02: 122 registered
worktrees, recursively nested, several locked.

The safety property under test is one-directional: it is fine to leave a
worktree behind, and never fine to remove one holding the only copy of work. So
every check below asserts a REFUSAL, not a removal.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Nesting — the mechanism that made the leak compound
# --------------------------------------------------------------------------- #


def test_worktree_base_resolves_to_the_canonical_repo_root():
    """A dispatch from inside a worktree must not nest the next one under it."""
    from tools.genesis.reflexes import kanban

    root = kanban._canonical_repo_root()
    assert (root / ".git").exists(), f"{root} is not a repository root"
    assert kanban.WORKTREE_BASE == root / ".tmp" / "worktrees"


def test_canonical_root_is_the_main_worktree_not_the_linked_one():
    """git rev-parse --git-common-dir reports the MAIN .git from anywhere."""
    proc = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip("git unavailable")
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (REPO / common).resolve()

    from tools.genesis.reflexes import kanban

    assert kanban._canonical_repo_root() == common.parent


# --------------------------------------------------------------------------- #
# Reclamation refuses whenever work could be lost
# --------------------------------------------------------------------------- #


@pytest.fixture()
def watcher():
    from tools.ci.pr_watcher import PRWatcher

    try:
        return PRWatcher(dry_run=True)
    except TypeError:
        return PRWatcher()


def _stub_path(monkeypatch, path):
    import tools.genesis.reflexes.kanban as kb

    monkeypatch.setattr(kb, "_task_worktree_path", lambda task_id: path)


def test_absent_worktree_is_reported_not_treated_as_success(watcher, monkeypatch, tmp_path):
    _stub_path(monkeypatch, tmp_path / "gone")
    v = watcher.reclaim_worktree("t-1")
    assert v["reclaimed"] is False
    assert v["reason"] == "already gone"


def test_dirty_worktree_is_refused(watcher, monkeypatch, tmp_path):
    """Someone may be mid-edit even after the PR merged."""
    wt = tmp_path / "dirty"
    wt.mkdir()
    _stub_path(monkeypatch, wt)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, " M tools/x.py\n", ""),
    )
    v = watcher.reclaim_worktree("t-2")
    assert v["reclaimed"] is False
    assert "uncommitted" in v["reason"]


def test_unmerged_commits_refuse_removal(watcher, monkeypatch, tmp_path):
    """A commit not on the default branch may be the only copy in existence."""
    wt = tmp_path / "ahead"
    wt.mkdir()
    _stub_path(monkeypatch, wt)

    calls = {"n": 0}

    def _fake(args, *a, **k):
        calls["n"] += 1
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, "3", "")
        if "remove" in args:
            pytest.fail("removal attempted despite unmerged commits")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake)
    v = watcher.reclaim_worktree("t-3")
    assert v["reclaimed"] is False
    assert "not on origin" in v["reason"]


def test_git_refusal_is_surfaced_not_swallowed(watcher, monkeypatch, tmp_path):
    wt = tmp_path / "refused"
    wt.mkdir()
    _stub_path(monkeypatch, wt)

    def _fake(args, *a, **k):
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, "0", "")
        if "remove" in args:
            return subprocess.CompletedProcess(args, 1, "", "fatal: contains modified files")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake)
    v = watcher.reclaim_worktree("t-4")
    assert v["reclaimed"] is False
    assert "git refused" in v["reason"]


def test_clean_merged_worktree_is_reclaimed(watcher, monkeypatch, tmp_path):
    wt = tmp_path / "clean"
    wt.mkdir()
    _stub_path(monkeypatch, wt)

    seen = []

    def _fake(args, *a, **k):
        seen.append(args)
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, "0", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake)
    v = watcher.reclaim_worktree("t-5")
    assert v["reclaimed"] is True
    assert any("remove" in a for a in seen)


def test_force_is_never_used(watcher):
    """--force bypasses git's own dirty/unmerged refusal — the last backstop.

    Inspects the parsed body rather than the raw source, so the prose in the
    method's own docstring explaining that --force is never used does not
    satisfy (or trip) the check.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(watcher.reclaim_worktree)))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    docstring = ast.get_docstring(tree.body[0]) or ""
    executable = [s for s in literals if s != docstring]

    offenders = [s for s in executable if s in ("--force", "-f") or s.startswith("--force")]
    assert not offenders, (
        f"reclamation passes {offenders} to git; it must never force — git's "
        "refusal is the final guard against deleting the only copy of a "
        "session's work"
    )


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def test_settings_allow_reclamation_but_deny_force():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    perms = settings["permissions"]

    assert any("git worktree remove" in a for a in perms["allow"]), (
        "the pipeline cannot reclaim a worktree without permission to remove it"
    )
    assert any("git worktree remove --force" in d for d in perms["deny"]), (
        "--force must stay denied; it bypasses the check that protects unmerged work"
    )
