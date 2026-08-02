"""Tests for tools/kanban/reverify.py (kpr-rvfy-01).

The bug being guarded: the dispatch-time verifier reads process-local dicts
(`_worktrees`, `_dispatch_main_heads`), so after a daemon restart it reports
"No git commits found on task branch" for a task whose branch is full of work.
That verdict then blocks auto-merge permanently, because only a re-dispatch can
write a newer row. Re-verification must therefore depend on nothing but remote
git refs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.kanban import reverify as rv


# ── branch resolution ───────────────────────────────────────────────────────

def test_resolve_branch_defaults_to_canonical():
    assert rv.resolve_branch("gdx-aud-01") == "kanban/gdx-aud-01"


def test_resolve_branch_prefers_recorded_branch_name():
    """A retry lives on a suffixed branch — verifying kanban/<id> reads the wrong ref."""
    assert rv.resolve_branch(
        "gdx-aud-01", {"branch_name": "kanban/gdx-aud-01-r2"}
    ) == "kanban/gdx-aud-01-r2"


def test_resolve_branch_ignores_blank_branch_name():
    assert rv.resolve_branch("t1", {"branch_name": "   "}) == "kanban/t1"


def test_remote_ref_is_idempotent():
    assert rv._remote_ref("kanban/t1") == "origin/kanban/t1"
    assert rv._remote_ref("origin/kanban/t1") == "origin/kanban/t1"


# ── compute_verification ────────────────────────────────────────────────────

def _runner(*, exists=True, log_out="", count="0", log_rc=0):
    """Fake git. Returns canned output per subcommand."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "fetch":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1] == "rev-parse":
            return SimpleNamespace(returncode=0 if exists else 128, stdout="", stderr="")
        if args[1] == "log":
            return SimpleNamespace(returncode=log_rc, stdout=log_out, stderr="boom")
        if args[1] == "rev-list":
            return SimpleNamespace(returncode=0, stdout=count, stderr="")
        raise AssertionError(f"unexpected git call: {args}")

    run.calls = calls
    return run


def test_branch_with_changes_passes():
    v = rv.compute_verification(
        "t1", runner=_runner(log_out="a.py\nb.py\na.py\n", count="3\n"))
    assert v["result"] == "passed"
    assert v["files_changed"] == 2      # deduplicated
    assert v["commits"] == 3
    assert "2 file(s) changed" in v["reason"]


def test_branch_with_no_changes_fails_with_an_accurate_reason():
    v = rv.compute_verification("t1", runner=_runner(log_out="", count="0"))
    assert v["result"] == "failed"
    assert "carries no work ahead of base" in v["reason"]


def test_missing_branch_does_not_claim_the_agent_produced_nothing():
    """Deleted-after-merge is not evidence of missing work — the reason must say so."""
    v = rv.compute_verification("t1", runner=_runner(exists=False))
    assert v["result"] == "failed"
    assert "deleted after merge" in v["reason"]
    assert "no commits" not in v["reason"].lower()


def test_git_log_failure_is_reported_not_swallowed():
    v = rv.compute_verification("t1", runner=_runner(log_rc=128))
    assert v["result"] == "failed"
    assert "git log" in v["reason"]


def test_no_fetch_skips_the_network_call():
    r = _runner(log_out="a.py", count="1")
    rv.compute_verification("t1", runner=r, fetch=False)
    assert not any(c[1] == "fetch" for c in r.calls)


def test_fetch_failure_still_produces_a_verdict_from_cached_refs():
    """Offline must degrade to cached refs, not to a false 'failed'."""
    def run(args, **kwargs):
        if args[1] == "fetch":
            raise OSError("network down")
        if args[1] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1] == "log":
            return SimpleNamespace(returncode=0, stdout="a.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="1", stderr="")
    assert rv.compute_verification("t1", runner=run)["result"] == "passed"


def test_uses_the_recorded_branch_in_the_git_range():
    r = _runner(log_out="a.py", count="1")
    rv.compute_verification("t1", task_row={"branch_name": "kanban/t1-r2"}, runner=r)
    log = [c for c in r.calls if c[1] == "log"][0]
    assert "origin/main..origin/kanban/t1-r2" in log


# ── reverify (DB write) ─────────────────────────────────────────────────────

class FakeConn:
    def __init__(self, task=None):
        self._task = task
        self.inserts = []
        self.committed = False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("SELECT"):
            return SimpleNamespace(fetchone=lambda: self._task)
        assert "INSERT INTO kanban_verifications" in sql, sql
        assert "UPDATE" not in sql.upper()
        self.inserts.append((sql, params))
        return SimpleNamespace(fetchone=lambda: None)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_reverify_appends_a_row():
    conn = FakeConn({"id": "t1", "branch_name": None})
    v = rv.reverify("t1", lambda: conn, runner=_runner(log_out="a.py", count="1"))
    assert v["result"] == "passed" and v["written"] is True
    assert len(conn.inserts) == 1
    sql, params = conn.inserts[0]
    assert rv.DISPATCH_SOURCE in params
    assert conn.committed


def test_reverify_never_updates_an_existing_row():
    """kanban_verifications is append-only (NIST AU) — history must stay readable."""
    conn = FakeConn({"id": "t1", "branch_name": None})
    rv.reverify("t1", lambda: conn, runner=_runner(log_out="a.py", count="1"))
    assert all("INSERT" in sql.upper() for sql, _ in conn.inserts)


def test_reverify_leaves_review_passed_null():
    """NULL = 'not judged, allowed'; 0 would block a merge this module cannot judge."""
    conn = FakeConn({"id": "t1", "branch_name": None})
    rv.reverify("t1", lambda: conn, runner=_runner(log_out="a.py", count="1"))
    sql, _ = conn.inserts[0]
    assert "review_passed" not in sql


def test_reverify_dry_run_writes_nothing():
    conn = FakeConn({"id": "t1", "branch_name": None})
    v = rv.reverify("t1", lambda: conn,
                    runner=_runner(log_out="a.py", count="1"), dry_run=True)
    assert v["result"] == "passed" and v["written"] is False
    assert conn.inserts == []


def test_reverify_unknown_task_raises_instead_of_no_op():
    conn = FakeConn(None)
    with pytest.raises(LookupError, match="no such task"):
        rv.reverify("nope", lambda: conn, runner=_runner())
    assert conn.inserts == []


def test_reverify_records_a_genuine_failure_too():
    """A failing re-verification must be recorded, not silently dropped."""
    conn = FakeConn({"id": "t1", "branch_name": None})
    v = rv.reverify("t1", lambda: conn, runner=_runner(log_out="", count="0"))
    assert v["result"] == "failed" and v["written"] is True
    assert len(conn.inserts) == 1
