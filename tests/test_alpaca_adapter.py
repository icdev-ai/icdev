"""Tests for tools.trading.brokers.alpaca_adapter."""

import os
from unittest import mock

import pytest

from tools.trading.brokers.alpaca_adapter import AlpacaAdapter, AlpacaError


def test_no_credentials_marks_unavailable(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    a = AlpacaAdapter(api_key="", secret_key="")
    assert a.is_available() is False
    with pytest.raises(AlpacaError):
        a.get_account()


def test_paper_url_detection():
    a = AlpacaAdapter(api_key="x", secret_key="y", base_url="https://paper-api.alpaca.markets/v2")
    assert a.is_paper() is True
    a2 = AlpacaAdapter(api_key="x", secret_key="y", base_url="https://api.alpaca.markets/v2")
    assert a2.is_paper() is False


def test_headers_require_credentials():
    a = AlpacaAdapter(api_key="K", secret_key="S")
    h = a._headers()
    assert h["APCA-API-KEY-ID"] == "K"
    assert h["APCA-API-SECRET-KEY"] == "S"
    assert h["Content-Type"] == "application/json"


def test_submit_order_builds_payload():
    a = AlpacaAdapter(api_key="K", secret_key="S")
    captured = {}

    def fake_request(method, url, params=None, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["body"] = body
        return {"id": "abc", "status": "accepted"}

    with mock.patch.object(a, "_request", side_effect=fake_request):
        out = a.submit_order("aapl", 5, "buy", "limit", limit_price=190.5, client_order_id="cid-1")
    assert out["id"] == "abc"
    assert captured["method"] == "POST"
    assert captured["body"]["symbol"] == "AAPL"
    assert captured["body"]["limit_price"] == "190.5"
    assert captured["body"]["client_order_id"] == "cid-1"


def test_market_data_falls_back_to_sample(monkeypatch):
    """When adapter raises, fetch_bars should still return sample bars."""
    from tools.trading.brokers import alpaca_adapter as adapter_mod
    from tools.trading.data import market_data

    # Force a fresh adapter that "looks available" but raises on call.
    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)

    def boom(*a, **kw):
        raise AlpacaError("simulated outage")

    monkeypatch.setattr(fake, "get_bars", boom)

    bars = market_data.fetch_bars("AAPL", limit=3)
    assert len(bars) == 3
    assert all({"o", "h", "l", "c", "v"}.issubset(b) for b in bars)


def test_quote_falls_back_to_sample(monkeypatch):
    from tools.trading.brokers import alpaca_adapter as adapter_mod
    from tools.trading.data import market_data

    fake = AlpacaAdapter(api_key="K", secret_key="S")
    monkeypatch.setattr(adapter_mod, "_DEFAULT", fake)
    monkeypatch.setattr(fake, "get_latest_quote", lambda *_a, **_k: (_ for _ in ()).throw(AlpacaError("x")))
    q = market_data.fetch_latest_quote("AAPL")
    assert q["source"] == "sample"
    assert q["last"] > 0


@pytest.mark.skipif(
    not (os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY")),
    reason="live alpaca creds not configured",
)
def test_live_paper_account_probe():
    """Smoke test against real paper endpoint when creds present."""
    a = AlpacaAdapter()
    assert a.is_available()
    acct = a.get_account()
    assert "cash" in acct
    assert "equity" in acct
