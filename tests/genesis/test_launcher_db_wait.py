# CUI // SP-CTI
"""launcher.py waits for the DECLARED database before its DB-bound children (mfx-boot-01).

MEASURED on two consecutive boots. 2026-09-03: the launcher logged "Waiting
for PostgreSQL (up to 120s)" at 05:02:55 and gave up at 05:05:04; the
dashboard, genesis daemon and proposal_genesis then crashed at assert_identity
and were restarted on the 30s loop until ~05:11, when PostgreSQL finally
accepted -- ~8 minutes after logon. 2026-09-04 (07:20:38 -> ~07:29): the same.
The 120s bound was sized for a fast restart and expired on both real boots,
and the scheduler and pr_watcher, which do not crash, registered once into a
dead database and ran the day unrecorded.

What is asserted: the wait ends when the probe flips (the card's "stub that
flips after 3 calls"); it names the database and the children it is holding
back; it logs progress every 15s; on expiry it starts the children ANYWAY and
says so; a SQLite fallback connection never reads as ready; and the default
bound covers the measured recovery.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis import launcher  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def pg_env(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.setenv("ICDEV_PG_DATABASE", "icdev")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    monkeypatch.delenv(launcher.DB_WAIT_SECONDS_ENV, raising=False)


def _run(probe, *, max_wait):
    clock = _Clock()
    logs = []
    result = launcher._wait_for_postgres(
        max_wait=max_wait, probe=probe, sleep=clock.sleep, clock=clock, log=logs.append)
    return result, logs, clock


# --------------------------------------------------------------------------- #
# 1. The probe flips after three calls
# --------------------------------------------------------------------------- #
def test_the_wait_ends_when_the_probe_flips_after_three_calls(pg_env):
    calls = []

    def probe():
        calls.append(1)
        return len(calls) >= 3

    result, logs, clock = _run(probe, max_wait=600)
    assert result["ready"] is True and result["skipped"] is False
    assert result["attempts"] == 3
    assert result["waited"] == 2 * launcher.DB_PROBE_EVERY_SECONDS
    assert result["database"] == "icdev" and result["backend"] == "postgresql"
    assert "'icdev'" in logs[-1] and "ready" in logs[-1]


def test_the_first_line_names_the_database_and_every_db_bound_child(pg_env):
    result, logs, _ = _run(lambda: True, max_wait=600)
    assert "'icdev'" in logs[0] and "up to 600s" in logs[0]
    for child in launcher.DB_BOUND_CHILDREN:
        assert child in logs[0]
    assert launcher.DB_BOUND_CHILDREN == (
        "dashboard", "genesis_daemon", "proposal_genesis", "kanban_scheduler", "pr_watcher")


# --------------------------------------------------------------------------- #
# 2. Progress every 15s; expiry starts the children anyway and says so
# --------------------------------------------------------------------------- #
def test_progress_is_logged_every_fifteen_seconds_with_the_database_name(pg_env):
    result, logs, clock = _run(lambda: False, max_wait=60)
    progress = [m for m in logs if m.startswith("Still waiting")]
    assert len(progress) == 3, progress          # at 15s, 30s, 45s
    for line in progress:
        assert "'icdev'" in line and "of 60s" in line
    assert result["ready"] is False
    assert result["waited"] >= 60 and clock.t == 60
    assert result["attempts"] == 13               # 0, 5, ..., 60


def test_on_expiry_the_children_are_started_anyway_and_the_log_says_which(pg_env):
    result, logs, _ = _run(lambda: False, max_wait=30)
    last = logs[-1]
    assert last.startswith("WARNING")
    assert "did not accept a connection within 30s" in last
    assert "starting DB-bound children anyway" in last
    for child in launcher.DB_BOUND_CHILDREN:
        assert child in last
    assert "retry their registration" in last
    assert result["ready"] is False and result["bound"] == 30


def test_an_unnamed_database_is_reported_as_such_never_guessed(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.delenv("ICDEV_PG_DATABASE", raising=False)
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    result, logs, _ = _run(lambda: True, max_wait=10)
    assert result["database"] is None
    assert "no database named in .env" in logs[0]


# --------------------------------------------------------------------------- #
# 3. The probe is honest, and the wait is skipped where no server exists
# --------------------------------------------------------------------------- #
def test_a_sqlite_fallback_connection_is_not_ready(monkeypatch):
    import tools.db.storage as storage

    class _Conn:
        _backend = "sqlite"

        def execute(self, *_):
            return self

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    assert launcher._check_postgres() is False, (
        "without ICDEV_PG_NO_FALLBACK get_connection hands back SQLite while PG is "
        "down; a probe that accepts it reports ready for a database still in recovery")
    _Conn._backend = "postgresql"
    assert launcher._check_postgres() is True


def test_a_raising_probe_is_not_ready(monkeypatch):
    import tools.db.storage as storage

    def boom(*a, **k):
        raise ConnectionError("the database system is starting up")

    monkeypatch.setattr(storage, "get_connection", boom)
    assert launcher._check_postgres() is False


def test_a_sqlite_backend_skips_the_wait(monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    calls = []
    result, logs, _ = _run(lambda: calls.append(1) or True, max_wait=600)
    assert result["ready"] is True and result["skipped"] is True
    assert calls == [] and logs == []


# --------------------------------------------------------------------------- #
# 4. The bound is measured, overridable, and applied before the first child
# --------------------------------------------------------------------------- #
def test_the_default_bound_covers_the_measured_eight_minute_recovery(monkeypatch):
    monkeypatch.delenv(launcher.DB_WAIT_SECONDS_ENV, raising=False)
    assert launcher._db_wait_seconds() == launcher.DEFAULT_DB_WAIT_SECONDS == 600
    assert launcher.DEFAULT_DB_WAIT_SECONDS >= 9 * 60, (
        "PostgreSQL took ~8.5 minutes on 2026-09-04; a bound below that expires on a real boot")
    monkeypatch.setenv(launcher.DB_WAIT_SECONDS_ENV, "42")
    assert launcher._db_wait_seconds() == 42
    monkeypatch.setenv(launcher.DB_WAIT_SECONDS_ENV, "not-a-number")
    assert launcher._db_wait_seconds() == 600


def test_main_waits_before_the_first_db_bound_child_and_never_pins_120s():
    src = inspect.getsource(launcher.main)
    assert src.index("_wait_for_postgres(") < src.index("_start_dashboard()")
    assert "max_wait=120" not in src, "the 120s bound expired on both measured boots"
