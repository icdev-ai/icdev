# CUI // SP-CTI
"""Wake tick + event emitters (agov-wake-03).

The store (agov-wake-01) can record a suspension and evaluate its condition.
This file covers the two halves that make a suspension actually end:

  1. **The tick.** One Genesis reflex cadence fires every due wake and resumes
     its session — and, crucially, does so EXACTLY ONCE. The tick can overlap
     with itself (a slow resume outlasting the one-minute cadence is enough), so
     "two consecutive ticks resume one wake once" is the load-bearing assertion,
     not a nicety: a wake delivered twice is an agent that resumed twice from one
     suspension.
  2. **The emitters.** ``wake_on_event("pr:1342:ci_green")`` is inert unless
     something in the platform says that string. A matching key must move a
     pending wake to ``due``; an unmatched key must change nothing at all —
     firing a near-miss key and having *something* move is the failure that
     would make every subscription untrustworthy.

Plus the two structural guarantees this task was given: **no new daemon** (the
whole point of riding the existing cron reflex) and a byte-identical ``icdev/``
mirror of the reflex, since a stale mirror is a known way for a reflex change to
look applied and do nothing.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import wake_signals
from tools.agent_runtime.wake import (
    STATE_DUE,
    STATE_FIRED,
    STATE_PENDING,
    TABLE,
    add_event,
    add_timer,
    fire_event,
)
from tools.agent_runtime.wake_tick import DEFAULT_MAX_PER_TICK, run_due_wakes

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "20260809221051_agov_agent_wakes"

SESSION = "sess-agov-wake-03"
REFLEX_REL = Path("tools") / "genesis" / "reflexes" / "agent_cron_reflex.py"


# ---------------------------------------------------------------------------
# Fixtures — the real table, behind the production %s translation
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    return (REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql").read_text(
        encoding="utf-8"
    )


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    A named factory rather than an inline ``translating(...)``: the coherence
    checker's test-db-isolation gate seeds its safe-name set from local factory
    FUNCTIONS, so a name bound straight from the imported helper reads to that
    gate as a raw sqlite3 handle.
    """
    return translating(raw, unclosable=True)


def _storage_module():
    """The module the wake store actually resolves ``get_connection`` from.

    ``sys.modules['tools.db.storage']`` is the compat shim; ``import
    tools.db.storage`` binds the canonical ``icdev.tools.db.storage``. They are
    different objects, and the store imports the shim from inside ``_connect``.
    """
    return sys.modules["tools.db.storage"]


@pytest.fixture
def wake_db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "wake.db"))
    raw.executescript(_migration_ddl())
    conn = _translating_conn(raw)
    monkeypatch.setattr(_storage_module(), "get_connection", lambda *a, **k: conn)
    yield raw
    raw.close()


def _state(raw: sqlite3.Connection, wake_id: str):
    row = raw.execute(f"SELECT state FROM {TABLE} WHERE wake_id = ?", (wake_id,)).fetchone()
    return row[0] if row else None


def _all_states(raw: sqlite3.Connection) -> dict:
    return {
        r[0]: r[1] for r in raw.execute(f"SELECT wake_id, state FROM {TABLE}").fetchall()
    }


class _Recorder:
    """A resumer that counts deliveries per wake id."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[str] = []

    def __call__(self, wake) -> tuple[bool, str]:
        self.calls.append(wake.wake_id)
        return self.ok, "recorded"

    def count(self, wake_id: str) -> int:
        return self.calls.count(wake_id)


class _StubCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubConnection:
    """Just enough DB for one ``poll_once`` pass over one task."""

    def __init__(self, tasks):
        self._tasks = tasks

    def cursor(self):
        return _StubCursor([])

    def execute(self, sql, params=()):
        upper = sql.upper()
        if upper.startswith("SELECT") and "KANBAN_TASKS" in upper:
            return _StubCursor(list(self._tasks))
        return _StubCursor([])

    def commit(self):
        pass

    def close(self):
        pass


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


# ---------------------------------------------------------------------------
# 1. The tick: fired once, resumed once
# ---------------------------------------------------------------------------
class TestTickFiresAndResumesExactlyOnce:
    def test_due_timer_is_fired_and_resumed_by_one_tick(self, wake_db):
        wake = add_timer(SESSION, _past(), note="PR should be green by now")
        resumer = _Recorder()

        result = run_due_wakes(resumer=resumer)

        assert _state(wake_db, wake.wake_id) == STATE_FIRED
        assert result["due"] == 1
        assert result["fired"] == 1
        assert result["resumed"] == 1
        assert resumer.count(wake.wake_id) == 1

    def test_second_consecutive_tick_does_not_resume_again(self, wake_db):
        """The invariant. One suspension, one resumption — ever."""
        wake = add_timer(SESSION, _past())
        resumer = _Recorder()

        first = run_due_wakes(resumer=resumer)
        second = run_due_wakes(resumer=resumer)

        assert resumer.count(wake.wake_id) == 1, "the wake was delivered twice"
        assert first["resumed"] == 1
        assert second["due"] == 0
        assert second["fired"] == 0
        assert second["resumed"] == 0
        assert _state(wake_db, wake.wake_id) == STATE_FIRED

    def test_an_overlapping_tick_that_loses_the_claim_does_not_deliver(self, wake_db):
        """Claim before deliver: the loser of the race must stay silent.

        Simulated by making ``mark_fired`` report False — which is precisely what
        the real conditional UPDATE returns to the tick that arrived second.
        """
        add_timer(SESSION, _past())
        resumer = _Recorder()
        import tools.agent_runtime.wake_tick as tick_module

        # The tick imports mark_fired from the store inside run_due_wakes, so the
        # patch has to land on the store module the import resolves to.
        wake_store = sys.modules["tools.agent_runtime.wake"]
        original = wake_store.mark_fired
        try:
            wake_store.mark_fired = lambda *a, **k: False
            result = tick_module.run_due_wakes(resumer=resumer)
        finally:
            wake_store.mark_fired = original

        assert result["due"] == 1
        assert result["fired"] == 0
        assert result["skipped"] == 1
        assert resumer.calls == [], "a tick that did not claim the wake delivered it anyway"

    def test_a_pending_timer_is_left_alone(self, wake_db):
        wake = add_timer(SESSION, _future())
        resumer = _Recorder()

        result = run_due_wakes(resumer=resumer)

        assert _state(wake_db, wake.wake_id) == STATE_PENDING
        assert result["due"] == 0
        assert resumer.calls == []

    def test_a_failed_delivery_is_counted_not_swallowed(self, wake_db):
        """At most once, and loudly. A wake that fired and did not arrive is the
        invisible failure this epic exists for, so it must show in the result."""
        wake = add_timer(SESSION, _past())
        resumer = _Recorder(ok=False)

        result = run_due_wakes(resumer=resumer)

        assert result["fired"] == 1
        assert result["resumed"] == 0
        assert result["failed"] == 1
        assert _state(wake_db, wake.wake_id) == STATE_FIRED
        assert result["wakes"][0]["resumed"] is False

    def test_a_raising_resumer_does_not_take_down_the_batch(self, wake_db):
        first = add_timer(SESSION, _past() - timedelta(minutes=1))
        second = add_timer(SESSION, _past())
        delivered: list[str] = []

        def resumer(wake):
            if wake.wake_id == first.wake_id:
                raise RuntimeError("boom")
            delivered.append(wake.wake_id)
            return True, "ok"

        result = run_due_wakes(resumer=resumer)

        assert delivered == [second.wake_id]
        assert result["fired"] == 2
        assert result["resumed"] == 1
        assert result["failed"] == 1

    def test_batch_is_capped_and_the_remainder_stays_due(self, wake_db):
        for _ in range(3):
            add_timer(SESSION, _past())
        resumer = _Recorder()

        result = run_due_wakes(resumer=resumer, limit=2)

        assert result["due"] == 3
        assert result["fired"] == 2
        assert sorted(_all_states(wake_db).values()) == [STATE_DUE, STATE_FIRED, STATE_FIRED]

    def test_default_cap_is_declared_not_magic(self):
        assert DEFAULT_MAX_PER_TICK > 0


# ---------------------------------------------------------------------------
# 2. Event keys: a match promotes, a near-miss does nothing
# ---------------------------------------------------------------------------
class TestEventKeys:
    def test_matching_key_moves_a_pending_event_wake_to_due(self, wake_db):
        wake = add_event(SESSION, "pr:1342:ci_green")

        promoted = fire_event("pr:1342:ci_green")

        assert promoted == [wake.wake_id]
        assert _state(wake_db, wake.wake_id) == STATE_DUE

    def test_unmatched_key_changes_nothing(self, wake_db):
        wake = add_event(SESSION, "pr:1342:ci_green")
        before = _all_states(wake_db)

        assert fire_event("pr:1343:ci_green") == []
        assert fire_event("pr:1342:ci_failed") == []
        assert fire_event("task:1342:ci_green") == []

        assert _all_states(wake_db) == before
        assert _state(wake_db, wake.wake_id) == STATE_PENDING

    def test_a_promoted_event_wake_is_then_delivered_by_the_tick(self, wake_db):
        """End to end: pr_watcher's key -> due -> resumed on the next cadence."""
        wake = add_event(SESSION, "pr:1342:ci_green")
        resumer = _Recorder()

        wake_signals.emit_pr_state(
            "https://github.com/icdev-ai/ICDev/pull/1342",
            classification="done",
            pr_state="OPEN",
        )
        result = run_due_wakes(resumer=resumer)

        assert resumer.count(wake.wake_id) == 1
        assert result["resumed"] == 1
        assert _state(wake_db, wake.wake_id) == STATE_FIRED

    def test_re_emitting_the_same_key_promotes_nothing_new(self, wake_db):
        """pr_watcher polls the same green PR every cycle; that must be free."""
        wake = add_event(SESSION, "pr:1342:ci_green")

        first = wake_signals.emit(["pr:1342:ci_green"])
        second = wake_signals.emit(["pr:1342:ci_green"])

        assert first["promoted"] == [wake.wake_id]
        assert second["promoted"] == []


# ---------------------------------------------------------------------------
# 3. The key vocabulary (pure — no DB)
# ---------------------------------------------------------------------------
class TestKeyVocabulary:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://github.com/icdev-ai/ICDev/pull/1342", "1342"),
            ("#1342", "1342"),
            ("1342", "1342"),
            ("", None),
            (None, None),
            ("kanban/agov-wake-03", None),
        ],
    )
    def test_pr_number_parsing(self, raw, expected):
        assert wake_signals.pr_number(raw) == expected

    def test_green_pr_yields_the_documented_key(self):
        assert wake_signals.pr_event_keys(
            "https://github.com/icdev-ai/ICDev/pull/1342", classification="done"
        ) == ["pr:1342:ci_green"]

    def test_merged_pr_yields_both_keys(self):
        keys = wake_signals.pr_event_keys(
            "https://github.com/icdev-ai/ICDev/pull/1342",
            classification="done",
            pr_state="MERGED",
        )
        assert keys == ["pr:1342:ci_green", "pr:1342:merged"]

    def test_open_pr_state_is_not_an_event(self):
        assert wake_signals.pr_event_keys("#7", pr_state="OPEN") == []

    def test_unparseable_pr_yields_no_keys(self):
        """A key built from an unparsed URL is one no agent could subscribe to."""
        assert wake_signals.pr_event_keys("not-a-pr", classification="done") == []

    def test_task_keys_cover_every_status_not_just_done(self):
        assert wake_signals.task_event_keys("agov-wake-03", "done") == [
            "task:agov-wake-03:done"
        ]
        assert wake_signals.task_event_keys("agov-wake-03", "ci_failed") == [
            "task:agov-wake-03:ci_failed"
        ]
        assert wake_signals.task_event_keys("", "done") == []

    def test_emit_with_no_keys_is_a_no_op(self, wake_db):
        assert wake_signals.emit([]) == {"keys": [], "promoted": []}


# ---------------------------------------------------------------------------
# 4. The producers are actually wired
# ---------------------------------------------------------------------------
class TestProducersAreWired:
    def test_kanban_transition_emits_the_task_key(self, monkeypatch, wake_db):
        from tools.kanban import state_machine as sm

        wake = add_event(SESSION, "task:agov-wake-03:done")
        sm.transition(
            "agov-wake-03",
            sm.KanbanState.PR_OPENED,
            sm.KanbanState.DONE,
            actor="test",
            db_exec=None,
            audit_exec=None,
        )
        assert _state(wake_db, wake.wake_id) == STATE_DUE

    def test_pr_watcher_emits_on_classification(self, wake_db):
        """The watcher is the only component that observes 'CI just went green'."""
        from tools.ci.pr_watcher import PRWatcher

        watcher = PRWatcher.__new__(PRWatcher)
        watcher.dry_run = False
        wake = add_event(SESSION, "pr:1342:ci_green")

        out = watcher._emit_wake_events(
            "https://github.com/icdev-ai/ICDev/pull/1342", "done", {"state": "OPEN"}
        )

        assert out["promoted"] == [wake.wake_id]
        assert _state(wake_db, wake.wake_id) == STATE_DUE

    def test_poll_once_actually_calls_the_emitter(self):
        """The method above works — this proves the watch loop reaches it.

        Without this, the emitter could be tested in full and never invoked by
        ``poll_once``, which is the only caller that matters.
        """
        from tools.ci import pr_watcher as pw

        pr_url = "https://github.com/o/r/pull/1342"
        task = {
            "id": "agov-wake-03", "title": "T", "description": "",
            "status": "in_progress", "executor_url": pr_url,
        }
        state = {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"state": "SUCCESS", "conclusion": "SUCCESS", "name": "Test"}],
            "reviews": [{"state": "APPROVED"}],
            "baseRefName": "main",
        }
        watcher = pw.PRWatcher(
            config={"link_prs_on_poll": False, "sibling_conflict_check": False},
            get_connection=lambda: _StubConnection([task]),
            queue_message=lambda *a, **k: {"queued": True},
            fetch_state=lambda url, **k: state,
            fetch_logs=lambda url, **k: "",
            dry_run=True,
        )
        seen: list[tuple] = []
        watcher._emit_wake_events = lambda url, cls, st: seen.append((url, cls, st)) or {}

        watcher.poll_once()

        assert seen, "poll_once never emitted wake events"
        assert seen[0][0] == pr_url
        assert wake_signals.pr_event_keys(seen[0][0], classification=seen[0][1]) == [
            "pr:1342:ci_green"
        ]

    def test_pr_watcher_dry_run_emits_nothing(self, wake_db):
        from tools.ci.pr_watcher import PRWatcher

        watcher = PRWatcher.__new__(PRWatcher)
        watcher.dry_run = True
        wake = add_event(SESSION, "pr:1342:ci_green")

        out = watcher._emit_wake_events(
            "https://github.com/icdev-ai/ICDev/pull/1342", "done", {"state": "OPEN"}
        )

        assert out["keys"] == []
        assert _state(wake_db, wake.wake_id) == STATE_PENDING


# ---------------------------------------------------------------------------
# 5. The reflex — it ticks wakes, it is dispatched, and it is not a daemon
# ---------------------------------------------------------------------------
class TestReflex:
    def test_reflex_run_drains_due_wakes(self, wake_db):
        from tools.genesis.reflexes import agent_cron_reflex

        add_timer(SESSION, _past())
        result = agent_cron_reflex.run({})

        assert result["details"]["wakes"]["due"] == 1
        assert result["details"]["wakes"]["fired"] == 1
        assert result["metric_value"] >= 1.0

    def test_reflex_declares_full_implementation(self):
        from tools.genesis.reflexes import agent_cron_reflex

        assert agent_cron_reflex.IMPLEMENTATION_STATUS == "full"

    def test_reflex_is_actually_dispatched_not_merely_listed(self):
        """Registered AND dispatched. `reflex_registry` listing a reflex has
        historically not been enough — the daemon's own name list is what runs."""
        from tools.genesis import daemon

        assert "agent_cron_reflex" in daemon.REFLEX_NAMES

    def test_reflex_is_configured_on_a_cadence(self):
        import yaml

        cfg = yaml.safe_load(
            (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
        )
        entry = cfg["reflexes"]["agent_cron_reflex"]
        assert entry["enabled"] is True
        assert int(entry["interval"]) > 0

    def test_a_cron_failure_does_not_cost_the_wakes_their_tick(self, wake_db, monkeypatch):
        from tools.genesis.reflexes import agent_cron_reflex

        add_timer(SESSION, _past())
        monkeypatch.setattr(
            agent_cron_reflex, "_tick_cron", lambda conn=None: {"error": "cron exploded"}
        )
        result = agent_cron_reflex.run({})

        assert result["details"]["wakes"]["fired"] == 1
        assert result["success"] is False

    def test_a_wake_store_failure_is_reported_not_raised(self, monkeypatch):
        """A raise here would wedge the daemon for every other reflex."""
        from tools.genesis.reflexes import agent_cron_reflex

        def boom(conn=None):
            raise RuntimeError("store down")

        monkeypatch.setattr(
            sys.modules["tools.agent_runtime.wake_tick"], "run_due_wakes", boom
        )
        result = agent_cron_reflex.run({})

        assert result["status"] == "error"
        assert result["success"] is False
        assert "store down" in result["details"]["error"]


class TestNoNewDaemon:
    """The task's structural constraint: wakes ride an existing loop.

    ICDEV already runs the Genesis daemon, the kanban scheduler and pr_watcher.
    A fourth long-lived process is a fourth thing that dies unnoticed — which is
    the failure mode ('nobody noticed for four days') this epic exists to fix.
    """

    #: Every module under ``tools/`` that offers a ``--daemon`` entrypoint,
    #: measured on 2026-08-09. Growing this list is the decision this test exists
    #: to make deliberate — it should take an argument, not a drive-by edit.
    KNOWN_DAEMON_MODULES = {
        "tools/ci/pr_watcher.py",
        "tools/cloud/csp_monitor.py",
        "tools/creative/creative_engine.py",
        "tools/filesync/sync_engine.py",
        "tools/govcon/govcon_engine.py",
        "tools/innovation/innovation_manager.py",
        "tools/migration_intelligence/migration_manager.py",
        "tools/research/research_engine.py",
    }

    NEW_MODULES = [
        "tools/agent_runtime/wake_tick.py",
        "tools/agent_runtime/wake_signals.py",
        str(REFLEX_REL).replace("\\", "/"),
    ]

    def test_no_new_module_runs_its_own_loop(self):
        for rel in self.NEW_MODULES:
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "while True" not in source, f"{rel} opened a loop of its own"
            assert "def run_daemon" not in source, f"{rel} declared a daemon entrypoint"
            assert "time.sleep" not in source, f"{rel} paces itself — that is a daemon"

    def test_no_new_daemon_module_appeared(self):
        """Nothing new answers to --daemon. Measured, not asserted from memory."""
        found = set()
        for path in (REPO_ROOT / "tools").rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            if "def run_daemon" in source and "--daemon" in source:
                found.add(rel)
        assert found <= self.KNOWN_DAEMON_MODULES, (
            f"a new daemon entrypoint appeared: {sorted(found - self.KNOWN_DAEMON_MODULES)}"
        )

    def test_wakes_did_not_get_their_own_reflex(self):
        """The tick belongs to the cron reflex; a wake reflex would be a new
        scheduled surface with its own cadence, health row and failure mode."""
        from tools.genesis import daemon

        reflex_dir = REPO_ROOT / "tools" / "genesis" / "reflexes"
        assert not list(reflex_dir.glob("*wake*.py")), "a wake reflex module appeared"
        assert not [n for n in daemon.REFLEX_NAMES if "wake" in n]


class TestMirrorParity:
    def test_reflex_copies_are_identical(self):
        """A stale ``icdev/`` mirror is a known way for a reflex change to look
        applied and do nothing."""
        canonical = (REPO_ROOT / REFLEX_REL).read_bytes()
        mirrored = (REPO_ROOT / "icdev" / REFLEX_REL).read_bytes()
        assert canonical == mirrored

    @pytest.mark.parametrize(
        "rel",
        [
            Path("tools") / "agent_runtime" / "wake_tick.py",
            Path("tools") / "agent_runtime" / "wake_signals.py",
        ],
    )
    def test_new_modules_are_mirrored(self, rel: Path):
        assert (REPO_ROOT / "icdev" / rel).read_bytes() == (REPO_ROOT / rel).read_bytes()
