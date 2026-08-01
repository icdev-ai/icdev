# CUI // SP-CTI
"""Unit tests for the SAG user-facing cron (sag-cron-01).

DB-independent: persistence is faked with an in-memory sqlite connection injected
via shim-aware monkeypatch of ``tools.db.storage.get_connection`` (with the %s→?
placeholder translation the real storage layer performs). No shared-DB tables are
required. Execution modes are stubbed so no LLM / subprocess runs.
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timezone

import pytest

import tools.agent_runtime.cron as cron


class _Conn:
    """Thin sqlite wrapper translating the %s placeholders the module emits."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()


@pytest.fixture()
def fake_db(monkeypatch):
    conn = _Conn()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    return conn


# ---------------------------------------------------------------------------
# schedule parsing
# ---------------------------------------------------------------------------
def test_parse_interval_units():
    assert cron.parse_interval("90s") == 90
    assert cron.parse_interval("15m") == 900
    assert cron.parse_interval("2h") == 7200
    assert cron.parse_interval("1d") == 86400
    assert cron.parse_interval("300") == 300


def test_parse_interval_rejects_garbage():
    with pytest.raises(ValueError):
        cron.parse_interval("banana")
    with pytest.raises(ValueError):
        cron.parse_interval("")


def test_parse_cron_field_expansion():
    parsed = cron.parse_cron("*/15 9-17 * * 1-5")
    assert parsed["minute"] == {0, 15, 30, 45}
    assert parsed["hour"] == set(range(9, 18))
    assert parsed["dow"] == {1, 2, 3, 4, 5}


def test_parse_cron_wrong_field_count():
    with pytest.raises(ValueError):
        cron.parse_cron("* * * *")


def test_cron_matches_weekday_nine_am():
    parsed = cron.parse_cron("0 9 * * 1-5")
    # 2026-07-27 is a Monday
    monday_9 = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
    saturday_9 = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)  # Saturday
    assert cron._cron_matches(parsed, monday_9) is True
    assert cron._cron_matches(parsed, saturday_9) is False


def test_next_run_at_interval():
    after = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    nxt = cron.next_run_at("interval", "15m", after=after)
    assert (nxt - after).total_seconds() == 900


def test_next_run_at_cron_advances_to_next_minute_slot():
    after = datetime(2026, 7, 27, 8, 59, tzinfo=timezone.utc)  # Monday 08:59
    nxt = cron.next_run_at("cron", "0 9 * * 1-5", after=after)
    assert nxt.hour == 9 and nxt.minute == 0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def test_create_and_get_job(fake_db):
    job = cron.create_job(
        "nightly", "script", "python tools/testing/health_check.py --json",
        "cron", "0 3 * * *", conn=fake_db,
    )
    assert job["id"].startswith("cron-")
    assert job["status"] == "active"
    assert job["next_run_at"]
    fetched = cron.get_job(job["id"], conn=fake_db)
    assert fetched["name"] == "nightly"
    assert fetched["mode"] == "script"


def test_create_rejects_bad_mode(fake_db):
    with pytest.raises(ValueError):
        cron.create_job("x", "telepathy", "hi", "interval", "5m", conn=fake_db)


def test_create_rejects_bad_cron(fake_db):
    with pytest.raises(ValueError):
        cron.create_job("x", "agent", "hi", "cron", "not a cron", conn=fake_db)


def test_list_pause_resume_remove(fake_db):
    a = cron.create_job("a", "agent", "hi", "interval", "5m", conn=fake_db)
    cron.create_job("b", "agent", "yo", "interval", "10m", conn=fake_db)
    assert len(cron.list_jobs(conn=fake_db)) == 2

    cron.set_status(a["id"], "paused", conn=fake_db)
    assert cron.get_job(a["id"], conn=fake_db)["status"] == "paused"
    assert len(cron.list_jobs(status="active", conn=fake_db)) == 1

    assert cron.remove_job(a["id"], conn=fake_db) is True
    assert cron.get_job(a["id"], conn=fake_db) is None
    assert cron.remove_job("cron-doesnotexist", conn=fake_db) is False


# ---------------------------------------------------------------------------
# execution + tick + retry/backoff
# ---------------------------------------------------------------------------
def test_run_due_jobs_executes_and_records(fake_db, monkeypatch):
    monkeypatch.setattr(cron, "execute_job", lambda job: (True, "done", ""))
    job = cron.create_job("j", "agent", "hi", "interval", "5m", conn=fake_db)
    # force it due
    fake_db.execute(
        "UPDATE agent_cron_jobs SET next_run_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", job["id"]),
    )
    fake_db.commit()
    tick = cron.run_due_jobs(conn=fake_db)
    assert tick["due"] == 1
    assert tick["succeeded"] == 1
    runs = cron.list_runs(job["id"], conn=fake_db)
    assert len(runs) == 1 and runs[0]["success"] == 1
    # rescheduled forward, attempt reset
    refreshed = cron.get_job(job["id"], conn=fake_db)
    assert refreshed["last_status"] == "success"
    assert refreshed["attempt"] == 0
    assert refreshed["run_count"] == 1


def test_failure_triggers_backoff_retry(fake_db, monkeypatch):
    monkeypatch.setattr(cron, "execute_job", lambda job: (False, "", "boom"))
    job = cron.create_job(
        "j", "agent", "hi", "interval", "1h", conn=fake_db,
        max_retries=2, retry_backoff_seconds=30,
    )
    fake_db.execute(
        "UPDATE agent_cron_jobs SET next_run_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", job["id"]),
    )
    fake_db.commit()
    cron.run_due_jobs(conn=fake_db)
    refreshed = cron.get_job(job["id"], conn=fake_db)
    # a retry is pending, attempt incremented, and next_run is soon (not 1h out)
    assert refreshed["attempt"] == 1
    assert refreshed["last_status"] == "failed"
    nxt = cron._parse_iso(refreshed["next_run_at"])
    assert nxt is not None
    delta = (nxt - datetime.now(timezone.utc)).total_seconds()
    assert delta < 300  # backoff ~30s, well under the 1h schedule


def test_script_mode_rejects_non_allowlisted(fake_db):
    job = cron.create_job("bad", "script", "rm -rf /", "interval", "5m", conn=fake_db)
    success, out, err = cron._execute_script(job)
    assert success is False
    assert "allowlist" in err.lower()


def test_delivery_log_is_noop(fake_db):
    job = cron.create_job("j", "agent", "hi", "interval", "5m", conn=fake_db)
    delivered, target = cron.deliver_result(job, True, "output", "")
    assert delivered is False
    assert target == "log"
