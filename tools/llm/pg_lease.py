"""Multi-host (cluster-wide) LLM concurrency lease via PostgreSQL advisory locks.

:mod:`tools.llm.cross_process_lease` caps calls across processes on ONE host
(file locks). When ICDEV runs on several hosts that share one API key, the cap
must span hosts too. Every host already shares the same PostgreSQL database, so
PG **advisory locks** are the natural coordinator — no new infrastructure.

Like file locks, a PG advisory lock is session-scoped: it auto-releases when the
connection ends, including on crash, so there is no stale lease and no reaper.
This module holds one dedicated autocommit connection per lease and grabs one of
``max_slots`` advisory-lock keys on it; the connection is held for the lease's
lifetime and closed on release (which frees the lock).

Fail-open by contract: if psycopg2 is missing, the DB is unreachable, or any
query errors, :func:`acquire` returns ``None`` and the caller proceeds under the
in-process cap alone — a cluster-lease problem must never hang an LLM call.

Scope: all hosts pointing at the same PostgreSQL. The connection is built from
the same ``ICDEV_DATABASE_URL`` / ``ICDEV_PG_*`` env (and SSL/session options)
that ``tools/db/storage.py`` uses, so it reaches the same database.
"""

from __future__ import annotations

import os
import time
import zlib
from typing import Callable, Optional


def _ns_key(name: str) -> int:
    """Stable signed-int4 namespace key for a lease name (advisory-lock key1)."""
    h = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h  # fold to signed int4


def _default_connect():
    """Open a dedicated autocommit psycopg2 connection to the shared PG.

    Reuses storage's SSL + session-option helpers so IL5 / mTLS deployments keep
    working. Raises on any failure — :func:`acquire` turns that into fail-open.
    """
    import psycopg2

    try:
        from tools.db.storage import _pg_session_options, _pg_ssl_kwargs
        ssl_kwargs = _pg_ssl_kwargs()
        options = _pg_session_options()
    except Exception:
        ssl_kwargs = {}
        options = ""

    timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
    db_url = (os.environ.get("ICDEV_DATABASE_URL") or "").strip()
    if db_url:
        conn = psycopg2.connect(db_url, connect_timeout=timeout, options=options, **ssl_kwargs)
    else:
        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "localhost"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            connect_timeout=timeout,
            options=options,
            **ssl_kwargs,
        )
    conn.autocommit = True
    return conn


class PgLease:
    """A held cluster-wide advisory-lock slot. Call :meth:`release`."""

    __slots__ = ("_conn", "_ns", "_slot")

    def __init__(self, conn, ns: int, slot: int):
        self._conn = conn
        self._ns = ns
        self._slot = slot

    def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (self._ns, self._slot))
        except Exception:
            pass  # closing the connection below releases the lock regardless
        try:
            conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def _fetch_bool(cur) -> bool:
    row = cur.fetchone()
    if row is None:
        return False
    # RealDictCursor -> dict; plain cursor -> tuple. Handle both.
    if isinstance(row, dict):
        return bool(next(iter(row.values())))
    return bool(row[0])


def acquire(
    name: str,
    max_slots: int,
    timeout: Optional[float] = None,
    poll: float = 0.1,
    connect: Optional[Callable[[], object]] = None,
) -> Optional[PgLease]:
    """Acquire one of *max_slots* cluster-wide slots named *name*.

    Holds a dedicated autocommit PG connection and tries ``pg_try_advisory_lock``
    on keys ``(ns, 0..max_slots-1)`` until one succeeds, polling every *poll* s
    until a slot frees or *timeout* elapses. ``timeout=None`` waits indefinitely
    (safe — the lock auto-releases if a holder's connection dies).

    Returns a :class:`PgLease` (caller MUST ``release()``), or ``None`` on
    timeout / missing psycopg2 / unreachable DB / any query error (fail-open).
    *connect* is injectable for testing.
    """
    if max_slots <= 0:
        return None
    connect = connect or _default_connect
    ns = _ns_key(name)

    try:
        conn = connect()
    except Exception:
        return None  # psycopg2 missing or DB unreachable -> in-process cap only

    deadline = None if timeout is None else (time.monotonic() + max(0.0, timeout))
    try:
        cur = conn.cursor()
        while True:
            for i in range(max_slots):
                cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (ns, i))
                if _fetch_bool(cur):
                    return PgLease(conn, ns, i)
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(poll)
    except Exception:
        pass  # any error -> fail open
    try:
        conn.close()
    except Exception:
        pass
    return None
