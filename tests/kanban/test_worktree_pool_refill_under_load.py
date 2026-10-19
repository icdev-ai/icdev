#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""The warm pool must be WARM when a loaded host needs it (mfx-own-10).

Measured 2026-10-18: 11 of 12 parks landed while the pool was EMPTY; refills
died at the 30s budget 30 times in 50; a burst drained target=2 in seconds and
the quiet gate then refused to refill. And `--measure` itself had gone blind --
it read only the live log, which rotates daily, so a 24h window asked in the
morning saw one hour. Each test below is one of the levers, held to account.

The dispatch-path rails of mfx-own-09 are NOT reopened and stay in
tests/kanban/test_worktree_pool.py: the dispatch add keeps its 30s, no retry is
added, and a pool miss never parks.
"""
from __future__ import annotations

import ast
import contextlib
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
POOL_YAML = REPO_ROOT / "args" / "worktree_pool.yaml"


def _git(args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed in {cwd}: {r.stderr}")
    return r


@pytest.fixture()
def repo(tmp_path):
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
    return dict(P.DEFAULT_CONFIG, target_size=1, max_size=3, max_refills_per_run=1,
                refill_requires_quiet=False)


@pytest.fixture()
def events(monkeypatch):
    seen = []
    real = P._log_event

    def capture(event, **fields):
        seen.append({"event": event, **fields})
        real(event, **fields)

    monkeypatch.setattr(P, "_log_event", capture)
    return seen


def _reflex_fn(name):
    src = REFLEX.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    return src, fn, ast.get_source_segment(src, fn)


# ── the refill's own budget ────────────────────────────────────────────────
def test_the_refill_budget_is_its_own_and_fits_inside_the_dispatchers_lock_wait():
    """A refill holds the add lock while it checks out. At or under the
    dispatcher's lock wait, a dispatch queued behind it always gets the lock
    (and then claims the entry); over it, the dispatch would time out on the
    wait and overlap the refill -- the collision the lock exists to prevent."""
    tree = ast.parse(REFLEX.read_text(encoding="utf-8"))
    lock_wait = None
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "WORKTREE_ADD_LOCK_WAIT_SECONDS"
                        for t in node.targets)):
            lock_wait = ast.literal_eval(node.value.args[1])
    assert lock_wait == 90
    assert P.ADD_TIMEOUT_SECONDS == 30, "the DISPATCH budget is not touched"
    assert P.ADD_TIMEOUT_SECONDS < P.REFILL_ADD_TIMEOUT_SECONDS <= lock_wait
    declared = P.load_config(POOL_YAML)["refill_add_timeout_seconds"]
    assert P.ADD_TIMEOUT_SECONDS < declared <= lock_wait


def test_a_refill_add_runs_under_the_refill_budget_not_the_dispatch_budget(
        repo, cfg, monkeypatch):
    seen = {}

    def fake_create(root, base, expect_manifest, timeout=None):
        seen["timeout"] = timeout
        return {"ok": False, "reason": "budget_exceeded", "hex": "x",
                "budget_seconds": timeout}

    monkeypatch.setattr(P, "_create_entry", fake_create)
    rep = P.refill(repo_root=repo, base="main",
                   cfg=dict(cfg, refill_add_timeout_seconds=77), expect_manifest=False)
    assert seen["timeout"] == 77
    assert rep["failures"][0]["budget_seconds"] == 77


# ── an empty pool is not protected by declining to fill it ─────────────────
def test_an_empty_pool_refills_even_when_the_host_reports_wait(
        repo, cfg, monkeypatch):
    from tools.kanban import host_io

    monkeypatch.setattr(host_io, "assess",
                        lambda *_a, **_k: {"verdict": host_io.WAIT, "reason": "slow"})
    rep = P.refill(repo_root=repo, base="main",
                   cfg=dict(cfg, refill_requires_quiet=True), expect_manifest=False)
    assert rep["created"] == 1, rep
    assert rep["quiet_basis"] == f"{host_io.WAIT}+pool_empty_override"


def test_a_pool_with_an_entry_in_hand_still_honours_wait(repo, cfg, monkeypatch):
    from tools.kanban import host_io

    P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
    monkeypatch.setattr(host_io, "assess",
                        lambda *_a, **_k: {"verdict": host_io.WAIT, "reason": "slow"})
    rep = P.refill(repo_root=repo, base="main",
                   cfg=dict(cfg, target_size=2, refill_requires_quiet=True),
                   expect_manifest=False)
    assert rep["created"] == 0 and rep["skipped"] == "host_busy"


# ── a burst is refilled in one run, without starving a dispatch add ────────
def test_one_run_fills_to_target_taking_the_add_lock_per_add(repo, cfg, monkeypatch):
    import tools.coordination.gitlock as gitlock

    takes = []

    @contextlib.contextmanager
    def counting_lock(timeout=60.0):
        takes.append(timeout)
        yield True

    monkeypatch.setattr(gitlock, "worktree_add_lock", counting_lock)
    rep = P.refill(repo_root=repo, base="main",
                   cfg=dict(cfg, target_size=3, max_refills_per_run=3),
                   expect_manifest=False)
    assert rep["created"] == 3 and rep["depth_after"] == 3, rep
    assert len(takes) == 3, "the add lock is released between entries"


def test_the_declared_config_fills_to_target_in_one_run():
    declared = P.load_config(POOL_YAML)
    assert declared["max_refills_per_run"] >= declared["target_size"]
    assert declared["max_size"] > declared["target_size"]
    assert declared["refill_detached"] is True


def test_only_one_refill_runs_per_pool(repo, cfg):
    with P._refill_flight(repo) as flying:
        assert flying
        assert P.refill_in_flight(repo) is True
        rep = P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
        assert rep["skipped"] == "refill_in_flight" and rep["created"] == 0
    assert P.refill_in_flight(repo) is False


def test_logged_depth_is_reread_not_computed(repo, cfg, events, monkeypatch):
    """Claims do not take the add lock, so `healthy + created` over-counts when a
    dispatch claims during a refill -- and `measure` would then file that
    dispatch's park as `pool_nonempty`."""
    monkeypatch.setattr(P, "_healthy_depth", lambda _root: 0)
    P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
    refills = [e for e in events if e["event"] == "refill"]
    assert refills and refills[0]["pool_depth"] == 0


# ── the refill is off the cycle ────────────────────────────────────────────
def test_spawn_refill_detaches_and_spawns_nothing_when_there_is_nothing_to_do(
        repo, cfg, monkeypatch):
    launched = []
    real_popen = subprocess.Popen

    class FakePopen:
        def __new__(cls, argv, **kwargs):
            if "--refill" not in argv:  # the pool's own git plumbing runs for real
                return real_popen(argv, **kwargs)
            return super().__new__(cls)

        def __init__(self, argv, **kwargs):
            launched.append((argv, kwargs))
            self.pid = 4242

    # depth read stubbed: this test is about the spawn, and the real read
    # shells out through the very Popen being faked
    monkeypatch.setattr(P, "_healthy_depth", lambda _root: 0)
    monkeypatch.setattr(P.subprocess, "Popen", FakePopen)
    out = P.spawn_refill(repo_root=repo, base="main", cfg=cfg)
    assert out["spawned"] is True and out["pid"] == 4242
    argv, kwargs = launched[0]
    assert argv[1:4] == ["-m", "tools.kanban.worktree_pool", "--refill"]
    assert argv[argv.index("--base") + 1] == "main"
    assert argv[argv.index("--repo-root") + 1] == str(repo)
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs.get("creationflags") or kwargs.get("start_new_session")

    with P._refill_flight(repo):
        assert P.spawn_refill(repo_root=repo, base="main", cfg=cfg)["skipped"] == \
            "refill_in_flight"
    monkeypatch.setattr(P, "_healthy_depth", lambda _root: 1)
    assert P.spawn_refill(repo_root=repo, base="main", cfg=cfg)["skipped"] == "at_target"
    assert len(launched) == 1


def test_the_cycle_spawns_the_refill_instead_of_running_it_inline():
    _src, _fn, body = _reflex_fn("_tend_worktree_pool")
    assert "_pool.spawn_refill(" in body
    assert body.index("refill_detached") < body.index("_pool.spawn_refill(")


# ── the dispatcher asks again after the add lock ───────────────────────────
def test_the_dispatcher_asks_the_pool_again_after_the_add_lock_and_before_the_add():
    src, fn, body = _reflex_fn("_create_worktree")
    lock = body.index("worktree_add_lock(timeout=WORKTREE_ADD_LOCK_WAIT_SECONDS)")
    second = body.index("phase=_pool2.PHASE_AFTER_ADD_LOCK")
    add = body.index('"worktree", "add", "-b",')
    assert lock < second < add
    # still exactly ONE add on the dispatch path: a second claim is not a retry
    assert src.count('"worktree", "add", "-b",') == 1
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_move_task" not in calls


def test_a_second_ask_is_recorded_as_such(repo, cfg, events):
    P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
    dest = repo / ".tmp" / "worktrees" / "t-2"
    got = P.claim("t-2", dest, repo_root=repo, base="main", cfg=cfg,
                  expect_manifest=False, phase=P.PHASE_AFTER_ADD_LOCK)
    assert got == str(dest)
    claim = [e for e in events if e["event"] == "claim"][0]
    assert claim["phase"] == P.PHASE_AFTER_ADD_LOCK


# ── convert_failed explains itself ─────────────────────────────────────────
def test_a_convert_failure_names_its_step_and_gits_words(repo, cfg, events, monkeypatch):
    P.refill(repo_root=repo, base="main", cfg=cfg, expect_manifest=False)
    real_git = P._git

    def fail_reset(args, cwd, timeout=60):
        if args[:1] == ["reset"]:
            return 128, "", "warning: x\nfatal: ambiguous argument 'nope'\n"
        return real_git(args, cwd, timeout)

    monkeypatch.setattr(P, "_git", fail_reset)
    assert P.claim("t-9", repo / ".tmp" / "worktrees" / "t-9", repo_root=repo,
                   base="main", cfg=cfg, expect_manifest=False) is None
    miss = [e for e in events if e["event"] == "miss"][0]
    assert miss["reason"] == P.MISS_CONVERT_FAILED
    assert miss["detail"] == "reset:fatal: ambiguous argument 'nope'"


# ── the acceptance number can see past midnight ────────────────────────────
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ts, extra in events:
            fh.write(json.dumps({"ts": ts.isoformat(), "extra": extra}) + "\n")


def test_measure_reads_the_rotated_log_a_24h_window_spans(repo):
    now = datetime.now(timezone.utc)
    live = P.log_file(repo)
    rotated = live.with_name(live.name + ".2026-10-18")
    _write(rotated, [
        (now - timedelta(hours=10), {"event": "pool_depth", "pool_depth": 0}),
        (now - timedelta(hours=9), {"event": "refill_failed",
                                    "reason": "budget_exceeded"}),
        (now - timedelta(hours=8), {"event": "refill", "pool_depth": 1}),
    ])
    _write(live, [
        (now - timedelta(minutes=30), {"event": "refill", "pool_depth": 2}),
        (now - timedelta(minutes=20), {"event": "claim", "phase": "dispatch"}),
        (now - timedelta(minutes=10), {"event": "miss", "reason": "pool_empty",
                                       "phase": P.PHASE_AFTER_ADD_LOCK}),
        (now - timedelta(minutes=9), {"event": "claim",
                                      "phase": P.PHASE_AFTER_ADD_LOCK}),
    ])
    rows = [{"task_id": "cold", "reason": "worktree creation failed",
             "recorded_at": (now - timedelta(hours=9, minutes=30)).isoformat()}]
    out = P.measure(24.0, repo_root=repo, get_conn=lambda: _FakeConn(rows))
    assert out["state"] == "measured"
    # the park sits in YESTERDAY's file's window: it is measured, not unmeasurable
    assert out["parks_while_pool_empty"] == 1 and out["parks_unmeasurable"] == 0
    assert out["refills"] == 2 and out["refill_failures"] == 1
    assert out["refill_attempts"] == 3
    assert out["refill_budget_exceeded_share"] == round(1 / 3, 3)
    # the dispatcher's second ask is counted apart, never as a second miss
    assert out["claims"] == 1 and out["claims_after_add_lock"] == 1
    assert out["misses"] == 0 and out["misses_after_add_lock"] == 1


def test_a_rotated_log_older_than_the_window_is_not_opened(repo, monkeypatch):
    import os

    live = P.log_file(repo)
    now = datetime.now(timezone.utc)
    old = live.with_name(live.name + ".2026-01-01")
    _write(old, [(now - timedelta(minutes=5), {"event": "refill", "pool_depth": 1})])
    stale = (now - timedelta(days=30)).timestamp()
    os.utime(old, (stale, stale))
    out = P.measure(24.0, repo_root=repo, get_conn=lambda: _FakeConn([]))
    assert out["state"] == "unmeasurable"
