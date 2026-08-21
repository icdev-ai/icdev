"""Migration — a live process records WHICH CODE it is running.

`agent_sessions` already holds one row per live process, heartbeated and reaped
by TTL: pid, host, started_at, last_heartbeat. It was missing the only thing
that answers "is the code doing this work the code that was merged".

WHY THIS TABLE AND NOT A NEW ONE. The two other candidates are the wrong GRAIN,
not merely already-taken:

  genesis_reflex_state  PRIMARY KEY (reflex_name) — one row per reflex
                        DEFINITION, shared across restarts and hosts. It
                        structurally cannot hold a per-process fact; two
                        daemons running different code would overwrite each
                        other's identity.
  heartbeat_checks      one row per check_type, same problem one layer over.

A second liveness surface beside `agent_sessions` would then need its own
registration, heartbeat, TTL and reaper, and the two would disagree the first
time one of them was wired somewhere the other was not.

FOUR COLUMNS, AND WHY NOT FEWER:

  module               the entry point — a pid is meaningless after it exits,
                       and is reused
  code_version         the commit the tree was at when the process booted.
                       NULL means UNKNOWN and must never read as current
  code_version_source  git | env | unavailable. A bare NULL cannot distinguish
                       "there is no git on this host" from "git was unreadable"
  code_dirty           1/0/NULL. A commit alone OVERSTATES what is known: a
                       process booted from a modified tree is not running the
                       tree that SHA names

EXISTING ROWS ARE LEFT NULL, DELIBERATELY. A process registered before this
migration genuinely has no recorded identity, and defaulting it to the current
HEAD would assert that every process already running is up to date — inventing
precisely the reassurance this card exists to refuse. `processes` reports those
rows as `unknown`.

The table is self-creating in tools/coordination/session_registry.py, so a
database that has never registered a session has no table yet. That case is a
no-op here: the module's own DDL carries these columns for fresh databases, and
creating the table from this migration would fork the DDL into two places.
"""
# CUI // SP-CTI

#: The identity columns this migration adds, in declaration order. Each is
#: nullable: a process that cannot determine its code version must be able to
#: register anyway, reporting unknown, rather than fail to register at all.
COLUMNS = (
    ("module", "TEXT"),
    ("code_version", "TEXT"),
    ("code_version_source", "TEXT"),
    ("code_dirty", "INTEGER"),
)


def _columns(conn, table: str) -> set:
    """Live column names for *table*, on either backend.

    Reads the catalogue rather than trusting the DDL: `CREATE TABLE IF NOT
    EXISTS` never alters an existing table, so the shipped DDL and the deployed
    shape drift apart (CLAUDE.md, INSERT/schema parity).
    """
    backend = getattr(conn, "_backend", None) or ""
    if "postgres" in str(backend).lower():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    out = set()
    for r in rows:
        d = dict(r)
        out.add(d.get("name") or list(d.values())[1])
    return out


def up(conn):
    existing = _columns(conn, "agent_sessions")
    if not existing:
        # No session has ever registered on this database, so the self-creating
        # DDL in session_registry.py has not run. It carries these columns, and
        # creating the table here would fork the definition.
        return

    for name, sql_type in COLUMNS:
        if name not in existing:
            conn.execute(
                f"ALTER TABLE agent_sessions ADD COLUMN {name} {sql_type}"
            )
    conn.commit()
