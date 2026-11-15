# CUI // SP-CTI
"""ACE event log — the durable record of ecosystem events and what each led to (icx-ace-01).

Any ICDEV canvas emits a topic/payload event here. The event dispatcher
(``event_dispatcher.py``) reads the unprocessed ones and, for each role that
listens to the topic, either records a RECOMMENDATION (a pending step of the
declared launch template, ``decl-wft-ace-run-launch``, which a person decides at
``/decisions``) or, only under a live auto-dispatch grant (``auto_dispatch.py``),
launches the role and says so. Every outcome is a row in ``ace_event_results``.

This module used to live in ``event_bus.py``. Commit 36d611a31 (#64) turned that
file into the per-instance SSE queue that ``/api/ace/<id>/stream`` uses and
dropped these six functions, while their callers swallowed the ImportError:
``/api/ace/events`` answered 500 and completion events were silently lost. The
two jobs now have two modules; ``event_bus.py`` is the queue only.

Usage (from any canvas):
    from icdev.tools.ace.event_log import emit
    emit("document.analyzed", {"content": "..."}, source_canvas="dic", source_id="doc-1")

Topics are not hand-listed. ``dispatch_topics()`` derives them from the role
YAMLs (``args/ace/roles/*.yaml``: ``communication.listen_topics`` and
``emit_topics``), and ``emit`` refuses a topic no role declares. Wildcard topics
(``ace.task.*``) are reported as declared; dispatch matching is exact.

ACE tables are canvas tables (no classification/tenant_id columns), so every
query uses ``get_canvas_connection``. ``%s`` placeholders are rewritten for
SQLite by the storage layer.

Reads (``get_pending``, ``get_recent_results``, ``pending_count``) RAISE when the
log cannot be read, so a caller can say so (the route answers 503). ``emit`` is
best-effort: it never breaks the caller, and returns None when it wrote nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.event_log")

_DB_ENV = "ICDEV_ACE_DB_URL"

#: What an event led to, per role (``ace_event_results.status``).
RESULT_STATUSES: tuple[str, ...] = (
    "recommended",    # a pending decl-wft-ace-run-launch step waits for a person
    "launched",       # a person decided it; the step was spent and the team launched
    "declined",       # a person declined it
    "withdrawn",      # the step was withdrawn (expired, or no longer what was decided)
    "auto_launched",  # launched under a live auto-dispatch grant (grant_id says which)
    "auto_refused",   # a grant applied but the launch could not be recorded or failed
)


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection(_DB_ENV)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _role_topics(roles: list | None = None) -> list[tuple[str, list[str], list[str]]]:
    if roles is None:
        from icdev.tools.ace.role_loader import RoleLoader

        roles = RoleLoader().list_roles()
    out = []
    for role in roles:
        comm = role.communication or {}
        out.append((role.role_id, list(comm.get("listen_topics") or []),
                    list(comm.get("emit_topics") or [])))
    return out


def dispatch_topics(roles: list | None = None) -> dict[str, list[str]]:
    """Every topic a role declares -> the roles that listen to it (derived, sorted)."""
    topics: dict[str, set[str]] = {}
    for role_id, listen, emits in _role_topics(roles):
        for topic in emits:
            topics.setdefault(topic, set())
        for topic in listen:
            topics.setdefault(topic, set()).add(role_id)
    return {t: sorted(r) for t, r in sorted(topics.items())}


def emit(
    topic: str,
    payload: dict[str, Any],
    source_canvas: str = "",
    source_id: str = "",
    *,
    roles: list | None = None,
) -> int | None:
    """Record an event. Returns its id, or None when nothing was written.

    A topic no role declares is refused (None) and logged: nothing could act on
    it, and an undeclared topic is usually a typo.
    """
    try:
        if topic not in dispatch_topics(roles):
            logger.info("ace event refused: topic %r is declared by no role", topic)
            return None
        conn = _conn()
        try:
            row = conn.execute(
                "INSERT INTO ace_events (topic, source_canvas, source_id, payload_json, "
                "processed, created_at) VALUES (%s, %s, %s, %s, 0, %s) RETURNING id",
                (topic, source_canvas or "", source_id or "",
                 json.dumps(payload or {}, sort_keys=True, default=str), _now()),
            ).fetchone()
            conn.commit()
            return int(row[0]) if row else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — emitting never breaks the caller
        logger.warning("ace event %s not recorded: %s", topic, exc)
        return None


def _event(row) -> dict[str, Any]:
    r = tuple(row)
    return {"id": int(r[0]), "topic": r[1], "source_canvas": r[2], "source_id": r[3],
            "payload": json.loads(r[4] or "{}"), "created_at": str(r[5] or "")}


def get_pending(limit: int = 30) -> list[dict[str, Any]]:
    """Unprocessed events, oldest first. Raises when the log cannot be read."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, topic, source_canvas, source_id, payload_json, created_at "
            "FROM ace_events WHERE processed = 0 ORDER BY created_at ASC, id ASC LIMIT %s",
            (int(limit),),
        ).fetchall()
        return [_event(r) for r in rows]
    finally:
        conn.close()


def get_event(event_id: int) -> dict[str, Any] | None:
    """One event by id, processed or not. Raises when the log cannot be read."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, topic, source_canvas, source_id, payload_json, created_at "
            "FROM ace_events WHERE id = %s", (int(event_id),),
        ).fetchone()
        return _event(row) if row else None
    finally:
        conn.close()


def mark_processed(event_id: int) -> None:
    """Mark an event processed: the dispatcher has acted on it for every role."""
    conn = _conn()
    try:
        conn.execute("UPDATE ace_events SET processed = 1 WHERE id = %s", (int(event_id),))
        conn.commit()
    finally:
        conn.close()


def store_result(
    event_id: int,
    role_id: str,
    instance_id: str = "",
    status: str = "recommended",
    *,
    step_id: str = "",
    grant_id: str = "",
    reason: str = "",
) -> int | None:
    """Record what an event led to for one role. Returns the result id."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(RESULT_STATUSES)}")
    conn = _conn()
    try:
        row = conn.execute(
            "INSERT INTO ace_event_results (event_id, role_id, instance_id, status, step_id, "
            "grant_id, reason, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (int(event_id), role_id, instance_id or "", status, step_id or "", grant_id or "",
             reason or "", _now(), _now()),
        ).fetchone()
        conn.commit()
        return int(row[0]) if row else None
    finally:
        conn.close()


def update_result(result_id: int, status: str, *, instance_id: str = "",
                  reason: str = "") -> None:
    """Move a recommendation on (launched, declined, withdrawn). The step ledger
    (``wf_decision_steps``) is the immutable record; this row is the feed's view."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(RESULT_STATUSES)}")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE ace_event_results SET status = %s, instance_id = %s, reason = %s, "
            "updated_at = %s WHERE id = %s",
            (status, instance_id or "", reason or "", _now(), int(result_id)),
        )
        conn.commit()
    finally:
        conn.close()


_RESULT_COLS = ("id", "event_id", "role_id", "instance_id", "status", "step_id", "grant_id",
                "reason", "created_at", "updated_at", "topic", "source_canvas", "payload_json")


def _results(where: str, params: tuple, limit: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT r.id, r.event_id, r.role_id, r.instance_id, r.status, r.step_id, "
            "r.grant_id, r.reason, r.created_at, r.updated_at, e.topic, e.source_canvas, "
            "e.payload_json FROM ace_event_results r "
            "LEFT JOIN ace_events e ON e.id = r.event_id "
            f"{where} ORDER BY r.created_at DESC, r.id DESC LIMIT %s",  # nosec B608 — constants
            (*params, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(zip(_RESULT_COLS, tuple(row)))
        payload = json.loads(item.pop("payload_json") or "{}")
        item["payload_preview"] = str(payload.get("content") or "")[:120]
        for key in ("created_at", "updated_at"):
            item[key] = str(item[key] or "")
        out.append(item)
    return out


def get_recent_results(limit: int = 50) -> list[dict[str, Any]]:
    """Recent results, newest first, for the event feed. Raises when unreadable."""
    return _results("", (), limit)


def results_with_status(status: str, limit: int = 200) -> list[dict[str, Any]]:
    """Results in one status (the dispatcher sweeps its ``recommended`` rows)."""
    return _results("WHERE r.status = %s", (status,), limit)


def pending_count() -> int:
    """Unprocessed events. Raises when the log cannot be read."""
    conn = _conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM ace_events WHERE processed = 0").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()
