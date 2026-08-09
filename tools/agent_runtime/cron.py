# CUI // SP-CTI
"""User-facing cron for the standalone agent (sag-cron-01).

A thin, durable scheduling layer on top of ICDEV's existing daemon/reflex
infrastructure. The scheduling *engine* already exists — ``tools/daemon/base.py``
drives schedule-based reflexes and the Genesis daemon ticks them — so this module
does NOT add a new loop. It adds:

- a **durable job store** (``agent_cron_jobs`` + append-only ``agent_cron_runs``,
  migration 289, self-created via :func:`_ensure_schema` so a checkout that has
  not migrated still works);
- a user CLI: ``icdev cron create|list|pause|resume|remove|run``;
- **two execution modes** — ``agent`` (run a prompt single-shot through the SAG
  :class:`~tools.agent_runtime.runtime.AgentRuntime`) and ``script`` (an
  allowlisted ``python tools/...`` command, no LLM — reusing the
  ``tools/skills/invoke.py`` allowlist);
- **retry with exponential backoff**; and
- **delivery** of the result (``log`` — the run row itself; ``email`` — via the
  existing SMTP adapter, degrading to an on-file outbox; ``gateway:<ch>:<chat>``
  — best-effort outbound through a gateway adapter).

The actual ticking is done by the ``agent_cron`` Genesis reflex
(``tools/genesis/reflexes/agent_cron_reflex.py``), which calls
:func:`run_due_jobs`. Everything degrades gracefully: a missing table, an
unavailable LLM, or an unconfigured delivery target yields a recorded failure,
never an exception that wedges the daemon.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.cron")

_JOBS_TABLE = "agent_cron_jobs"
_RUNS_TABLE = "agent_cron_runs"
_DEFAULT_USER = "default"
_MAX_OUTPUT = 8000
_MAX_CRON_SCAN_MINUTES = 366 * 24 * 60  # bound the next-fire search (~1 year)

MODES = ("agent", "script")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Schedule parsing (pure-Python, no deps)
# ---------------------------------------------------------------------------
_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(text: str) -> int:
    """Parse an interval like ``15m`` / ``2h`` / ``90s`` / ``1d`` into seconds.

    A bare integer is treated as seconds. Raises ``ValueError`` on garbage.
    """
    t = str(text).strip().lower()
    if not t:
        raise ValueError("empty interval")
    if t.isdigit():
        return int(t)
    unit = t[-1]
    if unit not in _INTERVAL_UNITS or not t[:-1].isdigit():
        raise ValueError(f"invalid interval {text!r} (use e.g. 30s, 15m, 2h, 1d)")
    return int(t[:-1]) * _INTERVAL_UNITS[unit]


def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field (``*``, ``*/n``, ``a-b``, ``a,b``, ``n``) to a set."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
        else:
            base = part
        if base in ("*", ""):
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        if start < lo or end > hi or start > end or step < 1:
            raise ValueError(f"cron field out of range: {field!r}")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> dict[str, set[int]]:
    """Parse a 5-field cron expression into per-field value sets.

    Fields: minute(0-59) hour(0-23) day-of-month(1-31) month(1-12) day-of-week(0-6,
    Sunday=0). Raises ``ValueError`` if the expression is not exactly 5 fields.
    """
    fields = str(expr).split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(fields)}: {expr!r}")
    m, h, dom, mon, dow = fields
    return {
        "minute": _parse_cron_field(m, 0, 59),
        "hour": _parse_cron_field(h, 0, 23),
        "dom": _parse_cron_field(dom, 1, 31),
        "month": _parse_cron_field(mon, 1, 12),
        "dow": _parse_cron_field(dow, 0, 6),
    }


def _cron_matches(parsed: dict[str, set[int]], dt: datetime) -> bool:
    """True when ``dt`` (minute precision) satisfies the parsed cron spec.

    Day matching follows Vixie cron: when both DOM and DOW are restricted the
    minute fires if EITHER matches; otherwise both must match.
    """
    if dt.minute not in parsed["minute"]:
        return False
    if dt.hour not in parsed["hour"]:
        return False
    if dt.month not in parsed["month"]:
        return False
    dom_restricted = len(parsed["dom"]) < 31
    dow_restricted = len(parsed["dow"]) < 7
    dow_val = (dt.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
    dom_ok = dt.day in parsed["dom"]
    dow_ok = dow_val in parsed["dow"]
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_run_at(kind: str, expr: str, *, after: Optional[datetime] = None) -> datetime:
    """Compute the next fire time strictly after ``after`` (default: now)."""
    after = after or _utcnow()
    if kind == "interval":
        return after + timedelta(seconds=parse_interval(expr))
    if kind == "cron":
        parsed = parse_cron(expr)
        # advance to the next whole minute, then scan minute-by-minute
        cursor = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(_MAX_CRON_SCAN_MINUTES):
            if _cron_matches(parsed, cursor):
                return cursor
            cursor += timedelta(minutes=1)
        raise ValueError(f"cron expression {expr!r} never fires within a year")
    raise ValueError(f"unknown schedule kind {kind!r}")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
_JOBS_DDL = f"""
CREATE TABLE IF NOT EXISTS {_JOBS_TABLE} (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    user_id               TEXT NOT NULL DEFAULT 'default',
    tenant_id             TEXT DEFAULT '',
    classification        TEXT DEFAULT 'CUI',
    mode                  TEXT NOT NULL,
    payload               TEXT NOT NULL,
    schedule_kind         TEXT NOT NULL,
    schedule_expr         TEXT NOT NULL,
    delivery              TEXT DEFAULT 'log',
    status                TEXT DEFAULT 'active',
    max_retries           INTEGER DEFAULT 0,
    retry_backoff_seconds INTEGER DEFAULT 60,
    attempt               INTEGER DEFAULT 0,
    next_run_at           TEXT,
    last_run_at           TEXT,
    last_status           TEXT,
    run_count             INTEGER DEFAULT 0,
    created_at            TEXT,
    updated_at            TEXT
)
"""

_RUNS_DDL = f"""
CREATE TABLE IF NOT EXISTS {_RUNS_TABLE} (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    success         INTEGER,
    attempt         INTEGER DEFAULT 0,
    mode            TEXT,
    output          TEXT,
    error           TEXT,
    delivered       INTEGER DEFAULT 0,
    delivery_target TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT
)
"""

_JOB_COLUMNS = [
    "id", "name", "user_id", "tenant_id", "classification", "mode", "payload",
    "schedule_kind", "schedule_expr", "delivery", "status", "max_retries",
    "retry_backoff_seconds", "attempt", "next_run_at", "last_run_at",
    "last_status", "run_count", "created_at", "updated_at",
]


def _connect(conn=None):
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection

    return get_connection(), False


def _ensure_schema(conn) -> None:
    try:
        conn.execute(_JOBS_DDL)
        conn.execute(_RUNS_DDL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cron: ensure_schema failed: %s", exc)


def _row_to_job(row) -> dict[str, Any]:
    return {col: row[i] for i, col in enumerate(_JOB_COLUMNS)}


def create_job(
    name: str,
    mode: str,
    payload: str,
    schedule_kind: str,
    schedule_expr: str,
    *,
    delivery: str = "log",
    user_id: str = _DEFAULT_USER,
    tenant_id: str = "",
    max_retries: int = 0,
    retry_backoff_seconds: int = 60,
    classification: str = "CUI",
    conn=None,
) -> dict[str, Any]:
    """Create and persist a cron job. Validates the schedule up front."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if schedule_kind not in ("interval", "cron"):
        raise ValueError("schedule_kind must be 'interval' or 'cron'")
    # Validate + normalise the schedule (raises on garbage) and seed next_run_at.
    first = next_run_at(schedule_kind, schedule_expr)

    c, _ = _connect(conn)
    _ensure_schema(c)
    now = _utcnow()
    job_id = "cron-" + uuid.uuid4().hex[:12]
    c.execute(
        f"INSERT INTO {_JOBS_TABLE} "
        f"(id, name, user_id, tenant_id, classification, mode, payload, "
        f"schedule_kind, schedule_expr, delivery, status, max_retries, "
        f"retry_backoff_seconds, attempt, next_run_at, last_run_at, last_status, "
        f"run_count, created_at, updated_at) VALUES "
        f"(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            job_id, name, user_id, tenant_id, classification, mode, payload,
            schedule_kind, schedule_expr, delivery, "active", int(max_retries),
            int(retry_backoff_seconds), 0, _iso(first), None, None, 0,
            _iso(now), _iso(now),
        ),
    )
    c.commit()
    return get_job(job_id, conn=c) or {"id": job_id, "name": name}


def get_job(job_id: str, *, conn=None) -> Optional[dict[str, Any]]:
    c, _ = _connect(conn)
    _ensure_schema(c)
    cur = c.execute(
        f"SELECT {', '.join(_JOB_COLUMNS)} FROM {_JOBS_TABLE} WHERE id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    *, user_id: Optional[str] = None, status: Optional[str] = None, conn=None
) -> list[dict[str, Any]]:
    c, _ = _connect(conn)
    _ensure_schema(c)
    clauses, params = [], []
    if user_id is not None:
        clauses.append("user_id = %s")
        params.append(user_id)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = c.execute(
        f"SELECT {', '.join(_JOB_COLUMNS)} FROM {_JOBS_TABLE}{where} "
        f"ORDER BY next_run_at",
        tuple(params),
    )
    return [_row_to_job(r) for r in cur.fetchall()]


def set_status(job_id: str, status: str, *, conn=None) -> bool:
    if status not in ("active", "paused"):
        raise ValueError("status must be 'active' or 'paused'")
    c, _ = _connect(conn)
    _ensure_schema(c)
    c.execute(
        f"UPDATE {_JOBS_TABLE} SET status = %s, updated_at = %s WHERE id = %s",
        (status, _iso(_utcnow()), job_id),
    )
    c.commit()
    return get_job(job_id, conn=c) is not None


def remove_job(job_id: str, *, conn=None) -> bool:
    c, _ = _connect(conn)
    _ensure_schema(c)
    existed = get_job(job_id, conn=c) is not None
    c.execute(f"DELETE FROM {_JOBS_TABLE} WHERE id = %s", (job_id,))
    c.commit()
    return existed


def _record_run(
    c,
    job: dict[str, Any],
    *,
    started: datetime,
    success: bool,
    output: str,
    error: str,
    delivered: bool,
    delivery_target: str,
) -> None:
    try:
        c.execute(
            f"INSERT INTO {_RUNS_TABLE} "
            f"(id, job_id, started_at, finished_at, success, attempt, mode, "
            f"output, error, delivered, delivery_target, classification, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "run-" + uuid.uuid4().hex[:12], job["id"], _iso(started),
                _iso(_utcnow()), 1 if success else 0, int(job.get("attempt", 0)),
                job.get("mode"), (output or "")[:_MAX_OUTPUT], (error or "")[:2000],
                1 if delivered else 0, delivery_target,
                job.get("classification", "CUI"), _iso(_utcnow()),
            ),
        )
        c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cron: record_run failed: %s", exc)


def list_runs(job_id: str, *, limit: int = 20, conn=None) -> list[dict[str, Any]]:
    c, _ = _connect(conn)
    _ensure_schema(c)
    cols = ["id", "job_id", "started_at", "finished_at", "success", "attempt",
            "mode", "output", "error", "delivered", "delivery_target"]
    cur = c.execute(
        f"SELECT {', '.join(cols)} FROM {_RUNS_TABLE} WHERE job_id = %s "
        f"ORDER BY started_at DESC LIMIT {int(limit)}",
        (job_id,),
    )
    return [{cols[i]: r[i] for i in range(len(cols))} for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _execute_agent(job: dict[str, Any]) -> tuple[bool, str, str]:
    """Run the job payload as a single-shot SAG agent turn."""
    try:
        from tools.agent_runtime.runtime import AgentRuntime

        runtime = AgentRuntime(
            user_id=job.get("user_id", _DEFAULT_USER),
            tenant_id=job.get("tenant_id", "") or "",
        )
        result = runtime.run_turn(job["payload"])
        text = getattr(result, "final_content", "") or ""
        return True, text, ""
    except Exception as exc:  # noqa: BLE001
        return False, "", f"agent turn failed: {exc}"


def _execute_script(job: dict[str, Any]) -> tuple[bool, str, str]:
    """Run the job payload as an allowlisted ``python tools/...`` command."""
    try:
        from tools.skills.invoke import _is_safe_command, run_command

        cmd = job["payload"]
        if not _is_safe_command(cmd):
            return False, "", (
                "command not in allowlist (python tools/, python -m tools, python -c)"
            )
        res = run_command(cmd, [], timeout=600)
        if res.get("skipped"):
            return False, "", res.get("reason", "command skipped")
        rc = res.get("returncode", 1)
        out = (res.get("stdout", "") or "") + (res.get("stderr", "") or "")
        return rc == 0, out, "" if rc == 0 else f"exit code {rc}"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"script exec failed: {exc}"


def execute_job(job: dict[str, Any]) -> tuple[bool, str, str]:
    """Dispatch a job by mode. Returns ``(success, output, error)``."""
    if job.get("mode") == "agent":
        return _execute_agent(job)
    if job.get("mode") == "script":
        return _execute_script(job)
    return False, "", f"unknown mode {job.get('mode')!r}"


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def _outbox_dir() -> Path:
    root = Path(__file__).resolve()
    # tools/agent_runtime/cron.py -> repo root via upward sentinel walk
    for parent in root.parents:
        if (parent / ".git").exists() or (parent / "CLAUDE.md").exists():
            return parent / "data" / "cron_outbox"
    return Path("data") / "cron_outbox"


def deliver_result(job: dict[str, Any], success: bool, output: str, error: str) -> tuple[bool, str]:
    """Deliver a run result per the job's ``delivery`` setting.

    ``log`` (default): no-op — the recorded run row IS the delivery.
    ``email``: SMTP via the existing notifications EmailAdapter; degrades to an
    on-file outbox (``data/cron_outbox/``) when SMTP is not configured.
    ``gateway:<channel>:<chat_id>``: best-effort outbound via a gateway adapter.

    Returns ``(delivered, target)``.
    """
    delivery = (job.get("delivery") or "log").strip()
    title = f"Cron job '{job.get('name')}' {'succeeded' if success else 'FAILED'}"
    body = output if success else (error or "job failed")

    if delivery in ("", "log"):
        return False, "log"

    if delivery == "email":
        try:
            from tools.notifications.adapters.email_adapter import EmailAdapter

            adapter = EmailAdapter({})
            if adapter.send(title, body, severity="info" if success else "warning"):
                return True, "email"
        except Exception as exc:  # noqa: BLE001
            logger.debug("cron: email delivery failed: %s", exc)
        # degrade to on-file outbox (email-on-file)
        try:
            outbox = _outbox_dir()
            outbox.mkdir(parents=True, exist_ok=True)
            fname = f"{job['id']}-{_utcnow().strftime('%Y%m%dT%H%M%SZ')}.txt"
            (outbox / fname).write_text(
                f"Subject: {title}\n\n{body}\n", encoding="utf-8", newline=""
            )
            return True, f"email-on-file:{fname}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("cron: email-on-file failed: %s", exc)
            return False, "email-failed"

    if delivery.startswith("gateway:"):
        return _deliver_gateway(delivery, title, body)

    return False, f"unknown:{delivery}"


def _deliver_gateway(delivery: str, title: str, body: str) -> tuple[bool, str]:
    """Best-effort outbound via a gateway adapter (degrades to un-delivered)."""
    try:
        parts = delivery.split(":", 2)
        if len(parts) != 3:
            return False, "gateway-bad-target"
        _, channel, chat_id = parts
        from tools.gateway.gateway_agent import _load_adapters, _load_config

        adapters = _load_adapters(_load_config())
        adapter = adapters.get(channel)
        if adapter is None:
            return False, "gateway-no-adapter"
        ok = adapter.send_message(chat_id, f"{title}\n\n{body}")
        return bool(ok), f"gateway:{channel}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("cron: gateway delivery unavailable: %s", exc)
        return False, "gateway-unavailable"


# ---------------------------------------------------------------------------
# Tick — run all due jobs (called by the agent_cron reflex)
# ---------------------------------------------------------------------------
def _reschedule_after_run(c, job: dict[str, Any], success: bool) -> None:
    """Advance a job's schedule after a run, applying retry/backoff on failure."""
    now = _utcnow()
    attempt = int(job.get("attempt", 0) or 0)
    max_retries = int(job.get("max_retries", 0) or 0)
    backoff = int(job.get("retry_backoff_seconds", 60) or 60)

    if not success and attempt < max_retries:
        # Exponential backoff retry: keep the same scheduled slot pending.
        delay = backoff * (2 ** attempt)
        next_dt = now + timedelta(seconds=delay)
        new_attempt = attempt + 1
    else:
        # Success, or retries exhausted: advance to the next scheduled fire.
        next_dt = next_run_at(job["schedule_kind"], job["schedule_expr"], after=now)
        new_attempt = 0

    c.execute(
        f"UPDATE {_JOBS_TABLE} SET next_run_at = %s, attempt = %s, "
        f"last_run_at = %s, last_status = %s, run_count = run_count + 1, "
        f"updated_at = %s WHERE id = %s",
        (
            _iso(next_dt), new_attempt, _iso(now),
            "success" if success else "failed", _iso(now), job["id"],
        ),
    )
    c.commit()


def run_job_now(job: dict[str, Any], *, conn=None, deliver: bool = True) -> dict[str, Any]:
    """Execute one job immediately, record the run, and (optionally) deliver.

    Does NOT reschedule — used both by :func:`run_due_jobs` (which reschedules
    afterward) and by ``icdev cron run <id>`` for an ad-hoc invocation.
    """
    c, _ = _connect(conn)
    _ensure_schema(c)
    started = _utcnow()
    success, output, error = execute_job(job)
    delivered, target = (False, "log")
    if deliver:
        delivered, target = deliver_result(job, success, output, error)
    _record_run(
        c, job, started=started, success=success, output=output, error=error,
        delivered=delivered, delivery_target=target,
    )
    return {
        "job_id": job["id"], "success": success, "delivered": delivered,
        "delivery_target": target, "error": error,
    }


def run_due_jobs(*, now: Optional[datetime] = None, conn=None) -> dict[str, Any]:
    """Execute every active job whose ``next_run_at`` is due. Reflex entry point."""
    c, _ = _connect(conn)
    _ensure_schema(c)
    now = now or _utcnow()
    cur = c.execute(
        f"SELECT {', '.join(_JOB_COLUMNS)} FROM {_JOBS_TABLE} "
        f"WHERE status = %s AND next_run_at <= %s ORDER BY next_run_at",
        ("active", _iso(now)),
    )
    due = [_row_to_job(r) for r in cur.fetchall()]
    results = []
    for job in due:
        try:
            res = run_job_now(job, conn=c, deliver=True)
            _reschedule_after_run(c, job, bool(res.get("success")))
            results.append(res)
        except Exception as exc:  # noqa: BLE001 — never wedge the tick
            logger.warning("cron: job %s failed to tick: %s", job.get("id"), exc)
            try:
                _reschedule_after_run(c, job, False)
            except Exception:  # noqa: BLE001
                pass
            results.append({"job_id": job.get("id"), "success": False, "error": str(exc)})
    return {
        "checked_at": _iso(now),
        "due": len(due),
        "ran": len(results),
        "succeeded": sum(1 for r in results if r.get("success")),
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI — icdev cron ...
# ---------------------------------------------------------------------------
def cron_main(argv: list[str] | None = None) -> int:
    """Handle ``icdev cron <cmd> [...]``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="icdev cron",
        description="Schedule ICDEV standalone-agent prompts or allowlisted scripts.",
    )
    subs = parser.add_subparsers(dest="cmd")

    create_p = subs.add_parser("create", help="Create a scheduled job.")
    create_p.add_argument("name", help="Human-readable job name.")
    create_p.add_argument(
        "--mode", choices=MODES, required=True,
        help="agent = run a prompt through the SAG runtime; script = allowlisted python tools/... command.",
    )
    create_p.add_argument(
        "--payload", required=True,
        help="The agent prompt (mode=agent) or the command (mode=script).",
    )
    sched = create_p.add_mutually_exclusive_group(required=True)
    sched.add_argument("--interval", help="Interval schedule, e.g. 15m, 2h, 1d, 90s.")
    sched.add_argument("--cron", dest="cron_expr", help="5-field cron expression, e.g. '0 9 * * 1-5'.")
    create_p.add_argument(
        "--delivery", default="log",
        help="log (default) | email | gateway:<channel>:<chat_id>.",
    )
    create_p.add_argument("--max-retries", type=int, default=0)
    create_p.add_argument("--retry-backoff", type=int, default=60, help="Base backoff seconds.")
    create_p.add_argument("--user", default=_DEFAULT_USER)
    create_p.add_argument("--tenant", default="")
    create_p.add_argument("--json", action="store_true")

    list_p = subs.add_parser("list", help="List scheduled jobs.")
    list_p.add_argument("--user", default=None)
    list_p.add_argument("--status", default=None, choices=["active", "paused"])
    list_p.add_argument("--json", action="store_true")

    for name in ("pause", "resume", "remove", "run"):
        sp = subs.add_parser(name, help=f"{name.capitalize()} a job by id.")
        sp.add_argument("job_id")
        sp.add_argument("--json", action="store_true")

    runs_p = subs.add_parser("runs", help="Show recent run history for a job.")
    runs_p.add_argument("job_id")
    runs_p.add_argument("--limit", type=int, default=20)
    runs_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "create":
        kind = "interval" if args.interval else "cron"
        expr = args.interval or args.cron_expr
        try:
            job = create_job(
                args.name, args.mode, args.payload, kind, expr,
                delivery=args.delivery, user_id=args.user, tenant_id=args.tenant,
                max_retries=args.max_retries, retry_backoff_seconds=args.retry_backoff,
            )
        except ValueError as exc:
            print(f"icdev cron create: {exc}", file=__import__("sys").stderr)
            return 2
        if args.json:
            print(json.dumps(job, indent=2, default=str))
        else:
            print(f"Created job {job['id']} ({job['name']}) — next run {job.get('next_run_at')}")
        return 0

    if args.cmd == "list":
        jobs = list_jobs(user_id=args.user, status=args.status)
        if args.json:
            print(json.dumps({"count": len(jobs), "jobs": jobs}, indent=2, default=str))
            return 0
        if not jobs:
            print("No cron jobs.")
            return 0
        print(f"{len(jobs)} job(s):")
        for j in jobs:
            sch = f"{j['schedule_kind']}:{j['schedule_expr']}"
            print(
                f"  {j['id']:<20} [{j['status']:<7}] {j['mode']:<6} {sch:<22} "
                f"next={j.get('next_run_at') or '-':<28} {j['name']}"
            )
        return 0

    if args.cmd in ("pause", "resume", "remove", "run", "runs"):
        job = get_job(args.job_id)
        if job is None:
            print(f"icdev cron {args.cmd}: no such job {args.job_id!r}", file=__import__("sys").stderr)
            return 2
        if args.cmd == "pause":
            set_status(args.job_id, "paused")
            print(f"Paused {args.job_id}.")
            return 0
        if args.cmd == "resume":
            set_status(args.job_id, "active")
            print(f"Resumed {args.job_id}.")
            return 0
        if args.cmd == "remove":
            remove_job(args.job_id)
            print(f"Removed {args.job_id}.")
            return 0
        if args.cmd == "run":
            res = run_job_now(job, deliver=True)
            if args.json:
                print(json.dumps(res, indent=2, default=str))
            else:
                status = "OK" if res["success"] else "FAILED"
                print(f"Ran {args.job_id}: {status} (delivery={res['delivery_target']})")
            return 0 if res["success"] else 1
        if args.cmd == "runs":
            runs = list_runs(args.job_id, limit=args.limit)
            if args.json:
                print(json.dumps({"count": len(runs), "runs": runs}, indent=2, default=str))
                return 0
            if not runs:
                print("No runs yet.")
                return 0
            for r in runs:
                st = "OK " if r["success"] else "ERR"
                print(f"  {st} {r['started_at']}  attempt={r['attempt']}  delivered={r['delivered']}")
            return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(cron_main(sys.argv[1:]))
