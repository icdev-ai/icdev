"""Tests for analytics.slippage_tracker + strategy_attribution."""

from datetime import datetime, timezone

import pytest

from tools.trading.analytics import slippage_tracker, strategy_attribution


@pytest.fixture(autouse=True)
def _bootstrap():
    from tools.trading.audit.trade_audit import _conn as audit_conn
    from tools.trading.db import get_conn

    a = audit_conn()
    a.commit()
    a.close()
    c = get_conn()
    c.commit()
    c.close()
    slippage_tracker._conn().close()


def _insert_filled_order(order_id, ticker, side, qty, fill_price, expected_price, strategy_id, when=None):
    from tools.db.storage import get_connection

    when = when or datetime.now(timezone.utc).isoformat()
    c = get_connection()
    c.execute("DELETE FROM ad_orders WHERE id = ?", (order_id,))
    c.execute(
        "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, "pf-test", ticker, side, qty, "market", "filled", fill_price, None, when, when),
    )
    c.commit()
    c.close()
    slippage_tracker.record_expected(order_id, expected_price, strategy_id=strategy_id)


def test_slippage_buy_paying_more_is_positive_bps():
    _insert_filled_order("slip-buy-1", "AAPL", "buy", 10, 102.0, 100.0, "value")
    rows = slippage_tracker.per_order(days=1, ticker="AAPL")
    target = [r for r in rows if r["id"] == "slip-buy-1"]
    assert target and target[0]["slippage_bps"] == 200.0  # 2% = 200 bps


def test_slippage_sell_above_expected_is_negative_bps():
    _insert_filled_order("slip-sell-1", "MSFT", "sell", 5, 105.0, 100.0, "value")
    rows = slippage_tracker.per_order(days=1, ticker="MSFT")
    target = [r for r in rows if r["id"] == "slip-sell-1"]
    # Sell got 105 vs expected 100 — that's GOOD for trader, so normalized negative
    assert target and target[0]["slippage_bps"] == -500.0


def test_slippage_summary_alerts_above_threshold():
    _insert_filled_order("slip-bad-1", "ZZBAD", "buy", 1, 110.0, 100.0, "noisy")  # 1000 bps
    out = slippage_tracker.summary(days=1, alert_bps=30)
    assert out["status"] == "alert"
    assert out["filled_count"] >= 1


def test_attribution_realized_pnl_per_strategy():
    _insert_filled_order("attr-buy-1", "GOOGL", "buy", 10, 100.0, 100.0, "growth")
    _insert_filled_order("attr-sell-1", "GOOGL", "sell", 10, 110.0, 110.0, "growth")
    out = strategy_attribution.attribution(days=2)
    growth = [s for s in out["strategies"] if s["strategy_id"] == "growth"]
    assert growth
    assert growth[0]["realized_pnl"] == 100.0   # (110 - 100) * 10
    assert growth[0]["fill_count"] >= 2


def test_attribution_retirement_candidates():
    # Strategy "loser" with realized loss
    for i in range(5):
        _insert_filled_order(f"loser-buy-{i}", "ZZL", "buy", 1, 100.0, 100.0, "loser")
        _insert_filled_order(f"loser-sell-{i}", "ZZL", "sell", 1, 95.0, 95.0, "loser")
    candidates = strategy_attribution.retirement_candidates(days=2, min_fills=5, max_realized=0.0)
    assert any(c["strategy_id"] == "loser" for c in candidates)
