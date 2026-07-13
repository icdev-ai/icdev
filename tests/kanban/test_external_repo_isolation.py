# CUI // SP-CTI
"""Safety — an external-repo task must NEVER touch the ICDev checkout.

This is the invariant that already bit us: a ``prem-*`` task whose deliverables
land in compass / idea_lab was dispatched into an ICDev worktree, branched off
ICDev's origin/main, and then judged by ICDev's merge-to-origin/main done-gate.
It could never pass, so it churned — burning agent sessions re-doing work the
external repo's own session had already shipped.

The invariant pinned here is deliberately a NEGATIVE one, because it must hold
both before and after the dispatcher learns to build INTO the external repo
(ked-core-01/02/03):

    For an external task, ICDev gains no branch, no worktree, and no commit.

Today an external task is parked inertly (``validating``) by the repo-aware
guard in ``_dispatch_to_claude``, so nothing is built anywhere. Once ked-core-01
and ked-core-02 land, the task will instead be built in the TARGET repo's root.
The ICDev-tree assertions below must survive that change unaltered — that is the
point of writing them against a real on-disk git repo rather than against the
guard's current internals.

The two POSITIVE halves — "builds in the target repo root" (ked-core-01/02) and
"the done-gate consults the target repo's origin" (ked-core-03) — are not
implemented yet and are marked ``xfail(strict=True)``. When those tasks land,
the xfails turn into XPASS and the suite goes red until the marker is removed,
which is the intended ratchet: the spec cannot silently rot.

Nothing here touches the real ICDev checkout. ``BASE_DIR`` / ``WORKTREE_BASE``
are redirected at a throwaway git repo under ``tmp_path``, so a regression that
DOES create an ICDev branch shows up as a failure here, not as damage on disk.
"""
from __future__ import annotations

import subprocess
import textwrap

import pytest

import tools.genesis.reflexes.kanban as km

_GIT_ID = ["-c", "user.email=t@t.test", "-c", "user.name=Test"]

# Task ids: 'prem-cpmp-*' is registered external (compass); 'ctx-core-*' matches
# no prefix and must therefore resolve to ICDev exactly as it always has.
EXTERNAL_TASK = "prem-cpmp-42"
ICDEV_TASK = "ctx-core-42"


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_GIT_ID, *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )


def _make_repo(root, seed_files):
    """A real git repo on ``main`` with a bare origin and one pushed commit."""
    origin = root.parent / f"{root.name}-origin.git"
    root.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=root)
    # `git init -b main` needs git>=2.28; set the ref explicitly so old git works.
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=root)
    for rel, body in seed_files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    subprocess.run(["git", "init", "--bare", str(origin)],
                   capture_output=True, text=True, timeout=30)
    _git("remote", "add", "origin", str(origin), cwd=root)
    _git("push", "-u", "origin", "main", cwd=root)
    return root


def _commit_on_branch(root, branch: str, filename: str = "work.txt") -> None:
    """Create ``branch`` off main with one commit that is NOT on origin/main."""
    _git("checkout", "-b", branch, "main", cwd=root)
    (root / filename).write_text("work\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", f"work on {branch}", cwd=root)
    _git("checkout", "main", cwd=root)


@pytest.fixture
def icdev_repo(tmp_path):
    """Stand-in for the ICDev checkout. Must come out of every test unchanged.

    Carries tools/manifest.md because _create_worktree treats its absence as a
    partial checkout, tears the worktree down, and falls back to BASE_DIR.
    """
    return _make_repo(
        tmp_path / "icdev",
        {"README.md": "icdev\n", "tools/manifest.md": "# tools\n"},
    )


@pytest.fixture
def external_repo(tmp_path):
    """Stand-in for the compass checkout — the repo the task really belongs to.

    Deliberately has NO tools/manifest.md: compass is not ICDev and does not
    have one. _create_worktree's structural check is ICDev-specific, so if the
    repo-aware dispatcher reuses it unchanged against compass it will delete the
    external worktree and fall back to BASE_DIR — i.e. build the external task
    inside ICDev, the exact bug this file exists to prevent. Seeding a fake
    manifest here would hide that; the safety test must be allowed to catch it.
    """
    return _make_repo(tmp_path / "compass", {"README.md": "compass\n"})


@pytest.fixture
def registry(tmp_path):
    p = tmp_path / "kanban_external_repos.yaml"
    p.write_text(textwrap.dedent("""
        repos:
          compass: { base_branch: main, root_env: TEST_KED_COMPASS_ROOT }
        prefixes:
          prem-cpmp: compass
    """), encoding="utf-8")
    return p


@pytest.fixture
def dispatched(monkeypatch, icdev_repo, registry, tmp_path):
    """Redirect kanban at the fake ICDev repo and neuter every real executor.

    Returns a dict that records what the dispatcher did:
      ``moves``     — every _move_task(task_id, status, ...) call
      ``work_dirs`` — the cwd each executor was handed (the load-bearing one:
                      for an external task it must never be inside ICDev)
      ``proceeded`` — task ids that got PAST the repo-aware guard
    """
    seen: dict = {"moves": [], "work_dirs": [], "proceeded": []}

    worktree_base = icdev_repo / ".tmp" / "worktrees"
    monkeypatch.setattr(km, "BASE_DIR", icdev_repo)
    monkeypatch.setattr(km, "WORKTREE_BASE", worktree_base)
    monkeypatch.setattr(km, "PROMPT_DIR", tmp_path / "prompts")
    (tmp_path / "prompts").mkdir(exist_ok=True)
    # _default_branch() shells out to BASE_DIR and caches process-wide; pin it so
    # one test can't poison another and so we never probe the real checkout.
    monkeypatch.setattr(km, "_default_branch_cache", "main")
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(registry))
    # Single-tier chain: the claude_cli executor, which we replace with a spy.
    monkeypatch.setenv("ICDEV_KANBAN_EXECUTOR_CHAIN", "claude_cli")
    km._worktrees.clear()
    km._dispatch_main_heads.clear()

    def _record_move(task_id, new_status, actor="scheduler", reason=None, **_kw):
        seen["moves"].append(
            {"id": task_id, "status": new_status, "actor": actor, "reason": reason}
        )

    def _record_proceed(task_id, within_minutes=30):
        # First collaborator called after the repo-aware guard — a task reaching
        # it is proof the guard let it through rather than parking it.
        seen["proceeded"].append(task_id)
        return False

    def _record_exec(task, prompt_path, instruction, work_dir, task_log):
        seen["work_dirs"].append(str(work_dir))

    monkeypatch.setattr(km, "_move_task", _record_move)
    monkeypatch.setattr(km, "_had_recent_success", _record_proceed)
    monkeypatch.setattr(km, "_has_open_pr", lambda _tid: False)
    monkeypatch.setattr(km, "_pre_dispatch_check", lambda _t: (False, ""))
    monkeypatch.setattr(km, "_build_instruction", lambda *_a, **_kw: "instruction")
    monkeypatch.setattr(km, "_claude_code_available", lambda: True)
    monkeypatch.setattr(km, "_dispatch_via_claude_cli", _record_exec)
    monkeypatch.setattr(km, "_set_executor_type", lambda *_a, **_kw: None)
    return seen


def _prompt_for(tmp_path, task_id: str) -> str:
    p = tmp_path / f"{task_id}.md"
    p.write_text(f"# {task_id}\nDo the thing.\n", encoding="utf-8")
    return str(p)


def _icdev_footprint(icdev_repo) -> dict:
    """Everything an external task is forbidden from adding to the ICDev tree."""
    branches = _git("branch", "--list", "--format=%(refname:short)", cwd=icdev_repo)
    worktrees = _git("worktree", "list", "--porcelain", cwd=icdev_repo)
    head = _git("rev-parse", "HEAD", cwd=icdev_repo)
    wt_base = icdev_repo / ".tmp" / "worktrees"
    return {
        "branches": sorted(b for b in branches.stdout.split() if b),
        "worktree_lines": sorted(
            ln for ln in worktrees.stdout.splitlines() if ln.startswith("worktree ")
        ),
        "head": head.stdout.strip(),
        "worktree_dirs": sorted(p.name for p in wt_base.iterdir()) if wt_base.is_dir() else [],
    }


# ── (a) no regression: an unregistered prefix is still an ICDev task ──────────

def test_unregistered_prefix_dispatches_normally_in_icdev(dispatched, tmp_path):
    """A task matching no registry prefix must sail past the guard untouched."""
    task = {"id": ICDEV_TASK, "title": "Normal ICDev task"}
    km._dispatch_to_claude(task, _prompt_for(tmp_path, ICDEV_TASK))

    # It reached the first post-guard collaborator => the guard did not park it.
    assert dispatched["proceeded"] == [ICDEV_TASK]
    parks = [m for m in dispatched["moves"] if m["actor"] == "repo-aware-guard"]
    assert parks == [], f"ICDev task was wrongly parked as external: {parks}"


def test_unregistered_prefix_builds_inside_icdev(dispatched, icdev_repo, tmp_path):
    """The ICDev path is unchanged: the worktree IS in the ICDev tree."""
    task = {"id": ICDEV_TASK, "title": "Normal ICDev task"}
    km._dispatch_to_claude(task, _prompt_for(tmp_path, ICDEV_TASK))

    assert dispatched["work_dirs"], "ICDev task was never handed to an executor"
    work_dir = dispatched["work_dirs"][0].replace("\\", "/")
    assert str(icdev_repo).replace("\\", "/") in work_dir
    # ...and it really did get an ICDev worktree + branch, as it always has.
    fp = _icdev_footprint(icdev_repo)
    assert f"kanban/{ICDEV_TASK}" in fp["branches"]
    assert ICDEV_TASK in fp["worktree_dirs"]


# ── (b) a registered-but-unconfigured external task is PARKED, not built ──────

def test_unconfigured_external_task_is_parked_not_built(
    dispatched, icdev_repo, monkeypatch, tmp_path
):
    """Root env unset => park inertly. Never fall back to building in ICDev."""
    monkeypatch.delenv("TEST_KED_COMPASS_ROOT", raising=False)
    before = _icdev_footprint(icdev_repo)

    task = {"id": EXTERNAL_TASK, "title": "Compass work"}
    km._dispatch_to_claude(task, _prompt_for(tmp_path, EXTERNAL_TASK))

    parks = [m for m in dispatched["moves"] if m["actor"] == "repo-aware-guard"]
    assert len(parks) == 1, f"expected exactly one park, got {dispatched['moves']}"
    assert parks[0]["id"] == EXTERNAL_TASK
    assert parks[0]["status"] == "validating"
    assert "compass" in (parks[0]["reason"] or "")

    # Parked means parked: it never reached the dispatch pipeline at all.
    assert dispatched["proceeded"] == []
    assert dispatched["work_dirs"] == []
    assert _icdev_footprint(icdev_repo) == before


# ── (c) a CONFIGURED external task builds in the target repo, not in ICDev ────

def test_configured_external_task_leaves_icdev_tree_untouched(
    dispatched, icdev_repo, external_repo, monkeypatch, tmp_path
):
    """THE safety invariant. Green today (parked) and after ked-core-01/02
    (built in compass). Either way ICDev gains no branch, worktree, or commit."""
    monkeypatch.setenv("TEST_KED_COMPASS_ROOT", str(external_repo))
    before = _icdev_footprint(icdev_repo)

    task = {"id": EXTERNAL_TASK, "title": "Compass work"}
    km._dispatch_to_claude(task, _prompt_for(tmp_path, EXTERNAL_TASK))

    after = _icdev_footprint(icdev_repo)
    assert after["branches"] == before["branches"], (
        f"external task created branch(es) in the ICDev tree: "
        f"{set(after['branches']) - set(before['branches'])}"
    )
    assert after["worktree_dirs"] == before["worktree_dirs"] == []
    assert after["worktree_lines"] == before["worktree_lines"]
    assert after["head"] == before["head"], "external task committed into ICDev"

    # Whatever the executor was handed, it must not be inside the ICDev checkout.
    icdev_str = str(icdev_repo).replace("\\", "/")
    for wd in dispatched["work_dirs"]:
        assert icdev_str not in wd.replace("\\", "/"), (
            f"external task was dispatched into the ICDev tree: {wd}"
        )


@pytest.mark.xfail(
    strict=True,
    reason="ked-core-01/02 not landed: the dispatcher parks configured external "
           "tasks instead of building them in the target repo. When those land "
           "this XPASSes — delete this marker.",
)
def test_configured_external_task_builds_in_target_repo(
    dispatched, external_repo, monkeypatch, tmp_path
):
    """The positive half of (c): the executor runs in the compass checkout."""
    monkeypatch.setenv("TEST_KED_COMPASS_ROOT", str(external_repo))

    task = {"id": EXTERNAL_TASK, "title": "Compass work"}
    km._dispatch_to_claude(task, _prompt_for(tmp_path, EXTERNAL_TASK))

    assert dispatched["work_dirs"], "configured external task was never built"
    work_dir = dispatched["work_dirs"][0].replace("\\", "/")
    assert str(external_repo).replace("\\", "/") in work_dir


# ── (d) the done-gate for an external task consults the TARGET repo's origin ──

def test_done_gate_for_icdev_task_still_consults_icdev(dispatched, icdev_repo):
    """No regression: an ICDev branch with unmerged commits still blocks done."""
    _commit_on_branch(icdev_repo, f"kanban/{ICDEV_TASK}")
    assert km._branch_has_unmerged_commits(ICDEV_TASK) is True
    # A task with no branch at all fails open (never wedge completion).
    assert km._branch_has_unmerged_commits("ctx-core-nonexistent") is False


@pytest.mark.xfail(
    strict=True,
    reason="ked-core-03 not landed: _branch_has_unmerged_commits is repo-blind "
           "(cwd=BASE_DIR), so it looks for the branch in ICDev, doesn't find it, "
           "and fails open. When ked-core-03 lands this XPASSes — delete this marker.",
)
def test_done_gate_for_external_task_consults_target_origin(
    dispatched, icdev_repo, external_repo, monkeypatch
):
    """The gate must compare the branch against COMPASS's origin/main.

    The branch exists only in compass, with a commit that is not on compass's
    origin/main. A repo-aware gate answers True (work not landed => not done).
    A repo-blind gate looks in ICDev, finds no branch, and fails open (False) —
    which is exactly how an external task reached 'done' without shipping.
    """
    monkeypatch.setenv("TEST_KED_COMPASS_ROOT", str(external_repo))
    _commit_on_branch(external_repo, f"kanban/{EXTERNAL_TASK}")
    # The branch deliberately does NOT exist in ICDev.
    assert f"kanban/{EXTERNAL_TASK}" not in _icdev_footprint(icdev_repo)["branches"]

    assert km._branch_has_unmerged_commits(EXTERNAL_TASK) is True
