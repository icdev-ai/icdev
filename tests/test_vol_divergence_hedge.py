"""Tests for vol_divergence + hedge_recommender."""

from unittest import mock

from tools.trading.analysis import vol_divergence as vd
from tools.trading.risk import hedge_recommender as hr


# vol_divergence


def _closes_with_daily_vol(daily_pct: float, n: int = 25, start: float = 100.0) -> list[float]:
    closes = [start]
    for i in range(n):
        # Alternate up/down so log-returns actually vary → non-zero stdev
        closes.append(closes[-1] * (1 + daily_pct if i % 2 == 0 else 1 - daily_pct))
    return closes


def test_realized_vol_matches_annualized_formula():
    # ±1% daily zigzag → daily stdev = 0.01, annualized ≈ 0.01 * sqrt(252) ≈ 15.87%
    closes = [100.0]
    for i in range(21):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    rv = vd._annualized_realized_vol(closes, window=20)
    assert 14 < rv < 18


def test_realized_vol_none_on_short_series():
    assert vd._annualized_realized_vol([100, 101], window=20) is None


def test_analyze_vol_rich_when_iv_much_higher_than_rv(monkeypatch):
    monkeypatch.setattr("tools.trading.analysis.vol_divergence.fetch_bars",
                        lambda *a, **kw: [{"c": 100.0 + i * 0.01, "t": "t"} for i in range(25)])
    with mock.patch("tools.trading.analysis.vol_divergence.current_vix", return_value=35.0):
        out = vd.analyze("SPY")
    assert out["status"] == "ok"
    assert out["signal"] == "vol_rich"
    assert out["divergence"] > 5


def test_analyze_hedges_cheap_when_rv_much_higher_than_iv(monkeypatch):
    closes = _closes_with_daily_vol(0.02)  # ~32% annualized
    monkeypatch.setattr("tools.trading.analysis.vol_divergence.fetch_bars",
                        lambda *a, **kw: [{"c": c, "t": "t"} for c in closes])
    with mock.patch("tools.trading.analysis.vol_divergence.current_vix", return_value=12.0):
        out = vd.analyze("SPY")
    assert out["signal"] == "hedges_cheap"


def test_analyze_no_data(monkeypatch):
    monkeypatch.setattr("tools.trading.analysis.vol_divergence.fetch_bars", lambda *a, **kw: [])
    with mock.patch("tools.trading.analysis.vol_divergence.current_vix", return_value=None):
        out = vd.analyze("SPY")
    assert out["status"] == "no_data"


# hedge_recommender


def test_no_hedge_in_neutral():
    with mock.patch(
        "tools.trading.risk.vix_term_structure.snapshot",
        return_value=mock.Mock(shape="NORMAL", backwardation_pct=0.0, slope_9d_to_6m_pct=0.03),
    ), mock.patch(
        "tools.trading.analysis.vol_divergence.analyze",
        return_value={"signal": "aligned", "divergence": 1, "realized_vol_pct": 15, "implied_vol_pct": 16},
    ), mock.patch(
        "tools.trading.brokers.alpaca_adapter.get_default"
    ):
        out = hr.recommend(portfolio_equity_usd=50000)
    assert out["recommendation"]["action"] == "no_hedge"


def test_backwardation_triggers_protection_above_25k():
    with mock.patch(
        "tools.trading.risk.vix_term_structure.snapshot",
        return_value=mock.Mock(shape="BACKWARDATED", backwardation_pct=0.08, slope_9d_to_6m_pct=-0.08),
    ), mock.patch(
        "tools.trading.analysis.vol_divergence.analyze",
        return_value={"signal": "aligned", "divergence": 0, "realized_vol_pct": 25, "implied_vol_pct": 30},
    ):
        out = hr.recommend(portfolio_equity_usd=50000)
    assert out["recommendation"]["action"] == "buy_protection"
    assert "VXX" in out["recommendation"]["instrument"] or "SPX" in out["recommendation"]["instrument"]


def test_steep_contango_and_rich_vol_suggests_selling():
    with mock.patch(
        "tools.trading.risk.vix_term_structure.snapshot",
        return_value=mock.Mock(shape="CONTANGO", backwardation_pct=0.0, slope_9d_to_6m_pct=0.30),
    ), mock.patch(
        "tools.trading.analysis.vol_divergence.analyze",
        return_value={"signal": "vol_rich", "divergence": 10, "realized_vol_pct": 12, "implied_vol_pct": 22},
    ):
        out = hr.recommend(portfolio_equity_usd=50000)
    assert out["recommendation"]["action"] == "sell_vol"


def test_cheap_hedges_in_contango():
    with mock.patch(
        "tools.trading.risk.vix_term_structure.snapshot",
        return_value=mock.Mock(shape="CONTANGO", backwardation_pct=0.0, slope_9d_to_6m_pct=0.10),
    ), mock.patch(
        "tools.trading.analysis.vol_divergence.analyze",
        return_value={"signal": "hedges_cheap", "divergence": -8, "realized_vol_pct": 25, "implied_vol_pct": 17},
    ):
        out = hr.recommend(portfolio_equity_usd=50000)
    assert out["recommendation"]["action"] == "buy_cheap_protection"


def test_disclaimer_always_present():
    with mock.patch(
        "tools.trading.risk.vix_term_structure.snapshot",
        return_value=mock.Mock(shape="UNKNOWN", backwardation_pct=None, slope_9d_to_6m_pct=None),
    ), mock.patch(
        "tools.trading.analysis.vol_divergence.analyze",
        return_value={"signal": "unknown", "divergence": None, "realized_vol_pct": None, "implied_vol_pct": None},
    ):
        out = hr.recommend(portfolio_equity_usd=0)
    assert "Advisory only" in out["disclaimer"]
