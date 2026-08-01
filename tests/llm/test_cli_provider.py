# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/cli_provider.py (CLILLMProvider).

The provider no longer shells out inline (uclb-job-03): ``invoke`` now
``create_job`` → ``_dispatch`` → ``wait_for_job`` and branches on the terminal
status. These tests exercise that orchestration against the *real* job store
(``tools.llm.cli_bridge.job_store``) backed by a throwaway SQLite ``cli_llm_jobs``
table, so the migration/schema is validated end-to-end — no real ``claude`` CLI
or background thread is ever spawned.

Per the shim-aware monkeypatch rule (tools.* vs icdev.tools.* are distinct module
objects), ``get_connection`` is rebound on the imported ``job_store`` module via
``setattr`` rather than pytest's string-form ``monkeypatch.setattr("tools...")``.

Covers (uclb-job-03):
  - 'done'  → invoke returns an LLMResponse carrying the job result
  - 'error' → invoke raises LLMUnavailableError (router falls through)
  - still running at soft-wait → invoke raises CLIJobDeferred(job_id=...)
  - CLIJobDeferred subclasses LLMUnavailableError (non-chat callers fall back)
  - no backend wired (default dispatch) → job left pending → deferred
"""
from __future__ import annotations

import importlib
import pathlib
import sqlite3
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Modules under test + the contracts they return/raise.
cli_provider = importlib.import_module("tools.llm.cli_bridge.cli_provider")
job_store = importlib.import_module("tools.llm.cli_bridge.job_store")
from tools.llm.provider import LLMRequest, LLMResponse  # noqa: E402
from tools.llm.router import LLMUnavailableError  # noqa: E402

CLIJobDeferred = cli_provider.CLIJobDeferred


# The cli_llm_jobs DDL mirrors migration 183 / conftest MINIMAL schema.
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


@pytest.fixture
def request_obj():
    return LLMRequest(messages=[{"role": "user", "content": "ping"}], system_prompt="be terse")


def _provider(dispatcher=None, soft_wait_seconds=5):
    return cli_provider.CLILLMProvider(
        cli_binary="claude",
        soft_wait_seconds=soft_wait_seconds,
        dispatcher=dispatcher,
        poll_interval=0.02,
    )


# ────────────────────────────────────────────────────────────────────────────
# invoke() — 'done' → LLMResponse
# ────────────────────────────────────────────────────────────────────────────


def test_invoke_done_returns_llmresponse(job_db, request_obj):
    def completing_dispatcher(job_id, backend):
        # A real backend would run the CLI; here we just complete the row.
        job_store.complete_job(job_id, "real answer\n", input_tokens=3, output_tokens=5)

    provider = _provider(dispatcher=completing_dispatcher)
    resp = provider.invoke(request_obj, model_id="claude-cli", model_config={})

    assert isinstance(resp, LLMResponse)
    assert resp.content == "real answer"  # result, stripped
    assert resp.provider == "cli"
    assert resp.model_id == "claude-cli"
    assert resp.input_tokens == 3
    assert resp.output_tokens == 5
    assert resp.stop_reason == "stop"


def test_invoke_creates_job_with_flattened_prompt(job_db, request_obj, monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "subprocess")
    captured = {}

    def completing_dispatcher(job_id, backend):
        captured["job"] = job_store.get_job(job_id)
        captured["backend"] = backend
        job_store.complete_job(job_id, "ok")

    provider = _provider(dispatcher=completing_dispatcher)
    provider.invoke(request_obj, model_id="claude-cli", model_config={})

    job = captured["job"]
    assert job is not None
    # The flattened prompt carries both system and user text.
    assert "ping" in job["prompt"] and "be terse" in job["prompt"]
    assert job["model_id"] == "claude-cli"
    # 'auto' was resolved to a concrete backend and recorded on the row.
    assert captured["backend"] == "subprocess"
    assert job["backend"] == "subprocess"


def test_invoke_defaults_model_id_when_blank(job_db, request_obj):
    def completing_dispatcher(job_id, backend):
        job_store.complete_job(job_id, "ok")

    provider = _provider(dispatcher=completing_dispatcher)
    resp = provider.invoke(request_obj, model_id="", model_config={})
    assert resp.model_id == cli_provider.DEFAULT_MODEL_ID


# ────────────────────────────────────────────────────────────────────────────
# invoke() — 'error' → LLMUnavailableError
# ────────────────────────────────────────────────────────────────────────────


def test_invoke_error_raises_unavailable(job_db, request_obj):
    def failing_dispatcher(job_id, backend):
        job_store.fail_job(job_id, "Claude CLI exited 1: boom")

    provider = _provider(dispatcher=failing_dispatcher)
    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.invoke(request_obj, model_id="claude-cli", model_config={})
    # The underlying CLI error is surfaced in the message.
    assert "exited 1" in str(excinfo.value)
    # An error is NOT a deferral.
    assert not isinstance(excinfo.value, CLIJobDeferred)


# ────────────────────────────────────────────────────────────────────────────
# invoke() — still running at soft-wait → CLIJobDeferred
# ────────────────────────────────────────────────────────────────────────────


def test_invoke_still_running_raises_deferred(job_db, request_obj):
    # Dispatcher kicks off "work" but never completes the row within the wait.
    def slow_dispatcher(job_id, backend):
        return  # leaves the job pending

    provider = _provider(dispatcher=slow_dispatcher, soft_wait_seconds=0.2)
    with pytest.raises(CLIJobDeferred) as excinfo:
        provider.invoke(request_obj, model_id="claude-cli", model_config={})

    deferred = excinfo.value
    assert deferred.job_id
    # The job is still in the store for a background worker to finish + cache.
    row = job_store.get_job(deferred.job_id)
    assert row is not None
    assert row["status"] in ("pending", "running")


def test_invoke_no_backend_wired_defers(job_db, request_obj, monkeypatch):
    # No injected dispatcher AND no backend module → graceful no-op → deferred.
    monkeypatch.setattr(cli_provider, "_resolve_backend_dispatcher", lambda backend: None)
    provider = _provider(dispatcher=None, soft_wait_seconds=0.2)
    with pytest.raises(CLIJobDeferred):
        provider.invoke(request_obj, model_id="claude-cli", model_config={})


# ────────────────────────────────────────────────────────────────────────────
# resolve_backend() — dynamic subprocess-else-mailbox selection (uclb-job-06)
# ────────────────────────────────────────────────────────────────────────────


def test_resolve_backend_auto_headless_picks_subprocess(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)
    from tools.llm.cli_bridge import capability

    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    assert cli_provider.resolve_backend("auto") == "subprocess"


def test_resolve_backend_auto_not_headless_picks_mailbox(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)
    from tools.llm.cli_bridge import capability

    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: False)
    assert cli_provider.resolve_backend("auto") == "mailbox"


def test_resolve_backend_explicit_override_wins(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)
    from tools.llm.cli_bridge import capability

    # Configured explicitly as mailbox even though the host IS headless-capable.
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    assert cli_provider.resolve_backend("mailbox") == "mailbox"
    assert cli_provider.resolve_backend("subprocess") == "subprocess"


def test_resolve_backend_env_overrides_config(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "mailbox")
    # Config says subprocess, but the env var takes precedence.
    assert cli_provider.resolve_backend("subprocess") == "mailbox"


def test_resolve_backend_env_auto_falls_through_to_capability(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "auto")
    from tools.llm.cli_bridge import capability

    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: False)
    assert cli_provider.resolve_backend("subprocess") == "mailbox"


def test_resolve_backend_unknown_value_resolves_via_capability(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)
    from tools.llm.cli_bridge import capability

    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    assert cli_provider.resolve_backend("garbage") == "subprocess"


def test_invoke_records_resolved_backend_on_row(job_db, request_obj, monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)
    from tools.llm.cli_bridge import capability

    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: False)

    captured = {}

    def completing_dispatcher(job_id, backend):
        captured["job"] = job_store.get_job(job_id)
        job_store.complete_job(job_id, "ok")

    # Provider configured 'auto'; host not headless → mailbox recorded on the row.
    provider = cli_provider.CLILLMProvider(
        backend="auto", soft_wait_seconds=5, dispatcher=completing_dispatcher, poll_interval=0.02
    )
    provider.invoke(request_obj, model_id="claude-cli", model_config={})
    assert captured["job"]["backend"] == "mailbox"


def test_invoke_mailbox_no_worker_still_enqueues_and_defers(job_db, request_obj, monkeypatch):
    # Mailbox selected, no worker module wired, no heartbeat → job enqueued + deferred.
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "mailbox")
    monkeypatch.delenv("ICDEV_CLI_MAILBOX_HEARTBEAT", raising=False)
    monkeypatch.setattr(cli_provider, "_resolve_backend_dispatcher", lambda backend: None)

    provider = _provider(dispatcher=None, soft_wait_seconds=0.2)
    with pytest.raises(CLIJobDeferred) as excinfo:
        provider.invoke(request_obj, model_id="claude-cli", model_config={})

    # The row was still written (enqueued) for an external worker to claim.
    row = job_store.get_job(excinfo.value.job_id)
    assert row is not None
    assert row["backend"] == "mailbox"
    assert row["status"] in ("pending", "running")


def test_resolve_backend_dispatcher_routes_to_subprocess_module():
    dispatch = cli_provider._resolve_backend_dispatcher("subprocess")
    from tools.llm.cli_bridge import subprocess_backend

    assert dispatch is subprocess_backend.dispatch


def test_resolve_backend_dispatcher_none_for_unwired_mailbox():
    # mailbox_backend module doesn't exist yet (uclb-job-05) → None, no raise.
    assert cli_provider._resolve_backend_dispatcher("mailbox") is None
    assert cli_provider._resolve_backend_dispatcher("bogus") is None


# ────────────────────────────────────────────────────────────────────────────
# CLIJobDeferred contract
# ────────────────────────────────────────────────────────────────────────────


def test_cli_job_deferred_is_llm_unavailable_subclass():
    # Non-chat callers that only catch LLMUnavailableError still degrade cleanly.
    assert issubclass(CLIJobDeferred, LLMUnavailableError)
    exc = CLIJobDeferred("still working", job_id="abc123", chain=["claude-cli"])
    assert isinstance(exc, LLMUnavailableError)
    assert exc.job_id == "abc123"
    assert exc.chain == ["claude-cli"]


# ────────────────────────────────────────────────────────────────────────────
# misc surface
# ────────────────────────────────────────────────────────────────────────────


def test_flatten_messages_combines_system_and_user():
    flat = cli_provider._flatten_messages(
        [{"role": "user", "content": "hello"}], system_prompt="sys"
    )
    assert "sys" in flat and "hello" in flat


def test_provider_name_is_cli():
    assert _provider().provider_name == "cli"


def test_check_availability_reflects_path_resolution():
    provider = _provider()
    real_which = cli_provider.shutil.which
    try:
        cli_provider.shutil.which = lambda name: "/usr/bin/claude"
        assert provider.check_availability("claude-cli") is True
        cli_provider.shutil.which = lambda name: None
        assert provider.check_availability("claude-cli") is False
    finally:
        cli_provider.shutil.which = real_which


def test_constructor_coerces_bad_soft_wait():
    p = cli_provider.CLILLMProvider(soft_wait_seconds="not-an-int")
    assert p._soft_wait_seconds == 60


def test_constructor_coerces_bad_poll_interval():
    p = cli_provider.CLILLMProvider(poll_interval="nope")
    assert p._poll_interval == cli_provider.DEFAULT_POLL_INTERVAL
