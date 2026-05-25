"""Tests for tools.trading.execution.exit_manager."""

from datetime import datetime, timedelta, timezone

import pytest

from tools.trading.execution import exit_manager


@pytest.fixture(autouse=True)
def _bootstrap():
    exit_manager._conn().close()


def test_register_requires_pct_or_abs():
    with pytest.raises(ValueError):
        exit_manager.register("pos-1", "AAPL", "stop_loss", entry_price=100.0)


def test_register_time_stop_requires_hours():
    with pytest.raises(ValueError):
        exit_manager.register("pos-1", "AAPL", "time_stop", entry_price=100.0)


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        exit_manager.register("pos-1", "AAPL", "moonshot", entry_price=100.0, pct=0.1)


def test_stop_loss_triggers():
    eid = exit_manager.register("pos-stoploss", "AAPL", "stop_loss", entry_price=100.0, pct=0.03)
    out = exit_manager.evaluate_exits(price_overrides={"AAPL": 96.5})
    triggered = [t for t in out["triggered"] if t["exit_id"] == eid]
    assert triggered, f"expected stop-loss to trigger; got {out}"
    assert "stop_loss" in triggered[0]["reason"]


def test_take_profit_triggers():
    eid = exit_manager.register("pos-tp", "MSFT", "take_profit", entry_price=200.0, pct=0.06)
    out = exit_manager.evaluate_exits(price_overrides={"MSFT": 215.0})
    triggered = [t for t in out["triggered"] if t["exit_id"] == eid]
    assert triggered


def test_no_trigger_within_band():
    eid = exit_manager.register("pos-band", "GOOGL", "stop_loss", entry_price=100.0, pct=0.05)
    out = exit_manager.evaluate_exits(price_overrides={"GOOGL": 98.0})
    assert all(t["exit_id"] != eid for t in out["triggered"])


def test_trailing_stop_updates_peak_then_triggers():
    eid = exit_manager.register("pos-trail", "NVDA", "trailing_stop", entry_price=100.0, pct=0.05)
    # Push peak up first
    exit_manager.evaluate_exits(price_overrides={"NVDA": 120.0})
    active = [r for r in exit_manager.list_active("NVDA") if r["id"] == eid]
    assert active and active[0]["peak_price"] >= 120.0
    # Now drop to below peak * (1 - 0.05) = 114
    out = exit_manager.evaluate_exits(price_overrides={"NVDA": 113.0})
    triggered = [t for t in out["triggered"] if t["exit_id"] == eid]
    assert triggered, f"trailing stop should fire; got {out}"


def test_time_stop_triggers_after_hours():
    # Inject an entry time in the past via direct DB insert
    eid = exit_manager.register("pos-time", "META", "time_stop", entry_price=100.0, max_hold_hours=1)
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    c = exit_manager._conn()
    c.execute("UPDATE ad_position_exits SET entry_time = ? WHERE id = ?", (past, eid))
    c.commit()
    c.close()
    out = exit_manager.evaluate_exits(price_overrides={"META": 100.0})
    triggered = [t for t in out["triggered"] if t["exit_id"] == eid]
    assert triggered


def test_cancel_marks_inactive():
    eid = exit_manager.register("pos-cancel", "AMZN", "stop_loss", entry_price=100.0, pct=0.05)
    exit_manager.cancel(eid)
    active_ids = [r["id"] for r in exit_manager.list_active("AMZN")]
    assert eid not in active_ids
