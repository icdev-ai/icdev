#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""The warm worktree pool is an OPTIMISATION and never a new way to fail (mfx-own-09).

Every test here is one of the card's honesty rails. The behavioural half runs
against a real throwaway git repository, because the whole subject is git
plumbing -- a mocked `git worktree move` proves nothing about whether a
registration survives one. The structural half reads ASTs, because the failure
modes it guards (a future edit parking a task on a pool miss, a second spelling
of the add budget, a board write appearing in a library) would all pass a
behavioural test written against today's callers.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.kanban import worktree_pool as P  # noqa: E402

REFLEX = REPO_ROOT / "tools" / "genesis" / "reflexes" / "kanban.py"


def _git(args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed in {cwd}: {r.stderr}")
    return r


@pytest.fixture()
def repo(tmp_path):
    """A throwaway repository with one commit on `main`."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-b", "main"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "t"], root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "init"], root)
    return root


@pytest.fixture()
def cfg():
    return dict(P.DEFAULT_CONFIG, target_size=1, max_size=2, max_refills_per_run=1,
                refill_requires_quiet=False)


def _fill(repo, cfg, n=1):
    made = []
    for _ in range(n):
        rep = P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
        assert rep["created"] == 1, rep
        made.append(rep)
    return made


# ── the fallback rail ───────────────────────────────────────────────────────
def test_empty_pool_declines_rather_than_failing(repo, cfg):
    """An empty pool is None, not an error. The caller's inline add is the contract."""
    got = P.claim("t-1", repo / ".tmp" / "worktrees" / "t-1", repo_root=repo,
                  base="main", cfg=cfg, expect_manifest=False)
    assert got is None


def test_claim_never_raises_even_when_git_is_broken(repo, cfg, monkeypatch):
    """A pool defect must degrade to an inline add, never into a dispatch failure."""
    def boom(*_a, **_k):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(P, "worktree_listing", boom)
    assert P.claim("t-2", repo / "dest", repo_root=repo, base="main", cfg=cfg,
                   expect_manifest=False) is None


def test_unreadable_listing_declines_and_does_not_invent_an_empty_pool(repo, cfg):
    """A FAILED `git worktree list` proves nothing; reading it as empty is the
    inversion that cost three sessions their work (kpr-dup-10)."""
    _fill(repo, cfg)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(P, "worktree_listing", lambda *_a, **_k: None)
        assert P.claim("t-3", repo / "dest", repo_root=repo, base="main", cfg=cfg,
                       expect_manifest=False) is None
    # and the entry is untouched, so the next claim still gets it
    assert len([e for e in P.list_entries(repo) if e["healthy"]]) == 1


# ── the claim itself ────────────────────────────────────────────────────────
def test_claim_converts_a_pool_entry_into_the_tasks_worktree(repo, cfg):
    _fill(repo, cfg)
    before = [e["path"] for e in P.list_entries(repo) if e["healthy"]]
    dest = repo / ".tmp" / "worktrees" / "t-4"
    got = P.claim("t-4", dest, repo_root=repo, base="main", cfg=cfg,
                  expect_manifest=False)
    assert got == str(dest)
    assert (dest / ".git").exists()
    head = _git(["rev-parse", "--abbrev-ref", "HEAD"], dest).stdout.strip()
    assert head == "kanban/t-4"
    assert _git(["rev-parse", "HEAD"], dest).stdout.strip() == \
        _git(["rev-parse", "main"], repo).stdout.strip()
    # the entry left the pool, and its directory went with it
    assert not Path(before[0]).exists()
    assert [e for e in P.list_entries(repo) if e["healthy"]] == []


def test_claim_declines_when_the_task_branch_already_exists(repo, cfg):
    """The dispatcher's own stale-branch cleanup owns that case; renaming over it
    would destroy a branch that may hold work."""
    _fill(repo, cfg)
    _git(["branch", "kanban/t-5", "main"], repo)
    assert P.claim("t-5", repo / "dest", repo_root=repo, base="main", cfg=cfg,
                   expect_manifest=False) is None
    assert len([e for e in P.list_entries(repo) if e["healthy"]]) == 1


def test_claim_declines_for_a_repository_with_no_pool(repo, cfg, tmp_path):
    """An EXTERNAL-repo task (compass / idea_lab) asks its OWN repository for a
    warm entry and finds a directory nothing refills. The pool is per-repository
    by construction, so a pool entry can never be handed to a foreign checkout."""
    other = tmp_path / "other"
    other.mkdir()
    _git(["init", "-b", "main"], other)
    _fill(repo, cfg)
    assert P.claim("t-6", other / "dest", repo_root=other, base="main", cfg=cfg,
                   expect_manifest=False) is None
    assert len([e for e in P.list_entries(repo) if e["healthy"]]) == 1


def test_a_linked_worktree_reads_the_MAIN_checkouts_pool(repo, cfg):
    """MEASURED on this card's first live claim: the dispatcher hands its own
    `_repo_root` around, which is a LINKED worktree whenever a cycle runs from
    one. Resolving the pool relative to that read an empty directory and reported
    `pool_empty` for a pool that was full."""
    linked = repo.parent / "linked"
    _git(["worktree", "add", "-b", "sidebranch", str(linked), "main"], repo)
    assert P.pool_root(linked) == P.pool_root(repo)
    _fill(repo, cfg)
    got = P.claim("t-6b", repo / ".tmp" / "worktrees" / "t-6b", repo_root=linked,
                  base="main", cfg=cfg, expect_manifest=False)
    assert got is not None


# ── a half-claim is torn down, never left ───────────────────────────────────
def test_a_failed_reset_tears_the_claim_down_so_the_inline_add_starts_clean(
        repo, cfg, monkeypatch):
    """The dangerous state is a worktree at the TASK path in an unverified state:
    the dispatcher's exists-branch would adopt it and hand a worker that tree."""
    _fill(repo, cfg)
    real_git = P._git

    def fail_reset(args, cwd, timeout=60):
        if args[:1] == ["reset"]:
            return 1, "", "simulated reset failure"
        return real_git(args, cwd, timeout)

    monkeypatch.setattr(P, "_git", fail_reset)
    dest = repo / ".tmp" / "worktrees" / "t-7"
    assert P.claim("t-7", dest, repo_root=repo, base="main", cfg=cfg,
                   expect_manifest=False) is None
    monkeypatch.undo()
    assert not dest.exists()
    assert _git(["rev-parse", "--verify", "--quiet", "kanban/t-7"], repo,
                check=False).returncode != 0


def test_a_failed_verification_tears_the_claim_down(repo, cfg):
    """`expect_manifest` stands in for any post-move check: a claimed worktree
    that cannot be verified is removed rather than handed over."""
    _fill(repo, cfg)
    dest = repo / ".tmp" / "worktrees" / "t-8"
    # the fixture repo has no tools/manifest.md, so demanding one must fail
    assert P.claim("t-8", dest, repo_root=repo, base="main", cfg=cfg,
                   expect_manifest=True) is None
    assert not dest.exists()
    assert _git(["rev-parse", "--verify", "--quiet", "kanban/t-8"], repo,
                check=False).returncode != 0


# ── refill ──────────────────────────────────────────────────────────────────
def test_refill_stops_at_target_and_reports_why(repo, cfg):
    _fill(repo, cfg)
    rep = P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
    assert rep["created"] == 0 and rep["skipped"] == "at_target"


def test_refill_failures_are_named_never_swallowed(repo, cfg):
    """A refill that silently does nothing is indistinguishable from a full pool."""
    rep = P.refill(repo_root=repo, base="no-such-ref", cfg=cfg, expect_manifest=False)
    assert rep["created"] == 0
    assert rep["failures"], "a failed add must be reported"
    assert rep["failures"][0]["reason"]


def test_refill_honours_the_measured_busy_signal_only(cfg, monkeypatch):
    """WAIT is measured and honoured; UNMEASURABLE still refills, because it is
    the ordinary state of an idle board -- refusing there empties the pool
    exactly when filling it is cheapest."""
    from tools.kanban import host_io

    monkeypatch.setattr(host_io, "assess",
                        lambda *_a, **_k: {"verdict": host_io.WAIT, "reason": "slow"})
    quiet = P.quiet_check(dict(cfg, refill_requires_quiet=True))
    assert quiet["refill"] is False and quiet["basis"] == host_io.WAIT

    monkeypatch.setattr(host_io, "assess",
                        lambda *_a, **_k: {"verdict": host_io.UNMEASURABLE, "reason": "x"})
    quiet = P.quiet_check(dict(cfg, refill_requires_quiet=True))
    assert quiet["refill"] is True and quiet["basis"] == host_io.UNMEASURABLE


def test_the_pool_is_disabled_by_its_kill_switch(cfg, monkeypatch):
    monkeypatch.setenv("KANBAN_WORKTREE_POOL", "0")
    assert P.enabled(cfg) is False
    assert P.refill(cfg=cfg)["skipped"] == P.MISS_DISABLED


# ── reaping: the pool must not accumulate ───────────────────────────────────
def test_reap_removes_an_aged_entry_and_leaves_a_foreign_directory_alone(repo, cfg):
    _fill(repo, cfg)
    stray = P.pool_root(repo) / "not-a-pool-entry"
    stray.mkdir()
    later = datetime.now(timezone.utc) + timedelta(hours=48)
    out = P.reap(repo_root=repo, cfg=dict(cfg, entry_max_age_hours=24), now=later)
    assert any(r["why"] == "aged" for r in out["removed"]), out
    assert str(stray) in out["foreign"]
    assert stray.exists(), "an unrecognised directory is reported, never deleted"


def test_reap_enforces_the_ceiling(repo, cfg):
    small = dict(cfg, target_size=3, max_size=3, entry_max_age_hours=999)
    _fill(repo, small, n=3)
    out = P.reap(repo_root=repo, cfg=dict(small, max_size=1))
    assert sum(1 for r in out["removed"] if r["why"] == "over_max_size") == 2
    assert len([e for e in P.list_entries(repo) if e["hex"]]) == 1


def test_a_husk_whose_walk_is_unmeasurable_is_never_removed(repo, cfg, monkeypatch):
    """None (cannot tell) refuses -- a partial walk over-estimates the age, which
    is the direction that deletes (mfx-own-04's rule, imported)."""
    _fill(repo, cfg)
    entry = Path([e["path"] for e in P.list_entries(repo)][0])
    (entry / ".git").unlink()
    monkeypatch.setattr(P, "_husk_is_dead", lambda *_a, **_k: None)
    out = P.reap(repo_root=repo, cfg=cfg)
    assert str(entry) in out["unmeasurable"]
    assert entry.exists()


# ── measurement ─────────────────────────────────────────────────────────────
def _write_events(repo, events):
    p = P.log_file(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for ts, extra in events:
            fh.write(json.dumps({"ts": ts.isoformat(), "extra": extra}) + "\n")


def test_measure_is_unmeasurable_never_clean_when_the_pool_never_ran(repo):
    out = P.measure(24.0, repo_root=repo, get_conn=lambda: (_ for _ in ()).throw(
        RuntimeError("no board")))
    assert out["state"] == "unmeasurable"
    # None, NEVER 0: "measured clean" and "never measured" justify opposite calls
    assert out["parks_while_pool_nonempty"] is None
    assert out["claims"] is None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def test_measure_separates_a_park_with_a_warm_pool_from_one_without(repo):
    now = datetime.now(timezone.utc)
    _write_events(repo, [
        (now - timedelta(minutes=30), {"event": "pool_depth", "pool_depth": 2}),
        (now - timedelta(minutes=10), {"event": "pool_depth", "pool_depth": 0}),
    ])
    rows = [
        {"task_id": "warm", "recorded_at": (now - timedelta(minutes=20)).isoformat(),
         "reason": "worktree creation failed"},
        {"task_id": "cold", "recorded_at": (now - timedelta(minutes=5)).isoformat(),
         "reason": "worktree creation failed"},
        {"task_id": "before-any-observation",
         "recorded_at": (now - timedelta(minutes=50)).isoformat(), "reason": "x"},
    ]
    out = P.measure(24.0, repo_root=repo, get_conn=lambda: _FakeConn(rows))
    assert out["state"] == "measured"
    assert out["parks_while_pool_nonempty"] == 1      # THE FINDING
    assert out["parks_while_pool_empty"] == 1         # sanctioned fallback
    assert out["parks_unmeasurable"] == 1             # not a clean result
    assert {p["task_id"] for p in out["park_detail"]} == {
        "warm", "cold", "before-any-observation"}


def test_measure_counts_refill_failures(repo):
    now = datetime.now(timezone.utc)
    _write_events(repo, [
        (now - timedelta(minutes=5), {"event": "pool_depth", "pool_depth": 1}),
        (now - timedelta(minutes=4), {"event": "refill_failed", "reason": "budget_exceeded"}),
    ])
    out = P.measure(24.0, repo_root=repo, get_conn=lambda: _FakeConn([]))
    assert out["refill_failures"] == 1
    assert out["refill_failure_reasons"] == {"budget_exceeded": 1}


# ── structural rails ────────────────────────────────────────────────────────
def _reflex_literal(name):
    tree = ast.parse(REFLEX.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module scope in {REFLEX}")


def test_the_pool_uses_the_dispatchers_add_budget_and_never_its_own():
    """Read from the reflex's AST rather than imported: importing that module
    drags 13,840 lines into a library. A second SPELLING of one constant is how
    two halves of a policy come to disagree about a number neither changed."""
    assert P.ADD_TIMEOUT_SECONDS == _reflex_literal("WORKTREE_ADD_TIMEOUT_SECONDS") == 30
    assert tuple(P.ADD_GIT_CONFIG) == tuple(_reflex_literal("WORKTREE_ADD_GIT_CONFIG"))


def test_the_dispatchers_budget_is_not_raised_and_no_retry_is_added():
    """kph-repark-kph-repark-mfx-ci-04 forbids both, and this card does neither."""
    src = REFLEX.read_text(encoding="utf-8")
    assert "WORKTREE_ADD_TIMEOUT_SECONDS = 30" in src
    # exactly one add remains on the dispatch path
    assert src.count('"worktree", "add", "-b",') == 1


def test_the_claim_is_tried_before_the_add_and_cannot_park():
    """The pool call must sit inside `_create_worktree` ahead of the add, and its
    failure path must fall through -- never into the isolation guard."""
    tree = ast.parse(REFLEX.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_create_worktree")
    src = ast.get_source_segment(REFLEX.read_text(encoding="utf-8"), fn)
    assert "worktree_pool" in src and "_pool.claim(" in src
    assert src.index("_pool.claim(") < src.index('"worktree", "add", "-b",')
    # THE PARK LIVES IN THE CALLER, and nothing on the creation path may reach
    # it. Asserted over CALLS rather than over the text: a comment in this
    # function already names `_move_task` while calling it nowhere, and a
    # substring test would fail on the prose and pass on a real call added
    # inside a string.
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_move_task" not in calls


def test_the_pool_module_never_writes_to_the_board():
    """A library that could move a card is a library that could park one. The
    only board access here is the SELECT `measure` reads parks with."""
    src = (REPO_ROOT / "tools" / "kanban" / "worktree_pool.py").read_text(
        encoding="utf-8")
    lowered = src.lower()
    for verb in ("insert into", "update kanban", "delete from"):
        assert verb not in lowered, f"{verb!r} must not appear in the pool"
    assert "_move_task" not in src


def test_pool_branches_can_never_be_read_as_a_task_branch():
    """`kanban/` is how `_worktree_task_id`, `_branches_for_task` (mfx-own-05) and
    pr_watcher's sweep identify a TASK's branch. A pool entry belongs to no task."""
    assert P.POOL_BRANCH_PREFIX == "kanban-pool/"
    assert not P.POOL_BRANCH_PREFIX.startswith("kanban/")
    assert not P.entry_branch("abc").startswith("kanban/")


def test_the_pool_root_is_not_a_child_of_the_worktree_base():
    """Every DIRECT child of `.tmp/worktrees` is read by `worktree_husks` as
    `<base>/<task-id>`; a pool entry parked there would be refused as
    `no_board_row` on every sweep and pollute the one survey that names real
    husks."""
    assert P.POOL_RELPATH == Path(".tmp") / "worktree-pool"
    assert P.POOL_RELPATH != Path(".tmp") / "worktrees"


def test_the_sweeper_backstop_covers_the_pool_root():
    src = REFLEX.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_sweep_roots")
    assert "worktree_pool" in ast.get_source_segment(src, fn)


def test_the_pool_is_tended_on_every_exit_path_from_the_cycle():
    """`_run_cycle` returns from a dozen places, and the idle ones are exactly
    where the pool should be filling -- so the call lives in a `finally`."""
    src = REFLEX.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    body = ast.get_source_segment(src, fn)
    assert "_run_cycle(" in body
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries and any(
        "_tend_worktree_pool" in ast.get_source_segment(src, t) for t in tries)
    assert any(t.finalbody for t in tries)


# ── the lock the pool bookkeeps under ───────────────────────────────────────
def test_the_pool_lock_is_not_the_add_lock():
    """DELIBERATELY SEPARATE. `worktree_add_lock` is held for the 5-30s of a
    checkout; a claim is ~1.3s of git plumbing. Sharing them would make every
    claim queue behind whatever add is in flight -- which is exactly the wait the
    pool exists to remove, and exactly the moment the board most needs a warm
    worktree."""
    from tools.coordination import gitlock

    assert gitlock._WORKTREE_POOL_LOCK_PATH != gitlock._WORKTREE_ADD_LOCK_PATH
    assert gitlock._WORKTREE_POOL_LOCK_PATH != gitlock._GIT_LOCK_PATH


def test_holding_the_pool_lock_does_not_block_an_add():
    """A dispatch adding inline must never be delayed by pool bookkeeping."""
    from tools.coordination.gitlock import worktree_add_lock, worktree_pool_lock

    with worktree_pool_lock(timeout=5) as pool_held:
        assert pool_held is True
        with worktree_add_lock(timeout=1) as add_held:
            assert add_held is True
