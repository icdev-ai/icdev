# CUI // SP-CTI
"""Delivery events — give the DORA query something real to measure.

``/api/sre/dora`` (``tools/dashboard/api/sre.py``, surfaced at ``/sre``) is
correct and honest: it bands all four DORA keys, and it refuses to launder a
DB error or the absence of data into a favourable rating — it emits
``Not Assessed`` instead. Measured 2026-08-02 it reported
``metrics_assessed: 0``, because **every input table was empty**: of 14,176
``audit_trail`` rows, zero carried any of the nine deploy/rollback event types
the query reads; ``ci_pipeline_runs`` and ``sre_incidents`` held no rows at all.
A correct metric with no inputs is indistinguishable from a broken one.

This module supplies the inputs. It does not touch the query.

**The ledger already exists.** Every change to this repo lands as a kanban task
that reaches ``done``, and ``done`` is merge-verified — ``tools/kanban/cli.py``
refuses the transition while a branch carrying that task id still has commits
that are not on ``origin/main``. So ``kanban_tasks.status = 'done'`` with a
``completed_at`` *is* a record of a change reaching main, and
``kanban_verifications`` (dated, with pass/fail/bypassed per attempt) is a
record of what the verifier said about it on the way. Those two tables are
projected here into the shapes the DORA query already reads.

    python tools/idp/delivery_events.py --status --json     # what DORA can see now
    python tools/idp/delivery_events.py --sync --dry-run    # what would be emitted
    python tools/idp/delivery_events.py --sync --days 90    # emit

The mapping, stated plainly so nobody has to reverse-engineer it from a rating:

**Deployment** = one kanban task that reached ``done`` with a ``completed_at``.
One merged change, one deploy — this repo practises trunk-based delivery, so a
merge to main *is* the release. Emitted as an ``audit_trail`` row of type
``deployment_initiated`` timestamped at the moment the change landed (not at
backfill time), which is what ``deploy_frequency`` counts and what
``change_failure_rate`` divides by.

**Failed deployment** = a deployment whose *most recent* verification attempt
returned ``failed`` or ``phantom``. That is: the last thing the verifier said
about this change before it landed was that it did not pass, and it landed
anyway. Emitted as ``deployment_failed``. Note what is deliberately **not**
counted: ``bypassed`` (verification was skipped, e.g. a force-done with an
audited reason — that is an *unverified* change, not a failed one) and
``failure_count > 0`` (the delivery attempt was retried before landing, which
is rework, not a change failure). Both are still carried in the event's
``details`` so a stricter definition can be recomputed later without re-deriving
anything.

**Lead time** = work-start → landed, emitted as a ``ci_pipeline_runs`` row.
Work-start is ``kanban_tasks.scheduled_at`` — the dispatch timestamp — falling
back to the task's *earliest* verification attempt when it was never formally
dispatched. A task with neither signal gets its deployment event but **no**
pipeline row: its lead time is genuinely unknown, and inventing a start from
``created_at`` would measure how long the card sat in the backlog, not how long
the change took. Those are counted and reported as ``no_start_signal``.

**MTTR is deliberately left unassessed.** It reads ``sre_incidents``, and this
platform has no production incident ledger — nothing here records a service
degradation and its restoration. The nearest available signals (bug-type tasks,
failed verifications) are not production incidents, and projecting them into
``sre_incidents`` would put a rating on the dashboard that no measurement
supports. ``mttr`` therefore stays ``Not Assessed`` after a full sync, which is
the correct answer and not a gap in this module.

Emission is idempotent and incremental: the set of already-emitted task ids is
read back out of ``audit_trail`` itself (parsed in Python — see the PG
portability rule in CLAUDE.md), so re-running only adds changes that landed
since the last run. ``audit_trail`` is append-only; nothing here updates or
deletes a row.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.audit.audit_logger import VALID_EVENT_TYPES  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

# Event types the DORA query already reads. Both are members of
# audit_logger.VALID_EVENT_TYPES and of the live audit_trail CHECK constraint —
# asserted at import time below rather than discovered as a write failure that
# some caller's `except` swallows.
DEPLOY_EVENT_TYPE = "deployment_initiated"
DEPLOY_FAILED_EVENT_TYPE = "deployment_failed"

for _event_type in (DEPLOY_EVENT_TYPE, DEPLOY_FAILED_EVENT_TYPE):
    if _event_type not in VALID_EVENT_TYPES:  # pragma: no cover - guards a vocabulary regression
        raise RuntimeError(
            f"{_event_type!r} is not in audit_logger.VALID_EVENT_TYPES; the audit_trail "
            "CHECK constraint would reject every event this module emits."
        )

#: Verification results that mark a landed change as a failed deployment.
FAILURE_RESULTS = ("failed", "phantom")

ACTOR = "kanban-delivery-pipeline"
SOURCE = "kanban_merge_ledger"
PIPELINE_ID_PREFIX = "kanban:"
PIPELINE_SESSION_KEY = "kanban-delivery"
PIPELINE_PLATFORM = "kanban"
PIPELINE_STATUS = "completed"

DEFAULT_WINDOW_DAYS = 90

#: Commit every N changes so one bad row cannot cost a whole backfill. Both
#: writes for a single change (audit event + pipeline run) always land in the
#: same transaction as each other.
_CHUNK = 200


class DeliveryEventError(RuntimeError):
    """Raised when delivery events cannot be derived or emitted."""


# ── timestamp helpers ────────────────────────────────────────────────────────


def _to_dt(value: Any) -> datetime | None:
    """Coerce a stored timestamp to an aware datetime, or None.

    PostgreSQL hands back ``datetime`` objects; SQLite hands back strings.
    Both backends are read here, so neither shape is assumed.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        else:
            parsed = None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """Render an aware datetime as a naive-UTC ISO string.

    ``audit_trail.created_at`` and ``ci_pipeline_runs.created_at`` are
    ``timestamp without time zone`` on the primary PostgreSQL backend. Writing
    an offset-bearing string works (PG casts it) but stores something that no
    longer round-trips comparably against the naive values already there, so
    the offset is normalised away here instead.
    """
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _cutoff(days: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=max(int(days), 0)))


# ── derivation ───────────────────────────────────────────────────────────────


def _verification_bounds(conn, task_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Earliest verification timestamp and latest result, per task id.

    Reduced in Python rather than with a LATERAL/window query: the same code
    has to run on the SQLite fallback, and the row count here is bounded by the
    number of changes in the window.
    """
    bounds: dict[str, dict[str, Any]] = {}
    for start in range(0, len(task_ids), 500):
        chunk = task_ids[start : start + 500]
        placeholders = ", ".join(["%s"] * len(chunk))
        rows = conn.execute(
            f"SELECT task_id, verified_at, result FROM kanban_verifications "  # noqa: S608 - placeholders only
            f"WHERE task_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            task_id, verified_at, result = row[0], row[1], row[2]
            when = _to_dt(verified_at)
            if when is None:
                continue
            entry = bounds.setdefault(task_id, {"first_at": when, "last_at": when, "last_result": result})
            if when < entry["first_at"]:
                entry["first_at"] = when
            if when >= entry["last_at"]:
                entry["last_at"] = when
                entry["last_result"] = result
    return bounds


def collect_changes(conn, days: int = DEFAULT_WINDOW_DAYS) -> list[dict[str, Any]]:
    """Project the kanban merge ledger into delivery-change records.

    One record per change that landed on main inside the window, carrying the
    evidence each DORA key needs plus the provenance fields that justify the
    classification.
    """
    rows = conn.execute(
        "SELECT id, title, task_type, created_at, scheduled_at, completed_at, "
        "       completed_via_bypass, failure_count, files_changed, lines_added, lines_removed "
        "FROM kanban_tasks "
        "WHERE status = 'done' AND completed_at IS NOT NULL AND completed_at >= %s "
        "ORDER BY completed_at",
        (_cutoff(days),),
    ).fetchall()

    changes: list[dict[str, Any]] = []
    for row in rows:
        landed = _to_dt(row[5])
        if landed is None:
            continue
        changes.append(
            {
                "task_id": row[0],
                "title": row[1],
                "task_type": row[2] or "build",
                "created_at": _to_dt(row[3]),
                "scheduled_at": _to_dt(row[4]),
                "landed_at": landed,
                "completed_via_bypass": bool(row[6]),
                "failure_count": int(row[7] or 0),
                "files_changed": int(row[8] or 0),
                "lines_added": int(row[9] or 0),
                "lines_removed": int(row[10] or 0),
            }
        )

    bounds = _verification_bounds(conn, [c["task_id"] for c in changes])
    for change in changes:
        entry = bounds.get(change["task_id"]) or {}
        change["verification_result"] = entry.get("last_result")
        change["failed"] = change["verification_result"] in FAILURE_RESULTS
        # Work-start: dispatch if we have it, else the first time anyone
        # verified the change. Never created_at — see the module docstring.
        start = change["scheduled_at"] or entry.get("first_at")
        if start is not None and start > change["landed_at"]:
            start = None
        change["started_at"] = start
    return changes


def emitted_task_ids(conn) -> set[str]:
    """Task ids that already carry a deployment event.

    ``details`` is read raw and parsed with ``json.loads`` rather than filtered
    with ``json_extract``/``->>``: the former is SQLite-only dialect and the
    latter would not run on the SQLite fallback. See the PG portability rule in
    CLAUDE.md.
    """
    rows = conn.execute(
        "SELECT details FROM audit_trail WHERE event_type = %s",
        (DEPLOY_EVENT_TYPE,),
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        raw = row[0]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("source") == SOURCE:
            task_id = payload.get("task_id")
            if task_id:
                seen.add(str(task_id))
    return seen


def _existing_pipeline_ids(conn) -> set[str]:
    rows = conn.execute(
        "SELECT id FROM ci_pipeline_runs WHERE id LIKE %s",
        (PIPELINE_ID_PREFIX + "%",),
    ).fetchall()
    return {row[0] for row in rows}


def _details(change: dict[str, Any], *, failure: bool = False) -> str:
    payload = {
        "source": SOURCE,
        "task_id": change["task_id"],
        "task_type": change["task_type"],
        "title": change["title"],
        "landed_at": _iso(change["landed_at"]),
        "started_at": _iso(change["started_at"]),
        "verification_result": change["verification_result"],
        "completed_via_bypass": change["completed_via_bypass"],
        "failure_count": change["failure_count"],
        "files_changed": change["files_changed"],
        "lines_added": change["lines_added"],
        "lines_removed": change["lines_removed"],
    }
    if failure:
        payload["failure_signal"] = "final_verification_did_not_pass"
    return json.dumps(payload)


# ── emission ─────────────────────────────────────────────────────────────────


def _emit_change(conn, change: dict[str, Any], pipeline_ids: set[str]) -> dict[str, bool]:
    """Write one change's events. Caller owns the transaction."""
    written = {"deploy": False, "failure": False, "pipeline": False}

    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, details, classification, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            DEPLOY_EVENT_TYPE,
            ACTOR,
            f"change landed on main: {change['task_id']}",
            _details(change),
            "CUI",
            _iso(change["landed_at"]),
        ),
    )
    written["deploy"] = True

    if change["failed"]:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, classification, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                DEPLOY_FAILED_EVENT_TYPE,
                ACTOR,
                f"change landed without a passing verification: {change['task_id']}",
                _details(change, failure=True),
                "CUI",
                _iso(change["landed_at"]),
            ),
        )
        written["failure"] = True

    pipeline_id = PIPELINE_ID_PREFIX + change["task_id"]
    if change["started_at"] is not None and pipeline_id not in pipeline_ids:
        conn.execute(
            "INSERT INTO ci_pipeline_runs "
            "(id, session_key, run_id, platform, workflow, status, trigger_source, "
            " classification, created_at, completed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pipeline_id,
                PIPELINE_SESSION_KEY,
                change["task_id"],
                PIPELINE_PLATFORM,
                change["task_type"],
                PIPELINE_STATUS,
                "tools.idp.delivery_events",
                "CUI",
                _iso(change["started_at"]),
                _iso(change["landed_at"]),
            ),
        )
        pipeline_ids.add(pipeline_id)
        written["pipeline"] = True

    return written


def sync_delivery_events(
    days: int = DEFAULT_WINDOW_DAYS,
    dry_run: bool = False,
    conn=None,
) -> dict[str, Any]:
    """Emit delivery events for every change that landed and has none yet.

    Returns a summary. ``no_start_signal`` is reported explicitly rather than
    silently dropped: it is the count of changes whose lead time is genuinely
    unknown, and a silent drop would read as full coverage.
    """
    own_conn = conn is None
    conn = conn or get_connection()
    try:
        changes = collect_changes(conn, days=days)
        already = emitted_task_ids(conn)
        pipeline_ids = _existing_pipeline_ids(conn)

        pending = [c for c in changes if c["task_id"] not in already]
        summary: dict[str, Any] = {
            "window_days": days,
            "dry_run": bool(dry_run),
            "changes_in_window": len(changes),
            "already_emitted": len(changes) - len(pending),
            "deploy_events": 0,
            "failure_events": 0,
            "pipeline_runs": 0,
            "no_start_signal": sum(1 for c in pending if c["started_at"] is None),
        }

        if dry_run:
            summary["would_emit"] = len(pending)
            return summary

        for start in range(0, len(pending), _CHUNK):
            batch = pending[start : start + _CHUNK]
            try:
                for change in batch:
                    written = _emit_change(conn, change, pipeline_ids)
                    summary["deploy_events"] += int(written["deploy"])
                    summary["failure_events"] += int(written["failure"])
                    summary["pipeline_runs"] += int(written["pipeline"])
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - re-raised; never swallowed
                conn.rollback()
                raise DeliveryEventError(
                    f"delivery event emission failed after {summary['deploy_events']} "
                    f"deploy event(s): {exc}"
                ) from exc

        return summary
    finally:
        if own_conn:
            conn.close()


def dora_input_status(days: int = 30, conn=None) -> dict[str, Any]:
    """What the DORA query can currently see, table by table.

    Deliberately reports raw counts and no ratings — the endpoint owns the
    banding, and duplicating it here would create a second place for a rating
    to be wrong.
    """
    own_conn = conn is None
    conn = conn or get_connection()
    try:
        cutoff = _cutoff(days)
        status: dict[str, Any] = {"window_days": days}

        def _count(sql: str, params: tuple) -> Any:
            try:
                row = conn.execute(sql, params).fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            except Exception as exc:  # noqa: BLE001 - reported, not hidden
                return {"error": str(exc)}

        status["deploy_events"] = _count(
            "SELECT COUNT(*) FROM audit_trail WHERE event_type IN "
            "('deployment_initiated', 'deploy', 'ci_deploy') AND created_at >= %s",
            (cutoff,),
        )
        status["failure_events"] = _count(
            "SELECT COUNT(*) FROM audit_trail WHERE event_type IN "
            "('deployment_failed', 'deploy_failed', 'rollback_executed', "
            "'deploy_rollback', 'rollback') AND created_at >= %s",
            (cutoff,),
        )
        status["completed_pipeline_runs"] = _count(
            "SELECT COUNT(*) FROM ci_pipeline_runs WHERE status = 'completed' "
            "AND completed_at IS NOT NULL AND created_at >= %s",
            (cutoff,),
        )
        status["resolved_incidents"] = _count(
            "SELECT COUNT(*) FROM sre_incidents WHERE status IN "
            "('resolved', 'postmortem', 'closed') AND resolved_at >= %s "
            "AND mttr_seconds IS NOT NULL",
            (cutoff,),
        )
        return status
    finally:
        if own_conn:
            conn.close()


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit DORA delivery events derived from the kanban merge ledger."
    )
    parser.add_argument("--sync", action="store_true", help="emit events for changes with none yet")
    parser.add_argument("--status", action="store_true", help="report what the DORA query can see")
    parser.add_argument("--dry-run", action="store_true", help="with --sync: count, do not write")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"look-back window in days (default {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if not (args.sync or args.status):
        parser.error("nothing to do — pass --sync and/or --status")

    result: dict[str, Any] = {}
    if args.sync:
        result["sync"] = sync_delivery_events(days=args.days, dry_run=args.dry_run)
    if args.status:
        result["status"] = dora_input_status(days=min(args.days, 30))

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for section, payload in result.items():
            print(f"[{section}]")
            for key, value in payload.items():
                print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
