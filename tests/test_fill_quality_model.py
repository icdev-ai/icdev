"""Tests for ml.fill_quality_model."""

import random
from datetime import datetime, timezone

import pytest

from tools.trading.ml import fill_quality_model as fqm


@pytest.fixture(autouse=True)
def _bootstrap():
    fqm._conn().close()
    from tools.trading.analytics.slippage_tracker import _conn as sc

    sc().close()

    from tools.db.storage import get_connection

    c = get_connection()
    for t in ("ad_fill_quality_models", "ad_orders", "ad_order_meta"):
        c.execute(f"DELETE FROM {t} WHERE 1=1")   # nosec B608
    c.commit()
    c.close()

    if fqm._MODEL_PATH.exists():
        fqm._MODEL_PATH.unlink()


def _seed_orders(n: int, base_slippage_bps: float = 15.0):
    from tools.db.storage import get_connection
    from tools.trading.analytics.slippage_tracker import record_expected

    random.seed(7)
    c = get_connection()
    for i in range(n):
        oid = f"ord-fqm-{i:04d}"
        expected = 100.0
        # Slippage encoded as fill-vs-expected with some noise
        slip_bps = base_slippage_bps + random.uniform(-5, 5)
        fill = expected * (1 + slip_bps / 10000.0)
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (oid, "pf", "ZZFQM", "buy", 10, "market", "filled", fill, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
    c.commit()
    c.close()
    # Record expected
    for i in range(n):
        record_expected(f"ord-fqm-{i:04d}", 100.0, strategy_id="default")


def test_insufficient_data_reports_status():
    _seed_orders(5)
    out = fqm.train(days=30)
    assert out["status"] == "insufficient_data"
    assert out["samples"] == 5


def test_trains_persists_and_predicts():
    _seed_orders(40)
    out = fqm.train(days=30)
    assert out["status"] == "trained"
    assert out["samples"] >= 30
    assert out["mae_bps"] < 20.0
    assert fqm._MODEL_PATH.exists()

    # Predict
    pred = fqm.predict_slippage_bps("AAPL", "buy", 10.0, vix=20.0)
    assert pred is not None
    assert -50 < pred < 100


def test_no_model_returns_none():
    # Ensure no model on disk
    if fqm._MODEL_PATH.exists():
        fqm._MODEL_PATH.unlink()
    assert fqm.predict_slippage_bps("AAPL", "buy", 10) is None


def test_should_skip_when_slippage_exceeds_alpha():
    _seed_orders(40, base_slippage_bps=25.0)
    fqm.train(days=30)
    out = fqm.should_skip("AAPL", "buy", 10, expected_alpha_bps=5.0, vix=25.0)
    assert out["skip"] is True
    assert out["reason"] == "predicted_slippage_exceeds_alpha"


def test_should_not_skip_when_slippage_within_budget():
    _seed_orders(40, base_slippage_bps=5.0)
    fqm.train(days=30)
    out = fqm.should_skip("AAPL", "buy", 10, expected_alpha_bps=50.0, vix=15.0)
    assert out["skip"] is False


def test_should_skip_handles_no_model():
    # No training → no model → no_model reason, skip=False
    if fqm._MODEL_PATH.exists():
        fqm._MODEL_PATH.unlink()
    out = fqm.should_skip("AAPL", "buy", 10, expected_alpha_bps=5)
    assert out["skip"] is False
    assert out["reason"] == "no_model"
