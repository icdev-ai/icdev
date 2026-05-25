"""Tests for execution.exit_executor."""

import pytest

from tools.trading.brokers import alpaca_adapter as adapter_mod
from tools.trading.brokers.alpaca_adapter import AlpacaAdapter
from tools.trading.execution import exit_executor, exit_manager


@pytest.fixture(autouse=True)
def _bootstrap():
    from tools.db.storage import get_connection
    from tools.trading.audit.trade_audit import _conn as audit_conn
    from tools.trading.db import get_conn

    audit_conn().close()
    get_conn().close()
    exit_manager._conn().close()
    exit_executor._conn().close()
    # Clean up any stale exit rows from previous tests in this session
    c = get_connection()
    c.execute("DELETE FROM ad_position_exits")
    c.commit()
    c.close()


def _seed_position(ticker, qty, price):
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_positions WHERE ticker = ?", (ticker,))
    c.execute(
        "INSERT INTO ad_positions (id, portfolio_id, ticker, qty, avg_cost, market_value, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"pos-{ticker}-test", "pf-test", ticker, qty, price, qty * price, "2026-04-13T00:00:00Z"),
    )
    c.commit()
    c.close()


def _seed_triggered_exit(ticker, order_id="ord-test-001"):
    eid = exit_manager.register(
        f"pos-{ticker}-{order_id}",
        ticker,
        "stop_loss",
        entry_price=100.0,
        pct=0.03,
    )
    # Flip status to triggered manually (simulating evaluate_exits firing)
    c = exit_manager._conn()
    c.execute(
        "UPDATE ad_position_exits SET status = 'triggered', triggered_at = ?, triggered_price = 96.5 WHERE id = ?",
        ("2026-04-13T10:00:00Z", eid),
    )
    c.commit()
    c.close()
    return eid


def test_idle_when_no_triggered(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    out = exit_executor.run_once()
    assert out["status"] == "ok"
    assert out["checked"] == 0


def test_halts_when_killswitch_tripped(monkeypatch):
    from tools.trading.risk import kill_switch

    monkeypatch.setattr(kill_switch, "_FLAG_FILE", __import__("pathlib").Path("nonexistent-flag-file"))
    monkeypatch.setenv("ICDEV_TRADING_KILLED", "1")
    out = exit_executor.run_once()
    assert out["status"] == "halted"
    assert out["reason"] == "kill_switch_active"
    monkeypatch.delenv("ICDEV_TRADING_KILLED")


def test_dry_run_does_not_submit(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    _seed_position("ZZDRY", 10, 100.0)
    _seed_triggered_exit("ZZDRY", "ord-zzdry-001")
    out = exit_executor.run_once(dry_run=True)
    assert any(e["ticker"] == "ZZDRY" and e["status"] == "dry_run" for e in out["executed"])


def test_executes_sell_and_marks_exit(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    _seed_position("ZZSELL", 10, 100.0)
    eid = _seed_triggered_exit("ZZSELL", "ord-zzsell-001")
    out = exit_executor.run_once()
    assert any(e["exit_id"] == eid and e["ticker"] == "ZZSELL" for e in out["executed"])
    # Row should now have executed_order_id
    from tools.db.storage import get_connection

    c = get_connection()
    row = c.execute("SELECT executed_order_id, executed_at FROM ad_position_exits WHERE id = ?", (eid,)).fetchone()
    c.close()
    assert row["executed_order_id"] is not None
    assert row["executed_at"] is not None


def test_noop_when_position_flat(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    # No position seeded for ZZFLAT
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_positions WHERE ticker = ?", ("ZZFLAT",))
    c.commit()
    c.close()
    _seed_triggered_exit("ZZFLAT", "ord-zzflat-001")
    out = exit_executor.run_once()
    assert any(s["reason"] == "no_open_position" and s["ticker"] == "ZZFLAT" for s in out["skipped"])


def test_idempotent_second_run_is_noop(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    _seed_position("ZZIDEM", 10, 100.0)
    _seed_triggered_exit("ZZIDEM", "ord-zzidem-001")
    out1 = exit_executor.run_once()
    out2 = exit_executor.run_once()
    assert out1["checked"] == 1
    assert out2["checked"] == 0  # second pass sees no pending
