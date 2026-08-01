# CUI // SP-CTI
"""Integration tests for the CLI LLM bridge backends + dynamic selection.

Where ``test_cli_subprocess_backend.py`` drives the subprocess worker against an
*in-memory fake* store (isolating the worker logic), this module exercises the
backends *end-to-end against the real job store* (a throwaway SQLite
``cli_llm_jobs`` table) so the create → claim/run → complete/fail lifecycle is
validated through the actual persistence layer.

Three slices, matching the uclb-job-07 task:

  1. **subprocess backend** — a mocked ``claude`` invocation completes a real job
     row and walks the queued → running → done progress phases.
  2. **mailbox backend** — there is no in-process worker (the mailbox worker is
     external, uclb-job-05); a *simulated worker* claims and completes/fails the
     row via the job store, proving the claim/complete contract a real worker
     relies on.
  3. **dynamic selection** — ``resolve_backend`` picks ``subprocess`` when the
     host is headless-capable and ``mailbox`` otherwise (capability monkeypatched).

Shim-aware monkeypatch rule applies throughout: ``tools.*`` and ``icdev.tools.*``
are distinct module objects, so we ``setattr`` on the imported module object.
"""
from __future__ import annotations

import importlib
import pathlib
import sqlite3
import subprocess
import sys
import threading
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

job_store = importlib.import_module("tools.llm.cli_bridge.job_store")
subprocess_backend = importlib.import_module("tools.llm.cli_bridge.subprocess_backend")
cli_provider = importlib.import_module("tools.llm.cli_bridge.cli_provider")
capability = importlib.import_module("tools.llm.cli_bridge.capability")


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
    """Real SQLite cli_llm_jobs wired into job_store.get_connection.

    The subprocess backend imports ``job_store`` and calls its get/complete/fail
    helpers, so rebinding ``get_connection`` here makes the whole backend run
    against this throwaway DB.
    """
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

    monkeypatch.setattr(job_store, "get_connection", fake_get_connection)
    return db_path


@pytest.fixture
def captured_progress(monkeypatch):
    """Capture emit_progress calls the subprocess backend makes."""
    events: list = []

    def fake_emit(*args, **kwargs):
        events.append(kwargs or args)

    sse = importlib.import_module("tools.dashboard.sse_manager")
    monkeypatch.setattr(sse, "emit_progress", fake_emit)
    return events


@pytest.fixture(autouse=True)
def _binary_on_path(monkeypatch):
    """Pretend the claude binary resolves so the subprocess not-found guard passes."""
    monkeypatch.setattr(subprocess_backend.shutil, "which", lambda _b: "/usr/bin/claude")


def _stub_run(stdout="", returncode=0, exc=None):
    def _run(cmd, **kwargs):
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return _run


def _wait_terminal(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = job_store.get_job(job_id)
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(0.01)
    return job_store.get_job(job_id)


# ════════════════════════════════════════════════════════════════════════════
# 1. Subprocess backend — end-to-end against the real job store
# ════════════════════════════════════════════════════════════════════════════


def test_subprocess_completes_mocked_claude_job(job_db, monkeypatch):
    job_id = job_store.create_job(function="f", prompt="say hi", backend="subprocess")
    payload = (
        '{"result": "hello there", "is_error": false, '
        '"usage": {"input_tokens": 7, "output_tokens": 11}}'
    )
    monkeypatch.setattr(subprocess_backend.subprocess, "run", _stub_run(stdout=payload))

    subprocess_backend.dispatch(job_id, "subprocess")
    row = _wait_terminal(job_id)

    assert row["status"] == "done"
    assert row["result"] == "hello there"
    assert row["input_tokens"] == 7
    assert row["output_tokens"] == 11
    assert row["completed_at"] is not None


def test_subprocess_emits_queued_running_done_progress(job_db, monkeypatch, captured_progress):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(
        subprocess_backend.subprocess,
        "run",
        _stub_run(stdout='{"result": "ok", "is_error": false, "usage": {}}'),
    )

    # Run synchronously so progress is fully captured without a thread join race.
    subprocess_backend._run_job(job_id)

    phases = [e.get("phase") for e in captured_progress]
    assert phases == ["queued", "running", "done"]
    assert all(e.get("operation_type") == "cli_synthesis" for e in captured_progress)
    assert all(e.get("operation_id") == job_id for e in captured_progress)
    assert captured_progress[-1].get("status") == "completed"


def test_subprocess_nonzero_returncode_fails_job(job_db, monkeypatch):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(subprocess_backend.subprocess, "run", _stub_run(returncode=1))

    subprocess_backend._run_job(job_id)

    row = job_store.get_job(job_id)
    assert row["status"] == "error"
    assert "exited 1" in row["error"]


def test_subprocess_non_json_stdout_is_best_effort(job_db, monkeypatch):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(
        subprocess_backend.subprocess, "run", _stub_run(stdout="plain text answer")
    )

    subprocess_backend._run_job(job_id)

    row = job_store.get_job(job_id)
    assert row["status"] == "done"
    assert row["result"] == "plain text answer"


def test_subprocess_dispatch_is_threaded_and_completes(job_db, monkeypatch):
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(
        subprocess_backend.subprocess,
        "run",
        _stub_run(stdout='{"result": "threaded", "is_error": false, "usage": {}}'),
    )

    subprocess_backend.dispatch(job_id)  # non-blocking; runs in a daemon thread
    row = _wait_terminal(job_id)
    assert row["status"] == "done"
    assert row["result"] == "threaded"


# ════════════════════════════════════════════════════════════════════════════
# 2. Mailbox backend — simulated external worker via the job store
# ════════════════════════════════════════════════════════════════════════════


def _simulated_mailbox_worker(answer="mailbox answer", fail_with=None, claims=1):
    """Run one pass of a mailbox worker: claim pending mailbox jobs and resolve.

    Mirrors what the external (uclb-job-05) worker does — it never touches a
    subprocess, only the job store: ``claim_job('mailbox')`` then
    ``complete_job`` / ``fail_job``. Returns the list of job ids it handled.
    """
    handled = []
    for _ in range(claims):
        job = job_store.claim_job("mailbox")
        if job is None:
            break
        if fail_with is not None:
            job_store.fail_job(job["id"], fail_with)
        else:
            job_store.complete_job(job["id"], answer, input_tokens=2, output_tokens=4)
        handled.append(job["id"])
    return handled


def test_mailbox_simulated_worker_claim_complete(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="mailbox")

    handled = _simulated_mailbox_worker(answer="mailbox answer")

    assert handled == [job_id]
    row = job_store.get_job(job_id)
    assert row["status"] == "done"
    assert row["result"] == "mailbox answer"
    assert row["input_tokens"] == 2
    assert row["output_tokens"] == 4
    assert row["claimed_at"] is not None  # claim_job stamped it
    assert row["completed_at"] is not None


def test_mailbox_simulated_worker_claim_fail(job_db):
    job_id = job_store.create_job(function="f", prompt="p", backend="mailbox")

    handled = _simulated_mailbox_worker(fail_with="worker could not authenticate")

    assert handled == [job_id]
    row = job_store.get_job(job_id)
    assert row["status"] == "error"
    assert "authenticate" in row["error"]


def test_mailbox_worker_ignores_subprocess_jobs(job_db):
    # A subprocess-targeted job must not be claimed by the mailbox worker.
    sub_id = job_store.create_job(function="f", prompt="p", backend="subprocess")

    handled = _simulated_mailbox_worker()

    assert handled == []
    assert job_store.get_job(sub_id)["status"] == "pending"


def test_mailbox_worker_in_background_thread(job_db):
    """A realistic async worker: enqueue, then a thread drains the mailbox."""
    job_id = job_store.create_job(function="f", prompt="p", backend="mailbox")

    def worker_loop():
        # Poll until the row appears claimable, then resolve it.
        deadline = time.time() + 3
        while time.time() < deadline:
            if _simulated_mailbox_worker(answer="async mailbox"):
                return
            time.sleep(0.01)

    t = threading.Thread(target=worker_loop)
    t.start()
    try:
        row = job_store.wait_for_job(job_id, timeout=5.0, poll_interval=0.02)
    finally:
        t.join(timeout=5)

    assert row["status"] == "done"
    assert row["result"] == "async mailbox"


# ════════════════════════════════════════════════════════════════════════════
# 3. Dynamic backend selection — subprocess when capable, else mailbox
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_backend_env(monkeypatch):
    """Selection tests must see no env override leaking in."""
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BACKEND", raising=False)


def test_auto_selects_subprocess_when_headless_capable(monkeypatch):
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    assert cli_provider.resolve_backend("auto") == "subprocess"


def test_auto_selects_mailbox_when_not_headless_capable(monkeypatch):
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: False)
    assert cli_provider.resolve_backend("auto") == "mailbox"


def test_unknown_backend_resolves_via_capability(monkeypatch):
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: False)
    assert cli_provider.resolve_backend("garbage") == "mailbox"


def test_explicit_backend_overrides_capability(monkeypatch):
    # Even on a headless-capable host, an explicit mailbox config is honored.
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    assert cli_provider.resolve_backend("mailbox") == "mailbox"
    assert cli_provider.resolve_backend("subprocess") == "subprocess"


def test_env_override_beats_config(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "mailbox")
    # Config asks for subprocess but the env var wins.
    assert cli_provider.resolve_backend("subprocess") == "mailbox"


def test_env_auto_falls_through_to_capability(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BACKEND", "auto")
    monkeypatch.setattr(capability, "is_cli_headless_capable", lambda *a, **k: True)
    # Even with an explicit config, env 'auto' re-routes through capability.
    assert cli_provider.resolve_backend("mailbox") == "subprocess"


# --- capability probe itself (the input to dynamic selection) ---------------


def test_capability_env_override_forces_true(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_HEADLESS", "true")
    assert capability.is_cli_headless_capable("definitely-not-a-real-binary") is True


def test_capability_env_override_forces_false(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_HEADLESS", "false")
    monkeypatch.setattr(capability.shutil, "which", lambda _b: "/usr/bin/claude")
    assert capability.is_cli_headless_capable() is False


def test_capability_uses_path_lookup_when_no_override(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_HEADLESS", raising=False)
    monkeypatch.setattr(capability.shutil, "which", lambda _b: "/usr/bin/claude")
    assert capability.is_cli_headless_capable("claude") is True
    monkeypatch.setattr(capability.shutil, "which", lambda _b: None)
    assert capability.is_cli_headless_capable("claude") is False


def test_mailbox_worker_alive_false_without_heartbeat(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_MAILBOX_HEARTBEAT", raising=False)
    assert capability.mailbox_worker_alive() is False


def test_mailbox_worker_alive_true_for_fresh_heartbeat(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv(
        "ICDEV_CLI_MAILBOX_HEARTBEAT", datetime.now(timezone.utc).isoformat()
    )
    assert capability.mailbox_worker_alive(stale_seconds=90) is True


def test_mailbox_worker_alive_false_for_stale_heartbeat(monkeypatch):
    from datetime import datetime, timedelta, timezone

    stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    monkeypatch.setenv("ICDEV_CLI_MAILBOX_HEARTBEAT", stale)
    assert capability.mailbox_worker_alive(stale_seconds=90) is False


# ── env-var scope (SIPA env_secret false-positive guard) ───────────────────
#
# SIPA's env_secret sweep has previously mis-flagged the ICDEV_* routing
# overrides in the subprocess backend as credential reads (e8a7daa40 — same
# false positive in cli_bridge/activate.py, b1a6f6215 — same fix applied to
# cli_bridge/capability.py). Lock in the exact allowlist so the scope stays
# auditable and any future addition breaks this test until a scoping note +
# companion docstring update is added.
SUBPROCESS_BACKEND_ALLOWED_ENV_VARS = frozenset(
    {
        "ICDEV_CLI_BRIDGE_MAX_SECONDS",
        "ICDEV_CLI_BRIDGE_MAX_CONCURRENT",
        "ICDEV_CLI_BRIDGE_BINARY",
    }
)


def test_no_unauthorized_env_secret_reads():
    """The subprocess backend reads only documented ICDEV_* routing overrides.

    Any ``os.environ.get`` of a var not in SUBPROCESS_BACKEND_ALLOWED_ENV_VARS
    is treated as an unauthorized credential read. Update
    SUBPROCESS_BACKEND_ALLOWED_ENV_VARS + the module docstring together when
    adding a new var.
    """
    import ast

    src = pathlib.Path(subprocess_backend.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    reads: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match os.environ.get("FOO") and os.environ.get("FOO", default)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                reads.add(node.args[0].value)

    unauthorized = reads - SUBPROCESS_BACKEND_ALLOWED_ENV_VARS
    assert not unauthorized, (
        f"Unauthorized env-var reads detected in subprocess_backend.py: "
        f"{sorted(unauthorized)}. Add the var to "
        f"SUBPROCESS_BACKEND_ALLOWED_ENV_VARS in test_cli_backends.py AND "
        f"update the env-var scope block in the module docstring."
    )
