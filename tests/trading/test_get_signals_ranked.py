"""Unit tests for get_signals(ranked=True) in tools/trading/db.py."""

import sqlite3
import os
import pytest

from tools.db.storage import StorageConnection

# Force schema re-init per test process
os.environ["ICDEV_FATHOMDESK_FORCE_SCHEMA_CHECK"] = "1"


@pytest.fixture()
def mem_conn(monkeypatch):
    """In-memory SQLite connection wired into trading db.

    db.get_signals() is authored PG-native (`%s` placeholders) and expects a
    StorageConnection (its type hint), which translates `%s`->`?` for SQLite.
    Passing a raw sqlite3 connection raised `near "%": syntax error`; wrap the
    in-memory DB in StorageConnection so the real translation path runs.
    Runtime is PostgreSQL; SQLite is the conftest-forced test backend only.
    """

    # Build a minimal in-memory DB with the required schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ad_signals (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            composite_score REAL NOT NULL,
            confidence REAL NOT NULL,
            component_scores TEXT,
            run_id TEXT,
            status TEXT DEFAULT 'pending',
            signal_decay_weight REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            actioned_at TEXT
        )"""
    )
    conn.commit()

    # Insert two signals with same score but different decay weights
    conn.execute(
        "INSERT INTO ad_signals (id, ticker, direction, composite_score, confidence, signal_decay_weight, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-new", "AAPL", "long", 0.8, 0.9, 1.0, "2026-04-28 10:00:00"),
    )
    conn.execute(
        "INSERT INTO ad_signals (id, ticker, direction, composite_score, confidence, signal_decay_weight, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-old", "AAPL", "long", 0.8, 0.9, 0.3, "2026-04-27 10:00:00"),
    )
    conn.commit()
    # Hand db.get_signals() the same translating wrapper it sees in production
    # (raw sqlite3 rejects the %s placeholders).
    return StorageConnection(conn, "sqlite")


def test_ranked_true_orders_by_score_times_decay(mem_conn):
    from tools.trading.db import get_signals

    results = get_signals(ranked=True, conn=mem_conn)
    assert len(results) >= 2
    ids = [r["id"] for r in results]
    # sig-new (0.8*1.0=0.8) must rank above sig-old (0.8*0.3=0.24)
    assert ids.index("sig-new") < ids.index("sig-old")


def test_ranked_false_preserves_created_at_order(mem_conn):
    from tools.trading.db import get_signals

    results = get_signals(ranked=False, conn=mem_conn)
    assert len(results) >= 2
    ids = [r["id"] for r in results]
    # Default ORDER BY created_at DESC → sig-new (2026-04-28) first
    assert ids.index("sig-new") < ids.index("sig-old")


def test_default_ranked_false_backward_compat(mem_conn):
    from tools.trading.db import get_signals

    results_default = get_signals(conn=mem_conn)
    results_explicit = get_signals(ranked=False, conn=mem_conn)
    assert [r["id"] for r in results_default] == [r["id"] for r in results_explicit]
