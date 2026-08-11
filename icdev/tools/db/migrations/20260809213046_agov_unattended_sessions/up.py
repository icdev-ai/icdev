#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260809213046: the per-session unattended flag (agov-inbox-04).

Two changes, one idea. ``unattended`` is a **routing** setting: it decides where
an approval ask is delivered, never what the agent is allowed to do. It has to
be durable because a flag that dies with the process is not a flag — an
overnight run that restarts would silently drop back to a console approver that
denies on EOF, and the operator would see an agent that "randomly" refuses.

``agent_unattended_sessions``
    One row per session id. MUTABLE on purpose (an operator turns it on and off)
    and deliberately NOT in ``APPEND_ONLY_TABLES``: the state is the current
    answer to "where do this session's asks go", and the history of who flipped
    it lives in the append-only ``hook_events`` trail that
    :mod:`tools.agent_runtime.unattended` writes on every change.

``agent_cron_jobs.unattended``
    A cron job has no console at all, so the flag belongs on the job row itself
    rather than being re-supplied at every fire. Added with the column-presence
    guard rather than a bare ``ALTER``, because ``cron.py`` self-creates its
    schema with ``CREATE TABLE IF NOT EXISTS``, which never ALTERs an existing
    table — a DB provisioned by migration 289 has the old column set, and an
    INSERT naming a phantom column is exactly the silent failure CLAUDE.md warns
    about.

``agent_cron_jobs`` may legitimately be absent (it is also created at runtime by
``cron.py::_ensure_schema``), so its half self-skips rather than aborting the
chain — the same posture as migration 020.
"""

MIGRATION_ID = "20260809213046"
MIGRATION_NAME = "agov_unattended_sessions"
DESCRIPTION = "Per-session unattended flag: agent_unattended_sessions + agent_cron_jobs.unattended"

TABLE = "agent_unattended_sessions"

# TEXT-only and dialect-neutral, matching migrations 287-289. ``tenant_id`` and
# ``classification`` are first class so the table is RLS-eligible through
# get_connection() like every other agent_runtime table.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    session_id     TEXT PRIMARY KEY,
    unattended     INTEGER NOT NULL DEFAULT 0,
    source         TEXT,
    actor          TEXT,
    reason         TEXT,
    inbox          TEXT,
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    set_at         TEXT,
    updated_at     TEXT
)
"""

_INDEX = (
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_unattended "
    f"ON {TABLE} (unattended)"
)


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _has_column(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        return (
            conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=? AND column_name=?",
                (table, column),
            ).fetchone()
            is not None
        )
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(
        (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == column
        for r in rows
    )


def up(conn) -> dict:
    actions = []

    conn.execute(_DDL)
    actions.append(f"created_{TABLE}")
    try:
        conn.execute(_INDEX)
        actions.append("created_index")
    except Exception as exc:  # noqa: BLE001 — an index is an optimisation
        actions.append(f"index_skipped: {exc}")

    if not _table_exists(conn, "agent_cron_jobs"):
        # Created at runtime by tools/agent_runtime/cron.py::_ensure_schema on a
        # migrate-only fresh DB. Its DDL already carries the column.
        actions.append("agent_cron_jobs absent — column add skipped")
    elif _has_column(conn, "agent_cron_jobs", "unattended"):
        actions.append("agent_cron_jobs.unattended already present")
    else:
        conn.execute(
            "ALTER TABLE agent_cron_jobs ADD COLUMN unattended INTEGER DEFAULT 0"
        )
        actions.append("added_agent_cron_jobs_unattended")

    conn.commit()
    return {"status": "applied", "actions": actions}
