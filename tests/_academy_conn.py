# CUI // SP-CTI
"""A SQLite connection that translates SQL the way production does.

The academy's runtime SQL is authored PG-native (`%s`), per the CLAUDE.md rule that
translate_sql is an init-time fallback and must never be load-bearing. Production
reaches the database through storage.StorageConnection, which rewrites `%s` to `?`
when the backend is SQLite.

A bare `sqlite3.connect()` in a test skips that wrapper entirely, so the test would
be exercising a dialect the application never actually sends — PG-native SQL would
fail against it while working perfectly in production, and, worse, SQLite-dialect SQL
would pass a test while failing against the real backend. Wrapping here keeps the
test on the same path as the app.
"""
from __future__ import annotations

import sqlite3

from tools.db.storage import StorageConnection


def academy_conn(target: str = ":memory:") -> StorageConnection:
    """Wrap a SQLite connection in the same translator production uses.

    row_factory is set on the underlying connection because StorageConnection does
    not forward attribute writes — setting it on the wrapper would silently leave
    rows as plain tuples.
    """
    raw = sqlite3.connect(target)
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")
