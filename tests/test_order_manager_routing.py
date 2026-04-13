"""Tests for execution.order_manager broker routing + fallback."""

import pytest

from tools.trading.brokers import alpaca_adapter as adapter_mod
from tools.trading.brokers.alpaca_adapter import AlpacaAdapter, AlpacaError
from tools.trading.execution import order_manager


def test_validation_rejects_bad_side():
    with pytest.raises(ValueError):
        order_manager.place_order("AAPL", "hodl", 1)


def test_validation_rejects_zero_qty():
    with pytest.raises(ValueError):
        order_manager.place_order("AAPL", "buy", 0)


def test_validation_rejects_unknown_type():
    with pytest.raises(ValueError):
        order_manager.place_order("AAPL", "buy", 1, order_type="iceberg")


def test_falls_back_to_simulation_when_no_creds(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    out = order_manager.place_order("AAPL", "buy", 1)
    assert out["source"] == "simulation"
    assert out["status"] == "pending"
    assert out["symbol"] == "AAPL"


def test_routes_to_alpaca_when_available(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    captured = {}

    def fake_submit(**kw):
        captured.update(kw)
        return {"id": "broker-id", "status": "accepted", "symbol": kw["symbol"], "qty": str(kw["qty"]),
                "side": kw["side"], "type": kw["order_type"], "time_in_force": "day"}

    monkeypatch.setattr(fake, "submit_order", fake_submit)
    out = order_manager.place_order("aapl", "buy", 5, order_type="limit", limit_price=190.0, client_order_id="cid-1")
    assert out["source"] == "alpaca-paper"
    assert out["id"] == "broker-id"
    assert captured["symbol"] == "aapl"
    assert captured["client_order_id"] == "cid-1"
    assert captured["limit_price"] == 190.0


def test_records_rejection_on_alpaca_error(monkeypatch):
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    monkeypatch.setattr(fake, "submit_order", lambda **_kw: (_ for _ in ()).throw(AlpacaError("rate limited")))
    out = order_manager.place_order("AAPL", "buy", 1)
    assert out["status"] == "rejected"
    assert out["source"] == "alpaca-error"
    assert "rate limited" in out["error"]


def test_get_orders_falls_back_to_empty(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    assert order_manager.get_orders() == []
    assert order_manager.get_positions() == []


def test_cancel_order_simulation(monkeypatch):
    fake = AlpacaAdapter(api_key="", secret_key="")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    out = order_manager.cancel_order("xyz")
    assert out["source"] == "simulation"
    assert out["status"] == "canceled"
