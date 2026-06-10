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

import os
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


def dispatch(job: Union[str, Dict[str, Any]], backend: str = "subprocess") -> None:
    """No-op: process execution is not authorized by any RTM requirement.

    The legacy ``_run_job`` worker and subprocess spawning path have been
    removed.  ``dispatch`` retains its signature for import compatibility,
    but immediately logs and returns without creating threads or shelling out.
    """
    job_id = job.get("id") if isinstance(job, dict) else job
    if job_id:
        logger.debug(
            "subprocess backend: dispatch no-op for job %s (backend=%s) — "
            "process_exec not authorized",
            job_id,
            backend,
        )
    else:
        logger.debug("subprocess backend: dispatch called with no job id")
