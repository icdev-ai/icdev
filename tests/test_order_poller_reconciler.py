"""Tests for execution.order_poller and execution.position_reconciler."""

import pytest

from tools.trading.brokers import alpaca_adapter as adapter_mod
from tools.trading.brokers.alpaca_adapter import AlpacaAdapter, AlpacaError
from tools.trading.execution import order_poller, position_reconciler


@pytest.fixture(autouse=True)
def _bootstrap_ad_tables():
    """Ensure ad_orders + ad_positions + ad_trade_audit exist."""
    from tools.trading.audit.trade_audit import _conn as audit_conn
    from tools.trading.db import get_conn

    c = get_conn()
    c.commit()
    c.close()
    a = audit_conn()
    a.commit()
    a.close()


def test_poller_no_broker(monkeypatch):
    monkeypatch.setattr(adapter_mod, "_DEFAULT", AlpacaAdapter(api_key="", secret_key=""))
    out = order_poller.poll_open_orders()
    assert out["status"] == "no_broker"


def test_poller_handles_orphan(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    # Insert a non-terminal order whose broker lookup will 404
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_orders WHERE id = ?", ("orphan-test-001",))
    c.execute(
        "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("orphan-test-001", "pf-test", "ZZTOP", "buy", 1, "market", "accepted", "2026-04-12T00:00:00Z"),
    )
    c.commit()
    c.close()

    monkeypatch.setattr(fake, "get_order", lambda _id: (_ for _ in ()).throw(AlpacaError("HTTP 404 Not Found")))
    out = order_poller.poll_open_orders(limit=10)
    assert out["status"] == "ok"
    assert out["orphan"] >= 1
    assert any(d["id"] == "orphan-test-001" and d["result"] == "orphan" for d in out["details"])


def test_poller_marks_filled(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_orders WHERE id = ?", ("fill-test-001",))
    c.execute(
        "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("fill-test-001", "pf-test", "ZZTOP", "buy", 2, "market", "accepted", "2026-04-12T00:00:00Z"),
    )
    c.commit()
    c.close()

    monkeypatch.setattr(
        fake,
        "get_order",
        lambda _id: {"id": _id, "status": "filled", "filled_avg_price": "192.34"},
    )
    out = order_poller.poll_open_orders(limit=10)
    assert out["transitioned"] >= 1

    c = get_connection()
    row = c.execute("SELECT status, fill_price, filled_at FROM ad_orders WHERE id = ?", ("fill-test-001",)).fetchone()
    c.close()
    assert row["status"] == "filled"
    assert abs(row["fill_price"] - 192.34) < 1e-6
    assert row["filled_at"] is not None


def test_reconciler_no_broker(monkeypatch):
    monkeypatch.setattr(adapter_mod, "_DEFAULT", AlpacaAdapter(api_key="", secret_key=""))
    out = position_reconciler.reconcile()
    assert out["status"] == "no_broker"


def test_reconciler_detects_qty_drift(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_positions WHERE ticker = ?", ("ZZTEST",))
    c.execute(
        "INSERT INTO ad_positions (id, portfolio_id, ticker, qty, avg_cost, market_value, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("pos-recon-001", "pf-test", "ZZTEST", 10, 100.0, 1000.0, "2026-04-12T00:00:00Z"),
    )
    c.commit()
    c.close()

    monkeypatch.setattr(fake, "list_positions", lambda: [{"symbol": "ZZTEST", "qty": "5", "current_price": "100", "market_value": "500"}])
    out = position_reconciler.reconcile(portfolio_id="pf-test")
    assert out["status"] == "drift_detected"
    drift = [d for d in out["drift"] if d["ticker"] == "ZZTEST"]
    assert drift and drift[0]["kind"] == "qty_mismatch"
    assert drift[0]["severity"] == "high"


def test_reconciler_flags_broker_only(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    monkeypatch.setattr(fake, "list_positions", lambda: [{"symbol": "PHANTOM", "qty": "3", "current_price": "50", "market_value": "150"}])
    # Ensure no local position for PHANTOM
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_positions WHERE ticker = ?", ("PHANTOM",))
    c.commit()
    c.close()

    out = position_reconciler.reconcile()
    assert any(d["kind"] == "broker_only" and d["ticker"] == "PHANTOM" for d in out["drift"])


def test_reconciler_autofix_price(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_positions WHERE ticker = ?", ("PXFIX",))
    c.execute(
        "INSERT INTO ad_positions (id, portfolio_id, ticker, qty, avg_cost, market_value, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("pos-pxfix-001", "pf-test", "PXFIX", 10, 100.0, 1000.0, "2026-04-12T00:00:00Z"),
    )
    c.commit()
    c.close()

    monkeypatch.setattr(fake, "list_positions", lambda: [{"symbol": "PXFIX", "qty": "10", "current_price": "120", "market_value": "1200"}])
    out = position_reconciler.reconcile(portfolio_id="pf-test", auto_fix=True)
    assert any(f["ticker"] == "PXFIX" for f in out["fixed"])

    c = get_connection()
    mv = c.execute("SELECT market_value FROM ad_positions WHERE id = ?", ("pos-pxfix-001",)).fetchone()["market_value"]
    c.close()
    assert abs(mv - 1200.0) < 1e-6
