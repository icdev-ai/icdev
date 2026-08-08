# CUI // SP-CTI
"""`--pause-runner` must be releasable by `--resume-runner`.

Observed 2026-08-07: the kanban board sat paused for hours and every
`--resume-runner` answered "NOT PAUSED BY THIS SESSION". The lease was real and
the scheduler was honouring it — `Cycle 220: paused (session) ... lease held` —
but nothing could release it.

The cause is a mismatch between what the lease is for and how it is keyed.
`leases.release()` matches on `holder_session`, which is right for a lease
guarding concurrent work: only the worker holding it may drop it. The runner
pause is the opposite kind of object — an operator toggle that is MEANT to
outlive its process. `--pause-runner` acquires and exits immediately, and
`get_session_id()` mints a fresh `local-<uuid>` per process when
CLAUDE_SESSION_ID is unset, so the next invocation is a different session by
construction. The pause was therefore releasable only by waiting out its 4-hour
TTL, or by impersonating the dead holder via ICDEV_SESSION_ID.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.coordination import leases  # noqa: E402

RESOURCE = "kanban:runner:global"


@pytest.fixture
def lease_dir(tmp_path, monkeypatch):
    """Point the lease store at a scratch dir so tests never touch the real board."""
    monkeypatch.setattr(leases, "LEASE_DIR", tmp_path, raising=False)
    yield tmp_path


def _write_lease(session: str, pid: int, acquired_ts: float | None = None,
                 ttl_seconds: int = 3600):
    import time
    lease = leases.acquire(RESOURCE, intent="test", ttl_seconds=ttl_seconds, block=False)
    assert lease is not None
    _, meta_path = leases._paths(RESOURCE)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["holder_session"] = session
    meta["pid"] = pid
    meta["acquired_at_ts"] = acquired_ts if acquired_ts is not None else time.time()
    meta["ttl_seconds"] = ttl_seconds
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def _dead_pid() -> int:
    """A pid that is certainly not running: spawn, wait, reap."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=60)
    return p.pid


# ---------------------------------------------------------------------------
# The bug

def test_release_alone_cannot_free_a_pause_taken_by_an_exited_process(lease_dir):
    """This is the failing behaviour, pinned so the fix is not silently undone."""
    _write_lease("local-df440924dde1", _dead_pid())
    assert leases.release(RESOURCE) is False, (
        "session-scoped release matching a DIFFERENT session should not succeed"
    )
    assert leases.holder(RESOURCE) is not None, "lease should still be held"


def test_release_stale_frees_a_pause_whose_process_is_gone(lease_dir):
    _write_lease("local-df440924dde1", _dead_pid())
    assert leases.release_stale(RESOURCE) is True
    assert leases.holder(RESOURCE) is None


def test_release_stale_refuses_while_the_holder_is_alive(lease_dir):
    """One live session must not steal a pause another live session is relying on."""
    _write_lease("local-someone-else", os.getpid())
    assert leases.release_stale(RESOURCE) is False
    assert leases.holder(RESOURCE) is not None


def test_release_stale_on_a_free_resource_is_a_no_op(lease_dir):
    assert leases.holder(RESOURCE) is None
    assert leases.release_stale(RESOURCE) is False


def test_holder_is_alive_reports_none_when_unheld(lease_dir):
    assert leases.holder_is_alive(RESOURCE) is None


def test_holder_is_alive_true_for_this_process(lease_dir):
    _write_lease("local-me", os.getpid())
    assert leases.holder_is_alive(RESOURCE) is True


def test_pid_reuse_does_not_keep_a_dead_lease_alive(lease_dir):
    """A recycled pid must not make a long-gone holder look alive.

    The lease is backdated far behind this process's start time, so the pid is
    'alive' but belongs to a process that started long after the lease.
    """
    psutil = pytest.importorskip("psutil")
    started = psutil.Process(os.getpid()).create_time()
    # TTL must outlast the backdating, or the lease reads as EXPIRED and
    # holder() returns None — which is a different code path than the pid-reuse
    # one under test here.
    _write_lease("local-ancient", os.getpid(), acquired_ts=started - 60,
                 ttl_seconds=86400)
    assert leases.holder_is_alive(RESOURCE) is False
    assert leases.release_stale(RESOURCE) is True


def test_unknowable_liveness_does_not_reclaim(lease_dir, monkeypatch):
    """Reclaiming on ignorance would let any error unpause a deliberate pause."""
    _write_lease("local-unknown", 424242)
    monkeypatch.setattr(leases, "holder_is_alive", lambda _r: None)
    assert leases.release_stale(RESOURCE) is False


# ---------------------------------------------------------------------------
# End to end through the CLI command

def test_resume_command_reclaims_the_stale_pause(lease_dir, capsys):
    from tools.kanban.cli import cmd_resume_runner
    _write_lease("local-df440924dde1", _dead_pid())
    rc = cmd_resume_runner(json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["resumed"] is True
    assert out["reclaimed_from_exited_session"] is True
    assert out["still_held_by"] is None


def test_resume_command_refuses_a_live_pause_and_names_the_holder(lease_dir, capsys):
    from tools.kanban.cli import cmd_resume_runner
    _write_lease("local-someone-else", os.getpid())
    rc = cmd_resume_runner(json_out=False)
    text = capsys.readouterr().out
    assert rc == 1
    assert "NOT RESUMED" in text
    assert "local-someone-else" in text, "must say WHO holds it, not just refuse"


def test_resume_command_on_a_free_board_says_so(lease_dir, capsys):
    from tools.kanban.cli import cmd_resume_runner
    rc = cmd_resume_runner(json_out=False)
    text = capsys.readouterr().out
    assert rc == 1
    assert "NOT PAUSED" in text
