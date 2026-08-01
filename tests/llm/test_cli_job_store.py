# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/job_store.py.

The job store is the pure persistence layer for the CLI LLM bridge — it knows
nothing about subprocesses or routing, only how to create, claim, complete,
fail, read, and wait on rows in ``cli_llm_jobs``. These tests run it against a
*real* throwaway SQLite ``cli_llm_jobs`` table (the migration-183 / conftest
schema), so the SQL is validated end-to-end without touching real storage.

Per the shim-aware monkeypatch rule (``tools.*`` and ``icdev.tools.*`` are
distinct module objects), ``get_connection`` is rebound on the imported
``job_store`` module via ``setattr`` rather than pytest's string form.

Covers (uclb-job-07):
  - create_job        → row written as 'pending' with the given fields
  - claim_job         → oldest pending flips to 'running'; backend-filtered
  - claim_job race    → only one of two concurrent claimers wins; loser gets None
  - complete_job      → 'done' with result + token counts + completed_at
  - fail_job          → 'error' with message + completed_at
  - get_job           → dict for known id, None for unknown
  - wait_for_job      → returns immediately on terminal status
  - wait_for_job      → soft-wait timeout returns the still-'running' row (not None)
  - wait_for_job      → None for an unknown id
  - list_jobs         → newest-first, filterable by status / context_id
"""
from __future__ import annotations

import importlib
import pathlib
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

job_store = importlib.import_module("tools.llm.cli_bridge.job_store")


# The cli_llm_jobs DDL mirrors migration 183 / the conftest MINIMAL schema.
_CLI_LLM_JOBS_DDL = """
CREATE TABLE cli_llm_jobs (
    id             TEXT PRIMARY KEY,
    function       TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    system_prompt  TEXT DEFAULT '',
    model_id       TEXT,
    backend        TEXT DEFAULT 'auto',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'error')),
    result         TEXT,
    error          TEXT,
    context_id     TEXT,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at     TEXT,
    updated_at     TEXT,
    claimed_at     TEXT,
    completed_at   TEXT
);
CREATE INDEX idx_cli_llm_jobs_claim   ON cli_llm_jobs (status, backend, created_at);
CREATE INDEX idx_cli_llm_jobs_context ON cli_llm_jobs (context_id);
"""


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """Throwaway SQLite cli_llm_jobs table wired into job_store.get_connection."""
    db_path = tmp_path / "cli_jobs.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(_CLI_LLM_JOBS_DDL)
    seed.commit()
    seed.close()

    # job_store authors %s placeholders for PostgreSQL and relies on
    # StorageConnection to rewrite them; a bare sqlite3 connection drops that
    # layer and every statement raises `near "%": syntax error`.
    from _sql_compat import connect as _tconnect

    def fake_get_connection():
        return _tconnect(db_path)

    # Shim-aware: rebind the name on the imported module object directly.
    monkeypatch.setattr(job_store, "get_connection", fake_get_connection)
    return db_path


# ────────────────────────────────────────────────────────────────────────────
# create_job
# ────────────────────────────────────────────────────────────────────────────


def test_create_job_returns_hex_id_and_writes_pending(job_db):
    job_id = job_store.create_job(
        function="code_generation",
        prompt="write a haiku",
        system_prompt="be terse",
        model_id="claude-cli",
        backend="subprocess",
        context_id="ctx-1",
    )
    # uuid4().hex is 32 lowercase hex chars.
    assert isinstance(job_id, str) and len(job_id) == 32

    row = job_store.get_job(job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["function"] == "code_generation"
    assert row["prompt"] == "write a haiku"
    assert row["system_prompt"] == "be terse"
    assert row["model_id"] == "claude-cli"
    assert row["backend"] == "subprocess"
    assert row["context_id"] == "ctx-1"
    # created_at / updated_at stamped; terminal timestamps not yet set.
    assert row["created_at"] and row["updated_at"]
    assert row["claimed_at"] is None
    assert row["completed_at"] is None


def test_create_job_defaults_backend_and_classification(job_db):
    job_id = job_store.create_job(function="f", prompt="p")
    row = job_store.get_job(job_id)
    assert row["backend"] == "auto"
    assert row["classification"] == job_store.DEFAULT_CLASSIFICATION
    assert row["system_prompt"] == ""


def test_create_job_unique_ids(job_db):
    ids = {job_store.create_job(function="f", prompt=str(i)) for i in range(5)}
    assert len(ids) == 5


# ────────────────────────────────────────────────────────────────────────────
# claim_job
# ────────────────────────────────────────────────────────────────────────────


def test_claim_job_flips_pending_to_running(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    claimed = job_store.claim_job("subprocess")

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["claimed_at"] is not None


def test_claim_job_none_when_empty(job_db):
    assert job_store.claim_job("subprocess") is None


def test_claim_job_is_backend_filtered(job_db):
    # Only a mailbox job is pending; a subprocess worker must not claim it.
    mailbox_id = job_store.create_job(function="f", prompt="p", backend="mailbox")
    assert job_store.claim_job("subprocess") is None

    claimed = job_store.claim_job("mailbox")
    assert claimed is not None and claimed["id"] == mailbox_id


def test_claim_job_takes_oldest_first(job_db):
    first = job_store.create_job(function="f", prompt="first", backend="subprocess")
    # Distinct created_at so ORDER BY created_at ASC is deterministic.
    time.sleep(0.01)
    job_store.create_job(function="f", prompt="second", backend="subprocess")

    claimed = job_store.claim_job("subprocess")
    assert claimed["id"] == first


def test_claim_job_second_claim_returns_none(job_db):
    # One pending row; first claim wins, second finds nothing pending.
    job_store.create_job(function="f", prompt="p", backend="subprocess")
    first = job_store.claim_job("subprocess")
    second = job_store.claim_job("subprocess")
    assert first is not None
    assert second is None


def test_claim_job_concurrent_only_one_winner(job_db):
    """Two threads race for the single pending row — exactly one wins."""
    job_store.create_job(function="f", prompt="p", backend="subprocess")

    results: list = []
    results_lock = threading.Lock()
    start = threading.Barrier(2)

    def worker():
        start.wait(timeout=2)
        claimed = job_store.claim_job("subprocess")
        with results_lock:
            results.append(claimed)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1


# ────────────────────────────────────────────────────────────────────────────
# complete_job / fail_job
# ────────────────────────────────────────────────────────────────────────────


def test_complete_job_sets_done_with_tokens(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")

    ok = job_store.complete_job(job_id, "the answer", input_tokens=12, output_tokens=34)
    assert ok is True

    row = job_store.get_job(job_id)
    assert row["status"] == "done"
    assert row["result"] == "the answer"
    assert row["input_tokens"] == 12
    assert row["output_tokens"] == 34
    assert row["completed_at"] is not None


def test_complete_job_unknown_id_returns_false(job_db):
    assert job_store.complete_job("does-not-exist", "x") is False


def test_fail_job_sets_error_with_message(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")

    ok = job_store.fail_job(job_id, "Claude CLI exited 1: boom")
    assert ok is True

    row = job_store.get_job(job_id)
    assert row["status"] == "error"
    assert "boom" in row["error"]
    assert row["completed_at"] is not None


def test_fail_job_unknown_id_returns_false(job_db):
    assert job_store.fail_job("ghost", "nope") is False


# ────────────────────────────────────────────────────────────────────────────
# get_job
# ────────────────────────────────────────────────────────────────────────────


def test_get_job_unknown_id_returns_none(job_db):
    assert job_store.get_job("no-such-id") is None


def test_get_job_roundtrips_all_columns(job_db):
    job_id = job_store.create_job(
        function="f", prompt="p", model_id="m", backend="mailbox", context_id="c"
    )
    row = job_store.get_job(job_id)
    # All schema columns present in the returned dict.
    for col in (
        "id", "function", "prompt", "system_prompt", "model_id", "backend",
        "status", "result", "error", "context_id", "input_tokens",
        "output_tokens", "tenant_id", "classification", "created_at",
        "updated_at", "claimed_at", "completed_at",
    ):
        assert col in row


# ────────────────────────────────────────────────────────────────────────────
# wait_for_job
# ────────────────────────────────────────────────────────────────────────────


def test_wait_for_job_returns_immediately_when_done(job_db):
    job_id = job_store.create_job(function="f", prompt="p")
    job_store.complete_job(job_id, "already done")

    start = time.time()
    row = job_store.wait_for_job(job_id, timeout=5.0, poll_interval=0.5)
    elapsed = time.time() - start

    assert row["status"] == "done"
    assert row["result"] == "already done"
    # Terminal on first read — should not have slept a full poll interval.
    assert elapsed < 0.5


def test_wait_for_job_returns_error_row(job_db):
    job_id = job_store.create_job(function="f", prompt="p")
    job_store.fail_job(job_id, "kaboom")

    row = job_store.wait_for_job(job_id, timeout=5.0, poll_interval=0.05)
    assert row["status"] == "error"
    assert "kaboom" in row["error"]


def test_wait_for_job_soft_wait_timeout_returns_running_row(job_db):
    """The soft-wait core contract: a still-running job is returned, NOT None."""
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")  # status → running, but never completed

    start = time.time()
    row = job_store.wait_for_job(job_id, timeout=0.2, poll_interval=0.05)
    elapsed = time.time() - start

    # It waited (didn't return instantly) but handed back the running row.
    assert row is not None
    assert row["status"] == "running"
    assert elapsed >= 0.2


def test_wait_for_job_completes_mid_wait(job_db):
    """A worker finishing during the wait window is observed before timeout."""
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")

    def finisher():
        time.sleep(0.1)
        job_store.complete_job(job_id, "finished mid-wait", output_tokens=9)

    t = threading.Thread(target=finisher)
    t.start()
    try:
        row = job_store.wait_for_job(job_id, timeout=5.0, poll_interval=0.02)
    finally:
        t.join(timeout=5)

    assert row["status"] == "done"
    assert row["result"] == "finished mid-wait"
    assert row["output_tokens"] == 9


def test_wait_for_job_unknown_id_returns_none(job_db):
    assert job_store.wait_for_job("nope", timeout=0.1, poll_interval=0.05) is None


# ────────────────────────────────────────────────────────────────────────────
# list_jobs
# ────────────────────────────────────────────────────────────────────────────


def test_list_jobs_newest_first(job_db):
    a = job_store.create_job(function="f", prompt="a")
    time.sleep(0.01)
    b = job_store.create_job(function="f", prompt="b")

    rows = job_store.list_jobs()
    ids = [r["id"] for r in rows]
    # Newest (b) first.
    assert ids[0] == b and ids[1] == a


def test_list_jobs_filters_by_status(job_db):
    done_id = job_store.create_job(function="f", prompt="done")
    job_store.complete_job(done_id, "x")
    job_store.create_job(function="f", prompt="pending")

    done_rows = job_store.list_jobs(status="done")
    assert [r["id"] for r in done_rows] == [done_id]


def test_list_jobs_filters_by_context_id(job_db):
    mine = job_store.create_job(function="f", prompt="p", context_id="agent-7")
    job_store.create_job(function="f", prompt="p", context_id="agent-other")

    rows = job_store.list_jobs(context_id="agent-7")
    assert [r["id"] for r in rows] == [mine]


def test_list_jobs_respects_limit(job_db):
    for i in range(4):
        job_store.create_job(function="f", prompt=str(i))
    rows = job_store.list_jobs(limit=2)
    assert len(rows) == 2


# ────────────────────────────────────────────────────────────────────────────
# reap_stale_jobs — orphaned 'running' rows from a dead worker host
# ────────────────────────────────────────────────────────────────────────────


def _backdate_claimed_at(db_path, job_id, seconds_ago):
    """Push a row's claimed_at into the past so it looks orphaned."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE cli_llm_jobs SET claimed_at = ?, created_at = ? WHERE id = ?",
        (old, old, job_id),
    )
    conn.commit()
    conn.close()


def test_reap_stale_jobs_transitions_orphaned_running_to_error(job_db):
    # A claimed (running) job whose worker host died long ago.
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")  # → running, claimed_at = now
    _backdate_claimed_at(job_db, job_id, seconds_ago=3600)

    reaped = job_store.reap_stale_jobs(max_age_seconds=60)
    assert reaped == 1

    row = job_store.get_job(job_id)
    assert row["status"] == "error"
    assert "orphaned" in (row["error"] or "")
    assert row["completed_at"] is not None


def test_reap_stale_jobs_leaves_fresh_running_untouched(job_db):
    # Claimed just now — well within any sane cutoff; must NOT be reaped.
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")

    reaped = job_store.reap_stale_jobs(max_age_seconds=60)
    assert reaped == 0
    assert job_store.get_job(job_id)["status"] == "running"


def test_reap_stale_jobs_ignores_pending(job_db):
    # Pending jobs were never claimed — they stay enqueued for a worker.
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    _backdate_claimed_at(job_db, job_id, seconds_ago=3600)  # backdates created_at too

    reaped = job_store.reap_stale_jobs(max_age_seconds=60)
    assert reaped == 0
    assert job_store.get_job(job_id)["status"] == "pending"


def test_reap_stale_jobs_ignores_terminal_rows(job_db):
    done_id = job_store.create_job(function="f", prompt="p")
    job_store.complete_job(done_id, "answer")
    err_id = job_store.create_job(function="f", prompt="p")
    job_store.fail_job(err_id, "boom")
    _backdate_claimed_at(job_db, done_id, seconds_ago=3600)
    _backdate_claimed_at(job_db, err_id, seconds_ago=3600)

    assert job_store.reap_stale_jobs(max_age_seconds=60) == 0
    assert job_store.get_job(done_id)["status"] == "done"
    assert job_store.get_job(err_id)["status"] == "error"


def test_reap_stale_jobs_counts_multiple(job_db):
    ids = []
    for i in range(3):
        jid = job_store.create_job(function="f", prompt=str(i), backend="subprocess")
        job_store.claim_job("subprocess")
        _backdate_claimed_at(job_db, jid, seconds_ago=3600)
        ids.append(jid)

    assert job_store.reap_stale_jobs(max_age_seconds=60) == 3
    assert all(job_store.get_job(j)["status"] == "error" for j in ids)


def test_reap_stale_jobs_default_cutoff_from_env(job_db, monkeypatch):
    # Default cutoff = ICDEV_CLI_BRIDGE_MAX_SECONDS + STALE_GRACE_SECONDS.
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_MAX_SECONDS", "100")
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_STALE_GRACE_SECONDS", "50")
    assert job_store._stale_running_cutoff_seconds() == 150

    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")
    # Aged 200s > 150s cutoff → reaped with the default (no explicit max_age).
    _backdate_claimed_at(job_db, job_id, seconds_ago=200)
    assert job_store.reap_stale_jobs() == 1
    assert job_store.get_job(job_id)["status"] == "error"


def test_reap_stale_jobs_zero_cutoff_is_noop(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    job_store.claim_job("subprocess")
    _backdate_claimed_at(job_db, job_id, seconds_ago=3600)
    assert job_store.reap_stale_jobs(max_age_seconds=0) == 0
    assert job_store.get_job(job_id)["status"] == "running"


# ────────────────────────────────────────────────────────────────────────────
# module surface
# ────────────────────────────────────────────────────────────────────────────


def test_terminal_statuses_constant():
    assert set(job_store.TERMINAL_STATUSES) == {"done", "error"}
