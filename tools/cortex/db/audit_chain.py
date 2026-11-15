# CUI // SP-CTI
"""cortex_audit hash chain and its database guards (icx-cnf-06).

``cortex_audit`` was append-only by declaration only: ``APPEND_ONLY_TABLES``
stops an agent's tool call, but the database took any UPDATE or DELETE, and no
row carried anything that could show it had been changed. This module adds the
two missing guarantees.

**Triggers.** BEFORE UPDATE and BEFORE DELETE on ``cortex_audit`` (and on its
genesis table) raise, on SQLite and on PostgreSQL. Dropping a trigger is DDL,
which the chain below still exposes.

**The chain.** Every row written through :func:`next_link` carries
``chain_seq`` (1, 2, 3, ... with no gaps), ``prev_hash`` (the previous row's
``row_hash``) and ``row_hash`` (a digest of the row's own fields, its
``chain_seq`` and its ``prev_hash``). Writes are serialized (an advisory lock on
PostgreSQL, ``BEGIN IMMEDIATE`` on SQLite) so two writers cannot claim the same
predecessor, and a UNIQUE index on ``chain_seq`` refuses a fork if they somehow
do. The digest uses :func:`tools.audit.row_hash.compute_payload_hash`, the
repo's one canonical-JSON digest, so nothing here invents a recipe.

**Genesis, not backfill.** Rows written before the chain keep NULL chain
columns and are never rewritten. The first time the chain is set up on a
database (the migration, or the first write on a database that never ran it),
one ``cortex_audit_chain_genesis`` row records how many unchained rows there
were and a digest over them. The first chained row's ``prev_hash`` is the
genesis hash. So :func:`verify` can check the old rows too: if one is altered,
removed, or an unchained row is added later, the prior digest no longer matches.

Reads that build or check the chain go through
:func:`tools.audit.chain.unfiltered_cursor`: the chain is one sequence across
tenants and classifications, and a writer that could not see its predecessor
would fork it. Only digests are read for linking; :func:`verify` reads rows to
recompute their digests and returns ids and reasons, never row content.

Usage:
    python -m tools.cortex.db.audit_chain --verify [--json]
    python -m tools.cortex.db.audit_chain --ensure [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cortex.audit_chain")

TABLE = "cortex_audit"
GENESIS_TABLE = "cortex_audit_chain_genesis"
LOCK_NAME = "cortex_audit:chain"
CHAIN_COLUMNS = (("chain_seq", "INTEGER"), ("prev_hash", "TEXT"), ("row_hash", "TEXT"))
#: The fields a row's digest covers, in the order they are rendered. Every
#: column a writer sets is here; the chain columns are added beside them.
HASH_FIELDS = ("id", "session_id", "tenant_id", "classification", "function", "agent_id",
               "user_id", "gates_json", "outcome", "blocked", "provenance_id", "created_at")
#: At most this many breaks are listed by :func:`verify`; the count is exact.
MAX_BREAKS = 20

GENESIS_DDL = f"""CREATE TABLE IF NOT EXISTS {GENESIS_TABLE} (
    id              TEXT PRIMARY KEY,
    genesis_hash    TEXT NOT NULL,
    prior_rows      INTEGER NOT NULL,
    prior_digest    TEXT NOT NULL,
    hash_algorithm  TEXT NOT NULL DEFAULT 'sha256',
    note            TEXT,
    created_at      TEXT NOT NULL
)"""

_GUARDED = (TABLE, GENESIS_TABLE)
_HAS_CHAIN: dict = {}


def _sqlite_triggers(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only (NIST AU-9)'); END",
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only (NIST AU-9)'); END",
    ]


def _pg_triggers(table: str) -> list[str]:
    # One statement each: a ';' split would cut the $$ body in two.
    return [
        f"""CREATE OR REPLACE FUNCTION {table}_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '{table} is append-only (NIST AU-9)';
END;
$$ LANGUAGE plpgsql""",
        f"DROP TRIGGER IF EXISTS {table}_no_update ON {table}",
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {table}_immutable()",
        f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {table}_immutable()",
    ]


# ── digests ──────────────────────────────────────────────────────────────────


def now_text() -> str:
    """UTC in the shape CURRENT_TIMESTAMP writes (``YYYY-MM-DD HH:MM:SS``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _render(field: str, value):
    """One field as the digest sees it, the same on SQLite and PostgreSQL.

    ``gates_json`` is TEXT on SQLite and JSONB on PostgreSQL, which re-orders
    keys, so it is digested as parsed JSON. ``blocked`` is 0/1 or a boolean.
    ``created_at`` comes back as text or as a datetime.
    """
    if field == "gates_json":
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return str(value)
    if field == "blocked":
        return bool(int(value or 0)) if not isinstance(value, bool) else value
    if field == "created_at" and isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return None if value is None else str(value)


def _fields(row: dict) -> dict:
    return {f: _render(f, row.get(f)) for f in HASH_FIELDS}


def row_digest(row: dict) -> str:
    """Digest of a row's own fields (used for the rows before genesis)."""
    from tools.audit.row_hash import compute_payload_hash

    return compute_payload_hash({"fields": _fields(row)})


def link_hash(row: dict, chain_seq: int, prev_hash: str) -> str:
    """``row_hash`` of a chained row: its fields, its place and its predecessor."""
    from tools.audit.row_hash import compute_payload_hash

    return compute_payload_hash({"fields": _fields(row), "chain_seq": int(chain_seq),
                                 "prev_hash": prev_hash})


def _genesis_hash(prior_rows: int, prior_digest: str, created_at: str) -> str:
    from tools.audit.row_hash import compute_payload_hash

    return compute_payload_hash({"table": TABLE, "prior_rows": int(prior_rows),
                                 "prior_digest": prior_digest, "created_at": created_at})


# ── reads ────────────────────────────────────────────────────────────────────


def _cursor(conn):
    from tools.audit.chain import unfiltered_cursor

    return unfiltered_cursor(conn)


def _dicts(cur, columns) -> list[dict]:
    return [dict(zip(columns, tuple(r))) for r in cur.fetchall()]


def _db_identity(conn) -> tuple:
    if getattr(conn, "_backend", "sqlite") == "postgresql":
        return ("postgresql",)
    try:
        return ("sqlite", str(tuple(_cursor(conn).execute("PRAGMA database_list").fetchone())[2]))
    except Exception:  # noqa: BLE001
        return ("sqlite", "")


def has_chain(conn) -> bool:
    """Whether ``cortex_audit`` here has the chain columns and the genesis table.

    A yes is cached per database (the probe is four catalog reads, too many for
    every audit write); a no is asked again next time, so a migration that runs
    while the process is up is picked up on the next write.
    """
    from tools.db.storage import column_exists, table_exists

    key = _db_identity(conn)
    if _HAS_CHAIN.get(key):
        return True
    try:
        present = (table_exists(conn, GENESIS_TABLE)
                   and all(column_exists(conn, TABLE, c) for c, _ in CHAIN_COLUMNS))
    except Exception:  # noqa: BLE001 — an unanswerable probe is "no chain", never a guess
        return False
    if present:
        _HAS_CHAIN[key] = True
    return present


def _prior(cur) -> tuple[int, str]:
    """(count, digest) of the rows written before the chain, in id order."""
    from tools.audit.row_hash import compute_payload_hash

    cur.execute(f"SELECT {', '.join(HASH_FIELDS)} FROM {TABLE} "  # nosec B608 — constants
                "WHERE chain_seq IS NULL")
    # Sorted here, not by ORDER BY: a text collation differs between databases.
    rows = sorted(_dicts(cur, HASH_FIELDS), key=lambda r: str(r["id"]))
    digests = [row_digest(r) for r in rows]
    return len(digests), compute_payload_hash(digests)


def genesis(conn) -> Optional[dict]:
    """The recorded genesis row, or None."""
    cols = ("id", "genesis_hash", "prior_rows", "prior_digest", "hash_algorithm", "note",
            "created_at")
    try:
        cur = _cursor(conn)
        cur.execute(f"SELECT {', '.join(cols)} FROM {GENESIS_TABLE} "  # nosec B608
                    "ORDER BY created_at, id")
        rows = _dicts(cur, cols)
    except Exception:  # noqa: BLE001 — no table is "no genesis"
        return None
    return rows[0] if rows else None


# ── setup ────────────────────────────────────────────────────────────────────


def _serialize(conn) -> None:
    from tools.audit.chain import serialize_chain_writes

    serialize_chain_writes(conn, LOCK_NAME)


def _record_genesis(conn) -> Optional[dict]:
    """Write the genesis row if the chain has not started. Caller holds the lock."""
    existing = genesis(conn)
    if existing:
        return existing
    cur = _cursor(conn)
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE chain_seq IS NOT NULL")  # nosec B608
    if int(tuple(cur.fetchone())[0] or 0):
        # Chained rows with no genesis: the genesis was removed. Never paper over
        # that with a new one; verify() reports it.
        return None
    prior_rows, prior_digest = _prior(cur)
    created_at = now_text()
    ghash = _genesis_hash(prior_rows, prior_digest, created_at)
    row = {"id": f"cag-{ghash[:16]}", "genesis_hash": ghash, "prior_rows": prior_rows,
           "prior_digest": prior_digest, "hash_algorithm": "sha256",
           "note": (f"cortex_audit chain starts here (icx-cnf-06). {prior_rows} earlier row(s) "
                    "keep NULL chain columns and are covered by prior_digest."),
           "created_at": created_at}
    cur.execute(
        f"INSERT INTO {GENESIS_TABLE} (id, genesis_hash, prior_rows, prior_digest, "  # nosec B608
        "hash_algorithm, note, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        tuple(row[k] for k in ("id", "genesis_hash", "prior_rows", "prior_digest",
                               "hash_algorithm", "note", "created_at")))
    return row


def _triggers_present(conn, table: str) -> set:
    from tools.db.storage import is_pg

    cur = _cursor(conn)
    if is_pg(conn):
        cur.execute("SELECT trigger_name FROM information_schema.triggers "
                    "WHERE event_object_table = %s", (table,))
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = %s",
                    (table,))
    return {str(tuple(r)[0]).lower() for r in cur.fetchall()}


def ensure_chain(conn) -> dict:
    """Add the chain columns, the genesis table, the genesis row and the triggers.

    Idempotent; existing rows are never touched. Commits. Called by the
    migration and, once per process, by ``init_db._ensure_schema`` before the
    first audit write, so a database that never ran the migration still gets
    the chain rather than silently going without it.
    """
    from tools.db.storage import column_exists, is_pg

    pg = is_pg(conn)
    added = []
    for column, dtype in CHAIN_COLUMNS:
        if not column_exists(conn, TABLE, column):
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} {dtype}")
            added.append(column)
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_chain_seq "
                 f"ON {TABLE}(chain_seq)")
    conn.execute(GENESIS_DDL)
    conn.commit()

    _serialize(conn)
    gen = _record_genesis(conn)
    conn.commit()

    installed = []
    for table in _GUARDED:
        if {f"{table}_no_update", f"{table}_no_delete"} <= _triggers_present(conn, table):
            continue
        for stmt in (_pg_triggers(table) if pg else _sqlite_triggers(table)):
            conn.execute(stmt)
        installed.append(table)
    conn.commit()
    return {"columns_added": added, "genesis": (gen or {}).get("id"),
            "triggers_installed": installed}


# ── write ────────────────────────────────────────────────────────────────────


def next_link(conn, row: dict) -> Optional[tuple]:
    """``(chain_seq, prev_hash, row_hash)`` for ``row``, about to be inserted on ``conn``.

    Takes the chain lock on ``conn``; the caller's INSERT and COMMIT release it.
    Returns None when this database has no chain (the caller then writes the
    row unchained: an audit row is never dropped for want of a link).
    """
    if not has_chain(conn):
        return None
    _serialize(conn)
    cur = _cursor(conn)
    cur.execute(f"SELECT chain_seq, row_hash FROM {TABLE} "  # nosec B608
                "WHERE chain_seq IS NOT NULL ORDER BY chain_seq DESC LIMIT 1")
    last = cur.fetchone()
    if last is not None:
        seq, prev = int(tuple(last)[0]) + 1, str(tuple(last)[1] or "")
    else:
        gen = _record_genesis(conn)
        if gen is None:
            return None
        seq, prev = 1, gen["genesis_hash"]
    return seq, prev, link_hash(row, seq, prev)


# ── verify ───────────────────────────────────────────────────────────────────


def verify(conn) -> dict:
    """Walk the chain and say whether it is intact. Reads only; never raises.

    ``status`` is ``intact``, ``broken``, ``not_started`` (chain columns but no
    genesis and no chained row) or ``absent`` (no chain columns). ``breaks``
    lists the first :data:`MAX_BREAKS` problems as ``{id, chain_seq, reason}``;
    ``break_count`` is exact. ``prior`` compares the rows before genesis with
    the digest the genesis recorded.
    """
    out = {"table": TABLE, "status": "absent", "chained_rows": 0, "unchained_rows": 0,
           "genesis_id": "", "prior": None, "breaks": [], "break_count": 0,
           "first_break_id": "", "latest_seq": 0}
    if not has_chain(conn):
        return out
    breaks: list[dict] = []
    try:
        cur = _cursor(conn)
        gen = genesis(conn)
        cols = HASH_FIELDS + ("chain_seq", "prev_hash", "row_hash")
        cur.execute(f"SELECT {', '.join(cols)} FROM {TABLE} "  # nosec B608
                    "WHERE chain_seq IS NOT NULL ORDER BY chain_seq")
        rows = _dicts(cur, cols)
        prior_rows, prior_digest = _prior(cur)
    except Exception as exc:  # noqa: BLE001
        out.update(status="broken", breaks=[{"id": "", "chain_seq": None,
                                              "reason": f"chain unreadable: {exc}"}],
                   break_count=1)
        return out
    out.update(chained_rows=len(rows), unchained_rows=prior_rows,
               latest_seq=int(rows[-1]["chain_seq"]) if rows else 0)
    if gen is None:
        if rows:
            breaks.append({"id": rows[0]["id"], "chain_seq": rows[0]["chain_seq"],
                           "reason": "chained rows exist but the genesis row is missing"})
        else:
            out["status"] = "not_started"
            return out
    else:
        out["genesis_id"] = gen["id"]
        expected = _genesis_hash(gen["prior_rows"], gen["prior_digest"], gen["created_at"])
        if expected != gen["genesis_hash"]:
            breaks.append({"id": gen["id"], "chain_seq": 0,
                           "reason": "genesis row does not hash to its genesis_hash"})
        out["prior"] = {"recorded_rows": int(gen["prior_rows"]), "found_rows": prior_rows,
                        "intact": prior_digest == gen["prior_digest"]}
        if prior_digest != gen["prior_digest"]:
            reason = ("rows written before the chain were altered or removed"
                      if prior_rows <= int(gen["prior_rows"]) else
                      f"{prior_rows - int(gen['prior_rows'])} row(s) written without the "
                      "chain since genesis, or earlier rows altered")
            breaks.append({"id": gen["id"], "chain_seq": 0, "reason": reason})
    prev = gen["genesis_hash"] if gen else ""
    for n, r in enumerate(rows, start=1):
        seq = int(r["chain_seq"])
        if seq != n:
            breaks.append({"id": r["id"], "chain_seq": seq,
                           "reason": f"expected chain_seq {n}: a row is missing before it"})
        if gen is not None and r["prev_hash"] != prev:
            breaks.append({"id": r["id"], "chain_seq": seq,
                           "reason": "prev_hash does not match the previous row"})
        if link_hash(r, seq, r["prev_hash"] or "") != r["row_hash"]:
            breaks.append({"id": r["id"], "chain_seq": seq,
                           "reason": "row does not hash to its row_hash (altered)"})
        prev = r["row_hash"]
    out["break_count"] = len(breaks)
    out["breaks"] = breaks[:MAX_BREAKS]
    out["first_break_id"] = breaks[0]["id"] if breaks else ""
    out["status"] = "broken" if breaks else "intact"
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="cortex_audit hash chain")
    ap.add_argument("--verify", action="store_true", help="Walk the chain and report breaks")
    ap.add_argument("--ensure", action="store_true",
                    help="Add chain columns, genesis and triggers (idempotent)")
    ap.add_argument("--json", action="store_true", help="JSON output (the default)")
    args = ap.parse_args(argv)
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        if args.ensure:
            out = ensure_chain(conn)
        else:
            out = verify(conn)
    finally:
        conn.close()
    print(json.dumps(out, indent=2, default=str))
    return 0 if args.ensure or out["status"] in ("intact", "not_started") else 1


if __name__ == "__main__":
    sys.exit(main())
