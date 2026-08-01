# CUI // SP-CTI
"""Migration 310 — reconcile studio_trigger_events with the canonical schema.

The live PostgreSQL table matched NO schema source in this tree. It carried
``match_reason`` / ``evaluated_at`` where both ``tools/studio/init_db.py`` and
migration 304 define ``reason`` / ``received_at``, and it was missing all six
columns migration 308 (dwo-evt-02) adds. Nothing in the repository creates it
that way — ``match_reason`` appears only in an unrelated aiify module — so it was
created ad hoc by a worker session before 304 landed, and every subsequent
``CREATE TABLE IF NOT EXISTS`` silently accepted the divergence.

The consequence was invisible and total: ``event_sources.log_trigger_event()``
writes ``reason`` and ``received_at``, so every INSERT raised, the exception was
swallowed by design (an audit write must not break ingest), and the function
returned "". The trigger audit trail — the table whose entire purpose is
answering "why did this run start" — recorded nothing on the primary backend,
while the SQLite fallback and every test looked fine.

This migration is corrective rather than additive:

* renames match_reason -> reason and evaluated_at -> received_at when the old
  names are present and the new ones are not, preserving existing rows;
* adds any of the dwo-evt-02 columns that are absent.

Both halves are conditional and idempotent, so it is safe on a database already
matching the canonical schema (adds nothing), on the drifted one (repairs it),
and on a fresh install (no-ops after init_db). Column existence is checked
rather than assumed because SQLite cannot express ADD COLUMN IF NOT EXISTS and
RENAME COLUMN fails hard on a missing source column.
"""
from tools.db.storage import get_connection, is_pg

TABLE = "studio_trigger_events"

#: old -> new. Values that already carry the new name are left alone.
_RENAMES = (("match_reason", "reason"), ("evaluated_at", "received_at"))

#: dwo-evt-02 dispatch columns, added when absent. idempotency_key carries the
#: replay guard; it is indexed separately below rather than declared UNIQUE
#: inline, because ADD COLUMN cannot add a table constraint on either backend.
_ADDITIONS = (
    ("workflow_id", "TEXT"),
    ("outcome", "TEXT"),
    ("classification", "TEXT"),
    ("idempotency_key", "TEXT"),
    ("envelope_id", "TEXT"),
)


def _columns(conn) -> set:
    """Column names on the live table, or an empty set if it does not exist."""
    try:
        if is_pg():
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (TABLE,),
            ).fetchall()
            return {dict(r)["column_name"] for r in rows}
        rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
        return {dict(r).get("name") for r in rows}
    except Exception:
        return set()


def up(conn=None) -> None:
    conn = get_connection()
    try:
        cols = _columns(conn)
        if not cols:
            return  # table not created yet; init_db/304 will build it correctly

        for old, new in _RENAMES:
            if old in cols and new not in cols:
                conn.execute(f"ALTER TABLE {TABLE} RENAME COLUMN {old} TO {new}")
                cols.discard(old)
                cols.add(new)

        for name, ddl_type in _ADDITIONS:
            if name not in cols:
                conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl_type}")
                cols.add(name)

        # The replay guard. Partial on PG so the many rows with no delivery id
        # do not collide; SQLite treats NULLs as distinct in a UNIQUE index
        # already, so the plain form is equivalent there.
        try:
            if is_pg():
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE}_idem "
                    f"ON {TABLE} (idempotency_key) WHERE idempotency_key IS NOT NULL"
                )
            else:
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE}_idem "
                    f"ON {TABLE} (idempotency_key)"
                )
        except Exception:
            # A pre-existing duplicate would block the unique index. Losing the
            # replay guard is bad; failing the migration and leaving the audit
            # trail broken is worse. The guard is re-attempted on next run.
            pass

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    up()
    print("Migration 310 applied.")
