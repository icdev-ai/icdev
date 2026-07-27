"""Data-quality guards for FathomDesk market data.

Hard violations raise DataQualityError — callers must not use the data.
Soft warnings are logged but do not raise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from tools.logging.icdev_logger import get_logger

log = get_logger("trading.quality")

# Tuning constants — change via args not code
_STALE_QUOTE_SECONDS: float = 300.0   # 5 min — real-time quote age limit
_STALE_BAR_DAYS: int = 3              # daily bars: flag if latest is >3 days old
_GAP_MULTIPLIER: float = 2.5          # gap > 2.5× median interval triggers warning
_SPLIT_RATIO_LOW: float = 0.60        # prev_close/curr_open below this → possible split
_SPLIT_RATIO_HIGH: float = 1.70       # above this → possible reverse split


class DataQualityError(ValueError):
    """Hard data quality violation — caller must not use this data."""


# ---------------------------------------------------------------------------
# Quote guards
# ---------------------------------------------------------------------------

def check_quote_freshness(
    quote,  # tools.trading.data.provider.Quote
    max_age_seconds: float = _STALE_QUOTE_SECONDS,
) -> bool:
    """Return True if quote is fresh, False if stale. Never raises.

    A False return means the caller should surface the stale indicator to the
    user — it does NOT mean the data should be discarded silently.
    """
    as_of = quote.as_of
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - as_of).total_seconds()
    if age > max_age_seconds:
        log.warning(
            "Stale quote for %s: age=%.0fs > threshold=%.0fs (source=%s)",
            quote.symbol, age, max_age_seconds, quote.source,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Bar series guards
# ---------------------------------------------------------------------------

def check_bar_series(bars: Sequence) -> None:  # Sequence[Bar]
    """Check a bar series for hard structural violations.

    Raises DataQualityError on:
      - duplicate timestamps
      - non-monotonic timestamps

    Logs warnings on:
      - gaps larger than 2.5× the median interval
    """
    if not bars:
        return

    timestamps: list[datetime] = []
    seen: set = set()

    for i, bar in enumerate(bars):
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if ts in seen:
            raise DataQualityError(
                f"Duplicate timestamp {ts} at index {i} for {bar.symbol}"
            )
        seen.add(ts)

        if timestamps and ts <= timestamps[-1]:
            raise DataQualityError(
                f"Non-monotonic timestamp at index {i} for {bar.symbol}: "
                f"{ts} <= {timestamps[-1]}"
            )
        timestamps.append(ts)

    # Gap detection requires at least 3 bars to compute median
    if len(timestamps) < 3:
        return

    intervals = [
        (timestamps[i + 1] - timestamps[i]).total_seconds()
        for i in range(len(timestamps) - 1)
    ]
    sorted_intervals = sorted(intervals)
    median_interval = sorted_intervals[len(sorted_intervals) // 2]

    if median_interval <= 0:
        return

    threshold = median_interval * _GAP_MULTIPLIER
    for i, gap in enumerate(intervals):
        if gap > threshold:
            log.warning(
                "Bar gap detected for %s between index %d and %d: "
                "%.0fs > %.0fs (%.1f× median)",
                bars[0].symbol, i, i + 1, gap, threshold,
                gap / median_interval,
            )


def check_split_adjusted(bars: Sequence) -> None:  # Sequence[Bar]
    """Warn when consecutive bars show a price jump consistent with an unadjusted split.

    This is a heuristic. FathomDesk does NOT guarantee split adjustment.
    Verify with the provider whether bars are adjusted=True.
    """
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        curr_open = bars[i].open
        if prev_close <= 0:
            continue
        ratio = curr_open / prev_close
        if ratio < _SPLIT_RATIO_LOW or ratio > _SPLIT_RATIO_HIGH:
            log.warning(
                "Possible unadjusted split/dividend in %s near bar %d: "
                "prev_close=%.4f curr_open=%.4f ratio=%.4f — verify split adjustment",
                bars[i].symbol, i, prev_close, curr_open, ratio,
            )


def is_stale_quote(quote, max_age_seconds: float = _STALE_QUOTE_SECONDS) -> bool:
    """Convenience inverse of check_quote_freshness — returns True when stale."""
    return not check_quote_freshness(quote, max_age_seconds)
