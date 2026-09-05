# CUI // SP-CTI
"""The scheduler and pr_watcher RETRY their coordination registration (mfx-boot-01).

THE DEFECT, measured on two consecutive boots (2026-09-03 05:02, 2026-09-04
07:20). The supervisor started both services while PostgreSQL still answered
"the database system is starting up". Each registered ONCE inside a `try`
whose `except` gave up for the life of the process, so both ran the whole day
with no `agent_sessions` row: supervisor_status read `not recorded`,
code_staleness could not see what they were running, and the
scheduler_heartbeat_is_fresh claim was blind. A human restarted the scheduler
by verified pid both mornings.

The acceptance the card states: a scheduler started against a registry that
fails twice and then succeeds SHOWS A ROW. That is asserted here against the
REAL session_registry writing a real SQLite table -- the fake is only the
connect step, which is the step that raises when PostgreSQL is in recovery.
"""

from __future__ import annotations

import ast
import inspect
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coordination import registration_retry as rr  # noqa: E402
from tools.coordination import service_identity as si  # noqa: E402
from tools.coordination import session_registry as sr  # noqa: E402

RETRY_MODULE = ROOT / "tools" / "coordination" / "registration_retry.py"


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args):
        self.lines.append(("info", msg % args if args else msg))

    def warning(self, msg, *args):
        self.lines.append(("warning", msg % args if args else msg))

    def text(self):
        return "\n".join(m for _, m in self.lines)


def _fails_then_succeeds(failures: int, exc=ConnectionError("the database system is starting up")):
    calls = {"n": 0}

    def register(intent=None):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise exc
        return {"ok": True, "session_id": "svc-1"}

    register.calls = calls
    return register


# --------------------------------------------------------------------------- #
# 1. The retry's own semantics
# --------------------------------------------------------------------------- #
def test_the_first_attempt_is_immediate_and_a_raise_is_a_failed_attempt():
    log = _Log()
    retry = rr.RegistrationRetry("svc", _fails_then_succeeds(2), log=log)
    assert retry.attempt(0) == "failed"
    assert retry.attempts == 1 and not retry.registered
    assert "starting up" in retry.last_reason
    assert retry.attempt(0) == "not_due", "a failed attempt is not repeated in the same cycle"


def test_backoff_doubles_in_cycles_and_caps():
    """Due at 0, then gaps 1, 2, 4, 8, 16, 32, 32... -- cycles, not seconds,
    because a cycle can run 9-37 minutes and a wall clock would burst."""
    retry = rr.RegistrationRetry("svc", _fails_then_succeeds(99), max_attempts=9, gap_cap=32, log=_Log())
    due = []
    for cycle in range(0, 200):
        if retry.attempt(cycle) == "failed":
            due.append(cycle)
        if retry.exhausted:
            break
    assert due == [0, 1, 3, 7, 15, 31, 63, 95]
    assert retry.exhausted and retry.attempts == 9


def test_twelve_attempts_cover_the_measured_eight_minute_recovery_many_times():
    """Default 12 attempts span ~223 cycles: 3.7h at the scheduler's 60s
    interval, 1.9h at the watcher's 30s. PG took ~8 minutes on both boots."""
    retry = rr.RegistrationRetry("svc", _fails_then_succeeds(99), log=_Log())
    last = None
    for cycle in range(0, 10_000):
        if retry.attempt(cycle) in ("failed", "exhausted"):
            last = cycle
        if retry.exhausted:
            break
    assert retry.attempts == rr.DEFAULT_MAX_ATTEMPTS == 12
    assert last == 223, "the last attempt is at cycle 223: 1+2+4+8+16+32*6"
    assert last * 60 > 8.5 * 60 * 10, "at 60s cycles that outlasts the 8.5-minute recovery tenfold"


def test_a_returned_refusal_counts_as_a_failure_not_a_success():
    """The registry catches its own SQL errors and RETURNS {"ok": False};
    only the connect step raises. Treating one and not the other is how a
    failure goes quiet."""
    log = _Log()
    calls = {"n": 0}

    def register(intent=None):
        calls["n"] += 1
        return {"ok": False, "reason": "no db"} if calls["n"] == 1 else {"ok": True, "session_id": "s"}

    retry = rr.RegistrationRetry("svc", register, log=log)
    assert retry.attempt(0) == "failed" and retry.last_reason == "no db"
    assert retry.attempt(1) == "registered" and retry.session_id == "s"
    assert retry.attempt(2) == "registered", "once registered, attempt() is a no-op"
    assert calls["n"] == 2


def test_every_attempt_is_logged_with_its_number_reason_and_next_cycle():
    log = _Log()
    retry = rr.RegistrationRetry("kanban scheduler", _fails_then_succeeds(2), log=log)
    retry.attempt(0)
    retry.attempt(1)
    retry.attempt(3)
    text = log.text()
    assert "attempt 1/12 FAILED" in text and "retrying at cycle 1" in text
    assert "attempt 2/12 FAILED" in text and "retrying at cycle 3" in text
    assert "succeeded on attempt 3/12 (cycle 3) as svc-1" in text
    assert "starting up" in text, "the reason travels with the attempt"


def test_exhaustion_is_loud_and_terminal():
    log = _Log()
    retry = rr.RegistrationRetry("svc", _fails_then_succeeds(99), max_attempts=2, log=log)
    assert retry.attempt(0) == "failed"
    assert retry.attempt(1) == "exhausted"
    assert retry.attempt(50) == "exhausted"
    assert retry.describe()["next_due_cycle"] is None
    warnings = [m for lvl, m in log.lines if lvl == "warning"]
    assert any("FAILED on all 2 attempts" in m and "silent service" in m for m in warnings)


def test_the_retry_never_restarts_or_exits_the_loop():
    """The 'never restart' rule (pid 29880's five silent hours) is about the
    LOOP. Registration is retried; the process is never re-executed or exited
    for it, and attempt() never raises out of the loop either."""
    tree = ast.parse(RETRY_MODULE.read_text(encoding="utf-8"))
    forbidden = {"exit", "_exit", "execv", "execve", "execl", "kill", "abort"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name not in forbidden, f"registration_retry calls {name}()"
    retry = rr.RegistrationRetry("svc", _fails_then_succeeds(99, exc=RuntimeError("boom")), log=_Log())
    assert retry.attempt(0) == "failed"


# --------------------------------------------------------------------------- #
# 2. The REAL registry, failing twice then succeeding, shows a row
# --------------------------------------------------------------------------- #
@pytest.fixture
def flaky_registry(tmp_path, monkeypatch):
    """session_registry whose CONNECT step raises twice, then hands back a
    SQLite connection on a temp file -- the shape of a service started while
    PostgreSQL is in recovery. Never the ambient database."""
    from tools.db.storage import get_connection as real_get_connection
    import tools.coordination.code_identity as code_identity

    db = tmp_path / "coordination.db"
    calls = {"n": 0}

    def flaky_get_connection(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionError(
                "PostgreSQL unavailable and ICDEV_PG_NO_FALLBACK=true — "
                "the database system is starting up")
        return real_get_connection(db_path=str(db))

    monkeypatch.setattr(sr, "get_connection", flaky_get_connection)
    monkeypatch.setattr(sr, "_table_ready", False)
    monkeypatch.setattr(code_identity, "boot_identity", lambda: {})
    monkeypatch.setattr(si, "_OWNED", set())
    # get_session_id() reads CLAUDE_SESSION_ID *before* ICDEV_SESSION_ID, so
    # clearing si.SESSION_ENV alone leaves a leaked CLAUDE_SESSION_ID winning
    # and the service registers under someone else's id. Any test in this
    # process that sets one without restoring it (tests/coordination/
    # test_code_identity.py::_as_session sets BOTH, unmonkeypatched) lands its
    # id on our row -- and in shard 4 that file runs 33 entries ahead of this
    # one, so the order is CI's, not a local accident.
    import tools.airgap.hook_compat as _hc

    for var in ("CLAUDE_SESSION_ID", si.SESSION_ENV, si.AGENT_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_hc, "_session_id", None)  # the cached fallback uuid
    return db, calls


def _rows(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT session_id, agent_type, pid, current_intent FROM agent_sessions"
        ).fetchall()
    finally:
        conn.close()


def test_the_scheduler_registers_on_the_third_cycle_and_shows_a_row(flaky_registry):
    from tools.genesis import kanban_scheduler as ks

    db, calls = flaky_registry
    registry, retry = ks._coordination_setup()
    assert registry is sr and retry is not None

    assert ks._ensure_registered(retry, registry, cycle=0) is None   # start-up: connect raises
    assert ks._ensure_registered(retry, registry, cycle=1) is None   # cycle 1: connect raises
    assert ks._ensure_registered(retry, registry, cycle=2) is None   # not due until cycle 3
    assert retry.attempts == 2 and not db.exists()
    assert ks._ensure_registered(retry, registry, cycle=3) is sr     # PG accepts

    rows = _rows(db)
    assert len(rows) == 1
    session_id, agent_type, pid, intent = rows[0]
    assert session_id == f"kanban-scheduler-{os.getpid()}"
    assert agent_type == "kanban" and pid == os.getpid()
    assert intent == ks._REGISTRATION_INTENT
    assert retry.registered and retry.attempts == 3


def test_the_watcher_daemon_registers_after_two_failed_iterations(flaky_registry, monkeypatch):
    from tools.ci import pr_watcher as pw
    from tools.genesis import code_reload

    db, calls = flaky_registry
    monkeypatch.setattr(code_reload, "snapshot", lambda: {})
    monkeypatch.setattr(code_reload, "restart_if_code_changed", lambda *a, **k: None)
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)

    watcher = pw.PRWatcher(config={"restart_on_code_change": False},
                           get_connection=lambda: None, dry_run=True)
    watcher.poll_once = lambda: pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    watcher._sweep_unlinked_prs = lambda report: None

    watcher.run_daemon(interval=0, max_iterations=4)

    rows = _rows(db)
    assert len(rows) == 1
    session_id, agent_type, pid, intent = rows[0]
    assert session_id == f"pr-watcher-{os.getpid()}"
    assert agent_type == "pr_watcher" and pid == os.getpid()
    assert intent == pw._REGISTRATION_INTENT
    assert calls["n"] >= 3, "the third connect is the one that landed the row"


# --------------------------------------------------------------------------- #
# 3. The loops ask again -- the give-up shape is gone from both
# --------------------------------------------------------------------------- #
def test_the_scheduler_main_retries_inside_its_loop():
    from tools.genesis import kanban_scheduler as ks

    src = inspect.getsource(ks.main)
    first = src.index("_ensure_registered(_registration, _coord_mod, cycle=0)")
    loop = src.index("while True:")
    retry = src.index("_ensure_registered(_registration, _coord_mod, cycle=cycle)")
    assert first < loop < retry, "one attempt at start-up, then again inside the loop"
    assert "_start_heartbeat_pump(_pump_state, _coord_reg)" in src[retry:], (
        "the pump must start the moment a late registration lands")
    assert "except Exception as _reg_exc" not in src, "the one-shot give-up is gone"


def test_the_watcher_loop_retries_and_still_heartbeats():
    from tools.ci import pr_watcher as pw

    src = inspect.getsource(pw.PRWatcher.run_daemon)
    assert "_registration_attempt(_registration, 0)" in src
    loop = src.index("while True:")
    assert src.index("_registration_attempt(_registration, iteration)") > loop
    assert "session_registry" in src and "heartbeat()" in src
    assert "_reg.register(" not in src, "the one-shot registration is gone"


def test_both_services_still_claim_a_per_process_identity_first():
    """The identity is claimed BEFORE the first attempt, on the same helper
    (autonomy-sid-01) -- retrying must not re-invent the id scheme."""
    from tools.ci import pr_watcher as pw
    from tools.genesis import kanban_scheduler as ks

    for fn in (ks._coordination_setup, pw._coordination_registration):
        src = inspect.getsource(fn)
        assert "claim_service_identity(" in src
        assert src.index("claim_service_identity(") < src.index("RegistrationRetry(")
