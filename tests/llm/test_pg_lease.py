"""Cluster-wide LLM lease via PG advisory locks — tested with a fake PG.

Real ``pg_try_advisory_lock`` needs a live PostgreSQL (the standard suite runs
on SQLite), so we inject a fake connection factory that simulates advisory-lock
semantics with a shared in-memory set. This covers the slot-iteration, hold,
release, timeout, and fail-open logic without a DB.
"""

from tools.llm import pg_lease


class _FakeCursor:
    def __init__(self, held: set):
        self._held = held
        self._last = None

    def execute(self, sql, params):
        ns, slot = params
        if "pg_try_advisory_lock" in sql:
            key = (ns, slot)
            if key in self._held:
                self._last = False
            else:
                self._held.add(key)
                self._last = True
        elif "pg_advisory_unlock" in sql:
            self._held.discard((ns, slot))
            self._last = True

    def fetchone(self):
        return (self._last,)


class _FakeConn:
    def __init__(self, held: set):
        self._held = held
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._held)

    def close(self):
        self.closed = True


def _factory(held):
    return lambda: _FakeConn(held)


def test_ns_key_is_signed_int4_and_stable():
    k1 = pg_lease._ns_key("llm")
    k2 = pg_lease._ns_key("llm")
    assert k1 == k2  # stable
    assert -(2**31) <= k1 < 2**31  # fits signed int4
    assert pg_lease._ns_key("other") != k1  # distinct names -> distinct keys


def test_zero_slots_returns_none():
    assert pg_lease.acquire("x", 0, timeout=0, connect=_factory(set())) is None


def test_single_slot_mutual_exclusion():
    held = set()  # one shared "PG" across all simulated connections
    a = pg_lease.acquire("t", 1, timeout=0, connect=_factory(held))
    assert a is not None
    # slot 0 already locked cluster-wide -> second acquire times out
    b = pg_lease.acquire("t", 1, timeout=0.2, connect=_factory(held))
    assert b is None
    a.release()
    c = pg_lease.acquire("t", 1, timeout=0.3, connect=_factory(held))
    assert c is not None
    c.release()


def test_two_slots_allow_two_then_block():
    held = set()
    a = pg_lease.acquire("t2", 2, timeout=0, connect=_factory(held))
    b = pg_lease.acquire("t2", 2, timeout=0, connect=_factory(held))
    assert a is not None and b is not None
    c = pg_lease.acquire("t2", 2, timeout=0.2, connect=_factory(held))
    assert c is None
    a.release()
    d = pg_lease.acquire("t2", 2, timeout=0.3, connect=_factory(held))
    assert d is not None
    b.release()
    d.release()


def test_release_closes_connection_and_frees_slot():
    held = set()
    a = pg_lease.acquire("close", 1, timeout=0, connect=_factory(held))
    conn = a._conn
    a.release()
    assert conn.closed is True
    assert held == set()  # unlock ran


def test_failopen_when_connect_raises():
    def _boom():
        raise RuntimeError("DB unreachable")

    # any connection failure -> None (in-process cap only), never raises
    assert pg_lease.acquire("t", 1, timeout=0, connect=_boom) is None


def test_context_manager():
    held = set()
    with pg_lease.acquire("cm", 1, timeout=0, connect=_factory(held)) as lease:
        assert lease is not None
        assert pg_lease.acquire("cm", 1, timeout=0.1, connect=_factory(held)) is None
    # released on exit
    again = pg_lease.acquire("cm", 1, timeout=0.2, connect=_factory(held))
    assert again is not None
    again.release()
