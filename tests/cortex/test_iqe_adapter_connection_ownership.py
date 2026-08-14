# CUI // SP-CTI
"""IQE Cortex adapters must not close a connection they did not open.

``Executor._fetch_union`` / ``_fetch_join`` fetch several collections IN PARALLEL
over ONE caller-supplied connection (``analyst._ask_iqe`` opens it). The adapters
closed it unconditionally in ``finally``, so adapter #1 closed the connection out
from under adapter #2 while it was still executing. #2 raised, the bare
``except Exception: return []`` swallowed it with no log, and the union returned
HALF ITS ROWS presented as a complete answer — on the collections an operator
uses to audit Cortex itself.

Both halves are asserted: ownership (a caller-supplied connection survives) and
honesty (a failure is not reported as an empty result set).
"""
from __future__ import annotations

import importlib

import pytest

adapters = importlib.import_module("tools.iqe.adapters.cortex")

_ADAPTERS = [
    ("cortex.chat_sessions", "sessions_adapter"),
    ("cortex.audit", "audit_adapter"),
    ("cortex.search_history", "search_history_adapter"),
]


class _Cursor:
    description = (("a",), ("b",))

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """Records close() and refuses to execute once closed, like a real one."""

    def __init__(self, rows=None, fail=False):
        self.closed = False
        self.execute_calls = 0
        self._rows = rows if rows is not None else [(1, 2)]
        self._fail = fail

    def execute(self, sql, params=None):
        if self.closed:
            raise RuntimeError("connection already closed")
        self.execute_calls += 1
        if self._fail:
            raise RuntimeError("relation does not exist")
        return _Cursor(self._rows)

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,fn_name", _ADAPTERS)
def test_a_caller_supplied_connection_is_not_closed(name, fn_name):
    conn = _Conn()

    getattr(adapters, fn_name)(conn)

    assert not conn.closed, (
        f"{name} closed a connection it did not open — a sibling fetch sharing "
        f"it on another thread would raise mid-query"
    )


def test_a_shared_connection_survives_every_adapter_in_sequence():
    """Reproduces the union case: several collections, one connection."""
    conn = _Conn()

    for _name, fn_name in _ADAPTERS:
        getattr(adapters, fn_name)(conn)

    assert not conn.closed
    assert conn.execute_calls == len(_ADAPTERS), (
        "a later adapter did not get to execute — the connection was closed "
        "under it"
    )


@pytest.mark.parametrize("name,fn_name", _ADAPTERS)
def test_a_self_opened_connection_is_closed(name, fn_name, monkeypatch):
    """The other half of ownership: don't leak the one we did open."""
    opened = _Conn()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: opened)

    getattr(adapters, fn_name)(None)

    assert opened.closed, f"{name} leaked the connection it opened itself"


# ---------------------------------------------------------------------------
# Honesty — a failure is not an empty result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,fn_name", _ADAPTERS)
def test_a_failure_is_raised_not_returned_as_no_rows(name, fn_name):
    conn = _Conn(fail=True)

    with pytest.raises(RuntimeError):
        getattr(adapters, fn_name)(conn)


@pytest.mark.parametrize("name,fn_name", _ADAPTERS)
def test_a_failure_is_logged(name, fn_name, caplog):
    conn = _Conn(fail=True)
    adapters.logger.propagate = True

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        getattr(adapters, fn_name)(conn)

    assert any(name in r.getMessage() for r in caplog.records), (
        "the failure was not logged; a silent failure is how half a union went "
        "missing without anything going red"
    )


@pytest.mark.parametrize("name,fn_name", _ADAPTERS)
def test_an_empty_collection_still_returns_empty(name, fn_name):
    """Honesty must not become noise: genuinely no rows is still []."""
    conn = _Conn(rows=[])

    assert getattr(adapters, fn_name)(conn) == []
    assert not conn.closed


# ---------------------------------------------------------------------------
# Truncation is observable (ctx-trust-04 — partial; see the module docstring)
# ---------------------------------------------------------------------------


def test_hitting_the_row_cap_logs_a_warning(caplog):
    """The executor filters AFTER this cap, so a capped scan is a wrong answer.

    Predicate pushdown is the real fix and needs an Executor API change; until
    then a truncated scan must at least be visible rather than silent.
    """
    conn = _Conn(rows=[(1, 2)] * 5)
    adapters.logger.propagate = True

    with caplog.at_level("WARNING"):
        adapters.audit_adapter(conn, limit=5)

    assert any("cap" in r.getMessage() for r in caplog.records)


def test_below_the_cap_logs_nothing(caplog):
    conn = _Conn(rows=[(1, 2)])
    adapters.logger.propagate = True

    with caplog.at_level("WARNING"):
        adapters.audit_adapter(conn, limit=5)

    assert not [r for r in caplog.records if "cap" in r.getMessage()]
