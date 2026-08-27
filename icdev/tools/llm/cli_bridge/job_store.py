# [TEMPLATE: CUI // SP-CTI]
"""Job store for the CLI LLM bridge — CRUD over ``cli_llm_jobs``.

When the synchronous CLI provider cannot satisfy a request within its soft
wait window, it defers the work by writing a row to ``cli_llm_jobs`` and a
background worker (subprocess or mailbox backend) picks it up. This module is
the *pure persistence layer* for that flow: it knows nothing about subprocesses
or routing, only how to create, claim, complete, fail, read, and wait on jobs.

Lifecycle of a job ``status``::

    pending --claim_job()--> running --complete_job()--> done
                                     \\--fail_job()-----> error

All access goes through :func:`tools.db.storage.get_connection`, so the rows are
RLS-aware: inside a Flask request the connection is scoped to the caller's
tenant/classification automatically; background workers run without a request
context and therefore see the full table.

The table is *mutable* (``status`` transitions) — it is intentionally NOT in
``APPEND_ONLY_TABLES``. Schema (created by the ``uclb-job-01`` migration)::

    id, function, prompt, system_prompt, model_id, backend, status,
    result, error, context_id, input_tokens, output_tokens,
    tenant_id, classification, created_at, updated_at, claimed_at, completed_at

Race safety: :func:`claim_job` uses a guarded ``UPDATE ... WHERE status='pending'``
so that when two workers target the same oldest row only one wins; the loser
gets ``None`` rather than an exception. Benign races never raise.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.job_store")

# Terminal statuses — a job in one of these will never change again.
TERMINAL_STATUSES = ("done", "error")

DEFAULT_CLASSIFICATION = "CUI // SP-CTI"

# Grace (seconds) added on top of the subprocess backend's hard ceiling before a
# still-``running`` job is treated as orphaned. Must exceed the ceiling so a job
# legitimately executing under its own timeout is never reaped early.
DEFAULT_STALE_GRACE_SECONDS = 300


def _now() -> str:
    """Return an ISO-8601 UTC timestamp string for the TEXT timestamp columns."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(cur, row) -> Optional[Dict[str, Any]]:
    """Normalize a cursor row to a plain dict across SQLite and PostgreSQL.

    SQLite returns tuples; PostgreSQL (via :class:`~tools.db.storage.DictRow`)
    returns a mapping. Column names come from ``cur.description`` so both shapes
    collapse to the same dict.
    """
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    if hasattr(row, "keys"):
        return {c: row[c] for c in cols}
    return dict(zip(cols, row))


def create_job(
    function: str,
    prompt: str,
    system_prompt: str = "",
    model_id: Optional[str] = None,
    backend: str = "auto",
    context_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    classification: str = DEFAULT_CLASSIFICATION,
) -> str:
    """Insert a new job in ``status='pending'`` and return its generated id."""
    job_id = uuid.uuid4().hex
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cli_llm_jobs (
                id, function, prompt, system_prompt, model_id, backend, status,
                context_id, tenant_id, classification, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                function,
                prompt,
                system_prompt or "",
                model_id,
                backend or "auto",
                context_id,
                tenant_id,
                classification or DEFAULT_CLASSIFICATION,
                now,
                now,
            ),
        )
    logger.debug("created cli_llm_job %s (function=%s backend=%s)", job_id, function, backend)
    return job_id


def claim_job(backend: str) -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest pending job for ``backend``; flip it to running.

    Returns the claimed job as a dict, or ``None`` when there is nothing to do —
    either no pending jobs exist or another worker won the race for the oldest
    one. Never raises on a benign race: the guarded ``UPDATE`` ensures exactly
    one claimer succeeds, and the loser simply gets ``None``.
    """
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id FROM cli_llm_jobs
                WHERE status = 'pending' AND backend = %s
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (backend,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            job_id = row[0] if not hasattr(row, "keys") else row["id"]

            now = _now()
            upd = conn.execute(
                """
                UPDATE cli_llm_jobs
                SET status = 'running', claimed_at = %s, updated_at = %s
                WHERE id = %s AND status = 'pending'
                """,
                (now, now, job_id),
            )
            # Lost the race — another worker claimed it between our SELECT and UPDATE.
            if getattr(upd, "rowcount", 0) != 1:
                logger.debug("claim_job race: %s already claimed", job_id)
                return None
    except Exception as exc:  # pragma: no cover - defensive; benign races never raise
        logger.debug("claim_job failed for backend=%s: %s", backend, exc)
        return None

    return get_job(job_id)


def complete_job(
    job_id: str,
    result: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> bool:
    """Mark a job ``done`` with its result and token counts. Returns success.

    cch-obs-04: the two cache counters are persisted alongside input/output. Claude Code
    reports them and the backend used to drop them, so ``ai_telemetry`` recorded 0 for every
    claude-cli call and the cache dashboard called the provider ``unreported`` — a statement
    about this pipeline rather than about the transport.

    ``input_tokens`` stays RAW. Anthropic's accounting is DISJOINT, so it already excludes
    both cache reads and writes and ``by_provider._split_tokens`` adds them back itself.
    """
    now = _now()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE cli_llm_jobs
                SET status = 'done', result = %s, input_tokens = %s, output_tokens = %s,
                    cache_read_input_tokens = %s, cache_creation_input_tokens = %s,
                    completed_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (result, input_tokens or 0, output_tokens or 0,
                 cache_read_input_tokens or 0, cache_creation_input_tokens or 0,
                 now, now, job_id),
            )
            return getattr(cur, "rowcount", 0) >= 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("complete_job failed for %s: %s", job_id, exc)
        return False


def fail_job(job_id: str, error: str) -> bool:
    """Mark a job ``error`` with its failure message. Returns success."""
    now = _now()
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE cli_llm_jobs
                SET status = 'error', error = %s, completed_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (error, now, now, job_id),
            )
            return getattr(cur, "rowcount", 0) >= 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("fail_job failed for %s: %s", job_id, exc)
        return False


def _stale_running_cutoff_seconds() -> int:
    """Age (seconds) past which a still-``running`` job is deemed orphaned.

    The subprocess backend runs the CLI in a daemon thread of the host process,
    bounded by its own hard ceiling (``ICDEV_CLI_BRIDGE_MAX_SECONDS``, default
    900s) — a worker that respects that ceiling fails its row at 900s. So the
    only way a row stays ``running`` past ceiling + grace is that the host
    process that owned the thread *died* (kill, OOM, reboot), taking the worker
    with it before it could call :func:`complete_job` / :func:`fail_job`.

    Grace is ``ICDEV_CLI_BRIDGE_STALE_GRACE_SECONDS`` (default 300s).
    """

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name)
        try:
            val = int(raw) if raw else default
        except (TypeError, ValueError):
            return default
        return val if val > 0 else default

    ceiling = _int_env("ICDEV_CLI_BRIDGE_MAX_SECONDS", 900)
    grace = _int_env("ICDEV_CLI_BRIDGE_STALE_GRACE_SECONDS", DEFAULT_STALE_GRACE_SECONDS)
    return ceiling + grace


def reap_stale_jobs(max_age_seconds: Optional[int] = None) -> int:
    """Transition orphaned ``running`` jobs to terminal ``error``; return the count.

    A job is claimed by a backend worker (``pending`` → ``running``) that runs in
    a daemon thread of the host process. If that host dies mid-flight the thread
    dies with it and the row is never moved to a terminal status — nothing else
    ever will, because the in-process subprocess ceiling only fires while the
    host lives. A caller polling such a row via :func:`wait_for_job` then sees
    ``running`` forever and defers indefinitely, stranding the requesting agent
    (the "premature stale status" failure mode). This reaper flips definitively
    orphaned rows to ``error`` so those callers fall through to their fallback.

    Only rows whose ``running`` age (measured from ``claimed_at``, falling back to
    ``created_at``) exceeds the cutoff are touched; a job still executing under
    its own ceiling is left alone. ``pending`` rows are never reaped — they were
    never claimed and remain enqueued for a worker. Best-effort: any DB error is
    swallowed and reported as ``0`` reaped so the reaper never blocks a caller.

    Args:
        max_age_seconds: Override the cutoff age. Defaults to the subprocess
            ceiling plus grace via :func:`_stale_running_cutoff_seconds`.

    Returns:
        Number of rows transitioned to ``error``.
    """
    cutoff_age = int(max_age_seconds) if max_age_seconds is not None else _stale_running_cutoff_seconds()
    if cutoff_age <= 0:
        return 0

    now = _now()
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(seconds=cutoff_age)).isoformat()
    reason = (
        f"reaped: worker host died — job stayed 'running' with no completion for "
        f"over {cutoff_age}s (orphaned CLI subprocess)"
    )
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE cli_llm_jobs
                SET status = 'error', error = %s, completed_at = %s, updated_at = %s
                WHERE status = 'running'
                  AND COALESCE(claimed_at, created_at) IS NOT NULL
                  AND COALESCE(claimed_at, created_at) < %s
                """,
                (reason, now, now, cutoff_ts),
            )
            reaped = getattr(cur, "rowcount", 0) or 0
    except Exception as exc:  # pragma: no cover - defensive; reaping is best-effort
        logger.debug("reap_stale_jobs failed: %s", exc)
        return 0

    if reaped:
        logger.info(
            "reap_stale_jobs: transitioned %d orphaned 'running' job(s) to error "
            "(cutoff=%ss)",
            reaped,
            cutoff_age,
        )
    return reaped


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Return the job row as a dict, or ``None`` if it does not exist."""
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM cli_llm_jobs WHERE id = %s",
                (job_id,),
            )
            return _row_to_dict(cur, cur.fetchone())
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("get_job failed for %s: %s", job_id, exc)
        return None


def wait_for_job(
    job_id: str,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
) -> Optional[Dict[str, Any]]:
    """Poll a job until it reaches a terminal status or ``timeout`` elapses.

    Returns the job row as a dict. If the job is still ``running``/``pending``
    when ``timeout`` is reached, the most recent row is returned anyway — the
    caller decides what to do with a not-yet-finished job (keep waiting, surface
    a "still working" placeholder, etc.). Returns ``None`` only if the job id is
    unknown.

    ``time`` is imported lazily so importing this module never pulls a clock in.
    """
    import time

    deadline = time.time() + max(0.0, float(timeout))
    interval = max(0.05, float(poll_interval))

    job = get_job(job_id)
    if job is None:
        return None

    while job.get("status") not in TERMINAL_STATUSES:
        if time.time() >= deadline:
            break
        time.sleep(interval)
        refreshed = get_job(job_id)
        if refreshed is None:
            # Row vanished mid-wait (e.g. test teardown) — return last known state.
            break
        job = refreshed

    return job


def list_jobs(
    status: Optional[str] = None,
    context_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return jobs, optionally filtered by ``status`` and/or ``context_id``.

    Convenience reader for workers and dashboards; ordered newest-first.
    """
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if context_id:
        clauses.append("context_id = ?")
        params.append(context_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM cli_llm_jobs {where} ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    try:
        with get_connection() as conn:
            cur = conn.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [
                ({c: r[c] for c in cols} if hasattr(r, "keys") else dict(zip(cols, r)))
                for r in cur.fetchall()
            ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("list_jobs failed: %s", exc)
        return []
