#!/usr/bin/env python3
# CUI // SP-CTI
"""The audit hash chain writer — populates hash / previous_hash / signature.

Migration 149 added ``hash``, ``previous_hash`` and ``signature`` to
``audit_trail``, but nothing ever wrote them. Every row carried NULL, so
``provenance_verifier.verify_audit_integrity`` reported ``hash_valid=False``
on every row (honestly fail-closed, but useless), and
``chain_anchor.periodic_anchor``'s ``WHERE hash IS NULL OR signature IS NULL``
matched the whole table forever. This module is the writer half.

The digest recipe itself lives in :mod:`tools.audit.row_hash` (exa-audit-01) and
is NOT restated here — writer and both verifiers must agree byte for byte.

Three properties make the chain verifiable, and all three are load-bearing:

**1. The id is part of the digest, so it must be reserved before the INSERT.**
``AUDIT_HASH_FIELDS`` begins with ``id``. A row cannot be hashed after an
autoincrement assigns its id, because ``audit_trail`` is append-only — there is
no UPDATE to come back and fill the hash in. So the id is drawn from the
sequence first and inserted explicitly.

**2. previous_hash is defined as "the hash of the row at id - 1".**
That is not an arbitrary choice; it is the rule
``verify_audit_integrity`` already applies when it checks a link. Writer and
verifier therefore agree by construction, including at a gap: if row ``id-1``
does not exist (a burned sequence value from a rolled-back write) or carries a
NULL hash (a row written before cutover, or one whose chain columns failed),
BOTH sides fall back to ``GENESIS_HASH``. A gap restarts the chain rather than
faking a break. Deleting a chained row is still detected, because the row after
it holds that row's real hash and would no longer match.

**3. Writes are serialized.**
Two concurrent writers that both read the same ``id-1`` hash and both claim to
be its successor produce a fork, and one of them is wrong. Reserve, read-back
and INSERT therefore run inside a critical section: ``pg_advisory_xact_lock``
on PostgreSQL (released automatically at COMMIT — a crashed writer cannot wedge
the table) and ``BEGIN IMMEDIATE`` on SQLite. The lock is per-connection, not a
separate connection as in :mod:`tools.coordination.dblock`, so an audit write
costs no extra session; only the key derivation is shared with that module.

Cutover, not backfill: rows written before this module existed keep their NULL
hashes and are never rewritten. The id of the first chained row is recorded once
in ``audit_chain_genesis`` so a verifier can tell "written before the chain
existed" from "chained and broken" — see :func:`chain_start_id`.

**log_event is not the only writer, so expect a sparse chain.** 156 files under
``tools/`` issue their own ``INSERT INTO audit_trail`` naming explicit columns,
none of which include the chain columns; 154 go through ``log_event``. Every
unchained row lands between two chained ones and restarts the chain at
GENESIS — which is why the gap-tolerant "row at id - 1" rule above is not a
nicety but the thing that keeps those restarts from reading as tampering.
Routing those call sites through the writer is follow-on work (exa-audit-04
surfaces the count as chain health); nothing here depends on it having happened.

Usage:
    from icdev.tools.audit.chain import chain_insert_values
"""

import contextlib
import json
import sqlite3
import threading

from icdev.tools.audit.row_hash import GENESIS_HASH, compute_audit_row_hash
from tools.logging.icdev_logger import get_logger

logger = get_logger("audit.chain")

#: Lock name for the audit-chain critical section. Hashed to a bigint by
#: dblock.lock_key so it shares the salt/namespace of every other advisory lock.
CHAIN_LOCK_NAME = "audit_trail:chain"

#: The three columns migration 149 added, in the order this module writes them.
CHAIN_COLUMNS = ("hash", "previous_hash", "signature")

#: How long a SQLite writer waits for the write lock before giving up. Without
#: this, concurrent writers raise "database is locked" instead of queueing, and
#: the serialization that makes the chain correct would instead make it lossy.
SQLITE_BUSY_TIMEOUT_MS = 10000

# has_chain_columns() hits information_schema / PRAGMA, which is far too slow to
# repeat on every audit write. Cached per (backend, database identity); the set
# of columns on a table only changes when a migration runs, which restarts or at
# least outlives the process far more often than not.
_COLUMN_CACHE: dict = {}
_COLUMN_CACHE_LOCK = threading.Lock()

# Whether the cutover marker has already been written for a given database, so
# record_chain_start stops issuing a COUNT(*) on every single audit write. Only
# ever set to True after the row is confirmed present, so a stale entry cannot
# cause a missing marker.
_GENESIS_CACHE: dict = {}


def _backend(conn) -> str:
    """'postgresql' or 'sqlite', for both StorageConnection and raw sqlite3."""
    return getattr(conn, "_backend", "sqlite") or "sqlite"


def _unfiltered_cursor(conn):
    """A cursor with RLS predicate injection switched off.

    rls-bypass: the chain reads back only the SHA-256 digest of the preceding
    row, never its content, and it must read the same row the verifier will.
    With a security context attached, `classification IN (<dominated set>)` is
    injected into the read; a writer running under a CUI request context would
    then not see a SECRET predecessor, fall back to GENESIS_HASH, and produce a
    row that a cleared verifier reports as a broken link. Chain topology is a
    structural property of the table and is deliberately not classification
    filtered. A digest is one-way, so no content crosses the boundary.
    """
    cur = conn.cursor()
    try:
        # A writer that cannot see a higher-classification predecessor would
        # chain from GENESIS, and a cleared verifier would then read that row as
        # a broken link. Only the SHA-256 digest is read, never row content.
        cur.set_security_context(None)  # rls-bypass: chain topology is not classification-scoped — required for exa-audit-03
    except AttributeError:
        pass  # raw sqlite3 cursor — no RLS layer to disable
    return cur


@contextlib.contextmanager
def _contained(conn):
    """Run statements whose failure must not poison the caller's transaction.

    On PostgreSQL a failed statement aborts the whole transaction: every
    later statement raises "current transaction is aborted" until someone
    rolls back. Both places this module touches ``audit_chain_genesis`` can
    legitimately fail — the table does not exist until migration
    20260812041301 has run, which is the normal state of any deployment
    between merging this code and running migrations — and neither is worth
    breaking the caller over.

    A plain ``conn.rollback()`` would clear the abort but also discard whatever
    uncommitted work a caller-owned connection was in the middle of, so this
    uses a SAVEPOINT and unwinds only its own statements. On SQLite a failed
    statement does not abort the transaction, so there is nothing to contain.
    """
    name = "icdev_audit_chain_probe"
    contained = _backend(conn) == "postgresql"
    if contained:
        try:
            conn.cursor().execute(f"SAVEPOINT {name}")
        except Exception:
            contained = False
    try:
        yield
    except Exception:
        if contained:
            try:
                conn.cursor().execute(f"ROLLBACK TO SAVEPOINT {name}")
            except Exception:
                pass
        raise
    else:
        if contained:
            try:
                conn.cursor().execute(f"RELEASE SAVEPOINT {name}")
            except Exception:
                pass


def _scalar(row):
    """First column of a row, for sqlite3.Row / psycopg row / plain tuple."""
    if row is None:
        return None
    try:
        return row[0]
    except (KeyError, IndexError, TypeError):
        return None


def has_chain_columns(conn) -> bool:
    """Whether this database's audit_trail actually has the migration-149 columns.

    Not every database has run migration 149: ``init_icdev_db.py``'s CREATE TABLE
    still omits the three columns, so a freshly initialised SQLite database has
    an audit_trail without them. Naming a column that is not in the LIVE schema
    is the swallowed-INSERT failure this repo keeps hitting, so the writer asks
    rather than assumes, and degrades to the unchained 9-column INSERT.
    """
    key = (_backend(conn), id(conn.__class__), _db_identity(conn))
    with _COLUMN_CACHE_LOCK:
        if key in _COLUMN_CACHE:
            return _COLUMN_CACHE[key]
    present = _probe_chain_columns(conn)
    if present is None:
        # The probe could not answer — on PostgreSQL an already-aborted
        # transaction makes EVERY query fail, including one against
        # information_schema, so a table that exists reads as missing. Caching
        # that would silently un-chain every subsequent write on this backend
        # for the life of the process. Degrade for this row only, and ask again
        # next time.
        return False
    with _COLUMN_CACHE_LOCK:
        _COLUMN_CACHE[key] = present
    return present


def _db_identity(conn) -> str:
    """A stable-enough label for which database this connection points at."""
    if _backend(conn) == "postgresql":
        return "pg"
    try:
        cur = conn.cursor()
        row = cur.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row else "sqlite"
    except Exception:
        return "sqlite"


def _probe_chain_columns(conn):
    """True/False if the probe ran; None if it could not answer at all."""
    backend = _backend(conn)
    try:
        with _contained(conn):
            cur = _unfiltered_cursor(conn)
            if backend == "postgresql":
                row = cur.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_name = 'audit_trail' "
                    "AND column_name IN ('hash', 'previous_hash', 'signature')"
                ).fetchone()
                return int(_scalar(row) or 0) == len(CHAIN_COLUMNS)
            cols = {r[1] for r in cur.execute("PRAGMA table_info(audit_trail)").fetchall()}
            return set(CHAIN_COLUMNS).issubset(cols)
    except Exception:
        return None


def serialize_chain_writes(conn) -> None:
    """Enter the audit-chain critical section on this connection.

    PostgreSQL takes a transaction-scoped advisory lock, which the next COMMIT
    or ROLLBACK releases — there is no unlock path to leak and no way for a
    crashed writer to hold it. SQLite escalates to a write lock immediately, so
    two writers cannot both read the same predecessor.

    Best effort by design: if the lock cannot be taken the write still proceeds.
    Losing the audit event is worse than a chain that has to report a fork, and
    a fork is visible to the verifier whereas a dropped event is not.
    """
    backend = _backend(conn)
    try:
        if backend == "postgresql":
            from tools.coordination.dblock import lock_key

            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(%s)", (lock_key(CHAIN_LOCK_NAME),)
            )
            return
        cur = conn.cursor()
        cur.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        try:
            cur.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # Already inside a transaction the caller opened. Their write lock
            # is what serializes us, so this is success, not failure.
            pass
    except Exception:
        pass


def reserve_entry_id(conn, placeholder: str = "?"):
    """Draw the id this row will be inserted under, before computing its hash.

    PostgreSQL pulls from audit_trail's own sequence so that rows inserted by
    code paths which do NOT chain keep getting distinct ids. SQLite has no
    sequence to draw from outside an INSERT, so the high-water mark is read
    under the write lock taken by serialize_chain_writes.

    Returns None when no id could be reserved, which sends the caller down the
    unchained path rather than guessing an id that may already be taken.
    """
    try:
        cur = _unfiltered_cursor(conn)
        if _backend(conn) == "postgresql":
            row = cur.execute(
                "SELECT nextval(pg_get_serial_sequence('audit_trail', 'id'))"
            ).fetchone()
            reserved = _scalar(row)
            if reserved is not None:
                return int(reserved)
            # No sequence attached (id is a plain integer column). Safe under
            # the advisory lock, which no other chained writer can hold.
            row = cur.execute("SELECT COALESCE(MAX(id), 0) FROM audit_trail").fetchone()
            return int(_scalar(row) or 0) + 1

        high = int(_scalar(cur.execute("SELECT COALESCE(MAX(id), 0) FROM audit_trail").fetchone()) or 0)
        try:
            # AUTOINCREMENT keeps a high-water mark that survives deletes, and
            # reusing an id it has already handed out would collide.
            seq = _scalar(
                cur.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'audit_trail'"
                ).fetchone()
            )
            if seq is not None:
                high = max(high, int(seq))
        except Exception:
            pass  # no sqlite_sequence: table was not declared AUTOINCREMENT
        return high + 1
    except Exception:
        return None


def previous_hash_for(conn, entry_id: int, placeholder: str = "?") -> str:
    """The previous_hash for a row about to be written at ``entry_id``.

    Defined as the hash of the row at ``entry_id - 1``, falling back to
    GENESIS_HASH when that row is absent or unchained. This is deliberately the
    same rule verify_audit_integrity applies, so the two cannot disagree.
    """
    if entry_id is None or entry_id <= 1:
        return GENESIS_HASH
    try:
        cur = _unfiltered_cursor(conn)
        row = cur.execute(
            f"SELECT hash FROM audit_trail WHERE id = {placeholder}",
            (entry_id - 1,),
        ).fetchone()
    except Exception:
        return GENESIS_HASH
    if row is None:
        return GENESIS_HASH
    try:
        prev = row["hash"] if hasattr(row, "keys") else row[0]
    except (KeyError, IndexError, TypeError):
        prev = None
    return prev or GENESIS_HASH


def sign_row_hash(row_hash: str) -> str:
    """Sign a row hash, returning the JSON blob stored in audit_trail.signature.

    Signs the hex digest exactly as verify_audit_integrity verifies it —
    ``verify_payload(row["hash"].encode(), ...)``. Signing is delegated to
    tools.crypto.key_manager (ECDSA-P256, then Ed25519, then an HMAC fallback),
    the same signer attestation_signer.py and sbom_signer.py use; this module
    adds no signing path of its own.

    With no key and no HMAC secret configured, key_manager returns
    ``algorithm: "none"``, which the verifier scores as signature_valid=False.
    That is recorded rather than hidden: an unsigned chain still detects
    tampering by an actor who cannot recompute hashes, and claiming a signature
    that does not exist would be worse than reporting none.
    """
    from tools.crypto.key_manager import sign_payload

    return json.dumps(sign_payload(row_hash.encode("utf-8")))


def chain_insert_values(conn, entry_id: int, row: dict, placeholder: str = "?") -> tuple:
    """Compute (hash, previous_hash, signature) for a row about to be inserted.

    ``row`` must hold the values as they will land in the columns — in
    particular ``details`` already JSON-serialised — because the verifier hashes
    what it reads back out of the database.
    """
    previous_hash = previous_hash_for(conn, entry_id, placeholder)
    row_hash = compute_audit_row_hash(dict(row, id=entry_id))
    return row_hash, previous_hash, sign_row_hash(row_hash)


# ---------------------------------------------------------------------------
# Cutover marker
# ---------------------------------------------------------------------------
def record_chain_start(conn, entry_id: int, placeholder: str = "?") -> None:
    """Record, once, the id of the first row ever written with a hash.

    Backfill is out of scope: rows older than this id have NULL hashes because
    no writer existed, not because anyone removed them. Without a recorded
    cutover a verifier cannot tell those two apart, and a table of rows that all
    report "invalid" is indistinguishable from a table that was tampered with.

    Deriving the boundary as ``MIN(id) WHERE hash IS NOT NULL`` would move
    silently if the first chained row were ever removed — which is precisely the
    event the marker exists to expose. So it is written down.
    """
    key = (_backend(conn), _db_identity(conn))
    if _GENESIS_CACHE.get(key):
        return  # already recorded — do not re-query on every audit write
    try:
        with _contained(conn):
            cur = _unfiltered_cursor(conn)
            existing = cur.execute("SELECT COUNT(*) FROM audit_chain_genesis").fetchone()
            if int(_scalar(existing) or 0) > 0:
                _GENESIS_CACHE[key] = True
                return
            cur.execute(
                f"INSERT INTO audit_chain_genesis (chain_start_id, hash_algorithm, note) "
                f"VALUES ({placeholder}, {placeholder}, {placeholder})",
                (
                    entry_id,
                    "sha256",
                    "First audit_trail row written with hash/previous_hash/signature "
                    "(exa-audit-03). Rows below this id predate the chain writer and "
                    "are unverifiable, not tampered.",
                ),
            )
        _GENESIS_CACHE[key] = True
    except Exception as exc:
        # The marker is diagnostic. A database that has not run the migration
        # yet must still be able to write audit rows, and the row this call
        # follows is already committed — losing the marker costs a diagnostic,
        # losing the caller's transaction would cost their business logic.
        #
        # Logged rather than silent: if this fails FOREVER the chain still
        # works, but every pre-cutover row keeps reading as "chained and
        # unverified" instead of "predates the chain", which is the confusion
        # the marker exists to remove.
        logger.warning(
            "audit chain cutover marker not recorded for id %s (%s); "
            "run migration 20260812041301 if audit_chain_genesis is missing",
            entry_id,
            exc,
        )


def chain_start_id(conn):
    """The lowest audit_trail id that the chain writer produced, or None.

    None means the cutover has not happened in this database — either the
    migration has not run or nothing has been written since. A verifier should
    then treat every unchained row as "no chain here", not as a broken one.
    """
    try:
        with _contained(conn):
            row = _unfiltered_cursor(conn).execute(
                "SELECT MIN(chain_start_id) FROM audit_chain_genesis"
            ).fetchone()
        value = _scalar(row)
        return int(value) if value is not None else None
    except Exception:
        # No table yet (migration not run) is the common case and reads the
        # same as "no cutover recorded", which is the honest answer.
        return None
