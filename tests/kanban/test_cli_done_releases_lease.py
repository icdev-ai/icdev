# CUI // SP-CTI
"""``cli.py --set-status <id> done`` hands the task's coordination lease back.

THE INCIDENT (claim-verif-a6a1517970, 2026-08-22). The ``held_task_lease_has_a_
live_holder`` claim fired on 20 ``kanban:task:fdx-*`` leases whose holder pid was
dead and whose task had never heartbeat. Every one of them was a
``create_tasks(specs, claim=True)`` seed -- the protection CLAUDE.md tells a
session to take before building a task by hand -- and SEVENTEEN of the twenty
were on tasks already marked ``done`` through ``--set-status ... --force-done``.
The work had landed, the row said so, and the claim sat there for the rest of
its four-hour TTL: CLAUDE.md leaves ``--release`` to the human, and the human
did not.

The verifier's reduction was RIGHT (pid dead, session unregistered, no
heartbeat -- every signal agreed). The WRITER was wrong: the one seam a manual
session uses to say "this landed" never let go of the claim it told that
session to take. The runner's ``_move_task`` already releases on a terminal
transition; the CLI did not.

THE RELEASE IS THE SAME LADDER ``--release`` CLIMBS, not a second copy:
ownership first (``leases.release``), then the two-signal verdict
(``lease_liveness.reap_if_litter``). A lease whose holder is still RUNNING, or
whose task is HEARTBEATING under a dead pid, is left alone and REPORTED --
marking a task done must never be the lever that steals a live worker's claim.
"""
from __future__ import annotations

import inspect
import json
import sqlite3

import pytest

import tools.coordination.leases as leases
import tools.kanban.lease_liveness as lease_liveness
from tests._sql_compat import translating

TASK = "fdx-x-01"
RESOURCE = f"kanban:task:{TASK}"


_SCHEMA = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT DEFAULT 'backlog',
    priority      TEXT DEFAULT 'medium',
    task_type     TEXT DEFAULT 'build',
    created_at    TEXT,
    updated_at    TEXT,
    completed_at  TEXT,
    failure_count INTEGER DEFAULT 0,
    last_failure_reason TEXT
);
CREATE TABLE kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT 'unknown',
    reason       TEXT,
    recorded_at  TEXT NOT NULL
);
"""


def _dead_pid() -> int:
    """A pid no process on this host currently owns."""
    try:
        import psutil

        exists = psutil.pid_exists
    except ImportError:  # pragma: no cover - CI has psutil; keep the fallback honest
        from tools.compat.platform_utils import pid_exists as exists
    for pid in range(4_000_000, 4_100_000, 4):
        if not exists(pid):
            return pid
    raise RuntimeError("could not find a free pid")  # pragma: no cover


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_SCHEMA)
    raw.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
                (TASK, "seeded and claimed", "backlog"))
    raw.commit()
    raw.close()

    def _fake_conn(*_a, **_kw):
        # %s -> ? for the CLI's Postgres-authored SQL against a sqlite fixture.
        raw = sqlite3.connect(str(db_path))
        raw.row_factory = sqlite3.Row
        return translating(raw)

    import tools.kanban.cli as cli_mod

    monkeypatch.setattr(cli_mod, "get_connection", _fake_conn)
    # The lease store is a directory under the repo; point it at scratch so the
    # test neither reads nor leaves a real claim behind.
    monkeypatch.setattr(leases, "LEASE_DIR", tmp_path / "leases")
    # The task never started, so it has no heartbeat to consult. Patched rather
    # than read, because lease_liveness opens the AMBIENT database.
    monkeypatch.setattr(lease_liveness, "task_is_heartbeating", lambda _tid: False)
    # --claim hands its lease to a detached KEEPER process (mfx-own-02). No test
    # here may spawn one: the claim store is scratch and the keeper is faked.
    import tools.kanban.interactive_claim as ic

    monkeypatch.setattr(ic, "CLAIM_DIR", tmp_path / "claims")
    monkeypatch.setattr(ic, "spawn_keeper", _fake_keeper)
    return {"cli": cli_mod, "db": db_path, "ic": ic}


def _fake_keeper(task_id, sid, intent, ttl):
    """Stand in for the spawned keeper: report holding, under THIS live pid."""
    import os

    import tools.kanban.interactive_claim as ic

    ic.write_state(task_id, {"task_id": task_id, "session_id": sid, "pid": os.getpid(),
                             "intent": intent, "expires_at": "2099-01-01T00:00:00+00:00"})
    return os.getpid()


def _seed_claim(**overrides):
    """Take a real lease the way ``claim_seeded_tasks`` does, then rewrite the
    parts of its metadata that describe WHO took it."""
    lease = leases.acquire(RESOURCE, intent="seeded-and-claimed by this session",
                           ttl_seconds=14_400, block=False)
    assert lease is not None
    if overrides:
        _, meta_path = leases._paths(RESOURCE)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update(overrides)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return lease


def _status(db_path):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status FROM kanban_tasks WHERE id = ?", (TASK,)).fetchone()
    conn.close()
    return row[0]


# ── the incident: the claiming process has exited ──────────────────────────
class TestDoneReleasesTheClaim:
    def test_done_by_the_session_that_claimed_releases_by_ownership(self, ctx):
        _seed_claim()                                    # this process, this session
        rc = ctx["cli"].cmd_set_status([TASK], "done", json_out=False,
                                       force_done=True, reason="landed elsewhere")
        assert rc == 0
        assert _status(ctx["db"]) == "done"
        assert leases.holder(RESOURCE) is None, "the claim outlived the done"

    def test_done_after_the_seeding_process_exited_reclaims_the_litter(self, ctx, capsys):
        """The measured case: a one-shot seeder took the claim and is gone."""
        _seed_claim(pid=_dead_pid(), holder_session="local-81f9e0c8ff92")
        rc = ctx["cli"].cmd_set_status([TASK], "done", json_out=True,
                                       force_done=True, reason="landed elsewhere")
        assert rc == 0
        assert leases.holder(RESOURCE) is None
        payload = json.loads(capsys.readouterr().out)
        (row,) = payload
        assert row["lease"]["state"] == "reclaimed"
        assert row["lease"]["prior_holder"] == "local-81f9e0c8ff92"

    def test_text_output_says_what_happened_to_the_lease(self, ctx, capsys):
        _seed_claim(pid=_dead_pid(), holder_session="local-81f9e0c8ff92")
        ctx["cli"].cmd_set_status([TASK], "done", json_out=False,
                                  force_done=True, reason="landed elsewhere")
        out = capsys.readouterr().out
        assert "lease: reclaimed" in out


# ── what must NOT be released ──────────────────────────────────────────────
class TestDoneNeverStealsLiveWork:
    def test_a_live_foreign_holder_is_left_alone_and_reported(self, ctx, capsys):
        """pid alive (this process), session not ours -> another live session
        owns it. Marking done is not authority to take it from them."""
        _seed_claim(holder_session="some-other-live-session")
        rc = ctx["cli"].cmd_set_status([TASK], "done", json_out=True,
                                       force_done=True, reason="landed elsewhere")
        assert rc == 0, "the done itself still succeeds"
        assert _status(ctx["db"]) == "done"
        assert leases.holder(RESOURCE) is not None, "a live holder's claim was stolen"
        (row,) = json.loads(capsys.readouterr().out)
        assert row["lease"]["state"] == "kept"
        assert row["lease"]["still_held_by"] == "some-other-live-session"

    def test_a_heartbeating_task_under_a_dead_pid_is_left_alone(self, ctx, monkeypatch):
        """autonomy-adm-03: a dead pid is not dead work."""
        monkeypatch.setattr(lease_liveness, "task_is_heartbeating", lambda _tid: True)
        _seed_claim(pid=_dead_pid(), holder_session="kanban-scheduler-1")
        ctx["cli"].cmd_set_status([TASK], "done", json_out=False,
                                  force_done=True, reason="landed elsewhere")
        assert leases.holder(RESOURCE) is not None

    def test_a_non_terminal_status_does_not_touch_the_lease(self, ctx):
        _seed_claim(pid=_dead_pid(), holder_session="local-81f9e0c8ff92")
        ctx["cli"].cmd_set_status([TASK], "in_progress", json_out=False)
        assert leases.holder(RESOURCE) is not None

    def test_an_unclaimed_task_reports_none_and_still_completes(self, ctx, capsys):
        rc = ctx["cli"].cmd_set_status([TASK], "done", json_out=True,
                                       force_done=True, reason="landed elsewhere")
        assert rc == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert row["lease"]["state"] == "none"


# ── one ladder, two doors ──────────────────────────────────────────────────
def test_set_status_and_release_climb_the_same_ladder():
    """A second copy of release-then-reap is the defect rem-hyg-15 was about:
    pid-only readers each forming their own opinion. Both doors call the one
    helper, and only the helper reaches the lease layer."""
    import tools.kanban.cli as cli_mod

    helper = inspect.getsource(cli_mod._release_task_lease)
    assert "leases.release(" in helper and "reap_if_litter(" in helper
    for door in (cli_mod.cmd_set_status, cli_mod.cmd_release):
        src = inspect.getsource(door)
        assert "_release_task_lease(" in src, door.__name__
        assert "reap_if_litter(" not in src, f"{door.__name__} grew its own ladder"


# ── --claim says what its lease is actually worth (2026-09-02, mfx-own-02) ──
class TestClaimSaysWhatTheLeaseIsWorth:
    """``--claim`` used to end with "runner will skip this task until you
    --release it". From a shell that was false: the lease recorded THIS
    process's pid, which exits on the next line, and every reader treats a
    dead-pid lease as litter within seconds. kpr-stale-03 was claimed by hand
    with its PR in flight and reset to backlog by startup recovery 34 minutes
    later. The 2026-09-02 fix made the CLI SAY which case it was; the advice it
    gave -- export ICDEV_SESSION_ID -- was not one a shell could act on, and on
    2026-09-03 an operator holding rmf-ui-13 by hand was overtaken anyway.

    Now (mfx-own-02) the claim is handed to a KEEPER that registers its own
    ``cli-claim-<id>-*`` session and heartbeats it. The CLI still reads the
    verdict BACK from the lease and the registry rather than trusting the
    keeper's report, and says which case it is.
    """

    @staticmethod
    def _widen(db_path):
        raw = sqlite3.connect(str(db_path))
        raw.execute("ALTER TABLE kanban_tasks ADD COLUMN branch_name TEXT")
        raw.execute("ALTER TABLE kanban_tasks ADD COLUMN commit_summary TEXT")
        raw.commit()
        raw.close()

    def test_the_lease_is_bound_to_a_keeper_session_not_the_shell(self, ctx, monkeypatch, capsys):
        self._widen(ctx["db"])
        monkeypatch.setattr(lease_liveness, "session_is_live", lambda sid: True)
        assert ctx["cli"].cmd_claim(TASK, json_out=False) == 0
        assert _status(ctx["db"]) == "in_progress"
        holder = leases.holder(RESOURCE)
        assert ctx["ic"].claim_session_for(TASK, holder["holder_session"]), holder
        out = capsys.readouterr().out
        assert "runner will skip this task until you --release it" not in out, (
            "the old promise is back, and it is false from a one-shot process"
        )
        assert "ICDEV_SESSION_ID" not in out, (
            "exporting an id never registered it; that advice must not come back")

    def test_an_unlinked_claim_is_reported_as_litter_in_waiting(self, ctx, monkeypatch, capsys):
        """The keeper died before it could report -- the lease is bound to an id
        nobody heartbeats, and the CLI must say so rather than promise."""
        self._widen(ctx["db"])
        monkeypatch.setattr(lease_liveness, "session_is_live", lambda sid: False)
        monkeypatch.setattr(ctx["ic"], "spawn_keeper", lambda *_a: _dead_pid())
        assert ctx["cli"].cmd_claim(TASK, json_out=False) == 0
        assert _status(ctx["db"]) == "in_progress", "the claim itself still stands"
        out = capsys.readouterr().out
        assert "litter" in out and "keeper" in out
        assert "honour this claim" not in out

    def test_a_session_linked_claim_is_reported_as_held(self, ctx, monkeypatch, capsys):
        self._widen(ctx["db"])
        monkeypatch.setattr(lease_liveness, "session_is_live", lambda sid: True)
        assert ctx["cli"].cmd_claim(TASK, json_out=False) == 0
        out = capsys.readouterr().out
        assert "honour this claim" in out and "heartbeats" in out
        assert "keeper pid" in out and "--release" in out

    def test_json_output_carries_the_link_verdict(self, ctx, monkeypatch, capsys):
        self._widen(ctx["db"])
        seen = []
        monkeypatch.setattr(lease_liveness, "session_is_live",
                            lambda sid: seen.append(sid) or False)
        assert ctx["cli"].cmd_claim(TASK, json_out=True) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["claimed"] is True
        assert body["session_linked"] is False
        assert body["holder_session"] == seen[0], "the verdict was asked about the lease's own session"
        assert body["keeper"] == "running" and body["expires_at"]
