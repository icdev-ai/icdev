# CUI // SP-CTI
"""Migration 20261115114944 — ACE event dispatch: recommendations and auto-dispatch grants (icx-ace-01).

``ace_event_results`` records what an ACE event led to for each listening role.
It gains four columns: ``step_id`` (the pending ``decl-wft-ace-run-launch`` step
a recommendation waits on), ``grant_id`` (the auto-dispatch grant an
auto-launch ran under), ``reason`` and ``updated_at``.

``ace_dispatch_grants`` is the append-only log of auto-dispatch grants: granted,
revoked, suspended (trust fell), expired, stop_all, resume_all. A grant's state
is derived from its events; nothing is updated in place. Immutable through a
PostgreSQL trigger function (or SQLite RAISE(ABORT) triggers) on UPDATE and
DELETE. The CHECK is derived from ``tools/ace/db/init_db.GRANT_EVENTS``.

Every statement converges on a re-run. The PostgreSQL trigger message is
concatenated, not a RAISE format string (the storage layer rewrites ``%``).
ACE tables are canvas tables; on SQLite they may live in their own file, so a
missing ``ace_event_results`` is skipped here and ``tools/ace/db/init_db.init``
adds the columns there.
"""
from __future__ import annotations

_TAG = "[20261115114944_ace_event_dispatch]"

COLUMNS = (
    ("step_id", "TEXT NOT NULL DEFAULT ''"),
    ("grant_id", "TEXT NOT NULL DEFAULT ''"),
    ("reason", "TEXT NOT NULL DEFAULT ''"),
    ("updated_at", "TEXT NOT NULL DEFAULT ''"),
)


def _grant_events() -> tuple[str, ...]:
    from tools.ace.db.init_db import GRANT_EVENTS

    return GRANT_EVENTS


def _grants_ddl() -> tuple[str, ...]:
    check = ", ".join(f"'{e}'" for e in _grant_events())
    return (
        f"""CREATE TABLE IF NOT EXISTS ace_dispatch_grants (
    event_id        TEXT PRIMARY KEY,
    grant_id        TEXT NOT NULL,
    event           TEXT NOT NULL CHECK(event IN ({check})),
    role_id         TEXT NOT NULL DEFAULT '',
    topic           TEXT NOT NULL DEFAULT '',
    expires_at      TEXT NOT NULL DEFAULT '',
    max_per_day     INTEGER NOT NULL DEFAULT 0,
    reason          TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL DEFAULT '',
    actor_role      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
)""",
        "CREATE INDEX IF NOT EXISTS idx_ace_dispatch_grants_grant ON ace_dispatch_grants (grant_id)",
        "CREATE INDEX IF NOT EXISTS idx_ace_event_results_status ON ace_event_results (status)",
    )


PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION ace_dispatch_grants_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is append-only';
END;
$$ LANGUAGE plpgsql""",
) + tuple(
    stmt
    for op in ("update", "delete")
    for stmt in (
        f"DROP TRIGGER IF EXISTS ace_dispatch_grants_no_{op} ON ace_dispatch_grants",
        (f"CREATE TRIGGER ace_dispatch_grants_no_{op} BEFORE {op.upper()} ON ace_dispatch_grants "
         "FOR EACH ROW EXECUTE FUNCTION ace_dispatch_grants_immutable()"),
    )
)

SQLITE_TRIGGERS = tuple(
    (f"CREATE TRIGGER IF NOT EXISTS ace_dispatch_grants_no_{op} BEFORE {op.upper()} "
     "ON ace_dispatch_grants BEGIN SELECT RAISE(ABORT, 'ace_dispatch_grants is append-only'); END")
    for op in ("update", "delete")
)


def _is_pg(conn) -> bool:
    from tools.db.storage import is_pg

    return is_pg(conn)


def _sqlite_columns(conn, table: str) -> set[str] | None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {tuple(r)[1] for r in rows} if rows else None


def up(conn) -> dict:
    pg = _is_pg(conn)
    added: list[str] = []
    if pg:
        for name, decl in COLUMNS:
            conn.execute(f"ALTER TABLE ace_event_results ADD COLUMN IF NOT EXISTS {name} {decl}")
            added.append(name)
        ddl = _grants_ddl()
    else:
        have = _sqlite_columns(conn, "ace_event_results")
        if have is not None:
            for name, decl in COLUMNS:
                if name not in have:
                    conn.execute(f"ALTER TABLE ace_event_results ADD COLUMN {name} {decl}")
                    added.append(name)
            ddl = _grants_ddl()
        else:
            ddl = _grants_ddl()[:2]  # no results table in this file: init_db makes it
    for stmt in ddl:
        conn.execute(stmt)
    for stmt in PG_TRIGGERS if pg else SQLITE_TRIGGERS:
        conn.execute(stmt)
    conn.commit()
    print(f"{_TAG} ace_event_results +{len(added)} column(s); ace_dispatch_grants append-only")
    return {"status": "applied", "columns": added, "tables": ["ace_dispatch_grants"]}
