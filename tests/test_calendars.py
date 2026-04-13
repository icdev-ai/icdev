"""Tests for market_calendar + earnings_calendar."""

from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from tools.trading.calendar import earnings_calendar, market_calendar


@pytest.fixture(autouse=True)
def _bootstrap():
    earnings_calendar._conn().close()


def test_holiday_is_closed():
    info = market_calendar.session_for(datetime(2026, 12, 25, 14, 0, tzinfo=ZoneInfo("America/New_York")))
    assert info.is_open is False
    assert info.is_holiday is True


def test_weekend_is_closed():
    sat = datetime(2026, 4, 11, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert market_calendar.is_market_open(sat) is False


def test_regular_session_open():
    midday_mon = datetime(2026, 4, 13, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    info = market_calendar.session_for(midday_mon)
    assert info.is_open is True
    assert info.session == "regular"


def test_pre_market_not_open():
    pre = datetime(2026, 4, 13, 7, 0, tzinfo=ZoneInfo("America/New_York"))
    info = market_calendar.session_for(pre)
    assert info.is_open is False
    assert info.session == "pre"


def test_post_market_not_open():
    post = datetime(2026, 4, 13, 17, 30, tzinfo=ZoneInfo("America/New_York"))
    info = market_calendar.session_for(post)
    assert info.is_open is False
    assert info.session == "post"


def test_half_day_closes_at_1pm():
    half = datetime(2026, 11, 27, 13, 30, tzinfo=ZoneInfo("America/New_York"))
    info = market_calendar.session_for(half)
    assert info.is_half_day is True
    assert info.is_open is False  # already past 1pm close


def test_next_open_skips_weekend():
    fri_close = datetime(2026, 4, 17, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    nxt = market_calendar.next_open(fri_close)
    assert nxt.weekday() == 0  # Monday


def test_earnings_add_and_in_blackout():
    now = datetime.now(timezone.utc)
    rep = (now + timedelta(hours=2)).isoformat()
    earnings_calendar.add("ZZEARN", rep, source="pytest")
    out = earnings_calendar.in_blackout("ZZEARN", now=now)
    assert out["in_blackout"] is True
    assert out["reason"] == "earnings_blackout"


def test_earnings_outside_window_clear():
    now = datetime.now(timezone.utc)
    rep = (now + timedelta(days=10)).isoformat()
    earnings_calendar.add("ZZCLEAR", rep, source="pytest")
    out = earnings_calendar.in_blackout("ZZCLEAR", now=now)
    assert out["in_blackout"] is False


def test_unknown_ticker_clear():
    out = earnings_calendar.in_blackout("UNKNOWN-TICKER-XYZ")
    assert out["in_blackout"] is False
