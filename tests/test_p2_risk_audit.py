"""Tests for P2 risk hardening: kill-switch, PDT, drawdown, audit."""

from unittest import mock

import pytest

from tools.trading.audit import trade_audit
from tools.trading.risk import drawdown_monitor, kill_switch, pdt_tracker


@pytest.fixture(autouse=True)
def _clean_killswitch_env(monkeypatch):
    monkeypatch.delenv("ICDEV_TRADING_KILLED", raising=False)
    yield


def test_killswitch_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(kill_switch, "_FLAG_FILE", tmp_path / ".kill_trading")
    kill_switch.clear("test")
    state = kill_switch.is_killed()
    assert state["killed"] is False
    assert state["sources"] == []


def test_killswitch_env_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(kill_switch, "_FLAG_FILE", tmp_path / ".kill_trading")
    monkeypatch.setenv("ICDEV_TRADING_KILLED", "1")
    state = kill_switch.is_killed()
    assert state["killed"] is True
    assert any(s["source"] == "env" for s in state["sources"])


def test_killswitch_file_trips(monkeypatch, tmp_path):
    flag = tmp_path / ".kill_trading"
    flag.write_text("manual halt")
    monkeypatch.setattr(kill_switch, "_FLAG_FILE", flag)
    state = kill_switch.is_killed()
    assert state["killed"] is True
    assert any(s["source"] == "file" for s in state["sources"])


def test_killswitch_db_trip_and_clear(monkeypatch, tmp_path):
    monkeypatch.setattr(kill_switch, "_FLAG_FILE", tmp_path / ".kill_trading")
    kill_switch.clear("test-setup")
    out = kill_switch.trip("test reason", "pytest")
    assert out["killed"] is True
    cleared = kill_switch.clear("pytest")
    assert cleared["killed"] is False


def test_pdt_above_threshold_allows_unlimited():
    fake = mock.Mock()
    fake.is_available.return_value = True
    fake.get_account.return_value = {"equity": "30000", "pattern_day_trader": False, "daytrade_count": 5}
    with mock.patch("tools.trading.brokers.alpaca_adapter.get_default", return_value=fake):
        out = pdt_tracker.evaluate(prospective_is_daytrade=True)
    assert out["allowed"] is True
    assert out["reason"] == "equity_above_threshold"


def test_pdt_sub_25k_blocks_4th_daytrade():
    fake = mock.Mock()
    fake.is_available.return_value = True
    fake.get_account.return_value = {"equity": "10000", "pattern_day_trader": False, "daytrade_count": 3}
    with mock.patch("tools.trading.brokers.alpaca_adapter.get_default", return_value=fake):
        out = pdt_tracker.evaluate(prospective_is_daytrade=True)
    assert out["allowed"] is False
    assert "sub_25k_pdt_cap_exceeded" in out["reason"]


def test_drawdown_no_data_when_no_broker():
    fake = mock.Mock()
    fake.is_available.return_value = False
    with mock.patch("tools.trading.brokers.alpaca_adapter.get_default", return_value=fake):
        out = drawdown_monitor.check()
    assert out["status"] == "no_data"
    assert out["killed"] is False


def test_drawdown_halt_trips_killswitch(monkeypatch, tmp_path):
    monkeypatch.setattr(kill_switch, "_FLAG_FILE", tmp_path / ".kill_trading")
    kill_switch.clear("setup")
    fake = mock.Mock()
    fake.is_available.return_value = True
    fake.get_account.return_value = {"equity": "97000", "last_equity": "100000"}
    with mock.patch("tools.trading.brokers.alpaca_adapter.get_default", return_value=fake):
        out = drawdown_monitor.check(warn_pct=-1.0, halt_pct=-2.0)
    assert out["status"] == "halted"
    assert out["killed"] is True
    state = kill_switch.is_killed()
    assert state["killed"] is True
    kill_switch.clear("teardown")


def test_audit_record_and_query():
    eid = trade_audit.record(
        "signal_generated",
        actor="pytest",
        ticker="ZZTOP",
        signal_id="sig-test-1",
        payload={"score": 0.8},
    )
    assert isinstance(eid, str) and len(eid) > 10
    rows = trade_audit.query(ticker="ZZTOP", limit=5)
    assert any(r["id"] == eid for r in rows)
    assert any(r.get("payload", {}).get("score") == 0.8 for r in rows)


def test_audit_rejects_unknown_event_type():
    with pytest.raises(ValueError):
        trade_audit.record("not_a_real_event", actor="pytest")
