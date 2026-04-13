"""Tests for M-tier confluence pillars: multi-timeframe + price levels."""


from tools.trading.analysis.confluence_pillars import multi_timeframe, price_levels


# ---------------- multi_timeframe ----------------


def _mk_bars(prices: list[float]) -> list[dict]:
    return [{"t": "2026-01-01T00:00:00Z", "o": p, "h": p, "l": p, "c": p, "v": 1000} for p in prices]


def test_trend_rising_is_bull():
    bars = _mk_bars([100, 101, 102, 103, 105, 108, 110, 112])
    assert multi_timeframe._trend(bars) == "bull"


def test_trend_falling_is_bear():
    bars = _mk_bars([110, 108, 105, 103, 100, 98, 95, 93])
    assert multi_timeframe._trend(bars) == "bear"


def test_trend_flat_is_neutral():
    bars = _mk_bars([100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.1, 100.3])
    assert multi_timeframe._trend(bars) == "neutral"


def test_trend_sparse_is_unknown():
    assert multi_timeframe._trend(_mk_bars([100])) == "unknown"
    assert multi_timeframe._trend([]) == "unknown"


def test_multi_timeframe_all_bullish_agrees(monkeypatch):
    rising = _mk_bars([100, 102, 104, 106, 108, 110, 112, 114])
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.multi_timeframe.fetch_bars",
        lambda *a, **kw: rising,
    )
    p = multi_timeframe.build("ZZBULL")
    assert p.direction == "bull"
    assert "daily" in p.evidence


def test_multi_timeframe_all_bearish(monkeypatch):
    falling = _mk_bars([110, 108, 105, 102, 99, 96, 93, 90])
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.multi_timeframe.fetch_bars",
        lambda *a, **kw: falling,
    )
    p = multi_timeframe.build("ZZBEAR")
    assert p.direction == "bear"


def test_multi_timeframe_no_data(monkeypatch):
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.multi_timeframe.fetch_bars",
        lambda *a, **kw: [],
    )
    p = multi_timeframe.build("ZZEMPTY")
    assert p.direction == "unknown"


# ---------------- price_levels ----------------


def test_sma_basic():
    assert price_levels._sma([1, 2, 3, 4, 5], 3) == 4.0
    assert price_levels._sma([1, 2], 5) is None


def test_swing_high_low():
    bars = [{"h": 110, "l": 95}, {"h": 115, "l": 92}, {"h": 113, "l": 96}]
    hi, lo = price_levels._swing_high_low(bars)
    assert hi == 115
    assert lo == 92


def test_fib_levels_are_between_swings():
    f = price_levels._fib_levels(100, 50)
    assert 50 < f["fib_61.8"] < f["fib_50"] < f["fib_38.2"] < 100


def test_round_number_scales():
    assert price_levels._nearest_round(27.3) == 27.0  # step 1 under 50
    assert price_levels._nearest_round(184.2) == 185.0  # step 5 under 200
    assert price_levels._nearest_round(512.7) == 510.0  # step 10 above 200


def test_price_level_cluster_detects_support(monkeypatch):
    # Build 60 bars so SMA20/50 land near 100; current price 101 puts several
    # levels just below current → support cluster → bull
    closes = [100.0] * 60
    bars = [{"t": "t", "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 1000} for c in closes]
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.price_levels.fetch_bars",
        lambda *a, **kw: bars,
    )
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.price_levels.fetch_latest_quote",
        lambda *a, **kw: {"last": 101.0, "ask": 101.01},
    )
    p = price_levels.build("ZZSUP")
    assert p.direction == "bull"
    assert "support cluster" in p.evidence


def test_price_level_no_data(monkeypatch):
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.price_levels.fetch_latest_quote",
        lambda *a, **kw: {},
    )
    p = price_levels.build("ZZNO")
    assert p.direction == "unknown"


def test_price_level_insufficient_bars(monkeypatch):
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.price_levels.fetch_latest_quote",
        lambda *a, **kw: {"last": 100.0},
    )
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.price_levels.fetch_bars",
        lambda *a, **kw: _mk_bars([100, 101, 102]),
    )
    p = price_levels.build("ZZSHORT")
    assert p.direction == "unknown"
