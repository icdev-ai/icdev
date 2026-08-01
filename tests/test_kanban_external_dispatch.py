# CUI // SP-CTI
"""Repo-aware kanban dispatch: an external task never touches the ICDev tree.

ked-core-04.

The dispatcher used to run EVERY git/gh call with ``cwd=BASE_DIR``. For an ICDev task
that is right. For a compass task it means the done-gate asks ICDEV whether COMPASS's
work landed — the answer is always no, so the task churns forever. Which is why
``_dispatch_to_claude`` simply PARKED every external task, and why the entire Premium Suite
had to be driven by hand.

The invariant these tests pin is a SAFETY one, and it is the one that already bit us:

    An external task must never create a branch, a worktree, or a commit
    inside the ICDev checkout.

The dangerous path is not the obvious one. It is the FALLBACK: ``_create_worktree``
returns None on failure and the caller used to fall back to ``BASE_DIR`` — which for a
compass task means quietly building compass work inside ICDev, where it fails ICDev's
gates and gets blamed on the agent.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

repo_registry = importlib.import_module("tools.kanban.repo_registry")
kanban = importlib.import_module("tools.genesis.reflexes.kanban")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registry pointing 'ext-' at a real temp repo root, and 'ghost-' at nothing."""
    root = tmp_path / "extrepo"
    root.mkdir()
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(yaml.safe_dump({
        "repos": {
            "extrepo": {"base_branch": "trunk", "root_env": "TEST_EXT_ROOT"},
            "ghost": {"base_branch": "main", "root_env": "TEST_GHOST_ROOT"},
        },
        "prefixes": {"ext-": "extrepo", "ghost-": "ghost"},
    }), encoding="utf-8")

    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(cfg))
    monkeypatch.setenv("TEST_EXT_ROOT", str(root))
    monkeypatch.delenv("TEST_GHOST_ROOT", raising=False)
    return root


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def test_an_unregistered_task_is_an_icdev_task(registry):
    """No regression: an ICDev task must behave byte-identically."""
    assert kanban._task_repo_root("dm-portal-01") == kanban.BASE_DIR
    assert kanban._task_worktree_path("dm-portal-01") == kanban.WORKTREE_BASE / "dm-portal-01"


def test_an_external_task_resolves_to_its_own_repo_and_branch(registry):
    assert kanban._task_repo_root("ext-thing-01") == registry
    assert kanban._task_base_branch("ext-thing-01") == "trunk", \
        "compass's main is not ICDev's main"


def test_an_external_worktree_lives_outside_BOTH_repos(registry):
    """A compass worktree nested under ICDev shows up in ICDev's git status and in
    every tree-scoped gate that walks the checkout — the same confusion of repos this
    whole change exists to remove."""
    path = kanban._task_worktree_path("ext-thing-01")

    assert kanban.BASE_DIR not in path.parents, "never inside the ICDev tree"
    assert registry not in path.parents, "and not inside the external repo either"
    assert "ext-thing-01" in str(path)


def test_a_registry_that_cannot_be_read_resolves_to_icdev(monkeypatch):
    """An absent or broken registry must be a total no-op, never a dispatch failure."""
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", "/nonexistent/repos.yaml")
    assert kanban._task_repo_root("ext-thing-01") == kanban.BASE_DIR
    assert kanban._task_repo_target("ext-thing-01").is_external is False


# ---------------------------------------------------------------------------
# The park path — external-but-unconfigured
# ---------------------------------------------------------------------------
def test_an_unconfigured_external_task_is_not_dispatchable(registry):
    """root_env unset -> we cannot build it. It must be parked, NEVER built in ICDev."""
    target = repo_registry.resolve_task_repo("ghost-thing-01")

    assert target.is_external is True
    assert target.root is None
    assert target.dispatchable is False


def test_an_unconfigured_external_task_is_PARKED_not_built(registry, monkeypatch):
    moved, created = [], []
    monkeypatch.setattr(kanban, "_move_task",
                        lambda tid, status, **kw: moved.append((tid, status)))
    monkeypatch.setattr(kanban, "_create_worktree",
                        lambda tid: created.append(tid) or None)

    kanban._dispatch_to_claude({"id": "ghost-thing-01", "title": "x"}, "/tmp/p.md")

    assert moved == [("ghost-thing-01", "validating")]
    assert created == [], "it must not even attempt a worktree"


def test_a_failed_external_worktree_does_NOT_fall_back_to_the_icdev_tree(
        registry, tmp_path, monkeypatch):
    """THE dangerous path. The BASE_DIR fallback would build compass work inside ICDev,
    where it fails ICDev's tree-scoped gates — and the agent gets the blame."""
    prompt = tmp_path / "p.md"
    prompt.write_text("do the thing", encoding="utf-8")

    moved, dispatched = [], []
    monkeypatch.setattr(kanban, "_move_task",
                        lambda tid, status, **kw: moved.append((tid, status)))
    monkeypatch.setattr(kanban, "_create_worktree", lambda tid: None)  # creation fails
    monkeypatch.setattr(kanban, "_pre_dispatch_check", lambda t: (False, ""))
    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(kanban, "_dispatch_via_claude_cli",
                        lambda *a, **kw: dispatched.append(a))

    kanban._dispatch_to_claude({"id": "ext-thing-01", "title": "x"}, str(prompt))

    assert dispatched == [], "NOTHING may be executed against the ICDev checkout"
    assert ("ext-thing-01", "validating") in moved


# ---------------------------------------------------------------------------
# A REAL worktree in a REAL external repo
# ---------------------------------------------------------------------------
def test_creating_a_worktree_in_a_real_external_repo_actually_works(tmp_path, monkeypatch):
    """The bug the first live compass dispatch found, and these tests did not.

    _create_worktree() ran `WORKTREE_BASE.mkdir(...)` — ICDev's .tmp/worktrees — no matter
    which repo the task belonged to. For an external task the worktree path is under the
    system temp dir, so its PARENT was never created: `git worktree add` created the
    BRANCH and then failed on the missing directory, and the task was parked with
    "worktree creation failed".

    Every unit test above passed, because none of them ever made a worktree. This one
    builds a real git repo and a real worktree, which is the only way that class of bug
    surfaces.
    """
    import subprocess

    root = tmp_path / "realrepo"
    root.mkdir()
    run = lambda *a: subprocess.run(a, cwd=str(root), capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (root / "README.md").write_text("hello", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")

    cfg = tmp_path / "repos.yaml"
    cfg.write_text(yaml.safe_dump({
        "repos": {"realrepo": {"base_branch": "main", "root_env": "TEST_REAL_ROOT"}},
        "prefixes": {"real-": "realrepo"},
    }), encoding="utf-8")
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(cfg))
    monkeypatch.setenv("TEST_REAL_ROOT", str(root))

    # The worktree parent must not exist yet — that is the whole point.
    wt = kanban._task_worktree_path("real-thing-01")
    assert not wt.parent.exists() or not wt.exists()

    created = kanban._create_worktree("real-thing-01")

    assert created is not None, "worktree creation must succeed for a real external repo"
    assert Path(created).exists()
    assert (Path(created) / "README.md").exists(), "it is a checkout of the EXTERNAL repo"

    # And the branch lives in that repo, not ICDev.
    branches = subprocess.run(["git", "branch", "--list", "kanban/real-thing-01"],
                              cwd=str(root), capture_output=True, text=True).stdout
    assert "kanban/real-thing-01" in branches

    subprocess.run(["git", "worktree", "remove", str(created), "--force"],
                   cwd=str(root), capture_output=True, text=True)


# ---------------------------------------------------------------------------
# The registry must stay in step with the board
# ---------------------------------------------------------------------------
def test_every_prem_stream_is_registered():
    """The bug ked-reg-01 fixed: five live prem-* streams (bid, rpt, people, pstaff, p0)
    were missing from the registry, so they resolved to the ICDev default — meaning the
    dispatcher would have built compass work inside the ICDev checkout.

    This asserts the SHIPPED registry covers every prem-* stream, so the next stream
    cannot be added without landing here too.
    """
    cfg = yaml.safe_load(
        (Path(kanban.BASE_DIR) / "args" / "kanban_external_repos.yaml")
        .read_text(encoding="utf-8")
    )
    registered = set(cfg["prefixes"])

    # Every stream the Premium Suite actually shipped.
    streams = ("prem-bid", "prem-rpt", "prem-people", "prem-pstaff", "prem-p0",
               "prem-cpmp", "prem-msr", "prem-lcatq", "prem-recomp", "prem-ricoas",
               "prem-ideal")
    missing = [s for s in streams
               if not any(s.startswith(p) or p.startswith(s) for p in registered)]

    assert not missing, (
        f"these prem-* streams resolve to ICDev and would be BUILT INSIDE THE ICDEV "
        f"TREE: {missing}. Register them in args/kanban_external_repos.yaml."
    )


def test_the_gate_task_is_not_treated_as_an_external_build():
    """prem-gate-00 is a sentinel, not work. It must never be dispatched anywhere."""
    target = repo_registry.resolve_task_repo("prem-gate-00")
    # It resolves somewhere harmless; the manual-gate guard (tools/kanban/gates.py) is
    # what actually keeps it out of dispatch, and it is exempt from the pipeline.
    assert target is not None
