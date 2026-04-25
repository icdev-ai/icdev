# CUI // SP-CTI
"""Integration tests for trap detection (ad710-integration-02).

Five handcrafted fixture scenarios:
  (a) Bull trap — breakout above $100 with declining volume, close back at $98,
      lower high at $99 → bull_trap detected.
  (b) Bear trap — mirror: breakdown below $100 with declining volume, recovery
      above $100, higher low → bear_trap detected.
  (c) Clean breakout — rising volume, price stays above — NO trap detected.
  (d) Liquidity trap active → check_liquidity_trap blocks short-condor proposal.
  (e) Coach event emitted when bull_trap fires on a user's LONG position.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from tools.trading.ta.traps import detect_traps
from tools.trading.ta.macro_liquidity import detect_liquidity_trap
from tools.scout.preflight import check_liquidity_trap
from tools.trading.options.coach_engine import scan_traps_against_positions


# ---------------------------------------------------------------------------
# Shared bar helper
# ---------------------------------------------------------------------------

def _bar(h: float, l: float, v: float = 1000.0) -> dict:
    return {"h": h, "l": l, "v": v, "c": (h + l) / 2}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
# Each fixture uses 20 alternating setup bars to establish S/R at $100
# (swing highs and lows confirmed at the 1.5 % threshold), then appends
# the scenario-specific bars.  Average volume across setup bars = 1 000.

def _bull_trap_setup() -> list[dict]:
    """20 bars alternating between resistance at $100 and support at $94.

    Even bars: h=100, l=96 → close=98   (swing HIGH at $100)
    Odd bars:  h=99,  l=94 → close=96.5 (swing LOW  at $94)

    find_swings (threshold 1.5%) confirms:
      HIGH at $100 → price must fall to ≤98.5 to confirm (odd bar l=94 satisfies).
      LOW  at $94  → price must rise to ≥95.41 to confirm (even bar h=100 satisfies).
    After compute_sr: resistance cluster at $100 (strength=1.0, ≥10 touches).
    """
    bars = []
    for i in range(20):
        if i % 2 == 0:
            bars.append(_bar(h=100.0, l=96.0))   # close=98
        else:
            bars.append(_bar(h=99.0, l=94.0))    # close=96.5
    return bars


def _bear_trap_setup() -> list[dict]:
    """20 bars alternating between resistance at $106 and support at $100.

    Even bars: h=106, l=100 → close=103  (swing HIGH at $106)
    Odd bars:  h=101.5, l=100 → close=100.75 (swing LOW at $100)

    find_swings confirms:
      HIGH at $106 → fall to ≤104.41 (odd bar l=100 satisfies).
      LOW  at $100 → rise to ≥101.5  (even bar h=106 satisfies, odd h=101.5 is boundary).
    After compute_sr: support cluster at $100 (strength=1.0, ≥10 touches).
    """
    bars = []
    for i in range(20):
        if i % 2 == 0:
            bars.append(_bar(h=106.0, l=100.0))    # close=103
        else:
            bars.append(_bar(h=101.5, l=100.0))    # close=100.75
    return bars


# ---------------------------------------------------------------------------
# (a) Bull trap fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def bull_trap_bars() -> list[dict]:
    """Resistance at $100.  Breakout bar 20 has v=280 (avg 1000, threshold 700).
    Bar 21 closes at $98.20 (re-entry, within max_reentry_bars=3).
    Bar 22 has high=$99 < $101.5 (breakout high) → lower high confirms bull trap.
    """
    bars = _bull_trap_setup()
    # Bar 20: breakout — close=100.85 > $100, v=280 << 700 threshold
    bars.append(_bar(h=101.5, l=100.2, v=280.0))  # close=100.85
    # Bar 21: re-entry — close=98.20 < $100  (reentry within 1 bar)
    bars.append(_bar(h=100.4, l=96.0, v=800.0))   # close=98.20
    # Bar 22: lower high — h=99.0 < 101.5 (breakout bar high)
    bars.append(_bar(h=99.0, l=97.0, v=900.0))    # close=98.0
    # Bar 23: continuation
    bars.append(_bar(h=98.0, l=96.0, v=900.0))
    return bars


# ---------------------------------------------------------------------------
# (b) Bear trap fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def bear_trap_bars() -> list[dict]:
    """Support at $100.  Breakdown bar 20 has v=280 (avg 1000, threshold 700).
    Bar 21 closes at $101.25 (recovery, within max_reentry_bars=3).
    Bar 22 has low=$100.5 > $98.5 (breakdown bar low) → higher low confirms bear trap.
    """
    bars = _bear_trap_setup()
    # Bar 20: breakdown — close=99.25 < $100, v=280 << 700 threshold
    bars.append(_bar(h=100.0, l=98.5, v=280.0))   # close=99.25
    # Bar 21: recovery — close=101.25 > $100 (recovery within 1 bar)
    bars.append(_bar(h=103.0, l=99.5, v=800.0))   # close=101.25
    # Bar 22: higher low — l=100.5 > 98.5 (breakdown bar low)
    bars.append(_bar(h=102.0, l=100.5, v=900.0))  # close=101.25
    # Bar 23: continuation
    bars.append(_bar(h=103.0, l=101.0, v=900.0))
    return bars


# ---------------------------------------------------------------------------
# (c) Clean breakout fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def clean_breakout_bars() -> list[dict]:
    """Resistance at $100.  Breakout bar 20 has v=2500 >> 700 threshold.
    Price stays above $100 for all subsequent bars — no re-entry.
    detect_traps should return [] (no trap detected).
    """
    bars = _bull_trap_setup()
    # Bar 20: breakout with STRONG volume — not a trap candidate
    bars.append(_bar(h=102.0, l=100.2, v=2500.0))  # close=101.1
    # Bars 21-23: continuation above $100
    bars.append(_bar(h=103.0, l=101.0, v=2000.0))
    bars.append(_bar(h=104.0, l=102.0, v=1800.0))
    bars.append(_bar(h=105.0, l=103.0, v=1900.0))
    return bars


# ---------------------------------------------------------------------------
# (d) Liquidity-trap preflight fixture
# ---------------------------------------------------------------------------

LIQTRAP_ACTIVE_DATA = {
    "fed_funds": 0.25,         # < 1.0  ✓
    "m2_velocity_change": 1.5, # < 2.0  ✓
    "cpi_yoy": 2.0,            # < 2.5  ✓
}

LIQTRAP_INACTIVE_DATA = {
    "fed_funds": 4.5,          # > 1.0  — normal rate environment
    "m2_velocity_change": 3.5, # > 2.0
    "cpi_yoy": 5.5,            # > 2.5
}


# ---------------------------------------------------------------------------
# (e) Coach event DB fixture
# ---------------------------------------------------------------------------

_COACH_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS ad_options_coach_events (
    id                    TEXT PRIMARY KEY,
    position_id           TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    tenant_id             TEXT NOT NULL DEFAULT 'default',
    event_type            TEXT NOT NULL,
    severity              TEXT NOT NULL DEFAULT 'info',
    summary               TEXT NOT NULL,
    recommendation        TEXT,
    position_snapshot_json TEXT,
    trap_event_id         TEXT,
    created_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ad_coach_position_trap_dedup
    ON ad_options_coach_events(position_id, trap_event_id)
    WHERE trap_event_id IS NOT NULL;
"""


@pytest.fixture()
def coach_db():
    """In-memory SQLite connection with ad_options_coach_events table."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_COACH_EVENTS_DDL)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ===========================================================================
# (a) Bull Trap Detection
# ===========================================================================

class TestBullTrapDetection:
    def test_bull_trap_detected(self, bull_trap_bars):
        """Weak-volume breakout above $100 followed by re-entry must yield bull_trap."""
        traps = detect_traps(bull_trap_bars)
        patterns = [t["pattern"] for t in traps]
        assert "bull_trap" in patterns, (
            f"Expected bull_trap in results, got: {traps}"
        )

    def test_bull_trap_broken_level_near_100(self, bull_trap_bars):
        """Broken level must be the $100 resistance (within 1%)."""
        traps = detect_traps(bull_trap_bars)
        bull = next((t for t in traps if t["pattern"] == "bull_trap"), None)
        assert bull is not None
        assert abs(bull["broken_level"] - 100.0) <= 1.0, (
            f"broken_level {bull['broken_level']} not near $100"
        )

    def test_bull_trap_reentry_within_3_bars(self, bull_trap_bars):
        """Re-entry bar must be at most max_reentry_bars=3 after the breakout."""
        traps = detect_traps(bull_trap_bars)
        bull = next(t for t in traps if t["pattern"] == "bull_trap")
        assert bull["reentry_bar"] - bull["breakout_bar"] <= 3, (
            f"reentry_bar {bull['reentry_bar']} too far from breakout_bar {bull['breakout_bar']}"
        )

    def test_bull_trap_lower_high_bar_present(self, bull_trap_bars):
        """lower_high_bar key must be present and after the breakout bar."""
        traps = detect_traps(bull_trap_bars)
        bull = next(t for t in traps if t["pattern"] == "bull_trap")
        assert "lower_high_bar" in bull
        assert bull["lower_high_bar"] > bull["breakout_bar"]

    def test_bull_trap_volume_ratio_low(self, bull_trap_bars):
        """Volume ratio (bar_vol/avg_vol) must be below 0.7 threshold."""
        traps = detect_traps(bull_trap_bars)
        bull = next(t for t in traps if t["pattern"] == "bull_trap")
        assert bull["volume_ratio"] < 0.7, (
            f"volume_ratio {bull['volume_ratio']} not below 0.7"
        )

    def test_bull_trap_confidence_positive(self, bull_trap_bars):
        """Confidence must be a positive float."""
        traps = detect_traps(bull_trap_bars)
        bull = next(t for t in traps if t["pattern"] == "bull_trap")
        assert 0.0 < bull["confidence"] <= 1.0


# ===========================================================================
# (b) Bear Trap Detection
# ===========================================================================

class TestBearTrapDetection:
    def test_bear_trap_detected(self, bear_trap_bars):
        """Weak-volume breakdown below $100 followed by recovery must yield bear_trap."""
        traps = detect_traps(bear_trap_bars)
        patterns = [t["pattern"] for t in traps]
        assert "bear_trap" in patterns, (
            f"Expected bear_trap in results, got: {traps}"
        )

    def test_bear_trap_broken_level_near_100(self, bear_trap_bars):
        """Broken level must be the $100 support (within 1%)."""
        traps = detect_traps(bear_trap_bars)
        bear = next((t for t in traps if t["pattern"] == "bear_trap"), None)
        assert bear is not None
        assert abs(bear["broken_level"] - 100.0) <= 1.0, (
            f"broken_level {bear['broken_level']} not near $100"
        )

    def test_bear_trap_reentry_within_3_bars(self, bear_trap_bars):
        """Recovery bar must be at most max_reentry_bars=3 after the breakdown."""
        traps = detect_traps(bear_trap_bars)
        bear = next(t for t in traps if t["pattern"] == "bear_trap")
        assert bear["reentry_bar"] - bear["breakout_bar"] <= 3

    def test_bear_trap_higher_low_bar_present(self, bear_trap_bars):
        """higher_low_bar key must be present and after the breakout bar."""
        traps = detect_traps(bear_trap_bars)
        bear = next(t for t in traps if t["pattern"] == "bear_trap")
        assert "higher_low_bar" in bear
        assert bear["higher_low_bar"] > bear["breakout_bar"]

    def test_bear_trap_volume_ratio_low(self, bear_trap_bars):
        """Volume ratio must be below 0.7 threshold."""
        traps = detect_traps(bear_trap_bars)
        bear = next(t for t in traps if t["pattern"] == "bear_trap")
        assert bear["volume_ratio"] < 0.7

    def test_bear_trap_confidence_positive(self, bear_trap_bars):
        traps = detect_traps(bear_trap_bars)
        bear = next(t for t in traps if t["pattern"] == "bear_trap")
        assert 0.0 < bear["confidence"] <= 1.0


# ===========================================================================
# (c) Clean Breakout — NO trap
# ===========================================================================

class TestCleanBreakout:
    def test_no_trap_detected(self, clean_breakout_bars):
        """High-volume breakout with continuation must NOT yield any trap."""
        traps = detect_traps(clean_breakout_bars)
        assert traps == [], (
            f"Expected no traps for clean breakout, got: {traps}"
        )

    def test_no_bull_trap_when_volume_strong(self, clean_breakout_bars):
        """Specifically, no bull_trap when volume exceeds 70% of average."""
        traps = detect_traps(clean_breakout_bars)
        assert not any(t["pattern"] == "bull_trap" for t in traps)

    def test_no_bear_trap_when_no_breakdown(self, clean_breakout_bars):
        """No bear_trap when price never breaks below $100 support."""
        traps = detect_traps(clean_breakout_bars)
        assert not any(t["pattern"] == "bear_trap" for t in traps)


# ===========================================================================
# (d) Liquidity Trap Preflight — blocks short-condor
# ===========================================================================

class TestLiquidityTrapPreflight:
    def test_detect_liquidity_trap_active(self):
        """All three macro conditions met → active=True, confidence=1.0."""
        result = detect_liquidity_trap(LIQTRAP_ACTIVE_DATA)
        assert result["active"] is True
        assert result["confidence"] == 1.0

    def test_detect_liquidity_trap_inactive(self):
        """Normal rate environment → active=False."""
        result = detect_liquidity_trap(LIQTRAP_INACTIVE_DATA)
        assert result["active"] is False

    def test_detect_liquidity_trap_partial(self):
        """Only two of three conditions met → active=False, 0 < confidence < 1."""
        partial = {"fed_funds": 0.25, "m2_velocity_change": 1.5, "cpi_yoy": 4.0}
        result = detect_liquidity_trap(partial)
        assert result["active"] is False
        assert 0.0 < result["confidence"] < 1.0

    def test_preflight_blocks_short_condor_when_active(self):
        """When liquidity trap is active, short-condor proposal must be blocked."""
        ok, reason = check_liquidity_trap(LIQTRAP_ACTIVE_DATA, strategy="short_condor")
        assert ok is False, f"Expected preflight to block, got ok=True, reason={reason!r}"
        assert "liquidity_trap" in reason.lower() or "blocked" in reason.lower(), (
            f"Reason does not mention trap or block: {reason!r}"
        )

    def test_preflight_allows_when_inactive(self):
        """No liquidity trap → preflight passes for short-condor."""
        ok, _ = check_liquidity_trap(LIQTRAP_INACTIVE_DATA, strategy="short_condor")
        assert ok is True

    def test_preflight_allows_non_short_premium_strategy(self):
        """Non-short-premium strategy is always allowed regardless of trap state."""
        ok, reason = check_liquidity_trap(LIQTRAP_ACTIVE_DATA, strategy="long_call")
        assert ok is True
        assert "not subject" in reason.lower()

    def test_preflight_bypass_env_var(self):
        """ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE=true bypasses the block."""
        original = os.environ.pop("ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE", None)
        try:
            os.environ["ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE"] = "true"
            ok, reason = check_liquidity_trap(LIQTRAP_ACTIVE_DATA, strategy="short_condor")
            assert ok is True
            assert "bypass" in reason.lower()
        finally:
            if original is None:
                os.environ.pop("ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE", None)
            else:
                os.environ["ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE"] = original

    def test_preflight_bypass_kwarg(self):
        """bypass=True kwarg bypasses the block without env var."""
        ok, reason = check_liquidity_trap(
            LIQTRAP_ACTIVE_DATA, strategy="short_condor", bypass=True
        )
        assert ok is True
        assert "bypass" in reason.lower()


# ===========================================================================
# (e) Coach Event Emitted When Trap Fires on a User's Position
# ===========================================================================

class TestCoachEventOnTrap:
    _POSITION = {
        "id": "pos-aapl-long-001",
        "ticker": "AAPL",
        "direction": "LONG",
        "user_id": "user-42",
        "tenant_id": "default",
    }

    _BULL_TRAP_EVENT = {
        "id": "trap-evt-001",
        "ticker": "AAPL",
        "pattern": "bull_trap",
        "broken_level": 100.0,
        "confidence": 0.65,
    }

    def test_coach_event_inserted(self, coach_db):
        """A coach event with event_type=trap_against_position must be written."""
        inserted = scan_traps_against_positions(
            [self._POSITION], [self._BULL_TRAP_EVENT], coach_db
        )
        assert len(inserted) == 1, f"Expected 1 inserted event, got {inserted}"

        row = coach_db.execute(
            "SELECT * FROM ad_options_coach_events WHERE id = ?",
            (inserted[0]["id"],),
        ).fetchone()

        assert row is not None
        assert row["event_type"] == "trap_against_position"
        assert row["severity"] == "critical"
        assert row["position_id"] == self._POSITION["id"]
        assert row["trap_event_id"] == self._BULL_TRAP_EVENT["id"]

    def test_coach_event_summary_mentions_ticker(self, coach_db):
        """Coach event summary must reference the ticker and trap pattern."""
        inserted = scan_traps_against_positions(
            [self._POSITION], [self._BULL_TRAP_EVENT], coach_db
        )
        row = coach_db.execute(
            "SELECT summary FROM ad_options_coach_events WHERE id = ?",
            (inserted[0]["id"],),
        ).fetchone()
        assert "AAPL" in row["summary"]
        assert "bull trap" in row["summary"]

    def test_coach_event_recommendation_present(self, coach_db):
        """Coach event recommendation must not be empty."""
        inserted = scan_traps_against_positions(
            [self._POSITION], [self._BULL_TRAP_EVENT], coach_db
        )
        row = coach_db.execute(
            "SELECT recommendation FROM ad_options_coach_events WHERE id = ?",
            (inserted[0]["id"],),
        ).fetchone()
        assert row["recommendation"] and len(row["recommendation"]) > 10

    def test_no_event_for_wrong_direction(self, coach_db):
        """SHORT position should not trigger a coach event for bull_trap."""
        short_pos = {**self._POSITION, "id": "pos-aapl-short-001", "direction": "SHORT"}
        inserted = scan_traps_against_positions(
            [short_pos], [self._BULL_TRAP_EVENT], coach_db
        )
        assert inserted == [], (
            f"Expected no events for SHORT position + bull_trap, got {inserted}"
        )

    def test_no_event_for_wrong_ticker(self, coach_db):
        """Position in TSLA should not trigger a AAPL bull_trap event."""
        tsla_pos = {**self._POSITION, "id": "pos-tsla-long-001", "ticker": "TSLA"}
        inserted = scan_traps_against_positions(
            [tsla_pos], [self._BULL_TRAP_EVENT], coach_db
        )
        assert inserted == []

    def test_coach_event_deduplication(self, coach_db):
        """Same (position, trap) pair must not emit a second event."""
        scan_traps_against_positions(
            [self._POSITION], [self._BULL_TRAP_EVENT], coach_db
        )
        second = scan_traps_against_positions(
            [self._POSITION], [self._BULL_TRAP_EVENT], coach_db
        )
        assert second == [], f"Expected dedup to suppress second event, got {second}"

        count = coach_db.execute(
            "SELECT COUNT(*) FROM ad_options_coach_events WHERE position_id = ? AND trap_event_id = ?",
            (self._POSITION["id"], self._BULL_TRAP_EVENT["id"]),
        ).fetchone()[0]
        assert count == 1, f"Expected exactly 1 row in DB, found {count}"

    def test_bear_trap_fires_on_short_position(self, coach_db):
        """Bear trap against a SHORT position must emit a coach event."""
        short_pos = {
            "id": "pos-spy-short-001",
            "ticker": "SPY",
            "direction": "SHORT",
            "user_id": "user-42",
            "tenant_id": "default",
        }
        bear_trap = {
            "id": "trap-evt-bear-001",
            "ticker": "SPY",
            "pattern": "bear_trap",
            "broken_level": 450.0,
            "confidence": 0.80,
        }
        inserted = scan_traps_against_positions([short_pos], [bear_trap], coach_db)
        assert len(inserted) == 1
        row = coach_db.execute(
            "SELECT event_type, severity FROM ad_options_coach_events WHERE id=?",
            (inserted[0]["id"],),
        ).fetchone()
        assert row["event_type"] == "trap_against_position"
        assert row["severity"] == "critical"
