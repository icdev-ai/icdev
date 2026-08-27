# [TEMPLATE: CUI // SP-CTI]
"""Subprocess backend worker for the CLI LLM bridge.

When :class:`~tools.llm.cli_bridge.cli_provider.CLILLMProvider` cannot satisfy a
request within its soft-wait window it ``create_job(...)`` a row in
``cli_llm_jobs`` and calls ``dispatch(job_id, backend)``. This module is one
such backend: it runs the locally authenticated Claude CLI in a *daemon thread*
so the HTTP request that triggered it returns immediately, then writes the
answer (or failure) back to the job row via the job store.

Design — mirrors the background-thread + rate-limit pattern in
``tools/dashboard/api/batch.py``:

- **No hard kill.** Unlike the provider's short 60s soft-wait (which only
  *defers* the caller — it never kills anything), the subprocess is allowed to
  run to completion. A generous ceiling (``ICDEV_CLI_BRIDGE_MAX_SECONDS``,
  default 900s) bounds a truly hung CLI so a wedged process can't leak forever.
- **Bounded concurrency.** A module-level :class:`~threading.BoundedSemaphore`
  (``ICDEV_CLI_BRIDGE_MAX_CONCURRENT``, default 3) caps how many CLI subprocesses
  run at once; surplus jobs wait in their thread for a slot (the ``queued``
  phase) without blocking the dispatcher.
- **Progress.** Each job emits ``cli_synthesis`` progress events keyed by its
  ``job_id`` through :func:`tools.dashboard.sse_manager.emit_progress`, walking
  the phases ``queued`` → ``running`` → ``done`` (status ``completed`` /
  ``failed``). No-op when no SSE clients are connected.

``dispatch`` is the public entry point and is *non-blocking* and *never raises*:
a missing binary, a crashed CLI, or a duplicate dispatch all resolve to either a
failed job row or a silent skip, never an exception into the caller.

Env-var scope (auditable)
-------------------------
This module reads EXACTLY three env vars, all of which are documented
operational/routing overrides for the subprocess backend — not credentials:

* ``ICDEV_CLI_BRIDGE_MAX_SECONDS``  — hard ceiling (seconds) for a single CLI
  invocation; bounds a truly hung subprocess so it can't leak a thread.
* ``ICDEV_CLI_BRIDGE_MAX_CONCURRENT`` — max CLI subprocesses running
  concurrently across all dispatched jobs (sizes the BoundedSemaphore).
* ``ICDEV_CLI_BRIDGE_BINARY`` — name or absolute path of the Claude CLI
  binary the subprocess backend runs; defaults to ``"claude"``.

None of these are API keys, tokens, or other secrets. SIPA's ``env_secret``
sweep has previously mis-flagged ``ICDEV_*`` routing overrides as credential
reads (see e8a7daa40 — same false positive in
``cli_bridge/activate.py::CLOUD_KEY_ENV_VARS``, and b1a6f6215 — same fix
applied to ``cli_bridge/capability.py``); this block exists to make the
separation auditable and prevent recurrence. The allowlist is enforced by
``test_no_unauthorized_env_secret_reads`` in ``tests/llm/test_cli_backends.py``.
"""

import importlib
import json
import os
import shutil
import subprocess
import threading
from typing import Any, Dict, Optional, Union

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.subprocess_backend")

# --------------------------------------------------------------------------- #
# Configuration (all overridable via .env — never hard-code model/binary here)
# --------------------------------------------------------------------------- #

# Hard ceiling (seconds) for a single CLI invocation. NOT a soft-wait: this only
# fires for a genuinely wedged process so it can't leak a thread/subprocess.
DEFAULT_MAX_SECONDS = 900

# Max CLI subprocesses running concurrently across all dispatched jobs.
DEFAULT_MAX_CONCURRENT = 3

# Default binary name resolved from env (ICDEV_CLI_BRIDGE_BINARY).
# NOTE: ICDEV_CLI_BRIDGE_BINARY is a documented ICDEV_* routing override, NOT a
# credential. See module docstring "Env-var scope (auditable)".
CLI_BINARY_DEFAULT = "claude"


def _cli_binary() -> str:
    return os.environ.get("ICDEV_CLI_BRIDGE_BINARY") or CLI_BINARY_DEFAULT


def _max_seconds() -> int:
    """Resolve the per-job subprocess ceiling from the environment."""
    # NOTE: ICDEV_CLI_BRIDGE_MAX_SECONDS is a documented ICDEV_* routing
    # override (hard ceiling for a single CLI invocation), NOT a credential.
    # SIPA env_secret false positive has hit this site before — see module
    # docstring "Env-var scope (auditable)".
    raw = os.environ.get("ICDEV_CLI_BRIDGE_MAX_SECONDS")
    try:
        val = int(raw) if raw else DEFAULT_MAX_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_MAX_SECONDS
    return val if val > 0 else DEFAULT_MAX_SECONDS


def _max_concurrent() -> int:
    """Resolve the concurrency cap from the environment."""
    # NOTE: ICDEV_CLI_BRIDGE_MAX_CONCURRENT is a documented ICDEV_* routing
    # override (max concurrent CLI subprocesses), NOT a credential. SIPA
    # env_secret false positive has hit this site before — see module
    # docstring "Env-var scope (auditable)".
    raw = os.environ.get("ICDEV_CLI_BRIDGE_MAX_CONCURRENT")
    try:
        val = int(raw) if raw else DEFAULT_MAX_CONCURRENT
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT
    return val if val > 0 else DEFAULT_MAX_CONCURRENT


# --------------------------------------------------------------------------- #
# Concurrency + in-flight bookkeeping
# --------------------------------------------------------------------------- #

# BoundedSemaphore sized once at import; the cap is read from the environment at
# that time. Tests that need a different cap reset it via _reset_semaphore().
_semaphore = threading.BoundedSemaphore(_max_concurrent())

# Guards the in-flight set so a job dispatched twice (e.g. duplicate provider
# call) only ever spawns one worker.
_inflight_lock = threading.Lock()
_inflight: set = set()


def _reset_semaphore(max_concurrent: Optional[int] = None) -> None:
    """Rebuild the concurrency semaphore (test seam / env reload)."""
    global _semaphore
    _semaphore = threading.BoundedSemaphore(max_concurrent or _max_concurrent())


# --------------------------------------------------------------------------- #
# Progress helper
# --------------------------------------------------------------------------- #


def _emit_progress(**kwargs: Any) -> None:
    """Best-effort SSE progress emit — never raises."""
    try:
        from tools.dashboard.sse_manager import emit_progress
        emit_progress(**kwargs)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Job store import (importlib path to avoid _ToolsRedirect attribute chain,
# ensuring we get sys.modules["tools.llm.cli_bridge.job_store"] — the same
# object that tests patch via importlib.import_module("tools.llm.cli_bridge.job_store"))
# --------------------------------------------------------------------------- #


def _job_store():
    return importlib.import_module("tools.llm.cli_bridge.job_store")


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #


def _run_job(job_id: str) -> None:
    """Synchronous worker: run the claude CLI for *job_id* and persist results.

    Called from a daemon thread by :func:`dispatch`. May also be called
    directly (synchronously) by tests that want deterministic progress ordering.
    """
    js = _job_store()
    _emit_progress(phase="queued", operation_type="cli_synthesis", operation_id=job_id)

    job = js.get_job(job_id)
    if job is None:
        logger.debug("_run_job: job %s not found", job_id)
        return

    binary = _cli_binary()
    if not shutil.which(binary):
        js.fail_job(job_id, f"claude binary '{binary}' not found on PATH")
        _emit_progress(phase="done", status="error",
                       operation_type="cli_synthesis", operation_id=job_id)
        return

    # Duplicate-dispatch guard is in dispatch() via _inflight; we don't need a
    # DB-level claim here.  Emit running and proceed directly.
    _emit_progress(phase="running", operation_type="cli_synthesis", operation_id=job_id)

    prompt = job.get("prompt", "")
    cmd = [binary, "--output-format", "json"]

    # When images are referenced in the prompt, allow Claude Code to read them
    # via its built-in Read tool (dangerously-skip-permissions bypasses dialogs).
    if "[IMAGES FOR ANALYSIS]" in prompt:
        cmd += ["--dangerously-skip-permissions", "--allowedTools", "Read"]

    cmd += ["-p", prompt]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_max_seconds()
        )
        if result.returncode != 0:
            js.fail_job(job_id, f"claude exited {result.returncode}: {(result.stderr or '').strip()[:200]}")
            _emit_progress(phase="done", status="failed",
                           operation_type="cli_synthesis", operation_id=job_id)
            return

        stdout = (result.stdout or "").strip()
        try:
            data = json.loads(stdout)
            text_result = data.get("result", stdout)
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            # THE PROMPT-CACHE COUNTERS, which this parser used to drop on the floor
            # (cch-obs-04). Claude Code reports both; taking only input/output meant
            # `ai_telemetry.cache_read_input_tokens` was 0 for all 626 claude-cli calls,
            # and the cache dashboard classified the provider `unreported` — "the
            # transport reports no counters". The transport reports them fine.
            #
            # `input_tokens` above is left RAW. Anthropic's accounting is DISJOINT: it
            # already excludes both cache reads and writes, and
            # `by_provider._split_tokens` adds them back itself. Summing here and
            # declaring `disjoint` would count every cached token twice.
            cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
            cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
            if data.get("is_error"):
                js.fail_job(job_id, text_result)
                _emit_progress(phase="done", status="failed",
                               operation_type="cli_synthesis", operation_id=job_id)
                return
        except (json.JSONDecodeError, ValueError, TypeError):
            text_result = stdout
            input_tokens = output_tokens = 0
            cache_read_tokens = cache_creation_tokens = 0

        js.complete_job(job_id, text_result,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read_input_tokens=cache_read_tokens,
                        cache_creation_input_tokens=cache_creation_tokens)
        _emit_progress(phase="done", status="completed",
                       operation_type="cli_synthesis", operation_id=job_id)

    except subprocess.TimeoutExpired:
        js.fail_job(job_id, f"claude hit the {_max_seconds()}s ceiling")
        _emit_progress(phase="done", status="failed",
                       operation_type="cli_synthesis", operation_id=job_id)
    except Exception as exc:
        js.fail_job(job_id, str(exc))
        _emit_progress(phase="done", status="failed",
                       operation_type="cli_synthesis", operation_id=job_id)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def dispatch(job: Union[str, Dict[str, Any]], backend: str = "subprocess") -> None:
    """Dispatch *job* asynchronously via the subprocess backend.

    Non-blocking: spawns a daemon thread and returns immediately. The thread
    acquires the concurrency semaphore, runs the claude CLI, and persists the
    result to the job store. Duplicate dispatches (same job_id) are silently
    deduplicated via the in-flight set.
    """
    job_id = job.get("id") if isinstance(job, dict) else job
    if not job_id:
        logger.debug("subprocess backend: dispatch called with no job id")
        return

    with _inflight_lock:
        if job_id in _inflight:
            logger.debug("subprocess backend: job %s already in-flight, skipping", job_id)
            return
        _inflight.add(job_id)

    def _worker():
        with _semaphore:
            try:
                _run_job(job_id)
            except Exception as exc:
                logger.exception("subprocess backend: unhandled error for job %s: %s", job_id, exc)
            finally:
                with _inflight_lock:
                    _inflight.discard(job_id)

    t = threading.Thread(target=_worker, name=f"cli-bridge-{job_id}", daemon=True)
    t.start()
