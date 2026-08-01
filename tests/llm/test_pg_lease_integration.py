"""Live-PostgreSQL integration test for the cluster-wide LLM lease.

Exercises REAL ``pg_try_advisory_lock`` across separate real connections — the
thing the fake-PG unit test (``test_pg_lease.py``) can only simulate.

ICDEV is PostgreSQL-primary; this test connects straight to the configured PG
(``ICDEV_PG_*`` / ``ICDEV_DATABASE_URL``), independent of the storage backend
the rest of the suite uses. It **skips** when PG is unreachable — e.g. the CI
``test`` job runs on SQLite with no PG service — so it adds real coverage in
every PG environment without breaking SQLite-only runs.
"""
import os

import pytest

from tools.llm import pg_lease


def _pg_available() -> bool:
    try:
        conn = pg_lease._default_connect()
    except Exception:
        return False
    try:
        conn.close()
    except Exception:
        pass
    return True


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not reachable (ICDEV_PG_*/ICDEV_DATABASE_URL) — cluster lease needs a live PG",
)

# Unique per test process so concurrent CI shards / dev runs don't collide on
# the same advisory-lock key namespace.
_NAME = f"__pg_lease_it_{os.getpid()}__"


def test_real_advisory_lock_serializes_single_slot():
    a = pg_lease.acquire(_NAME, 1, timeout=0)
    assert a is not None, "first real advisory lock should be granted"
    try:
        # A second, independent connection cannot take the one slot.
        b = pg_lease.acquire(_NAME, 1, timeout=0.5)
        assert b is None, "advisory lock failed to serialize across connections"
    finally:
        a.release()
    # Released — now available again (proves release() unlocked, not just closed).
    c = pg_lease.acquire(_NAME, 1, timeout=1.0)
    assert c is not None
    c.release()


def test_real_advisory_lock_two_slots():
    name = _NAME + "2"
    x = pg_lease.acquire(name, 2, timeout=0)
    y = pg_lease.acquire(name, 2, timeout=0)
    try:
        assert x is not None and y is not None, "two slots should admit two holders"
        z = pg_lease.acquire(name, 2, timeout=0.5)
        assert z is None, "third holder must block when both slots are taken"
    finally:
        if x:
            x.release()
        if y:
            y.release()


def test_real_release_closes_connection():
    a = pg_lease.acquire(_NAME + "3", 1, timeout=0)
    assert a is not None
    conn = a._conn
    a.release()
    assert conn.closed  # dedicated connection is closed on release
