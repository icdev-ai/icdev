# CUI // SP-CTI
"""Regression — restarting the scheduler must never cost in-flight work.

Two independent sweeps reset ``in_progress`` tasks when the scheduler starts:
the entrypoint block in ``tools/genesis/kanban_scheduler.py`` and
``reflexes/kanban.py::_startup_recover_stale_in_progress`` on cycle 1. Both used
to reset EVERY non-gate row unconditionally, so a restart was a decision with a
cost — a task whose session had not yet committed lost its work. On 2026-08-08
``kax-obs-01`` was in that state and a needed restart was deferred to avoid it,
delaying ~30 commits of reflex fixes that only go live on a restart.

This file pins both halves of the fixed behaviour (kax-recover-04):

  * a task a LIVE session is working is held, not reset;
  * a genuinely orphaned task IS still reset — the sweep exists for a real
    reason and deleting it would strand rows no promotion path can see;
  * the notification says what survived: "commits preserved on branch X" vs
    "no branch — work discarded";
  * ``failure_count`` / ``last_failure_reason`` are untouched, because an
    interruption is not a failure and a reason here feeds ``failure_triage``.

The earlier version of this file re-implemented the recovery block inline and
asserted against its own copy, so it passed while the shipped code diverged
(it still asserted a failure_count bump the scheduler had stopped doing). Every
test here drives the real ``tools.kanban.startup_recovery`` entry point.
"""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import _sql_compat
from tools.kanban import startup_recovery as sr


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT,
    status                TEXT,
    executor_type         TEXT DEFAULT 'claude_cli',
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    updated_at            TEXT
);
CREATE TABLE agent_sessions (
    session_id     TEXT PRIMARY KEY,
    agent_type     TEXT,
    pid            INTEGER,
    host           TEXT,
    cwd            TEXT,
    started_at     TEXT,
    last_heartbeat TEXT,
    current_intent TEXT,
    status         TEXT DEFAULT 'active'
);
CREATE TABLE kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT,
    from_status  TEXT,
    to_status    TEXT,
    actor        TEXT,
    reason       TEXT,
    recorded_at  TEXT
);
"""

LIVE_ID = "kax-live-01"
ORPHAN_ID = "kax-orphan-01"
GATE_ID = "kax-gate-00"


def _insert_task(db_path, task_id, title, status="in_progress", **cols):
    keys = ["id", "title", "status", *cols.keys()]
    vals = [task_id, title, status, *cols.values()]
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"INSERT INTO kanban_tasks ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' * len(keys))})",
        vals,
    )
    conn.commit()
    conn.close()


def _read(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _register_session(db_path, cwd, *, heartbeat, session_id="sess-live", status="active"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO agent_sessions "
        "(session_id, agent_type, pid, cwd, last_heartbeat, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, "kanban", 4242, str(cwd), heartbeat, status),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """A DB, a stubbed Telegram, and no ambient liveness leaking in.

    The process scan and the lease check read HOST state, so an unpinned test
    would pass or fail depending on what else is running on the machine. Both
    are pinned to "nothing found" here and exercised on their own below.
    """
    db_path = tmp_path / "k.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    sent: list = []
    monkeypatch.setattr(sr, "_send", lambda s, b, sev: sent.append((s, b, sev)))
    monkeypatch.setattr(sr, "scan_live_task_processes", lambda ids: {})
    monkeypatch.setattr(sr, "_lease_holder_pid", lambda tid: None)
    monkeypatch.setattr(sr, "_lease_session", lambda tid: None)
    monkeypatch.setattr(sr, "foreign_scheduler_pid", lambda: 0)

    def _factory():
        return _sql_compat.connect(db_path)

    return {"db": db_path, "sent": sent, "factory": _factory,
            "worktree": tmp_path / "worktrees" / LIVE_ID}


def _recover(ctx, **kw):
    kw.setdefault("conn_factory", ctx["factory"])
    kw.setdefault("repo_root", ctx["db"].parent)
    return sr.recover_interrupted_tasks(**kw)


# ── the two halves of the acceptance criterion ────────────────────────────────


class TestLiveSessionIsNotReset:
    def test_task_with_a_live_session_stays_in_progress(self, ctx):
        """A fresh agent_sessions heartbeat whose cwd IS the task worktree is
        proof that something is still working the task."""
        _insert_task(ctx["db"], LIVE_ID, "live build")
        _register_session(ctx["db"], ctx["worktree"], heartbeat=_now_iso())

        result = _recover(ctx)

        assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"
        held = [h for h in result["held"] if h["id"] == LIVE_ID]
        assert held and held[0]["reason"] == sr.EV_SESSION
        assert "sess-live" in held[0]["detail"]

    def test_stale_heartbeat_is_not_proof_of_life(self, ctx):
        """A session that stopped heartbeating 3 h ago is gone; its task is an
        orphan and must be recovered."""
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        _insert_task(ctx["db"], LIVE_ID, "abandoned build")
        _register_session(ctx["db"], ctx["worktree"], heartbeat=stale)

        _recover(ctx)

        assert _read(ctx["db"], LIVE_ID)["status"] == "backlog"

    def test_a_session_in_another_task_worktree_does_not_shield_this_one(self, ctx):
        """Ownership is by worktree, not by "some session is alive"."""
        _insert_task(ctx["db"], ORPHAN_ID, "orphan build")
        _register_session(
            ctx["db"], ctx["db"].parent / "worktrees" / "some-other-task",
            heartbeat=_now_iso(),
        )

        _recover(ctx)

        assert _read(ctx["db"], ORPHAN_ID)["status"] == "backlog"

    def test_scheduler_process_handle_holds_the_reset(self, ctx):
        _insert_task(ctx["db"], LIVE_ID, "live build")

        result = _recover(ctx, running_ids={LIVE_ID})

        assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"
        assert result["held"][0]["reason"] == sr.EV_HANDLE

    def test_live_lease_holder_holds_the_reset(self, ctx, monkeypatch):
        monkeypatch.setattr(sr, "_lease_holder_pid", lambda tid: 9191)
        _insert_task(ctx["db"], LIVE_ID, "live build")

        result = _recover(ctx)

        assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"
        assert result["held"][0]["reason"] == sr.EV_LEASE

    def test_live_os_process_holds_the_reset(self, ctx, monkeypatch):
        monkeypatch.setattr(sr, "scan_live_task_processes", lambda ids: {LIVE_ID: 7777})
        _insert_task(ctx["db"], LIVE_ID, "live build")

        result = _recover(ctx)

        assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"
        assert result["held"][0]["reason"] == sr.EV_PROCESS


class TestOrphanIsStillRecovered:
    def test_orphan_is_reset_to_backlog(self, ctx):
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        result = _recover(ctx)

        assert _read(ctx["db"], ORPHAN_ID)["status"] == "backlog"
        assert [r["id"] for r in result["reset"]] == [ORPHAN_ID]

    def test_interruption_is_not_recorded_as_a_failure(self, ctx):
        """failure_count and last_failure_reason must stay untouched.

        A reason written here made the task match
        ``failure_triage.find_recent_failures`` and enter the autofix queue with
        nothing wrong with it; a failure_count bump walks it toward the fc>=5
        'suggested' quarantine for having been interrupted.
        """
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        _recover(ctx)

        row = _read(ctx["db"], ORPHAN_ID)
        assert row["failure_count"] == 0
        assert row["last_failure_reason"] is None
        assert row["last_failure_at"] is None

    def test_reset_is_recorded_in_the_transition_ledger(self, ctx):
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        _recover(ctx)

        conn = sqlite3.connect(str(ctx["db"]))
        rows = conn.execute(
            "SELECT from_status, to_status, actor, reason FROM "
            "kanban_status_transitions WHERE task_id = ?", (ORPHAN_ID,),
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "in_progress" and rows[0][1] == "backlog"
        assert rows[0][2] == "startup-recovery"
        assert rows[0][3]

    def test_manual_gate_is_held(self, ctx):
        _insert_task(ctx["db"], GATE_ID, "MANUAL-MODE GATE - do not dispatch")

        result = _recover(ctx)

        assert _read(ctx["db"], GATE_ID)["status"] == "in_progress"
        assert result["held"][0]["reason"] == sr.HELD_GATE

    def test_github_actions_task_is_held(self, ctx):
        _insert_task(ctx["db"], "kax-ga-01", "GA build", executor_type="github_actions")

        result = _recover(ctx)

        assert _read(ctx["db"], "kax-ga-01")["status"] == "in_progress"
        assert result["held"][0]["reason"] == sr.HELD_EXTERNAL

    def test_non_in_progress_tasks_are_never_swept(self, ctx):
        _insert_task(ctx["db"], "kax-done-01", "already done", status="done")

        result = _recover(ctx)

        assert result["swept"] == 0
        assert _read(ctx["db"], "kax-done-01")["status"] == "done"

    def test_dry_run_changes_nothing(self, ctx):
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        result = _recover(ctx, dry_run=True)

        assert _read(ctx["db"], ORPHAN_ID)["status"] == "in_progress"
        assert [r["id"] for r in result["reset"]] == [ORPHAN_ID]
        assert ctx["sent"] == []

    def test_sweep_is_a_noop_when_another_scheduler_owns_the_runner(self, ctx, monkeypatch):
        """--once bypasses the entrypoint lockfile check, so this guard is what
        keeps a one-shot run from sweeping the live daemon's board."""
        monkeypatch.setattr(sr, "foreign_scheduler_pid", lambda: 4242)
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        result = sr.recover_interrupted_tasks(
            conn_factory=ctx["factory"], respect_foreign_owner=True,
        )

        assert result["sweep_skipped"] is True
        assert "4242" in result["reason"]
        assert _read(ctx["db"], ORPHAN_ID)["status"] == "in_progress"


# ── the notification distinguishes preserved work from discarded work ─────────


class TestNotificationDistinguishesWorkState:
    def test_no_branch_says_work_discarded(self, ctx, monkeypatch):
        monkeypatch.setattr(
            sr, "work_provenance",
            lambda tid, repo_root=None: sr.Provenance(summary="no branch — work discarded"),
        )
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        _recover(ctx)

        subject, body, severity = ctx["sent"][0]
        assert ORPHAN_ID in body
        assert "no branch — work discarded" in body
        assert severity == "warning", "losing in-flight work is not an FYI"

    def test_commits_say_preserved_on_branch(self, ctx, monkeypatch):
        monkeypatch.setattr(
            sr, "work_provenance",
            lambda tid, repo_root=None: sr.Provenance(
                branch=f"kanban/{tid}", commits=3, recoverable=True,
                summary=f"commits preserved on branch kanban/{tid} (3 commit(s))",
            ),
        )
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        _recover(ctx)

        subject, body, severity = ctx["sent"][0]
        assert f"commits preserved on branch kanban/{ORPHAN_ID} (3 commit(s))" in body
        assert severity == "info"

    def test_held_live_tasks_are_reported_once(self, ctx):
        _insert_task(ctx["db"], LIVE_ID, "live build")
        _insert_task(ctx["db"], GATE_ID, "MANUAL-MODE GATE - do not dispatch")
        _register_session(
            ctx["db"], ctx["worktree"],
            heartbeat=_now_iso(),
        )

        _recover(ctx)

        # One notification, and only for the LIVE task — a gate is held on every
        # restart by design and alerting on it would be pure noise.
        assert len(ctx["sent"]) == 1
        subject, body, _ = ctx["sent"][0]
        assert "HELD" in subject
        assert LIVE_ID in body and GATE_ID not in body


# ── provenance is derived from git, not from kanban_tasks.branch_name ─────────


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True, timeout=30)


@pytest.fixture
def repo(tmp_path):
    """A tiny real repo with one commit on the default branch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    return root


class TestWorkProvenance:
    def test_no_branch_reports_discarded(self, repo):
        prov = sr.work_provenance("kax-nothing-01", repo_root=repo)

        assert prov.branch is None
        assert prov.recoverable is False
        assert prov.summary == "no branch — work discarded"

    def test_commits_on_the_task_branch_are_reported_as_preserved(self, repo):
        """The branch_name COLUMN is NULL for every interrupted task (migration
        114 writes it at completion), so provenance has to come from git."""
        tid = "kax-committed-01"
        _git(repo, "checkout", "-b", f"kanban/{tid}")
        (repo / "work.txt").write_text("progress\n", encoding="utf-8")
        _git(repo, "add", "work.txt")
        _git(repo, "commit", "-m", "wip")
        _git(repo, "checkout", "main")

        prov = sr.work_provenance(tid, repo_root=repo)

        assert prov.branch == f"kanban/{tid}"
        assert prov.commits == 1
        assert prov.recoverable is True
        assert prov.summary.startswith(f"commits preserved on branch kanban/{tid}")

    def test_worktree_with_no_commits_is_still_recoverable(self, repo, tmp_path):
        """An uncommitted worktree is reused by the re-dispatch, so its work is
        not lost — reporting it as discarded would be a false alarm."""
        tid = "kax-uncommitted-01"
        wt = tmp_path / "worktrees" / tid
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "-b", f"kanban/{tid}", str(wt), "main")
        (wt / "wip.txt").write_text("not committed\n", encoding="utf-8")

        prov = sr.work_provenance(tid, repo_root=repo)

        assert prov.commits == 0
        assert prov.worktree is not None
        assert prov.dirty is True
        assert prov.recoverable is True
        assert "worktree retained" in prov.summary


class TestPathMentionsTask:
    def test_matches_the_worktree_directory(self):
        assert sr.path_mentions_task(r"C:\AI\ICDev\.tmp\worktrees\kax-recover-04", "kax-recover-04")
        assert sr.path_mentions_task("/tmp/icdev-kanban/compass/prem-p2-01", "prem-p2-01")

    def test_matches_the_merge_worktree(self):
        assert sr.path_mentions_task("/w/.merge-kax-recover-04", "kax-recover-04")

    def test_does_not_match_a_sibling_task_id_by_prefix(self):
        assert not sr.path_mentions_task("/w/worktrees/kax-recover-041", "kax-recover-04")
        assert not sr.path_mentions_task("/w/worktrees/kax-recover-04-r2", "kax-recover-04")

    def test_none_safe(self):
        assert not sr.path_mentions_task(None, "kax-recover-04")
        assert not sr.path_mentions_task("/w/x", "")


# ── the reflex's cycle-1 sweep must use the SAME policy ───────────────────────


class TestReflexSweepUsesTheSharedPolicy:
    """Both sweeps run on a restart, so a guard in only one of them buys nothing.

    The entrypoint block runs first and the reflex's ``_startup_recover_stale_
    in_progress`` runs on cycle 1, roughly a minute later. Before kax-recover-04
    they were independent implementations: the entrypoint exempted manual gates
    and the reflex additionally exempted GitHub Actions, and NEITHER asked whether
    a session was still alive. Holding the reset in one place while the other
    still resets is worse than not fixing it — the work is lost a cycle later and
    the log says the restart was safe.
    """

    @pytest.fixture
    def reflex(self, ctx, monkeypatch):
        km = pytest.importorskip("tools.genesis.reflexes.kanban")

        # A placeholder-TRANSLATING connection, not a bare sqlite3 handle: the
        # reflex authors its SQL for PostgreSQL and swallows the resulting
        # `near "%": syntax error`, which would make these tests assert a no-op.
        def _translating_factory(*_a, **_kw):
            return _sql_compat.connect(ctx["db"])

        monkeypatch.setattr(km, "get_connection", _translating_factory)
        monkeypatch.setattr(km, "_running", {})
        monkeypatch.setattr(km, "_foreign_scheduler_pid", lambda: 0)
        monkeypatch.setattr(km, "_startup_recovery_done", False)
        return km

    def test_reflex_holds_a_task_with_a_live_session(self, ctx, reflex):
        _insert_task(ctx["db"], LIVE_ID, "live build")
        _register_session(ctx["db"], ctx["worktree"], heartbeat=_now_iso())

        reflex._startup_recover_stale_in_progress()

        assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"

    def test_reflex_still_recovers_an_orphan(self, ctx, reflex):
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        reflex._startup_recover_stale_in_progress()

        assert _read(ctx["db"], ORPHAN_ID)["status"] == "backlog"

    def test_reflex_no_longer_stamps_a_failure_reason(self, ctx, reflex):
        """It used to write last_failure_reason='startup-recovery: …', which made
        an interrupted task match failure_triage's autofix queue."""
        _insert_task(ctx["db"], ORPHAN_ID, "orphaned build")

        reflex._startup_recover_stale_in_progress()

        assert _read(ctx["db"], ORPHAN_ID)["last_failure_reason"] is None


class TestExternalRepoProvenance:
    """An external task's branch lives in ITS repo, not in ICDev's.

    Provenance defaults to ICDev's root, and asking ICDev whether a compass
    branch exists always answers no — which would report every external task's
    work as discarded and send a warning for work that is safely committed.
    """

    def test_repo_root_follows_the_external_registry(self, monkeypatch, tmp_path):
        import types

        fake = types.SimpleNamespace(is_external=True, root=str(tmp_path / "compass"))
        monkeypatch.setattr(
            "tools.kanban.repo_registry.resolve_task_repo", lambda tid, **k: fake,
        )

        assert sr._task_repo_root("prem-p2-01") == tmp_path / "compass"

    def test_icdev_tasks_stay_on_the_icdev_root(self, monkeypatch):
        import types

        fake = types.SimpleNamespace(is_external=False, root=None)
        monkeypatch.setattr(
            "tools.kanban.repo_registry.resolve_task_repo", lambda tid, **k: fake,
        )

        assert sr._task_repo_root("kax-recover-04") == sr.BASE_DIR

    def test_a_broken_registry_falls_back_to_icdev(self, monkeypatch):
        def _boom(tid, **k):
            raise RuntimeError("registry unreadable")

        monkeypatch.setattr("tools.kanban.repo_registry.resolve_task_repo", _boom)

        assert sr._task_repo_root("kax-recover-04") == sr.BASE_DIR


# --------------------------------------------------------------------------- #
# 2026-09-02: a claim held by a live REGISTERED SESSION is not an orphan
# --------------------------------------------------------------------------- #
def test_a_claim_held_by_a_live_session_holds_the_reset(ctx, monkeypatch):
    """kpr-stale-03: claimed by hand with `cli.py --claim`, PR in flight, reset to
    backlog by this sweep at 21:28 with "no live session was found working it" --
    the claiming pid had exited, and nothing read the session id on the lease."""
    monkeypatch.setattr(sr, "_lease_session", lambda tid: "785a5ee7-claiming-session")
    _insert_task(ctx["db"], LIVE_ID, "hand-built fix")

    result = _recover(ctx)

    assert _read(ctx["db"], LIVE_ID)["status"] == "in_progress"
    assert result["held"][0]["reason"] == sr.EV_LEASE
    assert "session 785a5ee7-claiming-session" in result["held"][0]["detail"]


def test_lease_session_reads_the_shared_verdict(monkeypatch):
    """No private opinion: `_lease_session` consumes lease_liveness' verdict, so
    startup recovery and the dispatch reaper cannot disagree about a claim."""
    from tools.kanban import lease_liveness as ll

    live = ll.LeaseVerdict("x", "kanban:task:x", ll.STATE_LIVE,
                           {"holder_session": "s-1", "pid": 1}, False, None, True)
    monkeypatch.setattr(ll, "task_lease_verdict", lambda tid: live)
    assert sr._lease_session("x") == "s-1"

    litter = ll.LeaseVerdict("x", "kanban:task:x", ll.STATE_LITTER,
                             {"holder_session": "s-1", "pid": 1}, False, False)
    monkeypatch.setattr(ll, "task_lease_verdict", lambda tid: litter)
    assert sr._lease_session("x") is None

    pid_live = ll.LeaseVerdict("x", "kanban:task:x", ll.STATE_LIVE,
                               {"holder_session": "s-1", "pid": 1}, True, None)
    monkeypatch.setattr(ll, "task_lease_verdict", lambda tid: pid_live)
    assert sr._lease_session("x") is None, "a pid-live lease is the pid half's answer, not this one's"
