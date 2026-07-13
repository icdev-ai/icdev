# CUI // SP-CTI
"""Safety tests: an external-repo kanban task never touches the ICDev tree (ked-core-04).

The invariant pinned here is the one that already bit us: a task that belongs to
another repo (``prem-*`` compass / idea_lab work, per
``args/kanban_external_repos.yaml``) must never create a branch, a worktree, or a
commit inside the ICDev checkout — and ICDev's git/gh state must never be the
authority on whether that task is done.

Coverage (task ked-core-04):
  (a) an unregistered prefix resolves to ICDev and dispatches normally — no regression
  (b) a registered-but-unconfigured external task is PARKED, not built
  (c) a configured external task builds in the target repo root, NOT in BASE_DIR
  (d) the done-gate for an external task consults the target repo's origin, not ICDev's

Nothing here shells out to real git: ``subprocess.run`` is replaced with a small
git simulator that records every (argv, cwd) it is asked to run and refuses to
materialise any path inside the ICDev checkout. A violation therefore shows up as
a failed assertion, never as a stray branch or directory in the real tree.

(c) and (d) describe behaviour introduced by ked-core-01/02/03, which make
worktree creation, dispatch and the done-gate repo-aware via ``_task_repo_root``.
Until those land the reflex parks *every* external task — safe (the invariant in
(a)/(b) and the never-touch-ICDev assertions below hold today), but it never
builds one. Those two tests are therefore gated on the helper's existence and arm
themselves automatically when it appears.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as kb  # noqa: E402

EXTERNAL_TASK = "prem-cpmp-ked04"   # matches the temp registry -> compass
ICDEV_TASK = "ked-core-ked04"       # matches no prefix -> ICDev (the default)

# _install() stubs _has_open_pr out of the dispatch path; keep a handle on the
# real one so the PR-guard test below can exercise it.
_REAL_HAS_OPEN_PR = kb._has_open_pr

requires_repo_aware = pytest.mark.skipif(
    not hasattr(kb, "_task_repo_root"),
    reason="repo-aware dispatch (ked-core-01/02/03: _task_repo_root) has not landed yet",
)

# git verbs that WRITE to a repo. A read-only probe (rev-parse, log, status) in
# the ICDev checkout is harmless; creating a branch/worktree/commit there is the
# bug this file exists to catch.
_MUTATING = (
    ("git", "worktree", "add"), ("git", "worktree", "prune"), ("git", "worktree", "remove"),
    ("git", "branch"), ("git", "checkout"), ("git", "switch"), ("git", "commit"),
    ("git", "push"), ("git", "merge"), ("git", "update-ref"), ("git", "reset"), ("git", "rm"),
)


class Spy:
    """Records everything the reflex tried to run, move, or dispatch."""

    def __init__(self):
        self.procs: list[tuple[list[str], str]] = []   # (argv, cwd)
        self.moves: list[tuple[str, str]] = []         # (task_id, new_status)
        self.work_dirs: list[str] = []                 # cwd handed to the executor

    def calls_under(self, root) -> list[list[str]]:
        root = str(pathlib.Path(root).resolve()).replace("\\", "/").rstrip("/")
        out = []
        for argv, cwd in self.procs:
            cwd_norm = str(pathlib.Path(cwd).resolve()).replace("\\", "/").rstrip("/")
            if cwd_norm == root or cwd_norm.startswith(root + "/"):
                out.append(argv)
        return out

    def mutating_calls_under(self, root) -> list[list[str]]:
        return [
            argv for argv in self.calls_under(root)
            if any(tuple(argv[:len(verb)]) == verb for verb in _MUTATING)
        ]

    @property
    def parked(self) -> list[str]:
        return [tid for tid, status in self.moves if status == "validating"]


def _install(monkeypatch, spy: Spy, *, worktree_base=None) -> None:
    """Neuter every side effect of a dispatch, keeping the routing logic intact.

    ``subprocess.run`` becomes a git simulator: a ``git worktree add`` succeeds
    (creating the .git file + tools/manifest.md the real _create_worktree checks
    for) only when the requested path lies OUTSIDE the ICDev checkout. Nothing is
    ever written into the real tree, so a mis-routed worktree fails an assertion
    instead of littering the repo.
    """
    base = pathlib.Path(kb.BASE_DIR).resolve()

    def fake_run(cmd, **kwargs):
        argv = list(cmd)
        cwd = kwargs.get("cwd") or str(base)
        spy.procs.append((argv, cwd))

        if argv[:3] == ["git", "worktree", "add"]:
            # `git worktree add -b <branch> <path> <base>` — path is argv[-2].
            path = pathlib.Path(argv[-2])
            inside_icdev = base == path.resolve() or base in path.resolve().parents
            if not inside_icdev:
                (path / "tools").mkdir(parents=True, exist_ok=True)
                (path / ".git").write_text("gitdir: fake\n", encoding="utf-8")
                (path / "tools" / "manifest.md").write_text("# fake\n", encoding="utf-8")
            # Inside ICDev: report success but create nothing. _create_worktree's
            # own .git check then fails it — and the caller's fallback to BASE_DIR
            # is caught by the work_dir assertions.
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        if argv[:3] == ["git", "rev-parse", "--verify"]:
            # No stale kanban/* branch; origin/<base> exists.
            found = argv[-1].startswith("origin/")
            return SimpleNamespace(returncode=0 if found else 1, stdout="", stderr="")

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def no_spawn(*a, **k):  # pragma: no cover — a dispatch must never spawn a process here
        raise AssertionError(f"unexpected process spawn: {a!r}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", no_spawn)

    if worktree_base is not None:
        monkeypatch.setattr(kb, "WORKTREE_BASE", pathlib.Path(worktree_base))

    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    monkeypatch.setattr(kb, "_move_task",
                        lambda tid, status, **kw: spy.moves.append((tid, status)))
    monkeypatch.setattr(kb, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(kb, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(kb, "_pre_dispatch_check", lambda task: (False, ""))
    monkeypatch.setattr(kb, "_build_instruction", lambda *a, **kw: "INSTRUCTION")
    monkeypatch.setattr(kb, "_build_effective_executor_chain", lambda chain: ["claude_cli"])
    monkeypatch.setattr(kb, "_claude_code_available", lambda: True)
    monkeypatch.setattr(kb, "_set_executor_type", lambda tid, kind: None)
    monkeypatch.setattr(
        kb, "_dispatch_via_claude_cli",
        lambda task, prompt_path, instruction, work_dir, task_log: spy.work_dirs.append(work_dir),
    )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Temp registry: prem-cpmp -> compass, root supplied by TEST_KED_COMPASS_ROOT.

    Using a temp config + test-only env var keeps the suite independent of the
    shipped yaml and of whatever the host has configured for the real compass.
    """
    cfg = tmp_path / "kanban_external_repos.yaml"
    cfg.write_text(textwrap.dedent("""
        repos:
          compass: { base_branch: main, root_env: TEST_KED_COMPASS_ROOT }
        prefixes:
          prem-cpmp: compass
    """), encoding="utf-8")
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(cfg))
    monkeypatch.delenv("TEST_KED_COMPASS_ROOT", raising=False)   # unconfigured by default
    return cfg


@pytest.fixture
def compass_root(tmp_path, monkeypatch, registry):
    """A configured external repo root — the target an external task must build in."""
    root = tmp_path / "compass"
    root.mkdir()
    monkeypatch.setenv("TEST_KED_COMPASS_ROOT", str(root))
    return root


@pytest.fixture
def prompt(tmp_path):
    p = tmp_path / "prompt.md"
    p.write_text("do the thing", encoding="utf-8")
    return str(p)


def _task(task_id):
    return {"id": task_id, "title": "T", "description": "d",
            "task_type": "chore", "failure_count": 0, "max_retries": 5}


# ── (a) unregistered prefix → ICDev, dispatches normally ─────────────────────

def test_icdev_task_still_dispatches_into_the_icdev_tree(monkeypatch, tmp_path, registry, prompt):
    """No-regression: a task matching no prefix is ICDev's and builds in ICDev."""
    spy = Spy()
    icdev_worktrees = tmp_path / "icdev_worktrees"
    _install(monkeypatch, spy, worktree_base=icdev_worktrees)

    kb._dispatch_to_claude(_task(ICDEV_TASK), prompt)

    assert spy.parked == [], "an ICDev task must not be parked as external"
    assert len(spy.work_dirs) == 1, "the executor must be dispatched exactly once"
    # It built in ICDev's own worktree base, off ICDev's checkout.
    assert spy.work_dirs[0] == str(icdev_worktrees / ICDEV_TASK)
    added = [argv for argv, _ in spy.procs if argv[:3] == ["git", "worktree", "add"]]
    assert added, "the ICDev path must still create a worktree"
    assert spy.mutating_calls_under(kb.BASE_DIR), (
        "regression: the ICDev worktree is no longer created from the ICDev checkout"
    )


# ── (b) registered but unconfigured → PARKED, not built ─────────────────────

def test_unconfigured_external_task_is_parked_not_built(monkeypatch, registry, prompt):
    """Root env unset => external-but-unconfigured => park; never fall back to ICDev."""
    spy = Spy()
    _install(monkeypatch, spy)

    kb._dispatch_to_claude(_task(EXTERNAL_TASK), prompt)

    assert spy.parked == [EXTERNAL_TASK], "an unconfigured external task must be parked"
    assert spy.work_dirs == [], "a parked task must never reach an executor"
    assert spy.mutating_calls_under(kb.BASE_DIR) == [], (
        "an unconfigured external task wrote to the ICDev checkout"
    )


# ── The headline invariant: ICDev is never touched, configured or not ───────

def test_configured_external_task_never_touches_the_icdev_tree(monkeypatch, compass_root, prompt):
    """Whatever the reflex does with an external task — park it today, build it in
    compass tomorrow — it must not create a branch, worktree or commit in ICDev.

    Unconditional by design: this holds under the current park-everything guard AND
    under repo-aware dispatch, so it keeps guarding the invariant while ked-core-01/
    02/03 are in flight.
    """
    spy = Spy()
    _install(monkeypatch, spy)

    kb._dispatch_to_claude(_task(EXTERNAL_TASK), prompt)

    assert spy.mutating_calls_under(kb.BASE_DIR) == [], (
        "an external task ran a writing git command in the ICDev checkout"
    )
    for work_dir in spy.work_dirs:
        resolved = pathlib.Path(work_dir).resolve()
        assert pathlib.Path(kb.BASE_DIR).resolve() not in [resolved, *resolved.parents], (
            f"the executor for an external task was pointed at the ICDev tree: {work_dir}"
        )
    # And no worktree was left behind in ICDev's worktree base.
    assert not (pathlib.Path(kb.WORKTREE_BASE) / EXTERNAL_TASK).exists()


# ── (c) configured external task builds in the target repo root ─────────────

@requires_repo_aware
def test_configured_external_task_builds_in_the_target_repo(monkeypatch, compass_root, prompt):
    """Root env set => build there: worktree created from compass, executor cwd in compass."""
    spy = Spy()
    _install(monkeypatch, spy)

    kb._dispatch_to_claude(_task(EXTERNAL_TASK), prompt)

    assert spy.parked == [], "a CONFIGURED external task must be built, not parked"
    assert len(spy.work_dirs) == 1, "the executor must be dispatched into the external repo"

    work_dir = pathlib.Path(spy.work_dirs[0]).resolve()
    assert compass_root.resolve() in [work_dir, *work_dir.parents], (
        f"executor cwd {work_dir} is not inside the target repo {compass_root}"
    )
    added = [argv for argv in spy.calls_under(compass_root)
             if argv[:3] == ["git", "worktree", "add"]]
    assert added, "the worktree must be created FROM the target repo root"
    assert "origin/main" in added[0], "the external worktree must branch off the TARGET repo's base"


@requires_repo_aware
def test_task_repo_root_resolves_icdev_and_external(compass_root):
    """The helper the rest of the repo-aware path hangs off of."""
    assert pathlib.Path(kb._task_repo_root(EXTERNAL_TASK)).resolve() == compass_root.resolve()
    assert pathlib.Path(kb._task_repo_root(ICDEV_TASK)).resolve() == pathlib.Path(kb.BASE_DIR).resolve()


# ── (d) the done-gate asks the TARGET repo's origin, not ICDev's ────────────

@requires_repo_aware
def test_done_gate_for_external_task_consults_the_target_origin(monkeypatch, compass_root):
    """_branch_has_unmerged_commits must ask compass whether compass's work landed.

    Asking ICDev is the exact churn repo_registry was written to stop: ICDev's
    origin/main will never carry a compass commit, so the task can never be done.
    """
    spy = Spy()
    _install(monkeypatch, spy)

    kb._branch_has_unmerged_commits(EXTERNAL_TASK)

    assert spy.procs, "the done-gate must actually consult git"
    assert spy.calls_under(kb.BASE_DIR) == [], (
        "the done-gate asked ICDev whether an external task's work landed"
    )
    assert spy.calls_under(compass_root), "the done-gate must consult the target repo"


@requires_repo_aware
def test_open_pr_guard_for_external_task_consults_the_target_repo(monkeypatch, compass_root):
    """gh infers the repo from cwd, so the PR guard must run in the target repo."""
    spy = Spy()
    _install(monkeypatch, spy)

    _REAL_HAS_OPEN_PR(EXTERNAL_TASK)

    gh_calls = [argv for argv in spy.calls_under(compass_root) if argv[:1] == ["gh"]]
    assert gh_calls, "the PR guard must run gh inside the target repo"
    assert [argv for argv in spy.calls_under(kb.BASE_DIR) if argv[:1] == ["gh"]] == [], (
        "the PR guard asked ICDev's remote about an external task's PR"
    )


def test_done_gate_for_icdev_task_still_consults_icdev(monkeypatch, registry):
    """No-regression twin of (d): ICDev tasks are still judged by ICDev's origin."""
    spy = Spy()
    _install(monkeypatch, spy)

    kb._branch_has_unmerged_commits(ICDEV_TASK)

    assert spy.calls_under(kb.BASE_DIR), "the ICDev done-gate must still consult ICDev"
