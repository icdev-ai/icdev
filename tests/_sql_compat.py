# CUI // SP-CTI
"""Placeholder-translating SQLite connections for tests.

This repo authors SQL for PostgreSQL — ``%s`` placeholders — and
``tools.db.storage.StorageConnection`` rewrites them to ``?`` when the backend
is SQLite. A test that patches ``get_connection`` with a bare
``sqlite3.connect`` removes that layer, so every statement the code under test
runs raises::

    sqlite3.OperationalError: near "%": syntax error

Where the caller wraps its work in a best-effort ``except`` — common for audit,
link and memory writes — nothing surfaces at all. The test then asserts against
a no-op it caused itself, and reads as a missing feature rather than a broken
fixture. That has accounted for 100+ failures across a dozen files.

Use :func:`translating` (wrap an existing connection) or :func:`connect`
(open one) instead of ``sqlite3.connect`` wherever the connection is handed to
production code.

Translation delegates to ``tools.db.storage.translate_sql`` — the same function
the runtime uses — so a test can never drift from the behaviour it stands in for.
"""
from __future__ import annotations

import sqlite3
from typing import Any

__all__ = ["TranslatingConnection", "TranslatingCursor", "translating", "connect"]


class TranslatingCursor:
    """Wraps a sqlite3 cursor, applying %s -> ? before every statement.

    Production code reaches for ``conn.cursor()`` as often as ``conn.execute``
    — ``tools/ci/pr_watcher.py::_enforced_done_ok`` is one of several. Without
    this, ``__getattr__`` handed back the *raw* sqlite3 cursor, which does not
    translate, so a ``%s`` query issued through it still raised
    ``near "%": syntax error`` inside the caller's ``except`` — the same silent
    no-op this module exists to prevent, one layer down.
    """

    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def execute(self, sql: str, params: Any = None):
        if params is None:
            self._cursor.execute(TranslatingConnection._t(sql))
        else:
            self._cursor.execute(TranslatingConnection._t(sql), params)
        return self

    def executemany(self, sql: str, seq: Any):
        self._cursor.executemany(TranslatingConnection._t(sql), seq)
        return self

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class TranslatingConnection:
    """Wraps a sqlite3 connection, applying %s -> ? before every statement.

    Everything not overridden here (commit, rollback, close, row_factory,
    executescript, …) delegates to the wrapped connection unchanged.
    """

    def __init__(self, conn: sqlite3.Connection, *, unclosable: bool = False):
        self._conn = conn
        self._unclosable = unclosable

    @staticmethod
    def _t(sql: str) -> str:
        from tools.db.storage import translate_sql

        return translate_sql(sql, backend="sqlite")

    def execute(self, sql: str, params: Any = None):
        if params is None:
            return self._conn.execute(self._t(sql))
        return self._conn.execute(self._t(sql), params)

    def executemany(self, sql: str, seq: Any):
        return self._conn.executemany(self._t(sql), seq)

    def cursor(self):
        return TranslatingCursor(self._conn.cursor())

    def close(self):
        # In-memory databases die with their connection, so a fixture that
        # shares one with code calling close() in a finally block needs this.
        if not self._unclosable:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Mirror StorageConnection.__exit__ exactly. Production code writes
        # through ``with get_connection() as conn:`` and never calls commit()
        # itself, so a no-op __exit__ leaves every INSERT in an uncommitted
        # transaction that the next connection cannot see. The test then reads
        # None back and looks like a missing feature.
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def translating(conn: sqlite3.Connection, *, unclosable: bool = False) -> TranslatingConnection:
    """Wrap an existing sqlite3 connection."""
    return TranslatingConnection(conn, unclosable=unclosable)


def connect(db_path, *, row_factory: bool = True, **kwargs) -> TranslatingConnection:
    """Open a translating connection to *db_path* (drop-in for sqlite3.connect)."""
    conn = sqlite3.connect(str(db_path), **kwargs)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return TranslatingConnection(conn)
