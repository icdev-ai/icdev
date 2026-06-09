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

import json
import os
import shutil
import subprocess
import threading
from typing import Any, Dict, Optional, Union

from tools.llm.cli_bridge import job_store
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

# Progress operation_type for SSE consumers (dashboard progress widget).
OPERATION_TYPE = "cli_synthesis"

# Three logical phases per job, used for the progress percent denominator.
_TOTAL_PHASES = 3


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


def _cli_binary() -> str:
    """Resolve the Claude CLI binary name/path (``ICDEV_CLI_BRIDGE_BINARY``)."""
    # NOTE: ICDEV_CLI_BRIDGE_BINARY is a documented ICDEV_* routing override
    # (binary name/path the subprocess backend runs), NOT a credential. SIPA
    # env_secret false positive has hit this site before — see module
    # docstring "Env-var scope (auditable)".
    return os.environ.get("ICDEV_CLI_BRIDGE_BINARY") or "claude"


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


def _emit(job_id: str, phase: str, completed: int, status: str, detail: str = None) -> None:
    """Emit a ``cli_synthesis`` progress event; never raise on SSE failure."""
    try:
        from tools.dashboard.sse_manager import emit_progress

        emit_progress(
            operation_id=job_id,
            operation_type=OPERATION_TYPE,
            phase=phase,
            completed=completed,
            total=_TOTAL_PHASES,
            status=status,
            detail=detail,
        )
    except Exception as exc:  # pragma: no cover - SSE is best-effort observability
        logger.debug("emit_progress failed for job %s (%s): %s", job_id, phase, exc)


def _parse_cli_json(stdout: str) -> Dict[str, Any]:
    """Parse ``claude --output-format json`` stdout into result + token counts.

    The CLI emits a single JSON object: ``{"result": "...", "is_error": bool,
    "usage": {"input_tokens": N, "output_tokens": N}, ...}``. We tolerate drift:
    a parse failure falls back to treating the raw stdout as the answer text so a
    non-empty response is never silently dropped.
    """
    text = (stdout or "").strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # Not JSON — best-effort: surface whatever the CLI printed as the result.
        return {"result": text, "is_error": False, "input_tokens": 0, "output_tokens": 0}

    if not isinstance(data, dict):
        return {"result": text, "is_error": False, "input_tokens": 0, "output_tokens": 0}

    usage = data.get("usage") or {}
    return {
        "result": str(data.get("result", "") or ""),
        "is_error": bool(data.get("is_error", False)),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _run_job(job_id: str) -> None:
    """Worker body: wait for a slot, run the CLI, write the result back.

    Runs inside a daemon thread. Acquires the concurrency semaphore (the
    ``queued`` phase), shells out to the Claude CLI bounded by the max-seconds
    ceiling (the ``running`` phase), then completes or fails the job row (the
    ``done`` phase). Always releases the slot and clears the in-flight marker.
    """
    acquired = False
    try:
        job = job_store.get_job(job_id)
        if job is None:
            logger.debug("subprocess backend: job %s vanished before run", job_id)
            return

        # ---- queued: waiting for a concurrency slot -------------------------
        _emit(job_id, "queued", 0, "running", detail="waiting for CLI slot")
        _semaphore.acquire()
        acquired = True

        prompt = job.get("prompt") or ""
        binary = _cli_binary()
        if shutil.which(binary) is None and not os.path.isabs(binary):
            msg = f"Claude CLI binary not found on PATH: {binary}"
            logger.warning("subprocess backend: %s (job %s)", msg, job_id)
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg)
            return

        cmd = [binary, "-p", prompt, "--output-format", "json"]
        ceiling = _max_seconds()

        # ---- running: CLI subprocess in flight (no hard kill < ceiling) -----
        _emit(job_id, "running", 1, "running", detail="invoking Claude CLI")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=ceiling,
            )
        except subprocess.TimeoutExpired:
            msg = f"Claude CLI exceeded ceiling of {ceiling}s"
            logger.warning("subprocess backend: job %s timed out (%ss)", job_id, ceiling)
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg)
            return
        except FileNotFoundError:
            msg = f"Claude CLI binary not found: {binary}"
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg)
            return
        except Exception as exc:  # pragma: no cover - defensive
            msg = f"Claude CLI invocation error: {exc}"
            logger.warning("subprocess backend: job %s errored: %s", job_id, exc)
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg)
            return

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            msg = f"Claude CLI exited {proc.returncode}: {stderr[:500]}"
            logger.warning("subprocess backend: job %s failed (%s)", job_id, msg)
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg[:200])
            return

        parsed = _parse_cli_json(proc.stdout)
        if parsed["is_error"]:
            msg = f"Claude CLI reported error: {parsed['result'][:500]}"
            job_store.fail_job(job_id, msg)
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=msg[:200])
            return

        # ---- done: persist the answer + token counts ------------------------
        job_store.complete_job(
            job_id,
            parsed["result"],
            input_tokens=parsed["input_tokens"],
            output_tokens=parsed["output_tokens"],
        )
        _emit(job_id, "done", _TOTAL_PHASES, "completed", detail="answer cached")
        logger.debug("subprocess backend: job %s done (out_tokens=%s)", job_id, parsed["output_tokens"])

    except Exception as exc:  # pragma: no cover - last-resort safety net
        logger.warning("subprocess backend: unhandled error for job %s: %s", job_id, exc)
        try:
            job_store.fail_job(job_id, f"subprocess backend error: {exc}")
            _emit(job_id, "done", _TOTAL_PHASES, "failed", detail=str(exc)[:200])
        except Exception:
            pass
    finally:
        if acquired:
            try:
                _semaphore.release()
            except ValueError:  # pragma: no cover - release without acquire
                pass
        with _inflight_lock:
            _inflight.discard(job_id)


def dispatch(job: Union[str, Dict[str, Any]], backend: str = "subprocess") -> None:
    """Hand a pending job to a daemon worker thread. Non-blocking; never raises.

    Args:
        job: A ``cli_llm_jobs`` job id, or a job dict carrying an ``id`` key.
            (The provider calls ``dispatch(job_id, backend)``; accepting a dict
            too keeps the ``dispatch(job)`` ergonomics noted in the task.)
        backend: The backend label that claimed the job; accepted for signature
            parity with the provider's dispatcher contract (unused here — this
            module *is* the subprocess backend).

    A duplicate dispatch of the same id while one is already in flight is a
    silent no-op so a row is never run twice.
    """
    job_id = job.get("id") if isinstance(job, dict) else job
    if not job_id:
        logger.debug("subprocess backend: dispatch called with no job id")
        return

    with _inflight_lock:
        if job_id in _inflight:
            logger.debug("subprocess backend: job %s already in flight; skipping", job_id)
            return
        _inflight.add(job_id)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        name=f"cli-bridge-subprocess-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    logger.debug("subprocess backend: dispatched job %s (backend=%s)", job_id, backend)
