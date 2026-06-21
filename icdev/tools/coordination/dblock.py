# CUI // SP-CTI
"""PostgreSQL advisory locks for cross-process critical sections.

Use to serialize work that MUST NOT run from two processes at once — schema
migrations (the `ALTER TABLE … ADD COLUMN` storm), or a hot-table critical
section. Advisory locks are cooperative + automatic: they release when the
holding DB session ends, so a crashed holder never deadlocks the others.

    from tools.coordination import dblock
    with dblock.advisory_lock("migrations:schema"):
        run_pending_migrations()        # only one process here at a time

On non-PostgreSQL backends (e.g. the SQLite test harness) this degrades to a
no-op — there is no shared server to contend on.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from typing import Iterator, Optional

from tools.coordination.constants import ADVISORY_SALT

try:
    from tools.db.storage import get_connection
except Exception:  # pragma: no cover
    get_connection = None  # type: ignore[assignment]


def _is_postgres() -> bool:
    return os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() == "postgresql"


def lock_key(name: str) -> int:
    """Map a lock name to a stable signed 63-bit int for pg_advisory_lock."""
    h = hashlib.sha256(f"{ADVISORY_SALT}:{name}".encode("utf-8")).digest()
    # 63 bits → always positive, fits PostgreSQL bigint.
    return int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _new_conn():
    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: infra lock, no tenant context
    except Exception:
        pass
    return conn


def try_acquire(name: str):
    """Non-blocking acquire. Returns the holding connection on success, else None.

    Caller MUST pass the returned connection to ``release()`` to free the lock.
    """
    if not _is_postgres() or get_connection is None:
        return _NULL_HANDLE
    key = lock_key(name)
    conn = _new_conn()
    try:
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,)).fetchone()
        got = (dict(row).get("got") if hasattr(row, "keys") else row[0]) if row else False
        if got:
            return conn
        conn.close()
        return None
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return None


def release(handle, name: str) -> None:
    """Release a lock acquired via try_acquire()."""
    if handle is None or handle is _NULL_HANDLE:
        return
    try:
        handle.execute("SELECT pg_advisory_unlock(%s)", (lock_key(name),))
        handle.commit()
    except Exception:
        pass
    finally:
        try:
            handle.close()
        except Exception:
            pass


@contextlib.contextmanager
def advisory_lock(name: str, timeout_ms: Optional[int] = 10000) -> Iterator[bool]:
    """Blocking advisory lock context manager.

    Yields True once held (or on a non-PG backend). Respects lock_timeout so a
    contended lock fails fast instead of hanging; on timeout raises RuntimeError.
    Always unlocks + closes on exit.
    """
    if not _is_postgres() or get_connection is None:
        yield True
        return
    key = lock_key(name)
    conn = _new_conn()
    held = False
    try:
        if timeout_ms is not None:
            try:
                conn.execute(f"SET LOCAL lock_timeout = {int(timeout_ms)}")
            except Exception:
                pass
        conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        held = True
        yield True
    finally:
        if held:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
                conn.commit()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


# Sentinel returned on non-PG backends so try_acquire() is truthy (lock "held").
class _NullHandle:
    def close(self):  # noqa: D401
        pass


_NULL_HANDLE = _NullHandle()
