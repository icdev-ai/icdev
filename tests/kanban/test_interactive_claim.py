# CUI // SP-CTI
"""mfx-own-02: ``cli.py --claim`` from a plain shell takes a lease that HOLDS.

MEASURED 2026-09-03. An operator ran ``--claim rmf-ui-13`` to hold the task
while repairing its PR by hand. The CLI replied that the lease was bound to a
pid that exits immediately and to an UNREGISTERED session id -- so every reader
would treat it as litter within seconds -- and a second session repaired the
same branch concurrently at 14:01, producing two conflicting resolutions. The
lease primitive existed; only a registered service session could make it hold.

Now the claim is handed to a KEEPER: a detached process that registers a
dedicated ``cli-claim-<task>-t<hex>`` session (``agent_type`` cli, the stated
intent), re-takes the lease under its own live pid and heartbeats until the TTL
or ``--release``. These tests drive the keeper's steps in-process -- no thread,
no sleep, no subprocess -- and then ask the SAME readers the card names: the
shared verdict, the runner's dispatch guard, and ``restore_acts``.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import tools.coordination.leases as leases
import tools.coordination.service_identity as service_identity
import tools.coordination.session_registry as reg
import tools.kanban.interactive_claim as ic
import tools.kanban.lease_liveness as ll
from tests._sql_compat import translating

TASK = "mfx-x-01"
RES = f"kanban:task:{TASK}"

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT DEFAULT 'backlog',
    priority      TEXT DEFAULT 'medium',
    task_type     TEXT DEFAULT 'build',
    branch_name   TEXT,
    commit_summary TEXT,
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
    for pid in range(4_000_000, 4_100_000, 4):
        if ic.pid_alive(pid) is False:
            return pid
    raise RuntimeError("could not find a free pid")  # pragma: no cover


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A hermetic board, registry, lease store and claim store.

    The shell's own identity is pinned (``shell-abc``) and every identity
    variable is registered with monkeypatch BEFORE the keeper rewrites them,
    so the test process gets its environment back.
    """
    db = tmp_path / "k.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(_SCHEMA)
    raw.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
                (TASK, "held by hand", "backlog"))
    raw.commit()
    raw.close()

    def _conn(*_a, **_kw):
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return translating(c)

    monkeypatch.setattr(leases, "LEASE_DIR", tmp_path / "leases")
    monkeypatch.setattr(ic, "CLAIM_DIR", tmp_path / "claims")
    monkeypatch.setattr(ic, "get_connection", _conn)
    monkeypatch.setattr(reg, "get_connection", _conn)
    monkeypatch.setattr(reg, "_table_ready", False)
    import tools.coordination.code_identity as code_identity

    monkeypatch.setattr(code_identity, "boot_identity", lambda **_k: {})
    monkeypatch.setenv("CLAUDE_SESSION_ID", "shell-abc")
    monkeypatch.setenv("ICDEV_SESSION_ID", "shell-abc")
    monkeypatch.setenv("ICDEV_AGENT", "claude")
    monkeypatch.setattr(service_identity, "_OWNED", set())
    # The reflex aliases lease_liveness.task_is_heartbeating at IMPORT time; a
    # first import under the patch below would bind the lambda for the whole
    # session and fail test_lease_liveness's identity check. Import it first.
    import tools.genesis.reflexes.kanban  # noqa: F401
    # The task never started, so it has no heartbeat: the one-shot-claim case.
    monkeypatch.setattr(ll, "task_is_heartbeating", lambda _tid: False)

    keepers = []

    def inline_spawner(task_id, sid, intent, ttl):
        """The keeper's ``start`` run in THIS process, in place of a spawn."""
        k = ic.Keeper(task_id, sid, intent, ttl, handover_wait=1.0, sleep=lambda _s: None)
        reason = k.start()
        assert reason is None, ic.read_state(task_id)
        keepers.append(k)
        return os.getpid()

    def rows():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        out = [dict(r) for r in c.execute("SELECT * FROM agent_sessions").fetchall()]
        c.close()
        return out

    def set_status(status):
        c = sqlite3.connect(str(db))
        c.execute("UPDATE kanban_tasks SET status = ? WHERE id = ?", (status, TASK))
        c.commit()
        c.close()

    return SimpleNamespace(db=db, tmp=tmp_path, conn=_conn, spawner=inline_spawner,
                           keepers=keepers, rows=rows, set_status=set_status)


def _claim(world, **kw):
    return ic.claim(TASK, spawner=world.spawner, sleep=lambda _s: None, **kw)


# ── the claim is bound to a registered, dedicated cli session ─────────────
class TestClaimHolds:
    def test_the_lease_is_handed_to_a_registered_cli_session(self, world):
        out = _claim(world, intent="repairing its PR by hand")
        assert out["claimed"] is True and out["renewed"] is False
        assert out["keeper"] == "running", out
        sid = out["session_id"]
        assert ic.claim_session_for(TASK, sid)
        holder = leases.holder(RES)
        assert holder["holder_session"] == sid, "the lease must name the keeper, not the shell"
        assert holder["pid"] == os.getpid(), "the lease's pid is the KEEPER's, which lives"
        assert holder["holder_agent"] == "cli"
        rows = [r for r in world.rows() if r["session_id"] == sid]
        assert len(rows) == 1
        assert rows[0]["agent_type"] == ic.AGENT_TYPE == "cli"
        assert rows[0]["current_intent"] == "repairing its PR by hand"
        assert rows[0]["status"] == "active"
        st = ic.read_state(TASK)
        assert st["session_id"] == sid and st["pid"] == os.getpid()
        exp = datetime.fromisoformat(st["expires_at"])
        assert timedelta(hours=1, minutes=59) < exp - datetime.now(timezone.utc) <= timedelta(hours=2)

    def test_the_default_intent_names_the_task(self, world):
        _claim(world)
        row = [r for r in world.rows() if ic.is_interactive_session(r["session_id"])][0]
        assert row["current_intent"] == f"manual repair of {TASK}"

    def test_a_stated_ttl_is_honoured(self, world):
        out = _claim(world, ttl_seconds=600)
        exp = datetime.fromisoformat(out["expires_at"])
        assert exp - datetime.now(timezone.utc) <= timedelta(seconds=600)

    def test_a_live_foreign_holder_is_refused(self, world, monkeypatch):
        (world.tmp / "leases").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "somebody-else")
        monkeypatch.setenv("ICDEV_SESSION_ID", "somebody-else")
        assert leases.acquire(RES, intent="theirs", ttl_seconds=600) is not None
        monkeypatch.setenv("CLAUDE_SESSION_ID", "shell-abc")
        monkeypatch.setenv("ICDEV_SESSION_ID", "shell-abc")
        out = _claim(world)
        assert out["claimed"] is False
        assert out["held_by"] == "somebody-else"
        assert "LIVE" in out["reason"]
        assert world.keepers == [], "nothing may be spawned for a refused claim"

    def test_a_litter_lease_is_reaped_before_claiming(self, world, monkeypatch):
        """The one-shot claim of an EXITED shell must not block a human."""
        (world.tmp / "leases").mkdir(parents=True, exist_ok=True)
        assert leases.acquire(RES, intent="old one-shot", ttl_seconds=3600) is not None
        _, meta = leases._paths(RES)
        import json

        cur = json.loads(meta.read_text(encoding="utf-8"))
        cur["holder_session"] = "local-gone"
        cur["pid"] = _dead_pid()
        meta.write_text(json.dumps(cur), encoding="utf-8")
        out = _claim(world)
        assert out["claimed"] is True and out["keeper"] == "running"
        assert leases.holder(RES)["holder_session"] == out["session_id"]


# ── the readers the card names ────────────────────────────────────────────
class TestEveryReaderHonoursIt:
    def test_the_shared_verdict_reads_live_by_pid(self, world):
        _claim(world)
        v = ll.task_lease_verdict(TASK)
        assert v.state == ll.STATE_LIVE and v.blocks_dispatch and not v.reapable

    def test_the_shared_verdict_reads_live_by_session_when_the_pid_cannot(self, world, monkeypatch):
        """The signal the card asks for: a CLI-registered holder is live while
        its heartbeat is fresh, even to a reader that cannot see its pid."""
        _claim(world)
        monkeypatch.setattr(leases, "holder_is_alive", lambda _r: False)
        v = ll.task_lease_verdict(TASK)
        assert v.state == ll.STATE_LIVE
        assert v.session_alive is True
        assert "registered" in ll.describe(v)

    def test_the_runner_skips_the_task(self, world, monkeypatch):
        import tools.genesis.reflexes.kanban as km

        _claim(world)
        assert km._lease_blocks_dispatch(TASK) is True
        monkeypatch.setattr(leases, "holder_is_alive", lambda _r: False)
        assert km._lease_blocks_dispatch(TASK) is True, "session-live must block too"
        assert leases.holder(RES) is not None, "and nothing may have reaped it"

    def test_restore_acts_plan_reports_a_live_holder(self, world):
        from tools.awareness import restore_acts

        _claim(world)
        proof = restore_acts.prove_dead_lease(TASK, leases=leases,
                                              heartbeating=lambda _t: False)
        assert proof.proven is False
        assert "running" in proof.reason
        plan = restore_acts.plan(
            root=world.tmp, leases=leases, heartbeating=lambda _t: False,
            staleness_fn=lambda: {"state": "unmeasurable", "processes": []},
            freshness_fn=lambda _r: {"state": "unmeasurable", "conflicts": []})
        cands = [c for c in plan["candidates"] if c["act"] == "reap_dead_lease"]
        assert [c["target"] for c in cands] == [TASK]
        assert cands[0]["proven"] is False and "running" in cands[0]["reason"]


# ── renew, release, and the keeper's own exits ────────────────────────────
class TestLifecycle:
    def test_claim_again_renews_a_running_keeper(self, world):
        first = _claim(world, ttl_seconds=600)
        again = _claim(world, ttl_seconds=7200, intent="still at it")
        assert again["renewed"] is True and again["claimed"] is True
        assert again["session_id"] == first["session_id"], "one keeper, not two"
        assert again["expires_at"] > first["expires_at"]
        assert len(world.keepers) == 1
        k = world.keepers[0]
        assert k.beat() is None
        assert k.expires_at == datetime.fromisoformat(again["expires_at"])
        st = ic.read_state(TASK)
        assert st["intent"] == "still at it" and st["beats"] == 1
        rows = [r for r in world.rows() if r["session_id"] == first["session_id"]]
        assert rows[0]["current_intent"] == "still at it", "the beat carries the new intent"

    def test_release_ends_the_keeper_session_and_frees_the_lease(self, world):
        out = _claim(world)
        sid = out["session_id"]
        rel = ic.release(TASK)
        assert rel["released"] is True and rel["interactive"] is True
        assert rel["session_id"] == sid
        assert leases.holder(RES) is None
        assert ic.read_state(TASK) is None
        rows = [r for r in world.rows() if r["session_id"] == sid]
        assert rows[0]["status"] == "ended"
        assert ll.session_is_live(sid) is False
        k = world.keepers[0]
        assert k.beat() == "released"
        k.finish("released")
        assert leases.holder(RES) is None

    def test_release_leaves_an_ordinary_lease_to_the_ladder(self, world):
        (world.tmp / "leases").mkdir(parents=True, exist_ok=True)
        assert leases.acquire(RES, intent="a runner's", ttl_seconds=600) is not None
        rel = ic.release(TASK)
        assert rel == {"released": False, "interactive": False, "session_id": None}
        assert leases.holder(RES) is not None

    def test_a_dead_keeper_is_our_own_litter_and_is_replaced(self, world):
        first = _claim(world)
        import json

        st = ic.read_state(TASK)
        st["pid"] = _dead_pid()
        ic.write_state(TASK, st)
        _, meta = leases._paths(RES)
        cur = json.loads(meta.read_text(encoding="utf-8"))
        cur["pid"] = st["pid"]
        meta.write_text(json.dumps(cur), encoding="utf-8")

        again = _claim(world)
        assert again["claimed"] is True and again["renewed"] is False
        assert again["keeper"] == "running"
        assert again["session_id"] != first["session_id"]
        by_id = {r["session_id"]: r for r in world.rows()}
        assert by_id[first["session_id"]]["status"] == "ended"
        assert by_id[again["session_id"]]["status"] == "active"

    def test_the_keeper_lets_go_on_a_terminal_status(self, world):
        out = _claim(world)
        k = world.keepers[0]
        world.set_status("done")
        reason = k.beat()
        assert reason == "task_done"
        k.finish(reason)
        assert leases.holder(RES) is None
        assert ic.read_state(TASK) is None
        rows = [r for r in world.rows() if r["session_id"] == out["session_id"]]
        assert rows[0]["status"] == "ended"

    def test_the_keeper_lets_go_when_the_claim_expires(self, world):
        _claim(world)
        k = world.keepers[0]
        st = ic.read_state(TASK)
        st["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ic.write_state(TASK, st)
        assert k.beat() == "expired"

    def test_the_keeper_stands_down_when_its_lease_was_taken(self, world):
        _claim(world)
        k = world.keepers[0]
        leases.release_all_for_session(k.session_id)
        assert k.beat() == "lease_lost"

    def test_an_unreadable_board_is_never_terminal(self, world, monkeypatch):
        _claim(world)
        k = world.keepers[0]

        def _boom(*_a, **_k):
            raise OSError("board unreachable")

        monkeypatch.setattr(ic, "get_connection", _boom)
        assert k.beat() is None

    def test_run_keeper_drives_start_beat_finish(self, world):
        (world.tmp / "leases").mkdir(parents=True, exist_ok=True)
        assert leases.acquire(RES, intent="by hand", ttl_seconds=600) is not None
        sid = ic.mint_session_id(TASK)
        assert leases.handover(RES, sid)
        reason = ic.run_keeper(TASK, sid, "by hand", 600, beat_seconds=0,
                               sleep=lambda _s: None, max_beats=2)
        assert reason == "max_beats"
        assert leases.holder(RES) is None, "finish releases"
        assert [r for r in world.rows() if r["session_id"] == sid][0]["status"] == "ended"


# ── never the inherited service identity (claim-verif-33c9f4cd11) ─────────
class TestIdentity:
    def test_a_claim_from_a_worker_shell_does_not_wear_the_schedulers_id(self, world, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "kanban-scheduler-4242")
        monkeypatch.setenv("ICDEV_SESSION_ID", "kanban-scheduler-4242")
        monkeypatch.setenv("ICDEV_AGENT", "kanban")
        out = _claim(world)
        assert out["keeper"] == "running"
        sid = out["session_id"]
        assert ic.claim_session_for(TASK, sid)
        ids = [r["session_id"] for r in world.rows()]
        assert sid in ids, "the keeper's OWN row"
        assert "kanban-scheduler-4242" not in ids, "the scheduler's row was never written"
        assert not any("/child-" in i for i in ids), "nor a descendant row of it"
        assert leases.holder(RES)["holder_agent"] == "cli"

    def test_the_keeper_id_never_parses_as_a_service_pid(self):
        for _ in range(200):
            sid = ic.mint_session_id("abc-def-07")
            assert service_identity.embedded_pid(sid) is None, sid
            assert service_identity.is_inherited_identity(sid) is False
            assert ic.claim_session_for("abc-def-07", sid)
            assert not ic.claim_session_for("abc-def-0", sid)

    def test_adopt_identity_overrides_what_was_inherited(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "kanban-scheduler-4242")
        monkeypatch.setenv("ICDEV_SESSION_ID", "kanban-scheduler-4242")
        monkeypatch.setenv("ICDEV_AGENT", "kanban")
        from tools.coordination.constants import get_agent_type, get_session_id

        ic.adopt_identity("cli-claim-x-01-tdeadbeef")
        assert get_session_id() == "cli-claim-x-01-tdeadbeef"
        assert get_agent_type() == "cli"
        assert reg._own_session_id() == "cli-claim-x-01-tdeadbeef"


# ── spawn mechanics and the seams the CLI and seeder use ──────────────────
class TestSeams:
    def test_keep_spawns_nothing_when_this_session_holds_no_lease(self, world):
        def _never(*_a):
            raise AssertionError("spawned without a lease to keep")

        out = ic.keep(TASK, spawner=_never, sleep=lambda _s: None)
        assert out["keeper"] == "none"

    def test_a_keeper_that_dies_before_reporting_is_said_out_loud(self, world):
        (world.tmp / "leases").mkdir(parents=True, exist_ok=True)
        assert leases.acquire(RES, intent="by hand", ttl_seconds=600) is not None
        dead = _dead_pid()
        out = ic.keep(TASK, spawner=lambda *_a: dead, sleep=lambda _s: None)
        assert out["keeper"] == "failed"
        assert str(dead) in out["reason"] and ".log" in out["reason"]
        assert leases.holder(RES)["holder_session"] == out["session_id"], (
            "the lease is left where it is and REPORTED, never silently dropped")

    def test_spawn_keeper_detaches_and_rewrites_the_identity(self, monkeypatch, tmp_path):
        import subprocess

        seen = {}

        class _P:
            pid = 4321

        def _popen(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return _P()

        monkeypatch.setattr(subprocess, "Popen", _popen)
        monkeypatch.setattr(ic, "CLAIM_DIR", tmp_path / "claims")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "kanban-scheduler-4242")
        monkeypatch.setenv("ICDEV_SESSION_ID", "kanban-scheduler-4242")
        sid = ic.mint_session_id(TASK)
        assert ic.spawn_keeper(TASK, sid, "by hand", 7200) == 4321
        cmd, kw = seen["cmd"], seen["kw"]
        assert cmd[1:4] == ["-m", "tools.kanban.interactive_claim", "--keep"]
        assert cmd[4] == TASK and sid in cmd and "7200" in cmd
        env = kw["env"]
        assert "CLAUDE_SESSION_ID" not in env
        assert env["ICDEV_SESSION_ID"] == sid and env["ICDEV_AGENT"] == "cli"
        if os.name == "nt":
            assert kw["creationflags"] & subprocess.DETACHED_PROCESS
        else:
            assert kw["start_new_session"] is True
        assert kw["stdin"] is subprocess.DEVNULL

    def test_the_cli_claims_through_the_keeper_and_releases_on_one_ladder(self):
        from tools.kanban import cli

        assert "interactive_claim.claim(" in inspect.getsource(cli.cmd_claim)
        ladder = inspect.getsource(cli._release_task_lease)
        assert "interactive_claim.release(" in ladder
        assert "interactive_claim.release(" not in inspect.getsource(cli.cmd_release)
        assert "interactive_claim.release(" not in inspect.getsource(cli.cmd_set_status)

    def test_the_cli_release_ladder_ends_an_interactive_claim(self, world, monkeypatch):
        from tools.kanban import cli

        monkeypatch.setattr(cli, "get_connection", world.conn)
        out = _claim(world)
        rel = cli._release_task_lease(TASK)
        assert rel["state"] == "released" and rel["interactive"] is True
        assert rel["prior_holder"] == out["session_id"]
        assert leases.holder(RES) is None

    def test_the_cli_claim_moves_the_task_and_reports_the_link(self, world, monkeypatch, capsys):
        import json

        from tools.kanban import cli

        monkeypatch.setattr(cli, "get_connection", world.conn)
        monkeypatch.setattr(ic, "spawn_keeper", world.spawner)
        assert cli.cmd_claim(TASK, json_out=True, intent="by hand") == 0
        body = json.loads(capsys.readouterr().out)
        assert body["claimed"] is True and body["session_linked"] is True
        assert body["keeper"] == "running" and body["keeper_pid"] == os.getpid()
        assert ic.claim_session_for(TASK, body["holder_session"])
        c = world.conn()
        assert dict(c.execute("SELECT status FROM kanban_tasks WHERE id = %s",
                              (TASK,)).fetchone())["status"] == "in_progress"
        n = dict(c.execute("SELECT COUNT(*) AS n FROM kanban_status_transitions").fetchone())["n"]
        c.close()
        assert n == 1
        # Renewal does not record a second in_progress -> in_progress transition.
        assert cli.cmd_claim(TASK, json_out=True) == 0
        body = json.loads(capsys.readouterr().out)
        assert body["renewed"] is True
        c = world.conn()
        n = dict(c.execute("SELECT COUNT(*) AS n FROM kanban_status_transitions").fetchone())["n"]
        c.close()
        assert n == 1

    def test_the_seeder_hands_a_claim_to_a_keeper_only_from_a_non_service_process(
            self, world, monkeypatch):
        import tools.kanban.task_factory as tf

        kept = []
        monkeypatch.setattr(ic, "keep", lambda tid, **kw: kept.append((tid, kw)) or
                            {"keeper": "running", "reason": None})
        monkeypatch.setattr(leases, "acquire", lambda *_a, **_k: object())
        assert tf.claim_seeded_tasks(["a-01"])["claimed"] == ["a-01"]
        assert [t for t, _ in kept] == ["a-01"]
        assert kept[0][1]["ttl_seconds"] == tf.SEED_CLAIM_TTL_SECONDS

        kept.clear()
        monkeypatch.setattr(service_identity, "_OWNED", {"kanban-scheduler"})
        assert tf.claim_seeded_tasks(["a-02"])["claimed"] == ["a-02"]
        assert kept == [], "a registered service's own session already holds"

    def test_the_seeders_keeper_failure_is_said_not_hidden(self, world, monkeypatch, caplog):
        import tools.kanban.task_factory as tf

        monkeypatch.setattr(ic, "keep", lambda tid, **kw: {"keeper": "failed",
                                                             "reason": "no python"})
        monkeypatch.setattr(leases, "acquire", lambda *_a, **_k: object())
        tf.logger.propagate = True
        with caplog.at_level("WARNING", logger=tf.logger.name):
            tf.claim_seeded_tasks(["a-01"])
        assert any("litter" in r.getMessage() for r in caplog.records)

    def test_the_verdict_module_is_untouched_for_ordinary_holders(self):
        """The claim is made to look like what the verdict already honours;
        the verdict itself must not have learned a keeper-shaped special case."""
        assert "interactive" not in inspect.getsource(ll).lower()
        assert "cli-claim" not in inspect.getsource(ll)
