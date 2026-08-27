# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/subprocess_backend.py.

The subprocess backend runs the Claude CLI in a daemon thread and writes the
answer back to the ``cli_llm_jobs`` row via the job store. These tests never
spawn a real ``claude`` process: ``subprocess.run`` is replaced with a stub on
the imported module object, and the job store's get/complete/fail functions are
rebound to an in-memory fake (shim-aware: ``tools.*`` and ``icdev.tools.*`` are
distinct module objects, so we ``setattr`` the imported object rather than use
pytest's string form).

Covers (uclb-job-04):
  - happy path: parse JSON → complete_job with result + token counts
  - non-zero return code → fail_job
  - CLI timeout (ceiling) → fail_job
  - missing binary → fail_job
  - non-JSON stdout → best-effort result text
  - is_error JSON → fail_job
  - emit_progress walks queued → running → done
  - bounded concurrency: never more than the cap run at once
  - duplicate dispatch of an in-flight id is a no-op
"""
from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
import threading
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

backend = importlib.import_module("tools.llm.cli_bridge.subprocess_backend")
job_store = importlib.import_module("tools.llm.cli_bridge.job_store")


class _FakeStore:
    """Minimal in-memory stand-in for the job store used by the backend."""

    def __init__(self):
        self.jobs: dict = {}
        self.lock = threading.Lock()

    def add(self, job_id: str, prompt: str = "hi"):
        self.jobs[job_id] = {"id": job_id, "prompt": prompt, "status": "pending"}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def complete_job(
        self,
        job_id,
        result,
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    ):
        # cch-obs-04: the signature tracks the real job_store. A fake that lags it turns a
        # kwargs mismatch into a TypeError inside the worker thread, which surfaces as
        # "the job never completed" rather than as the signature drift it is.
        with self.lock:
            row = self.jobs.get(job_id)
            if row is None:
                return False
            row.update(
                status="done",
                result=result,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
            )
            return True

    def fail_job(self, job_id, error):
        with self.lock:
            row = self.jobs.get(job_id)
            if row is None:
                return False
            row.update(status="error", error=error)
            return True


@pytest.fixture
def store(monkeypatch):
    """Rebind the backend's job-store calls to an in-memory fake."""
    fake = _FakeStore()
    # Shim-aware: patch the imported module object, not a pytest string path.
    monkeypatch.setattr(job_store, "get_job", fake.get_job)
    monkeypatch.setattr(job_store, "complete_job", fake.complete_job)
    monkeypatch.setattr(job_store, "fail_job", fake.fail_job)
    return fake


@pytest.fixture
def captured_progress(monkeypatch):
    """Capture emit_progress calls made by the backend."""
    events = []

    def fake_emit(*args, **kwargs):
        events.append(kwargs or args)

    # _emit imports emit_progress lazily from sse_manager; patch there.
    sse = importlib.import_module("tools.dashboard.sse_manager")
    monkeypatch.setattr(sse, "emit_progress", fake_emit)
    return events


@pytest.fixture(autouse=True)
def _binary_on_path(monkeypatch):
    """Pretend the claude binary resolves so the not-found guard passes."""
    monkeypatch.setattr(backend.shutil, "which", lambda _b: "/usr/bin/claude")


def _stub_run(stdout="", returncode=0, exc=None):
    def _run(cmd, **kwargs):
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return _run


# ────────────────────────────────────────────────────────────────────────────
# _run_job — core synchronous logic (no thread)
# ────────────────────────────────────────────────────────────────────────────


def test_run_job_completes_with_parsed_json(store, monkeypatch):
    store.add("j1", prompt="say hi")
    payload = (
        '{"result": "hello there", "is_error": false, '
        '"usage": {"input_tokens": 7, "output_tokens": 11}}'
    )
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout=payload))

    backend._run_job("j1")

    row = store.jobs["j1"]
    assert row["status"] == "done"
    assert row["result"] == "hello there"
    assert row["input_tokens"] == 7
    assert row["output_tokens"] == 11


def test_run_job_nonzero_returncode_fails(store, monkeypatch):
    store.add("j2")
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(returncode=1))

    backend._run_job("j2")

    assert store.jobs["j2"]["status"] == "error"
    assert "exited 1" in store.jobs["j2"]["error"]


def test_run_job_timeout_fails(store, monkeypatch):
    store.add("j3")
    exc = subprocess.TimeoutExpired(cmd="claude", timeout=900)
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(exc=exc))

    backend._run_job("j3")

    assert store.jobs["j3"]["status"] == "error"
    assert "ceiling" in store.jobs["j3"]["error"]


def test_run_job_missing_binary_fails(store, monkeypatch):
    store.add("j4")
    monkeypatch.setattr(backend.shutil, "which", lambda _b: None)

    backend._run_job("j4")

    assert store.jobs["j4"]["status"] == "error"
    assert "not found" in store.jobs["j4"]["error"]


def test_run_job_non_json_stdout_is_best_effort(store, monkeypatch):
    store.add("j5")
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout="plain text answer"))

    backend._run_job("j5")

    assert store.jobs["j5"]["status"] == "done"
    assert store.jobs["j5"]["result"] == "plain text answer"


def test_run_job_is_error_json_fails(store, monkeypatch):
    store.add("j6")
    payload = '{"result": "boom happened", "is_error": true}'
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout=payload))

    backend._run_job("j6")

    assert store.jobs["j6"]["status"] == "error"
    assert "boom happened" in store.jobs["j6"]["error"]


def test_run_job_missing_job_is_noop(store, monkeypatch):
    # No row added — get_job returns None; must not raise.
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout="x"))
    backend._run_job("ghost")  # should simply return


# ────────────────────────────────────────────────────────────────────────────
# Progress phases
# ────────────────────────────────────────────────────────────────────────────


def test_emit_walks_queued_running_done(store, monkeypatch, captured_progress):
    store.add("j7")
    payload = '{"result": "ok", "is_error": false, "usage": {}}'
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout=payload))

    backend._run_job("j7")

    phases = [e.get("phase") for e in captured_progress]
    assert phases == ["queued", "running", "done"]
    # operation_type and operation_id are stable across the run.
    assert all(e.get("operation_type") == "cli_synthesis" for e in captured_progress)
    assert all(e.get("operation_id") == "j7" for e in captured_progress)
    # Final event reports completed status.
    assert captured_progress[-1].get("status") == "completed"


def test_emit_final_failed_on_error(store, monkeypatch, captured_progress):
    store.add("j8")
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(returncode=2))

    backend._run_job("j8")

    assert captured_progress[-1].get("phase") == "done"
    assert captured_progress[-1].get("status") == "failed"


# ────────────────────────────────────────────────────────────────────────────
# dispatch — threading, concurrency, dedup
# ────────────────────────────────────────────────────────────────────────────


def _wait_terminal(store, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = store.jobs.get(job_id)
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(0.01)
    return store.jobs.get(job_id)


def test_dispatch_runs_in_thread_and_completes(store, monkeypatch):
    store.add("d1")
    payload = '{"result": "threaded", "is_error": false, "usage": {}}'
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout=payload))

    backend.dispatch("d1", "subprocess")
    row = _wait_terminal(store, "d1")
    assert row["status"] == "done"
    assert row["result"] == "threaded"


def test_dispatch_accepts_job_dict(store, monkeypatch):
    store.add("d2")
    monkeypatch.setattr(backend.subprocess, "run", _stub_run(stdout='{"result":"ok"}'))

    backend.dispatch({"id": "d2"})
    row = _wait_terminal(store, "d2")
    assert row["status"] == "done"


def test_dispatch_empty_job_id_is_noop(store):
    # No exception, no thread.
    backend.dispatch("")
    backend.dispatch({})


def test_dispatch_dedup_inflight(store, monkeypatch):
    # A long-running job: hold subprocess.run on a gate so the id stays in flight.
    store.add("d3")
    gate = threading.Event()
    started = threading.Event()
    calls = {"n": 0}

    def gated_run(cmd, **kwargs):
        calls["n"] += 1
        started.set()
        gate.wait(timeout=3)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result":"done"}', stderr="")

    monkeypatch.setattr(backend.subprocess, "run", gated_run)

    backend.dispatch("d3")
    assert started.wait(timeout=2)  # first worker is running
    backend.dispatch("d3")  # duplicate while in flight — must be skipped
    gate.set()
    _wait_terminal(store, "d3")

    assert calls["n"] == 1  # only one subprocess ever ran


def test_bounded_concurrency_caps_simultaneous(store, monkeypatch):
    # Cap the semaphore at 2 and launch 4; never more than 2 run concurrently.
    backend._reset_semaphore(2)
    try:
        gate = threading.Event()
        live = {"now": 0, "peak": 0}
        live_lock = threading.Lock()

        def gated_run(cmd, **kwargs):
            with live_lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            gate.wait(timeout=3)
            with live_lock:
                live["now"] -= 1
            return subprocess.CompletedProcess(cmd, 0, stdout='{"result":"x"}', stderr="")

        monkeypatch.setattr(backend.subprocess, "run", gated_run)

        ids = [f"c{i}" for i in range(4)]
        for jid in ids:
            store.add(jid)
            backend.dispatch(jid)

        # Let the first wave grab slots, then release everyone.
        time.sleep(0.3)
        peak_before_release = live["peak"]
        gate.set()
        for jid in ids:
            _wait_terminal(store, jid)

        assert peak_before_release <= 2
        assert all(store.jobs[j]["status"] == "done" for j in ids)
    finally:
        backend._reset_semaphore()
