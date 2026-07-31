"""Connection registry backing the transaction-leak guard (tsh-leak-01).

Lives outside conftest.py deliberately: pytest loads conftest under its own
module name, so `import tests.conftest` produces a *second* module object with
its own module-level state. Anything the guard's fixtures and the tests that
cover them must agree on has to live in a normally-importable module.

The fixtures themselves are in tests/conftest.py.
"""
import os
import sqlite3
import threading
import traceback

#: Disable the guard for a whole run: ICDEV_TXN_LEAK_GUARD=0
GUARD_DISABLED = os.environ.get("ICDEV_TXN_LEAK_GUARD", "1").strip().lower() in (
    "0",
    "false",
    "no",
    "off",
)

# sqlite3.Connection is not weak-referenceable, so the registry holds strong
# refs and is cleared around every test. That bounds retention to one test's
# worth of connections — keeping them for the whole session would pin file
# handles across 31k tests. The trade-off: a connection opened in an earlier
# test (or by a module-scoped fixture) and left in a transaction by a later one
# is not attributed, since only connections opened during the test are tracked.
TRACKED_CONNECTIONS = []

#: id(conn) -> (thread_name, "file:line in func" frames) captured at open time.
#: Reporting only the test that FINISHED with a transaction open blames the
#: wrong test whenever a background thread opens the connection: the registry is
#: cleared per test, so a thread that opens one mid-test gets attributed to
#: whichever test happened to be running. tests/cortex/test_client.py::
#: test_connection_refused_returns_none was reported as leaking while passing in
#: isolation and touching no database at all -- it is a pure HTTP test against a
#: refused port.
CONNECTION_ORIGINS = {}

#: How many frames of the opening stack to keep. Enough to identify the caller
#: without turning a failure message into a wall of pytest internals.
_ORIGIN_FRAMES = 6


def track(conn):
    """Register a freshly opened connection, remembering who opened it."""
    TRACKED_CONNECTIONS.append(conn)
    try:
        # `limit` keeps the innermost frames only, which is both what we want to
        # show and much cheaper: this runs on every sqlite3.connect in the
        # suite, and walking a full ~40-frame pytest stack costs roughly 5x
        # extracting the handful we keep. The two extra frames are this
        # function and the conftest connect wrapper, dropped by the slice.
        frames = traceback.extract_stack(limit=_ORIGIN_FRAMES + 2)[:-2]
        CONNECTION_ORIGINS[id(conn)] = (
            threading.current_thread().name,
            [f"{f.filename.rsplit(os.sep, 1)[-1]}:{f.lineno} in {f.name}" for f in frames],
        )
    except Exception:  # noqa: BLE001 - diagnostics must never break a test
        pass
    return conn


def origin_of(conn):
    """Return ``(thread_name, frames)`` for *conn*, or None if not recorded."""
    return CONNECTION_ORIGINS.get(id(conn))


def describe_origin(conn) -> str:
    """One-line-per-frame description of where *conn* was opened."""
    origin = origin_of(conn)
    if not origin:
        # The connection never passed through track(), so it was opened before
        # the session fixture patched sqlite3.connect — or by a path that does
        # not use sqlite3.connect at all. That is a DIFFERENT defect from a test
        # leaking its own transaction, and worth saying so rather than printing
        # a blank.
        return (
            "        (opened before the tracker was installed, or not via "
            "sqlite3.connect — not attributable to this test)"
        )
    thread_name, frames = origin
    header = f"        opened on thread {thread_name!r}:"
    return "\n".join([header] + [f"          {f}" for f in frames])


def reset():
    """Forget every tracked connection (called around each test)."""
    TRACKED_CONNECTIONS.clear()
    CONNECTION_ORIGINS.clear()


def open_write_transactions():
    """Return tracked connections with an uncommitted write transaction.

    `Connection.in_transaction` is False for plain SELECTs under the default
    isolation level — it flips to True only once DML has opened an implicit
    BEGIN — so this reports write transactions specifically. Connections that
    are already closed raise ProgrammingError on attribute access and are, by
    definition, not leaking.
    """
    leaked = []
    for conn in list(TRACKED_CONNECTIONS):
        try:
            if conn.in_transaction:
                leaked.append(conn)
        except sqlite3.Error:
            continue
    return leaked
