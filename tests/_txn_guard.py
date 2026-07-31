"""Connection registry backing the transaction-leak guard (tsh-leak-01).

Lives outside conftest.py deliberately: pytest loads conftest under its own
module name, so `import tests.conftest` produces a *second* module object with
its own module-level state. Anything the guard's fixtures and the tests that
cover them must agree on has to live in a normally-importable module.

The fixtures themselves are in tests/conftest.py.
"""
import os
import sqlite3

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


def track(conn):
    """Register a freshly opened connection with the guard."""
    TRACKED_CONNECTIONS.append(conn)
    return conn


def reset():
    """Forget every tracked connection (called around each test)."""
    TRACKED_CONNECTIONS.clear()


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
