# CUI // SP-CTI
"""The scheduler pause must mean the same thing from every checkout.

Two independent worktree blind spots, both observed live on 2026-07-28 while the
board was supposedly frozen:

1. The sentinel path came from ``Path(__file__).parents[2]`` — the WORKTREE root
   inside a worktree — so a scheduler started there saw no sentinel and
   dispatched. The dashboard spawns a scheduler child on every start, so
   restarting a dashboard from a worktree was enough. Four tasks went out two
   hours into a verified pause.
2. ``KANBAN_PAUSE_MAX_MINUTES`` lives in ``.env``, which a worktree does not
   have. A worktree process fell back to the 120-minute default, judged a pause
   taken under a 1440-minute ceiling stale, and DELETED it — turning "paused"
   into "not paused" silently. (That is not hypothetical: a probe written for
   this very fix expired the live sentinel.)
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tools.kanban import scheduler_control as sc


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _fresh_flag_cache():
    sc._flag_path.cache_clear()
    yield
    sc._flag_path.cache_clear()


# ── 1. The sentinel is anchored to the shared repo root ────────────────────

def test_canonical_root_is_the_main_repo_not_the_worktree(monkeypatch, tmp_path):
    """git rev-parse --git-common-dir answers with the MAIN .git from a worktree."""
    main_root = tmp_path / "main"
    (main_root / ".git").mkdir(parents=True)

    def fake_run(cmd, *a, **k):
        assert "--git-common-dir" in cmd
        return type("R", (), {"returncode": 0, "stdout": str(main_root / ".git"), "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sc._canonical_repo_root() == main_root


def test_falls_back_to_this_tree_when_git_is_unavailable(monkeypatch):
    """A non-git install has no worktrees, so the two roots agree anyway."""
    def boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert sc._canonical_repo_root() == sc._ROOT


def test_falls_back_when_git_errors(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 128, "stdout": "", "stderr": "fatal"})())
    assert sc._canonical_repo_root() == sc._ROOT


def test_env_override_still_wins(monkeypatch, tmp_path):
    """KANBAN_PAUSE_FLAG is the deliberate escape hatch."""
    target = tmp_path / "elsewhere.paused"
    monkeypatch.setenv("KANBAN_PAUSE_FLAG", str(target))
    sc._flag_path.cache_clear()
    assert sc._flag_path() == target


def test_sentinel_sits_under_the_canonical_root(monkeypatch, tmp_path):
    monkeypatch.delenv("KANBAN_PAUSE_FLAG", raising=False)
    monkeypatch.setattr(sc, "_canonical_repo_root", lambda: tmp_path)
    sc._flag_path.cache_clear()
    assert sc._flag_path() == tmp_path / "data" / "kanban_scheduler.paused"


# ── 2. A reader must not apply its own TTL to someone else's pause ─────────

def test_reader_honours_the_pausers_deadline_not_its_own(monkeypatch):
    """The exact bug: 1440-min pause, reader defaulting to 120, still live."""
    monkeypatch.delenv("KANBAN_PAUSE_MAX_MINUTES", raising=False)  # reader -> 120
    now = datetime.now(timezone.utc)
    meta = {
        "actor": "manual-takeover",
        "since": _iso(now - timedelta(minutes=200)),   # older than the reader's 120
        "max_minutes": 1440,
        "expires_at": _iso(now + timedelta(minutes=1240)),
    }
    assert sc._flag_is_stale(meta) is False, (
        "a reader without .env must not expire a pause taken under a longer ceiling"
    )


def test_expired_pause_is_still_expired():
    now = datetime.now(timezone.utc)
    meta = {"since": _iso(now - timedelta(minutes=200)), "expires_at": _iso(now - timedelta(minutes=1))}
    assert sc._flag_is_stale(meta) is True


def test_pre_expires_at_sentinels_keep_working(monkeypatch):
    """An in-flight pause written by the old code must not be invalidated."""
    monkeypatch.setenv("KANBAN_PAUSE_MAX_MINUTES", "120")
    now = datetime.now(timezone.utc)
    assert sc._flag_is_stale({"since": _iso(now - timedelta(minutes=30))}) is False
    assert sc._flag_is_stale({"since": _iso(now - timedelta(minutes=200))}) is True


def test_unparseable_expires_at_falls_back_rather_than_wedging(monkeypatch):
    monkeypatch.setenv("KANBAN_PAUSE_MAX_MINUTES", "120")
    now = datetime.now(timezone.utc)
    meta = {"since": _iso(now - timedelta(minutes=30)), "expires_at": "not-a-timestamp"}
    assert sc._flag_is_stale(meta) is False


def test_pause_stamps_its_own_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("KANBAN_PAUSE_MAX_MINUTES", "1440")
    monkeypatch.setenv("KANBAN_PAUSE_FLAG", str(tmp_path / "p.paused"))
    sc._flag_path.cache_clear()

    meta = sc.pause(actor="tester", reason="why")
    assert meta["max_minutes"] == 1440
    written = json.loads((tmp_path / "p.paused").read_text(encoding="utf-8"))
    assert written["expires_at"], "the deadline must be persisted, not recomputed by readers"

    deadline = datetime.fromisoformat(written["expires_at"].replace("Z", "+00:00"))
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds() / 60.0
    assert 1430 < remaining <= 1440


def test_remaining_time_is_read_from_the_sentinel(monkeypatch):
    """Otherwise the UI tells an operator the wrong time left."""
    monkeypatch.delenv("KANBAN_PAUSE_MAX_MINUTES", raising=False)  # reader -> 120
    now = datetime.now(timezone.utc)
    meta = {"since": _iso(now), "expires_at": _iso(now + timedelta(minutes=1440))}
    assert sc._minutes_remaining(meta) > 1000


# ── End to end ─────────────────────────────────────────────────────────────

def test_pause_then_read_with_a_bare_environment(monkeypatch, tmp_path):
    """Pause under a long ceiling; read it back as a worktree process would.

    The reader has no KANBAN_PAUSE_MAX_MINUTES at all — exactly the worktree
    case — and must still see the pause, and must NOT delete the sentinel.
    """
    flag = tmp_path / "data" / "kanban_scheduler.paused"
    monkeypatch.setenv("KANBAN_PAUSE_FLAG", str(flag))
    monkeypatch.setenv("KANBAN_PAUSE_MAX_MINUTES", "1440")
    sc._flag_path.cache_clear()
    sc.pause(actor="main-checkout", reason="long takeover")

    monkeypatch.delenv("KANBAN_PAUSE_MAX_MINUTES", raising=False)
    sc._flag_path.cache_clear()

    assert sc.manual_paused() is True
    assert flag.exists(), "the reader deleted a pause it had no business expiring"
