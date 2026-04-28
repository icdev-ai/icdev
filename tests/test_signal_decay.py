"""Unit tests for tools/trading/signal_decay.py."""

import math
import os
import sqlite3


from tools.trading.signal_decay import (
    DECAY_LAMBDA,
    WEIGHT_FLOOR,
    batch_decay,
    compute_signal_decay,
)


def test_decay_at_age_zero():
    """weight must be 1.0 when age_days=0."""
    assert compute_signal_decay(0) == 1.0


def test_decay_at_age_14_lambda_005():
    """e^(-0.05 * 14) = e^(-0.7) ≈ 0.496."""
    expected = math.exp(-0.05 * 14)
    result = compute_signal_decay(14, decay_lambda=0.05)
    assert abs(result - expected) < 1e-9
    assert 0.495 < result < 0.498


def test_weight_floor_large_age():
    """age=1000 must be clamped at WEIGHT_FLOOR (0.01)."""
    result = compute_signal_decay(1000)
    assert result >= WEIGHT_FLOOR
    assert result == WEIGHT_FLOOR


def test_get_signals_ranked_ordering():
    """get_signals(ranked=True) orders by composite_score * signal_decay_weight DESC."""
    os.environ["ICDEV_FATHOMDESK_FORCE_SCHEMA_CHECK"] = "1"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE ad_signals (
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
    conn.execute(
        "INSERT INTO ad_signals (id, ticker, direction, composite_score, confidence,"
        " signal_decay_weight, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-new", "AAPL", "long", 0.8, 0.9, 1.0, "2026-04-28 10:00:00"),
    )
    conn.execute(
        "INSERT INTO ad_signals (id, ticker, direction, composite_score, confidence,"
        " signal_decay_weight, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-old", "AAPL", "long", 0.8, 0.9, 0.3, "2026-04-27 10:00:00"),
    )
    conn.commit()

    from tools.trading.db import get_signals  # noqa: PLC0415

    results = get_signals(ranked=True, conn=conn)
    ids = [r["id"] for r in results]
    # sig-new (0.8*1.0=0.8) must rank above sig-old (0.8*0.3=0.24)
    assert ids.index("sig-new") < ids.index("sig-old")


def test_batch_decay_updates_three_signals():
    """batch_decay applies decay to all 3 signals and returns the updated list."""
    signals = [
        {"id": "s1", "age_days": 0},
        {"id": "s2", "age_days": 14},
        {"id": "s3", "age_days": 1000},
    ]
    result = batch_decay(signals)

    assert len(result) == 3
    assert result[0]["signal_decay_weight"] == 1.0
    assert abs(result[1]["signal_decay_weight"] - math.exp(-DECAY_LAMBDA * 14)) < 1e-9
    assert result[2]["signal_decay_weight"] == WEIGHT_FLOOR
