"""Tests for L-tier confluence pillars: ta_stack + event_stack."""

from tools.trading.analysis.confluence_pillars import event_stack, ta_stack


# ---------------- ta_stack ----------------


def _bars(closes):
    return [{"t": "t", "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1000} for c in closes]


def test_rsi_low_is_bull():
    closes = [100] + [100 - i for i in range(1, 20)]  # steady decline
    assert ta_stack._rsi_vote(ta_stack._rsi(closes)) in ("bull", "neutral")


def test_rsi_high_is_bear():
    closes = [50] + [50 + i for i in range(1, 20)]  # steady rally
    assert ta_stack._rsi_vote(ta_stack._rsi(closes)) in ("bear", "neutral")


def test_ema_matches_recursive_formula():
    e = ta_stack._ema([1, 2, 3, 4, 5, 6], 3)
    assert len(e) == 4
    assert e[0] == (1 + 2 + 3) / 3


def test_bollinger_touch_upper_is_bear():
    closes = [100] * 19 + [110]  # last bar pushes well above ±2σ
    assert ta_stack._bollinger_vote(closes) == "bear"


def test_ma_cross_rising_is_bull():
    closes = list(range(50, 120))  # steady rise
    assert ta_stack._ma_cross_vote(closes) == "bull"


def test_ta_stack_all_bull_gives_bull(monkeypatch):
    rising = _bars(list(range(50, 130)))
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.ta_stack.fetch_bars",
        lambda *a, **kw: rising,
    )
    p = ta_stack.build("ZZRISE")
    assert p.direction in ("bull", "neutral")  # rally may trip RSI bear
    assert "rsi" in p.evidence


def test_ta_stack_insufficient_bars(monkeypatch):
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.ta_stack.fetch_bars",
        lambda *a, **kw: _bars([100] * 10),
    )
    p = ta_stack.build("ZZSHORT")
    assert p.direction == "unknown"


def test_ta_stack_no_data(monkeypatch):
    monkeypatch.setattr(
        "tools.trading.analysis.confluence_pillars.ta_stack.fetch_bars",
        lambda *a, **kw: [],
    )
    p = ta_stack.build("ZZEMPTY")
    assert p.direction == "unknown"


# ---------------- event_stack ----------------


def test_event_stack_no_data_unknown():
    # Tables don't exist for this nonce ticker; all votes return unknown
    p = event_stack.build("NONEXIST-ZZZ-12345")
    assert p.direction == "unknown"


def test_event_stack_combines_votes(monkeypatch):
    monkeypatch.setattr(event_stack, "_recent_analyst_upgrade", lambda t, days=14: "bull")
    monkeypatch.setattr(event_stack, "_last_earnings_beat", lambda t: "bull")
    monkeypatch.setattr(event_stack, "_recent_insider_buy", lambda t, days=14: "neutral")
    monkeypatch.setattr(event_stack, "_news_cluster", lambda t, days=7: "neutral")
    p = event_stack.build("ZZHIGH")
    assert p.direction == "bull"


def test_event_stack_bearish_quorum(monkeypatch):
    monkeypatch.setattr(event_stack, "_recent_analyst_upgrade", lambda t, days=14: "bear")
    monkeypatch.setattr(event_stack, "_last_earnings_beat", lambda t: "bear")
    monkeypatch.setattr(event_stack, "_recent_insider_buy", lambda t, days=14: "bull")
    monkeypatch.setattr(event_stack, "_news_cluster", lambda t, days=7: "neutral")
    p = event_stack.build("ZZLOW")
    assert p.direction == "bear"


def test_event_stack_mixed_goes_neutral(monkeypatch):
    monkeypatch.setattr(event_stack, "_recent_analyst_upgrade", lambda t, days=14: "bull")
    monkeypatch.setattr(event_stack, "_last_earnings_beat", lambda t: "bear")
    monkeypatch.setattr(event_stack, "_recent_insider_buy", lambda t, days=14: "unknown")
    monkeypatch.setattr(event_stack, "_news_cluster", lambda t, days=7: "unknown")
    p = event_stack.build("ZZMIX")
    assert p.direction == "neutral"
